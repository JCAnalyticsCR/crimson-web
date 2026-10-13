"""Reservas formales de inventario (documento de Andres §6, AC06 y AC25).

- "Ver existencia no es retirar" (AC25): una reserva nace pendiente y la confirma quien tiene
  inventory.aprobar_reserva, salvo la regla automatica de Ajustes (proyectos propios hasta X unidades).
- Solo las reservas CONFIRMADAS y vigentes comprometen existencia: disponible = existencia - comprometido.
- AC06: confirmar es atomico. Postgres: fila de control (stock_lock) con SELECT ... FOR UPDATE. SQLite
  (una replica): candado del proceso + escritura de la fila de control, que serializa la transaccion.
  Dos confirmaciones sobre la ultima unidad: una pasa y la otra recibe 409. Nunca comprometido > existencia
  en el momento de confirmar.
- Lo existente no cambia: ventas, POS, ordenes de trabajo y ajustes siguen pudiendo dejar stock negativo.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..models import Product, Project, StockLock, StockReservation, Tenant, Warehouse, WorkOrder
from . import inventory as invsvc
from .documents import audit

OPEN = ("pendiente", "confirmada")
DEFAULT_DAYS = 15

_sqlite_locks: dict[tuple[int, int, int], threading.Lock] = {}
_sqlite_guard = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite devuelve fechas sin zona; se tratan como UTC
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _dec(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def settings_of(db: Session, tenant_id: int) -> dict:
    t = db.get(Tenant, tenant_id)
    return (t.settings or {}) if t else {}


def _vigente():
    return or_(StockReservation.expires_at.is_(None), StockReservation.expires_at > _now())


def committed(db: Session, tenant_id: int, product_id: int | None = None, warehouse_id: int | None = None, exclude_id: int | None = None) -> dict:
    """{(product_id, warehouse_id): cantidad} comprometida por reservas confirmadas y vigentes (lo no consumido)."""
    q = (
        select(
            StockReservation.product_id,
            StockReservation.warehouse_id,
            func.coalesce(func.sum(StockReservation.quantity - StockReservation.consumed_quantity), 0),
        )
        .where(StockReservation.tenant_id == tenant_id, StockReservation.status == "confirmada", _vigente())
        .group_by(StockReservation.product_id, StockReservation.warehouse_id)
    )
    if product_id:
        q = q.where(StockReservation.product_id == product_id)
    if warehouse_id:
        q = q.where(StockReservation.warehouse_id == warehouse_id)
    if exclude_id:
        q = q.where(StockReservation.id != exclude_id)
    return {(pid, wid): max(_dec(qty), Decimal(0)) for pid, wid, qty in db.execute(q)}


def committed_by_project(db: Session, tenant_id: int) -> dict:
    """{(project_id, product_id): cantidad} confirmada y vigente. project_id None = reserva sin proyecto."""
    q = (
        select(StockReservation.project_id, StockReservation.product_id, func.sum(StockReservation.quantity - StockReservation.consumed_quantity))
        .where(StockReservation.tenant_id == tenant_id, StockReservation.status == "confirmada", _vigente())
        .group_by(StockReservation.project_id, StockReservation.product_id)
    )
    return {(proj, pid): max(_dec(qty), Decimal(0)) for proj, pid, qty in db.execute(q)}


def available(db: Session, tenant_id: int, product_id: int, warehouse_id: int, exclude_id: int | None = None) -> tuple[Decimal, Decimal, Decimal]:
    """(existencia, comprometido, disponible) de un producto en una bodega."""
    stock = invsvc.on_hand(db, tenant_id, product_id, warehouse_id)
    comp = committed(db, tenant_id, product_id, warehouse_id, exclude_id).get((product_id, warehouse_id), Decimal(0))
    return stock, comp, stock - comp


# ---------- candado por (empresa, producto, bodega) ----------
def _lock(db: Session, tenant_id: int, product_id: int, warehouse_id: int) -> threading.Lock | None:
    """Toma el candado de la fila de control. Devuelve el candado de proceso (SQLite) para soltarlo al final."""
    dialect = db.get_bind().dialect.name
    vals = {"tenant_id": tenant_id, "product_id": product_id, "warehouse_id": warehouse_id, "version": 0}
    proc_lock = None
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        db.execute(pg_insert(StockLock).values(**vals).on_conflict_do_nothing(constraint="uq_stock_lock"))
        db.execute(
            select(StockLock.id)
            .where(StockLock.tenant_id == tenant_id, StockLock.product_id == product_id, StockLock.warehouse_id == warehouse_id)
            .with_for_update()
        )
    else:
        key = (tenant_id, product_id, warehouse_id)
        with _sqlite_guard:
            proc_lock = _sqlite_locks.setdefault(key, threading.Lock())
        proc_lock.acquire()
        try:
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as lite_insert

                db.execute(lite_insert(StockLock).values(**vals).on_conflict_do_nothing())
            elif not db.scalar(
                select(StockLock.id).where(StockLock.tenant_id == tenant_id, StockLock.product_id == product_id, StockLock.warehouse_id == warehouse_id)
            ):
                db.add(StockLock(**vals))
                db.flush()
        except Exception:
            proc_lock.release()
            raise
    # escribir la fila de control abre la transaccion de escritura: en SQLite serializa a otros procesos
    db.execute(
        update(StockLock)
        .where(StockLock.tenant_id == tenant_id, StockLock.product_id == product_id, StockLock.warehouse_id == warehouse_id)
        .values(version=StockLock.version + 1)
    )
    return proc_lock


# ---------- ciclo de vida ----------
def create(
    db: Session,
    tenant_id: int,
    user_id: int | None,
    product_id: int,
    quantity: Decimal,
    warehouse_id: int | None = None,
    project_id: int | None = None,
    survey_id: int | None = None,
    quote_id: int | None = None,
    reason: str | None = None,
    expires_at: datetime | None = None,
    ip: str | None = None,
) -> StockReservation:
    quantity = _dec(quantity)
    if quantity <= 0:
        raise HTTPException(422, "La cantidad a reservar debe ser mayor que cero")
    prod = db.get(Product, product_id)
    if not prod or prod.tenant_id != tenant_id:
        raise HTTPException(404, "Producto no encontrado")
    if prod.item_type != "producto":
        raise HTTPException(422, "Los servicios no llevan inventario")
    if warehouse_id:
        w = db.get(Warehouse, warehouse_id)
        if not w or w.tenant_id != tenant_id:
            raise HTTPException(404, "Inventario no encontrado")
    else:
        w = (db.get(Warehouse, prod.warehouse_id) if prod.warehouse_id else None) or invsvc.default_warehouse(db, tenant_id)
        if not w or w.tenant_id != tenant_id:
            raise HTTPException(422, "No hay bodega configurada")
    if project_id:
        pr = db.get(Project, project_id)
        if not pr or pr.tenant_id != tenant_id:
            raise HTTPException(404, "Proyecto no encontrado")
    if expires_at is None:
        days = int(settings_of(db, tenant_id).get("reservation_days") or DEFAULT_DAYS)
        expires_at = _now() + timedelta(days=days)
    r = StockReservation(
        tenant_id=tenant_id,
        product_id=prod.id,
        warehouse_id=w.id,
        quantity=quantity,
        consumed_quantity=Decimal(0),
        project_id=project_id,
        survey_id=survey_id,
        quote_id=quote_id,
        status="pendiente",
        expires_at=expires_at,
        requested_by=user_id,
        reason=reason,
    )
    db.add(r)
    db.flush()
    audit(db, tenant_id, user_id, "create", "stock_reservation", r.id, {"product": prod.code, "qty": str(quantity)}, ip=ip)
    return r


def auto_approves(db: Session, r: StockReservation) -> bool:
    """Regla de Ajustes: reservas de proyectos propios hasta X unidades se confirman solas (0 = nunca)."""
    limit = _dec(settings_of(db, r.tenant_id).get("reservation_auto_approve_units") or 0)
    return bool(r.project_id) and limit > 0 and _dec(r.quantity) <= limit


def confirm(db: Session, r: StockReservation, user_id: int | None, ip: str | None = None) -> StockReservation:
    """Confirma bajo candado y hace commit (el candado vive hasta el commit). 409 si no alcanza."""
    if r.status != "pendiente":
        raise HTTPException(409, f"La reserva está {r.status}; solo se confirma una pendiente")
    if r.expires_at and _aware(r.expires_at) <= _now():
        r.status, r.closed_note = "vencida", "Venció antes de confirmarse"
        db.commit()
        raise HTTPException(409, "La reserva venció antes de confirmarse")
    rid, tenant_id, product_id, warehouse_id = r.id, r.tenant_id, r.product_id, r.warehouse_id
    proc_lock = None
    try:
        proc_lock = _lock(db, tenant_id, product_id, warehouse_id)
        # bajo el candado: releer la reserva (otro pudo cancelarla o confirmarla) y la disponibilidad
        status = db.scalar(select(StockReservation.status).where(StockReservation.id == rid))
        if status != "pendiente":
            raise HTTPException(409, f"La reserva ya está {status}")
        stock, comp, libre = available(db, tenant_id, product_id, warehouse_id, exclude_id=rid)
        qty = _dec(r.quantity)
        if qty > libre:
            prod = db.get(Product, product_id)
            raise HTTPException(
                409,
                f"No alcanza la existencia de {prod.name if prod else 'el producto'}: hay {stock.normalize():f}, "
                f"ya comprometido {comp.normalize():f}, libre {max(libre, Decimal(0)).normalize():f} y la reserva pide {qty.normalize():f}. "
                "Otra reserva pudo confirmarse primero.",
            )
        now = _now()
        db.execute(
            update(StockReservation)
            .where(StockReservation.id == rid, StockReservation.status == "pendiente")
            .values(status="confirmada", approved_by=user_id, approved_at=now, updated_at=now)
        )
        audit(db, tenant_id, user_id, "confirm", "stock_reservation", rid, {"qty": str(qty), "libre_antes": str(libre)}, ip=ip)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if proc_lock is not None:
            proc_lock.release()
    db.refresh(r)
    return r


def cancel(db: Session, r: StockReservation, user_id: int | None, note: str | None = None, ip: str | None = None) -> StockReservation:
    if r.status not in OPEN:
        raise HTTPException(409, f"La reserva está {r.status}; no se puede cancelar")
    r.status, r.closed_note = "cancelada", (note or "Cancelada")[:300]
    audit(db, r.tenant_id, user_id, "cancel", "stock_reservation", r.id, {"note": note}, ip=ip)
    return r


def expire_due(db: Session) -> int:
    """Worker: pendientes o confirmadas cuyo vencimiento paso -> vencida (liberan lo comprometido)."""
    now = _now()
    ids = db.scalars(
        select(StockReservation.id).where(StockReservation.status.in_(OPEN), StockReservation.expires_at.is_not(None), StockReservation.expires_at <= now)
    ).all()
    if ids:
        db.execute(update(StockReservation).where(StockReservation.id.in_(ids)).values(status="vencida", closed_note="Venció sin usarse", updated_at=now))
    db.commit()
    return len(ids)


def consume_for_order(db: Session, o: WorkOrder) -> int:
    """Al cerrar la OT: lo usado consume las reservas confirmadas del proyecto (no toca el descuento de
    inventario, que sigue haciendo _apply_materials). Si el proyecto ya no tiene ordenes abiertas, lo
    reservado que no se uso se libera. Devuelve cuantas reservas cambiaron."""
    if not o.project_id:
        return 0
    rows = db.scalars(
        select(StockReservation)
        .where(StockReservation.tenant_id == o.tenant_id, StockReservation.project_id == o.project_id, StockReservation.status == "confirmada")
        .order_by(StockReservation.id)
    ).all()
    if not rows:
        return 0
    changed: set[int] = set()
    for m in o.materials:
        resto = _dec(m.quantity)
        if not m.product_id or resto <= 0:
            continue
        # primero la bodega de la orden, luego las demas
        cand = sorted((r for r in rows if r.product_id == m.product_id and r.status == "confirmada"), key=lambda r: (r.warehouse_id != o.warehouse_id, r.id))
        for r in cand:
            if resto <= 0:
                break
            pend = _dec(r.quantity) - _dec(r.consumed_quantity)
            take = min(pend, resto)
            if take <= 0:
                continue
            r.consumed_quantity = _dec(r.consumed_quantity) + take
            resto -= take
            changed.add(r.id)
            if _dec(r.consumed_quantity) >= _dec(r.quantity):
                r.status, r.closed_note = "consumida", f"Usada en {o.number}"
    abiertas = db.scalar(
        select(func.count(WorkOrder.id)).where(
            WorkOrder.tenant_id == o.tenant_id,
            WorkOrder.project_id == o.project_id,
            WorkOrder.id != o.id,
            WorkOrder.status.not_in(("finalizada", "cancelada")),
            WorkOrder.trashed_at.is_(None),
        )
    )
    if not abiertas:
        for r in rows:
            if r.status != "confirmada":
                continue
            changed.add(r.id)
            if _dec(r.consumed_quantity) > 0:
                r.status, r.closed_note = "consumida", f"Proyecto sin órdenes abiertas: se usó {_dec(r.consumed_quantity).normalize():f}, el resto se liberó"
            else:
                r.status, r.closed_note = "cancelada", "Proyecto sin órdenes abiertas: no se usó, se liberó"
    return len(changed)
