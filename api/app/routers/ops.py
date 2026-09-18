"""Impresion/PDF/envio de documentos, factura electronica, inventario, contabilidad, reportes, recurrencias."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, Expense, ExpenseCategory, Invoice, Quote, Recurrence, Supplier, Warehouse
from ..schemas.sales import DocumentIn
from ..services import documents as docsvc
from ..services import einvoice as esvc
from ..services import inventory as inv
from ..services import reports as rp
from ..services.documents import audit
from ..services.mail import doc_email_html, queue_email
from ..services.render import money, render_html, render_pdf

router = APIRouter(tags=["operacion"])


def _doc(db: Session, kind: str, id_: int, tenant_id: int):
    model = Quote if kind == "quotes" else Invoice
    d = db.get(model, id_)
    if not d or d.tenant_id != tenant_id:
        raise HTTPException(404, "Documento no encontrado")
    return d


# ---------- Documento imprimible / PDF / envio ----------
@router.get("/{kind}/{id_}/html", response_class=HTMLResponse)
def doc_html(kind: str, id_: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    return render_html(db, _doc(db, kind, id_, p.tenant.id), p.tenant)


@router.get("/{kind}/{id_}/pdf")
def doc_pdf(kind: str, id_: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    d = _doc(db, kind, id_, p.tenant.id)
    html = render_html(db, d, p.tenant)
    pdf = render_pdf(html)
    if pdf is None:  # sin WeasyPrint (Windows local): el navegador imprime el HTML
        return HTMLResponse(html, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{d.number}.pdf"'})


class SendIn(BaseModel):
    to: str | None = None
    message: str | None = None
    with_payment_link: bool = True


@router.post("/{kind}/{id_}/email")
def doc_send(kind: str, id_: int, data: SendIn, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    d = _doc(db, kind, id_, p.tenant.id)
    c = db.get(Customer, d.customer_id) if d.customer_id else None
    to = data.to or (c.email if c else None)
    if not to:
        raise HTTPException(422, "El cliente no tiene correo; indique uno")
    is_inv = kind == "invoices"
    link = None
    if is_inv and data.with_payment_link and Decimal(str(d.balance)) > 0:
        link = docsvc.get_or_create_payment_link(db, p.tenant.id, d).url
    subject = f"{'Factura' if is_inv else 'Cotización'} {d.number} · {p.tenant.name}"
    m = queue_email(
        db,
        p.tenant,
        to,
        subject,
        doc_email_html(
            p.tenant,
            "factura" if is_inv else "cotización",
            d.number,
            money(d.total, d.currency),
            link,
            data.message or (p.tenant.settings or {}).get("invoice_footer" if is_inv else "quote_footer"),
        ),
        kind[:-1],
        d.id,
        [{"name": f"{d.number}.pdf", "kind": kind, "ref": d.id}],
    )
    if d.status == "creado":
        d.status = "enviada"
    audit(db, p.tenant.id, p.user.id, "send", kind[:-1], d.id, {"to": to, "mail": m.status}, ip=p.ip)
    db.commit()
    return {
        "status": m.status,
        "to": to,
        "note": "Sin RESEND_API_KEY el correo queda simulado (visible en Ajustes → Correo)." if m.status == "simulado" else None,
    }


# ---------- Factura electronica ----------
@router.post("/invoices/{id_}/emit")
def emit(id_: int, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    d = _doc(db, "invoices", id_, p.tenant.id)
    doc = esvc.emit(db, p.tenant, p.user.id, d)
    db.commit()
    return {"status": doc.status, "clave": doc.clave, "message": doc.hacienda_message, "provider": doc.provider}


class VoidNcIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/invoices/{id_}/credit-note")
def void_nc(id_: int, data: VoidNcIn, p: Principal = Depends(require("sales", "anular")), db: Session = Depends(get_db)):
    d = _doc(db, "invoices", id_, p.tenant.id)
    doc = esvc.credit_note(db, p.tenant, p.user.id, d, data.reason)
    inv.restock_for_void(db, d, p.user.id)
    db.commit()
    return {"status": doc.status, "clave": doc.clave, "consecutive": doc.consecutive}


@router.get("/invoices/{id_}/xml")
def xml_list(id_: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    d = _doc(db, "invoices", id_, p.tenant.id)
    return [
        {
            "id": x.id,
            "doc_type": x.doc_type,
            "consecutive": x.consecutive,
            "clave": x.clave,
            "status": x.status,
            "message": x.hacienda_message,
            "provider": x.provider,
            "sent_at": x.sent_at,
            "has_document": bool(x.xml_document),
            "has_response": bool(x.xml_response),
        }
        for x in esvc.documents_for(db, d)
    ]


@router.get("/invoices/{id_}/xml/{doc_id}/{which}")
def xml_get(id_: int, doc_id: int, which: str, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    _doc(db, "invoices", id_, p.tenant.id)
    x = next((x for x in esvc.documents_for(db, db.get(Invoice, id_)) if x.id == doc_id), None)
    if not x:
        raise HTTPException(404, "Documento no encontrado")
    body = x.xml_document if which == "document" else x.xml_response
    if not body:
        raise HTTPException(404, "XML no disponible")
    return Response(body, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{x.clave or x.consecutive}-{which}.xml"'})


# ---------- Inventario ----------
class WarehouseIn(BaseModel):
    name: str
    description: str | None = None
    location: str | None = None
    is_default: bool = False
    active: bool = True


@router.get("/warehouses")
def warehouses(p: Principal = Depends(require("inventory", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": w.id, "name": w.name, "description": w.description, "location": w.location, "is_default": w.is_default, "active": w.active}
        for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id).order_by(Warehouse.is_default.desc(), Warehouse.id))
    ]


@router.post("/warehouses", status_code=201)
def warehouse_create(data: WarehouseIn, p: Principal = Depends(require("inventory", "crear")), db: Session = Depends(get_db)):
    if data.is_default:
        for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id)):
            w.is_default = False
    w = Warehouse(tenant_id=p.tenant.id, **data.model_dump())
    db.add(w)
    db.commit()
    return {"id": w.id}


@router.put("/warehouses/{wid}")
def warehouse_update(wid: int, data: WarehouseIn, p: Principal = Depends(require("inventory", "editar")), db: Session = Depends(get_db)):
    w = db.get(Warehouse, wid)
    if not w or w.tenant_id != p.tenant.id:
        raise HTTPException(404, "Inventario no encontrado")
    if data.is_default:
        for o in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id)):
            o.is_default = False
    for k, v in data.model_dump().items():
        setattr(w, k, v)
    db.commit()
    return {"id": w.id}


@router.get("/stock")
def stock(warehouse_id: int | None = None, p: Principal = Depends(require("inventory", "ver")), db: Session = Depends(get_db)):
    from ..models import Product

    levels = inv.stock_levels(db, p.tenant.id, warehouse_id=warehouse_id)
    prods = {pr.id: pr for pr in db.scalars(select(Product).where(Product.tenant_id == p.tenant.id))}
    whs = {w.id: w.name for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id))}
    return {
        "levels": [
            {
                **lv,
                "code": prods[lv["product_id"]].code,
                "name": prods[lv["product_id"]].name,
                "min_stock": prods[lv["product_id"]].min_stock,
                "warehouse": whs.get(lv["warehouse_id"]),
            }
            for lv in levels
            if lv["product_id"] in prods
        ],
        "low": inv.low_stock(db, p.tenant.id),
    }


class MoveIn(BaseModel):
    product_id: int
    warehouse_id: int
    kind: str = Field(pattern="^(entrada|salida|ajuste)$")
    quantity: Decimal
    unit_cost: Decimal | None = None
    reference: str | None = None
    note: str | None = None


@router.post("/stock/movements", status_code=201)
def stock_move(data: MoveIn, p: Principal = Depends(require("inventory", "crear")), db: Session = Depends(get_db)):
    qty = abs(data.quantity) if data.kind == "entrada" else -abs(data.quantity) if data.kind == "salida" else data.quantity
    m = inv.move(db, p.tenant.id, p.user.id, data.product_id, data.warehouse_id, data.kind, qty, data.reference, data.note, data.unit_cost)
    audit(db, p.tenant.id, p.user.id, "stock", "product", data.product_id, {"kind": data.kind, "qty": str(qty)}, ip=p.ip)
    db.commit()
    return {"id": m.id}


class TransferIn(BaseModel):
    product_id: int
    from_id: int
    to_id: int
    quantity: Decimal = Field(gt=0)
    note: str | None = None


@router.post("/stock/transfer", status_code=201)
def stock_transfer(data: TransferIn, p: Principal = Depends(require("inventory", "crear")), db: Session = Depends(get_db)):
    inv.transfer(db, p.tenant.id, p.user.id, data.product_id, data.from_id, data.to_id, data.quantity, data.note)
    db.commit()
    return {"ok": True}


# ---------- Contabilidad ----------
class CategoryIn(BaseModel):
    name: str
    active: bool = True


@router.get("/expense-categories")
def exp_cats(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": c.id, "name": c.name, "active": c.active}
        for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.tenant_id == p.tenant.id).order_by(ExpenseCategory.name))
    ]


@router.post("/expense-categories", status_code=201)
def exp_cat_create(data: CategoryIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    c = ExpenseCategory(tenant_id=p.tenant.id, **data.model_dump())
    db.add(c)
    db.commit()
    return {"id": c.id}


class ExpenseIn(BaseModel):
    category_id: int | None = None
    supplier_id: int | None = None
    description: str = Field(min_length=1, max_length=300)
    date: date
    currency: str = "CRC"
    subtotal: Decimal = Field(ge=0)
    tax_rate: Decimal = Decimal(13)
    iva_credit: str = Field("credito", pattern="^(credito|no_credito|proporcional)$")
    bank_account_id: int | None = None
    reference: str | None = None
    status: str = Field("registrado", pattern="^(registrado|pagado|anulado)$")


def _exp_out(e: Expense, db: Session):
    cat = db.get(ExpenseCategory, e.category_id) if e.category_id else None
    return {
        "id": e.id,
        "category_id": e.category_id,
        "category": cat.name if cat else None,
        "supplier_id": e.supplier_id,
        "description": e.description,
        "date": e.date,
        "currency": e.currency,
        "subtotal": e.subtotal,
        "tax_rate": e.tax_rate,
        "tax_amount": e.tax_amount,
        "total": e.total,
        "iva_credit": e.iva_credit,
        "bank_account_id": e.bank_account_id,
        "reference": e.reference,
        "status": e.status,
    }


@router.get("/expenses")
def expenses(limit: int = Query(50, le=200), p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    return [
        _exp_out(e, db)
        for e in db.scalars(select(Expense).where(Expense.tenant_id == p.tenant.id).order_by(Expense.date.desc(), Expense.id.desc()).limit(limit))
    ]


def _apply_expense(e: Expense, data: ExpenseIn):
    for k, v in data.model_dump().items():
        setattr(e, k, v)
    e.tax_amount = (data.subtotal * data.tax_rate / Decimal(100)).quantize(Decimal("0.00001"))
    e.total = data.subtotal + e.tax_amount


@router.post("/expenses", status_code=201)
def expense_create(data: ExpenseIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    e = Expense(tenant_id=p.tenant.id, created_by=p.user.id)
    _apply_expense(e, data)
    db.add(e)
    db.commit()
    return _exp_out(e, db)


@router.put("/expenses/{eid}")
def expense_update(eid: int, data: ExpenseIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e or e.tenant_id != p.tenant.id:
        raise HTTPException(404, "Gasto no encontrado")
    _apply_expense(e, data)
    db.commit()
    return _exp_out(e, db)


class SupplierIn(BaseModel):
    name: str
    tax_id: str | None = None
    email: str | None = None
    phone: str | None = None


@router.get("/suppliers")
def suppliers(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": s.id, "name": s.name, "tax_id": s.tax_id, "email": s.email, "phone": s.phone}
        for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id).order_by(Supplier.name))
    ]


@router.post("/suppliers", status_code=201)
def supplier_create(data: SupplierIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    s = Supplier(tenant_id=p.tenant.id, **data.model_dump())
    db.add(s)
    db.commit()
    return {"id": s.id}


# ---------- Reportes ----------
@router.get("/reports")
def report_catalog(_: Principal = Depends(require("reports", "ver"))):
    return [{"key": k, "title": t, "description": d} for k, t, d in rp.CATALOG]


@router.get("/reports/{key}")
def report(
    key: str,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    format: str = "json",
    p: Principal = Depends(require("reports", "ver")),
    db: Session = Depends(get_db),
):
    fn = rp.REPORTS.get(key)
    if not fn:
        raise HTTPException(404, "Reporte no encontrado")
    b = to or date.today()
    a = from_ or b.replace(day=1)
    rep = fn(db, p.tenant.id, a, b)
    if format == "xlsx":
        if not p.can("reports", "exportar"):
            raise HTTPException(403, "Sin permiso para exportar")
        data = rp.to_xlsx(rep, a, b, p.tenant.name)
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{key}-{a}-{b}.xlsx"'},
        )
    return {"key": rep.key, "title": rep.title, "from": a, "to": b, "columns": rep.columns, "rows": rep.rows, "totals": rep.totals}


# ---------- Recurrencias ----------
class RecurrenceIn(BaseModel):
    name: str
    customer_id: int
    template: DocumentIn
    frequency: str = Field("mensual", pattern="^(semanal|quincenal|mensual|anual)$")
    next_date: date
    end_date: date | None = None
    auto_send: bool = True
    active: bool = True


def _next(d: date, f: str) -> date:
    if f == "semanal":
        return d + timedelta(days=7)
    if f == "quincenal":
        return d + timedelta(days=15)
    if f == "anual":
        return d.replace(year=d.year + 1)
    m = d.month % 12 + 1
    y = d.year + (1 if m == 1 else 0)
    return d.replace(year=y, month=m, day=min(d.day, 28))


def _rec_out(r: Recurrence, db: Session):
    c = db.get(Customer, r.customer_id)
    return {
        "id": r.id,
        "name": r.name,
        "customer_id": r.customer_id,
        "customer": c.name if c else None,
        "frequency": r.frequency,
        "next_date": r.next_date,
        "end_date": r.end_date,
        "auto_send": r.auto_send,
        "active": r.active,
        "runs": r.runs,
        "last_invoice_id": r.last_invoice_id,
        "template": r.template,
    }


@router.get("/recurrences")
def recurrences(p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    return [_rec_out(r, db) for r in db.scalars(select(Recurrence).where(Recurrence.tenant_id == p.tenant.id).order_by(Recurrence.next_date))]


@router.post("/recurrences", status_code=201)
def recurrence_create(data: RecurrenceIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    r = Recurrence(
        tenant_id=p.tenant.id,
        name=data.name,
        customer_id=data.customer_id,
        template=data.template.model_dump(mode="json"),
        frequency=data.frequency,
        next_date=data.next_date,
        end_date=data.end_date,
        auto_send=data.auto_send,
        active=data.active,
    )
    db.add(r)
    db.commit()
    return _rec_out(r, db)


@router.put("/recurrences/{rid}")
def recurrence_update(rid: int, data: RecurrenceIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    r = db.get(Recurrence, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Recurrencia no encontrada")
    r.name, r.customer_id, r.template, r.frequency, r.next_date, r.end_date, r.auto_send, r.active = (
        data.name,
        data.customer_id,
        data.template.model_dump(mode="json"),
        data.frequency,
        data.next_date,
        data.end_date,
        data.auto_send,
        data.active,
    )
    db.commit()
    return _rec_out(r, db)


def run_recurrence(db: Session, r: Recurrence, user_id: int | None) -> Invoice:
    payload = DocumentIn(**{**r.template, "customer_id": r.customer_id})
    invoice = docsvc.create_invoice(db, r.tenant_id, user_id or 0, payload)
    inv.deduct_for_invoice(db, invoice, user_id)
    r.runs += 1
    r.last_invoice_id = invoice.id
    r.next_date = _next(r.next_date, r.frequency)
    if r.end_date and r.next_date > r.end_date:
        r.active = False
    return invoice


@router.post("/recurrences/{rid}/run", status_code=201)
def recurrence_run(rid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    r = db.get(Recurrence, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Recurrencia no encontrada")
    invoice = run_recurrence(db, r, p.user.id)
    db.commit()
    return {"invoice_id": invoice.id, "number": invoice.number, "next_date": r.next_date}


def run_due_recurrences(db: Session) -> int:
    n = 0
    for r in db.scalars(select(Recurrence).where(Recurrence.active, Recurrence.next_date <= date.today())):
        run_recurrence(db, r, None)
        n += 1
    db.commit()
    return n


# ---------- Utilidad para el dashboard: stock bajo ----------
@router.get("/alerts")
def alerts(p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    return {"low_stock": inv.low_stock(db, p.tenant.id), "at": datetime.now(UTC)}
