"""Calculo de planilla (Costa Rica). Todas las tasas son configurables por empresa en Ajustes -> Planilla.

Valores por defecto (verificar cada enero con CCSS y el decreto de Hacienda del periodo fiscal):
- CCSS trabajador 10.83 % (SEM 5.50 + IVM 4.33 + Banco Popular 1.00), patrono 26.83 % (el seguro de riesgos del trabajo del INS se paga aparte).
- Impuesto al salario mensual por tramos (base 2025, Hacienda): exento hasta 922 000; 10 % hasta 1 352 000;
  15 % hasta 2 373 000; 20 % hasta 4 745 000; 25 % sobre el exceso. Creditos: 1 710 por hijo, 2 590 conyuge.
- Provisiones: aguinaldo 8.33 % y vacaciones 4.17 % del bruto (costo patronal, no se rebajan al trabajador).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

DEFAULTS: dict = {
    "ccss_worker": 10.83,
    "ccss_employer": 26.83,
    "brackets": [[922000, 0], [1352000, 10], [2373000, 15], [4745000, 20], [None, 25]],
    "child_credit": 1710,
    "spouse_credit": 2590,
    "aguinaldo": 8.33,
    "vacaciones": 4.17,
    "year": 2025,
}

C = Decimal("0.01")


def q(v: Decimal) -> Decimal:
    return v.quantize(C, rounding=ROUND_HALF_UP)


def rates_for(tenant_settings: dict | None) -> dict:
    return {**DEFAULTS, **((tenant_settings or {}).get("payroll") or {})}


def monthly_income_tax(monthly_gross: Decimal, rates: dict, children: int = 0, spouse: bool = False) -> Decimal:
    tax = Decimal(0)
    lower = Decimal(0)
    for limit, pct in rates["brackets"]:
        upper = Decimal(str(limit)) if limit is not None else None
        if monthly_gross <= lower:
            break
        top = monthly_gross if upper is None else min(monthly_gross, upper)
        tax += (top - lower) * Decimal(str(pct)) / 100
        if upper is None:
            break
        lower = upper
    if tax > 0:
        tax -= Decimal(str(rates["child_credit"])) * children + (Decimal(str(rates["spouse_credit"])) if spouse else 0)
    return q(max(tax, Decimal(0)))


def compute_line(
    salary_monthly: Decimal, frequency: str, rates: dict, overtime=Decimal(0), bonus=Decimal(0), other=Decimal(0), children=0, spouse=False
) -> dict:
    """Una linea de planilla para el periodo (mensual o quincenal). El impuesto se calcula sobre el equivalente
    mensual y se prorratea, que es como lo retiene Hacienda para pagos quincenales."""
    periods = 2 if frequency == "quincenal" else 1
    base = q(salary_monthly / periods)
    gross = q(base + overtime + bonus)
    ccss_w = q(gross * Decimal(str(rates["ccss_worker"])) / 100)
    tax = q(monthly_income_tax(gross * periods, rates, children, spouse) / periods)
    net = q(gross - ccss_w - tax - other)
    ccss_e = q(gross * Decimal(str(rates["ccss_employer"])) / 100)
    prov = q(gross * (Decimal(str(rates["aguinaldo"])) + Decimal(str(rates["vacaciones"]))) / 100)
    return {
        "base": base,
        "overtime": q(overtime),
        "bonus": q(bonus),
        "other_deductions": q(other),
        "gross": gross,
        "ccss_worker": ccss_w,
        "income_tax": tax,
        "net": net,
        "ccss_employer": ccss_e,
        "provisions": prov,
    }
