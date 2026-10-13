"""Tienda conectada al inventario real: disponibilidad web, reservas de pedidos web (sin sobreventa),
vencimiento, cancelacion, despacho que descuenta una sola vez, panel "Inventario web" y "Avisarme"."""

import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import (
    EmailOutbox,
    Order,
    OrderLine,
    Product,
    PurchaseRequest,
    StockMovement,
    StockReservation,
    StockUnit,
    StoreStockAlert,
    Tenant,
)
from app.services import reservations as rsv
from app.services import store_inventory as sinv

D = Decimal


# ---------- utilidades ----------
def _publish(client, kind="tienda"):
    r = client.put("/store", json={"kind": kind, "published": True, "whatsapp": "+50688889892"})
    assert r.status_code == 200, r.text


def _cfg(client, **kw):
    r = client.put("/store/inventory/settings", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def _prod(client, code, name=None, price=10000, web=True):
    r = client.post("/products", json={"name": name or f"Producto {code}", "code": code, "price": price, "cost": 20, "show_on_web": web})
    assert r.status_code == 201, r.text
    return r.json()


def _whs(client):
    ws = client.get("/warehouses").json()
    return next(w for w in ws if w["is_default"]), next(w for w in ws if not w["is_default"])


def _in(client, pid, wid, qty):
    r = client.post("/stock/movements", json={"product_id": pid, "warehouse_id": wid, "kind": "entrada", "quantity": qty})
    assert r.status_code == 201, r.text


def _supplier(db, pid, qty, days_ago=0):
    p = db.get(Product, pid)
    p.supplier_stock = qty
    p.supplier_updated_at = datetime.now(UTC) - timedelta(days=days_ago)
    db.commit()


def _avail(client, *ids):
    r = client.get("/public/store/crimson/availability", params={"ids": ",".join(str(i) for i in ids)})
    assert r.status_code == 200, r.text
    return r


def _buy(client, lines, payment="SINPE Móvil"):
    body = {"lines": lines, "contact": {"name": "Ana Vega", "email": "ana@ejemplo.com"}, "payment_method": payment}
    return client.post("/public/store/crimson/checkout", json=body)


def _order(db, number):
    return db.scalar(select(Order).where(Order.number == number))


def _ledger(db, pid):
    return sum((D(str(m.quantity)) for m in db.scalars(select(StockMovement).where(StockMovement.product_id == pid))), D(0))


# ---------- 1) disponibilidad ----------
def test_disponibilidad_propio_proveedor_agotado_y_bodegas_web(client, auth, db_session):
    _publish(client)
    main, truck = _whs(client)
    a, b, c, d = (_prod(client, f"DISP-{x}") for x in "ABCD")
    _in(client, a["id"], main["id"], 5)
    _in(client, a["id"], truck["id"], 4)
    _supplier(db_session, b["id"], 10)  # proveedor con dato de hoy -> bajo pedido
    _supplier(db_session, c["id"], 10, days_ago=30)  # dato viejo -> consultar
    # d: nada -> agotado

    # por defecto la cantidad exacta, con el texto de siempre
    assert _avail(client, a["id"]).json()[str(a["id"])]["mensaje"] == "Disponible (9 en bodega)"
    _cfg(client, quantity_display="umbral")
    r = _avail(client, a["id"], b["id"], c["id"], d["id"])
    assert "max-age=30" in r.headers["cache-control"]
    av = r.json()
    assert av[str(a["id"])]["estado"] == "disponible" and av[str(a["id"])]["mensaje"] == "Disponible" and av[str(a["id"])]["disponible"]
    assert av[str(a["id"])]["cantidad"] is None  # modo umbral: no se publica el numero
    assert av[str(b["id"])]["estado"] == "bajo_pedido" and av[str(b["id"])]["plazo"] == "3-5 días hábiles" and av[str(b["id"])]["disponible"]
    assert av[str(c["id"])]["estado"] == "consultar" and av[str(c["id"])]["mensaje"] == "Consultar disponibilidad" and not av[str(c["id"])]["disponible"]
    assert av[str(d["id"])]["estado"] == "agotado" and av[str(d["id"])]["accion"] == "whatsapp" and not av[str(d["id"])]["disponible"]
    # nada interno en lo publico
    assert not ({"propio", "proveedor", "reservado", "libre", "proveedor_al"} & set(av[str(b["id"])]))

    # cantidad exacta: todas las bodegas activas (5 + 4) por defecto
    _cfg(client, quantity_display="exacta")
    assert D(str(_avail(client, a["id"]).json()[str(a["id"])]["cantidad"])) == 9
    # solo la bodega principal alimenta la web
    _cfg(client, warehouse_ids=[main["id"]])
    one = _avail(client, a["id"]).json()[str(a["id"])]
    assert D(str(one["cantidad"])) == 5 and one["mensaje"] == "Disponible (5 en bodega)"
    # una reserva confirmada (proyecto, bodega) baja lo disponible web; bajo el umbral -> ultimas unidades
    tid = auth["tenant"]["id"]
    res = rsv.create(db_session, tid, None, a["id"], D(3), main["id"])
    db_session.commit()
    rsv.confirm(db_session, res, None)
    _cfg(client, quantity_display="umbral", low_threshold=3)
    one = _avail(client, a["id"]).json()[str(a["id"])]
    assert one["mensaje"] == "¡Últimas unidades!" and one["ultimas"] and one["cantidad"] is None
    # sin stock libre pero con proveedor viejo: consultar; "sell_when_unknown" lo vuelve comprable
    _cfg(client, sell_when_unknown=True)
    assert _avail(client, c["id"]).json()[str(c["id"])]["disponible"] is True

    # la vitrina recibe la misma disponibilidad y la etiqueta de siempre (state/label)
    pub = {p["id"]: p for p in client.get("/public/store/crimson/products").json()}
    assert pub[b["id"]]["availability"]["state"] == "bajo_pedido" and pub[b["id"]]["availability"]["label"].startswith("Bajo pedido")
    assert pub[a["id"]]["availability"]["state"] == "en_bodega"
    # agotados ocultos del catalogo si asi se elige (la ficha directa sigue abriendo)
    _cfg(client, out_of_stock="ocultar")
    pub = {p["id"] for p in client.get("/public/store/crimson/products").json()}
    assert d["id"] not in pub and b["id"] in pub
    assert client.get(f"/public/store/crimson/products/{d['id']}").json()["availability"]["estado"] == "agotado"
    # lo no publicado no se informa
    hidden = _prod(client, "DISP-OCULTO", web=False)
    assert _avail(client, hidden["id"]).json() == {}
    assert client.get("/public/store/crimson/availability", params={"ids": "a,b"}).status_code == 422


def test_disponibilidad_variantes_y_serializados(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    cam = _prod(client, "VAR-CAM", "Cámara domo")
    vs = client.put(f"/products/{cam['id']}/variants", json=[{"name": "Blanca", "code": "VAR-CAM-B"}, {"name": "Negra", "code": "VAR-CAM-N"}])
    assert vs.status_code == 200, vs.text
    blanca, negra = vs.json()
    _in(client, cam["id"], main["id"], 2)
    _cfg(client, enforce_stock=True, quantity_display="exacta")
    # las variantes comparten la existencia del producto: 1 blanca + 1 negra se llevan las 2
    ok = _buy(
        client, [{"product_id": cam["id"], "variant_id": blanca["id"], "quantity": 1}, {"product_id": cam["id"], "variant_id": negra["id"], "quantity": 1}]
    )
    assert ok.status_code == 201, ok.text
    assert _avail(client, cam["id"]).json()[str(cam["id"])]["estado"] == "agotado"
    no = _buy(client, [{"product_id": cam["id"], "variant_id": negra["id"], "quantity": 1}])
    assert no.status_code == 409 and "quedan 0" in no.json()["detail"]

    # serializado: el ledger dice 3 pero solo 2 series estan disponibles -> 2
    nvr = _prod(client, "SER-NVR", "NVR serializado")
    r = client.post("/stock/units", json={"product_id": nvr["id"], "serials": ["S-1", "S-2", "S-3"], "register_entry": True})
    assert r.status_code == 201, r.text
    u3 = db_session.scalar(select(StockUnit).where(StockUnit.serial == "S-3"))
    u3.status = "averiado"
    db_session.commit()
    assert D(str(_avail(client, nvr["id"]).json()[str(nvr["id"])]["cantidad"])) == 2
    interno = sinv.availability(db_session, auth["tenant"]["id"], [nvr["id"]], public=False)[nvr["id"]]
    assert interno["propio"] == 2 and interno["libre"] == 2


# ---------- 2) checkout: reserva automatica, bajo pedido y no sobrevender ----------
def test_checkout_reserva_web_y_bajo_pedido(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "WEB-1", "Switch PoE")
    _in(client, p["id"], main["id"], 1)
    _supplier(db_session, p["id"], 5)
    _cfg(client, enforce_stock=True)
    r = _buy(client, [{"product_id": p["id"], "quantity": 3}])
    assert r.status_code == 201, r.text
    body = r.json()
    assert D(str(body["stock"]["bajo_pedido"][0]["quantity"])) == 2 and body["stock"]["bajo_pedido"][0]["plazo"] == "3-5 días hábiles"
    assert body["stock"]["reserva_vence"]
    o = _order(db_session, body["number"])
    res = db_session.scalars(select(StockReservation).where(StockReservation.order_id == o.id)).all()
    assert len(res) == 1 and res[0].status == "confirmada" and res[0].origin == "web" and D(str(res[0].quantity)) == 1
    assert res[0].approved_by is None and res[0].approved_at  # automatica: sin aprobacion humana
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    assert req.reason == "pedido_web" and req.status == "borrador" and D(str(req.lines[0].quantity)) == 2
    assert db_session.scalar(select(EmailOutbox).where(EmailOutbox.entity == "purchase_request", EmailOutbox.entity_id == req.id))
    # ya no queda stock propio: el siguiente cliente lo ve bajo pedido
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "bajo_pedido"
    st = client.get(f"/store/orders/{o.id}/inventory").json()
    assert st["estado"] == "requiere_compra" and st["solicitudes_compra"][0]["number"] == req.number
    # el panel interno de reservas muestra el origen
    lista = client.get("/stock/reservations", params={"status": "abiertas"}).json()["items"]
    assert any(x["origin"] == "web" and x["order_id"] == o.id for x in lista)
    # pedir mas de lo que el proveedor reporta, con bloqueo: 409 claro
    no = _buy(client, [{"product_id": p["id"], "quantity": 9}])
    assert no.status_code == 409 and "el proveedor reporta 5" in no.json()["detail"]


def test_dos_clientes_la_ultima_unidad_por_la_api(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "ULT-API", "Lector biométrico")
    _in(client, p["id"], main["id"], 1)
    _cfg(client, enforce_stock=True)
    antes = db_session.scalar(select(PurchaseRequest.id).order_by(PurchaseRequest.id.desc()))
    assert _buy(client, [{"product_id": p["id"], "quantity": 1}]).status_code == 201
    no = _buy(client, [{"product_id": p["id"], "quantity": 1}])
    assert no.status_code == 409 and no.json()["detail"] == "Ya no hay suficiente Lector biométrico: quedan 0."
    assert len([o for o in client.get("/orders").json() if o["channel"] == "tienda"]) == 1  # el rechazado no dejo pedido
    # modo aviso (defecto): no rechaza; reserva lo que hay y el resto queda "requiere compra"
    _cfg(client, enforce_stock=False)
    ok = _buy(client, [{"product_id": p["id"], "quantity": 1}])
    assert ok.status_code == 201
    o = _order(db_session, ok.json()["number"])
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "requiere_compra"
    assert db_session.scalar(select(PurchaseRequest.id).order_by(PurchaseRequest.id.desc())) != antes
    # apagado del todo: el checkout es el de siempre (sin reservas)
    _cfg(client, enabled=False)
    off = _buy(client, [{"product_id": p["id"], "quantity": 1}])
    assert off.status_code == 201
    assert not db_session.scalars(select(StockReservation).where(StockReservation.order_id == _order(db_session, off.json()["number"]).id)).all()


# ---------- 3) vencimiento y cancelacion ----------
def test_vencimiento_libera_y_pagar_fija(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "VEN-1", "Disco duro")
    _in(client, p["id"], main["id"], 2)
    _cfg(client, enforce_stock=True, hold_hours_manual=48, hold_minutes_card=30)
    sinpe = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "SINPE Móvil").json()["number"])
    card = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}], "Tarjeta de crédito").json()["number"])
    r1 = db_session.scalar(select(StockReservation).where(StockReservation.order_id == sinpe.id))
    r2 = db_session.scalar(select(StockReservation).where(StockReservation.order_id == card.id))
    now = datetime.now(UTC)
    assert timedelta(hours=47) < rsv._aware(r1.expires_at) - now <= timedelta(hours=48)
    assert timedelta(minutes=29) < rsv._aware(r2.expires_at) - now <= timedelta(minutes=30)
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "agotado"

    # el de SINPE se paga: la reserva deja de vencer
    assert client.patch(f"/orders/{sinpe.id}", json={"status": "pagado"}).status_code == 200
    db_session.refresh(r1)
    assert r1.expires_at is None and r1.status == "confirmada"
    # el de tarjeta no se paga: vence, el worker la libera y deja nota en el pedido
    r2.expires_at = now - timedelta(minutes=1)
    db_session.commit()
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "disponible"  # vencida ya no compromete
    assert sinv.expire_holds(db_session) == 1
    db_session.refresh(r2)
    db_session.refresh(card)
    assert r2.status == "vencida" and "Venció la reserva" in card.notes and card.status == "nuevo"
    assert client.get(f"/store/orders/{card.id}/inventory").json()["estado"] == "vencido"
    # si igual lo pagan tarde, se vuelve a reservar (hay stock libre)
    assert client.patch(f"/orders/{card.id}", json={"status": "pagado"}).status_code == 200
    again = db_session.scalars(select(StockReservation).where(StockReservation.order_id == card.id, StockReservation.status == "confirmada")).all()
    assert len(again) == 1 and again[0].expires_at is None

    # con "cancelar al vencer": el pedido sin pagar se cancela solo
    _in(client, p["id"], main["id"], 1)
    _cfg(client, cancel_unpaid_on_expiry=True)
    late = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    r3 = db_session.scalar(select(StockReservation).where(StockReservation.order_id == late.id))
    r3.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert client.post("/stock/reservations/expire").json()["expired"] == 1
    db_session.refresh(late)
    assert late.status == "cancelado"


