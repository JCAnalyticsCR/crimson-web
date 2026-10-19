"""Mi Tienda: personalizacion, paginas con bloques, cupones, ordenes (admin) y sitio publico (catalogo, carrito, checkout)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import public_limiter
from ..models import Category, Coupon, Customer, Invoice, Order, Product, ProductVariant, StorePage, Tenant
from ..services import store_inventory as sinv
from ..services.documents import audit
from ..services.store_orders import STATUS_LABEL, aware
from ..services.totals import LineIn, compute_document, d

router = APIRouter(tags=["tienda"])

STORE_DEFAULTS = {
    "name": None,
    "tagline": "",
    "logo_url": None,
    "favicon_url": None,
    "primary": "#e2233a",
    "secondary": "#15131a",
    "font": "Arial",
    "domain": None,
    "published": False,
    "kind": "catalogo",  # catalogo | tienda (carrito y checkout)
    "shipping_rates": [{"name": "Retiro en tienda", "amount": 0, "active": True}, {"name": "Envío GAM", "amount": 3500, "active": True}],
    "legal": {"privacy": "", "terms": ""},
    "whatsapp": "",
    "currency": "CRC",
}


def manual_methods(t: Tenant) -> list[dict]:
    """Metodos de pago manuales del tenant; si no se han configurado, los predeterminados de Ajustes."""
    from .settings import DEFAULTS as SETTINGS_DEFAULTS

    methods = (t.settings or {}).get("manual_payment_methods") or SETTINGS_DEFAULTS["manual_payment_methods"]
    return [m for m in methods if m.get("active", True)]


def store_cfg(t: Tenant) -> dict:
    return {**STORE_DEFAULTS, **((t.settings or {}).get("store") or {}), "name": ((t.settings or {}).get("store") or {}).get("name") or t.name}


# ---------- Admin: personalizacion ----------
class StoreIn(BaseModel):
    name: str | None = None
    tagline: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    primary: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    secondary: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    font: str | None = None
    domain: str | None = None
    published: bool | None = None
    kind: str | None = Field(None, pattern="^(catalogo|tienda)$")
    shipping_rates: list[dict] | None = None
    legal: dict | None = None
    whatsapp: str | None = None
    currency: str | None = None


@router.get("/store")
def get_store(p: Principal = Depends(require("settings", "ver"))):
    return {**store_cfg(p.tenant), "slug": p.tenant.slug}


@router.put("/store")
def put_store(data: StoreIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    t = db.get(Tenant, p.tenant.id)
    st = {**(t.settings or {})}
    st["store"] = {**STORE_DEFAULTS, **(st.get("store") or {}), **data.model_dump(exclude_unset=True)}
    t.settings = st
    db.commit()
    return {**store_cfg(t), "slug": t.slug}


# ---------- Admin: paginas y bloques ----------
BLOCK_TYPES = {"hero", "text", "image_text", "cta", "columns", "products", "gallery", "form", "cases"}


class PageIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    title: str
    blocks: list[dict] = Field(default_factory=list)
    published: bool = False
    in_nav: bool = True
    position: int = 0


def _page_out(pg: StorePage):
    return {
        "id": pg.id,
        "slug": pg.slug,
        "title": pg.title,
        "blocks": pg.blocks,
        "published": pg.published,
        "in_nav": pg.in_nav,
        "position": pg.position,
        "updated_at": pg.updated_at,
    }


@router.get("/store/pages")
def pages(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return [_page_out(x) for x in db.scalars(select(StorePage).where(StorePage.tenant_id == p.tenant.id).order_by(StorePage.position, StorePage.id))]


@router.post("/store/pages", status_code=201)
def page_create(data: PageIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    for b in data.blocks:
        if b.get("type") not in BLOCK_TYPES:
            raise HTTPException(422, f"Bloque desconocido: {b.get('type')}")
    if db.scalar(select(StorePage).where(StorePage.tenant_id == p.tenant.id, StorePage.slug == data.slug)):
        raise HTTPException(409, "Ya existe una página con ese slug")
    pg = StorePage(tenant_id=p.tenant.id, **data.model_dump())
    db.add(pg)
    db.commit()
    return _page_out(pg)


@router.put("/store/pages/{pid}")
def page_update(pid: int, data: PageIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    pg = db.get(StorePage, pid)
    if not pg or pg.tenant_id != p.tenant.id:
        raise HTTPException(404, "Página no encontrada")
    for b in data.blocks:
        if b.get("type") not in BLOCK_TYPES:
            raise HTTPException(422, f"Bloque desconocido: {b.get('type')}")
    for k, v in data.model_dump().items():
        setattr(pg, k, v)
    db.commit()
    return _page_out(pg)


@router.delete("/store/pages/{pid}", status_code=204)
def page_delete(pid: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    pg = db.get(StorePage, pid)
    if not pg or pg.tenant_id != p.tenant.id:
        raise HTTPException(404, "Página no encontrada")
    db.delete(pg)
    db.commit()


# ---------- Admin: cupones ----------
class CouponIn(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    kind: str = Field("percent", pattern="^(percent|amount)$")
    value: Decimal = Field(gt=0)
    min_total: Decimal = Decimal(0)
    valid_from: date | None = None
    valid_to: date | None = None
    max_uses: int | None = None
    active: bool = True


def _coupon_out(c: Coupon):
    return {
        "id": c.id,
        "code": c.code,
        "kind": c.kind,
        "value": c.value,
        "min_total": c.min_total,
        "valid_from": c.valid_from,
        "valid_to": c.valid_to,
        "max_uses": c.max_uses,
        "uses": c.uses,
        "active": c.active,
    }


@router.get("/coupons")
def coupons(p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    # los codigos y montos de descuento vigentes son de quien administra la tienda (la seccion pide catalog.editar):
    # con catalog.ver (el tecnico, el aliado) los repartiria o los usaria en la tienda publica
    # los desactivados se siguen viendo (se reactivan aqui); lo archivado o en la papelera se ve en Archivo
    stmt = select(Coupon).where(Coupon.tenant_id == p.tenant.id, Coupon.archived_at.is_(None), Coupon.trashed_at.is_(None))
    return [_coupon_out(c) for c in db.scalars(stmt.order_by(Coupon.id.desc()))]


@router.post("/coupons", status_code=201)
def coupon_create(data: CouponIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    if db.scalar(select(Coupon).where(Coupon.tenant_id == p.tenant.id, Coupon.code == data.code.upper())):
        raise HTTPException(409, "Ese código ya existe")
    c = Coupon(tenant_id=p.tenant.id, **{**data.model_dump(), "code": data.code.upper()})
    db.add(c)
    db.commit()
    return _coupon_out(c)


@router.put("/coupons/{cid}")
def coupon_update(cid: int, data: CouponIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    c = db.get(Coupon, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Cupón no encontrado")
    for k, v in data.model_dump().items():
        setattr(c, k, v.upper() if k == "code" else v)
    db.commit()
    return _coupon_out(c)


def valid_coupon(db: Session, tenant_id: int, code: str | None, total: Decimal) -> Coupon | None:
    if not code:
        return None
    c = db.scalar(select(Coupon).where(Coupon.tenant_id == tenant_id, Coupon.code == code.upper(), Coupon.active))
    today = date.today()
    if (
        not c
        or (c.valid_from and c.valid_from > today)
        or (c.valid_to and c.valid_to < today)
        or (c.max_uses and c.uses >= c.max_uses)
        or total < d(c.min_total)
    ):
        raise HTTPException(422, "Cupón inválido o vencido")
    return c


# ---------- Admin: ordenes ----------
def _order_out(o: Order):
    return {
        "id": o.id,
        "number": o.number,
        "channel": o.channel,
        "customer_id": o.customer_id,
        "contact": o.contact,
        "currency": o.currency,
        "subtotal": o.subtotal,
        "discount_total": o.discount_total,
        "shipping": o.shipping,
        "tax_total": o.tax_total,
        "total": o.total,
        "coupon_code": o.coupon_code,
        "shipping_method": o.shipping_method,
        "payment_method": o.payment_method,
        "status": o.status,
        "invoice_id": o.invoice_id,
        "notes": o.notes,
        "created_at": aware(o.created_at),
        "status_label": STATUS_LABEL.get(o.status, o.status),
        "payment_kind": o.payment_kind,
        "delivery": o.delivery,
        "paid_at": aware(o.paid_at),
        "payment_ref": o.payment_ref,
        "tracking_number": o.tracking_number,
        "carrier": o.carrier,
        "cancel_reason": o.cancel_reason,
        "refund": o.refund,
        "history": o.history or [],
        "proofs": o.proofs or [],
        "expires_at": aware(o.expires_at),
        "inventory_state": o.inventory_state,
        "lines": [
            {
                "id": ln.id,
                "product_id": ln.product_id,
                "name": ln.name,
                "quantity": ln.quantity,
                "unit_price": ln.unit_price,
                "tax_rate": ln.tax_rate,
                "subtotal": ln.subtotal,
                "tax_amount": ln.tax_amount,
                "total": ln.total,
            }
            for ln in o.lines
        ],
    }


@router.get("/orders")
def orders(status: str | None = None, limit: int = Query(50, le=200), p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    from ..services.ownership import visible_invoice_ids

    q = select(Order).where(Order.tenant_id == p.tenant.id)
    if status:
        q = q.where(Order.status == status)
    rows = db.scalars(q.order_by(Order.id.desc()).limit(limit)).all()
    # la factura del pedido se enlaza solo si es del que mira (services/ownership.py): la de otro es un numero sin enlace
    mias = visible_invoice_ids(db, p, [o.invoice_id for o in rows])
    return [{**_order_out(o), "invoice_visible": (o.invoice_id in mias) if o.invoice_id else None} for o in rows]


class OrderStatusIn(BaseModel):
    status: str = Field(pattern="^(nuevo|pagado|preparando|enviado|entregado|cancelado)$")


@router.patch("/orders/{oid}")
def order_status(oid: int, data: OrderStatusIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    """Endpoint de siempre (eventos, POS, integraciones): sin reglas de transicion. La gestion de pedidos web usa
    /store-orders/{id}/status, que valida saltos y avisa al cliente."""
    from ..services import store_orders as so

    o = db.get(Order, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Orden no encontrada")
    old = o.status
    o.status = data.status
    if data.status == "pagado":
        o.paid_at = o.paid_at or datetime.now(UTC)
    so._inventory(db, o, old, data.status, p.user.id)
    so._hist(o, data.status, p.user.id)
    from .events import on_order_status

    tickets = on_order_status(db, o, data.status)  # entradas de evento: pagado activa y envia, cancelado anula
    audit(db, p.tenant.id, p.user.id, "status", "order", o.id, {"status": data.status, "tickets": tickets}, ip=p.ip)
    db.commit()
    return _order_out(o)


@router.post("/orders/{oid}/invoice", status_code=201)
def order_to_invoice(
    oid: int,
    doc_type: str = "TE",
    register_payment: bool = False,
    p: Principal = Depends(require("sales", "crear")),
    db: Session = Depends(get_db),
):
    """Convierte la orden en tiquete (TE, consumidor final) o factura (FE, requiere cliente con identificacion).
    register_payment: si el pedido ya se pago, deja el cobro aplicado a la factura (asi no queda "por cobrar")."""
    from ..services.store_orders import invoice_order

    o = db.get(Order, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Orden no encontrada")
    invoice = invoice_order(db, p.tenant, o, p.user.id, doc_type, register_payment, ip=p.ip)
    db.commit()
    return {"invoice_id": invoice.id, "number": invoice.number}


# ---------- Publico: tienda ----------
pub = APIRouter(prefix="/public/store/{slug}", tags=["publico"])


def _tenant(db: Session, slug: str) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug, Tenant.active))
    if not t or not store_cfg(t)["published"]:
        raise HTTPException(404, "Tienda no disponible")
    return t


def _variants(db: Session, product_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    if not product_ids:
        return out
    q = select(ProductVariant).where(ProductVariant.product_id.in_(product_ids), ProductVariant.active).order_by(ProductVariant.position, ProductVariant.id)
    for v in db.scalars(q):
        out.setdefault(v.product_id, []).append({"id": v.id, "name": v.name, "price": v.price, "options": v.options})
    return out


def _stock_of(db: Session, tenant_id: int, product_ids: list[int]) -> dict[int, dict]:
    """Disponibilidad web real por producto (bodegas web - reservas; ver services/store_inventory)."""
    return sinv.availability(db, tenant_id, product_ids)


class Pricer:
    """Lleva cualquier precio a la moneda de la tienda con el tipo de cambio del dia (venta BCCR).
    El catalogo puede mezclar monedas (Fygaro publicaba en dolares, la lista de proveedor queda en colones):
    el cliente ve y paga TODO en una sola moneda, la de Mi Tienda."""

    def __init__(self, db: Session, t: Tenant):
        from ..services.documents import today_fx

        self.currency = (store_cfg(t).get("currency") or t.default_currency or "CRC").upper()
        self.usd = d(today_fx(db, "USD")[0]) or Decimal(1)

    def __call__(self, amount, currency: str | None) -> Decimal:
        a, cur = d(amount or 0), (currency or "CRC").upper()
        if cur == self.currency:
            return a
        crc = a * self.usd if cur == "USD" else a  # solo CRC y USD en el sistema
        return crc if self.currency == "CRC" else (crc / self.usd).quantize(Decimal("0.00001"))


def _gross(net: Decimal, rate) -> Decimal:
    return (net * (1 + d(rate) / 100)).quantize(Decimal("0.01"))


def _prod_pub(
    p: Product,
    variants: list[dict] | None = None,
    stock: Decimal | None = None,
    pricer: Pricer | None = None,
    cats: dict | None = None,
    compact: bool = False,
):
    main = next((i.get("url") for i in (p.images or []) if i.get("main")), (p.images or [{}])[0].get("url") if p.images else None)
    rate = float(p.taxes[0].tax.rate) if p.taxes else 13
    desc = p.description_store or p.description_invoice
    if compact and desc and len(desc) > 180:
        desc = desc[:177].rstrip() + "…"
    out = {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "price": sinv.web_price(p),
        "currency": p.currency,
        "featured": bool(p.web_featured),
        "description": desc,
        "image": main,
        "images": [i.get("url") for i in (p.images or [])],
        "category_id": p.category_id,
        "category": (cats or {}).get(p.category_id),
        "brand": p.brand,
        "tax_rate": rate,
        "item_type": p.item_type,
        "variants": [{**v, "price": v["price"] if v["price"] is not None else sinv.web_price(p)} for v in (variants or [])],
        "availability": _availability(p, stock if stock is not None else Decimal(0)),
    }
    if pricer is not None:
        # precio final al consumidor: en la moneda de la tienda y CON IVA (asi se exhibe en Costa Rica)
        out["display_currency"] = pricer.currency
        out["display_price"] = _gross(pricer(sinv.web_price(p), p.currency), rate)
        for v in out["variants"]:
            v["display_price"] = _gross(pricer(v["price"], p.currency), rate)
    return out


def _availability(p: Product, own) -> dict:
    if isinstance(own, dict):  # ya viene calculada por store_inventory
        return own
    from .catalog import store_availability

    return store_availability(p, own)


@router.get("/public/store-domain")
def store_by_domain(host: str = Query(min_length=3, max_length=200), db: Session = Depends(get_db)):
    """Dominio propio de una tienda (Mi Tienda -> Dominio, p. ej. tienda.crimsoncr.com) -> slug publico."""
    h = host.strip().lower().split(":")[0]
    for t in db.scalars(select(Tenant).where(Tenant.active)):
        cfg = store_cfg(t)
        dom = (cfg.get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        if cfg["published"] and dom == h:
            return {"slug": t.slug}
    raise HTTPException(404, "Tienda no disponible")


@router.get("/public/store/{slug}")
def store_home(slug: str, db: Session = Depends(get_db)):
    t = _tenant(db, slug)
    cfg = store_cfg(t)
    pricer = Pricer(db, t)
    pages = db.scalars(select(StorePage).where(StorePage.tenant_id == t.id, StorePage.published).order_by(StorePage.position)).all()
    cats = db.scalars(select(Category).where(Category.tenant_id == t.id, Category.show_on_web).order_by(Category.name)).all()
    counts = dict(
        db.execute(
            select(Product.category_id, func.count()).where(Product.tenant_id == t.id, Product.active, Product.show_on_web).group_by(Product.category_id)
        ).all()
    )
    # subcategorias (Fygaro: categoria > subcategoria): el conteo de la madre suma el de sus hijas
    kids: dict[int | None, list] = {}
    for c in cats:
        kids.setdefault(c.parent_id, []).append(c)
    rolled: dict[int, int] = {}

    def total(c) -> int:
        if c.id not in rolled:
            rolled[c.id] = counts.get(c.id, 0) + sum(total(k) for k in kids.get(c.id, []))
        return rolled[c.id]

    shown = {c.id for c in cats}
    return {
        "store": {k: cfg[k] for k in ("name", "tagline", "logo_url", "primary", "secondary", "font", "kind", "whatsapp", "currency", "legal")},
        "nav": [{"slug": pg.slug, "title": pg.title} for pg in pages if pg.in_nav],
        # solo categorias con productos publicados (una categoria vacia en el menu es un callejon sin salida)
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "count": total(c),
                "subcategories": [{"id": k.id, "name": k.name, "count": total(k)} for k in kids.get(c.id, []) if total(k)],
            }
            for c in cats
            if (c.parent_id is None or c.parent_id not in shown) and total(c)
        ],
        "product_count": sum(counts.values()),
        # las tarifas se configuran en colones (Mi Tienda -> "Base ₡"); display_amount = en la moneda de la tienda, con IVA
        "shipping_rates": [{**r, "display_amount": _gross(pricer(r.get("amount") or 0, "CRC"), 13)} for r in cfg["shipping_rates"] if r.get("active")],
        "payment_methods": manual_methods(t),
    }


@router.get("/public/store/{slug}/pages/{page_slug}")
def store_page(slug: str, page_slug: str, db: Session = Depends(get_db)):
    t = _tenant(db, slug)
    pg = db.scalar(select(StorePage).where(StorePage.tenant_id == t.id, StorePage.slug == page_slug, StorePage.published))
    if not pg:
        raise HTTPException(404, "Página no encontrada")
    return _page_out(pg)


def _category_and_children(db: Session, tenant_id: int, category_id: int) -> set[int]:
    pairs = db.execute(select(Category.id, Category.parent_id).where(Category.tenant_id == tenant_id)).all()
    out, frontier = {category_id}, {category_id}
    while frontier:
        frontier = {cid for cid, parent in pairs if parent in frontier and cid not in out}
        out |= frontier
    return out


def _cat_names(db: Session, tenant_id: int) -> dict[int, str]:
    return dict(db.execute(select(Category.id, Category.name).where(Category.tenant_id == tenant_id)).all())


@router.get("/public/store/{slug}/products")
def store_products(
    slug: str,
    response: Response,
    q: str | None = Query(None, max_length=120),
    category_id: int | None = None,
    sort: str = Query("name", pattern="^(name|price_asc|price_desc|recent)$"),
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
    compact: bool = False,
    db: Session = Depends(get_db),
):
    """Catalogo publico. Sin `limit` devuelve todo (compatibilidad con los bloques); con `limit` pagina y deja el
    total en X-Total-Count. La busqueda ignora tildes y mayusculas y exige todas las palabras (camara 4mp hik)."""
    from ..services.tabular import norm

    t = _tenant(db, slug)
    stmt = select(Product).where(Product.tenant_id == t.id, Product.active, Product.show_on_web)
    if category_id:  # la categoria y sus subcategorias
        stmt = stmt.where(Product.category_id.in_(_category_and_children(db, t.id, category_id)))
    rows = list(db.scalars(stmt.order_by(Product.name)).all())
    cats = _cat_names(db, t.id)
    if q and q.strip():
        words = [w for w in norm(q).split("_") if w]
        hay = {p.id: "_" + norm(" ".join(x or "" for x in (p.name, p.code, p.brand, p.model, cats.get(p.category_id)))) + "_" for p in rows}
        rows = [p for p in rows if all(w in hay[p.id] for w in words)]
    rows = sinv.visible(db, t, rows)  # Ajustes de la tienda: agotados ocultos si asi se eligio
    pricer = Pricer(db, t)
    if sort in ("price_asc", "price_desc"):
        rows.sort(key=lambda p: pricer(sinv.web_price(p), p.currency), reverse=sort == "price_desc")
    elif sort == "recent":
        rows.sort(key=lambda p: p.id, reverse=True)
    response.headers["X-Total-Count"] = str(len(rows))
    if limit is not None:
        rows = rows[offset : offset + limit]
    vmap = _variants(db, [p.id for p in rows])
    stock = _stock_of(db, t.id, [p.id for p in rows])
    return [_prod_pub(p, vmap.get(p.id), stock.get(p.id, Decimal(0)), pricer, cats, compact) for p in rows]


@router.get("/public/store/{slug}/products/{pid}")
def store_product(slug: str, pid: int, db: Session = Depends(get_db)):
    t = _tenant(db, slug)
    p = db.get(Product, pid)
    if not p or p.tenant_id != t.id or not p.show_on_web:
        raise HTTPException(404, "Producto no encontrado")
    related = db.scalars(
        select(Product).where(Product.tenant_id == t.id, Product.show_on_web, Product.active, Product.category_id == p.category_id, Product.id != p.id).limit(4)
    ).all()
    stock = _stock_of(db, t.id, [p.id] + [r.id for r in related])
    pricer, cats = Pricer(db, t), _cat_names(db, t.id)
    return {
        **_prod_pub(p, _variants(db, [p.id]).get(p.id), stock.get(p.id, Decimal(0)), pricer, cats),
        "related": [_prod_pub(r, None, stock.get(r.id, Decimal(0)), pricer, cats, True) for r in related],
    }


@router.get("/public/store/{slug}/legacy/{kind}/{ref}")
def store_legacy(slug: str, kind: str, ref: str, db: Session = Depends(get_db)):
    """Direcciones viejas de Fygaro (tienda.crimsoncr.com/products/<uuid>/, /products/category/<id>/) -> ids propios.
    Asi los enlaces ya compartidos en redes, WhatsApp y Google no mueren al cambiar de plataforma."""
    t = _tenant(db, slug)
    fy = (t.settings or {}).get("fygaro") or {}
    if kind == "product":
        e = (fy.get("products") or {}).get(ref)
        p = db.get(Product, e["id"]) if e else None
        if p and p.tenant_id == t.id and p.show_on_web and p.active:
            return {"product_id": p.id}
    elif kind == "category":
        cid = (fy.get("categories") or {}).get(ref)
        if cid:
            return {"category_id": cid}
    raise HTTPException(404, "Sin equivalente")


class CartLine(BaseModel):
    # sin precios: si el navegador manda unit_price o total, se rechaza (el monto lo calcula el servidor)
    model_config = ConfigDict(extra="forbid")

    product_id: int
    variant_id: int | None = None
    quantity: Decimal = Field(gt=0, le=999)


class DeliveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: str = Field(pattern="^(retiro|envio)$")
    province: str | None = Field(None, max_length=40)
    canton: str | None = Field(None, max_length=60)
    district: str | None = Field(None, max_length=80)
    address: str | None = Field(None, max_length=400)


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[CartLine] = Field(min_length=1, max_length=100)
    contact: dict = Field(default_factory=dict)
    delivery: DeliveryIn | None = None
    shipping_method: str | None = Field(None, max_length=80)
    payment_method: str | None = Field(None, max_length=60)
    coupon_code: str | None = Field(None, max_length=40)
    notes: str | None = Field(None, max_length=1000)
    accept_terms: bool = False
    # misma clave = mismo pedido (doble clic, reintento por red lenta)
    idempotency_key: str | None = Field(None, min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    # el total que el comprador vio: si el servidor calcula otro, no se crea el pedido
    expected_total: Decimal | None = Field(None, ge=0)
    website: str | None = Field(None, max_length=200)  # honeypot


def rate_zone(r: dict) -> str | None:
    """retiro | gam | fuera_gam | None (cualquiera). Si la tarifa no lo dice, se deduce del nombre."""
    z = r.get("zone")
    if z in ("retiro", "gam", "fuera_gam"):
        return z
    n = (r.get("name") or "").lower()
    if "retiro" in n or "recoger" in n:
        return "retiro"
    if "fuera" in n:
        return "fuera_gam"
    if "gam" in n:
        return "gam"
    return None


def _check_delivery(ship: dict | None, delivery: DeliveryIn | None, rates_active: bool) -> None:
    from ..services import cr_geo

    if delivery is None:
        return
    if delivery.type == "envio":
        if not cr_geo.valid(delivery.province, delivery.canton):
            raise HTTPException(422, "Elegí provincia y cantón de entrega")
        if not delivery.address or len(delivery.address) < 5:
            raise HTTPException(422, "Escribí la dirección exacta (otras señas)")
        if rates_active and not ship:
            raise HTTPException(422, "Elegí una tarifa de envío")
        if ship:
            z = rate_zone(ship)
            if z == "retiro":
                raise HTTPException(422, "Esa opción es de retiro, no de envío")
            if z == "gam" and cr_geo.fuera_gam(delivery.province, delivery.canton):
                raise HTTPException(422, "Esa dirección queda fuera de la GAM: elegí la tarifa fuera de GAM")
    elif ship and rate_zone(ship) not in ("retiro", None):
        raise HTTPException(422, "Para retirar elegí la opción de retiro")


def _quote_cart(db: Session, t: Tenant, data: CheckoutIn):
    """Cotiza el carrito con el MISMO motor que la factura: el cupon se aplica por linea de producto y el envio
    es una linea mas con IVA. Asi el total del pedido y el del comprobante coinciden al centimo."""
    cfg = store_cfg(t)
    prods = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.tenant_id == t.id, Product.show_on_web, Product.active, Product.id.in_([ln.product_id for ln in data.lines]))
        )
    }
    if len(prods) != len({ln.product_id for ln in data.lines}):
        raise HTTPException(422, "Producto no disponible")
    ship = next((r for r in cfg["shipping_rates"] if r.get("active") and r["name"] == data.shipping_method), None) if data.shipping_method else None
    if data.shipping_method and not ship:
        raise HTTPException(422, "Esa tarifa de envío no está disponible")
    _check_delivery(ship, data.delivery, any(r.get("active") for r in cfg["shipping_rates"]))
    vids = [ln.variant_id for ln in data.lines if ln.variant_id]
    variants = {v.id: v for v in db.scalars(select(ProductVariant).where(ProductVariant.id.in_(vids), ProductVariant.active))} if vids else {}

    pricer = Pricer(db, t)

    def unit_of(ln: CartLine) -> tuple[Decimal, str]:
        """Precio sin IVA en la moneda de la tienda (el motor de totales suma el IVA despues)."""
        pr = prods[ln.product_id]
        if ln.variant_id:
            v = variants.get(ln.variant_id)
            if not v or v.product_id != pr.id:
                raise HTTPException(422, "Variante no disponible")
            return pricer(v.price if v.price is not None else sinv.web_price(pr), pr.currency), f"{pr.name} · {v.name}"
        return pricer(sinv.web_price(pr), pr.currency), pr.name

    gross = sum((d(ln.quantity) * unit_of(ln)[0] for ln in data.lines), Decimal(0))
    coupon = valid_coupon(db, t.id, data.coupon_code, gross)
    items = []
    for ln in data.lines:
        pr = prods[ln.product_id]
        unit, name = unit_of(ln)
        base = d(ln.quantity) * unit
        if not coupon:
            dt, dv = "percent", Decimal(0)
        elif coupon.kind == "percent":
            dt, dv = "percent", d(coupon.value)
        else:
            dt, dv = "amount", ((d(coupon.value) * base / gross).quantize(Decimal("0.00001")) if gross else Decimal(0))
        items.append(
            {
                "product": pr,
                "name": name,
                "quantity": d(ln.quantity),
                "unit_price": unit,
                "discount_type": dt,
                "discount_value": dv,
                "tax_rate": lines_rate(pr),
            }
        )
    ship_amount = shipping_cost(ship, sum((d(ln.quantity) * d(prods[ln.product_id].weight_kg or 0) for ln in data.lines), Decimal(0))) if ship else Decimal(0)
    if ship_amount and pricer.currency != "CRC":  # las tarifas de envio se configuran en colones
        ship_amount = pricer(ship_amount, "CRC").quantize(Decimal("0.01"))
    if ship_amount > 0 and rate_zone(ship) != "retiro" and free_shipping_ok(t, pricer, items):
        ship_amount = Decimal(0)  # envio gratis desde el monto de Mi Tienda -> Pagos y entrega
    if ship and ship_amount > 0:
        items.append(
            {
                "product": None,
                "name": f"Envio - {ship['name']}",
                "quantity": Decimal(1),
                "unit_price": ship_amount,
                "discount_type": "percent",
                "discount_value": Decimal(0),
                "tax_rate": Decimal(13),
            }
        )

    calc = compute_document([LineIn(i["quantity"], i["unit_price"], i["discount_type"], i["discount_value"], i["tax_rate"]) for i in items])
    shipping = ship_amount
    discount_total = sum((i["quantity"] * i["unit_price"] for i in items), Decimal(0)) - calc.subtotal
    return items, calc, coupon, ship, shipping, discount_total


@router.post("/public/store/{slug}/quote")
def store_quote(slug: str, data: CheckoutIn, request: Request, db: Session = Depends(get_db)):
    from ..core.ratelimit import quote_limiter

    quote_limiter.hit(f"quote:{request.client.host if request.client else '?'}")
    t = _tenant(db, slug)
    items, calc, coupon, ship, shipping, discount_total = _quote_cart(db, t, data)
    return {
        **quote_detail(items, calc),
        "subtotal": calc.subtotal + discount_total - shipping,
        "discount_total": discount_total,
        "tax_total": calc.tax_total,
        "shipping": shipping,
        "total": calc.total,
        "coupon": coupon.code if coupon else None,
        "shipping_method": ship["name"] if ship else None,
        "currency": Pricer(db, t).currency,
        "free_shipping": bool(
            ship and rate_zone(ship) != "retiro" and shipping == 0 and free_shipping_ok(t, Pricer(db, t), [i for i in items if i["product"]])
        ),
        "free_shipping_from": free_shipping_threshold(t, Pricer(db, t)),
    }


def free_shipping_threshold(t: Tenant, pricer: Pricer) -> Decimal | None:
    """Monto (con IVA, en la moneda de la tienda) desde el que el envio es gratis. Se configura en colones."""
    from ..services.store_orders import checkout_cfg

    raw = d(checkout_cfg(t).get("free_shipping_from") or 0)
    return pricer(raw, "CRC").quantize(Decimal("0.01")) if raw > 0 else None


def free_shipping_ok(t: Tenant, pricer: Pricer, items: list[dict]) -> bool:
    limit = free_shipping_threshold(t, pricer)
    if limit is None:
        return False
    products = compute_document([LineIn(i["quantity"], i["unit_price"], i["discount_type"], i["discount_value"], i["tax_rate"]) for i in items])
    return products.total >= limit  # productos con descuento e IVA, sin el envio


def quote_detail(items: list[dict], calc) -> dict:
    """IVA desglosado por tarifa y lineas con el precio que calculo el servidor (lo que el comprador ve)."""
    taxes: dict[str, Decimal] = {}
    lines = []
    for i, lo in zip(items, calc.lines, strict=True):
        k = f"{d(i['tax_rate']).normalize():f}"
        taxes[k] = taxes.get(k, Decimal(0)) + d(lo.tax_amount)
        lines.append({"name": i["name"], "quantity": i["quantity"], "total": lo.total, "shipping": i["product"] is None})
    return {"taxes": [{"rate": k, "amount": v} for k, v in sorted(taxes.items(), key=lambda x: -Decimal(x[0])) if v], "lines": lines}


def shipping_cost(rate: dict, weight_kg: Decimal) -> Decimal:
    """Tarifa fija, o por peso estilo Correos de Costa Rica: base + por_kg x kg (minimo 1 kg), mas un recargo %."""
    base = d(rate.get("amount") or 0)
    per_kg = d(rate.get("per_kg") or 0)
    if per_kg > 0:
        base += per_kg * max(weight_kg, Decimal(1)).quantize(Decimal("0.001"))
    overhead = d(rate.get("overhead_pct") or 0)
    return (base * (1 + overhead / 100)).quantize(Decimal("0.01"))


class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    phone: str | None = Field(None, max_length=40)
    message: str = Field(min_length=3, max_length=4000)


@router.post("/public/store/{slug}/contact", status_code=201)
def store_contact(slug: str, data: ContactIn, request: Request, db: Session = Depends(get_db)):
    """Formulario de contacto del sitio: el mensaje queda como nota en la ficha del cliente (lo crea si es nuevo)
    y se avisa por correo a los administradores."""
    import html

    from ..models import CustomerNote, TenantUser
    from ..services.mail import queue_email

    public_limiter.hit(f"contact:{request.client.host if request.client else '?'}")
    t = _tenant(db, slug)
    email = str(data.email).lower()
    c = db.scalar(select(Customer).where(Customer.tenant_id == t.id, Customer.email == email))
    if not c:
        c = Customer(tenant_id=t.id, name=data.name, email=email, phone=data.phone, whatsapp=data.phone, id_type="fisica")
        db.add(c)
        db.flush()
    db.add(CustomerNote(tenant_id=t.id, customer_id=c.id, body=f"Mensaje desde la tienda: {data.message}"))
    body = f"<p><b>{html.escape(data.name)}</b> ({html.escape(email)}{' · ' + html.escape(data.phone) if data.phone else ''}) escribió desde la tienda:</p><blockquote>{html.escape(data.message)}</blockquote>"
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.role_code == "admin", TenantUser.active)):
        queue_email(db, t, m.user.email, f"Nuevo mensaje de {data.name} · {t.name}", body, "customer", c.id)
    audit(db, t.id, None, "contact", "customer", c.id)
    db.commit()
    return {"ok": True}


def lines_rate(p: Product) -> Decimal:
    return d(p.taxes[0].tax.rate) if p.taxes else Decimal(13)


# ---------- Buscador global ----------
@router.get("/search")
def search(q: str = Query(min_length=1), p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    from ..models import Quote

    like = f"%{q}%"
    tid = p.tenant.id
    out = []
    from ..services.ownership import doc_scope

    # mismo alcance que Facturas y Cotizaciones (vendedor responsable): lo ajeno no aparece
    mine_inv = doc_scope(Invoice, p)
    mine_q = doc_scope(Quote, p)
    can_sales, can_crm, can_cat = p.can("sales", "ver"), p.can("crm", "ver"), p.can("catalog", "ver")
    for i in db.scalars(select(Invoice).where(Invoice.tenant_id == tid, mine_inv, Invoice.number.ilike(like)).limit(5)) if can_sales else []:
        out.append({"kind": "factura", "id": i.id, "title": i.number, "sub": f"{i.status} · {i.currency} {d(i.total):,.2f}", "to": f"/facturas/{i.id}"})
    for qd in db.scalars(select(Quote).where(Quote.tenant_id == tid, mine_q, Quote.number.ilike(like)).limit(5)) if can_sales else []:
        out.append({"kind": "cotización", "id": qd.id, "title": qd.number, "sub": qd.status, "to": f"/cotizaciones/{qd.id}"})
    for c in (
        db.scalars(
            select(Customer)
            .where(Customer.tenant_id == tid, or_(Customer.name.ilike(like), Customer.id_number.ilike(like), Customer.email.ilike(like)))
            .limit(5)
        )
        if can_crm
        else []
    ):
        out.append({"kind": "cliente", "id": c.id, "title": c.name, "sub": c.id_number or c.email or "", "to": f"/clientes/{c.id}"})
        for i in (
            db.scalars(select(Invoice).where(Invoice.tenant_id == tid, mine_inv, Invoice.customer_id == c.id).order_by(Invoice.id.desc()).limit(3))
            if can_sales
            else []
        ):
            out.append({"kind": "factura", "id": i.id, "title": i.number, "sub": f"{c.name} · {i.status}", "to": f"/facturas/{i.id}"})
    for pr in db.scalars(select(Product).where(Product.tenant_id == tid, or_(Product.name.ilike(like), Product.code.ilike(like))).limit(5)) if can_cat else []:
        # el tecnico busca equipos sin ver precios (Andres 22:07): sin catalog.precios solo el codigo
        sub = f"{pr.code} · {pr.currency} {d(pr.price):,.2f}" if p.sees_prices else pr.code
        out.append({"kind": "producto", "id": pr.id, "title": pr.name, "sub": sub, "to": f"/productos?q={pr.code}"})
    for o in db.scalars(select(Order).where(Order.tenant_id == tid, Order.number.ilike(like)).limit(5)) if can_sales else []:
        out.append({"kind": "orden", "id": o.id, "title": o.number, "sub": o.status, "to": "/ordenes"})
    return out[:20]


_ = datetime.now(UTC)  # noqa: F841  (mantiene la importacion util para futuras marcas de tiempo)
