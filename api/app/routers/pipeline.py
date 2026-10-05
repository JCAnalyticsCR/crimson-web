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
from ..services.archive import live
from ..services.documents import CR, audit
from ..services.totals import d

router = APIRouter(tags=["oportunidades"])

STATES = ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion", "ganada", "perdida")
OPEN_STATES = STATES[:7]
# venta: solo equipo, sin instalacion (camino: cotizacion -> factura) | proyecto: levantamiento -> cotizacion -> proyecto
KINDS = ("venta", "proyecto")


def advance_status(o: Opportunity, to: str) -> str | None:
    """Mueve la oportunidad hacia adelante en el embudo y nunca hacia atras: si ya estaba "enviada" o en
    "negociacion", crear o ligar una cotizacion no la devuelve a "cotizando". Devuelve el estado anterior si cambio."""
    if o.status in ("ganada", "perdida") or o.status not in STATES or STATES.index(o.status) >= STATES.index(to):
        return None
    before, o.status = o.status, to
    return before


def _initials(name: str | None) -> str | None:
    parts = [x for x in (name or "").split() if x]
    return "".join(x[0] for x in parts[:2]).upper() if parts else None


def _log(o: Opportunity, who: str, text: str) -> None:
    """Una linea en la bitacora (notes), con la hora de Costa Rica, igual que los seguimientos."""
    stamp = datetime.now(CR).strftime("%d/%m/%Y %H:%M")
    o.notes = f"{stamp} · {who}: {text}\n{o.notes or ''}".strip()


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
        "archived_at": o.archived_at,
        "trashed_at": o.trashed_at,
        "customer_id": o.customer_id,
        "customer": c.name if c else (o.contact or {}).get("name"),
        "contact": o.contact,
        "source": o.source,
        "solution": o.solution,
        "owner_id": o.owner_id,
        "owner": owner.full_name if owner else None,
        "owner_initials": _initials(owner.full_name) if owner else None,
        "kind": o.kind or "proyecto",
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
    kind: str = Field("proyecto", pattern="^(venta|proyecto)$")
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
    owner_id: int | None = None,
    kind: str | None = Query(None, pattern="^(venta|proyecto)$"),
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("crm_pipeline", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, live(Opportunity))
    stmt = _scope(stmt, p, owner_id, mine)
    if kind:
        stmt = stmt.where(Opportunity.kind == kind)
    if status == "abiertas":
        stmt = stmt.where(Opportunity.status.in_(OPEN_STATES))
    elif status:
        stmt = stmt.where(Opportunity.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Opportunity.title.ilike(like), Opportunity.number.ilike(like)))
    rows = db.scalars(stmt.order_by(Opportunity.next_action_date.is_(None), Opportunity.next_action_date, Opportunity.id.desc()).limit(limit)).all()
    return [_out(db, o) for o in rows]


def _scope(stmt, p: Principal, owner_id: int | None, mine: bool = False):
    """El vendedor sin "ver_todo" solo ve lo suyo, pida el responsable que pida (el filtro no abre nada).
    Quien ve todo puede filtrar por un responsable (owner_id) o por las suyas (mine)."""
    if not p.can("crm_pipeline", "ver_todo") or mine:
        return stmt.where(Opportunity.owner_id == p.user.id)
    if owner_id is not None:
        return stmt.where(Opportunity.owner_id == owner_id)
    return stmt


@router.get("/opportunities/board")
def board(
    owner_id: int | None = None,
    kind: str | None = Query(None, pattern="^(venta|proyecto)$"),
    p: Principal = Depends(require("crm_pipeline", "ver")),
    db: Session = Depends(get_db),
):
    """Embudo por estado: monto total y monto ponderado por probabilidad."""
    stmt = select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.status.in_(OPEN_STATES), live(Opportunity))
    stmt = _scope(stmt, p, owner_id)
    if kind:
        stmt = stmt.where(Opportunity.kind == kind)
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


