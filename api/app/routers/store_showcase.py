"""Vitrina de la tienda propia: portada administrable, catalogo con filtros, ficha, configurador, asesoria y SEO.

Lo publico vive bajo /public/store/{slug}/... (mismo criterio que store.py: solo tiendas publicadas) y bajo
/public/seo/... (lo que nginx pide por dominio: metas por ruta via SSI, robots.txt, sitemap.xml y redirecciones
301 de las direcciones viejas de Fygaro). Carrito, checkout y pedidos NO viven aqui (store.py / modulo de pedidos).
"""

from __future__ import annotations

import html as htmllib
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only, selectinload

from ..core.clientip import ip_key
from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import SlidingWindow
from ..models import Category, Opportunity, Product, ProductTax, Tenant
from ..services import store_showcase as sv
from ..services.availability import settings_of, sort_key
from ..services.documents import audit
from ..services.email_layout import cta
from ..services.fx import FxMissing
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


class KitPhotoIn(BaseModel):
    """Foto fija de la tarjeta del kit que elige Andres: un producto y, si tiene varias, cual de sus fotos."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    product_id: int = Field(ge=1)
    image: str = Field(default="", max_length=500, pattern=r"^(|/[^\s]*|https://[^\s]+)$")


class KitExtraIn(BaseModel):
    """Linea adicional del kit que agrega Andres (p. ej. una UPS): producto y cantidad."""

    model_config = ConfigDict(extra="ignore")
    product_id: int = Field(ge=1)
    quantity: int = Field(1, ge=1, le=99)

    @model_validator(mode="before")
    @classmethod
    def _finitos(cls, v):
        # Infinity / 1e999: un 422 normal (como si el campo viniera vacio), no un error interno
        return {k: (None if _no_finito(x) else x) for k, x in v.items()} if isinstance(v, dict) else v


class KitIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(max_length=40, pattern=r"^[a-z0-9-]{1,40}$")
    title: str = Field(max_length=80)
    text: str = Field(default="", max_length=200)
    answers: dict = Field(default_factory=dict)
    active: bool = True
    # vacio = las fotos de los equipos que el kit arma hoy (camara, grabador, disco, switch)
    photos: list[KitPhotoIn] = Field(default_factory=list, max_length=sv.MAX_KIT_PHOTOS)
    # producto fijo por pieza ({"nvr": 123}); la pieza que no esta queda "Automatico" (sugerencia del configurador)
    pins: dict[str, int] = Field(default_factory=dict)
    extras: list[KitExtraIn] = Field(default_factory=list, max_length=sv.MAX_KIT_EXTRAS)

    @field_validator("pins", mode="before")
    @classmethod
    def _pins_finitos(cls, v):
        # Infinity / 1e999 no es un producto: se descarta aqui (el 422 no lo podria devolver: no es JSON)
        return {k: x for k, x in v.items() if not _no_finito(x)} if isinstance(v, dict) else v

    @field_validator("pins")
    @classmethod
    def _pins(cls, v):
        bad = set(v) - set(sv.KIT_ROLES)
        if bad:
            raise ValueError(f"Pieza desconocida: {', '.join(sorted(bad))}")
        return {k: int(x) for k, x in v.items() if int(x) > 0}


def _no_finito(x) -> bool:
    """Infinity, -Infinity o NaN: json.loads los acepta (tambien 1e999) pero no son un numero que se pueda usar ni devolver."""
    return isinstance(x, float) and not math.isfinite(x)


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


def _photo_ids(kits: list[dict]) -> set[int]:
    ids: set[int] = set()
    for k in kits or []:
        for ph in k.get("photos") or []:
            try:
                ids.add(int((ph or {}).get("product_id") or 0))
            except (TypeError, ValueError):
                continue
    ids.discard(0)
    return ids


def _kit_products(db: Session, tid: int, kits: list[dict]) -> dict[int, Product]:
    """Los productos de las fotos que Andres eligio para los kits (una consulta para todos). Solo los publicados en la
    web, como el resto de la vitrina: uno que se oculta despues de elegido deja la tarjeta con sus fotos automaticas."""
    ids = _photo_ids(kits)
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == tid, Product.active, Product.show_on_web, Product.id.in_(ids)))}


def _pins_ids(kits: list[dict]) -> set[int]:
    ids: set[int] = set()
    for k in kits or []:
        ids |= set(sv.clean_pins(k.get("pins")).values())
        ids |= {e["product_id"] for e in sv.clean_extras(k.get("extras"))}
    return ids


def _why_not_here(pr: Product | None) -> str:
    """El motivo concreto de que un producto fijado en un kit no este en la tienda (para Mi Tienda)."""
    if pr is None:
        return "El producto ya no existe en el catálogo."
    if not pr.active:
        return "Está inactivo en el catálogo."
    if not pr.show_on_web:
        return "No está publicado en la web."
    return "Está oculto en la tienda (agotado, según los ajustes de inventario)."


def _tax_rate(p: Product) -> float:
    return float(p.taxes[0].tax.rate) if p.taxes else 13


def _kit_total(pricer: Pricer, lines: list[dict]) -> Decimal:
    """Total con IVA de las lineas con precio de hoy: el precio web de cada pieza, el mismo de su tarjeta y del carrito,
    asi la tarjeta del kit, la vista previa de Mi Tienda y "Arma tu sistema" dan el mismo monto."""
    total = sum((_gross(pricer.of(ln["product"]), _tax_rate(ln["product"])) * ln["quantity"] for ln in lines), Decimal(0))
    return total.quantize(Decimal("1")) if pricer.currency == "CRC" else total.quantize(Decimal("0.01"))


def _kit_price(pricer: Pricer) -> Callable[[Product], Decimal]:
    """El precio con el que el armador de kits COMPARA productos (orden, equivalentes hasta 18 %, rotulo «Económica»):
    el que ve el cliente, o sea el precio web con IVA en la moneda de la tienda, el mismo de la tarjeta y del carrito.
    0 = "a consultar" (retenido por margen o sin tipo de cambio): ese producto no se propone. No cambia precios."""
    return lambda p: _gross(pricer.of(p), _tax_rate(p))


def _unique_kit_ids(kits: list[dict]) -> None:
    """Dos kits con el mismo id (los generaba «+ Kit») confunden la vista previa de Mi Tienda y el enlace de la tarjeta:
    el repetido recibe un id propio al guardar."""
    seen: set[str] = set()
    todos = {k["id"] for k in kits}
    for k in kits:
        if k["id"] in seen:
            n = 2
            while f"{k['id'][:36]}-{n}" in todos:
                n += 1
            k["id"] = f"{k['id'][:36]}-{n}"
            todos.add(k["id"])
        seen.add(k["id"])


def _priced_lines(pricer: Pricer, lines: list[dict]) -> list[dict]:
    """solo piezas con precio de hoy (retenido por margen o sin tipo de cambio = "a consultar", fuera del total)"""
    return [ln for ln in lines if pricer.of(ln["product"]) > 0]


def _admin_out(t: Tenant, db: Session) -> dict:
    """Mi Tienda -> Vitrina, con la disponibilidad de la funcion unica en los destacados y en lo que arma cada kit
    (los kits proponen primero lo que se puede comprar; lo que no, se ve marcado)."""
    from ..services.availability import availability_many

    cfg = store_cfg(t)
    sc = sv.showcase_cfg(cfg)
    ids = sc["featured"]
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.id.in_(ids)))} if ids else {}
    published = _published(db, t.id)
    av = availability_many(db, t, list({*prods.values(), *published}), audience="publico")
    pool = [p for p in published if not (av.get(p.id) or {}).get("ocultar")]
    sellable = {p.id for p in pool if _sellable(av.get(p.id))}
    kits_preview = []
    photo_prods = _kit_products(db, t.id, sc["kits"])
    pricer = Pricer(db, t)
    pin_ids = _pins_ids(sc["kits"])
    pinned = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.id.in_(pin_ids)))} if pin_ids else {}

    gross = _kit_price(pricer)

    def reason(st: dict) -> str | None:
        return _why_not_here(pinned.get(st["product_id"])) if st.get("reason") == sv.NOT_HERE else st.get("reason")

    for k in sc["kits"]:
        kit = sv.build_kit(pool, k.get("answers") or {}, prefer=sellable, pins=k.get("pins"), extras=k.get("extras"), options=8, price_of=gross)
        photos = sv.kit_photos(kit["lines"], k.get("photos"), photo_prods)
        roles = [
            {
                "key": ln["key"],
                "label": sv.KIT_ROLE_NAMES[ln["key"]],
                "product_id": ln["product"].id,
                "name": ln["product"].name,
                "source": ln["source"],
                "options": [{"id": o.id, "name": o.name, "code": o.code, "price": gross(o)} for o in ln.get("options", [])],
            }
            for ln in kit["lines"]
            if ln["key"] in sv.KIT_ROLES
        ] + [
            {"key": m["key"], "label": sv.KIT_ROLE_NAMES[m["key"]], "product_id": None, "name": None, "source": "missing", "options": []}
            for m in kit["missing"]
        ]
        kits_preview.append(
            {
                "id": k.get("id"),
                # lo que hoy muestra la tarjeta: lo elegido a mano (manual) o los equipos del kit; con todas las
                # fotos de cada producto elegido para cambiar cual se ve
                "photos": [{**ph, "images": sv.product_images(photo_prods.get(ph["product_id"]))} for ph in photos],
                "photos_manual": bool(k.get("photos")) and any(ph["product_id"] in photo_prods for ph in photos),
                "lines": [
                    {
                        "key": ln["key"],
                        "role": ln["role"],
                        "source": ln["source"],
                        "product_id": ln["product"].id,
                        "name": ln["product"].name,
                        "quantity": ln["quantity"],
                        "state": (av.get(ln["product"].id) or {}).get("state"),
                        "label": (av.get(ln["product"].id) or {}).get("label"),
                    }
                    for ln in kit["lines"]
                ],
                "missing": [m["name"] for m in kit["missing"]],
                # lo mismo que muestra la tarjeta de la portada: total con IVA de las piezas con precio de hoy
                "total": _kit_total(pricer, _priced_lines(pricer, kit["lines"])),
                "currency": pricer.currency,
                # composicion editable: cada pieza con su producto de hoy y los candidatos que calzan
                "roles": roles,
                "pins": [
                    {
                        "key": key,
                        "label": sv.KIT_ROLE_NAMES[key],
                        "product_id": st["product_id"],
                        "name": pinned[st["product_id"]].name if st["product_id"] in pinned else None,
                        "ok": st["ok"],
                        "used": st["used"],
                        "reason": reason(st),
                    }
                    for key, st in kit["pin_status"].items()
                ],
                "extras": [
                    {**e, "name": pinned[e["product_id"]].name if e["product_id"] in pinned else None, "reason": reason(e) if not e["ok"] else None}
                    for e in kit["extra_status"]
                ],
            }
        )
    return {
        **sc,
        "legal": cfg.get("legal") or {},
        "featured_products": [
            {
                "id": i,
                "name": prods[i].name,
                "code": prods[i].code,
                "show_on_web": prods[i].show_on_web,
                "state": (av.get(i) or {}).get("state"),
                "label": (av.get(i) or {}).get("label"),
            }
            for i in ids
            if i in prods
        ],
        "kits_preview": kits_preview,
        "slug": t.slug,
        "domain": cfg.get("domain"),
    }


@router.get("/store/showcase")
def showcase_get(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return _admin_out(db.get(Tenant, p.tenant.id), db)


@router.put("/store/showcase")
def showcase_put(data: ShowcaseIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    # Se relee la fila con candado (populate_existing: la sesion ya la tenia cargada por los permisos): los ajustes se
    # guardan enteros (JSON) y nunca desde una copia vieja. Otro guardado o el rearmado del snapshot pudieron escribir
    # despues de que esta peticion cargo la empresa.
    t = db.get(Tenant, p.tenant.id, with_for_update=True, populate_existing=True)
    st = {**(t.settings or {})}
    store = {**STORE_DEFAULTS, **(st.get("store") or {})}
    sc = dict(store.get("showcase") or {})
    patch = data.model_dump(exclude_unset=True)
    if "featured" in patch:
        own = set(db.scalars(select(Product.id).where(Product.tenant_id == t.id, Product.id.in_(patch["featured"] or [])))) if patch["featured"] else set()
        patch["featured"] = [i for i in dict.fromkeys(patch["featured"] or []) if i in own]
    if "banners" in patch:
        patch["banners"] = [{**b, "id": b.get("id") or f"b{i + 1}"} for i, b in enumerate(patch["banners"] or [])]
    warnings: list[str] = []
    if "kits" in patch and patch["kits"] is not None:
        _unique_kit_ids(patch["kits"])
        # productos fijos y adicionales: solo de esta empresa; uno que nunca sirve para la pieza (un disco como camara)
        # se descarta con aviso. Lo pasajero (sin publicar, sin precio, pocos canales) se guarda y la vista previa
        # dice por que hoy se usa la sugerencia automatica.
        ids = _pins_ids(patch["kits"])
        own = {x.id: x for x in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.id.in_(ids)))} if ids else {}
        for k in patch["kits"]:
            pins: dict[str, int] = {}
            for key, pid in sv.clean_pins(k.get("pins")).items():
                pr = own.get(pid)
                prob = "no existe en su catálogo" if pr is None else sv.pin_type_problem(key, pr)
                if prob:
                    quien = pr.name if pr is not None else f"El producto {pid}"
                    motivo = prob.rstrip(".")
                    motivo = motivo[:1].lower() + motivo[1:]  # solo la inicial: «no es un grabador NVR», «4TB», «PoE»
                    warnings.append(f"«{k['title']}» · {sv.KIT_ROLE_NAMES[key]}: {quien} no se fijó ({motivo}); queda automático.")
                    continue
                pins[key] = pid
            k["pins"] = pins
            extras = []
            for e in sv.clean_extras(k.get("extras")):
                if e["product_id"] in own:
                    extras.append(e)
                else:
                    warnings.append(f"«{k['title']}»: el adicional {e['product_id']} no existe en su catálogo y se quitó.")
            k["extras"] = extras
        # fotos fijas: solo productos de esta empresa publicados en la web (activos y visibles) con foto; la foto elegida
        # tiene que ser de ese producto
        prods = {
            p.id: p
            for p in db.scalars(
                select(Product).where(Product.tenant_id == t.id, Product.active, Product.show_on_web, Product.id.in_(_photo_ids(patch["kits"])))
            )
        }
        for k in patch["kits"]:
            fotos, vistos = [], set()
            for ph in k.get("photos") or []:
                p_ = prods.get(ph["product_id"])
                if p_ is None or ph["product_id"] in vistos or not sv.product_images(p_):
                    continue
                vistos.add(ph["product_id"])
                fotos.append({"product_id": ph["product_id"], "image": ph["image"] if ph.get("image") in sv.product_images(p_) else ""})
            k["photos"] = fotos
    if "pages" in patch:
        patch["pages"] = {**(sc.get("pages") or {}), **(patch["pages"] or {})}
    sc.update(patch)
    store["showcase"] = sc
    st["store"] = store
    t.settings = st
    # la portada responde 304 mientras no cambie la version: un kit editado se ve de inmediato
    sv.bump_catalog_version(db, t)
    audit(db, t.id, p.user.id, "update", "store_showcase", t.id, {"campos": sorted(patch)}, ip=p.ip)
    db.commit()
    return {**_admin_out(t, db), "warnings": warnings}


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
    # catalog.ver lo tienen el tecnico y el aliado, que nunca ven precios (D5): sin catalog.precios van en None
    precios = p.sees_prices
    return {
        "id": pr.id,
        "name": pr.name,
        "code": pr.code,
        "price": pr.price if precios else None,
        "currency": pr.currency,
        "compare_price": pr.compare_price if precios else None,
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


# Columnas livianas para filtrar/ordenar/contar facetas: sin texto largo ni specs.
# description_store se incluye porque build_kit/_pick_camera lo usa para detectar PoE.
_LIGHT_COLS = (
    Product.id,
    Product.name,
    Product.code,
    Product.brand,
    Product.model,
    Product.supplier_sku,
    Product.category_id,
    Product.price,
    Product.currency,
    Product.web_price,
    Product.web_featured,
    Product.created_at,
    Product.item_type,
    Product.active,
    Product.show_on_web,
    Product.tenant_id,
    Product.margin_hold,
    Product.images,
    Product.compare_price,
    Product.description_store,
)


def _published_light(db: Session, tid: int) -> list[Product]:
    """Productos publicados con solo columnas livianas: sin description_invoice, specs ni margin_info.
    Suficiente para filtrar, ordenar y contar facetas en el catalogo y la portada.
    Los productos de la pagina se cargan completos con _load_full antes de pasarlos a ctx.card."""
    return list(
        db.scalars(
            select(Product)
            .where(Product.tenant_id == tid, Product.active, Product.show_on_web)
            .options(
                load_only(*_LIGHT_COLS),
                selectinload(Product.taxes).selectinload(ProductTax.tax),
            )
            .order_by(Product.name)
        )
    )


def _load_full(db: Session, ids: list[int]) -> list[Product]:
    """Carga completa (description_invoice, specs, etc.) para los IDs dados.
    populate_existing=True sobreescribe instancias livianas ya en la sesion."""
    if not ids:
        return []
    return list(
        db.scalars(
            select(Product).where(Product.id.in_(ids)).options(selectinload(Product.taxes).selectinload(ProductTax.tax)),
            execution_options={"populate_existing": True},
        )
    )


def _categories(db: Session, tid: int) -> list[Category]:
    return list(db.scalars(select(Category).where(Category.tenant_id == tid, Category.show_on_web).order_by(Category.name)))


AVAIL_KEYS = ("state", "label", "label_short", "max_qty", "ultimas", "cantidad", "disponible", "accion", "plazo")


def _avail(row: dict | None) -> dict:
    """La disponibilidad de la vitrina ES la de la funcion unica (services/availability, audiencia publico): mismos
    estados y etiquetas que la lista, la ficha y /availability en vivo. Solo se recortan las llaves."""
    from .store import UNKNOWN_AVAILABILITY

    r = row if isinstance(row, dict) and row.get("state") else UNKNOWN_AVAILABILITY
    return {k: r.get(k) for k in AVAIL_KEYS}


def _sellable(row: dict | None) -> bool:
    return bool((row or {}).get("disponible")) and (row or {}).get("state") != "servicio"


class Ctx:
    """Lo que toda respuesta publica necesita una sola vez por pedido."""

    def __init__(self, db: Session, t: Tenant):
        self.db, self.t = db, t
        self.cfg = store_cfg(t)
        self.cfg_inv = settings_of(t)
        self.pricer = Pricer(db, t)
        self.cats = _categories(db, t.id)
        self.cat_name = {c.id: c.name for c in self.cats}
        self.cat_slug = sv.category_slugs(self.cats)

    def card(self, p: Product, own: dict | None, variants: list | None = None, full: bool = False) -> dict:
        out = _prod_pub(p, variants, own, self.pricer, self.cat_name, compact=not full)
        price = Decimal(str(out["display_price"]))
        cmp = None
        if p.compare_price:
            c = _gross(self.pricer.of(p, p.compare_price), out["tax_rate"])
            cmp = c if c > price else None
        out.update(
            {
                "path": sv.product_path(p),
                "model": p.model,
                "type": sv.product_type(p),
                "compare_display_price": cmp,
                "price_on_request": price <= 0,
                "category_path": f"/c/{self.cat_slug[p.category_id]}" if p.category_id in self.cat_slug else None,
                "availability": _avail(own),
            }
        )
        if not full:
            out["images"] = out["images"][:2]
        return out


def _stock_map(db: Session, tid: int, prods: list[Product]) -> dict[int, dict]:
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
    """'Relevancia' sin búsqueda: destacados primero, luego Hikvision, luego lo que se puede comprar ya."""
    return (
        not bool(p.web_featured),  # 1. destacados (Hikvision y los que Andrés marcó)
        (p.brand or "").lower() != "hikvision",  # 2. Hikvision no marcado explícitamente
        not p.images,  # 3. con foto
        not (p.price and Decimal(str(p.price)) > 0),  # 4. con precio
        p.name.lower(),  # 5. alfabético
    )


def etag_matches(header: str | None, version: str | None) -> bool:
    """If-None-Match contra un ETag (sin comillas): acepta la forma debil W/"..." que agrega el borde (gzip), varias
    separadas por coma y "*"."""
    if not header or not version:
        return False
    for tag in header.split(","):
        tag = tag.strip()
        if tag == "*" or tag.removeprefix("W/").strip('"') == version:
            return True
    return False


def _vitrina_version(db: Session, t: Tenant) -> str:
    """Version de lo que muestran /showcase y /catalog (O1-13): la del panel (catalog_v) y la del inventario (stock_v
    del snapshot, sube con cada movimiento, reserva o cambio de margen). Antes solo la del panel: un 304 podia servir
    existencias viejas sin limite."""
    from ..models import StoreSnapshot

    sv_ = db.scalar(select(StoreSnapshot.stock_v).where(StoreSnapshot.tenant_id == t.id)) or 0
    return f"{sv.get_catalog_version(t)}.{sv_}"


@pub.get("/snapshot")
def store_snapshot_endpoint(slug: str, response: Response, if_none_match: str | None = Header(None), db: Session = Depends(get_db)):
    """Snapshot pre-armado de la tienda: JSON comprimido (gzip) con todos los productos visibles y su
    disponibilidad. El cliente lo descarga una vez (~60-200 KB) y filtra/busca/ordena sin mas peticiones.
    ETag = sha256[:16] del contenido; 304 si If-None-Match coincide.
    Content-Encoding: gzip + application/json: el navegador lo descomprime automaticamente."""
    from ..services import store_snapshot as snap

    t = _tenant(db, slug)
    # Leer solo (tenant_id, version) sin cargar el blob gz
    from sqlalchemy.orm import load_only as _lo

    from ..models import StoreSnapshot as _SS

    row = db.scalar(select(_SS).where(_SS.tenant_id == t.id).options(_lo(_SS.version, _SS.built_at, _SS.n_products, _SS.dirty_at)))
    # O1-13: el 304 solo si el guardado esta al dia (antes se respondia 304 aunque hubiera cambiado el inventario, y
    # quien ya lo tenia en cache nunca veia la existencia nueva). El borde agrega W/ al ETag: se acepta igual.
    if row is not None and etag_matches(if_none_match, row.version) and not snap.is_stale(row, t.settings):
        return Response(status_code=304, headers={"ETag": f'"{row.version}"'})
    gz, version = snap.get_or_rebuild(db, t.id)
    if etag_matches(if_none_match, version):
        return Response(status_code=304, headers={"ETag": f'"{version}"'})
    response.headers["ETag"] = f'"{version}"'
    response.headers["Cache-Control"] = "public, max-age=120, stale-while-revalidate=86400"
    response.headers["Content-Encoding"] = "gzip"
    return Response(content=gz, media_type="application/json", headers=dict(response.headers))


@pub.get("/site")
def site(slug: str, response: Response, db: Session = Depends(get_db)):
    """Datos livianos que toda vista necesita: por ahora la medicion (GA4), que el navegador solo activa con
    consentimiento explicito de la persona."""
    t = _tenant(db, slug)
    sc = sv.showcase_cfg(store_cfg(t))
    response.headers["Cache-Control"] = "public, max-age=300"
    return {"analytics": {"ga4_id": sc["ga4_id"] or None}}


@pub.get("/showcase")
def showcase_public(slug: str, response: Response, if_none_match: str | None = Header(None), db: Session = Depends(get_db)):
    """Portada de la tienda en un solo viaje: banners, categorias con conteo, destacados, novedades, kits y sellos."""
    from ..core.timeutil import cr_today

    t = _tenant(db, slug)
    _ver = _vitrina_version(db, t)
    # los kits rotan sus articulos equivalentes cada dia (sv._pick): el dia va en el ETag para no servir el de ayer
    _etag = f'"{_ver}-{cr_today().toordinal()}"'
    if etag_matches(if_none_match, _etag.strip('"')):
        return Response(status_code=304, headers={"ETag": _etag})
    ctx = Ctx(db, t)
    sc = sv.showcase_cfg(ctx.cfg)
    # Carga liviana: sin description_invoice ni specs. description_store incluida para build_kit (PoE).
    prods = _published_light(db, t.id)
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
    # O1-05: conteos por categoria, por marca y total del snapshot (lo que la tienda lista), los mismos de /store
    from ..services.store_snapshot import counts as snapshot_counts

    snap_n = snapshot_counts(db, t.id)
    if snap_n is not None:
        counts = dict(snap_n["direct"])
        brands = {b["name"]: int(b["count"]) for b in snap_n["brands"] if b.get("name")}
    by_id = {p.id: p for p in prods}
    stock = _stock_map(db, t.id, prods)  # una sola consulta: destacados, novedades y kits con la misma disponibilidad
    # Ajustes "ocultar agotados": tampoco salen en destacados, novedades ni kits
    pool = [p for p in prods if not (stock.get(p.id) or {}).get("ocultar")]
    sellable = {p.id for p in pool if _sellable(stock.get(p.id))}
    featured = [by_id[i] for i in sc["featured"] if i in by_id and by_id[i] in pool]
    if not featured:  # sin eleccion de Andres: uno por tipo de equipo, con foto y precio, primero lo que se puede comprar
        featured = sv.default_featured(pool, prefer=sellable)
    fresh = [p for p in sorted(pool, key=lambda p: (_created(p), p.id), reverse=True) if p.images and p not in featured][:8]
    kits = []
    photo_prods = _kit_products(db, t.id, sc["kits"])
    kit_price = _kit_price(ctx.pricer)
    for k in sc["kits"]:
        if not k.get("active", True):
            continue
        # cada pieza: primero lo que se puede comprar; con los productos fijos y adicionales que eligio Andres
        kit = sv.build_kit(pool, k.get("answers") or {}, prefer=sellable, pins=k.get("pins"), extras=k.get("extras"), price_of=kit_price)
        lines = _priced_lines(ctx.pricer, kit["lines"])
        if not lines:
            continue
        photos = sv.kit_photos(lines, k.get("photos"), photo_prods)
        kits.append(
            {
                "id": k.get("id"),
                "title": k.get("title"),
                "text": k.get("text"),
                "answers": kit["answers"],
                "items": sum(ln["quantity"] for ln in lines),
                "total": _kit_total(ctx.pricer, lines),
                "image": photos[0]["image"] if photos else None,
                # fotos fijas de los equipos reales del kit (o las que eligio Andres): {image, label, name}
                "photos": [{k_: ph[k_] for k_ in ("image", "label", "name")} for ph in photos],
                "cameras": kit["cameras"],
            }
        )
    # Carga completa para los productos que se renderizan como tarjeta (featured + fresh).
    _render_ids = list({p.id for p in featured[:8]} | {p.id for p in fresh})
    _full = {p.id: p for p in _load_full(db, _render_ids)}
    response.headers["ETag"] = _etag
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return {
        "headline": sc["headline"],
        "subline": sc["subline"],
        "banners": sorted([b for b in sc["banners"] if b.get("active", True) and (b.get("image") or b.get("title"))], key=lambda b: b.get("position", 0)),
        "trust": sc["trust"],
        "categories": _cat_tree(ctx, counts, images),
        "brands": [{"name": b, "count": n} for b, n in sorted(brands.items(), key=lambda x: (x[0].lower() != "hikvision", -x[1]))[:14]],
        "featured": [ctx.card(_full.get(p.id, p), stock.get(p.id)) for p in featured[:8]],
        "fresh": [ctx.card(_full.get(p.id, p), stock.get(p.id)) for p in fresh],
        "kits": kits,
        "product_count": snap_n["n"] if snap_n is not None else len(prods),
        "currency": ctx.pricer.currency,
        "seo": _seo(db, t, "/"),
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
    if_none_match: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Catalogo con filtros (categoria con sus subcategorias, marca, tipo, precio con IVA, disponibilidad), orden y
    paginacion. Devuelve tambien las facetas calculadas sobre el resultado (sin el filtro de la propia faceta)."""
    t = _tenant(db, slug)
    _etag = f'"{_vitrina_version(db, t)}"'
    if etag_matches(if_none_match, _etag.strip('"')):
        return Response(status_code=304, headers={"ETag": _etag})
    ctx = Ctx(db, t)
    # Carga liviana para todo el catalogo; description_invoice y specs se cargan solo para la pagina.
    prods = _published_light(db, t.id)
    stock_all = _stock_map(db, t.id, prods)
    if ids:
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()][:12]
        by_id = {p.id: p for p in prods}
        wanted = [i for i in want if i in by_id]
        full_ids = {p.id: p for p in _load_full(db, wanted)}
        rows = [full_ids[i] for i in wanted if i in full_ids]
        return {"items": [ctx.card(p, stock_all.get(p.id), full=True) | {"specs": sv.derive_specs(p)} for p in rows], "total": len(rows)}
    # Ajustes: los agotados ocultos (todos, o los que agoto el proveedor) no se listan; la ficha directa abre
    prods = [p for p in prods if not (stock_all.get(p.id) or {}).get("ocultar")]

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

    disp = {p.id: _gross(ctx.pricer.of(p, p.price), float(p.taxes[0].tax.rate) if p.taxes else 13) for p in prods}
    av = {p.id: (stock_all.get(p.id) or {}).get("state") for p in prods}
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
            if avail in ("disponible", "inmediata") and st != "en_bodega":
                return False
            if avail == "bajo_pedido" and st not in ("en_bodega", "bajo_pedido"):
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
            if st == "en_bodega":
                facet_av["disponible"] += 1
            if st in ("en_bodega", "bajo_pedido"):
                facet_av["bajo_pedido"] += 1
    priced = [disp[p.id] for p in prods if keep(p, "price") and disp[p.id] > 0]

    if sort == "relevance":  # primero lo que se puede comprar ya (lo propio), luego bajo pedido
        rows.sort(key=lambda p: (-scores.get(p.id, 0), sort_key(stock_all.get(p.id)), _rank_default(p)))
    elif sort == "name":
        # destacados y Hikvision primero, luego alfabético dentro de cada grupo
        rows.sort(key=lambda p: (not bool(p.web_featured), (p.brand or "").lower() != "hikvision", p.name.lower()))
    elif sort in ("price_asc", "price_desc"):
        con = sorted([p for p in rows if disp[p.id] > 0], key=lambda p: disp[p.id], reverse=sort == "price_desc")
        rows = con + [p for p in rows if disp[p.id] <= 0]  # "precio a consultar" siempre al final
    else:
        rows.sort(key=lambda p: (_created(p), p.id), reverse=True)

    total = len(rows)
    page = rows[offset : offset + limit]
    page_ids = [p.id for p in page]
    # Carga completa solo para los productos de esta pagina (description_invoice, specs, etc.)
    full_page = {p.id: p for p in _load_full(db, page_ids)}
    vmap = _variants(db, page_ids)
    response.headers["X-Total-Count"] = str(total)
    response.headers["ETag"] = _etag
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return {
        "items": [ctx.card(full_page.get(p.id, p), stock_all.get(p.id), vmap.get(p.id)) for p in page],
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
            "brands": [{"name": b, "count": n} for b, n in sorted(facet_brand.items(), key=lambda x: (x[0].lower() != "hikvision", -x[1], x[0]))[:30]],
            "types": [{"name": k, "count": n} for k, n in sorted(facet_type.items(), key=lambda x: (-x[1], x[0]))],
            "availability": facet_av,
            "price": {"min": min(priced) if priced else 0, "max": max(priced) if priced else 0},
        },
        "currency": ctx.pricer.currency,
        "nav": nav,
        "seo": _seo(db, t, f"/c/{ctx.cat_slug[cat_obj.id]}" if cat_obj else ("/buscar" if terms else "/catalogo")),
    }


