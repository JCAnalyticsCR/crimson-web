"""Tareas del worker para Hacienda y recepcion (worker/celery_app.py solo las agenda). Reciben una sesion; nunca
credenciales ni XML como argumento."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import EInvoiceDocument, FiscalCredential, Invoice, ReceivedDocument, ReceptionResponse, Tenant, TenantUser
from . import hacienda_config as hc
from .reception_calc import business_days_left

log = logging.getLogger("crimson.hacienda")


def poll_inboxes(db: Session) -> int:
    """Cada empresa con buzon (entorno o Ajustes). Arriendo tomado -> se salta sin error; un buzon con error no frena a los demas."""
    from .inbox import MailboxBusy, mailbox_config, poll

    n = 0
    for t in db.scalars(select(Tenant).where(Tenant.active)).all():
        if mailbox_config(db, t) is None:
            continue
        try:
            n += poll(db, t)["nuevos"]
        except MailboxBusy:
            continue
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning("buzon %s: %s", t.slug, type(e).__name__)
    return n


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def alerts_for(db: Session, t: Tenant, today: date | None = None) -> list[str]:
    today = today or date.today()
    now = datetime.now(UTC)
    out: list[str] = []
    for d in db.scalars(select(ReceivedDocument).where(ReceivedDocument.tenant_id == t.id, ReceivedDocument.status == "por_revisar")):
        left = business_days_left(today, d.deadline)
        if left is None:
            continue
        if left == -1:
            out.append(f"Venció ayer el plazo para responder el comprobante de {d.issuer_name or d.issuer_id} ({d.consecutive or d.clave[-10:]})")
        elif 0 <= left <= 2:
            out.append(f"Quedan {left} días hábiles para responder el comprobante de {d.issuer_name or d.issuer_id} ({d.consecutive or d.clave[-10:]})")
    def recent(dt) -> bool:
        return bool(_aware(dt) and now - _aware(dt) <= timedelta(hours=24))

    watched = ("por_enviar", "rechazado", "enviado", "procesando", "error")
    # solo el ULTIMO mensaje de cada recepcion y solo si sigue respondida: un intento viejo (el de pruebas antes de
    # reabrir, uno que reemplazo el historico de Fygaro) o una recepcion que ya se reabrio o se respondio de nuevo no
    # piden nada a nadie
    last = (
        select(ReceptionResponse.received_document_id.label("did"), func.max(ReceptionResponse.attempt).label("att"))
        .where(ReceptionResponse.tenant_id == t.id)
        .group_by(ReceptionResponse.received_document_id)
        .subquery()
    )
    q = (
        select(ReceptionResponse)
        .join(last, (ReceptionResponse.received_document_id == last.c.did) & (ReceptionResponse.attempt == last.c.att))
        .where(ReceptionResponse.tenant_id == t.id, ReceptionResponse.status.in_(watched))
    )
    for r in db.scalars(q):
        d = db.get(ReceivedDocument, r.received_document_id)
        if d is None or d.status != "respondido":
            continue
        left = business_days_left(today, d.deadline)
        if r.status == "por_enviar" and left is not None and left <= 2:  # 0 = vence hoy: tambien se avisa
            out.append(f"Mensaje receptor sin enviar con plazo a punto de vencer ({d.issuer_name}): {r.last_error or 'revise las credenciales'}")
        elif r.status == "rechazado" and recent(r.resolved_at):
            out.append(f"Hacienda rechazó el mensaje receptor de {d.issuer_name if d else ''}: {r.hacienda_detail or ''}"[:300])
        elif r.status == "error" and r.mode in ("pruebas", "produccion") and recent(r.resolved_at):
            # incluye "Sin respuesta de Hacienda en 72 h": la consulta horaria lo pasa a error antes del correo diario
            out.append(f"Mensaje receptor con error ante Hacienda ({d.issuer_name if d else ''}): {r.last_error or r.hacienda_detail or ''}"[:300])
        elif r.status in ("enviado", "procesando") and _aware(r.sent_at) and now - _aware(r.sent_at) > timedelta(hours=72):
            out.append(f"Mensaje receptor sin respuesta de Hacienda en 72 h ({d.issuer_name if d else ''})")
    for e in db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == t.id, EInvoiceDocument.mode.in_(("pruebas", "produccion")))):
        if e.status == "rechazada" and recent(e.resolved_at):
            out.append(f"Hacienda rechazó el comprobante {e.doc_type} {e.consecutive}: {e.hacienda_message or ''}"[:300])
        elif e.status == "error" and recent(e.resolved_at):
            out.append(f"Comprobante {e.doc_type} {e.consecutive} con error ante Hacienda: {e.last_error or e.hacienda_message or ''}"[:300])
        elif e.status in ("enviado", "procesando") and _aware(e.sent_at) and now - _aware(e.sent_at) > timedelta(hours=72):
            out.append(f"Comprobante {e.doc_type} {e.consecutive} sin respuesta de Hacienda en 72 h")
        if e.receiver_message in ("2", "3"):
            nc = db.scalar(
                select(EInvoiceDocument.id).where(
                    EInvoiceDocument.invoice_id == e.invoice_id, EInvoiceDocument.doc_type == "NC", EInvoiceDocument.status.in_(("por_enviar", "enviando", "enviado", "procesando", "aceptada"))
                )
            )
            if not nc:
                inv = db.get(Invoice, e.invoice_id)
                what = "aceptó parcialmente" if e.receiver_message == "2" else "rechazó"
                out.append(f"El cliente {what} la factura {inv.number if inv else e.consecutive} y no hay nota de crédito emitida")
    for env in ("pruebas", "produccion"):
        c = db.scalar(select(FiscalCredential).where(FiscalCredential.tenant_id == t.id, FiscalCredential.environment == env))
        days = hc.cert_days_left(c)
        if days is not None and (days in (60, 30) or days <= 7):
            out.append(f"El certificado de {env} vence en {days} días: cargue el nuevo .p12")
    if hc.production_on(db, t):
        ok, why = hc.production_state(db, t)
        if not ok:
            out.append(f"Producción activada pero el servidor no la permite: {why}")
    m = today.strftime("%Y-%m")
    from .reports import tax_summary

    try:
        pend = tax_summary(db, t, m)["credito_por_confirmar"]
        if pend:
            out.append(f"{len(pend)} comprobante(s) del mes con crédito por confirmar: pida al proveedor la respuesta de Hacienda")
    except Exception:  # noqa: BLE001
        pass
    return out


def daily_alerts(db: Session) -> int:
    """Correo diario a admin y contabilidad. Sin XML ni secretos."""
    from .email_layout import cta
    from .mail import queue_email

    n = 0
    for t in db.scalars(select(Tenant).where(Tenant.active)).all():
        items = alerts_for(db, t)
        if not items:
            continue
        # los textos traen datos de XML que manda cualquiera (nombre del emisor, detalles): siempre escapados
        lis = "".join(f"<li>{escape(x)}</li>" for x in items)
        html = f"<ul>{lis}</ul><p>El detalle está en Contabilidad → Tributario.</p>" + cta("/contabilidad/tributario", "Abrir Tributario")
        seen = set()
        for mbr in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.active, TenantUser.role_code.in_(("admin", "contabilidad")))):
            email = (mbr.user.email or "").lower()
            if email and email not in seen and mbr.user.active:
                seen.add(email)
                queue_email(db, t, email, f"Hacienda: {len(items)} pendiente(s) · {t.name}", html, "hacienda", 0)
                n += 1
        db.commit()
    return n
