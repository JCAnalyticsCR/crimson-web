"""Respuestas de Andres (19/10) a las decisiones del inventario:
- garantias: revision previa de Crimson -> distribuidor -> cliente, seguida en el kardex por bodegas que no se venden;
- lo que vuelve (o la compra ya hecha al proveedor) queda en la bodega de venta de Crimson;
- los ajustes de inventario solo los hacen los vendedores y el administrador."""

from decimal import Decimal

from sqlalchemy import select

from app.models import PurchaseRequest, StockMovement, Warehouse, WarrantyCase
from tests.test_session6 import _login


def _wh(client, kind="venta"):
    return next(w for w in client.get("/warehouses").json() if w.get("kind", "venta") == kind and (kind != "venta" or w["is_default"]))


def _product(client, code, price=1000):
    r = client.post("/products", json={"name": f"Equipo {code}", "code": code, "price": price, "cost": 500})
    assert r.status_code == 201, r.text
    return r.json()


def _entrada(client, pid, qty):
    r = client.post("/stock/movements", json={"product_id": pid, "warehouse_id": _wh(client)["id"], "kind": "entrada", "quantity": qty})
    assert r.status_code == 201, r.text


def _qty(client, pid, wid):
    lv = next((x for x in client.get("/stock").json()["levels"] if x["product_id"] == pid and x["warehouse_id"] == wid), None)
    return Decimal(str(lv["quantity"])) if lv else Decimal(0)


def _act(client, wid, **kw):
    return client.post(f"/warranties/{wid}/action", json=kw)


def test_warranty_review_distributor_and_back_moves_the_unit_between_non_sellable_warehouses(client, auth, db_session):
    cam = _product(client, "GAR-CAM")
    cust = client.get("/customers").json()["items"][0]
    asset = client.post("/assets", json={"customer_id": cust["id"], "product_id": cam["id"], "name": "Cámara portón", "serial": "SN-OLD"}).json()
    venta = _wh(client)["id"]
    antes = _qty(client, cam["id"], venta)

    r = client.post("/warranties", json={"customer_id": cust["id"], "asset_id": asset["id"], "problem": "No enciende"})
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["number"].startswith("GAR-") and g["status"] == "recibido" and g["serial"] == "SN-OLD" and g["equipment"] == "Cámara portón"
    rev = _wh(client, "revision")["id"]
    assert _qty(client, cam["id"], rev) == 1 and _qty(client, cam["id"], venta) == antes  # nunca entra a la venta
    assert client.get("/assets", params={"customer_id": cust["id"]}).json()[0]["status"] == "garantia"

    # no se manda al distribuidor sin la revision de Crimson
    assert _act(client, g["id"], action="enviar").status_code == 409
    assert _act(client, g["id"], action="revisar", review="procede", note="Fuente quemada, sin golpes").status_code == 200
    sup = client.post("/suppliers", json={"name": "Distribuidor X"})
    sid = sup.json()["id"] if sup.status_code == 201 else None
    r = _act(client, g["id"], action="enviar", supplier_id=sid, supplier_ref="RMA-881")
    assert r.status_code == 200, r.text
    prov = _wh(client, "proveedor")["id"]
    assert _qty(client, cam["id"], rev) == 0 and _qty(client, cam["id"], prov) == 1
    assert _act(client, g["id"], action="cerrar").status_code == 409  # sigue en el distribuidor

    r = _act(client, g["id"], action="recibir", result="reemplazado", new_serial="SN-NEW")
    assert r.json()["status"] == "devuelto" and _qty(client, cam["id"], prov) == 0 and _qty(client, cam["id"], rev) == 1
    r = _act(client, g["id"], action="cerrar", note="Entregado e instalado")
    assert r.json()["status"] == "cerrado" and _qty(client, cam["id"], rev) == 0
    a = client.get("/assets", params={"customer_id": cust["id"]}).json()[0]
    assert a["status"] == "activo" and a["serial"] == "SN-NEW"
    assert [h["status"] for h in r.json()["history"]][-1] == "cerrado" and all(h["by_name"] for h in r.json()["history"])
    movs = db_session.scalars(select(StockMovement).where(StockMovement.warranty_id == g["id"])).all()
    assert len(movs) == 6 and all(m.created_by and m.reference == g["number"] and m.kind == "garantia" for m in movs)
    # la unidad que volvio cambiada entra con su serie nueva; la que se fue, con la vieja
    assert [m.serial for m in movs if m.warehouse_id == rev and m.quantity > 0] == ["SN-OLD", "SN-NEW"]
    # repetir el cierre no mueve nada
    assert _act(client, g["id"], action="cerrar").status_code == 409

    # la bodega de revision no alimenta la tienda ni el bajo minimo, y no puede ser la predeterminada
    w = client.get("/warehouses").json()
    assert {x["kind"] for x in w} >= {"venta", "revision", "proveedor"}
    r = client.put(f"/warehouses/{rev}", json={"name": "Revisión", "kind": "revision", "is_default": True})
    assert r.status_code == 422


