"""Pedidos de la tienda en linea: metodos de pago, estados, comprobantes, seguimiento y avisos.

Reglas:
- El total lo calcula SIEMPRE el servidor (routers/store._quote_cart); el navegador no manda precios.
- Cada cambio de estado pasa por transition(): valida el salto, deja historial + auditoria, mueve la reserva de
  inventario (store_inventory) y avisa al cliente. Un correo que no salio se reporta como tal (simulado / error).
- Un comprobante de pago es PRIVADO (visibility='private', module='sales'): solo con enlace firmado o por endpoint con permiso.
- El pago con tarjeta lo confirma el webhook de ONVO, nunca el regreso del navegador. Sin credenciales hay un modo
  de prueba (simulado) que solo existe fuera de produccion.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt
from fastapi import HTTPException
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import read_purpose_token, sign_purpose_token
from ..models import Customer, EmailOutbox, Media, Order, Tenant
from . import store_inventory
from .documents import audit
from .totals import d

log = logging.getLogger("crimson.onvo")

STATUS_LABEL = {
    "nuevo": "Pendiente de pago",
    "en_revision": "Comprobante en revisión",
    "pagado": "Pagado",
    "preparando": "Preparando",
    "listo": "Listo para retiro",
    "enviado": "Enviado",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
    "reembolsado": "Reembolsado",
}
FULFILL = ("preparando", "listo", "enviado", "entregado")
# saltos permitidos para pedidos de la tienda (los de eventos/POS conservan su flujo de siempre)
TRANSITIONS: dict[str, set[str]] = {
    "nuevo": {"en_revision", "pagado", "cancelado", *FULFILL},  # avanzar sin pago: solo efectivo contra entrega
    "en_revision": {"pagado", "nuevo", "cancelado"},  # nuevo = comprobante rechazado
    "pagado": {"preparando", "listo", "enviado", "entregado", "cancelado", "reembolsado"},
    "preparando": {"listo", "enviado", "entregado", "cancelado", "reembolsado"},
    "listo": {"enviado", "entregado", "cancelado", "reembolsado"},
    "enviado": {"entregado", "reembolsado"},
    "entregado": {"reembolsado"},
    "cancelado": {"reembolsado"},
    "reembolsado": set(),
}
LEGACY_STATES = ("nuevo", "pagado", "preparando", "enviado", "entregado", "cancelado")
PAID_STATES = ("pagado", "preparando", "listo", "enviado", "entregado")

METHOD_LABEL = {"sinpe": "SINPE Móvil", "transferencia": "Transferencia bancaria", "tarjeta": "Tarjeta (ONVO)", "efectivo": "Efectivo"}
NEEDS_PROOF = ("sinpe", "transferencia")
ATTENTION = ("en_revision", "pagado", "preparando")  # "pedidos por atender" del inicio
# el vendedor "toma" solo pedidos por atender y sin factura (21/10, items 24/42): reclamar ventas ya facturadas o
# historicas (con su comision retroactiva) es una reasignacion, y esa es del admin (sales.asignar)
TAKEABLE = ("nuevo", "en_revision", "pagado", "preparando")

CHECKOUT_DEFAULTS: dict = {
    "methods": {
        "sinpe": {"active": True, "phone": "", "holder": "", "note": ""},
        "transferencia": {"active": True, "bank": "", "iban": "", "holder": "", "holder_id": "", "note": ""},
        "tarjeta": {"active": False, "installments": False, "max_installments": 0},
        # efectivo solo con retiro en sitio (por defecto; Mi Tienda lo puede abrir al envio): contra entrega a domicilio se
        # despacha mercaderia que no se devuelve sin haberla cobrado
        "efectivo": {"active": False, "note": "Pago en efectivo al retirar el pedido.", "pickup_only": True},
    },
    "free_shipping_from": 0,  # envio gratis desde este monto (en colones, con IVA); 0 = nunca
    "auto_invoice": True,  # comprobante electronico automatico al confirmarse el pago
    "pickup": {"address": "", "hours": ""},
    "notify_roles": ["admin", "ventas"],
    # 21/10, items 24/42: la factura de un pedido web asignado a un vendedor comisiona como una venta propia
    "web_orders_commission": True,
    # 21/10, item 45: transportistas que se eligen al marcar "Enviado" (ademas se puede escribir otro)
    "carriers": ["Mensajería Crimson", "Correos de Costa Rica"],
}


def aware(dt: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona aunque se guarden en UTC: sin esto el navegador las lee como hora local."""
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _legacy_names(t: Tenant) -> dict[str, dict]:
    """Metodos manuales de Ajustes (lo que usaba la tienda antes): nombre -> {instructions, active}."""
    from ..routers.settings import DEFAULTS

    out = {}
    for m in (t.settings or {}).get("manual_payment_methods") or DEFAULTS["manual_payment_methods"]:
        code = legacy_code(m.get("name") or "")
        if code and code not in out:
            out[code] = m
    return out


def legacy_code(name: str) -> str | None:
    n = (name or "").lower()
    if "sinpe" in n:
        return "sinpe"
    if "transfer" in n or "iban" in n or "deposito" in n or "depósito" in n:
        return "transferencia"
    if "efectivo" in n or "contra entrega" in n:
        return "efectivo"
    if "tarjeta" in n or "onvo" in n:
        return "tarjeta"
    return None


def checkout_cfg(t: Tenant) -> dict:
    """Configuracion del checkout (Mi Tienda -> Pagos). Sin configurar, hereda los metodos manuales de Ajustes."""
    raw = ((t.settings or {}).get("store") or {}).get("checkout") or {}
    methods = {k: {**v} for k, v in CHECKOUT_DEFAULTS["methods"].items()}
    if not raw.get("methods"):
        legacy = _legacy_names(t)
        for code in ("sinpe", "transferencia", "efectivo"):
            m = legacy.get(code)
            methods[code]["active"] = bool(m and m.get("active", True))
            if m and m.get("instructions"):
                methods[code]["note"] = m["instructions"]
    else:
        for k, v in raw["methods"].items():
            if k in methods and isinstance(v, dict):
                methods[k].update(v)
    return {
        **CHECKOUT_DEFAULTS,
        **{k: v for k, v in raw.items() if k != "methods"},
        "methods": methods,
    }


def onvo_secret(db: Session, t: Tenant) -> str | None:
    """Llave ONVO para cobrar (Ajustes -> Pasarelas o variable de entorno). Reglas en services/onvo.secret_key."""
    from . import onvo

    return onvo.secret_key(db, t.id)


def simulation_allowed() -> bool:
    """El pago de prueba (sin ONVO) no existe en produccion: ahi cualquiera podria marcar su pedido pagado."""
    return not settings.is_prod


def methods_available(db: Session, t: Tenant) -> list[dict]:
    cfg = checkout_cfg(t)
    out = []
    for code in ("sinpe", "transferencia", "tarjeta", "efectivo"):
        m = cfg["methods"][code]
        if not m.get("active"):
            continue
        simulated = False
        if code == "tarjeta":
            if not onvo_secret(db, t):
                if not simulation_allowed():
                    continue  # sin credenciales en produccion: la tarjeta no se ofrece
                simulated = True
        out.append(
            {
                "code": code,
                "label": METHOD_LABEL[code],
                "needs_proof": code in NEEDS_PROOF,
                "simulated": simulated,
                "hint": method_hint(t, code),
                "pickup_only": pickup_only(m) if code == "efectivo" else False,
            }
        )
    return out


def pickup_only(method_cfg: dict) -> bool:
    """El efectivo se ofrece solo con "Retiro en sitio" salvo que Mi Tienda lo abra tambien al envio."""
    return bool(method_cfg.get("pickup_only", True))


def check_delivery(t: Tenant, code: str | None, delivery_type: str | None) -> None:
    """El metodo de pago segun el tipo de entrega: efectivo solo al retirar (si asi esta configurado)."""
    if code == "efectivo" and pickup_only(checkout_cfg(t)["methods"]["efectivo"]) and delivery_type != "retiro":
        raise HTTPException(422, "El pago en efectivo es solo para retiro en sitio. Elegí otro método de pago o retirá tu pedido.")


def method_hint(t: Tenant, code: str) -> str:
    m = checkout_cfg(t)["methods"][code]
    if code == "sinpe":
        return "Pagás desde la app de tu banco y subís el comprobante."
    if code == "transferencia":
        return "Transferencia a nuestra cuenta IBAN y subís el comprobante."
    if code == "tarjeta":
        return "Visa o Mastercard en la página segura de ONVO."
    return m.get("note") or "Pagás al recibir o al retirar."


def normalize_method(db: Session, t: Tenant, raw: str | None) -> tuple[str | None, str | None]:
    """Acepta el codigo (sinpe) o el nombre viejo (SINPE Movil). Devuelve (codigo, etiqueta).
    Sin metodo (integraciones viejas) el pedido queda "a coordinar": sin plazo de pago."""
    if not raw:
        return None, None
    avail = {m["code"]: m for m in methods_available(db, t)}
    code = raw if raw in METHOD_LABEL else legacy_code(raw)
    if not code or code not in avail:
        raise HTTPException(422, "Ese método de pago no está disponible")
    legacy = _legacy_names(t).get(code)
    label = (legacy or {}).get("name") if raw not in METHOD_LABEL and legacy else METHOD_LABEL[code]
    return code, label or METHOD_LABEL[code]


