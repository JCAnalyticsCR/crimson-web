"""Levantamientos tecnicos y ordenes de trabajo.

El tecnico levanta en sitio desde el celular (puntos, fotos, materiales) y envia. Administracion ve el costeo
(costos, mano de obra, margen) y con un boton genera la cotizacion. El tecnico nunca ve precios ni costos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, Opportunity, Product, Project, Survey, SurveyItem, SurveyPoint, User, WorkOrder, WorkOrderMaterial
from ..schemas.sales import DocumentIn, LineInSchema
from ..services import documents as docsvc
from ..services import inventory as invsvc
from ..services import pricing
from ..services.documents import audit
from ..services.sequences import next_number
from ..services.survey_specs import SPECS, suggest_materials
from ..services.totals import d

router = APIRouter(tags=["campo"])

LABOR_DAY = Decimal(25000)  # costo interno por tecnico por dia (Ajustes: labor_day_cost)
TRAVEL = Decimal(35000)  # transporte estimado por levantamiento (Ajustes: travel_cost)


def rates(tenant) -> tuple[Decimal, Decimal]:
    st = tenant.settings or {}
    return d(st.get("labor_day_cost", LABOR_DAY)), d(st.get("travel_cost", TRAVEL))


# ---------- Levantamientos ----------
def _survey(db: Session, sid: int, p: Principal) -> Survey:
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if not p.sees_field_all and s.technician_id not in (None, p.user.id):
        raise HTTPException(404, "Levantamiento no encontrado")
    return s


def _survey_out(db: Session, s: Survey, full: bool = True) -> dict:
    c = db.get(Customer, s.customer_id) if s.customer_id else None
    t = db.get(User, s.technician_id) if s.technician_id else None
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
        "sent_at": s.sent_at,
    }
    if full:
        out["points"] = [{"id": x.id, "code": x.code, "label": x.label, "data": x.data, "photos": x.photos, "notes": x.notes} for x in s.points]
        out["items"] = [{"id": i.id, "product_id": i.product_id, "name": i.name, "quantity": i.quantity, "unit": i.unit, "note": i.note} for i in s.items]
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


class SurveyIn(BaseModel):
    kind: str = Field("cctv", pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    customer_id: int | None = None
    opportunity_id: int | None = None
    contact: dict = Field(default_factory=dict)
    site: str | None = Field(None, max_length=300)
    visit_date: date | None = None
    techs: int = Field(2, ge=1, le=20)
    days: Decimal = Field(Decimal(1), gt=0, le=365)
    notes: str | None = None
    photos: list = Field(default_factory=list)
    points: list[PointIn] = Field(default_factory=list)
    items: list[ItemIn] = Field(default_factory=list)


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
    stmt = select(Survey).where(Survey.tenant_id == p.tenant.id)
    if not p.sees_field_all:
        stmt = stmt.where(Survey.technician_id == p.user.id)
    if status:
        stmt = stmt.where(Survey.status == status)
    return [_survey_out(db, s, full=False) for s in db.scalars(stmt.order_by(Survey.id.desc()).limit(limit))]


@router.post("/surveys", status_code=201)
def survey_create(data: SurveyIn, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    number, _ = next_number(db, p.tenant.id, "LEV")
    s = Survey(tenant_id=p.tenant.id, number=number, technician_id=p.user.id, **data.model_dump(exclude={"points", "items"}))
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
    s.points.clear()
    for pt in data.points:
        s.points.append(SurveyPoint(code=pt.code, label=pt.label, data=pt.data, photos=pt.photos, notes=pt.notes))
    s.items.clear()
    for it in data.items:
        s.items.append(SurveyItem(product_id=it.product_id, name=it.name, quantity=it.quantity, unit=it.unit, note=it.note))


@router.get("/surveys/{sid}")
def survey_get(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    return _survey_out(db, _survey(db, sid, p))


@router.put("/surveys/{sid}")
def survey_update(sid: int, data: SurveyIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado") and not p.sees_field_all:
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    for k, v in data.model_dump(exclude={"points", "items"}).items():
        setattr(s, k, v)
    _apply_children(s, data)
    db.commit()
    return _survey_out(db, s)


@router.post("/surveys/{sid}/suggest")
def survey_suggest(sid: int, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Sugiere materiales a partir de los puntos (cantidad de puntos y metros con 15 % de holgura)."""
    s = _survey(db, sid, p)
    return suggest_materials(s.kind, list(s.points))


