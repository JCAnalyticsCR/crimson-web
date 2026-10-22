"""Oportunidades: el embudo comercial antes de la cotizacion.

La vendedora ve las suyas; administracion y gerencia ven todas. Cada oportunidad lleva su proxima accion con fecha,
que es lo que evita que una cotizacion se enfrie sin que nadie la note (el worker avisa a los 5 dias)."""

from __future__ import annotations

import html
from datetime import UTC, date, datetime, timedelta
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
from ..services.email_layout import cta
from ..services.fx import rate_or_none
from ..services.mail import notify_roles
from ..services.ownership import opportunity_seller
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
    # Sin "ver_todo" (el vendedor) solo las suyas. Las que no tienen responsable (las solicitudes que entran por la
    # tienda) las ve y las asigna quien ve todo el embudo: antes cualquier vendedor las abria, editaba y cotizaba.
    if not p.can("crm_pipeline", "ver_todo") and o.owner_id != p.user.id:
        raise HTTPException(404, "Oportunidad no encontrada")
    return o


def _out(db: Session, o: Opportunity) -> dict:
    from ..services.allies import pending_count_for

    c = db.get(Customer, o.customer_id) if o.customer_id else None
    fin = db.get(Customer, o.end_customer_id) if o.end_customer_id else None
    owner = db.get(User, o.owner_id) if o.owner_id else None
    return {
        "id": o.id,
        "number": o.number,
        "title": o.title,
        "archived_at": o.archived_at,
        "trashed_at": o.trashed_at,
        "customer_id": o.customer_id,
        # partes: customer = contratante (a quien se cotiza y factura); end_customer = quien recibe la solucion
        "customer": c.name if c else (o.contact or {}).get("name"),
        "end_customer_id": o.end_customer_id,
        "end_customer": fin.name if fin else None,
        "site": o.site,
        "ally_pending": pending_count_for(db, o.id),
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
        "closed_at": o.closed_at,
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
    customer_id: int | None = None  # contratante
    end_customer_id: int | None = None  # cliente final (opcional)
    site: str | None = Field(None, max_length=300)
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


class _ToCompany:
    """Montos de oportunidades a la moneda de la empresa con el tipo de cambio del dia (services/fx; las oportunidades
    no guardan tipo de cambio). Cachea el tipo por moneda dentro de una misma respuesta. Lo convertido se marca (el
    portal muestra "≈"). Sin tipo de cambio cargado ese monto no se suma (nunca un dolar como un colon) y queda
    `missing`; el embudo sigue funcionando con lo demas."""

    def __init__(self, db: Session, tenant) -> None:
        self.db, self.base, self.rates, self.converted, self.missing = db, tenant.default_currency or "CRC", {}, False, False

    def _rate(self, cur: str) -> Decimal | None:  # colones por unidad de esa moneda (None: no hay tasa)
        if cur not in self.rates:
            self.rates[cur] = rate_or_none(self.db, cur)
        return self.rates[cur]

    def __call__(self, amount, currency: str | None) -> Decimal:
        cur = currency or self.base
        if cur == self.base:
            return d(amount)
        self.converted = True
        r = self._rate(cur)
        rb = self._rate(self.base) if self.base != "CRC" else Decimal(1)
        if r is None or rb is None:
            self.missing = True
            return Decimal(0)
        return d(amount) * r / rb


def _open_rows(db: Session, p: Principal, owner_id: int | None = None, kind: str | None = None) -> list[Opportunity]:
    """Oportunidades abiertas con el MISMO alcance que la lista (_scope): el embudo y el inicio cuentan esto."""
    stmt = select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.status.in_(OPEN_STATES), live(Opportunity))
    stmt = _scope(stmt, p, owner_id)
    if kind:
        stmt = stmt.where(Opportunity.kind == kind)
    return list(db.scalars(stmt).all())


def _closed_this_month(db: Session, p: Principal, status: str, owner_id: int | None = None, kind: str | None = None) -> list[Opportunity]:
    """Ganadas (o perdidas) desde el primer dia del mes en hora de Costa Rica, con el mismo alcance."""
    hoy = datetime.now(CR)
    m0 = datetime(hoy.year, hoy.month, 1, tzinfo=CR).astimezone(UTC)
    stmt = select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.status == status, Opportunity.closed_at >= m0, live(Opportunity))
    stmt = _scope(stmt, p, owner_id)
    if kind:
        stmt = stmt.where(Opportunity.kind == kind)
    return list(db.scalars(stmt).all())


