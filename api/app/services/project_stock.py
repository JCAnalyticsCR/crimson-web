"""Inventario real de los proyectos: cada equipo sale de bodega UNA sola vez.

Antes habia dos caminos independientes que sacaban el mismo material: la factura (al convertir la cotizacion del
proyecto o al facturar el proyecto) y la orden de trabajo (al finalizar). Solo un caso estaba protegido, asi que una
cotizacion convertida desde Ventas + sus ordenes descontaba dos veces, y si las ordenes llevaban solo parte del
material, el resto nunca salia. Instalar un equipo con su serie (o registrarlo como activo del cliente) no movia nada.

Regla (por proyecto y producto):

    sale de bodega = max(facturado, instalado)
    instalado      = max(lo anotado en ordenes finalizadas, series entregadas/instaladas, equipos del cliente)

Facturado = lineas de las facturas del proyecto que no estan anuladas. Cada evento (factura, anulacion, orden
finalizada, serie instalada, activo registrado) llama `reconcile`, que mira lo que el kardex YA saco para el proyecto
y escribe solo la diferencia: una salida si falta, una devolucion si sobra (p. ej. se anulo la factura de algo que
nunca se instalo). Es idempotente y corrige sola los dobles descuentos viejos la proxima vez que el proyecto se mueve.
Las filas viejas sin project_id se reconocen por la factura (invoice_id) o por el numero de la orden (reference).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import CustomerAsset, Invoice, InvoiceLine, Product, Project, StockMovement, StockUnit
from . import inventory as invsvc

ZERO = Decimal(0)
INSTALLED_UNIT = ("entregado_proyecto", "instalado")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


# ---------- a que proyecto pertenece una factura ----------
def project_for_invoice(db: Session, inv: Invoice) -> Project | None:
    pr = db.scalar(select(Project).where(Project.tenant_id == inv.tenant_id, Project.invoice_id == inv.id))
    if pr is None and inv.quote_id:
        pr = db.scalar(select(Project).where(Project.tenant_id == inv.tenant_id, Project.quote_id == inv.quote_id).order_by(Project.id))
    return pr


def invoices_of(db: Session, pr: Project) -> list[Invoice]:
    """Todas las facturas del proyecto (anuladas incluidas: sus filas del kardex tambien cuentan)."""
    conds = []
    if pr.invoice_id:
        conds.append(Invoice.id == pr.invoice_id)
    if pr.quote_id:
        conds.append(Invoice.quote_id == pr.quote_id)
    if not conds:
        return []
    return list(db.scalars(select(Invoice).where(Invoice.tenant_id == pr.tenant_id, or_(*conds))))


# ---------- cifras ----------
def figures(db: Session, pr: Project, product_id: int, invoices: list[Invoice] | None = None) -> dict:
    invoices = invoices if invoices is not None else invoices_of(db, pr)
    live = [i.id for i in invoices if i.status != "anulada"]
    invoiced = (
        _d(db.scalar(select(func.coalesce(func.sum(InvoiceLine.quantity), 0)).where(InvoiceLine.invoice_id.in_(live), InvoiceLine.product_id == product_id)))
        if live
        else ZERO
    )
    orders = [o for o in pr.orders if o.stock_applied and o.trashed_at is None]
    wo_used = sum((_d(m.quantity) for o in orders for m in o.materials if m.product_id == product_id), ZERO)
    units = _d(
        db.scalar(
            select(func.count()).where(
                StockUnit.tenant_id == pr.tenant_id,
                StockUnit.project_id == pr.id,
                StockUnit.product_id == product_id,
                StockUnit.status.in_(INSTALLED_UNIT),
            )
        )
    )
    assets = _d(
        db.scalar(
            select(func.count()).where(
                CustomerAsset.tenant_id == pr.tenant_id,
                CustomerAsset.project_id == pr.id,
                CustomerAsset.product_id == product_id,
                CustomerAsset.trashed_at.is_(None),
            )
        )
    )
    installed = max(wo_used, units, assets)
    # lo que el kardex ya saco para el proyecto (filas nuevas por project_id; viejas por factura o por numero de orden)
    refs = [o.number for o in pr.orders]
    match = [StockMovement.project_id == pr.id]
    if invoices:
        match.append(StockMovement.invoice_id.in_([i.id for i in invoices]))
    if refs:
        match.append((StockMovement.reference.in_(refs)) & (StockMovement.kind == "salida") & StockMovement.project_id.is_(None))
    net = _d(
        db.scalar(
            select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                StockMovement.tenant_id == pr.tenant_id, StockMovement.product_id == product_id, or_(*match)
            )
        )
    )
    out = -net
    return {
        "facturado": invoiced,
        "ordenes": wo_used,
        "series": units,
        "activos": assets,
        "instalado": installed,
        "objetivo": max(invoiced, installed),
        "salio": out,
    }


# ---------- conciliar ----------
def reconcile(
    db: Session,
    pr: Project,
    product_ids: Iterable[int],
    user_id: int | None,
    *,
    reason: str,
    warehouse_id: int | None = None,
    invoice: Invoice | None = None,
    reference: str | None = None,
) -> list[StockMovement]:
    """Lleva el kardex del proyecto al objetivo de cada producto. Sin bodega configurada no hace nada (como antes)."""
    wh = warehouse_id or (w.id if (w := invsvc.default_warehouse(db, pr.tenant_id)) else None)
    if not wh:
        return []
    db.flush()  # que las lineas, ordenes y series recien tocadas cuenten
    invoices = invoices_of(db, pr)
    moves = []
    for pid in sorted({p for p in product_ids if p}):
        prod = db.get(Product, pid)
        if prod is None or prod.tenant_id != pr.tenant_id or prod.item_type != "producto":
            continue
        f = figures(db, pr, pid, invoices)
        delta = f["objetivo"] - f["salio"]  # > 0: falta sacar; < 0: salio de mas
        if delta == 0:
            continue
        sale = delta > 0
        moves.append(
            invsvc.move(
                db,
                pr.tenant_id,
                user_id,
                pid,
                wh,
                ("venta" if invoice is not None else "salida") if sale else "devolucion",
                -delta,
                reference or (invoice.number if invoice is not None else pr.number),
                f"Proyecto {pr.number} · {reason} · facturado {f['facturado'].normalize():f}, instalado {f['instalado'].normalize():f}",
                None,
                invoice.id if invoice is not None else None,
                project_id=pr.id,
                movement_type="consumo_proyecto" if sale else "devolucion",
            )
        )
    return moves


def product_ids_of(pr: Project, invoices: list[Invoice]) -> set[int]:
    ids = {m.product_id for o in pr.orders for m in o.materials if m.product_id}
    for inv in invoices:
        ids |= {ln.product_id for ln in inv.lines if ln.product_id}
    return ids


def reconcile_all(db: Session, pr: Project, user_id: int | None, *, reason: str, invoice: Invoice | None = None) -> list[StockMovement]:
    return reconcile(db, pr, product_ids_of(pr, invoices_of(db, pr)), user_id, reason=reason, invoice=invoice)
