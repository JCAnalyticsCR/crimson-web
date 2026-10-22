"""Reunion 21/10: cliente obligatorio al convertir (5), foto del producto en la busqueda (1 y 8), clausulas compactas en
el PDF (17) y numero visible de la factura = consecutivo fiscal en produccion (14)."""

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


def test_aceptada_sin_cliente_elige_el_cliente_al_convertir(client, auth):
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


# ---------- 14. numero visible = consecutivo fiscal al emitir en produccion ----------
def _factura_db(client, db_session):
    from app.models import Invoice, Tenant

    c = _cust(client)
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [LINEA]}).json()
    row = db_session.get(Invoice, inv["id"])
    return row, db_session.get(Tenant, row.tenant_id)


def test_produccion_numera_la_factura_con_el_consecutivo(client, auth, db_session):
    from app.models import AuditLog, BillingGroup
    from app.services.einvoice import number_from_fiscal

    row, t = _factura_db(client, db_session)
    provisional = row.number
    number_from_fiscal(db_session, t, None, row, "FE", "00100001010000000087", "produccion")
    assert row.number == "FEC-000087" and provisional != row.number
    g = db_session.query(BillingGroup).filter(BillingGroup.tenant_id == t.id, BillingGroup.doc_type == "FE", BillingGroup.is_default).one()
    assert g.current >= 87  # el siguiente borrador no reusa el 87
    assert db_session.query(AuditLog).filter(AuditLog.action == "renumber", AuditLog.entity_id == row.id).count() == 1
    db_session.commit()
    sig = client.post("/invoices", json={"customer_id": _cust(client)["id"], "lines": [LINEA]}).json()
    assert sig["number"] == "FEC-000088"


def test_pruebas_simulado_o_ajuste_apagado_no_tocan_el_numero(client, auth, db_session):
    from app.services.einvoice import number_from_fiscal

    row, t = _factura_db(client, db_session)
    n = row.number
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


def test_tiquete_usa_el_prefijo_del_tiquete(client, auth, db_session):
    from app.services.einvoice import number_from_fiscal

    row, t = _factura_db(client, db_session)
    number_from_fiscal(db_session, t, None, row, "TE", "00100001040000000012", "produccion")
    assert row.number == "TE-000012"
