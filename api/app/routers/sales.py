"""Cotizaciones, facturas, pagos manuales, enlace de pago y dashboard."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import BillingGroup, Customer, Invoice, Payment, PaymentLink, Quote
from ..schemas.sales import (
    DocListItem,
    DocumentIn,
    InvoiceOut,
    PaymentIn,
    PaymentLinkOut,
    PaymentOut,
    QuoteOut,
)
from ..services import discounts
from ..services import documents as svc
from ..services import inventory as invsvc
from ..services.documents import audit
from ..services.totals import LineIn, compute_document, d

router = APIRouter(tags=["ventas"])


def _cust_name(db: Session, cid: int | None) -> str | None:
    if not cid:
        return None
    c = db.get(Customer, cid)
    return c.name if c else None


def _quote_out(db: Session, q: Quote) -> QuoteOut:
    o = QuoteOut.model_validate(q)
    o.customer_name = _cust_name(db, q.customer_id)
    return o


def _inv_out(db: Session, i: Invoice) -> InvoiceOut:
    o = InvoiceOut.model_validate(i)
    o.customer_name = _cust_name(db, i.customer_id)
    return o


def _list(db: Session, model, tenant_id: int, status: str | None, cursor: int | None, limit: int, q: str | None, p: Principal | None = None):
    stmt = select(model).where(model.tenant_id == tenant_id)
    if p is not None and not p.sees_all_sales:
        stmt = stmt.where(model.created_by == p.user.id)
    if status:
        stmt = stmt.where(model.status == status)
    if q:
        stmt = stmt.where(model.number.ilike(f"%{q}%"))
    if cursor:
        stmt = stmt.where(model.id < cursor)
    rows = db.scalars(stmt.order_by(model.updated_at.desc(), model.id.desc()).limit(limit + 1)).all()
    items = []
    for r in rows[:limit]:
        it = DocListItem.model_validate(r)
        it.customer_name = _cust_name(db, r.customer_id)
        items.append(it)
    return {"items": items, "next_cursor": rows[limit].id if len(rows) > limit else None}


def _own(db: Session, model, id_: int, tenant_id: int, p: Principal | None = None):
    obj = db.get(model, id_)
    if not obj or obj.tenant_id != tenant_id or (p is not None and not p.sees_all_sales and obj.created_by != p.user.id):
        raise HTTPException(404, "Documento no encontrado")
    return obj


# ---------- Preview de totales (editor en vivo, sin guardar) ----------
@router.post("/documents/preview")
def preview(data: DocumentIn, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    """Totales en vivo para el editor (sin guardar). Completa precio/impuesto desde el producto si falta."""
    lines = [svc._fill_line_from_product(db, p.tenant.id, ln) for ln in data.lines]
    calc = compute_document(
        [
            LineIn(d(ln.quantity), d(ln.unit_price or 0), ln.discount_type, d(ln.discount_value), d(ln.tax_rate if ln.tax_rate is not None else 13))
            for ln in lines
        ],
        data.discount_type,
        data.discount_value,
    )
    return {
        "subtotal": calc.subtotal,
        "discount_total": calc.discount_total,
        "tax_total": calc.tax_total,
        "total": calc.total,
        "taxable_by_rate": calc.taxable_by_rate,
        "lines": [
            {
                "name": ln.name,
                "code": ln.code,
                "unit_price": ln.unit_price,
                "tax_rate": ln.tax_rate,
                "subtotal": lo.subtotal,
                "tax_amount": lo.tax_amount,
                "total": lo.total,
            }
            for ln, lo in zip(lines, calc.lines, strict=True)
        ],
    }


# ---------- Cotizaciones ----------
@router.get("/quotes")
def quotes(
    status: str | None = None,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    p: Principal = Depends(require("sales", "ver")),
    db: Session = Depends(get_db),
):
    return _list(db, Quote, p.tenant.id, status, cursor, limit, q, p)


@router.post("/quotes", response_model=QuoteOut, status_code=201)
def create_quote(data: DocumentIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    chk = discounts.check(db, p, data)
    q = svc.create_quote(db, p.tenant.id, p.user.id, data)
    if chk.exceeds:
        q.status = "por_aprobar"
        audit(db, p.tenant.id, p.user.id, "discount_over_limit", "quote", q.id, {"pct": str(chk.worst), "limit": str(chk.limit)}, ip=p.ip)
    db.commit()
    return _quote_out(db, q)


@router.get("/quotes/{qid}", response_model=QuoteOut)
def get_quote(qid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    return _quote_out(db, _own(db, Quote, qid, p.tenant.id, p))


@router.put("/quotes/{qid}", response_model=QuoteOut)
def update_quote(qid: int, data: DocumentIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status in ("convertida", "anulada"):
        raise HTTPException(409, f"La cotizacion esta {q.status} y no se puede editar")
    from ..models import QuoteLine

    chk = discounts.check(db, p, data)
    svc.apply_document(db, q, data, QuoteLine, p.tenant.id)
    if chk.exceeds:
        q.status = "por_aprobar"
    elif q.status == "por_aprobar":
        q.status = "creado"  # quedo dentro del limite (o la edito un administrador)
    audit(db, p.tenant.id, p.user.id, "update", "quote", q.id, {"discount_pct": str(chk.worst)} if chk.worst else None, ip=p.ip)
    db.commit()
    return _quote_out(db, q)


@router.post("/quotes/{qid}/convert", response_model=InvoiceOut, status_code=201)
def convert_quote(qid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    inv = svc.convert_quote(db, p.tenant.id, p.user.id, q)
    invsvc.deduct_for_invoice(db, inv, p.user.id)
    db.commit()
    return _inv_out(db, inv)


@router.post("/quotes/{qid}/duplicate", response_model=QuoteOut, status_code=201)
def duplicate_quote(qid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    payload = svc.quote_to_payload(q)
    nq = svc.create_quote(db, p.tenant.id, p.user.id, payload)
    if discounts.check(db, p, payload).exceeds:
        nq.status = "por_aprobar"
    db.commit()
    return _quote_out(db, nq)


@router.post("/quotes/{qid}/void", response_model=QuoteOut)
def void_quote(qid: int, p: Principal = Depends(require("sales", "anular")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status == "convertida":
        raise HTTPException(409, "Una cotizacion convertida no se anula; anule la factura")
    q.status = "anulada"
    audit(db, p.tenant.id, p.user.id, "void", "quote", q.id, ip=p.ip)
    db.commit()
    return _quote_out(db, q)


@router.post("/quotes/{qid}/send", response_model=QuoteOut)
def send_quote(qid: int, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    """Marca como enviada (el envio real por correo lo hace el worker en Fase 1.2)."""
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    if q.status == "creado":
        q.status = "enviada"
    audit(db, p.tenant.id, p.user.id, "send", "quote", q.id, ip=p.ip)
    db.commit()
    return _quote_out(db, q)


@router.post("/quotes/{qid}/approve", response_model=QuoteOut)
def approve_quote(qid: int, p: Principal = Depends(require("sales", "aprobar")), db: Session = Depends(get_db)):
    """Un administrador aprueba el descuento que excede el limite del vendedor."""
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status != "por_aprobar":
        raise HTTPException(409, "La cotización no está pendiente de aprobación")
    q.status = "creado"
    audit(db, p.tenant.id, p.user.id, "approve_discount", "quote", q.id, ip=p.ip)
    db.commit()
    return _quote_out(db, q)


@router.get("/sales/discount-limit")
def discount_limit(p: Principal = Depends(require("sales", "ver"))):
    return {"limit": discounts.max_discount(p.tenant), "free": p.can("sales", "descuento_libre")}


# ---------- Facturas ----------
@router.get("/invoices")
def invoices(
    status: str | None = None,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    p: Principal = Depends(require("sales", "ver")),
    db: Session = Depends(get_db),
):
    return _list(db, Invoice, p.tenant.id, status, cursor, limit, q, p)


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(data: DocumentIn, doc_type: str = "FE", p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    if doc_type not in ("FE", "TE", "FEE"):
        raise HTTPException(422, "doc_type debe ser FE, TE o FEE")
    chk = discounts.check(db, p, data)
    if chk.exceeds:
        raise HTTPException(422, f"{chk.message} Hacé una cotización para que un administrador apruebe el descuento.")
    inv = svc.create_invoice(db, p.tenant.id, p.user.id, data, doc_type)
    invsvc.deduct_for_invoice(db, inv, p.user.id)
    db.commit()
    return _inv_out(db, inv)


@router.get("/invoices/{iid}", response_model=InvoiceOut)
def get_invoice(iid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    return _inv_out(db, _own(db, Invoice, iid, p.tenant.id, p))


@router.put("/invoices/{iid}", response_model=InvoiceOut)
def update_invoice(iid: int, data: DocumentIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    if inv.status == "anulada" or inv.einvoice_status in ("pendiente", "aceptada"):
        raise HTTPException(409, "La factura ya fue emitida o anulada; use nota de credito")
    if inv.payments:
        raise HTTPException(409, "La factura tiene pagos registrados")
    from ..models import InvoiceLine

    chk = discounts.check(db, p, data)
    if chk.exceeds:
        raise HTTPException(422, f"{chk.message} Hacé una cotización para que un administrador apruebe el descuento.")
    svc.apply_document(db, inv, data, InvoiceLine, p.tenant.id)
    audit(db, p.tenant.id, p.user.id, "update", "invoice", inv.id, ip=p.ip)
    db.commit()
    return _inv_out(db, inv)


@router.post("/invoices/{iid}/void", response_model=InvoiceOut)
def void_invoice(iid: int, p: Principal = Depends(require("sales", "anular")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    if inv.einvoice_status == "aceptada":
        raise HTTPException(409, "Factura aceptada por Hacienda: se anula emitiendo Nota de Credito (Fase 2)")
    inv.status = "anulada"
    invsvc.restock_for_void(db, inv, p.user.id)
    audit(db, p.tenant.id, p.user.id, "void", "invoice", inv.id, ip=p.ip)
    db.commit()
    return _inv_out(db, inv)


@router.post("/invoices/{iid}/payments", response_model=InvoiceOut, status_code=201)
def pay_invoice(iid: int, data: PaymentIn, p: Principal = Depends(require("payments", "crear")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    svc.add_payment(db, p.tenant.id, p.user.id, inv, data)
    db.commit()
    db.refresh(inv)
    return _inv_out(db, inv)


@router.post("/invoices/{iid}/payment-link", response_model=PaymentLinkOut)
def payment_link(iid: int, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    link = svc.get_or_create_payment_link(db, p.tenant.id, inv)
    db.commit()
    return PaymentLinkOut(
        url=link.url,
        whatsapp_url=svc.whatsapp_share_url(inv, link.url, _cust_name(db, inv.customer_id), p.tenant.name),
        expires_at=link.expires_at.isoformat(),
        opened_count=link.opened_count,
    )


# ---------- Pagos (listado) ----------
@router.get("/payments", response_model=list[PaymentOut])
def payments(limit: int = Query(20, le=100), p: Principal = Depends(require("payments", "ver")), db: Session = Depends(get_db)):
    q = select(Payment).where(Payment.tenant_id == p.tenant.id)
    if not p.sees_all_sales:
        q = q.where(Payment.created_by == p.user.id)
    return db.scalars(q.order_by(Payment.id.desc()).limit(limit)).all()


# ---------- Grupos de facturacion ----------
@router.get("/billing-groups")
def billing_groups(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    rows = db.scalars(select(BillingGroup).where(BillingGroup.tenant_id == p.tenant.id)).all()
    return [
        {"id": g.id, "doc_type": g.doc_type, "prefix": g.prefix, "branch": g.branch, "terminal": g.terminal, "current": g.current, "is_default": g.is_default}
        for g in rows
    ]


# ---------- Dashboard ----------
def _sum_between(db: Session, col_model, col, tenant_id: int, start: date, end: date, extra=None) -> Decimal:
    stmt = select(func.coalesce(func.sum(col), 0)).where(col_model.tenant_id == tenant_id)
    if extra is not None:
        stmt = stmt.where(extra)
    return d(
        db.scalar(
            stmt.where(
                col_model.__table__.c[col.key if hasattr(col, "key") else "paid_at"] >= start,
                col_model.__table__.c[col.key if hasattr(col, "key") else "paid_at"] <= end,
            )
        )
    )


@router.get("/dashboard")
def dashboard(p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    """Roles con reportes ven la empresa completa; el resto (p. ej. vendedor) solo lo que creo."""
    tid = p.tenant.id
    mine = not p.can("dashboard", "empresa")
    own_inv = (Invoice.created_by == p.user.id) if mine else true()
    own_quote = (Quote.created_by == p.user.id) if mine else true()
    own_pay = (Payment.created_by == p.user.id) if mine else true()
    today = date.today()
    m0 = today.replace(day=1)
    pm_end = m0 - timedelta(days=1)
    pm0 = pm_end.replace(day=1)

    def pay_sum(a: date, b: date) -> Decimal:
        return d(
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.tenant_id == tid, Payment.kind == "captura", Payment.status == "confirmado", Payment.paid_at >= a, Payment.paid_at <= b, own_pay
                )
            )
        )

    def inv_sum(a: date, b: date) -> Decimal:
        return d(
            db.scalar(
                select(func.coalesce(func.sum(Invoice.total), 0)).where(
                    Invoice.tenant_id == tid, Invoice.status != "anulada", Invoice.issue_date >= a, Invoice.issue_date <= b, own_inv
                )
            )
        )

    def pct(cur: Decimal, prev: Decimal) -> float | None:
        return None if prev == 0 else float((cur - prev) / prev * 100)

    show_cash = not mine or p.can("payments", "editar")  # vendedor: sin cobros; caja: sus cobros
    pagos = {"hoy": pay_sum(today, today), "mes": pay_sum(m0, today), "mes_anterior": pay_sum(pm0, pm_end)}
    fact = {"hoy": inv_sum(today, today), "mes": inv_sum(m0, today), "mes_anterior": inv_sum(pm0, pm_end)}
    pagos["variacion"] = pct(pagos["mes"], pagos["mes_anterior"])
    fact["variacion"] = pct(fact["mes"], fact["mes_anterior"])

    recent_pay = db.scalars(select(Payment).where(Payment.tenant_id == tid, own_pay).order_by(Payment.id.desc()).limit(6)).all()
    recent_inv = db.scalars(select(Invoice).where(Invoice.tenant_id == tid, own_inv).order_by(Invoice.id.desc()).limit(6)).all()
    pend_quotes = db.scalar(select(func.count()).select_from(Quote).where(Quote.tenant_id == tid, own_quote, Quote.status.in_(("creado", "enviada"))))
    overdue = db.scalar(
        select(func.count())
        .select_from(Invoice)
        .where(Invoice.tenant_id == tid, own_inv, Invoice.status.in_(("creado", "parcial", "vencida")), Invoice.due_date < today)
    )
    unpaid = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.tenant_id == tid, own_inv, Invoice.status.in_(("creado", "parcial"))))
    rejected = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.tenant_id == tid, Invoice.einvoice_status == "rechazada"))
    links_open = db.scalar(
        select(func.count())
        .select_from(PaymentLink)
        .where(PaymentLink.tenant_id == tid, PaymentLink.paid_at.is_(None), PaymentLink.expires_at > datetime.now(UTC))
    )

    to_approve = db.scalar(select(func.count()).select_from(Quote).where(Quote.tenant_id == tid, own_quote, Quote.status == "por_aprobar"))
    ceo = _ceo_row(db, p) if p.can("dashboard", "empresa") else None
    receivable = []
    if mine:
        for x in db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == tid, own_inv, Invoice.status.in_(("creado", "parcial", "vencida")), Invoice.balance > 0)
            .order_by(Invoice.due_date)
            .limit(8)
        ):
            receivable.append(
                {
                    "id": x.id,
                    "number": x.number,
                    "customer": _cust_name(db, x.customer_id),
                    "balance": x.balance,
                    "currency": x.currency,
                    "due_date": x.due_date,
                    "status": x.status,
                }
            )
    return {
        "scope": "mine" if mine else "company",
        "gerencia": ceo,
        "por_cobrar": receivable,
        "pagos": pagos if show_cash else None,
        "facturado": fact,
        "pagos_recientes": [
            {
                "id": x.id,
                "invoice_id": x.invoice_id,
                "ref": x.external_ref,
                "method": x.method,
                "kind": x.kind,
                "amount": x.amount,
                "currency": x.currency,
                "date": x.paid_at,
                "status": x.status,
            }
            for x in (recent_pay if show_cash else [])
        ],
        "facturas_recientes": [
            {
                "id": x.id,
                "number": x.number,
                "customer": _cust_name(db, x.customer_id),
                "total": x.total,
                "balance": x.balance,
                "currency": x.currency,
                "status": x.status,
                "date": x.issue_date,
            }
            for x in recent_inv
        ],
        "acciones_pendientes": {
            "cotizaciones_sin_respuesta": pend_quotes,
            "facturas_vencidas": overdue,
            "facturas_por_cobrar": unpaid,
            "documentos_rechazados": rejected,
            "enlaces_abiertos": links_open,
            "stock_bajo": len(invsvc.low_stock(db, tid)),
            "cotizaciones_por_aprobar": to_approve,
        },
    }


def _ceo_row(db: Session, p: Principal) -> dict:
    """Fila de gerencia: en 30 segundos, cómo está Crimson. Pipeline, cobros, proyectos y trabajos de la semana."""
    from datetime import timedelta

    from ..models import CustomerAsset, Opportunity, Project, WorkOrder
    from ..routers.pipeline import OPEN_STATES
    from ..routers.projects import consumed_cost
    from ..services import pricing

    tid = p.tenant.id
    today = date.today()
    m0 = today.replace(day=1)
    opps = db.scalars(select(Opportunity).where(Opportunity.tenant_id == tid, Opportunity.status.in_(OPEN_STATES))).all()
    pipeline = sum((d(o.amount) for o in opps), Decimal(0))
    weighted = sum((d(o.amount) * o.probability / 100 for o in opps), Decimal(0))
    receivable = d(
        db.scalar(select(func.coalesce(func.sum(Invoice.balance), 0)).where(Invoice.tenant_id == tid, Invoice.status.in_(("creado", "parcial", "vencida"))))
    )
    active = db.scalars(select(Project).where(Project.tenant_id == tid, Project.status.in_(("planificado", "en_curso", "pausado")))).all()
    closed = db.scalars(
        select(Project).where(Project.tenant_id == tid, Project.status.in_(("terminado", "entregado", "facturado")), Project.updated_at >= m0)
    ).all()
    profit = sum((d(x.price) - (consumed_cost(db, x) + d(x.cost_labor) + d(x.cost_travel) + d(x.cost_extra)) for x in closed), Decimal(0))
    sold = d(
        db.scalar(select(func.coalesce(func.sum(Invoice.total), 0)).where(Invoice.tenant_id == tid, Invoice.status != "anulada", Invoice.issue_date >= m0))
    )
    week = today + timedelta(days=7 - today.weekday())
    jobs = db.scalars(select(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")))).all()
    quotes_sent = db.scalar(select(func.count()).select_from(Quote).where(Quote.tenant_id == tid, Quote.status == "enviada"))
    quotes_won = db.scalar(select(func.count()).select_from(Quote).where(Quote.tenant_id == tid, Quote.status == "convertida", Quote.updated_at >= m0))
    warranties = [
        a
        for a in db.scalars(select(CustomerAsset).where(CustomerAsset.tenant_id == tid, CustomerAsset.warranty_until.is_not(None)))
        if a.warranty_until and 0 <= (a.warranty_until - today).days <= 45
    ]
    return {
        "pipeline": pipeline,
        "pipeline_weighted": weighted.quantize(Decimal("0.01")),
        "opportunities": len(opps),
        "receivable": receivable,
        "sold_month": sold,
        "profit_month": profit.quantize(Decimal("0.01")),
        "margin_month": pricing.margin_of(sum((d(x.price) for x in closed), Decimal(0)), sum((d(x.price) for x in closed), Decimal(0)) - profit),
        "projects_active": len(active),
        "projects_closed_month": len(closed),
        "jobs_open": len(jobs),
        "jobs_week": sum(1 for o in jobs if o.scheduled_at and o.scheduled_at.date() <= week),
        "jobs_late": sum(1 for o in jobs if o.scheduled_at and o.scheduled_at.date() < today),
        "quotes_sent": quotes_sent,
        "quotes_won_month": quotes_won,
        "warranties_soon": len(warranties),
    }
