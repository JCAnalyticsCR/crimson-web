"""Vitrina de la tienda propia: portada administrable, catalogo con filtros, ficha, configurador, asesoria y SEO.

Lo publico vive bajo /public/store/{slug}/... (mismo criterio que store.py: solo tiendas publicadas) y bajo
/public/seo/... (lo que nginx pide por dominio: metas por ruta via SSI, robots.txt, sitemap.xml y redirecciones
301 de las direcciones viejas de Fygaro). Carrito, checkout y pedidos NO viven aqui (store.py / modulo de pedidos).
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import SlidingWindow
from ..models import Category, Opportunity, Product, ProductTax, Tenant
from ..services import store_showcase as sv
from ..services.documents import audit
from ..services.mail import notify_roles
from .auth import client_ip
from .public_incidents import _CONTROL, _buscar_cliente
from .store import STORE_DEFAULTS, Pricer, _gross, _prod_pub, _stock_of, _tenant, _variants, store_cfg

router = APIRouter(tags=["tienda-vitrina"])

# endpoint abierto a internet: tope por IP y global (misma clase que incidencias y accesos)
advisory_limiter = SlidingWindow(limit=5, seconds=3600)
advisory_global_limiter = SlidingWindow(limit=150, seconds=3600)
kit_limiter = SlidingWindow(limit=60, seconds=600)  # el configurador solo calcula, pero recorre el catalogo


def reset_limiters() -> None:
    for lim in (advisory_limiter, advisory_global_limiter, kit_limiter):
        lim.reset()


# ================================================================ admin (Mi Tienda -> Vitrina)
_LINK = r"^(/[^\s]*|https://[^\s]+)?$"


class BannerIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(default="", max_length=40)
    image: str = Field(default="", max_length=500, pattern=r"^(|/[^\s]*|https://[^\s]+)$")
    title: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=300)
    link: str = Field(default="", max_length=300, pattern=_LINK)
    cta: str = Field(default="", max_length=40)
    position: int = 0
    active: bool = True


class TrustIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    icon: str = Field(default="shield", pattern="^(shield|tool|truck|doc|chat|clock)$")
    title: str = Field(max_length=60)
    text: str = Field(default="", max_length=200)


class KitIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(max_length=40, pattern=r"^[a-z0-9-]{1,40}$")
    title: str = Field(max_length=80)
    text: str = Field(default="", max_length=200)
    answers: dict = Field(default_factory=dict)
    active: bool = True


class FaqIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    q: str = Field(max_length=200)
    a: str = Field(max_length=2000)


class ShowcaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    headline: str | None = Field(None, max_length=120)
    subline: str | None = Field(None, max_length=300)
    seo_description: str | None = Field(None, max_length=300)
    ga4_id: str | None = Field(None, pattern=r"^(|G-[A-Z0-9]{4,16})$")
    banners: list[BannerIn] | None = Field(None, max_length=12)
    featured: list[int] | None = Field(None, max_length=24)
    kits: list[KitIn] | None = Field(None, max_length=8)
    trust: list[TrustIn] | None = Field(None, max_length=6)
    faq: list[FaqIn] | None = Field(None, max_length=40)
    pages: dict[str, str] | None = None

    @field_validator("pages")
    @classmethod
    def _pages(cls, v):
        if v is None:
            return v
        bad = set(v) - {"envios", "garantias"}
        if bad:
            raise ValueError(f"Página desconocida: {', '.join(sorted(bad))}")
        return {k: str(x)[:20000] for k, x in v.items()}


def _admin_out(t: Tenant, db: Session) -> dict:
    cfg = store_cfg(t)
    sc = sv.showcase_cfg(cfg)
    ids = sc["featured"]
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.id.in_(ids)))} if ids else {}
    return {
        **sc,
        "legal": cfg.get("legal") or {},
        "featured_products": [{"id": i, "name": prods[i].name, "code": prods[i].code, "show_on_web": prods[i].show_on_web} for i in ids if i in prods],
        "slug": t.slug,
        "domain": cfg.get("domain"),
    }


@router.get("/store/showcase")
def showcase_get(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return _admin_out(db.get(Tenant, p.tenant.id), db)


@router.put("/store/showcase")
def showcase_put(data: ShowcaseIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    t = db.get(Tenant, p.tenant.id)
    st = {**(t.settings or {})}
    store = {**STORE_DEFAULTS, **(st.get("store") or {})}
    sc = dict(store.get("showcase") or {})
    patch = data.model_dump(exclude_unset=True)
    if "featured" in patch:
        own = set(db.scalars(select(Product.id).where(Product.tenant_id == t.id, Product.id.in_(patch["featured"] or [])))) if patch["featured"] else set()
        patch["featured"] = [i for i in dict.fromkeys(patch["featured"] or []) if i in own]
    if "banners" in patch:
        patch["banners"] = [{**b, "id": b.get("id") or f"b{i + 1}"} for i, b in enumerate(patch["banners"] or [])]
    if "pages" in patch:
        patch["pages"] = {**(sc.get("pages") or {}), **(patch["pages"] or {})}
    sc.update(patch)
    store["showcase"] = sc
    st["store"] = store
    t.settings = st
    audit(db, t.id, p.user.id, "update", "store_showcase", t.id, {"campos": sorted(patch)}, ip=p.ip)
    db.commit()
    return _admin_out(t, db)


class ProductShowcaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    compare_price: Decimal | None = Field(None, ge=0)
    datasheet_url: str | None = Field(None, max_length=500, pattern=r"^(|/[^\s]*|https://[^\s]+)$")
    specs: dict[str, str] | None = None

    @field_validator("specs")
    @classmethod
    def _specs(cls, v):
        if v is None:
            return v
        if len(v) > 30:
            raise ValueError("Máximo 30 especificaciones")
        return {str(k).strip()[:40]: str(x).strip()[:120] for k, x in v.items() if str(k).strip() and str(x).strip()}


def _own_product(db: Session, tid: int, pid: int) -> Product:
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != tid:
        raise HTTPException(404, "Producto no encontrado")
    return pr


@router.get("/store/showcase/products/{pid}")
def showcase_product_get(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    pr = _own_product(db, p.tenant.id, pid)
    return {
        "id": pr.id,
        "name": pr.name,
        "code": pr.code,
        "price": pr.price,
        "currency": pr.currency,
        "compare_price": pr.compare_price,
        "datasheet_url": pr.datasheet_url,
        "specs": pr.specs or {},
        "derived_specs": sv.derive_specs(pr),
        "path": sv.product_path(pr),
    }


@router.put("/store/showcase/products/{pid}")
def showcase_product_put(pid: int, data: ProductShowcaseIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    pr = _own_product(db, p.tenant.id, pid)
    patch = data.model_dump(exclude_unset=True)
    if "compare_price" in patch:
        pr.compare_price = patch["compare_price"] or None
    if "datasheet_url" in patch:
        pr.datasheet_url = patch["datasheet_url"] or None
    if "specs" in patch:
        pr.specs = patch["specs"] or None
    audit(db, p.tenant.id, p.user.id, "update_showcase", "product", pr.id, {"campos": sorted(patch)}, ip=p.ip)
    db.commit()
    return showcase_product_get(pid, p, db)


# ================================================================ publico
pub = APIRouter(prefix="/public/store/{slug}", tags=["publico"])


def _published(db: Session, tid: int) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(Product.tenant_id == tid, Product.active, Product.show_on_web)
            .options(selectinload(Product.taxes).selectinload(ProductTax.tax))
            .order_by(Product.name)
        )
    )


def _categories(db: Session, tid: int) -> list[Category]:
    return list(db.scalars(select(Category).where(Category.tenant_id == tid, Category.show_on_web).order_by(Category.name)))


def _avail(base: dict, p: Product, own: Decimal | dict) -> dict:
    """Disponible / Ultimas unidades / Bajo pedido / Consultar. Es el respaldo: si existe el endpoint de inventario
    (/availability), el navegador lo usa y este dato solo cubre mientras carga.
    `own` es la existencia propia (Decimal) o, desde la integracion con el inventario, la fila ya calculada por
    services/store_inventory (dict con "cantidad" solo si Ajustes publica la cantidad exacta y "ultimas")."""
    st = base.get("state")
    if st == "servicio":
        return {"state": "servicio", "label": "Servicio", "max_qty": None}
    if st == "en_bodega":
        if isinstance(own, dict):
            qty = own.get("cantidad")
            limit = int(qty) if qty is not None and not (p.supplier_stock or 0) else None
            last = bool(own.get("ultimas")) or (qty is not None and qty <= 2)
        else:
            limit = int(own) if not (p.supplier_stock or 0) else None  # sin respaldo del proveedor: no vender mas de lo que hay
            last = own <= 2
        if last:
            return {"state": "ultimas", "label": "Últimas unidades", "max_qty": limit}
        return {"state": "disponible", "label": "Disponible", "max_qty": limit}
    if st == "bajo_pedido":
        return {"state": "bajo_pedido", "label": "Bajo pedido · 3 a 5 días hábiles", "max_qty": None}
    return {"state": "consultar", "label": "Consultar disponibilidad", "max_qty": None}


class Ctx:
    """Lo que toda respuesta publica necesita una sola vez por pedido."""

    def __init__(self, db: Session, t: Tenant):
        self.db, self.t = db, t
        self.cfg = store_cfg(t)
        self.pricer = Pricer(db, t)
        self.cats = _categories(db, t.id)
        self.cat_name = {c.id: c.name for c in self.cats}
        self.cat_slug = sv.category_slugs(self.cats)

    def card(self, p: Product, own: Decimal, variants: list | None = None, full: bool = False) -> dict:
        out = _prod_pub(p, variants, own, self.pricer, self.cat_name, compact=not full)
        price = Decimal(str(out["display_price"]))
        cmp = None
        if p.compare_price:
            c = _gross(self.pricer(p.compare_price, p.currency), out["tax_rate"])
            cmp = c if c > price else None
        out.update(
            {
                "path": sv.product_path(p),
                "model": p.model,
                "type": sv.product_type(p),
                "compare_display_price": cmp,
                "price_on_request": price <= 0,
                "category_path": f"/c/{self.cat_slug[p.category_id]}" if p.category_id in self.cat_slug else None,
                "availability": _avail(out["availability"], p, own),
            }
        )
        if not full:
            out["images"] = out["images"][:2]
        return out


def _stock_map(db: Session, tid: int, prods: list[Product]) -> dict[int, Decimal]:
    return _stock_of(db, tid, [p.id for p in prods]) if prods else {}


def _cat_tree(ctx: Ctx, counts: dict[int, int], images: dict[int, str]) -> list[dict]:
    kids: dict[int | None, list[Category]] = {}
    for c in ctx.cats:
        kids.setdefault(c.parent_id if c.parent_id in ctx.cat_name else None, []).append(c)

    def total(c: Category) -> int:
        return counts.get(c.id, 0) + sum(total(k) for k in kids.get(c.id, []))

    def node(c: Category) -> dict:
        subs = [node(k) for k in kids.get(c.id, []) if total(k)]
        return {
            "id": c.id,
            "name": c.name,
            "slug": ctx.cat_slug[c.id],
            "path": f"/c/{ctx.cat_slug[c.id]}",
            "count": total(c),
            "image": images.get(c.id),
            "children": subs,
        }

    return sorted((node(c) for c in kids.get(None, []) if total(c)), key=lambda n: -n["count"])


def _descendants(ctx: Ctx, cid: int) -> set[int]:
    out, todo = {cid}, [cid]
    while todo:
        cur = todo.pop()
        for c in ctx.cats:
            if c.parent_id == cur and c.id not in out:
                out.add(c.id)
                todo.append(c.id)
    return out


def _created(p: Product) -> float:
    """marca de tiempo comparable (SQLite devuelve fechas sin zona)"""
    c = p.created_at
    if c is None:
        return 0.0
    return (c if c.tzinfo else c.replace(tzinfo=UTC)).timestamp()


def _rank_default(p: Product) -> tuple:
    """'Relevancia' sin busqueda: primero lo que se puede comprar ya (con foto y con precio)."""
    return (not p.images, not (p.price and Decimal(str(p.price)) > 0), p.name.lower())


@pub.get("/site")
def site(slug: str, response: Response, db: Session = Depends(get_db)):
    """Datos livianos que toda vista necesita: por ahora la medicion (GA4), que el navegador solo activa con
    consentimiento explicito de la persona."""
    t = _tenant(db, slug)
    sc = sv.showcase_cfg(store_cfg(t))
    response.headers["Cache-Control"] = "public, max-age=300"
    return {"analytics": {"ga4_id": sc["ga4_id"] or None}}


@pub.get("/showcase")
def showcase_public(slug: str, response: Response, db: Session = Depends(get_db)):
    """Portada de la tienda en un solo viaje: banners, categorias con conteo, destacados, novedades, kits y sellos."""
    t = _tenant(db, slug)
    ctx = Ctx(db, t)
    sc = sv.showcase_cfg(ctx.cfg)
    prods = _published(db, t.id)
    counts: dict[int, int] = {}
    images: dict[int, str] = {}
    brands: dict[str, int] = {}
    for p in prods:
        if p.category_id:
            counts[p.category_id] = counts.get(p.category_id, 0) + 1
            if p.category_id not in images and p.images and p.price and Decimal(str(p.price)) > 0:
                images[p.category_id] = (p.images[0] or {}).get("url")
        if p.brand:
            brands[p.brand] = brands.get(p.brand, 0) + 1
    by_id = {p.id: p for p in prods}
    featured = [by_id[i] for i in sc["featured"] if i in by_id]
    if not featured:  # sin eleccion de Andres: uno por tipo de equipo, con foto y precio
        featured = sv.default_featured(prods)
    fresh = [p for p in sorted(prods, key=lambda p: (_created(p), p.id), reverse=True) if p.images and p not in featured][:8]
    shown = featured[:8] + fresh
    stock = _stock_map(db, t.id, shown)
    kits = []
    for k in sc["kits"]:
        if not k.get("active", True):
            continue
        kit = sv.build_kit(prods, k.get("answers") or {})
        lines = [ln for ln in kit["lines"] if Decimal(str(ln["product"].price or 0)) > 0]
        total = sum(
            (
                _gross(ctx.pricer(ln["product"].price, ln["product"].currency), float(ln["product"].taxes[0].tax.rate) if ln["product"].taxes else 13)
                * ln["quantity"]
                for ln in lines
            ),
            Decimal(0),
        )
        cam = next((ln["product"] for ln in lines if ln["role"].startswith("Cámaras")), None)
        kits.append(
            {
                "id": k.get("id"),
                "title": k.get("title"),
                "text": k.get("text"),
                "answers": kit["answers"],
                "items": sum(ln["quantity"] for ln in lines),
                "total": total.quantize(Decimal("1")) if ctx.pricer.currency == "CRC" else total.quantize(Decimal("0.01")),
                "image": ((cam.images or [{}])[0].get("url") if cam and cam.images else None),
                "cameras": kit["cameras"],
            }
        )
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "headline": sc["headline"],
        "subline": sc["subline"],
        "banners": sorted([b for b in sc["banners"] if b.get("active", True) and (b.get("image") or b.get("title"))], key=lambda b: b.get("position", 0)),
        "trust": sc["trust"],
        "categories": _cat_tree(ctx, counts, images),
        "brands": [{"name": b, "count": n} for b, n in sorted(brands.items(), key=lambda x: -x[1])[:14]],
        "featured": [ctx.card(p, stock.get(p.id, Decimal(0))) for p in featured[:8]],
        "fresh": [ctx.card(p, stock.get(p.id, Decimal(0))) for p in fresh],
        "kits": kits,
        "product_count": len(prods),
        "currency": ctx.pricer.currency,
        "info": [
            {"kind": k, "title": v, "path": f"/info/{k}"}
            for k, v in sv.INFO_KINDS.items()
            if k not in ("terminos", "privacidad") or (ctx.cfg.get("legal") or {}).get("privacy" if k == "privacidad" else "terms")
        ],
    }


SORTS = "^(relevance|name|price_asc|price_desc|recent)$"


@pub.get("/catalog")
def catalog(
    slug: str,
    response: Response,
    q: str | None = Query(None, max_length=120),
    category: str | None = Query(None, max_length=80),
    brand: list[str] | None = Query(None),
    type: str | None = Query(None, max_length=60),
    min_price: Decimal | None = Query(None, ge=0),
    max_price: Decimal | None = Query(None, ge=0),
    avail: str | None = Query(None, pattern="^(disponible|bajo_pedido|inmediata)$"),
    sort: str = Query("relevance", pattern=SORTS),
    ids: str | None = Query(None, max_length=200),
    limit: int = Query(24, ge=1, le=60),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Catalogo con filtros (categoria con sus subcategorias, marca, tipo, precio con IVA, disponibilidad), orden y
    paginacion. Devuelve tambien las facetas calculadas sobre el resultado (sin el filtro de la propia faceta)."""
    t = _tenant(db, slug)
    ctx = Ctx(db, t)
    prods = _published(db, t.id)
    stock_all = _stock_map(db, t.id, prods)
    if ids:
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()][:12]
        by_id = {p.id: p for p in prods}
        rows = [by_id[i] for i in want if i in by_id]
        return {"items": [ctx.card(p, stock_all.get(p.id, Decimal(0)), full=True) | {"specs": sv.derive_specs(p)} for p in rows], "total": len(rows)}

    all_counts: dict[int, int] = {}
    for p in prods:
        if p.category_id:
            all_counts[p.category_id] = all_counts.get(p.category_id, 0) + 1
    nav = [{"name": c["name"], "path": c["path"], "count": c["count"]} for c in _cat_tree(ctx, all_counts, {})]

    cat_obj = None
    if category:
        cid = next((i for i, s in ctx.cat_slug.items() if s == category), None) or (
            int(category) if category.isdigit() and int(category) in ctx.cat_name else None
        )
        if not cid:
            raise HTTPException(404, "Categoría no encontrada")
        cat_obj = next(c for c in ctx.cats if c.id == cid)
        allowed = _descendants(ctx, cid)
        prods = [p for p in prods if p.category_id in allowed]

    terms = sv.split_terms(q or "")
    scores: dict[int, int] = {}
    if terms:
        for p in prods:
            s = sv.search_score(p, ctx.cat_name.get(p.category_id), terms)
            if s:
                scores[p.id] = s
        prods = [p for p in prods if p.id in scores]

    disp = {p.id: _gross(ctx.pricer(p.price, p.currency), float(p.taxes[0].tax.rate) if p.taxes else 13) for p in prods}
    av = {p.id: _avail(_avail_base(p, stock_all.get(p.id, Decimal(0))), p, stock_all.get(p.id, Decimal(0)))["state"] for p in prods}
    types = {p.id: sv.product_type(p) for p in prods}
    brands_sel = {b.lower() for b in (brand or []) if b}

    def keep(p: Product, skip: str = "") -> bool:
        if skip != "brand" and brands_sel and (p.brand or "").lower() not in brands_sel:
            return False
        if skip != "type" and type and types[p.id] != type:
            return False
        if skip != "price":
            if min_price is not None and disp[p.id] < min_price:
                return False
            if max_price is not None and (disp[p.id] > max_price or disp[p.id] <= 0):
                return False
        if skip != "avail" and avail:
            st = av[p.id]
            if avail in ("disponible", "inmediata") and st not in ("disponible", "ultimas"):
                return False
            if avail == "bajo_pedido" and st not in ("disponible", "ultimas", "bajo_pedido"):
                return False
        return True

    rows = [p for p in prods if keep(p)]
    facet_brand: dict[str, int] = {}
    for p in prods:
        if p.brand and keep(p, "brand"):
            facet_brand[p.brand] = facet_brand.get(p.brand, 0) + 1
    facet_type: dict[str, int] = {}
    for p in prods:
        if types[p.id] and keep(p, "type"):
            facet_type[types[p.id]] = facet_type.get(types[p.id], 0) + 1
    facet_av = {"disponible": 0, "bajo_pedido": 0}
    for p in prods:
        if keep(p, "avail"):
            st = av[p.id]
            if st in ("disponible", "ultimas"):
                facet_av["disponible"] += 1
            if st in ("disponible", "ultimas", "bajo_pedido"):
                facet_av["bajo_pedido"] += 1
    priced = [disp[p.id] for p in prods if keep(p, "price") and disp[p.id] > 0]

    if sort == "relevance":
        rows.sort(key=lambda p: (-scores.get(p.id, 0), _rank_default(p)))
    elif sort == "name":
        rows.sort(key=lambda p: p.name.lower())
    elif sort in ("price_asc", "price_desc"):
        con = sorted([p for p in rows if disp[p.id] > 0], key=lambda p: disp[p.id], reverse=sort == "price_desc")
        rows = con + [p for p in rows if disp[p.id] <= 0]  # "precio a consultar" siempre al final
    else:
        rows.sort(key=lambda p: (_created(p), p.id), reverse=True)

    total = len(rows)
    page = rows[offset : offset + limit]
    vmap = _variants(db, [p.id for p in page])
    response.headers["X-Total-Count"] = str(total)
    return {
        "items": [ctx.card(p, stock_all.get(p.id, Decimal(0)), vmap.get(p.id)) for p in page],
        "total": total,
        "offset": offset,
        "category": (
            {
                "id": cat_obj.id,
                "name": cat_obj.name,
                "slug": ctx.cat_slug[cat_obj.id],
                "description": cat_obj.description,
                "parent": (
                    {"name": ctx.cat_name[cat_obj.parent_id], "path": f"/c/{ctx.cat_slug[cat_obj.parent_id]}"} if cat_obj.parent_id in ctx.cat_name else None
                ),
                "children": [{"name": c.name, "path": f"/c/{ctx.cat_slug[c.id]}"} for c in ctx.cats if c.parent_id == cat_obj.id],
            }
            if cat_obj
            else None
        ),
        "facets": {
            "brands": [{"name": b, "count": n} for b, n in sorted(facet_brand.items(), key=lambda x: (-x[1], x[0]))[:30]],
            "types": [{"name": k, "count": n} for k, n in sorted(facet_type.items(), key=lambda x: (-x[1], x[0]))],
            "availability": facet_av,
            "price": {"min": min(priced) if priced else 0, "max": max(priced) if priced else 0},
        },
        "currency": ctx.pricer.currency,
        "nav": nav,
    }