def _money(v: Decimal) -> Decimal:
    return d(v).quantize(Decimal("0.01"))


def pipeline_summary(db: Session, p: Principal, owner_id: int | None = None, kind: str | None = None) -> dict:
    """Totales del embudo con el MISMO alcance que la lista (_scope) y lo vendido este mes. Montos a la moneda de la
    empresa con el tipo de cambio del dia. Lo usan el inicio del vendedor (mi_embudo) y, con las mismas piezas, el
    tablero: un numero y el otro no pueden diferir."""
    conv = _ToCompany(db, p.tenant)
    rows = _open_rows(db, p, owner_id, kind)
    ganadas = _closed_this_month(db, p, "ganada", owner_id, kind)
    perdidas = _closed_this_month(db, p, "perdida", owner_id, kind)
    montos = [(conv(o.amount, o.currency), o.probability) for o in rows]
    return {
        "abiertas": len(rows),
        "monto": _money(sum((v for v, _ in montos), Decimal(0))),
        "ponderado": _money(sum((v * pr / 100 for v, pr in montos), Decimal(0))),
        "ganadas_mes": len(ganadas),
        "ganado_mes": _money(sum((conv(o.amount, o.currency) for o in ganadas), Decimal(0))),
        "perdidas_mes": len(perdidas),
        "moneda": conv.base,
        "convertido": conv.converted,
        "sin_tipo_de_cambio": conv.missing,  # algun monto en otra moneda no se sumo (falta su tasa): el portal avisa
    }


@router.get("/opportunities/board")
def board(
    owner_id: int | None = None,
    kind: str | None = Query(None, pattern="^(venta|proyecto)$"),
    p: Principal = Depends(require("crm_pipeline", "ver")),
    db: Session = Depends(get_db),
):
    """Embudo por estado: monto total y monto ponderado por probabilidad (en la moneda de la empresa), y lo ganado
    este mes. El vendedor solo suma lo suyo: filas y totales salen del mismo alcance (_scope)."""
    conv = _ToCompany(db, p.tenant)
    rows = _open_rows(db, p, owner_id, kind)
    en_base = {o.id: conv(o.amount, o.currency) for o in rows}
    cols = []
    for st in OPEN_STATES:
        items = [o for o in rows if o.status == st]
        cols.append(
            {
                "status": st,
                "count": len(items),
                "amount": _money(sum((en_base[o.id] for o in items), Decimal(0))),
                "weighted": _money(sum((en_base[o.id] * o.probability / 100 for o in items), Decimal(0))),
                "items": [_out(db, o) for o in items][:25],
            }
        )
    ganadas = _closed_this_month(db, p, "ganada", owner_id, kind)
    return {
        "columns": cols,
        "total": _money(sum(en_base.values(), Decimal(0))),
        "weighted": _money(sum((en_base[o.id] * o.probability / 100 for o in rows), Decimal(0))),
        "won_month": {"count": len(ganadas), "amount": _money(sum((conv(o.amount, o.currency) for o in ganadas), Decimal(0)))},
        "currency": conv.base,
        "converted": conv.converted,
        "sin_tipo_de_cambio": conv.missing,  # algun monto en otra moneda no se sumo (falta su tasa): el portal avisa
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

    _check_parties(db, p, data)
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
    from ..services import allies as allysvc

    out = _out(db, o)
    out["allies"] = [allysvc.ally_out(db, a, p) for a in allysvc.allies_of(db, p.tenant.id, opportunity_id=o.id)]
    out["parties"] = allysvc.party_names(db, o)
    out["sees_costs"] = p.sees_costs
    out["surveys"] = [
        {"id": s.id, "number": s.number, "kind": s.kind, "status": s.status}
        for s in db.scalars(select(Survey).where(Survey.opportunity_id == o.id, live(Survey)))
    ]
    from .fieldwork import pending_survey_request  # import local: fieldwork ya importa de aqui

    # «Solicitar levantamiento» ya pedido y sin atender (en cualquier etapa abierta: el portal no lo infiere del estado)
    req = pending_survey_request(db, o)
    out["survey_request"] = {"at": req.at} if req else None
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
    _check_parties(db, p, data)
    before = o.status
    partes = _parties_text(db, o)
    # el responsable no se cambia por accidente al editar: sin dato queda el que estaba, y cambiarlo pasa por _reassign
    new_owner = data.owner_id
    for k, v in data.model_dump(exclude={"owner_id"}).items():
        setattr(o, k, v)
    if _parties_text(db, o) != partes:
        _log(o, p.user.full_name, f"Partes: {_parties_text(db, o)}")
    if o.status != before:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": before, "a": o.status}, ip=p.ip)
    if new_owner is not None and new_owner != o.owner_id:
        _reassign(db, p, o, new_owner)
    db.commit()
    return _out(db, o)


def _check_parties(db: Session, p: Principal, data: OpportunityIn) -> None:
    """Contratante y cliente final deben ser clientes de la empresa; si son el mismo, no hay cliente final aparte."""
    for cid in (data.customer_id, data.end_customer_id):
        if cid:
            c = db.get(Customer, cid)
            if not c or c.tenant_id != p.tenant.id:
                raise HTTPException(404, "Cliente no encontrado")
    if data.end_customer_id and data.end_customer_id == data.customer_id:
        data.end_customer_id = None
    data.site = (data.site or "").strip() or None


def _parties_text(db: Session, o: Opportunity) -> str:
    """"Nodo Latam → para Yobel · Sitio X": la misma frase que la tarjeta del embudo."""
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    fin = db.get(Customer, o.end_customer_id) if o.end_customer_id else None
    txt = c.name if c else "sin contratante"
    if fin:
        txt += f" → para {fin.name}"
    if o.site:
        txt += f" · {o.site}"
    return txt


def _check_owner(db: Session, p: Principal, uid: int) -> User:
    from ..models import TenantUser

    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid, TenantUser.active))
    if not m:
        raise HTTPException(422, "Ese responsable no es un usuario activo de la empresa")
    # una oportunidad o una solicitud de costo la persigue alguien de Crimson: nunca un cliente ni un aliado
    from ..core.deps import EXTERNAL_ROLES

    if m.customer_id is not None or m.ally_company_id is not None or m.role_code in EXTERNAL_ROLES:
        raise HTTPException(422, "El responsable tiene que ser del equipo de Crimson")
    return m.user


