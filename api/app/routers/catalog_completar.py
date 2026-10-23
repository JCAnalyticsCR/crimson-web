"""Inventarios -> Artículos por completar (services/catalog_completar): lo que no está a la venta en la tienda porque le
falta un dato, con el cajón para completarlo y crear o publicar.

Permisos: ver y completar = catalog.editar (admin e inventario). Crear el producto = catalog.crear. Mostrar u ocultar
en la tienda = catalog.publicar (solo el admin). El costo y el margen solo llegan a quien ve costos (catalog.costos):
la vendedora nunca los ve (ni tiene esta pantalla). Todo cambio sube catalog_v (la tienda rearma su snapshot) y queda
en la bitácora.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Category, Product, SupplierItem, Tax
from ..services import catalog_completar as cc
from ..services import pricing
from ..services import store_showcase as sv
from ..services import supplier_catalog as sc
from ..services import supplier_sync as ss
from ..services.documents import audit

log = logging.getLogger("crimson.catalogo")
router = APIRouter(prefix="/catalog-completar", tags=["catalogo"])


class Img(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    main: bool = False


def _images(v: list[Img] | None) -> list[dict] | None:
    if v is None:
        return None
    out = [{"url": i.url, "main": bool(i.main)} for i in v[: ss.MAX_IMAGES]]
    if out and not any(i["main"] for i in out):
        out[0]["main"] = True
    return out


def _cabys(v: str | None) -> str | None:
    code = "".join(ch for ch in (v or "") if ch.isdigit())
    if not code:
        return None
    if not cc.CABYS_RE.fullmatch(code):
        raise HTTPException(422, "El CABYS lleva 13 dígitos")
    return code


def _category(db: Session, p: Principal, cid: int | None) -> int | None:
    if not cid:
        return None
    c = db.get(Category, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Categoría no encontrada")
    return c.id


def _recheck(db: Session, p: Principal, pr: Product) -> None:
    from ..services import margin_guard

    try:
        margin_guard.recheck(db, p.tenant, pr)
    except Exception:  # noqa: BLE001
        log.exception("guardian de margen al completar el producto %s", pr.id)


def _strip(r: dict, p: Principal) -> dict:
    if not p.sees_costs:
        for k in ("cost", "cost_currency", "cost_crc"):
            r.pop(k, None)
    return r


@router.get("")
def listado(
    reason: str | None = Query(None, pattern="^(sin_costo|sin_precio)$"),
    family: str | None = Query(None, max_length=120),
    brand: str | None = Query(None, max_length=60),
    q: str | None = Query(None, max_length=120),
    p: Principal = Depends(require("catalog", "editar")),
    db: Session = Depends(get_db),
):
    todos = cc.rows(db, p.tenant, sees_costs=p.sees_costs, sees_prices=p.sees_prices)
    out, counts = cc.filtered(todos, reason=reason, family=family, brand=brand, q=q)
    return {
        "items": [_strip(r, p) for r in out],
        "counts": counts,
        "total": len(todos),
        "reasons": cc.REASONS,
        "families": sorted({r["family"] for r in todos if r.get("family")}),
        "brands": sorted({r["brand"] for r in todos if r.get("brand")}, key=str.lower),
        "fx": sc.usd_rate(db) if p.sees_costs else None,
        "can_publish": p.can("catalog", "publicar"),
        "can_create": p.can("catalog", "crear"),
    }


@router.get("/count")
def contador(p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """El número del menú Inventarios: solo lo que no está a la venta por falta de datos."""
    return {"total": cc.count(db, p.tenant)}


class CreateIn(BaseModel):
    price: Decimal = Field(gt=0, le=Decimal("999999999"))
    currency: str = Field("CRC", pattern="^(CRC|USD)$")
    category_id: int | None = None
    cabys_code: str | None = Field(None, max_length=20)
    description_store: str | None = Field(None, max_length=4000)
    images: list[Img] | None = Field(None, max_length=12)
    cost: Decimal | None = Field(None, gt=0, le=Decimal("999999999"))
    show_on_web: bool = False


@router.post("/items/{iid}/crear", status_code=201)
def crear(iid: int, data: CreateIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    """Crear producto (y publicarlo, si el admin deja el interruptor encendido) desde un artículo de Eurocomp sin costo.
    Mismo camino que auto_catalog (supplier_sync.product_from_item). El precio lo puso una persona: la sincronización
    semanal nunca lo toca (solo costo y existencias)."""
    it = db.get(SupplierItem, iid)
    if not it or it.tenant_id != p.tenant.id:
        raise HTTPException(404, "Artículo del proveedor no encontrado")
    if it.product_id:
        raise HTTPException(409, "Este artículo ya tiene producto en su catálogo")
    if it.link_method is not None:
        raise HTTPException(409, "El administrador borró o descartó este artículo: no se vuelve a crear desde aquí")
    if data.show_on_web and not p.can("catalog", "publicar"):
        raise HTTPException(403, "Mostrar en la tienda en línea es solo del administrador: cree el producto sin publicarlo")
    sup = db.get(ss.Supplier, it.supplier_id)
    cost = data.cost if p.sees_costs else None
    fx = sc.usd_rate(db)
    margin = None
    if cost is not None:
        cost_crc, price_crc = sc.to_crc(cost, it.currency or "USD", fx), sc.to_crc(data.price, data.currency, fx)
        margin = pricing.margin_of(price_crc, cost_crc) if cost_crc and price_crc else None
    iva = db.scalar(select(Tax).where(Tax.tenant_id == p.tenant.id, Tax.rate_code == "08", Tax.active))
    imgs = _images(data.images)
    prod = ss.product_from_item(
        db,
        p.tenant,
        it,
        sup.name if sup else None,
        price=data.price,
        margin=margin,
        publish=data.show_on_web,
        iva=iva,
        fx=fx,
        user_id=p.user.id,
        currency=data.currency,
        category_id=_category(db, p, data.category_id),
        cabys_code=_cabys(data.cabys_code) if data.cabys_code is not None else cc.item_cabys(it),
        description_store=(data.description_store or "").strip() or None if data.description_store is not None else None,
        images=imgs if imgs is not None else cc.item_images(it),
        cost=cost,
    )
    _recheck(db, p, prod)
    sv.bump_catalog_version(db, p.tenant)
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "create",
        "product",
        prod.id,
        {"desde": "articulos_por_completar", "supplier_item": it.id, "code": prod.code, "price": str(prod.price), "currency": prod.currency, "show_on_web": prod.show_on_web},
        ip=p.ip,
    )
    db.commit()
    return {"product_id": prod.id, "code": prod.code, "name": prod.name, "show_on_web": prod.show_on_web}


@router.post("/items/{iid}/descartar")
def descartar(iid: int, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """No vender este artículo: sale de la lista y auto_catalog no lo crea (cuenta como skip_borrado)."""
    it = db.get(SupplierItem, iid)
    if not it or it.tenant_id != p.tenant.id:
        raise HTTPException(404, "Artículo del proveedor no encontrado")
    if it.product_id:
        raise HTTPException(409, "Este artículo ya tiene producto en su catálogo")
    it.link_method, it.linked_at, it.linked_by = "descartado", datetime.now(UTC), p.user.id
    audit(db, p.tenant.id, p.user.id, "discard", "supplier_item", it.id, {"code": it.code, "name": it.name}, ip=p.ip)
    db.commit()
    return {"ok": True}


class ProductPatch(BaseModel):
    price: Decimal | None = Field(None, ge=0, le=Decimal("999999999"))
    cabys_code: str | None = Field(None, max_length=20)
    description_store: str | None = Field(None, max_length=4000)
    category_id: int | None = None
    images: list[Img] | None = Field(None, max_length=12)
    show_on_web: bool | None = None


FIELDS = ("price", "cabys_code", "description_store", "category_id", "images", "show_on_web")


def _snap(pr: Product) -> dict:
    return {k: (str(getattr(pr, k)) if k == "price" and getattr(pr, k) is not None else getattr(pr, k)) for k in FIELDS}


@router.patch("/products/{pid}")
def completar_producto(pid: int, data: ProductPatch, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Completa lo que le falta a un producto: precio, CABYS, fotos, descripción, categoría y si se muestra en línea."""
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    sent = data.model_fields_set
    if "price" in sent and not p.sees_prices:
        raise HTTPException(403, "Sin permiso: catalog.precios")
    if "show_on_web" in sent and not p.can("catalog", "publicar"):
        raise HTTPException(403, "Mostrar en la tienda en línea es solo del administrador")
    before = _snap(pr)
    if "price" in sent and data.price is not None:
        pr.price = data.price
    if "cabys_code" in sent:
        code = _cabys(data.cabys_code)
        if code != pr.cabys_code:
            pr.cabys_code, pr.cabys_set_at, pr.cabys_set_by = code, datetime.now(UTC) if code else None, p.user.id if code else None
    if "description_store" in sent:
        pr.description_store = (data.description_store or "").strip() or None
    if "category_id" in sent:
        pr.category_id = _category(db, p, data.category_id)
    if "images" in sent:
        pr.images = _images(data.images) or []
    if "show_on_web" in sent and data.show_on_web is not None:
        pr.show_on_web = data.show_on_web
    after = _snap(pr)
    changed = {k for k in FIELDS if before[k] != after[k]}
    if changed:
        _recheck(db, p, pr)
        sv.bump_catalog_version(db, p.tenant)
        audit(
            db,
            p.tenant.id,
            p.user.id,
            "update",
            "product",
            pr.id,
            {"desde": "articulos_por_completar", "campos": sorted(changed)},
            ip=p.ip,
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    return {"id": pr.id, "changed": sorted(changed), "show_on_web": pr.show_on_web, "price": pr.price if p.sees_prices else None}


class WebBulkIn(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=500)
    show_on_web: bool


def set_web_many(db: Session, p: Principal, ids: list[int], on: bool, desde: str) -> int:
    """Mostrar u ocultar en la tienda en bloque (solo flush): sube catalog_v una vez y deja una entrada en la bitácora."""
    prods = list(db.scalars(select(Product).where(Product.tenant_id == p.tenant.id, Product.id.in_(set(ids)), Product.active)))
    changed = [pr for pr in prods if bool(pr.show_on_web) != on]
    for pr in changed:
        pr.show_on_web = on
        _recheck(db, p, pr)
    if changed:
        sv.bump_catalog_version(db, p.tenant)
        audit(
            db,
            p.tenant.id,
            p.user.id,
            "publish" if on else "unpublish",
            "product",
            None,
            {"desde": desde, "show_on_web": on, "productos": [(pr.id, pr.code) for pr in changed][:500], "n": len(changed)},
            ip=p.ip,
        )
    return len(changed)


@router.post("/web")
def web_bulk(data: WebBulkIn, p: Principal = Depends(require("catalog", "publicar")), db: Session = Depends(get_db)):
    n = set_web_many(db, p, data.product_ids, data.show_on_web, "articulos_por_completar")
    db.commit()
    return {"changed": n}