def test_cancelar_libera_reserva_y_solicitud(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "CAN-1", "Router")
    _in(client, p["id"], main["id"], 1)
    _supplier(db_session, p["id"], 3)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 2}]).json()["number"])
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "bajo_pedido"
    assert client.patch(f"/orders/{o.id}", json={"status": "cancelado"}).status_code == 200
    r = db_session.scalar(select(StockReservation).where(StockReservation.order_id == o.id))
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    db_session.refresh(r)
    db_session.refresh(req)
    assert r.status == "cancelada" and req.status == "cancelada"
    assert _avail(client, p["id"]).json()[str(p["id"])]["estado"] == "disponible"
    assert _ledger(db_session, p["id"]) == 1  # reservar y cancelar nunca toca el ledger


# ---------- 4) despacho y factura: una sola salida ----------
def test_despacho_luego_factura_descuenta_una_vez(client, auth, db_session):
    _publish(client)
    main, truck = _whs(client)
    p = _prod(client, "DES-1", "Cámara bala")
    _in(client, p["id"], truck["id"], 3)  # solo hay en el vehiculo: la reserva sale de ahi
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 2}]).json()["number"])
    client.patch(f"/orders/{o.id}", json={"status": "pagado"})
    assert client.patch(f"/orders/{o.id}", json={"status": "enviado"}).status_code == 200
    assert _ledger(db_session, p["id"]) == 1
    mv = db_session.scalars(select(StockMovement).where(StockMovement.order_id == o.id)).all()
    assert len(mv) == 1 and mv[0].warehouse_id == truck["id"] and D(str(mv[0].quantity)) == -2 and mv[0].invoice_id is None
    r = db_session.scalar(select(StockReservation).where(StockReservation.order_id == o.id))
    db_session.refresh(r)
    assert r.status == "consumida" and D(str(r.consumed_quantity)) == 2
    # la factura no vuelve a descontar: liga la salida del despacho
    te = client.post(f"/orders/{o.id}/invoice", params={"doc_type": "TE"})
    assert te.status_code == 201, te.text
    assert _ledger(db_session, p["id"]) == 1
    db_session.refresh(mv[0])
    assert mv[0].invoice_id == te.json()["invoice_id"]
    # entregado: nada mas
    client.patch(f"/orders/{o.id}", json={"status": "entregado"})
    assert _ledger(db_session, p["id"]) == 1
    assert client.get(f"/store/orders/{o.id}/inventory").json()["estado"] == "despachado"
    # anular la factura devuelve la mercaderia (como siempre)
    assert client.post(f"/invoices/{te.json()['invoice_id']}/void").status_code == 200
    assert _ledger(db_session, p["id"]) == 3


