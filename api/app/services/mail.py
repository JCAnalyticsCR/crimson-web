"""Correo transaccional: cola en BD (outbox) + envio por Resend si hay API key; si no, queda 'simulado' (visible en Ajustes)."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EmailOutbox, Tenant


def queue_email(
    db: Session,
    tenant: Tenant,
    to: str,
    subject: str,
    html: str,
    entity: str,
    entity_id: int,
    attachments: list | None = None,
    files: list[tuple[str, bytes]] | None = None,
) -> EmailOutbox:
    """files son los adjuntos reales (nombre, bytes); no se guardan en la BD, solo viajan con el envio.
    attachments es la descripcion que queda en la bitacora de salida."""
    bcc = ",".join((tenant.settings or {}).get("bcc") or []) or None
    m = EmailOutbox(tenant_id=tenant.id, to=to, bcc=bcc, subject=subject, html=html, attachments=attachments or [], entity=entity, entity_id=entity_id)
    db.add(m)
    db.flush()
    deliver(m, files)
    return m


def notify_roles(db: Session, tenant: Tenant, roles: tuple[str, ...], subject: str, html: str, entity: str, entity_id: int) -> int:
    """Avisa a todos los usuarios activos con esos roles. Lo que en las notas de Andres es
    'Administracion recibe: nuevo levantamiento' o 'proyecto terminado, listo para facturacion'."""
    from ..models import TenantUser

    n = 0
    vistos: set[str] = set()
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.active, TenantUser.role_code.in_(roles))):
        correo = (m.user.email or "").strip().lower()
        if not correo or correo in vistos:
            continue
        vistos.add(correo)
        queue_email(db, tenant, correo, subject, html, entity, entity_id)
        n += 1
    return n


DEFAULT_FROM = "Crimson Consulting <info@crimsoncr.com>"


def _sender() -> str:
    return os.getenv("MAIL_FROM") or DEFAULT_FROM


def mail_status() -> dict:
    """Diagnostico para Ajustes: por que un correo no llega. Sin llave todo queda 'simulado'; con llave pero el
    dominio sin verificar en Resend, el DMARC p=reject de crimsoncr.com hace que Gmail/Outlook lo rechacen."""
    key, sender = os.getenv("RESEND_API_KEY"), _sender()
    dominio = sender.rsplit("@", 1)[-1].strip("> ").lower()
    out = {"configured": bool(key), "sender": sender, "reply_to": os.getenv("MAIL_REPLY_TO"), "domain": dominio, "domain_status": None}
    if not key:
        return out
    try:
        r = httpx.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code == 200:
            d = next((x for x in r.json().get("data", []) if x.get("name", "").lower() == dominio), None)
            out["domain_status"] = d.get("status") if d else "no_agregado"
        else:  # llave restringida a envio: no puede listar dominios, no es un error
            out["domain_status"] = "desconocido"
    except Exception:  # noqa: BLE001
        out["domain_status"] = "desconocido"
    return out


def deliver(m: EmailOutbox, files: list[tuple[str, bytes]] | None = None) -> None:
    key, sender = os.getenv("RESEND_API_KEY"), _sender()
    if not key:
        m.status, m.sent_at = "simulado", datetime.now(UTC)
        return
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "from": sender,
                "to": [m.to],
                "bcc": [b for b in (m.bcc or "").split(",") if b],
                "subject": m.subject,
                "html": m.html,
                # las respuestas del cliente llegan al buzon real de Crimson, no a una direccion que nadie lee
                **({"reply_to": [os.getenv("MAIL_REPLY_TO")]} if os.getenv("MAIL_REPLY_TO") else {}),
                # Antes el correo decia "va adjunto en PDF" y no se mandaba nada: el cliente no recibia el documento.
                **({"attachments": [{"filename": n, "content": base64.b64encode(b).decode()} for n, b in files]} if files else {}),
            },
            timeout=20,
        )
        r.raise_for_status()
        m.status, m.provider_id, m.sent_at = "enviado", r.json().get("id"), datetime.now(UTC)
    except Exception as e:  # noqa: BLE001
        m.status, m.error = "error", str(e)[:500]


def doc_email_html(tenant: Tenant, kind: str, number: str, total: str, link: str | None, message: str | None, attached: bool = True) -> str:
    return f"""<div style="font-family:Manrope,Arial,sans-serif;max-width:560px;margin:auto;color:#15131a">
<div style="border-bottom:3px solid #e2233a;padding:12px 0;font-size:18px;font-weight:700">{tenant.name}</div>
<p>Le compartimos la <b>{kind} {number}</b> por <b>{total}</b>.</p>
{f'<p><a href="{link}" style="display:inline-block;background:#e2233a;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">Ver y pagar en línea</a></p>' if link else ""}
{f'<p style="color:#5a5560;white-space:pre-wrap">{message}</p>' if message else ""}
<p style="color:#8a858f;font-size:12px">{"El documento va adjunto en PDF. " if attached else ""}Cualquier consulta, responda a este correo.</p></div>"""