def test_crimson_replacement_leaves_sale_stock_and_the_repaired_unit_comes_back_to_sale(client, auth):
    cam = _product(client, "GAR-REP")
    _entrada(client, cam["id"], 5)
    cust = client.get("/customers").json()["items"][0]
    venta = _wh(client)["id"]
    g = client.post("/warranties", json={"customer_id": cust["id"], "product_id": cam["id"], "serial": "A1"}).json()
    r = _act(client, g["id"], action="reemplazar", product_id=cam["id"], serial="B2")
    assert r.status_code == 200, r.text
    assert _qty(client, cam["id"], venta) == 4  # el cliente se lleva un equipo de la bodega
    _act(client, g["id"], action="revisar", review="procede")
    _act(client, g["id"], action="enviar")
    r = _act(client, g["id"], action="recibir", result="reparado")
    # el cliente ya tiene el reemplazo: lo reparado es mercaderia de Crimson (el proveedor no acepta devoluciones)
    assert _qty(client, cam["id"], venta) == 5 and _qty(client, cam["id"], _wh(client, "revision")["id"]) == 0
    _act(client, g["id"], action="cerrar")
    assert _qty(client, cam["id"], venta) == 5


def test_review_says_no_and_void_returns_everything(client, auth):
    cam = _product(client, "GAR-NO")
    cust = client.get("/customers").json()["items"][0]
    g = client.post("/warranties", json={"customer_id": cust["id"], "product_id": cam["id"]}).json()
    assert _act(client, g["id"], action="cerrar").status_code == 409  # sin revision no se cierra
    _act(client, g["id"], action="revisar", review="no_procede", note="Golpe en la carcasa: no aplica garantía")
    assert _act(client, g["id"], action="enviar").status_code == 409
    assert _act(client, g["id"], action="cerrar").json()["status"] == "cerrado"
    rev = _wh(client, "revision")["id"]
    assert _qty(client, cam["id"], rev) == 0

    g2 = client.post("/warranties", json={"customer_id": cust["id"], "product_id": cam["id"]}).json()
    assert _qty(client, cam["id"], rev) == 1
    assert _act(client, g2["id"], action="anular").status_code == 422  # pide el motivo
    assert _act(client, g2["id"], action="anular", note="Se abrió por error").json()["status"] == "anulado"
    assert _qty(client, cam["id"], rev) == 0


def test_health_finds_and_fixes_a_warranty_out_of_sync(client, auth, db_session):
    cam = _product(client, "GAR-HLT")
    cust = client.get("/customers").json()["items"][0]
    g = client.post("/warranties", json={"customer_id": cust["id"], "product_id": cam["id"]}).json()
    db_session.query(StockMovement).filter(StockMovement.warranty_id == g["id"]).delete()
    db_session.commit()
    issue = next(i for i in client.get("/stock/health").json()["issues"] if i["kind"] == "garantia" and i["ref_id"] == g["id"])
    assert issue["fixable"]
    assert client.post("/stock/health/fix", json={"kind": "garantia", "ref_id": g["id"]}).json()["movimientos"] == 1
    assert not [i for i in client.get("/stock/health").json()["issues"] if i["kind"] == "garantia"]


def test_only_sellers_and_admin_adjust_stock(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    cam = _product(client, "AJ-1")
    wid = _wh(client)["id"]
    body = {"product_id": cam["id"], "warehouse_id": wid, "kind": "ajuste", "quantity": 3, "note": "conteo fisico"}
    ven = _login(client, db_session, tid, "ven-aj@ejemplo.com", "ventas", admin_h)
    bod = _login(client, db_session, tid, "bod-aj@ejemplo.com", "inventario", admin_h)
    sup = _login(client, db_session, tid, "sup-aj@ejemplo.com", "supervisor", admin_h)
    assert client.post("/stock/movements", json=body, headers=ven).status_code == 201
    assert client.post("/stock/movements", json=body, headers=bod).status_code == 403
    assert client.post("/stock/movements", json=body, headers=sup).status_code == 403
    assert client.post("/stock/movements", json=body).status_code == 201  # administrador
    # bodega sigue trasladando y viendo garantias; el vendedor no abre garantias
    assert client.get("/warranties", headers=bod).status_code == 200
    cust = client.get("/customers").json()["items"][0]
    assert client.post("/warranties", json={"customer_id": cust["id"], "equipment": "DVR"}, headers=ven).status_code == 403
    matrix = client.get("/auth/roles").json()["matrix"]
    assert "ajustar" in matrix["ventas"]["inventory"] and "ajustar" not in matrix["inventario"]["inventory"]


def test_cancelled_order_keeps_a_purchase_already_requested_from_the_supplier(client, auth, db_session):
    from app.models import Order, Tenant
    from app.services import store_inventory as sinv

    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    o = Order(tenant_id=tid, number="ORD-T1", channel="tienda", status="pagado", currency="CRC", contact={}, history=[])
    db_session.add(o)
    db_session.flush()
    pedida = PurchaseRequest(tenant_id=tid, number="SC-T1", status="solicitada", reason="pedido_web", order_id=o.id)
    borrador = PurchaseRequest(tenant_id=tid, number="SC-T2", status="borrador", reason="pedido_web", order_id=o.id)
    db_session.add_all([pedida, borrador])
    db_session.flush()
    sinv.release_for_order(db_session, o, "cancelado", None)
    assert pedida.status == "solicitada" and borrador.status == "cancelada"
    assert any("SC-T1" in (h.get("note") or "") and "bodega de Crimson" in h["note"] for h in o.history)
    assert t is not None and db_session.scalar(select(Warehouse).where(Warehouse.tenant_id == tid, Warehouse.kind == "revision")) is None
    assert db_session.scalar(select(WarrantyCase).where(WarrantyCase.tenant_id == tid)) is None