def test_factura_luego_despacho_y_pedido_viejo(client, auth, db_session):
    _publish(client)
    main, truck = _whs(client)
    p = _prod(client, "DES-2", "NVR 4 canales")
    _in(client, p["id"], truck["id"], 2)
    o = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    te = client.post(f"/orders/{o.id}/invoice", params={"doc_type": "TE"})
    assert te.status_code == 201
    assert _ledger(db_session, p["id"]) == 1
    sale = db_session.scalars(select(StockMovement).where(StockMovement.order_id == o.id)).all()
    assert len(sale) == 1 and sale[0].warehouse_id == truck["id"] and sale[0].invoice_id == te.json()["invoice_id"]  # de la bodega reservada
    client.patch(f"/orders/{o.id}", json={"status": "enviado"})
    client.patch(f"/orders/{o.id}", json={"status": "entregado"})
    assert _ledger(db_session, p["id"]) == 1

    # pedido viejo (sin reservas: capa apagada): la factura descuenta como siempre y el despacho no repite
    _in(client, p["id"], main["id"], 2)
    _cfg(client, enabled=False)
    old = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    client.post(f"/orders/{old.id}/invoice", params={"doc_type": "TE"})
    assert _ledger(db_session, p["id"]) == 2
    client.patch(f"/orders/{old.id}", json={"status": "enviado"})
    assert _ledger(db_session, p["id"]) == 2
    # pedido viejo despachado SIN factura: sale una vez al despachar y la factura posterior no repite
    old2 = _order(db_session, _buy(client, [{"product_id": p["id"], "quantity": 1}]).json()["number"])
    client.patch(f"/orders/{old2.id}", json={"status": "enviado"})
    assert _ledger(db_session, p["id"]) == 1
    client.post(f"/orders/{old2.id}/invoice", params={"doc_type": "TE"})
    assert _ledger(db_session, p["id"]) == 1


