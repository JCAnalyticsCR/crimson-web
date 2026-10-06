"""Partes y aliados de una oportunidad: salida JSON, pendientes de costo y nombres para "Aportado por".

Regla de costos (Andres): la vendedora nunca ve el costo del proveedor. El monto, la vigencia, las exclusiones y
los adjuntos de una solicitud de costo solo salen a quien tiene catalog.costos; los demas ven que se pidio,
a quien, para cuando y en que estado va.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import AllyCostRequest, AllyParticipation, Customer, Opportunity, User
from ..models.allies import COST_OPEN
from .archive import live


def is_overdue(r: AllyCostRequest, today: date | None = None) -> bool:
    """Vencida: se pidio con fecha limite y no ha llegado."""
    return r.status == "solicitado" and r.due_date is not None and r.due_date < (today or date.today())


def request_out(db: Session, r: AllyCostRequest, p: Principal) -> dict:
    who = db.get(User, r.responsible_id) if r.responsible_id else None
    out = {
        "id": r.id,
        "participation_id": r.participation_id,
        "opportunity_id": r.opportunity_id,
        "what": r.what,
        "responsible_id": r.responsible_id,
        "responsible": who.full_name if who else None,
        "due_date": r.due_date,
        "status": r.status,
        "pending": r.status in COST_OPEN,
        "overdue": is_overdue(r),
        "costs_visible": p.sees_costs,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }
    if p.sees_costs:
        out.update(
            amount=r.amount,
            currency=r.currency,
            valid_until=r.valid_until,
            exclusions=r.exclusions,
            attachments=r.attachments or [],
        )
    return out


def ally_out(db: Session, a: AllyParticipation, p: Principal, with_requests: bool = True) -> dict:
    out = {
        "id": a.id,
        "opportunity_id": a.opportunity_id,
        "project_id": a.project_id,
        "customer_id": a.customer_id,
        "supplier_id": a.supplier_id,
        "company_kind": "customer" if a.customer_id else "supplier" if a.supplier_id else None,
        "name": a.name,
        "role": a.role,
        "scope": a.scope,
        "scope_lines": a.scope_lines or [],
        "contacts": a.contacts or {},
    }
    if with_requests:
        out["requests"] = [request_out(db, r, p) for r in a.requests]
    return out


def allies_of(db: Session, tenant_id: int, opportunity_id: int | None = None, project_id: int | None = None) -> list[AllyParticipation]:
    stmt = select(AllyParticipation).where(AllyParticipation.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(AllyParticipation.project_id == project_id)
    else:
        stmt = stmt.where(AllyParticipation.opportunity_id == opportunity_id)
    return list(db.scalars(stmt.order_by(AllyParticipation.id)))


def pending_count_for(db: Session, opportunity_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(AllyCostRequest).where(AllyCostRequest.opportunity_id == opportunity_id, AllyCostRequest.status.in_(COST_OPEN))
        )
        or 0
    )


def cost_pending_where(p: Principal) -> list:
    """Solicitudes de costo pendientes (solicitadas o recibidas sin aprobar) de oportunidades abiertas.

    Una sola definicion para la tarjeta del inicio y la lista de Oportunidades -> Mis pendientes, asi no pueden decir
    numeros distintos. Alcance: toda la empresa si ve todo el embudo y el inicio de empresa; quien ve todo pero con
    inicio personal, las de sus oportunidades y las que le tocan; el vendedor, las de sus oportunidades."""
    from ..routers.pipeline import OPEN_STATES  # import local: el router importa este servicio

    tid = p.tenant.id
    abiertas = select(Opportunity.id).where(Opportunity.tenant_id == tid, Opportunity.status.in_(OPEN_STATES), live(Opportunity))
    out = [AllyCostRequest.tenant_id == tid, AllyCostRequest.status.in_(COST_OPEN), AllyCostRequest.opportunity_id.in_(abiertas)]
    mias = AllyCostRequest.opportunity_id.in_(select(Opportunity.id).where(Opportunity.tenant_id == tid, Opportunity.owner_id == p.user.id))
    if p.can("crm_pipeline", "ver_todo") and p.can("dashboard", "empresa"):
        return out
    if p.can("crm_pipeline", "ver_todo"):
        out.append(or_(mias, AllyCostRequest.responsible_id == p.user.id))
    else:
        out.append(mias)
    return out


def cost_pending_count(db: Session, p: Principal) -> int:
    return db.scalar(select(func.count()).select_from(AllyCostRequest).where(*cost_pending_where(p))) or 0


def party_names(db: Session, o: Opportunity) -> list[dict]:
    """Empresas que pueden aportar equipo en la cotizacion: contratante, cliente final y aliados (sin repetir)."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: str | None, role: str) -> None:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "role": role})

    c = db.get(Customer, o.customer_id) if o.customer_id else None
    add(c.name if c else None, "contratante")
    fin = db.get(Customer, o.end_customer_id) if o.end_customer_id else None
    add(fin.name if fin else None, "cliente_final")
    for a in allies_of(db, o.tenant_id, opportunity_id=o.id):
        add(a.name, a.role)
    return out
