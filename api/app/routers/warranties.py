"""Garantias: recibir el equipo del cliente, revisarlo en Crimson, mandarlo al distribuidor y devolverlo (Andres, 19/10).
La logica y el kardex viven en services/warranty.py; aca solo entrada, permisos y salida."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, CustomerAsset, Product, Supplier, SupportTicket, User, WarrantyCase
from ..services import warranty as ws
from ..services.documents import audit
from ..services.refs import check_refs
from ..services.sequences import next_number

router = APIRouter(tags=["garantias"])

REFS = {
    "customer_id": (Customer, "Cliente no encontrado"),
    "ticket_id": (SupportTicket, "Ticket no encontrado"),
    "asset_id": (CustomerAsset, "Equipo del cliente no encontrado"),
    "product_id": (Product, "Producto no encontrado"),
    "supplier_id": (Supplier, "Distribuidor no encontrado"),
}


def _out(db: Session, c: WarrantyCase, full: bool = False) -> dict:
    cust = db.get(Customer, c.customer_id)
    sup = db.get(Supplier, c.supplier_id) if c.supplier_id else None
    rep = db.get(Product, c.replacement_product_id) if c.replacement_product_id else None
    ticket = db.get(SupportTicket, c.ticket_id) if c.ticket_id else None
    created = c.created_at.replace(tzinfo=UTC) if c.created_at and c.created_at.tzinfo is None else c.created_at
    out = {
        "id": c.id,
        "number": c.number,
        "status": c.status,
        "status_label": ws.STATE_LABEL.get(c.status, c.status),
        "customer_id": c.customer_id,
        "customer": cust.name if cust else None,
        "ticket_id": c.ticket_id,
        "ticket_number": ticket.number if ticket else None,
        "asset_id": c.asset_id,
        "product_id": c.product_id,
        "equipment": c.equipment,
        "serial": c.serial,
        "quantity": c.quantity,
        "problem": c.problem,
        "review": c.review,
        "review_label": ws.REVIEW_LABEL.get(c.review or ""),
        "review_note": c.review_note,
        "supplier_id": c.supplier_id,
        "supplier": sup.name if sup else None,
        "supplier_ref": c.supplier_ref,
        "sent_at": c.sent_at,
        "returned_at": c.returned_at,
        "result": c.result,
        "result_label": ws.RESULT_LABEL.get(c.result or ""),
        "new_serial": c.new_serial,
        "replacement_product_id": c.replacement_product_id,
        "replacement": rep.name if rep else None,
        "replacement_qty": c.replacement_qty,
        "replacement_serial": c.replacement_serial,
        "closed_at": c.closed_at,
        "closing_note": c.closing_note,
        "created_at": c.created_at,
        "days_open": (datetime.now(UTC) - created).days if created and c.status in ws.OPEN else None,
    }
    if full:
        users = {u.id: u.full_name or u.email for u in db.scalars(select(User).where(User.id.in_({h.get("by") for h in (c.history or []) if h.get("by")})))}
        out["history"] = [{**h, "by_name": users.get(h.get("by"))} for h in (c.history or [])]
    return out


def _get(db: Session, p: Principal, wid: int) -> WarrantyCase:
    c = db.get(WarrantyCase, wid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Garantía no encontrada")
    return c


@router.get("/warranties")
def warranties(
    status: str | None = Query(None, pattern="^(abiertas|recibido|en_distribuidor|devuelto|cerrado|anulado)$"),
    q: str | None = Query(None, max_length=80),
    customer_id: int | None = None,
    ticket_id: int | None = None,
    p: Principal = Depends(require("garantias", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(WarrantyCase).where(WarrantyCase.tenant_id == p.tenant.id)
    if status == "abiertas":
        stmt = stmt.where(WarrantyCase.status.in_(ws.OPEN))
    elif status:
        stmt = stmt.where(WarrantyCase.status == status)
    if customer_id:
        stmt = stmt.where(WarrantyCase.customer_id == customer_id)
    if ticket_id:
        stmt = stmt.where(WarrantyCase.ticket_id == ticket_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(WarrantyCase.number.ilike(like), WarrantyCase.equipment.ilike(like), WarrantyCase.serial.ilike(like), WarrantyCase.supplier_ref.ilike(like))
        )
    rows = db.scalars(stmt.order_by(WarrantyCase.id.desc()).limit(300)).all()
    return {"items": [_out(db, c) for c in rows], "counts": {s: sum(1 for c in rows if c.status == s) for s in ws.STATE_LABEL}}


@router.get("/warranties/{wid}")
def warranty(wid: int, p: Principal = Depends(require("garantias", "ver")), db: Session = Depends(get_db)):
    return _out(db, _get(db, p, wid), full=True)


class WarrantyIn(BaseModel):
    customer_id: int
    ticket_id: int | None = None
    asset_id: int | None = None
    product_id: int | None = None
    equipment: str | None = Field(None, max_length=200)
    serial: str | None = Field(None, max_length=80)
    quantity: Decimal = Field(Decimal(1), gt=0, le=1000)
    problem: str | None = Field(None, max_length=4000)


@router.post("/warranties", status_code=201)
def warranty_create(data: WarrantyIn, p: Principal = Depends(require("garantias", "crear")), db: Session = Depends(get_db)):
    """Crimson recibe el equipo del cliente: entra a la bodega de revision (nunca a la de venta)."""
    check_refs(db, p.tenant.id, data.model_dump(), REFS)
    asset = db.get(CustomerAsset, data.asset_id) if data.asset_id else None
    if asset and asset.customer_id != data.customer_id:
        raise HTTPException(422, "Ese equipo es de otro cliente")
    ticket = db.get(SupportTicket, data.ticket_id) if data.ticket_id else None
    if ticket and ticket.customer_id and ticket.customer_id != data.customer_id:
        raise HTTPException(422, "Ese ticket es de otro cliente")
    product_id = data.product_id or (asset.product_id if asset else None)
    prod = db.get(Product, product_id) if product_id else None
    equipment = (data.equipment or "").strip() or (asset.name if asset else None) or (prod.name if prod else None)
    if not equipment:
        raise HTTPException(422, "Indique qué equipo es (o elija el equipo del cliente o un producto)")
    number, _ = next_number(db, p.tenant.id, "GAR")
    c = WarrantyCase(
        tenant_id=p.tenant.id,
        number=number,
        customer_id=data.customer_id,
        ticket_id=data.ticket_id,
        asset_id=data.asset_id,
        product_id=product_id,
        equipment=equipment[:200],
        serial=(data.serial or "").strip() or (asset.serial if asset else None),
        quantity=data.quantity,
        problem=data.problem,
        status="recibido",
        created_by=p.user.id,
        history=[],
    )
    db.add(c)
    db.flush()
    ws.note(c, p.user.id, "Equipo recibido del cliente: pasa a revisión de Crimson")
    if asset is not None:
        asset.status = "garantia"
    ws.reconcile(db, c, p.user.id, "Garantía · equipo recibido")
    audit(db, p.tenant.id, p.user.id, "create", "warranty", c.id, {"number": c.number}, ip=p.ip)
    db.commit()
    return _out(db, c, full=True)


class ActionIn(BaseModel):
    action: str = Field(pattern="^(revisar|enviar|recibir|reemplazar|cerrar|anular)$")
    review: str | None = Field(None, pattern="^(procede|no_procede)$")
    result: str | None = Field(None, pattern="^(reparado|reemplazado|rechazado)$")
    supplier_id: int | None = None
    supplier_ref: str | None = Field(None, max_length=80)
    new_serial: str | None = Field(None, max_length=80)
    product_id: int | None = None
    quantity: Decimal | None = Field(None, gt=0, le=1000)
    serial: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=1000)


@router.post("/warranties/{wid}/action")
def warranty_action(wid: int, data: ActionIn, p: Principal = Depends(require("garantias", "editar")), db: Session = Depends(get_db)):
    c = _get(db, p, wid)
    if data.action == "revisar" and not data.review:
        raise HTTPException(422, "Indicá si la garantía procede o no")
    if data.action == "recibir" and not data.result:
        raise HTTPException(422, "Indicá qué hizo el distribuidor (reparado, reemplazado o rechazado)")
    if data.action == "anular" and not (data.note or "").strip():
        raise HTTPException(422, "Escribí por qué se anula")
    check_refs(db, p.tenant.id, data.model_dump(include={"supplier_id", "product_id"}), REFS)
    n = ws.apply(db, c, data.action, data.model_dump(exclude_none=True), p.user.id)
    audit(db, p.tenant.id, p.user.id, data.action, "warranty", c.id, {"status": c.status, "movimientos": n}, ip=p.ip)
    db.commit()
    return _out(db, c, full=True)
