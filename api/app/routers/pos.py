"""POS web: venta de mostrador en un paso (tiquete o factura + pago(s) + inventario + emision fiscal).

El efectivo puede exceder el total: se registra solo lo que cubre la venta y se devuelve el vuelto.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Category, Customer, Product, ProductVariant, Warehouse
from ..schemas.sales import DocumentIn, LineInSchema, PaymentIn
from ..services import discounts
from ..services import documents as docsvc
from ..services import einvoice as esvc
from ..services import inventory as invsvc
from ..services.documents import audit
from ..services.fx import FxMissing, rate_or_none
from ..services.totals import d

router = APIRouter(prefix="/pos", tags=["pos"])


def _pos_price(db: Session, pr: Product, cur: str, fx: Decimal | None) -> Decimal | None:
    """Precio en la moneda de la caja; None si es de otra moneda y no hay tipo de cambio (nunca se convierte con 1)."""
    from ..services import pricing

    try:
        return pricing.price_in(db, pr, cur, fx)
    except FxMissing:
        return None


@router.get("/catalog")
def catalog(p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Productos activos con su disponibilidad y variantes, en un solo viaje (la pantalla filtra en el navegador).
    La existencia es la de la funcion unica (services/availability): lo libre en bodegas de venta menos reservas
    (las de garantias no cuentan) y lo del proveedor con su fecha. El precio va tambien en la moneda de la caja
    (pos_price / pos_currency), con el tipo de cambio del dia."""
    from ..services import availability as avsvc

    prods = list(db.scalars(select(Product).where(Product.tenant_id == p.tenant.id, Product.active).order_by(Product.name)))
    av = avsvc.availability_many(db, p.tenant, prods, audience="interno")
    cur = p.tenant.default_currency or "CRC"
    fx = rate_or_none(db, "USD")  # sin tasa la caja abre igual: solo lo que hay que convertir queda sin precio
    # existencia propia por bodega de venta activa (filtro "Bodega" de la caja, 26/10)
    venta = {w.id for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id, Warehouse.active, Warehouse.kind == "venta"))}
    por_bodega: dict[int, dict[str, float]] = defaultdict(dict)
    for lv in invsvc.stock_levels(db, p.tenant.id):
        if lv["warehouse_id"] in venta and lv["quantity"] > 0:
            por_bodega[lv["product_id"]][str(lv["warehouse_id"])] = float(lv["quantity"])
    variants: dict[int, list] = defaultdict(list)
    for v in db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == p.tenant.id, ProductVariant.active).order_by(ProductVariant.position)):
        variants[v.product_id].append({"id": v.id, "name": v.name, "code": v.code, "price": v.price})
    out = []
    for pr in prods:
        a = av.get(pr.id) or {}
        img = next((i.get("url") for i in (pr.images or []) if i.get("main")), (pr.images or [{}])[0].get("url") if pr.images else None)
        out.append(
            {
                "id": pr.id,
                "code": pr.code,
                "name": pr.name,
                "price": pr.price,
                "currency": pr.currency,
                "item_type": pr.item_type,
                "category_id": pr.category_id,
                "tax_rate": float(pr.taxes[0].tax.rate) if pr.taxes else 13,
                "stock": a.get("own_free") if pr.item_type == "producto" else None,
                "pos_price": _pos_price(db, pr, cur, fx),
                "pos_currency": cur,
                "availability": {
                    k: a.get(k)
                    for k in ("state", "label", "own_free", "own_reserved", "supplier_qty", "supplier_available", "supplier_name", "supplier_fresh", "supplier_age", "max_qty")
                },
                "image": img,
                "brand": pr.brand,
                "stock_by_wh": por_bodega.get(pr.id, {}),
                "variants": variants.get(pr.id, []),
            }
        )
    return out


