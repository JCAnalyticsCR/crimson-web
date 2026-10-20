"""Hora y fecha de Costa Rica (UTC-6, sin horario de verano).

Los contenedores corren en UTC: despues de las 18:00 de Costa Rica, `date.today()` ya es el dia siguiente. Por eso el
cierre de caja de las 21:00 reportaba "mañana" y una factura vencia un dia antes. Todo lo que dependa del dia de
Costa Rica (cierres, vencimientos, fechas fiscales) sale de aqui.

`_utcnow` es el unico reloj: las pruebas lo fijan (p. ej. 03:00 UTC = 21:00 CR del dia anterior).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CR = ZoneInfo("America/Costa_Rica")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def cr_now() -> datetime:
    """Hora de Costa Rica sin microsegundos: la fecha de la clave y la FechaEmision salen de aqui (no de UTC: despues de
    las 18:00 la fecha UTC ya es la del dia siguiente)."""
    return _utcnow().astimezone(CR).replace(microsecond=0)


def cr_today() -> date:
    """El dia de hoy en Costa Rica."""
    return _utcnow().astimezone(CR).date()


def cr_day_bounds(d: date) -> tuple[datetime, datetime]:
    """[inicio, fin) del dia `d` de Costa Rica, en UTC: para filtrar columnas DateTime(timezone=True)."""
    start = datetime.combine(d, time.min, tzinfo=CR).astimezone(UTC)
    return start, start + timedelta(days=1)
