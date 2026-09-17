"""Cotizaciones y facturas: aplicar lineas, recalcular totales, convertir, anular, pagar."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import sign_purpose_token
from ..models import (
    AuditLog,
    ExchangeRate,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentLink,
    Product,
    Quote,
    QuoteLine,
)
from ..schemas.sales import DocumentIn, LineInSchema
from .sequences import next_number
from .totals import LineIn, balance, compute_document, d


def audit(db: Session, tenant_id: int, user_id: int | None, action: str, entity: str, entity_id: int | None, diff=None, ip=None):
    db.add(AuditLog(tenant_id=tenant_id, user_id=user_id, at=datetime.now(UTC), ip=ip, action=action, entity=entity, entity_id=entity_id, diff=diff))


def today_fx(db: Session, currency: str) -> tuple[Decimal, Decimal]:
    if currency == "CRC":
        return Decimal(1), Decimal(1)
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency).order_by(ExchangeRate.date.desc()))
    return (d(fx.sell), d(fx.buy)) if fx else (Decimal(1), Decimal(1))


def _fill_line_from_product(db: Session, tenant_id: int, ln: LineInSchema) -> LineInSchema:
    if ln.product_id and (not ln.name or ln.unit_price is None):
        p = db.get(Product, ln.product_id)
        if not p or p.tenant_id != tenant_id:
            raise HTTPException(404, "Producto no encontrado")
        ln = ln.model_copy(
            update={
                "name": ln.name or p.name,
                "code": ln.code or p.code,
                "description": ln.description if ln.description is not None else p.description_invoice,
                "cabys_code": ln.cabys_code or p.cabys_code,
                "unit": ln.unit or p.unit,
                "unit_price": ln.unit_price if ln.unit_price is not None else d(p.price),
                "tax_rate": ln.tax_rate if ln.tax_rate is not None else (d(p.taxes[0].tax.rate) if p.taxes else Decimal(13)),
            }
        )
    return ln


def apply_document(db: Session, doc: Quote | Invoice, payload: DocumentIn, line_cls, tenant_id: int) -> None:
    """Escribe cabecera + lineas y recalcula totales. Reutilizado por cotizacion y factura."""
    doc.customer_id = payload.customer_id
    doc.currency = payload.currency
    if payload.fx_sell is None:
        doc.fx_sell, doc.fx_buy = today_fx(db, payload.currency)
    else:
        doc.fx_sell, doc.fx_buy = payload.fx_sell, payload.fx_buy or payload.fx_sell
    doc.issue_date = payload.issue_date or date.today()
    doc.due_date = payload.due_date
    doc.discount_type, doc.discount_value = payload.discount_type, payload.discount_value
    doc.internal_notes, doc.external_notes = payload.internal_notes, payload.external_notes
    doc.external_order, doc.activity_code = payload.external_order, payload.activity_code
    doc.medical_exemption_card = payload.medical_exemption_card

    lines = [_fill_line_from_product(db, tenant_id, ln) for ln in payload.lines]
    calc = compute_document(
        [LineIn(d(ln.quantity), d(ln.unit_price), ln.discount_type, d(ln.discount_value), d(ln.tax_rate if ln.tax_rate is not None else 13)) for ln in lines],
        payload.discount_type,
        payload.discount_value,
    )
    doc.lines.clear()
    for pos, (ln, lo) in enumerate(zip(lines, calc.lines, strict=True)):
        doc.lines.append(
            line_cls(
                position=pos,
                product_id=ln.product_id,
                code=ln.code,
                name=ln.name,
                description=ln.description,
                cabys_code=ln.cabys_code,
                unit=ln.unit or "Unid",
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                discount_type=ln.discount_type,
                discount_value=ln.discount_value,
                tax_rate=ln.tax_rate if ln.tax_rate is not None else 13,
                subtotal=lo.subtotal,
                tax_amount=lo.tax_amount,
                total=lo.total,
            )
        )
    doc.subtotal, doc.discount_total, doc.tax_total, doc.total = calc.subtotal, calc.discount_total, calc.tax_total, calc.total
    if isinstance(doc, Invoice):
        recompute_balance(doc)


def recompute_balance(inv: Invoice) -> None:
    inv.balance = balance(d(inv.total), [(p.kind, p.amount) for p in inv.payments if p.status == "confirmado"])
    if inv.status not in ("anulada",):
        if inv.balance <= 0 and d(inv.total) > 0:
            inv.status = "pagada"
        elif inv.balance < d(inv.total):
            inv.status = "parcial"
        elif inv.due_date and inv.due_date < date.today():
            inv.status = "vencida"
        elif inv.status in ("pagada", "parcial"):
            inv.status = "creado"


def create_quote(db: Session, tenant_id: int, user_id: int, payload: DocumentIn) -> Quote:
    number, _ = next_number(db, tenant_id, "COT")
    days = payload.valid_days or 15
    q = Quote(tenant_id=tenant_id, number=number, created_by=user_id, status="creado")
    apply_document(db, q, payload, QuoteLine, tenant_id)
    q.due_date = q.due_date or q.issue_date + timedelta(days=days)
    db.add(q)
    db.flush()
    audit(db, tenant_id, user_id, "create", "quote", q.id)
    return q


def create_invoice(db: Session, tenant_id: int, user_id: int, payload: DocumentIn, doc_type: str = "FE", quote: Quote | None = None) -> Invoice:
    number, consecutive = next_number(db, tenant_id, doc_type)
    inv = Invoice(tenant_id=tenant_id, number=number, consecutive=consecutive, doc_type=doc_type, created_by=user_id, status="creado")
    inv.sale_condition = payload.sale_condition or "01"
    inv.credit_days = payload.credit_days or 0
    inv.payment_method = payload.payment_method or "01"
    apply_document(db, inv, payload, InvoiceLine, tenant_id)
    inv.due_date = inv.due_date or inv.issue_date + timedelta(days=payload.valid_days or inv.credit_days or 8)
    if quote:
        inv.quote_id = quote.id
    db.add(inv)
    db.flush()
    audit(db, tenant_id, user_id, "create", "invoice", inv.id, {"from_quote": quote.id if quote else None})
    return inv


def quote_to_payload(q: Quote) -> DocumentIn:
    return DocumentIn(
        customer_id=q.customer_id,
        currency=q.currency,
        fx_sell=d(q.fx_sell),
        fx_buy=d(q.fx_buy),
        discount_type=q.discount_type,
        discount_value=d(q.discount_value),
        internal_notes=q.internal_notes,
        external_notes=q.external_notes,
        external_order=q.external_order,
        activity_code=q.activity_code,
        medical_exemption_card=q.medical_exemption_card,
        lines=[
            LineInSchema(
                product_id=ln.product_id,
                code=ln.code,
                name=ln.name,
                description=ln.description,
                cabys_code=ln.cabys_code,
                unit=ln.unit,
                quantity=d(ln.quantity),
                unit_price=d(ln.unit_price),
                discount_type=ln.discount_type,
                discount_value=d(ln.discount_value),
                tax_rate=d(ln.tax_rate),
            )
            for ln in q.lines
        ],
    )


def convert_quote(db: Session, tenant_id: int, user_id: int, q: Quote) -> Invoice:
    if q.status in ("convertida", "anulada"):
        raise HTTPException(409, f"La cotizacion esta {q.status}")
    inv = create_invoice(db, tenant_id, user_id, quote_to_payload(q), quote=q)
    q.status = "convertida"
    q.converted_invoice_id = inv.id
    audit(db, tenant_id, user_id, "convert", "quote", q.id, {"invoice_id": inv.id})
    return inv


def add_payment(db: Session, tenant_id: int, user_id: int, inv: Invoice, data) -> Payment:
    if inv.status == "anulada":
        raise HTTPException(409, "La factura esta anulada")
    p = Payment(
        tenant_id=tenant_id,
        invoice_id=inv.id,
        method=data.method,
        kind=data.kind,
        currency=data.currency or inv.currency,
        amount=data.amount,
        tip=data.tip or 0,
        bank_account_id=data.bank_account_id,
        external_ref=data.external_ref,
        paid_at=data.paid_at or date.today(),
        notify_customer=data.notify_customer,
        notes=data.notes,
        created_by=user_id,
    )
    db.add(p)
    db.flush()  # aplica defaults (status=confirmado) antes de recalcular el saldo
    if p not in inv.payments:
        inv.payments.append(p)
    recompute_balance(inv)
    db.flush()
    audit(db, tenant_id, user_id, "pay", "invoice", inv.id, {"payment_id": p.id, "amount": str(data.amount), "kind": data.kind})
    return p


def get_or_create_payment_link(db: Session, tenant_id: int, inv: Invoice) -> PaymentLink:
    now = datetime.now(UTC)
    link = db.scalar(select(PaymentLink).where(PaymentLink.invoice_id == inv.id, PaymentLink.expires_at > now))
    if link:
        return link
    token = sign_purpose_token("paylink", {"inv": inv.id, "tid": tenant_id}, settings.payment_link_days)
    link = PaymentLink(
        tenant_id=tenant_id,
        invoice_id=inv.id,
        token=token,
        url=f"{settings.public_base_url}/pagar/{token}",
        expires_at=now + timedelta(days=settings.payment_link_days),
    )
    db.add(link)
    db.flush()
    return link


def whatsapp_share_url(inv: Invoice, link_url: str, customer_name: str | None, business: str) -> str:
    from urllib.parse import quote as urlquote

    amount = f"{d(inv.balance):,.2f}"
    text = f"Hola {customer_name or ''}, le comparte {business}: factura {inv.number} por {inv.currency} {amount}. Puede pagar en linea aqui: {link_url}"
    return "https://wa.me/?text=" + urlquote(text)
