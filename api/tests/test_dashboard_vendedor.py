"""Inicio del vendedor (pregunta de Andres 21/10): ve lo que facturo el (solo sus facturas) y sus comisiones; nunca los
cobros ni los totales de la empresa."""

from tests.test_session4 import _user_with_role


def test_vendedor_ve_solo_lo_suyo(client, auth, db_session):
    from app.models import User

    tid = auth["tenant"]["id"]
    pw = _user_with_role(db_session, tid, "vendedora.cobros@ejemplo.com", "ventas")
    vid = db_session.query(User).filter(User.email == "vendedora.cobros@ejemplo.com").one().id
    c = client.get("/customers").json()["items"][0]
    suya = client.post("/invoices", json={"customer_id": c["id"], "seller_id": vid, "lines": [{"name": "Suya", "quantity": 1, "unit_price": 1000, "tax_rate": 0}]}).json()
    ajena = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Ajena", "quantity": 1, "unit_price": 500, "tax_rate": 0}]}).json()
    # el cobro lo registra el admin (como caja o contabilidad), no la vendedora
    assert client.post(f"/invoices/{suya['id']}/payments", json={"method": "sinpe", "amount": 1000}).status_code == 201
    assert client.post(f"/invoices/{ajena['id']}/payments", json={"method": "sinpe", "amount": 500}).status_code == 201
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "vendedora.cobros@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    d = client.get("/dashboard", headers={"Authorization": f"Bearer {tok}"}).json()
    assert d["pagos"] is None  # los cobros son de caja y contabilidad
    assert float(d["facturado"]["mes"]) == 1000  # solo su factura; la ajena no suma
    assert d.get("gerencia") is None  # sin numeros de toda la empresa