# ---------- 5) panel y publicacion ----------
def test_panel_inventario_web_y_publicacion(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    a = _prod(client, "PAN-A", "Cámara PTZ", price=100000)
    b = _prod(client, "PAN-B", "Cable UTP")
    _in(client, a["id"], main["id"], 4)
    _buy(client, [{"product_id": a["id"], "quantity": 1}])
    inv = client.get("/store/inventory").json()
    rows = {x["id"]: x for x in inv["items"]}
    assert [D(str(rows[a["id"]][k])) for k in ("propio", "reservado_pedidos", "libre")] == [4, 1, 3]
    assert rows[b["id"]]["estado"] == "agotado" and rows[b["id"]]["alerta"] is True
    assert inv["summary"]["alertas"] >= 1 and D(str(inv["summary"]["reservado_pedidos"])) >= 1
    assert any(h["product_id"] == a["id"] and h["order"] for h in inv["holds"])
    assert all(x["alerta"] for x in client.get("/store/inventory", params={"estado": "alertas"}).json()["items"])
    assert [x["id"] for x in client.get("/store/inventory", params={"estado": "reservado"}).json()["items"]] == [a["id"]]
    assert {w["web"] for w in inv["settings"]["warehouses"]} == {True}

    # precio web y destacado: la vitrina y el checkout usan el precio web
    r = client.patch(f"/store/inventory/products/{a['id']}", json={"web_price": 90000, "web_featured": True})
    assert r.status_code == 200 and r.json()["web_featured"] is True
    pub = client.get(f"/public/store/crimson/products/{a['id']}").json()
    assert D(str(pub["price"])) == 90000 and pub["featured"] is True and D(str(pub["display_price"])) == D("101700.00")
    q = client.post("/public/store/crimson/quote", json={"lines": [{"product_id": a["id"], "quantity": 1}], "contact": {"name": "X"}}).json()
    assert D(str(q["total"])) == D("101700.00")
    # quitar el precio web vuelve al de siempre; despublicar lo saca de la tienda
    client.patch(f"/store/inventory/products/{a['id']}", json={"web_price": None})
    assert D(str(client.get(f"/public/store/crimson/products/{a['id']}").json()["price"])) == 100000
    client.patch(f"/store/inventory/products/{b['id']}", json={"show_on_web": False})
    assert b["id"] not in {p["id"] for p in client.get("/public/store/crimson/products").json()}
    # ajustes validados
    assert client.put("/store/inventory/settings", json={"quantity_display": "todo"}).status_code == 422
    assert client.put("/store/inventory/settings", json={"warehouse_ids": [99999]}).status_code == 404


def test_avisarme_cuando_haya(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "AVI-1", "Kit alarma")
    _cfg(client, out_of_stock_action="avisarme")
    assert _avail(client, p["id"]).json()[str(p["id"])]["accion"] == "avisarme"
    url = "/public/store/crimson/stock-alert"
    assert client.post(url, json={"product_id": p["id"]}).status_code == 422
    assert client.post(url, json={"product_id": p["id"], "email": "luis@ejemplo.com"}).status_code == 201
    assert client.post(url, json={"product_id": p["id"], "email": "LUIS@ejemplo.com"}).status_code == 201  # no duplica
    assert client.get("/store/inventory").json()["summary"]["avisos"] == 1
    assert sinv.back_in_stock(db_session) == 0  # sigue agotado
    _in(client, p["id"], main["id"], 1)
    assert sinv.back_in_stock(db_session) == 1
    a = db_session.scalar(select(StoreStockAlert).where(StoreStockAlert.product_id == p["id"]))
    assert a.notified_at and db_session.scalar(select(EmailOutbox).where(EmailOutbox.to == "luis@ejemplo.com"))


def test_worker_tareas_registradas(db_session, monkeypatch):
    import sys

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pytest.importorskip("celery")
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    assert "back-in-stock" in celery_app.celery.conf.beat_schedule

    class _Ctx:
        def __enter__(self):
            return db_session

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx())
    assert celery_app.expire_reservations() == 0 and celery_app.back_in_stock() == 0


