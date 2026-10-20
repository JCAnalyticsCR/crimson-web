"""Cola fiscal "Por emitir" (plan maestro 1.7): mientras el modo fiscal es "ninguno", lo pagado o enviado espera a que
contabilidad lo emita en Fygaro y lo marque "emitida fuera". Lo marcado sale de la cola y la plataforma nunca lo vuelve a
emitir (tampoco lo importado del historico). Correo diario a contabilidad, una vez por dia de Costa Rica."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.hacienda_utils import role_headers

SLUG = "/public/store/crimson"


@pytest.fixture
def correos(monkeypatch):
    """El proveedor de correo acepta todo (como Resend con llave): lo enviado queda "enviado"."""
    from app.services import mail

    enviados = []

    def deliver(m, files=None):
        enviados.append({"to": m.to, "subject": m.subject, "html": m.html})
        m.status = "enviado"

    monkeypatch.setattr(mail, "deliver", deliver)
    return enviados


def _factura(client, headers=None):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    r = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def _pagar(client, inv, amount=None, headers=None):
    r = client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "amount": str(amount or inv["total"])}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def _cola(client, headers=None):
    r = client.get("/fiscal/queue", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(client, headers=None):
    return [x["id"] for x in _cola(client, headers)["items"]]


def _marcar(client, inv_id, number="00100001010000004321", when=None, headers=None):
    return client.post(f"/fiscal/queue/{inv_id}/external", json={"number": number, "date": (when or date.today()).isoformat()}, headers=headers or {})


def test_que_entra_a_por_emitir(client, auth, db_session, correos):
    from app.models import PaymentLink

    borrador = _factura(client)
    pagada = _pagar(client, _factura(client))
    parcial = _pagar(client, _factura(client), amount=Decimal("1000"))
    enviada = _factura(client)
    assert client.post(f"/invoices/{enviada['id']}/email", json={"to": "cliente@ejemplo.com"}).json()["sent"]
    anulada = _pagar(client, _factura(client))
    assert client.post(f"/invoices/{anulada['id']}/void").status_code == 200
    vencida_sin_enviar, vencida_con_enlace = _factura(client), _factura(client)
    client.post(f"/invoices/{vencida_con_enlace['id']}/payment-link")
    from app.models import Invoice

    for x in (vencida_sin_enviar, vencida_con_enlace):
        db_session.get(Invoice, x["id"]).status = "vencida"  # lo que hace sales.mark_overdue
    db_session.commit()
    assert db_session.query(PaymentLink).filter(PaymentLink.invoice_id == vencida_sin_enviar["id"]).count() == 0

    cola = _cola(client)
    ids = [x["id"] for x in cola["items"]]
    # un borrador sin enviar ni cobrar no se emite; una anulada tampoco
    assert set(ids) == {pagada["id"], parcial["id"], enviada["id"], vencida_con_enlace["id"]}
    assert borrador["id"] not in ids and anulada["id"] not in ids and vencida_sin_enviar["id"] not in ids
    assert cola["total"] == 4 and cola["mode"] == "ninguno" and cola["emitidas_fuera_mes"] == 0 and cola["can_mark"]
    assert ids == sorted(ids)  # la mas vieja primero (misma fecha: por numero)
    assert all(x["days_waiting"] == 0 for x in cola["items"]) and cola["oldest_issue_date"]


def test_venta_de_la_tienda_aparece_en_por_emitir(client, auth, db_session):
    from app.models import Invoice

    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    cfg = {
        "methods": {"sinpe": {"active": True, "phone": "8888-7777", "holder": "Crimson SA"}, "efectivo": {"active": False}},
        "auto_invoice": True,
        "pickup": {"address": "Oficinas Crimson, Escazú", "hours": "L-V 8-17"},
    }
    assert client.put("/store/checkout", json=cfg).status_code == 200
    prod = client.get(f"{SLUG}/products").json()[0]
    body = {
        "lines": [{"product_id": prod["id"], "quantity": 1}],
        "contact": {"name": "Ana Vega", "email": "ana@correo.com", "phone": "8888-0000", "id_type": "fisica", "id_number": "1-1111-0111"},
        "delivery": {"type": "retiro"},
        "payment_method": "sinpe",
        "accept_terms": True,
    }
    assert client.post(f"{SLUG}/checkout", json=body).status_code == 201
    oid = client.get("/store-orders").json()["items"][0]["id"]
    paid = client.post(f"/store-orders/{oid}/status", json={"status": "pagado", "payment_ref": "SINPE 123"}).json()
    inv = db_session.get(Invoice, paid["invoice_id"])
    assert inv.status == "pagada" and inv.einvoice_status == "sin_emitir"
    assert inv.id in _ids(client)
    # al marcarla sale de la cola y emit() la rechaza
    assert _marcar(client, inv.id).status_code == 200
    assert inv.id not in _ids(client)
    r = client.post(f"/invoices/{inv.id}/emit")
    assert r.status_code == 409 and "fuera de la plataforma" in r.json()["detail"] and "00100001010000004321" in r.json()["detail"]


def test_marcar_emitida_en_fygaro_y_emit_la_rechaza(client, auth, db_session):
    from app.models import AuditLog, EInvoiceDocument, Invoice

    inv = _pagar(client, _factura(client))
    r = _marcar(client, inv["id"], number="  FE-0000123 ", when=date.today() - timedelta(days=1))
    assert r.status_code == 200, r.text
    assert r.json()["einvoice_status"] == "emitida_fuera" and r.json()["external_number"] == "FE-0000123"
    got = client.get(f"/invoices/{inv['id']}").json()
    assert got["einvoice_status"] == "emitida_fuera" and got["external_number"] == "FE-0000123"
    assert got["external_date"] == (date.today() - timedelta(days=1)).isoformat()
    log = db_session.query(AuditLog).filter(AuditLog.action == "emitida_fuera", AuditLog.entity_id == inv["id"]).one()
    assert log.diff["antes"] == "sin_emitir" and log.diff["numero"] == "FE-0000123"
    cola = _cola(client)
    assert inv["id"] not in [x["id"] for x in cola["items"]] and cola["emitidas_fuera_mes"] == (1 if (date.today() - timedelta(days=1)).month == date.today().month else 0)

    # nunca se reemite: ni en simulado ni en pruebas ni en produccion (la guarda va antes del modo)
    for prov in ("none", "sandbox"):
        client.put("/settings", json={"einvoice_provider": prov})
        r = client.post(f"/invoices/{inv['id']}/emit")
        assert r.status_code == 409 and "n.º FE-0000123" in r.json()["detail"], r.text
    assert db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).count() == 0
    # dos veces no: ya esta marcada
    assert _marcar(client, inv["id"], number="OTRO-1").status_code == 409
    # ni se edita aunque no tenga pagos (el comprobante vive en Fygaro)
    sin_pagos = _factura(client)
    assert _marcar(client, sin_pagos["id"], number="FE-0000124").status_code == 200
    body = {"customer_id": got["customer_id"], "lines": [{"name": "Otra cosa", "quantity": 1, "unit_price": 10}]}
    r = client.put(f"/invoices/{sin_pagos['id']}", json=body)
    assert r.status_code == 409 and db_session.get(Invoice, sin_pagos["id"]).total == Decimal(sin_pagos["total"])


def test_importada_tampoco_se_emite(client, auth, db_session):
    from app.models import Invoice

    inv = _factura(client)
    db_session.get(Invoice, inv["id"]).einvoice_status = "importada"
    db_session.commit()
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 409 and "histórico" in r.json()["detail"]
    assert _marcar(client, inv["id"]).status_code == 409  # ya esta emitida fuera


def test_validaciones_de_la_marca(client, auth, db_session):
    from app.models import EInvoiceDocument, Invoice

    a, b = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    assert _marcar(client, a["id"], when=date.today() + timedelta(days=2)).status_code == 422  # fecha futura
    assert _marcar(client, a["id"], number="<script>").status_code == 422
    assert _marcar(client, a["id"], number="FE-77").status_code == 200
    r = _marcar(client, b["id"], number="FE-77")  # el mismo numero en otra factura: casi seguro un error
    assert r.status_code == 409 and a["number"] in r.json()["detail"]
    assert client.post("/fiscal/queue/999999/external", json={"number": "X1", "date": date.today().isoformat()}).status_code == 404
    # anulada
    c = _factura(client)
    client.post(f"/invoices/{c['id']}/void")
    assert _marcar(client, c["id"], number="FE-78").status_code == 409

    # lo emitido en SIMULADO (sin validez fiscal, plan 0.5) si se marca: la venta real se emitio en Fygaro
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    s = _pagar(client, _factura(client))
    assert client.post(f"/invoices/{s['id']}/emit").json()["status"] == "aceptada"
    client.put("/settings", json={"einvoice_provider": "none"})
    assert _marcar(client, s["id"], number="FE-79").status_code == 200
    assert client.post(f"/invoices/{s['id']}/credit-note", json={"reason": "No aplica"}).status_code == 409  # la NC va en Fygaro

    # un comprobante de PRODUCCION vivo nunca se tapa con la marca; uno en camino a Hacienda tampoco
    for status, einv in (("aceptada", "aceptada"), ("por_enviar", "pendiente")):
        p = _pagar(client, _factura(client))
        db_session.add(EInvoiceDocument(tenant_id=auth["tenant"]["id"], invoice_id=p["id"], doc_type="FE", consecutive="00100001010000000999", provider="hacienda", status=status, mode="produccion", attempts=1))
        db_session.get(Invoice, p["id"]).einvoice_status = einv
        db_session.commit()
        assert _marcar(client, p["id"], number=f"FE-{status}").status_code == 409


def test_permisos_y_alcance(client, auth, db_session):
    tid = auth["tenant"]["id"]
    conta = role_headers(client, db_session, tid, "conta.cola@ejemplo.com", "contabilidad")
    lectura = role_headers(client, db_session, tid, "lectura.cola@ejemplo.com", "lectura")
    vend = role_headers(client, db_session, tid, "vend.cola@ejemplo.com", "ventas")
    del_admin = _pagar(client, _factura(client))
    del_vendedor = _pagar(client, _factura(client, headers=vend), headers=vend)

    # contabilidad ve toda la cola y marca (duena de la emision); solo lectura ve pero no marca
    assert set(_ids(client, conta)) == {del_admin["id"], del_vendedor["id"]}
    assert _cola(client, lectura)["can_mark"] is False
    assert _marcar(client, del_admin["id"], number="FE-1", headers=lectura).status_code == 403
    # el vendedor solo ve y marca lo suyo; lo ajeno no existe
    assert _ids(client, vend) == [del_vendedor["id"]]
    assert _marcar(client, del_admin["id"], number="FE-1", headers=vend).status_code == 404
    assert _marcar(client, del_admin["id"], number="FE-1", headers=conta).status_code == 200
    # deshacer una marca equivocada: solo quien configura la empresa
    assert client.delete(f"/fiscal/queue/{del_admin['id']}/external", headers=conta).status_code == 403
    r = client.delete(f"/fiscal/queue/{del_admin['id']}/external")
    assert r.status_code == 200 and r.json()["einvoice_status"] == "sin_emitir" and r.json()["external_number"] is None
    assert del_admin["id"] in _ids(client, conta)
    assert client.delete(f"/fiscal/queue/{del_admin['id']}/external").status_code == 409


def test_emitidas_fuera_este_mes_en_hora_de_costa_rica(client, auth, db_session):
    from app.services import fiscal_queue as fq

    tid = auth["tenant"]["id"]
    a, b = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    from app.models import Invoice

    for inv, num, when in ((a, "FE-31", date(2026, 10, 31)), (b, "FE-01", date(2026, 11, 1))):
        x = db_session.get(Invoice, inv["id"])
        x.einvoice_status, x.external_number, x.external_date = "emitida_fuera", num, when
    db_session.commit()
    # 31/10 a las 21:00 en Costa Rica (ya 1/11 en UTC): todavia es octubre
    assert fq.external_month_count(db_session, tid, date(2026, 10, 31)) == 1
    assert fq.external_month_count(db_session, tid, date(2026, 11, 1)) == 1
    assert fq.external_month_count(db_session, tid, date(2026, 12, 15)) == 0
    assert fq.month_of(date(2026, 12, 9)) == (date(2026, 12, 1), date(2027, 1, 1))


def test_correo_diario_una_vez_por_dia_de_costa_rica(client, auth, db_session, correos, monkeypatch):
    from app.core.config import settings
    from app.models import EmailOutbox, TenantUser
    from app.tasks.fiscal import send_queue_digest

    monkeypatch.setattr(settings, "public_base_url", "https://panel.ejemplo.com")
    tid = auth["tenant"]["id"]
    # sin nada por emitir no sale nada
    assert send_queue_digest(db_session) == 0
    inv = _pagar(client, _factura(client))
    role_headers(client, db_session, tid, "conta.diario@ejemplo.com", "contabilidad")
    correos.clear()
    # 21:00 en Costa Rica del 20/10 = 03:00 UTC del 21/10: el correo es del 20 (no de manana)
    noche = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    assert send_queue_digest(db_session, now=noche) == 1
    assert [c["to"] for c in correos] == ["conta.diario@ejemplo.com"]  # contabilidad, no el admin
    html = correos[0]["html"]
    assert f"https://panel.ejemplo.com/facturas/{inv['id']}" in html and inv["number"] in html
    assert "https://panel.ejemplo.com/facturas?estado=por_emitir" in html and "Fygaro" in html
    assert "Facturas por emitir · 2026-10-20" in html and correos[0]["subject"].startswith("Por emitir: 1 factura")
    row = db_session.query(EmailOutbox).filter(EmailOutbox.entity == "fiscal_queue").one()
    assert row.entity_id == 20261020
    # doble disparo del beat el mismo dia de Costa Rica: no repite
    assert send_queue_digest(db_session, now=noche + timedelta(hours=2)) == 0
    # al dia siguiente (CR) si sale otra vez
    assert send_queue_digest(db_session, now=datetime(2026, 10, 21, 14, 30, tzinfo=UTC)) == 1
    # sin nadie de contabilidad activo, le llega al administrador (alguien es dueno del aviso)
    db_session.query(TenantUser).filter(TenantUser.role_code == "contabilidad").update({"active": False})
    db_session.commit()
    correos.clear()
    assert send_queue_digest(db_session, now=datetime(2026, 10, 22, 14, 30, tzinfo=UTC)) == 1
    assert correos[0]["to"] == "admin@crimsonapp.com"
    # marcada, la cola queda vacia y no hay correo
    assert _marcar(client, inv["id"]).status_code == 200
    assert send_queue_digest(db_session, now=datetime(2026, 10, 23, 14, 30, tzinfo=UTC)) == 0


def test_tarea_registrada_en_el_worker():
    from celery import Celery

    import app.tasks.fiscal  # noqa: F401

    w = Celery("prueba-cola")
    w.loader.import_default_modules()
    assert "fiscal.por_emitir_diario" in w.tasks
