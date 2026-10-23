"""Inventarios -> Artículos por completar: lo que NO está a la venta en la tienda porque le falta un dato.

Dos fuentes (pedido 23/10, "solo lo que no se ve en la tienda por falta de datos"):

(a) Artículos de Eurocomp de las familias de auto_catalog que no llegaron a ser producto: SiReTT no publica su costo
    (auto_catalog los cuenta en `skip_sin_costo`). Lo que el admin borró o descartó (link_method con product_id vacío,
    `skip_borrado`) NO aparece: esa decisión se respeta.
(b) Productos activos sin precio de venta (precio 0): en la tienda salen como «Consultar» y el checkout no los vende.
    Entran los publicados y los del catálogo del proveedor (vinculados a un artículo de Eurocomp), publicados o no.
    Los retenidos por el guardián de margen (margin_hold) tienen precio: viven en Aprobaciones -> Precios por revisar.

No entra lo que la tienda ya muestra y vende aunque le falte un dato opcional (foto, descripción, categoría o CABYS):
el CABYS no detiene ni la vitrina ni el checkout (la factura de una venta sin CABYS se completa en Productos ->
Completar CABYS). Esos datos se muestran como "también falta" para aprovechar la visita.

El producto se crea con supplier_sync.product_from_item, el mismo camino de auto_catalog (código, categoría
padre/hija, IVA, Hikvision destacado, vínculo "creado"). La fila sale de la lista cuando tiene precio.
"""

from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Category, Product, Supplier, SupplierItem, Tenant
from ..providers.sirett import image_url
from . import supplier_catalog as sc
from . import supplier_sync as ss
from . import tabular as tb

REASONS = {
    "sin_costo": "Eurocomp no publica su costo: no se pudo crear el producto",
    "sin_precio": "Sin precio de venta: en la tienda sale «Consultar» y no se puede comprar",
}
CABYS_RE = re.compile(r"\d{13}")


def _families(t: Tenant) -> list[str]:
    return [f for f in (ss.ac_settings(t).get("families") or []) if f]


def _items_stmt(t: Tenant, supplier: Supplier):
    return select(SupplierItem).where(
        SupplierItem.tenant_id == t.id,
        SupplierItem.supplier_id == supplier.id,
        SupplierItem.active,
        SupplierItem.category.in_(_families(t)),
        SupplierItem.product_id.is_(None),
        SupplierItem.link_method.is_(None),  # borrado o descartado por el admin: no vuelve (skip_borrado)
        or_(SupplierItem.cost.is_(None), SupplierItem.cost <= 0),
    )


def _products_stmt(t: Tenant):
    linked = select(SupplierItem.id).where(SupplierItem.product_id == Product.id, SupplierItem.active).exists()
    return select(Product).where(
        Product.tenant_id == t.id,
        Product.active,
        Product.item_type == "producto",
        or_(Product.price.is_(None), Product.price <= 0),
        Product.margin_hold.is_(False),
        or_(Product.show_on_web.is_(True), linked),
    )


def pending_items(db: Session, t: Tenant) -> list[SupplierItem]:
    supplier = ss.find_supplier(db, t.id)
    if supplier is None or not _families(t):
        return []
    return list(db.scalars(_items_stmt(t, supplier).order_by(SupplierItem.category, SupplierItem.subcategory, SupplierItem.name)))


def pending_products(db: Session, t: Tenant) -> list[Product]:
    return list(db.scalars(_products_stmt(t).order_by(Product.name)))


def count(db: Session, t: Tenant) -> int:
    return len(pending_items(db, t)) + len(pending_products(db, t))


def item_images(it: SupplierItem) -> list[dict]:
    raw = it.raw or {}
    urls = [u for k in ("image_url", *(f"image_url_{n}" for n in range(1, 6))) if (u := image_url(raw.get(k)))]
    return [{"url": u, "main": n == 0} for n, u in enumerate(dict.fromkeys(urls))][: ss.MAX_IMAGES]


def item_cabys(it: SupplierItem) -> str | None:
    code = ((it.raw or {}).get("cod_hacienda") or "").strip()
    return code if CABYS_RE.fullmatch(code) else None


def item_specs(it: SupplierItem) -> str | None:
    return "\n".join((it.description or "").splitlines()[1:]).strip() or None


def _find_cat(cats: list[Category], name: str | None, parent_id: int | None | bool = False) -> Category | None:
    if not name:
        return None
    for c in cats:
        if c.name.lower() == name.lower() and (parent_id is False or c.parent_id == parent_id):
            return c
    return None


