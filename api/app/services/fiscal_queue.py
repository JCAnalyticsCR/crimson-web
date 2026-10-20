"""Cola fiscal "Por emitir" (plan maestro 1.7).

Mientras el modo fiscal es "ninguno" la plataforma no emite: cada venta pagada o enviada se emite en Fygaro el mismo
dia y aqui se marca "emitida fuera" con su numero y su fecha. Lo marcado nunca se vuelve a emitir (einvoice.emit
responde 409) ni se reemite al pasar a produccion (plan 2.1): duplicaria el comprobante ante Hacienda.

- Por emitir = facturas no anuladas en "sin_emitir" que ya salieron de la empresa: enviadas, pagadas, con algun cobro,
  o vencidas con correo enviado o enlace de pago. Un borrador sin enviar ni cobrar no entra.
- Marcar: desde "sin_emitir" o desde lo que solo se emitio en simulado o en pruebas (sin validez fiscal, plan 0.5).
  Nunca con un comprobante de produccion vivo ni con uno en camino a Hacienda.
- Las fechas son las de Costa Rica (despues de las 18:00 la fecha UTC ya es la de manana).
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import EInvoiceDocument, EmailOutbox, Invoice, PaymentLink, Tenant
from ..providers.einvoice import cr_now
from .documents import audit
from .einvoice import doc_mode

QUEUE_STATUSES = ("enviada", "pagada", "parcial")
IN_FLIGHT = ("por_enviar", "enviando", "enviado", "procesando", "pendiente")
# numero del comprobante en Fygaro: consecutivo de 20 digitos, clave de 50 o el numero interno (FEC-…)
NUMBER_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z\-_/. ]{0,49}$")


def cr_today() -> date:
    return cr_now().date()


def queue_where(tenant_id: int) -> list:
    """La UNICA definicion de "Por emitir": la lista, el contador y el correo diario salen de aqui."""
    sent = (
        select(EmailOutbox.id)
        .where(
            EmailOutbox.tenant_id == Invoice.tenant_id, EmailOutbox.entity == "invoice", EmailOutbox.entity_id == Invoice.id, EmailOutbox.status == "enviado"
        )
        .exists()
    )
    link = select(PaymentLink.id).where(PaymentLink.invoice_id == Invoice.id).exists()
    return [
        Invoice.tenant_id == tenant_id,
        Invoice.einvoice_status == "sin_emitir",
        Invoice.status != "anulada",
        or_(Invoice.status.in_(QUEUE_STATUSES), Invoice.balance < Invoice.total, and_(Invoice.status == "vencida", or_(sent, link))),
    ]


def queue_rows(db: Session, tenant_id: int, scope=None, limit: int = 200) -> list[Invoice]:
    q = select(Invoice).where(*queue_where(tenant_id))
    if scope is not None:
        q = q.where(scope)
    return list(db.scalars(q.order_by(Invoice.issue_date, Invoice.id).limit(limit)))


def queue_count(db: Session, tenant_id: int, scope=None) -> int:
    q = select(func.count()).select_from(Invoice).where(*queue_where(tenant_id))
    if scope is not None:
        q = q.where(scope)
    return db.scalar(q) or 0


def month_of(today: date) -> tuple[date, date]:
    first = today.replace(day=1)
    nxt = first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1)
    return first, nxt


def external_month_count(db: Session, tenant_id: int, today: date | None = None, scope=None) -> int:
    """Emitidas fuera este mes (por la fecha de emision en Fygaro, mes de Costa Rica)."""
    first, nxt = month_of(today or cr_today())
    q = (
        select(func.count())
        .select_from(Invoice)
        .where(Invoice.tenant_id == tenant_id, Invoice.einvoice_status == "emitida_fuera", Invoice.external_date >= first, Invoice.external_date < nxt)
    )
    if scope is not None:
        q = q.where(scope)
    return db.scalar(q) or 0


def _docs(db: Session, inv: Invoice) -> list[EInvoiceDocument]:
    return list(db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id).order_by(EInvoiceDocument.id)))


def check_markable(db: Session, inv: Invoice) -> None:
    if inv.status == "anulada":
        raise HTTPException(409, "La factura está anulada")
    if inv.einvoice_status == "emitida_fuera":
        raise HTTPException(409, f"La factura ya está marcada emitida fuera de la plataforma (n.º {inv.external_number})")
    if inv.einvoice_status == "importada":
        raise HTTPException(409, "La factura viene del histórico importado: ya está emitida fuera de la plataforma")
    docs = _docs(db, inv)
    if inv.einvoice_status == "pendiente" or any(d.status in IN_FLIGHT for d in docs):
        raise HTTPException(409, "La factura tiene un comprobante en camino a Hacienda: esperá la respuesta antes de marcarla")
    if any(doc_mode(d) == "produccion" and d.status != "rechazada" for d in docs):
        raise HTTPException(409, "La factura tiene un comprobante de Producción en la plataforma: no se marca emitida fuera")


def clean_number(number: str) -> str:
    n = re.sub(r"\s+", " ", (number or "").strip())
    if not NUMBER_RE.match(n):
        raise HTTPException(422, "Número de Fygaro inválido: letras, números y guiones, hasta 50 caracteres")
    return n


def mark_external(db: Session, tenant: Tenant, user_id: int | None, inv: Invoice, number: str, when: date, ip: str | None = None) -> Invoice:
    """Marca la factura emitida en Fygaro. Bloquea la fila: dos clics (o dos personas) no la marcan dos veces."""
    db.execute(select(Invoice.id).where(Invoice.id == inv.id).with_for_update())
    db.refresh(inv, ["einvoice_status", "status", "external_number"])
    check_markable(db, inv)
    n = clean_number(number)
    if when > cr_today():
        raise HTTPException(422, "La fecha de emisión en Fygaro no puede ser posterior a hoy")
    dup = db.scalar(select(Invoice).where(Invoice.tenant_id == inv.tenant_id, Invoice.id != inv.id, Invoice.external_number == n))
    if dup is not None:
        raise HTTPException(409, f"El n.º {n} ya está en la factura {dup.number}: revisá el número")
    antes = inv.einvoice_status
    inv.einvoice_status, inv.external_number, inv.external_date = "emitida_fuera", n, when
    audit(db, tenant.id, user_id, "emitida_fuera", "invoice", inv.id, {"antes": antes, "numero": n, "fecha": when.isoformat()}, ip=ip)
    db.flush()
    return inv


def _status_from_docs(docs: list[EInvoiceDocument]) -> str:
    """El estado que dejaria hacienda_sync con el ultimo comprobante de la factura (sin NC)."""
    last = next((d for d in reversed(docs) if d.doc_type != "NC"), None)
    if last is None:
        return "sin_emitir"
    if last.status in IN_FLIGHT:
        return "pendiente"
    if last.status in ("aceptada", "rechazada"):
        return last.status
    return "sin_emitir" if (last.attempts or 0) == 0 else "error"


def undo_external(db: Session, tenant: Tenant, user_id: int | None, inv: Invoice, ip: str | None = None) -> Invoice:
    """Marca puesta por error (otra factura, otro numero): vuelve al estado que dicen sus comprobantes."""
    db.execute(select(Invoice.id).where(Invoice.id == inv.id).with_for_update())
    db.refresh(inv, ["einvoice_status", "external_number", "external_date"])
    if inv.einvoice_status != "emitida_fuera":
        raise HTTPException(409, "La factura no está marcada emitida fuera de la plataforma")
    despues = _status_from_docs(_docs(db, inv))
    audit(
        db,
        tenant.id,
        user_id,
        "emitida_fuera_deshacer",
        "invoice",
        inv.id,
        {"numero": inv.external_number, "fecha": inv.external_date.isoformat() if inv.external_date else None, "despues": despues},
        ip=ip,
    )
    inv.einvoice_status, inv.external_number, inv.external_date = despues, None, None
    db.flush()
    return inv
