"""Metas y tablero comercial (ventas etapa 3).

- Meta mensual por vendedor en colones (SalesGoal, la pone el admin o el jefe de ventas). Un mes sin meta propia usa la
  ultima anterior del vendedor; si no hay, la de Ajustes -> Ventas (meta_mensual_default, 0 = sin meta).
- "Vendido" = lo cobrado y CONFIRMADO en el mes (pagos tipo captura en estado confirmado, menos devoluciones y
  reembolsos) de las facturas del vendedor que no estan anuladas ni son venta de la casa, SIN IVA y en colones. Es la
  misma base que la comision por cobro (services/commissions._on_capture): pago x venta sin IVA / total, con el tipo de
  cambio del documento. Un cobro "por confirmar" no cuenta.
- Tasa de cierre: ganadas / (ganadas + perdidas) de sus oportunidades cerradas en el mes.
- Ticket promedio: venta sin IVA promedio de sus facturas emitidas en el mes (no anuladas).
- Embudo abierto: monto de sus oportunidades abiertas, a colones con el tipo de cambio del dia.
Nada de costo ni margen: el tablero lo ven el admin y el jefe de ventas, y el inicio del vendedor solo lo suyo.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Invoice, Opportunity, Payment
from ..models.ventas3 import SalesGoal
from .totals import d, net_sale

ZERO = Decimal(0)


def month_start(v: date) -> date:
    return v.replace(day=1)


def month_end(m: date) -> date:
    nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def parse_month(s: str | None) -> date:
    from .invoice_states import cr_today

    if not s:
        return month_start(cr_today())
    try:
        y, m = (int(x) for x in s.split("-")[:2])
        return date(y, m, 1)
    except (ValueError, TypeError):
        from fastapi import HTTPException

        raise HTTPException(422, "Mes inválido (use AAAA-MM)") from None


def goal_for(db: Session, tenant, uid: int, month: date) -> Decimal | None:
    g = db.scalar(
        select(SalesGoal.amount).where(SalesGoal.tenant_id == tenant.id, SalesGoal.user_id == uid, SalesGoal.month <= month).order_by(SalesGoal.month.desc()).limit(1)
    )
    if g is not None:
        return d(g)
    from .sale_assignment import ventas_settings

    dflt = d(ventas_settings(tenant).get("meta_mensual_default") or 0)
    return dflt if dflt > 0 else None


def set_goal(db: Session, p, uid: int, month: date, amount) -> SalesGoal:
    from fastapi import HTTPException

    from .documents import audit
    from .ownership import eligible_seller

    if not eligible_seller(db, p.tenant.id, uid):
        raise HTTPException(422, "Ese usuario no puede tener meta de ventas")
    amt = d(amount)
    if amt < 0:
        raise HTTPException(422, "La meta no puede ser negativa")
    g = db.scalar(select(SalesGoal).where(SalesGoal.tenant_id == p.tenant.id, SalesGoal.user_id == uid, SalesGoal.month == month))
    antes = str(g.amount) if g else None
    if g is None:
        g = SalesGoal(tenant_id=p.tenant.id, user_id=uid, month=month, amount=amt, set_by=p.user.id)
        db.add(g)
    else:
        g.amount, g.set_by = amt, p.user.id
    audit(db, p.tenant.id, p.user.id, "sales_goal", "user", uid, before={"meta": antes, "mes": month.isoformat()}, after={"meta": str(amt), "mes": month.isoformat()}, ip=p.ip)
    db.flush()
    return g


def sold(db: Session, tenant_id: int, uid: int, month: date) -> Decimal:
    """Cobrado y confirmado en el mes, sin IVA y en colones (la base de la comision por cobro)."""
    from .commissions import _doc_crc, convert

    rows = db.execute(
        select(Payment, Invoice)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(
            Payment.tenant_id == tenant_id,
            Payment.status == "confirmado",
            Payment.kind.in_(("captura", "devolucion", "reembolso")),
            Payment.paid_at >= month,
            Payment.paid_at <= month_end(month),
            Invoice.seller_id == uid,
            Invoice.status != "anulada",
            or_(Invoice.house_sale.is_(None), Invoice.house_sale.is_(False)),
        )
    ).all()
    total = ZERO
    for pay, inv in rows:
        tot, net = d(inv.total), net_sale(inv)
        if tot <= 0 or net <= 0:
            continue
        base = convert(db, d(pay.amount), pay.currency or inv.currency, inv.currency) * net / tot
        sign = 1 if pay.kind == "captura" else -1
        total += sign * _doc_crc(db, inv, base)
    return total.quantize(Decimal("0.01"))


def _stats(db: Session, tenant, uid: int, month: date) -> dict:
    from datetime import UTC, datetime, time

    from ..routers.pipeline import OPEN_STATES, _ToCompany
    from .archive import live
    from .commissions import _doc_crc
    from .documents import CR

    a = datetime.combine(month, time(0, 0), tzinfo=CR).astimezone(UTC)
    b = datetime.combine(month_end(month) + timedelta(days=1), time(0, 0), tzinfo=CR).astimezone(UTC)
    cerradas = db.scalars(
        select(Opportunity.status).where(Opportunity.tenant_id == tenant.id, Opportunity.owner_id == uid, Opportunity.status.in_(("ganada", "perdida")), Opportunity.closed_at >= a, Opportunity.closed_at < b)
    ).all()
    ganadas = sum(1 for s in cerradas if s == "ganada")
    invs = db.scalars(
        select(Invoice).where(Invoice.tenant_id == tenant.id, Invoice.seller_id == uid, Invoice.status != "anulada", Invoice.issue_date >= month, Invoice.issue_date <= month_end(month))
    ).all()
    netos = [_doc_crc(db, i, net_sale(i)) for i in invs]
    conv = _ToCompany(db, tenant)
    abiertas = db.scalars(select(Opportunity).where(Opportunity.tenant_id == tenant.id, Opportunity.owner_id == uid, Opportunity.status.in_(OPEN_STATES), live(Opportunity))).all()
    return {
        "cerradas": len(cerradas),
        "ganadas": ganadas,
        "tasa_cierre": round(ganadas * 100 / len(cerradas), 1) if cerradas else None,
        "facturas": len(invs),
        "ticket_promedio": (sum(netos, ZERO) / len(netos)).quantize(Decimal("0.01")) if netos else None,
        "embudo_abierto": sum((conv(o.amount, o.currency) for o in abiertas), ZERO).quantize(Decimal("0.01")),
        "oportunidades_abiertas": len(abiertas),
    }


def progress(db: Session, tenant, uid: int, month: date) -> dict:
    meta = goal_for(db, tenant, uid, month)
    vendido = sold(db, tenant.id, uid, month)
    return {
        "mes": month.isoformat()[:7],
        "meta": meta,
        "vendido": vendido,
        "avance_pct": round(float(vendido * 100 / meta), 1) if meta else None,
        "falta": max(ZERO, meta - vendido) if meta else None,
    }


def board(db: Session, p, month: date) -> dict:
    from .store_orders import sales_team

    rows = []
    for x in sales_team(db, p.tenant.id):
        r = {"id": x["id"], "name": x["name"], **progress(db, p.tenant, x["id"], month), **_stats(db, p.tenant, x["id"], month)}
        r["meta_propia"] = db.scalar(select(SalesGoal.amount).where(SalesGoal.tenant_id == p.tenant.id, SalesGoal.user_id == x["id"], SalesGoal.month == month))
        rows.append(r)
    rows.sort(key=lambda r: (r["vendido"], r["embudo_abierto"]), reverse=True)
    for i, r in enumerate(rows, 1):
        r["ranking"] = i
    tot_meta = sum((r["meta"] or ZERO for r in rows), ZERO)
    tot_vendido = sum((r["vendido"] for r in rows), ZERO)
    return {
        "mes": month.isoformat()[:7],
        "moneda": "CRC",
        "vendedores": rows,
        "total": {"meta": tot_meta, "vendido": tot_vendido, "avance_pct": round(float(tot_vendido * 100 / tot_meta), 1) if tot_meta else None},
    }
