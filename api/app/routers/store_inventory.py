"""Inventario web: ajustes de la tienda ligados al inventario, panel "Inventario web", controles de publicacion
por producto, estado de inventario de un pedido y endpoints publicos de disponibilidad y "Avisarme"."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.clientip import ip_key
from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import catalog_limiter, stock_alert_limiter
from ..models import Order, Product, StockReservation, StoreStockAlert, Tenant, Warehouse
from ..services import store_inventory as sinv
from ..services.documents import audit

router = APIRouter(tags=["tienda"])


# ---------- Ajustes ----------
class StoreInventorySettingsIn(BaseModel):
    enabled: bool | None = None
    warehouse_ids: list[int] | None = None
    quantity_display: str | None = Field(None, pattern="^(exacta|umbral|oculta)$")
    low_threshold: int | None = Field(None, ge=0, le=10000)
    backorder_enabled: bool | None = None
    backorder_lead: str | None = Field(None, max_length=60)
    # frescura del dato del proveedor (Eurocomp refresca cada 6 h): mas viejo -> "Consultar disponibilidad"
    supplier_max_age_hours: int | None = Field(None, ge=1, le=24 * 60)
    # obsoletos (se aceptan para no romper pantallas viejas y se ignoran): la tienda nunca vende en linea lo que no
    # tiene ni el proveedor confirma con dato vigente (services/availability, max_qty)
    enforce_stock: bool | None = None
    supplier_max_age_days: int | None = Field(None, ge=0, le=365)
    sell_when_unknown: bool | None = None
    out_of_stock: str | None = Field(None, pattern="^(mostrar|ocultar)$")
    out_of_stock_action: str | None = Field(None, pattern="^(avisarme|whatsapp|ninguna)$")
    hold_hours_manual: int | None = Field(None, ge=1, le=720)
    hold_minutes_card: int | None = Field(None, ge=5, le=1440)
    card_keywords: list[str] | None = None
    cancel_unpaid_on_expiry: bool | None = None


def _whs(db: Session, tenant_id: int, cfg: dict) -> list[dict]:
    """Solo las bodegas de venta: las de garantias (revision, distribuidor) nunca alimentan la web."""
    web = set(sinv.web_warehouses(db, tenant_id, cfg))
    return [
        {"id": w.id, "name": w.name, "is_default": w.is_default, "web": w.id in web}
        for w in db.scalars(
            select(Warehouse).where(Warehouse.tenant_id == tenant_id, Warehouse.active, Warehouse.kind == "venta").order_by(Warehouse.is_default.desc(), Warehouse.id)
        )
    ]


@router.get("/store/inventory/settings")
def get_inv_settings(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    cfg = sinv.settings_of(p.tenant)
    return {**cfg, "warehouses": _whs(db, p.tenant.id, cfg)}


@router.put("/store/inventory/settings")
def put_inv_settings(data: StoreInventorySettingsIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    t = db.get(Tenant, p.tenant.id)
    vals = data.model_dump(exclude_unset=True)
    if vals.get("warehouse_ids"):
        mine = set(db.scalars(select(Warehouse.id).where(Warehouse.tenant_id == t.id)))
        if not set(vals["warehouse_ids"]) <= mine:
            raise HTTPException(404, "Inventario no encontrado")
    if "card_keywords" in vals:
        vals["card_keywords"] = [k.strip().lower()[:30] for k in (vals["card_keywords"] or []) if k.strip()][:20]
    st = {**(t.settings or {})}
    antes = st.get(sinv.KEY) or {}
    st[sinv.KEY] = {**sinv.DEFAULTS, **antes, **vals}
    t.settings = st
    audit(db, t.id, p.user.id, "update", "store_inventory_settings", t.id, {k: str(v) for k, v in vals.items()}, ip=p.ip)
    db.commit()
    cfg = sinv.settings_of(t)
    return {**cfg, "warehouses": _whs(db, t.id, cfg)}


# ---------- Panel "Inventario web" ----------
@router.get("/store/inventory")
def web_inventory(
    estado: str | None = Query(None, pattern="^(en_bodega|disponible|bajo_pedido|consultar|agotado|alertas|reservado)$"),
    q: str | None = Query(None, max_length=120),
    todos: bool = False,
    p: Principal = Depends(require("inventory", "ver")),
    db: Session = Depends(get_db),
):
    """Lo publicado en la tienda con su disponible web, reservado por pedidos, bajo pedido y agotado.
    alerta = publicado sin existencia propia ni proveedor que lo respalde. todos=true incluye lo no publicado."""
    tid = p.tenant.id
    stmt = select(Product).where(Product.tenant_id == tid, Product.active, Product.item_type == "producto")
    if not todos:
        stmt = stmt.where(Product.show_on_web)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Product.name.ilike(like) | Product.code.ilike(like))
    prods = list(db.scalars(stmt.order_by(Product.name)))
    av = sinv.availability(db, p.tenant, [x.id for x in prods], public=False)
    waiting = dict(
        db.execute(
            select(StoreStockAlert.product_id, func.count(StoreStockAlert.id))
            .where(StoreStockAlert.tenant_id == tid, StoreStockAlert.notified_at.is_(None))
            .group_by(StoreStockAlert.product_id)
        ).all()
    )
    items = []
    # el tecnico (y el aliado) tienen inventory.ver para las cantidades, pero nunca ven precios (13/10, 22:07)
    precios = p.sees_prices
    summary = {"publicados": 0, "en_bodega": 0, "bajo_pedido": 0, "consultar": 0, "agotado": 0, "alertas": 0, "reservado_pedidos": Decimal(0), "avisos": 0}
    if estado == "disponible":  # nombre viejo del estado
        estado = "en_bodega"
    for x in prods:
        a = av.get(x.id) or {}
        alerta = bool(x.show_on_web and a.get("estado") in ("agotado", "consultar"))
        row = {
            "id": x.id,
            "code": x.code,
            "name": x.name,
            "show_on_web": x.show_on_web,
            "web_featured": bool(x.web_featured),
            "web_price": x.web_price if precios else None,
            "price": x.price if precios else None,
            "currency": x.currency,
            "serialized": bool(x.serialized),
            "estado": a.get("estado"),
            "mensaje": a.get("mensaje"),
            "propio": a.get("propio"),
            "reservado": a.get("reservado"),
            "reservado_pedidos": a.get("reservado_web"),
            "libre": a.get("libre"),
            "proveedor": a.get("proveedor"),
            "proveedor_al": a.get("proveedor_al"),
            "proveedor_viejo": a.get("proveedor_viejo"),
            "proveedor_nombre": a.get("supplier_name"),
            "proveedor_hace": a.get("supplier_age"),
            "proveedor_sin_vincular": a.get("supplier_unlinked_qty"),
            "max_qty": a.get("max_qty"),
            "alerta": alerta,
            "avisos": waiting.get(x.id, 0),
        }
        if x.show_on_web:
            summary["publicados"] += 1
            summary[a.get("estado")] = summary.get(a.get("estado"), 0) + 1
            summary["alertas"] += int(alerta)
        summary["reservado_pedidos"] += Decimal(str(a.get("reservado_web") or 0))
        summary["avisos"] += waiting.get(x.id, 0)
        if estado == "alertas" and not alerta:
            continue
        if estado == "reservado" and not (a.get("reservado_web") or 0):
            continue
        if estado in ("en_bodega", "bajo_pedido", "consultar", "agotado") and a.get("estado") != estado:
            continue
        items.append(row)
    summary["disponible"] = summary["en_bodega"]  # nombre viejo, para pantallas abiertas antes de esta version
    cfg = sinv.settings_of(p.tenant)
    return {"summary": summary, "items": items, "settings": {**cfg, "warehouses": _whs(db, tid, cfg)}, "holds": _holds(db, tid)}


def _holds(db: Session, tenant_id: int) -> list[dict]:
    """Reservas abiertas de pedidos web (lo "reservado por pedidos")."""
    rows = db.scalars(
        select(StockReservation)
        .where(StockReservation.tenant_id == tenant_id, StockReservation.origin == "web", StockReservation.status == "confirmada")
        .order_by(StockReservation.expires_at.is_(None), StockReservation.expires_at, StockReservation.id)
        .limit(300)
    ).all()
    if not rows:
        return []
    prods = dict(db.execute(select(Product.id, Product.name).where(Product.id.in_({r.product_id for r in rows}))).all())
    orders = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_({r.order_id for r in rows if r.order_id})))}
    whs = dict(db.execute(select(Warehouse.id, Warehouse.name).where(Warehouse.tenant_id == tenant_id)).all())
    out = []
    for r in rows:
        o = orders.get(r.order_id)
        out.append(
            {
                "id": r.id,
                "order_id": r.order_id,
                "order": o.number if o else None,
                "order_status": o.status if o else None,
                "product_id": r.product_id,
                "name": prods.get(r.product_id),
                "warehouse": whs.get(r.warehouse_id),
                "quantity": Decimal(str(r.quantity)) - Decimal(str(r.consumed_quantity or 0)),
                "expires_at": r.expires_at,
            }
        )
    return out


class WebProductIn(BaseModel):
    show_on_web: bool | None = None
    web_featured: bool | None = None
    web_price: Decimal | None = Field(None, ge=0)


@router.patch("/store/inventory/products/{pid}")
def web_product(pid: int, data: WebProductIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Publicado, destacado y precio web (null = vuelve al precio de siempre)."""
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    vals = data.model_dump(exclude_unset=True)
    for k, v in vals.items():
        if k != "web_price" and v is None:
            continue
        setattr(pr, k, v)
    audit(db, p.tenant.id, p.user.id, "web", "product", pr.id, {k: str(v) for k, v in vals.items()}, ip=p.ip)
    db.commit()
    a = sinv.availability(db, p.tenant, [pr.id], public=False).get(pr.id, {})
    return {
        "id": pr.id,
        "show_on_web": pr.show_on_web,
        "web_featured": pr.web_featured,
        "web_price": pr.web_price,
        "price": pr.price,
        "estado": a.get("estado"),
    }