@router.get("/opportunities/pending")
def pending(
    everyone: bool = False,
    p: Principal = Depends(require("crm_pipeline", "ver")),
    db: Session = Depends(get_db),
):
    """Mis pendientes: proximas acciones de hoy y vencidas, de la mas atrasada a la de hoy.
    Por defecto solo las propias; quien ve todo el embudo puede pedir las de todo el equipo (everyone=true)."""
    today = date.today()
    stmt = select(Opportunity).where(
        Opportunity.tenant_id == p.tenant.id,
        Opportunity.status.in_(OPEN_STATES),
        live(Opportunity),
        Opportunity.next_action_date.is_not(None),
        Opportunity.next_action_date <= today,
    )
    if not (everyone and p.can("crm_pipeline", "ver_todo")):
        stmt = stmt.where(Opportunity.owner_id == p.user.id)
    rows = db.scalars(stmt.order_by(Opportunity.next_action_date, Opportunity.id)).all()
    out = []
    for o in rows:
        item = _out(db, o)
        item["days_late"] = (today - o.next_action_date).days
        out.append(item)
    return out


@router.post("/opportunities", status_code=201)
def create(data: OpportunityIn, p: Principal = Depends(require("crm_pipeline", "crear")), db: Session = Depends(get_db)):
    from ..services.sequences import next_number

    number, _ = next_number(db, p.tenant.id, "OPO")
    o = Opportunity(tenant_id=p.tenant.id, number=number, created_by=p.user.id, **data.model_dump())
    # asignar a otra persona es de quien ve todo el embudo; el vendedor crea las suyas
    o.owner_id = (data.owner_id if p.can("crm_pipeline", "ver_todo") else None) or p.user.id
    if o.owner_id != p.user.id:
        _check_owner(db, p, o.owner_id)
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
        {"id": s.id, "number": s.number, "kind": s.kind, "status": s.status}
        for s in db.scalars(select(Survey).where(Survey.opportunity_id == o.id, live(Survey)))
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
    # el responsable no se cambia por accidente al editar: sin dato queda el que estaba, y cambiarlo pasa por _reassign
    new_owner = data.owner_id
    for k, v in data.model_dump(exclude={"owner_id"}).items():
        setattr(o, k, v)
    if o.status != before:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": before, "a": o.status}, ip=p.ip)
    if new_owner is not None and new_owner != o.owner_id:
        _reassign(db, p, o, new_owner)
    db.commit()
    return _out(db, o)


def _check_owner(db: Session, p: Principal, uid: int) -> User:
    from ..models import TenantUser

    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid, TenantUser.active))
    if not m:
        raise HTTPException(422, "Ese responsable no es un usuario activo de la empresa")
    return m.user


def _reassign(db: Session, p: Principal, o: Opportunity, uid: int) -> None:
    """Cambiar el responsable es de quien ve todo el embudo (gerencia/administracion); queda en bitacora y auditoria."""
    if not p.can("crm_pipeline", "ver_todo"):
        raise HTTPException(403, "Solo quien ve todo el embudo puede reasignar oportunidades")
    nuevo = _check_owner(db, p, uid)
    antes = db.get(User, o.owner_id) if o.owner_id else None
    o.owner_id = uid
    _log(o, p.user.full_name, f"Reasignada de {antes.full_name if antes else 'nadie'} a {nuevo.full_name}")
    audit(db, p.tenant.id, p.user.id, "assign", "opportunity", o.id, {"de": antes.id if antes else None, "a": uid}, ip=p.ip)


class AssignIn(BaseModel):
    owner_id: int


