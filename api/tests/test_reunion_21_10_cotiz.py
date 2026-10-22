"""Reunion 21/10: cliente obligatorio al convertir (5), foto del producto en la busqueda (1 y 8), clausulas compactas en
el PDF (17) y numero visible de la factura = consecutivo fiscal en produccion (14)."""

import pytest
from fastapi import HTTPException

from app.services.render import parrafos

LINEA = {"name": "Instalacion", "quantity": 1, "unit_price": 1000, "tax_rate": 13}


def _cust(client):
    return client.get("/customers").json()["items"][0]


def _quote(client, customer_id=None):
    r = client.post("/quotes", json={"customer_id": customer_id, "lines": [LINEA]})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 5. cliente obligatorio al convertir; la factura hereda el de la cotizacion ----------
def test_sin_cliente_no_convierte_y_lo_dice(client, auth, db_session):
    from app.models import Invoice

    q = _quote(client)
    antes = db_session.query(Invoice).count()
    r = client.post(f"/quotes/{q['id']}/convert")
    assert r.status_code == 409 and "cliente" in r.json()["detail"]
    assert db_session.query(Invoice).count() == antes  # no gasto numero ni creo factura
    assert client.get(f"/quotes/{q['id']}").json()["status"] != "convertida"


def test_la_factura_hereda_el_cliente_de_la_cotizacion(client, auth):
    c = _cust(client)
    q = _quote(client, c["id"])
    inv = client.post(f"/quotes/{q['id']}/convert", json={"customer_id": 999999})  # ya tiene: el del cuerpo no manda
    assert inv.status_code == 201, inv.text
    assert inv.json()["customer_id"] == c["id"]


def test_aceptada_sin_cliente_elige_el_cliente_al_convertir(client, auth, db_session):
    from app.models import AuditLog

    c = _cust(client)
    q = _quote(client)
    client.post(f"/quotes/{q['id']}/send")
    r = client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Ana", "channel": "correo"})
    assert r.status_code == 200, r.text
    assert client.post(f"/quotes/{q['id']}/convert").status_code == 409
    assert client.post(f"/quotes/{q['id']}/convert", json={"customer_id": 999999}).status_code == 404  # cliente ajeno o inexistente
    inv = client.post(f"/quotes/{q['id']}/convert", json={"customer_id": c["id"]})
    assert inv.status_code == 201, inv.text
    assert inv.json()["customer_id"] == c["id"]
    assert client.get(f"/quotes/{q['id']}").json()["customer_id"] == c["id"]
    log = db_session.query(AuditLog).filter(AuditLog.entity == "quote", AuditLog.entity_id == q["id"], AuditLog.action == "update").order_by(AuditLog.id.desc()).first()
    assert log is not None and log.ip is not None  # quien eligio el cliente y desde donde


