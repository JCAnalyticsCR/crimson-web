"""Correo transaccional: cola en BD (outbox) + envio por Resend si hay API key; si no, queda 'simulado' (visible en Ajustes).

Reintentos (los hace mail.retry_outbox, app/tasks/correo.py): un envio que falla queda "error" con `attempts` y
`next_attempt_at`; la espera se duplica (15, 30, 60, 120, 240 min) y al intento MAX_ATTEMPTS pasa a "fallido" (ya no
se reintenta y se le avisa al dueño del documento). Antes se reintentaba cada 15 min para siempre: una direccion mala
eran 96 intentos al dia. Un correo que llevaba adjuntos (PDF, XML) nunca se reintenta: los adjuntos no se guardan y
saldria sin ellos. Ni ese ni una invitacion (SENSITIVE_ENTITIES) llevan hora de reintento: `will_retry` dice si el
worker lo va a intentar de nuevo (Ajustes -> Correo solo muestra "se reintenta solo" en ese caso).

`dedupe_key`: el mismo aviso automatico no sale dos veces (dos corridas de la tarea, dos beats durante un despliegue).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import EmailOutbox, Tenant

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models import TenantUser

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
# Avisos internos de la plataforma (fallas de tareas, correo de Salud, "no le llegaron al cliente", cola "Por emitir") y correos internos
# con costos o margenes (resumen del guardian de margen): nunca van en copia al BCC de la empresa (esa lista es la de
# "toda factura": el contador o una firma externa que no ve costos), pero si se reintentan.
NO_BCC_ENTITIES = frozenset({"salud", "outbox", "margin_summary", "fiscal_queue"})
_LINK_TOKEN = re.compile(r"(/(?:invitacion|restablecer)/)[A-Za-z0-9_\-]{16,}")


def _layout(tenant: Tenant, subject: str, html: str) -> str:
    from . import email_layout

    try:
        return email_layout.ensure(tenant, subject, html)
    except Exception:  # noqa: BLE001 - el marco nunca impide que salga el aviso
        return html


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
    *,
    company_bcc: bool = True,
) -> EmailOutbox:
    """files son los adjuntos reales (nombre, bytes); no se guardan en la BD, solo viajan con el envio.
    attachments es la descripcion que queda en la bitacora de salida (si hay files y no viene, se arma con los nombres:
    asi el reintento sabe que ese correo no puede salir sin sus adjuntos).
    dedupe_key: si ya hay un correo de esta empresa con esa llave, se devuelve ese y no sale otro.
    company_bcc=False: un aviso interno del equipo (avisos_venta) no va en copia al BCC de la empresa ("de toda
    factura": archivo o contador externo)."""
    key = dedupe(dedupe_key)
    if key:
        prev = _by_key(db, tenant.id, key)
        if prev is not None:
            return prev
    sensitive = entity in SENSITIVE_ENTITIES
    blocked = blocked_recipients(tenant)
    company = [b for b in (tenant.settings or {}).get("bcc") or [] if b.strip().lower() not in blocked]
    bcc = None if sensitive or not company_bcc or entity in NO_BCC_ENTITIES else (",".join(company) or None)
    desc = attachments or ([{"name": n} for n, _ in files] if files else [])
    # todo correo sale con el diseno base (email_layout); un fragmento suelto se envuelve con el asunto como titulo
    html = _layout(tenant, subject, html)
    m = EmailOutbox(tenant_id=tenant.id, to=to, bcc=bcc, subject=subject, html=html, attachments=desc, entity=entity, entity_id=entity_id, dedupe_key=key)
    if not sensitive and (to or "").strip().lower() in blocked:
        # Ajustes -> "Correos que no reciben nada del sistema" (p. ej. la cuenta de quien desarrolla la plataforma): queda
        # en la bitacora como omitido y no sale. Lo de su propia cuenta (contrasena, invitacion, seguridad) si le llega.
        m.status = "omitido"
        db.add(m)
        db.flush()
        return m
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


def blocked_recipients(tenant: Tenant) -> set[str]:
    """Correos que nunca reciben nada del sistema (Ajustes): ni avisos, ni copias, ni resumenes."""
    return {str(x).strip().lower() for x in (tenant.settings or {}).get("mail_blocklist") or [] if str(x).strip()}


def member_inbox(m) -> str:
    """Correo al que le llegan los AVISOS internos de una membresia (reunion 21/10, pedido 22): su correo de avisos si
    el admin le puso uno, si no el de entrada. Lo de su cuenta (contrasena, invitacion, seguridad) nunca pasa por aqui:
    va siempre al correo de entrada (accounts.notify_security)."""
    if m is None:
        return ""
    return ((getattr(m, "notify_email", None) or (m.user.email if m.user is not None else "")) or "").strip().lower()


def user_inbox(db: Session, tenant_id: int, user) -> str:
    """member_inbox para un usuario suelto (p. ej. el tecnico de una orden o el vendedor de una cotizacion)."""
    from ..models import TenantUser

    if user is None:
        return ""
    aviso = db.scalar(select(TenantUser.notify_email).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user.id))
    return ((aviso or user.email) or "").strip().lower()