@router.post("/opportunities/{oid}/assign")
def assign(oid: int, data: AssignIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _own(db, oid, p)
    if data.owner_id != o.owner_id:
        _reassign(db, p, o, data.owner_id)
        db.commit()
    return _out(db, o)


class OppQuoteIn(BaseModel):
    customer_id: int | None = None  # obligatorio si la oportunidad todavia no tiene cliente


@router.post("/opportunities/{oid}/quote", status_code=201)
def create_quote(oid: int, data: OppQuoteIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Cotizacion directa desde la oportunidad, sin levantamiento (venta de equipo o instalacion que no lo necesita).

    Nace vacia con el cliente de la oportunidad y se completa en el editor de cotizaciones. La oportunidad queda
    ligada (quote_id) y pasa a "cotizando" solo si venia de una etapa anterior. El monto se queda con el estimado
    hasta que la cotizacion se guarde: a partir de ahi lo pone sync_amount_from_quote (PUT /quotes)."""
    from ..schemas.sales import DocumentIn
    from ..services import documents as docsvc

    if not p.can("crm_pipeline", "editar"):
        raise HTTPException(403, "No tenés permiso para editar oportunidades")
    o = _own(db, oid, p)
    if o.status in ("ganada", "perdida"):
        raise HTTPException(409, "La oportunidad ya está cerrada")
    if o.quote_id:
        q = db.get(Quote, o.quote_id)
        if q and q.status != "anulada":
            raise HTTPException(409, f"La oportunidad ya tiene la cotización {q.number}")
    cid = o.customer_id or data.customer_id
    if not cid:
        raise HTTPException(422, "Elegí el cliente antes de cotizar")
    c = db.get(Customer, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Cliente no encontrado")
    o.customer_id = cid
    q = docsvc.create_quote(
        db,
        p.tenant.id,
        p.user.id,
        DocumentIn(customer_id=cid, currency=o.currency or "CRC", internal_notes=f"Oportunidad {o.number} · {o.title}"[:500]),
    )
    o.quote_id = q.id
    antes = advance_status(o, "cotizando")
    _log(o, p.user.full_name, f"Cotización {q.number} creada desde la oportunidad")
    if antes:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": antes, "a": o.status}, ip=p.ip)
    audit(db, p.tenant.id, p.user.id, "quote", "opportunity", o.id, {"quote_id": q.id}, ip=p.ip)
    db.commit()
    return {"quote_id": q.id, "number": q.number, "opportunity": _out(db, o)}


class TouchIn(BaseModel):
    note: str = Field(min_length=2, max_length=500)
    next_action: str | None = Field(None, max_length=200)
    next_action_date: date | None = None
    status: str | None = Field(None, pattern="^(" + "|".join(STATES) + ")$")


@router.post("/opportunities/{oid}/touch")
def touch(oid: int, data: TouchIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Registra un seguimiento (llamada, visita, correo) y reprograma la próxima acción."""
    o = _own(db, oid, p)
    # La bitacora es texto que lee una persona en Costa Rica: la hora va en hora local, no en UTC.
    _log(o, p.user.full_name, data.note)
    if data.next_action is not None:
        o.next_action = data.next_action
    if data.next_action_date is not None:
        o.next_action_date = data.next_action_date
    before = o.status
    if data.status:
        o.status = data.status
    audit(db, p.tenant.id, p.user.id, "touch", "opportunity", o.id, {"note": data.note[:120]}, ip=p.ip)
    if o.status != before:
        # El arrastre entre columnas del embudo llega por aqui: queda en bitacora (notes) y en auditoria como cambio de etapa
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": before, "a": o.status}, ip=p.ip)
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
        "kinds": list(KINDS),
        "sellers": sellers,
        "can_see_all": p.can("crm_pipeline", "ver_todo"),
    }


def sync_amount_from_quote(db: Session, q: Quote) -> None:
    """El monto de la oportunidad sigue a su cotizacion ligada (o.quote_id).

    Mientras no hay cotizacion el monto es el que se escribio a mano. Cuando hay cotizacion, el monto es su TOTAL
    (con IVA): es la cifra que el cliente ve y firma, y la misma que ya pone el paso levantamiento -> cotizacion
    (fieldwork.survey_to_quote). Antes solo se copiaba al crear la cotizacion y al editarla quedaba el monto viejo.
    Una cotizacion anulada no pisa el monto."""
    if q.status == "anulada":
        return
    for o in db.scalars(select(Opportunity).where(Opportunity.tenant_id == q.tenant_id, Opportunity.quote_id == q.id)):
        o.amount = d(q.total)
        o.currency = q.currency or o.currency


def stale_followups(db: Session, tenant_id: int, days: int = 5) -> list[Opportunity]:
    """Oportunidades abiertas con la próxima acción de hoy o vencida, o sin movimiento en N días (lo usa el worker).
    Incluye las de hoy para que el correo de la mañana sirva de alerta del día de la acción."""
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    for o in db.scalars(select(Opportunity).where(Opportunity.tenant_id == tenant_id, Opportunity.status.in_(OPEN_STATES), live(Opportunity))):
        last = o.updated_at if o.updated_at.tzinfo else o.updated_at.replace(tzinfo=UTC)
        if (o.next_action_date and o.next_action_date <= date.today()) or last < cutoff:
            out.append(o)
    return out
