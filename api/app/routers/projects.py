"""Proyectos (cotizacion aprobada -> ejecucion), activos del cliente y solicitudes de compra.

Aqui se responde la pregunta que hoy es dificil: cuanto dejo realmente cada instalacion. El proyecto guarda el
precio vendido, el costo estimado al cotizar y el costo real (equipos consumidos + mano de obra + viaticos + otros).
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import (
    Customer,
    CustomerAsset,
    Invoice,
    Opportunity,
    Product,
    Project,
    PurchaseRequest,
    PurchaseRequestLine,
    Quote,
    Supplier,
    Survey,
    User,
    WorkOrder,
)
from ..services import inventory as invsvc
from ..services import pricing
from ..services.documents import audit, today_fx
from ..services.sequences import next_number
from ..services.totals import d

router = APIRouter(tags=["proyectos"])


def _project(db: Session, pid: int, p: Principal) -> Project:
    pr = db.get(Project, pid)
    if not pr or pr.tenant_id != p.tenant.id:
        raise HTTPException(404, "Proyecto no encontrado")
    if (
        not p.can("projects", "ver_todo")
        and pr.supervisor_id != p.user.id
        and not any(o.technician_id == p.user.id or p.user.id in (o.helpers or []) for o in pr.orders)
    ):
        raise HTTPException(404, "Proyecto no encontrado")
    return pr


def consumed_cost(db: Session, pr: Project, fx: Decimal | None = None) -> Decimal:
    """Costo de los equipos y materiales realmente usados en las órdenes de trabajo."""
    total = Decimal(0)
    for o in pr.orders:
        for m in o.materials:
            prod = db.get(Product, m.product_id) if m.product_id else None
            total += pricing.cost_in(db, prod, "CRC", fx) * d(m.quantity)
    return total


def economics(db: Session, pr: Project, fx: Decimal | None = None) -> dict:
    """Lo que costo de verdad el proyecto contra lo que se vendio. Unica fuente: la usan la ficha y el reporte."""
    from ..routers.fieldwork import _hours

    fx = fx if fx is not None else today_fx(db, "USD")[0]
    materials = consumed_cost(db, pr, fx)
    real = materials + d(pr.cost_labor) + d(pr.cost_travel) + d(pr.cost_extra)
    return {
        "price": pr.price,
        "cost_planned": pr.cost_planned,
        "cost_materials": materials.quantize(Decimal("0.01")),
        "cost_labor": pr.cost_labor,
        "cost_travel": pr.cost_travel,
        "cost_extra": pr.cost_extra,
        "cost_real": real.quantize(Decimal("0.01")),
        "profit": (d(pr.price) - real).quantize(Decimal("0.01")),
        "margin_real": pricing.margin_of(d(pr.price), real),
        "margin_planned": pricing.margin_of(d(pr.price), d(pr.cost_planned)),
        "hours": round(sum((_hours(o.started_at, o.finished_at) or 0 for o in pr.orders), 0.0), 2),
    }


def _out(db: Session, pr: Project, p: Principal, full: bool = True) -> dict:
    c = db.get(Customer, pr.customer_id) if pr.customer_id else None
    sup = db.get(User, pr.supervisor_id) if pr.supervisor_id else None
    out = {
        "id": pr.id,
        "number": pr.number,
        "name": pr.name,
        "status": pr.status,
        "customer_id": pr.customer_id,
        "customer": c.name if c else None,
        "site": pr.site,
        "scope": pr.scope,
        "supervisor_id": pr.supervisor_id,
        "supervisor": sup.full_name if sup else None,
        "start_date": pr.start_date,
        "end_date": pr.end_date,
        "quote_id": pr.quote_id,
        "survey_id": pr.survey_id,
        "invoice_id": pr.invoice_id,
        "notes": pr.notes,
        "photos": pr.photos,
        "delivered_at": pr.delivered_at,
        "orders_total": len(pr.orders),
        "orders_done": sum(1 for o in pr.orders if o.status == "finalizada"),
        "created_at": pr.created_at,
    }
    if p.sees_prices:
        out["economics"] = economics(db, pr)
    if full:
        out["orders"] = [
            {
                "id": o.id,
                "number": o.number,
                "title": o.title,
                "status": o.status,
                "kind": o.kind,
                "scheduled_at": o.scheduled_at,
                "technician_id": o.technician_id,
                "finished_at": o.finished_at,
                "materials": len(o.materials),
            }
            for o in pr.orders
        ]
        out["assets"] = [
            {"id": a.id, "name": a.name, "serial": a.serial, "location": a.location, "warranty_until": a.warranty_until}
            for a in db.scalars(select(CustomerAsset).where(CustomerAsset.project_id == pr.id))
        ]
    return out


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    customer_id: int | None = None
    site: str | None = Field(None, max_length=300)
    scope: str | None = None
    supervisor_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = Field("planificado", pattern="^(planificado|en_curso|pausado|terminado|entregado|facturado|cancelado)$")
    cost_labor: Decimal = Field(Decimal(0), ge=0)
    cost_travel: Decimal = Field(Decimal(0), ge=0)
    cost_extra: Decimal = Field(Decimal(0), ge=0)
    notes: str | None = None
    photos: list = Field(default_factory=list)


@router.get("/projects")
def projects(status: str | None = None, limit: int = Query(100, le=300), p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    stmt = select(Project).where(Project.tenant_id == p.tenant.id)
    if status == "activos":
        stmt = stmt.where(Project.status.in_(("planificado", "en_curso", "pausado")))
    elif status:
        stmt = stmt.where(Project.status == status)
    rows = db.scalars(stmt.order_by(Project.id.desc()).limit(limit)).all()
    if not p.can("projects", "ver_todo"):
        rows = [pr for pr in rows if pr.supervisor_id == p.user.id or any(o.technician_id == p.user.id or p.user.id in (o.helpers or []) for o in pr.orders)]
    return [_out(db, pr, p, full=False) for pr in rows]


@router.post("/projects", status_code=201)
def project_create(data: ProjectIn, p: Principal = Depends(require("projects", "crear")), db: Session = Depends(get_db)):
    number, _ = next_number(db, p.tenant.id, "PRO")
    pr = Project(tenant_id=p.tenant.id, number=number, **data.model_dump())
    db.add(pr)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "project", pr.id, ip=p.ip)
    db.commit()
    return _out(db, pr, p)


class FromQuoteIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    site: str | None = Field(None, max_length=300)
    supervisor_id: int | None = None
    start_date: date | None = None


@router.post("/quotes/{qid}/project", status_code=201)
def project_from_quote(qid: int, data: FromQuoteIn, p: Principal = Depends(require("projects", "crear")), db: Session = Depends(get_db)):
    """El botón más importante: cotización aprobada -> proyecto, sin volver a escribir nada."""
    q = db.get(Quote, qid)
    if not q or q.tenant_id != p.tenant.id:
        raise HTTPException(404, "Cotización no encontrada")
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    if db.scalar(select(Project).where(Project.quote_id == q.id)):
        raise HTTPException(409, "Esa cotización ya tiene proyecto")
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    survey = db.scalar(select(Survey).where(Survey.quote_id == q.id))
    fx = today_fx(db, "USD")[0]
    cost_planned = Decimal(0)
    for ln in q.lines:
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        cost_planned += pricing.cost_in(db, prod, "CRC", fx) * d(ln.quantity)
    number, _ = next_number(db, p.tenant.id, "PRO")
    pr = Project(
        tenant_id=p.tenant.id,
        number=number,
        name=data.name or f"{c.name if c else 'Proyecto'} · {q.number}",
        customer_id=q.customer_id,
        quote_id=q.id,
        survey_id=survey.id if survey else None,
        opportunity_id=None,
        site=data.site or (survey.site if survey else (c.address or {}).get("senas") if c and c.address else None),
        scope=q.external_notes,
        supervisor_id=data.supervisor_id,
        start_date=data.start_date,
        status="planificado",
        price=d(q.total),
        cost_planned=cost_planned.quantize(Decimal("0.01")),
    )
    db.add(pr)
    db.flush()
    # primera orden de trabajo con los materiales de la cotización ya cargados
    o_number, _ = next_number(db, p.tenant.id, "OT")
    order = WorkOrder(
        tenant_id=p.tenant.id,
        number=o_number,
        project_id=pr.id,
        customer_id=pr.customer_id,
        title=f"Instalación · {pr.name}"[:200],
        kind="instalacion",
        site=pr.site,
        scheduled_at=None,
        tasks=[{"text": ln.name[:120], "done": False} for ln in q.lines][:20],
    )
    from ..models import WorkOrderMaterial

    for ln in q.lines:
        if ln.product_id:
            order.materials.append(WorkOrderMaterial(product_id=ln.product_id, name=ln.name, quantity=0, unit=ln.unit, planned=d(ln.quantity)))
    db.add(order)
    opp = db.scalar(select(Opportunity).where(Opportunity.quote_id == q.id))
    if opp:
        opp.status, opp.project_id = "ganada", pr.id
        pr.opportunity_id = opp.id
    if survey:
        survey.status = "cerrado"
    audit(db, p.tenant.id, p.user.id, "from_quote", "project", pr.id, {"quote": q.number}, ip=p.ip)
    db.commit()
    return _out(db, pr, p)


@router.get("/projects/{pid}")
def project_get(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    return _out(db, _project(db, pid, p), p)


@router.put("/projects/{pid}")
def project_update(pid: int, data: ProjectIn, p: Principal = Depends(require("projects", "editar")), db: Session = Depends(get_db)):
    pr = _project(db, pid, p)
    for k, v in data.model_dump().items():
        setattr(pr, k, v)
    if pr.status == "entregado" and not pr.delivered_at:
        pr.delivered_at = datetime.now(UTC)
    db.commit()
    return _out(db, pr, p)


@router.get("/projects/{pid}/requirements")
def requirements(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    """Materiales planificados contra existencias: qué hay en bodega y qué hay que comprar."""
    pr = _project(db, pid, p)
    levels = {}
    for lv in invsvc.stock_levels(db, p.tenant.id):
        levels[lv["product_id"]] = levels.get(lv["product_id"], Decimal(0)) + lv["quantity"]
    need: dict[int, dict] = {}
    for o in pr.orders:
        for m in o.materials:
            if not m.product_id:
                continue
            row = need.setdefault(m.product_id, {"product_id": m.product_id, "name": m.name, "planned": Decimal(0), "used": Decimal(0)})
            row["planned"] += d(m.planned)
            row["used"] += d(m.quantity)
    out = []
    for row in need.values():
        stock = levels.get(row["product_id"], Decimal(0))
        pending = max(row["planned"] - row["used"], Decimal(0))
        out.append({**row, "stock": stock, "pending": pending, "to_buy": max(pending - stock, Decimal(0))})
    return sorted(out, key=lambda r: -r["to_buy"])


@router.post("/projects/{pid}/purchase-request", status_code=201)
def purchase_from_project(pid: int, p: Principal = Depends(require("purchases", "crear")), db: Session = Depends(get_db)):
    """Lo que falta para el proyecto se convierte en solicitud de compra (los proveedores no dan crédito:
    conviene avisar temprano)."""
    pr = _project(db, pid, p)
    faltan = [r for r in requirements(pid, p, db) if r["to_buy"] > 0]
    if not faltan:
        raise HTTPException(409, "No falta material: todo está en bodega")
    return _new_request(db, p, faltan, reason="proyecto", project_id=pr.id, note=f"Materiales faltantes del proyecto {pr.number}")


def _new_request(db: Session, p: Principal, rows: list[dict], reason: str, project_id: int | None = None, note: str | None = None) -> dict:
    number, _ = next_number(db, p.tenant.id, "SC")
    req = PurchaseRequest(tenant_id=p.tenant.id, number=number, status="borrador", reason=reason, project_id=project_id, notes=note, created_by=p.user.id)
    total = Decimal(0)
    for r in rows:
        prod = db.get(Product, r["product_id"]) if r.get("product_id") else None
        cost = d(prod.cost) if prod and prod.cost is not None else Decimal(0)
        qty = d(r.get("to_buy") or r.get("quantity") or 0)
        req.lines.append(
            PurchaseRequestLine(product_id=r.get("product_id"), name=r.get("name") or (prod.name if prod else "Material"), quantity=qty, unit_cost=cost)
        )
        total += cost * qty
        if prod and prod.supplier_id and not req.supplier_id:
            req.supplier_id = prod.supplier_id
    req.total_cost = total.quantize(Decimal("0.01"))
    db.add(req)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "purchase_request", req.id, {"reason": reason, "lineas": len(req.lines)}, ip=p.ip)
    db.commit()
    return _req_out(db, req)


def _req_out(db: Session, r: PurchaseRequest) -> dict:
    s = db.get(Supplier, r.supplier_id) if r.supplier_id else None
    pr = db.get(Project, r.project_id) if r.project_id else None
    return {
        "id": r.id,
        "number": r.number,
        "status": r.status,
        "reason": r.reason,
        "supplier_id": r.supplier_id,
        "supplier": s.name if s else None,
        "project_id": r.project_id,
        "project": pr.number if pr else None,
        "total_cost": r.total_cost,
        "currency": r.currency,
        "notes": r.notes,
        "created_at": r.created_at,
        "lines": [{"id": x.id, "product_id": x.product_id, "name": x.name, "quantity": x.quantity, "unit_cost": x.unit_cost} for x in r.lines],
    }


@router.get("/purchase-requests")
def purchase_requests(status: str | None = None, p: Principal = Depends(require("purchases", "ver")), db: Session = Depends(get_db)):
    stmt = select(PurchaseRequest).where(PurchaseRequest.tenant_id == p.tenant.id)
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    return [_req_out(db, r) for r in db.scalars(stmt.order_by(PurchaseRequest.id.desc()).limit(100))]


class ReqLineIn(BaseModel):
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(1), gt=0)
    unit_cost: Decimal = Field(Decimal(0), ge=0)


class ReqIn(BaseModel):
    supplier_id: int | None = None
    project_id: int | None = None
    notes: str | None = None
    lines: list[ReqLineIn] = Field(min_length=1)


@router.post("/purchase-requests", status_code=201)
def purchase_create(data: ReqIn, p: Principal = Depends(require("purchases", "crear")), db: Session = Depends(get_db)):
    rows = [{"product_id": x.product_id, "name": x.name, "quantity": x.quantity, "to_buy": x.quantity} for x in data.lines]
    out = _new_request(db, p, rows, reason="manual", project_id=data.project_id, note=data.notes)
    if data.supplier_id:
        r = db.get(PurchaseRequest, out["id"])
        r.supplier_id = data.supplier_id
        db.commit()
        return _req_out(db, r)
    return out


class ReqStatusIn(BaseModel):
    status: str = Field(pattern="^(borrador|solicitada|recibida|cancelada)$")
    warehouse_id: int | None = None


@router.patch("/purchase-requests/{rid}")
def purchase_status(rid: int, data: ReqStatusIn, p: Principal = Depends(require("purchases", "crear")), db: Session = Depends(get_db)):
    """Al marcarla recibida entra a inventario con su costo."""
    r = db.get(PurchaseRequest, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Solicitud no encontrada")
    if data.status == "recibida" and r.status != "recibida":
        wh = data.warehouse_id or (invsvc.default_warehouse(db, p.tenant.id).id if invsvc.default_warehouse(db, p.tenant.id) else None)
        if not wh:
            raise HTTPException(422, "No hay bodega configurada")
        for ln in r.lines:
            if ln.product_id and d(ln.quantity) > 0:
                invsvc.move(
                    db, p.tenant.id, p.user.id, ln.product_id, wh, "entrada", d(ln.quantity), reference=r.number, note="Compra recibida", unit_cost=ln.unit_cost
                )
    r.status = data.status
    audit(db, p.tenant.id, p.user.id, "status", "purchase_request", r.id, {"status": data.status}, ip=p.ip)
    db.commit()
    return _req_out(db, r)


# ---------- Activos del cliente ----------
class AssetIn(BaseModel):
    customer_id: int
    project_id: int | None = None
    work_order_id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=2, max_length=200)
    model: str | None = Field(None, max_length=120)
    serial: str | None = Field(None, max_length=80)
    location: str | None = Field(None, max_length=200)
    ip: str | None = Field(None, max_length=45)
    mac: str | None = Field(None, max_length=32)
    firmware: str | None = Field(None, max_length=40)
    installed_at: date | None = None
    warranty_until: date | None = None
    supplier_id: int | None = None
    purchase_ref: str | None = Field(None, max_length=120)
    status: str = Field("activo", pattern="^(activo|retirado|garantia|reemplazado)$")
    notes: str | None = None
    photos: list = Field(default_factory=list)


def _asset_out(db: Session, a: CustomerAsset) -> dict:
    c = db.get(Customer, a.customer_id)
    pr = db.get(Project, a.project_id) if a.project_id else None
    return {
        **{
            k: getattr(a, k)
            for k in (
                "id",
                "customer_id",
                "project_id",
                "work_order_id",
                "product_id",
                "name",
                "model",
                "serial",
                "location",
                "ip",
                "mac",
                "firmware",
                "installed_at",
                "warranty_until",
                "supplier_id",
                "purchase_ref",
                "status",
                "notes",
                "photos",
            )
        },
        "customer": c.name if c else None,
        "project": pr.number if pr else None,
        "warranty_days": (a.warranty_until - date.today()).days if a.warranty_until else None,
    }


@router.get("/assets")
def assets(
    customer_id: int | None = None,
    q: str | None = None,
    expiring: bool = False,
    p: Principal = Depends(require("assets", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(CustomerAsset).where(CustomerAsset.tenant_id == p.tenant.id)
    if customer_id:
        stmt = stmt.where(CustomerAsset.customer_id == customer_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((CustomerAsset.name.ilike(like)) | (CustomerAsset.serial.ilike(like)) | (CustomerAsset.location.ilike(like)))
    rows = db.scalars(stmt.order_by(CustomerAsset.id.desc()).limit(300)).all()
    if expiring:
        rows = [a for a in rows if a.warranty_until and 0 <= (a.warranty_until - date.today()).days <= 45]
    return [_asset_out(db, a) for a in rows]


@router.post("/assets", status_code=201)
def asset_create(data: AssetIn, p: Principal = Depends(require("assets", "crear")), db: Session = Depends(get_db)):
    a = CustomerAsset(tenant_id=p.tenant.id, **data.model_dump())
    db.add(a)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "asset", a.id, ip=p.ip)
    db.commit()
    return _asset_out(db, a)


@router.put("/assets/{aid}")
def asset_update(aid: int, data: AssetIn, p: Principal = Depends(require("assets", "editar")), db: Session = Depends(get_db)):
    a = db.get(CustomerAsset, aid)
    if not a or a.tenant_id != p.tenant.id:
        raise HTTPException(404, "Activo no encontrado")
    for k, v in data.model_dump().items():
        setattr(a, k, v)
    db.commit()
    return _asset_out(db, a)


# ---------- Informe de entrega ----------
@router.get("/projects/{pid}/report", response_class=HTMLResponse)
def delivery_report(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    """Informe técnico de entrega con fotografías, equipos instalados y tiempos. Imprimible o a PDF."""
    pr = _project(db, pid, p)
    c = db.get(Customer, pr.customer_id) if pr.customer_id else None
    esc = html.escape
    orders = ""
    photos: list[str] = list(pr.photos or [])
    for o in pr.orders:
        t = db.get(User, o.technician_id) if o.technician_id else None
        tasks = "".join(f"<li>{'☑' if x.get('done') else '☐'} {esc(str(x.get('text', '')))}</li>" for x in (o.tasks or []))
        mats = "".join(f"<tr><td>{esc(m.name)}</td><td class=n>{d(m.quantity):g} {esc(m.unit)}</td></tr>" for m in o.materials if d(m.quantity) > 0)
        photos += list(o.photos or [])
        hrs = f"{round((o.finished_at - o.started_at).total_seconds() / 3600, 1)} h" if o.started_at and o.finished_at else "—"
        orders += f"""<section class=block><h3>{esc(o.number)} · {esc(o.title)}</h3>
        <div class=m>{esc(t.full_name if t else "Sin técnico")} · {o.finished_at.astimezone().strftime("%d/%m/%Y") if o.finished_at else "en curso"} · {hrs}</div>
        {f"<ul class=tasks>{tasks}</ul>" if tasks else ""}
        {f"<table class=t><tr><th>Material utilizado</th><th class=n>Cantidad</th></tr>{mats}</table>" if mats else ""}
        {f"<p class=obs>{esc(o.notes)}</p>" if o.notes else ""}</section>"""
    assets = db.scalars(select(CustomerAsset).where(CustomerAsset.project_id == pr.id)).all()
    assets_html = "".join(
        f"<tr><td>{esc(a.name)}</td><td class=mono>{esc(a.serial or '—')}</td><td>{esc(a.location or '—')}</td><td>{a.warranty_until or '—'}</td></tr>"
        for a in assets
    )
    gal = "".join(f'<img src="{esc(u)}" alt="">' for u in photos[:24])
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><title>Informe {esc(pr.number)}</title>
<style>body{{font-family:Manrope,system-ui,sans-serif;color:#15131a;max-width:820px;margin:28px auto;padding:0 18px}}
h1{{font-size:26px;margin:0}}h3{{font-size:16px;margin:0 0 4px}}.m{{color:#6b6570;font-size:12.5px}}
.brand{{border-left:4px solid #e2233a;padding-left:12px;margin-bottom:20px}}
.block{{border:1px solid #eee;border-radius:12px;padding:14px;margin:12px 0}}
.t{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}}.t td,.t th{{border-bottom:1px solid #eee;padding:6px 0;text-align:left}}
.n{{text-align:right}}.mono{{font-family:ui-monospace,monospace}}.tasks{{font-size:13px;columns:2;padding-left:18px}}
.obs{{font-size:13px;background:#faf7f4;padding:10px;border-radius:8px}}
.gal{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:10px}}
.gal img{{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:8px}}
@media print{{button{{display:none}}.block{{break-inside:avoid}}}}</style></head><body>
<div class=brand><h1>{esc(p.tenant.name)}</h1><div class=m>Informe técnico de entrega · {esc(pr.number)}</div></div>
<h2 style="font-size:20px;margin:0 0 6px">{esc(pr.name)}</h2>
<div class=m>{esc(c.name if c else "")}{" · " + esc(pr.site) if pr.site else ""} · {pr.start_date or ""} a {pr.end_date or pr.delivered_at.date() if pr.delivered_at else ""}</div>
{f"<p>{esc(pr.scope)}</p>" if pr.scope else ""}
{orders}
{f"<section class=block><h3>Equipos instalados</h3><table class=t><tr><th>Equipo</th><th>Serie</th><th>Ubicación</th><th>Garantía</th></tr>{assets_html}</table></section>" if assets_html else ""}
{f"<section class=block><h3>Evidencias</h3><div class=gal>{gal}</div></section>" if gal else ""}
<p class=m>Recibido a satisfacción por: ______________________________  ·  Fecha: ____________</p>
<button onclick=print()>Imprimir</button></body></html>"""


@router.post("/projects/{pid}/invoice", status_code=201)
def project_invoice(pid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Proyecto entregado -> factura con las líneas de la cotización original."""
    pr = _project(db, pid, p)
    if pr.invoice_id:
        raise HTTPException(409, "El proyecto ya fue facturado")
    if not pr.quote_id:
        raise HTTPException(422, "El proyecto no viene de una cotización; facturá desde Facturas")
    q = db.get(Quote, pr.quote_id)
    from ..services import documents as svc

    ya_convertida = q.status == "convertida"
    inv = db.get(Invoice, q.converted_invoice_id) if ya_convertida else svc.convert_quote(db, p.tenant.id, p.user.id, q)
    # Si las ordenes ya sacaron el material de bodega, la factura no vuelve a descontarlo: seria contarlo dos veces.
    if not ya_convertida and not any(o.stock_applied for o in pr.orders):
        invsvc.deduct_for_invoice(db, inv, p.user.id)
    pr.invoice_id, pr.status = inv.id, "facturado"
    audit(db, p.tenant.id, p.user.id, "invoice", "project", pr.id, {"invoice_id": inv.id}, ip=p.ip)
    db.commit()
    return {"invoice_id": inv.id, "number": inv.number}