@router.get("/store/orders/{oid}/inventory")
def order_inventory(oid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    o = db.get(Order, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Orden no encontrada")
    return sinv.order_stock(db, o)


# ---------- Publico ----------
def _client(request: Request) -> str:
    """Clave de los limitadores publicos: la IP (una IPv6, por su /64)."""
    return ip_key(request.client.host if request.client else None)


@router.get("/public/store/{slug}/availability")
def public_availability(slug: str, request: Request, response: Response, ids: str = Query(max_length=2000), db: Session = Depends(get_db)):
    """Disponibilidad liviana para la vitrina: {id: {estado, disponible, mensaje, plazo, ...}}. Sin costos ni
    existencias del proveedor; la cantidad solo si Ajustes de la tienda dice "exacta". Cacheable 30 s."""
    from .store import _tenant

    catalog_limiter.hit(f"avail:{_client(request)}")
    t = _tenant(db, slug)
    try:
        wanted = [int(x) for x in ids.split(",") if x.strip()][:200]
    except ValueError as e:
        raise HTTPException(422, "ids debe ser una lista de números separados por coma") from e
    pub_ids = (
        list(db.scalars(select(Product.id).where(Product.tenant_id == t.id, Product.active, Product.show_on_web, Product.id.in_(wanted)))) if wanted else []
    )
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=60"
    return {str(k): v for k, v in sinv.availability(db, t, pub_ids).items()}


class StockAlertIn(BaseModel):
    product_id: int
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)


@router.post("/public/store/{slug}/stock-alert", status_code=201)
def public_stock_alert(slug: str, data: StockAlertIn, request: Request, db: Session = Depends(get_db)):
    """Avisarme cuando haya: queda en el panel Inventario web y el worker avisa por correo al volver."""
    from .store import _tenant

    stock_alert_limiter.hit(f"alert:{_client(request)}")
    t = _tenant(db, slug)
    if not data.email and not (data.phone or "").strip():
        raise HTTPException(422, "Indicá un correo o un teléfono")
    pr = db.get(Product, data.product_id)
    if not pr or pr.tenant_id != t.id or not pr.show_on_web or not pr.active:
        raise HTTPException(404, "Producto no encontrado")
    email = str(data.email).lower() if data.email else None
    dup = (
        db.scalar(
            select(StoreStockAlert.id).where(
                StoreStockAlert.tenant_id == t.id, StoreStockAlert.product_id == pr.id, StoreStockAlert.notified_at.is_(None), StoreStockAlert.email == email
            )
        )
        if email
        else None
    )
    if not dup:
        db.add(StoreStockAlert(tenant_id=t.id, product_id=pr.id, email=email, phone=(data.phone or "").strip() or None, created_at=datetime.now(UTC)))
        db.commit()
    return {"ok": True}
