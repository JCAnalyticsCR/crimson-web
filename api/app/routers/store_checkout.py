"""Tienda en linea: checkout, pago, confirmacion y seguimiento del pedido (publico) + gestion de pedidos web (portal).

Publico (sin sesion, todo lo que entra se trata como hostil):
- limite por IP y global, honeypot ("website"), campos extra prohibidos;
- el monto lo calcula el servidor (precio, IVA, cupon, envio); si el comprador vio otro total, no se crea;
- clave de idempotencia: doble clic o reintento devuelven el MISMO pedido;
- "Mi pedido" solo con numero + correo (misma respuesta exista o no) o con el enlace firmado del correo;
- el comprobante se guarda privado y solo lo ve el equipo con permiso de ventas.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.clientip import ip_key
from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import (
    checkout_global_limiter,
    email_check_global_limiter,
    email_check_limiter,
    order_card_limiter,
    order_lookup_limiter,
    order_proof_ip_limiter,
    order_proof_limiter,
    public_limiter,
)
from ..models import Media, Order, OrderLine, Tenant
from ..providers.payments import key_mode
from ..services import cr_geo, email_check, onvo, store_inventory
from ..services import inventory as invsvc
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
    from .store import ship_display

    rates = [
        # display_amount null = sin tipo de cambio (tienda en otra moneda): "a consultar"; el checkout no se cae
        {"name": r["name"], "zone": rate_zone(r), "per_kg": bool(r.get("per_kg")), "display_amount": ship_display(pricer, r)}
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
        "verify_email": bool(ck.get("verify_email", True)),
    }


class EmailCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=1, max_length=200)


@router.post("/public/store/{slug}/email-check")
def email_check_public(slug: str, data: EmailCheckIn, request: Request, db: Session = Depends(get_db)):
    """El checkout revisa el correo al salir del paso "Tus datos": formato, errores de dedo y que el dominio tenga MX
    (pedido 21/10, item 32). Si el DNS no responde, ok=True: no se frena una compra por una falla del DNS."""
    email_check_limiter.hit(f"email:{ip_key(client_ip(request))}")
    email_check_global_limiter.hit("email:*")  # muchas IPs con dominios que no responden no acaparan los hilos
    t = _tenant(db, slug)
    r = email_check.check_email(data.email, dns=bool(so.checkout_cfg(t).get("verify_email", True)))
    return {k: r[k] for k in ("ok", "reason", "message", "suggestion")}


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
    public_limiter.hit(f"checkout:{ip_key(ip)}")
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
    if c.email:
        # el mismo chequeo del paso "Tus datos" (cacheado): un dominio que no existe o sin MX no recibe la confirmacion.
        # Los errores de dedo (gmial.com) se corrigen siempre; Mi Tienda solo apaga la consulta del DNS.
        chk = email_check.check_email(str(c.email), dns=bool(so.checkout_cfg(t).get("verify_email", True)))
        if not chk["ok"]:
            raise HTTPException(422, chk["message"])
    id_type, id_number = so.check_id(c.id_type, c.id_number)
    if id_type == "juridica" and id_number and not c.company:
        raise HTTPException(422, "Para factura a nombre de una empresa, escribí la razón social")
    # el checkout actual siempre manda accept_terms; un cliente viejo (pestaña abierta antes de publicar esta
    # version, integraciones) no lo conoce: su pedido entra igual y queda sin fecha de aceptacion
    if (cfg.get("legal") or {}).get("terms") and "accept_terms" in data.model_fields_set and not data.accept_terms:
        raise HTTPException(422, "Para continuar aceptá los términos y condiciones")
    kind, label = so.normalize_method(db, t, data.payment_method)
    delivery = data.delivery
    so.check_delivery(t, kind, delivery.type if delivery else None)  # efectivo: solo con retiro en sitio
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
    if kind == "efectivo":  # contra entrega: el envio sale sin pago previo, el aviso del mensajero va desde ya
        so.notify_delivery(db, t, o)
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
    order_lookup_limiter.hit(f"lookup:{ip_key(client_ip(request))}")
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
    ip = client_ip(request)
    order_proof_ip_limiter.hit(f"proof-ip:{ip_key(ip)}")  # tope por IP: probar muchos enlaces de pedido
    t, o = _public_order(db, slug, token)
    # por pedido y por IP: 10 comprobantes de un pedido no le cierran la puerta a otro pedido desde la misma conexion
    # (una oficina con varias compras) ni el que tiene el enlace de un pedido traba a otra persona
    order_proof_limiter.hit(f"proof:{o.id}:{ip_key(ip)}")
    if o.payment_kind not in so.NEEDS_PROOF or o.status not in ("nuevo", "en_revision"):
        raise HTTPException(409, "Este pedido no espera un comprobante")
    data = await file.read(so.MAX_PROOF + 1)
    m = so.save_proof(db, t, o, data, file.filename)
    audit(db, t.id, None, "proof", "order", o.id, {"media_id": m.id, "size": m.size}, ip=ip)
    if o.status == "nuevo":
        so.transition(db, t, o, "en_revision", by="cliente", note="Comprobante subido", notify=False)
    so.notify_team(db, t, o, "comprobante")
    db.commit()
    return so.order_public(db, t, o)


_PROOF_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
# imagen: documento sandbox sin nada que ejecutar. PDF: sin `sandbox` ni CSP, porque el visor integrado de
# Chrome/Firefox no abre un PDF dentro de un documento sandbox (sale en blanco o "bloqueado"); el archivo ya se
# olfateo como PDF al subirlo (%PDF) y nosniff impide que el navegador lo trate como otra cosa.
_PROOF_CSP_IMG = "default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'; sandbox"


@router.get("/public/store/{slug}/orders/{token}/proofs/{n}")
def order_proof_file(slug: str, token: str, n: int, request: Request, db: Session = Depends(get_db)):
    """Reunion 21/10 (item 34): el cliente vuelve a ver el comprobante que subio. Solo con el enlace firmado de SU
    pedido, solo los archivos colgados de ese pedido (por posicion: nunca un id de multimedia que se pueda probar) y
    solo mientras el pedido siga abierto y dentro de so.PROOF_VIEW_DAYS (el enlace dura 400 dias y se reenvia)."""
    public_limiter.hit(f"proofview:{ip_key(client_ip(request))}")
    t, o = _public_order(db, slug, token)
    proofs = o.proofs or []
    if n < 0 or n >= len(proofs) or not so.proof_viewable(o, proofs[n]):
        raise HTTPException(404, "Comprobante no encontrado")
    m = db.get(Media, proofs[n].get("media_id"))
    if not m or m.tenant_id != t.id:
        raise HTTPException(404, "Comprobante no encontrado")
    raw = proofs[n].get("filename") or "comprobante"
    name = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "._-" else "_" for ch in raw)[:120] or "comprobante"
    headers = {**_PROOF_HEADERS, "Content-Disposition": f'inline; filename="{name}"'}
    if m.content_type != "application/pdf":
        headers["Content-Security-Policy"] = _PROOF_CSP_IMG
    return Response(m.data, media_type=m.content_type, headers=headers)


@router.post("/public/store/{slug}/orders/{token}/card")
def order_card(slug: str, token: str, request: Request, db: Session = Depends(get_db)):
    public_limiter.hit(f"card:{ip_key(client_ip(request))}")
    t, o = _public_order(db, slug, token)
    order_card_limiter.hit(f"card:{t.id}:{o.id}")  # por pedido: cambiar de IP no abre mas sesiones (tope: so.MAX_CARD_SESSIONS)
    out = so.card_intent(db, t, o)
    db.commit()
    return out


@router.post("/public/store/{slug}/orders/{token}/card/sync")
def order_card_sync(slug: str, token: str, request: Request, db: Session = Depends(get_db)):
    """Al volver de ONVO: le pregunta a ONVO por la sesion del pedido y, si esta pagada, la aplica (sin esperar el
    webhook). El regreso del navegador por si solo no confirma nada."""
    public_limiter.hit(f"cardsync:{ip_key(client_ip(request))}")
    t, o = _public_order(db, slug, token)
    onvo.sync_order(db, t, o)
    db.commit()
    return so.order_public(db, t, o)


@router.post("/public/store/{slug}/orders/{token}/simulate-payment")
def order_simulate(slug: str, token: str, request: Request, db: Session = Depends(get_db)):
    """Pago de PRUEBA sin ONVO. No existe en produccion ni cuando hay credenciales reales."""
    public_limiter.hit(f"sim:{ip_key(client_ip(request))}")
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
    pickup_only: bool = True  # efectivo solo con "Retiro en sitio" (contra entrega a domicilio = mercaderia sin cobrar)


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
    notify_roles: list[str] = Field(default_factory=lambda: ["admin", "ventas"], max_length=40)  # tambien los roles creados
    verify_email: bool = True
    delivery_notice: bool = True


def _cfg_out(db: Session, t: Tenant) -> dict:
    from ..core.deps import assignable_roles, custom_roles

    return {
        "checkout": so.checkout_cfg(t),
        # quien recibe el aviso de cada pedido web: los roles internos, tambien los que creo el admin (con su nombre)
        "role_choices": assignable_roles(db, t),
        "role_labels": {r.code: r.name for r in custom_roles(db, t)},
        "onvo": {"configured": bool(key := so.onvo_secret(db, t)), "simulation": so.simulation_allowed(), "mode": key_mode(key)},
    }


@router.get("/store/checkout")
def get_checkout_cfg(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return _cfg_out(db, p.tenant)


@router.put("/store/checkout")
def put_checkout_cfg(data: CheckoutCfgIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    from ..core.deps import assignable_roles

    # los avisos de pedidos web son para el equipo de Crimson: ni clientes ni aliados ni el acceso de soporte (los roles
    # que creo el admin en Ajustes -> Usuarios -> Roles si)
    if any(r not in assignable_roles(db, p.tenant) for r in data.notify_roles):
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
def _invoice_visible(db: Session, o: Order, p: Principal | None) -> bool | None:
    """La factura de un pedido se enlaza solo si es del que mira (services/ownership.py): el vendedor ve todos los
    pedidos de la tienda, pero la factura que hizo otro le sale como numero sin enlace (abrirla seria un 404)."""
    if not o.invoice_id or p is None:
        return None
    from ..services.ownership import visible_invoice_ids

    return o.invoice_id in visible_invoice_ids(db, p, [o.invoice_id])


def _staff_out(db: Session, o: Order, p: Principal | None = None) -> dict:
    out = _order_out(o)
    out["invoice_visible"] = _invoice_visible(db, o, p)
    out["emails"] = so.email_status(db, o)
    out["needs_proof"] = o.payment_kind in so.NEEDS_PROOF
    out["next"] = so.next_states(o)
    out["track_url"] = so.track_url(db.get(Tenant, o.tenant_id), o) if o.channel == "tienda" else None
    out["onvo_refundable"] = onvo.order_refundable(o)
    out["stock_out"] = sum(store_inventory.out_for_order(db, o).values(), Decimal(0))  # >0: cancelar/reembolsar pregunta si volvio
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
    from ..services.ownership import visible_invoice_ids

    mias = visible_invoice_ids(db, p, [o.invoice_id for o in rows])  # una consulta para toda la pagina
    items = [{**_order_out(o), "invoice_visible": (o.invoice_id in mias) if o.invoice_id else None} for o in rows]
    # misma revision: los comprobantes que los clientes suben desde la pagina de pago de una factura (/pagar)
    from ..services.invoice_proofs import pending_count

    facturas = pending_count(db, p.tenant.id, p) if p.can("payments", "ver") else None  # solo las facturas que ve
    return {"items": items, "counts": counts, "por_atender": attention, "labels": so.STATUS_LABEL, "comprobantes_facturas": facturas}


def _get(db: Session, p: Principal, oid: int) -> Order:
    o = db.get(Order, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Pedido no encontrado")
    return o


@router.get("/store-orders/{oid}")
def get_order(oid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    o = _get(db, p, oid)
    out = _staff_out(db, o, p)
    return out


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str = Field(pattern="^(nuevo|en_revision|pagado|preparando|listo|enviado|entregado|cancelado|reembolsado)$")
    note: str | None = Field(None, max_length=500)  # interna (solo el equipo)
    client_note: str | None = Field(None, max_length=500)  # la ve el cliente en su seguimiento
    tracking_number: str | None = Field(None, max_length=80)
    carrier: str | None = Field(None, max_length=80)
    refund_amount: Decimal | None = Field(None, gt=0)
    payment_ref: str | None = Field(None, max_length=120)
    notify: bool = True
    refund_via_onvo: bool = False  # devuelve el dinero a la tarjeta por la API de ONVO (requiere sales.anular)
    # cancelar o reembolsar un pedido que YA saco mercaderia: ¿volvio el producto? (obligatorio en ese caso)
    restock: bool | None = None
    restock_warehouse_id: int | None = None


@router.post("/store-orders/{oid}/status")
def set_status(oid: int, data: StatusIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    o = _get(db, p, oid)
    if o.channel != "tienda":
        raise HTTPException(409, "Solo pedidos de la tienda en línea")
    if data.payment_ref and data.status == "pagado":
        o.payment_ref = data.payment_ref
    note = data.note
    # el motivo de cancelar y lo que fallo en un comprobante rechazado el portal los rotula "lo ve el cliente": aqui
    # (y solo aqui) ese texto escrito por la persona pasa a ser la nota publica. transition() nunca lo promueve.
    client_note = data.client_note
    if not (client_note or "").strip() and (data.note or "").strip() and (data.status == "cancelado" or (data.status == "nuevo" and o.status == "en_revision")):
        client_note = data.note
    afuera = store_inventory.out_for_order(db, o) if data.status in ("cancelado", "reembolsado") else {}
    if afuera and data.restock is None:
        raise HTTPException(422, "Este pedido ya sacó mercadería de bodega: indicá si el producto volvió (restock) y a qué bodega")
    wh = None
    if afuera and data.restock:
        wh = data.restock_warehouse_id or (w.id if (w := invsvc.default_warehouse(db, p.tenant.id)) else None)
        if not wh:
            raise HTTPException(422, "No hay bodega configurada para recibir la devolución")
    if data.refund_via_onvo:
        if data.status != "reembolsado":
            raise HTTPException(422, "refund_via_onvo solo aplica al reembolsar")
        if not p.can("sales", "anular"):
            raise HTTPException(403, "Devolver dinero por ONVO requiere permiso para anular ventas")
        if "reembolsado" not in so.TRANSITIONS.get(o.status, set()) or (o.status == "cancelado" and not o.paid_at):
            raise HTTPException(409, "Este pedido no se puede reembolsar en su estado actual")
        if data.refund_amount is not None and data.refund_amount > Decimal(str(o.total)):
            raise HTTPException(422, "Monto de reembolso inválido")
        r = onvo.refund_order(db, p.tenant, o, data.refund_amount)
        ref = f"Reembolso ONVO {r.get('id')} ({r.get('status')}) por {data.refund_amount or o.total}"
        info = {"refund": r.get("id"), "status": r.get("status"), "monto": str(data.refund_amount or o.total)}
        # el dinero ya salio: queda anotado y guardado aunque el cambio de estado de abajo falle (y no se repite)
        so._note(o, p.user.id, ref)
        audit(db, p.tenant.id, p.user.id, "onvo_refund", "order", o.id, info, ip=p.ip)
        db.commit()
        note = f"{note.strip() + ' · ' if (note or '').strip() else ''}{ref}"
    mail = so.transition(
        db,
        p.tenant,
        o,
        data.status,
        by=p.user.id,
        note=note,
        ip=p.ip,
        tracking_number=data.tracking_number,
        carrier=data.carrier,
        refund_amount=data.refund_amount,
        notify=data.notify,
        client_note=client_note,
    )
    if afuera:
        if data.restock:
            store_inventory.return_for_order(db, o, wh, p.user.id)
        else:
            unidades = sum(afuera.values())
            so._note(
                o,
                p.user.id,
                f"Producto no devuelto: {unidades.normalize():f} unidad(es) quedan fuera de bodega (decidido al pasar a «{so.STATUS_LABEL.get(o.status, o.status)}»)",
            )
    db.commit()
    out = _staff_out(db, o, p)
    out["email_result"] = {"to": mail.to, "status": mail.status, "error": mail.error} if mail else None
    return out


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str = Field(min_length=1, max_length=500)
    client: bool = False  # True = la ve el cliente en su pagina de seguimiento; False = interna


@router.post("/store-orders/{oid}/note")
def add_note(oid: int, data: NoteIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    """Nota sin cambiar el estado: interna (solo el equipo) o para el cliente (sale en su historial)."""
    o = _get(db, p, oid)
    if o.channel != "tienda":
        raise HTTPException(409, "Solo pedidos de la tienda en línea")
    if data.client:
        so.add_client_note(o, p.user.id, data.note)
    else:
        so._note(o, p.user.id, data.note)
    audit(db, p.tenant.id, p.user.id, "note", "order", o.id, {"client": data.client}, ip=p.ip)
    db.commit()
    return _staff_out(db, o, p)


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
    """Version impresa del pedido (HTML): logo y datos de la empresa, cliente, entrega, pago, lineas, totales y estado."""
    from ..services import order_print

    o = _get(db, p, oid)
    doc = order_print.render(db, db.get(Tenant, o.tenant_id), o)
    return Response(
        doc,
        media_type="text/html",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
        },
    )


def pending_count(db: Session, p: Principal) -> int:
    """Tarjeta "Pedidos web por atender" del inicio: misma definicion que el contador "Por atender" de Pedidos de la
    tienda (/store-orders): los pedidos por atender mas los comprobantes de facturas (/pagar) que el que mira puede
    revisar (payments.ver, solo los de sus facturas si no ve todas)."""
    from ..services.invoice_proofs import pending_count as proofs_pending

    n = db.scalar(select(func.count()).select_from(Order).where(*so.attention_where(p.tenant.id))) or 0
    return n + (proofs_pending(db, p.tenant.id, p) if p.can("payments", "ver") else 0)
