"""Guardian de margen (plan 1.12): la tienda nunca vende bajo el margen minimo sin que alguien lo decida.

Corre despues de cada refresco de Eurocomp (supplier_sync.refresh_linked) y de cada tasa nueva (BCCR o manual).
Para cada producto publicado en la tienda con costo y precio:

    margen = (precio sin IVA - costo en colones) / precio sin IVA          (el precio web si lo tiene)

- Bajo `min_margin_pct` (Ajustes, 25 % por defecto) entra a Aprobaciones -> "Precios por revisar" con el precio
  sugerido = costo x TC / (1 - margen objetivo), redondeado a la centena hacia arriba, y en modo `activo` sale de venta
  en la tienda (margin_hold: la vitrina dice "Consultar" y el checkout no lo vende).
- NUNCA reprecia solo: el precio cambia unicamente con "Aplicar" (apply), que lo vuelve a publicar.
- Si el margen se recupera (bajo el costo o el dolar, o alguien cambio el precio) vuelve solo a la venta.
- Un salto de costo de 25 % o mas contra el costo de referencia se marca "dudoso" (posible error de la lista del
  proveedor): igual se protege el margen, pero el aviso pide verificar el costo antes de aplicar.
- `margin_guard_mode`: sin definir = la PRIMERA corrida es `informe` (no oculta nada) y queda `activo`. Un admin lo
  puede dejar en `informe`; en informe tambien se liberan los retenidos.
- Tope (`margin_guard_cap`, 20): una corrida no saca de venta mas productos que eso (un tipo de cambio mal digitado no
  vacia la tienda). Primero los de peor margen; el resto queda marcado "sobre el tope" para revisarlo a mano.
- Margen objetivo del sugerido: el del producto (margin_pct, con el que se calculo su precio) o el de Ajustes
  (default_margin_pct, 35 %), nunca menos que el minimo.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import Product, SupplierItem, Tenant
from . import fx as fxsvc
from .discounts import min_margin
from .pricing import ROUND_TO, default_margin, margin_of
from .totals import d

log = logging.getLogger("crimson.margen")

JUMP_PCT = Decimal(25)  # salto de costo que se marca "dudoso"
DEFAULT_CAP = 20
MODES = ("informe", "activo")


def web_price(p: Product):
    return p.web_price if p.web_price is not None else p.price


def config(t: Tenant) -> dict:
    st = t.settings or {}
    raw = st.get("margin_guard_mode")
    cap = st.get("margin_guard_cap")
    try:
        cap = max(1, int(cap)) if cap is not None else DEFAULT_CAP
    except (TypeError, ValueError):
        cap = DEFAULT_CAP
    return {
        "min_pct": min_margin(t),
        "target_pct": default_margin(t),
        # sin definir: primera corrida -> informe (despues queda activo)
        "mode": raw if raw in MODES else "informe",
        "first_run": raw not in MODES,
        "cap": cap,
    }


@dataclass
class Calc:
    price: Decimal  # precio sin IVA en la moneda del producto (el que vende la tienda)
    currency: str
    price_crc: Decimal
    cost: Decimal
    cost_currency: str
    cost_crc: Decimal
    margin_pct: Decimal
    target_pct: Decimal
    suggested: Decimal  # en la moneda del producto
    rate: fxsvc.Tasa | None  # la tasa usada (None si todo esta en colones)


class Rates:
    """Una consulta por moneda y por corrida. None = no hay tasa (no se puede evaluar ese producto)."""

    def __init__(self, db: Session):
        self.db = db
        self._c: dict[str, fxsvc.Tasa | None] = {}

    def get(self, cur: str) -> fxsvc.Tasa | None:
        cur = (cur or "CRC").upper()
        if cur not in self._c:
            try:
                self._c[cur] = fxsvc.fx(self.db, cur)
            except fxsvc.FxMissing:
                self._c[cur] = None
        return self._c[cur]


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


def evaluate(t: Tenant, p: Product, rates: Rates, cfg: dict) -> Calc | str:
    """El calculo del producto, o el motivo por el que no se puede evaluar ("sin_costo" | "sin_precio" | "sin_tasa")."""
    price = d(web_price(p))
    cost = d(p.cost) if p.cost is not None else Decimal(0)
    if price <= 0:
        return "sin_precio"  # ya es "precio a consultar" en la tienda
    if cost <= 0:
        return "sin_costo"
    cur, ccur = (p.currency or "CRC").upper(), (p.cost_currency or "USD").upper()
    rp, rc = rates.get(cur), rates.get(ccur)
    if rp is None or rc is None:
        return "sin_tasa"
    price_crc, cost_crc = price * rp.tasa, cost * rc.tasa
    used = next((r for r in (rc, rp) if r.fuente != "base"), None)
    target = _target(t, p, cfg)
    return Calc(
        price=price,
        currency=cur,
        price_crc=price_crc.quantize(Decimal("0.01")),
        cost=cost,
        cost_currency=ccur,
        cost_crc=cost_crc.quantize(Decimal("0.01")),
        margin_pct=margin_of(price_crc, cost_crc),
        target_pct=target,
        suggested=suggest(cost_crc, target, cur, rp.tasa),
        rate=used,
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


def _published(db: Session, tid: int) -> list[Product]:
    return list(db.scalars(select(Product).where(Product.tenant_id == tid, Product.active, Product.show_on_web).order_by(Product.id)))


def run(db: Session, t: Tenant, *, reason: str = "") -> dict:
    """Una corrida para una empresa. No hace commit (run_all si). Devuelve el resumen (tambien queda en Ajustes)."""
    cfg = config(t)
    mode, now = cfg["mode"], datetime.now(UTC)
    rates = Rates(db)
    out = {k: 0 for k in ("evaluados", "bajo_minimo", "nuevos_retenidos", "liberados", "dudosos", "sobre_tope", "sin_tasa", "sin_costo")}
    candidates: list[tuple[Decimal, Product]] = []
    for p in _published(db, t.id):
        c = evaluate(t, p, rates, cfg)
        if isinstance(c, str):
            if c in ("sin_tasa", "sin_costo"):
                out[c] += 1
            continue  # sin poder calcular: se queda como estaba (nunca se libera ni se retiene a ciegas)
        out["evaluados"] += 1
        info = dict(p.margin_info or {})
        ref, jump = _jump(db, p, c, info)
        dudoso = abs(jump) >= JUMP_PCT
        low = c.margin_pct < cfg["min_pct"]
        info.update(
            {
                "margin_pct": _s(c.margin_pct),
                "min_pct": _s(cfg["min_pct"]),
                "target_pct": _s(c.target_pct),
                "price": _s(c.price),
                "currency": c.currency,
                "price_crc": _s(c.price_crc),
                "cost": _s(c.cost),
                "cost_currency": c.cost_currency,
                "cost_crc": _s(c.cost_crc),
                "suggested": _s(c.suggested),
                "rate": _s(c.rate.tasa) if c.rate else None,
                "rate_date": c.rate.fecha.isoformat() if c.rate and c.rate.fecha else None,
                "rate_source": c.rate.fuente if c.rate else None,
                "rate_stale": bool(c.rate and c.rate.vieja),
                "dudoso": bool(low and dudoso),
                "jump_pct": _s(jump),
                # el salto queda marcado hasta que alguien aplique un precio (o el costo vuelva a lo normal)
                "cost_ref": _s(ref if low and dudoso else c.cost),
                "cost_ref_currency": c.cost_currency,
                "checked_at": now.isoformat(),
                "flagged": low,
                "over_cap": False,
            }
        )
        if low:
            out["bajo_minimo"] += 1
            out["dudosos"] += int(dudoso)
            p.margin_flagged_at = p.margin_flagged_at or now
            if mode == "activo" and not p.margin_hold:
                candidates.append((c.margin_pct, p))
            elif mode != "activo" and p.margin_hold:
                p.margin_hold = False  # informe: no oculta nada
                out["liberados"] += 1
        else:
            if p.margin_hold:
                out["liberados"] += 1
            p.margin_hold = False
            p.margin_flagged_at = None
        p.margin_info = info
    # tope: primero los de peor margen
    candidates.sort(key=lambda x: (x[0], x[1].id))
    for i, (_, p) in enumerate(candidates):
        if i < cfg["cap"]:
            p.margin_hold = True
            out["nuevos_retenidos"] += 1
        else:
            p.margin_info = {**(p.margin_info or {}), "over_cap": True}
            out["sobre_tope"] += 1
    # lo que dejo de publicarse no tiene nada que proteger: sale de la revision
    for p in db.scalars(
        select(Product).where(
            Product.tenant_id == t.id,
            or_(Product.margin_hold, Product.margin_flagged_at.is_not(None)),
            ~and_(Product.active, Product.show_on_web),
        )
    ):
        p.margin_hold, p.margin_flagged_at = False, None
    db.flush()
    out["retenidos"] = len(held(db, t.id))
    out.update(mode=mode, reason=reason, at=now.isoformat(), min_pct=_s(cfg["min_pct"]), cap=cfg["cap"])
    st = dict(t.settings or {})
    st["margin_guard_last"] = out
    if cfg["first_run"] and out["evaluados"]:
        # la primera corrida que evaluo algo fue solo informe; desde la proxima saca de venta lo que quede bajo el minimo
        st["margin_guard_mode"] = "activo"
        st["margin_guard_first_run_at"] = now.isoformat()
    t.settings = st
    return out


def recheck(db: Session, t: Tenant, p: Product) -> None:
    """Despues de editar a mano un producto que estaba en revision (precio, costo, moneda o publicacion): si su margen
    ya llega al minimo (o dejo de publicarse) sale de la revision y vuelve a la venta en el acto, sin esperar al
    proximo refresco; si sigue bajo, se actualiza el sugerido. Nunca saca de venta por una edicion manual (eso lo
    decide la proxima corrida, con su tope) ni toca el resumen. Sin commit; nunca lanza."""
    if not p.margin_hold and p.margin_flagged_at is None:
        return
    if not (p.active and p.show_on_web):
        p.margin_hold, p.margin_flagged_at = False, None
        return
    cfg = config(t)
    c = evaluate(t, p, Rates(db), cfg)
    if isinstance(c, str):
        return  # sin tasa o sin costo: se queda como estaba
    info = dict(p.margin_info or {})
    info.update(
        {
            "margin_pct": _s(c.margin_pct),
            "min_pct": _s(cfg["min_pct"]),
            "target_pct": _s(c.target_pct),
            "price": _s(c.price),
            "currency": c.currency,
            "cost": _s(c.cost),
            "cost_currency": c.cost_currency,
            "cost_crc": _s(c.cost_crc),
            "suggested": _s(c.suggested),
            "checked_at": datetime.now(UTC).isoformat(),
        }
    )
    if c.margin_pct >= cfg["min_pct"]:
        # alguien reviso el precio a mano: el costo de hoy pasa a ser la referencia de saltos
        info.update({"flagged": False, "dudoso": False, "over_cap": False, "cost_ref": _s(c.cost), "cost_ref_currency": c.cost_currency})
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
    """Todas las empresas activas, cada una en su propio commit. Un error se registra y no frena a las demas ni a quien
    llamo (el refresco de Eurocomp o la tasa nueva ya quedaron guardados)."""
    out = {}
    for tid in db.scalars(select(Tenant.id).where(Tenant.active)).all():
        try:
            t = db.get(Tenant, tid)
            out[tid] = run(db, t, reason=reason)
            db.commit()
        except Exception:  # noqa: BLE001  el guardian nunca tumba el refresco ni la carga del tipo de cambio
            db.rollback()
            log.exception("guardian de margen (empresa %s)", tid)
            out[tid] = {"error": True}
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
        "jump_pct": i.get("jump_pct"),
        "over_cap": bool(i.get("over_cap")),
        "held": bool(p.margin_hold),
    }


def apply(db: Session, t: Tenant, p: Product, user_id: int | None, ip: str | None = None) -> dict:
    """Boton "Aplicar": el precio sugerido, recalculado AHORA con el costo y el tipo de cambio vigentes, pasa a ser el
    precio de la tienda (el precio web si el producto lo tiene; si no, el de lista) y el producto vuelve a la venta.
    Sin commit. FxMissing (sin tasa) sube como 422."""
    from fastapi import HTTPException

    from .documents import audit

    cfg = config(t)
    c = evaluate(t, p, Rates(db), cfg)
    if c == "sin_tasa":
        cur = p.currency if (p.currency or "CRC").upper() != "CRC" else p.cost_currency
        raise fxsvc.FxMissing(cur or "USD")
    if isinstance(c, str):
        raise HTTPException(422, "El producto no tiene costo: no hay precio que sugerir")
    field = "web_price" if p.web_price is not None else "price"
    before = {"price": _s(web_price(p)), "currency": c.currency, "margin_pct": _s(c.margin_pct), "held": bool(p.margin_hold)}
    setattr(p, field, c.suggested)
    if field == "price":
        p.margin_pct = c.target_pct  # el precio de lista ahora sale de ese margen
    rate_price = c.price_crc / c.price  # colones por unidad de la moneda del producto (1 en colones)
    new_margin = margin_of(c.suggested * rate_price, c.cost_crc)
    p.margin_hold = False
    p.margin_flagged_at = None
    p.margin_info = {
        **(p.margin_info or {}),
        "margin_pct": _s(new_margin),
        "price": _s(c.suggested),
        "suggested": _s(c.suggested),
        "flagged": False,
        "dudoso": False,
        "over_cap": False,
        "cost_ref": _s(c.cost),  # el costo quedo revisado: es la nueva referencia de saltos
        "cost_ref_currency": c.cost_currency,
        "applied_at": datetime.now(UTC).isoformat(),
        "applied_by": user_id,
    }
    after = {"price": _s(c.suggested), "currency": c.currency, "margin_pct": _s(new_margin), "held": False, "field": field}
    audit(db, t.id, user_id, "margin_apply", "product", p.id, {"precio": _s(c.suggested), "campo": field}, ip=ip, before=before, after=after)
    return {"id": p.id, "price": c.suggested, "currency": c.currency, "field": field, "margin_pct": new_margin, "held": False}


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
                "guardián no puede revisar el margen de lo que cuesta en esa moneda. Cargalo en Ajustes → Facturación."
            )
        elif i["stale"]:
            fecha = i["date"].strftime("%d/%m/%Y") if i["date"] else "?"
            out.append(
                f"El tipo de cambio de {cur} es del {fecha} ({i['source_label']}, {i['business_days_old']} días hábiles): "
                "los precios y márgenes en esa moneda se calculan con una tasa vieja. Cargá la de hoy en Ajustes → Facturación."
            )
    return out


def summary_html(db: Session, t: Tenant, base_url: str) -> tuple[str, str] | None:
    """(asunto, html) del resumen diario, o None si no hay nada bajo el minimo ni un tipo de cambio viejo o faltante."""
    from .render import money

    rows = flagged(db, t.id)
    alerts = fx_alerts(db, t)
    if not rows and not alerts:
        return None
    fx_html = "".join(f"<p><b>Tipo de cambio:</b> {html.escape(a)}</p>" for a in alerts)
    if not rows:
        return f"Tipo de cambio viejo o faltante ({len(alerts)})", fx_html
    cfg = config(t)
    n_held = sum(1 for p in rows if p.margin_hold)
    n_dud = sum(1 for p in rows if (p.margin_info or {}).get("dudoso"))
    li = []
    for p in rows[:30]:
        r = review(t, p)
        estado = "fuera de venta (Consultar)" if r["held"] else ("sobre el tope: sigue a la venta" if r["over_cap"] else "solo informe: sigue a la venta")
        extra = f" · <b>dudoso</b>: el costo cambió {html.escape(str(r['jump_pct']))} %, verificá con el proveedor" if r["dudoso"] else ""
        precio = money(d(r["price"] or 0), r["currency"])
        sug = money(d(r["suggested"] or 0), r["currency"])
        li.append(
            f"<li><b>{html.escape(p.name)}</b> ({html.escape(p.code)}) · margen {html.escape(str(r['margin_pct']))} % · "
            f"precio {precio} → sugerido {sug} · {estado}{extra}</li>"
        )
    mas = f"<p>… y {len(rows) - 30} más.</p>" if len(rows) > 30 else ""
    link = f"{base_url.rstrip('/')}/aprobaciones#precios_por_revisar"
    modo = "activo: lo que queda bajo el mínimo sale de venta" if cfg["mode"] == "activo" else "informe: no se saca nada de venta"
    body = (
        f"{fx_html}<p>{len(rows)} producto(s) de la tienda están bajo el margen mínimo de {_s(cfg['min_pct'])} % "
        f"({n_held} fuera de venta, {n_dud} con un salto de costo dudoso). Modo {modo}.</p><ul>{''.join(li)}</ul>{mas}"
        f"<p>El sistema nunca cambia un precio solo: revisalos y aplicá el sugerido con un clic en "
        f'<a href="{html.escape(link)}">Aprobaciones → Precios por revisar</a>.</p>'
    )
    return f"Margen: {len(rows)} producto(s) bajo el {_s(cfg['min_pct'])} %", body
