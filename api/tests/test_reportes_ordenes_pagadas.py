"""Plan maestro 1.5: el reporte de ordenes suma SOLO lo pagado (PAID_STATES). Un pedido sin pagar, con comprobante en
revision, cancelado o reembolsado no es una venta: antes el total inflaba la cifra de ventas de la tienda."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Order, Tenant
from app.services.documents import CR


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
    hoy = datetime.now(CR).date()  # dias de Costa Rica (el reporte ya no usa la fecha UTC)
    r = client.get("/reports/ordenes", params={"from": (hoy - timedelta(days=1)).isoformat(), "to": hoy.isoformat()})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert len(rep["rows"]) == len(montos) and rep["totals"]["Órdenes"] == len(montos)
    assert Decimal(str(rep["totals"]["Total"])) == Decimal(15000)  # pagado + preparando + listo + enviado + entregado
    assert rep["totals"]["Pagadas"] == 5
    # el archivo exportado lleva el mismo total
    assert client.get("/reports/ordenes", params={"format": "xlsx"}).status_code == 200


def test_reporte_ordenes_usa_el_dia_de_costa_rica(client, auth, db_session):
    """Borde de las 18:00-24:00 CR: un pedido pagado a las 7 p. m. del 31/10 en Costa Rica (01:00 UTC del 1/11) es
    venta de octubre, no de noviembre."""
    tid = db_session.scalar(select(Tenant)).id
    noche = datetime(2026, 11, 1, 1, 0, tzinfo=UTC)  # 31/10 19:00 CR
    madrugada = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 1/11 00:30 CR
    for i, (cuando, total) in enumerate(((noche, 1000), (madrugada, 50))):
        db_session.add(Order(tenant_id=tid, number=f"ORD-CR{i}", channel="tienda", contact={}, status="pagado", total=total, created_at=cuando))
    db_session.commit()
    octubre = client.get("/reports/ordenes", params={"from": "2026-10-01", "to": "2026-10-31"}).json()
    assert [r[1] for r in octubre["rows"]] == ["ORD-CR0"] and octubre["rows"][0][0] == "2026-10-31"
    assert Decimal(str(octubre["totals"]["Total"])) == Decimal(1000)
    nov = client.get("/reports/ordenes", params={"from": "2026-11-01", "to": "2026-11-01"}).json()
    assert [r[1] for r in nov["rows"]] == ["ORD-CR1"] and Decimal(str(nov["totals"]["Total"])) == Decimal(50)
