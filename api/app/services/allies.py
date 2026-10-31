"""Partes y aliados de una oportunidad: salida JSON, pendientes de costo, acuerdos y nombres para "Aportado por".

Regla de costos (Andres): la vendedora nunca ve el costo del proveedor. El monto, la vigencia, las condiciones, las
exclusiones, el desglose y los adjuntos de la OFERTA de un aliado (y sus acuerdos) solo salen a quien tiene
catalog.costos; los demas ven la peticion completa (que se pidio, alcance, partidas, sitio, a quien, para cuando, si el
aliado la vio y en que estado va).

Revision 2 (puntos 7-11): la peticion y la oferta son dos momentos (borrador -> enviado -> visto -> respondido ->
aceptado | rechazado; "vencido" se calcula); aceptar guarda el acuerdo como version (AllyAgreement) y cada cambio
posterior crea otra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import AllyAgreement, AllyCompany, AllyCostRequest, AllyParticipation, Customer, Opportunity, User
from ..models.allies import COST_LEGACY, COST_OPEN, COST_STATES, COST_WAITING
from .archive import live

STATE_LABEL = {
    "borrador": "Borrador",
    "enviado": "Enviada",
    "visto": "Vista por el aliado",
    "respondido": "Respondida",
    "aceptado": "Aceptada",
    "rechazado": "Rechazada",
    "vencido": "Vencida",
}
CHANNEL_LABEL = {"portal": "por su portal de aliados", "correo": "por correo", "whatsapp": "por WhatsApp", "telefono": "por teléfono"}


def norm_status(v: str | None) -> str | None:
    """Estado que llega a la API: los de antes de la revision 2 (solicitado, recibido, aprobado) son sinonimos. "vencido"
    no se guarda (se calcula): como entrada significa "dejalo como esta" (None)."""
    if v is None or v == "vencido":
        return None
    v = COST_LEGACY.get(v, v)
    if v not in COST_STATES:
        raise HTTPException(422, "Estado de la solicitud desconocido")
    return v


def _today() -> date:
    from .invoice_states import cr_today

    return cr_today()


def is_overdue(r: AllyCostRequest, today: date | None = None) -> bool:
    """Vencida: se envio con fecha limite y el aliado no respondio a tiempo."""
    return r.status in COST_WAITING and r.due_date is not None and r.due_date < (today or _today())


def effective_status(r: AllyCostRequest, today: date | None = None) -> str:
    return "vencido" if is_overdue(r, today) else r.status


def overdue_where(today: date | None = None):
    """La misma regla que is_overdue, para filtrar en SQL."""
    return and_(AllyCostRequest.status.in_(COST_WAITING), AllyCostRequest.due_date.is_not(None), AllyCostRequest.due_date < (today or _today()))


def _name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return u.full_name if u else None


def _num(v) -> str | None:
    return None if v is None else str(v)


def delivery(db: Session, r: AllyCostRequest, a: AllyParticipation | None = None) -> dict:
    """Punto 9: a que aliado se envio, si la vio y cuando, y como respondera."""
    a = a or r.participation
    comp = db.get(AllyCompany, a.ally_company_id) if a and a.ally_company_id else None
    portal = bool(comp and a.visible_to_ally)
    canal = r.response_channel or "portal"
    if canal == "portal" and not portal:
        canal = "correo"  # sin portal (cliente/proveedor viejo u oculta) no puede responder por ahi
    return {
        "to": a.name if a else None,
        "to_portal": portal,
        "sent_at": r.sent_at,
        "seen_at": r.seen_at,
        "seen_by": _name(db, r.seen_by),
        "channel": canal,
        "channel_label": CHANNEL_LABEL.get(canal, canal),
    }


def request_out(db: Session, r: AllyCostRequest, p: Principal) -> dict:
    st = effective_status(r)
    out = {
        "id": r.id,
        "participation_id": r.participation_id,
        "opportunity_id": r.opportunity_id,
        "what": r.what,
        # peticion (sin montos): la ve todo el que ve la oportunidad
        "scope": r.scope,
        "site": r.site,
        "inclusions": r.inclusions,
        "req_exclusions": r.req_exclusions,
        "req_lines": r.req_lines or [],
        "req_attachments": r.req_attachments or [],
        "responsible_id": r.responsible_id,
        "responsible": _name(db, r.responsible_id),  # "Responsable interno de seguimiento"
        "due_date": r.due_date,
        "status": st,
        "stored_status": r.status,
        "status_label": STATE_LABEL.get(st, st),
        "pending": r.status in COST_OPEN,
        "overdue": st == "vencido",
        "costs_visible": p.sees_costs,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "delivery": delivery(db, r),
        # respuesta del aliado desde su portal: quien y cuando (sin montos) y si la envio por iniciativa
        "source": r.source or "crimson",
        "responded_at": r.responded_at,
        "responded_by": _name(db, r.responded_by),
        "has_offer": r.amount is not None,
        "decided_at": r.decided_at,
        "decided_by": _name(db, r.decided_by),
    }
    if p.sees_costs:
        out.update(
            amount=r.amount,
            currency=r.currency,
            valid_until=r.valid_until,
            conditions=r.conditions,
            exclusions=r.exclusions,
            attachments=r.attachments or [],
            lines=r.lines or [],
            decision_note=r.decision_note,
            agreements=[agreement_out(db, g) for g in agreements_of(db, request_id=r.id)],
        )
    return out


def ally_out(db: Session, a: AllyParticipation, p: Principal, with_requests: bool = True, opp: Opportunity | None = None) -> dict:
    comp = db.get(AllyCompany, a.ally_company_id) if a.ally_company_id else None
    o = opp or db.get(Opportunity, a.opportunity_id)
    payer = bool(o and o.customer_id and comp and comp.customer_id == o.customer_id) or bool(o and o.customer_id and a.customer_id == o.customer_id)
    out = {
        "id": a.id,
        "opportunity_id": a.opportunity_id,
        "project_id": a.project_id,
        "ally_company_id": a.ally_company_id,
        "customer_id": a.customer_id,
        "supplier_id": a.supplier_id,
        "company_kind": "ally" if a.ally_company_id else "customer" if a.customer_id else "supplier" if a.supplier_id else None,
        "ally_state": comp.state if comp else None,
        "visible_to_ally": a.visible_to_ally,
        "name": a.name,
        "role": a.role,
        # A2: relaciones concretas (lo que la interfaz muestra en vez del "Rol")
        "is_origin": bool(a.is_origin),
        "is_executor": bool(a.is_executor),
        "is_payer": payer,
        "relations": relation_labels(a, payer),
        "scope": a.scope,
        "scope_lines": a.scope_lines or [],
        "contacts": a.contacts or {},
    }
    if with_requests:
        out["requests"] = [request_out(db, r, p) for r in a.requests]
    return out


def relation_labels(a: AllyParticipation, payer: bool) -> list[str]:
    out = []
    if a.is_origin:
        out.append("Aliado comercial")
    if a.is_executor:
        out.append("Responsable de ejecución")
    if payer:
        out.append("Le paga a Crimson")
    if not out:
        out.append("Participa")
    return out


def allies_of(db: Session, tenant_id: int, opportunity_id: int | None = None, project_id: int | None = None) -> list[AllyParticipation]:
    stmt = select(AllyParticipation).where(AllyParticipation.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(AllyParticipation.project_id == project_id)
    else:
        stmt = stmt.where(AllyParticipation.opportunity_id == opportunity_id)
    return list(db.scalars(stmt.order_by(AllyParticipation.id)))


def relations_out(db: Session, o: Opportunity) -> dict:
    """A2: Cliente final, Aliado comercial (quien origino), Responsable de ejecucion y Quien le paga a Crimson. Sin
    "Rol" general: una misma empresa puede ser aliado aqui y el cliente al que Crimson le factura (sin otra ficha)."""
    parts = allies_of(db, o.tenant_id, opportunity_id=o.id)
    pay = db.get(Customer, o.customer_id) if o.customer_id else None
    fin = db.get(Customer, o.end_customer_id) if o.end_customer_id else None
    origin = next((a for a in parts if a.is_origin), None)
    execu = next((a for a in parts if a.is_executor), None)
    payer_part = None
    if pay is not None:
        for a in parts:
            comp = db.get(AllyCompany, a.ally_company_id) if a.ally_company_id else None
            if (comp and comp.customer_id == pay.id) or a.customer_id == pay.id:
                payer_part = a
                break
    final = fin or pay
    return {
        "end_customer": {"id": final.id, "name": final.name}
        if final
        else ({"id": None, "name": (o.contact or {}).get("name")} if (o.contact or {}).get("name") else None),
        "origin": {"participation_id": origin.id, "name": origin.name} if origin else None,
        "executor": {"kind": "aliado", "participation_id": execu.id, "name": execu.name}
        if execu
        else {"kind": "crimson", "participation_id": None, "name": "Crimson"},
        "payer": {
            "customer_id": pay.id if pay else None,
            "name": pay.name if pay else None,
            "participation_id": payer_part.id if payer_part else None,
            "is_end_customer": bool(pay and (fin is None or fin.id == pay.id)),
        },
        "billing_line": (f"Crimson le factura a {pay.name}" + (f" (por la obra de {fin.name})" if fin and fin.id != pay.id else ""))
        if pay
        else "Todavía sin definir a quién le factura Crimson",
        "open_costs": bool(o.ally_open_costs),
    }


def pending_count_for(db: Session, opportunity_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(AllyCostRequest).where(AllyCostRequest.opportunity_id == opportunity_id, AllyCostRequest.status.in_(COST_OPEN))
        )
        or 0
    )


def cost_pending_where(p: Principal) -> list:
    """Solicitudes de precio pendientes (borrador sin enviar, enviadas o vistas sin respuesta, respondidas sin decidir)
    de oportunidades abiertas.

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


