"""Contabilidad operativa (plan maestro 1.7): el contador envia facturas, crea enlaces de pago, emite o marca lo emitido
en Fygaro y registra cobros sin pedirle nada al admin, pero no crea ventas. El aviso "listo para facturacion" le llega
solo a quien puede facturar el proyecto."""

from decimal import Decimal

import pytest

from tests.hacienda_utils import role_headers


@pytest.fixture
def correos(monkeypatch):
    from app.services import mail

    enviados = []

    def deliver(m, files=None):
        enviados.append({"to": m.to, "subject": m.subject, "html": m.html})
        m.status = "enviado"

    monkeypatch.setattr(mail, "deliver", deliver)
    return enviados


def _factura(client):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    r = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_matriz_contabilidad():
    from app.core.deps import ROLE_PERMISSIONS

    conta = ROLE_PERMISSIONS["contabilidad"]
    assert "enviar" in conta["sales"] and "crear" in conta["payments"]
    assert "crear" not in conta["sales"] and "editar" not in conta["sales"] and "anular" not in conta["sales"]
    # "Marcar emitida en Fygaro" saca la venta de la cola: solo contabilidad (y el admin, "*")
    assert [r for r, perms in ROLE_PERMISSIONS.items() if "emitir_fuera" in perms.get("sales", [])] == ["contabilidad"]


def test_contabilidad_envia_enlaza_y_cobra_sin_403(client, auth, db_session, correos):
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.ops@ejemplo.com", "contabilidad")
    inv = _factura(client)
    # enviar por correo (con PDF y enlace de pago): queda "enviada"
    r = client.post(f"/invoices/{inv['id']}/email", json={"to": "cliente@ejemplo.com"}, headers=conta)
    assert r.status_code == 200 and r.json()["sent"], r.text
    assert client.get(f"/invoices/{inv['id']}", headers=conta).json()["status"] == "enviada"
    # enlace de pago para WhatsApp
    r = client.post(f"/invoices/{inv['id']}/payment-link", headers=conta)
    assert r.status_code == 200 and r.json()["url"]
    # registrar el cobro (SINPE, transferencia)
    r = client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": str(inv["total"]), "external_ref": "TRF-1"}, headers=conta)
    assert r.status_code == 201 and r.json()["status"] == "pagada" and Decimal(str(r.json()["balance"])) == 0
    # emitir: el permiso alcanza (en modo "ninguno" el modo es quien lo frena, no un 403)
    r = client.post(f"/invoices/{inv['id']}/emit", headers=conta)
    assert r.status_code == 502 and "Sin proveedor fiscal" in r.json()["detail"]
    # y marcarla emitida en Fygaro desde la cola (con la fecha de hoy en Costa Rica: el issue_date sale de la fecha de la
    # maquina y en un servidor en UTC, de 18:00 a 24:00 CR, ya es manana)
    from app.services.fiscal_queue import cr_today

    r = client.post(f"/fiscal/queue/{inv['id']}/external", json={"number": "FE-900", "date": cr_today().isoformat()}, headers=conta)
    assert r.status_code == 200 and r.json()["einvoice_status"] == "emitida_fuera"


def test_mensaje_del_correo_va_como_texto(client, auth, db_session, correos):
    """Quien envia escribe el mensaje: nunca entra como HTML (un enlace o un formulario saldria con la marca de la
    empresa junto al boton de pago)."""
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.mensaje@ejemplo.com", "contabilidad")
    inv = _factura(client)
    msg = 'Hola <a href="https://ejemplo.net/x">pague aqui</a>\nGracias & saludos'
    r = client.post(f"/invoices/{inv['id']}/email", json={"to": "cliente@ejemplo.com", "message": msg}, headers=conta)
    assert r.status_code == 200 and r.json()["sent"], r.text
    html = correos[-1]["html"]
    assert '<a href="https://ejemplo.net/x">' not in html
    assert "&lt;a href=&quot;https://ejemplo.net/x&quot;&gt;pague aqui&lt;/a&gt;" in html and "Gracias &amp; saludos" in html


