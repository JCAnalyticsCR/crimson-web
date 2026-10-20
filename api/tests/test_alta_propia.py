"""Alta de existencias propias (para cuando Crimson tenga bodega): carga inicial con el importador de productos y
entrada al inventario desde la factura del proveedor recibida por XML. Lo que entra aparece como propio en la funcion
unica (services/availability), primero que lo del proveedor."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, StockMovement
from app.services import availability as av
from app.services import inventory as invsvc
from tests.test_recepcion import respond, up, xml_hoy
from tests.test_session5 import _xlsx

D = Decimal


def _import(client, rows, commit):
    return client.post("/import/products", params={"commit": commit}, files={"file": ("productos.xlsx", _xlsx(rows), "application/octet-stream")}).json()


def test_importador_de_productos_con_cantidad_inicial(client, auth, db_session):
    tid = auth["tenant"]["id"]
    rev = invsvc.special_warehouse(db_session, tid, "revision")
    db_session.commit()
    head = ["Codigo", "Nombre", "Precio", "Bodega", "Cantidad inicial", "Stock minimo"]
    rows = [head, ["PROP-1", "Cámara propia", 45000, "Bodega principal", 5, 2], ["PROP-2", "NVR propio", 90000, "", 2, None], ["PROP-3", "Disco", 30000, rev.name, 1, None]]
    prev = _import(client, rows, commit=False)
    by = {r["row"]: r for r in prev["result"]}
    assert "entrada inicial 5 en Bodega principal" in by[2]["detail"] and "entrada inicial 2" in by[3]["detail"]
    assert by[4]["action"] == "error" and "garantías" in by[4]["detail"]  # nunca a una bodega de garantias
    assert not [m for m in db_session.scalars(select(StockMovement).where(StockMovement.reference == "INV-INICIAL")) if m.note and "importador" in m.note]
    done = _import(client, rows, commit=True)
    assert done["summary"]["nuevo"] == 2 and done["summary"]["error"] == 1
    p1 = db_session.scalar(select(Product).where(Product.code == "PROP-1"))
    p2 = db_session.scalar(select(Product).where(Product.code == "PROP-2"))
    assert p1.min_stock == 2
    r1, r2 = av.one(db_session, tid, p1), av.one(db_session, tid, p2)
    assert r1["state"] == "en_bodega" and r1["own_free"] == 5 and r2["own_free"] == 2  # sin bodega: la predeterminada
    # volver a importar no suma otra vez (ya tiene movimientos): se avisa
    again = _import(client, rows, commit=True)
    assert "no se aplica" in next(r for r in again["result"] if r["row"] == 2)["detail"]
    assert av.one(db_session, tid, p1)["own_free"] == 5


def _codigo_comercial(xml: str, code: str) -> str:
    return xml.replace("</CodigoCABYS>", f"</CodigoCABYS><CodigoComercial><Tipo>01</Tipo><Codigo>{code}</Codigo></CodigoComercial>", 1)


def test_entrada_al_inventario_desde_la_factura_del_proveedor(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prod = client.post("/products", json={"name": "Switch PoE 8", "code": "SW-PROP-8", "price": 75000}).json()
    db_session.get(Product, prod["id"]).supplier_sku = "PRV-SW8"
    db_session.commit()
    d = up(client, _codigo_comercial(xml_hoy(40), "PRV-SW8")).json()
    main = invsvc.default_warehouse(db_session, tid)
    lines = client.get(f"/reception/{d['id']}/stock-lines").json()
    assert lines["accepted"] is False and lines["can_stock"] and [w["id"] for w in lines["warehouses"]][0] == main.id
    ln = lines["lines"][0]
    assert ln["code"] == "PRV-SW8" and ln["product_id"] == prod["id"] and ln["match"] == "codigo" and D(str(ln["quantity"])) == 1
    body = {"warehouse_id": main.id, "items": [{"line": 1, "product_id": prod["id"]}]}
    assert client.post(f"/reception/{d['id']}/stock-in", json=body).status_code == 409  # sin aceptar todavia
    assert respond(client, d["id"]).status_code == 200
    rev = invsvc.special_warehouse(db_session, tid, "revision")
    db_session.commit()
    assert client.post(f"/reception/{d['id']}/stock-in", json={**body, "warehouse_id": rev.id}).status_code == 422
    assert client.post(f"/reception/{d['id']}/stock-in", json={"warehouse_id": main.id, "items": [{"line": 1, "product_id": prod["id"], "quantity": 2}]}).status_code == 422
    r = client.post(f"/reception/{d['id']}/stock-in", json=body)
    assert r.status_code == 201 and r.json()["movimientos"] == 1, r.text
    mv = db_session.scalar(select(StockMovement).where(StockMovement.reference == r.json()["reference"]))
    assert mv.movement_type == "compra" and D(str(mv.quantity)) == 1 and D(str(mv.unit_cost)) == D("100000")
    row = av.one(db_session, tid, prod["id"])
    assert row["state"] == "en_bodega" and row["own_free"] == 1
    # una sola vez por comprobante
    assert client.post(f"/reception/{d['id']}/stock-in", json=body).status_code == 409
    assert client.get(f"/reception/{d['id']}/stock-lines").json()["entered"] == 1
