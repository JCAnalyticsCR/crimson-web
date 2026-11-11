"""Mi Tienda -> Catalogo (O4-04): todo el catalogo web en un lugar, con filtros de calidad, y UN solo servicio para
editar lo web de un producto.

Antes, editar un producto para la web estaba repartido en cinco pantallas (Productos, Inventario web, Vitrina -> Por
producto, Articulos por completar y la ficha) y cada una tenia su propia regla de permisos, bitacora y marca del
catalogo. Ahora:
- listing(): GET /store/catalog, paginado en el servidor (48 por pagina), con busqueda, categoria, marca, estado que ve
  el cliente, publicado y faltantes; cada ficha de filtro con su numero.
- apply(): la unica escritura de los campos web (permisos por campo, guardian de margen, bitacora con antes y despues,
  una sola marca del catalogo). La usan PATCH /store/catalog/{id}, PATCH /store/inventory/products/{id},
  PUT /store/showcase/products/{id} y PATCH /products/{id}/web (los viejos devuelven lo mismo que antes).

Permisos por campo (los de hoy):
- show_on_web: catalog.publicar (solo el administrador, regla del 21/10 y O1-11b).
- web_price y compare_price: catalog.precios (ademas de catalog.editar).
- lo demas (destacado, textos, fotos, ficha tecnica, categoria, marca): catalog.editar.
Nunca se tocan Product.name ni Product.code (viven en kardex, cotizaciones y facturas): el nombre comercial es
Product.web_name (Ola 3, O3-01; el snapshot lo publica como display_name) y se edita aqui, marcando web_manual.
"""

from __future__ import annotations

import math
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Category, Product, ProductTax
from .documents import audit

PER_PAGE = 48
NOMBRE_LARGO = 60  # lo que Google muestra de un titulo (mismo corte que el Resumen, O4-03)

FALTAS = {
    "sin_descripcion": "Sin descripción",
    "una_foto": "Una sola foto",
    "sin_foto": "Sin foto",
    "sin_ficha": "Sin ficha técnica",
    "nombre_largo": "Nombre largo",
    "foto_proveedor": "Fotos del proveedor",
    "por_confirmar": "Por confirmar",
    "en_oferta": "En oferta",
}
ESTADOS = {"en_bodega": "En bodega", "bajo_pedido": "Bajo pedido", "consultar": "Por confirmar", "agotado": "Agotado", "servicio": "Servicio"}

# campos web que se editan desde Mi Tienda y el permiso de cada uno
FIELD_PERMS: dict[str, tuple[str, str]] = {
    "show_on_web": ("catalog", "publicar"),
    "web_price": ("catalog", "precios"),
    "compare_price": ("catalog", "precios"),
    "web_featured": ("catalog", "editar"),
    "description_store": ("catalog", "editar"),
    "images": ("catalog", "editar"),
    "specs": ("catalog", "editar"),
    "datasheet_url": ("catalog", "editar"),
    "category_id": ("catalog", "editar"),
    "brand": ("catalog", "editar"),
    # Ola 3 (O3-01): contenido propio de la tienda escrito a mano; nunca el nombre fiscal ni el codigo
    "web_name": ("catalog", "editar"),
    "web_subtitle": ("catalog", "editar"),
    "web_type": ("catalog", "editar"),
    "web_description": ("catalog", "editar"),
    "warranty_months": ("catalog", "editar"),
    "seo_title": ("catalog", "editar"),
    "seo_description": ("catalog", "editar"),
    "gtin": ("catalog", "editar"),
    "web_manual": ("catalog", "editar"),
}
# lo que escribe una persona: el generador de borradores (O3-11) no lo pisa (web_manual)
HAND_FIELDS = ("web_name", "web_subtitle", "web_type", "web_description")
PERM_MSG = {
    "show_on_web": "Solo el administrador publica o despublica productos en la tienda",
    "web_price": "Sin permiso: catalog.precios",
    "compare_price": "Sin permiso: catalog.precios",
}
# lo que cambia el precio o la venta: el guardian de margen lo vuelve a mirar
MARGIN_FIELDS = {"show_on_web", "web_price"}