# ---------- 5. Proyectos -> Facturar: la factura hereda el cliente ----------
def _proyecto(client, db_session, sin_cliente_en_proyecto=False):
    from app.models import Project, Quote

    q = _quote(client, _cust(client)["id"])
    r = client.post(f"/quotes/{q['id']}/project", json={"site": "Sitio de prueba"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    db_session.get(Quote, q["id"]).customer_id = None  # una cotizacion de antes, sin cliente
    if sin_cliente_en_proyecto:
        db_session.get(Project, pid).customer_id = None
    db_session.commit()
    return q, db_session.get(Project, pid)


def test_proyecto_factura_con_el_cliente_del_proyecto(client, auth, db_session):
    from app.models import AuditLog, Invoice

    q, pr = _proyecto(client, db_session)
    assert pr.customer_id is not None
    r = client.post(f"/projects/{pr.id}/invoice")
    assert r.status_code == 201, r.text
    assert db_session.get(Invoice, r.json()["invoice_id"]).customer_id == pr.customer_id
    log = db_session.query(AuditLog).filter(AuditLog.entity == "quote", AuditLog.entity_id == q["id"], AuditLog.action == "update").order_by(AuditLog.id.desc()).first()
    assert log is not None and log.ip is not None


def test_proyecto_sin_cliente_no_factura(client, auth, db_session):
    from app.models import Invoice

    _, pr = _proyecto(client, db_session, sin_cliente_en_proyecto=True)
    antes = db_session.query(Invoice).count()
    r = client.post(f"/projects/{pr.id}/invoice")
    assert r.status_code == 409 and "cliente" in r.json()["detail"]
    assert db_session.query(Invoice).count() == antes


# ---------- 1 y 8. el buscador trae la foto del producto ----------
def test_busqueda_de_productos_trae_la_foto(client, auth, db_session):
    from app.models import Product

    pr = db_session.query(Product).filter(Product.active).first()
    pr.images = [{"url": "https://cdn.ejemplo.test/a.jpg"}, {"url": "https://cdn.ejemplo.test/principal.jpg", "main": True}]
    db_session.commit()
    items = client.get("/products", params={"q": pr.code, "limit": 8}).json()["items"]
    assert any(i["id"] == pr.id and len(i["images"]) == 2 for i in items)
    hits = client.get("/search", params={"q": pr.code}).json()
    prod = next(h for h in hits if h["kind"] == "producto" and h["id"] == pr.id)
    assert prod["img"] == "https://cdn.ejemplo.test/principal.jpg"


# ---------- 17. clausulas y notas compactas, sin cortar texto ----------
def test_parrafos_no_pierde_texto():
    assert parrafos("Uno\r\n\r\n\r\nDos\nsigue\n\n  \n Tres ") == ["Uno", "Dos\nsigue", "Tres"]
    assert parrafos(None) == [] and parrafos("") == []


def test_pdf_de_la_factura_con_clausulas_compactas(client, auth):
    largo = "\n\n\n".join(f"Clausula {i}: " + "texto de la condicion " * 12 for i in range(1, 9))
    assert client.put("/settings", json={"invoice_footer": largo}).status_code == 200
    c = _cust(client)
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [LINEA]}).json()
    html = client.get(f"/invoices/{inv['id']}/html").text
    assert 'class="legal"' in html and ".legal{" in html and "font-size:8px" in html
    for i in range(1, 9):
        assert f"Clausula {i}:" in html  # todo lo escrito sale
    assert html.count("<p>Clausula") == 8


def test_notas_de_la_factura_compactas_y_la_cotizacion_igual(client, auth):
    notas = "Garantia de 12 meses.\n\n\n\nEntrega en 5 dias.\n\n\nPago contra entrega."
    assert client.put("/settings", json={"invoice_footer": "Clausula A", "quote_footer": "Condicion de la cotizacion"}).status_code == 200
    c = _cust(client)
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [LINEA], "external_notes": notas}).json()
    html = client.get(f"/invoices/{inv['id']}/html").text
    bloque = html.split('class="notes notes--compact">')[1].split("</div>")[0]
    assert "\n\n" not in bloque  # sin las lineas en blanco repetidas
    for frase in ("Garantia de 12 meses.", "Entrega en 5 dias.", "Pago contra entrega."):
        assert f"<p>{frase}</p>" in bloque  # todo lo escrito sale
    q = _quote(client, c["id"])
    qhtml = client.get(f"/quotes/{q['id']}/html").text
    assert '<div class="legal">' not in qhtml and '<div class="notes notes--compact">' not in qhtml  # la cotizacion conserva su tamano
    assert "Condicion de la cotizacion" in qhtml


# ---------- 14. numero visible = consecutivo fiscal al emitir en produccion ----------
def _factura_db(client, db_session, **extra):
    from app.models import Invoice, Tenant

    c = _cust(client)
    r = client.post("/invoices", json={"customer_id": c["id"], "lines": [LINEA], **extra})
    assert r.status_code == 201, r.text
    row = db_session.get(Invoice, r.json()["id"])
    return row, db_session.get(Tenant, row.tenant_id)


@pytest.fixture
def produccion(client, auth, db_session, monkeypatch):
    """Produccion activa sin red ni firma real: el F01 sigue a Fygaro (ultimo usado 86) y el F04 al tiquete 11."""
    from app.models import Tenant
    from app.services import einvoice
    from app.services import hacienda_config as hc
    from app.services.sequences import ensure_fiscal_groups

    monkeypatch.setattr(hc, "production_on", lambda db, t: True)
    monkeypatch.setattr(hc, "hacienda_mode", lambda db, t: "produccion")
    monkeypatch.setattr(einvoice, "_check_direct_ready", lambda db, t, env: object())
    monkeypatch.setattr(einvoice, "_sign_direct", lambda *a, **k: b"<FacturaElectronica/>")
    t = db_session.get(Tenant, auth["tenant"]["id"])
    gs = ensure_fiscal_groups(db_session, t.id)
    gs["F01"].current, gs["F04"].current = 86, 11
    db_session.commit()
    return t


