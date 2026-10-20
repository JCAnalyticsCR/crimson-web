"""Plan maestro 1.5: el reporte de ordenes suma SOLO lo pagado (PAID_STATES). Un pedido sin pagar, con comprobante en
revision, cancelado o reembolsado no es una venta: antes el total inflaba la cifra de ventas de la tienda."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Order, Tenant


def test_reporte_ordenes_suma_solo_pagadas(client, auth, db_session):
    tid = db_session.scalar(select(Tenant)).id
    montos = {
        "nuevo": 100,
        "en_revision": 200,
        "pagado": 1000,
        "preparando": 2000,
        "listo": 3000,
        "enviado": 4000,
        "entregado": 5000,
        "cancelado": 700,
        "reembolsado": 900,
    }
    for i, (status, total) in enumerate(montos.items()):
        db_session.add(Order(tenant_id=tid, number=f"ORD-RP{i}", channel="tienda", contact={"name": "Cliente"}, status=status, total=total))
    db_session.commit()
    r = client.get("/reports/ordenes")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert len(rep["rows"]) == len(montos) and rep["totals"]["Órdenes"] == len(montos)
    assert Decimal(str(rep["totals"]["Total"])) == Decimal(15000)  # pagado + preparando + listo + enviado + entregado
    assert rep["totals"]["Pagadas"] == 5
    # el archivo exportado lleva el mismo total
    assert client.get("/reports/ordenes", params={"format": "xlsx"}).status_code == 200