def test_contabilidad_no_crea_ventas(client, auth, db_session):
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.ventas@ejemplo.com", "contabilidad")
    inv = _factura(client)
    cust = client.get("/customers").json()["items"][0]
    body = {"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 1000}]}
    assert client.post("/invoices", json=body, headers=conta).status_code == 403
    assert client.post("/quotes", json=body, headers=conta).status_code == 403
    assert client.put(f"/invoices/{inv['id']}", json=body, headers=conta).status_code == 403
    assert client.post(f"/invoices/{inv['id']}/void", headers=conta).status_code == 403
    q = client.post("/quotes", json=body).json()
    assert client.post(f"/quotes/{q['id']}/convert", headers=conta).status_code == 403
    assert client.get("/pos/catalog", headers=conta).status_code == 403  # el POS vende: es crear ventas


def test_roles_que_pueden_actuar(client, auth, db_session):
    from app.core.deps import roles_that_can
    from app.models import Role, Tenant

    tid = auth["tenant"]["id"]
    for email, role in (("c.act@ejemplo.com", "contabilidad"), ("v.act@ejemplo.com", "ventas"), ("s.act@ejemplo.com", "supervisor")):
        role_headers(client, db_session, tid, email, role)
    t = db_session.get(Tenant, tid)
    assert roles_that_can(db_session, t, ("sales", "crear"), ("projects", "ver_todo")) == ("admin",)
    assert roles_that_can(db_session, t, ("sales", "enviar"), ("sales", "ver_todo")) == ("admin", "contabilidad")
    # un rol personalizado con esos permisos tambien recibe el aviso
    db_session.add(Role(code="facturador", name="Facturador", permissions={"sales": ["ver", "crear"], "projects": ["ver", "ver_todo"]}))
    db_session.commit()
    role_headers(client, db_session, tid, "f.act@ejemplo.com", "facturador")
    assert roles_that_can(db_session, t, ("sales", "crear"), ("projects", "ver_todo")) == ("admin", "facturador")


def test_listo_para_facturacion_solo_a_quien_factura(client, auth, db_session):
    from app.models import EmailOutbox

    role_headers(client, db_session, auth["tenant"]["id"], "conta.proyecto@ejemplo.com", "contabilidad")
    prod = client.post("/products", json={"name": "Cámara de prueba", "code": "CAM-AV", "price": 100, "cost": 50}).json()
    w = next(x for x in client.get("/warehouses").json() if x["is_default"])
    client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "entrada", "quantity": 5})
    cust = client.get("/customers").json()["items"][0]
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": prod["id"], "quantity": 1}]}).json()
    pr = client.post(f"/quotes/{q['id']}/project", json={"site": "Oficina"}).json()
    oid = client.get(f"/projects/{pr['id']}").json()["orders"][0]["id"]
    client.post(f"/work-orders/{oid}/start", json={})
    r = client.post(f"/work-orders/{oid}/finish", json={"materials": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "planned": 1}]})
    assert r.status_code == 200, r.text
    avisos = db_session.query(EmailOutbox).filter(EmailOutbox.subject.like("%listo para facturación%")).all()
    assert [a.to for a in avisos] == ["admin@crimsonapp.com"]  # contabilidad no puede facturar el proyecto: no se le avisa


def test_contabilidad_envia_facturas_no_cotizaciones(client, auth, db_session, correos):
    """sales.enviar es para mandar FACTURAS: una cotizacion la envia quien cotiza. Enviarla congela su version y pasa la
    oportunidad del vendedor a "enviada"; por correo tampoco se salta la aprobacion de un descuento."""
    from app.models import Quote, QuoteVersion

    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.cotiza@ejemplo.com", "contabilidad")
    cust = client.get("/customers").json()["items"][0]
    body = {"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]}
    q = client.post("/quotes", json=body).json()
    assert client.post(f"/quotes/{q['id']}/email", json={"to": "cliente@ejemplo.com"}, headers=conta).status_code == 403
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=conta).status_code == 403
    assert client.get(f"/quotes/{q['id']}").json()["status"] == "creado" and correos == []
    assert db_session.query(QuoteVersion).filter(QuoteVersion.quote_id == q["id"]).count() == 0
    # con un descuento esperando al admin no sale por correo (ni a una direccion cualquiera): la misma guarda que
    # "Marcar enviada"
    db_session.get(Quote, q["id"]).status = "por_aprobar"
    db_session.commit()
    r = client.post(f"/quotes/{q['id']}/email", json={"to": "otro@ejemplo.com"})
    assert r.status_code == 409 and "aprobación" in r.json()["detail"] and correos == []
    for estado in ("convertida", "anulada"):
        db_session.get(Quote, q["id"]).status = estado
        db_session.commit()
        assert client.post(f"/quotes/{q['id']}/email", json={"to": "cliente@ejemplo.com"}).status_code == 409
    # quien cotiza la sigue enviando
    db_session.get(Quote, q["id"]).status = "creado"
    db_session.commit()
    r = client.post(f"/quotes/{q['id']}/email", json={"to": "cliente@ejemplo.com"})
    assert r.status_code == 200 and r.json()["sent"], r.text
