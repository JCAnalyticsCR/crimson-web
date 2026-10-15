"""Inventario por ubicacion con ledger; la venta descuenta del inventario predeterminado del tenant."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Invoice, Product, StockMovement, Tenant, Warehouse


def default_warehouse(db: Session, tenant_id: int) -> Warehouse | None:
    return db.scalar(select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.active).order_by(Warehouse.is_default.desc(), Warehouse.id))


EXTRA_FIELDS = {"project_id", "serial", "movement_type", "voucher_media", "transfer_price", "owner", "custodian", "location", "order_id"}


def prevent_negative(db: Session, tenant_id: int) -> bool:
    """Ajustes › "Impedir stock negativo en salidas". Apagado (defecto): todo sigue como siempre."""
    t = db.get(Tenant, tenant_id)
    return bool(t and (t.settings or {}).get("prevent_negative_stock"))


def on_hand(db: Session, tenant_id: int, product_id: int, warehouse_id: int) -> Decimal:
    q = select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
        StockMovement.tenant_id == tenant_id, StockMovement.product_id == product_id, StockMovement.warehouse_id == warehouse_id
    )
    return Decimal(str(db.scalar(q) or 0))


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
    **extra,
) -> StockMovement:
    """extra (opcional): project_id, serial, movement_type, voucher_media, transfer_price, owner, custodian, location, order_id."""
    p = db.get(Product, product_id)
    w = db.get(Warehouse, warehouse_id)
    if not p or p.tenant_id != tenant_id or not w or w.tenant_id != tenant_id:
        raise HTTPException(404, "Producto o inventario no encontrado")
    if p.item_type != "producto":
        raise HTTPException(422, "Los servicios no llevan inventario")
    bad = set(extra) - EXTRA_FIELDS
    if bad:
        raise TypeError(f"campos de movimiento desconocidos: {sorted(bad)}")
    if Decimal(str(quantity)) < 0 and prevent_negative(db, tenant_id):
        saldo = on_hand(db, tenant_id, product_id, warehouse_id)
        if saldo + Decimal(str(quantity)) < 0:
            raise HTTPException(
                409,
                f"No hay existencia suficiente de {p.name} en {w.name}: hay {saldo.normalize():f} y salen {abs(Decimal(str(quantity))).normalize():f}. "
                "Ajustes tiene activado «Impedir stock negativo en salidas».",
            )
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
        **{k: v for k, v in extra.items() if v is not None},
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


def _invoice_net(db: Session, inv: Invoice) -> dict[tuple[int, int], Decimal]:
    """Lo que el kardex ya movio por esta factura, por (producto, bodega). Negativo = salio."""
    rows = db.execute(
        select(StockMovement.product_id, StockMovement.warehouse_id, func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.tenant_id == inv.tenant_id, StockMovement.invoice_id == inv.id)
        .group_by(StockMovement.product_id, StockMovement.warehouse_id)
    ).all()
    return {(pid, wid): Decimal(str(q or 0)) for pid, wid, q in rows}


def _home_warehouse(db: Session, p: Product) -> Warehouse | None:
    """Bodega de origen del producto si esta activa; si no, la predeterminada (igual que la tienda)."""
    if p.warehouse_id:
        w = db.get(Warehouse, p.warehouse_id)
        if w and w.active and w.tenant_id == p.tenant_id:
            return w
    return default_warehouse(db, p.tenant_id)


def reconcile_invoice(db: Session, inv: Invoice, user_id: int | None, *, reason: str = "factura") -> int:
    """Lleva el kardex de una factura a lo que dicen sus lineas HOY: crearla saca, editarla saca o devuelve la
    diferencia, anularla devuelve lo que falte. Idempotente: llamarla dos veces no mueve nada la segunda.

    - Factura de un PROYECTO: concilia el proyecto (lo ya instalado no vuelve a bodega aunque se anule).
    - Factura de un PEDIDO WEB: el pedido manda (store_inventory); aca solo se devuelve si se anula.
    """
    from ..models import Order
    from . import project_stock

    net = _invoice_net(db, inv)
    pr = project_stock.project_for_invoice(db, inv)
    if pr is not None:
        ids = {ln.product_id for ln in inv.lines if ln.product_id} | {pid for pid, _ in net}
        return len(project_stock.reconcile(db, pr, ids, user_id, reason=reason, invoice=inv))
    voided = inv.status == "anulada"
    if db.scalar(select(Order.id).where(Order.tenant_id == inv.tenant_id, Order.invoice_id == inv.id)) is not None and not voided:
        return 0
    target: dict[int, Decimal] = {}
    if not voided:
        for ln in inv.lines:
            if ln.product_id:
                target[ln.product_id] = target.get(ln.product_id, Decimal(0)) + Decimal(str(ln.quantity))
    out: dict[int, Decimal] = {}
    for (pid, _wid), q in net.items():
        out[pid] = out.get(pid, Decimal(0)) - q
    n = 0
    for pid in sorted(set(target) | set(out)):
        p = db.get(Product, pid)
        if p is None or p.tenant_id != inv.tenant_id or p.item_type != "producto":
            continue
        delta = target.get(pid, Decimal(0)) - out.get(pid, Decimal(0))  # > 0 falta sacar; < 0 sobra afuera
        if delta > 0:
            w = _home_warehouse(db, p)
            if w is None:
                continue  # sin bodega configurada: no hace nada (como siempre)
            move(db, inv.tenant_id, user_id, pid, w.id, "venta", -delta, inv.number, None if reason == "factura" else reason, None, inv.id)
            n += 1
        elif delta < 0:
            back = -delta
            # vuelve a la bodega de donde salio (la que mas saco primero)
            for (q_pid, wid), q in sorted(net.items(), key=lambda kv: kv[1]):
                if q_pid != pid or q >= 0 or back <= 0:
                    continue
                take = min(back, -q)
                move(db, inv.tenant_id, user_id, pid, wid, "devolucion", take, inv.number, "anulacion" if voided else reason, None, inv.id)
                back -= take
                n += 1
    return n


def deduct_for_invoice(db: Session, inv: Invoice, user_id: int | None) -> int:
    """Al crear una factura (ventas, POS, recurrencias, API publica): concilia su inventario."""
    return reconcile_invoice(db, inv, user_id)


def restock_for_void(db: Session, inv: Invoice, user_id: int | None) -> int:
    """Al anular (o al aceptarse la NC): devuelve lo que la factura saco y todavia no volvio. Idempotente."""
    return reconcile_invoice(db, inv, user_id, reason="factura anulada")


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
