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
from datetime import UTC, datetime
from decimal import Decimal

import jwt
from fastapi import HTTPException
from sqlalchemy import func, or_, select
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

CHECKOUT_DEFAULTS: dict = {
    "methods": {
        "sinpe": {"active": True, "phone": "", "holder": "", "note": ""},
        "transferencia": {"active": True, "bank": "", "iban": "", "holder": "", "holder_id": "", "note": ""},
        "tarjeta": {"active": False, "installments": False, "max_installments": 0},
        "efectivo": {"active": False, "note": "Pago en efectivo al recibir o al retirar el pedido."},
    },
    "free_shipping_from": 0,  # envio gratis desde este monto (en colones, con IVA); 0 = nunca
    "auto_invoice": True,  # comprobante electronico automatico al confirmarse el pago
    "pickup": {"address": "", "hours": ""},
    "notify_roles": ["admin", "ventas"],
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
        out.append({"code": code, "label": METHOD_LABEL[code], "needs_proof": code in NEEDS_PROOF, "simulated": simulated, "hint": method_hint(t, code)})
    return out


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
            if h.get("kind") != "nota"
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
    "listo": "Tu pedido {n} está listo para retiro",
    "enviado": "Tu pedido {n} va en camino",
    "entregado": "Pedido {n} entregado",
    "cancelado": "Pedido {n} cancelado",
    "reembolsado": "Reembolso registrado · pedido {n}",
}


