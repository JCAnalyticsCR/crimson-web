"""Motor de totales (puro, sin I/O). Reglas v4.4: 5 decimales internos, redondeo HALF_UP.

Linea:   base = cantidad x precio
         descuento = base x % | monto
         subtotal = base - descuento
         impuesto = subtotal x tarifa
         total = subtotal + impuesto
Documento: descuento global (% o monto) se prorratea sobre el subtotal de lineas y reduce
         proporcionalmente la base imponible de cada tarifa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

Q5 = Decimal("0.00001")
Q2 = Decimal("0.01")


def d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


def q5(x: Decimal) -> Decimal:
    return x.quantize(Q5, rounding=ROUND_HALF_UP)


def q2(x: Decimal) -> Decimal:
    return x.quantize(Q2, rounding=ROUND_HALF_UP)


@dataclass
class LineIn:
    quantity: Decimal
    unit_price: Decimal
    discount_type: str = "percent"  # percent | amount
    discount_value: Decimal = Decimal(0)
    tax_rate: Decimal = Decimal(13)


@dataclass
class LineOut:
    base: Decimal
    discount: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal


@dataclass
class DocOut:
    lines: list[LineOut] = field(default_factory=list)
    subtotal: Decimal = Decimal(0)  # suma de subtotales de linea (ya con descuento de linea)
    discount_total: Decimal = Decimal(0)  # descuento global
    tax_total: Decimal = Decimal(0)
    total: Decimal = Decimal(0)
    taxable_by_rate: dict[str, Decimal] = field(default_factory=dict)


def _discount(base: Decimal, kind: str, value: Decimal) -> Decimal:
    value = d(value)
    if value <= 0:
        return Decimal(0)
    disc = base * value / Decimal(100) if kind == "percent" else value
    return min(q5(disc), base)


def compute_line(line: LineIn, global_factor: Decimal = Decimal(1)) -> LineOut:
    base = q5(d(line.quantity) * d(line.unit_price))
    disc = _discount(base, line.discount_type, line.discount_value)
    subtotal = q5((base - disc) * global_factor)
    tax = q5(subtotal * d(line.tax_rate) / Decimal(100))
    return LineOut(base=base, discount=disc, subtotal=subtotal, tax_amount=tax, total=q5(subtotal + tax))


def compute_document(lines: list[LineIn], discount_type: str = "percent", discount_value: Decimal | float | int = 0) -> DocOut:
    if not lines:
        return DocOut()
    first = [compute_line(ln) for ln in lines]
    gross = sum((lo.subtotal for lo in first), Decimal(0))
    global_disc = _discount(gross, discount_type, d(discount_value)) if gross > 0 else Decimal(0)
    factor = (gross - global_disc) / gross if gross > 0 else Decimal(1)
    out = DocOut(discount_total=global_disc)
    for ln in lines:
        lo = compute_line(ln, factor)
        out.lines.append(lo)
        out.tax_total += lo.tax_amount
        key = str(d(ln.tax_rate).normalize())
        out.taxable_by_rate[key] = out.taxable_by_rate.get(key, Decimal(0)) + lo.subtotal
    out.subtotal = q5(gross)
    out.tax_total = q5(out.tax_total)
    out.total = q5(out.subtotal - out.discount_total + out.tax_total)
    return out


def balance(total: Decimal | float, payments: list[tuple[str, Decimal | float]]) -> Decimal:
    """Saldo = total - capturas + devoluciones/reembolsos. Autorizaciones y voids no afectan."""
    paid = Decimal(0)
    for kind, amount in payments:
        a = d(amount)
        if kind == "captura":
            paid += a
        elif kind in ("devolucion", "reembolso"):
            paid -= a
    return q5(d(total) - paid)
