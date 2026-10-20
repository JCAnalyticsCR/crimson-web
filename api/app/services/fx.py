"""Tipo de cambio: UNA sola funcion para todo el sistema (plan 1.12).

    fx(db, moneda, fecha=None) -> Tasa(tasa, fecha, fuente, vieja)

- CRC: siempre 1 (fuente "base"); no consulta la base.
- Otra moneda: la tasa de venta (BCCR o manual) de esa fecha o la ultima anterior. Sin fecha, la mas reciente cargada.
  Si no hay ninguna anterior a la fecha pedida (documento mas viejo que la primera tasa) se usa la mas cercana
  posterior, marcada vieja.
- NUNCA devuelve 1 para una moneda distinta de CRC: sin ninguna tasa cargada lanza FxMissing. El API la convierte en
  un 422 claro (main.py); la tienda la atrapa y pasa a "Consultar" solo los productos que la necesitan (un tipo de
  cambio faltante no tumba el checkout de lo que esta en colones).
- vieja = la tasa tiene mas de 2 dias habiles (lunes a viernes) respecto de la fecha de referencia (hoy en Costa Rica
  si no se pide fecha). No conoce los feriados: un lunes despues de un feriado puede marcarse vieja un dia antes.

Antes cada modulo tenia su copia (documents.today_fx, pricing.convert, store.Pricer, los `fx or 1` de reportes,
recepcion y gastos) y todas caian a 1 sin aviso: un producto en dolares salia a 50 colones.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ExchangeRate

CR = ZoneInfo("America/Costa_Rica")
STALE_BUSINESS_DAYS = 2
SOURCES = {"bccr": "BCCR", "manual": "Manual", "base": "Base"}


class FxMissing(LookupError):
    """No hay ninguna tasa cargada para esa moneda. Nunca se reemplaza por 1."""

    def __init__(self, currency: str):
        self.currency = (currency or "").upper()
        super().__init__(
            f"No hay tipo de cambio de {self.currency} cargado: cárguelo en Ajustes → Facturación → Tipo de cambio "
            "(o actualícelo desde el BCCR) y vuelva a intentar."
        )

    @property
    def detail(self) -> str:
        return str(self)


class Tasa(NamedTuple):
    tasa: Decimal  # colones por unidad de la moneda (venta salvo que se pida la compra)
    fecha: date | None  # fecha de la tasa usada (None en CRC)
    fuente: str  # bccr | manual | base
    vieja: bool  # mas de 2 dias habiles de antiguedad (o tomada de una fecha posterior)


def cr_today() -> date:
    return datetime.now(CR).date()


def business_days_between(a: date, b: date) -> int:
    """Dias habiles (lunes a viernes) despues de `a` hasta `b` inclusive. 0 si b <= a."""
    if b <= a:
        return 0
    n, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v if v is not None else 0))


def _row(db: Session, currency: str, on: date | None) -> tuple[ExchangeRate | None, bool]:
    """(fila, posterior). posterior=True: no habia ninguna en o antes de `on` y se tomo la mas cercana despues."""
    base = select(ExchangeRate).where(ExchangeRate.currency == currency)
    if on is None:
        return db.scalar(base.order_by(ExchangeRate.date.desc(), ExchangeRate.id.desc())), False
    row = db.scalar(base.where(ExchangeRate.date <= on).order_by(ExchangeRate.date.desc(), ExchangeRate.id.desc()))
    if row is not None:
        return row, False
    return db.scalar(base.where(ExchangeRate.date > on).order_by(ExchangeRate.date, ExchangeRate.id)), True


def fx(db: Session, currency: str | None, on: date | None = None, *, side: str = "sell") -> Tasa:
    """La tasa de esa moneda (ver el docstring del modulo). side="buy" devuelve la de compra."""
    cur = (currency or "CRC").upper()
    if cur == "CRC":
        return Tasa(Decimal(1), None, "base", False)
    row, later = _row(db, cur, on)
    value = _dec(getattr(row, side, None)) if row is not None else Decimal(0)
    if row is None or value <= 0:
        raise FxMissing(cur)
    ref = on or cr_today()
    stale = later or business_days_between(row.date, ref) > STALE_BUSINESS_DAYS
    return Tasa(value, row.date, row.source or "manual", stale)


def rate_or_none(db: Session, currency: str | None) -> Decimal | None:
    """Venta del dia o None si no hay ninguna. Para pasarla a funciones que consultan solas (fx=None) solo cuando
    de verdad hay que convertir: una lista entera no falla porque falte el dolar si nada de ella esta en dolares."""
    try:
        return fx(db, currency).tasa
    except FxMissing:
        return None


def doc_rate(db: Session, currency: str | None, stored, on: date | None) -> Decimal:
    """Tasa de un documento ya guardado (factura, gasto, comprobante recibido): la suya si es valida; si falta o quedo
    en 1 (lo que dejaba el sistema viejo cuando no habia tasa), la del BCCR de la fecha del documento.
    Lanza FxMissing si no hay ninguna tasa cargada: un reporte en colones nunca suma dolares como colones."""
    cur = (currency or "CRC").upper()
    if cur == "CRC":
        return Decimal(1)
    r = _dec(stored)
    if r > 1:
        return r
    return fx(db, cur, on).tasa


def for_date(db: Session, currency: str | None, on: date | None) -> tuple[Decimal, str | None]:
    """(tasa, aviso) para REGISTRAR un gasto o un movimiento de esa fecha: la de esa fecha o la ultima anterior; si no
    hay ninguna anterior, la mas cercana posterior con un aviso para corregirla. Nunca 1 para otra moneda (antes se
    guardaba 1 "con aviso"): sin ninguna tasa cargada lanza FxMissing (422 claro)."""
    t = fx(db, currency, on)
    if t.fecha is not None and on is not None and t.fecha > on:
        return t.tasa, (
            f"Sin tipo de cambio del BCCR para esa fecha: se usó el del {t.fecha.strftime('%d/%m/%Y')} "
            f"({t.tasa.normalize():f}); corríjalo a mano si hace falta"
        )
    return t.tasa, None


def info(db: Session, currency: str = "USD") -> dict | None:
    """Lo que muestran el editor de documentos y Ajustes: tasa del dia con su fecha, fuente y si esta vieja."""
    cur = (currency or "USD").upper()
    try:
        venta = fx(db, cur)
        compra = fx(db, cur, side="buy")
    except FxMissing:
        return None
    return {
        "currency": cur,
        "date": venta.fecha,
        "sell": venta.tasa,
        "buy": compra.tasa,
        "source": venta.fuente,
        "source_label": SOURCES.get(venta.fuente, venta.fuente),
        "stale": venta.vieja,
        "business_days_old": business_days_between(venta.fecha, cr_today()) if venta.fecha else 0,
    }