def fmt_money(v, cur: str) -> str:
    sym = "₡" if cur == "CRC" else "$"
    n = d(v)
    return f"{sym}{n:,.0f}".replace(",", " ") if cur == "CRC" else f"{sym}{n:,.2f}"  # CR: 542 965


def pay_instructions(t: Tenant, o: Order) -> str | None:
    """Texto para pagar el pedido (pantalla de confirmacion, seguimiento y correo)."""
    code = o.payment_kind
    if not code:  # pedidos de antes: instrucciones del metodo manual por nombre
        from ..routers.store import manual_methods

        return next((m.get("instructions") for m in manual_methods(t) if m.get("name") == o.payment_method), None)
    m = checkout_cfg(t)["methods"].get(code) or {}
    total = fmt_money(o.total, o.currency)
    if code == "sinpe":
        if m.get("phone"):
            return (
                f"Enviá {total} por SINPE Móvil al {m['phone']}" + (f" a nombre de {m['holder']}" if m.get("holder") else "") + ".\n"
                f"En el detalle escribí el número de pedido {o.number}.\nLuego subí el comprobante aquí o envialo por WhatsApp."
                + (f"\n{m['note']}" if m.get("note") else "")
            )
        return (m.get("note") or "Te enviamos el número de SINPE Móvil para pagar.") + f"\nMonto: {total} · Detalle: {o.number}"
    if code == "transferencia":
        if m.get("iban"):
            parts = [f"Transferí {total} a la cuenta IBAN {m['iban']}"]
            if m.get("bank"):
                parts.append(f"Banco: {m['bank']}")
            if m.get("holder"):
                parts.append(f"A nombre de: {m['holder']}" + (f" (céd. {m['holder_id']})" if m.get("holder_id") else ""))
            parts.append(f"Detalle: {o.number}. Luego subí el comprobante.")
            if m.get("note"):
                parts.append(m["note"])
            return "\n".join(parts)
        return (m.get("note") or "Te enviamos los datos de la cuenta para transferir.") + f"\nMonto: {total} · Detalle: {o.number}"
    if code == "efectivo":
        return (m.get("note") or "Pagás en efectivo al recibir o retirar el pedido.") + f"\nMonto: {total}"
    if code == "tarjeta":
        return "Pagá con tarjeta en la página segura de ONVO. Te confirmamos apenas el banco apruebe el pago."
    return None


# ---------- cliente ----------
def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


ID_LEN = {"fisica": (9, 9), "juridica": (10, 10), "dimex": (11, 12), "nite": (10, 10)}


def check_id(id_type: str | None, id_number: str | None) -> tuple[str, str | None]:
    """Cedula opcional; si viene, con el largo correcto para su tipo (fisica 9, juridica 10, DIMEX 11-12)."""
    t = id_type or "fisica"
    if t not in ID_LEN:
        raise HTTPException(422, "Tipo de identificación inválido")
    n = digits(id_number)
    if not n:
        return t, None
    lo, hi = ID_LEN[t]
    if not lo <= len(n) <= hi:
        raise HTTPException(
            422, "La cédula no tiene el largo correcto" + (" (física: 9 dígitos)" if t == "fisica" else " (jurídica: 10 dígitos)" if t == "juridica" else "")
        )
    return t, n


def link_customer(db: Session, t: Tenant, contact: dict, delivery: dict | None) -> Customer:
    """Liga el pedido a un cliente existente (cedula, luego correo) o lo crea. Nunca modifica una ficha existente
    desde la web: lo que el comprador escribe queda en el pedido, la ficha la cuida el equipo."""
    idn = contact.get("id_number")
    email = (contact.get("email") or "").strip().lower()
    c = None
    if idn:
        norm = func.replace(func.replace(Customer.id_number, "-", ""), " ", "")
        c = db.scalar(select(Customer).where(Customer.tenant_id == t.id, norm == idn).order_by(Customer.id))
    if not c and email:
        c = db.scalar(select(Customer).where(Customer.tenant_id == t.id, func.lower(Customer.email) == email).order_by(Customer.id))
    if c:
        return c
    name = (contact.get("company") if contact.get("id_type") == "juridica" and contact.get("company") else contact.get("name")) or "Cliente tienda"
    addr = None
    if delivery and delivery.get("province"):
        addr = {
            "pais": "CR",
            "provincia": delivery.get("province"),
            "canton": delivery.get("canton"),
            "distrito": delivery.get("district"),
            "senas": delivery.get("address"),
        }
    c = Customer(
        tenant_id=t.id,
        id_type=contact.get("id_type") or "fisica",
        id_number=idn,
        name=name[:200],
        email=email or None,
        phone=contact.get("phone"),
        whatsapp=contact.get("phone"),
        address=addr,
        notes="Creado desde la tienda en línea",
    )
    db.add(c)
    db.flush()
    return c


# ---------- seguimiento ----------
def track_token(o: Order) -> str:
    return sign_purpose_token("pedido", {"o": o.id, "t": o.tenant_id}, 400)


def read_track_token(db: Session, t: Tenant, token: str) -> Order:
    try:
        p = read_purpose_token("pedido", token)
    except jwt.InvalidTokenError:
        raise HTTPException(404, "Pedido no encontrado") from None
    o = db.get(Order, int(p.get("o") or 0))
    if not o or o.tenant_id != t.id or int(p.get("t") or 0) != t.id:
        raise HTTPException(404, "Pedido no encontrado")
    return o


