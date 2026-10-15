"""Inventario real: en un proyecto cada equipo sale de bodega UNA vez (max(facturado, instalado)), instalar un equipo
(orden, serie o activo del cliente) lo saca, anular no devuelve lo instalado ni devuelve dos veces, y una compra
recibida no vuelve a entrar."""

from decimal import Decimal

from app.models import StockMovement


def _product(client, code, name):
    r = client.post("/products", json={"name": name, "code": code, "price": 100, "cost": 50})
    assert r.status_code == 201, r.text
    return r.json()


def _wh(client):
    return next(w for w in client.get("/warehouses").json() if w["is_default"])


def _entrada(client, pid, wid, qty):
    r = client.post("/stock/movements", json={"product_id": pid, "warehouse_id": wid, "kind": "entrada", "quantity": qty})
    assert r.status_code == 201, r.text


def _stock(client, pid):
    w = _wh(client)
    lv = next((x for x in client.get("/stock").json()["levels"] if x["product_id"] == pid and x["warehouse_id"] == w["id"]), None)
    return Decimal(str(lv["quantity"])) if lv else Decimal(0)


def _quote(client, lines):
    cust = client.get("/customers").json()["items"][0]
    r = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": p["id"], "quantity": q} for p, q in lines]})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _project(client, q):
    r = client.post(f"/quotes/{q['id']}/project", json={"site": "Condominio"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _finish(client, pr, used):
    """Primera orden del proyecto: anota lo usado y la finaliza."""
    oid = client.get(f"/projects/{pr['id']}").json()["orders"][0]["id"]
    mats = [{"product_id": p["id"], "name": p["name"], "quantity": q, "planned": q} for p, q in used]
    client.post(f"/work-orders/{oid}/start", json={})
    r = client.post(f"/work-orders/{oid}/finish", json={"materials": mats})
    assert r.status_code == 200, r.text
    return oid


def _setup(client, n=20):
    cam = _product(client, "INV-CAM", "Cámara 4MP")
    sw = _product(client, "INV-SW", "Switch PoE 8")
    w = _wh(client)
    _entrada(client, cam["id"], w["id"], n)
    _entrada(client, sw["id"], w["id"], n)
    return cam, sw


def test_quote_converted_in_sales_then_installed_counts_once(client, auth):
    """El caso del doble descuento: la cotizacion del proyecto se convirtio desde Ventas y despues la orden se cerro."""
    cam, sw = _setup(client)
    q = _quote(client, [(cam, 10), (sw, 1)])
    pr = _project(client, q)
    inv = client.post(f"/quotes/{q['id']}/convert")
    assert inv.status_code == 201, inv.text
    assert _stock(client, cam["id"]) == 10 and _stock(client, sw["id"]) == 19  # la factura saco 10 y 1
    _finish(client, pr, [(cam, 10), (sw, 1)])
    assert _stock(client, cam["id"]) == 10 and _stock(client, sw["id"]) == 19  # la orden NO vuelve a sacarlos
    # facturar el proyecto (cotizacion ya convertida) tampoco
    assert client.post(f"/projects/{pr['id']}/invoice").status_code == 201
    assert _stock(client, cam["id"]) == 10 and _stock(client, sw["id"]) == 19


def test_partial_orders_then_project_invoice_takes_the_rest(client, auth):
    """Antes: con alguna orden aplicada la factura del proyecto no descontaba NADA y el switch nunca salia."""
    cam, sw = _setup(client)
    q = _quote(client, [(cam, 5), (sw, 1)])
    pr = _project(client, q)
    _finish(client, pr, [(cam, 5)])
    assert _stock(client, cam["id"]) == 15 and _stock(client, sw["id"]) == 20
    assert client.post(f"/projects/{pr['id']}/invoice").status_code == 201
    assert _stock(client, cam["id"]) == 15 and _stock(client, sw["id"]) == 19


def test_invoiced_first_then_used_more_and_void_keeps_installed(client, auth, db_session):
    cam, sw = _setup(client)
    q = _quote(client, [(cam, 10)])
    pr = _project(client, q)
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    assert _stock(client, cam["id"]) == 10
    _finish(client, pr, [(cam, 12)])  # se usaron 2 de mas: salen solo esos 2
    assert _stock(client, cam["id"]) == 8
    # anular la factura de algo ya instalado no lo devuelve a bodega (esta en el cliente)
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 200
    assert _stock(client, cam["id"]) == 8
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 409  # anular dos veces ya no se puede
    notas = [m.note for m in db_session.query(StockMovement).filter(StockMovement.project_id == pr["id"])]
    assert notas and all(f"Proyecto {pr['number']}" in n for n in notas)  # cada salida dice por que


def test_void_project_invoice_before_install_returns_stock(client, auth):
    cam, sw = _setup(client)
    q = _quote(client, [(cam, 4)])
    _project(client, q)
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    assert _stock(client, cam["id"]) == 16
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 200
    assert _stock(client, cam["id"]) == 20  # nada se instalo: vuelve todo


def test_plain_invoice_void_and_restock_are_idempotent(client, auth, db_session):
    from app.models import Invoice
    from app.services import inventory as invsvc

    cam, _ = _setup(client)
    cust = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "quantity": 3}]}).json()
    assert _stock(client, cam["id"]) == 17
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 200
    assert _stock(client, cam["id"]) == 20
    # una NC de Hacienda despues de la anulacion (o cualquier segunda llamada) no devuelve otra vez
    invsvc.restock_for_void(db_session, db_session.get(Invoice, inv["id"]), None)
    db_session.commit()
    assert _stock(client, cam["id"]) == 20


