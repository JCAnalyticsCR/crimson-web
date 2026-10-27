"""Punto de venta para la vendedora (pedido 26/10): cobra desde la caja, crea ahi el cliente y ese cliente queda en su
cartera. No le cobra a un cliente de otro vendedor; el cliente sin dueno al que le cobra pasa a ser suyo."""

from decimal import Decimal

from app.models import Customer
from tests.aliados_helpers import interno, uid


def _producto(client) -> dict:
    r = client.post("/products", json={"name": "Cámara domo POS", "code": "POS-VEN-1", "price": 10000, "currency": "CRC"})
    assert r.status_code == 201, r.text
    return r.json()


def _venta(client, h, pid, customer_id=None, doc_type="TE"):
    body = {"doc_type": doc_type, "lines": [{"product_id": pid, "quantity": 1}], "payments": [{"method": "efectivo", "amount": 20000}]}
    if customer_id:
        body["customer_id"] = customer_id
    return client.post("/pos/sale", json=body, headers=h)


def test_seller_charges_at_pos_with_a_customer_created_there(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    prod = _producto(client)
    ana = interno(client, db_session, tid, "ana.pos@ejemplo.com", "ventas")
    luis = interno(client, db_session, tid, "luis.pos@ejemplo.com", "ventas")
    client.headers.clear()
    client.headers.update(admin_h)

    # la caja le muestra el catalogo y cobra con tiquete sin cliente
    assert client.get("/pos/catalog", headers=ana).status_code == 200
    r = _venta(client, ana, prod["id"])
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["change"])) == Decimal(20000) - Decimal(str(r.json()["total"]))

    # crea el cliente desde la caja: queda en su cartera y le factura con el
    c = client.post("/customers", json={"name": "Hotel Caja", "id_type": "juridica", "id_number": "3101999888", "email": "caja@hotel.cr"}, headers=ana)
    assert c.status_code == 201, c.text
    assert c.json()["account_owner_id"] == uid(client, ana)
    r = _venta(client, ana, prod["id"], c.json()["id"], "FE")
    assert r.status_code == 201, r.text
    assert r.json()["doc_type"] == "FE"

    # el cliente de Ana no se lo cobra Luis
    r = _venta(client, luis, prod["id"], c.json()["id"])
    assert r.status_code == 422 and "atendido por" in r.json()["detail"]

    # un cliente sin dueno al que Luis le cobra en la caja pasa a su cartera
    libre = client.post("/customers", json={"name": "Cliente de mostrador", "id_type": "fisica"})  # lo crea el admin: sin dueno
    assert libre.status_code == 201 and libre.json()["account_owner_id"] is None
    assert _venta(client, luis, prod["id"], libre.json()["id"]).status_code == 201
    db_session.expire_all()
    assert db_session.get(Customer, libre.json()["id"]).account_owner_id == uid(client, luis)


def test_filtros_de_la_caja_con_el_permiso_de_vender(client, auth, db_session):
    """26/10: la caja filtra por categoria y bodega; la vendedora los ve sin permisos de inventario ni de catalogo."""
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    prod = _producto(client)
    ana = interno(client, db_session, tid, "ana.filtros@ejemplo.com", "ventas")
    client.headers.clear()
    client.headers.update(admin_h)
    f = client.get("/pos/filters", headers=ana)
    assert f.status_code == 200, f.text
    assert {"categories", "warehouses"} <= set(f.json()) and all("parent_id" in c for c in f.json()["categories"])
    item = next(i for i in client.get("/pos/catalog", headers=ana).json() if i["id"] == prod["id"])
    assert "stock_by_wh" in item and "brand" in item
