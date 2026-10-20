"""Plan maestro 1.5 · pedidos web sin perdidas (escenario de qa-juicio/tienda_bajo_pedido.py):

- la compra al proveedor nace al cobrar: sin pago la solicitud queda "reservada" (aparta, sin aviso); pagado -> borrador
  y UN aviso "PAGADO: comprar hoy" con los codigos del proveedor;
- un pedido SINPE sin pagar vence -> el producto vuelve a venderse y nunca hubo aviso de compra;
- con tarjeta, nunca se cancela sin preguntarle a ONVO (pagado sin webhook -> pagado; ONVO caido -> reintenta);
- comprobante en revision -> no se cancela;
- recordatorio de pago 24 h antes, una sola vez (y con la fecha de Costa Rica, no la UTC);
- consulta a ONVO cada 15 min, alerta al admin por 401/503 del webhook (una por dia de Costa Rica);
- efectivo solo con retiro en sitio y "Avisarme cuando haya" con enlace a la ficha.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import EmailOutbox, Order, PurchaseRequest, StockReservation, Tenant
from app.providers.einvoice import CR
from app.services import store_inventory as sinv
from app.services import store_orders as so
from app.tasks import tienda
from tests.test_disponibilidad import supplier_item
from tests.test_tienda_inventario import _avail, _buy, _cfg, _in, _prod, _publish, _whs

D = Decimal
SLUG = "/public/store/crimson"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _order(db, number) -> Order:
    return db.scalar(select(Order).where(Order.number == number))


def _req(db, o) -> PurchaseRequest:
    return db.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))


def _purchase_mails(db):
    return db.scalars(select(EmailOutbox).where(EmailOutbox.entity == "purchase_request")).all()


def _order_mails(db, o, prefix):
    return [m for m in db.scalars(select(EmailOutbox).where(EmailOutbox.entity == "order", EmailOutbox.entity_id == o.id)) if m.subject.startswith(prefix)]


def _pagos(client, tarjeta=False):
    ck = {
        "methods": {
            "sinpe": {"active": True, "phone": "8888-7777", "holder": "Crimson SA"},
            "transferencia": {"active": True, "iban": "CR05015201001026284066", "bank": "BCR", "holder": "Crimson SA"},
            "tarjeta": {"active": tarjeta, "installments": False, "max_installments": 0},
            "efectivo": {"active": True},
        },
        "auto_invoice": False,
        "pickup": {"address": "Oficinas, Escazú", "hours": "L-V 8-17"},
    }
    r = client.put("/store/checkout", json=ck)
    assert r.status_code == 200, r.text


def _vencer(db, o, minutes=1):
    """El plazo para pagar ya paso (y la reserva propia, si la hay)."""
    past = datetime.now(UTC) - timedelta(minutes=minutes)
    o.expires_at = past
    for r in db.scalars(select(StockReservation).where(StockReservation.order_id == o.id)):
        r.expires_at = past
    db.commit()


def _bajo_pedido(client, db, code="CIC-1", name="Grabador NVR", qty=2):
    p = _prod(client, code, name)
    supplier_item(db, p["id"], qty, code=f"SIRETT-{code}")  # nada propio: todo bajo pedido, con codigo del proveedor
    return p


# ---------- la compra nace al cobrar ----------
def test_sinpe_sin_pagar_vence_libera_y_nunca_avisa_compra(client, auth, db_session):
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session)
    _cfg(client, cancel_unpaid_on_expiry=True)
    r = _buy(client, [{"product_id": p["id"], "quantity": 2}])
    assert r.status_code == 201, r.text
    o = _order(db_session, r.json()["number"])
    req = _req(db_session, o)
    assert req.status == "reservada" and _purchase_mails(db_session) == []
    # mientras el pedido no vence, lo del proveedor queda apartado para el: el siguiente cliente no lo compra
    assert _avail(client, p["id"]).json()[str(p["id"])]["disponible"] is False
    assert _buy(client, [{"product_id": p["id"], "quantity": 1}]).status_code == 409
    # una solicitud reservada no se le pide al proveedor (ni se recibe) a mano
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"}).status_code == 409
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "recibida"}).status_code == 409
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "espera_pago"

    # vence sin pago: sin reserva propia igual se recorre por expires_at y se cancela
    _vencer(db_session, o)
    assert sinv.expire_holds(db_session) == 1
    db_session.refresh(o)
    db_session.refresh(req)
    assert o.status == "cancelado" and o.cancel_reason == "Venció sin pago" and req.status == "cancelada"
    assert not o.notes  # la nota va al historial, no a "Nota del cliente"
    assert any(h["status"] == "cancelado" and h.get("note") == "Venció sin pago" and h["by"] == "sistema" for h in o.history)
    # el producto vuelve a venderse y nunca hubo aviso de compra
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "bajo_pedido"
    assert _purchase_mails(db_session) == []
    # el cliente recibe el aviso con el enlace para rehacer la compra (la ficha del producto)
    venc = _order_mails(db_session, o, "Tu pedido")
    assert len(venc) == 1 and "venció sin pago" in venc[0].subject and f"/p/grabador-nvr-{p['id']}" in venc[0].html
    # otra corrida no hace nada
    assert sinv.expire_holds(db_session) == 0
    assert len(_order_mails(db_session, o, "Tu pedido")) == 1


def test_al_encender_cancelar_los_pedidos_viejos_se_cancelan_sin_correo(client, auth, db_session):
    """cancel_unpaid_on_expiry se enciende una semana despues del despliegue: lo abandonado de antes se cancela (y
    libera) pero el cliente no recibe "tu pedido venció" por un pedido de hace dias; uno recien vencido si."""
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session, "CIC-12", "Gabinete de rack", qty=4)
    viejo = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    nuevo = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    _vencer(db_session, viejo, minutes=6 * 24 * 60)
    _vencer(db_session, nuevo, minutes=30)
    sinv.expire_holds(db_session)  # todavia apagado: solo la nota del recien vencido (el viejo ya no se relee)
    db_session.refresh(viejo)
    assert viejo.status == "nuevo" and not so.has_note(viejo, "vencio")
    _cfg(client, cancel_unpaid_on_expiry=True)
    sinv.expire_holds(db_session)
    db_session.refresh(viejo)
    db_session.refresh(nuevo)
    assert viejo.status == nuevo.status == "cancelado" and _req(db_session, viejo).status == "cancelada"
    assert _order_mails(db_session, viejo, "Tu pedido") == [] and any("Sin correo al cliente" in (h.get("note") or "") for h in viejo.history)
    assert len(_order_mails(db_session, nuevo, "Tu pedido")) == 1


def test_pagado_pasa_a_borrador_con_un_solo_aviso_de_compra(client, auth, db_session):
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _bajo_pedido(client, db_session, "CIC-2", "Switch PoE", qty=5)
    _in(client, p["id"], main["id"], 1)  # 1 propio + 2 bajo pedido
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 3}]).json()["number"])
    req = _req(db_session, o)
    assert req.status == "reservada" and D(str(req.lines[0].quantity)) == 2
    st = lambda s, **kw: client.post(f"/store-orders/{o.id}/status", json={"status": s, **kw})  # noqa: E731
    assert st("pagado", payment_ref="SINPE 123").status_code == 200
    db_session.refresh(req)
    assert req.status == "borrador"
    avisos = _purchase_mails(db_session)
    assert avisos and len({m.subject for m in avisos}) == 1
    assert avisos[0].subject.startswith("PAGADO: comprar hoy a Eurocomp") and o.number in avisos[0].subject
    assert "SIRETT-CIC-2" in avisos[0].html and "Ojo" not in avisos[0].html
    n = len(avisos)
    # doble disparo: el pago repetido no cambia nada y avanzar el pedido no vuelve a avisar
    t = db_session.get(Tenant, o.tenant_id)
    assert so.mark_paid(db_session, t, o, by="onvo", ref="x") is False
    assert sinv.activate_requests(db_session, o) == []
    assert st("preparando").status_code == 200
    assert len(_purchase_mails(db_session)) == n
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "requiere_compra"
    # ahora si se le puede pedir al proveedor
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"}).status_code == 200


def test_sin_cancelar_al_vencer_nota_una_vez_y_pago_tardio(client, auth, db_session):
    """cancel_unpaid_on_expiry apagado (la primera semana): el pedido sigue "nuevo", queda UNA nota en el historial y lo
    apartado vuelve a venderse; si lo pagan tarde, la compra nace igual con la advertencia de que el plazo habia vencido."""
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session, "CIC-3", "Disco duro 4 TB")
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 2}]).json()["number"])
    _vencer(db_session, o)
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "bajo_pedido"  # vencido ya no aparta
    sinv.expire_holds(db_session)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "nuevo" and [h.get("key") for h in o.history].count("vencio") == 1 and not o.notes
    assert _req(db_session, o).status == "reservada"
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "vencido"
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "pagado"}).status_code == 200
    assert _req(db_session, o).status == "borrador"
    avisos = _purchase_mails(db_session)
    assert avisos and "Ojo" in avisos[0].html  # el pago llego despues del plazo: confirmar existencia


def test_pago_tardio_de_pedido_mixto_vuelve_a_reservar_lo_propio(client, auth, db_session):
    """1 propio + 2 bajo pedido, la reserva propia vence y lo pagan tarde: lo propio se vuelve a reservar (no queda
    sin apartar hasta el despacho) y la compra nace UNA vez, en una solicitud nueva que reemplaza a la reservada."""
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _bajo_pedido(client, db_session, "CIC-11", "Kit NVR 8 canales", qty=5)
    _in(client, p["id"], main["id"], 1)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 3}]).json()["number"])
    vieja = _req(db_session, o)
    assert vieja.status == "reservada"
    _vencer(db_session, o)
    sinv.expire_holds(db_session)  # cancel_unpaid_on_expiry apagado: sigue "nuevo"
    assert db_session.scalar(select(StockReservation).where(StockReservation.order_id == o.id)).status == "vencida"
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "pagado"}).status_code == 200
    db_session.refresh(vieja)
    activas = db_session.scalars(select(StockReservation).where(StockReservation.order_id == o.id, StockReservation.status == "confirmada")).all()
    assert len(activas) == 1 and D(str(activas[0].quantity)) == 1 and activas[0].expires_at is None
    reqs = db_session.scalars(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id).order_by(PurchaseRequest.id)).all()
    assert [r.status for r in reqs] == ["cancelada", "borrador"] and D(str(reqs[1].lines[0].quantity)) == 2
    avisos = _purchase_mails(db_session)
    assert len({m.subject for m in avisos}) == 1 and "Ojo" in avisos[0].html and reqs[1].number in avisos[0].subject
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "requiere_compra"


def test_comprobante_en_revision_no_se_cancela(client, auth, db_session):
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session, "CIC-4", "Cámara domo")
    _cfg(client, cancel_unpaid_on_expiry=True)
    r = _buy(client, [{"product_id": p["id"], "quantity": 2}])  # todo lo que el proveedor tiene
    o = _order(db_session, r.json()["number"])
    up = client.post(f"{SLUG}/orders/{r.json()['track_token']}/proof", files={"file": ("sinpe.png", PNG, "image/png")})
    assert up.status_code == 201 and up.json()["status"] == "en_revision"
    _vencer(db_session, o)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "en_revision" and _req(db_session, o).status == "reservada"
    # sigue apartando: el comprobante puede ser bueno
    assert _avail(client, p["id"]).json()[str(p["id"])]["disponible"] is False


# ---------- tarjeta: nunca se cancela sin preguntarle a ONVO ----------
def test_tarjeta_pagada_en_onvo_sin_webhook_queda_pagada(client, auth, db_session, fake_onvo):
    fake_onvo.connect(client)
    _publish(client)
    _pagos(client, tarjeta=True)
    main, _ = _whs(client)
    p = _prod(client, "CIC-5", "Kit de cámaras")
    _in(client, p["id"], main["id"], 3)
    _cfg(client, cancel_unpaid_on_expiry=True)

    pagado = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])
    assert pagado.payment_ref == "cs_1"
    fake_onvo.pay("cs_1")  # cobrado en ONVO; el webhook nunca llega y el comprador no vuelve
    _vencer(db_session, pagado)
    sinv.expire_holds(db_session)
    db_session.refresh(pagado)
    assert pagado.status == "pagado" and pagado.payment_ref == "pi_cs_1"

    # ONVO caido: no se cancela; la proxima corrida (ONVO responde que no cobro) si
    caido = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])
    _vencer(db_session, caido)
    fake_onvo.fail_get = 1
    sinv.expire_holds(db_session)
    db_session.refresh(caido)
    assert caido.status == "nuevo" and any(h.get("key") == "onvo_sin_confirmar" for h in caido.history)
    sinv.expire_holds(db_session)
    db_session.refresh(caido)
    assert caido.status == "cancelado"

    # pago en proceso (esperando al banco): tampoco se cancela
    proceso = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])
    sid = proceso.payment_ref
    fake_onvo.sessions[sid]["paymentIntentId"] = "pi_proceso"
    fake_onvo.intents["pi_proceso"] = {"id": "pi_proceso", "status": "processing", "amount": 1, "currency": "CRC"}
    _vencer(db_session, proceso)
    sinv.expire_holds(db_session)
    db_session.refresh(proceso)
    assert proceso.status == "nuevo"


def test_consulta_a_onvo_cada_15_min_aplica_el_pago(client, auth, db_session, fake_onvo):
    fake_onvo.connect(client)
    _publish(client)
    _pagos(client, tarjeta=True)
    main, _ = _whs(client)
    p = _prod(client, "CIC-6", "Lector de huella")
    _in(client, p["id"], main["id"], 2)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])
    assert tienda.sync_card_orders(db_session) == 0  # sin pagar: nada
    fake_onvo.pay(o.payment_ref)
    assert tienda.sync_card_orders(db_session) == 1
    db_session.refresh(o)
    assert o.status == "pagado" and o.paid_at
    assert any("consulta automática" in (h.get("note") or "") for h in o.history)
    assert tienda.sync_card_orders(db_session) == 0  # ya no se consulta


# ---------- recordatorio de pago ----------
def test_recordatorio_24h_antes_una_sola_vez(client, auth, db_session):
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "CIC-7", "UPS 1 kVA")
    _in(client, p["id"], main["id"], 10)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    otro = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Transferencia bancaria").json()["number"])
    # borde de las 18:00-24:00 CR: ahora son las 19:00 del 20/10 en Costa Rica (01:00 UTC del 21/10)
    now = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)
    o.created_at, o.expires_at = now - timedelta(hours=28), datetime(2026, 10, 21, 4, 30, tzinfo=UTC)  # vence 22:30 CR del 20/10
    otro.created_at, otro.expires_at = now - timedelta(hours=1), now + timedelta(hours=5)  # plazo corto: espera a la mitad
    db_session.commit()
    assert tienda.payment_reminders(db_session, now) == 1
    mails = _order_mails(db_session, o, "Tu pedido")
    assert len(mails) == 1 and mails[0].subject == f"Tu pedido {o.number} sigue pendiente de pago"
    html = mails[0].html
    assert "20/10/2026 a las 22:30" in html  # la hora de Costa Rica, no la fecha UTC
    assert "8888-7777" in html and "Subir el comprobante" in html and "?pedido=" in html
    # dos corridas (o dos procesos beat): un solo correo
    assert tienda.payment_reminders(db_session, now) == 0
    assert len(_order_mails(db_session, o, "Tu pedido")) == 1 and _order_mails(db_session, otro, "Tu pedido") == []
    # pasada la mitad del plazo corto, el otro si recibe el suyo
    assert tienda.payment_reminders(db_session, now + timedelta(hours=3)) == 1


def test_recordatorio_respeta_la_pausa_del_cliente(client, auth, db_session):
    """Customer.no_reminders (plan 1.3): si el cliente tiene los recordatorios pausados, no se le recuerda el pago."""
    from app.models import Customer

    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "CIC-13", "Fuente 12 V")
    _in(client, p["id"], main["id"], 2)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    c = db_session.get(Customer, o.customer_id)
    c.no_reminders = True  # el campo llega con 1.3; aqui basta el atributo
    now = datetime.now(UTC)
    o.created_at, o.expires_at = now - timedelta(hours=30), now + timedelta(hours=2)
    db_session.commit()
    assert tienda.payment_reminders(db_session, now) == 0
    db_session.refresh(o)
    assert _order_mails(db_session, o, "Tu pedido") == [] and so.has_note(o, so.REMINDER_NOTE)


def test_recordatorio_no_va_a_tarjeta_ni_a_comprobante_en_revision(client, auth, db_session):
    _publish(client)
    _pagos(client, tarjeta=True)
    main, _ = _whs(client)
    p = _prod(client, "CIC-8", "Cable UTP")
    _in(client, p["id"], main["id"], 10)
    card = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])
    r = _buy(client, [{"product_id": p["id"], "quantity": 1}])
    rev = _order(db_session, r.json()["number"])
    client.post(f"{SLUG}/orders/{r.json()['track_token']}/proof", files={"file": ("c.png", PNG + b"r", "image/png")})
    now = datetime.now(UTC)
    for o in (card, rev):
        o.created_at, o.expires_at = now - timedelta(hours=30), now + timedelta(hours=2)
    db_session.commit()
    assert tienda.payment_reminders(db_session, now) == 0


# ---------- alerta del webhook de ONVO ----------
def test_webhook_onvo_alerta_al_admin_una_vez_por_dia_de_costa_rica(client, auth, db_session, fake_onvo, monkeypatch):
    from app.services import onvo

    tid = auth["tenant"]["id"]
    alertas = lambda: db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "onvo_webhook").order_by(EmailOutbox.id)).all()  # noqa: E731
    # sin ONVO conectado, un 401 es una sonda a la URL: sin aviso; empresa inexistente: tampoco
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_x"}).status_code == 401
    assert fake_onvo.send(client, tid + 99, "checkout-session.succeeded", {"id": "cs_x"}).status_code == 401
    assert alertas() == []

    fake_onvo.connect(client)
    t0 = datetime(2026, 10, 20, 23, 30, tzinfo=CR)  # 05:30 UTC del 21/10
    monkeypatch.setattr(onvo, "_cr_now", lambda: t0)
    malo = "webhook_secret_" + "z" * 24
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_x"}, secret=malo).status_code == 401
    primeras = alertas()
    assert primeras and all("secreto" in a.subject for a in primeras)
    for a in primeras:
        a.created_at = t0.astimezone(UTC)  # el aviso salio a esa hora
    db_session.commit()
    # otro 401 y un 503 el mismo dia de Costa Rica (23:50): nada nuevo
    monkeypatch.setattr(onvo, "_cr_now", lambda: datetime(2026, 10, 20, 23, 50, tzinfo=CR))
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_y"}, secret=malo).status_code == 401
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_no_existe"}).status_code == 503
    assert len(alertas()) == len(primeras)
    # 00:10 del dia siguiente en Costa Rica (06:10 UTC: el MISMO dia UTC que el primer aviso): aviso nuevo
    monkeypatch.setattr(onvo, "_cr_now", lambda: datetime(2026, 10, 21, 0, 10, tzinfo=CR))
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_z"}).status_code == 503
    nuevas = alertas()[len(primeras) :]
    assert nuevas and all("no se pudo confirmar" in a.subject for a in nuevas)


# ---------- efectivo solo con retiro ----------
def test_efectivo_solo_con_retiro_en_sitio(client, auth):
    from tests.test_tienda_checkout import _body, _shop

    prods = _shop(client)
    m = {x["code"]: x for x in client.get(f"{SLUG}/checkout-config").json()["methods"]}
    assert m["efectivo"]["pickup_only"] is True and m["sinpe"]["pickup_only"] is False
    pid = prods[0]["id"]
    envio = client.post(f"{SLUG}/checkout", json=_body(pid, payment_method="efectivo"))
    assert envio.status_code == 422 and "retiro" in envio.json()["detail"]
    retiro = client.post(f"{SLUG}/checkout", json=_body(pid, payment_method="efectivo", delivery={"type": "retiro"}, shipping_method="Retiro en tienda"))
    assert retiro.status_code == 201, retiro.text
    # si Mi Tienda lo abre tambien al envio, se acepta
    cfg = client.get("/store/checkout").json()["checkout"]
    cfg["methods"]["efectivo"]["pickup_only"] = False
    body = {k: cfg[k] for k in ("methods", "free_shipping_from", "auto_invoice", "pickup", "notify_roles")}
    assert client.put("/store/checkout", json=body).status_code == 200
    assert client.post(f"{SLUG}/checkout", json=_body(pid, payment_method="efectivo", idempotency_key="ck-cash-envio")).status_code == 201


def test_efectivo_al_retirar_activa_la_compra_cuando_el_equipo_lo_avanza(client, auth, db_session):
    """El efectivo se paga al retirar: que el equipo avance el pedido es la decision de comprar lo bajo pedido."""
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session, "CIC-10", "Cerradura inteligente")
    body = {
        "lines": [{"product_id": p["id"], "quantity": 1}],
        "contact": {"name": "Ana Vega", "email": "ana@ejemplo.com"},
        "payment_method": "efectivo",
        "delivery": {"type": "retiro"},
        "shipping_method": "Retiro en tienda",
    }
    r = client.post(f"{SLUG}/checkout", json=body)
    assert r.status_code == 201, r.text
    o = _order(db_session, r.json()["number"])
    assert _req(db_session, o).status == "reservada" and _purchase_mails(db_session) == []
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "listo"}).status_code == 200
    assert _req(db_session, o).status == "borrador" and len({m.subject for m in _purchase_mails(db_session)}) == 1


# ---------- "Avisarme cuando haya" ----------
def test_avisarme_lleva_la_ficha_y_no_duplica_si_falla(client, auth, db_session, monkeypatch):
    from app.models import StoreStockAlert
    from app.services import mail

    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "CIC-9", "Videoportero IP")
    _cfg(client, out_of_stock_action="avisarme")
    assert client.post(f"{SLUG}/stock-alert", json={"product_id": p["id"], "email": "ana@ejemplo.com"}).status_code == 201
    _in(client, p["id"], main["id"], 1)
    monkeypatch.setattr(mail, "mail_configured", lambda: True)
    monkeypatch.setattr(mail, "deliver", lambda m, files=None: setattr(m, "status", "error"))  # el proveedor de correo fallo
    assert sinv.back_in_stock(db_session) == 0
    assert sinv.back_in_stock(db_session) == 0  # no encola otro: el worker de correo reintenta el mismo
    mails = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "stock_alert")).all()
    assert len(mails) == 1 and f"/p/videoportero-ip-{p['id']}" in mails[0].html
    a = db_session.scalar(select(StoreStockAlert).where(StoreStockAlert.product_id == p["id"]))
    assert a.notified_at is None
    mails[0].status = "enviado"  # retry_outbox lo entrego
    db_session.commit()
    assert sinv.back_in_stock(db_session) == 1
    db_session.refresh(a)
    assert a.notified_at is not None


# ---------- worker ----------
def test_worker_agenda_las_tareas_de_la_tienda(db_session, monkeypatch):
    import sys

    pytest.importorskip("celery")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    beat = {v["task"] for v in celery_app.celery.conf.beat_schedule.values()}
    assert {"store.payment_reminders", "store.sync_card_orders", "inventory.expire_reservations"} <= beat
    assert {"store.payment_reminders", "store.sync_card_orders"} <= set(celery_app.celery.tasks.keys())

    class _Ctx:
        def __enter__(self):
            return db_session

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx())
    assert tienda.payment_reminders_task() == 0 and tienda.sync_card_orders_task() == 0
