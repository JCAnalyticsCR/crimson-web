"""Ventas etapa 3: metas y tablero, agenda y tareas, kits de cotizacion y plantillas de mensaje.

Permisos:
- Tablero y poner metas: admin y jefe de ventas (crm.cartera). El vendedor ve su avance en su inicio (/dashboard).
- Tareas: quien ve oportunidades (crm_pipeline.ver); crear y cerrar, crm_pipeline.editar. Alcance en services/agenda.
- Kits: insertarlos quien crea ventas (sales.crear); armarlos, el admin (settings.configurar).
- Plantillas: usarlas quien envia (sales.enviar); escribirlas, el admin.
Nunca costo ni margen en ninguna respuesta de aqui.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, Product, Quote, User
from ..models.ventas3 import MessageTemplate, QuoteKit, QuoteKitLine, SalesTask
from ..services import agenda, metas
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(tags=["ventas"])
ADMIN = require("settings", "configurar")


# ---------- Metas y tablero ----------
@router.get("/goals/board")
def goals_board(month: str | None = None, p: Principal = Depends(require("crm", "cartera")), db: Session = Depends(get_db)):
    return metas.board(db, p, metas.parse_month(month))


class GoalIn(BaseModel):
    user_id: int = Field(ge=1)
    month: str | None = None  # AAAA-MM; sin dato, el mes en curso
    amount: Decimal = Field(ge=0, le=Decimal("999999999999"))


@router.put("/goals")
def goal_set(data: GoalIn, p: Principal = Depends(require("crm", "cartera")), db: Session = Depends(get_db)):
    m = metas.parse_month(data.month)
    metas.set_goal(db, p, data.user_id, m, data.amount)
    db.commit()
    return metas.progress(db, p.tenant, data.user_id, m)


@router.get("/goals/me")
def goal_me(month: str | None = None, p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    return metas.progress(db, p.tenant, p.user.id, metas.parse_month(month))


# ---------- Agenda y tareas ----------
class TaskIn(BaseModel):
    kind: Literal["llamada", "visita", "recordatorio", "facturar"] = "llamada"
    title: str = Field(min_length=2, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    due_at: datetime  # con zona; sin zona se toma como hora de Costa Rica
    assigned_to: int | None = None
    customer_id: int | None = None
    opportunity_id: int | None = None
    quote_id: int | None = None


def _due(v: datetime) -> datetime:
    from ..services.documents import CR

    return (v if v.tzinfo else v.replace(tzinfo=CR)).astimezone(UTC)


@router.get("/tasks")
def tasks(
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    quote_id: int | None = None,
    estado: Literal["pendientes", "hechas", "todas"] = "pendientes",
    mias: bool = False,
    limit: int = Query(50, le=200),
    p: Principal = Depends(require("crm_pipeline", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(SalesTask).where(SalesTask.tenant_id == p.tenant.id, agenda.scope(p))
    if mias:
        stmt = stmt.where(SalesTask.assigned_to == p.user.id)
    if customer_id:
        stmt = stmt.where(SalesTask.customer_id == customer_id)
    if opportunity_id:
        stmt = stmt.where(SalesTask.opportunity_id == opportunity_id)
    if quote_id:
        stmt = stmt.where(SalesTask.quote_id == quote_id)
    if estado == "pendientes":
        stmt = stmt.where(SalesTask.done_at.is_(None))
    elif estado == "hechas":
        stmt = stmt.where(SalesTask.done_at.is_not(None))
    rows = db.scalars(stmt.order_by(SalesTask.due_at, SalesTask.id).limit(limit)).all()
    return [agenda.out(db, t) for t in rows]


@router.get("/tasks/today")
def tasks_today(p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """La lista "Hoy" del inicio: lo suyo de hoy y lo atrasado, sin hacer."""
    return [agenda.out(db, t) for t in agenda.pending_today(db, p.tenant.id, p.user.id)]


@router.post("/tasks", status_code=201)
def task_create(data: TaskIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    cid, oid, qid = agenda.check_links(db, p, data.customer_id, data.opportunity_id, data.quote_id)
    t = SalesTask(
        tenant_id=p.tenant.id, kind=data.kind, title=data.title.strip(), notes=(data.notes or "").strip() or None, due_at=_due(data.due_at),
        assigned_to=agenda.assignee(db, p, data.assigned_to), customer_id=cid, opportunity_id=oid, quote_id=qid, created_by=p.user.id,
    )
    db.add(t)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "sales_task", t.id, {"tipo": t.kind, "para": t.assigned_to}, ip=p.ip)
    db.commit()
    return agenda.out(db, t)


class TaskPatch(BaseModel):
    done: bool | None = None
    title: str | None = Field(None, min_length=2, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    due_at: datetime | None = None


@router.patch("/tasks/{tid}")
def task_update(tid: int, data: TaskPatch, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    t = agenda.get(db, p, tid)
    if data.title is not None:
        t.title = data.title.strip()
    if data.notes is not None:
        t.notes = data.notes.strip() or None
    if data.due_at is not None:
        t.due_at = _due(data.due_at)
    if data.done is not None and bool(t.done_at) != data.done:
        agenda.mark_done(db, p, t, data.done)
    db.commit()
    return agenda.out(db, t)


# ---------- Kits ----------
class KitLineIn(BaseModel):
    product_id: int = Field(ge=1)
    quantity: Decimal = Field(Decimal(1), gt=0, le=Decimal("100000"))


class KitIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(None, max_length=2000)
    active: bool = True
    lines: list[KitLineIn] = Field(min_length=1, max_length=60)


def _kit_out(db: Session, k: QuoteKit, p: Principal) -> dict:
    lines = []
    for ln in k.lines:
        pr = db.get(Product, ln.product_id)
        lines.append({"product_id": ln.product_id, "quantity": ln.quantity, "name": pr.name if pr else "Producto eliminado", "code": pr.code if pr else None, "active": bool(pr and pr.active)})
    return {"id": k.id, "name": k.name, "description": k.description, "active": k.active, "lines": lines}


def _kit(db: Session, p: Principal, kid: int) -> QuoteKit:
    k = db.get(QuoteKit, kid)
    if not k or k.tenant_id != p.tenant.id:
        raise HTTPException(404, "Kit no encontrado")
    return k


def _check_products(db: Session, p: Principal, data: KitIn) -> None:
    for ln in data.lines:
        pr = db.get(Product, ln.product_id)
        if not pr or pr.tenant_id != p.tenant.id:
            raise HTTPException(404, "Producto no encontrado")


@router.get("/kits")
def kits(todos: bool = False, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    stmt = select(QuoteKit).where(QuoteKit.tenant_id == p.tenant.id)
    if not (todos and p.can("settings", "configurar")):
        stmt = stmt.where(QuoteKit.active.is_(True))
    return [_kit_out(db, k, p) for k in db.scalars(stmt.order_by(QuoteKit.name))]


@router.post("/kits", status_code=201)
def kit_create(data: KitIn, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    _check_products(db, p, data)
    k = QuoteKit(tenant_id=p.tenant.id, name=data.name.strip(), description=data.description, active=data.active)
    k.lines = [QuoteKitLine(product_id=ln.product_id, quantity=ln.quantity, position=i) for i, ln in enumerate(data.lines)]
    db.add(k)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "quote_kit", k.id, {"nombre": k.name, "lineas": len(k.lines)}, ip=p.ip)
    db.commit()
    return _kit_out(db, k, p)


@router.put("/kits/{kid}")
def kit_update(kid: int, data: KitIn, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    k = _kit(db, p, kid)
    _check_products(db, p, data)
    k.name, k.description, k.active = data.name.strip(), data.description, data.active
    k.lines = [QuoteKitLine(product_id=ln.product_id, quantity=ln.quantity, position=i) for i, ln in enumerate(data.lines)]
    audit(db, p.tenant.id, p.user.id, "update", "quote_kit", k.id, {"nombre": k.name, "lineas": len(k.lines), "activo": k.active}, ip=p.ip)
    db.commit()
    return _kit_out(db, k, p)


@router.get("/kits/{kid}/lines")
def kit_lines(kid: int, currency: str = Query("CRC", pattern="^[A-Z]{3}$"), p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Las lineas del kit listas para el editor, con el precio de catalogo de HOY en la moneda de la cotizacion. Se
    guardan como cualquier linea: el tope de descuento, el margen minimo y la aprobacion aplican igual."""
    from ..schemas.sales import LineInSchema
    from ..services.documents import _fill_line_from_product

    k = _kit(db, p, kid)
    if not k.active:
        raise HTTPException(409, "Este kit está desactivado")
    out, omitidos = [], []
    for ln in k.lines:
        pr = db.get(Product, ln.product_id)
        if not pr or pr.tenant_id != p.tenant.id or not pr.active:
            omitidos.append(pr.name if pr else "producto eliminado")
            continue
        filled = _fill_line_from_product(db, p.tenant.id, LineInSchema(product_id=pr.id, quantity=ln.quantity, unit=None), currency)
        out.append({**filled.model_dump(mode="json"), "unit": filled.unit or "Unid"})
    return {"lines": out, "omitidos": omitidos}