def _avail_base(p: Product, own) -> dict:
    if isinstance(own, dict):  # ya viene calculada por store_inventory (existencias reales, reservas y proveedor)
        return own
    from .catalog import store_availability

    return store_availability(p, own)


ACCESSORY_RULES = {
    # tipo de la ficha -> grupos sugeridos (titulo, tipos que lo cubren, motivo)
    "Cámara IP": [
        ("Grabador", ("Grabador NVR",), "Para grabar y ver las cámaras desde el celular."),
        ("Disco para el grabador", ("Disco duro",), "El grabador no trae disco: elegí la capacidad según los días de grabación."),
        ("Switch PoE", ("Switch PoE",), "Alimenta la cámara por el mismo cable de red."),
    ],
    "Cámara PTZ": [
        ("Grabador", ("Grabador NVR",), "Para grabar y controlar la PTZ."),
        ("Disco para el grabador", ("Disco duro",), "Capacidad según días de grabación."),
    ],
    "Cámara analógica": [
        ("Grabador DVR", ("Grabador DVR",), "Las cámaras analógicas graban en un DVR."),
        ("Disco para el grabador", ("Disco duro",), "El DVR no trae disco."),
    ],
    "Cámara WiFi": [("Memoria microSD", ("Memoria microSD",), "Graba en la propia cámara sin grabador.")],
    "Grabador NVR": [
        ("Disco duro de videovigilancia", ("Disco duro",), "El grabador no trae disco."),
        ("Cámaras IP", ("Cámara IP",), "Compatibles con el grabador."),
        ("Respaldo eléctrico", ("UPS",), "Sigue grabando si se va la luz."),
    ],
    "Grabador DVR": [
        ("Disco duro de videovigilancia", ("Disco duro",), "El grabador no trae disco."),
        ("Respaldo eléctrico", ("UPS",), "Sigue grabando si se va la luz."),
    ],
    "Disco duro": [("Grabador", ("Grabador NVR", "Grabador DVR"), "Donde va instalado el disco.")],
    "Switch PoE": [
        ("Respaldo eléctrico", ("UPS",), "Mantiene la red y las cámaras encendidas sin luz."),
        ("Conectores y cable", ("Conector", "Cable"), "Para armar el cableado."),
    ],
    "Punto de acceso WiFi": [("Switch PoE", ("Switch PoE", "Inyector PoE"), "Alimenta el punto de acceso por el cable de red.")],
    "Control de acceso": [("Cerradura", ("Cerradura",), "Lo que abre la puerta."), ("Respaldo eléctrico", ("UPS",), "La puerta sigue funcionando sin luz.")],
    "Videoportero": [("Cerradura", ("Cerradura",), "Para abrir desde la pantalla o la app.")],
}