def suggested_category(cats: list[Category], it: SupplierItem) -> tuple[int | None, str | None]:
    """La categoría que le pondría auto_catalog SIN crearla todavía: (id existente, o None + el nombre que se creará)."""
    if c := _find_cat(cats, it.subcategory):
        return c.id, None
    if not it.category:
        return None, None
    if not it.subcategory and (c := _find_cat(cats, it.category)):
        return c.id, None
    return None, f"{it.category} › {it.subcategory}" if it.subcategory else it.category


def _cat_path(cats: dict[int, Category], cid: int | None) -> tuple[str | None, str | None]:
    """(familia = categoría raíz, ruta legible) de una categoría propia."""
    if not cid or cid not in cats:
        return None, None
    names, cur, seen = [], cats.get(cid), set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        names.append(cur.name)
        cur = cats.get(cur.parent_id) if cur.parent_id else None
    names.reverse()
    return names[0], " › ".join(names)


def _extras(*, images, cabys, description, category) -> list[str]:
    out = []
    if not images:
        out.append("foto")
    if not (cabys and CABYS_RE.fullmatch(cabys)):
        out.append("cabys")
    if not (description or "").strip():
        out.append("descripcion")
    if not category:
        out.append("categoria")
    return out


def rows(db: Session, t: Tenant, *, sees_costs: bool, sees_prices: bool) -> list[dict]:
    cats_l = list(db.scalars(select(Category).where(Category.tenant_id == t.id)))
    cats = {c.id: c for c in cats_l}
    fx = sc.usd_rate(db)
    publish_default = bool(ss.ac_settings(t).get("publish_web"))
    out: list[dict] = []
    for it in pending_items(db, t):
        imgs = item_images(it)
        cid, new_cat = suggested_category(cats_l, it)
        cabys = item_cabys(it)
        specs = item_specs(it)
        r = {
            "key": f"i{it.id}",
            "kind": "proveedor",
            "id": it.id,
            "code": it.code,
            "name": it.name,
            "brand": it.brand,
            "model": it.model,
            "family": it.category,
            "category": _cat_path(cats, cid)[1] if cid else new_cat,
            "category_id": cid,
            "category_new": new_cat,
            "reason": "sin_costo",
            "reason_label": REASONS["sin_costo"],
            "missing": _extras(images=imgs, cabys=cabys, description=specs, category=cid or new_cat),
            "images": imgs,
            "cabys_code": cabys,
            "description_store": specs,
            "stock": it.stock,
            "show_on_web": publish_default,
            "price": None,
            "currency": "CRC",
        }
        out.append(r)
    for p in pending_products(db, t):
        fam, path = _cat_path(cats, p.category_id)
        r = {
            "key": f"p{p.id}",
            "kind": "producto",
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "brand": p.brand,
            "model": p.model,
            "family": fam,
            "category": path,
            "category_id": p.category_id,
            "category_new": None,
            "reason": "sin_precio",
            "reason_label": REASONS["sin_precio"],
            "missing": _extras(images=p.images, cabys=p.cabys_code, description=p.description_store, category=p.category_id),
            "images": list(p.images or []),
            "cabys_code": p.cabys_code,
            "description_store": p.description_store,
            "stock": p.supplier_stock,
            "show_on_web": bool(p.show_on_web),
            "price": p.price if sees_prices else None,
            "currency": p.currency,
        }
        if sees_costs:
            r.update(cost=p.cost, cost_currency=p.cost_currency, cost_crc=sc.to_crc(p.cost, p.cost_currency, fx) if p.cost is not None else None)
        out.append(r)
    if sees_costs:
        for r in out:
            r.setdefault("cost", None)
            r.setdefault("cost_crc", None)
    return out


def filtered(all_rows: list[dict], *, reason: str | None, family: str | None, brand: str | None, q: str | None) -> tuple[list[dict], dict]:
    """Filtra por familia, marca y búsqueda (sin tildes); los conteos por motivo salen de eso, antes del filtro de motivo."""
    words = [w for w in tb.norm(q).split("_") if w] if q else []

    def hay(r: dict) -> str:
        return tb.norm(" ".join(str(x) for x in (r["code"], r["name"], r.get("model") or "", r.get("brand") or "") if x)).replace("_", "")

    base = [
        r
        for r in all_rows
        if (not family or (r.get("family") or "") == family)
        and (not brand or (r.get("brand") or "").lower() == brand.lower())
        and (not words or all(w in hay(r) for w in words))
    ]
    counts = {k: sum(1 for r in base if r["reason"] == k) for k in REASONS}
    return ([r for r in base if not reason or r["reason"] == reason], counts)

