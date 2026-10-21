"""Pedido 21/10: solo el administrador anula facturas (y cotizaciones); borrar una factura no existe para nadie."""

import pytest

from app.core.deps import ROLE_PERMISSIONS
from tests.test_session4 import _user_with_role


def test_ningun_rol_salvo_admin_puede_anular():
    for rol, perms in ROLE_PERMISSIONS.items():
        if rol == "admin":
            continue
        assert "anular" not in perms.get("sales", []), rol
        assert "*" not in perms.get("sales", []) and "*" not in perms, rol


@pytest.mark.parametrize("rol", ["ventas", "contabilidad", "caja", "supervisor"])
def test_anular_da_403_a_otros_roles(client, auth, db_session, rol):
    c = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 0}]}).json()
    q = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 0}]}).json()
    pw = _user_with_role(db_session, auth["tenant"]["id"], f"{rol}.anular@ejemplo.com", rol)
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": f"{rol}.anular@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"/invoices/{inv['id']}/void", headers=h).status_code == 403
    assert client.post(f"/invoices/{inv['id']}/credit-note", json={"reason": "prueba"}, headers=h).status_code == 403
    assert client.post(f"/quotes/{q['id']}/void", headers=h).status_code == 403
    assert client.delete(f"/invoices/{inv['id']}", headers=h).status_code in (404, 405)  # no hay borrado de facturas
