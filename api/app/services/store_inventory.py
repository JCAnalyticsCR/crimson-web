"""Tienda conectada al inventario real: reservas de pedidos web, compra bajo pedido y salida al despachar.

La disponibilidad (propio + proveedor, estados y etiquetas) NO se calcula aca: es la funcion unica de
services/availability (`availability_many`), que este modulo reexporta junto con los Ajustes de la tienda.

Reglas
- Lo que se vende en linea por producto es `max_qty` = libre propio en las bodegas de la web + lo que el proveedor
  tiene con dato vigente, menos lo ya prometido en solicitudes de compra de pedidos web abiertas. Lo que no tiene ni
  existencia propia ni del proveedor (o el dato del proveedor esta viejo) NO se compra en linea: 409 y la tienda
  ofrece "Consultar por WhatsApp". El viejo enforce_stock ya no cambia esto (se acepta y se ignora).
- Las variantes (ProductVariant) comparten la existencia del producto padre: el ledger no tiene variante.
- Pedido web: reserva AUTOMATICA (origin='web', sin aprobacion humana) bajo el mismo candado de las reservas
  formales (AC06). Lo que no hay en bodega pero el proveedor tiene sale "bajo pedido": no reserva stock
  propio, genera una solicitud de compra (reason='pedido_web'). La compra nace al cobrar: mientras el pedido no
  se paga la solicitud queda "reservada" (aparta lo del proveedor hasta que el pedido vence, sin aviso: al proveedor
  no se le devuelve nada) y al pagarse pasa a borrador con UN aviso "PAGADO: comprar hoy" con los codigos del
  proveedor (activate_requests). Al recibir esa compra, lo que entra a bodega queda reservado para ESE pedido
  (reserve_received).
- Vencimiento (expire_holds): las reservas web vencen, y los pedidos "nuevo" con el plazo para pagar vencido (tengan
  o no reserva propia) dejan una nota en el historial; con cancel_unpaid_on_expiry se cancelan, pero nunca uno con
  tarjeta antes de su margen ni sin preguntarle a ONVO (si ONVO no responde, se reintenta) ni uno con comprobante en
  revision. El efectivo (se cobra al retirar) y "a coordinar" no tienen plazo para pagar: solo vence lo apartado.
  Con comprobante en revision lo apartado espera la revision hasta un tope contado desde el PRIMER comprobante
  (on_review); si lo rechazan, la primera vez el pedido tiene al menos 24 h para subir otro.
- force=True (pedido ya pagado que se vuelve a reservar) nunca rechaza: lo que falte queda "requiere compra".
- Despacho (enviado/entregado) o factura: la reserva se consume y sale del inventario UNA sola vez. Las
  salidas llevan order_id; la factura de un pedido ya despachado solo enlaza esas salidas (asi anular la
  factura devuelve la mercaderia, como siempre).

API para el checkout (todas reciben la sesion; las que dicen "hace commit" lo hacen):
- settings_of(tenant) -> dict
- availability(db, tenant, product_ids, public=True) -> {product_id: {...}}   (atajo a availability_many)
- hold_for_order(db, order, *, user_id=None, force=False, commit=True) -> dict   (hace commit; 409 si no alcanza)
- reserve_received(db, request, warehouse_id, user_id=None) -> int  (sin commit; compra recibida de un pedido web)
- mark_paid(db, order) -> dict                  (sin commit)
- activate_requests(db, order, user_id=None) -> list   (sin commit; reservada -> borrador + aviso de compra)
- order_alive(order, now=None, hold_hours=None) -> bool (lo bajo pedido de un pedido sin pagar todavia aparta)
- release_for_order(db, order, note=None, user_id=None) -> int   (sin commit)
- fulfill_for_order(db, order, user_id=None, invoice=None) -> int (sin commit)
- deduct_for_order_invoice(db, order, invoice, user_id=None) -> int (sin commit; reemplaza deduct_for_invoice)
- on_order_status(db, order, old, new, user_id=None) -> dict (sin commit, salvo la re-reserva al pagar)
- on_review(db, order, under_review) -> None     (sin commit; comprobante subido o rechazado)
- order_stock(db, order) -> dict                 (estado de inventario del pedido para el admin)
- expire_holds(db) -> int                        (worker; hace commit)
- back_in_stock(db) -> int                       (worker; hace commit)
"""

