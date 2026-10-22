"""Arranque del inventario local (reunion 21/10, item 11): limpiar lo viejo de la tienda anterior y asegurar la bodega.

- Productos viejos: los que vinieron de la tienda anterior (Fygaro: el mapa que deja el importador en
  tenant.settings["fygaro"]["products"]) que hoy no tienen existencia propia (cero en todas las bodegas, ninguna en
  negativo), no estan vinculados al catalogo de un proveedor (lo de Eurocomp lo maneja la sincronizacion: agotado sale
  solo de la tienda y sigue en el panel) y no estan en uso (reservas abiertas ni pedidos web abiertos). Se ARCHIVAN
  con el mecanismo de siempre (services/archive): salen de listas, busquedas y tienda, conservan toda su historia y se
  restauran desde Archivo. Nunca se borran.
- "Bodega principal": la bodega de VENTA donde arranca el inventario local (los equipos fisicos se cargan con
  "Ajuste de inventario" -> Entrada). Si existe se deja activa y de tipo venta; si no, se crea. Por defecto queda como
  predeterminada (de ahi salen las ventas que no dicen bodega).

La herramienta es app/tools/inventario_local.py (por defecto solo muestra; --aplicar escribe).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Order, OrderLine, Product, StockMovement, StockReservation, SupplierItem, Tenant, Warehouse
from . import archive as arch
from .documents import audit

MAIN_NAME = "Bodega principal"
OPEN_ORDER = ("nuevo", "en_revision", "pagado", "preparando", "listo")


def legacy_ids(t: Tenant) -> set[int]:
    """Productos que vinieron de la tienda anterior (los que dejo el importador de Fygaro en su mapa)."""
    pmap = ((t.settings or {}).get("fygaro") or {}).get("products") or {}
    out = set()
    for v in pmap.values():
        try:
            out.add(int((v or {}).get("id")))
        except (TypeError, ValueError):
            continue
    return out


def candidates(db: Session, t: Tenant) -> list[dict]:
    """Lo que se archivaria, con el motivo de cada uno (y lo que se deja, para que se vea por que)."""
    ids = legacy_ids(t)
    if not ids:
        return []
    prods = list(
        db.scalars(
            select(Product).where(
                Product.tenant_id == t.id, Product.id.in_(ids), Product.item_type == "producto", Product.archived_at.is_(None), Product.trashed_at.is_(None)
            )
        )
    )
    if not prods:
        return []
    pids = [p.id for p in prods]
    by_wh: dict[int, list[Decimal]] = {}
    for pid, _wid, q in db.execute(
        select(StockMovement.product_id, StockMovement.warehouse_id, func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.tenant_id == t.id, StockMovement.product_id.in_(pids))
        .group_by(StockMovement.product_id, StockMovement.warehouse_id)
    ):
        by_wh.setdefault(pid, []).append(Decimal(str(q or 0)))
    linked = set(db.scalars(select(SupplierItem.product_id).where(SupplierItem.tenant_id == t.id, SupplierItem.product_id.in_(pids), SupplierItem.active)))
    reserved = set(
        db.scalars(select(StockReservation.product_id).where(StockReservation.tenant_id == t.id, StockReservation.product_id.in_(pids), StockReservation.status.in_(("pendiente", "confirmada"))))
    )
    in_orders = set(
        db.scalars(
            select(OrderLine.product_id)
            .join(Order, Order.id == OrderLine.order_id)
            .where(Order.tenant_id == t.id, OrderLine.product_id.in_(pids), Order.status.in_(OPEN_ORDER))
        )
    )
    out = []
    for p in sorted(prods, key=lambda x: x.name.lower()):
        qs = by_wh.get(p.id, [])
        if any(q < 0 for q in qs):
            why = "existencia negativa: primero corregí el Kardex (Inventarios → Salud)"
        elif sum(qs, Decimal(0)) > 0:
            why = "tiene existencia propia"
        elif p.id in linked:
            why = "vinculado al catálogo del proveedor (lo maneja la sincronización)"
        elif p.id in reserved:
            why = "tiene reservas abiertas"
        elif p.id in in_orders:
            why = "está en un pedido web abierto"
        else:
            why = None
        out.append({"id": p.id, "code": p.code, "name": p.name, "active": p.active, "show_on_web": p.show_on_web, "archivar": why is None, "motivo": why or "sin existencia, de la tienda anterior"})
    return out


def archive_legacy(db: Session, t: Tenant, user_id: int | None = None) -> int:
    """Archiva los candidatos (sin commit). Devuelve cuantos archivo."""
    n = 0
    for c in candidates(db, t):
        if not c["archivar"]:
            continue
        p = db.get(Product, c["id"])
        arch.archive(db, "product", p, user_id)
        n += 1
    if n:
        audit(db, t.id, user_id, "archive_legacy", "product", 0, {"archivados": n, "origen": "tienda anterior (Fygaro)"})
    return n


def find_main(db: Session, tenant_id: int) -> Warehouse | None:
    return db.scalar(select(Warehouse).where(Warehouse.tenant_id == tenant_id, func.lower(Warehouse.name) == MAIN_NAME.lower()).order_by(Warehouse.active.desc(), Warehouse.id))


def main_warehouse_plan(db: Session, tenant_id: int, make_default: bool = True) -> dict:
    w = find_main(db, tenant_id)
    if w is None:
        return {"accion": "crear", "nombre": MAIN_NAME, "predeterminada": make_default or not _has_default(db, tenant_id)}
    cambios = []
    if not w.active:
        cambios.append("activar")
    if w.kind != "venta":
        cambios.append(f"tipo {w.kind} → venta")
    if make_default and not w.is_default:
        cambios.append("predeterminada")
    return {"accion": "ajustar" if cambios else "ok", "id": w.id, "nombre": w.name, "cambios": cambios}


def _has_default(db: Session, tenant_id: int) -> bool:
    return db.scalar(select(Warehouse.id).where(Warehouse.tenant_id == tenant_id, Warehouse.active, Warehouse.is_default).limit(1)) is not None


def ensure_main_warehouse(db: Session, tenant_id: int, user_id: int | None = None, make_default: bool = True) -> Warehouse:
    """Deja "Bodega principal" activa, de venta y (por defecto) predeterminada. Sin commit."""
    w = find_main(db, tenant_id)
    created = w is None
    if created:
        w = Warehouse(tenant_id=tenant_id, name=MAIN_NAME, description="Inventario local de Crimson", kind="venta", active=True, is_default=False)
        db.add(w)
        db.flush()
    w.active, w.kind = True, "venta"
    if make_default or not _has_default(db, tenant_id):
        for other in db.scalars(select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.id != w.id, Warehouse.is_default)):
            other.is_default = False
        w.is_default = True
    db.flush()
    audit(db, tenant_id, user_id, "create" if created else "update", "warehouse", w.id, {"bodega": MAIN_NAME, "tipo": "venta", "predeterminada": w.is_default})
    return w