# ---------- 6) AC06 de la tienda: dos pedidos simultaneos por la ultima unidad ----------
def _race_orders(Session, ids, n_threads=2):
    tid, pid, _ = ids
    barrier = threading.Barrier(n_threads)
    results: dict[int, object] = {}

    def worker(i):
        db = Session()
        try:
            barrier.wait()
            o = Order(tenant_id=tid, number=f"ORD-R{i}", channel="tienda", contact={}, payment_method="SINPE Móvil")
            o.lines.append(OrderLine(product_id=pid, name="Última cámara", quantity=1, unit_price=1))
            db.add(o)
            db.flush()
            sinv.hold_for_order(db, o)
            results[i] = "ok"
        except HTTPException as e:
            results[i] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001
            results[i] = repr(e)
        finally:
            db.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    return results


def _enforce(Session):
    s = Session()
    t = s.scalar(select(Tenant))
    st = {**(t.settings or {})}
    st[sinv.KEY] = {**sinv.DEFAULTS, "enforce_stock": True}
    t.settings = st
    s.commit()
    s.close()


def _check_one_wins(Session, ids, results, stock=1):
    oks = [v for v in results.values() if v == "ok"]
    bad = [v for v in results.values() if v != "ok"]
    assert len(oks) == stock, results
    assert all(isinstance(v, tuple) and v[0] == 409 and "Ya no hay suficiente" in v[1] for v in bad), results
    s = Session()
    tid, pid, wid = ids
    _, comp, libre = rsv.available(s, tid, pid, wid)
    assert comp == stock and libre == 0  # nunca comprometido > existencia
    # el pedido rechazado no quedo grabado (rollback de todo)
    assert len(s.scalars(select(Order).where(Order.number.like("ORD-R%"))).all()) == stock
    s.close()


