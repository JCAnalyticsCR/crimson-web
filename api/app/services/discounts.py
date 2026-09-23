"""Limite de descuento por rol.

Quien no tenga "sales.descuento_libre" (solo el administrador lo tiene) puede descontar como maximo el porcentaje
configurado en Ajustes -> Facturacion (max_discount_pct, 10 % por defecto). Cuenta como descuento:
- el descuento por linea (porcentaje o monto),
- el descuento global del documento (prorrateado),
- bajar el precio unitario por debajo del precio de catalogo (misma divisa).

Cotizacion que excede -> queda "por_aprobar" (un administrador la aprueba). Factura o POS que excede -> se rechaza.

Ademas del descuento hay otras dos razones para pedir aprobacion, que Andres pidio en la reunion del 22/09:
una cotizacion por encima de cierto monto y una con margen por debajo del minimo. Viven en approval_reasons().
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import Product
from ..schemas.sales import DocumentIn
from .totals import d

DEFAULT_MAX = Decimal(10)


def max_discount(tenant) -> Decimal:
    v = (tenant.settings or {}).get("max_discount_pct")
    return d(v) if v is not None else DEFAULT_MAX


@dataclass
class Check:
    exceeds: bool
    limit: Decimal
    worst: Decimal  # % efectivo mas alto encontrado
    line: str | None

    @property
    def message(self) -> str:
        where = f" en «{self.line}»" if self.line else ""
        return f"El descuento{where} es de {self.worst:.1f} % y tu límite es {self.limit:.0f} %."


def check(db: Session, p: Principal, payload: DocumentIn) -> Check:
    limit = max_discount(p.tenant)
    if p.can("sales", "descuento_libre"):
        return Check(False, limit, Decimal(0), None)
    rows = []  # (nombre, neto de linea, valor de lista)
    for ln in payload.lines:
        qty = d(ln.quantity)
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        if prod and prod.tenant_id != p.tenant.id:
            prod = None
        unit = d(ln.unit_price) if ln.unit_price is not None else (d(prod.price) if prod else Decimal(0))
        list_price = d(prod.price) if prod and prod.currency == payload.currency else unit
        base = qty * unit
        disc = base * d(ln.discount_value) / 100 if ln.discount_type == "percent" else min(d(ln.discount_value), base)
        rows.append((ln.name or (prod.name if prod else "línea"), base - disc, qty * list_price))
    net_total = sum((r[1] for r in rows), Decimal(0))
    if payload.discount_type == "percent":
        ratio = 1 - d(payload.discount_value) / 100
    else:
        ratio = (net_total - min(d(payload.discount_value), net_total)) / net_total if net_total else Decimal(1)
    worst, worst_line = Decimal(0), None
    for name, net, list_amt in rows:
        if list_amt <= 0:
            continue
        eff = (1 - (net * ratio) / list_amt) * 100
        if eff > worst:
            worst, worst_line = eff, name
    worst = worst.quantize(Decimal("0.1"))
    return Check(worst > limit, limit, worst, worst_line)


# ---------- Aprobacion de cotizaciones (monto y margen) ----------
DEFAULT_APPROVAL_AMOUNT = Decimal(2_000_000)  # "cotizacion mayor de 2 millones requiere aprobacion del CEO"
DEFAULT_MIN_MARGIN = Decimal(25)  # "margen menor a 25 % -> bloquear y solicitar autorizacion"


def approval_amount(tenant) -> Decimal:
    v = (tenant.settings or {}).get("quote_approval_amount")
    return d(v) if v is not None else DEFAULT_APPROVAL_AMOUNT


def min_margin(tenant) -> Decimal:
    v = (tenant.settings or {}).get("min_margin_pct")
    return d(v) if v is not None else DEFAULT_MIN_MARGIN


def quote_margin(db: Session, tenant_id: int, quote) -> Decimal | None:
    """Margen de la cotizacion sobre las lineas que SI tienen costo registrado.
    Devuelve None cuando ninguna linea tiene costo: ahi el margen no se puede afirmar y la regla no aplica."""
    from . import pricing

    neto = cost = Decimal(0)
    con_costo = False
    fx = None
    for ln in quote.lines:
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        if prod is None or prod.tenant_id != tenant_id or prod.cost is None:
            continue
        if fx is None:
            from .documents import today_fx

            fx = today_fx(db, "USD")[0]
        con_costo = True
        neto += d(ln.subtotal)
        cost += pricing.cost_in(db, prod, quote.currency, fx) * d(ln.quantity)
    if not con_costo or neto <= 0:
        return None
    return pricing.margin_of(neto, cost)


def approval_reasons(db: Session, p: Principal, quote) -> list[str]:
    """Por que esta cotizacion necesita el visto bueno de un administrador. Vacia = sale sola."""
    if p.can("sales", "aprobar"):
        return []  # quien aprueba no se pide permiso a si mismo
    razones = []
    tope = approval_amount(p.tenant)
    if tope > 0 and d(quote.total) > tope:
        razones.append(f"El monto ({quote.currency} {d(quote.total):,.0f}) supera el tope de {tope:,.0f} para cotizar sin aprobación.")
    minimo = min_margin(p.tenant)
    m = quote_margin(db, p.tenant.id, quote)
    if m is not None and minimo > 0 and m < minimo:
        razones.append(f"El margen queda en {m:.1f} % y el mínimo es {minimo:.0f} %.")
    return razones
