"""Salud del inventario: cada documento sabe cuanto deberia haber movido; aca se compara con el kardex.

Encuentra lo que descuadra sin que nadie lo note y deja corregirlo con un clic (la correccion es la misma
conciliacion que corre al crear, editar o anular el documento: escribe solo la diferencia, con su documento).

Revisa:
- existencias negativas por bodega;
- facturas (sueltas) cuyo kardex no coincide con sus lineas, o anuladas que no devolvieron;
- proyectos donde lo que salio no es max(facturado, instalado);
- pedidos web despachados que no sacaron todo, y cancelados/reembolsados con mercaderia afuera sin decidir
  si volvio;
- garantias cuyo kardex no coincide con su estado, y las abiertas hace mas de 30 dias.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Invoice, InvoiceLine, Order, Product, Project, StockMovement, Tenant, Warehouse, WarrantyCase
from . import inventory as invsvc
from . import project_stock, store_inventory
from . import warranty as warsvc

ZERO = Decimal(0)
DAYS = 180  # ventana de documentos que se revisan (lo viejo ya se reviso o se conto)
NO_DEVUELTO = "Producto no devuelto"
WARRANTY_MAX_DAYS = 30  # una garantia abierta mas de un mes se avisa (el cliente esta sin su equipo)


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _fmt(v: Decimal) -> str:
    return f"{v.normalize():f}"


def check(db: Session, tenant_id: int, days: int = DAYS) -> dict:
    since = date.today() - timedelta(days=days)
    issues: list[dict] = []
    names: dict[int, str] = {}

    def name(pid: int) -> str:
        if pid not in names:
            p = db.get(Product, pid)
            names[pid] = f"{p.code} · {p.name}" if p else f"#{pid}"
        return names[pid]

    goods = set(db.scalars(select(Product.id).where(Product.tenant_id == tenant_id, Product.item_type == "producto")))

    # 1) existencias negativas
    whs = {w.id: w.name for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == tenant_id))}
    for lv in invsvc.stock_levels(db, tenant_id):
        if lv["quantity"] < 0:
            issues.append(
                {
                    "kind": "negativo",
                    "ref_id": lv["product_id"],
                    "doc": whs.get(lv["warehouse_id"], "Bodega"),
                    "product": name(lv["product_id"]),
                    "detail": f"Existencia {_fmt(lv['quantity'])} en {whs.get(lv['warehouse_id'], 'la bodega')}: salió más de lo que entró",
                    "fixable": False,
                }
            )

    # 2) facturas sueltas (las de proyectos y pedidos web las revisan sus propios documentos)
    projects = list(db.scalars(select(Project).where(Project.tenant_id == tenant_id)))
    project_invoices = {p.invoice_id for p in projects if p.invoice_id}
    project_quotes = {p.quote_id for p in projects if p.quote_id}
    order_invoices = set(db.scalars(select(Order.invoice_id).where(Order.tenant_id == tenant_id, Order.invoice_id.is_not(None))))
    invs = {
        i.id: i
        for i in db.scalars(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.issue_date >= since))
        if i.id not in project_invoices and i.quote_id not in project_quotes and i.id not in order_invoices
    }
    if invs:
        ids = list(invs)
        lines: dict[tuple[int, int], Decimal] = {}
        for iid, pid, q in db.execute(
            select(InvoiceLine.invoice_id, InvoiceLine.product_id, func.sum(InvoiceLine.quantity))
            .where(InvoiceLine.invoice_id.in_(ids), InvoiceLine.product_id.is_not(None))
            .group_by(InvoiceLine.invoice_id, InvoiceLine.product_id)
        ):
            if pid in goods and invs[iid].status != "anulada":
                lines[(iid, pid)] = _d(q)
        moved: dict[tuple[int, int], Decimal] = {}
        for iid, pid, q in db.execute(
            select(StockMovement.invoice_id, StockMovement.product_id, func.sum(StockMovement.quantity))
            .where(StockMovement.tenant_id == tenant_id, StockMovement.invoice_id.in_(ids))
            .group_by(StockMovement.invoice_id, StockMovement.product_id)
        ):
            moved[(iid, pid)] = -_d(q)
        # sin bodega configurada nada se descuenta: no es un descuadre sino falta de configuracion
        if invsvc.default_warehouse(db, tenant_id) is not None:
            for key in sorted(set(lines) | set(moved)):
                esperado, salio = lines.get(key, ZERO), moved.get(key, ZERO)
                if esperado != salio:
                    inv = invs[key[0]]
                    issues.append(
                        {
                            "kind": "factura",
                            "ref_id": inv.id,
                            "doc": f"Factura {inv.number}" + (" (anulada)" if inv.status == "anulada" else ""),
                            "product": name(key[1]),
                            "detail": f"Debió salir {_fmt(esperado)} y el kardex registra {_fmt(salio)}",
                            "fixable": True,
                        }
                    )

    # 3) proyectos (activos o tocados en la ventana)
    for pr in projects:
        if pr.status == "cancelado" or (pr.updated_at and pr.updated_at.date() < since and pr.status in ("facturado", "entregado")):
            continue
        invoices = project_stock.invoices_of(db, pr)
        for pid in sorted(project_stock.product_ids_of(pr, invoices) & goods):
            f = project_stock.figures(db, pr, pid, invoices)
            if f["objetivo"] != f["salio"]:
                issues.append(
                    {
                        "kind": "proyecto",
                        "ref_id": pr.id,
                        "doc": f"Proyecto {pr.number}",
                        "product": name(pid),
                        "detail": f"Facturado {_fmt(f['facturado'])}, instalado {_fmt(f['instalado'])}: debió salir {_fmt(f['objetivo'])} y salió {_fmt(f['salio'])}",
                        "fixable": True,
                    }
                )

    # 4) pedidos web
    for o in db.scalars(
        select(Order).where(
            Order.tenant_id == tenant_id, Order.channel == "tienda", Order.created_at >= datetime.combine(since, datetime.min.time(), tzinfo=UTC)
        )
    ):
        if o.status in ("enviado", "entregado") and store_inventory._managed(db, o):
            need, _ = store_inventory._needs(db, o)
            moved = store_inventory._moved(db, o)
            for pid, q in need.items():
                if moved.get(pid, ZERO) < q:
                    issues.append(
                        {
                            "kind": "pedido",
                            "ref_id": o.id,
                            "doc": f"Pedido {o.number}",
                            "product": name(pid),
                            "detail": f"Se despachó pero el kardex solo sacó {_fmt(moved.get(pid, ZERO))} de {_fmt(q)}",
                            "fixable": True,
                        }
                    )
        elif o.status in ("cancelado", "reembolsado"):
            afuera = store_inventory.out_for_order(db, o)
            decidido = any((h.get("note") or "").startswith(NO_DEVUELTO) for h in (o.history or []))
            for pid, q in afuera.items():
                if not decidido:
                    issues.append(
                        {
                            "kind": "pedido_sin_decidir",
                            "ref_id": o.id,
                            "doc": f"Pedido {o.number} ({o.status})",
                            "product": name(pid),
                            "detail": f"{_fmt(q)} unidad(es) salieron y nadie registró si volvieron",
                            "fixable": False,
                        }
                    )

    # 5) garantias abiertas (o cerradas en la ventana)
    now = datetime.now(UTC)
    for c in db.scalars(select(WarrantyCase).where(WarrantyCase.tenant_id == tenant_id)):
        closed = c.closed_at.replace(tzinfo=UTC) if c.closed_at and c.closed_at.tzinfo is None else c.closed_at
        if c.status not in warsvc.OPEN and closed and closed.date() < since:
            continue
        want, have = warsvc.targets(db, c), warsvc.moved(db, c)
        for key in sorted(set(want) | set(have)):
            if want.get(key, ZERO) != have.get(key, ZERO):
                issues.append(
                    {
                        "kind": "garantia",
                        "ref_id": c.id,
                        "doc": f"Garantía {c.number}",
                        "product": name(key[0]),
                        "detail": f"{warsvc.STATE_LABEL[c.status]}: en {whs.get(key[1], 'la bodega')} debería haber {_fmt(want.get(key, ZERO))} y el kardex registra {_fmt(have.get(key, ZERO))}",
                        "fixable": True,
                    }
                )
        created = c.created_at.replace(tzinfo=UTC) if c.created_at and c.created_at.tzinfo is None else c.created_at
        if c.status in warsvc.OPEN and created and (now - created).days > WARRANTY_MAX_DAYS:
            issues.append(
                {
                    "kind": "garantia_vieja",
                    "ref_id": c.id,
                    "doc": f"Garantía {c.number}",
                    "product": c.equipment,
                    "detail": f"{warsvc.STATE_LABEL[c.status]} hace {(now - created).days} días: el cliente sigue sin su equipo",
                    "fixable": False,
                }
            )

    counts: dict[str, int] = {}
    for i in issues:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"checked_at": datetime.now(UTC), "days": days, "total": len(issues), "counts": counts, "issues": issues}


def fix(db: Session, tenant: Tenant, kind: str, ref_id: int, user_id: int | None) -> int:
    """Corrige un descuadre con la misma conciliacion del documento (sin commit)."""
    if kind == "factura":
        inv = db.get(Invoice, ref_id)
        if not inv or inv.tenant_id != tenant.id:
            raise HTTPException(404, "Factura no encontrada")
        return invsvc.reconcile_invoice(db, inv, user_id, reason="corregido desde salud del inventario")
    if kind == "proyecto":
        pr = db.get(Project, ref_id)
        if not pr or pr.tenant_id != tenant.id:
            raise HTTPException(404, "Proyecto no encontrado")
        return len(project_stock.reconcile_all(db, pr, user_id, reason="corregido desde salud del inventario"))
    if kind == "pedido":
        o = db.get(Order, ref_id)
        if not o or o.tenant_id != tenant.id:
            raise HTTPException(404, "Pedido no encontrado")
        return store_inventory.fulfill_for_order(db, o, user_id)
    if kind == "garantia":
        c = db.get(WarrantyCase, ref_id)
        if not c or c.tenant_id != tenant.id:
            raise HTTPException(404, "Garantía no encontrada")
        return warsvc.reconcile(db, c, user_id, "corregido desde salud del inventario")
    raise HTTPException(422, "Ese descuadre se resuelve a mano (ajuste o decisión sobre la devolución)")