def test_ac06_tienda_sqlite_archivo():
    from .test_inventario_reservas import _setup_race_db

    d = tempfile.mkdtemp(prefix="tienda_inv_")
    engine, Session, ids = _setup_race_db(f"sqlite:///{os.path.join(d, 'race.db')}")
    try:
        _enforce(Session)
        _check_one_wins(Session, ids, _race_orders(Session, ids))
    finally:
        engine.dispose()


def test_ac06_tienda_postgres_real():
    """Dos sesiones, dos pedidos web, misma ultima unidad: una reserva pasa y la otra recibe 409 'quedan 0'.
    Luego 6 pedidos contra 2 unidades libres: exactamente 2 pasan."""
    pytest.importorskip("psycopg")
    from app.core.db import Base
    from app.services import inventory as invsvc

    from .test_inventario_reservas import _pg_url, _setup_race_db

    url, srv = _pg_url()
    engine, Session, ids = _setup_race_db(url)
    try:
        _enforce(Session)
        _check_one_wins(Session, ids, _race_orders(Session, ids))
        s = Session()
        invsvc.move(s, ids[0], None, ids[1], ids[2], "entrada", D(2))
        s.commit()
        s.close()
        res = _race_orders(Session, ids, n_threads=6)
        assert list(res.values()).count("ok") == 2, res
        s = Session()
        _, comp, libre = rsv.available(s, *ids)
        assert comp == 3 and libre == 0
        s.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        if srv is not None:
            srv.cleanup()
