"""Tienda conectada al inventario real: disponibilidad web, reservas de pedidos web y salida al despachar.

Reglas
- Disponible web = existencias en las bodegas que alimentan la web (Ajustes de la tienda; vacio = todas las
  activas) - reservas confirmadas y vigentes (de proyectos, de bodega y de otros pedidos web).
  Productos serializados: por bodega cuenta el menor entre el ledger y las series "disponible".
- Las variantes (ProductVariant) comparten la existencia del producto padre: el ledger no tiene variante.
- Estados: disponible | bajo_pedido (proveedor con existencia y dato reciente o sin fecha) | consultar
  (proveedor con dato viejo) | agotado | servicio.
- Pedido web: reserva AUTOMATICA (origin='web', sin aprobacion humana) bajo el mismo candado de las reservas
  formales (AC06). Lo que no hay en bodega pero el proveedor tiene sale "bajo pedido": no reserva stock
  propio, genera una solicitud de compra (reason='pedido_web') para el admin.
- enforce_stock (Ajustes de la tienda): encendido, lo que no hay ni se puede pedir al proveedor se rechaza con
  409 "Ya no hay suficiente X: quedan N". Apagado (defecto, para no frenar la tienda mientras se carga el
  inventario): se reserva lo que hay y el resto queda "requiere compra".
- Despacho (enviado/entregado) o factura: la reserva se consume y sale del inventario UNA sola vez. Las
  salidas llevan order_id; la factura de un pedido ya despachado solo enlaza esas salidas (asi anular la
  factura devuelve la mercaderia, como siempre).

API para el checkout (todas reciben la sesion; las que dicen "hace commit" lo hacen):
- settings_of(tenant) -> dict
- availability(db, tenant, product_ids, public=True) -> {product_id: {...}}
- hold_for_order(db, order, *, user_id=None, force=False, commit=True) -> dict   (hace commit; 409 si no alcanza)
- mark_paid(db, order) -> dict                  (sin commit)
- release_for_order(db, order, note=None, user_id=None) -> int   (sin commit)
- fulfill_for_order(db, order, user_id=None, invoice=None) -> int (sin commit)
- deduct_for_order_invoice(db, order, invoice, user_id=None) -> int (sin commit; reemplaza deduct_for_invoice)
- on_order_status(db, order, old, new, user_id=None) -> dict (sin commit, salvo la re-reserva al pagar)
- order_stock(db, order) -> dict                 (estado de inventario del pedido para el admin)
- expire_holds(db) -> int                        (worker; hace commit)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from ..models import (
    Invoice,
    Order,
    Product,
    PurchaseRequest,
    PurchaseRequestLine,
    StockMovement,
    StockReservation,
    StockUnit,
    Tenant,
    Warehouse,
)
from . import inventory as invsvc
from . import reservations as rsv
from .documents import audit

KEY = "store_inventory"
DEFAULTS: dict = {
    "enabled": True,  # reservar al crear pedidos web
    "enforce_stock": False,  # True: rechaza lo que no hay ni se puede pedir al proveedor
    "warehouse_ids": [],  # bodegas que alimentan la web; vacio = todas las activas
    "quantity_display": "exacta",  # exacta | umbral | oculta (exacta = como la tienda de siempre)
    "low_threshold": 3,  # "ultimas unidades" a partir de aqui (modo umbral)
    "backorder_enabled": True,  # vender bajo pedido lo que el proveedor tiene
    "backorder_lead": "3-5 días hábiles",
    "supplier_max_age_days": 7,  # dato del proveedor mas viejo -> "Consultar disponibilidad"
    "sell_when_unknown": False,  # vender "consultar" como bajo pedido
    "out_of_stock": "mostrar",  # mostrar | ocultar (agotados en el catalogo)
    "out_of_stock_action": "whatsapp",  # avisarme | whatsapp | ninguna
    "hold_hours_manual": 48,  # SINPE, transferencia, efectivo
    "hold_minutes_card": 30,  # tarjeta
    "card_keywords": ["tarjeta", "card", "onvo", "paypal"],
    "cancel_unpaid_on_expiry": False,  # al vencer la reserva sin pago, cancelar el pedido
}
SELLABLE = ("disponible", "bajo_pedido")
LEGACY_STATE = {"disponible": "en_bodega", "bajo_pedido": "bajo_pedido", "consultar": "agotado", "agotado": "agotado", "servicio": "servicio"}
ZERO = Decimal(0)


def _dec(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def _fmt(q: Decimal) -> str:
    return f"{_dec(q).normalize():f}"


def _now() -> datetime:
    return datetime.now(UTC)


def _tenant(db: Session, tenant) -> Tenant:
    return tenant if isinstance(tenant, Tenant) else db.get(Tenant, int(tenant))


def settings_of(tenant: Tenant | None) -> dict:
    """Ajustes de inventario de la tienda (tenant.settings['store_inventory'] sobre los predeterminados)."""
    return {**DEFAULTS, **(((tenant.settings or {}) if tenant else {}).get(KEY) or {})}


def web_warehouses(db: Session, tenant_id: int, cfg: dict) -> list[int]:
    """Bodegas activas que alimentan la web, la predeterminada primero."""
    active = list(
        db.scalars(select(Warehouse.id).where(Warehouse.tenant_id == tenant_id, Warehouse.active).order_by(Warehouse.is_default.desc(), Warehouse.id))
    )
    chosen = {int(x) for x in (cfg.get("warehouse_ids") or [])}
    return [w for w in active if w in chosen] if chosen else active


def web_price(p: Product):
    """Precio base de la tienda (sin IVA, moneda del producto)."""
    return p.web_price if p.web_price is not None else p.price


# ---------- existencias ----------
def _own_by_wh(db: Session, tenant_id: int, product_ids: list[int] | None, whs: list[int]) -> dict[tuple[int, int], Decimal]:
    """{(producto, bodega): existencia}; serializados = min(ledger, series disponibles)."""
    if not whs:
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
    if not whs:
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


def _supplier(p: Product, cfg: dict, now: datetime) -> str | None:
    """'fresco' | 'viejo' | None (sin existencia del proveedor)."""
    if (p.supplier_stock or 0) <= 0:
        return None
    at = rsv._aware(p.supplier_updated_at)
    if at is None:
        return "fresco"  # sin fecha (cargado a mano): como siempre, bajo pedido
    if now - at > timedelta(days=int(cfg.get("supplier_max_age_days") or 0)):
        return "viejo"
    return "fresco"


def _state(p: Product, libre: Decimal, cfg: dict, now: datetime) -> dict:
    if p.item_type != "producto":
        return {"estado": "servicio", "mensaje": "Servicio", "plazo": None}
    if libre > 0:
        mode = cfg.get("quantity_display") or "umbral"
        ultimas = libre <= _dec(cfg.get("low_threshold") or 0)
        if mode == "exacta":
            msg = f"Disponible ({_fmt(libre)} en bodega)"  # mismo texto que la vitrina de siempre
        elif mode == "umbral" and ultimas:
            msg = "¡Últimas unidades!"
        else:
            msg = "Disponible"
        return {"estado": "disponible", "mensaje": msg, "plazo": None, "ultimas": bool(ultimas and mode != "oculta")}
    sup = _supplier(p, cfg, now)
    lead = cfg.get("backorder_lead") or None
    if sup == "fresco" and cfg.get("backorder_enabled", True):
        return {"estado": "bajo_pedido", "mensaje": f"Bajo pedido · entrega en {lead}" if lead else "Bajo pedido", "plazo": lead}
    if sup is not None:
        return {"estado": "consultar", "mensaje": "Consultar disponibilidad", "plazo": None}
    return {"estado": "agotado", "mensaje": "Agotado", "plazo": None}


def availability(db: Session, tenant, product_ids, public: bool = True) -> dict[int, dict]:
    """Disponibilidad web por producto. public=True: lo que puede ver el cliente (sin costos ni existencias
    del proveedor; la cantidad solo si Ajustes dice "exacta"). public=False agrega el detalle interno."""
    t = _tenant(db, tenant)
    cfg = settings_of(t)
    ids = sorted({int(x) for x in product_ids})
    if not ids or t is None:
        return {}
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.id.in_(ids)))}
    whs = web_warehouses(db, t.id, cfg)
    own, comp, free = _free_by_wh(db, t.id, list(prods), whs)
    now = _now()
    out: dict[int, dict] = {}
    for pid, p in prods.items():
        libre = sum((free.get((pid, w), ZERO) for w in whs), ZERO)
        st = _state(p, libre, cfg, now)
        estado = st["estado"]
        row = {
            "product_id": pid,
            "estado": estado,
            "state": LEGACY_STATE[estado],  # compatibilidad con la vitrina actual (data-s)
            "disponible": estado in SELLABLE or estado == "servicio" or (estado == "consultar" and bool(cfg.get("sell_when_unknown"))),
            "mensaje": st["mensaje"],
            "label": st["mensaje"],
            "plazo": st["plazo"],
            "ultimas": bool(st.get("ultimas")),
            "cantidad": libre if (estado == "disponible" and cfg.get("quantity_display") == "exacta") else None,
            "accion": (None if cfg.get("out_of_stock_action") == "ninguna" else cfg.get("out_of_stock_action")) if estado in ("agotado", "consultar") else None,
            "ocultar": estado == "agotado" and cfg.get("out_of_stock") == "ocultar",
        }
        if not public:
            row.update(
                {
                    "propio": sum((own.get((pid, w), ZERO) for w in whs), ZERO),
                    "reservado": sum((comp.get((pid, w), (ZERO, ZERO))[0] for w in whs), ZERO),
                    "reservado_web": sum((comp.get((pid, w), (ZERO, ZERO))[1] for w in whs), ZERO),
                    "libre": libre,
                    "proveedor": p.supplier_stock,
                    "proveedor_al": p.supplier_updated_at,
                    "proveedor_viejo": _supplier(p, cfg, now) == "viejo",
                }
            )
        out[pid] = row
    return out


def visible(db: Session, tenant, products: list[Product]) -> list[Product]:
    """Quita del catalogo los agotados cuando Ajustes dice "ocultar" (la ficha directa sigue abriendo)."""
    t = _tenant(db, tenant)
    if settings_of(t).get("out_of_stock") != "ocultar" or not products:
        return products
    av = availability(db, t, [p.id for p in products])
    return [p for p in products if not av.get(p.id, {}).get("ocultar")]


# ---------- pedidos ----------
def _needs(db: Session, o: Order) -> tuple[dict[int, Decimal], dict[int, Product]]:
    """{producto: cantidad} de las lineas con producto inventariable (variantes suman al padre)."""
    need: dict[int, Decimal] = {}
    for ln in o.lines:
        if ln.product_id:
            need[ln.product_id] = need.get(ln.product_id, ZERO) + _dec(ln.quantity)
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(need)))) if p.item_type == "producto"} if need else {}
    return {k: v for k, v in need.items() if k in prods and v > 0}, prods


def _hold_expiry(o: Order, cfg: dict) -> datetime | None:
    if o.status != "nuevo":
        return None  # pagado (o gratis): se mantiene hasta el despacho
    pm = (o.payment_method or "").lower()
    if pm and any(k.lower() in pm for k in (cfg.get("card_keywords") or [])):
        return _now() + timedelta(minutes=int(cfg.get("hold_minutes_card") or 30))
    return _now() + timedelta(hours=int(cfg.get("hold_hours_manual") or 48))


def _order_wh_order(p: Product, whs: list[int]) -> list[int]:
    """Primero la bodega de origen del producto (si alimenta la web), luego el resto."""
    return sorted(whs, key=lambda w: (w != p.warehouse_id, whs.index(w)))


def _purchase_request(db: Session, o: Order, rows: list[tuple[Product, Decimal, str]]) -> PurchaseRequest:
    from .sequences import next_number

    number, _ = next_number(db, o.tenant_id, "SC")
    req = PurchaseRequest(
        tenant_id=o.tenant_id,
        number=number,
        status="borrador",
        reason="pedido_web",
        order_id=o.id,
        notes=f"Pedido web {o.number}: " + "; ".join(f"{p.name} x {_fmt(q)} ({why})" for p, q, why in rows),
    )
    total = ZERO
    for p, q, why in rows:
        cost = _dec(p.cost) if p.cost is not None else ZERO
        req.lines.append(PurchaseRequestLine(product_id=p.id, name=p.name[:200], quantity=q, unit_cost=cost, note=why[:200]))
        total += cost * q
        if p.supplier_id and not req.supplier_id:
            req.supplier_id = p.supplier_id
    req.total_cost = total.quantize(Decimal("0.01"))
    db.add(req)
    db.flush()
    return req


def hold_for_order(db: Session, o: Order, *, user_id: int | None = None, force: bool = False, commit: bool = True) -> dict:
    """Reserva atomica de un pedido web (llamar con el pedido ya en la sesion, flush hecho, SIN commit previo).

    - Toma el candado de cada (producto, bodega web) en orden fijo (sin abrazos mortales), relee lo libre y
      crea reservas CONFIRMADAS origin='web' con order_id y vencimiento segun el metodo de pago.
    - Lo que no alcanza: bajo pedido (proveedor reciente) -> solicitud de compra; si no y enforce_stock esta
      encendido -> rollback de TODO (pedido incluido) y 409 "Ya no hay suficiente X: quedan N".
      force=True nunca rechaza (lo faltante queda "requiere compra"): para pedidos ya pagados.
    - Hace commit (el candado vive hasta el commit). Devuelve el resumen para la respuesta del checkout.
    """
    t = db.get(Tenant, o.tenant_id)
    cfg = settings_of(t)
    result = {"reservas": [], "bajo_pedido": [], "requiere_compra": False, "solicitud_compra": None, "vence": None}
    need, prods = _needs(db, o)
    ya = _order_reservations(db, o)
    if not cfg.get("enabled", True) or not need or ya:
        # apagado, sin productos inventariables o ya reservado (llamarla dos veces no duplica)
        result["reservas"] = [{"id": r.id, "product_id": r.product_id, "warehouse_id": r.warehouse_id, "quantity": r.quantity} for r in ya]
        if commit:
            db.commit()
        return result
    whs = web_warehouses(db, o.tenant_id, cfg)
    keys = sorted({(pid, w) for pid in need for w in whs})
    proc_locks = []
    now = _now()
    try:
        for pid, w in keys:
            lk = rsv._lock(db, o.tenant_id, pid, w)
            if lk is not None:
                proc_locks.append(lk)
        _, _, free = _free_by_wh(db, o.tenant_id, list(need), whs)
        plan: list[tuple[Product, int, Decimal]] = []
        back: list[tuple[Product, Decimal, str]] = []
        problems: list[str] = []
        for pid, qty in need.items():
            p = prods[pid]
            libre = sum((free.get((pid, w), ZERO) for w in whs), ZERO)
            take = min(qty, libre)
            resto = qty - take
            for w in _order_wh_order(p, whs):
                if take <= 0:
                    break
                q = min(free.get((pid, w), ZERO), take)
                if q > 0:
                    plan.append((p, w, q))
                    take -= q
            if resto <= 0:
                continue
            sup = _supplier(p, cfg, now)
            backorder_ok = cfg.get("backorder_enabled", True) and (sup == "fresco" or (sup == "viejo" and cfg.get("sell_when_unknown")))
            if backorder_ok and resto <= _dec(p.supplier_stock):
                back.append((p, resto, "bajo pedido al proveedor"))
            elif force or not cfg.get("enforce_stock"):
                back.append((p, resto, "sin existencia: requiere compra"))
            elif backorder_ok:
                problems.append(f"Ya no hay suficiente {p.name}: quedan {_fmt(libre)} y el proveedor reporta {p.supplier_stock}.")
            else:
                problems.append(f"Ya no hay suficiente {p.name}: quedan {_fmt(libre)}.")
        if problems:
            raise HTTPException(409, " ".join(problems))
        expires = _hold_expiry(o, cfg)
        for p, w, q in plan:
            r = StockReservation(
                tenant_id=o.tenant_id,
                product_id=p.id,
                warehouse_id=w,
                quantity=q,
                consumed_quantity=ZERO,
                status="confirmada",
                origin="web",
                order_id=o.id,
                expires_at=expires,
                requested_by=user_id,
                approved_at=now,  # automatica: approved_by vacio = "Regla automática"
                reason=f"Pedido web {o.number}",
            )
            db.add(r)
            db.flush()
            result["reservas"].append({"id": r.id, "product_id": p.id, "warehouse_id": w, "quantity": q})
        if back:
            req = _purchase_request(db, o, back)
            result.update(requiere_compra=True, solicitud_compra=req.number)
            result["bajo_pedido"] = [
                {"product_id": p.id, "name": p.name, "quantity": q, "motivo": why, "plazo": cfg.get("backorder_lead")} for p, q, why in back
            ]
            _notify_purchase(db, t, o, req, back)
        result["vence"] = expires
        audit(
            db,
            o.tenant_id,
            user_id,
            "hold",
            "order",
            o.id,
            {"reservas": len(result["reservas"]), "bajo_pedido": len(back), "solicitud": result["solicitud_compra"]},
        )
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        for lk in proc_locks:
            lk.release()
    return result


def _notify_purchase(db: Session, t: Tenant, o: Order, req: PurchaseRequest, rows) -> None:
    import html

    from .mail import notify_roles

    items = "".join(f"<li>{html.escape(p.name)} × {_fmt(q)} · {html.escape(why)}</li>" for p, q, why in rows)
    notify_roles(
        db,
        t,
        ("admin", "supervisor"),
        f"Pedido web {o.number} requiere compra ({req.number})",
        f"<p>El pedido <b>{html.escape(o.number)}</b> incluye artículos sin existencia propia:</p><ul>{items}</ul>"
        f"<p>Se creó la solicitud de compra <b>{html.escape(req.number)}</b> en borrador.</p>",
        "purchase_request",
        req.id,
    )


def _order_reservations(db: Session, o: Order, statuses=("pendiente", "confirmada")) -> list[StockReservation]:
    return list(
        db.scalars(
            select(StockReservation)
            .where(StockReservation.tenant_id == o.tenant_id, StockReservation.order_id == o.id, StockReservation.status.in_(statuses))
            .order_by(StockReservation.id)
        )
    )


def _moved(db: Session, o: Order, invoice_id: int | None = None) -> dict[int, Decimal]:
    """Cantidad neta que YA salio del inventario por este pedido (por despacho o por su factura)."""
    conds = [StockMovement.order_id == o.id]
    inv_id = invoice_id or o.invoice_id
    if inv_id:
        conds.append(and_(StockMovement.invoice_id == inv_id, StockMovement.kind.in_(("venta", "devolucion"))))
    rows = db.execute(
        select(StockMovement.product_id, func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.tenant_id == o.tenant_id, or_(*conds))
        .group_by(StockMovement.product_id)
    )
    return {pid: -_dec(q) for pid, q in rows}


def mark_paid(db: Session, o: Order, user_id: int | None = None) -> dict:
    """Pagado: las reservas web del pedido dejan de vencer (se mantienen hasta el despacho). Si ya se habian
    vencido y el pedido no tiene nada reservado ni despachado, se vuelve a reservar (force: lo que no haya
    queda "requiere compra"); esto hace commit."""
    rows = _order_reservations(db, o, ("confirmada",))
    for r in rows:
        r.expires_at = None
    if rows:
        return {"reservas_fijas": len(rows)}
    has_any = db.scalar(
        select(func.count(StockReservation.id)).where(StockReservation.order_id == o.id, StockReservation.status.in_(("confirmada", "consumida")))
    )
    has_req = db.scalar(select(func.count(PurchaseRequest.id)).where(PurchaseRequest.order_id == o.id, PurchaseRequest.status != "cancelada"))
    if has_any or has_req or any(v > 0 for v in _moved(db, o).values()):
        return {"reservas_fijas": 0}
    lapsed = db.scalar(select(func.count(StockReservation.id)).where(StockReservation.order_id == o.id, StockReservation.status == "vencida"))
    if not lapsed:
        return {"reservas_fijas": 0}
    res = hold_for_order(db, o, user_id=user_id, force=True, commit=True)
    return {"re_reserva": len(res["reservas"]), "requiere_compra": res["requiere_compra"]}


def release_for_order(db: Session, o: Order, note: str | None = None, user_id: int | None = None) -> int:
    """Cancelado: libera las reservas abiertas del pedido y cancela su solicitud de compra si sigue en borrador.
    No toca el ledger: si ya habia salido mercaderia, la devolucion se registra aparte (o anulando la factura)."""
    n = 0
    for r in _order_reservations(db, o):
        r.status, r.closed_note = "cancelada", (note or f"Pedido {o.number} cancelado")[:300]
        n += 1
    for req in db.scalars(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id, PurchaseRequest.status == "borrador")):
        req.status = "cancelada"
    if n:
        audit(db, o.tenant_id, user_id, "release", "order", o.id, {"reservas": n})
    return n


def out_for_order(db: Session, o: Order) -> dict[int, Decimal]:
    """Lo que salio de bodega por este pedido y no ha vuelto (por producto, solo lo positivo)."""
    return {pid: q for pid, q in _moved(db, o).items() if q > 0}


def return_for_order(db: Session, o: Order, warehouse_id: int, user_id: int | None = None, note: str | None = None) -> int:
    """Cancelado o reembolsado DESPUES de despachar, con el producto devuelto: entra a la bodega elegida lo que el
    pedido saco y no ha vuelto. Lleva el pedido y su factura, asi anular la factura despues no devuelve otra vez.
    Idempotente."""
    w = db.get(Warehouse, warehouse_id)
    if not w or w.tenant_id != o.tenant_id or not w.active:
        from fastapi import HTTPException

        raise HTTPException(422, "Bodega no encontrada")
    n = 0
    for pid, q in out_for_order(db, o).items():
        invsvc.move(
            db,
            o.tenant_id,
            user_id,
            pid,
            w.id,
            "devolucion",
            q,
            o.number,
            note or f"Pedido {o.number}: producto devuelto por el cliente",
            None,
            o.invoice_id,
            movement_type="devolucion_cliente",
            order_id=o.id,
        )
        n += 1
    if n:
        audit(db, o.tenant_id, user_id, "return", "order", o.id, {"movimientos": n, "bodega": w.id})
    return n


def _fallback_wh(db: Session, p: Product) -> int | None:
    if p.warehouse_id:
        w = db.get(Warehouse, p.warehouse_id)
        if w and w.active and w.tenant_id == p.tenant_id:
            return w.id
    w = invsvc.default_warehouse(db, p.tenant_id)
    return w.id if w else None


def fulfill_for_order(db: Session, o: Order, user_id: int | None = None, invoice: Invoice | None = None) -> int:
    """Despacho (o factura): saca del inventario lo que falte por sacar de cada linea, consumiendo primero las
    reservas del pedido (de su bodega) y luego la bodega de origen / predeterminada. Idempotente: lo que ya
    salio por este pedido o por su factura no se vuelve a descontar. Con invoice, las salidas previas del
    pedido quedan ligadas a esa factura. Devuelve cuantos movimientos creo."""
    need, prods = _needs(db, o)
    inv_id = invoice.id if invoice is not None else o.invoice_id
    if invoice is not None:
        db.execute(
            update(StockMovement)
            .where(StockMovement.tenant_id == o.tenant_id, StockMovement.order_id == o.id, StockMovement.invoice_id.is_(None))
            .values(invoice_id=invoice.id)
        )
    moved = _moved(db, o, inv_id)
    reservations = _order_reservations(db, o, ("confirmada",))
    ref = invoice.number if invoice is not None else o.number
    n = 0
    for pid, qty in need.items():
        p = prods[pid]
        falta = qty - max(moved.get(pid, ZERO), ZERO)
        for r in (r for r in reservations if r.product_id == pid):
            pend = _dec(r.quantity) - _dec(r.consumed_quantity)
            take = min(pend, max(falta, ZERO))
            if take <= 0:
                continue
            invsvc.move(
                db, o.tenant_id, user_id, pid, r.warehouse_id, "venta", -take, ref, f"Pedido {o.number}", None, inv_id, movement_type="venta", order_id=o.id
            )
            falta -= take
            n += 1
            r.consumed_quantity = _dec(r.consumed_quantity) + take
            if _dec(r.consumed_quantity) >= _dec(r.quantity):
                r.status, r.closed_note = "consumida", f"Despachado en {o.number}"
        if falta > 0:
            w = _fallback_wh(db, p)
            if w is None:
                continue  # sin bodega configurada: como deduct_for_invoice, no hace nada
            invsvc.move(db, o.tenant_id, user_id, pid, w, "venta", -falta, ref, f"Pedido {o.number}", None, inv_id, movement_type="venta", order_id=o.id)
            n += 1
    # reservas de productos que ya no estan en el pedido: se liberan
    for r in reservations:
        if r.status == "confirmada":
            r.status, r.closed_note = ("consumida" if _dec(r.consumed_quantity) > 0 else "cancelada"), f"Pedido {o.number} despachado; el resto se liberó"
    if n:
        audit(db, o.tenant_id, user_id, "fulfill", "order", o.id, {"movimientos": n, "factura": inv_id})
    return n


def _managed(db: Session, o: Order) -> bool:
    """El pedido ya lo lleva esta capa (tiene reservas, salidas o solicitud de compra propias)."""
    return bool(
        db.scalar(select(func.count(StockReservation.id)).where(StockReservation.order_id == o.id))
        or db.scalar(select(func.count(StockMovement.id)).where(StockMovement.order_id == o.id))
        or db.scalar(select(func.count(PurchaseRequest.id)).where(PurchaseRequest.order_id == o.id))
    )


def deduct_for_order_invoice(db: Session, o: Order, invoice: Invoice, user_id: int | None = None) -> int:
    """Reemplaza inventory.deduct_for_invoice al facturar un pedido. Pedido sin control de esta capa (viejo):
    exactamente como siempre. Con reservas o ya despachado: descuenta solo lo que falte, desde la bodega
    reservada, y liga todo a la factura (anularla devuelve la mercaderia)."""
    if not _managed(db, o):
        return invsvc.deduct_for_invoice(db, invoice, user_id)
    return fulfill_for_order(db, o, user_id, invoice)


def on_order_status(db: Session, o: Order, old: str | None, new: str, user_id: int | None = None) -> dict:
    """Gancho de PATCH /orders/{id}: pagado/preparando fija la reserva, enviado/entregado descuenta, cancelado libera."""
    if new == old:
        return {}
    if new in ("pagado", "preparando") and old == "nuevo":
        return mark_paid(db, o, user_id)
    if new in ("enviado", "entregado"):
        return {"movimientos": fulfill_for_order(db, o, user_id)}
    if new == "cancelado":
        return {"liberadas": release_for_order(db, o, None, user_id)}
    return {}


def order_stock(db: Session, o: Order) -> dict:
    """Estado de inventario del pedido para el admin."""
    res = list(
        db.scalars(select(StockReservation).where(StockReservation.tenant_id == o.tenant_id, StockReservation.order_id == o.id).order_by(StockReservation.id))
    )
    reqs = list(db.scalars(select(PurchaseRequest).where(PurchaseRequest.tenant_id == o.tenant_id, PurchaseRequest.order_id == o.id)))
    moved = _moved(db, o)
    names = {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_({r.product_id for r in res} | set(moved))))} if (res or moved) else {}
    whn = dict(db.execute(select(Warehouse.id, Warehouse.name).where(Warehouse.tenant_id == o.tenant_id)).all())
    activas = [r for r in res if r.status == "confirmada"]
    if any(v > 0 for v in moved.values()):
        estado = "despachado"
    elif any(rq.status in ("borrador", "solicitada") for rq in reqs):
        estado = "requiere_compra"
    elif activas:
        estado = "reservado"
    elif any(r.status == "vencida" for r in res):
        estado = "vencido"
    elif res:
        estado = "liberado"
    else:
        estado = "sin_control"
    return {
        "order_id": o.id,
        "estado": estado,
        "reservas": [
            {
                "id": r.id,
                "product_id": r.product_id,
                "name": names.get(r.product_id),
                "warehouse": whn.get(r.warehouse_id),
                "quantity": r.quantity,
                "consumed_quantity": r.consumed_quantity,
                "status": r.status,
                "expires_at": r.expires_at,
                "closed_note": r.closed_note,
            }
            for r in res
        ],
        "salidas": [{"product_id": pid, "name": names.get(pid), "quantity": q} for pid, q in moved.items() if q],
        "solicitudes_compra": [{"id": rq.id, "number": rq.number, "status": rq.status} for rq in reqs],
    }


# ---------- worker ----------
def expire_holds(db: Session) -> int:
    """Reservas web vencidas sin pago -> vencida, con nota en el pedido; si Ajustes lo pide, cancela el pedido
    (y su solicitud de compra en borrador). Hace commit. El resto de reservas las vence reservations.expire_due."""
    now = _now()
    rows = list(
        db.scalars(
            select(StockReservation).where(
                StockReservation.origin == "web",
                StockReservation.status.in_(rsv.OPEN),
                StockReservation.expires_at.is_not(None),
                StockReservation.expires_at <= now,
            )
        )
    )
    orders: set[int] = set()
    for r in rows:
        r.status, r.closed_note = "vencida", "Pedido sin pagar: venció la reserva"
        if r.order_id:
            orders.add(r.order_id)
    for oid in orders:
        o = db.get(Order, oid)
        if not o or o.status != "nuevo":
            continue
        stamp = f"[{now:%Y-%m-%d %H:%M}] Venció la reserva de inventario sin pago."
        o.notes = f"{o.notes}\n{stamp}" if o.notes else stamp
        if settings_of(db.get(Tenant, o.tenant_id)).get("cancel_unpaid_on_expiry"):
            o.status = "cancelado"
            release_for_order(db, o, "Venció sin pago")
            audit(db, o.tenant_id, None, "status", "order", o.id, {"status": "cancelado", "motivo": "reserva vencida sin pago"})
    db.commit()
    return len(rows)


def back_in_stock(db: Session) -> int:
    """Avisos "Avisarme cuando haya": producto otra vez disponible -> correo a quien lo pidio (una vez)."""
    import html

    from ..models import StoreStockAlert
    from .mail import queue_email

    n = 0
    pend = list(db.scalars(select(StoreStockAlert).where(StoreStockAlert.notified_at.is_(None))))
    by_tenant: dict[int, list] = {}
    for a in pend:
        by_tenant.setdefault(a.tenant_id, []).append(a)
    for tid, alerts in by_tenant.items():
        t = db.get(Tenant, tid)
        if not t or not t.active:
            continue
        av = availability(db, t, [a.product_id for a in alerts])
        for a in alerts:
            st = av.get(a.product_id)
            if not st or st["estado"] not in SELLABLE:
                continue
            p = db.get(Product, a.product_id)
            if a.email:
                queue_email(
                    db,
                    t,
                    a.email,
                    f"Ya hay {p.name} · {t.name}",
                    f"<p>¡Buenas noticias! <b>{html.escape(p.name)}</b> ya está {html.escape(st['mensaje'].lower())}.</p>",
                    "product",
                    p.id,
                )
            a.notified_at = _now()
            n += 1
    db.commit()
    return n
