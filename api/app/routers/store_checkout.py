"""Tienda en linea: checkout, pago, confirmacion y seguimiento del pedido (publico) + gestion de pedidos web (portal).

Publico (sin sesion, todo lo que entra se trata como hostil):
- limite por IP y global, honeypot ("website"), campos extra prohibidos;
- el monto lo calcula el servidor (precio, IVA, cupon, envio); si el comprador vio otro total, no se crea;
- clave de idempotencia: doble clic o reintento devuelven el MISMO pedido;
- "Mi pedido" solo con numero + correo (misma respuesta exista o no) o con el enlace firmado del correo;
- el comprobante se guarda privado y solo lo ve el equipo con permiso de ventas.
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import checkout_global_limiter, order_lookup_limiter, order_proof_limiter, public_limiter
from ..models import Media, Order, OrderLine, Tenant
from ..services import cr_geo, store_inventory
from ..services import store_orders as so
from ..services.documents import audit
from ..services.sequences import next_number
from .auth import client_ip
from .store import CheckoutIn, Pricer, _order_out, _quote_cart, _tenant, free_shipping_threshold, rate_zone, store_cfg

router = APIRouter(tags=["tienda"])


class ContactData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=160)
    email: EmailStr | None = Field(None, max_length=200)  # el checkout actual lo exige; antes bastaba un telefono
    phone: str | None = Field(None, max_length=25)
    id_type: str | None = Field(None, pattern="^(fisica|juridica|dimex|nite)$")
    id_number: str | None = Field(None, max_length=30)
    company: str | None = Field(None, max_length=160)
    address: str | None = Field(None, max_length=400)  # campo viejo (texto libre)


def _contact(raw: dict) -> ContactData:
    clean = {k: (None if isinstance(v, str) and not v.strip() else v) for k, v in (raw or {}).items()}
    try:
        return ContactData.model_validate(clean)
    except ValidationError as ex:
        campo = str(ex.errors(include_input=False)[0]["loc"][-1])
        msg = {"name": "Escribí tu nombre", "email": "Escribí un correo válido: ahí te llega la confirmación"}.get(campo, "Revisá tus datos")
        raise HTTPException(422, msg) from None


# ---------- configuracion publica del checkout ----------
@router.get("/public/store/{slug}/checkout-config")
def checkout_config(slug: str, db: Session = Depends(get_db)):
    t = _tenant(db, slug)
    cfg, ck = store_cfg(t), so.checkout_cfg(t)
    pricer = Pricer(db, t)
    from .store import _gross

    rates = [
        {"name": r["name"], "zone": rate_zone(r), "per_kg": bool(r.get("per_kg")), "display_amount": _gross(pricer(r.get("amount") or 0, "CRC"), 13)}
        for r in cfg["shipping_rates"]
        if r.get("active")
    ]
    return {
        "currency": pricer.currency,
        "methods": so.methods_available(db, t),
        "shipping_rates": rates,
        "provinces": cr_geo.PROVINCIAS,
        "outside_gam": {"provinces": sorted(cr_geo.PROVINCIAS_FUERA_GAM), "cantons": sorted(cr_geo.CANTONES_FUERA_GAM)},
        "terms_required": bool((cfg.get("legal") or {}).get("terms")),
        "pickup": ck.get("pickup") or {},
        "free_shipping_from": free_shipping_threshold(t, pricer),
        "auto_invoice": bool(ck.get("auto_invoice", True)),
        "whatsapp": cfg.get("whatsapp") or "",
    }


def _checkout_out(db: Session, t: Tenant, o: Order, pay: dict | None = None, duplicate: bool = False, stock: dict | None = None) -> dict:
    mails = [m for m in so.email_status(db, o) if m["to"] == ((o.contact or {}).get("email") or "")]
    first = mails[0] if mails else None
    return {
        "number": o.number,
        "status": o.status,
        "total": o.total,
        "currency": o.currency,
        "lines": [{"name": ln.name, "quantity": ln.quantity, "total": ln.total} for ln in o.lines],
        "payment_method": o.payment_method,
        "payment_kind": o.payment_kind,
        "shipping_method": o.shipping_method,
        "instructions": so.pay_instructions(t, o),
        "needs_proof": o.payment_kind in so.NEEDS_PROOF and o.status == "nuevo",
        "whatsapp": store_cfg(t)["whatsapp"],
        "track_token": so.track_token(o),
        "track_url": so.track_url(t, o),
        # honestidad: solo se dice "te enviamos un correo" si salio de verdad
        "email": {"to": so._mask_email(first["to"]), "status": first["status"]} if first else None,
        "pay": pay,
        "expires_at": so.aware(o.expires_at),
        "duplicate": duplicate,
        # lo que la reserva dejo dicho: hasta cuando se aparta y que va bajo pedido (la confirmacion lo muestra)
        "stock": stock,
    }


@router.post("/public/store/{slug}/checkout", status_code=201)
def store_checkout(slug: str, data: CheckoutIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    public_limiter.hit(f"checkout:{ip}")
    checkout_global_limiter.hit("checkout:*")
    if data.website:
        return {"ok": True, "number": None}  # honeypot: al bot se le dice que si, y no se crea nada
    t = _tenant(db, slug)
    cfg = store_cfg(t)
    if cfg["kind"] != "tienda":
        raise HTTPException(409, "Esta tienda es solo catalogo")

    if data.idempotency_key:
        prev = db.scalar(select(Order).where(Order.tenant_id == t.id, Order.idempotency_key == data.idempotency_key))
        if prev:
            response.status_code = 200
            return _checkout_out(db, t, prev, duplicate=True)

    c = _contact(data.contact)
    if not c.email and len(so.digits(c.phone)) < 8:
        raise HTTPException(422, "Indicá un correo para enviarte la confirmación")
    id_type, id_number = so.check_id(c.id_type, c.id_number)
    if id_type == "juridica" and id_number and not c.company:
        raise HTTPException(422, "Para factura a nombre de una empresa, escribí la razón social")
    # el checkout actual siempre manda accept_terms; un cliente viejo (pestaña abierta antes de publicar esta
    # version, integraciones) no lo conoce: su pedido entra igual y queda sin fecha de aceptacion
    if (cfg.get("legal") or {}).get("terms") and "accept_terms" in data.model_fields_set and not data.accept_terms:
        raise HTTPException(422, "Para continuar aceptá los términos y condiciones")
    kind, label = so.normalize_method(db, t, data.payment_method)
    delivery = data.delivery
    if delivery and delivery.type == "envio" and not (c.phone and len(so.digits(c.phone)) >= 8):
        raise HTTPException(422, "Para el envío necesitamos un teléfono de contacto")

    items, calc, coupon, ship, shipping, discount_total = _quote_cart(db, t, data)
    # existencias: las decide hold_for_order al reservar (rechaza solo si Ajustes bloquea ventas sin existencia)
    if data.expected_total is not None and abs(Decimal(data.expected_total) - calc.total) > Decimal("0.01"):
        raise HTTPException(409, "El total cambió mientras comprabas. Revisá el resumen y confirmá de nuevo.")

    contact = {
        "name": c.name,
        "email": str(c.email).lower() if c.email else None,
        "phone": c.phone,
        "id_type": id_type if id_number else None,
        "id_number": id_number,
        "company": c.company,
        "address": c.address,
    }
    deliv = None
    if delivery:
        deliv = delivery.model_dump()
        deliv["zone"] = rate_zone(ship) if ship else None
        if delivery.type == "envio":
            contact["address"] = ", ".join(x for x in (delivery.address, delivery.district, delivery.canton, delivery.province) if x)
    customer = so.link_customer(db, t, contact, deliv)
    now = datetime.now(UTC)
    number, _ = next_number(db, t.id, "ORD")
    o = Order(
        tenant_id=t.id,
        number=number,
        channel="tienda",
        customer_id=customer.id,
        contact={k: v for k, v in contact.items() if v},
        currency=Pricer(db, t).currency,
        subtotal=calc.subtotal + discount_total - shipping,
        discount_total=discount_total,
        shipping=shipping,
        tax_total=calc.tax_total,
        total=calc.total,
        coupon_code=coupon.code if coupon else None,
        shipping_method=ship["name"] if ship else None,
        payment_method=label,
        payment_kind=kind,
        delivery=deliv,
        notes=data.notes,
        idempotency_key=data.idempotency_key,
        terms_accepted_at=now if data.accept_terms else None,
        history=[{"status": "nuevo", "at": now.isoformat(), "by": "cliente"}],
    )
    for i, lo in zip(items, calc.lines, strict=True):
        o.lines.append(
            OrderLine(
                product_id=i["product"].id if i["product"] else None,
                name=i["name"],
                quantity=i["quantity"],
                unit_price=i["unit_price"],
                discount_type=i["discount_type"],
                discount_value=i["discount_value"],
                tax_rate=i["tax_rate"],
                subtotal=lo.subtotal,
                tax_amount=lo.tax_amount,
                total=lo.total,
            )
        )
    if coupon:
        coupon.uses += 1
    db.add(o)
    try:
        db.flush()
    except IntegrityError:  # dos envios simultaneos con la misma clave: gana el primero
        db.rollback()
        prev = db.scalar(select(Order).where(Order.tenant_id == t.id, Order.idempotency_key == data.idempotency_key))
        if not prev:
            raise
        response.status_code = 200
        return _checkout_out(db, t, prev, duplicate=True)
    o.inventory_state = "held"
    # reserva atomica: hace commit (o rollback de TODO el pedido y 409 si ya no alcanza); el vencimiento lo fija ella
    hold = store_inventory.hold_for_order(db, o)
    if hold.get("vence") and kind not in (None, "efectivo"):
        o.expires_at = hold["vence"]
    audit(db, t.id, None, "create", "order", o.id, {"channel": "tienda", "total": str(o.total), "metodo": kind, "cliente": customer.id}, ip=ip)
    pay = None
    if kind == "tarjeta":
        try:
            pay = so.card_intent(db, t, o)
        except HTTPException as ex:  # el pedido queda; puede reintentar el pago desde "Mi pedido"
            pay = {"error": ex.detail}
    so.notify_customer(db, t, o, "recibido")
    so.notify_team(db, t, o, "nuevo")
    db.commit()
    stock = {
        "reserva_vence": hold.get("vence"),
        "bajo_pedido": [{"name": b["name"], "quantity": b["quantity"], "plazo": b["plazo"]} for b in hold.get("bajo_pedido", [])],
    }
    return _checkout_out(db, t, o, pay=pay, stock=stock)


# ---------- Mi pedido ----------
class LookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    number: str = Field(min_length=3, max_length=40)
    email: EmailStr
    website: str | None = None


@router.post("/public/store/{slug}/orders/lookup")
def order_lookup(slug: str, data: LookupIn, request: Request, db: Session = Depends(get_db)):
    order_lookup_limiter.hit(f"lookup:{client_ip(request)}")
    if data.website:
        raise HTTPException(404, "No encontramos un pedido con ese número y correo")
    t = _tenant(db, slug)
    o = db.scalar(select(Order).where(Order.tenant_id == t.id, Order.channel == "tienda", func.upper(Order.number) == data.number.upper()))
    if not o or ((o.contact or {}).get("email") or "").lower() != str(data.email).lower():
        raise HTTPException(404, "No encontramos un pedido con ese número y correo")  # misma respuesta: no se adivina
    return {"token": so.track_token(o)}


def _public_order(db: Session, slug: str, token: str) -> tuple[Tenant, Order]:
    t = _tenant(db, slug)
    o = so.read_track_token(db, t, token)
    return t, o


@router.get("/public/store/{slug}/orders/{token}")
def order_status(slug: str, token: str, response: Response, db: Session = Depends(get_db)):
    t, o = _public_order(db, slug, token)
    response.headers["Cache-Control"] = "no-store"
    return so.order_public(db, t, o)


@router.post("/public/store/{slug}/orders/{token}/proof", status_code=201)
async def order_proof(slug: str, token: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    order_proof_limiter.hit(f"proof:{client_ip(request)}")
    t, o = _public_order(db, slug, token)
    if o.payment_kind not in so.NEEDS_PROOF or o.status not in ("nuevo", "en_revision"):
        raise HTTPException(409, "Este pedido no espera un comprobante")
    data = await file.read(so.MAX_PROOF + 1)
    m = so.save_proof(db, t, o, data, file.filename)
    audit(db, t.id, None, "proof", "order", o.id, {"media_id": m.id, "size": m.size}, ip=client_ip(request))
    if o.status == "nuevo":
        so.transition(db, t, o, "en_revision", by="cliente", note="Comprobante subido", notify=False)
    so.notify_team(db, t, o, "comprobante")
    db.commit()
    return so.order_public(db, t, o)


@router.post("/public/store/{slug}/orders/{token}/card")
def order_card(slug: str, token: str, request: Request, db: Session = Depends(get_db)):
    public_limiter.hit(f"card:{client_ip(request)}")
    t, o = _public_order(db, slug, token)
    out = so.card_intent(db, t, o)
    db.commit()
    return out


@router.post("/public/store/{slug}/orders/{token}/simulate-payment")
def order_simulate(slug: str, token: str, request: Request, db: Session = Depends(get_db)):
    """Pago de PRUEBA sin ONVO. No existe en produccion ni cuando hay credenciales reales."""
    public_limiter.hit(f"sim:{client_ip(request)}")
    t, o = _public_order(db, slug, token)
    if not so.simulation_allowed() or so.onvo_secret(db, t):
        raise HTTPException(404, "No disponible")
    if o.payment_kind != "tarjeta" or o.status != "nuevo":
        raise HTTPException(409, "Este pedido no tiene un pago con tarjeta pendiente")
    so.mark_paid(db, t, o, by="simulado", ref=o.payment_ref, note="Pago de prueba (simulado, sin ONVO)")
    db.commit()
    return so.order_public(db, t, o)


# ---------- Portal: configuracion de pagos de la tienda (Mi Tienda -> Pagos y entrega) ----------
class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SinpeCfg(_Cfg):
    active: bool = True
    phone: str | None = Field(None, max_length=25, pattern=r"^[0-9+()\s.-]*$")
    holder: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=500)


class TransferCfg(_Cfg):
    active: bool = True
    bank: str | None = Field(None, max_length=80)
    iban: str | None = Field(None, max_length=40, pattern=r"^[A-Za-z0-9\s-]*$")
    holder: str | None = Field(None, max_length=120)
    holder_id: str | None = Field(None, max_length=30)
    note: str | None = Field(None, max_length=500)


class CardCfg(_Cfg):
    active: bool = False
    # cuotas (tasa cero): se le pide a ONVO como preferencia del intento; depende del contrato con ONVO
    installments: bool = False
    max_installments: int = Field(0, ge=0, le=36)


class CashCfg(_Cfg):
    active: bool = False
    note: str | None = Field(None, max_length=500)


class MethodsCfg(_Cfg):
    sinpe: SinpeCfg = SinpeCfg()
    transferencia: TransferCfg = TransferCfg()
    tarjeta: CardCfg = CardCfg()
    efectivo: CashCfg = CashCfg()


class PickupCfg(_Cfg):
    address: str | None = Field(None, max_length=300)
    hours: str | None = Field(None, max_length=120)


class CheckoutCfgIn(_Cfg):
    methods: MethodsCfg
    free_shipping_from: Decimal = Field(Decimal(0), ge=0)
    auto_invoice: bool = True
    pickup: PickupCfg = PickupCfg()
    notify_roles: list[str] = Field(default_factory=lambda: ["admin", "ventas"], max_length=8)


def _cfg_out(db: Session, t: Tenant) -> dict:
    return {
        "checkout": so.checkout_cfg(t),
        "onvo": {"configured": bool(so.onvo_secret(db, t)), "simulation": so.simulation_allowed()},
    }


@router.get("/store/checkout")
def get_checkout_cfg(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return _cfg_out(db, p.tenant)


@router.put("/store/checkout")
def put_checkout_cfg(data: CheckoutCfgIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    from ..core.deps import ROLE_PERMISSIONS

    if any(r not in ROLE_PERMISSIONS for r in data.notify_roles):
        raise HTTPException(422, "Rol desconocido en avisos")
    if data.methods.sinpe.phone and len(so.digits(data.methods.sinpe.phone)) < 8:
        raise HTTPException(422, "El número de SINPE Móvil debe tener 8 dígitos")
    t = db.get(Tenant, p.tenant.id)
    st = {**(t.settings or {})}
    store = {**(st.get("store") or {})}
    store["checkout"] = data.model_dump(mode="json")
    st["store"] = store
    t.settings = st
    audit(db, t.id, p.user.id, "update", "store_checkout", t.id, {"methods": [k for k, v in data.methods.model_dump().items() if v.get("active")]}, ip=p.ip)
    db.commit()
    return _cfg_out(db, t)


# ---------- Portal: pedidos web ----------
def _staff_out(db: Session, o: Order) -> dict:
    out = _order_out(o)
    out["emails"] = so.email_status(db, o)
    out["needs_proof"] = o.payment_kind in so.NEEDS_PROOF
    out["next"] = so.next_states(o)
    out["track_url"] = so.track_url(db.get(Tenant, o.tenant_id), o) if o.channel == "tienda" else None
    out["customer_name"] = None
    if o.customer_id:
        from ..models import Customer

        c = db.get(Customer, o.customer_id)
        out["customer_name"] = c.name if c else None
    return out


def _filters(tid: int, status: str | None, method: str | None, date_from: date | None, date_to: date | None, q: str | None, pendiente: str | None) -> list:
    w = [Order.tenant_id == tid, Order.channel == "tienda"]
    if pendiente == "por_atender":
        w = so.attention_where(tid)
    elif pendiente:
        raise HTTPException(422, "Filtro de pendientes desconocido")
    if status:
        w.append(Order.status == status)
    if method:
        w.append(Order.payment_kind == method)
    if date_from:
        w.append(Order.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        w.append(Order.created_at <= datetime.combine(date_to, time.max))
    if q and q.strip():
        like = f"%{q.strip()}%"
        w.append(or_(Order.number.ilike(like), func.lower(cast(Order.contact, String)).like(like.lower())))
    return w


@router.get("/store-orders")
def list_orders(
    status: str | None = None,
    method: str | None = Query(None, pattern="^(sinpe|transferencia|tarjeta|efectivo)$"),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = Query(None, max_length=80),
    pendiente: str | None = None,
    limit: int = Query(100, ge=1, le=300),
    p: Principal = Depends(require("sales", "ver")),
    db: Session = Depends(get_db),
):
    w = _filters(p.tenant.id, status, method, date_from, date_to, q, pendiente)
    rows = db.scalars(select(Order).where(*w).order_by(Order.id.desc()).limit(limit)).all()
    counts = dict(db.execute(select(Order.status, func.count()).where(Order.tenant_id == p.tenant.id, Order.channel == "tienda").group_by(Order.status)).all())
    attention = db.scalar(select(func.count()).select_from(Order).where(*so.attention_where(p.tenant.id))) or 0
    return {"items": [_order_out(o) for o in rows], "counts": counts, "por_atender": attention, "labels": so.STATUS_LABEL}


def _get(db: Session, p: Principal, oid: int) -> Order:
    o = db.get(Order, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Pedido no encontrado")
    return o


@router.get("/store-orders/{oid}")
def get_order(oid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    o = _get(db, p, oid)
    out = _staff_out(db, o)
    return out


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str = Field(pattern="^(nuevo|en_revision|pagado|preparando|listo|enviado|entregado|cancelado|reembolsado)$")
    note: str | None = Field(None, max_length=500)
    tracking_number: str | None = Field(None, max_length=80)
    carrier: str | None = Field(None, max_length=80)
    refund_amount: Decimal | None = Field(None, gt=0)
    payment_ref: str | None = Field(None, max_length=120)
    notify: bool = True


@router.post("/store-orders/{oid}/status")
def set_status(oid: int, data: StatusIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    o = _get(db, p, oid)
    if o.channel != "tienda":
        raise HTTPException(409, "Solo pedidos de la tienda en línea")
    if data.payment_ref and data.status == "pagado":
        o.payment_ref = data.payment_ref
    mail = so.transition(
        db,
        p.tenant,
        o,
        data.status,
        by=p.user.id,
        note=data.note,
        ip=p.ip,
        tracking_number=data.tracking_number,
        carrier=data.carrier,
        refund_amount=data.refund_amount,
        notify=data.notify,
    )
    db.commit()
    out = _staff_out(db, o)
    out["email_result"] = {"to": mail.to, "status": mail.status, "error": mail.error} if mail else None
    return out


@router.post("/store-orders/{oid}/resend")
def resend(oid: int, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    """Reenvia al cliente el correo del estado actual (o la confirmacion si sigue pendiente)."""
    o = _get(db, p, oid)
    kind = {"nuevo": "recibido", "en_revision": "recibido"}.get(o.status, o.status)
    mail = so.notify_customer(db, p.tenant, o, kind)
    if not mail:
        raise HTTPException(409, "El pedido no tiene correo del cliente")
    audit(db, p.tenant.id, p.user.id, "resend", "order", o.id, {"status": mail.status}, ip=p.ip)
    db.commit()
    return {"to": mail.to, "status": mail.status, "error": mail.error}


@router.get("/store-orders/{oid}/proofs/{mid}")
def proof_file(oid: int, mid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    o = _get(db, p, oid)
    if not any(x.get("media_id") == mid for x in (o.proofs or [])):
        raise HTTPException(404, "Comprobante no encontrado")
    m = db.get(Media, mid)
    if not m or m.tenant_id != p.tenant.id:
        raise HTTPException(404, "Comprobante no encontrado")
    audit(db, p.tenant.id, p.user.id, "view_proof", "order", o.id, {"media_id": mid}, ip=p.ip)
    db.commit()
    return Response(
        m.data,
        media_type=m.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{m.filename}"',
            "Content-Security-Policy": "default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/store-orders/{oid}/print")
def print_order(oid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    """Hoja de despacho imprimible (HTML): datos de entrega, productos y totales."""
    o = _get(db, p, oid)
    e = html.escape
    c, dv = o.contact or {}, o.delivery or {}
    rows = "".join(
        f"<tr><td>{e(ln.name)}</td><td class=n>{Decimal(str(ln.quantity)):g}</td><td class=n>{so.fmt_money(ln.total, o.currency)}</td></tr>" for ln in o.lines
    )
    entrega = (
        "Retiro en sitio"
        if dv.get("type") == "retiro"
        else ", ".join(x for x in (dv.get("address"), dv.get("district"), dv.get("canton"), dv.get("province")) if x) or (c.get("address") or "—")
    )
    doc = f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Pedido {e(o.number)}</title>
<style>body{{font-family:Arial,sans-serif;max-width:720px;margin:24px auto;color:#15131a;font-size:14px}}h1{{font-size:22px;margin:0}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}td,th{{border-bottom:1px solid #ddd;padding:6px 4px;text-align:left}}.n{{text-align:right}}
.box{{border:1px solid #ccc;border-radius:6px;padding:10px 12px;margin-top:12px}}@media print{{button{{display:none}}}}</style>
<button onclick="print()">Imprimir</button>
<h1>Pedido {e(o.number)}</h1><p>{e(o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else "")} · {e(so.STATUS_LABEL.get(o.status, o.status))}</p>
<div class=box><b>{e(c.get("name") or "")}</b>{" · " + e(c.get("company")) if c.get("company") else ""}<br>{e(c.get("phone") or "")} · {e(c.get("email") or "")}
{"<br>Céd. " + e(c.get("id_number")) if c.get("id_number") else ""}<br><b>Entrega:</b> {e(o.shipping_method or "")} — {e(entrega)}
{"<br><b>Guía:</b> " + e(o.tracking_number) if o.tracking_number else ""}<br><b>Pago:</b> {e(o.payment_method or "")}{" · pagado" if o.paid_at else " · pendiente"}</div>
{"<div class=box><b>Nota del cliente:</b> " + e(o.notes) + "</div>" if o.notes else ""}
<table><tr><th>Producto</th><th class=n>Cant.</th><th class=n>Total</th></tr>{rows}</table>
<p class=n>IVA {so.fmt_money(o.tax_total, o.currency)} · Envío {so.fmt_money(o.shipping, o.currency)}</p><p class=n style="font-size:18px"><b>Total {so.fmt_money(o.total, o.currency)}</b></p></html>"""
    return Response(
        doc,
        media_type="text/html",
        headers={"Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"},
    )


def pending_count(db: Session, p: Principal) -> int:
    """Tarjeta "Pedidos web por atender" del inicio: misma definicion que /store-orders?pendiente=por_atender."""
    return db.scalar(select(func.count()).select_from(Order).where(*so.attention_where(p.tenant.id))) or 0