def _emit(db_session, t, row):
    from app.services import einvoice

    einvoice.emit(db_session, t, None, row)
    db_session.commit()
    db_session.refresh(row)
    return row.number


def _sin_repetidos(db_session, tid):
    from sqlalchemy import func

    from app.models import Invoice

    rep = db_session.query(Invoice.number).filter(Invoice.tenant_id == tid).group_by(Invoice.number).having(func.count() > 1).all()
    assert rep == [], rep


def test_produccion_borradores_provisionales_y_emision_fuera_de_orden(client, auth, db_session, produccion):
    from app.models import AuditLog

    a, t = _factura_db(client, db_session)
    b, _ = _factura_db(client, db_session)
    assert a.number.startswith("BOR-") and b.number.startswith("BOR-") and a.number != b.number  # no gastan FEC
    assert _emit(db_session, t, b) == "FEC-000087"  # se emite B primero
    assert _emit(db_session, t, a) == "FEC-000088"
    assert b.consecutive.endswith("0000000087") and a.consecutive.endswith("0000000088")  # el de 20 digitos, intacto
    c, _ = _factura_db(client, db_session)
    assert c.number.startswith("BOR-")  # el borrador siguiente no toma el 89 que la proxima emision va a dar
    _sin_repetidos(db_session, t.id)
    assert db_session.query(AuditLog).filter(AuditLog.action == "renumber", AuditLog.entity_id.in_([a.id, b.id])).count() == 2


def test_produccion_numero_provisional_se_avisa_y_no_sale_al_cliente(client, auth, db_session, produccion):
    """El portal sabe que el BOR- es provisional (y cual tomaria al emitir); el correo y el enlace de pago esperan a la
    emision, asi el cliente nunca recibe un numero que despues cambia."""
    a, t = _factura_db(client, db_session)
    out = client.get(f"/invoices/{a.id}").json()
    assert out["number"].startswith("BOR-") and out["number_provisional"] is True
    assert out["expected_number"] == "FEC-000087"  # sigue a Fygaro (ultimo usado 86)
    fila = next(i for i in client.get("/invoices").json()["items"] if i["id"] == a.id)
    assert fila["number_provisional"] is True
    r = client.post(f"/invoices/{a.id}/email", json={"to": "cliente@example.com"})
    assert r.status_code == 409 and "provisional" in r.json()["detail"]
    r = client.post(f"/invoices/{a.id}/payment-link")
    assert r.status_code == 409 and "emitila" in r.json()["detail"]
    db_session.refresh(a)
    assert a.sent_at is None and a.status == "creado"  # no quedo "enviada" ni por cobrar
    assert _emit(db_session, t, a) == "FEC-000087"
    out = client.get(f"/invoices/{a.id}").json()
    assert out["number_provisional"] is False and out["expected_number"] is None
    assert client.post(f"/invoices/{a.id}/payment-link").status_code == 200  # ya emitida: el enlace dice FEC-000087


def test_fuera_de_produccion_la_factura_no_es_provisional(client, auth, db_session):
    a, _ = _factura_db(client, db_session)
    out = client.get(f"/invoices/{a.id}").json()
    assert out["number"].startswith("FEC-") and out["number_provisional"] is False
    assert client.post(f"/invoices/{a.id}/payment-link").status_code == 200


def test_produccion_borrador_anulado_no_choca(client, auth, db_session, produccion):
    a, t = _factura_db(client, db_session)
    r = client.post(f"/invoices/{a.id}/void")
    assert r.status_code == 200, r.text
    b, _ = _factura_db(client, db_session)
    assert _emit(db_session, t, b) == "FEC-000087"
    db_session.refresh(a)
    assert a.number.startswith("BOR-") and a.status == "anulada"  # nunca se emitio: no tiene numero fiscal
    _sin_repetidos(db_session, t.id)


