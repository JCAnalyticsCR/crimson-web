"""Pedido 21/10: publicar productos en la tienda (show_on_web) es solo del administrador."""

import pytest

from tests.test_session4 import _user_with_role


@pytest.mark.parametrize("rol", ["ventas", "inventario"])
def test_publicar_solo_admin(client, auth, db_session, rol):
    pr = client.post("/products", json={"name": "Camara web", "code": f"WEB-{rol}", "price": 1000}).json()
    pw = _user_with_role(db_session, auth["tenant"]["id"], f"{rol}.web@ejemplo.com", rol)
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": f"{rol}.web@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    assert client.patch(f"/products/{pr['id']}/web", json={"show_on_web": True}, headers=h).status_code == 403
    if rol == "inventario":  # edita la ficha, pero el "Mostrar en sitio web" que mande no cambia nada
        body = {k: pr[k] for k in ("name", "code", "price")} | {"show_on_web": True}
        assert client.put(f"/products/{pr['id']}", json=body, headers=h).status_code == 200
        assert client.get(f"/products/{pr['id']}").json()["show_on_web"] is False
    assert client.patch(f"/products/{pr['id']}/web", json={"show_on_web": True}).json()["show_on_web"] is True  # admin
