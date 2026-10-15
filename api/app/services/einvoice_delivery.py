"""Entrega al cliente de lo emitido (Decreto 44739-H art. 18): el XML firmado + el PDF al emitir y, cuando llegue, la
respuesta de Hacienda. Idempotente por delivered_at / hacienda_delivered_at (el worker puede llamarlo dos veces).

Las marcas se ponen solo cuando TODOS los correos salieron (enviado o simulado). Los adjuntos no se guardan en la base y
mail.retry_outbox reintenta sin ellos: un correo de entrega que fallo queda "fallido" (fuera del reintento generico) y
sin la marca, para que hacienda.send_pending vuelva a entregar CON el XML y el PDF (solo a quien no lo recibio, y con una
espera que se duplica desde RETRY_WAIT, para no llenar la bitacora de salida mientras el proveedor de correo esta caido).
Tras MAX_FAILS fallos a una direccion (correo invalido que el proveedor rechaza siempre) ya no se insiste: la etapa queda
con delivery_failed_at / hacienda_delivery_failed_at, sale de la cola del worker y se avisa a los administradores para
que corrijan el correo y reenvien desde la factura.

Solo en pruebas y produccion. En pruebas NO va al cliente sino a los administradores, con "AMBIENTE DE PRUEBAS · sin
validez fiscal" (los clientes reales no deben recibir comprobantes de prueba). En simulado sigue el correo de siempre
(solo PDF con la franja)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, EInvoiceDocument, EmailOutbox, Invoice, Order, Tenant, TenantUser
from .documents import audit
from .mail import queue_email

KIND = {"FE": "factura electrónica", "TE": "tiquete electrónico", "NC": "nota de crédito", "ND": "nota de débito"}
TEST_TAG = "AMBIENTE DE PRUEBAS · sin validez fiscal"
SENT = ("enviado", "simulado")
FAILED = "fallido"  # mail.retry_outbox solo toma pendiente|error: este no sale de nuevo sin sus adjuntos
RETRY_WAIT = timedelta(minutes=15)  # tras el 1er fallo; despues se duplica (15, 30, 60, 120, 240 min)
MAX_WAIT = timedelta(hours=4)
MAX_FAILS = 6  # ~8 h de intentos por direccion y etapa: un proveedor de correo caido se recupera; un correo malo, no


def _customer_email(db: Session, inv: Invoice) -> str | None:
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    if c is not None and c.tenant_id != inv.tenant_id:
        c = None  # un cliente de otra empresa nunca recibe el comprobante (defensa: la factura ya no deberia tenerlo)
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


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _previous(db: Session, doc: EInvoiceDocument, inv: Invoice, stage: str) -> list[EmailOutbox]:
    """Correos de entrega anteriores de ESTE comprobante y etapa (por la descripcion de adjuntos de la bitacora)."""
    rows = db.scalars(select(EmailOutbox).where(EmailOutbox.tenant_id == doc.tenant_id, EmailOutbox.entity == "invoice", EmailOutbox.entity_id == inv.id))
    return [m for m in rows if any(a.get("kind") == "einvoice" and a.get("ref") == doc.id and a.get("stage") == stage for a in (m.attachments or []))]


def _wait(fails: int) -> timedelta:
    return min(RETRY_WAIT * 2 ** max(fails - 1, 0), MAX_WAIT)


def _plan(db: Session, doc: EInvoiceDocument, inv: Invoice, stage: str) -> tuple[list[str], bool, list[str]]:
    """(a quien mandar ahora, alguien espera su reintento, a quien ya no se insiste). Por direccion: lo recibido no se
    repite, lo que fallo espera RETRY_WAIT duplicado por cada fallo y, tras MAX_FAILS, se deja."""
    prev = _previous(db, doc, inv, stage)
    done = {m.to.strip().lower() for m in prev if m.status in SENT}
    fails: dict[str, list[datetime]] = {}
    for m in prev:
        if m.status == FAILED:
            fails.setdefault(m.to.strip().lower(), []).append(_aware(m.created_at) or datetime.now(UTC))
    now = datetime.now(UTC)
    ready, waiting, given_up = [], False, []
    for addr in recipients(db, doc, inv):
        k = addr.strip().lower()
        f = fails.get(k, [])
        if k in done:
            continue
        if len(f) >= MAX_FAILS:
            given_up.append(addr)
        elif f and now - max(f) < _wait(len(f)):
            waiting = True
        else:
            ready.append(addr)
    return ready, waiting, given_up


def _send(db: Session, t: Tenant, doc: EInvoiceDocument, inv: Invoice, stage: str, to: list[str], subject: str, html: str, files: list[tuple[str, bytes]]) -> tuple[int, bool]:
    """(correos que salieron a la cola, todos entregados)."""
    desc = [{"name": n_, "kind": "einvoice", "ref": doc.id, "stage": stage} for n_, _ in files]
    n, ok = 0, True
    for addr in to:
        m = queue_email(db, t, addr, subject, html, "invoice", inv.id, desc, files=files)
        n += 1
        if m.status not in SENT:
            m.status = FAILED
            ok = False
    return n, ok


def _close(db: Session, t: Tenant, doc: EInvoiceDocument, inv: Invoice, stage: str, n: int, finished: bool, given_up: list[str]) -> None:
    """Marca la etapa: entregada si todos lo recibieron; sin entregar (fuera de la cola del worker + aviso) si a alguien
    ya no se le insiste. Sin terminar (fallo ahora o espera su reintento) no marca nada."""
    prefix = "" if stage == "emitido" else "hacienda_"
    if finished and given_up:
        setattr(doc, f"{prefix}delivery_failed_at", datetime.now(UTC))
        _alert_undeliverable(db, t, doc, inv, stage, given_up)
    elif finished:
        setattr(doc, f"{prefix}delivered_at", datetime.now(UTC))
    if n or finished:
        info = {"etapa": stage, "correos": n, "modo": doc.mode, "entregado": finished and not given_up}
        if finished and given_up:
            info["sin_entregar"] = len(given_up)  # sin las direcciones: la bitacora no guarda correos de clientes
        audit(db, t.id, None, "einvoice_delivery", "invoice", inv.id, info)


def _alert_undeliverable(db: Session, t: Tenant, doc: EInvoiceDocument, inv: Invoice, stage: str, given_up: list[str]) -> None:
    from html import escape

    from .mail import notify_roles

    kind = KIND.get(doc.doc_type, "comprobante electrónico")
    what = "el XML y el PDF" if stage == "emitido" else "la respuesta de Hacienda"
    html = (
        f"<p>No se pudo entregar {what} de la {escape(kind)} <b>{escape(inv.number or '')}</b> a {escape(', '.join(given_up))}: "
        f"el correo falló {MAX_FAILS} veces y ya no se reintenta.</p>"
        "<p>Revise el correo del cliente y reenvíe desde la factura (Enviar por correo).</p>"
    )
    notify_roles(db, t, ("admin",), f"Entrega fallida · {kind} {inv.number}", html, "invoice", inv.id)


def deliver(db: Session, doc_id: int, stage: str) -> int:
    """stage: "emitido" (XML firmado + PDF) | "hacienda" (respuesta de Hacienda). Devuelve cuantos correos salieron a la cola.
    La marca (delivered_at / hacienda_delivered_at) queda solo si todos salieron; si alguno fallo, se reintenta despues
    (hasta MAX_FAILS: entonces delivery_failed_at / hacienda_delivery_failed_at y aviso al admin)."""
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
        if doc.delivered_at or doc.delivery_failed_at or not doc.xml_document:
            return 0
        ready, waiting, given_up = _plan(db, doc, inv, stage)
        if waiting and not ready:
            return 0  # nadie listo: ni se arma el PDF
        n, ok = 0, True
        if ready:
            files = [(f"{doc.clave}.xml", doc.xml_document.encode("utf-8"))]  # los bytes firmados exactos
            pdf = _pdf(db, inv, t)
            if pdf:
                files.append((f"{inv.number}.pdf", pdf))
            lines = [f"Le compartimos la <b>{kind} {inv.number}</b> (clave {doc.clave}).", "Adjuntamos el comprobante electrónico (XML) y su representación gráfica (PDF). Cuando Hacienda lo confirme le enviaremos su respuesta."]
            subject = f"{kind.capitalize()} {inv.number}{tag}"
            n, ok = _send(db, t, doc, inv, stage, ready, subject, _html(t, f"{kind.capitalize()} {inv.number}", lines, test), files)
        _close(db, t, doc, inv, stage, n, ok and not waiting, given_up)
        return n
    if stage == "hacienda":
        if doc.hacienda_delivered_at or doc.hacienda_delivery_failed_at or doc.status not in ("aceptada", "rechazada") or not doc.xml_response:
            return 0
        ready, waiting, given_up = _plan(db, doc, inv, stage)
        if waiting and not ready:
            return 0
        n, ok = 0, True
        if ready:
            files = [(f"{doc.clave}-respuesta-hacienda.xml", doc.xml_response.encode("utf-8"))]
            if doc.status == "aceptada":
                lines = [f"Hacienda aceptó la <b>{kind} {inv.number}</b>. Adjuntamos su respuesta (MensajeHacienda)."]
            else:
                lines = [f"Hacienda rechazó la <b>{kind} {inv.number}</b>: ese comprobante no tiene validez.", "Emitiremos uno nuevo y se lo enviaremos. Adjuntamos la respuesta de Hacienda."]
            subject = f"Respuesta de Hacienda · {kind} {inv.number}{tag}"
            n, ok = _send(db, t, doc, inv, stage, ready, subject, _html(t, f"Respuesta de Hacienda · {inv.number}", lines, test), files)
        _close(db, t, doc, inv, stage, n, ok and not waiting, given_up)
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
