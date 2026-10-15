"""Entrega al cliente de lo emitido (Decreto 44739-H art. 18): el XML firmado + el PDF al emitir y, cuando llegue, la
respuesta de Hacienda. Idempotente por delivered_at / hacienda_delivered_at (el worker puede llamarlo dos veces).

Solo en pruebas y produccion. En pruebas NO va al cliente sino a los administradores, con "AMBIENTE DE PRUEBAS · sin
validez fiscal" (los clientes reales no deben recibir comprobantes de prueba). En simulado sigue el correo de siempre
(solo PDF con la franja)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, EInvoiceDocument, Invoice, Order, Tenant, TenantUser
from .documents import audit
from .mail import queue_email

KIND = {"FE": "factura electrónica", "TE": "tiquete electrónico", "NC": "nota de crédito", "ND": "nota de débito"}
TEST_TAG = "AMBIENTE DE PRUEBAS · sin validez fiscal"


def _customer_email(db: Session, inv: Invoice) -> str | None:
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    if c and (c.email or "").strip():
        return c.email.strip()
    o = db.scalar(select(Order).where(Order.invoice_id == inv.id))
    return ((o.contact or {}).get("email") or "").strip() or None if o else None


def _admins(db: Session, tenant_id: int) -> list[str]:
    out = []
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.active, TenantUser.role_code == "admin")):
        e = (m.user.email or "").strip().lower()
        if e and e not in out:
            out.append(e)
    return out


def recipients(db: Session, doc: EInvoiceDocument, inv: Invoice) -> list[str]:
    if doc.mode == "pruebas":
        return _admins(db, doc.tenant_id)
    e = _customer_email(db, inv)
    return [e] if e else []


def _pdf(db: Session, inv: Invoice, t: Tenant) -> bytes | None:
    from .render import render_html, render_pdf

    try:
        return render_pdf(render_html(db, inv, t))
    except Exception:  # noqa: BLE001
        return None


def _html(t: Tenant, title: str, lines: list[str], test: bool) -> str:
    banner = f'<p style="background:#fff4d6;color:#7a5a00;padding:8px 12px;border-radius:8px;font-weight:700">{TEST_TAG}</p>' if test else ""
    body = "".join(f"<p>{x}</p>" for x in lines)
    return f"""<div style="font-family:Manrope,Arial,sans-serif;max-width:560px;margin:auto;color:#15131a">
<div style="border-bottom:3px solid #e2233a;padding:12px 0;font-size:18px;font-weight:700">{t.name}</div>{banner}
<h3 style="margin:14px 0 6px">{title}</h3>{body}
<p style="color:#8a858f;font-size:12px">Cualquier consulta, responda a este correo.</p></div>"""


def deliver(db: Session, doc_id: int, stage: str) -> int:
    """stage: "emitido" (XML firmado + PDF) | "hacienda" (respuesta de Hacienda). Devuelve cuantos correos salieron a la cola."""
    doc = db.get(EInvoiceDocument, doc_id)
    if not doc or doc.mode not in ("pruebas", "produccion"):
        return 0
    inv = db.get(Invoice, doc.invoice_id)
    t = db.get(Tenant, doc.tenant_id)
    if not inv or not t:
        return 0
    test = doc.mode == "pruebas"
    kind = KIND.get(doc.doc_type, "comprobante electrónico")
    tag = f" · {TEST_TAG}" if test else ""
    if stage == "emitido":
        if doc.delivered_at or not doc.xml_document:
            return 0
        to = recipients(db, doc, inv)
        files = [(f"{doc.clave}.xml", doc.xml_document.encode("utf-8"))]  # los bytes firmados exactos
        pdf = _pdf(db, inv, t)
        if pdf:
            files.append((f"{inv.number}.pdf", pdf))
        lines = [f"Le compartimos la <b>{kind} {inv.number}</b> (clave {doc.clave}).", "Adjuntamos el comprobante electrónico (XML) y su representación gráfica (PDF). Cuando Hacienda lo confirme le enviaremos su respuesta."]
        n = 0
        for addr in to:
            queue_email(
                db, t, addr, f"{kind.capitalize()} {inv.number}{tag}", _html(t, f"{kind.capitalize()} {inv.number}", lines, test), "invoice", inv.id,
                [{"name": n_, "kind": "einvoice", "ref": doc.id} for n_, _ in files], files=files,
            )
            n += 1
        doc.delivered_at = datetime.now(UTC)
        audit(db, t.id, None, "einvoice_delivery", "invoice", inv.id, {"etapa": "emitido", "correos": n, "modo": doc.mode})
        return n
    if stage == "hacienda":
        if doc.hacienda_delivered_at or doc.status not in ("aceptada", "rechazada") or not doc.xml_response:
            return 0
        to = recipients(db, doc, inv)
        files = [(f"{doc.clave}-respuesta-hacienda.xml", doc.xml_response.encode("utf-8"))]
        if doc.status == "aceptada":
            lines = [f"Hacienda aceptó la <b>{kind} {inv.number}</b>. Adjuntamos su respuesta (MensajeHacienda)."]
        else:
            lines = [f"Hacienda rechazó la <b>{kind} {inv.number}</b>: ese comprobante no tiene validez.", "Emitiremos uno nuevo y se lo enviaremos. Adjuntamos la respuesta de Hacienda."]
        n = 0
        for addr in to:
            queue_email(
                db, t, addr, f"Respuesta de Hacienda · {kind} {inv.number}{tag}", _html(t, f"Respuesta de Hacienda · {inv.number}", lines, test), "invoice", inv.id,
                [{"name": n_, "kind": "einvoice", "ref": doc.id} for n_, _ in files], files=files,
            )
            n += 1
        doc.hacienda_delivered_at = datetime.now(UTC)
        audit(db, t.id, None, "einvoice_delivery", "invoice", inv.id, {"etapa": "hacienda", "correos": n, "modo": doc.mode})
        return n
    return 0


def attachments_for_invoice(db: Session, inv: Invoice) -> list[tuple[str, bytes]]:
    """Para POST /invoices/{id}/email: XML firmado (y la respuesta de Hacienda si ya llego) del ultimo comprobante directo."""
    doc = db.scalar(
        select(EInvoiceDocument)
        .where(EInvoiceDocument.invoice_id == inv.id, EInvoiceDocument.doc_type == inv.doc_type, EInvoiceDocument.mode.in_(("pruebas", "produccion")), EInvoiceDocument.xml_document.is_not(None))
        .order_by(EInvoiceDocument.id.desc())
    )
    if not doc:
        return []
    out = [(f"{doc.clave}.xml", doc.xml_document.encode("utf-8"))]
    if doc.xml_response:
        out.append((f"{doc.clave}-respuesta-hacienda.xml", doc.xml_response.encode("utf-8")))
    return out