@pub.get("/item/{ref}")
def item(slug: str, ref: str, response: Response, db: Session = Depends(get_db)):
    """Ficha por direccion amigable (/p/<nombre>-<id>): fotos, precio con desglose, especificaciones, ficha tecnica,
    relacionados (misma categoria y marca) y "¿Que necesito ademas?"."""
    t = _tenant(db, slug)
    pid = sv.product_id_from_ref(ref)
    p = db.get(Product, pid) if pid else None
    if not p or p.tenant_id != t.id or not p.show_on_web or not p.active:
        raise HTTPException(404, "Producto no encontrado")
    ctx = Ctx(db, t)
    prods = _published(db, t.id)
    ptype = sv.product_type(p)
    related = [r for r in prods if r.id != p.id and r.category_id == p.category_id and sv.product_type(r) == ptype and r.images]
    related += [r for r in prods if r.id != p.id and r not in related and r.category_id == p.category_id and r.images]
    related += [r for r in prods if r.id != p.id and r not in related and p.brand and r.brand == p.brand and r.images]
    related = sorted(related[:24], key=lambda r: (r.brand != p.brand, _rank_default(r)))[:8]
    groups = []
    for title, kinds, why in ACCESSORY_RULES.get(ptype, []):
        cands = [r for r in prods if r.id != p.id and sv.product_type(r) in kinds and r.price and Decimal(str(r.price)) > 0]
        cands.sort(key=lambda r: (r.brand != p.brand, not r.images, Decimal(str(r.price))))
        if cands:
            groups.append({"title": title, "reason": why, "items": cands[:3]})
    every = [p] + related + [r for g in groups for r in g["items"]]
    stock = _stock_map(db, t.id, every)
    out = ctx.card(p, stock.get(p.id, Decimal(0)), _variants(db, [p.id]).get(p.id), full=True)
    rate = Decimal(str(out["tax_rate"]))
    gross = Decimal(str(out["display_price"]))
    net = (gross / (1 + rate / 100)).quantize(Decimal("0.01"))
    crumbs = [{"name": "Tienda", "path": "/"}]
    if p.category_id in ctx.cat_name:
        chain, cur = [], next((c for c in ctx.cats if c.id == p.category_id), None)
        while cur is not None and len(chain) < 4:
            chain.insert(0, cur)
            cur = next((c for c in ctx.cats if c.id == cur.parent_id), None) if cur.parent_id else None
        crumbs += [{"name": c.name, "path": f"/c/{ctx.cat_slug[c.id]}"} for c in chain]
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        **out,
        "description": p.description_store or p.description_invoice,
        "specs": sv.derive_specs(p),
        "datasheet_url": p.datasheet_url,
        "net_price": net,
        "tax_amount": (gross - net).quantize(Decimal("0.01")),
        "breadcrumbs": crumbs,
        "related": [ctx.card(r, stock.get(r.id, Decimal(0))) for r in related],
        "accessories": [{"title": g["title"], "reason": g["reason"], "items": [ctx.card(r, stock.get(r.id, Decimal(0))) for r in g["items"]]} for g in groups],
        "whatsapp": ctx.cfg.get("whatsapp") or "",
    }


