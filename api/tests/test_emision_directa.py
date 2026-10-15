"""Emision directa (FE/TE/NC) en el ambiente de pruebas con FakeHacienda: consecutivo fiscal al emitir (T01, nunca F01),
fecha de emision, emit() sin commit ni red (la tienda dentro del webhook), envio despues del commit, reintentos,
entrega al cliente (XML + PDF + respuesta de Hacienda) y NC que anula solo al aceptarse."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.hacienda_utils import FakeHacienda, modo_pruebas

SLUG = "/public/store/crimson"


def _factura(client, issue_date=None, qty=1):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    body = {"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": qty}]}
    if issue_date:
        body["issue_date"] = issue_date
    r = client.post("/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def pruebas(client, auth, monkeypatch, p12_empresa):
    fake = FakeHacienda(sequence=["aceptado"]).install(monkeypatch)
    modo_pruebas(client, *p12_empresa)
    return fake


@pytest.fixture
def correos(monkeypatch):
    from app.services import mail

    enviados = []

    def deliver(m, files=None):
        enviados.append({"to": m.to, "subject": m.subject, "files": dict(files or [])})
        m.status = "enviado"

    monkeypatch.setattr(mail, "deliver", deliver)
    return enviados


def test_emitir_consultar_y_consecutivo_t01(client, auth, db_session, pruebas, correos):
    from app.models import BillingGroup, EInvoiceDocument

    inv = _factura(client)
    assert inv["consecutive"] is None
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pendiente" and r.json()["doc_status"] == "enviado" and r.json()["mode"] == "pruebas"
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    assert doc.consecutive == "00100001010000000001" and doc.status == "enviado" and len(pruebas.posts) == 1
    assert db_session.query(BillingGroup).filter(BillingGroup.doc_type == "F01").one().current == 0  # no gasta la numeracion real
    r = client.post(f"/invoices/{inv['id']}/einvoice/refresh").json()
    assert r["status"] == "aceptada"
    got = client.get(f"/invoices/{inv['id']}").json()
    assert got["einvoice_status"] == "aceptada" and got["clave"] == doc.clave and got["consecutive"] == doc.consecutive
    # entrega (art. 18): en PRUEBAS va a los admin (no al cliente) con el XML firmado exacto; al aceptarse, la respuesta
    db_session.refresh(doc)
    cust_email = client.get("/customers").json()["items"][0]["email"]
    assert correos and all(c["to"] != cust_email for c in correos)
    xml_mail = next(c for c in correos if f"{doc.clave}.xml" in c["files"])
    assert xml_mail["files"][f"{doc.clave}.xml"] == doc.xml_document.encode("utf-8") and "PRUEBAS" in xml_mail["subject"]
    resp = [c for c in correos if f"{doc.clave}-respuesta-hacienda.xml" in c["files"]]
    assert len(resp) == 1
    from app.services.hacienda_sync import send_pending

    send_pending(db_session)  # el worker no la manda dos veces
    assert len([c for c in correos if f"{doc.clave}-respuesta-hacienda.xml" in c["files"]]) == 1
    # el correo de la factura lleva tambien el XML y la respuesta
    correos.clear()
    client.post(f"/invoices/{inv['id']}/email", json={"to": "cliente@ejemplo.com"})
    assert f"{doc.clave}.xml" in correos[-1]["files"] and f"{doc.clave}-respuesta-hacienda.xml" in correos[-1]["files"]
    # el PDF dice que es del ambiente de pruebas
    assert "Ambiente de pruebas de Hacienda" in client.get(f"/invoices/{inv['id']}/html").text


def test_reemitir_tras_rechazo_toma_consecutivo_nuevo(client, auth, db_session, pruebas):
    from app.models import AuditLog, EInvoiceDocument

    inv = _factura(client)
    client.post(f"/invoices/{inv['id']}/emit")
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    pruebas.final[doc.clave] = "rechazado"
    assert client.post(f"/invoices/{inv['id']}/einvoice/refresh").json()["status"] == "rechazada"
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 200, r.text
    docs = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).order_by(EInvoiceDocument.id).all()
    assert len(docs) == 2 and docs[1].consecutive != docs[0].consecutive and docs[1].clave != docs[0].clave
    assert db_session.query(AuditLog).filter(AuditLog.action == "einvoice_renumber").count() == 1


def test_sin_credenciales_perfil_incompleto_y_doble_emit(client, auth, db_session):
    from app.models import EInvoiceDocument, FiscalProfile

    client.put("/settings/hacienda/mode", json={"mode": "pruebas"})
    inv = _factura(client)
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 409 and "credenciales" in r.json()["detail"]
    prof = db_session.query(FiscalProfile).one()
    prof.district = None
    db_session.commit()
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 422 and "distrito" in r.json()["detail"]
    assert db_session.query(EInvoiceDocument).count() == 0


def test_red_caida_queda_pendiente_y_el_worker_la_manda(client, auth, db_session, pruebas):
    from app.models import EInvoiceDocument
    from app.services.hacienda_sync import send_pending

    pruebas.post_plan = ["timeout_lost"]
    inv = _factura(client, issue_date=(date.today() - timedelta(days=3)).isoformat())
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 200 and r.json()["status"] == "pendiente" and "reintentará" in r.json()["message"].lower()
    assert client.post(f"/invoices/{inv['id']}/emit").status_code == 409  # doble emit: un solo comprobante
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    assert doc.status == "por_enviar" and doc.next_attempt_at is not None
    got = client.get(f"/invoices/{inv['id']}").json()
    assert got["issue_date"] == doc.emitted_at.date().isoformat()  # N4: la fecha es la de emision
    doc.next_attempt_at = None
    db_session.commit()
    send_pending(db_session)
    db_session.refresh(doc)
    assert doc.status == "enviado" and doc.attempts == 2


def test_modo_cambiado_no_manda_a_produccion(client, auth, db_session, pruebas, monkeypatch):
    from app.models import EInvoiceDocument
    from app.services import hacienda_config
    from app.services.hacienda_sync import send_einvoice

    pruebas.post_plan = ["timeout_lost"]
    inv = _factura(client)
    client.post(f"/invoices/{inv['id']}/emit")
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    doc.next_attempt_at = None
    db_session.commit()
    monkeypatch.setattr(hacienda_config, "hacienda_mode", lambda db, t: "produccion")
    send_einvoice(db_session, doc.id)
    db_session.refresh(doc)
    assert doc.status == "error" and "Cambió el modo" in doc.last_error and pruebas.production_hits == []


def test_nc_directa_anula_y_devuelve_inventario_solo_al_aceptarse(client, auth, db_session, pruebas):
    from app.models import AuditLog, EInvoiceDocument

    inv = _factura(client, qty=2)
    client.post(f"/invoices/{inv['id']}/emit")
    client.post(f"/invoices/{inv['id']}/einvoice/refresh")
    stock0 = sum(Decimal(str(lv["quantity"])) for lv in client.get("/stock").json()["levels"])
    r = client.post(f"/invoices/{inv['id']}/credit-note", json={"reason": "Error en cantidad"})
    assert r.status_code == 200 and r.json()["status"] == "pendiente", r.text
    assert client.get(f"/invoices/{inv['id']}").json()["status"] != "anulada"
    assert sum(Decimal(str(lv["quantity"])) for lv in client.get("/stock").json()["levels"]) == stock0
    nc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.doc_type == "NC").one()
    assert nc.consecutive[8:10] == "03" and nc.reference_clave
    client.post(f"/invoices/{inv['id']}/einvoice/refresh")
    assert client.get(f"/invoices/{inv['id']}").json()["status"] == "anulada"
    stock1 = sum(Decimal(str(lv["quantity"])) for lv in client.get("/stock").json()["levels"])
    assert stock1 == stock0 + 2
    from app.services.hacienda_sync import _void_after_nc
    from app.models import Invoice

    i = db_session.get(Invoice, inv["id"])
    i.status = "creado"  # aunque alguien la reabriera, el inventario no vuelve dos veces
    _void_after_nc(db_session, i, nc, None)
    db_session.commit()
    assert sum(Decimal(str(lv["quantity"])) for lv in client.get("/stock").json()["levels"]) == stock1
    assert db_session.query(AuditLog).filter(AuditLog.action == "nc_restock").count() == 1


def test_tienda_pagada_en_pruebas_no_rompe_el_webhook(client, auth, db_session, pruebas, monkeypatch):
    from app.models import EInvoiceDocument, Invoice
    from app.services import store_inventory
    from app.services.hacienda_sync import send_pending
    from tests.test_tienda_checkout import _body, _shop

    monkeypatch.setattr(store_inventory, "deduct_for_order_invoice", lambda db, o, inv, uid=None: None)
    prods = _shop(client, auto_invoice=True)
    body = _body(prods[0]["id"], delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    o = client.post(f"{SLUG}/checkout", json=body)
    assert o.status_code == 201, o.text
    oid = client.get("/store-orders").json()["items"][0]["id"]
    paid = client.post(f"/store-orders/{oid}/status", json={"status": "pagado", "payment_ref": "SINPE 1"}).json()
    assert paid["status"] == "pagado" and paid["invoice_id"]
    notas = [h["note"] for h in paid["history"] if h.get("kind") == "nota"]
    assert any("pendiente de Hacienda" in n for n in notas), notas
    inv = db_session.get(Invoice, paid["invoice_id"])
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv.id).one()
    assert doc.status == "por_enviar" and inv.einvoice_status == "pendiente" and not pruebas.posts  # la tienda no envia en linea
    send_pending(db_session)
    db_session.refresh(doc)
    assert doc.status == "enviado" and len(pruebas.posts) == 1


def test_simulado_sigue_igual_con_franja(client, auth):
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    inv = _factura(client)
    r = client.post(f"/invoices/{inv['id']}/emit").json()
    assert r["status"] == "aceptada" and r["mode"] == "simulado"
    got = client.get(f"/invoices/{inv['id']}").json()
    assert got["consecutive"] == "00100001010000000001" and got["einvoice_status"] == "aceptada"
    html = client.get(f"/invoices/{inv['id']}/html").text
    assert "Comprobante simulado · sin validez fiscal" in html and "Clave (simulada)" in html


def test_error_tras_un_post_no_es_rechazo_y_sigue_en_el_iva(client, auth, db_session, pruebas):
    """§6.5.4 paso 5: un 403 o 72 h sin respuesta NO son un rechazo de Hacienda (pudo haberlo aceptado): la factura queda
    "error", sigue en el IVA debito y la alerta diaria lo avisa. "rechazada" queda solo para el rechazo real."""
    from datetime import UTC, datetime

    from app.models import EInvoiceDocument, Invoice, Tenant
    from app.services.hacienda_sync import refresh_einvoice
    from app.services.hacienda_tasks import alerts_for
    from app.services.reports import iva_breakdown

    tid = auth["tenant"]["id"]
    pruebas.post_plan = [(403, {})]  # credenciales de otro ambiente
    inv = _factura(client)
    assert client.post(f"/invoices/{inv['id']}/emit").status_code == 200
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    assert doc.status == "error" and doc.attempts == 1 and "credenciales" in doc.last_error
    assert client.get(f"/invoices/{inv['id']}").json()["einvoice_status"] == "error"
    i = db_session.get(Invoice, inv["id"])
    tax, day = Decimal(str(i.tax_total)), i.issue_date
    con_error = Decimal(str(iva_breakdown(db_session, tid, day, day)["debito_crc"]))
    i.einvoice_status = "rechazada"  # comparacion: una rechazada de verdad SI sale del debito
    rechazada = Decimal(str(iva_breakdown(db_session, tid, day, day)["debito_crc"]))
    db_session.rollback()
    assert con_error - rechazada == tax.quantize(Decimal("0.01")) and tax > 0
    # 72 h sin respuesta final: error (no rechazada) y sale en la alerta diaria
    inv2 = _factura(client)
    client.post(f"/invoices/{inv2['id']}/emit")
    doc2 = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv2["id"]).one()
    assert doc2.status == "enviado"
    pruebas.get_plan[doc2.clave] = ["procesando"]
    doc2.sent_at = datetime.now(UTC) - timedelta(hours=73)
    db_session.commit()
    refresh_einvoice(db_session, doc2.id)
    db_session.refresh(doc2)
    assert doc2.status == "error" and "72 h" in doc2.last_error and doc2.resolved_at is not None
    assert client.get(f"/invoices/{inv2['id']}").json()["einvoice_status"] == "error"
    alertas = alerts_for(db_session, db_session.get(Tenant, tid))
    assert any(doc2.consecutive in a and "72 h" in a for a in alertas), alertas
    # siguen en la lista de pendientes de atencion (junto con las rechazadas)
    ids = {x["id"] for x in client.get("/invoices", params={"pendiente": "documentos_rechazados"}).json()["items"]}
    assert {inv["id"], inv2["id"]} <= ids


def test_entrega_que_falla_no_se_marca_y_se_reintenta_con_adjuntos(client, auth, db_session, pruebas, monkeypatch):
    """Art. 18: si el correo con el XML falla (proveedor de correo caido), la entrega no queda marcada y el correo fallido
    no lo toma mail.retry_outbox (que lo mandaria SIN adjuntos): lo reintenta hacienda.send_pending con el XML y el PDF."""
    from datetime import UTC, datetime

    from app.models import EInvoiceDocument, EmailOutbox
    from app.services import mail
    from app.services.hacienda_sync import send_pending

    salidas, caido = [], {"v": True}

    def deliver(m, files=None):
        salidas.append({"to": m.to, "files": dict(files or [])})
        m.status, m.error = ("error", "Resend caído") if caido["v"] else ("enviado", None)

    monkeypatch.setattr(mail, "deliver", deliver)
    inv = _factura(client)
    assert client.post(f"/invoices/{inv['id']}/emit").status_code == 200
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    assert doc.status == "enviado" and doc.delivered_at is None and salidas
    fallidos = db_session.query(EmailOutbox).filter(EmailOutbox.entity == "invoice", EmailOutbox.entity_id == inv["id"]).all()
    assert fallidos and all(m.status == "fallido" for m in fallidos)  # fuera de retry_outbox (pendiente|error)
    caido["v"] = False
    n = len(salidas)
    send_pending(db_session)  # recien fallo: espera RETRY_WAIT (no llena la bitacora cada 2 min)
    assert len(salidas) == n
    for m in fallidos:
        m.created_at = datetime.now(UTC) - timedelta(minutes=20)
    db_session.commit()
    send_pending(db_session)
    db_session.refresh(doc)
    assert doc.delivered_at is not None
    otra = salidas[n]
    assert otra["files"][f"{doc.clave}.xml"] == doc.xml_document.encode("utf-8")  # los bytes firmados, no un correo vacio
    k = len(salidas)
    send_pending(db_session)  # ya entregado: no sale otra vez
    assert len([s for s in salidas[k:] if f"{doc.clave}.xml" in s["files"]]) == 0


def test_lo_que_hacienda_no_recibio_no_se_entrega_al_cliente(client, auth, db_session, pruebas, correos):
    """Un 400 en el primer POST deja el comprobante en error (se reemite con otra clave): su XML no sale al cliente."""
    from app.models import EInvoiceDocument

    pruebas.post_plan = [(400, {"X-Error-Cause": "Firma invalida"})]
    inv = _factura(client)
    assert client.post(f"/invoices/{inv['id']}/emit").status_code == 200
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    assert doc.status == "error" and doc.delivered_at is None
    assert not [c for c in correos if f"{doc.clave}.xml" in c["files"]]


def test_entrega_a_un_correo_malo_tiene_tope_y_no_deja_sin_turno_a_las_demas(client, auth, db_session, pruebas, monkeypatch):
    """Un correo que el proveedor rechaza siempre (4xx): la espera se duplica y, tras MAX_FAILS, la etapa queda sin
    entregar (fuera de la cola del worker, sin rehacer el PDF ni otro EmailOutbox) con UN aviso al admin. El lote va en
    orden y lo que espera no gasta el cupo; una aceptada sin XML de respuesta no se queda en la cola."""
    from datetime import UTC, datetime

    from app.models import EInvoiceDocument, EmailOutbox
    from app.services import einvoice_delivery as ed
    from app.services import mail
    from app.services.hacienda_sync import send_pending

    salidas, malos = [], set()

    def deliver(m, files=None):
        salidas.append({"inv": m.entity_id, "subject": m.subject, "files": bool(files)})
        m.status, m.error = ("error", "422: correo inválido") if files and m.entity_id in malos else ("enviado", None)

    monkeypatch.setattr(mail, "deliver", deliver)
    a = _factura(client)
    malos.add(a["id"])
    assert client.post(f"/invoices/{a['id']}/emit").status_code == 200
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == a["id"]).one()

    def fallos():
        return db_session.query(EmailOutbox).filter(EmailOutbox.entity_id == a["id"], EmailOutbox.status == "fallido").all()

    def envejecer(delta):
        for m in fallos():
            m.created_at = datetime.now(UTC) - delta
        db_session.commit()

    assert len(fallos()) == 1
    envejecer(timedelta(minutes=20))
    send_pending(db_session)
    assert len(fallos()) == 2
    envejecer(timedelta(minutes=20))
    send_pending(db_session)
    assert len(fallos()) == 2  # con 2 fallos la espera ya es de 30 min
    # otro comprobante pendiente sale aunque el lote sea de 1: el que espera no gasta el cupo
    b = _factura(client)
    assert client.post(f"/invoices/{b['id']}/emit").status_code == 200
    doc_b = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == b["id"]).one()
    db_session.query(EmailOutbox).filter(EmailOutbox.entity_id == b["id"]).delete()
    doc_b.delivered_at = None
    db_session.commit()
    send_pending(db_session, limit=1)
    db_session.refresh(doc_b)
    assert doc_b.delivered_at is not None
    # una aceptada sin XML de respuesta no tiene nada que entregar: no vuelve a la cola en cada vuelta
    doc_b.status, doc_b.xml_response, doc_b.hacienda_delivered_at = "aceptada", None, None
    db_session.commit()
    llamadas, real = [], ed.deliver
    monkeypatch.setattr(ed, "deliver", lambda db, did, stage: llamadas.append(did) or real(db, did, stage))
    send_pending(db_session)
    assert doc_b.id not in llamadas
    # tope: tras MAX_FAILS fallos ya no se insiste
    for _ in range(ed.MAX_FAILS):
        if len(fallos()) >= ed.MAX_FAILS:
            break
        envejecer(timedelta(hours=5))
        send_pending(db_session)
    assert len(fallos()) == ed.MAX_FAILS
    envejecer(timedelta(hours=5))
    n = len(salidas)
    send_pending(db_session)
    db_session.refresh(doc)
    assert doc.delivery_failed_at is not None and doc.delivered_at is None
    assert not [s for s in salidas[n:] if s["files"]]
    avisos = [s for s in salidas[n:] if s["subject"].startswith("Entrega fallida")]
    assert avisos and all(s["inv"] == a["id"] for s in avisos)
    envejecer(timedelta(hours=5))
    k = len(salidas)
    send_pending(db_session)
    assert len(salidas) == k and len(fallos()) == ed.MAX_FAILS  # ni otro intento ni otro aviso
    x = [d for d in client.get(f"/invoices/{a['id']}/xml").json() if d["id"] == doc.id][0]
    assert x["delivery_failed_at"] and not x["delivered_at"]
