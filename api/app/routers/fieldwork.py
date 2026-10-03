"""Levantamientos tecnicos y ordenes de trabajo.

El tecnico levanta en sitio desde el celular (puntos, fotos, materiales) y envia. Administracion ve el costeo
(costos, mano de obra, margen) y con un boton genera la cotizacion. El tecnico nunca ve precios ni costos.
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, Opportunity, Product, Project, Survey, SurveyItem, SurveyPoint, User, WorkOrder, WorkOrderMaterial
from ..schemas.sales import DocumentIn, LineInSchema
from ..services import documents as docsvc
from ..services import inventory as invsvc
from ..services import pricing
from ..services.archive import live
from ..services.documents import audit
from ..services.mail import notify_roles
from ..services.sequences import next_number
from ..services.survey_specs import SPECS, classify_kind, normalize_point_data, suggest_materials
from ..services.totals import d

router = APIRouter(tags=["campo"])

LABOR_DAY = Decimal(25000)  # costo interno por tecnico por dia (Ajustes: labor_day_cost)
TRAVEL = Decimal(35000)  # transporte estimado por levantamiento (Ajustes: travel_cost)

# Mano de obra por tipo (lo pidio Andres): cada tipo con su costo por persona por dia en Ajustes.
# Mientras no se configuren, civil y contratado cuestan lo mismo que el tecnico (no se inventan tarifas).
LABOR_TYPES: dict[str, tuple[str, str]] = {
    "tecnico": ("Personal técnico", "labor_day_cost"),
    "civil": ("Personal de obra civil", "labor_day_cost_civil"),
    "contratado": ("Personal contratado", "labor_day_cost_contratado"),
}
# Quien revisa lo que el tecnico envia. Andres aun no definio quien aprueba: por defecto admin y
# supervisor (lo que ya se notificaba); se cambia en Ajustes con survey_reviewer_roles sin tocar codigo.
REVIEWER_ROLES = ("admin", "supervisor")


def rates(tenant) -> tuple[Decimal, Decimal]:
    st = tenant.settings or {}
    return d(st.get("labor_day_cost", LABOR_DAY)), d(st.get("travel_cost", TRAVEL))


def labor_rates(tenant) -> dict[str, Decimal]:
    st = tenant.settings or {}
    base = d(st.get("labor_day_cost", LABOR_DAY))
    return {k: d(st.get(key, base)) for k, (_, key) in LABOR_TYPES.items()}


def per_diem(tenant) -> Decimal:
    """Viaticos (alimentacion) por persona por dia de obra. 0 si no esta configurado: antes no existia."""
    return d((tenant.settings or {}).get("per_diem_cost", 0))


def reviewer_roles(tenant) -> tuple[str, ...]:
    roles = (tenant.settings or {}).get("survey_reviewer_roles")
    return tuple(roles) if roles else REVIEWER_ROLES


def survey_labor(s: Survey) -> dict[str, dict]:
    """Personal requerido por tipo. Levantamientos viejos (sin labor) toman techs/days como personal tecnico."""
    raw = s.labor or {}
    out = {}
    for k in LABOR_TYPES:
        row = raw.get(k) or {}
        if k == "tecnico" and not raw:
            row = {"people": s.techs, "days": s.days}
        out[k] = {"people": int(row.get("people") or 0), "days": d(row.get("days") or 0)}
    return out


# ---------- Levantamientos ----------
def _survey(db: Session, sid: int, p: Principal) -> Survey:
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if not p.sees_field_all and s.technician_id not in (None, p.user.id):
        raise HTTPException(404, "Levantamiento no encontrado")
    return s


def _reviewers(db: Session, tenant) -> list[dict]:
    """Personas que reciben el aviso de "nuevo levantamiento": lo que el tecnico necesita ver al enviar."""
    from ..models import TenantUser

    roles = reviewer_roles(tenant)
    rows = db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.active, TenantUser.role_code.in_(roles)))
    return [{"id": m.user.id, "name": m.user.full_name, "role": m.role_code} for m in rows]


def _opp_ref(db: Session, oid: int | None) -> dict | None:
    """Numero y titulo de la oportunidad ligada, para mostrarla sin otra consulta."""
    o = db.get(Opportunity, oid) if oid else None
    return {"id": o.id, "number": o.number, "title": o.title, "status": o.status} if o else None


def _survey_out(db: Session, s: Survey, full: bool = True) -> dict:
    c = db.get(Customer, s.customer_id) if s.customer_id else None
    t = db.get(User, s.technician_id) if s.technician_id else None
    sender = db.get(User, s.sent_by) if s.sent_by else None
    visit_ids = [int(x) for x in (s.visit_tech_ids or []) if str(x).isdigit()]
    visitors = [u.full_name for u in (db.get(User, i) for i in visit_ids) if u]
    labor = survey_labor(s)
    out = {
        "id": s.id,
        "number": s.number,
        "kind": s.kind,
        "kind_label": SPECS.get(s.kind, SPECS["otro"])["label"],
        "status": s.status,
        "customer_id": s.customer_id,
        "customer": c.name if c else (s.contact or {}).get("name"),
        "contact": s.contact,
        "site": s.site,
        "opportunity_id": s.opportunity_id,
        "opportunity": _opp_ref(db, s.opportunity_id),
        "technician_id": s.technician_id,
        "technician": t.full_name if t else None,
        "visit_date": s.visit_date,
        "techs": s.techs,
        "days": s.days,
        "notes": s.notes,
        "photos": s.photos,
        "quote_id": s.quote_id,
        "points_count": len(s.points),
        "created_at": s.created_at,
        "archived_at": s.archived_at,
        "trashed_at": s.trashed_at,
        "sent_at": s.sent_at,
        "sent_by": sender.full_name if sender else None,
        "visit_tech_ids": visit_ids,
        "visit_techs": visitors,
        "labor": {k: {"label": LABOR_TYPES[k][0], "people": v["people"], "days": v["days"]} for k, v in labor.items()},
    }
    if s.status == "enviado":
        # "pendiente de revision por": se calcula al leer, asi refleja los roles configurados hoy
        from ..models import Tenant

        tenant = db.get(Tenant, s.tenant_id)
        out["pending_review"] = {"roles": list(reviewer_roles(tenant)), "people": [r["name"] for r in _reviewers(db, tenant)]}
    if full:
        out["points"] = [{"id": x.id, "code": x.code, "label": x.label, "data": x.data, "photos": x.photos, "notes": x.notes} for x in s.points]
        out["items"] = [
            {"id": i.id, "product_id": i.product_id, "name": i.name, "quantity": i.quantity, "unit": i.unit, "note": i.note, "kind": i.kind or "material"}
            for i in s.items
        ]
    return out


class PointIn(BaseModel):
    id: int | None = None
    code: str = Field(min_length=1, max_length=20)
    label: str | None = Field(None, max_length=160)
    data: dict = Field(default_factory=dict)
    photos: list = Field(default_factory=list)
    notes: str | None = None


class ItemIn(BaseModel):
    id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(1), ge=0)
    unit: str = Field("Unid", max_length=10)
    note: str | None = Field(None, max_length=200)
    kind: str | None = Field(None, pattern="^(equipo|material)$")  # sin dato se clasifica por el nombre


class LaborIn(BaseModel):
    people: int = Field(0, ge=0, le=200)
    days: Decimal = Field(Decimal(0), ge=0, le=365)


class SurveyIn(BaseModel):
    kind: str = Field("cctv", pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    customer_id: int | None = None
    opportunity_id: int | None = None
    contact: dict = Field(default_factory=dict)
    site: str | None = Field(None, max_length=300)
    visit_date: date | None = None
    # compatibilidad: clientes viejos mandan techs/days; si viene labor, manda labor
    techs: int = Field(2, ge=0, le=200)
    days: Decimal = Field(Decimal(1), ge=0, le=365)
    labor: dict[str, LaborIn] | None = None  # tecnico | civil | contratado
    visit_tech_ids: list[int] = Field(default_factory=list)  # quienes hicieron la visita (informativo)
    notes: str | None = None
    photos: list = Field(default_factory=list)
    points: list[PointIn] = Field(default_factory=list)
    items: list[ItemIn] = Field(default_factory=list)


def _labor_payload(data: SurveyIn) -> dict:
    """Guarda labor con todos los tipos; techs/days quedan espejados al personal tecnico."""
    if data.labor is None:
        src = {"tecnico": LaborIn(people=data.techs, days=data.days)}
    else:
        bad = set(data.labor) - set(LABOR_TYPES)
        if bad:
            raise HTTPException(422, f"Tipo de personal desconocido: {', '.join(sorted(bad))}")
        src = data.labor
    return {k: {"people": (src.get(k) or LaborIn()).people, "days": str((src.get(k) or LaborIn()).days)} for k in LABOR_TYPES}


def _apply_fields(s: Survey, data: SurveyIn, db: Session, p: Principal) -> None:
    labor = _labor_payload(data)
    for k, v in data.model_dump(exclude={"points", "items", "labor", "techs", "days", "visit_tech_ids"}).items():
        setattr(s, k, v)
    s.labor = labor
    s.techs, s.days = labor["tecnico"]["people"], d(labor["tecnico"]["days"])
    # solo usuarios de esta empresa
    from ..models import TenantUser

    validos = set(db.scalars(select(TenantUser.user_id).where(TenantUser.tenant_id == p.tenant.id)))
    s.visit_tech_ids = [i for i in dict.fromkeys(data.visit_tech_ids) if i in validos]


@router.get("/field/specs")
def specs(_: Principal = Depends(require("field", "ver"))):
    """Formularios por tipo de levantamiento: el portal los dibuja desde aquí."""
    return {
        k: {"label": v["label"], "point_prefix": v["point_prefix"], "point_label": v["point_label"], "fields": v["fields"], "materials": v["materials"]}
        for k, v in SPECS.items()
    }


@router.get("/field/technicians")
def technicians(p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """A quien se le puede asignar una orden. El supervisor no entra a Ajustes, asi que la lista vive aqui."""
    from ..models import TenantUser

    rows = db.scalars(
        select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.active, TenantUser.role_code.in_(("tecnico", "supervisor", "admin")))
    )
    return [{"id": m.user.id, "name": m.user.full_name, "role": m.role_code} for m in rows]


@router.get("/surveys")
def surveys(status: str | None = None, limit: int = Query(80, le=200), p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    stmt = select(Survey).where(Survey.tenant_id == p.tenant.id, live(Survey))
    if not p.sees_field_all:
        stmt = stmt.where(Survey.technician_id == p.user.id)
    if status:
        stmt = stmt.where(Survey.status == status)
    return [_survey_out(db, s, full=False) for s in db.scalars(stmt.order_by(Survey.id.desc()).limit(limit))]


@router.post("/surveys", status_code=201)
def survey_create(data: SurveyIn, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    number, _ = next_number(db, p.tenant.id, "LEV")
    s = Survey(tenant_id=p.tenant.id, number=number, technician_id=p.user.id)
    _apply_fields(s, data, db, p)
    _apply_children(s, data)
    db.add(s)
    db.flush()
    if s.opportunity_id:
        o = db.get(Opportunity, s.opportunity_id)
        if o and o.tenant_id == p.tenant.id and o.status in ("nuevo", "contactado", "requiere_visita"):
            o.status = "levantamiento"
    audit(db, p.tenant.id, p.user.id, "create", "survey", s.id, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


def _apply_children(s: Survey, data: SurveyIn) -> None:
    points = []
    for pt in data.points:
        try:
            clean = normalize_point_data(data.kind, pt.data)
        except ValueError as e:
            raise HTTPException(422, f"{pt.code}: {e}") from e
        points.append(SurveyPoint(code=pt.code, label=pt.label, data=clean, photos=pt.photos, notes=pt.notes))
    s.points.clear()
    s.points.extend(points)
    # el costo escrito en el costeo vive en la linea: el tecnico reescribe la lista al guardar y no debe borrarlo
    prev = {i.id: i for i in s.items if i.id}
    s.items.clear()
    for it in data.items:
        old = prev.get(it.id) if it.id else None
        keep_cost = old.unit_cost if old is not None and old.product_id == it.product_id else None
        s.items.append(
            SurveyItem(
                product_id=it.product_id,
                name=it.name,
                quantity=it.quantity,
                unit=it.unit,
                note=it.note,
                kind=it.kind or (old.kind if old is not None else classify_kind(it.name)),
                unit_cost=keep_cost,
            )
        )


@router.get("/surveys/{sid}")
def survey_get(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    return _survey_out(db, _survey(db, sid, p))


@router.put("/surveys/{sid}")
def survey_update(sid: int, data: SurveyIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado") and not p.sees_field_all:
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    _apply_fields(s, data, db, p)
    _apply_children(s, data)
    db.commit()
    return _survey_out(db, s)


class LinkOppIn(BaseModel):
    opportunity_id: int | None = None  # None = desligar


@router.post("/surveys/{sid}/opportunity")
def survey_link_opportunity(sid: int, data: LinkOppIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Liga un levantamiento ya hecho a una oportunidad (o lo desliga).

    Andres: "hice el levantamiento en el sitio antes de registrar la oportunidad; quiero poder asignarlo
    despues". Funciona aunque el levantamiento ya este cotizado, que es el caso normal: se visita, se cotiza
    y recien ahi se registra la oportunidad. Al ligar, la oportunidad hereda lo que el levantamiento ya
    avanzo (cliente, cotizacion, monto y etapa) y queda anotado en su bitacora."""
    from ..services.archive import is_live
    from ..services.documents import CR

    s = _survey(db, sid, p)
    stamp = datetime.now(CR).strftime("%d/%m/%Y %H:%M")
    anterior = db.get(Opportunity, s.opportunity_id) if s.opportunity_id else None

    if data.opportunity_id is None:
        if anterior:
            anterior.notes = f"{stamp} · {p.user.full_name}: se desligo el levantamiento {s.number}\n{anterior.notes or ''}".strip()
        s.opportunity_id = None
        audit(db, p.tenant.id, p.user.id, "unlink_opportunity", "survey", s.id, {"opportunity_id": anterior.id if anterior else None}, ip=p.ip)
        db.commit()
        return _survey_out(db, s)

    o = db.get(Opportunity, data.opportunity_id)
    if not o or o.tenant_id != p.tenant.id or not is_live(o):
        raise HTTPException(404, "Oportunidad no encontrada")
    if not p.can("crm_pipeline", "ver"):
        raise HTTPException(403, "Sin permiso para ver oportunidades")
    if o.status in ("ganada", "perdida"):
        raise HTTPException(409, "La oportunidad ya está cerrada (ganada o perdida)")
    if s.customer_id and o.customer_id and s.customer_id != o.customer_id:
        raise HTTPException(409, "El levantamiento y la oportunidad son de clientes distintos")
    if o.quote_id and s.quote_id and o.quote_id != s.quote_id:
        raise HTTPException(409, "La oportunidad ya tiene otra cotización ligada")

    # lo que uno tiene y el otro no, se completa
    if not s.customer_id and o.customer_id:
        s.customer_id = o.customer_id
    elif s.customer_id and not o.customer_id:
        o.customer_id = s.customer_id

    antes = o.status
    orden = ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion")
    if s.quote_id:
        from ..models import Quote

        q = db.get(Quote, s.quote_id)
        o.quote_id = s.quote_id
        if q and q.status != "anulada":
            o.amount = d(q.total)  # mismo criterio que el resto: el monto sigue a la cotizacion
        destino = "enviada" if q and q.status in ("enviada", "aprobada") else "cotizando"
    else:
        destino = "levantamiento"
    if o.status in orden and orden.index(o.status) < orden.index(destino):
        o.status = destino  # solo avanza: nunca devuelve una oportunidad que ya estaba mas adelante

    if anterior and anterior.id != o.id:
        anterior.notes = f"{stamp} · {p.user.full_name}: el levantamiento {s.number} se movio a {o.number}\n{anterior.notes or ''}".strip()
    s.opportunity_id = o.id
    o.notes = f"{stamp} · {p.user.full_name}: se ligo el levantamiento {s.number}\n{o.notes or ''}".strip()
    audit(db, p.tenant.id, p.user.id, "link_opportunity", "survey", s.id, {"opportunity_id": o.id}, ip=p.ip)
    if o.status != antes:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": antes, "a": o.status}, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