def _seo(db: Session, t: Tenant, path: str, **kw) -> dict | None:
    """Bloque seo de la ruta (O1-06): el mismo de /public/seo/head, armado por seo_meta (routers/store_seo.py)."""
    from .store_seo import seo_block

    return seo_block(db, t, path, **kw)


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
    out = ctx.card(p, stock.get(p.id), _variants(db, [p.id]).get(p.id), full=True)
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
        "related": [ctx.card(r, stock.get(r.id)) for r in related],
        "accessories": [{"title": g["title"], "reason": g["reason"], "items": [ctx.card(r, stock.get(r.id)) for r in g["items"]]} for g in groups],
        "whatsapp": ctx.cfg.get("whatsapp") or "",
        "seo": _seo(db, t, out["path"], ctx=ctx, card=out),
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
        return {"kind": kind, "title": sv.INFO_KINDS[kind], "body": "", "faq": sc["faq"], "seo": _seo(db, t, f"/info/{kind}")}
    if not str(body).strip():
        raise HTTPException(404, "Página no disponible")
    return {"kind": kind, "title": sv.INFO_KINDS[kind], "body": body, "faq": [], "seo": _seo(db, t, f"/info/{kind}")}


class KitAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place: str = Field("casa", pattern="^(casa|negocio|condominio|bodega)$")
    indoor: int = Field(2, ge=0, le=32)
    outdoor: int = Field(2, ge=0, le=32)
    days: int = Field(15, ge=1, le=90)
    remote: bool = True
    # detalles (opcionales, con lo de siempre por defecto: un enlace viejo arma el mismo kit)
    quality: str = Field("4mp", pattern="^(2mp|4mp|8mp)$")
    night: str = Field("ir", pattern="^(ir|color)$")
    audio: bool = False
    smart: bool = False
    record: str = Field("continuo", pattern="^(continuo|movimiento)$")
    distance: str = Field("media", pattern="^(corta|media|larga)$")
    growth: bool = False
    ups: bool = False
    # kit de la vitrina desde cuya tarjeta se abrio (aplica sus productos fijos y adicionales si las respuestas son las suyas)
    kit: str | None = Field(None, pattern=r"^[a-z0-9-]{1,40}$")
    # lo que el cliente cambio: {llave de la pieza: product_id}; lo que no calza se ignora (no da error: viene de un enlace)
    choices: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _finitos(cls, v):
        # Infinity, NaN o 1e999 en el cuerpo: se validan como si el campo viniera vacio (422 normal en una respuesta,
        # ignorado en `kit`). Sin esto el 422 no se podia serializar (no son JSON) y la peticion terminaba en 500.
        return {k: (None if _no_finito(x) else x) for k, x in v.items()} if isinstance(v, dict) else v

    @field_validator("choices", mode="before")
    @classmethod
    def _choices(cls, v):
        return sv.clean_pins(v) if isinstance(v, dict) else {}

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
    kit_limiter.hit(f"kit:{ip_key(client_ip(request))}")
    t = _tenant(db, slug)
    ctx = Ctx(db, t)
    prods = _published(db, t.id)
    stock = _stock_map(db, t.id, prods)
    # el armador propone primero lo que se puede comprar (en bodega o bajo pedido) y nunca lo oculto por agotado
    pool = [p for p in prods if not (stock.get(p.id) or {}).get("ocultar")]
    answers = data.model_dump(exclude={"kit", "choices"})
    shown = _kit_price(ctx.pricer)  # el precio que ve el cliente: con el se eligen y se rotulan las piezas

    def side(adds: list[dict]) -> Decimal:
        """lo que el kit suma por llevar un producto (el switch de un grabador sin PoE para todas las camaras)"""
        return sum((shown(x["product"]) * x["quantity"] for x in adds), Decimal(0))

    # el kit de la vitrina con estas respuestas (el de la tarjeta si vino su id): mismas fotos, productos fijos y
    # adicionales que su tarjeta, para que el precio cuadre. El cliente igual puede cambiar cada pieza.
    kits_cfg = sv.showcase_cfg(ctx.cfg)["kits"]
    na = sv.normalize_answers(answers)
    preset = next(
        (x for x in kits_cfg if data.kit and x.get("id") == data.kit and x.get("active", True) and sv.normalize_answers(x.get("answers")) == na),
        None,
    ) or sv.preset_for(kits_cfg, answers)
    k = sv.build_kit(
        pool,
        answers,
        prefer={p.id for p in pool if _sellable(stock.get(p.id))},
        pins=(preset or {}).get("pins"),
        extras=(preset or {}).get("extras"),
        choices=data.choices,
        alternatives=True,
        price_of=shown,
    )
    photos = sv.kit_photos(k["lines"], (preset or {}).get("photos"), _kit_products(db, t.id, [preset] if preset else []))
    lines, total = [], Decimal(0)
    for ln in k["lines"]:
        c = ctx.card(ln["product"], stock.get(ln["product"].id))
        unit = Decimal(str(c["display_price"]))
        sub = unit * ln["quantity"]
        total += sub
        alts = []
        cur_side = side(ln.get("adds") or [])
        for a in ln.get("alternatives", []):
            ac = ctx.card(a["product"], stock.get(a["product"].id))
            au = Decimal(str(ac["display_price"]))
            if au <= 0:  # la alternativa esta "a consultar": no se ofrece (la pieza actual si puede estarlo)
                continue
            asub = au * a["quantity"]
            # la diferencia es la del KIT, no la de la linea: cambiar el grabador puede meter o sacar el switch
            entra = [x["product"].name for x in a["adds"]] if a["adds"] and not ln["adds"] else []
            sale = [x["product"].name for x in ln["adds"]] if ln["adds"] and not a["adds"] else []
            note = None
            if entra:
                note = f"Sin PoE para todas las cámaras: suma {', '.join(entra)} (ya va en la diferencia)."
            elif sale:
                note = f"Con PoE integrado: ya no hace falta {', '.join(sale)} (ya va en la diferencia)."
            alts.append(
                {
                    "label": a["label"],
                    "auto": a["auto"],
                    "quantity": a["quantity"],
                    "subtotal": asub,
                    "diff": (asub + side(a["adds"])) - (sub + cur_side),
                    "note": note,
                    "specs": sv.key_specs(a["product"]),
                    "product": ac,
                }
            )
        lines.append(
            {
                "key": ln["key"],
                "role": ln["role"],
                "reason": ln["reason"],
                "source": ln["source"],
                # pinned: el kit de la tienda fija esta pieza. suggested: es lo que el kit lleva sin elegir nada (una
                # eleccion igual a la sugerencia de hoy, como las piezas de un enlace compartido, no es "un cambio")
                "pinned": ln["pinned"],
                "suggested": ln["suggested"],
                "quantity": ln["quantity"],
                "unit_price": unit,
                "subtotal": sub,
                "specs": sv.key_specs(ln["product"]),
                "product": c,
                "alternatives": alts,
            }
        )
    return {
        **{k_: v for k_, v in k.items() if k_ not in ("lines", "pin_status", "extra_status")},
        "lines": lines,
        "total": total,
        "currency": ctx.pricer.currency,
        "photos": [{k_: ph[k_] for k_ in ("image", "label", "name")} for ph in photos],
        "kit": {"id": preset.get("id"), "title": preset.get("title")} if preset else None,
    }


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
        select(Opportunity).where(Opportunity.tenant_id == t.id, Opportunity.source.in_(("Sitio web", "tienda web"))).order_by(Opportunity.id.desc()).limit(20)
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
        try:  # estimado interno de la oportunidad: con el precio de lista aunque la vitrina lo muestre "a consultar"
            neto = ctx.pricer(p.price, p.currency) * it.quantity
        except FxMissing:
            neto = Decimal(0)
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
        source="Sitio web",
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
        + cta(f"/oportunidades?id={o.id}", f"Abrir {number}")
    )
    # Reparto de consultas web: auto-asignar según la configuración ("turno" o "solucion") antes de avisar.
    from ..models import TenantUser, User
    from ..services.mail import member_inbox, queue_email
    from ..services.sale_assignment import auto_assign_opportunity

    assigned_uid = auto_assign_opportunity(db, t, o)

    if assigned_uid:
        # Se asignó automáticamente: aviso con detalle al vendedor asignado y al admin (sin datos al resto)
        m_v = db.scalar(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.user_id == assigned_uid, TenantUser.active))
        correo_v = member_inbox(m_v) if m_v else None
        if correo_v:
            queue_email(
                db,
                t,
                correo_v,
                f"Nueva consulta asignada: {e(number)} · {etiqueta}",
                # El vendedor recibe el detalle completo si fue asignado automáticamente
                cuerpo,
                "opportunity",
                o.id,
                dedupe_key=f"opp-asig-vendedor:{o.id}",
                company_bcc=False,
            )
        # Admin recibe el detalle completo siempre
        responsable = db.get(User, assigned_uid)
        notify_roles(
            db,
            t,
            ("admin",),
            f"Asesoría web {number} · {etiqueta} · {quien[:40]} (asignada a {(responsable.full_name or responsable.email) if responsable else 'un vendedor'})",
            cuerpo,
            "opportunity",
            o.id,
            internal_copy=True,
        )
    else:
        # Modo manual: el admin recibe con detalle; los vendedores solo "hay una consulta nueva por asignar"
        _cuerpo_vendedor = f"<p>Hay una nueva solicitud de asesoría desde la tienda (<b>{e(number)}</b>) que todavía no tiene responsable.</p>" + cta(
            "/ventas/por-asignar", "Ir a Ventas por asignar"
        )
        notify_roles(
            db,
            t,
            ("admin",),
            f"Asesoría web {number} · {etiqueta} · {quien[:40]}",
            cuerpo,
            "opportunity",
            o.id,
            internal_copy=True,
        )
        notify_roles(
            db,
            t,
            ("supervisor", "ventas"),
            f"Nueva consulta web por asignar · {etiqueta}",
            _cuerpo_vendedor,
            "opportunity",
            o.id,
        )
    db.commit()