def _images(p: Product) -> list[str]:
    return [str(i.get("url")) for i in (p.images or []) if isinstance(i, dict) and i.get("url")]


def faltas_of(p: Product, state: str | None, backed: bool) -> list[str]:
    from .store_health import external_image

    imgs = _images(p)
    out = []
    if not (p.description_store or "").strip():
        out.append("sin_descripcion")
    if len(imgs) == 1:
        out.append("una_foto")
    if not imgs:
        out.append("sin_foto")
    if not (p.specs or {}) and not (p.datasheet_url or "").strip():
        out.append("sin_ficha")
    if len(p.name or "") > NOMBRE_LARGO:
        out.append("nombre_largo")
    if any(external_image(u) for u in imgs):
        out.append("foto_proveedor")
    if state == "consultar":
        out.append("por_confirmar")
    if backed:
        out.append("en_oferta")
    return out


def _gross(net, rate) -> Decimal:
    return (Decimal(str(net or 0)) * (1 + Decimal(str(rate)) / 100)).quantize(Decimal("0.01"))


def tax_rate(p: Product) -> Decimal:
    return Decimal(str(p.taxes[0].tax.rate)) if p.taxes else Decimal(13)


def listing(
    db: Session,
    p,
    *,
    q: str | None = None,
    category_id: int | None = None,
    brand: str | None = None,
    estado: str | None = None,
    publicado: str = "si",
    falta: str | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
) -> dict:
    """La pagina pedida del catalogo web y el numero de cada ficha de filtro (sobre la busqueda, categoria, marca y
    publicado elegidos: lo que da cada filtro si se toca)."""
    from ..routers.store import Pricer
    from . import store_showcase as sv
    from .availability import availability_many
    from .store_snapshot import compare_backed

    t = p.tenant
    stmt = select(Product).where(Product.tenant_id == t.id, Product.active).options(selectinload(Product.taxes).selectinload(ProductTax.tax))
    if publicado == "si":
        stmt = stmt.where(Product.show_on_web)
    elif publicado == "no":
        stmt = stmt.where(Product.show_on_web.is_(False))
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if category_id:
        cats = list(db.scalars(select(Category).where(Category.tenant_id == t.id)))
        ids, frontier = {category_id}, {category_id}
        while frontier:
            frontier = {c.id for c in cats if c.parent_id in frontier} - ids
            ids |= frontier
        stmt = stmt.where(Product.category_id.in_(ids))
    prods = list(db.scalars(stmt.order_by(Product.name)))
    if q and q.strip():
        terms = sv.split_terms(q)
        prods = [x for x in prods if all(term in sv.fold(f"{x.name} {x.code} {x.brand or ''} {x.model or ''}") for term in terms)]
    av = availability_many(db, t, prods, audience="publico") if prods else {}
    backed = compare_backed(db, t.id, [x for x in prods if x.compare_price])
    rows = []
    for x in prods:
        st = (av.get(x.id) or {}).get("state")
        rows.append((x, st, faltas_of(x, st, x.id in backed and Decimal(str(x.compare_price or 0)) > Decimal(str(sv_web_price(x) or 0)))))
    cuenta_faltas = {k: sum(1 for _, _, f in rows if k in f) for k in FALTAS}
    cuenta_estados = {k: sum(1 for _, s, _ in rows if s == k) for k in ESTADOS}
    if estado:
        rows = [r for r in rows if r[1] == estado]
    if falta:
        rows = [r for r in rows if falta in r[2]]
    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    chunk = rows[(page - 1) * per_page : page * per_page]
    pricer = Pricer(db, t)
    precios = p.sees_prices
    cat_name = dict(db.execute(select(Category.id, Category.name).where(Category.tenant_id == t.id)).all())
    items = []
    for x, st, f in chunk:
        a = av.get(x.id) or {}
        imgs = _images(x)
        items.append(
            {
                "id": x.id,
                "name": x.name,
                "code": x.code,
                "brand": x.brand,
                "category": cat_name.get(x.category_id),
                "image": imgs[0] if imgs else None,
                "photos": len(imgs),
                "show_on_web": bool(x.show_on_web),
                "web_featured": bool(x.web_featured),
                "state": st,
                "label": a.get("label"),
                "accion": a.get("accion"),
                "faltas": f,
                "display_price": _gross(pricer.of(x), tax_rate(x)) if precios else None,
                "currency": pricer.currency,
                "path": sv.product_path(x),
            }
        )
    brands = sorted({x.brand for x in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.active, Product.show_on_web)) if x.brand})
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "faltas": [{"key": k, "label": v, "n": cuenta_faltas[k]} for k, v in FALTAS.items()],
        "estados": [{"key": k, "label": v, "n": cuenta_estados[k]} for k, v in ESTADOS.items() if cuenta_estados[k] or k != "servicio"],
        "brands": brands,
        "categories": [{"id": i, "name": n} for i, n in sorted(cat_name.items(), key=lambda kv: kv[1] or "")],
    }


