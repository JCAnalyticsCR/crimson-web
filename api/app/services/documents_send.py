"""Envio de cotizaciones y facturas al cliente, reutilizable (plan 1.3).

Antes vivia dentro del router (POST /{kind}/{id}/email). Ahora lo usan tambien la recurrencia que se envia sola
(mensualidad de aliados, plan 1.4) y la secuencia de cobro (tasks/cobranza.py): el mismo PDF, el mismo enlace de
pago, el mismo XML fiscal y la misma regla de "enviada solo si el proveedor acepto el correo".
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, EmailOutbox, Invoice, Quote, Tenant
from . import documents as docsvc
from .documents import audit
from .invoice_states import mark_sent
from .mail import doc_email_html, queue_email
from .render import money, render_html, render_pdf


# ---------- correo idempotente (interfaz del plan 1.2) ----------
def _accepts_dedupe() -> bool:
    try:
        return "dedupe_key" in inspect.signature(queue_email).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False


def queue_once(
    db: Session,
    tenant: Tenant,
    to: str,
    subject: str,
    html: str,
    entity: str,
    entity_id: int,
    attachments: list | None = None,
    files: list[tuple[str, bytes]] | None = None,
    dedupe_key: str | None = None,
) -> EmailOutbox:
    """queue_email con dedupe_key cuando el outbox ya la soporta (plan 1.2: la misma llave devuelve la fila existente).
    Mientras tanto la idempotencia de cada aviso la da su propia bitacora (collection_reminder, last_send_status)."""
    kw = {"dedupe_key": dedupe_key} if dedupe_key and _accepts_dedupe() else {}
    return queue_email(db, tenant, to, subject, html, entity, entity_id, attachments, files=files, **kw)


def queued_before(db: Session, tenant_id: int, to: str, subject_prefix: str) -> bool:
    """Ya hay en el outbox un correo a `to` cuyo asunto empieza con `subject_prefix` (que lleva la fecha del resumen).
    Resguardo de los resumenes al admin ante un doble disparo del beat o una reentrega de Celery (acks_late) que no
    depende del dedupe_key del plan 1.2."""
    q = select(EmailOutbox.id).where(EmailOutbox.tenant_id == tenant_id, EmailOutbox.to == to, EmailOutbox.subject.startswith(subject_prefix, autoescape=True))
    return db.scalar(q.limit(1)) is not None


# ---------- adjuntos ----------
def pdf_of(db: Session, tenant: Tenant, doc: Quote | Invoice, kind: str | None = None) -> bytes | None:
    """PDF del documento. Si el servidor no puede generarlo (sin WeasyPrint) el correo no promete un adjunto."""
    try:
        return render_pdf(render_html(db, doc, tenant, kind=kind))
    except Exception:  # noqa: BLE001  un PDF que falla no frena el envio del enlace de pago
        return None


def fiscal_files(db: Session, inv: Invoice, real_only: bool = False) -> list[tuple[str, bytes]]:
    """XML firmado y respuesta de Hacienda (Decreto 44739-H art. 18). real_only: solo produccion (recordatorios)."""
    from .einvoice_delivery import attachments_for_invoice

    return attachments_for_invoice(db, inv, ("produccion",) if real_only else ("pruebas", "produccion"))


# ---------- envio ----------
def send_document(
    db: Session,
    tenant: Tenant,
    doc: Quote | Invoice,
    to: str | None = None,
    message: str | None = None,
    with_payment_link: bool = True,
    actor: int | None = None,
    *,
    ip: str | None = None,
    label: str | None = None,
    dedupe_key: str | None = None,
) -> dict:
    """Manda la cotizacion o la factura con su PDF (y en facturas el enlace de pago y el XML fiscal si lo hay).

    Devuelve {status, sent, to, note, version, outbox_id}. Solo con el correo aceptado por el proveedor ("enviado") la
    factura pasa a "enviada" y queda por cobrar (sent_at), y la cotizacion congela su version: simulado (sin llave de
    Resend) o error no cuentan, porque harian creer que el cliente ya la tiene. No hace commit.
    label: "aviso de cobro" para la mensualidad sin comprobante fiscal (modo de Hacienda "ninguno").
    dedupe_key: solo los envios automaticos (regla 6 del plan); el reenvio de una persona nunca lleva llave, porque con
    el outbox del plan 1.2 la misma llave devolveria el intento que fallo en vez de mandarlo otra vez."""
    is_inv = isinstance(doc, Invoice)
    c = db.get(Customer, doc.customer_id) if doc.customer_id else None
    if c is not None and c.tenant_id != doc.tenant_id:
        c = None  # nunca el correo de un cliente de otra empresa
    to = (to or (c.email if c else None) or "").strip() or None
    if not to:
        raise HTTPException(422, "El cliente no tiene correo; indique uno")
    if not is_inv:
        from .units import check_ready

        check_ready(doc, "enviar la cotización")
    kind_text = label or ("factura" if is_inv else "cotización")
    link = None
    if is_inv and with_payment_link and Decimal(str(doc.balance)) > 0 and doc.status != "anulada":
        link = docsvc.get_or_create_payment_link(db, tenant.id, doc).url
    subject = f"{kind_text[:1].upper()}{kind_text[1:]} {doc.number} · {tenant.name}"
    pdf = pdf_of(db, tenant, doc, kind=(f"{kind_text[:1].upper()}{kind_text[1:]}" if label else None))
    files = [(f"{doc.number}.pdf", pdf)] if pdf else []
    if is_inv:
        files += fiscal_files(db, doc)
    entity = "invoice" if is_inv else "quote"
    m = queue_once(
        db,
        tenant,
        to,
        subject,
        doc_email_html(
            tenant,
            kind_text,
            doc.number,
            money(doc.total, doc.currency),
            link,
            message or (tenant.settings or {}).get("invoice_footer" if is_inv else "quote_footer"),
            attached=pdf is not None,
        ),
        entity,
        doc.id,
        [{"name": n, "kind": "invoices" if is_inv else "quotes", "ref": doc.id} for n, _ in files],
        files=files or None,
        dedupe_key=dedupe_key,
    )
    sent = m.status == "enviado"
    if sent and doc.status == "creado":
        doc.status = "enviada"
    if sent and is_inv:
        mark_sent(doc)
    version = None
    if sent and not is_inv:
        from ..models import Opportunity
        from . import quote_versions as qv

        # lo que salio queda congelado como version (v1, v2... si se edito despues de enviarla)
        version = qv.record_send(db, tenant, actor, doc, "correo", to, ip=ip)[0].version
        for o in db.scalars(select(Opportunity).where(Opportunity.tenant_id == tenant.id, Opportunity.quote_id == doc.id)):
            if o.status in ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando"):
                audit(db, tenant.id, actor, "status", "opportunity", o.id, {"de": o.status, "a": "enviada"}, ip=ip)
                o.status = "enviada"
    audit(db, tenant.id, actor, "send", entity, doc.id, {"to": to, "mail": m.status, **({"como": label} if label else {})}, ip=ip)
    if m.status == "simulado":
        note = "El correo NO salió: no hay servicio de correo configurado (falta la llave de Resend). El documento no se marcó como enviado."
    elif m.status == "error":
        note = f"El correo NO salió ({(m.error or 'error del proveedor')[:160]}). El documento no se marcó como enviado."
    else:
        note = None
    return {"status": m.status, "sent": sent, "to": to, "note": note, "version": version, "outbox_id": m.id}