# ---------- Acuerdos aceptados con versiones (punto 11) ----------
def agreements_of(db: Session, *, request_id: int | None = None, opportunity_id: int | None = None, participation_ids=None) -> list[AllyAgreement]:
    q = select(AllyAgreement)
    if request_id is not None:
        q = q.where(AllyAgreement.request_id == request_id)
    if opportunity_id is not None:
        q = q.where(AllyAgreement.opportunity_id == opportunity_id)
    if participation_ids is not None:
        q = q.where(AllyAgreement.participation_id.in_(list(participation_ids)))
    return list(db.scalars(q.order_by(AllyAgreement.request_id, AllyAgreement.version)))


def current_agreement(db: Session, request_id: int) -> AllyAgreement | None:
    return db.scalar(select(AllyAgreement).where(AllyAgreement.request_id == request_id).order_by(AllyAgreement.version.desc()).limit(1))


def agreement_out(db: Session, g: AllyAgreement, *, with_lines: bool = True) -> dict:
    return {
        "id": g.id,
        "request_id": g.request_id,
        "participation_id": g.participation_id,
        "opportunity_id": g.opportunity_id,
        "version": g.version,
        "status": g.status,
        "what": g.what,
        "scope": g.scope,
        "amount": g.amount,
        "currency": g.currency,
        "valid_until": g.valid_until,
        "conditions": g.conditions,
        "exclusions": g.exclusions,
        "lines": (g.lines or []) if with_lines else None,
        "shared": bool(g.shared),
        "changes": g.changes or [],
        "note": g.note,
        "created_by": _name(db, g.created_by),
        "created_at": g.created_at,
    }


