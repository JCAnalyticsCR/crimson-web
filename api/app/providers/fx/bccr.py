"""Tipo de cambio del BCCR (indicadores 317 = compra, 318 = venta).

Servicio oficial: https://gee.bccr.fi.cr/Indicadores/Suscripciones/WS/wsindicadoreseconomicos.asmx
Requiere correo + token de suscripcion (gratuitos). Sin credenciales, devuelve el ultimo valor manual.
"""

from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal

import httpx

WS = "https://gee.bccr.fi.cr/Indicadores/Suscripciones/WS/wsindicadoreseconomicos.asmx/ObtenerIndicadoresEconomicos"


def _indicator(code: str, day: date, email: str, token: str) -> Decimal:
    d = day.strftime("%d/%m/%Y")
    params = {"Indicador": code, "FechaInicio": d, "FechaFinal": d, "Nombre": "crimson", "SubNiveles": "N", "CorreoElectronico": email, "Token": token}
    r = httpx.get(WS, params=params, timeout=20)
    r.raise_for_status()
    m = re.search(r"<NUM_VALOR>([\d.]+)</NUM_VALOR>", r.text)
    if not m:
        raise RuntimeError(f"BCCR sin valor para {code} {d}")
    return Decimal(m.group(1))


def fetch_today(day: date | None = None) -> tuple[Decimal, Decimal]:
    day = day or date.today()
    email, token = os.getenv("BCCR_EMAIL"), os.getenv("BCCR_TOKEN")
    if not email or not token:
        raise RuntimeError("Faltan BCCR_EMAIL / BCCR_TOKEN; se mantiene el tipo de cambio manual")
    return _indicator("318", day, email, token), _indicator("317", day, email, token)
