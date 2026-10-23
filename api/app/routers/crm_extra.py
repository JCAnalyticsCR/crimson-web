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
from ..core.deps import Principal, require, require_any
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
    SupportTicket,
    User,
)
from ..services.archive import live as not_archived
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(tags=["crm"])


def _customer(db: Session, cid: int, tid: int, p: Principal | None = None) -> Customer:
    c = db.get(Customer, cid)
    if not c or c.tenant_id != tid:
        raise HTTPException(404, "Cliente no encontrado")
    if p is not None:
        from ..services.cartera import can_see

        if not can_see(db, p, c):  # cartera con dueno: el cliente de otro vendedor no existe para el
            raise HTTPException(404, "Cliente no encontrado")
    return c


def _owner_name(db: Session, uid: int | None) -> str | None:
    from ..services.cartera import owner_name

    return owner_name(db, uid)


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
    _customer(db, cid, p.tenant.id, p)
    return [_contact_out(c) for c in db.scalars(select(CustomerContact).where(CustomerContact.customer_id == cid).order_by(CustomerContact.id))]


@router.post("/customers/{cid}/contacts", status_code=201)
def contact_create(cid: int, data: ContactIn, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    _customer(db, cid, p.tenant.id, p)
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
def note_create(cid: int, data: NoteIn, p: Principal = Depends(require_any("crm", "editar", "notas")), db: Session = Depends(get_db)):
    # el vendedor anota en la ficha (crm.notas) aunque no la pueda editar (reunion 21/10, pedido 23)
    _customer(db, cid, p.tenant.id, p)
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
    c = _customer(db, cid, p.tenant.id, p)
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
    from .projects import sees_project  # una sola definicion del alcance de proyectos
    from .support_desk import ticket_scope  # una sola definicion del alcance de tickets (la de la pantalla de soporte)

    c = _customer(db, cid, p.tenant.id, p)
    tid = p.tenant.id
    from ..services.ownership import doc_scope

    own_i = [doc_scope(Invoice, p)]  # vendedor responsable, igual que Facturas y Cotizaciones
    own_q = [doc_scope(Quote, p)]
    # el vendedor (sin crm_pipeline.ver_todo) solo ve sus oportunidades, igual que en el embudo
    own_o = [Opportunity.owner_id == p.user.id] if not p.can("crm_pipeline", "ver_todo") else []
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
    from ..services.invoice_states import cr_today, is_cobrable

    live = [i for i in invs if i.status != "anulada"]
    billed = sum((d(i.total) for i in live), Decimal(0))
    # por cobrar = lo que le llego al cliente y tiene saldo (plan 1.3); un borrador sin enviar no es deuda
    cobrables = [i for i in live if is_cobrable(i)]
    due = sum((d(i.balance) for i in cobrables), Decimal(0))
    hoy = cr_today()
    inv_ids = [i.id for i in invs]
    pays = (
        db.scalars(select(Payment).where(Payment.tenant_id == tid, Payment.invoice_id.in_(inv_ids)).order_by(Payment.paid_at.desc()).limit(50)).all()
        if inv_ids
        else []
    )
    # los pedidos web traen montos: solo quien ve ventas (antes los veia cualquiera con crm.ver, incluido el tecnico).
    # El de otro vendedor no aparece (store_orders.order_scope, 21/10 items 24/42)
    from ..services.store_orders import order_scope

    orders = (
        db.scalars(select(Order).where(Order.tenant_id == tid, Order.customer_id == cid, order_scope(p)).order_by(Order.id.desc()).limit(30)).all()
        if p.can("sales", "ver")
        else []
    )
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
            "no_reminders": bool(c.no_reminders),
            "created_at": c.created_at,
            # cartera con dueno (ventas etapa 2): "Vendedor de la cuenta"
            "account_owner_id": c.account_owner_id,
            "account_owner_name": _owner_name(db, c.account_owner_id),
        },
        "kpis": {
            "billed": billed,
            "due": due,
            "invoices": len(live),
            "quotes": len(quotes),
            "last_purchase": live[0].issue_date if live else None,
            "overdue": sum(1 for i in cobrables if i.status == "vencida" or (i.due_date and i.due_date < hoy)),
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
                select(CustomerAsset).where(CustomerAsset.tenant_id == tid, CustomerAsset.customer_id == cid, not_archived(CustomerAsset)).order_by(CustomerAsset.id.desc()).limit(60)
            )
        ]
        if p.can("assets", "ver")
        else [],
        # mismo alcance que la pantalla de proyectos (el tecnico: los que supervisa o donde tiene una OT)
        "projects": [
            {"id": x.id, "number": x.number, "name": x.name, "status": x.status, "end_date": x.end_date}
            for x in [
                pr
                for pr in db.scalars(
                    select(Project)
                    .where(Project.tenant_id == tid, Project.customer_id == cid, not_archived(Project))
                    .order_by(Project.id.desc())
                    .limit(20 if p.can("projects", "ver_todo") else 300)
                )
                if sees_project(p, pr)
            ][:20]
        ]
        if p.can("projects", "ver")
        else [],
        # "si se les ha hecho soporte tecnico a ellos... y el costo que tiene esos soportes". Mismo alcance que la
        # pantalla de tickets (el tecnico, los suyos) y el monto solo para quien ve precios.
        "tickets": [
            {
                "id": t.id,
                "number": t.number,
                "subject": t.subject,
                "kind": t.kind,
                "status": t.status,
                "hours": t.hours,
                "billable": t.billable,
                "amount": t.amount if p.sees_prices else None,
                "created_at": t.created_at,
            }
            for t in db.scalars(select(SupportTicket).where(*ticket_scope(p), SupportTicket.customer_id == cid).order_by(SupportTicket.id.desc()).limit(20))
        ]
        if p.can("support_desk", "ver")
        else [],
        "opportunities": [
            {"id": x.id, "number": x.number, "title": x.title, "status": x.status, "amount": x.amount, "next_action_date": x.next_action_date}
            for x in db.scalars(
                select(Opportunity)
                .where(Opportunity.tenant_id == tid, Opportunity.customer_id == cid, not_archived(Opportunity), *own_o)
                .order_by(Opportunity.id.desc())
                .limit(20)
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


def variant_out(v: ProductVariant, base_price, precios: bool = True) -> dict:
    """precios=False (el tecnico: catalog.ver sin precios) no devuelve el precio de la variante ni el del producto."""
    return {
        "id": v.id,
        "name": v.name,
        "code": v.code,
        "price": v.price if precios else None,
        "effective_price": (v.price if v.price is not None else base_price) if precios else None,
        "options": v.options,
        "active": v.active,
    }


@router.get("/products/{pid}/variants")
def variants(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    pr = _product(db, pid, p.tenant.id)
    rows = db.scalars(select(ProductVariant).where(ProductVariant.product_id == pid).order_by(ProductVariant.position, ProductVariant.id)).all()
    return [variant_out(v, pr.price, p.sees_prices) for v in rows]


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
        row.name, row.code, row.options, row.active, row.position = v.name, v.code.strip(), v.options, v.active, pos
        if p.sees_prices:
            # quien no ve precios los recibe vacios y los mandaria vacios: sin esto borraba el precio de la variante
            row.price = v.price
        db.flush()
        keep.add(row.id)
    for vid, row in current.items():
        if vid not in keep:
            row.active = False
    audit(db, p.tenant.id, p.user.id, "variants", "product", pid, {"count": len(data)}, ip=p.ip)
    from .catalog import _margin_recheck

    _margin_recheck(db, p, pr)  # la tienda vende la variante a su precio: si estaba en revision por margen, se revisa ya
    db.commit()
    rows = db.scalars(select(ProductVariant).where(ProductVariant.product_id == pid).order_by(ProductVariant.position, ProductVariant.id)).all()
    return [variant_out(v, pr.price, p.sees_prices) for v in rows]


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