@pub.get("/info/{kind}")
def info(slug: str, kind: str, db: Session = Depends(get_db)):
    """Paginas propias: terminos y privacidad (los que trajo el importador de Fygaro), envios, garantias y FAQ."""
    t = _tenant(db, slug)
    if kind not in sv.INFO_KINDS:
        raise HTTPException(404, "Página no encontrada")
    cfg = store_cfg(t)
    sc = sv.showcase_cfg(cfg)
    legal = cfg.get("legal") or {}
    body = {"terminos": legal.get("terms"), "privacidad": legal.get("privacy")}.get(kind) or sc["pages"].get(kind) or ""
    if kind == "preguntas-frecuentes":
        return {"kind": kind, "title": sv.INFO_KINDS[kind], "body": "", "faq": sc["faq"]}
    if not str(body).strip():
        raise HTTPException(404, "Página no disponible")
    return {"kind": kind, "title": sv.INFO_KINDS[kind], "body": body, "faq": []}


class KitAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place: str = Field("casa", pattern="^(casa|negocio|condominio|bodega)$")
    indoor: int = Field(2, ge=0, le=32)
    outdoor: int = Field(2, ge=0, le=32)
    days: int = Field(15, ge=1, le=90)
    remote: bool = True

    @model_validator(mode="after")
    def _alguna(self):
        if self.indoor + self.outdoor == 0:
            raise ValueError("Indique al menos una cámara")
        if self.indoor + self.outdoor > 32:
            raise ValueError("Para más de 32 cámaras pida una asesoría")
        return self