def test_produccion_numero_ocupado_toma_el_consecutivo_de_20(client, auth, db_session, produccion):
    from app.models import AuditLog

    viejo, t = _factura_db(client, db_session)
    viejo.number = "FEC-000087"  # p. ej. del historico importado o numerada antes de activar el ajuste
    db_session.commit()
    row, _ = _factura_db(client, db_session)
    assert _emit(db_session, t, row) == "00100001010000000087"
    assert db_session.query(AuditLog).filter(AuditLog.action == "renumber_conflict", AuditLog.entity_id == row.id).count() == 1
    _sin_repetidos(db_session, t.id)


def test_produccion_emision_que_falla_devuelve_el_numero_y_el_kardex_sigue_a_la_factura(client, auth, db_session, produccion, monkeypatch):
    from app.models import BillingGroup, Invoice, StockMovement
    from app.services import einvoice

    pr = client.post("/products", json={"name": "Camara de prueba", "code": "R2110-CAM", "price": 100, "cost": 50}).json()
    w = next(x for x in client.get("/warehouses").json() if x["is_default"])
    assert client.post("/stock/movements", json={"product_id": pr["id"], "warehouse_id": w["id"], "kind": "entrada", "quantity": 5}).status_code == 201
    q = client.post("/quotes", json={"customer_id": _cust(client)["id"], "lines": [{"product_id": pr["id"], "quantity": 2}]}).json()
    r = client.post(f"/quotes/{q['id']}/convert")  # convertir descuenta el inventario con el numero provisional
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    row = db_session.get(Invoice, iid)
    provisional = row.number
    assert provisional.startswith("BOR-")

    def movs():
        return [m.reference for m in db_session.query(StockMovement).filter(StockMovement.invoice_id == iid).all()]

    assert movs() == [provisional]

    def falla(*a, **k):
        raise HTTPException(409, "No se pudo firmar el comprobante: prueba")

    monkeypatch.setattr(einvoice, "_sign_direct", falla)
    with pytest.raises(HTTPException):
        einvoice.emit(db_session, produccion, None, row)
    db_session.commit()
    db_session.expire_all()
    f01 = db_session.query(BillingGroup).filter(BillingGroup.tenant_id == produccion.id, BillingGroup.doc_type == "F01").one()
    assert db_session.get(Invoice, iid).number == provisional and f01.current == 86 and movs() == [provisional]
    monkeypatch.setattr(einvoice, "_sign_direct", lambda *a, **k: b"<FacturaElectronica/>")
    assert _emit(db_session, produccion, db_session.get(Invoice, iid)) == "FEC-000087"
    assert movs() == ["FEC-000087"]  # el kardex dice el numero que tiene la factura


def test_tiquete_en_produccion_usa_el_prefijo_del_tiquete(client, auth, db_session, produccion):
    row, t = _factura_db(client, db_session)
    row.doc_type = "TE"
    db_session.commit()
    assert _emit(db_session, t, row) == "TE-000012"


def test_ajuste_apagado_en_produccion_numera_como_siempre(client, auth, db_session, produccion):
    row, t = _factura_db(client, db_session)
    assert row.number.startswith("BOR-")
    assert client.put("/settings", json={"invoice_number_fiscal": False}).status_code == 200
    db_session.refresh(t)
    nuevo, _ = _factura_db(client, db_session)
    assert nuevo.number.startswith("FEC-")  # apagado: el numero de siempre desde el principio
    assert _emit(db_session, t, row).startswith("FEC-")  # el que quedo provisional nunca sale emitido como BOR-
    _sin_repetidos(db_session, t.id)


def test_simulado_o_ajuste_apagado_no_usan_el_consecutivo(client, auth, db_session):
    from app.services.einvoice import number_from_fiscal

    row, t = _factura_db(client, db_session)
    n = row.number
    assert n.startswith("FEC-")  # sin produccion activa, el numero de siempre
    number_from_fiscal(db_session, t, None, row, "FE", "00100001010000000087", "pruebas")
    number_from_fiscal(db_session, t, None, row, "FE", "00100001010000000087", "simulado")
    assert row.number == n
    db_session.commit()
    r = client.put("/settings", json={"invoice_number_fiscal": False})
    assert r.status_code == 200 and r.json()["invoice_number_fiscal"] is False
    db_session.refresh(t)
    number_from_fiscal(db_session, t, None, row, "FE", "00100001010000000087", "produccion")
    assert row.number == n
    assert client.get("/settings").json()["invoice_number_fiscal"] is False