def test_serial_installed_in_project_leaves_stock_once(client, auth):
    cam, _ = _setup(client, n=0)
    w = _wh(client)
    r = client.post("/stock/units", json={"product_id": cam["id"], "warehouse_id": w["id"], "serials": ["SN-1", "SN-2"], "register_entry": True})
    assert r.status_code == 201, r.text
    units = r.json()
    assert _stock(client, cam["id"]) == 2
    q = _quote(client, [(cam, 1)])
    pr = _project(client, q)
    r = client.patch(f"/stock/units/{units[0]['id']}", json={"status": "instalado", "project_id": pr["id"]})
    assert r.status_code == 200, r.text
    assert _stock(client, cam["id"]) == 1  # instalar la serie la saca de bodega
    # la orden del mismo proyecto anota esa misma camara: no sale otra
    _finish(client, pr, [(cam, 1)])
    assert _stock(client, cam["id"]) == 1
    # la serie ya creo su activo del cliente (no cuenta doble); un SEGUNDO activo es otra camara instalada: sale otra
    assets = [a for a in client.get("/assets").json() if a.get("project_id") == pr["id"]]
    assert len(assets) == 1 and assets[0]["serial"] == "SN-1"
    cust = client.get("/customers").json()["items"][0]
    a = client.post("/assets", json={"customer_id": cust["id"], "project_id": pr["id"], "product_id": cam["id"], "name": "Cámara patio"})
    assert a.status_code == 201, a.text
    assert _stock(client, cam["id"]) == 0


def test_asset_registered_in_project_without_order_leaves_stock(client, auth):
    cam, _ = _setup(client, n=3)
    q = _quote(client, [(cam, 2)])
    pr = _project(client, q)
    cust = client.get("/customers").json()["items"][0]
    for n in range(2):
        r = client.post("/assets", json={"customer_id": cust["id"], "project_id": pr["id"], "product_id": cam["id"], "name": f"Cámara {n}"})
        assert r.status_code == 201, r.text
    assert _stock(client, cam["id"]) == 1


def test_purchase_received_cannot_be_received_twice(client, auth):
    cam, _ = _setup(client, n=0)
    r = client.post("/purchase-requests", json={"lines": [{"product_id": cam["id"], "name": cam["name"], "quantity": 5}]})
    assert r.status_code in (200, 201), r.text
    rid = r.json()["id"]
    assert client.patch(f"/purchase-requests/{rid}", json={"status": "recibida"}).status_code == 200
    assert _stock(client, cam["id"]) == 5
    assert client.patch(f"/purchase-requests/{rid}", json={"status": "solicitada"}).status_code == 409
    assert client.patch(f"/purchase-requests/{rid}", json={"status": "recibida"}).status_code == 200  # mismo estado: nada
    assert _stock(client, cam["id"]) == 5


def test_kardex_shows_who_what_document_and_running_balance(client, auth):
    cam, _ = _setup(client)  # entrada de 20
    q = _quote(client, [(cam, 3)])
    pr = _project(client, q)
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    _finish(client, pr, [(cam, 5)])  # 3 por la factura + 2 de mas por la orden
    k = client.get("/stock/movements", params={"product_id": cam["id"]}).json()
    assert k["total"] == 3 and _stock(client, cam["id"]) == 15
    extra, venta, entrada = k["items"]  # del mas nuevo al mas viejo
    assert entrada["kind"] == "entrada" and Decimal(str(entrada["balance"])) == 20
    assert venta["invoice_number"] == inv["number"] and venta["project_number"] == pr["number"] and Decimal(str(venta["balance"])) == 17
    assert extra["project_number"] == pr["number"] and Decimal(str(extra["quantity"])) == -2 and Decimal(str(extra["balance"])) == 15
    assert all(m["user"] and m["user"] != "Sistema" for m in k["items"])  # quien lo hizo
    assert client.get("/stock/movements", params={"kind": "venta"}).json()["total"] == 1
    assert client.get("/stock/movements", params={"q": inv["number"]}).json()["total"] == 1
