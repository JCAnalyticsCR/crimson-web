"""Tareas del worker de la cola fiscal (plan maestro 1.7).

worker/celery_app.py solo importa este modulo y agenda "fiscal.por_emitir_diario" a las 08:30 (hora de Costa Rica):
un correo a contabilidad (la duena de la emision en la RACI; sin nadie de contabilidad, el administrador) con las
facturas "Por emitir" y un enlace a cada una. Sin cola no sale nada. Un correo por persona y por dia de Costa Rica:
si el beat dispara dos veces (despliegue, reinicio), el segundo no repite.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Customer, EmailOutbox, Tenant, TenantUser
from ..providers.einvoice import CR, cr_now
from ..services import fiscal_queue as fq
from ..services import hacienda_config as hc
from ..services.mail import queue_email
from ..services.render import money

log = logging.getLogger("crimson.fiscal")
ENTITY = "fiscal_queue"  # entity_id = dia de Costa Rica como AAAAMMDD: la llave de "ya se mando hoy"
MAX_ROWS = 40
STATUS = {"enviada": "enviada", "pagada": "pagada", "parcial": "pago parcial", "vencida": "vencida"}
INSTRUCTION = {
    "ninguno": "La conexión con Hacienda todavía no está en producción: emití cada una en Fygaro hoy mismo y marcala "
    "«Emitida en Fygaro» con su número. Lo marcado no se vuelve a emitir.",
    "simulado": "El modo fiscal es Simulado (sin validez fiscal): emití cada una en Fygaro y marcala «Emitida en Fygaro» "
    "con su número.",
    "pruebas": "El modo fiscal es Pruebas (sin validez fiscal): emití cada una en Fygaro y marcala «Emitida en Fygaro» "
    "con su número.",
    "produccion": "Emití cada una desde la factura con «Emitir a Hacienda».",
}


def recipients(db: Session, t: Tenant) -> list[str]:
    for roles in (("contabilidad",), ("admin",)):
        out: list[str] = []
        for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.active, TenantUser.role_code.in_(roles))):
            email = (m.user.email or "").strip().lower()
            if email and email not in out and m.user.active:
                out.append(email)
        if out:
            return out
    return []


def digest_html(db: Session, t: Tenant, rows: list, total: int, today, fuera_mes: int, mode: str) -> str:
    base = settings.public_base_url.rstrip("/")
    filas = []
    for inv in rows:
        c = db.get(Customer, inv.customer_id) if inv.customer_id else None
        dias = max((today - inv.issue_date).days, 0) if inv.issue_date else 0
        filas.append(
            f'<tr><td><a href="{base}/facturas/{inv.id}">{html.escape(inv.number)}</a></td>'
            f"<td>{html.escape(c.name) if c else 'Sin cliente'}</td>"
            f'<td align="right">{money(inv.total, inv.currency)}</td>'
            f"<td>{STATUS.get(inv.status, html.escape(inv.status))}</td>"
            f"<td>{inv.issue_date.isoformat() if inv.issue_date else ''}{f' · {dias} día(s)' if dias else ' · hoy'}</td></tr>"
        )
    mas = f"<p>… y {total - len(rows)} más en la lista.</p>" if total > len(rows) else ""
    return (
        f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;color:#15131a">'
        f"<h3>Facturas por emitir · {today.isoformat()}</h3>"
        f"<p>{INSTRUCTION.get(mode, INSTRUCTION['ninguno'])}</p>"
        '<table cellpadding="6" style="border-collapse:collapse;font-size:13px"><tr><th align="left">Factura</th>'
        '<th align="left">Cliente</th><th align="right">Total</th><th align="left">Estado</th><th align="left">Desde</th></tr>'
        f"{''.join(filas)}</table>{mas}"
        f'<p><a href="{base}/facturas?estado=por_emitir">Abrir la lista «Por emitir» ({total})</a></p>'
        f'<p style="color:#5a5560;font-size:12px">Emitidas fuera de la plataforma este mes: {fuera_mes}.</p></div>'
    )


def send_queue_digest(db: Session, now: datetime | None = None) -> int:
    """Devuelve cuantos correos encolo. `now` (con zona) solo para las pruebas: la fecha es la de Costa Rica."""
    today = (now.astimezone(CR) if now else cr_now()).date()
    key = int(today.strftime("%Y%m%d"))
    n = 0
    for t in db.scalars(select(Tenant).where(Tenant.active)).all():
        total = fq.queue_count(db, t.id)
        if not total:
            continue
        rows = fq.queue_rows(db, t.id, limit=MAX_ROWS)
        body = digest_html(db, t, rows, total, today, fq.external_month_count(db, t.id, today), hc.hacienda_mode(db, t))
        for email in recipients(db, t):
            ya = db.scalar(
                select(EmailOutbox.id).where(EmailOutbox.tenant_id == t.id, EmailOutbox.entity == ENTITY, EmailOutbox.entity_id == key, EmailOutbox.to == email)
            )
            if ya:
                continue
            queue_email(db, t, email, f"Por emitir: {total} factura(s) · {t.name}", body, ENTITY, key)
            n += 1
        db.commit()
    return n


@shared_task(name="fiscal.por_emitir_diario")
def por_emitir_diario() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        n = send_queue_digest(db)
    log.info("Por emitir: %s correo(s)", n)
    return n