@pub.post("/kit")
def kit(slug: str, data: KitAnswers, request: Request, db: Session = Depends(get_db)):
    """'Arma tu sistema': 4 respuestas -> kit con productos reales y cantidades de las reglas del levantamiento."""
    kit_limiter.hit(f"kit:{client_ip(request)}")
    t = _tenant(db, slug)
    ctx = Ctx(db, t)
    prods = _published(db, t.id)
    k = sv.build_kit(prods, data.model_dump())
    stock = _stock_map(db, t.id, [ln["product"] for ln in k["lines"]])
    lines, total = [], Decimal(0)
    for ln in k["lines"]:
        c = ctx.card(ln["product"], stock.get(ln["product"].id, Decimal(0)))
        sub = Decimal(str(c["display_price"])) * ln["quantity"]
        total += sub
        lines.append({"role": ln["role"], "reason": ln["reason"], "quantity": ln["quantity"], "subtotal": sub, "product": c})
    return {**{k_: v for k_, v in k.items() if k_ != "lines"}, "lines": lines, "total": total, "currency": ctx.pricer.currency}


# ---------------------------------------------------------------- asesoria personalizada -> oportunidad en el CRM
ADVISORY_MAX_BODY = 12 * 1024
ADVISORY_OK = {"ok": True, "message": "Recibimos su solicitud. Un asesor de Crimson le contacta en horario hábil."}


class AdvisoryLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(ge=1)
    quantity: int = Field(1, ge=1, le=999)


class AdvisoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=120)
    party: str = Field("particular", pattern="^(empresa|particular)$")
    company: str | None = Field(None, max_length=160)
    phone: str | None = Field(None, max_length=25, pattern=r"^[0-9+()\s.-]{7,25}$")
    email: EmailStr | None = Field(None, max_length=200)
    solution: str = Field(pattern="^(" + "|".join(sv.SOLUTIONS) + ")$")
    description: str = Field(min_length=10, max_length=3000)
    location: str | None = Field(None, max_length=200)
    environment: str | None = Field(None, pattern="^(" + "|".join(sv.ENVIRONMENTS) + ")$")
    timeline: str | None = Field(None, pattern="^(" + "|".join(sv.TIMELINES) + ")$")
    install: bool = False
    items: list[AdvisoryLine] = Field(default_factory=list, max_length=40)
    origin: str | None = Field(None, max_length=60, pattern=r"^[a-z0-9:_/-]*$")
    website: str | None = Field(None, max_length=200)  # honeypot: oculto en el formulario

    @field_validator("name", "company", "description", "location")
    @classmethod
    def _sin_control(cls, v):
        return _CONTROL.sub("", v) if isinstance(v, str) else v

    @field_validator("phone", "email", "company", "location", "website", "origin", "environment", "timeline", mode="before")
    @classmethod
    def _vacio_es_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _contacto(self):
        if not self.phone and not self.email:
            raise ValueError("Indique un teléfono o un correo para poder contactarle")
        if self.party == "empresa" and not self.company:
            raise ValueError("Indique el nombre de la empresa")
        return self