@router.get("/filters")
def filters(p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Lo que la caja ofrece para filtrar el catalogo (26/10): categorias (con su padre) y bodegas de venta activas.
    Con el permiso de vender: la vendedora no necesita ver el modulo de inventario ni el de catalogo para filtrar."""
    cats = db.scalars(select(Category).where(Category.tenant_id == p.tenant.id).order_by(Category.name)).all()
    whs = db.scalars(
        select(Warehouse).where(Warehouse.tenant_id == p.tenant.id, Warehouse.active, Warehouse.kind == "venta").order_by(Warehouse.is_default.desc(), Warehouse.name)
    ).all()
    return {
        "categories": [{"id": c.id, "name": c.name, "parent_id": c.parent_id} for c in cats],
        "warehouses": [{"id": w.id, "name": w.name, "is_default": w.is_default} for w in whs],
    }


class PosLine(BaseModel):
    product_id: int
    variant_id: int | None = None
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(None, ge=0)  # override con permiso de edicion
    discount_value: Decimal = Field(Decimal(0), ge=0, le=100)  # % por linea


class PosPayment(BaseModel):
    method: str = Field(pattern="^(efectivo|tarjeta|sinpe|transferencia)$")
    amount: Decimal = Field(gt=0)
    reference: str | None = Field(None, max_length=120)


class SaleIn(BaseModel):
    doc_type: str = Field("TE", pattern="^(TE|FE)$")
    customer_id: int | None = None
    lines: list[PosLine] = Field(min_length=1)
    payments: list[PosPayment] = Field(min_length=1)
    tip: Decimal = Field(Decimal(0), ge=0)
    emit: bool = True
    notes: str | None = Field(None, max_length=500)


@router.post("/sale", status_code=201)
def sale(data: SaleIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    if data.doc_type == "FE":
        c = db.get(Customer, data.customer_id) if data.customer_id else None
        if not c or c.tenant_id != p.tenant.id or not c.id_number:
            raise HTTPException(422, "La factura electrónica requiere un cliente con identificación; use tiquete para consumidor final")
    if any(ln.unit_price is not None for ln in data.lines) and not p.can("sales", "editar"):
        raise HTTPException(403, "Sin permiso para cambiar precios en caja")
    from ..services import cartera, pricing

    # cartera con dueno: una vendedora no cobra a un cliente de otro vendedor; el cliente sin dueno al que ella le
    # cobra queda en su cartera (igual que el que crea desde la caja)
    cartera.guard_customer(db, p, data.customer_id)
    if data.customer_id and not p.sees_all_customers:
        c = db.get(Customer, data.customer_id)
        if c is not None and c.tenant_id == p.tenant.id:
            cartera.ensure_owner(db, p.tenant.id, c, p.user.id, p.user.id, "Le cobró en el punto de venta", ip=p.ip)

    cur = p.tenant.default_currency or "CRC"
    fx = rate_or_none(db, "USD")  # None: se consulta (y da 422 claro) solo si una linea necesita convertirse
    lines = []
    for ln in data.lines:
        pr = db.get(Product, ln.product_id)
        if not pr or pr.tenant_id != p.tenant.id or not pr.active:
            raise HTTPException(404, "Producto no disponible")
        # el precio del producto en la moneda de la caja (un producto en dolares no entra como colones)
        name, code, price = pr.name, pr.code, pricing.price_in(db, pr, cur, fx)
        if ln.variant_id:
            v = db.get(ProductVariant, ln.variant_id)
            if not v or v.product_id != pr.id or not v.active:
                raise HTTPException(404, "Variante no disponible")
            name, code = f"{pr.name} · {v.name}", v.code
            if v.price is not None:
                price = pricing.price_in(db, pr, cur, fx, amount=v.price)
        lines.append(
            LineInSchema(
                product_id=pr.id,
                code=code,
                name=name,
                quantity=ln.quantity,
                unit_price=ln.unit_price if ln.unit_price is not None else price,
                discount_type="percent",
                discount_value=ln.discount_value,
            )
        )
    payload = DocumentIn(
        customer_id=data.customer_id, currency=cur, lines=lines, internal_notes=data.notes, external_order="POS", payment_method="01"
    )
    chk = discounts.check(db, p, payload)
    if chk.exceeds:
        raise HTTPException(422, f"{chk.message} Un administrador debe aprobar descuentos mayores.")
    inv = docsvc.create_invoice(db, p.tenant.id, p.user.id, payload, data.doc_type)
    razones = discounts.approval_reasons(db, p, inv)
    if razones:
        raise HTTPException(422, f"Esta venta necesita aprobación: hágala como cotización. Motivo: {razones[0]}")
    total = d(inv.total)
    paid = sum((pay.amount for pay in data.payments), Decimal(0))
    if paid < total:
        raise HTTPException(422, f"Pago insuficiente: faltan {total - paid:,.2f}")
    # el vuelto sale del efectivo; los metodos electronicos deben cubrir exacto
    non_cash = sum((pay.amount for pay in data.payments if pay.method != "efectivo"), Decimal(0))
    if non_cash > total:
        raise HTTPException(422, "Los pagos con tarjeta/SINPE/transferencia no pueden exceder el total")
    change = paid - total
    remaining = total
    for i, pay in enumerate(sorted(data.payments, key=lambda x: x.method == "efectivo")):
        amount = min(pay.amount, remaining)
        if amount <= 0:
            continue
        docsvc.add_payment(
            db,
            p.tenant.id,
            p.user.id,
            inv,
            PaymentIn(method=pay.method, amount=amount, external_ref=pay.reference, tip=data.tip if i == 0 else Decimal(0), notes="POS"),
        )
        remaining -= amount
    invsvc.deduct_for_invoice(db, inv, p.user.id)
    einvoice = None
    if data.emit:
        try:
            doc = esvc.emit(db, p.tenant, p.user.id, inv)
            einvoice = doc.status
        except HTTPException as e:  # la venta queda registrada; la emision se reintenta desde la factura
            einvoice = f"sin_emitir: {e.detail}"
    audit(db, p.tenant.id, p.user.id, "pos_sale", "invoice", inv.id, {"total": str(total), "change": str(change)}, ip=p.ip)
    db.commit()
    return {"invoice_id": inv.id, "number": inv.number, "doc_type": inv.doc_type, "total": total, "paid": paid, "change": change, "einvoice": einvoice}
