"""Reintento del correo saliente (tarea mail.retry_outbox, cada 15 minutos).

- Solo toma "pendiente" o "error" cuyo `next_attempt_at` ya paso (o sin hora: los que llevaban adjuntos y las filas de
  antes de los reintentos), de la mas vieja a la mas nueva. Nunca invitaciones ni enlaces de contraseña
  (SENSITIVE_ENTITIES: su HTML guardado ya no tiene el enlace; el admin usa "Reenviar").
- En este orden:
  1. Si despues salio bien otro correo del mismo documento a la misma direccion (el vendedor lo reenvio), el viejo
     queda "reemplazado": sin reintento, sin aviso y sin contar como fallido en Salud.
  2. Lo que lleva mas de MAX_AGE en la cola queda "fallido" sin salir: un recordatorio de hace dias confunde mas de lo
     que ayuda. Si es un correo de despues de este cambio (con intentos: el worker estuvo caido mas de 24 h), se le
     avisa al dueño del documento; las filas de antes (0 intentos, a veces de hace meses) no generan aviso. Salud los
     cuenta por la hora en que se dejaron (updated_at).
  3. Un correo que llevaba adjuntos (cotizacion o factura con su PDF, XML) no se reenvia sin ellos: queda "fallido" y
     se le avisa al dueño del documento, que lo reenvia desde el documento.
  4. Lo demas se reintenta; cada fallo duplica la espera (mail.retry_wait) y al intento mail.MAX_ATTEMPTS queda
     "fallido", con el mismo aviso. Una direccion mala ya no son 96 intentos al dia.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import EmailOutbox, Invoice, Order, Quote, Tenant, TenantUser
from ..services import mail

log = logging.getLogger("crimson.mail")

MAX_AGE = timedelta(hours=24)
BATCH = 50
FAILED = "fallido"
REPLACED = "reemplazado"  # salio despues otro envio del mismo documento a la misma direccion
_DOCS = {"quote": ("Cotización", Quote), "invoice": ("Factura", Invoice)}


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _give_up(m: EmailOutbox, motivo: str, status: str = FAILED) -> None:
    prev = (m.error or "").strip()
    m.status, m.next_attempt_at = status, None
    m.error = (f"{motivo} Último error: {prev}" if prev else motivo)[:500]


def _superseded(db: Session, m: EmailOutbox) -> bool:
    """Despues de este salio bien otro correo del mismo documento a la misma direccion (el vendedor lo reenvio): ni se
    reintenta ni se avisa."""
    if not m.entity or not m.entity_id:
        return False
    later = db.scalar(
        select(EmailOutbox.id)
        .where(
            EmailOutbox.tenant_id == m.tenant_id,
            EmailOutbox.entity == m.entity,
            EmailOutbox.entity_id == m.entity_id,
            func.lower(EmailOutbox.to) == (m.to or "").strip().lower(),
            EmailOutbox.id > m.id,
            EmailOutbox.status == "enviado",
        )
        .limit(1)
    )
    return later is not None


def _internal_email(db: Session, tenant_id: int, user_id: int | None) -> str | None:
    """Correo de un miembro ACTIVO e interno (ni cliente ni aliado) de la empresa."""
    if not user_id:
        return None
    tu = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id, TenantUser.active))
    if tu is None or tu.customer_id is not None or tu.ally_company_id is not None:
        return None
    return (tu.user.email or "").strip().lower() or None


def _admins(db: Session, tenant_id: int) -> list[str]:
    out: list[str] = []
    for tu in db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.active, TenantUser.role_code == "admin")):
        e = (tu.user.email or "").strip().lower()
        if e and e not in out:
            out.append(e)
    return out


def owners(db: Session, m: EmailOutbox) -> tuple[str, list[str]]:
    """(como se llama lo que no salio, a quien avisar). Cotizacion o factura: su vendedor responsable (o quien la
    tecleo); si ya no esta activo, los administradores. Pedido de la tienda: los administradores. Los avisos internos
    no tienen dueño: no se avisa de un aviso (asi un correo malo no genera una cadena de avisos)."""
    if m.entity in _DOCS and m.entity_id:
        label, model = _DOCS[m.entity]
        d = db.get(model, m.entity_id)
        if d is None or d.tenant_id != m.tenant_id:
            return label, _admins(db, m.tenant_id)
        e = _internal_email(db, m.tenant_id, d.seller_id or d.created_by)
        return f"{label} {d.number}", [e] if e else _admins(db, m.tenant_id)
    if m.entity == "order" and m.entity_id:
        o = db.get(Order, m.entity_id)
        return (f"Pedido {o.number}" if o is not None and o.tenant_id == m.tenant_id else "Pedido de la tienda"), _admins(db, m.tenant_id)
    return "", []


_DOC_PATH = {"invoice": ("/facturas/{id}", "Abrir la factura"), "quote": ("/cotizaciones/{id}", "Abrir la cotización"), "order": ("/ordenes?pedido={id}", "Abrir el pedido")}


def _doc_cta(items: list[tuple[str, EmailOutbox]]) -> str:
    """Boton del aviso: el documento del primer correo que fallo (desde ahi se reenvia)."""
    from ..services.email_layout import cta

    m = items[0][1]
    ruta = _DOC_PATH.get(m.entity or "")
    return cta(ruta[0].format(id=m.entity_id), ruta[1]) if ruta and m.entity_id else ""


def _notify_owners(db: Session, failed: list[EmailOutbox]) -> int:
    """Un solo correo por dueño con todo lo que quedo fallido en esta pasada."""
    groups: dict[tuple[int, str], list[tuple[str, EmailOutbox]]] = {}
    for m in failed:
        label, to = owners(db, m)
        for addr in to:
            if addr == (m.to or "").strip().lower():
                continue  # el correo que fallo era para el mismo dueño: el aviso tampoco le llegaria
            groups.setdefault((m.tenant_id, addr), []).append((label, m))
    n = 0
    for (tid, addr), items in groups.items():
        t = db.get(Tenant, tid)
        if t is None:
            continue
        filas = "".join(
            f"<li><b>{html.escape(label or m.subject or '')}</b> · para {html.escape(m.to or '')} · {html.escape((m.error or '')[:160])}</li>"
            for label, m in items
        )
        ids = ",".join(str(m.id) for _, m in items)
        mail.queue_email(
            db,
            t,
            addr,
            f"{len(items)} correo(s) no le llegaron al cliente · {t.name}",
            "<p>Estos correos no salieron y ya no se reintentan:</p>"
            f"<ul>{filas}</ul>"
            "<p>Revise el correo del cliente y vuelva a enviarlo desde el documento (Enviar por correo).</p>"
            + _doc_cta(items),
            "outbox",
            items[0][1].id,
            dedupe_key=f"outbox:fallido:{ids}:{addr}",
        )
        n += 1
    return n


def retry_outbox(db: Session, now: datetime | None = None, limit: int = BATCH) -> dict:
    """Una pasada del reintento. Hace commit. Devuelve los conteos (quedan en la corrida de la tarea)."""
    now = now or datetime.now(UTC)
    q = (
        select(EmailOutbox)
        .where(
            EmailOutbox.status.in_(("pendiente", "error")),
            or_(EmailOutbox.next_attempt_at.is_(None), EmailOutbox.next_attempt_at <= now),
            or_(EmailOutbox.entity.is_(None), EmailOutbox.entity.not_in(list(mail.SENSITIVE_ENTITIES))),
        )
        .order_by(EmailOutbox.created_at, EmailOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)  # dos workers nunca toman el mismo correo
    )
    out = {"reintentos": 0, "enviados": 0, "fallidos": 0, "vencidos": 0, "reemplazados": 0, "avisos": 0}
    avisar: list[EmailOutbox] = []  # lo que ya no sale y su dueño debe saberlo
    for m in db.scalars(q).all():
        if _superseded(db, m):
            _give_up(m, "Ya salió un envío posterior del mismo documento a esta dirección.", REPLACED)
            out["reemplazados"] += 1
        elif now - (_aware(m.created_at) or now) > MAX_AGE:  # antes que los adjuntos: lo viejo nunca genera aviso
            _give_up(m, f"Se dejó de reintentar: llevaba más de {int(MAX_AGE.total_seconds() // 3600)} h sin salir.")
            out["vencidos"] += 1
            if m.attempts:  # de despues de este cambio: el worker estuvo caido; las filas de antes tienen 0 intentos
                avisar.append(m)
        elif m.attachments:
            _give_up(m, "Llevaba adjuntos, que no se guardan: no se reenvía sin ellos.")
            out["fallidos"] += 1
            avisar.append(m)
        else:
            mail.deliver(m)
            out["reintentos"] += 1
            if m.status == "enviado":
                out["enviados"] += 1
            elif m.status == "error" and (m.attempts or 0) >= mail.MAX_ATTEMPTS:
                _give_up(m, f"Falló {mail.MAX_ATTEMPTS} veces: ya no se reintenta.")
                out["fallidos"] += 1
                avisar.append(m)
    db.commit()  # lo enviado y lo fallido queda marcado antes de mandar los avisos
    if avisar:
        out["avisos"] = _notify_owners(db, avisar)
        db.commit()
    if out["fallidos"] or out["vencidos"]:
        log.warning("Correo: %s fallido(s) y %s vencido(s) sin salir", out["fallidos"], out["vencidos"])
    return out