def _reassign(db: Session, p: Principal, o: Opportunity, uid: int) -> None:
    """Cambiar el responsable es de quien ve todo el embudo (gerencia/administracion); queda en bitacora y auditoria."""
    if not p.can("crm_pipeline", "ver_todo"):
        raise HTTPException(403, "Solo quien ve todo el embudo puede reasignar oportunidades")
    nuevo = _check_owner(db, p, uid)
    antes = db.get(User, o.owner_id) if o.owner_id else None
    o.owner_id = uid
    sync_seller_from_opportunity(db, o)  # la cotizacion abierta pasa al nuevo responsable; lo facturado no
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
        # la cotizacion de una oportunidad es de su responsable (si puede ser vendedor; si no, de quien la crea)
        seller_id=opportunity_seller(db, o) or p.user.id,
    )
    o.quote_id = q.id
    sync_seller_from_opportunity(db, o)
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

    equipo = [
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
        # responsable de la oportunidad: lo elige solo quien reasigna (ve todo el embudo); el vendedor no elige
        "sellers": equipo if p.can("crm_pipeline", "ver_todo") else [],
        # a quien se le asigna una tarea de la oportunidad (p. ej. la solicitud de costo a un aliado): el equipo, para
        # todos. Solo nombres: no dice nada de las oportunidades de nadie.
        "assignees": equipo,
        "can_see_all": p.can("crm_pipeline", "ver_todo"),
    }


def sync_seller_from_opportunity(db: Session, o: Opportunity) -> None:
    """La cotizacion ligada a una oportunidad es del responsable de la oportunidad (13/10 32:38). Lo ya facturado no
    se mueve: la factura conserva su vendedor (y su comision). Un responsable que no puede ser vendedor (desactivado,
    de un cliente o de un aliado: _check_owner solo pide miembro activo) no se lleva la cotizacion ni la comision."""
    nuevo = opportunity_seller(db, o)
    if not o.quote_id or not nuevo:
        return
    q = db.get(Quote, o.quote_id)
    if q and q.status not in ("convertida", "anulada"):
        q.seller_id = nuevo


class SurveyRequestIn(BaseModel):
    note: str | None = Field(None, max_length=500)
    preferred_date: date | None = None


