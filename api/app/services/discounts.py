"""Limite de descuento por rol.

Quien no tenga "sales.descuento_libre" (solo el administrador lo tiene) puede descontar como maximo el porcentaje
configurado en Ajustes -> Facturacion (max_discount_pct, 10 % por defecto). Cuenta como descuento:
- el descuento por linea (porcentaje o monto),
- el descuento global del documento (prorrateado),
- bajar el precio unitario por debajo del precio de catalogo en la moneda del documento (convertido con el tipo
  de cambio vigente cuando el producto esta en otra moneda).
- lineas de cortesia/aportado/excluido con precio de catalogo: se cuentan como 100 % de descuento porque el
  documento las muestra en 0 al cliente.

Cotizacion que excede -> queda "por_aprobar" (un administrador la aprueba). Factura o POS que excede -> se rechaza.

Ademas del descuento hay otras razones para pedir aprobacion (approval_reasons):
- cotizacion por encima de cierto monto (Andres, 22/09),
- margen por debajo del minimo (Andres, 22/09),
- lineas libres (sin producto del catalogo): todo vendedor que agregue una linea sin producto_id pide
  aprobacion cuando el subtotal supera el tope free_line_limit_amount (default 0 = siempre). Lineas de
  mano de obra o servicio marcadas con product_id siguen la regla normal de descuento.

Las facturas directas y el POS NO pueden quedar "por_aprobar": si approval_reasons devuelve algo, el endpoint
las rechaza con "Esta venta necesita aprobacion: hagala como cotizacion."
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import AuditLog, Product
from ..schemas.sales import DocumentIn
from .documents import audit
from .email_layout import cta
from .mail import notify_roles
from .totals import d

DEFAULT_MAX = Decimal(10)
# Treatments que hacen que la linea salga en 0 en el documento (100 % de descuento efectivo)
_NO_SUMA = frozenset(("aportado", "cortesia", "excluido"))


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
        return f"El descuento{where} es de {self.worst:.1f} % y su límite es {self.limit:.0f} %."


def _list_price_in_doc_currency(prod, doc_currency: str, fx_rate) -> Decimal:
    """Precio de catalogo del producto convertido a la moneda del documento.
    fx_rate: tasa USD->CRC (la unica conversion soportada; None si no hay tasa cargada)."""
    pc = (prod.currency or "CRC").upper()
    dc = (doc_currency or "CRC").upper()
    price = d(prod.price)
    if pc == dc:
        return price
    if pc == "USD" and dc == "CRC" and fx_rate:
        return price * d(fx_rate)
    if pc == "CRC" and dc == "USD" and fx_rate and d(fx_rate) > 0:
        return price / d(fx_rate)
    # conversion no disponible: sin referencia de precio, no se puede medir el descuento
    return Decimal(-1)  # sentinel: caller lo detecta y omite la linea del calculo


def check(db: Session, p: Principal, payload: DocumentIn) -> Check:
    limit = max_discount(p.tenant)
    if p.can("sales", "descuento_libre"):
        return Check(False, limit, Decimal(0), None)

    from .fx import rate_or_none

    _fx = None  # cargado solo si hay productos en moneda distinta (evita consulta innecesaria)
    rows = []  # (nombre, neto de linea, valor de lista en moneda doc)
    for ln in payload.lines:
        qty = d(ln.quantity)
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        if prod and prod.tenant_id != p.tenant.id:
            prod = None
        unit = d(ln.unit_price) if ln.unit_price is not None else (d(prod.price) if prod else Decimal(0))

        # Precio de catalogo en la moneda del documento
        if prod is None:
            list_price = unit  # linea libre: sin catalogo, el check no puede medir descuento
        elif prod.currency == payload.currency:
            list_price = d(prod.price)
        else:
            if _fx is None:
                _fx = rate_or_none(db, "USD")
            list_price = _list_price_in_doc_currency(prod, payload.currency, _fx)
            if list_price < 0:
                list_price = unit  # tipo de cambio no disponible: fallback sin bloquear

        # Cortesia/aportado/excluido: el documento los muestra en 0 -> 100 % de descuento efectivo
        trt = (ln.treatment or "normal")
        if trt in _NO_SUMA:
            if list_price > 0:
                rows.append((ln.name or (prod.name if prod else "línea"), Decimal(0), qty * list_price))
            continue

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


# ---------- Aprobacion de cotizaciones (monto, margen y lineas libres) ----------
DEFAULT_APPROVAL_AMOUNT = Decimal(2_000_000)  # "cotizacion mayor de 2 millones requiere aprobacion del CEO"
DEFAULT_MIN_MARGIN = Decimal(25)  # "margen menor a 25 % -> bloquear y solicitar autorizacion"
DEFAULT_FREE_LINE_LIMIT: Decimal | None = None  # None = sin limite (desactivado hasta que el admin lo configure)


def approval_amount(tenant) -> Decimal:
    v = (tenant.settings or {}).get("quote_approval_amount")
    return d(v) if v is not None else DEFAULT_APPROVAL_AMOUNT


def min_margin(tenant) -> Decimal:
    v = (tenant.settings or {}).get("min_margin_pct")
    return d(v) if v is not None else DEFAULT_MIN_MARGIN


def free_line_limit(tenant) -> Decimal | None:
    """Subtotal maximo que una linea sin producto del catalogo puede tener sin pedir aprobacion.
    None = sin limite (por defecto). El administrador lo activa poniendo free_line_limit_amount
    en los ajustes del tenant (ej. 50 000 = toda linea libre de mas de 50 000 CRC pide aprobacion)."""
    v = (tenant.settings or {}).get("free_line_limit_amount")
    if v is None:
        return DEFAULT_FREE_LINE_LIMIT  # None = desactivado
    return d(v)


def _cost_lines(db: Session, tenant_id: int, quote):
    """(linea, producto) de las lineas cuyo producto tiene costo registrado: las unicas que entran en el margen."""
    for ln in quote.lines:
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        if prod is None or prod.tenant_id != tenant_id or prod.cost is None:
            continue
        yield ln, prod


def quote_margin(db: Session, tenant_id: int, quote) -> Decimal | None:
    """Margen de la cotizacion sobre las lineas que SI tienen costo registrado.
    Devuelve None cuando ninguna linea tiene costo: ahi el margen no se puede afirmar y la regla no aplica."""
    from . import pricing

    neto = cost = Decimal(0)
    con_costo = False
    from .fx import rate_or_none

    fx = None
    for ln, prod in _cost_lines(db, tenant_id, quote):
        if fx is None:
            fx = rate_or_none(db, "USD")  # None: solo un costo en otra moneda la consulta (sin tasa, 422 claro)
        con_costo = True
        neto += d(ln.subtotal)
        cost += pricing.cost_in(db, prod, quote.currency, fx) * d(ln.quantity)
    if not con_costo or neto <= 0:
        return None
    return pricing.margin_of(neto, cost)


def approval_reasons(db: Session, p: Principal, quote) -> list[str]:
    """Por que este documento necesita el visto bueno de un administrador. Vacia = sale solo.
    Funciona tanto con cotizaciones (ORM Quote) como con facturas recien creadas (ORM Invoice):
    ambas tienen .total, .currency y .lines con los mismos atributos necesarios."""
    if p.can("sales", "aprobar"):
        return []  # quien aprueba no se pide permiso a si mismo
    razones = []
    tope = approval_amount(p.tenant)
    if tope > 0 and d(quote.total) > tope:
        razones.append(f"El monto ({quote.currency} {d(quote.total):,.0f}) supera el tope de {tope:,.0f} para vender sin aprobación.")
    minimo = min_margin(p.tenant)
    m = quote_margin(db, p.tenant.id, quote)
    if m is not None and minimo > 0 and m < minimo:
        # el margen exacto deja deducir el costo: quien no ve costos (el vendedor) solo sabe que quedo por debajo
        razones.append(
            f"El margen queda en {m:.1f} % y el mínimo es {minimo:.0f} %." if p.sees_costs else f"El margen queda por debajo del mínimo ({minimo:.0f} %)."
        )
    # Lineas libres (sin producto del catalogo): solo si el admin configuro un tope (free_line_limit_amount).
    tope_libre = free_line_limit(p.tenant)
    if tope_libre is not None:
        for ln in quote.lines:
            pid = getattr(ln, "product_id", None)
            trt = (getattr(ln, "treatment", None) or "normal")
            if pid is not None or trt != "normal":
                continue  # tiene producto (cubierto por check) o es cortesia/aportado/excluido
            subtotal = d(getattr(ln, "subtotal", None) or 0)
            if subtotal <= 0:
                subtotal = d(getattr(ln, "unit_price", 0)) * d(getattr(ln, "quantity", 1))
            if subtotal > tope_libre:
                nombre = (getattr(ln, "name", None) or "sin nombre")[:60]
                razones.append(f"La línea «{nombre}» no tiene producto del catálogo: necesita revisión del administrador.")
                break  # una sola razon es suficiente para retener
    return razones


# Motivos con el margen (y el minimo) en numeros: los guardados antes de la matriz de roles (13/10) traen el margen
# exacto, y los de ahora el minimo. Precio x (1 - margen) = costo: quien no ve costos lee el motivo sin cifras.
MARGIN_REASON = re.compile(r"El margen queda (?:en -?[\d.,]+ % y el mínimo es [\d.,]+ %|por debajo del mínimo \([\d.,]+ %\))\.")


def margin_sentence(text: str | None) -> str | None:
    """La frase del margen dentro de un motivo guardado (None si la retencion no fue por margen)."""
    m = MARGIN_REASON.search(text or "")
    return m.group(0) if m else None


def public_reason(text: str | None, sees_costs: bool) -> str | None:
    """approval_reason para mostrar: completo a quien ve costos; sin numeros de margen al resto (el vendedor).
    El texto guardado no cambia. El tope de monto ("supera el tope") se sigue viendo completo. Con el freno de sondeo
    puesto (PROBE_REASON) ni siquiera se dice si tambien fue el margen: todas se ven iguales."""
    if not text or sees_costs:
        return text
    if PROBE_REASON in text or PROBE_REASON_OLD in text:
        return " ".join(MARGIN_REASON.sub("", text).split())
    return MARGIN_REASON.sub("El margen queda por debajo del mínimo de la empresa.", text)


# ---------- Margen aprobado ----------
# Cuando un administrador aprueba una cotizacion con el margen por debajo del minimo (o la guarda el mismo), esa
# aprobacion sigue en pie mientras ninguna linea con costo baje de su precio aprobado, en la misma divisa. Antes,
# volver a guardarla (p. ej. «Guardar & enviar al cliente», que guarda antes de enviar) la retenia otra vez por el mismo
# margen que ya se habia aprobado, y en quien no ve costos esa retencion contaba para el freno de abajo: unas cuantas
# aprobadas y enviadas en un dia terminaban en el aviso de "probando precios" y en todo lo suyo retenido.
# Se compara precio contra precio, nunca margen: una holgura en puntos de margen dejaria sacar el costo (lo que hay que
# bajar el precio para perder un punto depende del costo). Vive en la bitacora (solo la ve administracion).
APPROVAL_ACTIONS = ("approve_quote", "approve_margin")
_PRICE_TOL = Decimal("0.0001")


def _unit_prices(db: Session, tenant_id: int, quote) -> dict[str, Decimal]:
    """Por producto con costo, el menor precio neto unitario de la cotizacion (con los descuentos de linea y global)."""
    out: dict[str, Decimal] = {}
    for ln, prod in _cost_lines(db, tenant_id, quote):
        qty = d(ln.quantity)
        if qty <= 0:
            continue
        neto = d(ln.subtotal) / qty
        k = str(prod.id)
        if k not in out or neto < out[k]:
            out[k] = neto
    return out


def margin_approval(db: Session, tenant, quote) -> dict | None:
    """Lo que queda aprobado al aprobar (o al guardar quien aprueba) una cotizacion con el margen bajo el minimo: la
    divisa y el precio neto unitario de cada producto con costo. None si el margen no esta bajo el minimo."""
    minimo = min_margin(tenant)
    m = quote_margin(db, tenant.id, quote)
    if m is None or minimo <= 0 or m >= minimo:
        return None
    return {"moneda": quote.currency, "precios": {k: str(v) for k, v in _unit_prices(db, tenant.id, quote).items()}}


def margin_covered(db: Session, tenant_id: int, quote) -> bool:
    """Si la ultima aprobacion del margen de esta cotizacion cubre sus precios actuales (ninguno mas bajo, misma divisa)."""
    if quote.id is None:
        return False
    rows = db.scalars(
        select(AuditLog.diff)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.entity == "quote", AuditLog.entity_id == quote.id, AuditLog.action.in_(APPROVAL_ACTIONS))
        .order_by(AuditLog.id.desc())
    )
    ok = next((r["margen_aprobado"] for r in rows if isinstance(r, dict) and r.get("margen_aprobado")), None)
    if ok is None or ok.get("moneda") != quote.currency:
        return False
    aprobados = ok.get("precios") or {}
    for k, precio in _unit_prices(db, tenant_id, quote).items():
        if k not in aprobados or precio < d(aprobados[k]) - _PRICE_TOL:
            return False
    return True


# ---------- Freno al sondeo del margen ----------
# Retenida o no, el estado de la cotizacion es una senal: quien no ve costos podria ir probando precios hasta dar con
# el que dispara el margen minimo y de ahi sacar el costo (R5/D3: "ni deducible"). La retencion por margen no se suelta
# subiendo el precio (routers/sales._hold) y el minimo no se le dice (/sales/discount-limit). Ademas cuenta cada
# evaluacion del margen que dice algo del costo: la que retiene por margen Y la que no retiene pero queda cerca del
# minimo (a menos de MARGIN_PROBE_BAND puntos). Antes solo contaban las retenciones y los guardados que no retenian eran
# gratis: bajando el precio de a poco (por PUT, con cotizaciones nuevas, o en otra divisa para que no cuente como
# descuento) la primera retencion daba el precio umbral, y con dos o tres pasadas mas finas el costo.
# Cuenta margenes DISTINTOS: repetir la misma cotizacion (o guardarla sin cambiar precios) no dice nada nuevo y no
# cuenta, asi el trabajo normal no dispara el aviso; lejos del minimo tampoco cuenta (un descuento comun).
# Pasadas MARGIN_PROBE_LIMIT en MARGIN_PROBE_HOURS, toda cotizacion suya queda retenida con un motivo que no dice si fue
# el margen (la senal se satura) y administracion recibe un aviso. Cuenta desde la bitacora (acciones "margin_hold" y
# "margin_probe", con el margen evaluado): sobrevive a reinicios y a varias replicas. Con paciencia (unas pocas por dia)
# se puede seguir afinando: el freno lo vuelve lento y lo deja a la vista de administracion en la bitacora.
MARGIN_PROBE_LIMIT = 5
MARGIN_PROBE_HOURS = 24
MARGIN_PROBE_BAND = Decimal(5)  # puntos de margen sobre el minimo
PROBE_ACTIONS = ("margin_hold", "margin_probe")
PROBE_REASON = "Por un tiempo, cada cotización suya pasa por la aprobación de administración antes de enviarse."
# el mismo motivo como se guardaba antes de que los correos trataran de usted (cotizaciones ya retenidas)
PROBE_REASON_OLD = "Por un tiempo, cada cotización tuya pasa por la aprobación de administración antes de enviarse."


def near_min(tenant, margin: Decimal | None) -> bool:
    """El margen evaluado dice algo del costo: por debajo del minimo o a menos de MARGIN_PROBE_BAND puntos de el."""
    minimo = min_margin(tenant)
    return margin is not None and minimo > 0 and margin < minimo + MARGIN_PROBE_BAND


def margin_probe(db: Session, p: Principal, quote, margin: Decimal | None, held: bool) -> bool:
    """Registra una evaluacion del margen de quien no ve costos y dice si su freno esta puesto.
    margin: el evaluado, o None si esta evaluacion no dice nada del costo (no cuenta). held: quedo retenida por margen."""
    desde = datetime.now(UTC) - timedelta(hours=MARGIN_PROBE_HOURS)
    vistos = db.scalars(
        select(AuditLog.diff).where(AuditLog.tenant_id == p.tenant.id, AuditLog.user_id == p.user.id, AuditLog.action.in_(PROBE_ACTIONS), AuditLog.at >= desde)
    ).all()
    n = len(vistos)
    if margin is not None and str(margin) not in {(v or {}).get("m") for v in vistos}:
        audit(db, p.tenant.id, p.user.id, "margin_hold" if held else "margin_probe", "quote", quote.id, {"m": str(margin)}, ip=p.ip)
        n += 1
        if n == MARGIN_PROBE_LIMIT:
            quien = html.escape(p.user.full_name or p.user.email or "Un vendedor")
            cuerpo = (
                f"<p>{quien} guardó en las últimas {MARGIN_PROBE_HOURS} horas {MARGIN_PROBE_LIMIT} cotizaciones con márgenes distintos "
                f"por debajo del mínimo o muy cerca de él (la última, <b>{html.escape(quote.number)}</b>).</p>"
                "<p>Puede ser trabajo normal con productos de margen bajo o alguien probando precios para deducir el costo "
                "(el detalle está en la bitácora). Mientras tanto, todas sus cotizaciones quedan esperando su aprobación, "
                "sin decirle si fue por el margen.</p>"
                + cta("/aprobaciones", "Abrir Aprobaciones")
            )
            notify_roles(db, p.tenant, ("admin",), f"Cotizaciones cerca del margen mínimo · {p.user.full_name}", cuerpo, "quote", quote.id)
    return n >= MARGIN_PROBE_LIMIT