def store_base(t: Tenant) -> str:
    from ..routers.store import store_cfg

    dom = (store_cfg(t).get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    return f"https://{dom}/" if dom else f"{settings.public_base_url.rstrip('/')}/tienda/{t.slug}"


def track_url(t: Tenant, o: Order) -> str:
    return f"{store_base(t)}?pedido={track_token(o)}"


def product_url(t: Tenant, p) -> str:
    """Ficha del producto en la tienda (dominio propio o /tienda/{slug} del portal)."""
    from .store_showcase import product_path

    return f"{store_base(t).rstrip('/')}{product_path(p)}"


def order_public(db: Session, t: Tenant, o: Order) -> dict:
    """Lo que ve el comprador: sin ids internos, sin notas del equipo, sin datos de otros pedidos."""
    from ..routers.store import store_cfg

    cfg = store_cfg(t)
    ck = checkout_cfg(t)
    card_pending = o.payment_kind == "tarjeta" and o.status == "nuevo"
    return {
        "number": o.number,
        "status": o.status,
        "status_label": STATUS_LABEL.get(o.status, o.status),
        "created_at": aware(o.created_at),
        "paid_at": aware(o.paid_at),
        "expires_at": aware(o.expires_at) if o.status == "nuevo" else None,
        "currency": o.currency,
        "subtotal": o.subtotal,
        "discount_total": o.discount_total,
        "shipping": o.shipping,
        "tax_total": o.tax_total,
        "total": o.total,
        "coupon_code": o.coupon_code,
        "lines": [
            {"name": ln.name, "quantity": ln.quantity, "unit_price": ln.unit_price, "tax_rate": ln.tax_rate, "total": ln.total, "shipping": _is_ship(ln)}
            for ln in o.lines
        ],
        "contact": {"name": (o.contact or {}).get("name"), "email": _mask_email((o.contact or {}).get("email"))},
        "shipping_method": o.shipping_method,
        "delivery": {k: (o.delivery or {}).get(k) for k in ("type", "province", "canton", "district")} if o.delivery else None,
        "pickup": ck.get("pickup") if (o.delivery or {}).get("type") == "retiro" else None,
        "payment_method": o.payment_method,
        "payment_kind": o.payment_kind,
        "instructions": pay_instructions(t, o) if o.status in ("nuevo", "en_revision") else None,
        "needs_proof": o.payment_kind in NEEDS_PROOF and o.status == "nuevo",
        "proofs": [{"filename": p.get("filename"), "at": p.get("at")} for p in (o.proofs or [])],
        "card": {"pending": card_pending, "simulated": card_pending and not onvo_secret(db, t) and simulation_allowed()}
        if o.payment_kind == "tarjeta"
        else None,
        "tracking_number": o.tracking_number,
        "carrier": o.carrier,
        "cancel_reason": o.cancel_reason if o.status == "cancelado" else None,
        "history": [
            {"status": h.get("status"), "label": STATUS_LABEL.get(h.get("status"), h.get("status")), "at": h.get("at")}
            for h in (o.history or [])
            # nunca las notas del equipo; "internal" (vendedor asignado, 21/10 item 42) tampoco si algun dia el
            # seguimiento muestra notas (item 36): ese historial tiene que seguir respetando este filtro
            if h.get("kind") != "nota" and not h.get("internal")
        ],
        "whatsapp": cfg.get("whatsapp") or "",
        "store_name": cfg.get("name"),
    }


def _is_ship(ln) -> bool:
    """La linea de envio del pedido (el motor de totales la factura como una linea mas con IVA)."""
    return ln.product_id is None and (ln.name or "").startswith("Envio - ")


def _mask_email(e: str | None) -> str | None:
    if not e or "@" not in e:
        return None
    u, dom = e.split("@", 1)
    return f"{u[:2]}{'•' * max(1, len(u) - 2)}@{dom}"


# ---------- avisos ----------
CUSTOMER_SUBJECT = {
    "recibido": "Recibimos tu pedido {n}",
    "pagado": "Pago confirmado · pedido {n}",
    "rechazado": "Revisamos tu comprobante · pedido {n}",
    "preparando": "Estamos preparando tu pedido {n}",  # solo "Reenviar correo" (pasar a preparando no avisa)
    "listo": "Tu pedido {n} está listo para retiro",
    "enviado": "Tu pedido {n} va en camino",
    "entregado": "Pedido {n} entregado",
    "cancelado": "Pedido {n} cancelado",
    "reembolsado": "Reembolso registrado · pedido {n}",
    "por_vencer": "Tu pedido {n} sigue pendiente de pago",
    "vencido": "Tu pedido {n} venció sin pago",
}
SHOW_INSTRUCTIONS = ("recibido", "por_vencer", "rechazado")  # correos que llevan "Cómo pagar"
# titulo grande de cada correo (el asunto dice el numero; el titulo dice que paso)
CUSTOMER_TITLE = {
    "recibido": "¡Gracias por tu compra!",
    "pagado": "Pago confirmado",
    "rechazado": "No pudimos validar tu comprobante",
    "preparando": "Estamos preparando tu pedido",
    "listo": "Tu pedido está listo para retiro",
    "enviado": "Tu pedido va en camino",
    "entregado": "Pedido entregado",
    "cancelado": "Pedido cancelado",
    "reembolsado": "Reembolso registrado",
    "por_vencer": "Tu pedido sigue pendiente de pago",
    "vencido": "Tu pedido venció sin pago",
}
CUSTOMER_INTRO = {
    "recibido": "Recibimos tu pedido. Este es el resumen; con el botón de abajo seguís su estado en cualquier momento.",
    "pagado": "Confirmamos el pago de tu pedido. Ya lo estamos preparando.",
    "rechazado": "Revisamos el comprobante que subiste y no lo pudimos validar. Subí el comprobante correcto con el botón de abajo y lo revisamos de nuevo.",
    "preparando": "Ya estamos preparando tu pedido. Te avisamos cuando salga o cuando esté listo para retiro.",
    "listo": "Tu pedido ya está listo. Podés pasar a retirarlo con el número de pedido.",
    "enviado": "Tu pedido salió hacia la dirección de entrega.",
    "entregado": "Tu pedido fue entregado. ¡Gracias por comprar con nosotros!",
    "cancelado": "Tu pedido fue cancelado.",
    "reembolsado": "Registramos el reembolso de tu pedido.",
    "por_vencer": "Tu pedido sigue pendiente de pago.",
    "vencido": "El plazo para pagar tu pedido venció y lo cancelamos: los productos volvieron a la venta.",
}
TRACK_LABEL = "Rastrear mi pedido"  # el boton de siempre (21/10, item 40): lleva al seguimiento del pedido
EMAIL_GREY, EMAIL_INK, EMAIL_LINE = "#6b6570", "#15131a", "#ebe7e3"


def link_kind(c: Customer, contact: dict) -> str:
    """Como quedo ligado el pedido a la ficha (Order.customer_link): "email" si la ficha tenia el correo del comprador
    (ligada por correo o creada por este pedido) o no tenia correo (no hay otro dueno que proteger); "cedula" si solo
    coincidio la cedula y la ficha tenia OTRO correo. El correo de la ficha al ligar va en Order.customer_link_email."""
    mail = ((contact or {}).get("email") or "").strip().lower()
    ficha = (c.email or "").strip().lower()
    return "email" if not ficha or ficha == mail else "cedula"


def link_email(c: Customer) -> str | None:
    """El correo que tenia la ficha al ligar el pedido (Order.customer_link_email): notify_email lo compara despues."""
    return (c.email or "").strip().lower() or None


def notify_email(db: Session, o: Order) -> str:
    """Correo al que van los avisos del pedido (21/10, item 43): el ACTUAL de la ficha del cliente si el pedido esta
    ligado a uno (el admin lo corrigio o lo agrego despues de la compra); si no, o la ficha no tiene correo, el del pedido.
    Un pedido ligado solo por cedula a una ficha con OTRO correo (customer_link="cedula") sigue al correo del pedido:
    la cedula es publica y escribirla no hace a nadie dueno de esa ficha (sus avisos llevan el enlace del pedido). Si el
    equipo cambia despues el correo de esa ficha (distinto del que tenia al ligar), los avisos lo siguen.
    customer_link NULL (pedido sin la marca): la ficha solo si su correo es el de la compra."""
    mail = ((o.contact or {}).get("email") or "").strip()
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    ficha = (c.email or "").strip() if c is not None and c.tenant_id == o.tenant_id else ""
    if not ficha:
        return mail
    if o.customer_link == "email":
        return ficha
    if o.customer_link == "cedula":
        antes = (o.customer_link_email or "").strip().lower()
        return ficha if antes and ficha.lower() != antes else mail
    return ficha if ficha.lower() == mail.lower() else mail


def store_logo(t: Tenant) -> str | None:
    """Logo de la tienda (Mi Tienda) o el de la empresa, como URL absoluta: un correo no resuelve rutas relativas."""
    from ..routers.store import store_cfg

    for raw in (store_cfg(t).get("logo_url"), getattr(t, "logo_url", None)):
        u = str(raw or "").strip()
        if u.startswith("/") and not u.startswith("//"):
            u = f"{settings.public_base_url.rstrip('/')}{u}"
        if u.startswith(("https://", "http://")):
            return u
    return None


def _steps(o: Order) -> list[tuple[str, bool]]:
    """Linea de avance del pedido para el correo: (paso, completado). Retiro termina en "Listo para retiro"."""
    retiro = (o.delivery or {}).get("type") == "retiro" or o.status == "listo"
    names = ["Pedido recibido", "Pago confirmado", "Preparando", "Listo para retiro" if retiro else "Enviado", "Entregado"]
    pos = {"nuevo": 0, "en_revision": 0, "pagado": 1, "preparando": 2, "listo": 3, "enviado": 3, "entregado": 4}.get(o.status, -1)
    pendiente = o.payment_kind == "efectivo" and not o.paid_at and pos > 0
    if pendiente:
        names[1] = "Pago al retirar"  # efectivo: avanza sin pago confirmado; el paso sigue abierto hasta que pague
    return [(n, i <= pos and not (pendiente and i == 1)) for i, n in enumerate(names)]


def email_button(url: str, label: str, color: str) -> str:
    """Boton "a prueba de Outlook": una tabla con fondo (Outlook ignora el padding de un <a> en linea)."""
    e = html.escape
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0 8px"><tr>'
        f'<td align="center" bgcolor="{e(color)}" style="border-radius:10px;background:{e(color)}">'
        f'<a href="{e(url)}" target="_blank" style="display:inline-block;padding:13px 22px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px">{e(label)}</a></td></tr></table>'
    )


