"""Checkout con la funcion unica: lo que no tiene ni existencia propia ni del proveedor (o con dato viejo) no se
compra en linea aunque enforce_stock este apagado; el tope es max_qty (lo prometido a otros pedidos web ya no se
vuelve a vender); la compra recibida para un pedido web queda reservada para ese pedido. Y la moneda: el precio del
producto se convierte a la del documento (cotizacion/factura), a la de la caja y al costeo en colones."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Invoice, Order, PurchaseRequest, StockMovement, StockReservation
from tests.test_campo import _survey
from tests.test_disponibilidad import supplier_item
from tests.test_tienda_inventario import _avail, _buy, _cfg, _in, _prod, _publish, _whs

D = Decimal
FX = D("512.35")  # tipo de cambio de la semilla


def _order(db, number):
    return db.scalar(select(Order).where(Order.number == number))


def _orders(db):
    return len(db.scalars(select(Order).where(Order.channel == "tienda")).all())


def test_no_se_vende_lo_no_disponible_y_se_respeta_max_qty(client, auth, db_session):
    _publish(client)
    _cfg(client, enforce_stock=False)  # apagado: igual no se vende lo que no hay
    agotado = _prod(client, "CK-AGO", "Lector de huella")
    viejo = _prod(client, "CK-VIE", "Cerradura")
    bajo = _prod(client, "CK-BP", "Switch 8 puertos")
    supplier_item(db_session, viejo["id"], 10, hours_ago=72)
    supplier_item(db_session, bajo["id"], 2, hours_ago=2)
    antes = _orders(db_session)
    for p in (agotado, viejo):
        r = _buy(client, [{"product_id": p["id"], "quantity": 1}])
        assert r.status_code == 409 and r.json()["detail"] == f"Ya no hay suficiente {p['name']}: quedan 0.", r.text
    assert _orders(db_session) == antes  # el rechazado no deja pedido
    # el carrito ya lo sabe antes de pagar (misma regla)
    q = client.post("/public/store/crimson/quote", json={"lines": [{"product_id": viejo["id"], "quantity": 1}], "contact": {"name": "X"}}).json()
    assert q["availability"][str(viejo["id"])]["disponible"] is False and q["availability"][str(viejo["id"])]["state"] == "consultar"
    # bajo pedido: tope = lo que el proveedor confirma
    assert _buy(client, [{"product_id": bajo["id"], "quantity": 3}]).status_code == 409
    ok = _buy(client, [{"product_id": bajo["id"], "quantity": 2}])
    assert ok.status_code == 201 and D(str(ok.json()["stock"]["bajo_pedido"][0]["quantity"])) == 2
    # lo prometido a ese pedido ya no se vuelve a vender: el siguiente cliente no puede comprarlo
    viv = _avail(client, bajo["id"]).json()[str(bajo["id"])]
    assert viv["state"] == "consultar" and viv["disponible"] is False
    no = _buy(client, [{"product_id": bajo["id"], "quantity": 1}])
    assert no.status_code == 409


def test_compra_recibida_queda_reservada_para_su_pedido(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "CK-REC", "Grabador 8 canales")
    supplier_item(db_session, p["id"], 3, hours_ago=1)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 2}]).json()["number"])
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    assert req.reason == "pedido_web"
    # la compra nace al cobrar (plan 1.5): sin pago la solicitud no se puede pedir al proveedor
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"}).status_code == 409
    client.patch(f"/orders/{o.id}", json={"status": "pagado"})
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"}).status_code == 200
    r = client.patch(f"/purchase-requests/{req.id}", json={"status": "recibida", "warehouse_id": main["id"]})
    assert r.status_code == 200, r.text
    res = db_session.scalars(select(StockReservation).where(StockReservation.order_id == o.id, StockReservation.status == "confirmada")).all()
    assert len(res) == 1 and D(str(res[0].quantity)) == 2 and res[0].origin == "web" and res[0].expires_at is None
    # entraron 2 a bodega, pero son de ese pedido: el siguiente cliente no se las lleva
    interno = client.get(f"/products/{p['id']}/availability").json()
    assert interno["own_on_hand"] == 2 and interno["own_free"] == 0 and interno["own_reserved_web"] == 2
    # el proveedor sigue reportando 3 (su proximo refresco lo baja) y ya nada esta prometido: bajo pedido
    assert _avail(client, p["id"]).json()[str(p["id"])]["state"] == "bajo_pedido"
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "reservado"
    # al despachar sale de esa reserva, una sola vez
    assert client.patch(f"/orders/{o.id}", json={"status": "enviado"}).status_code == 200
    total = sum((D(str(m.quantity)) for m in db_session.scalars(select(StockMovement).where(StockMovement.product_id == p["id"]))), D(0))
    assert total == 0
    db_session.refresh(res[0])
    assert res[0].status == "consumida"


def test_compra_recibida_de_un_pedido_cancelado_queda_libre(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "CK-CAN", "Disco 2 TB")
    supplier_item(db_session, p["id"], 5, hours_ago=1)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    client.patch(f"/orders/{o.id}", json={"status": "pagado"})  # la compra nace al cobrar (plan 1.5)
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"}).status_code == 200
    assert client.patch(f"/orders/{o.id}", json={"status": "cancelado"}).status_code == 200
    client.patch(f"/purchase-requests/{req.id}", json={"status": "recibida", "warehouse_id": main["id"]})
    assert not db_session.scalars(select(StockReservation).where(StockReservation.order_id == o.id, StockReservation.status == "confirmada")).all()
    assert _avail(client, p["id"]).json()[str(p["id"])]["state"] == "en_bodega"


def test_moneda_del_producto_en_documento_caja_y_costeo(client, auth, db_session):
    usd = client.post("/products", json={"name": "Cámara en dólares", "code": "USD-1", "price": 100, "currency": "USD", "show_on_web": False}).json()
    # cotizacion/factura en colones: el precio del producto se convierte (antes entraba como 100 colones)
    pv = client.post("/documents/preview", json={"currency": "CRC", "lines": [{"product_id": usd["id"], "quantity": 1}]}).json()
    assert D(str(pv["lines"][0]["unit_price"])) == D(100) * FX
    pv_usd = client.post("/documents/preview", json={"currency": "USD", "lines": [{"product_id": usd["id"], "quantity": 1}]}).json()
    assert D(str(pv_usd["lines"][0]["unit_price"])) == D(100)
    # punto de venta (moneda de la empresa, colones): precio convertido en el catalogo y en la venta
    pos = next(x for x in client.get("/pos/catalog").json() if x["id"] == usd["id"])
    assert pos["pos_currency"] == "CRC" and D(str(pos["pos_price"])) == D(100) * FX
    main, _ = _whs(client)
    _in(client, usd["id"], main["id"], 1)
    sale = client.post("/pos/sale", json={"lines": [{"product_id": usd["id"], "quantity": 1}], "payments": [{"method": "efectivo", "amount": 60000}], "emit": False})
    assert sale.status_code == 201, sale.text
    inv = db_session.get(Invoice, sale.json()["invoice_id"])
    assert inv.currency == "CRC" and D(str(inv.lines[0].unit_price)) == D(100) * FX
    # costeo del levantamiento (en colones): el precio de lista se convierte
    s = _survey(client, items=[{"product_id": usd["id"], "name": "Cámara en dólares", "quantity": 1, "kind": "equipo"}])
    line = next(x for x in client.get(f"/surveys/{s['id']}/costing").json()["lines"] if x["product_id"] == usd["id"])
    assert D(str(line["unit_price"])) == (D(100) * FX).quantize(D("0.01"))
