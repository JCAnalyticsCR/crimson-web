"""Guardian de margen (plan 1.12): la tienda nunca vende bajo el margen minimo sin que alguien lo decida.

Corre despues de cada refresco de Eurocomp (existencias y catalogo semanal), de cada lista de proveedor o importacion
de catalogo, de cada tasa nueva (BCCR o manual; desde el portal solo la empresa que la cargo, las demas en la red
diaria) y una vez al dia como red (tasks/margen.py). Para cada producto publicado en la tienda con costo y precio:

    margen = (precio sin IVA - costo en colones) / precio sin IVA

con el MENOR precio que vende la tienda: el precio web (o el de lista) y el de cada variante activa con precio propio
(el checkout cobra la variante a su precio).

- Bajo `min_margin_pct` (Ajustes, 25 % por defecto) entra a Aprobaciones -> "Precios por revisar" con el precio
  sugerido = costo x TC / (1 - margen objetivo), redondeado a la centena hacia arriba, y en modo `activo` sale de venta
  (margin_hold: la vitrina dice "Consultar" y el checkout no lo vende). Sale TODO lo que queda bajo el minimo; el unico
  limite es el freno de abajo.
- NUNCA reprecia solo: el precio cambia unicamente con "Aplicar" (apply), y solo al precio que la persona vio: si el
  sugerido de hoy es otro (cambio el costo o el tipo de cambio) se lo vuelve a mostrar en vez de aplicar otro monto.
- Si el margen se recupera (bajo el costo o el dolar, o alguien cambio el precio) vuelve solo a la venta. Despublicarlo
  NO lo libera: si se vuelve a publicar sigue fuera hasta que el margen llegue al minimo o alguien aplique el precio.
  Sin costo o sin precio no hay margen que proteger: sale de la revision (sin precio la tienda ya dice "Consultar").
- "Dudoso": un salto de costo de 25 % o mas (`margin_guard_jump_pct`) contra el costo de referencia, o un tipo de
  cambio que salto eso o mas contra el anterior (posible error de la lista del proveedor o de digitacion). Se marca en
  todo producto evaluado. Lo que sigue sobre el minimo NO sale de venta: se avisa en el resumen diario durante
  DUDOSO_DIAS dias y despues el costo nuevo queda como referencia. Bajo el minimo, la marca sigue hasta que alguien
  aplique o edite el precio (el aviso pide verificar el costo antes de aplicar).
- Freno (`margin_guard_cap`, 20): solo ante una SEÑAL DE ERROR (un tipo de cambio dudoso, o la mayoria de los que
  saldrian de venta con un salto de costo dudoso) y si con ellos quedarian mas de `cap` productos fuera de venta en
  TOTAL. Entonces se retienen hasta completar `cap` (primero los de peor margen), el resto queda "frenado" a la venta
  y se avisa al admin en el acto (una vez por dia, sin BCC). Lo destraba una persona: corrige el dato o confirma con
  "Sacar de venta todo" (run(confirm=True)). Sin señal de error no hay tope: una subida real del dolar o de la lista
  saca de venta todo lo que quede bajo el minimo. (Pregunta abierta para Andres: frenar ante una señal de error o
  sacar todo siempre; para lo segundo basta un tope alto en Ajustes.)
- `margin_guard_mode`: sin definir = la PRIMERA corrida es `informe` (no oculta nada) y queda `activo`. Un admin lo
  puede dejar en `informe`; en informe tambien se liberan los retenidos.
- Margen objetivo del sugerido: el del producto (margin_pct, con el que se calculo su precio) o el de Ajustes
  (default_margin_pct, 35 %), nunca menos que el minimo.
- Lo que el guardian guarda en Ajustes (ultima corrida, modo, avisos) se escribe releyendo la empresa con bloqueo y
  solo con sus llaves (save_settings): un PUT /settings confirmado mientras corria no se revierte.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.texto import forma, plural
from ..models import Product, ProductVariant, SupplierItem, Tenant
from . import fx as fxsvc
from .discounts import min_margin
from .pricing import ROUND_TO, default_margin, margin_of
from .totals import d

log = logging.getLogger("crimson.margen")

JUMP_PCT = Decimal(25)  # salto de costo o de tipo de cambio que se marca "dudoso" (Ajustes: margin_guard_jump_pct)
DEFAULT_CAP = 20  # freno ante una señal de error (Ajustes: margin_guard_cap)
DUDOSO_DIAS = 3  # un salto de costo sobre el minimo se avisa estos dias; despues el costo nuevo es la referencia
MODES = ("informe", "activo")
COUNTERS = ("evaluados", "bajo_minimo", "nuevos_retenidos", "liberados", "dudosos", "frenados", "sin_tasa", "sin_costo", "sin_precio")


class Stale(Exception):
    """ "Aplicar" no aplica: el producto salio de la revision o el sugerido de hoy no es el que la persona vio. El
    router guarda lo recalculado (commit) y responde 409 con este mensaje."""


def web_price(p: Product):
    return p.web_price if p.web_price is not None else p.price


def money_txt(v, cur: str | None) -> str:
    n = d(v or 0)
    if (cur or "CRC") == "CRC":
        return "₡" + f"{n:,.0f}".replace(",", " ")
    return "$" + f"{n:,.2f}".replace(",", " ").replace(".", ",")


def config(t: Tenant) -> dict:
    st = t.settings or {}
    raw = st.get("margin_guard_mode")
    cap = st.get("margin_guard_cap")
    try:
        cap = max(1, int(cap)) if cap is not None else DEFAULT_CAP
    except (TypeError, ValueError):
        cap = DEFAULT_CAP
    try:
        jump = d(st["margin_guard_jump_pct"]) if st.get("margin_guard_jump_pct") is not None else JUMP_PCT
    except (TypeError, ValueError, ArithmeticError):
        jump = JUMP_PCT
    return {
        "min_pct": min_margin(t),
        "target_pct": default_margin(t),
        # sin definir: primera corrida -> informe (despues queda activo)
        "mode": raw if raw in MODES else "informe",
        "first_run": raw not in MODES,
        "cap": cap,
        "jump_pct": jump if jump > 0 else JUMP_PCT,
    }


@dataclass
class Calc:
    price: Decimal  # el MENOR precio sin IVA que vende la tienda (moneda del producto): sobre ese se mide el margen
    base_price: Decimal  # precio web (o de lista); 0 = "precio a consultar"
    currency: str
    price_crc: Decimal
    cost: Decimal
    cost_currency: str
    cost_crc: Decimal
    margin_pct: Decimal
    target_pct: Decimal
    suggested: Decimal  # en la moneda del producto
    rate: fxsvc.Tasa | None  # la tasa usada (None si todo esta en colones)
    base_low: bool = False  # el precio base esta bajo el minimo ("Aplicar" lo sube al sugerido)
    low_variants: list[int] = field(default_factory=list)  # variantes con precio propio bajo el minimo (tambien suben)
    fx_jump: Decimal = Decimal(0)  # el mayor salto (%) de las tasas usadas contra la anterior
    fx_jump_currency: str | None = None


class Rates:
    """Una consulta por moneda y por corrida. None = no hay tasa (no se puede evaluar ese producto)."""

    def __init__(self, db: Session):
        self.db = db
        self._c: dict[str, fxsvc.Tasa | None] = {}
        self._j: dict[str, Decimal] = {}

    def get(self, cur: str) -> fxsvc.Tasa | None:
        cur = (cur or "CRC").upper()
        if cur not in self._c:
            try:
                self._c[cur] = fxsvc.fx(self.db, cur)
            except fxsvc.FxMissing:
                self._c[cur] = None
        return self._c[cur]

    def jump(self, cur: str) -> Decimal:
        cur = (cur or "CRC").upper()
        if cur not in self._j:
            self._j[cur] = fxsvc.change_pct(self.db, cur)
        return self._j[cur]


def _target(t: Tenant, p: Product, cfg: dict) -> Decimal:
    own = d(p.margin_pct) if p.margin_pct is not None and d(p.margin_pct) > 0 else cfg["target_pct"]
    return max(own, cfg["min_pct"])


def suggest(cost_crc: Decimal, target_pct: Decimal, currency: str, rate: Decimal) -> Decimal:
    """costo en colones / (1 - margen objetivo), en la moneda del producto: colones a la centena hacia arriba
    (como pricing.sale_price), otra moneda al centimo."""
    m = d(target_pct)
    if m >= 100:
        raise ValueError("El margen debe ser menor a 100 %")
    crc = d(cost_crc) / (1 - m / 100)
    if currency == "CRC":
        return (crc / ROUND_TO).quantize(Decimal(1), rounding=ROUND_CEILING) * ROUND_TO
    return (crc / d(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def variants_of(db: Session, product_ids: list[int]) -> dict[int, list[tuple[int, Decimal]]]:
    """Variantes activas con precio propio (> 0) por producto: la tienda las vende a ese precio."""
    out: dict[int, list[tuple[int, Decimal]]] = {}
    if not product_ids:
        return out
    q = select(ProductVariant.product_id, ProductVariant.id, ProductVariant.price).where(
        ProductVariant.product_id.in_(product_ids), ProductVariant.active, ProductVariant.price.is_not(None)
    )
    for pid, vid, price in db.execute(q):
        if d(price) > 0:
            out.setdefault(pid, []).append((vid, d(price)))
    return out


def evaluate(t: Tenant, p: Product, rates: Rates, cfg: dict, variants: list[tuple[int, Decimal]] | None = None) -> Calc | str:
    """El calculo del producto, o el motivo por el que no se puede evaluar ("sin_costo" | "sin_precio" | "sin_tasa")."""
    base = d(web_price(p))
    vs = variants_of(rates.db, [p.id]).get(p.id, []) if variants is None else variants
    prices = ([base] if base > 0 else []) + [v for _, v in vs]
    cost = d(p.cost) if p.cost is not None else Decimal(0)
    if not prices:
        return "sin_precio"  # ya es "precio a consultar" en la tienda
    if cost <= 0:
        return "sin_costo"
    cur, ccur = (p.currency or "CRC").upper(), (p.cost_currency or "USD").upper()
    rp, rc = rates.get(cur), rates.get(ccur)
    if rp is None or rc is None:
        return "sin_tasa"
    price = min(prices)
    price_crc, cost_crc = price * rp.tasa, cost * rc.tasa
    min_pct = cfg["min_pct"]

    def low(v: Decimal) -> bool:
        return margin_of(v * rp.tasa, cost_crc) < min_pct

    used = next((r for r in (rc, rp) if r.fuente != "base"), None)
    target = _target(t, p, cfg)
    jumps = [(rates.jump(x), x) for x in sorted({cur, ccur}) if x != "CRC"]
    fj = max(jumps, key=lambda j: abs(j[0]), default=(Decimal(0), None))
    return Calc(
        price=price,
        base_price=base,
        currency=cur,
        price_crc=price_crc.quantize(Decimal("0.01")),
        cost=cost,
        cost_currency=ccur,
        cost_crc=cost_crc.quantize(Decimal("0.01")),
        margin_pct=margin_of(price_crc, cost_crc),
        target_pct=target,
        suggested=suggest(cost_crc, target, cur, rp.tasa),
        rate=used,
        base_low=base > 0 and low(base),
        low_variants=[vid for vid, v in vs if low(v)],
        fx_jump=fj[0],
        fx_jump_currency=fj[1],
    )


def _prev_cost(db: Session, p: Product, currency: str) -> Decimal | None:
    """Costo de la lista anterior del proveedor vinculado (SupplierItem.prev_cost): la referencia la primera vez."""
    if not p.supplier_sku:
        return None
    v = db.scalar(
        select(SupplierItem.prev_cost).where(
            SupplierItem.tenant_id == p.tenant_id,
            SupplierItem.product_id == p.id,
            SupplierItem.code == p.supplier_sku,
            SupplierItem.currency == currency,
        )
    )
    return d(v) if v is not None and d(v) > 0 else None


def _jump(db: Session, p: Product, c: Calc, info: dict) -> tuple[Decimal, Decimal]:
    """(costo de referencia, salto %) contra el ultimo costo revisado (o la lista anterior del proveedor)."""
    ref = info.get("cost_ref") if info.get("cost_ref_currency") == c.cost_currency else None
    ref = d(ref) if ref is not None else (_prev_cost(db, p, c.cost_currency) or c.cost)
    if ref <= 0:
        return c.cost, Decimal(0)
    return ref, ((c.cost - ref) / ref * 100).quantize(Decimal("0.1"))


def _s(v) -> str:
    return f"{d(v).normalize():f}"


def _since(v) -> datetime | None:
    try:
        x = datetime.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None
    return x.replace(tzinfo=UTC) if x is not None and x.tzinfo is None else x


def _dudoso(db: Session, p: Product, c: Calc, info: dict, cfg: dict, low: bool, now: datetime) -> dict:
    """La marca "dudoso" y el costo de referencia de saltos (ver el docstring del modulo)."""
    ref, jump = _jump(db, p, c, info)
    cost_d = abs(jump) >= cfg["jump_pct"]
    since = None
    if cost_d:
        prev = _since(info.get("jump_since")) if info.get("jump_cost") == _s(c.cost) else None
        since = prev or now
        if not low and (now - since).days >= DUDOSO_DIAS:
            cost_d = False  # sobre el minimo y estable DUDOSO_DIAS dias: el costo nuevo queda como referencia
    fx_d = c.fx_jump_currency is not None and abs(c.fx_jump) >= cfg["jump_pct"]
    por = [k for k, ok in (("costo", cost_d), ("tipo_de_cambio", fx_d)) if ok]
    return {
        "dudoso": bool(por),
        "dudoso_por": por,
        "jump_pct": _s(jump),
        "fx_jump_pct": _s(c.fx_jump),
        "fx_jump_currency": c.fx_jump_currency,
        # el salto queda marcado (la referencia no se mueve) hasta que alguien revise el precio o el costo se estabilice
        "cost_ref": _s(ref if cost_d else c.cost),
        "cost_ref_currency": c.cost_currency,
        "jump_since": since.isoformat() if cost_d and since else None,
        "jump_cost": _s(c.cost) if cost_d else None,
    }


def _calc_info(c: Calc, cfg: dict, now: datetime) -> dict:
    return {
        "margin_pct": _s(c.margin_pct),
        "min_pct": _s(cfg["min_pct"]),
        "target_pct": _s(c.target_pct),
        "price": _s(c.price),
        "base_price": _s(c.base_price),
        "currency": c.currency,
        "price_crc": _s(c.price_crc),
        "cost": _s(c.cost),
        "cost_currency": c.cost_currency,
        "cost_crc": _s(c.cost_crc),
        "suggested": _s(c.suggested),
        "base_low": c.base_low,
        "low_variants": list(c.low_variants),
        "rate": _s(c.rate.tasa) if c.rate else None,
        "rate_date": c.rate.fecha.isoformat() if c.rate and c.rate.fecha else None,
        "rate_source": c.rate.fuente if c.rate else None,
        "rate_stale": bool(c.rate and c.rate.vieja),
        "checked_at": now.isoformat(),
    }


def _reviewed(c: Calc) -> dict:
    """Alguien reviso el precio (lo aplico o lo edito a mano): sale de la revision y el costo de hoy pasa a ser la
    referencia de saltos."""
    return {
        "flagged": False,
        "dudoso": False,
        "dudoso_por": [],
        "over_cap": False,
        "cost_ref": _s(c.cost),
        "cost_ref_currency": c.cost_currency,
        "jump_since": None,
        "jump_cost": None,
    }


def _release(p: Product, motivo: str, now: datetime) -> None:
    p.margin_hold, p.margin_flagged_at = False, None
    p.margin_info = {**(p.margin_info or {}), "flagged": False, "over_cap": False, "motivo": motivo, "checked_at": now.isoformat()}


def _published(db: Session, tid: int) -> list[Product]:
    return list(db.scalars(select(Product).where(Product.tenant_id == tid, Product.active, Product.show_on_web).order_by(Product.id)))


def save_settings(db: Session, tid: int, patch: dict) -> Tenant:
    """Relee la empresa CON BLOQUEO y cambia solo estas llaves de Ajustes. Antes se escribia la copia leida al
    empezar: un PUT /settings confirmado mientras el guardian corria (quien ve costos, BCC, efectivo contra entrega)
    se revertia en silencio. Sin commit."""
    db.flush()  # la sesion de produccion no hace autoflush: nada pendiente se pierde al releer
    t = db.execute(select(Tenant).where(Tenant.id == tid).with_for_update().execution_options(populate_existing=True)).scalar_one()
    t.settings = {**(t.settings or {}), **patch}
    return t


def _brake_signal(fx_flag: dict[str, Decimal], candidates: list) -> str | None:
    """Por que la corrida parece un error de datos (None = no lo parece)."""
    if fx_flag:
        cur, j = max(fx_flag.items(), key=lambda x: abs(x[1]))
        return f"el tipo de cambio de {cur} cambió {_s(j).replace('.', ',')} % contra el anterior"
    n_dud = sum(1 for x in candidates if x[3])
    if candidates and n_dud * 2 > len(candidates):
        return f"{n_dud} de {plural(len(candidates), 'producto', 'productos')} con un salto de costo dudoso"
    return None


def run(db: Session, t: Tenant, *, reason: str = "", confirm: bool = False) -> dict:
    """Una corrida para una empresa. confirm=True: una persona ya verifico los datos y destraba el freno (saca de
    venta todo lo que este bajo el minimo). No hace commit (run_safe si). Devuelve el resumen (tambien en Ajustes)."""
    cfg = config(t)
    mode, now = cfg["mode"], datetime.now(UTC)
    rates = Rates(db)
    out: dict = dict.fromkeys(COUNTERS, 0)
    candidates: list[tuple[Decimal, int, Product, bool]] = []
    kept = 0  # ya fuera de venta y siguen bajo el minimo
    fx_flag: dict[str, Decimal] = {}
    prods = _published(db, t.id)
    vmap = variants_of(db, [p.id for p in prods])
    for p in prods:
        c = evaluate(t, p, rates, cfg, vmap.get(p.id, []))
        if isinstance(c, str):
            out[c] += 1
            if c != "sin_tasa" and (p.margin_hold or p.margin_flagged_at is not None):
                out["liberados"] += int(bool(p.margin_hold))
                _release(p, c, now)  # sin costo o sin precio: nada que proteger (nunca queda "Consultar" para siempre)
            continue  # sin tasa: se queda como estaba (nunca se libera ni se retiene a ciegas)
        out["evaluados"] += 1
        info = dict(p.margin_info or {})
        low = c.margin_pct < cfg["min_pct"]
        dud = _dudoso(db, p, c, info, cfg, low, now)
        info.update(_calc_info(c, cfg, now))
        info.update(dud, flagged=low, over_cap=False, motivo=None)
        out["dudosos"] += int(dud["dudoso"])
        if c.fx_jump_currency and abs(c.fx_jump) >= cfg["jump_pct"]:
            fx_flag[c.fx_jump_currency] = c.fx_jump
        if low:
            out["bajo_minimo"] += 1
            p.margin_flagged_at = p.margin_flagged_at or now
            if mode == "activo":
                if p.margin_hold:
                    kept += 1
                else:
                    candidates.append((c.margin_pct, p.id, p, "costo" in dud["dudoso_por"]))
            elif p.margin_hold:
                p.margin_hold = False  # informe: no oculta nada
                out["liberados"] += 1
        else:
            if p.margin_hold:
                out["liberados"] += 1
            p.margin_hold = False
            p.margin_flagged_at = None
        p.margin_info = info
    # freno: solo ante una señal de error y contando lo que YA esta fuera de venta (varias corridas no lo suman)
    cupo = len(candidates)
    signal = _brake_signal(fx_flag, candidates) if candidates and not confirm else None
    if signal and kept + len(candidates) > cfg["cap"]:
        cupo = max(0, cfg["cap"] - kept)
        out["frenada"] = signal
    candidates.sort(key=lambda x: (x[0], x[1]))  # primero los de peor margen
    for i, (_, _, p, _) in enumerate(candidates):
        if i < cupo:
            p.margin_hold = True
            out["nuevos_retenidos"] += 1
        else:
            p.margin_info = {**(p.margin_info or {}), "over_cap": True}
            out["frenados"] += 1
    db.flush()
    out["retenidos"] = len(held(db, t.id))
    out.update(mode=mode, reason=reason, at=now.isoformat(), min_pct=_s(cfg["min_pct"]), cap=cfg["cap"], confirmada=confirm)
    patch: dict = {"margin_guard_last": out}
    if cfg["first_run"] and out["evaluados"]:
        # la primera corrida que evaluo algo fue solo informe; desde la proxima saca de venta lo que quede bajo el minimo
        patch["margin_guard_mode"] = "activo"
        patch["margin_guard_first_run_at"] = now.isoformat()
    if out.get("frenada"):
        _brake_alert(db, t, out, patch)
    save_settings(db, t.id, patch)
    return out


def _brake_alert(db: Session, t: Tenant, out: dict, patch: dict) -> None:
    """Aviso inmediato al admin (una vez por dia y por empresa, sin BCC): hay productos bajo el minimo que siguen a la
    venta porque la corrida parece un error de datos."""
    from ..core.config import settings
    from .mail import notify_roles

    hoy = fxsvc.cr_today().isoformat()
    if (t.settings or {}).get("margin_guard_brake_alert_on") == hoy:
        return
    link = f"{settings.public_base_url.rstrip('/')}/aprobaciones?seccion=precios_por_revisar"
    body = (
        f"<p>La revisión de margen ({html.escape(out.get('reason') or 'automática')}) encontró productos bajo el margen "
        f"mínimo, pero <b>{html.escape(out['frenada'])}</b>: puede ser un error del tipo de cambio o de la lista del "
        f"proveedor. Para no vaciar la tienda por un error {forma(out['nuevos_retenidos'], 'se sacó', 'se sacaron')} de venta "
        f"{plural(out['nuevos_retenidos'], 'producto', 'productos')} y "
        f"<b>{plural(out['frenados'], 'producto sigue', 'productos siguen')} a la venta bajo el mínimo</b> hasta que lo revise.</p>"
        "<p>Si el dato está mal, corríjalo (tipo de cambio en Ajustes → Facturación) y se libera solo. Si está bien, "
        f'toque «Sacar de venta todo» en <a href="{html.escape(link)}">Aprobaciones → Precios por revisar</a>.</p>'
        "<p>El sistema nunca cambia un precio solo.</p>"
        f'<p><a href="{html.escape(link)}">Revisar precios</a></p>'
    )
    notify_roles(db, t, ("admin",), f"Margen: revisión frenada, {plural(out['frenados'], 'producto', 'productos')} bajo el mínimo a la venta · {t.name}", body, "margin_summary", 0)
    patch["margin_guard_brake_alert_on"] = hoy


def run_safe(db: Session, t: Tenant, *, reason: str = "", confirm: bool = False) -> dict:
    """run + commit propio. Un error se registra y nunca tumba a quien llamo (la lista, el refresco o la tasa nueva ya
    quedaron guardados)."""
    tid = t.id
    try:
        out = run(db, t, reason=reason, confirm=confirm)
        db.commit()
        return out
    except Exception:  # noqa: BLE001  el guardian nunca tumba el refresco ni la carga del tipo de cambio
        db.rollback()
        log.exception("guardian de margen (empresa %s)", tid)
        return {"error": True}


def recheck(db: Session, t: Tenant, p: Product) -> None:
    """Despues de editar a mano un producto que estaba en revision (precio, variantes, costo, moneda o publicacion): si
    su margen ya llega al minimo sale de la revision y vuelve a la venta en el acto, sin esperar al proximo refresco;
    si sigue bajo, se actualiza el sugerido. Despublicarlo no lo libera (si se vuelve a publicar se revisa aqui mismo y
    sigue fuera si sigue bajo). Nunca saca de venta por una edicion manual (eso lo decide la proxima corrida) ni toca
    el resumen. Sin commit."""
    if not p.margin_hold and p.margin_flagged_at is None:
        return
    if not (p.active and p.show_on_web):
        return  # la tienda no lo muestra; el estado se conserva para cuando vuelva
    now, cfg = datetime.now(UTC), config(t)
    c = evaluate(t, p, Rates(db), cfg)
    if c in ("sin_costo", "sin_precio"):
        _release(p, c, now)
        return
    if isinstance(c, str):
        return  # sin tasa: se queda como estaba
    info = dict(p.margin_info or {})
    info.update(_calc_info(c, cfg, now))
    if c.margin_pct >= cfg["min_pct"]:
        info.update(_reviewed(c))
        p.margin_hold, p.margin_flagged_at = False, None
    p.margin_info = info


def held(db: Session, tid: int) -> list[Product]:
    return list(db.scalars(select(Product).where(Product.tenant_id == tid, Product.margin_hold, Product.active, Product.show_on_web)))


def flagged(db: Session, tid: int) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(Product.tenant_id == tid, Product.margin_flagged_at.is_not(None), Product.active, Product.show_on_web)
            .order_by(Product.margin_flagged_at, Product.id)
        )
    )


def run_all(db: Session, *, reason: str = "") -> dict:
    """Todas las empresas activas, cada una en su propio commit (la red diaria y la tasa diaria del BCCR del worker)."""
    out = {}
    for tid in db.scalars(select(Tenant.id).where(Tenant.active)).all():
        t = db.get(Tenant, tid)
        out[tid] = run_safe(db, t, reason=reason)
    return out


# ---------- Aprobaciones: "Precios por revisar" ----------
def review(t: Tenant, p: Product) -> dict:
    """Lo que muestra la bandeja (del ultimo calculo guardado)."""
    i = p.margin_info or {}
    return {
        "margin_pct": i.get("margin_pct"),
        "min_pct": i.get("min_pct"),
        "target_pct": i.get("target_pct"),
        "price": i.get("price"),
        "suggested": i.get("suggested"),
        "currency": i.get("currency") or p.currency,
        "dudoso": bool(i.get("dudoso")),
        "dudoso_por": list(i.get("dudoso_por") or (["costo"] if i.get("dudoso") else [])),
        "jump_pct": i.get("jump_pct"),
        "fx_jump_pct": i.get("fx_jump_pct"),
        "fx_jump_currency": i.get("fx_jump_currency"),
        "over_cap": bool(i.get("over_cap")),
        "held": bool(p.margin_hold),
        "low_variants": len(i.get("low_variants") or []),
        "base_low": bool(i.get("base_low", True)),
    }


def dudoso_txt(r: dict) -> str:
    """Por que se marco dudoso, para la bandeja y el resumen."""
    out = []
    if "costo" in r["dudoso_por"]:
        out.append(f"el costo cambió {str(r['jump_pct']).replace('.', ',')} %, verifíquelo con el proveedor")
    if "tipo_de_cambio" in r["dudoso_por"]:
        out.append(f"el tipo de cambio de {r['fx_jump_currency']} saltó {str(r['fx_jump_pct']).replace('.', ',')} %, verifíquelo en Ajustes → Facturación")
    return " y ".join(out)


def _quant(v, cur: str) -> Decimal | None:
    try:
        x = d(v)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return x.quantize(Decimal(1) if cur == "CRC" else Decimal("0.01"), rounding=ROUND_HALF_UP)


def apply(db: Session, t: Tenant, p: Product, user_id: int | None, ip: str | None = None, *, expected=None) -> dict:
    """Boton "Aplicar": el precio que la persona vio (`expected`, el sugerido de la bandeja) pasa a ser el de la tienda
    en cada precio que esta bajo el minimo (el web si el producto lo tiene, si no el de lista, y las variantes con
    precio propio) y el producto vuelve a la venta. Antes de aplicar se recalcula con el costo y el tipo de cambio de
    ahora: si el producto ya salio de la revision (doble clic, ya se aplico, recupero el margen) o el sugerido de hoy
    no es el que vio, lanza Stale (409) y deja guardado lo recalculado para que lo vuelva a confirmar; nunca aplica otro
    monto. Sin commit. FxMissing (sin tasa) sube como 422."""
    from fastapi import HTTPException

    from .documents import audit

    if p.margin_flagged_at is None:
        raise Stale("Este producto ya no está en Precios por revisar (ya se aplicó un precio o recuperó el margen). Actualizá la bandeja.")
    now, cfg = datetime.now(UTC), config(t)
    c = evaluate(t, p, Rates(db), cfg)
    if c == "sin_tasa":
        cur = p.currency if (p.currency or "CRC").upper() != "CRC" else p.cost_currency
        raise fxsvc.FxMissing(cur or "USD")
    if c == "sin_costo":
        raise HTTPException(422, "El producto no tiene costo: no hay precio que sugerir. Cargale el costo en Productos.")
    if isinstance(c, str):
        raise HTTPException(422, "El producto no tiene precio de venta (la tienda ya lo muestra «Consultar»): ponele precio en Productos.")
    info = {**(p.margin_info or {}), **_calc_info(c, cfg, now)}
    if c.margin_pct >= cfg["min_pct"]:
        p.margin_info = {**info, **_reviewed(c)}
        p.margin_hold, p.margin_flagged_at = False, None
        raise Stale("Con su precio actual ya cumple el margen mínimo: volvió a la venta sin cambiar el precio.")
    if _quant(expected, c.currency) != _quant(c.suggested, c.currency):
        p.margin_info = info  # la bandeja muestra el sugerido de hoy
        raise Stale(
            f"El precio sugerido ahora es {money_txt(c.suggested, c.currency)} (cambió el costo o el tipo de cambio): "
            "revisalo y volvé a aplicarlo. No se cambió nada."
        )
    field_ = "web_price" if p.web_price is not None else "price"
    vrows = list(db.scalars(select(ProductVariant).where(ProductVariant.id.in_(c.low_variants)))) if c.low_variants else []
    before = {"price": _s(c.base_price), "currency": c.currency, "margin_pct": _s(c.margin_pct), "held": bool(p.margin_hold)}
    before["variants"] = {str(v.id): _s(v.price) for v in vrows}
    if c.base_low:
        setattr(p, field_, c.suggested)
        if field_ == "price":
            p.margin_pct = c.target_pct  # el precio de lista ahora sale de ese margen
    for v in vrows:
        v.price = c.suggested
    db.flush()
    c2 = evaluate(t, p, Rates(db), cfg)
    new_margin = c2.margin_pct if isinstance(c2, Calc) else margin_of(c.suggested * c.price_crc / c.price, c.cost_crc)
    p.margin_hold = False
    p.margin_flagged_at = None
    p.margin_info = {
        **info,
        **(_calc_info(c2, cfg, now) if isinstance(c2, Calc) else {}),
        **_reviewed(c),  # el costo quedo revisado: es la nueva referencia de saltos
        "margin_pct": _s(new_margin),
        "applied_at": now.isoformat(),
        "applied_by": user_id,
    }
    price_now = d(web_price(p))
    after = {"price": _s(price_now), "currency": c.currency, "margin_pct": _s(new_margin), "held": False, "field": field_}
    after["variants"] = {str(v.id): _s(v.price) for v in vrows}
    audit(db, t.id, user_id, "margin_apply", "product", p.id, {"precio": _s(c.suggested), "campo": field_}, ip=ip, before=before, after=after)
    return {
        "id": p.id,
        "price": price_now,
        "suggested": c.suggested,
        "currency": c.currency,
        "field": field_,
        "variants": len(vrows),
        "margin_pct": new_margin,
        "held": False,
    }


# ---------- resumen diario al admin ----------
def fx_alerts(db: Session, t: Tenant) -> list[str]:
    """Alerta de tipo de cambio (plan 3, "BCCR diario mas alerta si queda viejo"): una linea por moneda que la tienda
    necesita (precio o costo de un producto publicado en otra moneda) y que falta o tiene mas de 2 dias habiles.
    Hoy BCCR_TOKEN puede estar vacio: la tasa se carga a mano y nadie se entera si se olvida."""
    need: set[str] = set()
    rows = db.execute(select(Product.currency, Product.cost_currency, Product.cost).where(Product.tenant_id == t.id, Product.active, Product.show_on_web)).all()
    for cur, ccur, cost in rows:
        if (cur or "CRC").upper() != "CRC":
            need.add(cur.upper())
        if cost is not None and d(cost) > 0 and (ccur or "USD").upper() != "CRC":
            need.add((ccur or "USD").upper())
    out = []
    for cur in sorted(need):
        i = fxsvc.info(db, cur)
        if i is None:
            out.append(
                f"No hay tipo de cambio de {cur} cargado: lo que tiene precio en {cur} sale «Consultar» en la tienda y el "
                "guardián no puede revisar el margen de lo que cuesta en esa moneda. Cárguelo en Ajustes → Facturación."
            )
        elif i["stale"]:
            fecha = i["date"].strftime("%d/%m/%Y") if i["date"] else "?"
            out.append(
                f"El tipo de cambio de {cur} es del {fecha} ({i['source_label']}, {i['business_days_old']} días hábiles): "
                "los precios y márgenes en esa moneda se calculan con una tasa vieja. Cargue la de hoy en Ajustes → Facturación."
            )
    return out


def dudosos_a_la_venta(db: Session, tid: int) -> list[Product]:
    """Publicados con un salto dudoso que siguen sobre el minimo (no salen de venta: solo se avisan)."""
    return [p for p in _published(db, tid) if p.margin_flagged_at is None and (p.margin_info or {}).get("dudoso")]


def summary_html(db: Session, t: Tenant, base_url: str) -> tuple[str, str] | None:
    """(asunto, html) del resumen diario, o None si no hay nada bajo el minimo, ningun salto dudoso ni un tipo de cambio
    viejo o faltante."""
    rows = flagged(db, t.id)
    dud = dudosos_a_la_venta(db, t.id)
    alerts = fx_alerts(db, t)
    if not rows and not alerts and not dud:
        return None
    cfg = config(t)
    last = (t.settings or {}).get("margin_guard_last") or {}
    link = f"{base_url.rstrip('/')}/aprobaciones?seccion=precios_por_revisar"
    fx_html = "".join(f"<p><b>Tipo de cambio:</b> {html.escape(a)}</p>" for a in alerts)
    dud_html = ""
    if dud:
        li = []
        for p in dud[:30]:
            r = review(t, p)
            li.append(
                f"<li><b>{html.escape(p.name)}</b> ({html.escape(p.code)}) · margen {html.escape(str(r['margin_pct']))} % · {html.escape(dudoso_txt(r))}</li>"
            )
        mas = f"<p>… y {len(dud) - 30} más.</p>" if len(dud) > 30 else ""
        dud_html = (
            f"<p>{plural(len(dud), 'producto con un salto', 'productos con un salto')} de costo o de tipo de cambio <b>dudoso</b> "
            f"{forma(len(dud), 'sigue', 'siguen')} a la venta porque su margen "
            f"está sobre el mínimo. Verifique que el dato sea correcto (posible error de la lista del proveedor):</p><ul>{''.join(li)}</ul>{mas}"
        )
    if not rows:
        subject = f"Margen: {plural(len(dud), 'costo dudoso', 'costos dudosos')}" if dud else f"Tipo de cambio viejo o faltante ({len(alerts)})"
        return subject, fx_html + dud_html + f'<p><a href="{html.escape(link)}">Revisar precios</a></p>'
    n_held = sum(1 for p in rows if p.margin_hold)
    n_dud = sum(1 for p in rows if (p.margin_info or {}).get("dudoso"))
    li = []
    for p in rows[:30]:
        r = review(t, p)
        estado = "fuera de venta (Consultar)" if r["held"] else ("frenado: sigue a la venta" if r["over_cap"] else "solo informe: sigue a la venta")
        extra = f" · <b>dudoso</b>: {html.escape(dudoso_txt(r))}" if r["dudoso"] else ""
        precio = money_txt(r["price"], r["currency"])
        sug = money_txt(r["suggested"], r["currency"])
        li.append(
            f"<li><b>{html.escape(p.name)}</b> ({html.escape(p.code)}) · margen {html.escape(str(r['margin_pct']))} % · "
            f"precio {precio} → sugerido {sug} · {estado}{extra}</li>"
        )
    mas = f"<p>… y {len(rows) - 30} más.</p>" if len(rows) > 30 else ""
    modo = "activo: lo que queda bajo el mínimo sale de venta" if cfg["mode"] == "activo" else "informe: no se saca nada de venta"
    freno = (
        f"<p><b>Revisión frenada:</b> {html.escape(last['frenada'])}. "
        f"{plural(last.get('frenados', 0), 'producto bajo el mínimo sigue', 'productos bajo el mínimo siguen')} "
        "a la venta hasta que corrija el dato o toque «Sacar de venta todo» en Aprobaciones.</p>"
        if last.get("frenada")
        else ""
    )
    body = (
        f"{fx_html}{freno}<p>{plural(len(rows), 'producto de la tienda está', 'productos de la tienda están')} bajo el margen mínimo de {_s(cfg['min_pct'])} % "
        f"({n_held} fuera de venta, {n_dud} con un salto dudoso). Modo {modo}.</p><ul>{''.join(li)}</ul>{mas}{dud_html}"
        f"<p>El sistema nunca cambia un precio solo: revíselos y aplique el sugerido con un clic en "
        f'<a href="{html.escape(link)}">Aprobaciones → Precios por revisar</a>.</p>'
        f'<p><a href="{html.escape(link)}">Revisar precios</a></p>'  # el boton principal del correo (email_layout)
    )
    return f"Margen: {plural(len(rows), 'producto', 'productos')} bajo el {_s(cfg['min_pct'])} %", body