def sv_web_price(p: Product):
    from .store_inventory import web_price

    return web_price(p)


def _snap(pr: Product, keys) -> dict:
    out = {}
    for k in keys:
        v = getattr(pr, k)
        out[k] = str(v) if isinstance(v, (Decimal, float)) and k in ("web_price", "compare_price") else v
    return out


def check_perms(p, keys) -> None:
    for k in keys:
        mod, act = FIELD_PERMS[k]
        if not p.can(mod, act) or not p.can("catalog", "editar") and k != "show_on_web":
            raise HTTPException(403, PERM_MSG.get(k, f"Sin permiso: {mod}.{act}"))


def apply(db: Session, p, pr: Product, vals: dict, *, desde: str, action: str = "web", only_changed_perms: bool = True) -> set[str]:
    """Escribe los campos web de un producto (solo flush; quien llama hace commit). Devuelve los campos que cambiaron.

    Permisos por campo: se exigen para lo que CAMBIA (only_changed_perms) — un panel que reenvia la publicacion como
    esta no necesita catalog.publicar. Guardian de margen si cambia el precio web o la publicacion; una sola entrada
    de bitacora con antes y despues; el catalogo se marca una vez para la vitrina y el snapshot."""
    from ..services import margin_guard
    from . import store_showcase as sv
    from .store_snapshot import mark_dirty_after_commit

    unknown = set(vals) - set(FIELD_PERMS)
    if unknown:
        raise HTTPException(422, f"Campo no editable desde la tienda: {', '.join(sorted(unknown))}")
    before = _snap(pr, vals)
    norm = dict(vals)
    if "description_store" in norm:
        norm["description_store"] = (norm["description_store"] or "").strip() or None
    if "datasheet_url" in norm:
        norm["datasheet_url"] = (norm["datasheet_url"] or "").strip() or None
    if "specs" in norm:
        norm["specs"] = norm["specs"] or None
    if "brand" in norm:
        norm["brand"] = (norm["brand"] or "").strip() or None
    if "compare_price" in norm:
        norm["compare_price"] = norm["compare_price"] or None
    for k in ("web_name", "web_subtitle", "web_type", "web_description", "seo_title", "seo_description", "gtin"):
        if k in norm and isinstance(norm[k], str | None):
            norm[k] = (norm[k] or "").strip() or None
    if "web_manual" in norm and norm["web_manual"] is None:
        del norm["web_manual"]
    if "category_id" in norm and norm["category_id"] is not None:
        cat = db.get(Category, norm["category_id"])
        if not cat or cat.tenant_id != p.tenant.id:
            raise HTTPException(404, "Categoría no encontrada")

    def differs(k: str) -> bool:
        a, b = getattr(pr, k), norm[k]
        if k in ("web_price", "compare_price"):
            return (None if a is None else Decimal(str(a))) != (None if b is None else Decimal(str(b)))
        if k in ("show_on_web", "web_featured", "web_manual"):
            return bool(a) != bool(b)
        return a != b

    changed = {k for k in norm if differs(k)}
    if "web_manual" not in norm and changed & set(HAND_FIELDS) and not pr.web_manual:
        norm["web_manual"] = True  # lo escribio una persona: ningun lote lo pisa
        changed.add("web_manual")
    check_perms(p, changed if only_changed_perms else set(norm))
    for k in changed:
        setattr(pr, k, norm[k])
    if not changed:
        return changed
    if changed & MARGIN_FIELDS:
        try:
            margin_guard.recheck(db, p.tenant, pr)
        except Exception:  # noqa: BLE001  guardar nunca falla por el guardian (como en catalog._margin_recheck)
            pass
    sv.bump_catalog_version(db, p.tenant)
    mark_dirty_after_commit(db, p.tenant.id)
    after = _snap(pr, changed)
    audit(
        db, p.tenant.id, p.user.id, action, "product", pr.id, {"desde": desde, "code": pr.code, "campos": sorted(changed)}, ip=p.ip,
        before={k: before[k] for k in changed}, after=after,
    )  # fmt: skip
    return changed


