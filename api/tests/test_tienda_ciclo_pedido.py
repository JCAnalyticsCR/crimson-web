"""Plan maestro 1.5 · pedidos web sin perdidas (escenario de qa-juicio/tienda_bajo_pedido.py):

- la compra al proveedor nace al cobrar: sin pago la solicitud queda "reservada" (aparta, sin aviso); pagado -> borrador
  y UN aviso "PAGADO: comprar hoy" con los codigos del proveedor;
- un pedido SINPE sin pagar vence -> el producto vuelve a venderse y nunca hubo aviso de compra;
- con tarjeta, nunca se cancela dentro de su margen ni sin preguntarle a ONVO por TODAS sus sesiones (pagado sin
  webhook -> pagado; ONVO caido -> reintenta con espera creciente; cobro sin aplicar -> revisar a mano, un aviso);
- comprobante en revision -> no se cancela, y lo apartado espera hasta un tope (un comprobante falso no lo congela);
- recordatorio de pago 24 h antes, una sola vez (y con la fecha de Costa Rica, no la UTC), con dedupe_key, pausa del
  cliente y solo con la tienda publicada;
- consulta a ONVO cada 15 min, alerta al admin por 401 con forma de ONVO / 503 del webhook (una por tipo y dia de CR);
- efectivo solo con retiro en sitio, sin plazo para pagar (no se cancela solo) y "Avisarme cuando haya" con enlace.
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
    """El comprobante en revision nunca se cancela. Lo apartado espera la revision hasta un tope contado desde el
    PRIMER comprobante (un comprobante falso no aparta inventario para siempre); pasado el tope vuelve a la venta y el
    equipo recibe UN aviso."""
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session, "CIC-4", "Cámara domo")
    _cfg(client, cancel_unpaid_on_expiry=True)
    r = _buy(client, [{"product_id": p["id"], "quantity": 2}])  # todo lo que el proveedor tiene
    o = _order(db_session, r.json()["number"])
    up = client.post(f"{SLUG}/orders/{r.json()['track_token']}/proof", files={"file": ("sinpe.png", PNG, "image/png")})
    assert up.status_code == 201 and up.json()["status"] == "en_revision"
    db_session.refresh(o)
    assert so.aware(o.expires_at) > datetime.now(UTC) + timedelta(hours=47)  # tope: 48 h desde el primer comprobante
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "en_revision" and _req(db_session, o).status == "reservada"
    # dentro del tope sigue apartando: el comprobante puede ser bueno
    assert _avail(client, p["id"]).json()[str(p["id"])]["disponible"] is False
    # paso el tope sin que nadie lo revisara: no se cancela, pero lo apartado vuelve a la venta y el equipo se entera
    _vencer(db_session, o)
    sinv.expire_holds(db_session)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "en_revision" and _req(db_session, o).status == "reservada"
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "bajo_pedido"
    assert len({m.subject for m in _order_mails(db_session, o, "Comprobante sin revisar")}) == 1
    assert [h.get("key") for h in o.history].count(sinv.REVIEW_LATE_NOTE) == 1


def test_comprobante_en_revision_mantiene_lo_propio_y_rechazado_da_plazo(client, auth, db_session):
    """Con el comprobante en revision lo propio tampoco vence antes del tope (si se liberara, otro cliente se lleva la
    ultima unidad de un pedido pagado). Rechazado, la PRIMERA vez el cliente tiene al menos 24 h para subir otro; las
    siguientes no ganan tiempo (subir comprobantes falsos no estira lo apartado)."""
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "CIC-14", "Cámara bala 4 MP")
    _in(client, p["id"], main["id"], 1)
    _cfg(client, cancel_unpaid_on_expiry=True)
    r = _buy(client, [{"product_id": p["id"], "quantity": 1}])
    tok = r.json()["track_token"]
    o = _order(db_session, r.json()["number"])
    client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("s.png", PNG + b"p", "image/png")})
    res = db_session.scalar(select(StockReservation).where(StockReservation.order_id == o.id))
    db_session.refresh(res)
    db_session.refresh(o)
    assert res.status == "confirmada" and so.aware(res.expires_at) == so.aware(o.expires_at) > datetime.now(UTC) + timedelta(hours=47)
    sinv.expire_holds(db_session)
    db_session.refresh(res)
    assert res.status == "confirmada" and _avail(client, p["id"]).json()[str(p["id"])]["disponible"] is False
    # el comprobante se subio hace 47 h: al rechazarlo quedaba 1 h, y gana 24 h para subir otro
    now = datetime.now(UTC)
    o.proofs = [{**o.proofs[0], "at": (now - timedelta(hours=47)).isoformat()}]
    o.expires_at = res.expires_at = now + timedelta(hours=1)
    db_session.commit()
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "nuevo"}).status_code == 200
    db_session.refresh(o)
    db_session.refresh(res)
    assert so.aware(o.expires_at) > now + timedelta(hours=23) and so.aware(res.expires_at) == so.aware(o.expires_at)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "nuevo"
    # otro comprobante: el tope sigue contando desde el primero (no se estira) y un segundo rechazo no da otras 24 h
    fin = so.aware(o.expires_at)
    client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("s2.png", PNG + b"q", "image/png")})
    db_session.refresh(o)
    assert o.status == "en_revision" and so.aware(o.expires_at) == fin
    o.expires_at = res.expires_at = now + timedelta(hours=1)
    db_session.commit()
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "nuevo"}).status_code == 200
    db_session.refresh(o)
    assert so.aware(o.expires_at) < now + timedelta(hours=2)
    assert [h.get("key") for h in o.history].count(sinv.GRACE_NOTE) == 1


# ---------- tarjeta: nunca se cancela sin preguntarle a ONVO ----------
TARDE = 25 * 60  # minutos: pasado el margen de la tarjeta (store_orders.CARD_GRACE = 24 h)


def _card_shop(client, db, fake_onvo, code, name, qty=6):
    fake_onvo.connect(client)
    _publish(client)
    _pagos(client, tarjeta=True)
    main, _ = _whs(client)
    p = _prod(client, code, name)
    _in(client, p["id"], main["id"], qty)
    _cfg(client, cancel_unpaid_on_expiry=True)
    return p


def _card(client, db, p) -> Order:
    return _order(db, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])


def _wait_aged(db, o, minutes):
    """La ultima consulta sin respuesta definitiva fue hace `minutes` (espera creciente de store_orders)."""
    when = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    o.history = [{**h, "checked_at": when} if h.get("key") == so.ONVO_WAIT_NOTE else h for h in o.history]
    db.commit()


def test_tarjeta_pagada_en_onvo_sin_webhook_queda_pagada(client, auth, db_session, fake_onvo):
    p = _card_shop(client, db_session, fake_onvo, "CIC-5", "Kit de cámaras")
    pagado = _card(client, db_session, p)
    assert pagado.payment_ref == "cs_1"
    fake_onvo.pay("cs_1")  # cobrado en ONVO; el webhook nunca llega y el comprador no vuelve
    _vencer(db_session, pagado, TARDE)
    sinv.expire_holds(db_session)
    db_session.refresh(pagado)
    assert pagado.status == "pagado" and pagado.payment_ref == "pi_cs_1"

    # ONVO caido: no se cancela, y el resto de sus pedidos con tarjeta ni se consulta en esa corrida
    caido, otro = _card(client, db_session, p), _card(client, db_session, p)
    _vencer(db_session, caido, TARDE)
    _vencer(db_session, otro, TARDE)
    fake_onvo.fail_get = 1
    sinv.expire_holds(db_session)
    db_session.refresh(caido)
    db_session.refresh(otro)
    assert caido.status == otro.status == "nuevo" and so.has_note(caido, so.ONVO_WAIT_NOTE) and not so.has_note(otro, so.ONVO_WAIT_NOTE)
    # espera creciente: la corrida siguiente (5 min despues) no le vuelve a preguntar por el caido
    fake_onvo.fail_get = 1  # si se consultara, fallaria otra vez y contaria otro intento
    sinv.expire_holds(db_session)  # el otro si se consulta (ONVO falla: sigue nuevo)
    db_session.refresh(caido)
    assert caido.status == "nuevo" and so._wait_entry(caido)["checks"] == 1
    # pasada la espera, ONVO responde que no cobro: se cancela y el cliente recibe el correo
    _wait_aged(db_session, caido, 20)
    sinv.expire_holds(db_session)
    db_session.refresh(caido)
    assert caido.status == "cancelado" and len(_order_mails(db_session, caido, "Tu pedido")) == 1

    # pago en proceso (esperando al banco): tampoco se cancela
    proceso = _card(client, db_session, p)
    sid = proceso.payment_ref
    fake_onvo.sessions[sid]["paymentIntentId"] = "pi_proceso"
    fake_onvo.intents["pi_proceso"] = {"id": "pi_proceso", "status": "processing", "amount": 1, "currency": "CRC"}
    _vencer(db_session, proceso, TARDE)
    sinv.expire_holds(db_session)
    db_session.refresh(proceso)
    assert proceso.status == "nuevo"


def test_tarjeta_no_se_cancela_dentro_del_margen_y_un_pago_tardio_se_aplica(client, auth, db_session, fake_onvo):
    """El pedido con tarjeta vence a los 30 min (lo apartado vuelve a la venta), pero el enlace de ONVO sigue abierto:
    dentro del margen no se cancela ni se consulta a ONVO, y si el comprador termina de pagar, el pago se aplica."""
    p = _card_shop(client, db_session, fake_onvo, "CIC-15", "Lector RFID")
    o = _card(client, db_session, p)
    _vencer(db_session, o, 60)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "nuevo" and so.has_note(o, sinv.EXPIRED_NOTE) and not so.has_note(o, so.ONVO_WAIT_NOTE)
    fake_onvo.pay(o.payment_ref)  # el comprador termino de llenar la tarjeta despues del plazo
    assert tienda.sync_card_orders(db_session) == 1
    db_session.refresh(o)
    assert o.status == "pagado" and _order_mails(db_session, o, "Tu pedido") == []

    # 3-D Secure sin completar pasado el margen: es un abandono, se cancela
    abandono = _card(client, db_session, p)
    fake_onvo.sessions[abandono.payment_ref]["paymentIntentId"] = "pi_3ds"
    fake_onvo.intents["pi_3ds"] = {"id": "pi_3ds", "status": "requires_action", "amount": 1, "currency": "CRC"}
    _vencer(db_session, abandono, TARDE)
    sinv.expire_holds(db_session)
    db_session.refresh(abandono)
    assert abandono.status == "cancelado"


def test_tarjeta_pagada_en_una_sesion_anterior_no_se_cancela(client, auth, db_session, fake_onvo):
    """Cada "Pagar con tarjeta" de Mi pedido abre otra sesion: el cliente pago en la primera y abandono la segunda.
    Antes de cancelar (y en la consulta automatica) se revisan TODAS las sesiones del pedido."""
    p = _card_shop(client, db_session, fake_onvo, "CIC-16", "Cerradura biométrica")
    r = _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)")
    o = _order(db_session, r.json()["number"])
    primera = o.payment_ref
    assert client.post(f"{SLUG}/orders/{r.json()['track_token']}/card").status_code == 200
    db_session.refresh(o)
    assert o.payment_ref != primera
    fake_onvo.pay(primera)
    _vencer(db_session, o, TARDE)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "pagado" and o.payment_ref == f"pi_{primera}"
    assert _order_mails(db_session, o, "Tu pedido") == []

    # la consulta automatica tambien mira las sesiones anteriores
    r2 = _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)")
    o2 = _order(db_session, r2.json()["number"])
    primera2 = o2.payment_ref
    client.post(f"{SLUG}/orders/{r2.json()['track_token']}/card")
    fake_onvo.pay(primera2)
    assert tienda.sync_card_orders(db_session) == 1
    db_session.refresh(o2)
    assert o2.status == "pagado"


def test_tarjeta_cobrada_sin_aplicar_avisa_una_vez_y_no_se_reconsulta(client, auth, db_session, fake_onvo):
    """ONVO dice "pagada" pero el pago no se aplica solo (sesion con metadata de otro pedido): nota con clave, UN
    aviso al equipo, sin cancelar, sin la nota falsa de "sin pago" y sin volver a consultarle a ONVO."""
    p = _card_shop(client, db_session, fake_onvo, "CIC-17", "Sirena exterior")
    o = _card(client, db_session, p)
    fake_onvo.pay(o.payment_ref)
    fake_onvo.sessions[o.payment_ref]["metadata"]["order_id"] = str(o.id + 999)
    _vencer(db_session, o, TARDE)
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "nuevo" and so.has_note(o, so.ONVO_REVIEW_NOTE) and not so.has_note(o, sinv.EXPIRED_NOTE)
    avisos = _order_mails(db_session, o, "Revisar pago ONVO")
    assert avisos and len({m.subject for m in avisos}) == 1
    fake_onvo.fail_get = 5  # si se le volviera a consultar, se veria
    sinv.expire_holds(db_session)
    assert tienda.sync_card_orders(db_session) == 0
    assert fake_onvo.fail_get == 5 and len(_order_mails(db_session, o, "Revisar pago ONVO")) == len(avisos)
    db_session.refresh(o)
    assert o.status == "nuevo"


def test_cancelar_al_vencer_con_onvo_lento_igual_lleva_correo(client, auth, db_session, fake_onvo):
    """El "viejo" (cancelar sin correo) se cuenta desde que se encendio la cancelacion automatica: un pedido con
    tarjeta que espero dias porque ONVO no respondia recibe igual el correo para rehacer la compra."""
    p = _card_shop(client, db_session, fake_onvo, "CIC-18", "Panel de alarma")
    t = db_session.get(Tenant, auth["tenant"]["id"])
    st = dict(t.settings)
    st["store_inventory"] = {**st["store_inventory"], "cancel_unpaid_since": (datetime.now(UTC) - timedelta(days=10)).isoformat()}
    t.settings = st
    db_session.commit()
    o = _card(client, db_session, p)
    _vencer(db_session, o, 3 * 24 * 60)  # vencio hace 3 dias; ONVO no respondio hasta hoy
    so._onvo_wait(o, datetime.now(UTC) - timedelta(days=2))
    db_session.commit()
    sinv.expire_holds(db_session)
    db_session.refresh(o)
    assert o.status == "cancelado" and len(_order_mails(db_session, o, "Tu pedido")) == 1


def test_consulta_a_onvo_cada_15_min_aplica_el_pago(client, auth, db_session, fake_onvo):
    fake_onvo.connect(client)
    _publish(client)
    _pagos(client, tarjeta=True)
    main, _ = _whs(client)
    p = _prod(client, "CIC-6", "Lector de huella")
    _in(client, p["id"], main["id"], 2)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)").json()["number"])
    assert tienda.sync_card_orders(db_session) == 0  # sin pagar: nada
    db_session.refresh(o)
    assert so._wait_entry(o, so.ONVO_SYNC_NOTE)["checks"] == 1
    fake_onvo.pay(o.payment_ref)
    assert tienda.sync_card_orders(db_session) == 0  # espera creciente: la corrida siguiente no le vuelve a preguntar
    _sync_aged(db_session, o, 16)
    assert tienda.sync_card_orders(db_session) == 1
    db_session.refresh(o)
    assert o.status == "pagado" and o.paid_at
    assert any(h.get("status") == "pagado" and "consulta automática" in (h.get("note") or "") for h in o.history)
    assert tienda.sync_card_orders(db_session) == 0  # ya no se consulta


def _sync_aged(db, o, minutes):
    """La ultima consulta automatica a ONVO fue hace `minutes`."""
    when = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    o.history = [{**h, "checked_at": when} if h.get("key") == so.ONVO_SYNC_NOTE else h for h in o.history]
    db.commit()


def _gets(monkeypatch):
    """Sesiones que se le consultan a ONVO (sobre el FakeOnvo ya instalado)."""
    from app.providers.payments import OnvoAdapter

    seen: list[str] = []
    orig = OnvoAdapter.get_checkout
    monkeypatch.setattr(OnvoAdapter, "get_checkout", lambda self, sid: (seen.append(sid), orig(self, sid))[1])
    return seen


def test_pagar_con_tarjeta_tiene_tope_de_sesiones_por_pedido(client, auth, db_session, fake_onvo):
    """Cada "Pagar con tarjeta" abre una sesion en ONVO que despues se consulta: limite por pedido (aunque cambie la
    IP) y tope de so.MAX_CARD_SESSIONS en total, contando la del checkout."""
    from app.core.ratelimit import order_card_limiter

    p = _card_shop(client, db_session, fake_onvo, "CIC-19", "Botón de pánico")
    r = _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)")
    card = f"{SLUG}/orders/{r.json()['track_token']}/card"
    for _ in range(3):
        assert client.post(card).status_code == 200
    assert client.post(card).status_code == 429  # 4 en 10 min para el mismo pedido
    order_card_limiter.reset()  # pasaron los 10 minutos
    assert client.post(card).status_code == 200  # la quinta sesion del pedido
    res = client.post(card)
    assert res.status_code == 409 and "varias páginas de pago" in res.json()["detail"]
    o = _order(db_session, r.json()["number"])
    assert so.card_sessions(db_session, o) == so.MAX_CARD_SESSIONS == len(fake_onvo.created)


def test_consulta_automatica_acotada(client, auth, db_session, fake_onvo, monkeypatch):
    """La consulta automatica no se dispara con pedidos abandonados: sesion vigente y las de las ultimas 24 h,
    espera creciente por pedido, tope por corrida (primero los consultados hace mas tiempo), corte por empresa cuando
    ONVO no responde y tiempo maximo por corrida."""
    p = _card_shop(client, db_session, fake_onvo, "CIC-20", "Sensor de humo", qty=10)
    seen = _gets(monkeypatch)

    # una sesion de hace mas de 24 h (que no es la vigente) ya no se consulta en cada corrida
    from app.models import AuditLog

    r = _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta (ONVO)")
    vieja = _order(db_session, r.json()["number"])
    primera = vieja.payment_ref
    client.post(f"{SLUG}/orders/{r.json()['track_token']}/card")
    db_session.refresh(vieja)
    for a in db_session.scalars(select(AuditLog).where(AuditLog.action == "card_intent", AuditLog.entity_id == vieja.id)):
        if (a.diff or {}).get("intent") == primera:
            a.at = datetime.now(UTC) - timedelta(hours=30)
    db_session.commit()
    assert tienda.sync_card_orders(db_session) == 0
    assert seen == [vieja.payment_ref]
    seen.clear()

    # tope por corrida: primero los nunca consultados, despues los consultados hace mas tiempo
    a, b = _card(client, db_session, p), _card(client, db_session, p)
    monkeypatch.setattr(tienda, "SYNC_PER_RUN", 1)
    _sync_aged(db_session, vieja, 60 * 7)
    assert tienda.sync_card_orders(db_session) == 0 and seen == [a.payment_ref]
    assert tienda.sync_card_orders(db_session) == 0 and seen == [a.payment_ref, b.payment_ref]
    assert tienda.sync_card_orders(db_session) == 0 and seen[-1] == vieja.payment_ref  # ya le tocaba de nuevo
    seen.clear()
    assert tienda.sync_card_orders(db_session) == 0 and seen == []  # todos esperan: ninguna llamada a ONVO
    monkeypatch.setattr(tienda, "SYNC_PER_RUN", 100)

    # ONVO no responde: cuenta el intento y el resto de la empresa espera a la proxima corrida
    c, d = _card(client, db_session, p), _card(client, db_session, p)
    sc, sd = c.payment_ref, d.payment_ref
    fake_onvo.fail_get = 1
    assert tienda.sync_card_orders(db_session) == 0
    assert seen == [sc] and fake_onvo.fail_get == 0
    db_session.refresh(c)
    assert so._wait_entry(c, so.ONVO_SYNC_NOTE)["checks"] == 1
    fake_onvo.pay(sd)
    assert tienda.sync_card_orders(db_session) == 1 and seen == [sc, sd]  # c espera
    db_session.refresh(d)
    assert d.status == "pagado"

    # tiempo maximo por corrida agotado: no se consulta nada mas
    e = _card(client, db_session, p)
    seen.clear()
    assert tienda.sync_card_orders(db_session, budget=0) == 0 and seen == []
    assert tienda.sync_card_orders(db_session) == 0 and seen == [e.payment_ref]


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


def test_correos_automaticos_llevan_dedupe_key_y_respetan_pausa_y_tienda(client, auth, db_session, monkeypatch):
    """Regla 6: el recordatorio y el "venció sin pago" llevan dedupe_key (cuando la outbox de 1.2 la recibe) y respetan
    la pausa del cliente; una tienda sin publicar no le manda al comprador ni el recordatorio ni el vencido."""
    from app.models import Customer
    from app.services import mail

    llaves: list[str | None] = []
    real = mail.queue_email

    def con_dedupe(db, tenant, to, subject, html, entity, entity_id, attachments=None, files=None, dedupe_key=None):
        llaves.append(dedupe_key)
        return real(db, tenant, to, subject, html, entity, entity_id, attachments, files)

    monkeypatch.setattr(mail, "queue_email", con_dedupe)  # como queda queue_email con el item 1.2
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "CIC-22", "Teclado de alarma")
    _in(client, p["id"], main["id"], 10)
    _cfg(client, cancel_unpaid_on_expiry=True)
    a = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    now = datetime.now(UTC)
    a.created_at, a.expires_at = now - timedelta(hours=30), now + timedelta(hours=2)
    db_session.commit()
    llaves.clear()
    assert tienda.payment_reminders(db_session, now) == 1
    assert llaves == [f"tienda-rem:{a.id}"]
    _vencer(db_session, a)
    sinv.expire_holds(db_session)
    assert llaves[-1] == f"tienda-venc:{a.id}"

    # cliente con la pausa: vencido sin correo
    b = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    cb = db_session.get(Customer, b.customer_id)  # referencia viva: el atributo no es columna (llega con 1.3)
    cb.no_reminders = True
    _vencer(db_session, b)
    sinv.expire_holds(db_session)
    db_session.refresh(b)
    assert b.status == "cancelado" and [m for m in _order_mails(db_session, b, "Tu pedido") if "venció" in m.subject] == []
    assert any("pausados" in (h.get("note") or "") for h in b.history)
    cb.no_reminders = False

    # tienda sin publicar: ni recordatorio ni vencido (el enlace del pedido daria 404)
    c = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    assert client.put("/store", json={"published": False}).status_code == 200
    c.created_at, c.expires_at = now - timedelta(hours=30), now + timedelta(hours=2)
    db_session.commit()
    assert tienda.payment_reminders(db_session, now) == 0
    _vencer(db_session, c)
    sinv.expire_holds(db_session)
    db_session.refresh(c)
    assert c.status == "cancelado" and [m for m in _order_mails(db_session, c, "Tu pedido")] == []
    assert any("no está publicada" in (h.get("note") or "") for h in c.history)


# ---------- alerta del webhook de ONVO ----------
def test_webhook_onvo_alerta_al_admin_una_vez_por_dia_de_costa_rica(client, auth, db_session, fake_onvo, monkeypatch):
    import json

    from app.services import onvo

    tid = auth["tenant"]["id"]
    alertas = lambda: db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity.like("onvo_webhook%")).order_by(EmailOutbox.id)).all()  # noqa: E731
    # sin ONVO conectado, un 401 es una sonda a la URL: sin aviso; empresa inexistente: tampoco
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_x"}).status_code == 401
    assert fake_onvo.send(client, tid + 99, "checkout-session.succeeded", {"id": "cs_x"}).status_code == 401
    assert alertas() == []

    fake_onvo.connect(client)
    t0 = datetime(2026, 10, 20, 23, 30, tzinfo=CR)  # 05:30 UTC del 21/10
    monkeypatch.setattr(onvo, "_cr_now", lambda: t0)
    # con ONVO conectado, una sonda sin la forma de ONVO (sin secreto, otro secreto u otro cuerpo) tampoco avisa
    evento = json.dumps({"type": "checkout-session.succeeded", "data": {"id": "cs_x"}}).encode()
    hdr = {"Content-Type": "application/json"}
    assert client.post(f"/webhooks/onvo/{tid}", content=evento, headers=hdr).status_code == 401
    assert client.post(f"/webhooks/onvo/{tid}", content=evento, headers={**hdr, "X-Webhook-Secret": "x" * 40}).status_code == 401
    assert fake_onvo.send(client, tid, "ping", {"id": "1"}, secret="webhook_secret_" + "q" * 24).status_code == 401
    assert alertas() == []
    # con la forma de ONVO (el secreto viejo): aviso, encolado para el worker de correo (no se entrega en la peticion)
    malo = "webhook_secret_" + "z" * 24
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_x"}, secret=malo).status_code == 401
    primeras = alertas()
    assert primeras and all("secreto" in a.subject and a.entity == "onvo_webhook_401" and a.status == "pendiente" for a in primeras)
    for a in primeras:
        a.created_at = t0.astimezone(UTC)  # el aviso salio a esa hora
    db_session.commit()
    # otro 401 el mismo dia de Costa Rica (23:50): nada nuevo; un 503 tiene su propio cupo (un 401 falso no lo tapa)
    t1 = datetime(2026, 10, 20, 23, 50, tzinfo=CR)
    monkeypatch.setattr(onvo, "_cr_now", lambda: t1)
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_y"}, secret=malo).status_code == 401
    assert len(alertas()) == len(primeras)
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_no_existe"}).status_code == 503
    del503 = alertas()[len(primeras) :]
    assert del503 and all("no se pudo confirmar" in a.subject and a.entity == "onvo_webhook_503" for a in del503)
    for a in del503:
        a.created_at = t1.astimezone(UTC)
    db_session.commit()
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_no_existe_2"}).status_code == 503
    assert len(alertas()) == len(primeras) + len(del503)
    # 00:10 del dia siguiente en Costa Rica (06:10 UTC: el MISMO dia UTC que el primer aviso): aviso nuevo
    monkeypatch.setattr(onvo, "_cr_now", lambda: datetime(2026, 10, 21, 0, 10, tzinfo=CR))
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_z"}).status_code == 503
    nuevas = alertas()[len(primeras) + len(del503) :]
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


def _cash(client, lines):
    body = {
        "lines": lines,
        "contact": {"name": "Ana Vega", "email": "ana@ejemplo.com"},
        "payment_method": "efectivo",
        "delivery": {"type": "retiro"},
        "shipping_method": "Retiro en tienda",
    }
    r = client.post(f"{SLUG}/checkout", json=body)
    assert r.status_code == 201, r.text
    return r


def test_efectivo_al_retirar_activa_la_compra_cuando_el_equipo_lo_avanza(client, auth, db_session):
    """El efectivo se paga al retirar: que el equipo avance el pedido es la decision de comprar lo bajo pedido. El aviso
    no dice "PAGADO" (todavia no se cobro) y lo propio deja de vencer hasta el retiro."""
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _bajo_pedido(client, db_session, "CIC-10", "Cerradura inteligente", qty=3)
    _in(client, p["id"], main["id"], 1)  # 1 propio + 1 bajo pedido
    o = _order(db_session, _cash(client, [{"product_id": p["id"], "quantity": 2}]).json()["number"])
    assert o.expires_at is None  # el efectivo no tiene plazo para pagar
    assert _req(db_session, o).status == "reservada" and _purchase_mails(db_session) == []
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "listo"}).status_code == 200
    assert _req(db_session, o).status == "borrador"
    avisos = _purchase_mails(db_session)
    assert len({m.subject for m in avisos}) == 1
    assert not avisos[0].subject.startswith("PAGADO") and "efectivo" in avisos[0].subject and "todavía no está cobrado" in avisos[0].html
    res = db_session.scalar(select(StockReservation).where(StockReservation.order_id == o.id))
    db_session.refresh(res)
    assert res.status == "confirmada" and res.expires_at is None  # "Listo para retiro": lo propio no vuelve a la venta
    sinv.expire_holds(db_session)
    db_session.refresh(res)
    assert res.status == "confirmada"


def test_efectivo_sin_plazo_no_se_cancela_y_lo_bajo_pedido_vence_con_su_reserva(client, auth, db_session):
    """El efectivo (se cobra al retirar) no tiene plazo para pagar: con la cancelacion encendida, al vencer lo apartado
    no se cancela ni recibe "venció sin pago"; queda UNA nota. Lo bajo pedido de un pedido en efectivo aparta lo del
    proveedor solo mientras duraria su reserva propia (hold_hours_manual), no para siempre."""
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "CIC-19", "Botón de pánico")
    _in(client, p["id"], main["id"], 1)
    q = _bajo_pedido(client, db_session, "CIC-20", "Detector de humo", qty=1)
    _cfg(client, cancel_unpaid_on_expiry=True)
    propio = _order(db_session, _cash(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    bajo = _order(db_session, _cash(client, [{"product_id": q["id"], "quantity": 1}]).json()["number"])
    _vencer(db_session, propio)  # vence su reserva (el pedido no tiene plazo: _vencer le pone uno, se quita)
    propio.expires_at = None
    db_session.commit()
    sinv.expire_holds(db_session)
    sinv.expire_holds(db_session)
    db_session.refresh(propio)
    assert propio.status == "nuevo" and [h.get("key") for h in propio.history].count(sinv.HOLD_NOTE) == 1
    assert not so.has_note(propio, sinv.EXPIRED_NOTE)
    assert [m for m in _order_mails(db_session, propio, "Tu pedido") if "venció" in m.subject] == []
    assert _avail(client, p["id"]).json()[str(p["id"])]["disponible"] is True  # lo propio volvio a la venta
    # todo bajo pedido: aparta lo del proveedor mientras duraria la reserva (48 h desde el pedido), despues no
    assert _avail(client, q["id"]).json()[str(q["id"])]["disponible"] is False
    assert client.get(f"/store/orders/{bajo.id}/inventory").json()["estado"] == "espera_pago"
    bajo.created_at = datetime.now(UTC) - timedelta(hours=49)
    db_session.commit()
    assert _avail(client, q["id"]).json()[str(q["id"])]["estado"] == "bajo_pedido"
    assert client.get(f"/store/orders/{bajo.id}/inventory").json()["estado"] == "vencido"
    sinv.expire_holds(db_session)
    db_session.refresh(bajo)
    assert bajo.status == "nuevo" and _req(db_session, bajo).status == "reservada"


def test_solicitud_reservada_se_descarta_cancelando_el_pedido_y_la_papelera_vuelve_al_pagar(client, auth, db_session):
    """Una solicitud "Espera el pago" no se cambia a mano (dejaria un pedido pagado sin su compra): el 409 dice como
    descartarla segun el ajuste. Si alguien la mando a la papelera y el pedido se paga, vuelve de la papelera."""
    _publish(client)
    _pagos(client)
    p = _bajo_pedido(client, db_session, "CIC-21", "Switch 24 puertos", qty=3)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    req = _req(db_session, o)
    r = client.patch(f"/purchase-requests/{req.id}", json={"status": "cancelada"})
    assert r.status_code == 409 and o.number in r.json()["detail"] and "cancelá ese pedido" in r.json()["detail"] and "solo al vencer" not in r.json()["detail"]
    _cfg(client, cancel_unpaid_on_expiry=True)
    assert "solo al vencer" in client.patch(f"/purchase-requests/{req.id}", json={"status": "cancelada"}).json()["detail"]
    req.trashed_at = datetime.now(UTC)
    db_session.commit()
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "pagado"}).status_code == 200
    db_session.refresh(req)
    assert req.status == "borrador" and req.trashed_at is None
    assert len({m.subject for m in _purchase_mails(db_session)}) == 1


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
