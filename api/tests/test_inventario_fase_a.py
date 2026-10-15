"""Inventario real, fase A: la factura manda sobre su kardex (editar saca o devuelve la diferencia), un pedido web
cancelado o reembolsado con mercaderia afuera obliga a decir si volvio (y nunca devuelve dos veces), y la salud del
inventario encuentra descuadres y los corrige con la conciliacion del documento."""

from decimal import Decimal

from app.models import StockMovement
from tests.test_tienda_checkout import SLUG, _body, _shop


def _wh(client):
    return next(w for w in client.get("/warehouses").json() if w["is_default"])


def _entrada(client, pid, qty):
    r = client.post("/stock/movements", json={"product_id": pid, "warehouse_id": _wh(client)["id"], "kind": "entrada", "quantity": qty})
    assert r.status_code == 201, r.text


def _stock(client, pid):
    w = _wh(client)
    lv = next((x for x in client.get("/stock").json()["levels"] if x["product_id"] == pid and x["warehouse_id"] == w["id"]), None)
    return Decimal(str(lv["quantity"])) if lv else Decimal(0)


def _product(client, code):
    r = client.post("/products", json={"name": f"Equipo {code}", "code": code, "price": 1000, "cost": 500})
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(client, lines):
    cust = client.get("/customers").json()["items"][0]
    r = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": p["id"], "quantity": q} for p, q in lines]})
    assert r.status_code == 201, r.text
    return r.json()


def test_editing_an_invoice_moves_only_the_difference(client, auth):
    cam, sw = _product(client, "FA-CAM"), _product(client, "FA-SW")
    _entrada(client, cam["id"], 10)
    _entrada(client, sw["id"], 10)
    inv = _invoice(client, [(cam, 3), (sw, 1)])
    assert _stock(client, cam["id"]) == 7 and _stock(client, sw["id"]) == 9
    cust = client.get("/customers").json()["items"][0]
    # 3 -> 5 camaras y se quita el switch: salen 2 camaras y el switch vuelve
    r = client.put(f"/invoices/{inv['id']}", json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "quantity": 5}]})
    assert r.status_code == 200, r.text
    assert _stock(client, cam["id"]) == 5 and _stock(client, sw["id"]) == 10
    # 5 -> 2: vuelven 3
    client.put(f"/invoices/{inv['id']}", json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "quantity": 2}]})
    assert _stock(client, cam["id"]) == 8
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 200
    assert _stock(client, cam["id"]) == 10 and _stock(client, sw["id"]) == 10


def test_refunded_web_order_asks_if_product_came_back_and_never_returns_twice(client, auth, db_session):
    client.put("/settings", json={"einvoice_provider": "none"})
    prods = _shop(client, auto_invoice=True)
    pid = prods[0]["id"]
    _entrada(client, pid, 10)
    antes = _stock(client, pid)
    client.post(f"{SLUG}/checkout", json=_body(pid))  # 2 unidades
    oid = client.get("/store-orders").json()["items"][0]["id"]
    st = lambda s, **kw: client.post(f"/store-orders/{oid}/status", json={"status": s, **kw})  # noqa: E731
    paid = st("pagado", payment_ref="SINPE 1")
    assert paid.status_code == 200, paid.text
    assert paid.json()["invoice_id"]  # se facturo al pagar: la mercaderia salio con la factura
    assert st("enviado", tracking_number="CR1").status_code == 200
    assert _stock(client, pid) == antes - 2
    detalle = client.get(f"/store-orders/{oid}").json()
    assert Decimal(str(detalle["stock_out"])) == 2
    # reembolsar sin decir si volvio: se pregunta
    r = st("reembolsado", note="No le sirvió")
    assert r.status_code == 422 and "volvió" in r.json()["detail"]
    r = st("reembolsado", note="No le sirvió", restock=True)
    assert r.status_code == 200, r.text
    assert _stock(client, pid) == antes and Decimal(str(r.json()["stock_out"])) == 0
    # anular despues la factura del pedido no devuelve otra vez
    assert client.post(f"/invoices/{paid.json()['invoice_id']}/void").status_code == 200
    assert _stock(client, pid) == antes
    dev = db_session.query(StockMovement).filter(StockMovement.order_id == oid, StockMovement.kind == "devolucion").all()
    assert len(dev) == 1 and dev[0].invoice_id == paid.json()["invoice_id"] and dev[0].created_by


def test_refund_without_return_is_recorded(client, auth):
    prods = _shop(client)
    pid = prods[0]["id"]
    _entrada(client, pid, 10)
    antes = _stock(client, pid)
    client.post(f"{SLUG}/checkout", json=_body(pid))
    oid = client.get("/store-orders").json()["items"][0]["id"]
    st = lambda s, **kw: client.post(f"/store-orders/{oid}/status", json={"status": s, **kw})  # noqa: E731
    st("pagado", payment_ref="SINPE 2")
    st("enviado", tracking_number="CR2")
    r = st("reembolsado", note="Llegó dañado, el cliente lo desechó", restock=False)
    assert r.status_code == 200, r.text
    assert _stock(client, pid) == antes - 2
    assert any((h.get("note") or "").startswith("Producto no devuelto") for h in r.json()["history"])
    # decidido: la salud del inventario no lo marca como pendiente
    assert client.get("/stock/health").json()["counts"].get("pedido_sin_decidir", 0) == 0


def test_health_finds_and_fixes_invoice_mismatch_and_flags_negative(client, auth, db_session):
    cam = _product(client, "FA-HLT")
    _entrada(client, cam["id"], 2)
    inv = _invoice(client, [(cam, 3)])  # vende 3 con 2 en bodega: queda en -1
    assert client.get("/stock/health").json()["counts"].get("negativo") == 1
    # alguien borro a mano la salida de la factura (descuadre que nadie ve)
    db_session.query(StockMovement).filter(StockMovement.invoice_id == inv["id"]).delete()
    db_session.commit()
    h = client.get("/stock/health").json()
    issue = next(i for i in h["issues"] if i["kind"] == "factura" and i["ref_id"] == inv["id"])
    assert issue["fixable"] and "Debió salir 3" in issue["detail"]
    r = client.post("/stock/health/fix", json={"kind": "factura", "ref_id": inv["id"]})
    assert r.status_code == 200 and r.json()["movimientos"] == 1
    h = client.get("/stock/health").json()
    assert not [i for i in h["issues"] if i["kind"] == "factura"] and h["counts"].get("negativo") == 1
    nota = db_session.query(StockMovement).filter(StockMovement.invoice_id == inv["id"]).one()
    assert nota.note == "corregido desde salud del inventario" and nota.created_by