@router.post("/surveys/{sid}/suggest")
def survey_suggest(sid: int, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Propone materiales y equipos a partir de los puntos, sus observaciones y lo ya cargado. NO modifica
    el levantamiento: el portal muestra la lista con el motivo de cada una y el usuario elige (ver /apply)."""
    s = _survey(db, sid, p)
    return suggest_materials(s.kind, list(s.points), list(s.items))


class SuggestionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    unit: str = Field("Unid", max_length=10)
    kind: str | None = Field(None, pattern="^(equipo|material)$")
    product_id: int | None = None
    note: str | None = Field(None, max_length=200)
    item_id: int | None = None  # linea existente a la que se suma (la sugerencia la cruzo por familia de material)


@router.post("/surveys/{sid}/suggest/apply")
def survey_suggest_apply(sid: int, data: list[SuggestionIn], p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Agrega las sugerencias elegidas a lo que ya estaba. Si el material ya existe (mismo producto o mismo
    nombre) suma la cantidad en vez de duplicar la linea. Nunca borra nada."""
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    added, summed = 0, 0
    for sg in data:
        match = next((it for it in s.items if sg.item_id and it.id == sg.item_id), None) or next(
            (it for it in s.items if (sg.product_id and it.product_id == sg.product_id) or (it.name or "").strip().lower() == sg.name.strip().lower()),
            None,
        )
        if match:
            match.quantity = d(match.quantity) + sg.quantity
            summed += 1
        else:
            s.items.append(
                SurveyItem(product_id=sg.product_id, name=sg.name, quantity=sg.quantity, unit=sg.unit, note=sg.note, kind=sg.kind or classify_kind(sg.name))
            )
            added += 1
    audit(db, p.tenant.id, p.user.id, "suggest_apply", "survey", s.id, {"agregados": added, "sumados": summed}, ip=p.ip)
    db.commit()
    return {"added": added, "summed": summed, "survey": _survey_out(db, s)}


@router.post("/surveys/{sid}/send")
def survey_send(sid: int, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    """El técnico envía el levantamiento a administración y ve a quién le llegó el aviso."""
    s = _survey(db, sid, p)
    if not s.points and not s.items:
        raise HTTPException(422, "Agregá al menos un punto o material antes de enviar")
    s.status = "enviado"
    s.sent_at = datetime.now(UTC)
    s.sent_by = p.user.id
    cliente = db.get(Customer, s.customer_id).name if s.customer_id else (s.contact or {}).get("name") or "sin cliente"
    spec = SPECS.get(s.kind, SPECS["otro"])
    filas = "".join(f"<li>{html.escape(x.code)} · {html.escape(x.label or '')}</li>" for x in s.points[:20])
    labor = survey_labor(s)
    personal = " · ".join(f"{LABOR_TYPES[k][0]}: {v['people']} × {v['days']:g} día(s)" for k, v in labor.items() if v["people"])
    roles = reviewer_roles(p.tenant)
    notify_roles(
        db,
        p.tenant,
        roles,
        f"Nuevo levantamiento {s.number} · {cliente}",
        f"<p><b>{html.escape(spec['label'])}</b> levantado por {html.escape(p.user.full_name)}.</p>"
        f"<p>Cliente: {html.escape(cliente)}<br>Sitio: {html.escape(s.site or '—')}<br>"
        f"{len(s.points)} {html.escape(spec['point_label'].lower())}(s) · {len(s.items)} materiales y equipos<br>{html.escape(personal or 'sin personal')}</p>"
        f"<ul>{filas}</ul><p>Abrí el levantamiento para costearlo y generar la cotización.</p>",
        "survey",
        s.id,
    )
    notified = [r for r in _reviewers(db, p.tenant)]
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "send",
        "survey",
        s.id,
        {"puntos": len(s.points), "materiales": len(s.items), "notificados": [r["name"] for r in notified]},
        ip=p.ip,
    )
    db.commit()
    out = _survey_out(db, s)
    out["notified"] = notified
    return out


@router.get("/surveys/{sid}/costing")
def survey_costing(sid: int, margin: Decimal | None = None, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Costeo para administración: equipos, materiales, mano de obra por tipo, viáticos, precio sugerido y margen."""
    if not p.sees_costs:
        raise HTTPException(403, "Tu rol no ve costos")
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    fx = docsvc.today_fx(db, "USD")[0]
    margin_pct = d(margin) if margin is not None else pricing.default_margin(p.tenant)
    labor_day, transport = rates(p.tenant)
    lines, cost_items, price_items = [], Decimal(0), Decimal(0)
    groups = {"equipo": {"cost": Decimal(0), "price": Decimal(0)}, "material": {"cost": Decimal(0), "price": Decimal(0)}}
    for it in s.items:
        prod = db.get(Product, it.product_id) if it.product_id else None
        qty = d(it.quantity)
        unit_cost = pricing.cost_in(db, prod, "CRC", fx) if prod else Decimal(0)
        source = "catalogo" if unit_cost > 0 else None
        if unit_cost <= 0 and it.unit_cost is not None and d(it.unit_cost) > 0:
            unit_cost, source = d(it.unit_cost), "levantamiento"  # escrito a mano en este costeo
        # precio de lista si el producto lo tiene; si no, sale del costo con el margen objetivo
        price_from_cost = not (prod and d(prod.price) > 0)
        unit_price = pricing.sale_price(db, unit_cost, "CRC", "CRC", margin_pct) if price_from_cost else d(prod.price)
        kind = it.kind or "material"
        lines.append(
            {
                "item_id": it.id,
                "product_id": it.product_id,
                "name": it.name if not prod else prod.name,
                "kind": kind,
                "quantity": qty,
                "unit": it.unit,
                "unit_cost": unit_cost.quantize(Decimal("0.01")),
                "cost": (unit_cost * qty).quantize(Decimal("0.01")),
                "unit_price": d(unit_price).quantize(Decimal("0.01")),
                "price": (d(unit_price) * qty).quantize(Decimal("0.01")),
                "has_cost": unit_cost > 0,
                "cost_source": source,  # catalogo | levantamiento | None
                "price_from_cost": price_from_cost,
            }
        )
        cost_items += unit_cost * qty
        price_items += d(unit_price) * qty
        g = groups.setdefault(kind, {"cost": Decimal(0), "price": Decimal(0)})
        g["cost"] += unit_cost * qty
        g["price"] += d(unit_price) * qty
    lines.sort(key=lambda x: 0 if x["kind"] == "equipo" else 1)  # equipos primero, como en el levantamiento

    # mano de obra por tipo de personal
    lrates = labor_rates(p.tenant)
    types, labor_cost, person_days = [], Decimal(0), Decimal(0)
    for k, v in survey_labor(s).items():
        c = lrates[k] * v["people"] * v["days"]
        labor_cost += c
        person_days += v["people"] * v["days"]
        types.append(
            {"key": k, "label": LABOR_TYPES[k][0], "people": v["people"], "days": v["days"], "day_cost": lrates[k], "cost": c.quantize(Decimal("0.01"))}
        )
    viaticos = (per_diem(p.tenant) * person_days).quantize(Decimal("0.01"))
    travel = transport + viaticos
    cost_total = cost_items + labor_cost + travel
    labor_price = pricing.sale_price(db, labor_cost + travel, "CRC", "CRC", margin_pct)
    suggested = price_items + labor_price
    return {
        "survey": _survey_out(db, s, full=False),
        "lines": lines,
        "groups": {k: {"cost": v["cost"].quantize(Decimal("0.01")), "price": v["price"].quantize(Decimal("0.01"))} for k, v in groups.items()},
        "labor": {
            "techs": s.techs,
            "days": s.days,
            "day_cost": labor_day,
            "types": types,
            "cost": labor_cost.quantize(Decimal("0.01")),
            "transport": transport,
            "per_diem": per_diem(p.tenant),
            "viaticos": viaticos,
            "travel": travel,
            "price": labor_price,
        },
        "cost_total": cost_total.quantize(Decimal("0.01")),
        "price_suggested": suggested.quantize(Decimal("0.01")),
        "margin_pct": pricing.margin_of(suggested, cost_total),
        "margin_target": margin_pct,
        "fx": fx,
        "missing_cost": [line["name"] for line in lines if not line["has_cost"]],
        "can_save_catalog": p.can("catalog", "editar"),
    }


class CostIn(BaseModel):
    item_id: int
    unit_cost: Decimal = Field(ge=0)  # colones, sin IVA
    save_to_catalog: bool = False


@router.post("/surveys/{sid}/costs")
def survey_costs(sid: int, data: list[CostIn], margin: Decimal | None = None, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Costo escrito a mano en el costeo para las lineas sin costo de proveedor. Opcionalmente se guarda en el
    producto del catalogo (en colones) para no tener que escribirlo en el proximo levantamiento."""
    if not p.sees_costs:
        raise HTTPException(403, "Tu rol no ve costos")
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if s.quote_id:
        raise HTTPException(409, "Este levantamiento ya generó una cotización")
    items = {i.id: i for i in s.items}
    saved = []
    for c in data:
        it = items.get(c.item_id)
        if it is None:
            raise HTTPException(404, f"Línea {c.item_id} no pertenece a este levantamiento")
        it.unit_cost = c.unit_cost
        if c.save_to_catalog:
            if not p.can("catalog", "editar"):
                raise HTTPException(403, "Sin permiso para editar el catálogo (catalog.editar)")
            prod = db.get(Product, it.product_id) if it.product_id else None
            if prod is None or prod.tenant_id != p.tenant.id:
                raise HTTPException(422, f"{it.name}: la línea no está enlazada a un producto del catálogo")
            # el costo del costeo esta en colones: se guarda asi, sin inventar un tipo de cambio
            prod.cost, prod.cost_currency = c.unit_cost, "CRC"
            saved.append(prod.id)
    audit(db, p.tenant.id, p.user.id, "costs", "survey", s.id, {"lineas": len(data), "catalogo": saved}, ip=p.ip)
    db.commit()
    return survey_costing(sid, margin, p, db)


class ToQuoteIn(BaseModel):
    margin: Decimal | None = Field(None, ge=0, le=95)
    labor_label: str = Field("Instalación y configuración", max_length=200)
    include_labor: bool = True
    notes: str | None = None


@router.post("/surveys/{sid}/quote", status_code=201)
def survey_to_quote(sid: int, data: ToQuoteIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Convierte el levantamiento en cotización: una línea por material y una de mano de obra. No se reescribe nada."""
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if s.quote_id:
        raise HTTPException(409, "Este levantamiento ya generó una cotización")
    if not s.customer_id:
        raise HTTPException(422, "Asigná un cliente al levantamiento antes de cotizar")
    costing = survey_costing(sid, data.margin, p, db)
    lines = [
        LineInSchema(product_id=x["product_id"], name=x["name"], quantity=d(x["quantity"]), unit_price=d(x["unit_price"]), unit=x["unit"])
        for x in costing["lines"]
        if d(x["quantity"]) > 0
    ]
    if data.include_labor:
        lines.append(
            LineInSchema(
                name=f"{data.labor_label} · "
                + (" · ".join(f"{t['label']} {t['people']} × {d(t['days']):g} día(s)" for t in costing["labor"]["types"] if t["people"]) or "sin personal"),
                quantity=Decimal(1),
                unit_price=d(costing["labor"]["price"]),
                unit="Sp",
            )
        )
    if not lines:
        raise HTTPException(422, "El levantamiento no tiene materiales con cantidad")
    payload = DocumentIn(
        customer_id=s.customer_id,
        currency="CRC",
        external_notes=data.notes
        or f"Según levantamiento técnico {s.number} ({SPECS.get(s.kind, SPECS['otro'])['label']}) en {s.site or 'sitio del cliente'}.",
        internal_notes=f"Levantamiento {s.number}. Costo estimado ₡{costing['cost_total']:,.0f} · margen {costing['margin_pct']} %.",
        lines=lines,
    )
    q = docsvc.create_quote(db, p.tenant.id, p.user.id, payload)
    s.quote_id, s.status = q.id, "cotizado"
    if s.opportunity_id:
        o = db.get(Opportunity, s.opportunity_id)
        if o:
            o.quote_id, o.status, o.amount = q.id, "cotizando", d(q.total)
    audit(db, p.tenant.id, p.user.id, "quote", "survey", s.id, {"quote_id": q.id, "cost": str(costing["cost_total"])}, ip=p.ip)
    db.commit()
    return {"quote_id": q.id, "number": q.number, "total": q.total, "cost_total": costing["cost_total"], "margin_pct": costing["margin_pct"]}


# ---------- Ordenes de trabajo ----------
def _order(db: Session, oid: int, p: Principal) -> WorkOrder:
    o = db.get(WorkOrder, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Orden de trabajo no encontrada")
    if not p.sees_field_all and o.technician_id != p.user.id and p.user.id not in (o.helpers or []):
        raise HTTPException(404, "Orden de trabajo no encontrada")
    return o


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    """Horas trabajadas. SQLite devuelve fechas sin zona y la recien escrita si la trae: se igualan antes de restar."""
    if not start or not end:
        return None
    if (start.tzinfo is None) != (end.tzinfo is None):
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    return round((end - start).total_seconds() / 3600, 2)


def _order_out(db: Session, o: WorkOrder, full: bool = True) -> dict:
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    t = db.get(User, o.technician_id) if o.technician_id else None
    pr = db.get(Project, o.project_id) if o.project_id else None
    out = {
        "id": o.id,
        "number": o.number,
        "title": o.title,
        "kind": o.kind,
        "status": o.status,
        "archived_at": o.archived_at,
        "trashed_at": o.trashed_at,
        "site": o.site,
        "scheduled_at": o.scheduled_at,
        "customer_id": o.customer_id,
        "customer": c.name if c else None,
        "project_id": o.project_id,
        "project": pr.number if pr else None,
        "technician_id": o.technician_id,
        "technician": t.full_name if t else None,
        "helpers": o.helpers,
        "arrived_at": o.arrived_at,
        "started_at": o.started_at,
        "finished_at": o.finished_at,
        "tasks": o.tasks,
        "photos": o.photos,
        "notes": o.notes,
        "customer_signature": o.customer_signature,
        "warehouse_id": o.warehouse_id,
        "stock_applied": o.stock_applied,
        "hours": _hours(o.started_at, o.finished_at),
    }
    if full:
        out["materials"] = [
            {"id": m.id, "product_id": m.product_id, "name": m.name, "quantity": m.quantity, "unit": m.unit, "planned": m.planned} for m in o.materials
        ]
    return out


class MaterialIn(BaseModel):
    id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(0), ge=0)
    unit: str = Field("Unid", max_length=10)
    planned: Decimal = Field(Decimal(0), ge=0)


class WorkOrderIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    kind: str = Field("instalacion", pattern="^(instalacion|visita|mantenimiento|soporte)$")
    project_id: int | None = None
    customer_id: int | None = None
    site: str | None = Field(None, max_length=300)
    scheduled_at: datetime | None = None
    technician_id: int | None = None
    helpers: list[int] = Field(default_factory=list)
    tasks: list[dict] = Field(default_factory=list)
    notes: str | None = None
    warehouse_id: int | None = None
    materials: list[MaterialIn] = Field(default_factory=list)


def _live_orders():
    """Ordenes activas: ni ellas ni su proyecto estan archivados o en la papelera."""
    return (live(WorkOrder), or_(WorkOrder.project_id.is_(None), WorkOrder.project_id.in_(select(Project.id).where(live(Project)))))


@router.get("/work-orders")
def work_orders(
    status: str | None = None,
    day: date | None = None,
    project_id: int | None = None,
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("field", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == p.tenant.id, *_live_orders())
    if status == "pendientes":
        stmt = stmt.where(WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")))
    elif status:
        stmt = stmt.where(WorkOrder.status == status)
    if project_id:
        stmt = stmt.where(WorkOrder.project_id == project_id)
    rows = db.scalars(stmt.order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at, WorkOrder.id.desc()).limit(limit)).all()
    if not p.sees_field_all:
        rows = [o for o in rows if o.technician_id == p.user.id or p.user.id in (o.helpers or [])]
    if day:
        rows = [o for o in rows if o.scheduled_at and o.scheduled_at.date() == day]
    return [_order_out(db, o, full=False) for o in rows]


@router.post("/work-orders", status_code=201)
def order_create(data: WorkOrderIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    number, _ = next_number(db, p.tenant.id, "OT")
    o = WorkOrder(tenant_id=p.tenant.id, number=number, **data.model_dump(exclude={"materials"}))
    for m in data.materials:
        o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))
    if o.project_id:
        pr = db.get(Project, o.project_id)
        if pr and pr.tenant_id == p.tenant.id:
            o.customer_id = o.customer_id or pr.customer_id
            o.site = o.site or pr.site
    db.add(o)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "work_order", o.id, ip=p.ip)
    db.commit()
    return _order_out(db, o)


@router.get("/work-orders/{oid}")
def order_get(oid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    return _order_out(db, _order(db, oid, p))


@router.put("/work-orders/{oid}")
def order_update(oid: int, data: WorkOrderIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    o = _order(db, oid, p)
    if o.stock_applied:
        raise HTTPException(409, "La orden ya descontó inventario; no se puede reescribir")
    for k, v in data.model_dump(exclude={"materials"}).items():
        setattr(o, k, v)
    o.materials.clear()
    for m in data.materials:
        o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))
    db.commit()
    return _order_out(db, o)


class ProgressIn(BaseModel):
    tasks: list[dict] | None = None
    photos: list | None = None
    notes: str | None = None
    materials: list[MaterialIn] | None = None
    customer_signature: str | None = Field(None, max_length=160)


@router.post("/work-orders/{oid}/{action}")
def order_action(oid: int, action: str, data: ProgressIn | None = None, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Pantalla del técnico: llegué / iniciar / avanzar / finalizar. Al finalizar descuenta el material usado."""
    o = _order(db, oid, p)
    now = datetime.now(UTC)
    data = data or ProgressIn()
    if data.tasks is not None:
        o.tasks = data.tasks
    if data.photos is not None:
        o.photos = data.photos
    if data.notes is not None:
        o.notes = data.notes
    if data.customer_signature is not None:
        o.customer_signature = data.customer_signature
    if data.materials is not None and not o.stock_applied:
        o.materials.clear()
        for m in data.materials:
            o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))

    if action == "arrive":
        o.arrived_at, o.status = o.arrived_at or now, "en_sitio"
    elif action == "start":
        o.started_at, o.status = o.started_at or now, "en_proceso"
    elif action == "progress":
        pass
    elif action == "finish":
        # Acuerdo con Andres en la reunion del 22/09: no se cierra un trabajo con "instale 5 camaras" en una
        # nota. Si un material estaba planificado hay que anotar cuanto se uso; si no se uso, se quita la linea.
        # Sin eso no queda respaldo de lo que realmente paso en el sitio.
        sin_anotar = [m.name for m in o.materials if d(m.planned) > 0 and d(m.quantity) <= 0]
        if sin_anotar:
            raise HTTPException(
                422,
                "Anotá cuánto usaste de: " + ", ".join(sin_anotar[:6]) + ("…" if len(sin_anotar) > 6 else "") + ". Si no se usó, quitá la línea.",
            )
        if not o.started_at:
            o.started_at = now
        o.finished_at, o.status = now, "finalizada"
        consumed = _apply_materials(db, o, p)
        audit(db, p.tenant.id, p.user.id, "finish", "work_order", o.id, {"materiales": consumed}, ip=p.ip)
        if o.project_id:
            pr = db.get(Project, o.project_id)
            if pr and all(x.status in ("finalizada", "cancelada") for x in pr.orders if x.trashed_at is None) and pr.status != "terminado":
                pr.status = "terminado"
                horas = sum((_hours(x.started_at, x.finished_at) or 0 for x in pr.orders), 0.0)
                notify_roles(
                    db,
                    p.tenant,
                    ("admin", "contabilidad"),
                    f"Proyecto {pr.number} terminado · listo para facturación",
                    f"<p><b>{html.escape(pr.name)}</b> quedó terminado.</p>"
                    f"<p>{len(pr.orders)} orden(es) de trabajo · {round(horas, 1)} horas de campo.</p>"
                    "<p>El informe técnico de entrega ya se puede imprimir desde la ficha del proyecto.</p>",
                    "project",
                    pr.id,
                )
    elif action == "cancel":
        o.status = "cancelada"
    else:
        raise HTTPException(404, "Acción no soportada")
    db.commit()
    return _order_out(db, o)


def _apply_materials(db: Session, o: WorkOrder, p: Principal) -> int:
    """Descuenta del inventario lo realmente utilizado (una sola vez por orden)."""
    if o.stock_applied:
        return 0
    wh = o.warehouse_id or (invsvc.default_warehouse(db, o.tenant_id).id if invsvc.default_warehouse(db, o.tenant_id) else None)
    n = 0
    for m in o.materials:
        if not m.product_id or d(m.quantity) <= 0 or not wh:
            continue
        invsvc.move(db, o.tenant_id, p.user.id, m.product_id, wh, "salida", -d(m.quantity), reference=o.number, note=f"Orden {o.number}")
        n += 1
    o.stock_applied = True
    return n


@router.get("/work-orders/meta/today")
def my_day(p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """Pantalla de inicio del técnico: lo de hoy y lo pendiente."""
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == p.tenant.id, WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")), *_live_orders())
    rows = db.scalars(stmt.order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at)).all()
    if not p.sees_field_all:
        rows = [o for o in rows if o.technician_id == p.user.id or p.user.id in (o.helpers or [])]
    today = date.today()
    return {
        "today": [_order_out(db, o, full=False) for o in rows if o.scheduled_at and o.scheduled_at.date() == today],
        "next": [_order_out(db, o, full=False) for o in rows if not o.scheduled_at or o.scheduled_at.date() != today][:10],
        "surveys": [
            _survey_out(db, s, full=False)
            for s in db.scalars(
                select(Survey)
                .where(Survey.tenant_id == p.tenant.id, Survey.status == "borrador", Survey.technician_id == p.user.id, live(Survey))
                .order_by(Survey.id.desc())
                .limit(5)
            )
        ],
    }


# ---------- Informe preliminar (para el cliente, antes de cotizar) ----------
@router.get("/surveys/{sid}/report.pdf")
def survey_report(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """PDF sin un solo monto (ver services/survey_report.py). Sin WeasyPrint (Windows local) devuelve el HTML
    imprimible con X-PDF-Fallback, igual que las cotizaciones."""
    from fastapi.responses import HTMLResponse, Response

    from ..services.render import render_pdf
    from ..services.survey_report import render_survey_html

    s = _survey(db, sid, p)
    page = render_survey_html(db, s, p.tenant)
    pdf = render_pdf(page)
    if pdf is None:
        return HTMLResponse(page, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Informe-{s.number}.pdf"'})


class ReportSendIn(BaseModel):
    to: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=2000)


@router.post("/surveys/{sid}/report/email")
def survey_report_send(sid: int, data: ReportSendIn, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    """Manda el informe preliminar al cliente (el informe va en el cuerpo del correo). Igual que las cotizaciones:
    solo cuenta como enviado si el proveedor acepto el correo; simulado o error no."""
    from ..services.mail import queue_email
    from ..services.survey_report import render_survey_html

    s = _survey(db, sid, p)
    c = db.get(Customer, s.customer_id) if s.customer_id else None
    to = (data.to or (c.email if c else None) or (s.contact or {}).get("email") or "").strip()
    if not to or "@" not in to:
        raise HTTPException(422, "El cliente no tiene correo; indicá uno")
    page = render_survey_html(db, s, p.tenant)
    if data.message:
        page = page.replace("<body>", f'<body><p style="white-space:pre-wrap">{html.escape(data.message)}</p>', 1)
    m = queue_email(db, p.tenant, to, f"Informe preliminar {s.number} · {p.tenant.name}", page, "survey", s.id)
    sent = m.status == "enviado"
    audit(db, p.tenant.id, p.user.id, "send_report", "survey", s.id, {"to": to, "mail": m.status}, ip=p.ip)
    db.commit()
    if m.status == "simulado":
        note = "El correo NO salió: no hay servicio de correo configurado (falta la llave de Resend). No se registró como enviado."
    elif m.status == "error":
        note = f"El correo NO salió ({(m.error or 'error del proveedor')[:160]}). No se registró como enviado."
    else:
        note = None
    return {"status": m.status, "sent": sent, "to": to, "note": note}


# Archivo y papelera: se monta aqui para no tocar main.py (ver routers/archive.py)
from .archive import router as _archive_router  # noqa: E402

router.include_router(_archive_router)