def _crear_oportunidad(db: Session, slug: str, data: AdvisoryIn, ip: str) -> None:
    t = _tenant(db, slug)
    email = str(data.email).lower() if data.email else None
    # doble envio (doble clic, reintento del navegador): misma persona y mismo texto en 10 minutos -> nada nuevo
    hace = datetime.now(UTC) - timedelta(minutes=10)
    recientes = db.scalars(
        select(Opportunity).where(Opportunity.tenant_id == t.id, Opportunity.source == "tienda web").order_by(Opportunity.id.desc()).limit(20)
    ).all()
    for o in recientes:
        c, cuando = o.contact or {}, o.created_at
        if cuando is not None and cuando.tzinfo is None:
            cuando = cuando.replace(tzinfo=UTC)
        if cuando and cuando < hace:
            continue
        mismo = (email and c.get("email") == email) or (data.phone and c.get("phone") == data.phone)
        if mismo and (o.notes or "").startswith(data.description[:200]):
            return
    ctx = Ctx(db, t)
    prods = (
        {
            p.id: p
            for p in db.scalars(
                select(Product).where(Product.tenant_id == t.id, Product.show_on_web, Product.active, Product.id.in_([i.product_id for i in data.items]))
            )
        }
        if data.items
        else {}
    )
    lineas, monto = [], Decimal(0)
    for it in data.items:
        p = prods.get(it.product_id)
        if not p:
            continue
        neto = ctx.pricer(p.price, p.currency) * it.quantity
        monto += neto
        lineas.append(f"• {it.quantity} × {p.name} ({p.code})")
    cliente = _buscar_cliente(db, t.id, email, data.phone)  # se liga si existe; no se crea cliente desde internet
    from ..services.sequences import next_number

    number, _ = next_number(db, t.id, "OPO")
    etiqueta = sv.SOLUTIONS[data.solution]
    quien = data.company if data.party == "empresa" and data.company else data.name
    notas = data.description
    ficha = [
        x
        for x in (f"Entorno: {sv.ENVIRONMENTS[data.environment]}" if data.environment else "", f"Plazo: {sv.TIMELINES[data.timeline]}" if data.timeline else "")
        if x
    ]
    if ficha:
        notas += "\n\n" + " · ".join(ficha)
    if lineas:
        notas += "\n\nEquipo de interés (desde la tienda):\n" + "\n".join(lineas)
    if data.install:
        notas += "\n\nSolicita instalación por Crimson."
    if data.origin:
        notas += f"\n\nOrigen: {data.origin}"
    o = Opportunity(
        tenant_id=t.id,
        number=number,
        customer_id=cliente.id if cliente else None,
        site=data.location,
        contact={
            "name": data.name,
            "company": data.company,
            "party": data.party,
            "phone": data.phone,
            "email": email,
            "environment": data.environment,
            "timeline": data.timeline,
        },
        title=f"Tienda web · {etiqueta} · {quien}"[:200],
        kind="venta" if lineas and not data.install else "proyecto",
        source="tienda web",
        solution=data.solution,
        amount=monto.quantize(Decimal("0.01")),
        currency=ctx.pricer.currency,
        probability=30,
        status="nuevo",
        next_action="Contactar al prospecto (solicitud desde la tienda)",
        next_action_date=date.today(),
        notes=notas,
    )
    db.add(o)
    db.flush()
    audit(db, t.id, None, "create_web", "opportunity", o.id, {"origen": data.origin or "asesoria", "ligado_a_cliente": bool(cliente)}, ip=ip)
    e = htmllib.escape
    cuerpo = (
        f"<p>Nueva solicitud de asesoría desde la tienda: <b>{e(number)}</b> · {e(etiqueta)}</p>"
        f"<p><b>{e(data.name)}</b>{' · ' + e(data.company) if data.company else ''} ({'empresa' if data.party == 'empresa' else 'particular'})<br>"
        f"{e(data.phone or '')} {e(email or '')}{'<br>Ubicación: ' + e(data.location) if data.location else ''}</p>"
        f"<blockquote>{e(data.description)}</blockquote>"
        + (f"<p>{e(' · '.join(ficha))}</p>" if ficha else "")
        + (f"<p>Equipo de interés:<br>{'<br>'.join(e(x) for x in lineas)}</p>" if lineas else "")
        + ("<p><b>Pide instalación.</b></p>" if data.install else "")
        + f"<p>{'Cliente registrado: ' + e(cliente.name) if cliente else 'Prospecto nuevo (sin ficha de cliente)'} · Oportunidades → {e(number)}</p>"
    )
    notify_roles(db, t, ("admin", "supervisor", "ventas"), f"Asesoría web {number} · {etiqueta} · {quien[:40]}", cuerpo, "opportunity", o.id)
    db.commit()


@pub.post("/asesoria", status_code=202)
async def advisory(slug: str, request: Request, db: Session = Depends(get_db)):
    """Solicitud de asesoria personalizada: crea una OPORTUNIDAD en el CRM (fuente "tienda web") y avisa a ventas.
    Mismas defensas que /public/incidents: tope de tamano leyendo el stream, validacion estricta, honeypot,
    limite por IP y global, y respuesta identica pase lo que pase."""
    ip = client_ip(request)
    advisory_limiter.hit(f"asesoria:{ip}")
    advisory_global_limiter.hit("asesoria:*")
    largo = request.headers.get("content-length")
    if largo and (not largo.isdigit() or int(largo) > ADVISORY_MAX_BODY):
        raise HTTPException(413, "La solicitud es demasiado larga")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > ADVISORY_MAX_BODY:
            raise HTTPException(413, "La solicitud es demasiado larga")
    try:
        data = AdvisoryIn.model_validate_json(raw or b"{}")
    except ValidationError as ex:
        errores = [{"campo": ".".join(str(x) for x in err["loc"]) or "general", "mensaje": err["msg"]} for err in ex.errors(include_input=False)]
        raise HTTPException(422, {"message": "Revise los datos del formulario", "errors": errores}) from None
    if data.website:
        return ADVISORY_OK  # honeypot: misma respuesta, nada creado
    await run_in_threadpool(_crear_oportunidad, db, slug, data, ip)
    return ADVISORY_OK


# ================================================================ SEO por dominio (nginx: SSI, robots, sitemap, 301)
seo = APIRouter(prefix="/public/seo", tags=["publico"])


