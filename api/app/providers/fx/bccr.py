"""Tipo de cambio del BCCR (indicadores 317 = compra, 318 = venta).

Desde la renovacion del sitio del BCCR (agosto 2026) el servicio SOAP gee.bccr.fi.cr responde 503; se usa la API
nueva del Sistema de Divulgacion de Datos Economicos (SDDE):

    GET https://apim.bccr.fi.cr/SDDE/api/Bccr.GE.SDDE.Publico.Indicadores.API/indicadoresEconomicos/{codigo}/series
        ?fechaInicio=aaaa/mm/dd&fechaFin=aaaa/mm/dd&idioma=ES
    Authorization: Bearer <token>

El token se genera gratis en el sitio de Indicadores Economicos del BCCR: registrarse -> Mi Perfil -> Generar token.
Se configura como variable de entorno BCCR_TOKEN (servicios api y worker). Sin token se conserva el ultimo valor manual.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import httpx

BASE = os.getenv("BCCR_API_URL", "https://apim.bccr.fi.cr/SDDE/api/Bccr.GE.SDDE.Publico.Indicadores.API")
SELL, BUY = "318", "317"


class BccrError(RuntimeError):
    pass


def _series(code: str, start: date, end: date, token: str, client: httpx.Client | None = None) -> list[tuple[date, Decimal]]:
    url = f"{BASE}/indicadoresEconomicos/{code}/series"
    params = {"fechaInicio": start.strftime("%Y/%m/%d"), "fechaFin": end.strftime("%Y/%m/%d"), "idioma": "ES"}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "CrimsonPlataforma/1.0"}
    r = (client or httpx).get(url, params=params, headers=headers, timeout=20)
    if r.status_code == 401:
        raise BccrError("El BCCR rechazó el token (401): revisá que BCCR_TOKEN esté completo y vigente")
    if r.status_code == 403:
        raise BccrError("El BCCR indica que la cuenta no tiene suscripción válida (403)")
    if r.status_code == 429:
        raise BccrError("El BCCR limitó las consultas (429); se reintenta más tarde")
    r.raise_for_status()
    body = r.json()
    if not body.get("estado", True):
        raise BccrError(f"BCCR: {body.get('mensaje') or 'consulta sin éxito'}")
    out: list[tuple[date, Decimal]] = []
    for ind in body.get("datos") or []:
        for s in ind.get("series") or []:
            v = s.get("valorDatoPorPeriodo")
            f = s.get("fecha")
            if v is None or not f:
                continue
            out.append((date.fromisoformat(str(f)[:10]), Decimal(str(v))))
    return sorted(out)


def latest(code: str, day: date, token: str, client: httpx.Client | None = None) -> tuple[date, Decimal]:
    """Ultimo valor publicado en los 10 dias previos (cubre fines de semana y feriados)."""
    rows = [r for r in _series(code, day - timedelta(days=10), day, token, client) if r[0] <= day]
    if not rows:
        raise BccrError(f"El BCCR no tiene datos del indicador {code} para {day.isoformat()}")
    return rows[-1]


def fetch_today(day: date | None = None, client: httpx.Client | None = None) -> tuple[Decimal, Decimal]:
    """(venta, compra) del dia. Lanza BccrError con un mensaje claro si falta el token o el BCCR rechaza."""
    day = day or date.today()
    token = (os.getenv("BCCR_TOKEN") or "").strip()
    if not token:
        raise BccrError("Falta BCCR_TOKEN; se mantiene el tipo de cambio manual")
    _, sell = latest(SELL, day, token, client)
    _, buy = latest(BUY, day, token, client)
    return sell, buy