AGREEMENT_FIELDS = (("amount", "precio"), ("currency", "moneda"), ("valid_until", "vigencia"), ("conditions", "condiciones"), ("exclusions", "exclusiones"))


def _fmt(k: str, v) -> str:
    if v is None or v == "":
        return "—"
    if k == "valid_until" and isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if k == "amount":
        return f"{Decimal(str(v)):,.2f}".replace(",", " ").replace(".", ",")  # 288 000,00 (es-CR)
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "…"


def _same(k: str, a, b) -> bool:
    if k == "amount":
        return (a is None and b is None) or (a is not None and b is not None and Decimal(str(a)) == Decimal(str(b)))
    return (a or None) == (b or None)


def accept_offer(db: Session, r: AllyCostRequest, user_id: int, *, shared: bool = True, note: str | None = None) -> AllyAgreement:
    """Aceptar la oferta: la foto de lo que se acepto queda como version 1 (o como la siguiente si ya habia un acuerdo
    que se anulo y se vuelve a aceptar)."""
    if r.amount is None:
        raise HTTPException(409, "Esta solicitud todavía no tiene una oferta con precio")
    prev = current_agreement(db, r.id)
    if prev is not None and prev.status == "vigente":
        return prev
    g = AllyAgreement(
        tenant_id=r.tenant_id,
        opportunity_id=r.opportunity_id,
        participation_id=r.participation_id,
        request_id=r.id,
        version=(prev.version + 1) if prev else 1,
        status="vigente",
        what=r.what,
        scope=r.scope,
        amount=r.amount,
        currency=r.currency or "CRC",
        valid_until=r.valid_until,
        conditions=r.conditions,
        exclusions=r.exclusions,
        lines=list(r.lines or []),
        shared=shared if prev is None else bool(prev.shared),
        changes=["oferta aceptada"] if prev is None else ["aceptada de nuevo"],
        note=(note or None),
        created_by=user_id,
    )
    db.add(g)
    db.flush()
    return g


