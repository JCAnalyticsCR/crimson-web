"""Margen por categoria (Catalogo -> Margenes).

Una categoria puede tener su propio margen sobre la venta. Lo heredan sus subcategorias y los productos que no tienen
margen propio. Prioridad para saber con que margen se calcula el precio de un producto:

    margen propio del producto  ->  margen de la categoria mas cercana (subiendo por los padres)  ->  general (Ajustes)

Conservar precios: guardar un margen NUNCA reprecia solo. El administrador ve primero la vista previa (cuantos productos
cambian, precio antes y despues, margen real) y los precios cambian unicamente al confirmar esa misma vista previa: la
confirmacion trae la huella (`token`) de lo que vio y, si entre tanto cambio un costo o el tipo de cambio, se rechaza y se
le muestra de nuevo. Quitar el margen de una categoria no cambia ningun precio.

Precio = costo x tipo de cambio / (1 - margen), redondeado como pricing.sale_price (centena hacia arriba en colones).
Sin costo no hay precio que calcular: el producto se lista pero no se toca. El precio web propio (web_price) es una
decision aparte: no se toca y la vista previa lo avisa.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, Product, Tenant
from . import pricing
from .totals import d

log = logging.getLogger("crimson.margen_categoria")

MIN_PCT, MAX_PCT = Decimal(0), Decimal(95)
MAX_PRODUCTS = 3000  # una categoria grande (SEGURIDAD ~800) cabe holgada; mas que eso se acota por subcategoria


class Stale(Exception):
    """La confirmacion no coincide con la vista previa actual (cambio un costo, el tipo de cambio o el catalogo)."""


def pct_txt(v) -> str:
    """40.00 -> "40", 37.50 -> "37,5" (es-CR)."""
    return f"{d(v).normalize():f}".replace(".", ",")


def _pct(v) -> Decimal | None:
    return d(v) if v is not None else None


def categories(db: Session, tid: int) -> dict[int, Category]:
    return {c.id: c for c in db.scalars(select(Category).where(Category.tenant_id == tid))}


def resolve(cats: dict[int, Category]) -> dict[int, tuple[Decimal | None, int | None]]:
    """Para cada categoria: (margen que rige, categoria de donde sale). (None, None) = no hay en la cadena: rige el general.
    Tolera ciclos en parent_id (datos viejos): corta al repetir."""
    out: dict[int, tuple[Decimal | None, int | None]] = {}
    for cid in cats:
        seen, cur = set(), cid
        res: tuple[Decimal | None, int | None] = (None, None)
        while cur is not None and cur in cats and cur not in seen:
            seen.add(cur)
            c = cats[cur]
            if c.margin_pct is not None:
                res = (d(c.margin_pct), c.id)
                break
            cur = c.parent_id
        out[cid] = res
    return out


def subtree(cats: dict[int, Category], cid: int) -> set[int]:
    keep, todo = {cid}, [cid]
    while todo:
        cur = todo.pop()
        for c in cats.values():
            if c.parent_id == cur and c.id not in keep:
                keep.add(c.id)
                todo.append(c.id)
    return keep


def path_name(cats: dict[int, Category], cid: int | None) -> str:
    parts, seen = [], set()
    while cid is not None and cid in cats and cid not in seen:
        seen.add(cid)
        parts.append(cats[cid].name)
        cid = cats[cid].parent_id
    return " › ".join(reversed(parts))


def effective(t: Tenant, p: Product, resolved: dict[int, tuple[Decimal | None, int | None]]) -> tuple[Decimal, str, int | None]:
    """(margen, origen, categoria) con la prioridad producto -> categoria -> general."""
    if p.margin_pct is not None and d(p.margin_pct) > 0:
        return d(p.margin_pct), "producto", None
    m, src = resolved.get(p.category_id, (None, None)) if p.category_id else (None, None)
    if m is not None:
        return m, "categoria", src
    return pricing.default_margin(t), "general", None


def target_map(db: Session, tid: int) -> dict[int, Decimal]:
    """categoria -> margen que rige por la cadena de categorias (solo las que tienen alguno). Para el guardian de margen."""
    return {cid: m for cid, (m, _src) in resolve(categories(db, tid)).items() if m is not None}


def _governed_by(cats: dict[int, Category], cid: int, root: int) -> int | None:
    """Categoria que define el margen de un producto de `cid` dentro del arbol de `root`, sin contar a `root`: la
    subcategoria con margen propio mas cercana, o None si manda `root`."""
    cur, seen = cid, set()
    while cur is not None and cur != root and cur in cats and cur not in seen:
        seen.add(cur)
        if cats[cur].margin_pct is not None:
            return cur
        cur = cats[cur].parent_id
    return None


def _price(db: Session, p: Product, margin: Decimal, fx: Decimal | None) -> Decimal:
    cost_cur = p.cost_currency or "USD"
    return pricing.sale_price(db, d(p.cost), cost_cur, p.currency or "CRC", margin, fx if cost_cur == "USD" else None)


def _token(cid: int, margin: Decimal, include_own: bool, rows: list[dict]) -> str:
    raw = json.dumps(
        [cid, str(margin), include_own, [(r["id"], str(r["price_after"]) if r["price_after"] is not None else None, r["clears_own"]) for r in rows]],
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def plan(db: Session, t: Tenant, cid: int, margin: Decimal, *, include_own: bool = False) -> dict:
    """Vista previa: que pasa con cada producto activo de la categoria (y sus subcategorias) si su margen es `margin`."""
    from .fx import FxMissing
    from .supplier_catalog import to_crc, usd_rate

    margin = d(margin)
    if not (MIN_PCT < margin < MAX_PCT):
        raise ValueError("El margen va de 0 % a 95 %")
    cats = categories(db, t.id)
    if cid not in cats:
        raise LookupError("Categoría no encontrada")
    tree = subtree(cats, cid)
    prods = list(
        db.scalars(
            select(Product).where(Product.tenant_id == t.id, Product.active, Product.category_id.in_(tree)).order_by(Product.name).limit(MAX_PRODUCTS + 1)
        )
    )
    if len(prods) > MAX_PRODUCTS:
        raise ValueError(f"La categoría tiene más de {MAX_PRODUCTS} productos: aplique el margen por subcategoría")
    fx = usd_rate(db)
    rows: list[dict] = []
    counts = {"cambian": 0, "sin_cambio": 0, "sin_costo": 0, "margen_propio": 0, "por_subcategoria": 0, "sin_tasa": 0}
    for p in prods:
        own = _pct(p.margin_pct) if p.margin_pct is not None and d(p.margin_pct) > 0 else None
        sub = _governed_by(cats, p.category_id, cid)
        antes = d(p.price or 0)
        nuevo, motivo, clears_own = None, None, False
        if sub is not None:
            motivo = f"Usa el margen de la subcategoría {cats[sub].name} ({pct_txt(cats[sub].margin_pct)} %)"
            counts["por_subcategoria"] += 1
        elif own is not None and not include_own:
            motivo = f"Tiene margen propio ({pct_txt(own)} %)"
            counts["margen_propio"] += 1
        else:
            clears_own = own is not None
            if p.cost is None or d(p.cost) <= 0:
                motivo = "Sin costo: no se puede calcular el precio"
                counts["sin_costo"] += 1
            else:
                try:
                    nuevo = _price(db, p, margin, fx)
                except FxMissing:
                    motivo = "Sin tipo de cambio del día"
                    counts["sin_tasa"] += 1
                if nuevo is not None and nuevo == antes:
                    motivo, nuevo = "Ya tiene ese precio", None
                    counts["sin_cambio"] += 1
                elif nuevo is not None:
                    counts["cambian"] += 1
        cost_crc = to_crc(p.cost, p.cost_currency, fx) if p.cost is not None else None

        def m_of(v, p=p, cost_crc=cost_crc):
            v_crc = to_crc(v, p.currency, fx) if v is not None else None
            return pricing.margin_of(v_crc, cost_crc) if v_crc and cost_crc else None

        rows.append({
            "id": p.id, "code": p.code, "name": p.name, "category": path_name(cats, p.category_id), "currency": p.currency or "CRC",
            "price_before": antes, "price_after": nuevo, "cost_crc": cost_crc, "margin_before": m_of(antes), "margin_after": m_of(nuevo),
            "own_margin": own, "clears_own": clears_own, "web_price": d(p.web_price) if p.web_price is not None else None,
            "show_on_web": bool(p.show_on_web), "skip": motivo,
        })  # fmt: skip
    from .discounts import min_margin

    return {
        "category": {"id": cid, "name": cats[cid].name, "path": path_name(cats, cid), "margin_pct": _pct(cats[cid].margin_pct)},
        "margin_pct": margin,
        "include_own": include_own,
        "total": len(rows),
        "counts": counts,
        "own_clears": sum(1 for r in rows if r["clears_own"]),
        "below_min": margin < min_margin(t),
        "min_margin_pct": min_margin(t),
        "items": rows,
        "token": _token(cid, margin, include_own, rows),
    }


def apply(db: Session, t: Tenant, cid: int, margin: Decimal, *, include_own: bool, token: str, user_id: int | None, ip: str | None = None) -> dict:
    """Guarda el margen de la categoria y aplica EXACTAMENTE la vista previa confirmada. Sin commit."""
    from . import margin_guard, price_history
    from . import store_showcase as sv
    from .documents import audit

    price_history.set_source(db, "lote")  # historial de precios (O0-07): un precio para toda la categoria
    pl = plan(db, t, cid, margin, include_own=include_own)
    if pl["token"] != token:
        raise Stale("Los precios cambiaron desde la vista previa (costo, tipo de cambio o catálogo). Revísela de nuevo.")
    cat = db.get(Category, cid)
    before_cat = _pct(cat.margin_pct)
    cat.margin_pct = d(margin)
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([r["id"] for r in pl["items"]])))}
    cambios, liberados = [], []
    for r in pl["items"]:
        p = prods[r["id"]]
        if r["clears_own"]:
            p.margin_pct = None  # deja de tener margen propio: hereda el de la categoria
            liberados.append(p.id)
        if r["price_after"] is not None:
            p.price = r["price_after"]
            cambios.append((p.id, p.code, str(r["price_before"]), str(r["price_after"])))
            try:
                margin_guard.recheck(db, t, p)
            except Exception:  # noqa: BLE001  guardar nunca falla por el guardian
                log.exception("guardian de margen tras el margen de categoria, producto %s", p.id)
    if cambios:
        sv.bump_catalog_version(db, t)
    audit(
        db, t.id, user_id, "category_margin", "category", cid,
        {"categoria": cat.name, "margen": str(margin), "antes": str(before_cat) if before_cat is not None else None,
         "incluye_propios": include_own, "n": len(cambios), "sin_margen_propio": liberados, "cambios": cambios},
        ip=ip,
    )  # fmt: skip
    return {"changes": len(cambios), "own_cleared": len(liberados), "category": pl["category"] | {"margin_pct": d(margin)}}


def clear(db: Session, t: Tenant, cid: int, *, user_id: int | None, ip: str | None = None) -> dict:
    """Quita el margen de la categoria: sus productos vuelven a heredar el de la categoria padre o el general. No
    cambia ningun precio. Sin commit."""
    from .documents import audit

    cat = db.get(Category, cid)
    if not cat or cat.tenant_id != t.id:
        raise LookupError("Categoría no encontrada")
    antes = _pct(cat.margin_pct)
    cat.margin_pct = None
    audit(db, t.id, user_id, "category_margin_clear", "category", cid, {"categoria": cat.name, "antes": str(antes) if antes is not None else None}, ip=ip)
    return {"ok": True}


def overview(db: Session, t: Tenant) -> dict:
    """Lista de categorias para la pantalla de margenes: margen propio, el que rige (y de donde sale) y cuantos
    productos activos tiene (directos y con subcategorias), cuantos con margen propio y cuantos sin costo."""
    from sqlalchemy import case, func

    cats = categories(db, t.id)
    res = resolve(cats)
    general = pricing.default_margin(t)
    stats: dict[int | None, dict[str, int]] = {}
    q = (
        select(
            Product.category_id,
            func.count(Product.id),
            func.count(Product.margin_pct),
            func.sum(case((Product.cost.is_(None), 1), else_=0)),
        )
        .where(Product.tenant_id == t.id, Product.active)
        .group_by(Product.category_id)
    )
    for cid, n, own, sin_costo in db.execute(q):
        stats[cid] = {"n": int(n or 0), "own": int(own or 0), "sin_costo": int(sin_costo or 0)}
    rows = []
    for cid, c in cats.items():
        tree = subtree(cats, cid)
        tot = {k: sum(stats.get(x, {}).get(k, 0) for x in tree) for k in ("n", "own", "sin_costo")}
        m, src = res[cid]
        rows.append({
            "id": cid, "name": c.name, "path": path_name(cats, cid), "parent_id": c.parent_id,
            "margin_pct": _pct(c.margin_pct),
            "effective_pct": m if m is not None else general,
            "source": "propio" if src == cid else ("heredado" if src is not None else "general"),
            "source_name": cats[src].name if src is not None and src != cid else None,
            "products": stats.get(cid, {}).get("n", 0), "products_tree": tot["n"], "own_margin": tot["own"], "sin_costo": tot["sin_costo"],
        })  # fmt: skip
    rows.sort(key=lambda r: r["path"].lower())
    sin_cat = stats.get(None, {"n": 0})["n"]
    return {"general_pct": general, "categories": rows, "uncategorized": sin_cat}


def listing(db: Session, t: Tenant, cid: int, q: str | None = None) -> dict:
    """Productos activos de la categoria (con subcategorias) con el margen que rige cada uno y de donde sale."""
    from .supplier_catalog import to_crc, usd_rate

    cats = categories(db, t.id)
    if cid not in cats:
        raise LookupError("Categoría no encontrada")
    res = resolve(cats)
    stmt = select(Product).where(Product.tenant_id == t.id, Product.active, Product.category_id.in_(subtree(cats, cid)))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Product.name.ilike(like) | Product.code.ilike(like) | Product.model.ilike(like))
    prods = list(db.scalars(stmt.order_by(Product.name).limit(MAX_PRODUCTS)))
    fx = usd_rate(db)
    items = []
    for p in prods:
        m, origen, src = effective(t, p, res)
        cost_crc = to_crc(p.cost, p.cost_currency, fx) if p.cost is not None else None
        price_crc = to_crc(p.price, p.currency, fx) if p.price is not None else None
        items.append({
            "id": p.id, "code": p.code, "name": p.name, "category": path_name(cats, p.category_id), "currency": p.currency or "CRC",
            "price": d(p.price or 0), "cost_crc": cost_crc,
            "margin_real": pricing.margin_of(price_crc, cost_crc) if price_crc and cost_crc else None,
            "margin_pct": m, "source": origen, "source_name": cats[src].name if src is not None else None,
            "show_on_web": bool(p.show_on_web),
        })  # fmt: skip
    return {"category": {"id": cid, "name": cats[cid].name, "path": path_name(cats, cid)}, "items": items}
