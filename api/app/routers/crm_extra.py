"""Ficha de cliente (contactos, notas, resumen y linea de tiempo) y variantes / link de producto."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import (
    Customer,
    CustomerAsset,
    CustomerContact,
    CustomerNote,
    Invoice,
    Opportunity,
    Order,
    Payment,
    Product,
    ProductVariant,
    Project,
    Quote,
    User,
)
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(tags=["crm"])


def _customer(db: Session, cid: int, tid: int) -> Customer:
    c = db.get(Customer, cid)
    if not c or c.tenant_id != tid:
        raise HTTPException(404, "Cliente no encontrado")
    return c


# ---------- Contactos ----------
class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    role: str | None = Field(None, max_length=80)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    receives_invoices: bool = False


def _contact_out(c: CustomerContact) -> dict:
    return {"id": c.id, "name": c.name, "role": c.role, "email": c.email, "phone": c.phone, "receives_invoices": c.receives_invoices}


@router.get("/customers/{cid}/contacts")
def contacts(cid: int, p: Principal = Depends(require("crm", "ver")), db: Session = Depends(get_db)):
    _customer(db, cid, p.tenant.id)
    return [_contact_out(c) for c in db.scalars(select(CustomerContact).where(CustomerContact.customer_id == cid).order_by(CustomerContact.id))]


@router.post("/customers/{cid}/contacts", status_code=201)
def contact_create(cid: int, data: ContactIn, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    _customer(db, cid, p.tenant.id)
    c = CustomerContact(tenant_id=p.tenant.id, customer_id=cid, **data.model_dump())
    db.add(c)
    db.commit()
    return _contact_out(c)


@router.put("/customers/{cid}/contacts/{kid}")
def contact_update(cid: int, kid: int, data: ContactIn, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    c = db.get(CustomerContact, kid)
    if not c or c.tenant_id != p.tenant.id or c.customer_id != cid:
        raise HTTPException(404, "Contacto no encontrado")
    for k, v in data.model_dump().items():
        setattr(c, k, v)
    db.commit()
    return _contact_out(c)


@router.delete("/customers/{cid}/contacts/{kid}", status_code=204)
def contact_delete(cid: int, kid: int, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    c = db.get(CustomerContact, kid)
    if not c or c.tenant_id != p.tenant.id or c.customer_id != cid:
        raise HTTPException(404, "Contacto no encontrado")
    db.delete(c)
    db.commit()


# ---------- Notas ----------
class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/customers/{cid}/notes", status_code=201)
def note_create(cid: int, data: NoteIn, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    _customer(db, cid, p.tenant.id)
    n = CustomerNote(tenant_id=p.tenant.id, customer_id=cid, body=data.body, created_by=p.user.id)
    db.add(n)
    db.commit()
    return {"id": n.id}


@router.delete("/customers/{cid}/notes/{nid}", status_code=204)
def note_delete(cid: int, nid: int, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    n = db.get(CustomerNote, nid)
    if not n or n.tenant_id != p.tenant.id or n.customer_id != cid:
        raise HTTPException(404, "Nota no encontrada")
    db.delete(n)
    db.commit()


@router.delete("/customers/{cid}", status_code=204)
def customer_archive(cid: int, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    """Archiva (no borra): el cliente puede tener documentos fiscales que deben conservarse 5 anos."""
    c = _customer(db, cid, p.tenant.id)
    c.active = False
    audit(db, p.tenant.id, p.user.id, "archive", "customer", cid, ip=p.ip)
    db.commit()


# ---------- Resumen + linea de tiempo ----------
def _ts(v) -> str:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat()
    return f"{v.isoformat()}T12:00:00+00:00"


@router.get("/customers/{cid}/overview")
def overview(cid: int, p: Principal = Depends(require("crm", "ver")), db: Session = Depends(get_db)):
    c = _customer(db, cid, p.tenant.id)
    tid = p.tenant.id
    own_i = [Invoice.created_by == p.user.id] if not p.sees_all_sales else []
    own_q = [Quote.created_by == p.user.id] if not p.sees_all_sales else []
    invs = (
        db.scalars(select(Invoice).where(Invoice.tenant_id == tid, Invoice.customer_id == cid, *own_i).order_by(Invoice.issue_date.desc())).all()
        if p.can("sales", "ver")
        else []
    )
    quotes = (
        db.scalars(select(Quote).where(Quote.tenant_id == tid, Quote.customer_id == cid, *own_q).order_by(Quote.issue_date.desc()).limit(50)).all()
        if p.can("sales", "ver")
        else []
    )
    live = [i for i in invs if i.status != "anulada"]
    billed = sum((d(i.total) for i in live), Decimal(0))
    due = sum((d(i.balance) for i in live), Decimal(0))
    inv_ids = [i.id for i in invs]
    pays = (
        db.scalars(select(Payment).where(Payment.tenant_id == tid, Payment.invoice_id.in_(inv_ids)).order_by(Payment.paid_at.desc()).limit(50)).all()
        if inv_ids
        else []
    )
    orders = db.scalars(select(Order).where(Order.tenant_id == tid, Order.customer_id == cid).order_by(Order.id.desc()).limit(30)).all()
    notes = db.scalars(select(CustomerNote).where(CustomerNote.customer_id == cid).order_by(CustomerNote.id.desc())).all()
    authors = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({n.created_by for n in notes if n.created_by})))} if notes else {}

    events = []
    for q in quotes:
        events.append(
            {
                "at": _ts(q.issue_date),
                "kind": "cotizacion",
                "title": f"Cotización {q.number}",
                "amount": q.total,
                "currency": q.currency,
                "status": q.status,
                "to": f"/cotizaciones/{q.id}",
            }
        )
    for i in invs[:60]:
        events.append(
            {
                "at": _ts(i.issue_date),
                "kind": "factura",
                "title": f"{i.doc_type} {i.number}",
                "amount": i.total,
                "currency": i.currency,
                "status": i.status,
                "to": f"/facturas/{i.id}",
            }
        )
    for pay in pays:
        events.append(
            {
                "at": _ts(pay.paid_at),
                "kind": "pago",
                "title": f"Pago · {pay.method}",
                "amount": pay.amount,
                "currency": pay.currency,
                "status": pay.status,
                "to": f"/facturas/{pay.invoice_id}",
            }
        )
    for o in orders:
        events.append(
            {
                "at": _ts(o.created_at),
                "kind": "orden",
                "title": f"Pedido {o.number}",
                "amount": o.total,
                "currency": o.currency,
                "status": o.status,
                "to": "/ordenes",
            }
        )
    for n in notes:
        events.append({"at": _ts(n.created_at), "kind": "nota", "id": n.id, "title": n.body, "author": authors.get(n.created_by), "status": None})
    events.sort(key=lambda e: e["at"], reverse=True)

    return {
        "customer": {
            "id": c.id,
            "name": c.name,
            "id_type": c.id_type,
            "id_number": c.id_number,
            "email": c.email,
            "phone": c.phone,
            "whatsapp": c.whatsapp,
            "currency": c.currency,
            "notes": c.notes,
            "address": c.address,
            "active": c.active,
            "created_at": c.created_at,
        },
        "kpis": {
            "billed": billed,
            "due": due,
            "invoices": len(live),
            "quotes": len(quotes),
            "last_purchase": live[0].issue_date if live else None,
            "overdue": sum(1 for i in live if i.status == "vencida"),
        },
        "timeline": events[:120],
        # "Aqui solo veo esto y no tengo mas datos": que equipo le instalamos, si sigue en garantia,
        # y en que anda el trabajo con el. Los activos y los proyectos ya existen; faltaba traerlos aqui.
        "assets": [
            {
                "id": a.id,
                "name": a.name,
                "model": a.model,
                "serial": a.serial,
                "location": a.location,
                "installed_at": a.installed_at,
                "warranty_until": a.warranty_until,
                "warranty_days": (a.warranty_until - date.today()).days if a.warranty_until else None,
                "project_id": a.project_id,
            }
            for a in db.scalars(
                select(CustomerAsset).where(CustomerAsset.tenant_id == tid, CustomerAsset.customer_id == cid).order_by(CustomerAsset.id.desc()).limit(60)
            )
        ]
        if p.can("assets", "ver")
        else [],
        "projects": [
            {"id": x.id, "number": x.number, "name": x.name, "status": x.status, "end_date": x.end_date}
            for x in db.scalars(select(Project).where(Project.tenant_id == tid, Project.customer_id == cid).order_by(Project.id.desc()).limit(20))
        ]
        if p.can("projects", "ver")
        else [],
        "opportunities": [
            {"id": x.id, "number": x.number, "title": x.title, "status": x.status, "amount": x.amount, "next_action_date": x.next_action_date}
            for x in db.scalars(
                select(Opportunity).where(Opportunity.tenant_id == tid, Opportunity.customer_id == cid).order_by(Opportunity.id.desc()).limit(20)
            )
        ]
        if p.can("crm_pipeline", "ver")
        else [],
    }


# ---------- Variantes de producto ----------
class VariantIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=60)
    price: Decimal | None = Field(None, ge=0)
    options: dict = Field(default_factory=dict)
    active: bool = True


def _product(db: Session, pid: int, tid: int) -> Product:
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != tid:
        raise HTTPException(404, "Producto no encontrado")
    return pr


def variant_out(v: ProductVariant, base_price) -> dict:
    return {
        "id": v.id,
        "name": v.name,
        "code": v.code,
        "price": v.price,
        "effective_price": v.price if v.price is not None else base_price,
        "options": v.options,
        "active": v.active,
    }


@router.get("/products/{pid}/variants")
def variants(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    pr = _product(db, pid, p.tenant.id)
    rows = db.scalars(select(ProductVariant).where(ProductVariant.product_id == pid).order_by(ProductVariant.position, ProductVariant.id)).all()
    return [variant_out(v, pr.price) for v in rows]


@router.put("/products/{pid}/variants")
def variants_replace(pid: int, data: list[VariantIn], p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Guarda la lista completa: actualiza las existentes, crea las nuevas y desactiva las que se quitaron
    (no se borran porque pueden estar en pedidos o facturas)."""
    pr = _product(db, pid, p.tenant.id)
    codes = [v.code.strip() for v in data]
    if len(codes) != len(set(codes)):
        raise HTTPException(422, "Hay codigos de variante repetidos")
    clash = db.scalar(
        select(func.count())
        .select_from(ProductVariant)
        .where(ProductVariant.tenant_id == p.tenant.id, ProductVariant.product_id != pid, ProductVariant.code.in_(codes))
    )
    if clash or db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == p.tenant.id, Product.code.in_(codes))):
        raise HTTPException(409, "Un codigo de variante ya existe en otro producto")
    current = {v.id: v for v in db.scalars(select(ProductVariant).where(ProductVariant.product_id == pid))}
    keep = set()
    for pos, v in enumerate(data):
        row = current.get(v.id) if v.id else None
        if not row:
            row = ProductVariant(tenant_id=p.tenant.id, product_id=pid)
            db.add(row)
        row.name, row.code, row.price, row.options, row.active, row.position = v.name, v.code.strip(), v.price, v.options, v.active, pos
        db.flush()
        keep.add(row.id)
    for vid, row in current.items():
        if vid not in keep:
            row.active = False
    audit(db, p.tenant.id, p.user.id, "variants", "product", pid, {"count": len(data)}, ip=p.ip)
    db.commit()
    rows = db.scalars(select(ProductVariant).where(ProductVariant.product_id == pid).order_by(ProductVariant.position, ProductVariant.id)).all()
    return [variant_out(v, pr.price) for v in rows]


@router.get("/products/{pid}/link")
def product_link(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """Link directo al producto en la tienda publica (para WhatsApp o redes)."""
    from .store import store_cfg

    pr = _product(db, pid, p.tenant.id)
    cfg = store_cfg(p.tenant)
    url = f"{settings.public_base_url.rstrip('/')}/tienda/{p.tenant.slug}?p={pr.id}"
    problems = []
    if not cfg["published"]:
        problems.append("La tienda no está publicada (Mi Tienda → Publicar).")
    if not pr.show_on_web:
        problems.append("El producto no está marcado para mostrarse en la web.")
    if cfg["kind"] != "tienda":
        problems.append("La tienda está en modo catálogo: el link muestra el producto pero no permite comprar.")
    return {"url": url, "ready": not problems[:2], "problems": problems}