def _tenant_by_host(db: Session, host: str | None) -> Tenant | None:
    h = (host or "").strip().lower().split(":")[0]
    if not h:
        return None
    for t in db.scalars(select(Tenant).where(Tenant.active)):
        cfg = store_cfg(t)
        dom = (cfg.get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        if cfg["published"] and dom and dom == h:
            return t
    return None


def _host(x_store_host: str | None, host: str | None) -> str:
    return (x_store_host or host or "").strip().lower().split(":")[0][:200]


def _base(host: str) -> str:
    return f"https://{host}"


def _abs(base: str, url: str) -> str:
    """WhatsApp y Google necesitan la foto con direccion completa (las del media del API vienen como /api/...)."""
    return f"{base}{url}" if url and url.startswith("/") and not url.startswith("//") else url


def seo_meta(db: Session, t: Tenant, base: str, path: str) -> dict:
    """Titulo, descripcion, canonica, Open Graph y JSON-LD de una ruta de la vitrina."""
    cfg = store_cfg(t)
    sc = sv.showcase_cfg(cfg)
    name = cfg["name"]
    path = "/" + (path or "").strip("/")
    parts = [x for x in path.split("/") if x]
    site_desc = (
        sc["seo_description"]
        or f"{name}: cámaras de seguridad, grabadores, redes, control de acceso y respaldo eléctrico en Costa Rica. Precios con IVA, factura electrónica, soporte técnico local e instalación."
    )
    org = {
        "@type": "Organization",
        "@id": base + "/#org",
        "name": name,
        "url": base + "/",
        **({"logo": cfg["logo_url"]} if cfg.get("logo_url") and str(cfg["logo_url"]).startswith("http") else {}),
        **({"telephone": "+" + re.sub(r"\D", "", cfg["whatsapp"])} if cfg.get("whatsapp") else {}),
        "address": {"@type": "PostalAddress", "addressCountry": "CR"},
    }
    meta = {"site_name": name, "robots": "index,follow", "description": site_desc}
    if not parts:
        meta |= {
            "title": f"{name} · Tienda de seguridad electrónica y redes en Costa Rica",
            "canonical": base + "/",
            "ld": {
                "@context": "https://schema.org",
                "@graph": [
                    org,
                    {
                        "@type": "WebSite",
                        "url": base + "/",
                        "name": f"Tienda {name}",
                        "inLanguage": "es-CR",
                        "publisher": {"@id": base + "/#org"},
                        "potentialAction": {
                            "@type": "SearchAction",
                            "target": base + "/buscar?q={search_term_string}",
                            "query-input": "required name=search_term_string",
                        },
                    },
                ],
            },
        }
        return meta
    head = parts[0]
    if head == "p" and len(parts) > 1:
        pid = sv.product_id_from_ref(parts[1])
        p = db.get(Product, pid) if pid else None
        if not p or p.tenant_id != t.id or not p.show_on_web or not p.active:
            return meta | {"title": f"Producto no disponible · {name}", "robots": "noindex,follow", "canonical": base + "/catalogo"}
        ctx = Ctx(db, t)
        own = _stock_of(db, t.id, [p.id]).get(p.id, Decimal(0))
        c = ctx.card(p, own, full=True)
        url = base + c["path"]
        desc = re.sub(r"\s+", " ", (p.description_store or p.description_invoice or p.name)).strip()
        price = Decimal(str(c["display_price"]))
        crumbs = [("Tienda", "/")]
        if c.get("category_path"):
            crumbs.append((c["category"], c["category_path"]))
        crumbs.append((p.name, c["path"]))
        offer = {
            "@type": "Offer",
            "url": url,
            "priceCurrency": c["display_currency"],
            "availability": sv.availability_schema(c["availability"]["state"]),
            "itemCondition": "https://schema.org/NewCondition",
            "seller": {"@id": base + "/#org"},
        }
        if price > 0:
            offer["price"] = str(price)
            offer["priceValidUntil"] = (date.today() + timedelta(days=30)).isoformat()
        product_ld = {
            "@type": "Product",
            "name": p.name,
            "sku": p.code,
            "description": desc[:500],
            "image": [_abs(base, u) for u in (c["images"][:5] or ([c["image"]] if c["image"] else []))],
            "category": c.get("category") or c.get("type") or None,
            "offers": offer,
        }
        if p.brand:
            product_ld["brand"] = {"@type": "Brand", "name": p.brand}
        if p.model:
            product_ld["mpn"] = p.model
        return meta | {
            "title": f"{p.name} · {name}"[:160],
            "description": (f"{desc[:200]} · " if desc else "")
            + ("Precio con IVA incluido. " if price > 0 else "")
            + "Envío a todo Costa Rica, factura electrónica y soporte local.",
            "canonical": url,
            "og_type": "product",
            "image": _abs(base, c["image"]) if c["image"] else None,
            "price": str(price) if price > 0 else None,
            "currency": c["display_currency"],
            "ld": {"@context": "https://schema.org", "@graph": [product_ld, sv.breadcrumb_ld(base, crumbs), org]},
        }
    if head == "c" and len(parts) > 1:
        cats = _categories(db, t.id)
        slugs = sv.category_slugs(cats)
        cid = next((i for i, s in slugs.items() if s == parts[1]), None)
        cat = next((c for c in cats if c.id == cid), None)
        if not cat:
            return meta | {"title": f"Categoría no disponible · {name}", "robots": "noindex,follow", "canonical": base + "/catalogo"}
        url = f"{base}/c/{slugs[cat.id]}"
        return meta | {
            "title": f"{cat.name} · {name}",
            "description": cat.description or f"{cat.name} en {name}: precios con IVA, disponibilidad, envío a todo Costa Rica y asesoría técnica.",
            "canonical": url,
            "ld": {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "CollectionPage", "name": cat.name, "url": url, "isPartOf": {"@type": "WebSite", "url": base + "/"}},
                    sv.breadcrumb_ld(base, [("Tienda", "/"), (cat.name, f"/c/{slugs[cat.id]}")]),
                    org,
                ],
            },
        }
    simple = {
        "catalogo": ("Catálogo completo", "Todos los productos: cámaras, grabadores, redes, control de acceso, UPS y herramientas, con precio e IVA incluido."),
        "arma-tu-sistema": (
            "Arma tu sistema de cámaras",
            "Respondé 4 preguntas y te armamos un kit de videovigilancia con productos reales, validado por un técnico de Crimson.",
        ),
        "asesoria": (
            "Solicitud de asesoría personalizada",
            "Contanos qué necesitás proteger o conectar y un asesor de Crimson te contacta con una propuesta a la medida.",
        ),
    }
    if head in simple and len(parts) == 1:
        title, desc = simple[head]
        url = f"{base}/{head}"
        return meta | {
            "title": f"{title} · {name}",
            "description": desc,
            "canonical": url,
            "ld": {"@context": "https://schema.org", "@graph": [sv.breadcrumb_ld(base, [("Tienda", "/"), (title, f"/{head}")]), org]},
        }
    if head == "info" and len(parts) == 2 and parts[1] in sv.INFO_KINDS:
        title = sv.INFO_KINDS[parts[1]]
        url = f"{base}/info/{parts[1]}"
        ld: dict = {"@context": "https://schema.org", "@graph": [sv.breadcrumb_ld(base, [("Tienda", "/"), (title, f"/info/{parts[1]}")]), org]}
        if parts[1] == "preguntas-frecuentes":
            ld["@graph"].insert(
                0,
                {
                    "@type": "FAQPage",
                    "mainEntity": [{"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in sc["faq"]],
                },
            )
        return meta | {"title": f"{title} · {name}", "description": f"{title} de la tienda {name}.", "canonical": url, "ld": ld}
    # busqueda, comparador y cualquier otra: no se indexan (contenido duplicado o vacio)
    return meta | {"title": f"Tienda · {name}", "robots": "noindex,follow", "canonical": base + "/"}


@seo.get("/head", response_class=PlainTextResponse)
@seo.get("/head/{path:path}", response_class=PlainTextResponse)
def seo_head(path: str = "", host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    """Fragmento <head> por ruta. nginx lo incrusta con SSI en index.html para el dominio de la tienda: asi
    WhatsApp, Facebook y Google ven titulo, foto y precio sin ejecutar JavaScript. Cuerpo vacio = nginx usa el
    titulo por defecto (dominio que no es tienda, tienda despublicada)."""
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    if not t:
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    try:
        meta = seo_meta(db, t, _base(h), path)
    except HTTPException:
        return PlainTextResponse("")
    return PlainTextResponse(sv.head_html(meta), media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def _sitemap(db: Session, t: Tenant, base: str) -> str:
    cats = _categories(db, t.id)
    slugs = sv.category_slugs(cats)
    prods = _published(db, t.id)
    used = {p.category_id for p in prods}
    today = date.today().isoformat()
    urls = [(base + "/", today, "1.0"), (base + "/catalogo", today, "0.8"), (base + "/arma-tu-sistema", None, "0.7"), (base + "/asesoria", None, "0.6")]
    urls += [(f"{base}/c/{slugs[c.id]}", today, "0.7") for c in cats if c.id in used]
    cfg = store_cfg(t)
    legal = cfg.get("legal") or {}
    for k in sv.INFO_KINDS:
        if (k == "terminos" and not legal.get("terms")) or (k == "privacidad" and not legal.get("privacy")):
            continue
        urls.append((f"{base}/info/{k}", None, "0.3"))
    for p in prods:
        urls.append((base + sv.product_path(p), (p.updated_at or p.created_at).date().isoformat() if (p.updated_at or p.created_at) else None, "0.6"))
    body = "".join(f"<url><loc>{htmllib.escape(u)}</loc>{f'<lastmod>{lm}</lastmod>' if lm else ''}<priority>{pr}</priority></url>" for u, lm, pr in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'


@seo.get("/exists")
@seo.get("/exists/{path:path}")
def seo_exists(
    path: str = "",
    host: str | None = Query(None, max_length=200),
    x_store_host: str | None = Header(None),
    x_original_uri: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Para nginx (auth_request): 204 si la ficha o la categoria existe, 403 si no (nginx lo sirve como 404 real
    con la vitrina). En un dominio que no es tienda no bloquea nada."""
    t = _tenant_by_host(db, _host(x_store_host, host))
    if not t:
        return Response(status_code=204)
    path = path or urlsplit(x_original_uri or "/").path
    parts = [x for x in (path or "").strip("/").split("/") if x]
    ok = True
    if len(parts) >= 2 and parts[0] == "p":
        pid = sv.product_id_from_ref(parts[1])
        p = db.get(Product, pid) if pid else None
        ok = bool(p and p.tenant_id == t.id and p.show_on_web and p.active)
    elif len(parts) >= 2 and parts[0] == "c":
        cats = _categories(db, t.id)
        ok = parts[1] in set(sv.category_slugs(cats).values()) or (parts[1].isdigit() and any(c.id == int(parts[1]) for c in cats))
    return Response(status_code=204 if ok else 403, headers={"Cache-Control": "no-store"})


@seo.get("/sitemap.xml")
def seo_sitemap(host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    if not t:
        raise HTTPException(404, "Sin sitemap para este dominio")
    return Response(_sitemap(db, t, _base(h)), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@seo.get("/robots.txt", response_class=PlainTextResponse)
def seo_robots(host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    """Dominio de la tienda: se indexa (menos busqueda y comparador) y anuncia el sitemap. Cualquier otro (el panel):
    no se indexa nada."""
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    if not t:
        return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"Cache-Control": "public, max-age=3600"})
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nDisallow: /buscar\nDisallow: /comparar\nDisallow: /api/\n\nSitemap: {_base(h)}/sitemap.xml\n",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@seo.get("/legacy")
def seo_legacy(
    uri: str | None = Query(None, max_length=1000),
    host: str | None = Query(None, max_length=200),
    x_store_host: str | None = Header(None),
    x_original_uri: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """301 de las direcciones de Fygaro (/products/<uuid>/, /products/category/<id>/, /products/search/?keywords=,
    /terms, /policies, /page/...) a las nuevas. Los enlaces ya compartidos y lo que Google tiene indexado no mueren."""
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    raw = x_original_uri or uri or "/"
    parts = urlsplit(raw if raw.startswith("/") else "/" + raw)
    if not t:
        return RedirectResponse("/", status_code=302)
    fy = (t.settings or {}).get("fygaro") or {}
    cats = _categories(db, t.id)
    slugs = sv.category_slugs(cats)

    def to_product(pid: int) -> str | None:
        p = db.get(Product, pid)
        return sv.product_path(p) if p and p.tenant_id == t.id and p.show_on_web and p.active else None

    def to_category(cid: int) -> str:
        return f"/c/{slugs[cid]}" if cid in slugs else "/catalogo"

    q = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
    target = sv.legacy_target(parts.path, q, fy.get("products") or {}, fy.get("categories") or {}, to_product, to_category)
    return RedirectResponse(_base(h) + target, status_code=301, headers={"Cache-Control": "public, max-age=86400"})


@pub.get("/sitemap.xml")
def store_sitemap(slug: str, db: Session = Depends(get_db)):
    """Sitemap de la tienda por slug (util antes del corte de dominio o para revisarlo desde el panel)."""
    t = _tenant(db, slug)
    dom = (store_cfg(t).get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    return Response(_sitemap(db, t, _base(dom) if dom else ""), media_type="application/xml")