def email_shell(t: Tenant, *, title: str, body: str, foot: str = "") -> str:
    """Marco comun de los correos del pedido (21/10, items 40 y 49): logo de la tienda, titulo, contenido y pie.
    Tablas y estilos en linea: Outlook y Hotmail no leen hojas de estilo ni flexbox. `body` y `foot` ya vienen escapados."""
    from ..routers.store import store_cfg

    e = html.escape
    cfg = store_cfg(t)
    primary = cfg.get("primary") or "#e2233a"
    name = cfg.get("name") or t.name
    logo = store_logo(t)
    brand = (
        f'<img src="{e(logo)}" alt="{e(name)}" height="40" style="display:block;height:40px;max-width:220px;border:0">'
        if logo
        else f'<span style="font-size:20px;font-weight:700;color:{EMAIL_INK}">{e(name)}</span>'
    )
    wa = (cfg.get("whatsapp") or "").strip()
    contacto = f" · WhatsApp {e(wa)}" if wa else ""
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f2f0" style="background:#f4f2f0">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:560px;background:#ffffff;border:1px solid {EMAIL_LINE};border-radius:12px;font-family:Arial,Helvetica,sans-serif;color:{EMAIL_INK}">
<tr><td style="padding:20px 24px;border-bottom:3px solid {e(primary)}">{brand}</td></tr>
<tr><td style="padding:24px 24px 8px">
<h1 style="margin:0 0 10px;font-size:21px;line-height:1.3;color:{EMAIL_INK}">{e(title)}</h1>
{body}
</td></tr>
<tr><td style="padding:16px 24px 22px;border-top:1px solid {EMAIL_LINE};font-size:12px;line-height:1.5;color:{EMAIL_GREY}">{foot}{e(name)}{contacto}</td></tr>
</table></td></tr></table>"""


def _status_pill(o: Order, primary: str) -> str:
    e = html.escape
    tono = "#b42318" if o.status in ("cancelado", "reembolsado") else primary
    return (
        f'<p style="margin:0 0 16px;font-size:13px;color:{EMAIL_GREY}">Pedido <b style="color:{EMAIL_INK}">{e(o.number)}</b> · '
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;border:1px solid {e(tono)};color:{e(tono)};font-weight:700">'
        f"Estado: {e(STATUS_LABEL.get(o.status, o.status))}</span></p>"
    )


def _steps_html(o: Order, primary: str) -> str:
    """Pasos del pedido unidos por la linea de avance (como el seguimiento). No va en cancelados ni reembolsados."""
    if o.status not in ("nuevo", "en_revision", "pagado", "preparando", "listo", "enviado", "entregado"):
        return ""
    e = html.escape
    cells = []
    for name, done in _steps(o):
        color = primary if done else "#d6d1cc"
        mark = "&#10003;" if done else "&nbsp;"
        cells.append(
            f'<td align="center" valign="top" width="20%" style="padding:0 2px;border-top:3px solid {e(color)}">'
            f'<div style="margin:-12px auto 4px;width:20px;height:20px;line-height:20px;border-radius:10px;background:{e(color)};color:#fff;font-size:12px;font-weight:700">{mark}</div>'
            f'<div style="font-size:11px;line-height:1.3;color:{EMAIL_INK if done else EMAIL_GREY}">{e(name)}</div></td>'
        )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0 10px"><tr>{"".join(cells)}</tr></table>'


def _summary_html(o: Order) -> str:
    """Resumen: productos, descuento, envio, IVA y total."""
    e = html.escape
    rows = "".join(
        f'<tr><td style="padding:8px 0;border-bottom:1px solid {EMAIL_LINE}">{e(ln.name)} <span style="color:{EMAIL_GREY}">× {d(ln.quantity):g}</span></td>'
        f'<td align="right" style="padding:8px 0;border-bottom:1px solid {EMAIL_LINE};white-space:nowrap">{fmt_money(ln.total, o.currency)}</td></tr>'
        for ln in o.lines
        if not _is_ship(ln)
    )
    sub = f'style="padding:4px 0;color:{EMAIL_GREY}"'
    extra = ""
    if d(o.discount_total) > 0:
        cupon = f" ({e(o.coupon_code)})" if o.coupon_code else ""
        extra += f'<tr><td {sub}>Descuento{cupon}</td><td align="right" {sub}>−{fmt_money(o.discount_total, o.currency)}</td></tr>'
    envio = f" · {e(o.shipping_method)}" if o.shipping_method else ""
    extra += f'<tr><td {sub}>Envío (sin IVA){envio}</td><td align="right" {sub}>{fmt_money(o.shipping, o.currency)}</td></tr>'
    extra += f'<tr><td {sub}>IVA</td><td align="right" {sub}>{fmt_money(o.tax_total, o.currency)}</td></tr>'
    extra += (
        '<tr><td style="padding:8px 0 0;font-size:17px;font-weight:700">Total</td>'
        f'<td align="right" style="padding:8px 0 0;font-size:17px;font-weight:700;white-space:nowrap">{fmt_money(o.total, o.currency)}</td></tr>'
    )
    return (
        f'<p style="margin:16px 0 4px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{EMAIL_GREY}">Resumen</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px;border-top:1px solid {EMAIL_LINE}">{rows}{extra}</table>'
    )


def _email_html(t: Tenant, o: Order, kind: str, extra: str = "") -> str:
    from ..routers.store import store_cfg

    e = html.escape
    primary = store_cfg(t).get("primary") or "#e2233a"
    instr = pay_instructions(t, o) if kind in SHOW_INSTRUCTIONS and o.status in ("nuevo", "en_revision") else None
    url = track_url(t, o)
    main_url, boton = url, TRACK_LABEL
    if kind in ("por_vencer", "rechazado") and o.payment_kind in NEEDS_PROOF:
        boton = "Subir el comprobante"
    pie = "Guardá este correo: el botón te lleva al estado de tu pedido."
    if kind == "vencido":
        # un SINPE puede haberse pagado sin subir el comprobante: nunca se afirma que "no se cobro nada"
        main_url, boton, pie = store_base(t), "Volver a la tienda", "Si ya habías pagado, respondé a este correo con el comprobante y lo resolvemos."
    pay_box = (
        '<div style="margin:16px 0 0;background:#f6f2ee;border-radius:10px;padding:14px 16px;font-size:14px;line-height:1.5;white-space:pre-wrap">'
        f"<b>Cómo pagar · {e(o.payment_method or '')}</b><br>{e(instr)}</div>"
        if instr
        else ""
    )
    # boton principal; si no es el de seguimiento, "Rastrear mi pedido" queda como enlace debajo (items 40/49)
    track_link = (
        f'<p style="margin:0 0 6px;font-size:13px"><a href="{e(url)}" style="color:{e(primary)};font-weight:700">{TRACK_LABEL} →</a></p>'
        if boton != TRACK_LABEL
        else ""
    )
    body = (
        f'<p style="margin:0 0 12px;font-size:15px;line-height:1.5">{e(CUSTOMER_INTRO[kind])}</p>'
        f"{_status_pill(o, primary)}{_steps_html(o, primary)}{extra}{_summary_html(o)}{pay_box}"
        f"{email_button(main_url, boton, primary)}{track_link}"
        f'<p style="margin:10px 0 0;font-size:12px;line-height:1.5;color:{EMAIL_GREY}">Si el botón no abre, copiá este enlace en el navegador:<br>'
        f'<a href="{e(url)}" style="color:{EMAIL_GREY};word-break:break-all">{e(url)}</a></p>'
    )
    return email_shell(t, title=CUSTOMER_TITLE[kind], body=body, foot=f"{e(pie)} Cualquier consulta, respondé a este correo.<br>")


def notify_customer(
    db: Session, t: Tenant, o: Order, kind: str, extra: str = "", dedupe_key: str | None = None, to: str | None = None
) -> EmailOutbox | None:
    """dedupe_key (plan 1.2, regla 6): la llave estable de un correo automatico (recordatorio, vencido, rechazo).
    Va al correo ACTUAL del cliente si el pedido esta ligado a uno (notify_email, item 43); si no, al del pedido.
    `to`: solo la confirmacion del checkout, que va al correo que el comprador acaba de escribir."""
    from .mail import queue_email

    to = (to or "").strip() or notify_email(db, o)
    if not to or kind not in CUSTOMER_SUBJECT:
        return None
    return queue_email(db, t, to, CUSTOMER_SUBJECT[kind].format(n=o.number), _email_html(t, o, kind, extra), "order", o.id, dedupe_key=dedupe_key)


def panel_order_url(o: Order) -> str:
    """El pedido en el panel (Pedidos de la tienda lo abre con ?pedido=)."""
    return f"{settings.public_base_url.rstrip('/')}/ordenes?pedido={o.id}"


def notify_team(db: Session, t: Tenant, o: Order, kind: str, detail: str | None = None) -> int:
    from ..routers.store import store_cfg
    from .mail import notify_roles

    e = html.escape
    c = o.contact or {}
    primary = store_cfg(t).get("primary") or "#e2233a"
    titulo = {
        "nuevo": f"Pedido web {o.number} · {fmt_money(o.total, o.currency)}",
        "comprobante": f"Comprobante subido · pedido {o.number}",
        "pagado": f"Pago con tarjeta confirmado · pedido {o.number}",
        "revisar": f"Revisar pago ONVO · pedido {o.number}",
        "revision": f"Comprobante sin revisar · pedido {o.number}",
    }[kind]
    vendedor = ""
    if o.seller_id:
        from ..models import User

        u = db.get(User, o.seller_id)
        vendedor = f"<br>Vendedor: <b>{e(u.full_name)}</b>" if u else ""
    datos = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px;line-height:1.5;margin:0 0 6px">'
        '<tr><td style="padding:10px 12px;background:#f6f2ee;border-radius:10px">'
        f"<b>{e(c.get('name') or '')}</b> · {e(c.get('email') or '')} · {e(c.get('phone') or '')}<br>"
        f"Pago: {e(o.payment_method or '—')} · Entrega: {e(o.shipping_method or '—')}{vendedor}</td></tr></table>"
    )
    body = (f'<p style="margin:0 0 12px;font-size:15px;line-height:1.5">{e(detail)}</p>' if detail else "") + (
        f"{_status_pill(o, primary)}{datos}{_summary_html(o)}{email_button(panel_order_url(o), 'Abrir en Pedidos de la tienda', primary)}"
    )
    cuerpo = email_shell(t, title=titulo, body=body, foot="Aviso interno de la tienda en línea. ")
    roles = tuple(checkout_cfg(t).get("notify_roles") or ("admin",))
    return notify_roles(db, t, roles, titulo, cuerpo, "order", o.id, keep=_team_filter(db, t, o))


def _team_filter(db: Session, t: Tenant, o: Order):
    """Con vendedor asignado, el aviso le llega a quien ve todos los pedidos (sales.ver_todo) y al propio vendedor:
    a otro vendedor el pedido no le aparece en ningun lado (order_scope), tampoco por correo."""
    if not o.seller_id:
        return None
    from ..core.deps import _role_permissions, effective_permissions, perms_allow

    cache: dict[str, bool] = {}

    def keep(m) -> bool:
        if m.user_id == o.seller_id:
            return True
        if m.role_code not in cache:
            cache[m.role_code] = perms_allow(effective_permissions(t, m.role_code, _role_permissions(db, m.role_code)), "sales", "ver_todo")
        return cache[m.role_code]

    return keep


def email_status(db: Session, o: Order) -> list[dict]:
    rows = db.scalars(
        select(EmailOutbox).where(EmailOutbox.tenant_id == o.tenant_id, EmailOutbox.entity == "order", EmailOutbox.entity_id == o.id).order_by(EmailOutbox.id)
    ).all()
    return [{"to": m.to, "subject": m.subject, "status": m.status, "error": m.error, "at": m.created_at} for m in rows]


# ---------- estados ----------
def _hist(o: Order, status: str, by, note: str | None = None) -> None:
    o.history = [*(o.history or []), {"status": status, "at": datetime.now(UTC).isoformat(), "by": by, **({"note": note} if note else {})}]


def _note(o: Order, by, note: str, key: str | None = None) -> None:
    """Anotacion sin cambio de estado (factura automatica, pago que no cubre el total...). `key` marca las que van
    una sola vez (recordatorio de pago, "venció el plazo"): has_note la busca."""
    o.history = [
        *(o.history or []),
        {"status": o.status, "at": datetime.now(UTC).isoformat(), "by": by, "note": note, "kind": "nota", **({"key": key} if key else {})},
    ]


def has_note(o: Order, key: str) -> bool:
    return any(h.get("key") == key for h in (o.history or []))


def _inventory(db: Session, o: Order, old: str, new: str, user_id: int | None = None) -> None:
    """Delega en la capa de inventario (store_inventory.on_order_status): pagado/preparando fija la reserva,
    enviado/entregado descuenta una sola vez, cancelado libera. "Comprobante en revision" sigue siendo un pedido
    sin pagar: para el inventario cuenta como "nuevo" (asi el pago confirmado tambien fija la reserva)."""
    store_inventory.on_order_status(db, o, "nuevo" if old in ("nuevo", "en_revision") else old, new, user_id)
    if {old, new} == {"nuevo", "en_revision"}:  # comprobante subido o rechazado: el plazo de lo apartado
        store_inventory.on_review(db, o, under_review=new == "en_revision")
    if new == "cancelado":
        o.inventory_state = "released"
    elif new in ("enviado", "entregado"):
        o.inventory_state = "fulfilled"


def transition(
    db: Session,
    t: Tenant,
    o: Order,
    new: str,
    *,
    by: int | str | None,
    note: str | None = None,
    ip: str | None = None,
    tracking_number: str | None = None,
    carrier: str | None = None,
    refund_amount: Decimal | None = None,
    notify: bool = True,
) -> EmailOutbox | None:
    """Unico camino para cambiar el estado de un pedido de la tienda. Devuelve el correo al cliente (o None)."""
    old = o.status
    if new == old:
        raise HTTPException(409, "El pedido ya está en ese estado")
    if new not in TRANSITIONS.get(old, set()):
        raise HTTPException(409, f"No se puede pasar de «{STATUS_LABEL.get(old, old)}» a «{STATUS_LABEL.get(new, new)}»")
    if old in ("nuevo", "en_revision") and new in FULFILL and o.payment_kind != "efectivo":
        raise HTTPException(409, "Primero confirmá el pago (solo el efectivo contra entrega avanza sin pago)")
    if new == "cancelado" and not (note or "").strip():
        raise HTTPException(422, "Indicá el motivo de la cancelación")
    if new == "reembolsado" and old == "cancelado" and not o.paid_at:
        raise HTTPException(409, "Ese pedido nunca se pagó: no hay nada que reembolsar")
    if new == "reembolsado":
        amt = d(refund_amount if refund_amount is not None else o.total)
        if amt <= 0 or amt > d(o.total):
            raise HTTPException(422, "Monto de reembolso inválido")
        o.refund = {"amount": str(amt), "note": note, "at": datetime.now(UTC).isoformat(), "by": by}
    o.status = new
    if new == "pagado" or (new == "entregado" and o.payment_kind == "efectivo" and not o.paid_at):
        o.paid_at = o.paid_at or datetime.now(UTC)
    if new == "cancelado":
        o.cancel_reason = note.strip()[:500] if note else None
    if tracking_number is not None:
        o.tracking_number = tracking_number.strip()[:80] or None
    if carrier is not None:
        o.carrier = carrier.strip()[:80] or None
    _inventory(db, o, old, new, by if isinstance(by, int) else None)
    _hist(o, new, by, note)
    if new == "pagado":
        # quien confirma el pago queda como quien "tecleo" la factura (created_by): sin vendedor asignado, la ve en sus
        # Facturas (services/ownership.doc_mine). Con vendedor, la factura es del vendedor (21/10, item 24).
        auto_invoice(db, t, o, by if isinstance(by, int) else None)
    from ..routers.events import on_order_status

    on_order_status(db, o, new)  # entradas de evento ligadas al pedido (si las hay)
    audit(
        db,
        o.tenant_id,
        by if isinstance(by, int) else None,
        "status",
        "order",
        o.id,
        {"from": old, "to": new, **({"note": note} if note else {}), **({"by": by} if not isinstance(by, int) else {})},
        ip=ip,
    )
    if not notify:
        return None
    kind = "rechazado" if (new == "nuevo" and old == "en_revision") else new
    if kind == "preparando":
        return None  # pasar a preparando no avisa al cliente (su correo es solo para "Reenviar correo")
    key = None
    if kind == "rechazado":
        # 21/10, item 47: el cliente sabe por que y como volver a subirlo. Una llave por rechazo (el n.o de rechazos
        # del historial): un reintento del mismo rechazo no manda dos correos; un rechazo posterior si sale.
        n = sum(1 for h in (o.history or []) if h.get("status") == "nuevo" and h.get("kind") != "nota")
        key = f"tienda-rech:{o.id}:{n}"
    return notify_customer(db, t, o, kind, status_extra(t, o, kind, note), dedupe_key=key)


def last_rejection(o: Order) -> dict | None:
    """El cambio de estado mas reciente fue el rechazo del comprobante (en_revision -> nuevo): su entrada del
    historial (con la nota = el motivo). "Reenviar correo" reenvia ese aviso y no la confirmacion de compra."""
    hs = [h for h in (o.history or []) if h.get("kind") != "nota"]
    if o.status == "nuevo" and len(hs) >= 2 and hs[-1].get("status") == "nuevo" and hs[-2].get("status") == "en_revision":
        return hs[-1]
    return None


def resend_mail(db: Session, t: Tenant, o: Order) -> EmailOutbox | None:
    """El correo del estado actual para "Reenviar correo" (sin dedupe_key: el equipo lo pide a proposito)."""
    rech = last_rejection(o)
    if rech is not None:
        return notify_customer(db, t, o, "rechazado", status_extra(t, o, "rechazado", rech.get("note")))
    # preparando: si ya pago, el de pago confirmado; el efectivo sin pagar, uno neutro (nunca "pago confirmado")
    kind = {"nuevo": "recibido", "en_revision": "recibido", "preparando": "pagado" if o.paid_at else "preparando"}.get(o.status, o.status)
    return notify_customer(db, t, o, kind, status_extra(t, o, kind))


def status_extra(t: Tenant, o: Order, kind: str, note: str | None = None) -> str:
    """Lo propio de cada aviso de estado (transportista y guia, motivo, donde retirar, como volver a subir el
    comprobante). Lo usan el cambio de estado y "Reenviar correo", asi el reenvio dice lo mismo."""
    e = html.escape
    if kind == "enviado" and (o.tracking_number or o.carrier):
        partes = []
        if o.carrier:
            partes.append(f"Lo lleva: <b>{e(o.carrier)}</b>")
        if o.tracking_number:
            partes.append(f"Número de guía: <b>{e(o.tracking_number)}</b>")
        return _note_box("<br>".join(partes))
    if kind == "cancelado" and o.cancel_reason:
        return _note_box(f"Motivo: {e(o.cancel_reason)}")
    if kind == "reembolsado" and o.refund:
        return _note_box(f"Monto: <b>{fmt_money(o.refund['amount'], o.currency)}</b>")
    if kind == "listo":
        pk = checkout_cfg(t).get("pickup") or {}
        donde = f"Dónde: {e(pk['address'])}{' · ' + e(pk['hours']) if pk.get('hours') else ''}<br>" if pk.get("address") else ""
        return _note_box(f"{donde}Presentá el número de pedido <b>{e(o.number)}</b> al retirar.")
    if kind == "rechazado":
        motivo = f"<b>Qué revisar:</b> {e(note.strip())}<br>" if (note or "").strip() else ""
        return _note_box(
            f"{motivo}<b>Cómo volver a subirlo:</b> tocá «Subir el comprobante», elegí la foto o el PDF del comprobante "
            f"correcto (que se lean el monto y el detalle <b>{e(o.number)}</b>) y envialo. Lo revisamos apenas llegue.",
            warn=True,
        )
    return ""


def _note_box(inner_html: str, warn: bool = False) -> str:
    """Recuadro de nota dentro del correo (motivo, guia, donde retirar). `inner_html` ya viene escapado."""
    fondo, borde = ("#fff4f2", "#f1b8ae") if warn else ("#f6f2ee", "#ebe7e3")
    return f'<div style="margin:6px 0 4px;background:{fondo};border:1px solid {borde};border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.5">{inner_html}</div>'


def next_states(o: Order) -> list[str]:
    """Botones que el portal ofrece: los mismos saltos que transition() acepta, en orden de avance."""
    nxt = set(TRANSITIONS.get(o.status, set())) - {"en_revision"}  # en revision solo lo pone el comprobante
    if o.status in ("nuevo", "en_revision") and o.payment_kind != "efectivo":
        nxt -= set(FULFILL)
    if o.status == "cancelado" and not o.paid_at:
        nxt.discard("reembolsado")
    kind = (o.delivery or {}).get("type")
    if kind == "envio":
        nxt.discard("listo")  # "listo para retiro" no aplica a un envio
    elif kind == "retiro":
        nxt.discard("enviado")
    order = ["nuevo", "pagado", "preparando", "listo", "enviado", "entregado", "cancelado", "reembolsado"]
    return [s for s in order if s in nxt]


def mark_paid(db: Session, t: Tenant, o: Order, *, by, ref: str | None, note: str | None = None) -> bool:
    """Idempotente: un pedido ya pagado (o mas adelante) no cambia. Devuelve True si quedo pagado ahora."""
    if o.status not in ("nuevo", "en_revision"):
        return False
    if ref:
        o.payment_ref = ref[:120]
    transition(db, t, o, "pagado", by=by, note=note)
    return True


# ---------- vencimiento y recordatorio de pago (worker) ----------
REMINDER_NOTE = "recordatorio_pago"  # clave de la nota: un recordatorio por pedido (dedupe)
ONVO_WAIT_NOTE = "onvo_sin_confirmar"  # ONVO no respondio o tiene un pago en proceso: se reconsulta con espera creciente
ONVO_REVIEW_NOTE = "onvo_revisar"  # ONVO dice que cobro pero el pago no se aplico solo: a mano, sin cancelar ni reconsultar
ONVO_SYNC_NOTE = "onvo_consulta"  # consulta automatica (store.sync_card_orders) sin pago todavia: misma espera creciente
# Paginas de pago de ONVO por pedido (la del checkout + las de "Pagar con tarjeta" en Mi pedido). Cada una es una
# llamada a ONVO con la llave del comercio y despues se consulta; una persona real no necesita mas.
MAX_CARD_SESSIONS = 5
# Tarjeta: el pedido vence a los hold_minutes_card (lo apartado vuelve a la venta), pero solo se cancela pasado este
# margen. El enlace de ONVO no se invalida al vencer el pedido: un comprador que todavia llena la tarjeta (o vuelve a
# la pestana) pagaria un pedido ya cancelado. Mientras tanto la consulta automatica (store.sync_card_orders) aplica
# cualquier pago; pasado el margen, una sesion abierta sin pago (o con 3-D Secure sin completar) es un abandono.
CARD_GRACE = timedelta(hours=24)
CARD_GIVE_UP = timedelta(days=7)  # tarjeta que ONVO nunca confirmo ni nego en esta ventana: deja de consultarse (a mano)
ONVO_RECHECK = timedelta(minutes=15)  # primera espera para reconsultar a ONVO; se duplica en cada intento (tope 6 h)
ONVO_RECHECK_MAX = timedelta(hours=6)
# Correo de "vencido": a los pedidos cuyo plazo paso mas de esto ANTES de encender cancel_unpaid_on_expiry (se
# enciende una semana despues del despliegue) se les cancela sin correo: nadie recibe "tu pedido vencio" por un pedido
# abandonado de hace dias. Lo que vence con el ajuste ya encendido siempre lleva correo (aunque ONVO tardara en
# responder). Sin la fecha de encendido (ajuste viejo), cuenta desde ahora.
EXPIRED_MAIL_WINDOW = timedelta(hours=24)


def _cr_when(dt: datetime) -> str:
    from ..providers.einvoice import CR

    return aware(dt).astimezone(CR).strftime("%d/%m/%Y a las %H:%M")


def store_live(t: Tenant) -> bool:
    """La tienda publica responde (mismo criterio que routers/store._tenant): empresa activa y tienda publicada. Si no,
    los correos automaticos al comprador no salen (el enlace del pedido daria 404 y no se podria pagar ni subir nada)."""
    from ..routers.store import store_cfg

    return bool(t.active and store_cfg(t).get("published"))


def reminders_paused(db: Session, o: Order) -> bool:
    """Pausa de correos automaticos del cliente (Customer.no_reminders, plan 1.3; regla 6) cuando el campo existe."""
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    return bool(c is not None and getattr(c, "no_reminders", False))


def lock_order(db: Session, o: Order) -> Order:
    """Relee el pedido con candado antes de escribirle: el historial es un JSON entero y un webhook concurrente no
    puede perder su anotacion (ni el pago) por una copia vieja."""
    return db.get(Order, o.id, with_for_update=True, populate_existing=True)


def wait_entry(history: list | None, key: str = ONVO_WAIT_NOTE) -> dict | None:
    return next((h for h in (history or []) if h.get("key") == key), None)


def _wait_entry(o: Order, key: str = ONVO_WAIT_NOTE) -> dict | None:
    return wait_entry(o.history, key)


def last_check(h: dict | None) -> datetime | None:
    """Cuando se consulto a ONVO por ultima vez segun la entrada de espera (None: nunca o dato raro)."""
    if h is None:
        return None
    try:
        return aware(datetime.fromisoformat(h.get("checked_at") or h["at"]))
    except (KeyError, TypeError, ValueError):
        return None


def wait_due(h: dict | None, now: datetime) -> bool:
    """Toca volver a preguntarle a ONVO: espera creciente (15 min, 30 min, 1 h... tope 6 h) desde la ultima consulta
    que no fue definitiva. Asi un pedido trabado no hace una llamada HTTP en cada corrida."""
    last = last_check(h)
    if last is None:
        return True
    try:
        n = max(int(h.get("checks") or 1), 1)
    except (TypeError, ValueError):
        return True
    return now - last >= min(ONVO_RECHECK * 2 ** min(n - 1, 10), ONVO_RECHECK_MAX)


def onvo_wait_due(o: Order, now: datetime, key: str = ONVO_WAIT_NOTE) -> bool:
    return wait_due(_wait_entry(o, key), now)


def _count_check(o: Order, now: datetime, key: str, note: str) -> None:
    """Anota (una vez) la nota `key` y cuenta el intento para la espera creciente. El pedido ya esta bloqueado."""
    if _wait_entry(o, key) is None:
        _note(o, "sistema", note, key=key)
    o.history = [{**h, "checks": int(h.get("checks") or 0) + 1, "checked_at": now.isoformat()} if h.get("key") == key else h for h in (o.history or [])]


def _onvo_wait(o: Order, now: datetime) -> None:
    """Vencido sin respuesta definitiva de ONVO: no se cancela y se reconsulta con espera creciente."""
    _count_check(
        o,
        now,
        ONVO_WAIT_NOTE,
        "Venció el plazo, pero ONVO no confirmó que no hubo pago (sin respuesta o pago en proceso): no se cancela; se vuelve a consultar.",
    )


def onvo_sync_wait(o: Order, now: datetime) -> None:
    """Consulta automatica (store.sync_card_orders) sin pago confirmado: la siguiente espera mas (misma escala)."""
    _count_check(o, now, ONVO_SYNC_NOTE, "Consulta automática a ONVO sin pago confirmado todavía: se vuelve a consultar con espera creciente.")


def cancel_since(t: Tenant) -> datetime | None:
    """Cuando se encendio cancel_unpaid_on_expiry (lo guarda PUT /store/inventory/settings)."""
    raw = store_inventory.settings_of(t).get("cancel_unpaid_since")
    try:
        return aware(datetime.fromisoformat(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _redo_html(db: Session, t: Tenant, o: Order) -> str:
    """Pedido vencido: enlace a la ficha de cada producto (lo que se pueda volver a comprar) para rehacer la compra."""
    from ..models import Product

    e = html.escape
    ids = list(dict.fromkeys(ln.product_id for ln in o.lines if ln.product_id))
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
    items = "".join(
        f"<li><a href='{e(product_url(t, p))}' style='color:#15131a'>{e(p.name)}</a></li>"
        for pid in ids
        if (p := prods.get(pid)) is not None and p.active and p.show_on_web
    )
    if not items:
        return "<p style='font-size:14px'>Si todavía lo querés, podés hacer el pedido de nuevo en la tienda.</p>"
    return f"<p style='font-size:14px'>Si todavía lo querés, podés hacer el pedido de nuevo:</p><ul style='font-size:14px'>{items}</ul>"


def expire_unpaid(db: Session, t: Tenant, o: Order, now: datetime | None = None, ask_onvo: bool = True) -> str:
    """Pedido "nuevo" con cancel_unpaid_on_expiry encendido. Devuelve que paso: cancelado | pagado | pendiente |
    sin_respuesta | revisar | sin_plazo | en_plazo | margen_tarjeta | espera_onvo.
    - Solo se cancela con un plazo para pagar (expires_at) vencido: el efectivo (se cobra al retirar) y "a coordinar"
      no tienen plazo y nunca se cancelan solos (su reserva propia vence igual: store_inventory.expire_holds).
    - Con tarjeta: nunca antes de CARD_GRACE; despues se le pregunta a ONVO por TODAS las sesiones del pedido
      (onvo.order_payment): si cobro, el pago se aplica; si no responde o el pago esta en proceso, NO se cancela y se
      reconsulta con espera creciente (ask_onvo=False: ONVO ya no respondio en esta corrida; ni se intenta).
    - Si no: transition(cancelado, by="sistema", "Venció sin pago") libera la reserva y cancela la solicitud de compra
      reservada, y el cliente recibe un correo con el enlace para rehacer la compra (dedupe_key), salvo que el pedido
      ya estuviera abandonado al encender el ajuste, la tienda no este publicada o el cliente tenga la pausa.
    La consulta a ONVO (HTTP) va sin candado; el pedido se bloquea justo antes de escribirlo."""
    from . import onvo

    now = now or datetime.now(UTC)
    if o.status != "nuevo":
        return o.status  # comprobante en revision, pagado...: no se toca
    if o.expires_at is None:
        return "sin_plazo"
    deadline = aware(o.expires_at)
    if deadline > now:
        return "en_plazo"
    if o.payment_kind == "tarjeta":
        deadline += CARD_GRACE
        if has_note(o, ONVO_REVIEW_NOTE):
            return "revisar"
        if deadline > now:
            return "margen_tarjeta"
        if not ask_onvo or not onvo_wait_due(o, now):
            return "espera_onvo"
        st = onvo.order_payment(db, t, o)
        if st != "sin_pago":
            if st in ("pendiente", "sin_respuesta"):
                o = lock_order(db, o)
                if o.status == "nuevo":
                    _onvo_wait(o, now)
            return st
    o = lock_order(db, o)
    if o.status != "nuevo":
        return o.status  # lo pagaron (webhook) mientras se consultaba
    since = cancel_since(t)
    viejo = deadline < since - EXPIRED_MAIL_WINDOW if since else (deadline < now - EXPIRED_MAIL_WINDOW and not has_note(o, ONVO_WAIT_NOTE))
    transition(db, t, o, "cancelado", by="sistema", note="Venció sin pago", notify=False)
    motivo = None
    if viejo:
        motivo = "el plazo para pagar ya había vencido antes de encender la cancelación automática"
    elif not store_live(t):
        motivo = "la tienda no está publicada"
    elif reminders_paused(db, o):
        motivo = "el cliente tiene pausados los correos automáticos"
    if motivo:
        _note(o, "sistema", f"Sin correo al cliente: {motivo}.")
    else:
        notify_customer(db, t, o, "vencido", _redo_html(db, t, o), dedupe_key=f"tienda-venc:{o.id}")
    return "cancelado"


def remind_unpaid(db: Session, t: Tenant, o: Order) -> EmailOutbox | None:
    """Recordatorio de pago antes del vencimiento (SINPE / transferencia): instrucciones y boton para subir el
    comprobante. Una sola vez por pedido: dedupe_key (plan 1.2) y la clave REMINDER_NOTE del historial (el pedido se
    bloquea antes de mirarla). Respeta la pausa del cliente (Customer.no_reminders, plan 1.3) cuando ese campo existe.
    Devuelve el correo (None si ya se recordo, el cliente los tiene pausados o el pedido no tiene correo)."""
    if o.status != "nuevo" or o.payment_kind not in NEEDS_PROOF or o.expires_at is None or has_note(o, REMINDER_NOTE):
        return None
    if reminders_paused(db, o):
        _note(o, "sistema", "Recordatorio de pago omitido: el cliente tiene los recordatorios pausados", key=REMINDER_NOTE)
        return None
    cancela = store_inventory.settings_of(t).get("cancel_unpaid_on_expiry")
    extra = (
        f"<p style='font-size:14px'>Pagá antes del <b>{html.escape(_cr_when(o.expires_at))}</b> para confirmar tu pedido."
        + (" Después de esa hora se cancela y los productos vuelven a la venta." if cancela else "")
        + " Si ya pagaste, subí el comprobante con el botón de abajo.</p>"
    )
    m = notify_customer(db, t, o, "por_vencer", extra, dedupe_key=f"tienda-rem:{o.id}")
    _note(o, "sistema", "Recordatorio de pago al cliente" + (f" ({m.status})" if m else ": el pedido no tiene correo"), key=REMINDER_NOTE)
    return m


def attention_where(tid: int) -> list:
    return [
        Order.tenant_id == tid,
        Order.channel == "tienda",
        or_(Order.status.in_(ATTENTION), (Order.status == "nuevo") & (Order.payment_kind == "efectivo")),
    ]


# ---------- tarjeta (ONVO) ----------
def card_intent(db: Session, t: Tenant, o: Order) -> dict:
    """Abre una sesion de Checkout de ONVO por el total del pedido (o el modo simulado sin credenciales)."""
    from ..providers.payments import OnvoAdapter, OnvoError

    if o.payment_kind != "tarjeta" or o.status != "nuevo":
        raise HTTPException(409, "Este pedido no tiene un pago con tarjeta pendiente")
    secret = onvo_secret(db, t)
    if not secret and not simulation_allowed():
        raise HTTPException(503, "El pago con tarjeta no está disponible en este momento")
    if secret and card_sessions(db, o) >= MAX_CARD_SESSIONS:
        raise HTTPException(
            409, "Este pedido ya abrió varias páginas de pago. Si ya pagaste, esperá unos minutos a que se confirme; si no, escribinos para completarlo."
        )
    cents = int((d(o.total) * 100).quantize(Decimal(1)))
    c = o.contact or {}
    try:
        it = OnvoAdapter(secret).create_checkout(
            amount_cents=cents,
            currency=o.currency,
            description=f"Pedido {o.number} · {t.name}",
            metadata={"order_id": o.id, "tenant_id": t.id, "order_number": o.number, **_installments(t)},
            redirect_url=track_url(t, o),
            cancel_url=track_url(t, o),
            customer={"name": c.get("name"), "email": c.get("email"), "phone": c.get("phone")},
        )
    except OnvoError as e:
        log.warning("ONVO no abrió el checkout del pedido %s: %s", o.number, e.message)
        raise HTTPException(502, "No pudimos abrir la página de pago. Probá de nuevo en un momento.") from None
    o.payment_ref = it.id[:120]
    audit(db, t.id, None, "card_intent", "order", o.id, {"intent": it.id, "simulated": it.status == "simulated"})
    return {"checkout_url": it.checkout_url, "simulated": it.status == "simulated"}


def card_sessions(db: Session, o: Order) -> int:
    """Paginas de pago de ONVO abiertas para el pedido (bitacora "card_intent", que tambien lee onvo.order_sessions)."""
    from ..models import AuditLog

    q = select(func.count()).select_from(AuditLog)
    return int(
        db.scalar(q.where(AuditLog.tenant_id == o.tenant_id, AuditLog.entity == "order", AuditLog.entity_id == o.id, AuditLog.action == "card_intent")) or 0
    )


def _installments(t: Tenant) -> dict:
    """Cuotas con tarjeta: solo una referencia en la metadata. El Checkout de ONVO no recibe cuotas: se habilitan en la
    cuenta ONVO (Credix / tasa cero segun el contrato del comercio)."""
    c = checkout_cfg(t)["methods"]["tarjeta"]
    return {"installments": int(c.get("max_installments") or 0)} if c.get("installments") and c.get("max_installments") else {}


# ---------- comprobante ----------
MAX_PROOF = 5 * 1024 * 1024


def save_proof(db: Session, t: Tenant, o: Order, data: bytes, filename: str | None) -> Media:
    from ..routers.media import sniff

    if len(data) > MAX_PROOF:
        raise HTTPException(413, "El archivo pesa más de 5 MB")
    kind = sniff(data)
    if kind not in ("image/png", "image/jpeg", "image/webp", "application/pdf"):
        raise HTTPException(415, "Subí una foto (JPG, PNG, WEBP) o un PDF")
    if len(o.proofs or []) >= 5:
        raise HTTPException(409, "Ya hay varios comprobantes para este pedido. Escribinos por WhatsApp.")
    digest = hashlib.sha256(data).hexdigest()
    m = db.scalar(select(Media).where(Media.tenant_id == t.id, Media.sha256 == digest, Media.visibility == "private", Media.module == "sales"))
    if not m:
        name = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "._- " else "_" for ch in (filename or "comprobante"))[:120] or "comprobante"
        m = Media(tenant_id=t.id, key=secrets.token_urlsafe(18), filename=name, content_type=kind, size=len(data), sha256=digest, data=data, visibility="private", module="sales")
        db.add(m)
        db.flush()
    if any(p.get("media_id") == m.id for p in (o.proofs or [])):
        raise HTTPException(409, "Ese comprobante ya estaba subido")
    o.proofs = [*(o.proofs or []), {"media_id": m.id, "filename": m.filename, "size": m.size, "content_type": kind, "at": datetime.now(UTC).isoformat()}]
    return m


# ---------- vendedor del pedido (21/10, items 24/42) ----------
def order_seller(db: Session, o: Order) -> int | None:
    """El vendedor del pedido si todavia puede serlo (activo, del equipo). Si no, la factura nace sin vendedor."""
    from .ownership import eligible_seller

    return o.seller_id if o.seller_id and eligible_seller(db, o.tenant_id, o.seller_id) else None


def order_scope(p):
    """Que pedidos web ve el que mira (21/10, items 24/42; services/ownership.py): con sales.ver_todo, todos; si no,
    los sin vendedor (la cola que se puede tomar) y los suyos. Los de otro vendedor no aparecen en ningun lado."""
    if p.sees_all_sales:
        return true()
    return or_(Order.seller_id.is_(None), Order.seller_id == p.user.id)


def sees_order(o: Order, p) -> bool:
    return bool(p.sees_all_sales or o.seller_id is None or o.seller_id == p.user.id)


def takeable_ids(db: Session, orders) -> set[int]:
    """De estos pedidos, cuales puede "tomar" un vendedor: sin vendedor, por atender y sin factura viva (una consulta)."""
    from ..models import Invoice

    cand = [o for o in orders if o.channel == "tienda" and o.seller_id is None and o.status in TAKEABLE]
    inv_ids = {o.invoice_id for o in cand if o.invoice_id}
    vivas = set(db.scalars(select(Invoice.id).where(Invoice.id.in_(inv_ids), Invoice.status != "anulada"))) if inv_ids else set()
    return {o.id for o in cand if o.invoice_id not in vivas}


def web_orders_commission(t: Tenant) -> bool:
    """Mi Tienda -> Pagos: la factura de un pedido web asignado comisiona como una venta propia (por defecto si)."""
    return bool(checkout_cfg(t).get("web_orders_commission", True))


def is_web_order_invoice(db: Session, invoice_id: int, tenant_id: int) -> bool:
    return (
        db.scalar(select(Order.id).where(Order.tenant_id == tenant_id, Order.invoice_id == invoice_id, Order.channel == "tienda").limit(1))
        is not None
    )


def assign_seller(db: Session, t: Tenant, o: Order, seller_id: int | None, *, by: int, ip: str | None = None) -> bool:
    """Asigna (o quita) el vendedor del pedido web. Si el pedido ya tiene factura, la factura pasa al vendedor y los
    cobros que ya tenga comisionan (idempotente: commissions.on_payment no repite lo devengado). Una factura que ya
    comisiono a otro vendedor no se reasigna sola: la comision se anula primero en Comisiones (sin dobles pagos).
    Devuelve False si no cambia nada."""
    from ..models import Commission, Invoice, Payment
    from .ownership import eligible_seller

    if o.channel != "tienda":
        raise HTTPException(409, "Solo pedidos de la tienda en línea")
    if seller_id is not None and not eligible_seller(db, t.id, seller_id):
        raise HTTPException(422, "Ese vendedor no es un usuario activo de la empresa")
    if seller_id == o.seller_id:
        return False
    inv = db.get(Invoice, o.invoice_id) if o.invoice_id else None
    live_inv = inv is not None and inv.tenant_id == t.id and inv.status != "anulada"
    if live_inv:
        otro = db.scalar(
            select(Commission.user_id)
            .where(
                Commission.tenant_id == t.id,
                Commission.invoice_id == inv.id,
                Commission.beneficiary == "vendedor",
                Commission.status != "anulada",
                Commission.user_id.is_not(None),
                Commission.user_id != seller_id if seller_id is not None else true(),
            )
            .limit(1)
        )
        if otro is not None:
            raise HTTPException(409, "La factura de este pedido ya comisionó a otro vendedor: anulá esa comisión en Comisiones antes de reasignarlo")
    antes = o.seller_id
    o.seller_id = seller_id
    o.assigned_at = datetime.now(UTC) if seller_id else None
    from ..models import User

    nombre = (db.get(User, seller_id).full_name if seller_id else None) or ""
    # nota interna (nunca la ve el comprador en el seguimiento: kind=nota + internal)
    _note(o, by, f"Asignado a {nombre}" if seller_id else "Sin vendedor asignado")
    o.history = [*o.history[:-1], {**o.history[-1], "internal": True}]
    if live_inv:
        inv.seller_id = seller_id
        db.flush()
        if seller_id:
            from . import commissions

            pagos = db.scalars(
                select(Payment)
                .where(
                    Payment.tenant_id == t.id,
                    Payment.invoice_id == inv.id,
                    Payment.status == "confirmado",
                    Payment.kind.in_(("captura", "devolucion", "reembolso")),
                )
                .order_by(Payment.paid_at, Payment.id)
            ).all()
            for pay in pagos:
                # reassign: la comision ANULADA de otro vendedor sobre este cobro no bloquea al nuevo (el 409 de arriba
                # pide anularla justamente para poder reasignar)
                commissions.on_payment(db, pay, reassign=True)
    audit(db, t.id, by, "assign_seller", "order", o.id, {"from": antes, "to": seller_id, "invoice_id": inv.id if live_inv else None}, ip=ip)
    return True


# ---------- factura del pedido ----------
PAY_METHOD = {"tarjeta": "onvo", "sinpe": "sinpe", "transferencia": "transferencia", "efectivo": "efectivo"}


def invoice_order(db: Session, t: Tenant, o: Order, user_id: int | None, doc_type: str, register_payment: bool, ip: str | None = None):
    """Pedido -> tiquete (TE) o factura (FE). Mismo motor de totales que el pedido: coinciden al centimo.
    register_payment: si el pedido ya se pago, el cobro queda aplicado (la factura no aparece "por cobrar")."""
    from ..schemas.sales import DocumentIn, LineInSchema, PaymentIn
    from . import documents as docsvc

    if o.invoice_id:
        raise HTTPException(409, "La orden ya tiene comprobante")
    if doc_type not in ("TE", "FE"):
        raise HTTPException(422, "doc_type debe ser TE o FE")
    contact = o.contact or {}
    customer_id = o.customer_id
    if not customer_id and contact.get("email"):
        c = Customer(
            tenant_id=t.id,
            id_type=contact.get("id_type") or "fisica",
            id_number=contact.get("id_number"),
            name=contact.get("name") or "Cliente tienda",
            email=contact.get("email"),
            phone=contact.get("phone"),
            whatsapp=contact.get("phone"),
        )
        db.add(c)
        db.flush()
        customer_id = c.id
    from ..models import Product

    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([ln.product_id for ln in o.lines if ln.product_id] or [0])))}
    payload = DocumentIn(
        customer_id=customer_id,
        currency=o.currency,
        external_order=o.number,
        external_notes=f"Pedido {o.number}" + (f" - {o.shipping_method}" if o.shipping_method else ""),
        lines=[
            LineInSchema(
                product_id=ln.product_id,
                name=ln.name,
                # el comprobante electronico necesita el CABYS (y la unidad) del producto
                cabys_code=(prods.get(ln.product_id).cabys_code if prods.get(ln.product_id) else None),
                code=(prods.get(ln.product_id).code if prods.get(ln.product_id) else None),
                unit=(prods.get(ln.product_id).unit if prods.get(ln.product_id) and prods.get(ln.product_id).unit else "Unid"),
                quantity=d(ln.quantity),
                unit_price=d(ln.unit_price),
                discount_type=ln.discount_type,
                discount_value=d(ln.discount_value),
                tax_rate=d(ln.tax_rate),
            )
            for ln in o.lines
        ],
    )
    # 21/10, items 24/42: la factura es del vendedor asignado al pedido (comisiona como venta propia salvo que Mi Tienda
    # lo apague: commissions._beneficiaries). Sin vendedor no comisiona; si la factura la vendedora, la ve por created_by.
    invoice = docsvc.create_invoice(db, t.id, user_id, payload, doc_type, seller_id=order_seller(db, o))
    # la capa de inventario de la tienda descuenta solo lo que falte (nunca dos veces si ya se despacho)
    store_inventory.deduct_for_order_invoice(db, o, invoice, user_id)
    o.invoice_id = invoice.id
    if o.status == "nuevo" and not o.payment_kind:  # pedidos de antes del checkout nuevo: comportamiento de siempre
        o.status = "preparando"
    paid = False
    if register_payment and o.paid_at and d(invoice.total) > 0:
        docsvc.add_payment(
            db,
            t.id,
            user_id,
            invoice,
            PaymentIn(
                method=PAY_METHOD.get(o.payment_kind or "", "transferencia"),
                amount=d(invoice.total),
                external_ref=o.payment_ref,
                notes=f"Pedido web {o.number}",
            ),
        )
        paid = True
    audit(db, t.id, user_id, "invoice", "order", o.id, {"invoice_id": invoice.id, "doc_type": doc_type, "cobro": paid}, ip=ip)
    return invoice


def auto_invoice(db: Session, t: Tenant, o: Order, user_id: int | None = None) -> str | None:
    """Al confirmarse el pago: comprobante electronico + cobro aplicado + correo al cliente, como hacia Fygaro.
    Nunca tumba la confirmacion del pago: si algo falla queda anotado en el historial para hacerlo a mano."""
    from ..core.config import settings as cfg
    from . import einvoice as esvc

    if o.channel != "tienda" or o.invoice_id or not checkout_cfg(t).get("auto_invoice", True):
        return None
    from . import hacienda_config as hc

    provider = (t.settings or {}).get("einvoice_provider") or cfg.einvoice_provider
    mode = hc.hacienda_mode(db, t)
    doc_type = "FE" if (o.contact or {}).get("id_number") else "TE"
    sp = db.begin_nested()
    try:
        invoice = invoice_order(db, t, o, user_id, doc_type, register_payment=True)
        db.flush()
        if mode == "ninguno":
            note = f"{invoice.number} creada; sin proveedor fiscal configurado: queda sin emitir ante Hacienda"
        else:
            # emit() no hace commit ni llama a la red: en emision directa el worker (hacienda.send_pending) lo envia
            doc = esvc.emit(db, t, None, invoice)
            if mode in ("pruebas", "produccion"):
                note = f"{invoice.number} emitida (pendiente de Hacienda); el comprobante sale al cliente al firmarse"
            else:
                note = f"{invoice.number} emitida ({doc.status})" + (" · SANDBOX: sin validez fiscal" if provider == "sandbox" else "")
        sp.commit()
    except Exception as ex:  # noqa: BLE001
        sp.rollback()
        msg = getattr(ex, "detail", None) or str(ex)
        _note(o, "sistema", f"Factura automática no se pudo crear: {str(msg)[:200]}. Hacela desde Pedidos.")
        return None
    if mode in ("pruebas", "produccion"):
        _note(o, "sistema", note)  # el XML + PDF los entrega einvoice_delivery (art. 18), no este correo
        return note
    # Sin Hacienda en produccion (ninguno o simulado) el documento NO tiene validez fiscal: nunca se le manda al
    # cliente como "factura electronica". Queda la factura interna (cobro e inventario) y el aviso para emitirla a mano.
    note += " · no se envió al cliente: emití el comprobante electrónico a mano mientras la conexión con Hacienda no esté en producción"
    _note(o, "sistema", note)
    return note
