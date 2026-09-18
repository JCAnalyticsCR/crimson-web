"""Limite de descuento por rol.

Quien no tenga "sales.descuento_libre" (solo el administrador lo tiene) puede descontar como maximo el porcentaje
configurado en Ajustes -> Facturacion (max_discount_pct, 10 % por defecto). Cuenta como descuento:
- el descuento por linea (porcentaje o monto),
- el descuento global del documento (prorrateado),
- bajar el precio unitario por debajo del precio de catalogo (misma divisa).

Cotizacion que excede -> queda "por_aprobar" (un administrador la aprueba). Factura o POS que excede -> se rechaza.
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