@router.post("/surveys/{sid}/send")
def survey_send(sid: int, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    """El técnico envía el levantamiento a administración."""
    s = _survey(db, sid, p)
    if not s.points and not s.items:
        raise HTTPException(422, "Agregá al menos un punto o material antes de enviar")
    s.status = "enviado"
    s.sent_at = datetime.now(UTC)
    audit(db, p.tenant.id, p.user.id, "send", "survey", s.id, {"puntos": len(s.points), "materiales": len(s.items)}, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


@router.get("/surveys/{sid}/costing")
def survey_costing(sid: int, margin: Decimal | None = None, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Costeo para administración: costo por línea, mano de obra, transporte, precio sugerido y margen."""
    if not p.sees_prices:
        raise HTTPException(403, "Tu rol no ve costos")
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    fx = docsvc.today_fx(db, "USD")[0]
    margin_pct = d(margin) if margin is not None else pricing.default_margin(p.tenant)
    labor_day, travel = rates(p.tenant)
    lines, cost_items, price_items = [], Decimal(0), Decimal(0)
    for it in s.items:
        prod = db.get(Product, it.product_id) if it.product_id else None
        qty = d(it.quantity)
        unit_cost = pricing.cost_in(db, prod, "CRC", fx) if prod else Decimal(0)
        unit_price = d(prod.price) if prod else pricing.sale_price(db, unit_cost, "CRC", "CRC", margin_pct)
        lines.append(
            {
                "product_id": it.product_id,
                "name": it.name if not prod else prod.name,
                "quantity": qty,
                "unit": it.unit,
                "unit_cost": unit_cost.quantize(Decimal("0.01")),
                "cost": (unit_cost * qty).quantize(Decimal("0.01")),
                "unit_price": d(unit_price).quantize(Decimal("0.01")),
                "price": (d(unit_price) * qty).quantize(Decimal("0.01")),
                "has_cost": unit_cost > 0,
            }
        )
        cost_items += unit_cost * qty
        price_items += d(unit_price) * qty
    labor_cost = labor_day * s.techs * d(s.days)
    cost_total = cost_items + labor_cost + travel
    labor_price = pricing.sale_price(db, labor_cost + travel, "CRC", "CRC", margin_pct)
    suggested = price_items + labor_price
    return {
        "survey": _survey_out(db, s, full=False),
        "lines": lines,
        "labor": {
            "techs": s.techs,
            "days": s.days,
            "day_cost": labor_day,
            "cost": labor_cost.quantize(Decimal("0.01")),
            "travel": travel,
            "price": labor_price,
        },
        "cost_total": cost_total.quantize(Decimal("0.01")),
        "price_suggested": suggested.quantize(Decimal("0.01")),
        "margin_pct": pricing.margin_of(suggested, cost_total),
        "margin_target": margin_pct,
        "fx": fx,
        "missing_cost": [line["name"] for line in lines if not line["has_cost"]],
    }


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
                name=f"{data.labor_label} · {s.techs} técnicos × {d(s.days):g} día(s)",
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


@router.get("/work-orders")
def work_orders(
    status: str | None = None,
    day: date | None = None,
    project_id: int | None = None,
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("field", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == p.tenant.id)
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
        if not o.started_at:
            o.started_at = now
        o.finished_at, o.status = now, "finalizada"
        consumed = _apply_materials(db, o, p)
        audit(db, p.tenant.id, p.user.id, "finish", "work_order", o.id, {"materiales": consumed}, ip=p.ip)
        if o.project_id:
            pr = db.get(Project, o.project_id)
            if pr and all(x.status in ("finalizada", "cancelada") for x in pr.orders):
                pr.status = "terminado"
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
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == p.tenant.id, WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")))
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
                .where(Survey.tenant_id == p.tenant.id, Survey.status == "borrador", Survey.technician_id == p.user.id)
                .order_by(Survey.id.desc())
                .limit(5)
            )
        ],
    }