def _email_html(t: Tenant, o: Order, kind: str, extra: str = "") -> str:
    from ..routers.store import store_cfg

    e = html.escape
    cfg = store_cfg(t)
    primary = cfg.get("primary") or "#e2233a"
    intro = {
        "recibido": "¡Gracias por tu compra! Este es el resumen de tu pedido.",
        "pagado": "Confirmamos el pago de tu pedido. Ya lo estamos preparando.",
        "rechazado": "No pudimos validar el comprobante que subiste. Revisá el monto y el detalle y volvé a subirlo, o escribinos.",
        "listo": "Tu pedido ya está listo. Podés pasar a retirarlo.",
        "enviado": "Tu pedido salió hacia la dirección de entrega.",
        "entregado": "Tu pedido fue entregado. ¡Gracias por comprar con nosotros!",
        "cancelado": "Tu pedido fue cancelado.",
        "reembolsado": "Registramos el reembolso de tu pedido.",
    }[kind]
    rows = "".join(
        f"<tr><td style='padding:6px 0'>{e(ln.name)} × {d(ln.quantity):g}</td><td style='padding:6px 0;text-align:right'>{fmt_money(ln.total, o.currency)}</td></tr>"
        for ln in o.lines
        if not _is_ship(ln)
    )
    instr = pay_instructions(t, o) if kind == "recibido" and o.status in ("nuevo", "en_revision") else None
    url = track_url(t, o)
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15131a">
<div style="border-bottom:3px solid {primary};padding:12px 0;font-size:18px;font-weight:700">{e(cfg.get("name") or t.name)}</div>
<p style="font-size:15px">{intro}</p>
<p style="font-size:13px;color:#5a5560">Pedido <b>{e(o.number)}</b> · Estado: <b>{e(STATUS_LABEL.get(o.status, o.status))}</b></p>
{extra}
<table style="width:100%;border-collapse:collapse;font-size:14px;border-top:1px solid #eee;border-bottom:1px solid #eee">{rows}</table>
<p style="text-align:right;font-size:13px;color:#5a5560;margin:8px 0 0">Envío (sin IVA) {fmt_money(o.shipping, o.currency)} · IVA {fmt_money(o.tax_total, o.currency)}</p>
<p style="text-align:right;font-size:18px;font-weight:700;margin:4px 0 16px">Total {fmt_money(o.total, o.currency)}</p>
{f'<div style="background:#f6f2ee;border-radius:8px;padding:12px 14px;white-space:pre-wrap;font-size:14px"><b>Cómo pagar · {e(o.payment_method or "")}</b><br>{e(instr)}</div>' if instr else ""}
<p><a href="{e(url)}" style="display:inline-block;background:{primary};color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">Ver mi pedido</a></p>
<p style="color:#8a858f;font-size:12px">Guardá este correo: el botón te lleva al estado de tu pedido. Cualquier consulta, respondé a este correo.</p></div>"""


def notify_customer(db: Session, t: Tenant, o: Order, kind: str, extra: str = "") -> EmailOutbox | None:
    from .mail import queue_email

    to = ((o.contact or {}).get("email") or "").strip()
    if not to or kind not in CUSTOMER_SUBJECT:
        return None
    return queue_email(db, t, to, CUSTOMER_SUBJECT[kind].format(n=o.number), _email_html(t, o, kind, extra), "order", o.id)


def notify_team(db: Session, t: Tenant, o: Order, kind: str, detail: str | None = None) -> int:
    from .mail import notify_roles

    e = html.escape
    c = o.contact or {}
    titulo = {
        "nuevo": f"Pedido web {o.number} · {fmt_money(o.total, o.currency)}",
        "comprobante": f"Comprobante subido · pedido {o.number}",
        "pagado": f"Pago con tarjeta confirmado · pedido {o.number}",
        "revisar": f"Revisar pago ONVO · pedido {o.number}",
    }[kind]
    cuerpo = (
        f"<p><b>{e(titulo)}</b></p>{f'<p>{e(detail)}</p>' if detail else ''}<p>{e(c.get('name') or '')} · {e(c.get('email') or '')} · {e(c.get('phone') or '')}</p>"
        f"<p>Pago: {e(o.payment_method or '—')} · Entrega: {e(o.shipping_method or '—')}</p>"
        f"<p>Estado: {e(STATUS_LABEL.get(o.status, o.status))}. Revisalo en Pedidos de la tienda.</p>"
    )
    roles = tuple(checkout_cfg(t).get("notify_roles") or ("admin",))
    return notify_roles(db, t, roles, titulo, cuerpo, "order", o.id)


def email_status(db: Session, o: Order) -> list[dict]:
    rows = db.scalars(
        select(EmailOutbox).where(EmailOutbox.tenant_id == o.tenant_id, EmailOutbox.entity == "order", EmailOutbox.entity_id == o.id).order_by(EmailOutbox.id)
    ).all()
    return [{"to": m.to, "subject": m.subject, "status": m.status, "error": m.error, "at": m.created_at} for m in rows]


# ---------- estados ----------
def _hist(o: Order, status: str, by, note: str | None = None) -> None:
    o.history = [*(o.history or []), {"status": status, "at": datetime.now(UTC).isoformat(), "by": by, **({"note": note} if note else {})}]


def _note(o: Order, by, note: str) -> None:
    """Anotacion sin cambio de estado (factura automatica, pago que no cubre el total...)."""
    o.history = [*(o.history or []), {"status": o.status, "at": datetime.now(UTC).isoformat(), "by": by, "note": note, "kind": "nota"}]


def _inventory(db: Session, o: Order, old: str, new: str, user_id: int | None = None) -> None:
    """Delega en la capa de inventario (store_inventory.on_order_status): pagado/preparando fija la reserva,
    enviado/entregado descuenta una sola vez, cancelado libera. "Comprobante en revision" sigue siendo un pedido
    sin pagar: para el inventario cuenta como "nuevo" (asi el pago confirmado tambien fija la reserva)."""
    store_inventory.on_order_status(db, o, "nuevo" if old in ("nuevo", "en_revision") else old, new, user_id)
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
        auto_invoice(db, t, o)
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
    extra = ""
    if new == "enviado" and o.tracking_number:
        extra = f"<p style='font-size:14px'>Número de guía: <b>{html.escape(o.tracking_number)}</b>{' · ' + html.escape(o.carrier) if o.carrier else ''}</p>"
    if new == "cancelado" and o.cancel_reason:
        extra = f"<p style='font-size:14px'>Motivo: {html.escape(o.cancel_reason)}</p>"
    if new == "reembolsado" and o.refund:
        extra = f"<p style='font-size:14px'>Monto: <b>{fmt_money(o.refund['amount'], o.currency)}</b></p>"
    if new == "listo":
        pk = checkout_cfg(t).get("pickup") or {}
        if pk.get("address"):
            extra = f"<p style='font-size:14px'>Dónde: {html.escape(pk['address'])}{' · ' + html.escape(pk['hours']) if pk.get('hours') else ''}</p>"
    return notify_customer(db, t, o, kind, extra)


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
    # venta de la tienda en linea: sin vendedor (no comisiona). Si la factura la vendedora, la ve por created_by.
    invoice = docsvc.create_invoice(db, t.id, user_id, payload, doc_type, seller_id=None)
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


def auto_invoice(db: Session, t: Tenant, o: Order) -> str | None:
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
        invoice = invoice_order(db, t, o, None, doc_type, register_payment=True)
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
    mail = _send_invoice(db, t, o, invoice)
    _note(o, "sistema", note + (f" · correo al cliente: {mail}" if mail else ""))
    return note


def _send_invoice(db: Session, t: Tenant, o: Order, invoice) -> str | None:
    from .mail import doc_email_html, queue_email
    from .render import money, render_html, render_pdf

    to = ((o.contact or {}).get("email") or "").strip()
    if not to:
        return None
    try:
        pdf = render_pdf(render_html(db, invoice, t))
    except Exception:  # noqa: BLE001
        pdf = None
    kind = "factura electrónica" if invoice.doc_type == "FE" else "tiquete electrónico"
    m = queue_email(
        db,
        t,
        to,
        f"{kind.capitalize()} {invoice.number} · pedido {o.number}",
        doc_email_html(
            t,
            kind,
            invoice.number,
            money(invoice.total, invoice.currency),
            None,
            f"Comprobante de tu pedido {o.number}. ¡Gracias por tu compra!",
            attached=pdf is not None,
        ),
        "invoice",
        invoice.id,
        [{"name": f"{invoice.number}.pdf", "kind": "invoices", "ref": invoice.id}] if pdf else [],
        files=[(f"{invoice.number}.pdf", pdf)] if pdf else None,
    )
    return {"enviado": "enviado", "simulado": "no salió (correo sin configurar)", "error": "no salió (error)"}.get(m.status, m.status)
