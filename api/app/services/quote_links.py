"""Aceptacion en linea de cotizaciones (ventas etapa 3).

- Un enlace publico por cotizacion (QuoteLink): token aleatorio de 32 bytes, sin sesion. Vence al terminar el dia de
  validez de la cotizacion (hora de Costa Rica); sin fecha de validez, a los N dias (Ajustes -> Ventas ->
  enlace_cotizacion_dias). Lo crea el envio (correo o "Marcar enviada") y el boton "Copiar enlace".
- La pagina publica muestra la ULTIMA VERSION ENVIADA (lo que el cliente recibio), con lineas y totales de venta: nunca
  costo, margen, notas internas ni otras cotizaciones.
- Aceptar pide nombre y cedula; guarda IP y hora (QuoteAcceptance canal "enlace"). Solo se acepta una cotizacion
  enviada (por lo tanto ya aprobada: una retenida no se envia), sin respuesta del cliente, vigente y sin convertir.
- Aceptar NO gana la oportunidad ("ganada solo con factura"): marca "Aceptada por el cliente", corre lo mismo que la
  aceptacion que registra el vendedor (comisiones por aceptacion, avisos) y le crea al vendedor la tarea de facturar.
- Primera y ultima apertura; aviso al vendedor al abrirla (una vez por dia) y al aceptarla.
"""

from __future__ import annotations

import html as htmllib
import secrets
from datetime import UTC, date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import AuditLog, Customer, Quote, QuoteAcceptance, Tenant
from ..models.ventas3 import QuoteLink, SalesTask
from .documents import CR, audit, local_date

LINE_FIELDS = ("name", "description", "code", "unit", "quantity", "unit_price", "discount_type", "discount_value", "tax_rate", "subtotal", "tax_amount", "total", "treatment")
HEADER_FIELDS = ("number", "currency", "issue_date", "due_date", "discount_type", "discount_value", "subtotal", "discount_total", "tax_total", "total", "external_notes")


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def enabled(tenant) -> bool:
    from .sale_assignment import ventas_settings

    return bool(ventas_settings(tenant).get("aceptacion_en_linea", True))


def expiry_for(tenant, q: Quote) -> datetime:
    """Fin del dia de validez en Costa Rica; sin validez, N dias desde hoy."""
    from .sale_assignment import ventas_settings

    if q.due_date:
        due = q.due_date if isinstance(q.due_date, date) else date.fromisoformat(str(q.due_date))
        return datetime.combine(due, time(23, 59, 59), tzinfo=CR).astimezone(UTC)
    dias = int(ventas_settings(tenant).get("enlace_cotizacion_dias") or 30)
    return datetime.now(UTC) + timedelta(days=dias)


def url_of(link: QuoteLink) -> str:
    return f"{settings.public_base_url.rstrip('/')}/cotizacion/{link.token}"


def get_or_create(db: Session, tenant, q: Quote) -> QuoteLink | None:
    """El enlace vivo de la cotizacion (lo crea si no hay) con el vencimiento al dia de su validez. None si la empresa
    apago la aceptacion en linea."""
    if not enabled(tenant):
        return None
    link = db.scalar(select(QuoteLink).where(QuoteLink.quote_id == q.id, QuoteLink.revoked_at.is_(None)).order_by(QuoteLink.id.desc()).limit(1))
    exp = expiry_for(tenant, q)
    if link is None:
        link = QuoteLink(tenant_id=q.tenant_id, quote_id=q.id, token=secrets.token_urlsafe(32), expires_at=exp)
        db.add(link)
        db.flush()
    elif q.due_date or _aware(link.expires_at) < datetime.now(UTC):
        link.expires_at = exp  # sigue a la validez de la cotizacion (o se renueva al volver a enviarla)
    return link


