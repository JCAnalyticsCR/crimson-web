"""Versiones de cotizacion (lo que realmente se le envio al cliente) y su aceptacion.

Regla: cada ENVIO guarda una instantanea inmutable. Si lo que ve el cliente no cambio desde la ultima version,
reenviar no crea otra; si cambio, el envio crea v2, v3... y las anteriores quedan intactas.
La huella (content_hash) mira solo lo que ve el cliente: las notas internas no generan version nueva.
Las columnas de linea se toman de la tabla (no de una lista fija), asi un campo nuevo de linea entra solo.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, func, select
from sqlalchemy.orm import Session

from ..models import Customer, Quote, QuoteLine, QuoteVersion, Tenant, User
from .documents import audit

# cabecera que se guarda en la instantanea
HEADER = (
    "number",
    "customer_id",
    "currency",
    "fx_sell",
    "fx_buy",
    "issue_date",
    "due_date",
    "discount_type",
    "discount_value",
    "subtotal",
    "discount_total",
    "tax_total",
    "total",
    "external_notes",
    "external_order",
    "activity_code",
    "medical_exemption_card",
    "internal_notes",
)
NO_HUELLA = {"internal_notes"}  # no lo ve el cliente
NO_RESTAURAR = {"number", "internal_notes"}
LINE_SKIP = {"id", "quote_id"}


def _val(v):
    if isinstance(v, Decimal):
        return f"{v.normalize():f}"
    if isinstance(v, float):
        return f"{Decimal(str(v)).normalize():f}"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _line_cols() -> list[str]:
    return [c.key for c in QuoteLine.__table__.columns if c.key not in LINE_SKIP]


def snapshot_of(db: Session, q: Quote, tenant: Tenant | None = None) -> dict:
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    snap = {k: _val(getattr(q, k)) for k in HEADER}
    snap["lines"] = [{k: _val(getattr(ln, k)) for k in _line_cols()} for ln in sorted(q.lines, key=lambda x: x.position)]
    snap["customer"] = {"name": c.name, "id_type": c.id_type, "id_number": c.id_number, "email": c.email, "phone": c.phone} if c else None
    if tenant is not None:
        snap["footer"] = (tenant.settings or {}).get("quote_footer")
    return snap


def content_hash(snap: dict) -> str:
    visible = {k: v for k, v in snap.items() if k not in NO_HUELLA and k != "footer"}
    return hashlib.sha256(json.dumps(visible, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def live_hash(db: Session, q: Quote) -> str:
    return content_hash(snapshot_of(db, q))


def versions(db: Session, q: Quote) -> list[QuoteVersion]:
    return list(db.scalars(select(QuoteVersion).where(QuoteVersion.quote_id == q.id).order_by(QuoteVersion.version)))


def latest(db: Session, q: Quote) -> QuoteVersion | None:
    return db.scalar(select(QuoteVersion).where(QuoteVersion.quote_id == q.id).order_by(QuoteVersion.version.desc()).limit(1))


def get_version(db: Session, q: Quote, n: int) -> QuoteVersion | None:
    return db.scalar(select(QuoteVersion).where(QuoteVersion.quote_id == q.id, QuoteVersion.version == n))


def state(db: Session, q: Quote) -> dict:
    """Lo que el editor necesita: version actual y si hay cambios sin enviar."""
    last = latest(db, q)
    # aceptada: la referencia es la version aceptada (la viva quedo igual a esa), no la ultima enviada
    ref = get_version(db, q, q.accepted_version) if q.acceptance_status == "aceptada" and q.accepted_version else last
    return {
        "current_version": last.version if last else None,
        "has_unsent_changes": bool(ref) and ref.content_hash != live_hash(db, q),
    }


def record_send(
    db: Session, tenant: Tenant, user_id: int | None, q: Quote, channel: str, recipient: str | None, ip: str | None = None
) -> tuple[QuoteVersion, bool]:
    """Al enviar: misma huella que la ultima version -> la misma (reenvio). Distinta -> version nueva."""
    db.flush()
    snap = snapshot_of(db, q, tenant)
    h = content_hash(snap)
    last = latest(db, q)
    if last and last.content_hash == h:
        audit(db, tenant.id, user_id, "resend_version", "quote", q.id, {"version": last.version, "canal": channel, "a": recipient}, ip=ip)
        return last, False
    n = (db.scalar(select(func.max(QuoteVersion.version)).where(QuoteVersion.quote_id == q.id)) or 0) + 1
    v = QuoteVersion(
        tenant_id=tenant.id,
        quote_id=q.id,
        version=n,
        content_hash=h,
        snapshot=snap,
        currency=q.currency,
        total=q.total,
        channel=channel,
        recipient=(recipient or "")[:200] or None,
        sent_by=user_id,
    )
    db.add(v)
    if q.acceptance_status == "rechazada":
        # el cliente rechazo una version anterior; esta nueva vuelve a esperar respuesta
        q.acceptance_status, q.accepted_version = "pendiente", None
    db.flush()
    audit(db, tenant.id, user_id, "version", "quote", q.id, {"version": n, "canal": channel, "a": recipient, "total": str(q.total)}, ip=ip)
    return v, True


def _parse(col, v):
    if v is None:
        return None
    t = col.type
    if isinstance(t, Numeric):
        return Decimal(str(v))
    if isinstance(t, DateTime):
        return datetime.fromisoformat(v) if isinstance(v, str) else v
    if isinstance(t, Date):
        return date.fromisoformat(v) if isinstance(v, str) else v
    return v


def restore(db: Session, q: Quote, v: QuoteVersion) -> None:
    """Deja la cotizacion viva igual a la version aceptada (lo que se convierte a factura o proyecto es lo que el
    cliente acepto). Las notas internas y el numero no se tocan."""
    snap = v.snapshot or {}
    qcols = Quote.__table__.columns
    for k in HEADER:
        if k in NO_RESTAURAR or k not in snap:
            continue
        setattr(q, k, _parse(qcols[k], snap[k]))
    lcols = QuoteLine.__table__.columns
    q.lines.clear()
    db.flush()
    for row in snap.get("lines", []):
        q.lines.append(QuoteLine(**{k: _parse(lcols[k], val) for k, val in row.items() if k in lcols and k not in LINE_SKIP}))
    db.flush()


def version_out(db: Session, v: QuoteVersion, q: Quote) -> dict:
    u = db.get(User, v.sent_by) if v.sent_by else None
    return {
        "id": v.id,
        "version": v.version,
        "sent_at": v.sent_at,
        "sent_by": u.full_name if u else None,
        "channel": v.channel,
        "recipient": v.recipient,
        "currency": v.currency,
        "total": v.total,
        "lines": len((v.snapshot or {}).get("lines", [])),
        "accepted": q.acceptance_status == "aceptada" and q.accepted_version == v.version,
    }
