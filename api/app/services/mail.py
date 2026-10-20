"""Correo transaccional: cola en BD (outbox) + envio por Resend si hay API key; si no, queda 'simulado' (visible en Ajustes).

Reintentos (los hace mail.retry_outbox, app/tasks/correo.py): un envio que falla queda "error" con `attempts` y
`next_attempt_at`; la espera se duplica (15, 30, 60, 120, 240 min) y al intento MAX_ATTEMPTS pasa a "fallido" (ya no
se reintenta y se le avisa al dueño del documento). Antes se reintentaba cada 15 min para siempre: una direccion mala
eran 96 intentos al dia. Un correo que llevaba adjuntos (PDF, XML) nunca se reintenta: los adjuntos no se guardan y
saldria sin ellos.

`dedupe_key`: el mismo aviso automatico no sale dos veces (dos corridas de la tarea, dos beats durante un despliegue).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import EmailOutbox, Tenant

RETRY_BASE = timedelta(minutes=15)  # espera tras el 1er fallo; se duplica con cada intento
MAX_ATTEMPTS = 6  # al 6.o envio fallido el correo queda "fallido" (~8 h de intentos)
DEDUPE_MAX = 200  # largo de la columna email_outbox.dedupe_key


def retry_wait(attempts: int) -> timedelta:
    """Espera antes del siguiente intento, despues de `attempts` envios fallidos: 15 min x 2^(n-1)."""
    return RETRY_BASE * 2 ** max(attempts - 1, 0)


def dedupe(key: str | None) -> str | None:
    """Llave normalizada: las largas (p. ej. con un correo adentro) se acortan con un hash, sin perder unicidad."""
    key = (key or "").strip()
    if not key:
        return None
    if len(key) <= DEDUPE_MAX:
        return key
    return f"{key[:DEDUPE_MAX - 41]}#{hashlib.sha256(key.encode()).hexdigest()[:40]}"


def _by_key(db: Session, tenant_id: int, key: str) -> EmailOutbox | None:
    return db.scalar(select(EmailOutbox).where(EmailOutbox.tenant_id == tenant_id, EmailOutbox.dedupe_key == key))

# Correos que llevan una llave de entrada (invitaciones, enlaces de contrasena) o avisan de un cambio de credenciales.
# Nunca van en copia al BCC de la empresa (seria robo de cuenta: el enlace le llegaria tambien a contabilidad), el
# enlace se borra del HTML guardado despues de enviarlo (el respaldo exporta email_outbox) y el worker no los
# reintenta (su HTML ya no sirve): el admin usa "Reenviar".
SENSITIVE_ENTITIES = frozenset({"invitation", "password_reset", "user_security"})
_LINK_TOKEN = re.compile(r"(/(?:invitacion|restablecer)/)[A-Za-z0-9_\-]{16,}")


def redact_links(html: str) -> str:
    return _LINK_TOKEN.sub(r"\1[oculto]", html or "")


def mail_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


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
    dedupe_key: str | None = None,
) -> EmailOutbox:
    """files son los adjuntos reales (nombre, bytes); no se guardan en la BD, solo viajan con el envio.
    attachments es la descripcion que queda en la bitacora de salida (si hay files y no viene, se arma con los nombres:
    asi el reintento sabe que ese correo no puede salir sin sus adjuntos).
    dedupe_key: si ya hay un correo de esta empresa con esa llave, se devuelve ese y no sale otro."""
    key = dedupe(dedupe_key)
    if key:
        prev = _by_key(db, tenant.id, key)
        if prev is not None:
            return prev
    sensitive = entity in SENSITIVE_ENTITIES
    bcc = None if sensitive else (",".join((tenant.settings or {}).get("bcc") or []) or None)
    desc = attachments or ([{"name": n} for n, _ in files] if files else [])
    m = EmailOutbox(tenant_id=tenant.id, to=to, bcc=bcc, subject=subject, html=html, attachments=desc, entity=entity, entity_id=entity_id, dedupe_key=key)
    if key:
        # carrera con otro proceso que mando el mismo aviso: la restriccion unica decide y se devuelve el que gano
        sp = db.begin_nested()
        try:
            db.add(m)
            db.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            prev = _by_key(db, tenant.id, key)
            if prev is not None:
                return prev
            raise
    else:
        db.add(m)
        db.flush()
    deliver(m, files)
    if sensitive:
        m.html = redact_links(m.html)  # el correo real ya salio con el enlace; lo guardado no lo tiene
    return m


def notify_roles(
    db: Session, tenant: Tenant, roles: tuple[str, ...], subject: str, html: str, entity: str, entity_id: int, dedupe_key: str | None = None
) -> int:
    """Avisa a todos los usuarios activos con esos roles. Lo que en las notas de Andres es
    'Administracion recibe: nuevo levantamiento' o 'proyecto terminado, listo para facturacion'.
    Con dedupe_key, cada destinatario lleva la suya (llave|correo): un aviso repetido no le llega dos veces a nadie."""
    from ..models import TenantUser

    n = 0
    vistos: set[str] = set()
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.active, TenantUser.role_code.in_(roles))):
        correo = (m.user.email or "").strip().lower()
        if not correo or correo in vistos:
            continue
        vistos.add(correo)
        queue_email(db, tenant, correo, subject, html, entity, entity_id, dedupe_key=f"{dedupe_key}|{correo}" if dedupe_key else None)
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
    """Un intento de envio. Si falla: "error", un intento mas y la hora del siguiente (retry_wait). Si llevaba
    adjuntos, el siguiente paso de mail.retry_outbox no lo reenvia: lo deja "fallido" y avisa al dueño."""
    key, sender = os.getenv("RESEND_API_KEY"), _sender()
    if not key:
        m.status, m.sent_at = "simulado", datetime.now(UTC)
        return
    m.attempts = (m.attempts or 0) + 1
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
        m.status, m.provider_id, m.sent_at, m.next_attempt_at = "enviado", r.json().get("id"), datetime.now(UTC), None
    except Exception as e:  # noqa: BLE001
        m.status, m.error = "error", str(e)[:500]
        # con adjuntos no hay espera: el siguiente paso del worker lo da por fallido (no lo reenvia sin ellos)
        m.next_attempt_at = datetime.now(UTC) + (timedelta(0) if files else retry_wait(m.attempts))


def doc_email_html(tenant: Tenant, kind: str, number: str, total: str, link: str | None, message: str | None, attached: bool = True) -> str:
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15131a">
<div style="border-bottom:3px solid #e2233a;padding:12px 0;font-size:18px;font-weight:700">{tenant.name}</div>
<p>Le compartimos la <b>{kind} {number}</b> por <b>{total}</b>.</p>
{f'<p><a href="{link}" style="display:inline-block;background:#e2233a;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">Ver y pagar en línea</a></p>' if link else ""}
{f'<p style="color:#5a5560;white-space:pre-wrap">{message}</p>' if message else ""}
<p style="color:#8a858f;font-size:12px">{"El documento va adjunto en PDF. " if attached else ""}Cualquier consulta, responda a este correo.</p></div>"""
