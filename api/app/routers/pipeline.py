"""Oportunidades: el embudo comercial antes de la cotizacion.

La vendedora ve las suyas; administracion y gerencia ven todas. Cada oportunidad lleva su proxima accion con fecha,
que es lo que evita que una cotizacion se enfrie sin que nadie la note (el worker avisa a los 5 dias)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, Opportunity, Project, Quote, Survey, User
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(tags=["oportunidades"])

STATES = ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion", "ganada", "perdida")
OPEN_STATES = STATES[:7]


def _own(db: Session, oid: int, p: Principal) -> Opportunity:
    o = db.get(Opportunity, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Oportunidad no encontrada")
    if not p.can("crm_pipeline", "ver_todo") and o.owner_id not in (None, p.user.id):
        raise HTTPException(404, "Oportunidad no encontrada")
    return o


def _out(db: Session, o: Opportunity) -> dict:
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    owner = db.get(User, o.owner_id) if o.owner_id else None
    return {
        "id": o.id,
        "number": o.number,
        "title": o.title,
        "customer_id": o.customer_id,
        "customer": c.name if c else (o.contact or {}).get("name"),
        "contact": o.contact,
        "source": o.source,
        "solution": o.solution,
        "owner_id": o.owner_id,
        "owner": owner.full_name if owner else None,
        "amount": o.amount,
        "currency": o.currency,
        "probability": o.probability,
        "status": o.status,
        "next_action": o.next_action,
        "next_action_date": o.next_action_date,
        "lost_reason": o.lost_reason,
        "notes": o.notes,
        "quote_id": o.quote_id,
        "project_id": o.project_id,
        "created_at": o.created_at,
        "weighted": (d(o.amount) * o.probability / 100).quantize(Decimal("0.01")),
    }


class OpportunityIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    customer_id: int | None = None
    contact: dict = Field(default_factory=dict)
    source: str | None = Field(None, max_length=60)
    solution: str | None = Field(None, max_length=20)
    owner_id: int | None = None
    amount: Decimal = Field(Decimal(0), ge=0)
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    probability: int = Field(30, ge=0, le=100)
    status: str = Field("nuevo", pattern="^(" + "|".join(STATES) + ")$")
    next_action: str | None = Field(None, max_length=200)
    next_action_date: date | None = None
    lost_reason: str | None = Field(None, max_length=200)
    notes: str | None = None


@router.get("/opportunities")
def opportunities(
    status: str | None = None,
    q: str | None = None,
    mine: bool = False,
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("crm_pipeline", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(Opportunity).where(Opportunity.tenant_id == p.tenant.id)
    if not p.can("crm_pipeline", "ver_todo") or mine:
        stmt = stmt.where(Opportunity.owner_id == p.user.id)
    if status == "abiertas":
        stmt = stmt.where(Opportunity.status.in_(OPEN_STATES))
    elif status:
        stmt = stmt.where(Opportunity.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Opportunity.title.ilike(like), Opportunity.number.ilike(like)))
    rows = db.scalars(stmt.order_by(Opportunity.next_action_date.is_(None), Opportunity.next_action_date, Opportunity.id.desc()).limit(limit)).all()
    return [_out(db, o) for o in rows]


@router.get("/opportunities/board")
def board(p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """Embudo por estado: monto total y monto ponderado por probabilidad."""
    stmt = select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.status.in_(OPEN_STATES))
    if not p.can("crm_pipeline", "ver_todo"):
        stmt = stmt.where(Opportunity.owner_id == p.user.id)
    rows = db.scalars(stmt).all()
    cols = []
    for st in OPEN_STATES:
        items = [o for o in rows if o.status == st]
        cols.append(
            {
                "status": st,
                "count": len(items),
                "amount": sum((d(o.amount) for o in items), Decimal(0)),
                "weighted": sum((d(o.amount) * o.probability / 100 for o in items), Decimal(0)).quantize(Decimal("0.01")),
                "items": [_out(db, o) for o in items][:25],
            }
        )
    return {
        "columns": cols,
        "total": sum((d(o.amount) for o in rows), Decimal(0)),
        "weighted": sum((d(o.amount) * o.probability / 100 for o in rows), Decimal(0)).quantize(Decimal("0.01")),
    }


@router.post("/opportunities", status_code=201)
def create(data: OpportunityIn, p: Principal = Depends(require("crm_pipeline", "crear")), db: Session = Depends(get_db)):
    from ..services.sequences import next_number

    number, _ = next_number(db, p.tenant.id, "OPO")
    o = Opportunity(tenant_id=p.tenant.id, number=number, created_by=p.user.id, **data.model_dump())
    o.owner_id = data.owner_id or p.user.id
    db.add(o)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "opportunity", o.id, ip=p.ip)
    db.commit()
    return _out(db, o)


@router.get("/opportunities/{oid}")
def get_one(oid: int, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    o = _own(db, oid, p)
    out = _out(db, o)
    out["surveys"] = [
        {"id": s.id, "number": s.number, "kind": s.kind, "status": s.status} for s in db.scalars(select(Survey).where(Survey.opportunity_id == o.id))
    ]
    if o.quote_id:
        q = db.get(Quote, o.quote_id)
        out["quote"] = {"id": q.id, "number": q.number, "status": q.status, "total": q.total} if q else None
    if o.project_id:
        pr = db.get(Project, o.project_id)
        out["project"] = {"id": pr.id, "number": pr.number, "status": pr.status} if pr else None
    return out


@router.put("/opportunities/{oid}")
def update(oid: int, data: OpportunityIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _own(db, oid, p)
    before = o.status
    for k, v in data.model_dump().items():
        setattr(o, k, v)
    if o.status != before:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": before, "a": o.status}, ip=p.ip)
    db.commit()
    return _out(db, o)


class TouchIn(BaseModel):
    note: str = Field(min_length=2, max_length=500)
    next_action: str | None = Field(None, max_length=200)
    next_action_date: date | None = None
    status: str | None = Field(None, pattern="^(" + "|".join(STATES) + ")$")


@router.post("/opportunities/{oid}/touch")
def touch(oid: int, data: TouchIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Registra un seguimiento (llamada, visita, correo) y reprograma la próxima acción."""
    o = _own(db, oid, p)
    stamp = datetime.now(UTC).strftime("%d/%m/%Y %H:%M")
    o.notes = f"{stamp} · {p.user.full_name}: {data.note}\n{o.notes or ''}".strip()
    if data.next_action is not None:
        o.next_action = data.next_action
    if data.next_action_date is not None:
        o.next_action_date = data.next_action_date
    if data.status:
        o.status = data.status
    audit(db, p.tenant.id, p.user.id, "touch", "opportunity", o.id, {"note": data.note[:120]}, ip=p.ip)
    db.commit()
    return _out(db, o)


@router.get("/opportunities/meta/config")
def meta(p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    from ..models import TenantUser

    sellers = [
        {"id": m.user.id, "name": m.user.full_name}
        for m in db.scalars(
            select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.active, TenantUser.role_code.in_(("admin", "ventas", "supervisor")))
        )
    ]
    return {
        "states": list(STATES),
        "sources": ["Referido", "Sitio web", "WhatsApp", "Llamada", "Cliente actual", "Feria", "Alianza"],
        "solutions": ["cctv", "redes", "acceso", "asistencia", "ups", "cableado", "anpr", "otro"],
        "sellers": sellers,
        "can_see_all": p.can("crm_pipeline", "ver_todo"),
    }


def stale_followups(db: Session, tenant_id: int, days: int = 5) -> list[Opportunity]:
    """Oportunidades abiertas con la próxima acción vencida, o sin movimiento en N días (lo usa el worker)."""
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    for o in db.scalars(select(Opportunity).where(Opportunity.tenant_id == tenant_id, Opportunity.status.in_(OPEN_STATES))):
        last = o.updated_at if o.updated_at.tzinfo else o.updated_at.replace(tzinfo=UTC)
        if (o.next_action_date and o.next_action_date < date.today()) or last < cutoff:
            out.append(o)
    return out
