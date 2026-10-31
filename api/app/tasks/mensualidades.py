"""Mensualidades que se cobran solas (plan 1.4): vista previa al admin tres dias antes de cada cobro.

La generacion y el envio viven en routers/ops.py (run_due_recurrences -> deliver_recurrence, tarea
sales.run_recurrences de las 05:00 CR). El aviso de atraso al aliado lo da la secuencia de cobro (tasks/cobranza.py,
D+5) y la propuesta de suspension a D+30 aparece en Aprobaciones (services/approvals.py): el sistema nunca suspende.

Tarea del worker:
- mensualidades.vista_previa (07:20 CR): las recurrencias de factura que se generan en 3 dias, con su monto y si
  saldran solas o esperan "Aprobar y enviar".
"""

from __future__ import annotations

import html
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.texto import forma, plural
from ..models import Customer, Recurrence, Tenant, TenantUser
from ..services.invoice_states import cr_today
from ..services.mail import member_inbox
from ..services.render import money

PREVIEW_DAYS = 3


def template_total(db: Session, r: Recurrence) -> tuple[Decimal, str] | None:
    """Total que tendria la factura de la plantilla hoy (mismas reglas que al crearla). None si la plantilla no sirve."""
    return template_total_of(db, r.tenant_id, r.customer_id, r.template)


def template_total_of(db: Session, tenant_id: int, customer_id: int | None, template: dict | None) -> tuple[Decimal, str] | None:
    from ..schemas.sales import DocumentIn
    from ..services import documents as docsvc
    from ..services.totals import compute_document

    try:
        payload = DocumentIn(**{**(template or {}), "customer_id": customer_id})
        lines = [docsvc._fill_line_from_product(db, tenant_id, ln, payload.currency) for ln in payload.lines]
        calc = compute_document([docsvc.line_in(ln) for ln in lines], payload.discount_type, payload.discount_value)
    except Exception:  # noqa: BLE001  una plantilla rota no frena la vista previa de las demas
        return None
    return Decimal(str(calc.total)), payload.currency


def what_happens(r: Recurrence, c: Customer | None, previa=None, produccion: bool = False) -> str:
    """Lo mismo que van a hacer run_due_recurrences y deliver_recurrence ese dia (routers/ops.py)."""
    if previa is not None:
        return f"Detenida: la anterior ({previa.number}) todavía no le llegó al cliente; se genera cuando la envíe o la anule"
    if not r.auto_send:
        return "Se genera; el envío automático está apagado (envíela a mano)"
    if r.first_run_requires_approval:
        return "Espera su «Aprobar y enviar» en Aprobaciones"
    if produccion:
        return "Espera que la emita (Hacienda en producción)"
    if c is None or not (c.email or "").strip():
        return "No va a salir: el cliente no tiene correo"
    return "Sale sola con su PDF y enlace de pago"


def upcoming(db: Session, tenant_id: int, on: date) -> list[Recurrence]:
    return list(
        db.scalars(
            select(Recurrence)
            .where(Recurrence.tenant_id == tenant_id, Recurrence.kind == "factura", Recurrence.active, Recurrence.next_date == on)
            .order_by(Recurrence.name, Recurrence.id)
        )
    )


def preview_html(on: date, filas: list[tuple[str, str, str, str]]) -> str:
    """Cuerpo de la vista previa de recurrencias: (recurrencia, cliente, total, que pasa) por fila, en texto plano.
    queue_email lo pone en el diseno base (el asunto como titulo y el enlace como boton)."""
    from ..services.email_layout import cta

    e = html.escape
    return (
        f"<p>El <b>{on:%d/%m/%Y}</b> {forma(len(filas), 'se genera', 'se generan')} {plural(len(filas), 'factura recurrente', 'facturas recurrentes')}:</p>"
        "<table border=0 cellpadding=6><tr><th align=left>Recurrencia</th><th align=left>Cliente</th><th align=right>Total</th><th align=left>Qué pasa</th></tr>"
        + "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td align=right>{e(c)}</td><td>{e(d)}</td></tr>" for a, b, c, d in filas)
        + "</table><p>Si algo no corresponde, cámbielo en Facturación → Recurrencias antes de esa fecha.</p>"
        + cta("/recurrencias", "Abrir Recurrencias")
    )


def preview_upcoming(db: Session, today: date | None = None, days: int = PREVIEW_DAYS) -> int:
    """Correo a los administradores con lo que se cobra en `days` dias. Devuelve cuantos correos salieron."""
    from ..routers.ops import pending_send
    from ..services.documents_send import queue_once, queued_before
    from ..services.hacienda_config import hacienda_mode

    today = today or cr_today()
    on = today + timedelta(days=days)
    n = 0
    for tid in db.scalars(select(Tenant.id).where(Tenant.active)).all():
        t = db.get(Tenant, tid)
        recs = upcoming(db, t.id, on)
        if not recs:
            continue
        filas = []
        produccion = hacienda_mode(db, t) == "produccion"  # la mensualidad no se emite sola todavia (plan 2.1)
        for r in recs:
            c = db.get(Customer, r.customer_id) if r.customer_id else None
            c = c if c is not None and c.tenant_id == t.id else None
            tot = template_total(db, r)
            filas.append((r.name or "", c.name if c else "sin cliente", money(*tot) if tot else "—", what_happens(r, c, pending_send(db, r, settle=False), produccion)))
        body = preview_html(on, filas)
        asunto = f"En {plural(days, 'día', 'días')} ({on:%d/%m/%Y}):"
        vistos: set[str] = set()
        for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.active, TenantUser.role_code == "admin")):
            correo = member_inbox(m)
            if not correo or correo in vistos or queued_before(db, t.id, correo, asunto):
                continue  # doble disparo del beat: la vista previa de ese dia ya salio
            vistos.add(correo)
            queue_once(db, t, correo, f"{asunto} {plural(len(recs), 'cobro recurrente', 'cobros recurrentes')} · {t.name}", body, "recurrence", recs[0].id, dedupe_key=f"prevista:{t.id}:{on}:{correo}")
            n += 1
        db.commit()
    return n


@shared_task(name="mensualidades.vista_previa")
def preview_upcoming_task() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return preview_upcoming(db)