def link_info(db: Session, q: Quote) -> dict | None:
    link = db.scalar(select(QuoteLink).where(QuoteLink.quote_id == q.id, QuoteLink.revoked_at.is_(None)).order_by(QuoteLink.id.desc()).limit(1))
    if link is None:
        return None
    return {
        "url": url_of(link),
        "expires_at": link.expires_at,
        "first_opened_at": link.first_opened_at,
        "last_opened_at": link.last_opened_at,
        "open_count": link.open_count or 0,
    }


# ---------- lado publico ----------
def resolve(db: Session, token: str) -> tuple[Tenant, Quote, QuoteLink]:
    if not token or len(token) > 80:
        raise HTTPException(404, "Enlace inválido")
    link = db.scalar(select(QuoteLink).where(QuoteLink.token == token))
    if link is None or link.revoked_at is not None:
        raise HTTPException(404, "Enlace inválido")
    q = db.get(Quote, link.quote_id)
    t = db.get(Tenant, link.tenant_id)
    if q is None or t is None or q.tenant_id != t.id or not t.active:
        raise HTTPException(404, "Enlace inválido")
    exp = expiry_for(t, q) if q.due_date else _aware(link.expires_at)
    if datetime.now(UTC) > exp:
        raise HTTPException(410, "Este enlace venció junto con la validez de la cotización. Pídale una nueva a su vendedor.")
    return t, q, link


def _version(db: Session, q: Quote):
    from . import quote_versions as qv

    return qv.latest(db, q)


def can_accept(q: Quote, version) -> tuple[bool, str | None]:
    if version is None:
        return False, "La cotización todavía no se ha enviado."
    if q.status in ("anulada", "convertida"):
        return False, "Esta cotización ya no está vigente."
    if q.status == "por_aprobar":
        return False, "La cotización está en revisión. Su vendedor le avisará."
    if q.status != "enviada":
        return False, "Esta cotización todavía no se puede aceptar en línea."
    if q.acceptance_status == "aceptada":
        return False, "Usted ya aceptó esta cotización."
    if q.acceptance_status == "rechazada":
        return False, "Esta cotización fue rechazada. Pídale una nueva a su vendedor."
    return True, None


def public_out(db: Session, t: Tenant, q: Quote) -> dict:
    """Solo lo que el cliente ya recibio: la ultima version enviada, sin notas internas ni costos."""
    v = _version(db, q)
    snap = (v.snapshot if v else None) or {}
    ok, why = can_accept(q, v)
    acc = None
    if q.acceptance_status == "aceptada":
        a = db.scalar(select(QuoteAcceptance).where(QuoteAcceptance.quote_id == q.id, QuoteAcceptance.status == "aceptada").order_by(QuoteAcceptance.id.desc()).limit(1))
        acc = {"name": a.contact_name, "at": a.recorded_at} if a else {"name": None, "at": None}
    cust = snap.get("customer") or {}
    from .email_layout import logo_url

    return {
        "company": {"name": t.name, "logo_url": logo_url(t)},
        "customer": {"name": cust.get("name")},
        "version": v.version if v else None,
        **{k: snap.get(k) for k in HEADER_FIELDS},
        "lines": [{k: ln.get(k) for k in LINE_FIELDS} for ln in (snap.get("lines") or [])],
        "footer": snap.get("footer"),
        "can_accept": ok,
        "why_not": why,
        "accepted": acc,
    }


def public_html(db: Session, t: Tenant, q: Quote) -> str:
    from .render import render_snapshot_html

    v = _version(db, q)
    if v is None:
        raise HTTPException(404, "La cotización todavía no se ha enviado")
    snap = {k: val for k, val in (v.snapshot or {}).items() if k != "internal_notes"}
    return render_snapshot_html(snap, t, v.version)


def _seller(q: Quote) -> int | None:
    from .avisos_venta import quote_owner

    return quote_owner(q)