from __future__ import annotations

import logging
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
    Tenant,
    Warehouse,
)
from . import inventory as invsvc
from . import reservations as rsv

# Ajustes, bodegas y existencias viven en services/availability (la funcion unica); se reexportan aca
from .availability import (  # noqa: F401
    DEFAULTS,
    KEY,
    RESERVED_REQUEST,
    SELLABLE_STATES,
    STATES,
    _committed,
    _free_by_wh,
    _own_by_wh,
    _pick,
    availability_many,
    settings_of,
    supplier_status,
    web_warehouses,
)
from .documents import audit

log = logging.getLogger("crimson.tienda")
SELLABLE = ("en_bodega", "bajo_pedido")
UNPAID = ("nuevo", "en_revision")  # pedido sin pago confirmado (el comprobante en revision tampoco es pago)
ZERO = Decimal(0)


def _dec(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def _fmt(q: Decimal) -> str:
    return f"{_dec(q).normalize():f}"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite devuelve las fechas sin zona aunque se guarden en UTC."""
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _tenant(db: Session, tenant) -> Tenant:
    return tenant if isinstance(tenant, Tenant) else db.get(Tenant, int(tenant))


def web_price(p: Product):
    """Precio base de la tienda (sin IVA, moneda del producto)."""
    return p.web_price if p.web_price is not None else p.price


def availability(db: Session, tenant, product_ids, public: bool = True) -> dict[int, dict]:
    """Disponibilidad web por producto, con la funcion unica (services/availability). public=True: lo que ve el
    cliente (audiencia publico). public=False: el detalle interno del panel "Inventario web" (bodegas de la web)."""
    return availability_many(db, tenant, product_ids, audience="publico" if public else "interno", web_only=True)


def visible(db: Session, tenant, products: list[Product]) -> list[Product]:
    """Quita del catalogo los agotados cuando Ajustes dice "ocultar", y los agotados en el proveedor (Eurocomp) si
    Ajustes los saca de la tienda (por defecto). La ficha directa sigue abriendo y en el panel siguen todos."""
    from .availability import hides_any

    t = _tenant(db, tenant)
    if not products or not hides_any(settings_of(t)):
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


def order_alive(o: Order, now: datetime | None = None, hold_hours: int | None = None) -> bool:
    """Pedido sin pagar que todavia aparta lo bajo pedido (mismo criterio que availability._web_requests): "nuevo" o
    con comprobante en revision, mientras no pase su plazo (expires_at; la revision tiene tope: on_review). Sin plazo
    para pagar (efectivo, "a coordinar"): mientras dure su reserva propia (hold_hours_manual desde que se hizo)."""
    if o.status not in UNPAID:
        return False
    now = now or _now()
    if o.expires_at is not None:
        return _aware(o.expires_at) > now
    return _aware(o.created_at) + timedelta(hours=int(hold_hours or DEFAULTS["hold_hours_manual"])) > now


def _order_wh_order(p: Product, whs: list[int]) -> list[int]:
    """Primero la bodega de origen del producto (si alimenta la web), luego el resto."""
    return sorted(whs, key=lambda w: (w != p.warehouse_id, whs.index(w)))


def _purchase_request(db: Session, o: Order, rows: list[tuple[Product, Decimal, str]], status: str = "borrador") -> PurchaseRequest:
    from .sequences import next_number

    number, _ = next_number(db, o.tenant_id, "SC")
    req = PurchaseRequest(
        tenant_id=o.tenant_id,
        number=number,
        status=status,
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
    - Lo que no alcanza: bajo pedido (proveedor con dato vigente, hasta lo que todavia no esta prometido a otros
      pedidos web) -> solicitud de compra. Si tampoco alcanza (o no hay nada que vender): rollback de TODO (pedido
      incluido) y 409 "Ya no hay suficiente X: quedan N" (con o sin enforce_stock: en linea no se vende lo que no
      esta disponible). force=True nunca rechaza (lo faltante queda "requiere compra"): para pedidos ya pagados.
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
        # el proveedor se relee bajo el candado: lo prometido por otro pedido web que acaba de entrar ya cuenta
        sup = supplier_status(db, t, list(prods.values()), cfg, now)
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
            avail = (sup.get(pid) or {}).get("available") or ZERO  # vendible bajo pedido (fresco, sin prometer)
            if resto <= avail:
                back.append((p, resto, "bajo pedido al proveedor"))
            elif force:
                back.append((p, resto, "sin existencia: requiere compra"))
            elif avail > 0:
                problems.append(f"Ya no hay suficiente {p.name}: quedan {_fmt(libre)} y {_fmt(avail)} más bajo pedido.")
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
            # la compra nace al cobrar: sin pago, la solicitud solo aparta lo del proveedor (sin aviso); ya pagado
            # (re-reserva con force), nace en borrador con el aviso de compra
            unpaid = o.status in UNPAID
            req = _purchase_request(db, o, back, RESERVED_REQUEST if unpaid else "borrador")
            result.update(requiere_compra=True, solicitud_compra=req.number)
            result["bajo_pedido"] = [
                {"product_id": p.id, "name": p.name, "quantity": q, "motivo": why, "plazo": cfg.get("backorder_lead")} for p, q, why in back
            ]
            if not unpaid:
                _notify_paid_purchase(db, t, o, [req])
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


def _supplier_codes(db: Session, tenant_id: int, product_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """{producto: (codigo del proveedor, nombre del proveedor)} del item vinculado que lo representa (el mismo que
    usa la disponibilidad): el codigo con el que se le pide a Eurocomp (SiReTT)."""
    from ..models import Supplier, SupplierItem

    if not product_ids:
        return {}
    by_pid: dict[int, list] = {}
    for it in db.scalars(select(SupplierItem).where(SupplierItem.tenant_id == tenant_id, SupplierItem.product_id.in_(product_ids))):
        by_pid.setdefault(it.product_id, []).append(it)
    picked = {pid: _pick(items) for pid, items in by_pid.items()}
    sup_ids = {it.supplier_id for it in picked.values() if it is not None}
    names = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.id.in_(sup_ids)))} if sup_ids else {}
    return {pid: (it.code, names.get(it.supplier_id)) for pid, it in picked.items() if it is not None}


def _notify_paid_purchase(db: Session, t: Tenant, o: Order, reqs: list[PurchaseRequest]) -> int:
    """UN aviso por pago: "PAGADO: comprar hoy" con lo que falta y el codigo de cada articulo en el proveedor. El
    efectivo que el equipo avanza sin pago (se cobra al retirar) avisa lo mismo pero sin decir que esta pagado."""
    import html

    from .mail import notify_roles

    e = html.escape
    lines = [ln for r in reqs for ln in r.lines]
    codes = _supplier_codes(db, o.tenant_id, [ln.product_id for ln in lines if ln.product_id])
    proveedores = sorted({c[1] for c in codes.values() if c[1]})
    quien = " y ".join(proveedores) if proveedores else "el proveedor"
    items = "".join(
        f"<li><b>{e(ln.name)}</b> × {_fmt(_dec(ln.quantity))}"
        + (f" · código {e(codes[ln.product_id][0])}" + (f" ({e(codes[ln.product_id][1])})" if codes[ln.product_id][1] else "") if ln.product_id in codes else " · sin código del proveedor: buscalo a mano")
        + "</li>"
        for ln in lines
    )
    numeros = ", ".join(r.number for r in reqs)
    tarde = ""
    if o.expires_at is not None and o.paid_at is not None and _aware(o.paid_at) > _aware(o.expires_at):
        tarde = "<p><b>Ojo:</b> el pago llegó después del plazo; lo apartado ya había vuelto a la venta. Confirmá la existencia con el proveedor antes de prometer fecha.</p>"
    if o.paid_at is not None:
        asunto = f"PAGADO: comprar hoy a {quien} · pedido {o.number} ({numeros})"
        estado = f"<p>El pedido web <b>{e(o.number)}</b> ya está <b>pagado</b>. Hay que comprarle hoy a {e(quien)}:</p>"
    else:
        efectivo = o.payment_kind == "efectivo"
        asunto = f"Comprar hoy a {quien} · pedido {o.number} {'en efectivo' if efectivo else 'sin pago confirmado'} ({numeros})"
        como = "se paga en efectivo y <b>todavía no está cobrado</b>" if efectivo else "<b>todavía no tiene el pago confirmado</b>"
        estado = f"<p>El pedido web <b>{e(o.number)}</b> {como}, pero el equipo ya lo avanzó. Hay que comprarle hoy a {e(quien)}:</p>"
    return notify_roles(
        db,
        t,
        ("admin", "supervisor"),
        asunto,
        f"{estado}<ul>{items}</ul>{tarde}"
        f"<p>La solicitud de compra <b>{e(numeros)}</b> quedó en borrador: marcala «solicitada» al hacer el pedido. Al recibir la compra queda reservada para este pedido.</p>",
        "purchase_request",
        reqs[0].id,
    )


def activate_requests(db: Session, o: Order, user_id: int | None = None) -> list[PurchaseRequest]:
    """El pedido se pago (o el efectivo contra entrega avanza): sus solicitudes "reservada" pasan a borrador y sale UN
    aviso de compra. Una que alguien mando a la papelera para limpiar vuelve de la papelera: el pedido pagado necesita
    esa compra (filtrarla dejaba un pedido cobrado sin comprar). Idempotente: lo ya activado no vuelve a avisar. Sin
    commit."""
    reqs = list(
        db.scalars(
            select(PurchaseRequest)
            .where(PurchaseRequest.tenant_id == o.tenant_id, PurchaseRequest.order_id == o.id, PurchaseRequest.status == RESERVED_REQUEST)
            .order_by(PurchaseRequest.id)
        )
    )
    if not reqs:
        return []
    motivo = f"pedido {o.number} pagado" if o.paid_at is not None else f"pedido {o.number} en efectivo avanzado por el equipo"
    for r in reqs:
        r.status = "borrador"
        diff = {"status": "borrador", "motivo": motivo}
        if r.trashed_at is not None:
            r.trashed_at, r.trashed_by = None, None
            diff["restaurada"] = "estaba en la papelera"
        audit(db, o.tenant_id, user_id, "status", "purchase_request", r.id, diff)
    db.flush()
    _notify_paid_purchase(db, db.get(Tenant, o.tenant_id), o, reqs)
    return reqs


def reserve_received(db: Session, req: PurchaseRequest, warehouse_id: int, user_id: int | None = None) -> int:
    """Compra recibida de un pedido web (reason='pedido_web'): lo que entro a bodega queda reservado para ESE pedido
    (confirmada, sin vencimiento: se compro para el) y no se lo lleva el siguiente cliente. Si el pedido ya se cancelo,
    se despacho o se entrego, la mercaderia queda libre en bodega. Sin commit. Devuelve cuantas reservas creo."""
    if req.reason != "pedido_web" or not req.order_id:
        return 0
    o = db.get(Order, req.order_id)
    if o is None or o.tenant_id != req.tenant_id or o.status in ("cancelado", "reembolsado", "enviado", "entregado"):
        return 0
    need, _ = _needs(db, o)
    n = 0
    now = _now()
    for ln in req.lines:
        if not ln.product_id or ln.product_id not in need or _dec(ln.quantity) <= 0:
            continue
        db.add(
            StockReservation(
                tenant_id=o.tenant_id,
                product_id=ln.product_id,
                warehouse_id=warehouse_id,
                quantity=_dec(ln.quantity),
                consumed_quantity=ZERO,
                status="confirmada",
                origin="web",
                order_id=o.id,
                expires_at=None,
                requested_by=user_id,
                approved_at=now,
                reason=f"Pedido web {o.number}: compra {req.number} recibida",
            )
        )
        n += 1
    if n:
        db.flush()
        audit(db, o.tenant_id, user_id, "reserve_received", "order", o.id, {"solicitud": req.number, "reservas": n})
    return n


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
    """Pagado: la compra bajo pedido nace ahora (solicitud reservada -> borrador + aviso "PAGADO: comprar hoy") y las
    reservas web del pedido dejan de vencer (se mantienen hasta el despacho).
    Pago tardio (la reserva propia ya habia vencido y no hay nada reservado, consumido ni despachado): se vuelve a
    planear TODO el pedido con force (lo propio que haya hoy se reserva; el resto nace en borrador con UN aviso, o
    "requiere compra" si ya no hay) y la solicitud que seguia reservada queda reemplazada: activarla sola dejaba la
    parte propia sin reservar. Esto hace commit. Si al proveedor ya se le pidio algo, no se vuelve a planear."""
    rows = _order_reservations(db, o, ("confirmada",))
    for r in rows:
        r.expires_at = None
    if not rows and _late_replan(db, o):
        for req in db.scalars(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id, PurchaseRequest.status == RESERVED_REQUEST)):
            req.status = "cancelada"
            audit(db, o.tenant_id, user_id, "status", "purchase_request", req.id, {"status": "cancelada", "motivo": f"pedido {o.number} pagado tarde: se vuelve a reservar"})
        db.flush()
        res = hold_for_order(db, o, user_id=user_id, force=True, commit=True)
        return {"re_reserva": len(res["reservas"]), "requiere_compra": res["requiere_compra"]}
    compra = [r.number for r in activate_requests(db, o, user_id)]
    return {"reservas_fijas": len(rows), **({"compra": compra} if compra else {})}


def _late_replan(db: Session, o: Order) -> bool:
    """Lo propio del pedido vencio (sin nada reservado, consumido ni despachado) y su compra al proveedor, si la hay,
    sigue "reservada" (nunca se le pidio): al pagarse hay que planear el pedido de nuevo."""
    lapsed = db.scalar(select(func.count(StockReservation.id)).where(StockReservation.order_id == o.id, StockReservation.status == "vencida"))
    if not lapsed:
        return False
    held = db.scalar(
        select(func.count(StockReservation.id)).where(StockReservation.order_id == o.id, StockReservation.status.in_(("confirmada", "consumida")))
    )
    if held or any(v > 0 for v in _moved(db, o).values()):
        return False
    other = db.scalar(
        select(func.count(PurchaseRequest.id)).where(PurchaseRequest.order_id == o.id, PurchaseRequest.status.not_in((RESERVED_REQUEST, "cancelada")))
    )
    return not other


def release_for_order(db: Session, o: Order, note: str | None = None, user_id: int | None = None) -> int:
    """Cancelado: libera las reservas abiertas del pedido y cancela su solicitud de compra si sigue reservada (sin
    pagar) o en borrador. No toca el ledger: si ya habia salido mercaderia, la devolucion se registra aparte (o
    anulando la factura)."""
    n = 0
    for r in _order_reservations(db, o):
        r.status, r.closed_note = "cancelada", (note or f"Pedido {o.number} cancelado")[:300]
        n += 1
    open_reqs = (RESERVED_REQUEST, "borrador", "solicitada", "recibida")
    for req in db.scalars(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id, PurchaseRequest.status.in_(open_reqs))):
        if req.status in (RESERVED_REQUEST, "borrador"):
            req.status = "cancelada"
            continue
        # ya se le pidio (o ya llego) del proveedor: el proveedor casi nunca acepta devoluciones (Andres, 19/10), asi
        # que la compra sigue y la mercaderia queda en la bodega de Crimson, a la venta
        from .store_orders import _note

        _note(
            o,
            user_id,
            f"La compra {req.number} al proveedor ya estaba {'recibida' if req.status == 'recibida' else 'pedida'}: el producto queda en la bodega de Crimson, a la venta",
        )
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


REVIEW_HOLD = timedelta(hours=48)  # lo apartado espera la revision hasta aqui, contado desde el PRIMER comprobante
REVIEW_GRACE = timedelta(hours=24)  # comprobante rechazado (solo la primera vez): tiempo minimo para subir otro
GRACE_NOTE = "gracia_comprobante"  # clave de la nota: la gracia por rechazo se da una sola vez


def _first_proof_at(o: Order) -> datetime | None:
    ats = []
    for pr in o.proofs or []:
        try:
            ats.append(_aware(datetime.fromisoformat(pr.get("at"))))
        except (TypeError, ValueError):
            continue
    return min(ats) if ats else None


def on_review(db: Session, o: Order, under_review: bool) -> None:
    """Comprobante subido (under_review): el plazo del pedido (y de lo apartado, propio y del proveedor) se estira
    hasta REVIEW_HOLD desde el PRIMER comprobante: el comprobante puede ser bueno y el equipo necesita tiempo, pero un
    comprobante falso no aparta inventario para siempre. Rechazado: la primera vez el pedido vuelve a "nuevo" con al
    menos REVIEW_GRACE para subir otro (el correo de rechazo se lo pide); las siguientes no ganan tiempo. Lo propio
    vence con el pedido. Sin commit. Un pedido sin plazo para pagar no cambia."""
    from . import store_orders as so

    if o.expires_at is None:
        return
    if under_review:
        o.expires_at = max(_aware(o.expires_at), (_first_proof_at(o) or _now()) + REVIEW_HOLD)
    elif not so.has_note(o, GRACE_NOTE):
        o.expires_at = max(_aware(o.expires_at), _now() + REVIEW_GRACE)
        so._note(o, "sistema", f"Comprobante rechazado: el cliente tiene hasta el {so._cr_when(o.expires_at)} para subir otro.", key=GRACE_NOTE)
    for r in _order_reservations(db, o, ("confirmada",)):
        r.expires_at = o.expires_at


def on_order_status(db: Session, o: Order, old: str | None, new: str, user_id: int | None = None) -> dict:
    """Gancho de PATCH /orders/{id}: pagado/preparando fija la reserva, enviado/entregado descuenta, cancelado libera."""
    if new == old:
        return {}
    if old == "nuevo" and new in ("pagado", "preparando", "listo"):
        # pagado, o el efectivo (se cobra al retirar) que el equipo avanza: lo propio deja de vencer hasta el retiro
        # (si ya habia vencido se vuelve a reservar) y la compra bajo pedido nace ahora
        return mark_paid(db, o, user_id)
    if old == "nuevo" and new in ("enviado", "entregado"):
        # efectivo contra entrega que sale directo: la salida consume lo apartado; la compra bajo pedido nace ahora
        activate_requests(db, o, user_id)
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
    apartada = any(rq.status == RESERVED_REQUEST for rq in reqs)
    if any(v > 0 for v in moved.values()):
        estado = "despachado"
    elif any(rq.status in ("borrador", "solicitada") for rq in reqs):
        estado = "requiere_compra"
    elif apartada and order_alive(o, hold_hours=settings_of(db.get(Tenant, o.tenant_id)).get("hold_hours_manual")):
        estado = "espera_pago"  # bajo pedido: se compra al cobrar
    elif activas:
        estado = "reservado"
    elif any(r.status == "vencida" for r in res) or apartada:
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
EXPIRED_NOTE = "vencio"  # marca (clave) de la nota "venció el plazo" en el historial: una sola vez por pedido
HOLD_NOTE = "reserva_vencio"  # sin plazo para pagar (efectivo, "a coordinar"): solo vencio lo apartado
REVIEW_LATE_NOTE = "revision_vencio"  # el comprobante sigue sin revisar al pasar el tope: aviso al equipo, una vez
NOTE_WINDOW = timedelta(days=3)  # un pedido vencido hace mas ya tiene su nota: no se relee cada 5 min


def expire_holds(db: Session) -> int:
    """Pedidos web sin pagar que vencieron. Hace commit (por pedido: uno con datos raros no frena a los demas).

    1. Reservas web vencidas -> vencida (lo propio vuelve a venderse).
    2. Cada pedido "nuevo" de la tienda cuyo plazo para pagar (expires_at) paso, tenga o no reserva propia (todo bajo
       pedido): lo apartado del proveedor ya no cuenta (availability._web_requests) y queda UNA nota en el historial
       (nunca en o.notes, que es la nota del cliente). Si Ajustes lo pide (cancel_unpaid_on_expiry),
       store_orders.expire_unpaid lo cancela: con tarjeta, solo pasado su margen y despues de preguntarle a ONVO
       (pagado -> se aplica; sin respuesta o en proceso -> no se cancela y se reconsulta con espera creciente; si ONVO
       no responde, el resto de sus pedidos con tarjeta espera a la proxima corrida).
    3. Sin plazo para pagar (efectivo: se cobra al retirar; "a coordinar"): solo vencio lo apartado. Nota, sin cancelar
       ni correo al cliente.
    4. Comprobante en revision que paso su tope (on_review): nunca se cancela; nota y UN aviso al equipo de que lo
       apartado volvio a la venta.
    Las empresas inactivas solo vencen lo apartado (paso 1). Lo que se relee esta acotado: NOTE_WINDOW, salvo los
    pedidos de empresas con cancelacion (los sin tarjeta salen en la primera pasada; los con tarjeta, hasta
    CARD_GRACE + CARD_GIVE_UP). Devuelve las reservas vencidas mas los pedidos sin reserva propia que se cancelaron.
    El resto de reservas las vence reservations.expire_due."""
    from . import store_orders as so

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
    with_hold: set[int] = set()
    for r in rows:
        r.status, r.closed_note = "vencida", "Pedido sin pagar: venció la reserva"
        if r.order_id:
            with_hold.add(r.order_id)
    db.commit()
    tenants = {t.id: t for t in db.scalars(select(Tenant))}
    cancel_tids = {tid for tid, t in tenants.items() if t.active and settings_of(t).get("cancel_unpaid_on_expiry")}
    cond = Order.expires_at > now - NOTE_WINDOW
    if cancel_tids:
        card_window = Order.expires_at > now - so.CARD_GRACE - so.CARD_GIVE_UP
        cond = or_(
            cond,
            and_(
                Order.tenant_id.in_(cancel_tids),
                Order.status == "nuevo",
                or_(Order.payment_kind.is_(None), Order.payment_kind != "tarjeta", card_window),
            ),
        )
    due = set(
        db.scalars(select(Order.id).where(Order.channel == "tienda", Order.status.in_(UNPAID), Order.expires_at.is_not(None), Order.expires_at <= now, cond))
    )
    n = len(rows)
    onvo_down: set[int] = set()  # empresas cuyo ONVO no respondio en esta corrida
    for oid in sorted(with_hold | due):
        try:
            o = db.get(Order, oid, populate_existing=True)  # sin candado: la consulta a ONVO no bloquea la fila
            if not o or o.status not in UNPAID:
                continue  # pagado o cancelado: no se toca
            t = tenants.get(o.tenant_id)
            if t is None or not t.active:
                continue
            vencido = o.expires_at is not None and _aware(o.expires_at) <= now
            if o.status == "en_revision":
                if vencido and not so.has_note(o, REVIEW_LATE_NOTE):
                    o = so.lock_order(db, o)
                    if o.status == "en_revision" and not so.has_note(o, REVIEW_LATE_NOTE):
                        so._note(
                            o,
                            "sistema",
                            "El comprobante sigue sin revisar y pasó el tiempo de espera: lo apartado volvió a la venta. "
                            "El pedido no se cancela solo: confirmá o rechazá el pago.",
                            key=REVIEW_LATE_NOTE,
                        )
                        so.notify_team(db, t, o, "revision", detail="Lo apartado para este pedido volvió a la venta mientras el comprobante esperaba revisión.")
                db.commit()
                continue
            if not vencido:
                if oid in with_hold and not so.has_note(o, HOLD_NOTE):
                    o = so.lock_order(db, o)
                    if o.status == "nuevo" and not so.has_note(o, HOLD_NOTE):
                        so._note(o, "sistema", "Venció la reserva de inventario: lo apartado volvió a la venta. El pedido sigue abierto.", key=HOLD_NOTE)
                db.commit()
                continue
            outcome = "sin_cancelar"
            if t.id in cancel_tids:
                outcome = so.expire_unpaid(db, t, o, now, ask_onvo=t.id not in onvo_down)
                if outcome == "sin_respuesta":
                    onvo_down.add(t.id)
            if outcome not in ("cancelado", "revisar") and o.status == "nuevo" and not so.has_note(o, EXPIRED_NOTE):
                o = so.lock_order(db, o)
                if o.status == "nuevo" and not so.has_note(o, EXPIRED_NOTE):
                    so._note(o, "sistema", "Venció el plazo para pagar sin pago confirmado: lo apartado volvió a la venta.", key=EXPIRED_NOTE)
            if outcome == "cancelado" and oid not in with_hold:
                n += 1
            db.commit()
        except Exception:  # noqa: BLE001  un pedido raro no frena a los demas; se reintenta en la proxima corrida
            db.rollback()
            log.exception("vencimiento del pedido %s", oid)
    return n


def back_in_stock(db: Session) -> int:
    """Avisos "Avisarme cuando haya": producto otra vez disponible -> correo a quien lo pidio, con el enlace a la ficha.
    Se marca avisado SOLO cuando el correo quedo "enviado": si fallo, el worker de correo lo reintenta y la proxima
    corrida lo marca (sin encolar otro: el correo va ligado al aviso). Uno "simulado" (sin proveedor de correo) se
    vuelve a mandar cuando el correo ya esta configurado. Sin correo (solo telefono) se marca como siempre: el
    aviso por WhatsApp lo hace el equipo desde el panel."""
    import html

    from ..models import EmailOutbox, StoreStockAlert
    from . import store_orders as so
    from .mail import deliver, mail_configured, queue_email

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
            if not a.email:
                a.notified_at = _now()
                n += 1
                continue
            p = db.get(Product, a.product_id)
            m = db.scalar(
                select(EmailOutbox)
                .where(EmailOutbox.tenant_id == tid, EmailOutbox.entity == "stock_alert", EmailOutbox.entity_id == a.id)
                .order_by(EmailOutbox.id.desc())
                .limit(1)
            )
            if m is None:
                url = so.product_url(t, p)
                m = queue_email(
                    db,
                    t,
                    a.email,
                    f"Ya hay {p.name} · {t.name}",
                    f"<div style='font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15131a'>"
                    f"<p>¡Buenas noticias! <b>{html.escape(p.name)}</b> ya está {html.escape(st['mensaje'].lower())}.</p>"
                    f"<p><a href='{html.escape(url)}' style='display:inline-block;background:#e2233a;color:#fff;padding:12px 18px;border-radius:10px;"
                    f"text-decoration:none;font-weight:700'>Ver el producto</a></p>"
                    f"<p style='color:#8a858f;font-size:12px'>Te escribimos porque pediste que te avisáramos. La existencia puede cambiar: si lo querés, "
                    f"no esperés mucho.</p></div>",
                    "stock_alert",
                    a.id,
                )
            elif m.status == "simulado" and mail_configured():
                deliver(m)  # quedo simulado sin proveedor de correo; ahora si hay
            if m.status == "enviado":
                a.notified_at = _now()
                n += 1
    db.commit()
    return n
