"""Precio de aliado (Andres 18:39-19:16): "costo 100 -> aliado 105 -> publico 115". Es RECARGO SOBRE EL COSTO.

Orden de resolucion (ally_price):
1. precio fijo para esa empresa aliada (AllyProductPrice) -> "precio_aliado"
2. precio fijo general (ally_company_id NULL) -> "precio_general"
3. costo del producto + recargo (el de la empresa aliada, si no el de Ajustes ally_markup_pct, si no 5 %) -> "recargo"
4. nada aplica (o el recargo es 0 %) -> (None, None, "a_consultar"): "Precio a consultar con Crimson". NUNCA se devuelve
   el costo como precio.

Redondeo solo con recargo: colones hacia arriba a 10 (no a 100: comeria un recargo chico); otras monedas a 2 decimales.
Tope: nunca mas caro que el precio publico (estado "igual_al_publico").
Visibilidad: el aliado ve su precio y el publico; el tecnico aliado ninguno; adentro solo quien ve costos (el precio de
aliado deja deducir el costo: 105 / 1,05). El porcentaje nunca sale al portal.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import AllyCompany, AllyProductPrice, Product
from .pricing import cost_in, price_from_markup
from .totals import d

DEFAULT_MARKUP = Decimal(5)


def tenant_markup(tenant) -> Decimal:
    v = (tenant.settings or {}).get("ally_markup_pct")
    return d(v) if v is not None else DEFAULT_MARKUP


def markup_for(tenant, company: AllyCompany | None) -> Decimal:
    if company is not None and company.markup_pct is not None:
        return d(company.markup_pct)
    return tenant_markup(tenant)


def fixed_prices(db: Session, tenant_id: int, product_ids, company_id: int | None) -> dict:
    """{(product_id, ally_company_id|None): fila} de los productos del lote (una consulta, sin N+1)."""
    ids = list({i for i in product_ids if i})
    if not ids:
        return {}
    conds = [AllyProductPrice.ally_company_id.is_(None)]
    if company_id is not None:
        conds.append(AllyProductPrice.ally_company_id == company_id)
    rows = db.scalars(select(AllyProductPrice).where(AllyProductPrice.tenant_id == tenant_id, AllyProductPrice.product_id.in_(ids), or_(*conds)))
    return {(r.product_id, r.ally_company_id): r for r in rows}


def _round(p: Decimal, currency: str) -> Decimal:
    if currency == "CRC":
        return (p / 10).quantize(Decimal(1), rounding=ROUND_CEILING) * 10
    return p.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ally_price(
    db: Session, tenant, product: Product, company: AllyCompany | None, *, fx: Decimal, fixed: dict | None = None
) -> tuple[Decimal | None, str | None, str]:
    """(precio sin IVA en la moneda del producto | None, fuente, estado ok|a_consultar|igual_al_publico)."""
    if fixed is None:
        fixed = fixed_prices(db, product.tenant_id, [product.id], company.id if company else None)
    cid = company.id if company is not None else None
    row = fixed.get((product.id, cid)) if cid is not None else None
    fuente = "precio_aliado" if row is not None else None
    if row is None:
        row = fixed.get((product.id, None))
        fuente = "precio_general" if row is not None else None
    if row is not None:
        precio = d(row.price)
        if (row.currency or product.currency) != product.currency:
            # precio fijo en otra moneda: se lleva a la del producto con el tipo de cambio del dia
            precio = precio * d(fx) if product.currency == "CRC" else precio / d(fx)
            precio = _round(precio, product.currency)
    elif product.cost is not None and d(product.cost) > 0 and markup_for(tenant, company) > 0:
        # un recargo de 0 % (o negativo, de un dato viejo) daria el costo exacto como precio: eso nunca sale
        costo = cost_in(db, product, product.currency, fx)
        precio = _round(price_from_markup(costo, markup_for(tenant, company)), product.currency)
        fuente = "recargo"
    else:
        return None, None, "a_consultar"
    publico = d(product.price)
    if publico > 0 and precio > publico:
        return publico, fuente, "igual_al_publico"
    return precio, fuente, "ok"


def with_tax(price: Decimal | None, product: Product) -> Decimal | None:
    if price is None:
        return None
    rate = d(product.taxes[0].tax.rate) if product.taxes else Decimal(0)
    return (price * (1 + rate / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
