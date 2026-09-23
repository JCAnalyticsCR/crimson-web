"""Mesa de soporte: tickets de clientes, contratos de mantenimiento y comisiones.

Un ticket puede nacer de tres lados: lo abre alguien de Crimson, lo genera el worker cuando toca un
mantenimiento preventivo, o lo dispara una garantia por vencer. Si hay que ir al sitio, el ticket
genera una orden de trabajo y se reusa todo lo de campo en lugar de inventar un flujo paralelo.

Ojo: "support.py" es otra cosa (el acceso temporal de soporte de JC Analytics a la cuenta). Esto es
el soporte que Crimson le da a SUS clientes.
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import (
    Commission,
    CommissionRule,
    Customer,
    CustomerAsset,
    Invoice,
    MaintenanceContract,
    Payment,
    Product,
    Project,
    SupportNote,
    SupportTicket,
    TenantUser,
    User,
    WorkOrder,
)
from ..services.documents import audit, local_date
from ..services.mail import notify_roles, queue_email
from ..services.sequences import next_number
from ..services.totals import d

router = APIRouter(tags=["soporte"])

OPEN_STATES = ("nuevo", "asignado", "en_proceso", "esperando_cliente")
# Cuanto tiempo damos para la primera respuesta segun la prioridad. Es el compromiso que Crimson
# le puede ofrecer a Hikvision por escrito; sin esto "soporte nivel 1" no significa nada medible.
SLA_HORAS = {"critica": 2, "alta": 4, "media": 8, "baja": 24}


def _own(db: Session, tid: int, p: Principal) -> SupportTicket:
    t = db.get(SupportTicket, tid)
    if not t or t.tenant_id != p.tenant.id:
        raise HTTPException(404, "Ticket no encontrado")
    if not p.can("support_desk", "ver_todo") and t.assigned_to not in (None, p.user.id) and t.opened_by != p.user.id:
        raise HTTPException(404, "Ticket no encontrado")
    return t


def _out(db: Session, t: SupportTicket, full: bool = True) -> dict:
    c = db.get(Customer, t.customer_id) if t.customer_id else None
    u = db.get(User, t.assigned_to) if t.assigned_to else None
    a = db.get(CustomerAsset, t.asset_id) if t.asset_id else None
    ahora = datetime.now(UTC)
    vencido = bool(t.due_at and not t.first_reply_at and _aware(t.due_at) < ahora and t.status in OPEN_STATES)
    out = {
        "id": t.id,
        "number": t.number,
        "subject": t.subject,
        "kind": t.kind,
        "channel": t.channel,
        "level": t.level,
        "priority": t.priority,
        "status": t.status,
        "customer_id": t.customer_id,
        "customer": c.name if c else (t.contact or {}).get("name"),
        "asset_id": t.asset_id,
        "asset": a.name if a else None,
        "project_id": t.project_id,
        "contract_id": t.contract_id,
        "work_order_id": t.work_order_id,
        "invoice_id": t.invoice_id,
        "assigned_to": t.assigned_to,
        "assigned": u.full_name if u else None,
        "due_at": t.due_at,
        "first_reply_at": t.first_reply_at,
        "resolved_at": t.resolved_at,
        "sla_vencido": vencido,
        "hours": t.hours,
        "billable": t.billable,
        "amount": t.amount,
        "tags": t.tags,
        "created_at": t.created_at,
    }
    if full:
        out["description"] = t.description
        out["solution"] = t.solution
        out["photos"] = t.photos
        out["notes"] = [
            {
                "id": n.id,
                "body": n.body,
                "internal": n.internal,
                "photos": n.photos,
                "user": db.get(User, n.user_id).full_name if n.user_id else "Sistema",
                "created_at": n.created_at,
            }
            for n in t.notes
        ]
    return out


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    customer_id: int | None = None
    contact: dict = Field(default_factory=dict)
    asset_id: int | None = None
    project_id: int | None = None
    kind: str = Field("soporte", pattern="^(soporte|garantia|mantenimiento|visita|instalacion|consulta)$")
    channel: str | None = Field(None, max_length=20)
    level: int = Field(1, ge=1, le=3)
    priority: str = Field("media", pattern="^(baja|media|alta|critica)$")
    description: str | None = None
    assigned_to: int | None = None
    billable: bool = False
    tags: list = Field(default_factory=list)


@router.get("/tickets")
def tickets(
    status: str | None = None,
    kind: str | None = None,
    customer_id: int | None = None,
    mine: bool = False,
    q: str | None = None,
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("support_desk", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(SupportTicket).where(SupportTicket.tenant_id == p.tenant.id)
    if status == "abiertos":
        stmt = stmt.where(SupportTicket.status.in_(OPEN_STATES))
    elif status:
        stmt = stmt.where(SupportTicket.status == status)
    if kind:
        stmt = stmt.where(SupportTicket.kind == kind)
    if customer_id:
        stmt = stmt.where(SupportTicket.customer_id == customer_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(SupportTicket.subject.ilike(like), SupportTicket.number.ilike(like)))
    rows = db.scalars(stmt.order_by(SupportTicket.id.desc()).limit(limit)).all()
    if mine or not p.can("support_desk", "ver_todo"):
        rows = [t for t in rows if t.assigned_to == p.user.id or t.opened_by == p.user.id]
    return [_out(db, t, full=False) for t in rows]


@router.post("/tickets", status_code=201)
def ticket_create(data: TicketIn, p: Principal = Depends(require("support_desk", "crear")), db: Session = Depends(get_db)):
    number, _ = next_number(db, p.tenant.id, "TCK")
    t = SupportTicket(tenant_id=p.tenant.id, number=number, opened_by=p.user.id, **data.model_dump())
    t.status = "asignado" if t.assigned_to else "nuevo"
    t.due_at = datetime.now(UTC) + timedelta(hours=SLA_HORAS.get(t.priority, 8))
    db.add(t)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "support_ticket", t.id, {"prioridad": t.priority}, ip=p.ip)
    _avisar_asignado(db, p, t, nuevo=True)
    db.commit()
    return _out(db, t)


@router.get("/tickets/{tid}")
def ticket_get(tid: int, p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    return _out(db, _own(db, tid, p))


@router.put("/tickets/{tid}")
def ticket_update(tid: int, data: TicketIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    t = _own(db, tid, p)
    antes = t.assigned_to
    for k, v in data.model_dump().items():
        setattr(t, k, v)
    if t.assigned_to and t.status == "nuevo":
        t.status = "asignado"
    if t.assigned_to and t.assigned_to != antes:
        _avisar_asignado(db, p, t)
    db.commit()
    return _out(db, t)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    internal: bool = False
    photos: list = Field(default_factory=list)
    hours: Decimal | None = Field(None, ge=0)
    status: str | None = Field(None, pattern="^(nuevo|asignado|en_proceso|esperando_cliente|resuelto|cerrado)$")


@router.post("/tickets/{tid}/notes", status_code=201)
def ticket_note(tid: int, data: NoteIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    """Cada respuesta queda con hora y autor: eso es lo que después se le enseña al cliente."""
    t = _own(db, tid, p)
    t.notes.append(SupportNote(tenant_id=t.tenant_id, user_id=p.user.id, body=data.body, internal=data.internal, photos=data.photos))
    if not data.internal and not t.first_reply_at:
        t.first_reply_at = datetime.now(UTC)  # el reloj del SLA para la primera respuesta
    if data.hours is not None:
        t.hours = d(t.hours) + d(data.hours)
    if data.status:
        _mover(t, data.status)
    db.commit()
    return _out(db, t)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(nuevo|asignado|en_proceso|esperando_cliente|resuelto|cerrado)$")
    solution: str | None = None
    hours: Decimal | None = Field(None, ge=0)
    billable: bool | None = None
    amount: Decimal | None = Field(None, ge=0)


def _mover(t: SupportTicket, status: str) -> None:
    ahora = datetime.now(UTC)
    t.status = status
    if status == "resuelto" and not t.resolved_at:
        t.resolved_at = ahora
    if status == "cerrado":
        t.resolved_at = t.resolved_at or ahora
        t.closed_at = ahora


@router.patch("/tickets/{tid}")
def ticket_status(tid: int, data: StatusIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    t = _own(db, tid, p)
    if data.status in ("resuelto", "cerrado") and not (data.solution or t.solution):
        raise HTTPException(422, "Escribí qué se hizo antes de cerrar el ticket")
    if data.solution is not None:
        t.solution = data.solution
    if data.hours is not None:
        t.hours = data.hours
    if data.billable is not None:
        t.billable = data.billable
    if data.amount is not None:
        t.amount = data.amount
    _mover(t, data.status)
    audit(db, p.tenant.id, p.user.id, "status", "support_ticket", t.id, {"status": data.status}, ip=p.ip)
    db.commit()
    return _out(db, t)


class VisitIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    technician_id: int | None = None
    scheduled_at: datetime | None = None
    site: str | None = Field(None, max_length=300)


@router.post("/tickets/{tid}/work-order", status_code=201)
def ticket_to_work_order(tid: int, data: VisitIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    """Hay que ir al sitio: el ticket se convierte en orden de trabajo y no se escribe nada de nuevo."""
    t = _own(db, tid, p)
    if t.work_order_id:
        raise HTTPException(409, "Este ticket ya tiene una orden de trabajo")
    number, _ = next_number(db, p.tenant.id, "OT")
    o = WorkOrder(
        tenant_id=p.tenant.id,
        number=number,
        title=data.title or f"{t.number} · {t.subject}",
        kind="soporte" if t.kind in ("soporte", "garantia", "consulta") else "mantenimiento",
        customer_id=t.customer_id,
        project_id=t.project_id,
        site=data.site,
        scheduled_at=data.scheduled_at,
        technician_id=data.technician_id,
        notes=t.description,
    )
    db.add(o)
    db.flush()
    t.work_order_id = o.id
    if t.status == "nuevo":
        t.status = "asignado"
    audit(db, p.tenant.id, p.user.id, "work_order", "support_ticket", t.id, {"work_order": o.number}, ip=p.ip)
    db.commit()
    return {"work_order_id": o.id, "number": o.number}


@router.get("/tickets/meta/config")
def ticket_meta(p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    agentes = [
        {"id": m.user.id, "name": m.user.full_name, "role": m.role_code}
        for m in db.scalars(
            select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.active, TenantUser.role_code.in_(("admin", "supervisor", "tecnico")))
        )
    ]
    abiertos = db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.tenant_id == p.tenant.id, SupportTicket.status.in_(OPEN_STATES)))
    return {
        "kinds": ["soporte", "garantia", "mantenimiento", "visita", "instalacion", "consulta"],
        "states": ["nuevo", "asignado", "en_proceso", "esperando_cliente", "resuelto", "cerrado"],
        "priorities": ["baja", "media", "alta", "critica"],
        "channels": ["whatsapp", "correo", "llamada", "presencial", "portal"],
        "sla_horas": SLA_HORAS,
        "agents": agentes,
        "abiertos": abiertos,
        "can_see_all": p.can("support_desk", "ver_todo"),
    }


def _avisar_asignado(db: Session, p: Principal, t: SupportTicket, nuevo: bool = False) -> None:
    u = db.get(User, t.assigned_to) if t.assigned_to else None
    cuerpo = (
        f"<p><b>{html.escape(t.subject)}</b> · prioridad {t.priority}</p>"
        f"<p>Cliente: {html.escape((db.get(Customer, t.customer_id).name if t.customer_id else '') or 'sin ficha')}</p>"
        f"{f'<p>{html.escape(t.description)}</p>' if t.description else ''}"
        f"<p>Primera respuesta antes de {SLA_HORAS.get(t.priority, 8)} h.</p>"
    )
    if u and u.email:
        queue_email(db, p.tenant, u.email, f"Ticket {t.number} · {t.subject[:60]}", cuerpo, "support_ticket", t.id)
    elif nuevo:
        notify_roles(db, p.tenant, ("admin", "supervisor"), f"Ticket {t.number} sin asignar · {t.subject[:50]}", cuerpo, "support_ticket", t.id)


# ---------- Contratos de mantenimiento ----------
class ContractIn(BaseModel):
    customer_id: int
    project_id: int | None = None
    name: str = Field(min_length=2, max_length=200)
    kind: str = Field("mantenimiento", pattern="^(mantenimiento|soporte|renting|licencia)$")
    every_months: int = Field(6, ge=1, le=60)
    next_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    amount: Decimal = Field(Decimal(0), ge=0)
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    scope: str | None = None
    active: bool = True
    notes: str | None = None


def _contract_out(db: Session, c: MaintenanceContract) -> dict:
    cl = db.get(Customer, c.customer_id)
    pr = db.get(Project, c.project_id) if c.project_id else None
    dias = (c.next_date - date.today()).days if c.next_date else None
    return {
        "id": c.id,
        "number": c.number,
        "name": c.name,
        "kind": c.kind,
        "customer_id": c.customer_id,
        "customer": cl.name if cl else None,
        "project": pr.number if pr else None,
        "every_months": c.every_months,
        "next_date": c.next_date,
        "last_done": c.last_done,
        "start_date": c.start_date,
        "end_date": c.end_date,
        "amount": c.amount,
        "currency": c.currency,
        "scope": c.scope,
        "active": c.active,
        "notes": c.notes,
        "dias_para_la_proxima": dias,
    }


@router.get("/contracts")
def contracts(active: bool | None = None, p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    stmt = select(MaintenanceContract).where(MaintenanceContract.tenant_id == p.tenant.id)
    if active is not None:
        stmt = stmt.where(MaintenanceContract.active.is_(active))
    return [_contract_out(db, c) for c in db.scalars(stmt.order_by(MaintenanceContract.next_date.is_(None), MaintenanceContract.next_date))]


@router.post("/contracts", status_code=201)
def contract_create(data: ContractIn, p: Principal = Depends(require("support_desk", "crear")), db: Session = Depends(get_db)):
    number, _ = next_number(db, p.tenant.id, "CTR")
    c = MaintenanceContract(tenant_id=p.tenant.id, number=number, **data.model_dump())
    c.next_date = c.next_date or c.start_date or date.today()
    db.add(c)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "maintenance_contract", c.id, ip=p.ip)
    db.commit()
    return _contract_out(db, c)


@router.put("/contracts/{cid}")
def contract_update(cid: int, data: ContractIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    c = db.get(MaintenanceContract, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Contrato no encontrado")
    for k, v in data.model_dump().items():
        setattr(c, k, v)
    db.commit()
    return _contract_out(db, c)


def due_contracts(db: Session, tenant_id: int, dias: int = 0) -> list[MaintenanceContract]:
    """Contratos a los que ya les toca (o les toca dentro de N dias)."""
    limite = date.today() + timedelta(days=dias)
    return [
        c
        for c in db.scalars(select(MaintenanceContract).where(MaintenanceContract.tenant_id == tenant_id, MaintenanceContract.active))
        if c.next_date and c.next_date <= limite and (not c.end_date or c.end_date >= date.today())
    ]


def open_maintenance(db: Session, c: MaintenanceContract) -> SupportTicket:
    """Abre el ticket del mantenimiento y programa el siguiente. Lo usa el worker y el boton "hacer ahora"."""
    number, _ = next_number(db, c.tenant_id, "TCK")
    t = SupportTicket(
        tenant_id=c.tenant_id,
        number=number,
        customer_id=c.customer_id,
        project_id=c.project_id,
        contract_id=c.id,
        kind="mantenimiento",
        subject=f"Mantenimiento preventivo · {c.name}",
        description=c.scope,
        priority="media",
        status="nuevo",
        billable=d(c.amount) > 0,
        amount=c.amount,
        due_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(t)
    base = c.next_date or date.today()
    c.last_done = base
    mes = base.month - 1 + c.every_months
    c.next_date = date(base.year + mes // 12, mes % 12 + 1, min(base.day, 28))
    return t


@router.post("/contracts/{cid}/ticket", status_code=201)
def contract_ticket(cid: int, p: Principal = Depends(require("support_desk", "crear")), db: Session = Depends(get_db)):
    c = db.get(MaintenanceContract, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Contrato no encontrado")
    t = open_maintenance(db, c)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "maintenance", "support_ticket", t.id, {"contrato": c.number}, ip=p.ip)
    db.commit()
    return _out(db, t)


# ---------- Comisiones ----------
class RuleIn(BaseModel):
    user_id: int | None = None
    name: str = Field("Comisión", max_length=120)
    base: str = Field("venta", pattern="^(venta|margen)$")
    percent: Decimal = Field(Decimal(0), ge=0, le=100)
    active: bool = True
    notes: str | None = None


@router.get("/commissions/rules")
def rules(p: Principal = Depends(require("commissions", "ver")), db: Session = Depends(get_db)):
    out = []
    for r in db.scalars(select(CommissionRule).where(CommissionRule.tenant_id == p.tenant.id).order_by(CommissionRule.id)):
        u = db.get(User, r.user_id) if r.user_id else None
        out.append(
            {
                "id": r.id,
                "user_id": r.user_id,
                "user": u.full_name if u else "Todos",
                "name": r.name,
                "base": r.base,
                "percent": r.percent,
                "active": r.active,
                "notes": r.notes,
            }
        )
    return out


@router.post("/commissions/rules", status_code=201)
def rule_create(data: RuleIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    r = CommissionRule(tenant_id=p.tenant.id, **data.model_dump())
    db.add(r)
    db.commit()
    return {"id": r.id}


@router.put("/commissions/rules/{rid}")
def rule_update(rid: int, data: RuleIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    r = db.get(CommissionRule, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Regla no encontrada")
    for k, v in data.model_dump().items():
        setattr(r, k, v)
    db.commit()
    return {"id": r.id}


def _fecha(v) -> date:
    """paid_at es date en un pago manual y datetime cuando viene de la pasarela."""
    if isinstance(v, datetime):
        return local_date(v) or date.today()
    return v or date.today()


def rule_for(db: Session, tenant_id: int, user_id: int | None) -> CommissionRule | None:
    """La regla del vendedor manda sobre la general."""
    if user_id:
        propia = db.scalar(select(CommissionRule).where(CommissionRule.tenant_id == tenant_id, CommissionRule.user_id == user_id, CommissionRule.active))
        if propia:
            return propia
    return db.scalar(select(CommissionRule).where(CommissionRule.tenant_id == tenant_id, CommissionRule.user_id.is_(None), CommissionRule.active))


def accrue_for_payment(db: Session, payment: Payment) -> Commission | None:
    """Comision de un pago confirmado. Andres: "venta cobrada -> comision correspondiente", asi que la
    comision nace con la plata en la cuenta y en proporcion a lo cobrado, no al facturar."""
    from ..services import pricing
    from ..services.documents import today_fx

    inv = db.get(Invoice, payment.invoice_id) if payment.invoice_id else None
    if inv is None or inv.status == "anulada" or not inv.created_by:
        return None
    if db.scalar(select(Commission).where(Commission.payment_id == payment.id)):
        return None  # ya se devengo: un webhook reintentado no comisiona dos veces
    regla = rule_for(db, inv.tenant_id, inv.created_by)
    if not regla or d(regla.percent) <= 0:
        return None
    cobrado = d(payment.amount)
    if regla.base == "margen":
        fx = today_fx(db, "USD")[0]
        costo = Decimal(0)
        for ln in inv.lines:
            prod = db.get(Product, ln.product_id) if ln.product_id else None
            if prod is not None and prod.cost is not None:
                costo += pricing.cost_in(db, prod, inv.currency, fx) * d(ln.quantity)
        neto = d(inv.subtotal) - d(inv.discount_total)
        if neto <= 0:
            return None
        proporcion = cobrado / d(inv.total) if d(inv.total) > 0 else Decimal(0)
        base_amount = (neto - costo) * proporcion
    else:
        base_amount = cobrado
    if base_amount <= 0:
        return None
    c = Commission(
        tenant_id=inv.tenant_id,
        user_id=inv.created_by,
        invoice_id=inv.id,
        payment_id=payment.id,
        rule_id=regla.id,
        base=regla.base,
        base_amount=base_amount.quantize(Decimal("0.01")),
        percent=regla.percent,
        amount=(base_amount * d(regla.percent) / 100).quantize(Decimal("0.01")),
        currency=inv.currency,
        earned_on=_fecha(payment.paid_at),
    )
    db.add(c)
    return c


@router.get("/commissions")
def commissions(
    status: str | None = None,
    user_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    p: Principal = Depends(require("commissions", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(Commission).where(Commission.tenant_id == p.tenant.id)
    if not p.can("commissions", "ver_todo"):
        stmt = stmt.where(Commission.user_id == p.user.id)  # cada quien ve la suya
    elif user_id:
        stmt = stmt.where(Commission.user_id == user_id)
    if status:
        stmt = stmt.where(Commission.status == status)
    if from_:
        stmt = stmt.where(Commission.earned_on >= from_)
    if to:
        stmt = stmt.where(Commission.earned_on <= to)
    rows = db.scalars(stmt.order_by(Commission.earned_on.desc(), Commission.id.desc()).limit(300)).all()
    out = []
    for c in rows:
        u = db.get(User, c.user_id)
        inv = db.get(Invoice, c.invoice_id) if c.invoice_id else None
        out.append(
            {
                "id": c.id,
                "user_id": c.user_id,
                "user": u.full_name if u else None,
                "invoice": inv.number if inv else None,
                "invoice_id": c.invoice_id,
                "base": c.base,
                "base_amount": c.base_amount,
                "percent": c.percent,
                "amount": c.amount,
                "currency": c.currency,
                "earned_on": c.earned_on,
                "status": c.status,
                "paid_on": c.paid_on,
            }
        )
    return {
        "rows": out,
        "totals": {
            "pendiente": sum((d(c.amount) for c in rows if c.status == "pendiente"), Decimal(0)),
            "aprobada": sum((d(c.amount) for c in rows if c.status == "aprobada"), Decimal(0)),
            "pagada": sum((d(c.amount) for c in rows if c.status == "pagada"), Decimal(0)),
        },
    }


class CommissionStatusIn(BaseModel):
    ids: list[int] = Field(min_length=1)
    status: str = Field(pattern="^(pendiente|aprobada|pagada|anulada)$")


@router.patch("/commissions")
def commission_status(data: CommissionStatusIn, p: Principal = Depends(require("commissions", "aprobar")), db: Session = Depends(get_db)):
    n = 0
    for c in db.scalars(select(Commission).where(Commission.tenant_id == p.tenant.id, Commission.id.in_(data.ids))):
        c.status = data.status
        c.paid_on = date.today() if data.status == "pagada" else None
        n += 1
    audit(db, p.tenant.id, p.user.id, "status", "commission", data.ids[0], {"status": data.status, "n": n}, ip=p.ip)
    db.commit()
    return {"actualizadas": n}