MAX_INTERNAL = 5


def internal_inboxes(tenant: Tenant) -> list[str]:
    """Ajustes -> Notificaciones -> "Correo para avisos internos" (reunion 21/10, pedido 48; p. ej. el buzon de ventas de
    la empresa): recibe los avisos internos de pedidos y ventas ADEMAS de los usuarios. Nunca a clientes; y si esta en
    "Correos que no reciben nada del sistema", no recibe nada."""
    raw = (tenant.settings or {}).get("internal_notify_emails") or []
    blocked = blocked_recipients(tenant)
    out: list[str] = []
    for x in raw if isinstance(raw, list) else [raw]:
        c = str(x or "").strip().lower()
        if c and "@" in c and c not in blocked and c not in out:
            out.append(c)
    return out[:MAX_INTERNAL]


def copy_internal(
    db: Session, tenant: Tenant, subject: str, html: str, entity: str, entity_id: int | None, *, skip: set[str] | None = None, dedupe_key: str | None = None
) -> int:
    """Copia de un aviso interno de pedidos o ventas al buzon interno de la empresa (internal_inboxes). Sin BCC de la
    empresa (es interno). `skip`: correos que ya lo recibieron en esta tanda (un usuario con ese mismo correo)."""
    n = 0
    for correo in internal_inboxes(tenant):
        if skip and correo in skip:
            continue
        queue_email(db, tenant, correo, subject, html, entity, entity_id or 0, dedupe_key=f"{dedupe_key}|{correo}" if dedupe_key else None, company_bcc=False)
        n += 1
    return n


def notify_roles(
    db: Session,
    tenant: Tenant,
    roles: tuple[str, ...],
    subject: str,
    html: str,
    entity: str,
    entity_id: int,
    dedupe_key: str | None = None,
    *,
    internal_copy: bool = False,
    like: bool = True,
    company_bcc: bool = True,
    keep: Callable[[TenantUser], bool] | None = None,
) -> int:
    """Avisa a todos los usuarios activos con esos roles (a su correo de avisos, member_inbox). Lo que en las notas de
    Andres es 'Administracion recibe: nuevo levantamiento' o 'proyecto terminado, listo para facturacion'.
    Con dedupe_key, cada destinatario lleva la suya (llave|correo): un aviso repetido no le llega dos veces a nadie.
    internal_copy: tambien al "Correo para avisos internos" (avisos de pedidos y ventas).
    like: tambien a los roles que el admin creo a partir de esos (un "Vendedor senior" recibe lo de ventas). False
    cuando la lista viene de un ajuste que el admin arma a mano (p. ej. quien recibe los pedidos web).
    company_bcc=False: aviso interno que no va en copia a la lista de la empresa (Ajustes -> copia de toda factura).
    keep: filtro por membresia (p. ej. un pedido web de un vendedor no le llega a otro vendedor)."""
    from ..core.deps import roles_like
    from ..models import TenantUser

    if like:
        roles = roles_like(db, tenant, roles)
    n = 0
    vistos: set[str] = set()
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.active, TenantUser.role_code.in_(roles))):
        if keep is not None and not keep(m):
            continue
        correo = member_inbox(m)
        if not correo or correo in vistos:
            continue
        vistos.add(correo)
        queue_email(
            db, tenant, correo, subject, html, entity, entity_id, dedupe_key=f"{dedupe_key}|{correo}" if dedupe_key else None, company_bcc=company_bcc
        )
        n += 1
    if internal_copy:
        n += copy_internal(db, tenant, subject, html, entity, entity_id, skip=vistos, dedupe_key=dedupe_key)
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


