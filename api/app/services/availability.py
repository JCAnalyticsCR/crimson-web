"""Disponibilidad: UNA sola fuente de verdad de lo que hay de cada producto, para todas las pantallas.

Nadie mas suma bodegas ni mira el proveedor por su cuenta: la tienda (lista, vitrina, ficha, kits, /availability en
vivo y el checkout), la ficha y la lista internas, Inventario -> Disponibilidad, el punto de venta, cotizaciones y
facturas, la recomendacion de equipos, los proyectos y el portal del aliado llaman a `availability_many`.

De donde sale
- PROPIO: lo libre en bodegas de VENTA (Warehouse.kind == "venta", activas) menos las reservas confirmadas y vigentes
  (proyectos, bodega y pedidos web). Las bodegas de garantias (revision y "proveedor" = taller del distribuidor) nunca
  cuentan. Serializados: por bodega, el menor entre el kardex y las series "disponible". Es solo lo fisico de Crimson.
- PROVEEDOR (hoy Eurocomp): solo items del catalogo de proveedores VINCULADOS al producto (SupplierItem.product_id) y
  activos, con su fecha y hora (Product.supplier_updated_at: la escriben sync_product y el refresco de SiReTT cada 6 h;
  si no la hay, la fecha de la lista del item). Sin fecha = dato viejo. Mas viejo que "supplier_max_age_hours" de
  Ajustes de la tienda (48 h por defecto) = viejo: se muestra como tal y no se vende "bajo pedido". Lo ya prometido en
  solicitudes de compra abiertas de pedidos web (borrador o solicitada) se resta. Un Product.supplier_stock SIN item
  vinculado (lo que dejaba el importador Excel viejo) no cuenta: se informa aparte (supplier_unlinked_qty).
  Eurocomp no es una bodega ni tiene kardex: vive en la capa de proveedor.

Estados (un solo diccionario, STATES): en_bodega | bajo_pedido | consultar (el proveedor tiene pero el dato es viejo,
ya esta comprometido o Ajustes no vende bajo pedido) | agotado | servicio. Se vende en linea lo de SELLABLE_STATES;
lo demas ofrece la accion de Ajustes ("Consultar por WhatsApp" por defecto). El plazo de "bajo pedido" sale de
Ajustes (backorder_lead), nunca de un texto fijo.

Audiencias (quien ve que)
- interno: el panel. Propio (libre / reservado), proveedor con nombre, cantidad, fuente y fecha. Nunca costos.
- publico: la tienda. Nunca el proveedor, sus cantidades ni su fecha; la cantidad propia solo si Ajustes publica la
  exacta (quantity_display).
- aliado: el portal del aliado. Propio libre y "bajo pedido: N" con su fecha, sin nombrar al proveedor ni costos (las
  llaves tampoco dicen supplier/cost/price: la prueba de aislamiento las busca).

API
    availability_many(db, tenant, products, *, audience="interno", web_only=None, by_warehouse=False) -> {id: dict}
    one(db, tenant, product, **kw) -> dict
    supplier_status(db, tenant, products, cfg=None) -> {id: dict}       (el checkout lo usa bajo su candado)
    settings_of(tenant) / web_warehouses(db, tenant_id, cfg) / sellable_warehouses(db, tenant_id)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Product,
    PurchaseRequest,
    PurchaseRequestLine,
    StockMovement,
    StockReservation,
    StockUnit,
    Supplier,
    SupplierItem,
    Tenant,
    Warehouse,
)
from . import inventory as invsvc
from . import reservations as rsv

ZERO = Decimal(0)
AUDIENCES = ("interno", "publico", "aliado")

# ---------- Ajustes de la tienda / inventario (tenant.settings["store_inventory"]) ----------
KEY = "store_inventory"
DEFAULTS: dict = {
    "enabled": True,  # reservar al crear pedidos web (apagado: la tienda vende como siempre, sin control)
    "warehouse_ids": [],  # bodegas que alimentan la web; vacio = todas las de venta activas
    "quantity_display": "exacta",  # exacta | umbral | oculta (exacta = como la tienda de siempre)
    "low_threshold": 3,  # "ultimas unidades" a partir de aqui
    "backorder_enabled": True,  # vender bajo pedido lo que el proveedor tiene (con dato vigente)
    "backorder_lead": "3-5 días hábiles",  # plazo de "bajo pedido" que ve el cliente
    "supplier_max_age_hours": 48,  # dato del proveedor mas viejo -> "Consultar disponibilidad" (SiReTT refresca cada 6 h)
    "out_of_stock": "mostrar",  # mostrar | ocultar (agotados en el catalogo, destacados y kits)
    "out_of_stock_action": "whatsapp",  # whatsapp | avisarme | ninguna
    "hold_hours_manual": 48,  # SINPE, transferencia, efectivo
    "hold_minutes_card": 30,  # tarjeta
    "card_keywords": ["tarjeta", "card", "onvo", "paypal"],
    "cancel_unpaid_on_expiry": False,  # al vencer la reserva sin pago, cancelar el pedido
}

# ---------- un solo diccionario de estados ----------
STATES: dict[str, str] = {
    "en_bodega": "En bodega",
    "bajo_pedido": "Bajo pedido",
    "consultar": "Consultar disponibilidad",
    "agotado": "Agotado",
    "servicio": "Servicio",
}
SELLABLE_STATES = ("en_bodega", "bajo_pedido", "servicio")
OPEN_REQUEST = ("borrador", "solicitada")  # solicitudes de compra de pedidos web que ya prometen existencia del proveedor


def settings_of(tenant: Tenant | None) -> dict:
    """Ajustes de inventario de la tienda (tenant.settings['store_inventory'] sobre los predeterminados)."""
    return {**DEFAULTS, **(((tenant.settings or {}) if tenant else {}).get(KEY) or {})}


def max_age_hours(cfg: dict) -> int:
    try:
        h = int(cfg.get("supplier_max_age_hours") if cfg.get("supplier_max_age_hours") is not None else DEFAULTS["supplier_max_age_hours"])
    except (TypeError, ValueError):
        h = DEFAULTS["supplier_max_age_hours"]
    return max(h, 0)


def _dec(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _num(v: Decimal | None):
    """Cantidad para JSON: 3 y no 3.000 (se lee tres mil); 2.5 queda decimal."""
    if v is None:
        return None
    q = _dec(v).normalize()
    return int(q) if q == q.to_integral_value() else q


def qty_text(v) -> str:
    """Cantidad para leer en es-CR: Decimal("3.000") -> "3", 2.5 -> "2,5"."""
    q = _dec(v).normalize()
    if q == q.to_integral_value():
        return str(int(q))
    return f"{q:f}".replace(".", ",")


def age_text(at: datetime | None, now: datetime | None = None) -> str:
    """'hace 3 h', 'hace 2 días', 'hace menos de 1 h' o 'sin fecha'."""
    at = _aware(at)
    if at is None:
        return "sin fecha"
    secs = max(((now or _now()) - at).total_seconds(), 0)
    h = int(secs // 3600)
    if h < 1:
        return "hace menos de 1 h"
    if h < 48:
        return f"hace {h} h"
    return f"hace {h // 24} días"


def _tenant(db: Session, tenant) -> Tenant | None:
    if isinstance(tenant, Tenant):
        return tenant
    return db.get(Tenant, int(getattr(tenant, "id", tenant)))


# ---------- bodegas ----------
def sellable_warehouses(db: Session, tenant_id: int) -> list[int]:
    """Bodegas de venta activas (la predeterminada primero). Revision y proveedor (garantias) nunca."""
    return list(
        db.scalars(
            select(Warehouse.id)
            .where(Warehouse.tenant_id == tenant_id, Warehouse.active, Warehouse.kind == invsvc.SELLABLE)
            .order_by(Warehouse.is_default.desc(), Warehouse.id)
        )
    )


def web_warehouses(db: Session, tenant_id: int, cfg: dict) -> list[int]:
    """Las de venta que alimentan la web (Ajustes de la tienda; vacio = todas las de venta activas)."""
    active = sellable_warehouses(db, tenant_id)
    chosen = {int(x) for x in (cfg.get("warehouse_ids") or [])}
    return [w for w in active if w in chosen] if chosen else active


# ---------- existencia propia ----------
def _own_by_wh(db: Session, tenant_id: int, product_ids: list[int] | None, whs: list[int]) -> dict[tuple[int, int], Decimal]:
    """{(producto, bodega): existencia}; serializados = min(kardex, series disponibles)."""
    if not whs or product_ids == []:
        return {}
    q = (
        select(StockMovement.product_id, StockMovement.warehouse_id, func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.tenant_id == tenant_id, StockMovement.warehouse_id.in_(whs))
        .group_by(StockMovement.product_id, StockMovement.warehouse_id)
    )
    if product_ids is not None:
        q = q.where(StockMovement.product_id.in_(product_ids))
    out = {(pid, wid): _dec(qty) for pid, wid, qty in db.execute(q)}
    sq = select(Product.id).where(Product.tenant_id == tenant_id, Product.serialized.is_(True))
    if product_ids is not None:
        sq = sq.where(Product.id.in_(product_ids))
    serial_ids = set(db.scalars(sq))
    if serial_ids:
        units = {
            (pid, wid): _dec(n)
            for pid, wid, n in db.execute(
                select(StockUnit.product_id, StockUnit.warehouse_id, func.count(StockUnit.id))
                .where(
                    StockUnit.tenant_id == tenant_id, StockUnit.product_id.in_(serial_ids), StockUnit.status == "disponible", StockUnit.warehouse_id.in_(whs)
                )
                .group_by(StockUnit.product_id, StockUnit.warehouse_id)
            )
        }
        for pid in serial_ids:
            for w in whs:
                k = (pid, w)
                if k in out or k in units:
                    out[k] = min(out.get(k, ZERO), units.get(k, ZERO))
    return out


def _committed(db: Session, tenant_id: int, product_ids: list[int] | None, whs: list[int]) -> dict[tuple[int, int], tuple[Decimal, Decimal]]:
    """{(producto, bodega): (comprometido total, comprometido por pedidos web)} confirmado y vigente."""
    if not whs or product_ids == []:
        return {}
    q = (
        select(
            StockReservation.product_id,
            StockReservation.warehouse_id,
            StockReservation.origin,
            func.coalesce(func.sum(StockReservation.quantity - StockReservation.consumed_quantity), 0),
        )
        .where(StockReservation.tenant_id == tenant_id, StockReservation.status == "confirmada", rsv._vigente(), StockReservation.warehouse_id.in_(whs))
        .group_by(StockReservation.product_id, StockReservation.warehouse_id, StockReservation.origin)
    )
    if product_ids is not None:
        q = q.where(StockReservation.product_id.in_(product_ids))
    out: dict[tuple[int, int], tuple[Decimal, Decimal]] = {}
    for pid, wid, origin, qty in db.execute(q):
        qty = max(_dec(qty), ZERO)
        tot, web = out.get((pid, wid), (ZERO, ZERO))
        out[(pid, wid)] = (tot + qty, web + (qty if origin == "web" else ZERO))
    return out


def _free_by_wh(db: Session, tenant_id: int, product_ids: list[int] | None, whs: list[int]) -> tuple[dict, dict, dict]:
    own = _own_by_wh(db, tenant_id, product_ids, whs)
    comp = _committed(db, tenant_id, product_ids, whs)
    free = {k: max(own.get(k, ZERO) - comp.get(k, (ZERO, ZERO))[0], ZERO) for k in set(own) | set(comp)}
    return own, comp, free


# ---------- existencia del proveedor ----------
def list_datetime(list_date: date | None) -> datetime | None:
    """Fecha de la lista del item -> marca de tiempo (mediodia UTC; la de hoy, ahora)."""
    if list_date is None:
        return None
    if list_date >= date.today():
        return _now()
    return datetime.combine(list_date, time(12, 0), tzinfo=UTC)


def _web_requests(db: Session, tenant_id: int, product_ids: list[int]) -> dict[int, Decimal]:
    """Lo ya prometido al proveedor por pedidos web en solicitudes de compra abiertas (borrador o solicitada)."""
    if not product_ids:
        return {}
    rows = db.execute(
        select(PurchaseRequestLine.product_id, func.coalesce(func.sum(PurchaseRequestLine.quantity), 0))
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestLine.request_id)
        .where(
            PurchaseRequest.tenant_id == tenant_id,
            PurchaseRequest.reason == "pedido_web",
            PurchaseRequest.status.in_(OPEN_REQUEST),
            PurchaseRequest.trashed_at.is_(None),
            PurchaseRequestLine.product_id.in_(product_ids),
        )
        .group_by(PurchaseRequestLine.product_id)
    )
    return {pid: max(_dec(q), ZERO) for pid, q in rows}


def _pick(items: list):
    """El item que representa al proveedor: activo con existencia, por API, la lista mas reciente."""
    act = [i for i in items if i.active]
    pool = act or items
    if not pool:
        return None
    return sorted(pool, key=lambda i: ((i.stock or 0) <= 0, i.source != "api", -(i.list_date.toordinal() if i.list_date else 0), i.id))[0]


def supplier_status(db: Session, tenant, products: Iterable[Product], cfg: dict | None = None, now: datetime | None = None) -> dict[int, dict]:
    """Existencia del proveedor por producto (solo de items vinculados): qty, at, fresh, available (lo vendible bajo
    pedido: fresco, Ajustes lo permite y menos lo ya prometido a pedidos web), committed, name, source, linked."""
    t = _tenant(db, tenant)
    prods = [p for p in products if p is not None]
    if t is None or not prods:
        return {}
    cfg = cfg if cfg is not None else settings_of(t)
    now = now or _now()
    ids = [p.id for p in prods]
    by_pid: dict[int, list] = defaultdict(list)
    # solo las columnas que hacen falta: la fila cruda de SiReTT (raw) y la descripcion pesan y aca no se usan
    cols = (SupplierItem.id, SupplierItem.product_id, SupplierItem.supplier_id, SupplierItem.stock, SupplierItem.active, SupplierItem.source, SupplierItem.list_date)
    for it in db.execute(select(*cols).where(SupplierItem.tenant_id == t.id, SupplierItem.product_id.in_(ids))):
        by_pid[it.product_id].append(it)
    names = (
        {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.id.in_({i.supplier_id for v in by_pid.values() for i in v})))} if by_pid else {}
    )
    committed = _web_requests(db, t.id, [pid for pid in ids if pid in by_pid])
    limit = timedelta(hours=max_age_hours(cfg))
    backorder = bool(cfg.get("backorder_enabled", True))
    out: dict[int, dict] = {}
    for p in prods:
        items = by_pid.get(p.id) or []
        if not items:
            unlinked = int(p.supplier_stock or 0) if (p.supplier_stock or 0) > 0 else None
            out[p.id] = {
                "linked": False,
                "qty": ZERO,
                "at": None,
                "fresh": False,
                "available": ZERO,
                "committed": ZERO,
                "name": None,
                "source": None,
                "unlinked_qty": unlinked,
            }
            continue
        best = _pick(items)
        qty = _dec(sum(int(i.stock or 0) for i in items if i.active and (i.stock or 0) > 0))
        pa = _aware(p.supplier_updated_at)
        ld = best.list_date if best else None
        if pa is None or (ld is not None and pa.date() > ld + timedelta(days=1)):
            at = list_datetime(ld)  # la marca del producto no es de este dato (o no la hay): manda la fecha de la lista
        else:
            at = pa
        fresh = at is not None and now - at <= limit
        comm = committed.get(p.id, ZERO)
        avail = max(qty - comm, ZERO) if (fresh and backorder) else ZERO
        out[p.id] = {
            "linked": True,
            "qty": qty,
            "at": at,
            "fresh": bool(fresh),
            "available": avail,
            "committed": comm,
            "name": names.get(best.supplier_id) if best else None,
            "source": best.source if best else None,
            "unlinked_qty": None,
        }
    return out


# ---------- la funcion unica ----------
def _products(db: Session, tenant_id: int, products) -> list[Product]:
    plist = list(products or [])
    objs = [p for p in plist if isinstance(p, Product)]
    ids = sorted({int(p) for p in plist if not isinstance(p, Product)})
    if ids:
        objs += list(db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.id.in_(ids))))
    seen: dict[int, Product] = {}
    for p in objs:
        if p.tenant_id == tenant_id and p.id not in seen:
            seen[p.id] = p
    return list(seen.values())


def _action(state: str, cfg: dict) -> str | None:
    if state in SELLABLE_STATES:
        return "comprar"
    a = cfg.get("out_of_stock_action") or "whatsapp"
    return None if a == "ninguna" else a


def _public_label(state: str, own_free: Decimal, ultimas: bool, lead: str | None, cfg: dict) -> str:
    if state == "en_bodega":
        mode = cfg.get("quantity_display") or "exacta"
        if mode == "exacta":
            return f"En bodega · {qty_text(own_free)} {'disponible' if own_free == 1 else 'disponibles'}"
        if mode == "umbral" and ultimas:
            return "¡Últimas unidades!"
        return STATES["en_bodega"]
    if state == "bajo_pedido":
        return f"Bajo pedido · entrega en {lead}" if lead else STATES["bajo_pedido"]
    return STATES[state]


def _ally_label(state: str, own_free: Decimal, sup_avail: Decimal, lead: str | None) -> str:
    if state == "en_bodega":
        return f"En bodega: {qty_text(own_free)}" + (f" · bajo pedido: {qty_text(sup_avail)}" if sup_avail > 0 else "")
    if state == "bajo_pedido":
        return f"Bajo pedido: {qty_text(sup_avail)}" + (f" · entrega en {lead}" if lead else "")
    if state == "consultar":
        return "Consultar disponibilidad con Crimson"
    return STATES[state]


def _internal_label(state: str, own_free: Decimal, own_res: Decimal, s: dict, now: datetime) -> str:
    if state == "servicio":
        return STATES["servicio"]
    parts = [f"Propio: {qty_text(own_free)}" + (f" ({qty_text(own_res)} reservado)" if own_res > 0 else "")]
    if s["linked"]:
        txt = f"{s['name'] or 'Proveedor'}: {qty_text(s['qty'])} · actualizado {age_text(s['at'], now)}"
        if not s["fresh"]:
            txt += " (dato viejo)"
        if s["committed"] > 0:
            txt += f" · {qty_text(s['committed'])} ya prometido a pedidos web"
        parts.append(txt)
    elif s.get("unlinked_qty"):
        parts.append("Proveedor sin vincular (no cuenta)")
    return " · ".join(parts)


def availability_many(
    db: Session,
    tenant,
    products,
    *,
    audience: str = "interno",
    web_only: bool | None = None,
    by_warehouse: bool = False,
) -> dict[int, dict]:
    """Disponibilidad de varios productos (Product o ids) en un solo viaje. Ver el docstring del modulo.

    web_only: contar solo las bodegas que alimentan la web (por defecto si la audiencia es publico).
    by_warehouse: agrega "bodegas" [{nombre, disponible}] (lo libre por bodega de venta)."""
    if audience not in AUDIENCES:
        raise ValueError(f"audiencia desconocida: {audience}")
    t = _tenant(db, tenant)
    if t is None:
        return {}
    prods = _products(db, t.id, products)
    if not prods:
        return {}
    cfg = settings_of(t)
    if web_only is None:
        web_only = audience == "publico"
    whs = web_warehouses(db, t.id, cfg) if web_only else sellable_warehouses(db, t.id)
    goods = [p.id for p in prods if p.item_type == "producto"]
    own, comp, free = _free_by_wh(db, t.id, goods, whs) if goods else ({}, {}, {})
    now = _now()
    sup = supplier_status(db, t, [p for p in prods if p.item_type == "producto"], cfg, now)
    wh_names = dict(db.execute(select(Warehouse.id, Warehouse.name).where(Warehouse.id.in_(whs))).all()) if (by_warehouse and whs) else {}
    lead = (cfg.get("backorder_lead") or "").strip() or None
    mode = cfg.get("quantity_display") or "exacta"
    low = _dec(cfg.get("low_threshold") or 0)
    out: dict[int, dict] = {}
    for p in prods:
        s = sup.get(p.id) or {
            "linked": False,
            "qty": ZERO,
            "at": None,
            "fresh": False,
            "available": ZERO,
            "committed": ZERO,
            "name": None,
            "source": None,
            "unlinked_qty": None,
        }
        if p.item_type != "producto":
            state = "servicio"
            on_hand = own_res = own_web = own_free = ZERO
            max_qty = None
        else:
            on_hand = sum((own.get((p.id, w), ZERO) for w in whs), ZERO)
            own_res = sum((comp.get((p.id, w), (ZERO, ZERO))[0] for w in whs), ZERO)
            own_web = sum((comp.get((p.id, w), (ZERO, ZERO))[1] for w in whs), ZERO)
            own_free = sum((free.get((p.id, w), ZERO) for w in whs), ZERO)
            if own_free > 0:
                state = "en_bodega"
            elif s["available"] > 0:
                state = "bajo_pedido"
            elif s["qty"] > 0:
                state = "consultar"
            else:
                state = "agotado"
            max_qty = own_free + s["available"]
        ultimas = state == "en_bodega" and own_free <= low and mode != "oculta"
        if audience == "publico":
            label = _public_label(state, own_free, ultimas, lead, cfg)
        elif audience == "aliado":
            label = _ally_label(state, own_free, s["available"], lead)
        else:
            label = _internal_label(state, own_free, own_res, s, now)
        row = {
            "product_id": p.id,
            "state": state,
            "estado": state,  # mismo valor: el nombre viejo de la llave sigue sirviendo a la vitrina y al panel
            "label": label,
            "mensaje": label,
            "plazo": lead if state == "bajo_pedido" else None,
            "disponible": state in SELLABLE_STATES,
            "accion": _action(state, cfg),
            "ultimas": bool(ultimas),
            "ocultar": state == "agotado" and cfg.get("out_of_stock") == "ocultar",
        }
        if audience == "publico":
            exact = mode == "exacta"
            row["cantidad"] = _num(own_free) if (state == "en_bodega" and exact) else None
            row["max_qty"] = _num(max_qty) if (exact and max_qty is not None) else None
        elif audience == "aliado":
            row.update(
                {
                    "en_bodega": _num(own_free),
                    "bajo_pedido_cantidad": _num(s["available"]),
                    "actualizado_al": s["at"] if s["linked"] else None,
                    "dato_vigente": bool(s["fresh"]),
                    "max_qty": _num(max_qty),
                }
            )
        else:
            row.update(
                {
                    "own_on_hand": _num(on_hand),
                    "own_free": _num(own_free),
                    "own_reserved": _num(own_res),
                    "own_reserved_web": _num(own_web),
                    "supplier_linked": bool(s["linked"]),
                    "supplier_qty": _num(s["qty"]) if s["linked"] else None,
                    "supplier_available": _num(s["available"]),
                    "supplier_committed": _num(s["committed"]),
                    "supplier_name": s["name"],
                    "supplier_source": s["source"],
                    "supplier_updated_at": s["at"],
                    "supplier_fresh": bool(s["fresh"]),
                    "supplier_age": age_text(s["at"], now) if s["linked"] else None,
                    "supplier_unlinked_qty": s["unlinked_qty"],
                    "max_qty": _num(max_qty),
                    "label_publico": _public_label(state, own_free, ultimas, lead, cfg),
                    # nombres del panel "Inventario web" (compatibilidad)
                    "propio": _num(on_hand),
                    "reservado": _num(own_res),
                    "reservado_web": _num(own_web),
                    "libre": _num(own_free),
                    "proveedor": _num(s["qty"]) if s["linked"] else None,
                    "proveedor_al": s["at"],
                    "proveedor_viejo": bool(s["linked"] and s["qty"] > 0 and not s["fresh"]),
                }
            )
        if by_warehouse and p.item_type == "producto":
            row["bodegas"] = [
                {"nombre": wh_names.get(w), "disponible": _num(free.get((p.id, w), ZERO))}
                for w in whs
                if (p.id, w) in own or (p.id, w) in free
            ]
        out[p.id] = row
    return out


def one(db: Session, tenant, product, **kw) -> dict:
    pid = product.id if isinstance(product, Product) else int(product)
    return availability_many(db, tenant, [product], **kw).get(pid) or {}


def sort_key(row: dict | None) -> int:
    """Primero lo propio: en bodega, bajo pedido, servicio, consultar, agotado."""
    return {"en_bodega": 0, "bajo_pedido": 1, "servicio": 2, "consultar": 3, "agotado": 4}.get((row or {}).get("state"), 5)
