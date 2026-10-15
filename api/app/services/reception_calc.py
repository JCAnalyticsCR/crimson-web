"""Calculo puro del mensaje receptor: IVA acreditable, gasto aplicable, montos que van en el XML y plazo.

D11: el credito es solo IVA (impuestos 01, 07 y 08 del desglose), no el TotalImpuesto completo. El gasto aplicable es
la parte del comprobante que va a gasto (IVA no acreditable, otros impuestos y otros cargos incluidos).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

Q5 = Decimal("0.00001")
IVA_CODES = {"credito": "01", "credito_parcial": "02", "bienes_capital": "03", "no_credito": "04", "proporcional": "05"}
IVA_NAMES = {v: k for k, v in IVA_CODES.items()}
MESSAGE_CODE = {"aceptada": "1", "parcial": "2", "rechazada": "3"}
ACTION_OF_CODE = {v: k for k, v in MESSAGE_CODE.items()}
FISCAL_CODE = {"aceptada": "05", "parcial": "06", "rechazada": "07"}


class CalcError(ValueError):
    """Montos invalidos para la condicion elegida (el router lo devuelve como 422)."""


def q5(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Q5, rounding=ROUND_HALF_UP)


def name_of(cond: str) -> str:
    """Acepta el nombre o el codigo (01-05) y devuelve el nombre que se guarda."""
    if cond in IVA_CODES:
        return cond
    if cond in IVA_NAMES:
        return IVA_NAMES[cond]
    raise CalcError("Condición del IVA desconocida")


def code_of(cond: str) -> str:
    return IVA_CODES[name_of(cond)]


@dataclass
class Calc:
    credit: Decimal | None
    applicable: Decimal | None
    base_iva: Decimal | None
    base_total: Decimal | None
    xml_amounts: bool


def xml_amounts(action: str, cond: str | None, tax_total: Decimal, iva_total: Decimal | None) -> bool:
    """Si el mensaje lleva MontoTotalImpuestoAcreditar y MontoTotalDeGastoAplicable (tabla de spec 6.1.2)."""
    if action == "rechazada" or cond is None:
        return False
    code = code_of(cond)
    v = iva_total if iva_total is not None else tax_total
    if action == "parcial":
        return code != "05"
    return code in ("02", "03", "04") or (code == "01" and q5(v) != q5(tax_total))


def compute(
    action: str,
    cond: str,
    tax_total: Decimal,
    iva_total: Decimal | None,
    total: Decimal,
    accepted_total: Decimal | None = None,
    accepted_tax: Decimal | None = None,
    credit_given: Decimal | None = None,
) -> Calc:
    tax_total, total = Decimal(str(tax_total or 0)), Decimal(str(total or 0))
    v = Decimal(str(iva_total)) if iva_total is not None else tax_total
    if action == "rechazada":
        return Calc(None, None, None, None, False)
    code = code_of(cond)
    if action == "parcial":
        if accepted_total is None or accepted_tax is None:
            raise CalcError("Aceptación parcial: indique el monto aceptado y el IVA aceptado")
        accepted_total, accepted_tax = Decimal(str(accepted_total)), Decimal(str(accepted_tax))
        if not (0 < accepted_total < total):
            raise CalcError("El monto aceptado debe ser mayor que 0 y menor que el total del comprobante")
        if not (0 <= accepted_tax <= v):
            raise CalcError("El IVA aceptado debe estar entre 0 y el IVA del comprobante")
        if not accepted_tax < accepted_total:
            raise CalcError("El IVA aceptado debe ser menor que el monto aceptado")
        base_iva, base_total = min(accepted_tax, v), accepted_total
    else:
        base_iva, base_total = v, total
    if code == "01":
        credit: Decimal | None = base_iva
    elif code == "02":
        if credit_given is None or not (0 < Decimal(str(credit_given)) < base_iva):
            raise CalcError("Crédito parcial: el IVA a acreditar debe ser mayor que 0 y menor que el IVA aceptado")
        credit = Decimal(str(credit_given))
    elif code == "03":
        credit = Decimal(str(credit_given)) if credit_given is not None else base_iva
        if not (0 <= credit <= base_iva):
            raise CalcError("Bienes de capital: el IVA a acreditar debe estar entre 0 y el IVA aceptado")
    elif code == "04":
        credit = Decimal(0)
    else:  # 05: la prorrata se aplica en el reporte de IVA
        credit = None
    applicable = None if code == "05" else q5(base_total - (credit or 0))
    return Calc(
        credit=q5(credit) if credit is not None else None,
        applicable=applicable,
        base_iva=q5(base_iva),
        base_total=q5(base_total),
        xml_amounts=xml_amounts(action, cond, tax_total, v),
    )


def deadline(issue: date | None) -> date | None:
    """Res. MH-DGT-RES-0027-2024 art. 10: 8 dias habiles contados a partir del primer dia del mes siguiente (el dia 1
    cuenta si es habil). Solo lunes a viernes, sin feriados: es conservador (nunca posterior a la real)."""
    if not issue:
        return None
    d = (issue.replace(day=28) + timedelta(days=4)).replace(day=1)
    n = 0
    while True:
        if d.weekday() < 5:
            n += 1
            if n == 8:
                return d
        d += timedelta(days=1)


def business_days_left(today: date, due: date | None) -> int | None:
    """Dias habiles desde hoy hasta la fecha limite (0 = vence hoy; negativo = vencido)."""
    if not due:
        return None
    if due >= today:
        n, d = 0, today
        while d < due:
            d += timedelta(days=1)
            if d.weekday() < 5:
                n += 1
        return n
    n, d = 0, due
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return n