def will_retry(m: EmailOutbox) -> bool:
    """El worker lo va a volver a intentar solo: "error" con hora de reintento, sin adjuntos y que no sea una invitacion
    ni un enlace de contraseña. Lo demas que fallo ya no sale de nuevo (se reenvia a mano)."""
    return m.status == "error" and m.next_attempt_at is not None and not m.attachments and m.entity not in SENSITIVE_ENTITIES


def deliver(m: EmailOutbox, files: list[tuple[str, bytes]] | None = None) -> None:
    """Un intento de envio. Si falla: "error" y un intento mas; la hora del siguiente (retry_wait) solo si el worker lo
    reintenta. Con adjuntos o si es una invitacion queda sin hora: mail.retry_outbox no reenvia lo primero (lo deja
    "fallido" y avisa al dueño en su siguiente pasada) ni toca lo segundo (el admin usa "Reenviar").
    Si sale, se borra el error de un intento anterior (Ajustes lo mostraba en rojo debajo de un correo confirmado)."""
    from . import email_layout

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
                # version en texto plano: el correo que trae solo HTML puntua peor en los filtros de spam (22/10)
                "text": email_layout.html_to_text(m.html),
                # las respuestas del cliente llegan al buzon real de Crimson, no a una direccion que nadie lee
                **({"reply_to": [os.getenv("MAIL_REPLY_TO")]} if os.getenv("MAIL_REPLY_TO") else {}),
                # Antes el correo decia "va adjunto en PDF" y no se mandaba nada: el cliente no recibia el documento.
                **({"attachments": [{"filename": n, "content": base64.b64encode(b).decode()} for n, b in files]} if files else {}),
            },
            timeout=20,
        )
        r.raise_for_status()
        m.status, m.provider_id, m.sent_at, m.next_attempt_at, m.error = "enviado", r.json().get("id"), datetime.now(UTC), None, None
    except Exception as e:  # noqa: BLE001
        m.status, m.error = "error", str(e)[:500]
        no_retry = bool(files) or m.entity in SENSITIVE_ENTITIES
        m.next_attempt_at = None if no_retry else datetime.now(UTC) + retry_wait(m.attempts)


def doc_email_html(
    tenant: Tenant,
    kind: str,
    number: str,
    total: str,
    link: str | None,
    message: str | None,
    attached: bool = True,
    *,
    due: str | None = None,
    issued: str | None = None,
    view_link: str | None = None,
) -> str:
    """Cotizacion, factura o aviso de cobro al cliente (diseno base: email_layout). Con enlace de pago, el boton es
    "Ver y pagar en línea". Sin enlace de pago (una cotizacion, una factura sin saldo), view_link es el portal de
    clientes (Documentos) cuando el cliente tiene acceso: el boton es "Ver cotización" / "Ver factura". Sin ninguno de
    los dos el documento va solo en PDF y el correo sale sin boton (no hay enlace publico de un documento).
    El mensaje lo escribe quien envia (POST /{kind}/{id}/email, sales.enviar): va como texto, nunca como HTML (un
    enlace o un formulario inyectado saldria con la marca y el dominio de la empresa)."""
    from . import email_layout as el

    art = "el" if kind.startswith("aviso") else "la"
    titulo = f"{kind[:1].upper()}{kind[1:]} {number}"
    vence = "Válida hasta" if kind.startswith("cotiz") else "Vence"
    body = (
        el.p_html(f"Le compartimos {art} <b>{escape(kind)} {escape(number)}</b> por <b>{escape(total)}</b>.")
        + el.data_rows([("Número", number), ("Fecha", issued), (vence, due)], total=("Total", total))
        + (el.note(message) if message else "")
        + (el.p("El documento va adjunto en PDF.", small=True) if attached else "")
    )
    return el.render(
        tenant,
        title=titulo,
        body=body,
        preheader=f"{titulo} por {total}" + (f" · {vence.lower()} {due}" if due else "") + ".",
        action=(link, "Ver y pagar en línea") if link else ((view_link, f"Ver {kind}") if view_link else None),
        foot="Cualquier consulta, responda a este correo.",
    )