@router.post("/opportunities/{oid}/request-survey")
def request_survey(oid: int, data: SurveyRequestIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """La vendedora no llena levantamientos: los pide desde su oportunidad (13/10 33:16). Avisa por correo a quien
    revisa levantamientos (los mismos roles que reciben "nuevo levantamiento") y deja la oportunidad en
    "requiere visita" (si ya iba mas adelante, p. ej. cotizando, se queda ahi). Sin montos en el aviso. Hasta que
    alguien la atienda (fieldwork.pending_survey_request) el equipo de campo interno (supervisor, tecnico) puede crear
    y ligar ese levantamiento aunque la oportunidad no le salga en el embudo, en cualquier etapa abierta: la regla es
    la misma en los dos lados."""
    from ..core.ratelimit import survey_request_limiter
    from ..models import AuditLog
    from .fieldwork import reviewer_roles  # import local: fieldwork ya importa de aqui
    from .projects import SOLUCIONES

    o = _own(db, oid, p)
    # dos clics (o dos pestanas) a la vez: el segundo espera al primero y ve su registro de "ya se pidio"
    db.execute(select(Opportunity.id).where(Opportunity.id == o.id).with_for_update())
    if (o.kind or "proyecto") == "venta":
        raise HTTPException(409, "Una oportunidad de solo venta no lleva levantamiento: cotizala directo.")
    if o.status in ("ganada", "perdida"):
        raise HTTPException(409, "La oportunidad ya está cerrada.")
    vivo = db.scalar(select(Survey).where(Survey.tenant_id == p.tenant.id, Survey.opportunity_id == o.id, live(Survey)).limit(1))
    if vivo:
        raise HTTPException(409, f"Ya tiene el levantamiento {vivo.number}.")
    desde = datetime.now(UTC) - timedelta(hours=24)
    reciente = db.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.tenant_id == p.tenant.id,
            AuditLog.action == "request_survey",
            AuditLog.entity == "opportunity",
            AuditLog.entity_id == o.id,
            AuditLog.at >= desde,
        )
        .limit(1)
    )
    if reciente:
        raise HTTPException(409, "Ya se pidió en las últimas 24 horas; la oficina ya recibió el aviso.")
    # cada solicitud es un correo a supervisores y administradores con texto del vendedor: tope por persona (crear
    # oportunidades para pedir en cada una no inunda la bandeja de la oficina)
    survey_request_limiter.hit(f"{p.tenant.id}:{p.user.id}")

    antes = advance_status(o, "requiere_visita")  # solo avanza
    nota = (data.note or "").strip()
    texto = "Solicitó levantamiento técnico"
    if data.preferred_date:
        texto += f" para el {data.preferred_date:%d/%m/%Y}"
    if nota:
        texto += f": {nota}"
    _log(o, p.user.full_name, texto)

    c = db.get(Customer, o.customer_id) if o.customer_id else None
    cliente = c.name if c else (o.contact or {}).get("name") or "sin cliente"
    solucion = SOLUCIONES.get(o.solution or "", o.solution or "—")
    enlace = f"/levantamientos?nuevo={o.id}"
    cuerpo = (
        f"<p>{html.escape(p.user.full_name)} pide un levantamiento técnico para la oportunidad <b>{html.escape(o.number)}</b> "
        f"· {html.escape(o.title)}.</p>"
        f"<p>Cliente: {html.escape(cliente)}<br>Sitio: {html.escape(o.site or '—')}<br>Solución: {html.escape(solucion)}<br>"
        f"Fecha preferida: {html.escape(data.preferred_date.strftime('%d/%m/%Y') if data.preferred_date else 'sin preferencia')}</p>"
        + (f"<p>Nota: {html.escape(nota)}</p>" if nota else "")
        + "<p>El levantamiento ya viene con el cliente de la oportunidad.</p>"
        + cta(enlace, "Crear el levantamiento")  # el boton principal del correo (email_layout)
    )
    n = notify_roles(db, p.tenant, reviewer_roles(p.tenant), f"Solicitud de levantamiento {o.number} · {cliente}", cuerpo, "opportunity", o.id)
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "request_survey",
        "opportunity",
        o.id,
        {"preferred_date": data.preferred_date.isoformat() if data.preferred_date else None, "notified": n},
        ip=p.ip,
    )
    if antes:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": antes, "a": o.status}, ip=p.ip)
    db.commit()
    return {**_out(db, o), "notified": n}


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