# ---------- Plantillas de mensaje ----------
VARIABLES = ("cliente", "numero", "total", "enlace", "vendedor", "validez", "empresa")


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    channel: Literal["correo", "whatsapp"] = "correo"
    body: str = Field(min_length=2, max_length=4000)
    active: bool = True


def _tpl_out(t: MessageTemplate) -> dict:
    return {"id": t.id, "name": t.name, "channel": t.channel, "body": t.body, "active": t.active}


@router.get("/message-templates")
def templates(todos: bool = False, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    stmt = select(MessageTemplate).where(MessageTemplate.tenant_id == p.tenant.id)
    if not (todos and p.can("settings", "configurar")):
        stmt = stmt.where(MessageTemplate.active.is_(True))
    return {"variables": list(VARIABLES), "items": [_tpl_out(t) for t in db.scalars(stmt.order_by(MessageTemplate.channel, MessageTemplate.name))]}


@router.post("/message-templates", status_code=201)
def template_create(data: TemplateIn, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    t = MessageTemplate(tenant_id=p.tenant.id, **data.model_dump())
    db.add(t)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "message_template", t.id, {"nombre": t.name}, ip=p.ip)
    db.commit()
    return _tpl_out(t)


@router.put("/message-templates/{tid}")
def template_update(tid: int, data: TemplateIn, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    t = db.get(MessageTemplate, tid)
    if not t or t.tenant_id != p.tenant.id:
        raise HTTPException(404, "Plantilla no encontrada")
    for k, v in data.model_dump().items():
        setattr(t, k, v)
    audit(db, p.tenant.id, p.user.id, "update", "message_template", t.id, {"nombre": t.name}, ip=p.ip)
    db.commit()
    return _tpl_out(t)


def _fill(text: str, values: dict[str, str]) -> str:
    out = text
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


@router.post("/quotes/{qid}/message")
def quote_message(qid: int, template_id: int | None = None, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    """El texto de la plantilla con los datos de la cotizacion y su enlace de aceptacion en linea (lo crea si hace
    falta). Para WhatsApp devuelve tambien el enlace wa.me al telefono del cliente."""
    from urllib.parse import quote as urlquote

    from ..services import quote_links
    from ..services.ownership import owns_doc
    from ..services.render import money

    q = db.get(Quote, qid)
    if not q or q.tenant_id != p.tenant.id or not owns_doc(q, p):
        raise HTTPException(404, "Documento no encontrado")
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    t = None
    if template_id:
        t = db.get(MessageTemplate, template_id)
        if not t or t.tenant_id != p.tenant.id or not t.active:
            raise HTTPException(404, "Plantilla no encontrada")
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    link = quote_links.get_or_create(db, p.tenant, q)
    seller = db.get(User, q.seller_id or q.created_by) if (q.seller_id or q.created_by) else None
    values = {
        "cliente": c.name if c else "",
        "numero": q.number,
        "total": money(d(q.total), q.currency),
        "enlace": quote_links.url_of(link) if link else "",
        "vendedor": (seller.full_name if seller else "") or "",
        "validez": q.due_date.strftime("%d/%m/%Y") if q.due_date else "",
        "empresa": p.tenant.name,
    }
    default = "Hola {cliente}, le compartimos la cotización {numero} por {total}." + (" Puede verla y aceptarla aquí: {enlace}" if link else "")
    text = _fill(t.body if t else default, values)
    phone = "".join(ch for ch in ((c.whatsapp or c.phone) if c else "") or "" if ch.isdigit())
    if len(phone) == 8:
        phone = "506" + phone
    db.commit()
    return {"text": text, "whatsapp_url": f"https://wa.me/{phone}?text={urlquote(text)}" if phone else f"https://wa.me/?text={urlquote(text)}", "link": values["enlace"] or None}