def detail(db: Session, p, pr: Product) -> dict:
    """Todo lo web de un producto para el cajon de Mi Tienda: textos, precio (con IVA), fotos, ficha, disponibilidad,
    historial de precio (ofertas solo con respaldo) y como se ve en Google."""
    from ..models import ProductPriceHistory
    from ..routers.store import Pricer
    from ..routers.store_seo import seo_block
    from . import product_typing as pt
    from . import store_inventory
    from . import store_showcase as sv
    from .availability import availability_many
    from .store_snapshot import COMPARE_MIN_DAYS, compare_backed

    t = p.tenant
    precios = p.sees_prices
    pub = availability_many(db, t, [pr], audience="publico").get(pr.id) or {}
    inner = store_inventory.availability(db, t, [pr.id], public=False).get(pr.id) or {}
    cats = list(db.scalars(select(Category).where(Category.tenant_id == t.id)))
    rate = tax_rate(pr)
    pricer = Pricer(db, t)
    backed = pr.id in compare_backed(db, t.id, [pr]) if pr.compare_price else False
    hist = []
    if precios:
        for h in db.scalars(
            select(ProductPriceHistory).where(ProductPriceHistory.product_id == pr.id).order_by(ProductPriceHistory.valid_from.desc()).limit(12)
        ):
            hist.append({"valid_from": h.valid_from, "price_final": h.price_final, "currency": h.currency, "source": h.source})
    path = sv.product_path(pr)
    try:
        seo = seo_block(db, t, f"/p/{path}")
    except Exception:  # noqa: BLE001  la vista previa de Google nunca tumba el cajon
        seo = None
    return {
        "id": pr.id,
        "name": pr.name,
        "code": pr.code,
        "brand": pr.brand,
        "model": pr.model,
        "category_id": pr.category_id,
        "categories": [{"id": c.id, "name": c.name} for c in sorted(cats, key=lambda c: c.name or "")],
        "description_store": pr.description_store or "",
        "images": [{"url": u} for u in _images(pr)],
        "specs": pr.specs or {},
        "derived_specs": sv.derive_specs(pr, pt.display_type(pr, *pt.category_chain(cats).get(pr.category_id, (None, None)))),
        "datasheet_url": pr.datasheet_url or "",
        "show_on_web": bool(pr.show_on_web),
        "web_featured": bool(pr.web_featured),
        "currency": pr.currency,
        "store_currency": pricer.currency,
        "tax_rate": rate,
        "price": pr.price if precios else None,
        "web_price": pr.web_price if precios else None,
        "compare_price": pr.compare_price if precios else None,
        "display_price": _gross(pricer.of(pr), rate) if precios else None,
        "compare_backed": backed,
        "compare_min_days": COMPARE_MIN_DAYS,
        "price_history": hist,
        "margin_hold": bool(pr.margin_hold),
        "availability": {
            "publico": {k: pub.get(k) for k in ("state", "label", "accion", "plazo")},
            "interno": {k: inner.get(k) for k in ("estado", "mensaje", "propio", "libre", "proveedor", "proveedor_al")},
        },
        "path": path,
        "seo": seo,
    }