def new_agreement_version(
    db: Session, r: AllyCostRequest, user_id: int, values: dict, *, note: str | None = None, shared: bool | None = None
) -> AllyAgreement | None:
    """Cambio posterior a la aceptacion: nueva version con que cambio. La solicitud refleja la version vigente (el
    precio aprobado es el pago acordado del aliado: comisiones y la rentabilidad del proyecto lo leen de ahi).
    Devuelve None si no cambio nada."""
    prev = current_agreement(db, r.id)
    if prev is None or prev.status != "vigente":
        raise HTTPException(409, "Esta solicitud no tiene un acuerdo vigente")
    cambios = []
    nuevo = {}
    for k, label in AGREEMENT_FIELDS:
        v = values.get(k, getattr(prev, k)) if k in values else getattr(prev, k)
        nuevo[k] = v
        if not _same(k, getattr(prev, k), v):
            cambios.append(f"{label}: {_fmt(k, getattr(prev, k))} → {_fmt(k, v)}")
    share = prev.shared if shared is None else shared
    if share != prev.shared:
        cambios.append("compartido con los aliados" if share else "ya no se comparte con los aliados")
    if not cambios:
        return None
    if nuevo["amount"] is None:
        raise HTTPException(422, "El acuerdo necesita un precio")
    prev.status = "reemplazado"
    g = AllyAgreement(
        tenant_id=r.tenant_id,
        opportunity_id=r.opportunity_id,
        participation_id=r.participation_id,
        request_id=r.id,
        version=prev.version + 1,
        status="vigente",
        what=r.what,
        scope=r.scope,
        amount=nuevo["amount"],
        currency=nuevo["currency"] or prev.currency,
        valid_until=nuevo["valid_until"],
        conditions=nuevo["conditions"],
        exclusions=nuevo["exclusions"],
        lines=list(prev.lines or []),
        shared=share,
        changes=cambios,
        note=(note or None),
        created_by=user_id,
    )
    db.add(g)
    r.amount, r.currency, r.valid_until, r.conditions, r.exclusions = g.amount, g.currency, g.valid_until, g.conditions, g.exclusions
    db.flush()
    return g


def void_agreement(db: Session, r: AllyCostRequest) -> bool:
    g = current_agreement(db, r.id)
    if g is not None and g.status == "vigente":
        g.status = "anulado"
        return True
    return False


def mark_decided(r: AllyCostRequest, user_id: int, note: str | None = None) -> None:
    r.decided_at, r.decided_by = datetime.now(UTC), user_id
    if note is not None:
        r.decision_note = note[:400] or None


def waiting_for_ally_where() -> list:
    """Enviadas o vistas (incluidas las vencidas): lo que el aliado tiene por responder."""
    return [AllyCostRequest.status.in_(COST_WAITING)]


def not_draft():
    """El aliado nunca ve un borrador."""
    return not_(AllyCostRequest.status == "borrador")