@pub.post("/asesoria", status_code=202)
async def advisory(slug: str, request: Request, db: Session = Depends(get_db)):
    """Solicitud de asesoria personalizada: crea una OPORTUNIDAD en el CRM (fuente "tienda web") y avisa a ventas.
    Mismas defensas que /public/incidents: tope de tamano leyendo el stream, validacion estricta, honeypot,
    limite por IP y global, y respuesta identica pase lo que pase."""
    ip = client_ip(request)
    advisory_limiter.hit(f"asesoria:{ip_key(ip)}")
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


@pub.get("/sitemap.xml")
def store_sitemap(slug: str, db: Session = Depends(get_db)):
    """Sitemap de la tienda por slug (util antes del corte de dominio o para revisarlo desde el panel)."""
    from .store_seo import _base, _sitemap

    t = _tenant(db, slug)
    dom = (store_cfg(t).get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    return Response(_sitemap(db, t, _base(dom) if dom else ""), media_type="application/xml")


# ================================================================ SEO por dominio: vive en routers/store_seo.py (O1-01)
_SEO_NAMES = frozenset(
    {"seo", "seo_meta", "seo_head", "_sitemap", "seo_exists", "seo_sitemap", "seo_robots", "seo_legacy", "_tenant_by_host", "_host", "_base", "_abs"}
)


def __getattr__(name: str):
    """Compatibilidad: los nombres del SEO se movieron a routers/store_seo.py; se importan aqui solo al pedirlos (una
    importacion arriba seria circular: store_seo usa Ctx y _published de este modulo)."""
    if name in _SEO_NAMES:
        from . import store_seo

        return getattr(store_seo, name)
    raise AttributeError(name)