def register_open(db: Session, t: Tenant, q: Quote, link: QuoteLink, ip: str | None) -> None:
    """Primera y ultima apertura; aviso al vendedor una vez por dia."""
    now = datetime.now(UTC)
    primera = link.first_opened_at is None
    link.first_opened_at = link.first_opened_at or now
    link.last_opened_at = now
    link.open_count = (link.open_count or 0) + 1
    hoy = local_date(now)
    ya = db.scalars(
        select(AuditLog.at).where(AuditLog.tenant_id == t.id, AuditLog.entity == "quote", AuditLog.entity_id == q.id, AuditLog.action == "link_open").order_by(AuditLog.id.desc()).limit(1)
    ).first()
    audit(db, t.id, None, "link_open", "quote", q.id, {"aperturas": link.open_count}, ip=ip)
    if primera or ya is None or local_date(_aware(ya)) != hoy:
        _notify_seller(
            db, t, q,
            f"{_cliente(db, q)} abrió la cotización {q.number}",
            f"<b>{htmllib.escape(_cliente(db, q))}</b> abrió el enlace de la cotización <b>{htmllib.escape(q.number)}</b>"
            + (" por primera vez." if primera else " otra vez hoy.")
            + " Es un buen momento para darle seguimiento.",
        )


def _cliente(db: Session, q: Quote) -> str:
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    return c.name if c is not None and c.tenant_id == q.tenant_id else "El cliente"


def _notify_seller(db: Session, t: Tenant, q: Quote, asunto: str, texto: str) -> int:
    from . import avisos_venta as avisos

    destinos = avisos.miembros(db, t, user_ids=[_seller(q)])
    if not destinos:
        return 0
    cuerpo = avisos._html(texto, url=avisos.enlace(f"/cotizaciones/{q.id}"), texto=f"Abrir {q.number}")
    return avisos.avisar(db, t, destinos, f"{asunto} · {t.name}", lambda _m: cuerpo, "quote", q.id)


def accept(db: Session, t: Tenant, q: Quote, name: str, id_number: str, ip: str | None) -> None:
    from . import avisos_venta as avisos
    from . import commissions as comsvc
    from . import quote_versions as qv

    nombre = " ".join((name or "").split())[:160]
    ced = " ".join((id_number or "").split())[:30]
    if len(nombre) < 3:
        raise HTTPException(422, "Escriba su nombre completo")
    if len([c for c in ced if c.isalnum()]) < 9:
        raise HTTPException(422, "Escriba su número de cédula completo")
    v = _version(db, q)
    ok, why = can_accept(q, v)
    if not ok:
        raise HTTPException(409, why)
    if v.content_hash != qv.live_hash(db, q):
        qv.restore(db, q, v)  # se acepta lo que el cliente vio (igual que la aceptacion que registra el vendedor)
    hoy = local_date(datetime.now(UTC))
    db.add(
        QuoteAcceptance(
            tenant_id=t.id, quote_id=q.id, version_id=v.id, version=v.version, status="aceptada", contact_name=nombre,
            contact_id_number=ced, ip=(ip or "")[:64] or None, channel="enlace", decided_on=hoy, recorded_by=None,
        )
    )
    antes = q.acceptance_status
    q.acceptance_status = "aceptada"
    q.accepted_version = v.version
    comsvc.on_quote_acceptance(db, q, antes, "aceptada", hoy)
    audit(db, t.id, None, "acceptance", "quote", q.id, {"de": antes, "a": "aceptada", "version": v.version, "quien": nombre, "cedula": ced, "medio": "enlace"}, ip=ip)
    # la oportunidad NO se gana aqui: ganada solo con factura. La vendedora recibe la tarea de facturar.
    seller = _seller(q)
    if seller:
        db.add(
            SalesTask(
                tenant_id=t.id, kind="facturar", title=f"Facturar la cotización {q.number} (aceptada en línea)",
                notes=f"La aceptó {nombre} (cédula {ced}).", due_at=datetime.now(UTC), assigned_to=seller,
                customer_id=q.customer_id, quote_id=q.id, created_by=None,
            )
        )
    avisos.cotizacion_aceptada(db, t, q, version=v.version, quien=nombre, medio="enlace en línea", actor_id=None)
    db.flush()
