"""Costo -> precio de venta. El costo del proveedor suele venir en dolares y el precio de lista se guarda en colones.

precio = costo x tipo de cambio x (1 + margen)   ·   margen por defecto en Ajustes (default_margin_pct, 35 %).
El redondeo hacia arriba a la centena evita precios con colones sueltos en la cotizacion.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from .documents import today_fx
from .totals import d

DEFAULT_MARGIN = Decimal(35)
ROUND_TO = Decimal(100)  # colones


def default_margin(tenant) -> Decimal:
    v = (tenant.settings or {}).get("default_margin_pct")
    return d(v) if v is not None else DEFAULT_MARGIN


def sale_price(db: Session, cost: Decimal, cost_currency: str, target_currency: str, margin_pct: Decimal, fx: Decimal | None = None) -> Decimal:
    """Precio de venta sin IVA. fx se pasa para no consultar el tipo de cambio en cada linea de un import."""
    cost = d(cost)
    if cost_currency != target_currency:
        rate = fx if fx is not None else today_fx(db, cost_currency if cost_currency != "CRC" else target_currency)[0]
        cost = cost * d(rate) if cost_currency != "CRC" else cost / d(rate)
    price = cost * (1 + d(margin_pct) / 100)
    if target_currency == "CRC":
        return (price / ROUND_TO).quantize(Decimal(1), rounding=ROUND_CEILING) * ROUND_TO
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def margin_of(price: Decimal, cost_crc: Decimal) -> Decimal:
    """Margen sobre la venta (lo que se usa para hablar de "margen 35 %" en una cotizacion)."""
    price, cost_crc = d(price), d(cost_crc)
    if price <= 0:
        return Decimal(0)
    return ((price - cost_crc) / price * 100).quantize(Decimal("0.1"))


def cost_in(db: Session, product, currency: str = "CRC", fx: Decimal | None = None) -> Decimal:
    """Costo del producto en la divisa pedida. Sin costo registrado devuelve 0 (no inventa numeros)."""
    if product is None or product.cost is None:
        return Decimal(0)
    cost = d(product.cost)
    if (product.cost_currency or "USD") == currency:
        return cost
    rate = fx if fx is not None else today_fx(db, product.cost_currency or "USD")[0]
    return cost * d(rate) if currency == "CRC" else cost / d(rate)
