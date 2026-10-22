"""Impresion/PDF/envio de documentos, factura electronica, inventario, contabilidad, reportes, recurrencias."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, get_principal, require
from ..models import Customer, Expense, ExpenseCategory, Invoice, Quote, Recurrence, Supplier, Warehouse
from ..schemas.sales import DocumentIn
from ..services import documents as docsvc
from ..services import einvoice as esvc
from ..services import inventory as inv
from ..services import reports as rp
from ..services.ally_access import PLAN_ORDER  # marca de las facturas de mensualidad (plan 1.4)
from ..services.bitacora import audit_change, next_version, snapshot
from ..services.documents import audit
from ..services.ownership import owns_doc
from ..services.render import render_html, render_pdf

router = APIRouter(tags=["operacion"])
log = logging.getLogger("crimson.ops")


def _doc(db: Session, kind: str, id_: int, tenant_id: int, p: Principal | None = None):
    model = Quote if kind == "quotes" else Invoice
    d = db.get(model, id_)
    # mismo alcance que la lista y el detalle (vendedor responsable): lo ajeno es 404
    if not d or d.tenant_id != tenant_id or (p is not None and not owns_doc(d, p)):
        raise HTTPException(404, "Documento no encontrado")
    return d


# ---------- Documento imprimible / PDF / envio ----------
@router.get("/{kind}/{id_}/html", response_class=HTMLResponse)
def doc_html(kind: str, id_: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    return render_html(db, _doc(db, kind, id_, p.tenant.id, p), p.tenant)


@router.get("/{kind}/{id_}/pdf")
def doc_pdf(kind: str, id_: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    d = _doc(db, kind, id_, p.tenant.id, p)
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
    """Envoltorio de services/documents_send.send_document (lo usan tambien la recurrencia y la cobranza)."""
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    if kind == "quotes" and not p.sends_quotes:
        # contabilidad envia facturas (plan 1.7); la cotizacion es de quien cotiza
        raise HTTPException(403, "Enviar cotizaciones es de quien cotiza (crear o editar ventas)")
    from ..services.documents_send import send_document

    d = _doc(db, kind, id_, p.tenant.id, p)
    if kind == "quotes":
        # las mismas guardas que "Marcar enviada" (POST /quotes/{id}/send): con un descuento o un margen esperando al
        # admin, por correo se le saltaria la aprobacion
        if d.status == "por_aprobar":
            raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
        if d.status in ("convertida", "anulada"):
            raise HTTPException(409, f"La cotización está {d.status}")
    r = send_document(db, p.tenant, d, data.to, data.message, data.with_payment_link, p.user.id, ip=p.ip)
    db.commit()
    return {k: r[k] for k in ("status", "sent", "to", "note", "version")}


# ---------- Factura electronica ----------
@router.post("/invoices/{id_}/emit")
def emit(id_: int, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    d = _doc(db, "invoices", id_, p.tenant.id, p)
    doc = esvc.emit(db, p.tenant, p.user.id, d)
    db.commit()
    if esvc.is_direct(doc):
        # el envio va DESPUES del commit (emit() nunca llama a la red). Fallas de red no son 502: el worker reintenta.
        from ..services.hacienda_sync import send_einvoice

        try:
            send_einvoice(db, doc.id)
        except Exception:  # noqa: BLE001
            log.exception("emision de la factura %s: el envio inmediato fallo, queda para el worker", id_)
            db.rollback()
        db.refresh(doc)
        db.refresh(d)
        msg = doc.last_error or doc.hacienda_message
        if doc.status in ("por_enviar", "enviando"):
            return {"status": "pendiente", "doc_status": doc.status, "clave": doc.clave, "message": f"Se reintentará automáticamente{': ' + msg if msg else ''}", "provider": doc.provider, "mode": doc.mode}
        return {"status": d.einvoice_status, "doc_status": doc.status, "clave": doc.clave, "message": msg, "provider": doc.provider, "mode": doc.mode}
    return {"status": doc.status, "clave": doc.clave, "message": doc.hacienda_message, "provider": doc.provider, "mode": doc.mode}


@router.post("/invoices/{id_}/einvoice/refresh")
def einvoice_refresh(id_: int, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    """Consulta ya el estado del ultimo comprobante (o lo envia si quedo por enviar)."""
    from ..services.hacienda_sync import refresh_einvoice, send_einvoice

    d = _doc(db, "invoices", id_, p.tenant.id, p)
    doc = esvc.last_document(db, d)
    if not doc or not esvc.is_direct(doc):
        raise HTTPException(409, "La factura no tiene un comprobante enviado a Hacienda")
    if doc.status == "por_enviar":
        doc.next_attempt_at = None
        db.commit()
        send_einvoice(db, doc.id)
    else:
        refresh_einvoice(db, doc.id)
    db.refresh(doc)
    db.refresh(d)
    return {"status": d.einvoice_status, "doc_status": doc.status, "clave": doc.clave, "message": doc.hacienda_message, "last_error": doc.last_error, "mode": doc.mode}


class VoidNcIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/invoices/{id_}/credit-note")
def void_nc(id_: int, data: VoidNcIn, p: Principal = Depends(require("sales", "anular")), db: Session = Depends(get_db)):
    d = _doc(db, "invoices", id_, p.tenant.id, p)
    doc = esvc.credit_note(db, p.tenant, p.user.id, d, data.reason)
    if not esvc.is_direct(doc):
        inv.restock_for_void(db, d, p.user.id)  # simulado: anulada al instante
        from ..services import commissions as comsvc

        # la NC es el unico camino para anular una factura aceptada: lo devengado se anula o se revierte (falla E).
        # En directo esto pasa cuando Hacienda acepta la NC (hacienda_sync._void_after_nc).
        comsvc.on_invoice_void(db, d, p.user.id)
    db.commit()
    if esvc.is_direct(doc):
        # directo: la factura se anula y el inventario vuelve solo cuando Hacienda acepta la NC (hacienda_sync)
        from ..services.hacienda_sync import send_einvoice

        try:
            send_einvoice(db, doc.id)
        except Exception:  # noqa: BLE001
            log.exception("nota de credito de la factura %s: el envio inmediato fallo, queda para el worker", id_)
            db.rollback()
        db.refresh(doc)
        return {"status": "pendiente" if doc.status in ("por_enviar", "enviando", "enviado", "procesando") else doc.status, "doc_status": doc.status, "clave": doc.clave, "consecutive": doc.consecutive, "mode": doc.mode}
    return {"status": doc.status, "clave": doc.clave, "consecutive": doc.consecutive, "mode": doc.mode}


@router.get("/invoices/{id_}/xml")
def xml_list(id_: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    d = _doc(db, "invoices", id_, p.tenant.id, p)
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
            "mode": x.mode or ("simulado" if x.provider == "sandbox" else None),
            "last_error": x.last_error,
            "emitted_at": x.emitted_at,
            "delivered_at": x.delivered_at,
            "hacienda_delivered_at": x.hacienda_delivered_at,
            "delivery_failed_at": x.delivery_failed_at,
            "hacienda_delivery_failed_at": x.hacienda_delivery_failed_at,
            "receiver_message": x.receiver_message,
            "receiver_detail": x.receiver_detail,
            "receiver_at": x.receiver_at,
            "attempts": x.attempts,
        }
        for x in esvc.documents_for(db, d)
    ]


@router.get("/invoices/{id_}/xml/{doc_id}/{which}")
def xml_get(id_: int, doc_id: int, which: str, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    _doc(db, "invoices", id_, p.tenant.id, p)
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
    # venta (se vende e instala) | revision (garantias en revision de Crimson) | proveedor (en el distribuidor)
    kind: str = Field("venta", pattern="^(venta|revision|proveedor)$")


@router.get("/warehouses")
def warehouses(p: Principal = Depends(require("inventory", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": w.id, "name": w.name, "description": w.description, "location": w.location, "is_default": w.is_default, "active": w.active, "kind": w.kind}
        for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id).order_by(Warehouse.is_default.desc(), Warehouse.id))
    ]


@router.post("/warehouses", status_code=201)
def warehouse_create(data: WarehouseIn, p: Principal = Depends(require("inventory", "crear")), db: Session = Depends(get_db)):
    if data.is_default and data.kind != "venta":
        raise HTTPException(422, "La bodega predeterminada tiene que ser de venta")
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
    if data.is_default and data.kind != "venta":
        raise HTTPException(422, "La bodega predeterminada tiene que ser de venta")
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
    from ..services.reservations import committed

    levels = inv.stock_levels(db, p.tenant.id, warehouse_id=warehouse_id)
    prods = {pr.id: pr for pr in db.scalars(select(Product).where(Product.tenant_id == p.tenant.id))}
    wh_rows = list(db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id)))
    whs = {w.id: w.name for w in wh_rows}
    kinds = {w.id: w.kind for w in wh_rows}  # el portal no suma las de garantias (revision, distribuidor) en los totales
    # comprometido = reservas confirmadas y vigentes (AC25). Sin reservas es 0 y disponible = cantidad.
    comp = committed(db, p.tenant.id, warehouse_id=warehouse_id)
    return {
        "levels": [
            {
                **lv,
                "code": prods[lv["product_id"]].code,
                "name": prods[lv["product_id"]].name,
                "min_stock": prods[lv["product_id"]].min_stock,
                "serialized": bool(prods[lv["product_id"]].serialized),
                "warehouse": whs.get(lv["warehouse_id"]),
                "warehouse_kind": kinds.get(lv["warehouse_id"]) or "venta",
                "committed": comp.get((lv["product_id"], lv["warehouse_id"]), Decimal(0)),
                "available": lv["quantity"] - comp.get((lv["product_id"], lv["warehouse_id"]), Decimal(0)),
            }
            for lv in levels
            if lv["product_id"] in prods
        ],
        "low": inv.low_stock(db, p.tenant.id),
    }


AVAIL_FILTERS = "^(propio|eurocomp|sin|viejo|sin_vincular)$"


@router.get("/stock/availability")
def stock_availability(
    filtro: str | None = Query(None, pattern=AVAIL_FILTERS),
    q: str | None = Query(None, max_length=120),
    p: Principal = Depends(require("inventory", "ver")),
    db: Session = Depends(get_db),
):
    """Inventario -> Disponibilidad: TODOS los productos vendibles con la funcion unica (services/availability):
    propio (libre / reservado, solo bodegas de venta), proveedor (Eurocomp) con su fecha, y el estado. Con cero propio
    muestra lo del proveedor en vez de salir vacia. Primero lo propio. Filtros: propio (con existencia propia),
    eurocomp (solo del proveedor), sin (sin existencia), viejo (dato del proveedor viejo), sin_vincular."""
    from sqlalchemy import or_

    from ..models import Product
    from ..services.availability import availability_many, sort_key

    stmt = select(Product).where(Product.tenant_id == p.tenant.id, Product.active, Product.item_type == "producto")
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.model.ilike(like), Product.supplier_sku.ilike(like)))
    prods = list(db.scalars(stmt.order_by(Product.name)))
    av = availability_many(db, p.tenant, prods, audience="interno")
    summary = {
        "productos": len(prods),
        "con_propio": 0,
        "solo_proveedor": 0,
        "sin_existencia": 0,
        "dato_viejo": 0,
        "sin_vincular": 0,
        "propio_libre": Decimal(0),
        "propio_reservado": Decimal(0),
        "proveedor": Decimal(0),
    }
    rows = []
    for pr in prods:
        a = av.get(pr.id) or {}
        own = Decimal(str(a.get("own_on_hand") or 0))
        sup = Decimal(str(a.get("supplier_qty") or 0))
        viejo = bool(a.get("supplier_linked") and sup > 0 and not a.get("supplier_fresh"))
        kind = "propio" if own > 0 else ("eurocomp" if sup > 0 else "sin")
        summary["con_propio" if kind == "propio" else "solo_proveedor" if kind == "eurocomp" else "sin_existencia"] += 1
        summary["dato_viejo"] += int(viejo)
        summary["sin_vincular"] += int(bool(a.get("supplier_unlinked_qty")))
        summary["propio_libre"] += Decimal(str(a.get("own_free") or 0))
        summary["propio_reservado"] += Decimal(str(a.get("own_reserved") or 0))
        summary["proveedor"] += sup
        if filtro in ("propio", "eurocomp", "sin") and kind != filtro:
            continue
        if filtro == "viejo" and not viejo:
            continue
        if filtro == "sin_vincular" and not a.get("supplier_unlinked_qty"):
            continue
        rows.append(
            {
                "id": pr.id,
                "code": pr.code,
                "name": pr.name,
                "brand": pr.brand,
                "show_on_web": pr.show_on_web,
                "min_stock": pr.min_stock,
                **{
                    k: a.get(k)
                    for k in (
                        "state",
                        "label",
                        "label_publico",
                        "own_on_hand",
                        "own_free",
                        "own_reserved",
                        "own_reserved_web",
                        "supplier_linked",
                        "supplier_qty",
                        "supplier_available",
                        "supplier_committed",
                        "supplier_name",
                        "supplier_source",
                        "supplier_updated_at",
                        "supplier_fresh",
                        "supplier_age",
                        "supplier_changed_at",
                        "supplier_changed_age",
                        "agotado_proveedor",
                        "ocultar",
                        "supplier_unlinked_qty",
                        "max_qty",
                    )
                },
                "supplier_old": viejo,
            }
        )
    rows.sort(key=lambda r: (sort_key(r), r["name"].lower()))
    return {"summary": summary, "items": rows[:3000], "total": len(rows)}


class MoveIn(BaseModel):
    product_id: int
    warehouse_id: int
    kind: str = Field(pattern="^(entrada|salida|ajuste)$")
    quantity: Decimal
    unit_cost: Decimal | None = None
    reference: str | None = None
    note: str | None = None
    # opcionales (existencias enriquecidas): sin ellos el movimiento es el de siempre
    project_id: int | None = None
    serial: str | None = Field(None, max_length=80)
    movement_type: str | None = Field(None, pattern="^(compra|venta|consumo_proyecto|prestamo|consignacion|asignacion_interna|devolucion|ajuste|traslado)$")
    voucher_media: str | None = Field(None, max_length=400)
    transfer_price: Decimal | None = None
    owner: str | None = Field(None, max_length=160)
    custodian: str | None = Field(None, max_length=160)
    location: str | None = Field(None, max_length=160)


@router.post("/stock/movements", status_code=201)
def stock_move(data: MoveIn, p: Principal = Depends(require("inventory", "ajustar")), db: Session = Depends(get_db)):
    """Entrada, salida o ajuste manual (sin documento que lo respalde): inventory.ajustar, que hoy tienen solo los
    vendedores y el administrador (Andres, 19/10). Lo que nace de un documento (factura, compra, orden) no pasa por aca."""
    qty = abs(data.quantity) if data.kind == "entrada" else -abs(data.quantity) if data.kind == "salida" else data.quantity
    if data.project_id:
        from ..models import Project

        pr = db.get(Project, data.project_id)
        if not pr or pr.tenant_id != p.tenant.id:
            raise HTTPException(404, "Proyecto no encontrado")
    extra = data.model_dump(include=inv.EXTRA_FIELDS, exclude_none=True)
    if "transfer_price" in extra and not p.sees_costs:
        extra.pop("transfer_price")  # el precio de traslado es informacion de costos
    m = inv.move(db, p.tenant.id, p.user.id, data.product_id, data.warehouse_id, data.kind, qty, data.reference, data.note, data.unit_cost, **extra)
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
D151_KINDS = "^(alquiler|servicios_profesionales|comisiones|intereses)$"


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    active: bool = True
    d151_kind: str | None = Field(None, pattern=D151_KINDS)


@router.get("/expense-categories")
def exp_cats(active: int = 0, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    q = select(ExpenseCategory).where(ExpenseCategory.tenant_id == p.tenant.id)
    if active:
        q = q.where(ExpenseCategory.active)  # los selectores solo ofrecen las activas
    return [{"id": c.id, "name": c.name, "active": c.active, "d151_kind": c.d151_kind} for c in db.scalars(q.order_by(ExpenseCategory.name))]


@router.post("/expense-categories", status_code=201)
def exp_cat_create(data: CategoryIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    c = ExpenseCategory(tenant_id=p.tenant.id, **data.model_dump())
    db.add(c)
    db.commit()
    return {"id": c.id}


@router.put("/expense-categories/{cid}")
def exp_cat_update(cid: int, data: CategoryIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    c = db.get(ExpenseCategory, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Categoría no encontrada")
    antes = snapshot(c)
    c.name, c.active, c.d151_kind = data.name, data.active, data.d151_kind  # desactivar no borra
    audit_change(db, p, "update", "expense_category", c.id, antes, snapshot(c))
    db.commit()
    return {"id": c.id, "name": c.name, "active": c.active, "d151_kind": c.d151_kind}


IVA_CONDITIONS = "^(credito|credito_parcial|bienes_capital|no_credito|proporcional)$"
PAYMENT_METHODS = "^(efectivo|tarjeta|transferencia|sinpe|cheque|otro)$"
# lo unico que se edita de un gasto que viene de un comprobante electronico (los montos salen del XML)
LOCKED_EDITABLE = (
    "category_id",
    "description",
    "notes",
    "status",
    "bank_account_id",
    "payment_method",
    "purchase_type",
    "due_date",
    "bank_instrument_id",
    "check_number",
    "attachment_url",
)


class ExpenseIn(BaseModel):
    category_id: int | None = None
    supplier_id: int | None = None
    description: str = Field(min_length=1, max_length=300)
    date: date
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    # sin ge=0 aqui: un gasto de una NC recibida guarda sus montos en negativo y el portal los reenvia al editar la
    # categoria o el estado. La regla ">= 0" vale para los gastos a mano (_apply_expense); los del XML no se recalculan.
    subtotal: Decimal
    tax_rate: Decimal = Decimal(13)
    iva_credit: str = Field("credito", pattern=IVA_CONDITIONS)
    bank_account_id: int | None = None
    reference: str | None = None
    status: str = Field("registrado", pattern="^(registrado|pagado|anulado)$")
    attachment_url: str | None = Field(None, max_length=400)
    reason: str | None = Field(None, max_length=500)  # opcional: motivo del cambio, va a la bitacora
    fx_rate: Decimal | None = Field(None, gt=0)  # vacio = tipo de cambio de venta del BCCR de esa fecha (CRC = 1)
    notes: str | None = Field(None, max_length=2000)
    payment_method: str | None = Field(None, pattern=PAYMENT_METHODS)
    purchase_type: str = Field("contado", pattern="^(contado|credito)$")
    due_date: date | None = None
    bank_instrument_id: int | None = None
    check_number: str | None = Field(None, max_length=20)
    tax_credit_amount: Decimal | None = None  # vacio = segun la condicion (>= 0 en gastos a mano, ver _apply_expense)


def expense_origin(e: Expense, bank_ids: set[int] | None = None) -> str:
    if e.received_document_id:
        return "xml"
    if e.recurrence_id:
        return "recurrente"
    if (e.reference or "").startswith("PLANILLA-"):
        return "planilla"
    if bank_ids and e.id in bank_ids:
        return "banco"
    return "manual"


def _bank_created(db: Session, tenant_id: int) -> set[int]:
    """Gastos creados desde una linea del extracto (Conciliacion -> crear gasto)."""
    from ..models import AuditLog

    out = set()
    for diff in db.scalars(select(AuditLog.diff).where(AuditLog.tenant_id == tenant_id, AuditLog.action == "expense_from_line")):
        if isinstance(diff, dict) and diff.get("expense_id"):
            out.add(int(diff["expense_id"]))
    return out


def _crc_amount(v, e: Expense) -> Decimal:
    """Monto del gasto en colones con su tasa; si le falta (o quedo en 1), la del BCCR de su fecha (services/fx).
    Nunca suma dolares como colones: sin ninguna tasa, FxMissing -> 422."""
    from sqlalchemy.orm import object_session

    from ..services.fx import FxMissing, doc_rate

    if (e.currency or "CRC") == "CRC":
        return Decimal(str(v or 0))
    db = object_session(e)
    if db is None:
        stored = Decimal(str(e.fx_rate or 0))
        if stored <= 1:
            raise FxMissing(e.currency)
        return Decimal(str(v or 0)) * stored
    return Decimal(str(v or 0)) * doc_rate(db, e.currency, e.fx_rate, e.date)


def _own_or_none(db: Session, model, oid: int | None, tenant_id: int):
    """La fila solo si es de la empresa (nunca el nombre de un proveedor o categoria de otra)."""
    row = db.get(model, oid) if oid else None
    return row if row is not None and row.tenant_id == tenant_id else None


def _check_refs(db: Session, tenant_id: int, supplier_id: int | None, category_id: int | None, bank_account_id: int | None) -> None:
    """Proveedor, categoria y cuenta del gasto (o de su plantilla recurrente) tienen que ser de la misma empresa."""
    from ..models import BankAccount

    refs = (
        (Supplier, supplier_id, "Proveedor no encontrado"),
        (ExpenseCategory, category_id, "Categoría no encontrada"),
        (BankAccount, bank_account_id, "Cuenta no encontrada"),
    )
    for model, oid, msg in refs:
        if oid and _own_or_none(db, model, oid, tenant_id) is None:
            raise HTTPException(404, msg)


def _exp_out(e: Expense, db: Session, bank_ids: set[int] | None = None):
    cat = _own_or_none(db, ExpenseCategory, e.category_id, e.tenant_id)
    sup = _own_or_none(db, Supplier, e.supplier_id, e.tenant_id)
    return {
        "id": e.id,
        "category_id": e.category_id,
        "category": cat.name if cat else None,
        "supplier_id": e.supplier_id,
        "supplier_name": sup.name if sup else None,
        "description": e.description,
        "date": e.date,
        "currency": e.currency,
        "fx_rate": e.fx_rate,
        "subtotal": e.subtotal,
        "tax_rate": e.tax_rate,
        "tax_amount": e.tax_amount,
        "total": e.total,
        "total_crc": _crc_amount(e.total, e).quantize(Decimal("0.01")),
        "iva_credit": e.iva_credit,
        "tax_credit_amount": e.tax_credit_amount,
        "bank_account_id": e.bank_account_id,
        "reference": e.reference,
        "status": e.status,
        "attachment_url": e.attachment_url,
        "notes": e.notes,
        "payment_method": e.payment_method,
        "purchase_type": e.purchase_type,
        "due_date": e.due_date,
        "origin": expense_origin(e, bank_ids),
        "received_document_id": e.received_document_id,
        "recurrence_id": e.recurrence_id,
        "bank_instrument_id": e.bank_instrument_id,
        "check_number": e.check_number,
        "locked": e.received_document_id is not None,
    }


def _expense_query(p: Principal, from_, to, category_id, supplier_id, currency, status, q):
    query = select(Expense).where(Expense.tenant_id == p.tenant.id)
    if from_:
        query = query.where(Expense.date >= from_)
    if to:
        query = query.where(Expense.date <= to)
    if category_id:
        query = query.where(Expense.category_id == category_id)
    if supplier_id:
        query = query.where(Expense.supplier_id == supplier_id)
    if currency:
        query = query.where(Expense.currency == currency)
    if status:
        query = query.where(Expense.status == status)
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(Expense.description.ilike(like, escape="\\") | Expense.reference.ilike(like, escape="\\"))
    return query


def _origin_filter(rows: list[Expense], origin: str | None, bank_ids: set[int]) -> list[Expense]:
    return [e for e in rows if expense_origin(e, bank_ids) == origin] if origin else rows


@router.get("/expenses")
def expenses(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    origin: str | None = Query(None, pattern="^(xml|recurrente|banco|planilla|manual)$"),
    currency: str | None = None,
    status: str | None = None,
    q: str | None = None,
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    bank_ids = _bank_created(db, p.tenant.id)
    query = _expense_query(p, from_, to, category_id, supplier_id, currency, status, q).order_by(Expense.date.desc(), Expense.id.desc())
    if origin:
        rows = _origin_filter(db.scalars(query).all(), origin, bank_ids)[offset : offset + limit]
    else:
        rows = db.scalars(query.offset(offset).limit(limit)).all()
    return [_exp_out(e, db, bank_ids) for e in rows]


@router.get("/expenses/summary")
def expenses_summary(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    origin: str | None = Query(None, pattern="^(xml|recurrente|banco|planilla|manual)$"),
    currency: str | None = None,
    status: str | None = None,
    q: str | None = None,
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    bank_ids = _bank_created(db, p.tenant.id)
    rows = _origin_filter(db.scalars(_expense_query(p, from_, to, category_id, supplier_id, currency, status, q)).all(), origin, bank_ids)
    live = [e for e in rows if e.status != "anulado"]
    q2 = Decimal("0.01")
    return {
        "count": len(rows),
        "total_crc": sum((_crc_amount(e.total, e) for e in live), Decimal(0)).quantize(q2),
        "tax_crc": sum((_crc_amount(e.tax_amount, e) for e in live), Decimal(0)).quantize(q2),
        "credit_crc": sum((_crc_amount(e.tax_credit_amount, e) for e in live if e.tax_credit_amount is not None), Decimal(0)).quantize(q2),
    }


def bccr_sell(db: Session, currency: str, on: date) -> Decimal | None:
    """Venta del BCCR de esa fecha o la ultima anterior, con la funcion unica (services/fx). None si no hay ninguna
    anterior: el llamador avisa en lugar de inventar una."""
    from ..services import fx as fxsvc

    try:
        t = fxsvc.fx(db, currency, on)
    except fxsvc.FxMissing:
        return None
    return None if (t.fecha and on and t.fecha > on) else t.tasa


def _apply_expense(e: Expense, data: ExpenseIn, db: Session | None = None) -> list[str]:
    if data.subtotal < 0:
        raise HTTPException(422, "El subtotal no puede ser negativo")
    if data.tax_credit_amount is not None and data.tax_credit_amount < 0:
        raise HTTPException(422, "El IVA acreditable no puede ser negativo")
    warnings = []
    for k, v in data.model_dump(exclude={"reason", "fx_rate", "tax_credit_amount"}).items():
        setattr(e, k, v)
    e.tax_amount = (data.subtotal * data.tax_rate / Decimal(100)).quantize(Decimal("0.00001"))
    e.total = data.subtotal + e.tax_amount
    if data.currency == "CRC":
        e.fx_rate = Decimal(1)
    elif data.fx_rate is not None:
        e.fx_rate = data.fx_rate
    else:
        # la del BCCR de su fecha (o la mas cercana, con aviso); nunca 1: sin ninguna tasa, FxMissing -> 422 claro
        from ..services.fx import FxMissing, for_date

        if db is None:
            raise FxMissing(data.currency)
        e.fx_rate, aviso = for_date(db, data.currency, data.date)
        if aviso:
            warnings.append(aviso)
    if data.tax_credit_amount is not None:
        e.tax_credit_amount = data.tax_credit_amount
    elif data.iva_credit in ("credito", "bienes_capital"):
        e.tax_credit_amount = e.tax_amount
    elif data.iva_credit == "no_credito":
        e.tax_credit_amount = Decimal(0)
    elif data.iva_credit == "credito_parcial":
        raise HTTPException(422, "Crédito parcial: indique el IVA acreditable")
    else:
        e.tax_credit_amount = None  # proporcional: la prorrata se aplica en el reporte de IVA
    return warnings


def _check_instrument(db: Session, p: Principal, e: Expense) -> None:
    """Cheque: si el numero usado alcanza al siguiente de la chequera, la chequera avanza."""
    if not e.bank_instrument_id:
        return
    from ..models import BankInstrument

    ins = db.get(BankInstrument, e.bank_instrument_id)
    if not ins or ins.tenant_id != p.tenant.id:
        raise HTTPException(404, "Chequera o tarjeta no encontrada")
    if ins.kind == "chequera" and e.check_number and e.check_number.isdigit():
        n = int(e.check_number)
        if ins.next_check is None or n >= ins.next_check:
            ins.next_check = n + 1


@router.post("/expenses", status_code=201)
def expense_create(data: ExpenseIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    _check_refs(db, p.tenant.id, data.supplier_id, data.category_id, data.bank_account_id)
    e = Expense(tenant_id=p.tenant.id, created_by=p.user.id)
    warnings = _apply_expense(e, data, db)
    _check_instrument(db, p, e)
    db.add(e)
    db.flush()
    _audit_expense(db, p, e, {}, "create", data.reason)
    db.commit()
    return {**_exp_out(e, db), "warnings": warnings}


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.00001")
    except Exception:  # noqa: BLE001
        return str(a) == str(b)


@router.put("/expenses/{eid}")
def expense_update(eid: int, data: ExpenseIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e or e.tenant_id != p.tenant.id:
        raise HTTPException(404, "Gasto no encontrado")
    _check_refs(db, p.tenant.id, None if e.received_document_id else data.supplier_id, data.category_id, data.bank_account_id)
    antes = snapshot(e)
    warnings: list[str] = []
    if e.received_document_id:
        # montos, divisa, fecha, tasa y condicion salen del XML: nunca se recalculan (tarifas mixtas, otros cargos)
        cambios = [
            not _same(data.subtotal, e.subtotal),
            not _same(data.tax_rate, e.tax_rate),
            data.currency != e.currency,
            data.date != e.date,
            data.iva_credit != e.iva_credit,
            data.fx_rate is not None and not _same(data.fx_rate, e.fx_rate),
            data.tax_credit_amount is not None and not _same(data.tax_credit_amount, e.tax_credit_amount),
        ]
        if any(cambios):
            raise HTTPException(409, "Este gasto viene de un comprobante electrónico: los montos salen del XML")
        for k in LOCKED_EDITABLE:
            setattr(e, k, getattr(data, k))
        if e.status == "anulado" and antes.get("status") != "anulado":
            warnings.append("El gasto quedó anulado; el crédito de IVA sale del comprobante, no del gasto")
    else:
        warnings = _apply_expense(e, data, db)
    _check_instrument(db, p, e)
    _audit_expense(db, p, e, antes, "update", data.reason)
    db.commit()
    return {**_exp_out(e, db), "warnings": warnings}


def _audit_expense(db: Session, p: Principal, e: Expense, antes: dict, action: str, reason: str | None) -> None:
    """Gasto: antes/despues de los campos que cambiaron; company = el proveedor."""
    sup = _own_or_none(db, Supplier, e.supplier_id, e.tenant_id)
    version = next_version(db, p.tenant.id, "expense", e.id) if action == "update" else 1
    audit_change(
        db, p, action, "expense", e.id, antes, snapshot(e), reason=reason, version=version, company=sup.name if sup else None, always=action == "create"
    )


class SupplierIn(BaseModel):
    name: str
    tax_id: str | None = None
    email: str | None = None
    phone: str | None = None


@router.get("/suppliers")
def suppliers(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": s.id, "name": s.name, "tax_id": s.tax_id, "email": s.email, "phone": s.phone}
        # lo archivado o en la papelera no se ofrece (se restaura desde Archivo)
        for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id, Supplier.archived_at.is_(None), Supplier.trashed_at.is_(None)).order_by(Supplier.name))
    ]


@router.post("/suppliers", status_code=201)
def supplier_create(data: SupplierIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    s = Supplier(tenant_id=p.tenant.id, **data.model_dump())
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.put("/suppliers/{sid}")
def supplier_update(sid: int, data: SupplierIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    s = db.get(Supplier, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Proveedor no encontrado")
    for k, v in data.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return {"id": s.id}


# ---------- Reportes ----------
REPORT_NEEDS = {
    "facturacion": ("sales", "exportar"),
    "pendientes": ("sales", "exportar"),
    "impuesto": ("accounting", "ver"),
    "resultados": ("accounting", "ver"),
    "cierre": ("payments", "ver"),
    "transacciones": ("payments", "ver"),
    "gastos": ("accounting", "ver"),
    "iva": ("accounting", "ver"),
    "inventario": ("inventory", "ver"),
    "productos": ("sales", "exportar"),
    "movimientos": ("inventory", "ver"),
    "ordenes": ("sales", "exportar"),
    "recepciones": ("accounting", "ver"),
    "propinas": ("payments", "ver"),
    "d151": ("accounting", "ver"),
    "compras": ("accounting", "ver"),
    "planilla": ("payroll", "ver"),
    "conciliacion": ("accounting", "ver"),
    "rentabilidad": ("projects", "ver_todo"),
    "rentabilidad_tipo": ("projects", "ver_todo"),
    "comisiones": ("commissions", "ver_todo"),  # contabilidad lo baja para la planilla
}


def report_allowed(p: Principal, key: str) -> bool:
    mod, act = REPORT_NEEDS.get(key, ("settings", "configurar"))
    if mod == "payments" and not p.sees_all_sales:
        return False  # cierres y transacciones son de toda la empresa
    if key.startswith("rentabilidad") and not p.sees_costs:
        return False  # el margen por proyecto es informacion de costos
    return p.can(mod, act)


@router.get("/reports")
def report_catalog(p: Principal = Depends(require("reports", "ver"))):
    return [{"key": k, "title": t, "description": d} for k, t, d in rp.CATALOG if report_allowed(p, k)]


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
    if not report_allowed(p, key):
        raise HTTPException(403, "Este reporte no está disponible para tu rol")
    if format == "xlsx" and not p.can("reports", "exportar"):
        raise HTTPException(403, "Tu rol no puede exportar reportes")
    b = to or date.today()
    a = from_ or b.replace(day=1)
    if key == "comisiones":
        from ..services.commissions import hides_payout

        # el pago acordado al aliado es su precio (costo de Crimson): solo quien ve costos o quien lo aprueba y paga
        rep = rp.comisiones(db, p.tenant.id, a, b, hide_payouts=hides_payout(p))
    else:
        rep = fn(db, p.tenant.id, a, b)
    if format == "xlsx":
        if not p.can("reports", "exportar"):
            raise HTTPException(403, "Sin permiso para exportar")
        data = rp.to_xlsx(rep, a, b, p.tenant.name)
        # exportacion: el archivo sale del sistema, queda quien, que reporte, que rango y cuantas filas
        audit(db, p.tenant.id, p.user.id, "export", "report", None, {"report": key, "format": "xlsx", "from": str(a), "to": str(b), "rows": len(rep.rows)}, ip=p.ip)
        db.commit()
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{key}-{a}-{b}.xlsx"'},
        )
    return {"key": rep.key, "title": rep.title, "from": a, "to": b, "columns": rep.columns, "rows": rep.rows, "totals": rep.totals}


# ---------- Recurrencias ----------
class RecurrenceIn(BaseModel):
    name: str
    kind: str = Field("factura", pattern="^(factura|gasto)$")
    customer_id: int | None = None
    template: dict
    frequency: str = Field("mensual", pattern="^(semanal|quincenal|mensual|anual)$")
    next_date: date
    end_date: date | None = None
    auto_send: bool = True
    active: bool = True
    anchor_day: int | None = Field(None, ge=1, le=31)
    # plan 1.4: la primera factura espera "Aprobar y enviar". None al editar = no cambia (al crear: verdadero)
    first_run_requires_approval: bool | None = None


REC_PERMS = {
    "factura": {"ver": ("sales", "recurrencias"), "crear": ("sales", "recurrencias"), "editar": ("sales", "recurrencias")},
    "gasto": {"ver": ("accounting", "ver"), "crear": ("accounting", "crear"), "editar": ("accounting", "editar")},
}


def _rec_guard(p: Principal, kind: str, accion: str) -> None:
    mod, act = REC_PERMS[kind][accion]
    if not p.can(mod, act):
        raise HTTPException(403, f"Sin permiso: {mod}.{act}")


def _last_day(y: int, m: int) -> int:
    import calendar

    return calendar.monthrange(y, m)[1]


def _next(d: date, f: str, anchor: int | None = None) -> date:
    """Siguiente fecha. El dia ancla evita que un 31 "baje al 28" para siempre y que el 29 de febrero reviente."""
    a = anchor or d.day
    if f == "semanal":
        return d + timedelta(days=7)
    if f == "quincenal":
        first, second = (a, a + 15) if a <= 15 else (a - 15, a)
        if d.day < min(second, _last_day(d.year, d.month)):
            return d.replace(day=min(second, _last_day(d.year, d.month)))
        m = d.month % 12 + 1
        y = d.year + (1 if m == 1 else 0)
        return date(y, m, min(first, _last_day(y, m)))
    if f == "anual":
        y = d.year + 1
        return date(y, d.month, min(a, _last_day(y, d.month)))
    m = d.month % 12 + 1
    y = d.year + (1 if m == 1 else 0)
    return date(y, m, min(a, _last_day(y, m)))


def _rec_template(data: RecurrenceIn, db: Session | None = None, tenant_id: int | None = None) -> dict:
    """Valida la plantilla segun el tipo: factura -> DocumentIn, gasto -> ExpenseIn (la fecha se pone al correr).
    Gasto: por defecto sin credito (el hosting en dolares del exterior no trae factura electronica costarricense; si la
    trae, el credito llega al vincular la recepcion al gasto generado)."""
    if data.kind == "factura":
        if not data.customer_id:
            raise HTTPException(422, "La recurrencia de factura requiere cliente")
        if db is not None and tenant_id is not None:
            docsvc.check_customer(db, tenant_id, data.customer_id)  # de ESTA empresa: cada corrida le factura a el
        return DocumentIn(**data.template).model_dump(mode="json")
    tpl = {"iva_credit": "no_credito", "tax_rate": 0, **data.template}
    exp = ExpenseIn(**{**tpl, "date": data.next_date})
    if exp.subtotal < 0 or (exp.tax_credit_amount is not None and exp.tax_credit_amount < 0):
        raise HTTPException(422, "La plantilla del gasto no puede tener montos negativos")
    if db is not None and tenant_id is not None:
        _check_refs(db, tenant_id, exp.supplier_id, exp.category_id, exp.bank_account_id)
    return exp.model_dump(mode="json", exclude={"date"})


def _last_invoice_out(db: Session, r: Recurrence) -> dict | None:
    """Ultima factura generada con su estado ("FEC-000512 · enviada" en vez de "#2", plan 1.4)."""
    i = db.get(Invoice, r.last_invoice_id) if r.last_invoice_id else None
    if i is None or i.tenant_id != r.tenant_id:
        return None
    return {"id": i.id, "number": i.number, "status": i.status, "sent_at": i.sent_at, "balance": i.balance, "currency": i.currency}


def _plan_ally_id(db: Session, r: Recurrence) -> int | None:
    """La empresa aliada de una mensualidad (la plantilla lleva PLAN_ORDER): se edita desde su ficha, no desde aqui."""
    from ..models import AllyCompany

    if (r.template or {}).get("external_order") != PLAN_ORDER:
        return None
    return db.scalar(select(AllyCompany.id).where(AllyCompany.tenant_id == r.tenant_id, AllyCompany.recurrence_id == r.id).limit(1))


def _rec_out(r: Recurrence, db: Session):
    c = db.get(Customer, r.customer_id) if r.customer_id else None
    c = c if c is not None and c.tenant_id == r.tenant_id else None  # nunca el nombre de un cliente de otra empresa
    plan = (r.template or {}).get("external_order") == PLAN_ORDER
    return {
        "id": r.id,
        "name": r.name,
        "kind": r.kind,
        "last_expense_id": r.last_expense_id,
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
        "anchor_day": r.anchor_day,
        "first_run_requires_approval": r.first_run_requires_approval,
        "last_send_status": r.last_send_status,
        "last_send_note": r.last_send_note,
        "last_invoice": _last_invoice_out(db, r),
        # mensualidad de un aliado: el portal la abre en la ficha del aliado (Aliados -> Mensualidad)
        "plan": plan,
        "ally_id": _plan_ally_id(db, r) if plan else None,
        # las mensuales que el calculo viejo ya bajo al 28 quedaron ancladas en 28
        "anchor_warning": "Ancla 28: revisá si era fin de mes" if r.anchor_day == 28 and r.runs > 0 and r.frequency in ("mensual", "quincenal") else None,
    }


@router.get("/recurrences")
def recurrences(kind: str | None = Query(None, pattern="^(factura|gasto)$"), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    kinds = [k for k in ("factura", "gasto") if p.can(*REC_PERMS[k]["ver"]) and (kind is None or k == kind)]
    if not kinds:
        raise HTTPException(403, "Sin permiso: sales.recurrencias|accounting.ver")
    q = select(Recurrence).where(Recurrence.tenant_id == p.tenant.id, Recurrence.kind.in_(kinds)).order_by(Recurrence.next_date)
    return [_rec_out(r, db) for r in db.scalars(q)]


def send_terms(db: Session, tenant_id: int, customer_id: int | None, template: dict | None) -> tuple:
    """A quien y cuanto le cobra la recurrencia: (cliente, moneda, total de la plantilla). Si cambia, la aprobacion
    de la primera ya no cubre lo que sale (otro destinatario u otro monto)."""
    from ..tasks.mensualidades import template_total_of

    tot = template_total_of(db, tenant_id, customer_id, template)
    return (customer_id, tot[1] if tot else (template or {}).get("currency"), tot[0] if tot else None)


def send_flags(r: Recurrence) -> dict:
    return {"auto_send": bool(r.auto_send), "first_run_requires_approval": bool(r.first_run_requires_approval)}


def schedule_of(r: Recurrence) -> dict:
    """Cuando y cada cuanto le llega una factura al cliente (lo que cubre la revision humana, ademas de send_terms)."""
    return {"frequency": r.frequency, "next_date": r.next_date, "anchor_day": r.anchor_day, "auto_send": bool(r.auto_send), "active": bool(r.active)}


def sends_sooner(antes: dict, r: Recurrence) -> bool:
    """El cambio hace que al cliente le lleguen facturas antes, mas seguido o de nuevo solas: otra frecuencia, la
    proxima fecha adelantada o puesta en el pasado (el worker se pone al dia), otro dia ancla sin posponer la fecha, o
    el envio automatico (o la recurrencia) que se vuelve a encender."""
    from ..services.invoice_states import cr_today

    if r.frequency != antes["frequency"]:
        return True
    if r.next_date != antes["next_date"] and (r.next_date < antes["next_date"] or r.next_date < cr_today()):
        return True
    if r.anchor_day != antes["anchor_day"] and r.next_date <= antes["next_date"]:
        return True
    return (bool(r.auto_send) and not antes["auto_send"]) or (bool(r.active) and not antes["active"])


def needs_rearm(p: Principal, terms_changed: bool, antes_schedule: dict, r: Recurrence) -> bool:
    """La proxima vuelve a esperar "Aprobar y enviar": cambio a quien o cuanto se le cobra (cualquiera que edite), o
    alguien sin permiso de envio (contabilidad: crea y edita, pero no envia) cambio cuando o cada cuanto sale. Sin esto,
    una frecuencia semanal o una fecha de hace un ano le mandarian una factura por corrida al cliente sin que nadie
    con sales.enviar la viera."""
    return terms_changed or (not p.can("sales", "enviar") and sends_sooner(antes_schedule, r))


def set_send_approval(p: Principal, r: Recurrence, requested: bool | None, rearm: bool = False) -> None:
    """La revision humana antes de que la recurrencia le mande correos solos al cliente (plan 1.4).

    - Cambio el cliente, la moneda o el total, o alguien sin sales.enviar cambio la frecuencia, adelanto la fecha o
      volvio a encender el envio (rearm, needs_rearm): la proxima vuelve a esperar "Aprobar y enviar", pida lo que pida
      el formulario. Quien quiera quitarla la desmarca en otro guardado.
    - Quitar la revision (dejar que salgan solas) es de quien puede enviar facturas (sales.enviar): contabilidad crea y
      edita recurrencias y planes, pero no envia (la aprobacion exige ese permiso)."""
    if rearm:
        r.first_run_requires_approval = True
        return
    if requested is None or requested == bool(r.first_run_requires_approval):
        return
    if not requested and not p.can("sales", "enviar"):
        raise HTTPException(403, "Sin permiso: sales.enviar. Solo quien envía facturas puede dejar que salgan solas sin revisar la próxima.")
    r.first_run_requires_approval = requested


@router.post("/recurrences", status_code=201)
def recurrence_create(data: RecurrenceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _rec_guard(p, data.kind, "crear")
    r = Recurrence(
        tenant_id=p.tenant.id,
        name=data.name,
        kind=data.kind,
        customer_id=data.customer_id if data.kind == "factura" else None,
        template=_rec_template(data, db, p.tenant.id),
        frequency=data.frequency,
        next_date=data.next_date,
        end_date=data.end_date,
        auto_send=data.auto_send,
        active=data.active,
        anchor_day=data.anchor_day or data.next_date.day,
        first_run_requires_approval=True,
    )
    if r.kind == "factura":
        set_send_approval(p, r, data.first_run_requires_approval)
    db.add(r)
    db.flush()
    if r.kind == "factura":
        audit(db, p.tenant.id, p.user.id, "create", "recurrence", r.id, {**send_flags(r), "customer_id": r.customer_id}, ip=p.ip)
    db.commit()
    return _rec_out(r, db)


def _rec_get(db: Session, p: Principal, rid: int) -> Recurrence:
    r = db.get(Recurrence, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Recurrencia no encontrada")
    return r


@router.put("/recurrences/{rid}")
def recurrence_update(rid: int, data: RecurrenceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _rec_get(db, p, rid)
    _rec_guard(p, r.kind, "editar")
    if data.kind != r.kind:
        raise HTTPException(409, "No se puede cambiar el tipo de una recurrencia")
    if (r.template or {}).get("external_order") == PLAN_ORDER:
        # la plantilla de la mensualidad (monto, moneda, marca de aliado) la arma el plan; este formulario la pisaria con
        # solo producto y cantidad (precio del catalogo, en colones) y dejaria de ser la mensualidad del aliado
        raise HTTPException(409, "Es la mensualidad de un aliado: cambiala desde su ficha (Aliados → Mensualidad).")
    factura = r.kind == "factura"
    antes = {**send_flags(r), "terms": send_terms(db, r.tenant_id, r.customer_id, r.template)} if factura else None
    agenda = schedule_of(r)
    r.anchor_day = _anchor_for_update(r, data)  # antes de pisar next_date: compara con la fecha guardada
    r.name, r.customer_id, r.template, r.frequency, r.next_date, r.end_date, r.auto_send, r.active = (
        data.name,
        data.customer_id if r.kind == "factura" else None,
        _rec_template(data, db, p.tenant.id),
        data.frequency,
        data.next_date,
        data.end_date,
        data.auto_send,
        data.active,
    )
    if factura:
        terms = send_terms(db, r.tenant_id, r.customer_id, r.template)
        set_send_approval(p, r, data.first_run_requires_approval, rearm=needs_rearm(p, terms != antes["terms"], agenda, r))
        antes = {**antes, **_jsonable_schedule(agenda)}
        despues = {**send_flags(r), "terms": terms, **_jsonable_schedule(schedule_of(r))}
        if despues != antes:
            # a quien, cuanto, cuando y si sale solo: queda en la bitacora con el antes y el despues
            audit_change(db, p, "update", "recurrence", r.id, _jsonable_terms(antes), _jsonable_terms(despues))
    db.commit()
    return _rec_out(r, db)


def _jsonable_terms(x: dict) -> dict:
    cliente, moneda, total = x["terms"]
    return {**{k: v for k, v in x.items() if k != "terms"}, "customer_id": cliente, "currency": moneda, "total": str(total) if total is not None else None}


def _jsonable_schedule(x: dict) -> dict:
    return {"frequency": x["frequency"], "next_date": x["next_date"].isoformat() if x["next_date"] else None, "anchor_day": x["anchor_day"], "active": x["active"]}


def _anchor_for_update(r: Recurrence, data: RecurrenceIn) -> int:
    """Dia ancla al editar. Un ancla distinta a la guardada manda. Si solo cambio la proxima fecha (el portal reenvia el
    ancla vieja o la deja vacia), el ancla pasa a ser el dia de la fecha nueva, salvo que la fecha nueva sea justo el
    ancla recortada al fin de ese mes (31 -> 28 de febrero): ahi el 31 se conserva."""
    if data.anchor_day is not None and data.anchor_day != r.anchor_day:
        return data.anchor_day
    if data.next_date != r.next_date:
        old = r.anchor_day or r.next_date.day
        if min(old, _last_day(data.next_date.year, data.next_date.month)) != data.next_date.day:
            return data.next_date.day
        return old
    return data.anchor_day or r.anchor_day or data.next_date.day


def _advance(r: Recurrence) -> None:
    r.next_date = _next(r.next_date, r.frequency, r.anchor_day)
    if r.end_date and r.next_date > r.end_date:
        r.active = False


def run_recurrence(db: Session, r: Recurrence, user_id: int | None) -> Invoice | Expense:
    """Genera el documento del periodo r.next_date y avanza. Gastos: idempotente por (recurrencia, periodo)."""
    if r.kind == "gasto":
        from sqlalchemy.exc import IntegrityError

        periodo = r.next_date
        e = db.scalar(select(Expense).where(Expense.tenant_id == r.tenant_id, Expense.recurrence_id == r.id, Expense.recurrence_date == periodo))
        if e is None:
            data = ExpenseIn(**{**r.template, "date": periodo})
            if data.fx_rate is None and data.currency != "CRC":
                # el tipo de cambio es el de la fecha del periodo (al ponerse al dia, cada mes con el suyo)
                data = data.model_copy(update={"fx_rate": bccr_sell(db, data.currency, periodo)})
            sp = db.begin_nested()
            try:
                e = Expense(tenant_id=r.tenant_id, created_by=user_id or None, recurrence_id=r.id, recurrence_date=periodo)
                _apply_expense(e, data, db)
                db.add(e)
                db.flush()
                sp.commit()
                r.runs += 1
            except IntegrityError:
                sp.rollback()  # otro proceso ya genero este periodo: solo se avanza
                e = db.scalar(select(Expense).where(Expense.tenant_id == r.tenant_id, Expense.recurrence_id == r.id, Expense.recurrence_date == periodo))
        r.last_expense_id = e.id
        _advance(r)
        return e
    payload = DocumentIn(**{**r.template, "customer_id": r.customer_id})
    result = docsvc.create_invoice(db, r.tenant_id, user_id, payload, seller_id=None)  # recurrencia: sin vendedor
    result.recurrence_id = r.id  # sin comprobante fiscal aceptado sale como "aviso de cobro" (render.doc_kind)
    inv.deduct_for_invoice(db, result, user_id)
    r.last_invoice_id = result.id
    r.runs += 1
    # el envio va despues del commit (deliver_recurrence): un correo nunca sale por una factura que no quedo guardada.
    # "enviando" hasta que deliver_recurrence diga que paso: si el worker se cae entre los dos pasos, la factura no se
    # pierde de vista (Aprobaciones, recover_interrupted_sends) ni deja generar el periodo siguiente
    r.last_send_status = "por_aprobar" if (r.auto_send and r.first_run_requires_approval) else ("enviando" if r.auto_send else "pendiente")
    r.last_send_note = None
    _advance(r)
    return result


def _rec_alert(db: Session, r: Recurrence, invoice: Invoice, title: str, body: str) -> None:
    from html import escape

    from ..models import Tenant
    from ..services.email_layout import cta
    from ..services.mail import notify_roles
    from ..services.render import money

    t = db.get(Tenant, r.tenant_id)
    c = db.get(Customer, invoice.customer_id) if invoice.customer_id else None
    quien = c.name if c is not None and c.tenant_id == r.tenant_id else "sin cliente"
    notify_roles(
        db,
        t,
        ("admin",),
        f"{title} · {invoice.number}",
        f"<p><b>{escape(r.name or '')}</b> · {escape(quien)} · {escape(invoice.number)} por {money(invoice.total, invoice.currency)}.</p><p>{escape(body)}</p>"
        + cta(f"/facturas/{invoice.id}", f"Abrir la factura {invoice.number}"),
        "recurrence",
        r.id,
    )


def _rec_behind_alert(db: Session, r: Recurrence) -> None:
    """La recurrencia tiene periodos atrasados: se genero una sola factura y administracion revisa antes de las demas."""
    from html import escape

    from ..models import Tenant
    from ..services.email_layout import cta
    from ..services.mail import notify_roles

    t = db.get(Tenant, r.tenant_id)
    notify_roles(
        db,
        t,
        ("admin",),
        f"Recurrencia «{r.name}» con periodos atrasados",
        f"<p>La recurrencia de factura <b>{escape(r.name or '')}</b> tiene periodos atrasados (siguiente: {r.next_date}). "
        "Se generó una sola factura; revísela antes de generar las demás.</p>"
        + cta("/recurrencias", "Abrir Recurrencias"),
        "recurrence",
        r.id,
    )


def _rec_alert_once(db: Session, r: Recurrence, invoice: Invoice, title: str, body: str) -> None:
    """_rec_alert que no se repite: el worker corre a diario y el periodo detenido sigue igual hasta que alguien actua."""
    from ..models import EmailOutbox

    ya = db.scalar(
        select(EmailOutbox.id)
        .where(EmailOutbox.tenant_id == r.tenant_id, EmailOutbox.entity == "recurrence", EmailOutbox.entity_id == r.id, EmailOutbox.subject == f"{title} · {invoice.number}")
        .limit(1)
    )
    if ya is None:
        _rec_alert(db, r, invoice, title, body)


PRODUCCION_SIN_EMITIR = "Emítala antes de enviarla: Hacienda está en producción y la entrega fiscal le manda el XML al cliente."


def _unemitted_in_production(db: Session, tenant, invoice: Invoice) -> bool:
    """Hacienda en produccion y la factura sin comprobante aceptado: no sale como "aviso de cobro" sin el XML. La
    emision automatica de las mensualidades es del plan 2.1; mientras tanto una persona la emite."""
    from ..services.hacienda_config import hacienda_mode

    return invoice.einvoice_status != "aceptada" and hacienda_mode(db, tenant) == "produccion"


def deliver_recurrence(db: Session, r: Recurrence, invoice: Invoice, user_id: int | None = None, approved: bool = False, hold: bool = False) -> dict:
    """Envio automatico de la factura de la recurrencia (plan 1.4), despues de guardarla.

    - Sin auto_send no se envia (queda "pendiente" para enviarla a mano).
    - La primera de cada plan (first_run_requires_approval) queda en Aprobaciones -> "Mensualidades por aprobar" y el
      admin recibe el aviso. approved=True es "Aprobar y enviar": desde ahi las siguientes salen solas.
    - hold: la genero alguien sin permiso de envio ("Generar ahora" de contabilidad): tambien espera "Aprobar y enviar".
    - Queda "enviada" solo si el proveedor acepto el correo; si falla (o el cliente no tiene correo) se avisa al admin.
    - Sin comprobante fiscal (modo de Hacienda "ninguno" o sin emitir) sale como "aviso de cobro" con su PDF y enlace,
      sea la mensualidad de un aliado o cualquier otra recurrencia (render.doc_kind: Invoice.recurrence_id). Con Hacienda en produccion no sale sin emitir: queda por aprobar con la nota y el
      aviso al admin (la emision automatica llega con el plan 2.1).
    No hace commit."""
    from ..models import Tenant
    from ..services.documents_send import send_document

    plan = invoice.external_order == PLAN_ORDER
    que = "La mensualidad" if plan else "La factura recurrente"
    sin_enviar = "Mensualidad sin enviar" if plan else "Factura recurrente sin enviar"
    por_aprobar = "Mensualidad por aprobar" if plan else "Factura recurrente por aprobar"
    t = db.get(Tenant, r.tenant_id)
    if not approved:
        if not r.auto_send:
            r.last_send_status = "pendiente"
            return {"status": "pendiente", "sent": False, "note": None}
        if r.first_run_requires_approval or hold:
            r.last_send_status = "por_aprobar"
            r.last_send_note = None if r.first_run_requires_approval else "La generó alguien sin permiso de envío"
            _rec_alert(
                db,
                r,
                invoice,
                por_aprobar,
                f"{que} quedó generada y espera su visto bueno: revísela y toque «Aprobar y enviar» en Aprobaciones. "
                + ("Desde la siguiente, sale sola con su PDF y enlace de pago." if r.first_run_requires_approval else "La generó alguien que no envía facturas."),
            )
            return {"status": "por_aprobar", "sent": False, "note": r.last_send_note}
        if _unemitted_in_production(db, t, invoice):
            r.last_send_status, r.last_send_note = "por_aprobar", PRODUCCION_SIN_EMITIR
            _rec_alert(db, r, invoice, por_aprobar, f"{que} quedó generada pero no salió: {PRODUCCION_SIN_EMITIR}")
            return {"status": "por_aprobar", "sent": False, "note": r.last_send_note}
    else:
        if _unemitted_in_production(db, t, invoice):
            raise HTTPException(409, PRODUCCION_SIN_EMITIR)
        r.first_run_requires_approval = False  # se aprobo el contenido; si el correo falla se reenvia aparte
    c = db.get(Customer, invoice.customer_id) if invoice.customer_id else None
    if c is None or c.tenant_id != r.tenant_id or not (c.email or "").strip():
        r.last_send_status, r.last_send_note = "sin_correo", "El cliente no tiene correo"
        _rec_alert(db, r, invoice, sin_enviar, f"{que} no salió: el cliente no tiene correo. Agréguelo en su ficha y reenvíela desde Aprobaciones.")
        return {"status": "sin_correo", "sent": False, "note": r.last_send_note}
    try:
        # el envio automatico lleva llave (una factura, un correo); "Aprobar y enviar" o "Reenviar" no
        res = send_document(db, t, invoice, None, None, True, user_id, dedupe_key=None if approved else f"rec:{r.id}:{invoice.id}")
    except HTTPException as e:
        res = {"sent": False, "status": "error", "note": str(e.detail)}
    if res["sent"]:
        r.last_send_status, r.last_send_note = "enviada", None
    else:
        r.last_send_status, r.last_send_note = "fallida", (res.get("note") or "El correo no salió")[:240]
        _rec_alert(db, r, invoice, sin_enviar, f"{que} no salió: {res.get('note') or 'el correo falló'} Reenvíela desde Aprobaciones o desde la factura.")
    return {"status": r.last_send_status, "sent": bool(res["sent"]), "note": r.last_send_note}


def pending_send(db: Session, r: Recurrence, settle: bool = True) -> Invoice | None:
    """La ultima factura de la recurrencia todavia no le llego al cliente y espera a una persona: "Aprobar y enviar"
    (por_aprobar) o "Reenviar" (fallida, sin_correo). Mientras tanto no se genera otra: no se le apilan facturas al
    cliente ni se pierde de vista la anterior (Aprobaciones solo mira la ultima). Devuelve esa factura o None.

    Resuelta: anulada, ya enviada (sent_at o un estado que solo existe despues de enviar) o pagada por otra via (saldo
    0). settle=True registra lo que paso si salio por otra via (settle_sent; normalmente ya lo hizo mark_sent)."""
    from ..services.invoice_states import is_sent

    if r.kind != "factura" or not r.last_invoice_id:
        return None
    last = db.get(Invoice, r.last_invoice_id)
    if last is None or last.tenant_id != r.tenant_id or last.status == "anulada":
        return None
    if is_sent(last) or Decimal(str(last.balance or 0)) <= 0:
        if settle:
            settle_sent(r, last)
        return None
    from ..services.approvals import PLAN_SEND_PENDING

    return last if r.last_send_status in PLAN_SEND_PENDING else None


# la ultima factura todavia no le llego al cliente (cualquier resultado de deliver_recurrence menos "enviada")
SEND_OPEN = ("por_aprobar", "enviando", "pendiente", "fallida", "sin_correo")


def settle_sent(r: Recurrence, last: Invoice) -> None:
    """La ultima factura ya le llego al cliente por otra via (enviada a mano desde la factura, enlace de pago compartido,
    entrega fiscal): la recurrencia muestra el estado real ("enviada") y, si era la que esperaba «Aprobar y enviar»,
    eso cuenta como la aprobacion y las siguientes salen solas. Un envio viejo (el relleno de sent_at de la migracion
    de cobranza, con last_send_status vacio) no apaga la revision pedida."""
    if last.sent_at is None or r.last_send_status not in SEND_OPEN:
        return
    if r.last_send_status == "por_aprobar" and r.first_run_requires_approval:
        r.first_run_requires_approval = False
    r.last_send_status, r.last_send_note = "enviada", None


def settle_invoice_sent(db: Session, invoice: Invoice) -> None:
    """Gancho de services/invoice_states.mark_sent: la recurrencia cuya ultima factura es esta registra que salio."""
    q = select(Recurrence).where(Recurrence.tenant_id == invoice.tenant_id, Recurrence.kind == "factura", Recurrence.last_invoice_id == invoice.id)
    for r in db.scalars(q).all():
        settle_sent(r, invoice)


def _pending_message(r: Recurrence, last: Invoice) -> str:
    if r.last_send_status == "por_aprobar":
        return f"La factura anterior ({last.number}) espera «Aprobar y enviar». Apruébela o anúlela antes de generar otra."
    return f"La factura anterior ({last.number}) no le llegó al cliente. Reenvíela o anúlela antes de generar otra."


@router.post("/recurrences/{rid}/run", status_code=201)
def recurrence_run(rid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _rec_get(db, p, rid)
    _rec_guard(p, r.kind, "editar")
    previa = pending_send(db, r)
    if previa is not None:
        db.commit()
        raise HTTPException(409, _pending_message(r, previa))
    doc = run_recurrence(db, r, p.user.id)
    db.commit()
    if isinstance(doc, Expense):
        return {"expense_id": doc.id, "total": doc.total, "next_date": r.next_date}
    doc_id, numero = doc.id, doc.number
    # le llega al cliente solo si quien la genera puede enviar facturas; si no, espera "Aprobar y enviar"
    envio = _deliver_or_fail(db, r.id, doc_id, p.user.id, hold=not p.can("sales", "enviar"))
    r = db.get(Recurrence, rid)
    return {"invoice_id": doc_id, "number": numero, "next_date": r.next_date, "send_status": envio["status"], "sent": envio["sent"]}


@router.post("/recurrences/{rid}/approve-send")
def recurrence_approve_send(rid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """"Aprobar y enviar" (Aprobaciones -> Mensualidades por aprobar): la persona revisa la primera factura del plan y
    la manda; desde ahi las siguientes salen solas. Tambien reenvia una que fallo o que quedo sin correo."""
    r = _rec_get(db, p, rid)
    if r.kind != "factura":
        raise HTTPException(409, "Solo las recurrencias de factura se envían")
    _rec_guard(p, "factura", "editar")
    if not p.can("sales", "enviar"):
        raise HTTPException(403, "Sin permiso: sales.enviar")
    d = db.get(Invoice, r.last_invoice_id) if r.last_invoice_id else None
    if d is None or d.tenant_id != p.tenant.id or d.status == "anulada":
        raise HTTPException(409, "Esta recurrencia no tiene una factura por enviar")
    if d.sent_at is not None and r.last_send_status == "enviada":
        raise HTTPException(409, "Esa factura ya se envió")
    if d.status == "pagada" or Decimal(str(d.balance or 0)) <= 0:
        raise HTTPException(409, "Esa factura ya está pagada: no se le cobra al cliente")
    out = deliver_recurrence(db, r, d, p.user.id, approved=True)
    audit(db, p.tenant.id, p.user.id, "approve_send", "recurrence", r.id, {"invoice_id": d.id, "resultado": r.last_send_status}, ip=p.ip)
    db.commit()
    return {"invoice_id": d.id, "number": d.number, "sent": out["sent"], "status": r.last_send_status, "note": r.last_send_note}


def _deliver_or_fail(db: Session, rid: int, invoice_id: int, user_id: int | None = None, hold: bool = False) -> dict:
    """deliver_recurrence con su commit. Un error interno (el PDF sin memoria, un fallo inesperado) no deja la recurrencia
    "enviando" en silencio: queda "fallida", con el aviso al admin y en Aprobaciones para reenviarla."""
    import logging

    r, factura = db.get(Recurrence, rid), db.get(Invoice, invoice_id)
    try:
        out = deliver_recurrence(db, r, factura, user_id, hold=hold)
        db.commit()
        return out
    except Exception:  # noqa: BLE001
        db.rollback()
        logging.getLogger("crimson.recurrences").exception("recurrencia %s: envio", rid)
    nota = "Error interno al enviarla"
    try:
        r, factura = db.get(Recurrence, rid), db.get(Invoice, invoice_id)
        r.last_send_status, r.last_send_note = "fallida", nota
        titulo = "Mensualidad sin enviar" if factura.external_order == PLAN_ORDER else "Factura recurrente sin enviar"
        _rec_alert(db, r, factura, titulo, "No salió por un error interno. Reenvíela desde Aprobaciones o desde la factura.")
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"status": "fallida", "sent": False, "note": nota}


STUCK_AFTER = timedelta(minutes=15)  # un envio tarda segundos: "enviando" por mas que esto es un envio interrumpido


def recover_interrupted_sends(db: Session, now: datetime | None = None) -> int:
    """Recurrencias que quedaron "enviando" (el worker se cayo entre guardar la factura y mandarla; Celery reentrega la
    tarea, pero next_date ya avanzo): pasan a "fallida" con el aviso al admin y siguen en Aprobaciones para que una
    persona la revise y la reenvie. No se mandan solas otra vez: el correo pudo haber salido justo antes de la caida.
    Devuelve cuantas recupero."""
    now = now or datetime.now(UTC)
    n = 0
    for r in db.scalars(select(Recurrence).where(Recurrence.kind == "factura", Recurrence.last_send_status == "enviando")).all():
        upd = r.updated_at if r.updated_at is None or r.updated_at.tzinfo else r.updated_at.replace(tzinfo=UTC)
        if upd is not None and now - upd < STUCK_AFTER:
            continue  # en curso
        last = db.get(Invoice, r.last_invoice_id) if r.last_invoice_id else None
        last = last if last is not None and last.tenant_id == r.tenant_id else None
        if last is not None and last.sent_at is not None:
            settle_sent(r, last)
            continue
        r.last_send_status, r.last_send_note = "fallida", "El envío automático se interrumpió"
        if last is not None and last.status != "anulada" and Decimal(str(last.balance or 0)) > 0:
            titulo = "Mensualidad sin enviar" if last.external_order == PLAN_ORDER else "Factura recurrente sin enviar"
            _rec_alert(db, r, last, titulo, "El envío automático se interrumpió y no se sabe si le llegó. Revísela y reenvíela desde Aprobaciones o desde la factura.")
        n += 1
    db.commit()
    return n


MAX_CATCH_UP = 12


def run_due_recurrences(db: Session, today: date | None = None) -> int:
    """Cada recurrencia vencida en su propia transaccion, con la fila bloqueada y next_date releido despues del bloqueo.
    Gastos: se pone al dia (max. 12 periodos por corrida). Facturas: UN periodo por corrida y, si hay mas atrasados,
    alerta al admin (no se le mandan varias facturas de golpe a un cliente). Una que falla no frena a las demas."""
    import logging

    from ..services.invoice_states import cr_today

    log = logging.getLogger("crimson.recurrences")
    today = today or cr_today()  # fecha de Costa Rica: a las 18:00 CR la de UTC ya es la de manana
    n = 0
    try:
        recover_interrupted_sends(db)
    except Exception:  # noqa: BLE001  la recuperacion no frena la corrida
        db.rollback()
        log.exception("recurrencias: envios interrumpidos")
    ids = db.scalars(select(Recurrence.id).where(Recurrence.active, Recurrence.next_date <= today).order_by(Recurrence.id)).all()
    db.commit()
    for rid in ids:
        try:
            r = db.execute(select(Recurrence).where(Recurrence.id == rid).with_for_update()).scalar_one()
            db.refresh(r)
            if not r.active or r.next_date > today:
                db.commit()
                continue
            if r.kind == "gasto":
                for _ in range(MAX_CATCH_UP):
                    run_recurrence(db, r, None)
                    n += 1
                    if not r.active or r.next_date > today:
                        break
            else:
                previa = pending_send(db, r)
                if previa is not None:
                    # la anterior sigue esperando a una persona: no se genera otra. Un aviso por periodo detenido.
                    _rec_alert_once(
                        db,
                        r,
                        previa,
                        f"Recurrencia detenida ({r.next_date:%d/%m/%Y})",
                        f"El periodo del {r.next_date:%d/%m/%Y} no se generó. {_pending_message(r, previa)} Se genera en la primera corrida después de que la envíe o la anule.",
                    )
                    db.commit()
                    continue
                factura = run_recurrence(db, r, None)
                n += 1
                if r.active and r.next_date <= today:
                    _rec_behind_alert(db, r)
                db.commit()
                _deliver_or_fail(db, rid, factura.id)  # ya guardada: el correo sale despues del commit
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.exception("recurrencia %s: %s", rid, type(e).__name__)  # con traza: es dinero que no se facturo
    return n


# ---------- Utilidad para el dashboard: stock bajo ----------
@router.get("/alerts")
def alerts(p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    return {"low_stock": inv.low_stock(db, p.tenant.id), "at": datetime.now(UTC)}
