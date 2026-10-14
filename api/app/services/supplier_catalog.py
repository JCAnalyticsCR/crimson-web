"""Catalogo de ofertas de equipo: lo propio (Product + existencias) y lo de proveedores (Eurocomp).

STUB: se reemplaza al integrar con la version real del catalogo de proveedores. Esta version busca SOLO en el
catalogo propio (Product: nombre, modelo, marca, categoria, costo y existencias propias). Las firmas y el formato
de cada oferta son los de la version real; la recomendacion de equipos (services/equipment_reco.py) solo usa esto.

Cada oferta es un dict:
  source 'propio'|'proveedor', product_id|None, supplier_item_id|None, supplier, code, name, model, cost, currency,
  cost_crc, sale_price (si es propio), stock_propio, stock_proveedor, updated_at
y, como extra opcional de este stub, brand, unit, image y sale_currency (la recomendacion no depende de ellos).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Category, Product, StockMovement, Supplier
from . import pricing
from .documents import today_fx
from .store_showcase import compact, fold


def _image(p: Product) -> str | None:
    imgs = [x for x in (p.images or []) if isinstance(x, dict) and x.get("url")]
    main = next((x for x in imgs if x.get("main")), imgs[0] if imgs else None)
    return main["url"] if main else None


def _stock(db: Session, tenant_id: int, ids: list[int]) -> dict[int, Decimal]:
    if not ids:
        return {}
    q = (
        select(StockMovement.product_id, func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.tenant_id == tenant_id, StockMovement.product_id.in_(ids))
        .group_by(StockMovement.product_id)
    )
    return {pid: Decimal(str(qty or 0)) for pid, qty in db.execute(q)}


def _offer(db: Session, p: Product, stock: Decimal, supplier: str | None, fx: Decimal) -> dict:
    cost = Decimal(str(p.cost)) if p.cost is not None else None
    return {
        "source": "propio",
        "product_id": p.id,
        "supplier_item_id": None,
        "supplier": supplier,
        "code": p.code,
        "name": p.name,
        "model": p.model,
        "cost": cost,
        "currency": p.cost_currency or "USD",
        "cost_crc": pricing.cost_in(db, p, "CRC", fx).quantize(Decimal("0.01")) if cost is not None else None,
        "sale_price": Decimal(str(p.price)) if p.price is not None else None,
        "stock_propio": stock,
        "stock_proveedor": Decimal(p.supplier_stock) if p.supplier_stock is not None else None,
        "updated_at": p.supplier_updated_at or p.updated_at,
        # extras del stub
        "brand": p.brand,
        "unit": p.unit or "Unid",
        "image": _image(p),
        "sale_currency": p.currency or "CRC",
    }


def _matches(hay: str, comp: str, term: str) -> bool:
    t, c = fold(term).strip(), compact(term)
    return bool(t) and (t in hay or (len(c) >= 3 and c in comp))


def search_offers(
    db: Session,
    tenant_id: int,
    *,
    terms: list[str] = (),
    category: str | None = None,
    attrs: dict | None = None,
    limit: int = 10,
) -> list[dict]:
    """Ofertas que contienen todos los terminos (sin tildes ni mayusculas) en nombre, codigo, modelo, marca o
    categoria. attrs no filtra: los valores que aparecen en el texto suben la oferta en la lista."""
    rows = db.execute(
        select(Product.id, Product.name, Product.code, Product.model, Product.brand, Product.supplier_sku, Category.name)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(Product.tenant_id == tenant_id, Product.active.is_(True), Product.item_type == "producto")
    ).all()
    wanted = [t for t in (terms or ()) if str(t or "").strip()]
    soft = [str(v) for v in (attrs or {}).values() if isinstance(v, (str, int, float)) and str(v).strip() and not isinstance(v, bool)]
    hits: list[tuple[int, int]] = []
    for pid, name, code, model, brand, sku, cat in rows:
        raw = " ".join(x or "" for x in (name, code, model, brand, sku, cat))
        hay, comp = " " + fold(raw) + " ", compact(raw)
        if not all(_matches(hay, comp, t) for t in wanted):
            continue
        if category and not _matches(hay, comp, category):
            continue
        hits.append((sum(1 for v in soft if _matches(hay, comp, v)), pid))
    hits.sort(key=lambda x: (-x[0], x[1]))
    ids = [pid for _, pid in hits[: max(1, int(limit))]]
    if not ids:
        return []
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}
    stock = _stock(db, tenant_id, ids)
    sup_ids = {p.supplier_id for p in prods.values() if p.supplier_id}
    sups = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.id.in_(sup_ids)))} if sup_ids else {}
    fx = today_fx(db, "USD")[0]
    return [_offer(db, prods[i], stock.get(i, Decimal(0)), sups.get(prods[i].supplier_id), fx) for i in ids if i in prods]


def best_offer_for_product(db: Session, product: Product | None) -> dict | None:
    """La mejor oferta para un producto propio. En el stub es el mismo producto (su costo y sus existencias)."""
    if product is None:
        return None
    sup = db.get(Supplier, product.supplier_id) if product.supplier_id else None
    stock = _stock(db, product.tenant_id, [product.id]).get(product.id, Decimal(0))
    return _offer(db, product, stock, sup.name if sup else None, today_fx(db, "USD")[0])
