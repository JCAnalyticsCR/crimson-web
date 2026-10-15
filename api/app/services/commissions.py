"""Economia (libro unico de comisiones y pagos a aliados): CONTRATO que usa el portal de aliados.

El frente economia es el dueno del dinero que recibe un aliado (pago acordado, comision por regla, bonos, ajustes,
aprobacion y pago). Este archivo solo fija el contrato que consume el portal (/aliado/ganancias*, routers/ally_portal.py)
y por ahora no tiene movimientos: devuelve la forma completa en cero. El frente economia lo reemplaza por el calculo
real sin que el portal cambie.

Forma de ally_earnings:
    {"moneda": "CRC", "totales": {"recibido", "por_recibir", "proyectado", "devengado"},
     "por_mes": [{"mes": "YYYY-MM", "devengado", "recibido"}], "proyectos": [{"project_id", "number", "name", ...}]}
Montos sin IVA, en colones.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

ZERO = Decimal("0.00")


def _meses(desde: date | None, hasta: date | None) -> list[str]:
    if not desde or not hasta or desde > hasta:
        return []
    out, y, m = [], desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month) and len(out) < 60:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def ally_earnings(db: Session, tenant, ally_company_id: int, desde: date | None = None, hasta: date | None = None) -> dict:
    """Lo que recibe la empresa aliada en el periodo (por mes y por proyecto). Sin movimientos todavia: ceros."""
    return {
        "moneda": "CRC",
        "totales": {"recibido": ZERO, "por_recibir": ZERO, "proyectado": ZERO, "devengado": ZERO},
        "por_mes": [{"mes": k, "devengado": ZERO, "recibido": ZERO} for k in _meses(desde, hasta)],
        "proyectos": [],
    }


def ally_project_earnings(db: Session, tenant, ally_company_id: int, project_id: int) -> dict:
    """ "Lo que recibo en este proyecto": acuerdos, comisiones, bonos y pagos de ESA empresa en ESE proyecto."""
    return {
        "project_id": project_id,
        "moneda": "CRC",
        "totales": {"recibido": ZERO, "por_recibir": ZERO, "proyectado": ZERO, "devengado": ZERO},
        "movimientos": [],
    }
