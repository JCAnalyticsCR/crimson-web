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
from ..services.bitacora import audit_change, next_version, snapshot
from ..services.documents import audit
from ..services.mail import doc_email_html, queue_email
from ..services.ownership import owns_doc
from ..services.render import money, render_html, render_pdf

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
    if kind not in ("quotes", "invoices"):
        raise HTTPException(404)
    if kind == "quotes" and not p.sends_quotes:
        # contabilidad envia facturas (plan 1.7); la cotizacion es de quien cotiza
        raise HTTPException(403, "Enviar cotizaciones es de quien cotiza (crear o editar ventas)")
    d = _doc(db, kind, id_, p.tenant.id, p)
    if kind == "quotes":
        # las mismas guardas que "Marcar enviada" (POST /quotes/{id}/send): con un descuento o un margen esperando al
        # admin, por correo se le saltaria la aprobacion
        if d.status == "por_aprobar":
            raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
        if d.status in ("convertida", "anulada"):
            raise HTTPException(409, f"La cotización está {d.status}")
    c = db.get(Customer, d.customer_id) if d.customer_id else None
    to = data.to or (c.email if c else None)
    if not to:
        raise HTTPException(422, "El cliente no tiene correo; indique uno")
    is_inv = kind == "invoices"
    if not is_inv:
        from ..services.units import check_ready

        check_ready(d, "enviar la cotización")
    link = None
    if is_inv and data.with_payment_link and Decimal(str(d.balance)) > 0:
        link = docsvc.get_or_create_payment_link(db, p.tenant.id, d).url
    subject = f"{'Factura' if is_inv else 'Cotización'} {d.number} · {p.tenant.name}"
    # El PDF se genera aca y viaja adjunto. Si el servidor no puede generarlo (sin WeasyPrint), el correo no
    # promete un adjunto que no lleva.
    pdf = render_pdf(render_html(db, d, p.tenant))
    files = [(f"{d.number}.pdf", pdf)] if pdf else []
    if is_inv:
        from ..services.einvoice_delivery import attachments_for_invoice

        files += attachments_for_invoice(db, d)  # Decreto 44739-H art. 18: XML firmado + respuesta de Hacienda
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
            attached=pdf is not None,
        ),
        kind[:-1],
        d.id,
        [{"name": n, "kind": kind, "ref": d.id} for n, _ in files],
        files=files or None,
    )
    # Solo se marca como enviada si el proveedor acepto el correo. Simulado (sin llave de Resend) o error
    # no cuentan: marcarla enviada haria creer al vendedor que el cliente ya la tiene.
    sent = m.status == "enviado"
    if sent and d.status == "creado":
        d.status = "enviada"
    version = None
    if sent and not is_inv:
        from ..services import quote_versions as qv

        # lo que salio queda congelado como version (v1, v2... si se edito despues de enviarla)
        version = qv.record_send(db, p.tenant, p.user.id, d, "correo", to, ip=p.ip)[0].version
    if sent and not is_inv:
        from ..models import Opportunity

        for o in db.scalars(select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.quote_id == d.id)):
            if o.status in ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando"):
                audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": o.status, "a": "enviada"}, ip=p.ip)
                o.status = "enviada"
    audit(db, p.tenant.id, p.user.id, "send", kind[:-1], d.id, {"to": to, "mail": m.status}, ip=p.ip)
    db.commit()
    if m.status == "simulado":
        note = "El correo NO salió: no hay servicio de correo configurado (falta la llave de Resend). El documento no se marcó como enviado."
    elif m.status == "error":
        note = f"El correo NO salió ({(m.error or 'error del proveedor')[:160]}). El documento no se marcó como enviado."
    else:
        note = None
    return {"status": m.status, "sent": sent, "to": to, "note": note, "version": version}


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
    return Decimal(str(v or 0)) * (Decimal(str(e.fx_rate or 1)) if e.currency != "CRC" else Decimal(1))


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
    """Venta del BCCR de esa fecha o la ultima anterior (None si no hay ninguna)."""
    from ..models import ExchangeRate

    if currency == "CRC":
        return Decimal(1)
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency, ExchangeRate.date <= on).order_by(ExchangeRate.date.desc()))
    return Decimal(str(fx.sell)) if fx else None


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
        fx = bccr_sell(db, data.currency, data.date) if db is not None else None
        e.fx_rate = fx or Decimal(1)
        if fx is None:
            warnings.append("Sin tipo de cambio del BCCR para esa fecha: se usó 1; indíquelo a mano")
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


def _rec_out(r: Recurrence, db: Session):
    c = db.get(Customer, r.customer_id) if r.customer_id else None
    c = c if c is not None and c.tenant_id == r.tenant_id else None  # nunca el nombre de un cliente de otra empresa
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
    )
    db.add(r)
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
    db.commit()
    return _rec_out(r, db)


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
    inv.deduct_for_invoice(db, result, user_id)
    r.last_invoice_id = result.id
    r.runs += 1
    _advance(r)
    return result


@router.post("/recurrences/{rid}/run", status_code=201)
def recurrence_run(rid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _rec_get(db, p, rid)
    _rec_guard(p, r.kind, "editar")
    doc = run_recurrence(db, r, p.user.id)
    db.commit()
    if isinstance(doc, Expense):
        return {"expense_id": doc.id, "total": doc.total, "next_date": r.next_date}
    return {"invoice_id": doc.id, "number": doc.number, "next_date": r.next_date}


MAX_CATCH_UP = 12


def run_due_recurrences(db: Session, today: date | None = None) -> int:
    """Cada recurrencia vencida en su propia transaccion, con la fila bloqueada y next_date releido despues del bloqueo.
    Gastos: se pone al dia (max. 12 periodos por corrida). Facturas: UN periodo por corrida y, si hay mas atrasados,
    alerta al admin (no se le mandan varias facturas de golpe a un cliente). Una que falla no frena a las demas."""
    import logging

    log = logging.getLogger("crimson.recurrences")
    today = today or date.today()
    n = 0
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
                run_recurrence(db, r, None)
                n += 1
                if r.active and r.next_date <= today:
                    from html import escape

                    from ..models import Tenant
                    from ..services.mail import notify_roles

                    t = db.get(Tenant, r.tenant_id)
                    notify_roles(
                        db,
                        t,
                        ("admin",),
                        f"Recurrencia «{r.name}» con periodos atrasados",
                        f"<p>La recurrencia de factura <b>{escape(r.name or '')}</b> tiene periodos atrasados (siguiente: {r.next_date}). "
                        "Se generó una sola factura; revísela antes de generar las demás.</p>",
                        "recurrence",
                        r.id,
                    )
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.exception("recurrencia %s: %s", rid, type(e).__name__)  # con traza: es dinero que no se facturo
    return n


# ---------- Utilidad para el dashboard: stock bajo ----------
@router.get("/alerts")
def alerts(p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    return {"low_stock": inv.low_stock(db, p.tenant.id), "at": datetime.now(UTC)}
