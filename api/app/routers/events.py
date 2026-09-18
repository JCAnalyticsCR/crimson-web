"""Eventos y entradas con QR: venta publica (orden + entradas), activacion al confirmar el pago, envio por correo
y control de acceso (check-in por codigo o camara). Cada entrada solo puede usarse una vez."""

from __future__ import annotations

import io
import re
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import public_limiter
from ..models import Event, Order, OrderLine, Tenant, Ticket, TicketType
from ..services.documents import audit
from ..services.mail import queue_email
from ..services.sequences import next_number
from ..services.totals import LineIn, compute_document, d

router = APIRouter(tags=["eventos"])
pub = APIRouter(prefix="/public", tags=["publico"])

CR = ZoneInfo("America/Costa_Rica")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I para dictarlo por telefono


def aware(dt: datetime) -> datetime:
    """SQLite devuelve fechas sin zona: se interpretan como UTC (asi se guardan)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def cr_fmt(dt: datetime) -> str:
    return aware(dt).astimezone(CR).strftime("%d/%m/%Y %H:%M")


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def ticket_url(code: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/entrada/{code}"


def qr_svg(text: str) -> str:
    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#15131a", xmldecl=False, svgns=True)
    return buf.getvalue().decode()


def _sold(db: Session, type_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Ticket).where(Ticket.ticket_type_id == type_id, Ticket.status != "anulado")) or 0


def _event_out(db: Session, e: Event, admin: bool = False) -> dict:
    types = []
    for t in e.ticket_types:
        if not admin and not t.active:
            continue
        sold = _sold(db, t.id)
        row = {
            "id": t.id,
            "name": t.name,
            "price": t.price,
            "quantity": t.quantity,
            "max_per_order": t.max_per_order,
            "active": t.active,
            "available": max(t.quantity - sold, 0),
        }
        if admin:
            row["sold"] = sold
        types.append(row)
    out = {
        "id": e.id,
        "slug": e.slug,
        "name": e.name,
        "description": e.description,
        "venue": e.venue,
        "starts_at": e.starts_at,
        "ends_at": e.ends_at,
        "image_url": e.image_url,
        "currency": e.currency,
        "tax_rate": e.tax_rate,
        "status": e.status,
        "ticket_types": types,
    }
    if admin:
        counts = dict(db.execute(select(Ticket.status, func.count()).where(Ticket.event_id == e.id).group_by(Ticket.status)).all())
        out["stats"] = {
            "valido": counts.get("valido", 0),
            "usado": counts.get("usado", 0),
            "pendiente": counts.get("pendiente", 0),
            "anulado": counts.get("anulado", 0),
        }
        out["public_url"] = f"{settings.public_base_url.rstrip('/')}/eventos/{_tenant_slug(db, e.tenant_id)}/{e.slug}"
    return out


def _tenant_slug(db: Session, tid: int) -> str:
    t = db.get(Tenant, tid)
    return t.slug if t else ""


# ---------- Admin ----------
class TicketTypeIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(ge=0)
    quantity: int = Field(ge=0, le=100000)
    max_per_order: int = Field(10, ge=1, le=100)
    active: bool = True


class EventIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(None, max_length=80)
    description: str | None = None
    venue: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    image_url: str | None = None
    currency: str = Field("CRC", pattern="^(CRC|USD)$")
    tax_rate: Decimal = Field(Decimal(13), ge=0, le=13)
    status: str = Field("borrador", pattern="^(borrador|publicado|cerrado)$")
    ticket_types: list[TicketTypeIn] = Field(default_factory=list)


def _slugify(s: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:70] or "evento"


def _own(db: Session, eid: int, tid: int) -> Event:
    e = db.get(Event, eid)
    if not e or e.tenant_id != tid:
        raise HTTPException(404, "Evento no encontrado")
    return e


def _apply(db: Session, e: Event, data: EventIn) -> None:
    with db.no_autoflush:  # no insertar el evento a medio llenar mientras se consultan slugs y ventas
        _apply_fields(db, e, data)


def _apply_fields(db: Session, e: Event, data: EventIn) -> None:
    slug = _slugify(data.slug or data.name)
    clash = db.scalar(select(Event).where(Event.tenant_id == e.tenant_id, Event.slug == slug, Event.id != (e.id or 0)))
    if clash:
        slug = f"{slug}-{secrets.token_hex(2)}"
    e.slug = slug
    for k in ("name", "description", "venue", "starts_at", "ends_at", "image_url", "currency", "tax_rate", "status"):
        setattr(e, k, getattr(data, k))
    current = {t.id: t for t in e.ticket_types}
    keep = set()
    for ti in data.ticket_types:
        t = current.get(ti.id) if ti.id else None
        if not t:
            t = TicketType()
            e.ticket_types.append(t)
        elif _sold(db, t.id) > ti.quantity:
            raise HTTPException(409, f"'{t.name}' ya vendio mas entradas que la nueva cantidad")
        t.name, t.price, t.quantity, t.max_per_order, t.active = ti.name, ti.price, ti.quantity, ti.max_per_order, ti.active
        if t.id:
            keep.add(t.id)
    for tid_, t in current.items():
        if tid_ not in keep:
            if _sold(db, tid_):
                t.active = False  # con ventas no se borra: se oculta
            else:
                e.ticket_types.remove(t)


@router.get("/events")
def events(p: Principal = Depends(require("events", "ver")), db: Session = Depends(get_db)):
    return [_event_out(db, e, admin=True) for e in db.scalars(select(Event).where(Event.tenant_id == p.tenant.id).order_by(Event.starts_at.desc()))]


@router.post("/events", status_code=201)
def event_create(data: EventIn, p: Principal = Depends(require("events", "crear")), db: Session = Depends(get_db)):
    e = Event(tenant_id=p.tenant.id)
    db.add(e)
    _apply(db, e, data)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "event", e.id, ip=p.ip)
    db.commit()
    return _event_out(db, e, admin=True)


@router.get("/events/{eid}")
def event_get(eid: int, p: Principal = Depends(require("events", "ver")), db: Session = Depends(get_db)):
    return _event_out(db, _own(db, eid, p.tenant.id), admin=True)


@router.put("/events/{eid}")
def event_update(eid: int, data: EventIn, p: Principal = Depends(require("events", "editar")), db: Session = Depends(get_db)):
    e = _own(db, eid, p.tenant.id)
    _apply(db, e, data)
    db.commit()
    return _event_out(db, e, admin=True)


def _ticket_out(t: Ticket, types: dict) -> dict:
    return {
        "id": t.id,
        "code": t.code,
        "holder_name": t.holder_name,
        "holder_email": t.holder_email,
        "type": types.get(t.ticket_type_id),
        "status": t.status,
        "order_id": t.order_id,
        "checked_in_at": t.checked_in_at,
        "url": ticket_url(t.code),
    }


@router.get("/events/{eid}/tickets")
def tickets(eid: int, q: str | None = None, p: Principal = Depends(require("events", "ver")), db: Session = Depends(get_db)):
    e = _own(db, eid, p.tenant.id)
    types = {t.id: t.name for t in e.ticket_types}
    stmt = select(Ticket).where(Ticket.event_id == eid)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Ticket.holder_name.ilike(like)) | (Ticket.code.ilike(like)) | (Ticket.holder_email.ilike(like)))
    return [_ticket_out(t, types) for t in db.scalars(stmt.order_by(Ticket.id.desc()).limit(500))]


class CourtesyIn(BaseModel):
    ticket_type_id: int
    holder_name: str = Field(min_length=2, max_length=160)
    holder_email: EmailStr | None = None
    quantity: int = Field(1, ge=1, le=50)


@router.post("/events/{eid}/tickets", status_code=201)
def courtesy(eid: int, data: CourtesyIn, p: Principal = Depends(require("events", "crear")), db: Session = Depends(get_db)):
    """Entradas de cortesia o venta en puerta: nacen validas y se envian por correo si hay email."""
    e = _own(db, eid, p.tenant.id)
    tt = next((t for t in e.ticket_types if t.id == data.ticket_type_id), None)
    if not tt:
        raise HTTPException(404, "Tipo de entrada no encontrado")
    if _sold(db, tt.id) + data.quantity > tt.quantity:
        raise HTTPException(409, "No hay suficientes entradas disponibles")
    made = []
    for _ in range(data.quantity):
        t = Ticket(
            tenant_id=p.tenant.id,
            event_id=e.id,
            ticket_type_id=tt.id,
            code=new_code(),
            holder_name=data.holder_name,
            holder_email=data.holder_email,
            status="valido",
        )
        db.add(t)
        made.append(t)
    db.flush()
    if data.holder_email:
        _email_tickets(db, p.tenant, e, made)
    audit(db, p.tenant.id, p.user.id, "courtesy", "event", e.id, {"quantity": data.quantity}, ip=p.ip)
    db.commit()
    return [_ticket_out(t, {tt.id: tt.name}) for t in made]


@router.post("/tickets/{tid}/void")
def ticket_void(tid: int, p: Principal = Depends(require("events", "editar")), db: Session = Depends(get_db)):
    t = db.get(Ticket, tid)
    if not t or t.tenant_id != p.tenant.id:
        raise HTTPException(404, "Entrada no encontrada")
    t.status = "anulado"
    audit(db, p.tenant.id, p.user.id, "void", "ticket", t.id, ip=p.ip)
    db.commit()
    return {"ok": True}


class CheckinIn(BaseModel):
    code: str = Field(min_length=4, max_length=300)
    event_id: int | None = None


@router.post("/events/checkin")
def checkin(data: CheckinIn, p: Principal = Depends(require("events", "checkin")), db: Session = Depends(get_db)):
    """Acepta el codigo o la URL completa leida del QR. Responde el motivo exacto si no puede entrar."""
    raw = data.code.strip().rstrip("/").split("/")[-1].upper()
    code = raw if "-" in raw else (f"{raw[:5]}-{raw[5:]}" if len(raw) == 10 else raw)
    t = db.scalar(select(Ticket).where(Ticket.code == code, Ticket.tenant_id == p.tenant.id))
    if not t:
        return {"ok": False, "reason": "Código no existe", "code": code}
    tt = db.get(TicketType, t.ticket_type_id)
    ev = db.get(Event, t.event_id)
    info = {"code": t.code, "holder_name": t.holder_name, "type": tt.name if tt else None, "event": ev.name if ev else None}
    if data.event_id and t.event_id != data.event_id:
        return {"ok": False, "reason": f"La entrada es de otro evento: {ev.name if ev else ''}", **info}
    if t.status == "usado":
        return {"ok": False, "reason": f"Ya ingresó el {cr_fmt(t.checked_in_at) if t.checked_in_at else ''}", **info}
    if t.status == "pendiente":
        return {"ok": False, "reason": "Pago pendiente: la orden no está confirmada", **info}
    if t.status == "anulado":
        return {"ok": False, "reason": "Entrada anulada", **info}
    t.status, t.checked_in_at, t.checked_in_by = "usado", datetime.now(UTC), p.user.id
    audit(db, p.tenant.id, p.user.id, "checkin", "ticket", t.id, ip=p.ip)
    db.commit()
    return {"ok": True, "reason": "Bienvenido", **info}


# ---------- Activacion y correo ----------
def _email_tickets(db: Session, tenant: Tenant, e: Event, items: list[Ticket]) -> None:
    by_mail: dict[str, list[Ticket]] = {}
    for t in items:
        if t.holder_email:
            by_mail.setdefault(t.holder_email, []).append(t)
    for mail, ts in by_mail.items():
        links = "".join(f'<li><a href="{ticket_url(t.code)}">Entrada {t.code}</a></li>' for t in ts)
        when = cr_fmt(e.starts_at)
        queue_email(
            db,
            tenant,
            mail,
            f"Tus entradas · {e.name}",
            f"<p>¡Listo! Estas son tus entradas para <b>{e.name}</b> ({when}{' · ' + e.venue if e.venue else ''}).</p><ul>{links}</ul><p>Presentá el código QR de cada entrada al ingresar. Cada entrada se puede usar una sola vez.</p>",
            "ticket",
            ts[0].id,
        )


def on_order_status(db: Session, o: Order, status: str) -> int:
    """Llamado desde /orders/{id} al cambiar estado: pagado activa y envia; cancelado anula."""
    ts = db.scalars(select(Ticket).where(Ticket.order_id == o.id)).all()
    if not ts:
        return 0
    if status == "pagado":
        pending = [t for t in ts if t.status == "pendiente"]
        for t in pending:
            t.status = "valido"
        if pending:
            e = db.get(Event, pending[0].event_id)
            _email_tickets(db, db.get(Tenant, o.tenant_id), e, pending)
        return len(pending)
    if status == "cancelado":
        for t in ts:
            if t.status in ("pendiente", "valido"):
                t.status = "anulado"
        return len(ts)
    return 0


# ---------- Publico ----------
def _pub_tenant(db: Session, slug: str) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug, Tenant.active))
    if not t:
        raise HTTPException(404, "No encontrado")
    return t


@pub.get("/events/{slug}")
def pub_events(slug: str, db: Session = Depends(get_db)):
    t = _pub_tenant(db, slug)
    now = datetime.now(UTC)
    rows = db.scalars(select(Event).where(Event.tenant_id == t.id, Event.status == "publicado").order_by(Event.starts_at)).all()
    return {"tenant": {"name": t.name, "logo_url": t.logo_url}, "events": [_event_out(db, e) for e in rows if aware(e.ends_at or e.starts_at) >= now]}


def _pub_event(db: Session, slug: str, event_slug: str) -> tuple[Tenant, Event]:
    t = _pub_tenant(db, slug)
    e = db.scalar(select(Event).where(Event.tenant_id == t.id, Event.slug == event_slug, Event.status.in_(("publicado", "cerrado"))))
    if not e:
        raise HTTPException(404, "Evento no encontrado")
    return t, e


@pub.get("/events/{slug}/{event_slug}")
def pub_event(slug: str, event_slug: str, db: Session = Depends(get_db)):
    from .store import manual_methods

    t, e = _pub_event(db, slug, event_slug)
    return {"tenant": {"name": t.name, "logo_url": t.logo_url}, "event": _event_out(db, e), "payment_methods": manual_methods(t)}


class EventCartItem(BaseModel):
    ticket_type_id: int
    quantity: int = Field(ge=1, le=100)


class EventCheckoutIn(BaseModel):
    items: list[EventCartItem] = Field(min_length=1)
    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    phone: str | None = Field(None, max_length=40)
    payment_method: str | None = None


@pub.post("/events/{slug}/{event_slug}/checkout", status_code=201)
def pub_checkout(slug: str, event_slug: str, data: EventCheckoutIn, request: Request, db: Session = Depends(get_db)):
    from .store import manual_methods

    public_limiter.hit(f"checkout:{request.client.host if request.client else '?'}")
    t, e = _pub_event(db, slug, event_slug)
    if e.status != "publicado":
        raise HTTPException(409, "La venta de entradas está cerrada")
    if aware(e.ends_at or e.starts_at) < datetime.now(UTC):
        raise HTTPException(409, "El evento ya pasó")
    types = {tt.id: tt for tt in e.ticket_types if tt.active}
    rate = d(e.tax_rate)
    lines, calc_in = [], []
    for it in data.items:
        tt = types.get(it.ticket_type_id)
        if not tt:
            raise HTTPException(422, "Tipo de entrada no disponible")
        if it.quantity > tt.max_per_order:
            raise HTTPException(422, f"Máximo {tt.max_per_order} entradas '{tt.name}' por compra")
        # bloquea la fila del tipo para que dos compras simultaneas no sobrevendan (Postgres)
        db.execute(select(TicketType.id).where(TicketType.id == tt.id).with_for_update())
        if _sold(db, tt.id) + it.quantity > tt.quantity:
            raise HTTPException(409, f"Quedan {max(tt.quantity - _sold(db, tt.id), 0)} entradas '{tt.name}'")
        unit = (d(tt.price) / (1 + rate / 100)).quantize(Decimal("0.00001"))  # el precio publicado incluye IVA
        lines.append((tt, it.quantity, unit))
        calc_in.append(LineIn(Decimal(it.quantity), unit, "percent", Decimal(0), rate))
    calc = compute_document(calc_in)
    number, _ = next_number(db, t.id, "ORD")
    o = Order(
        tenant_id=t.id,
        number=number,
        channel="evento",
        contact={"name": data.name, "email": str(data.email), "phone": data.phone},
        currency=e.currency,
        subtotal=calc.subtotal,
        discount_total=0,
        shipping=0,
        tax_total=calc.tax_total,
        total=calc.total,
        payment_method=data.payment_method,
        notes=f"Entradas · {e.name}",
    )
    for (tt, qty, unit), lo in zip(lines, calc.lines, strict=True):
        o.lines.append(
            OrderLine(
                name=f"Entrada {tt.name} · {e.name}"[:200],
                quantity=qty,
                unit_price=unit,
                tax_rate=rate,
                subtotal=lo.subtotal,
                tax_amount=lo.tax_amount,
                total=lo.total,
            )
        )
    free = calc.total == 0
    if free:
        o.status = "pagado"
    db.add(o)
    db.flush()
    made = []
    for tt, qty, _unit in lines:
        for _ in range(qty):
            tk = Ticket(
                tenant_id=t.id,
                event_id=e.id,
                ticket_type_id=tt.id,
                order_id=o.id,
                code=new_code(),
                holder_name=data.name,
                holder_email=str(data.email),
                status="valido" if free else "pendiente",
            )
            db.add(tk)
            made.append(tk)
    db.flush()
    if free:
        _email_tickets(db, t, e, made)
    audit(db, t.id, None, "create", "order", o.id, {"channel": "evento", "tickets": len(made)})
    db.commit()
    return {
        "number": o.number,
        "total": o.total,
        "currency": o.currency,
        "free": free,
        "tickets": [ticket_url(tk.code) for tk in made] if free else [],
        "instructions": next((m.get("instructions") for m in manual_methods(t) if m.get("name") == data.payment_method), None),
    }


@pub.get("/tickets/{code}")
def pub_ticket(code: str, db: Session = Depends(get_db)):
    tk = db.scalar(select(Ticket).where(Ticket.code == code.upper()))
    if not tk:
        raise HTTPException(404, "Entrada no encontrada")
    e = db.get(Event, tk.event_id)
    tt = db.get(TicketType, tk.ticket_type_id)
    t = db.get(Tenant, tk.tenant_id)
    return {
        "code": tk.code,
        "status": tk.status,
        "holder_name": tk.holder_name,
        "type": tt.name if tt else None,
        "event": {"name": e.name, "venue": e.venue, "starts_at": e.starts_at, "image_url": e.image_url},
        "tenant": {"name": t.name, "logo_url": t.logo_url},
        "qr_svg": qr_svg(ticket_url(tk.code)) if tk.status in ("valido", "usado") else None,
    }
