"""Diferencia de Kardex del 21/10 (item 13): "del sensor magnetico salio mas de lo que entro".

Causa: al confirmarse el pago de un pedido web, la factura automatica sacaba de bodega TODO el pedido, tambien lo que
se vende bajo pedido y todavia no se habia comprado ni recibido -> existencia negativa. Ahora lo bajo pedido sale al
despachar (consumiendo la reserva que deja la compra al recibirse). Lo que quedo de antes se corrige con movimientos
documentados: la salida adelantada se devuelve y el negativo que quede se ajusta con motivo (Salud y la herramienta
app/tools/corregir_kardex)."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Order, PurchaseRequest, StockMovement, Tenant
from app.services import inventory as invsvc
from app.tools import corregir_kardex
from tests.test_disponibilidad import supplier_item
from tests.test_tienda_inventario import _buy, _in, _prod, _publish, _whs

D = Decimal


def _pagos(client):
    ck = {
        "methods": {
            "sinpe": {"active": True, "phone": "8888-7777", "holder": "Empresa de Prueba SA"},
            "transferencia": {"active": False},
            "tarjeta": {"active": False},
            "efectivo": {"active": False},
        },
        "auto_invoice": True,  # como en produccion: el pago confirmado crea el comprobante
    }
    assert client.put("/store/checkout", json=ck).status_code == 200


def _saldo(db, pid, wid) -> Decimal:
    db.expire_all()
    return invsvc.on_hand(db, db.scalar(select(StockMovement.tenant_id).limit(1)) or 1, pid, wid)


def _issues(client, kind):
    return [i for i in client.get("/stock/health").json()["issues"] if i["kind"] == kind]


def _pedido_pagado(client, db):
    """Sensor: 1 propio + 2 bajo pedido (Eurocomp). Se compran 3 y se confirma el pago."""
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "SENSOR-MAG", "Sensor magnético para puerta")
    supplier_item(db, p["id"], 5, code="SIRETT-SENSOR")
    _in(client, p["id"], main["id"], 1)
    o = db.scalar(select(Order).where(Order.number == _buy(client, [{"product_id": p["id"], "quantity": 3}]).json()["number"]))
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "pagado", "payment_ref": "SINPE 1"}).status_code == 200
    db.refresh(o)
    assert o.invoice_id, "el pago confirmado crea la factura"
    return p, main, o


def test_pago_facturado_no_saca_lo_bajo_pedido_y_el_despacho_cuadra(client, auth, db_session):
    p, main, o = _pedido_pagado(client, db_session)
    # la factura saco solo lo propio (1); lo bajo pedido (2) espera la compra: la bodega NO queda en negativo
    assert _saldo(db_session, p["id"], main["id"]) == 0
    assert _issues(client, "negativo") == [] and _issues(client, "pedido_adelantado") == []
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    assert client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"}).status_code == 200
    r = client.patch(f"/purchase-requests/{req.id}", json={"status": "recibida", "warehouse_id": main["id"]})
    assert r.status_code == 200, r.text
    assert _saldo(db_session, p["id"], main["id"]) == 2  # entro la compra, apartada para el pedido
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "enviado", "tracking_number": "G-1"}).status_code == 200
    assert _saldo(db_session, p["id"], main["id"]) == 0
    # salio exactamente lo del pedido, todo ligado a su factura
    db_session.expire_all()
    salidas = db_session.scalars(select(StockMovement).where(StockMovement.product_id == p["id"], StockMovement.quantity < 0)).all()
    assert sum(-D(str(m.quantity)) for m in salidas) == 3 and all(m.invoice_id == o.invoice_id for m in salidas)
    assert client.get("/stock/health").json()["total"] == 0


def test_salida_adelantada_de_antes_se_devuelve_documentada(client, auth, db_session):
    """Lo que dejo la version anterior: la factura del pago saco tambien lo bajo pedido -> -2 en bodega."""
    p, main, o = _pedido_pagado(client, db_session)
    invsvc.move(db_session, o.tenant_id, None, p["id"], main["id"], "venta", D(-2), "FAC-VIEJA", f"Pedido {o.number}", None, o.invoice_id, movement_type="venta", order_id=o.id)
    db_session.commit()
    assert _saldo(db_session, p["id"], main["id"]) == -2
    neg = _issues(client, "negativo")
    assert len(neg) == 1 and neg[0]["fixable"] and neg[0]["warehouse_id"] == main["id"]
    assert "salió más de lo que entró" in neg[0]["detail"] and "Compra sin recibir" in neg[0]["detail"]
    adel = _issues(client, "pedido_adelantado")
    assert len(adel) == 1 and adel[0]["ref_id"] == o.id
    # con una compra sin recibir, ajustar no es la correccion: se rechaza salvo que se confirme
    r = client.post("/stock/health/fix", json={"kind": "negativo", "ref_id": p["id"], "warehouse_id": main["id"], "motivo": "Conteo físico"})
    assert r.status_code == 409 and "sin recibir" in r.json()["detail"]
    # la correccion buena: devolver la salida adelantada (ligada al pedido y a su factura)
    r = client.post("/stock/health/fix", json={"kind": "pedido_adelantado", "ref_id": o.id})
    assert r.status_code == 200 and r.json()["movimientos"] == 1
    assert _saldo(db_session, p["id"], main["id"]) == 0
    assert _issues(client, "negativo") == [] and _issues(client, "pedido_adelantado") == []
    # y el ciclo sigue normal: llega la compra, se despacha, cuadra
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    client.patch(f"/purchase-requests/{req.id}", json={"status": "solicitada"})
    client.patch(f"/purchase-requests/{req.id}", json={"status": "recibida", "warehouse_id": main["id"]})
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "enviado", "tracking_number": "G-2"}).status_code == 200
    assert _saldo(db_session, p["id"], main["id"]) == 0
    assert client.get("/stock/health").json()["total"] == 0


def test_negativo_sin_compra_se_ajusta_con_motivo(client, auth, db_session):
    main, _ = _whs(client)
    p = _prod(client, "SENSOR-AJ", "Sensor de prueba")
    r = client.post("/stock/movements", json={"product_id": p["id"], "warehouse_id": main["id"], "kind": "salida", "quantity": 1})
    assert r.status_code == 201, r.text
    assert _saldo(db_session, p["id"], main["id"]) == -1
    base = {"kind": "negativo", "ref_id": p["id"], "warehouse_id": main["id"]}
    assert client.post("/stock/health/fix", json={**base, "motivo": ""}).status_code == 422
    r = client.post("/stock/health/fix", json={**base, "motivo": "Conteo físico: no hay unidades en bodega"})
    assert r.status_code == 200 and r.json()["movimientos"] == 1
    assert _saldo(db_session, p["id"], main["id"]) == 0
    aj = db_session.scalar(select(StockMovement).where(StockMovement.product_id == p["id"], StockMovement.kind == "ajuste"))
    assert D(str(aj.quantity)) == 1 and aj.reference.startswith("AJ-SALUD-") and "Conteo físico" in aj.note and aj.created_by
    # ya no esta en negativo: no se vuelve a ajustar
    assert client.post("/stock/health/fix", json={**base, "motivo": "otra vez"}).status_code == 409


def test_herramienta_corregir_kardex(client, auth, db_session):
    tid = auth["tenant"]["id"]
    p, main, o = _pedido_pagado(client, db_session)
    invsvc.move(db_session, tid, None, p["id"], main["id"], "venta", D(-2), "FAC-VIEJA", None, None, o.invoice_id, movement_type="venta", order_id=o.id)
    otro = _prod(client, "SENSOR-TOOL", "Sensor sin compra")
    invsvc.move(db_session, tid, None, otro["id"], main["id"], "salida", D(-1), "PRUEBA")
    db_session.commit()
    plan = corregir_kardex.plan(db_session, tid)
    assert [a["order"] for a in plan["adelantadas"]] == [o.number]
    assert {n["product_id"] for n in plan["negativos"]} == {p["id"], otro["id"]}
    res = corregir_kardex.apply(db_session, tid, "Corrección de la diferencia de Kardex del 21/10")
    db_session.commit()
    assert res["devoluciones"] == 1 and res["ajustes"] == 1 and res["omitidos"] == []
    assert _saldo(db_session, p["id"], main["id"]) == 0 and _saldo(db_session, otro["id"], main["id"]) == 0
    # dos veces no duplica
    assert corregir_kardex.plan(db_session, tid) == {"adelantadas": [], "negativos": []}
    assert db_session.get(Tenant, tid) is not None
