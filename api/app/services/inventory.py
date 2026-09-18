"""Inventario por ubicacion con ledger; la venta descuenta del inventario predeterminado del tenant."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Invoice, Product, StockMovement, Warehouse


def default_warehouse(db: Session, tenant_id: int) -> Warehouse | None:
    return db.scalar(select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.active).order_by(Warehouse.is_default.desc(), Warehouse.id))


def stock_levels(db: Session, tenant_id: int, product_id: int | None = None, warehouse_id: int | None = None) -> list[dict]:
    q = (
        select(StockMovement.product_id, StockMovement.warehouse_id, func.coalesce(func.sum(StockMovement.quantity), 0).label("qty"))
        .where(StockMovement.tenant_id == tenant_id)
        .group_by(StockMovement.product_id, StockMovement.warehouse_id)
    )
    if product_id:
        q = q.where(StockMovement.product_id == product_id)
    if warehouse_id:
        q = q.where(StockMovement.warehouse_id == warehouse_id)
    return [{"product_id": r.product_id, "warehouse_id": r.warehouse_id, "quantity": Decimal(str(r.qty))} for r in db.execute(q)]


def move(
    db: Session,
    tenant_id: int,
    user_id: int | None,
    product_id: int,
    warehouse_id: int,
    kind: str,
    quantity: Decimal,
    reference: str | None = None,
    note: str | None = None,
    unit_cost=None,
    invoice_id: int | None = None,
) -> StockMovement:
    p = db.get(Product, product_id)
    w = db.get(Warehouse, warehouse_id)
    if not p or p.tenant_id != tenant_id or not w or w.tenant_id != tenant_id:
        raise HTTPException(404, "Producto o inventario no encontrado")
    if p.item_type != "producto":
        raise HTTPException(422, "Los servicios no llevan inventario")
    m = StockMovement(
        tenant_id=tenant_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        kind=kind,
        quantity=quantity,
        unit_cost=unit_cost,
        reference=reference,
        note=note,
        invoice_id=invoice_id,
        at=datetime.now(UTC),
        created_by=user_id,
    )
    db.add(m)
    db.flush()
    return m


def transfer(db: Session, tenant_id: int, user_id: int | None, product_id: int, from_id: int, to_id: int, quantity: Decimal, note: str | None = None) -> None:
    if from_id == to_id:
        raise HTTPException(422, "Origen y destino deben ser distintos")
    ref = f"TRF-{datetime.now(UTC):%Y%m%d%H%M%S}"
    move(db, tenant_id, user_id, product_id, from_id, "transferencia_out", -abs(quantity), ref, note)
    move(db, tenant_id, user_id, product_id, to_id, "transferencia_in", abs(quantity), ref, note)


def deduct_for_invoice(db: Session, inv: Invoice, user_id: int | None) -> int:
    """Descuenta cada linea de producto del inventario predeterminado. Sin inventario configurado: no hace nada."""
    w = default_warehouse(db, inv.tenant_id)
    if not w:
        return 0
    n = 0
    for ln in inv.lines:
        if not ln.product_id:
            continue
        p = db.get(Product, ln.product_id)
        if p and p.item_type == "producto":
            move(db, inv.tenant_id, user_id, p.id, w.id, "venta", -Decimal(str(ln.quantity)), inv.number, None, None, inv.id)
            n += 1
    return n


def restock_for_void(db: Session, inv: Invoice, user_id: int | None) -> int:
    rows = db.scalars(select(StockMovement).where(StockMovement.invoice_id == inv.id, StockMovement.kind == "venta")).all()
    for r in rows:
        move(db, inv.tenant_id, user_id, r.product_id, r.warehouse_id, "devolucion", -Decimal(str(r.quantity)), inv.number, "anulacion", None, inv.id)
    return len(rows)


def low_stock(db: Session, tenant_id: int) -> list[dict]:
    levels = {}
    for lv in stock_levels(db, tenant_id):
        levels[lv["product_id"]] = levels.get(lv["product_id"], Decimal(0)) + lv["quantity"]
    out = []
    for p in db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.active, Product.item_type == "producto", Product.min_stock > 0)):
        q = levels.get(p.id, Decimal(0))
        if q <= p.min_stock:
            out.append({"product_id": p.id, "code": p.code, "name": p.name, "quantity": q, "min_stock": p.min_stock})
    return out
