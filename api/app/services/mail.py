"""Correo transaccional: cola en BD (outbox) + envio por Resend si hay API key; si no, queda 'simulado' (visible en Ajustes)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from ..models import EmailOutbox, Tenant


def queue_email(db: Session, tenant: Tenant, to: str, subject: str, html: str, entity: str, entity_id: int, attachments: list | None = None) -> EmailOutbox:
    bcc = ",".join((tenant.settings or {}).get("bcc") or []) or None
    m = EmailOutbox(tenant_id=tenant.id, to=to, bcc=bcc, subject=subject, html=html, attachments=attachments or [], entity=entity, entity_id=entity_id)
    db.add(m)
    db.flush()
    deliver(m)
    return m


def deliver(m: EmailOutbox) -> None:
    key, sender = os.getenv("RESEND_API_KEY"), os.getenv("MAIL_FROM", "Crimson <no-reply@crimsoncr.com>")
    if not key:
        m.status, m.sent_at = "simulado", datetime.now(UTC)
        return
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": sender, "to": [m.to], "bcc": [b for b in (m.bcc or "").split(",") if b], "subject": m.subject, "html": m.html},
            timeout=20,
        )
        r.raise_for_status()
        m.status, m.provider_id, m.sent_at = "enviado", r.json().get("id"), datetime.now(UTC)
    except Exception as e:  # noqa: BLE001
        m.status, m.error = "error", str(e)[:500]


def doc_email_html(tenant: Tenant, kind: str, number: str, total: str, link: str | None, message: str | None) -> str:
    return f"""<div style="font-family:Manrope,Arial,sans-serif;max-width:560px;margin:auto;color:#15131a">
<div style="border-bottom:3px solid #e2233a;padding:12px 0;font-size:18px;font-weight:700">{tenant.name}</div>
<p>Le compartimos la <b>{kind} {number}</b> por <b>{total}</b>.</p>
{f'<p><a href="{link}" style="display:inline-block;background:#e2233a;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">Ver y pagar en línea</a></p>' if link else ""}
{f'<p style="color:#5a5560;white-space:pre-wrap">{message}</p>' if message else ""}
<p style="color:#8a858f;font-size:12px">El documento va adjunto en PDF. Cualquier consulta, responda a este correo.</p></div>"""
