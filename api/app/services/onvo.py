"""ONVO del lado del negocio: credenciales por empresa, estado para Ajustes, cobros y aplicacion de pagos.

Un solo camino aplica un pago, venga del webhook o de la consulta que se hace cuando el comprador vuelve (sync):
objeto confirmado con la API de ONVO (pagado, monto, moneda) -> pedido de la tienda o factura. El header del webhook
solo dice "esto viene de ONVO"; lo que se cobro lo dice siempre una consulta a ONVO con la llave secreta.

Idempotencia de negocio por id de la intencion de pago: checkout-session.succeeded y payment-intent.succeeded pueden
llegar los dos, en cualquier orden, y a la vez que un sync; la fila del pedido/factura se bloquea (FOR UPDATE) y el
pago se aplica una sola vez.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.crypto import decrypt
from ..models import Customer, Invoice, Order, Payment, PaymentGatewayConfig, PaymentLink, Tenant
from ..providers.payments import OnvoAdapter, OnvoError, key_mode
from .documents import audit

log = logging.getLogger("crimson.onvo")


# ---------- credenciales ----------
def gateway(db: Session, tenant_id: int) -> PaymentGatewayConfig | None:
    return db.scalar(select(PaymentGatewayConfig).where(PaymentGatewayConfig.tenant_id == tenant_id, PaymentGatewayConfig.provider == "onvo"))


def _dec(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return decrypt(token)
    except Exception:  # noqa: BLE001  (JWT_SECRET rotado: el secreto guardado ya no se puede leer)
        log.warning("secreto ONVO cifrado ilegible (¿cambió JWT_SECRET?)")
        return None


def env_applies(db: Session, tenant_id: int) -> bool:
    """Las llaves por variable de entorno son de UNA empresa: la de ONVO_ENV_TENANT (slug) o, sin ese ajuste, la unica
    activa. Nunca de cualquier empresa sin configuracion propia (cobraria en la cuenta ONVO de otra)."""
    if settings.onvo_env_tenant:
        t = db.get(Tenant, tenant_id)
        return bool(t and t.slug == settings.onvo_env_tenant)
    ids = db.scalars(select(Tenant.id).where(Tenant.active).limit(2)).all()
    return ids == [tenant_id]


def _stored_secret(db: Session, tenant_id: int) -> tuple[str | None, str | None]:
    """(llave, origen). Ajustes activo manda; si no, la variable de entorno ONVO_SECRET_KEY (ver env_applies)."""
    g = gateway(db, tenant_id)
    if g and g.active and g.secret_encrypted:
        return _dec(g.secret_encrypted), "ajustes"
    if settings.onvo_secret_key and env_applies(db, tenant_id):
        return settings.onvo_secret_key, "entorno"
    return None, None


def usable(key: str | None) -> bool:
    """Formato de ONVO, y en produccion solo llaves live: con una de pruebas cualquiera 'pagaria' con la 4242."""
    mode = key_mode(key)
    return bool(mode) and not (settings.is_prod and mode == "test")


def secret_key(db: Session, tenant_id: int) -> str | None:
    """Llave para cobrar (abrir Checkout). None = la tarjeta no se ofrece (o va simulada fuera de produccion)."""
    key, _ = _stored_secret(db, tenant_id)
    return key if usable(key) else None


def verify_key(db: Session, tenant_id: int) -> str | None:
    """Llave para CONSULTAR pagos ya hechos: aunque la pasarela se haya desactivado despues, lo cobrado se aplica."""
    key, _ = _stored_secret(db, tenant_id)
    if not usable(key):
        g = gateway(db, tenant_id)
        key = _dec(g.secret_encrypted) if g else None
    return key if usable(key) else None


def webhook_secret(db: Session, tenant_id: int) -> str | None:
    g = gateway(db, tenant_id)
    own = _dec(g.webhook_secret_encrypted) if g else None
    return own or (settings.onvo_webhook_secret if settings.onvo_webhook_secret and env_applies(db, tenant_id) else None)


def webhook_url(t: Tenant) -> str:
    base = (settings.api_public_url or f"{settings.public_base_url.rstrip('/')}/api").rstrip("/")
    return f"{base}/webhooks/onvo/{t.id}"


def status(db: Session, t: Tenant) -> dict:
    """Lo que Ajustes muestra de ONVO, con los problemas en lenguaje claro (nunca la llave)."""
    g = gateway(db, t.id)
    key, source = _stored_secret(db, t.id)
    mode = key_mode(key)
    wh = webhook_secret(db, t.id)
    url = webhook_url(t)
    problems: list[str] = []
    if g and g.secret_encrypted and not g.active:
        problems.append("La pasarela ONVO está guardada pero inactiva: la tienda y los enlaces de pago no la ofrecen.")
    if key and not mode:
        problems.append("La llave secreta no tiene el formato de ONVO (onvo_test_secret_key_… u onvo_live_secret_key_…).")
    if settings.is_prod and mode == "test":
        problems.append("En producción no se aceptan llaves de pruebas (onvo_test_): el pago en línea queda apagado.")
    if not settings.is_prod and mode == "live":
        problems.append("Llave de PRODUCCIÓN en un entorno de pruebas: cada pago cobra dinero real.")
    if settings.onvo_secret_key and not env_applies(db, t.id):
        problems.append("Hay llaves ONVO en variables de entorno, pero son de otra empresa (ONVO_ENV_TENANT): esta usa solo Ajustes.")
    if usable(key) and not wh:
        problems.append("Falta el secreto del webhook: los pagos solo se confirmarán cuando el comprador vuelva a la página.")
    if "localhost" in url or "127.0.0.1" in url:
        problems.append("La URL del webhook es local: ONVO no puede llegar a ella. En este equipo el pago se confirma al volver de ONVO.")
    return {
        "configured": usable(key),
        "source": source,
        "mode": mode,
        "webhook_secret": bool(wh),
        "webhook_url": url,
        "active": bool(g and g.active) or source == "entorno",
        "problems": problems,
    }


def cents(amount) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1)))


# ---------- cobro de una factura (enlace de pago) ----------
def invoice_checkout(db: Session, t: Tenant, inv: Invoice, link: PaymentLink) -> dict:
    if inv.status == "anulada" or Decimal(str(inv.balance)) <= 0:
        raise HTTPException(409, "Esta factura no tiene saldo por pagar")
    key = secret_key(db, t.id)
    if not key:
        raise HTTPException(503, "El pago en línea no está disponible en este momento")
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    try:
        it = OnvoAdapter(key).create_checkout(
            amount_cents=cents(inv.balance),
            currency=inv.currency,
            description=f"Factura {inv.number} · {t.name}",
            metadata={"invoice_id": inv.id, "tenant_id": t.id, "invoice_number": inv.number, "link_id": link.id},
            redirect_url=f"{link.url}?onvo=volver",
            cancel_url=f"{link.url}?onvo=cancelado",
            customer={"name": c.name, "email": c.email, "phone": c.phone} if c else None,
        )
    except OnvoError as e:
        log.warning("ONVO no abrió el checkout de la factura %s: %s", inv.number, e.message)
        raise HTTPException(502, "No pudimos abrir la página de pago. Probá de nuevo en un momento.") from None
    link.provider_ref = it.id[:120]
    audit(db, t.id, None, "onvo_checkout", "invoice", inv.id, {"session": it.id, "monto": str(inv.balance)})
    return {"checkout_url": it.checkout_url}


# ---------- aplicar un pago confirmado ----------
SLIM_KEYS = (
    "id",
    "mode",
    "status",
    "paymentStatus",
    "amount",
    "amountTotal",
    "receivedAmount",
    "currency",
    "paymentIntentId",
    "metadata",
    "error",
    "createdAt",
    "updatedAt",
)


def slim(obj: dict) -> dict:
    """Lo que se guarda del objeto de ONVO: montos, estados e ids. Sin nombre, correo ni telefono del comprador."""
    return {k: obj[k] for k in SLIM_KEYS if k in obj}


def _amount(cts) -> Decimal:
    return Decimal(int(cts or 0)) / Decimal(100)


def apply_paid(db: Session, t: Tenant, meta: dict, obj: dict, *, amount_cents, currency: str | None, intent_id: str, source: str) -> str:
    if meta.get("tenant_id") and str(meta["tenant_id"]) != str(t.id):
        return "metadata de otra empresa: no se aplica"
    try:
        if meta.get("order_id") and not meta.get("invoice_id"):
            return _paid_order(db, t, int(meta["order_id"]), obj, amount_cents, currency, intent_id, source)
        if meta.get("invoice_id"):
            return _paid_invoice(db, t, int(meta["invoice_id"]), obj, amount_cents, currency, intent_id, source)
    except (TypeError, ValueError):
        return "metadata con ids inválidos"
    return "sin pedido ni factura en metadata"


APPLIED = ("ONVO cobró", "Pago ONVO por")  # notas que deja un pago ya procesado (no las de intentos rechazados)


def _seen(o: Order, intent_id: str) -> bool:
    """Ese pago ya se aplico o ya se anoto para revisar. Una nota de intento RECHAZADO no cuenta: la misma intencion
    puede reintentarse y salir aprobada."""
    if o.payment_ref == intent_id and o.paid_at:
        return True
    return any(intent_id in (h.get("note") or "") and (h.get("note") or "").startswith(APPLIED) for h in (o.history or []))


def _paid_order(db: Session, t: Tenant, oid: int, obj: dict, amount_cents, currency, intent_id: str, source: str) -> str:
    from . import store_orders as so

    o = db.get(Order, oid, with_for_update=True, populate_existing=True)
    if not o or o.tenant_id != t.id:
        return "pedido no encontrado"
    amount = _amount(amount_cents)
    if amount <= 0:
        return f"monto {amount} inválido: no se aplica"
    if _seen(o, intent_id):
        return f"pedido {o.number}: pago {intent_id} ya registrado"
    if currency and currency != o.currency:
        msg = f"ONVO cobró {currency} {amount} (intención {intent_id}) pero el pedido es en {o.currency}: revisar"
        so._note(o, "onvo", msg)
        so.notify_team(db, t, o, "revisar", detail=msg)
        return msg
    if amount + Decimal("0.01") < Decimal(str(o.total)):
        msg = f"Pago ONVO por {amount} (intención {intent_id}) no cubre el total {o.total}: no se aplica"
        so._note(o, "onvo", msg)
        so.notify_team(db, t, o, "revisar", detail=msg)
        return msg
    if o.status in ("nuevo", "en_revision"):
        so.mark_paid(db, t, o, by="onvo", ref=intent_id, note=f"Pago con tarjeta confirmado por ONVO (intención {intent_id}, {source})")
        so.notify_team(db, t, o, "pagado")
        return f"pedido {o.number} pagado"
    # llego dinero para un pedido que ya no lo esperaba (cancelado, vencido o pagado por otro medio)
    msg = f"ONVO cobró {so.fmt_money(amount, o.currency)} (intención {intent_id}) con el pedido «{so.STATUS_LABEL.get(o.status, o.status)}»: revisar y devolver el dinero si corresponde"
    so._note(o, "onvo", msg)
    so.notify_team(db, t, o, "revisar", detail=msg)
    return msg


def _paid_invoice(db: Session, t: Tenant, iid: int, obj: dict, amount_cents, currency, intent_id: str, source: str) -> str:
    from ..schemas.sales import PaymentIn
    from .documents import add_payment
    from .invoice_states import cr_today

    inv = db.get(Invoice, iid, with_for_update=True, populate_existing=True)
    if not inv or inv.tenant_id != t.id:
        return "factura no encontrada"
    dup = db.scalar(
        select(Payment.id).where(Payment.tenant_id == t.id, Payment.invoice_id == inv.id, Payment.provider == "onvo", Payment.external_ref == intent_id)
    )
    if dup:
        return f"pago {dup} ya aplicado a {inv.number}"
    amount = _amount(amount_cents)
    if amount <= 0:
        return f"monto {amount} inválido: no se aplica"
    if currency and currency != inv.currency:
        msg = f"ONVO cobró {currency} {amount} pero la factura {inv.number} es en {inv.currency}: revisar"
        if not _flagged(db, t, inv, intent_id):
            audit(db, t.id, None, "onvo_revisar", "invoice", inv.id, {"intencion": intent_id, "moneda": currency, "monto": str(amount)})
            _invoice_alert(db, t, inv, f"Pago ONVO en otra moneda · factura {inv.number}", f"{msg}. No se aplicó a la factura.", red=True)
        return msg
    if inv.status == "anulada":
        if not _flagged(db, t, inv, intent_id):
            audit(db, t.id, None, "onvo_revisar", "invoice", inv.id, {"intencion": intent_id, "monto": str(amount), "motivo": "factura anulada"})
            _invoice_alert(
                db,
                t,
                inv,
                f"Dinero por devolver · factura {inv.number} anulada",
                f"ONVO cobró {_m(amount, inv.currency)} (intención {intent_id}) por una factura anulada. Devolvé el dinero desde el panel de ONVO o aplicalo a otra factura.",
                red=True,
            )
        return f"factura {inv.number} anulada: ONVO cobró {amount}, devolver el dinero"
    p = add_payment(
        db,
        t.id,
        None,
        inv,
        # fecha de Costa Rica: un pago a las 20:00 CR no queda con la fecha UTC del dia siguiente (el resumen semanal la
        # compara con la del recordatorio, que tambien es de CR)
        PaymentIn(
            method="onvo", kind="captura", currency=inv.currency, amount=amount, external_ref=intent_id[:120], notes=f"Pago en línea ONVO ({source})", paid_at=cr_today()
        ),
    )
    p.provider, p.provider_event = "onvo", slim(obj)
    saldo = Decimal(str(inv.balance))
    if saldo < 0:
        audit(db, t.id, None, "onvo_sobrepago", "invoice", inv.id, {"intencion": intent_id, "monto": str(amount), "saldo": str(inv.balance)})
        _invoice_alert(
            db,
            t,
            inv,
            f"Sobrepago ONVO · factura {inv.number}",
            f"ONVO cobró {_m(amount, inv.currency)} y la factura quedó con saldo a favor del cliente de {_m(-saldo, inv.currency)}. Devolvé la diferencia o aplicala a otra factura.",
            red=True,
        )
    else:
        restante = f"Saldo pendiente: {_m(saldo, inv.currency)}." if saldo > 0 else "La factura quedó pagada."
        _invoice_alert(db, t, inv, f"Pago en línea recibido · factura {inv.number}", f"ONVO cobró {_m(amount, inv.currency)} (intención {intent_id}). {restante}")
    _receipt(db, t, inv, amount, intent_id)
    if saldo <= 0:
        from datetime import UTC, datetime

        for link in db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == inv.id, PaymentLink.paid_at.is_(None))):
            link.paid_at = datetime.now(UTC)
    return f"pago {p.id} aplicado a {inv.number}"


# ---------- avisos del pago de una factura (plan 1.3) ----------
def _m(v, cur: str) -> str:
    from .render import money

    return money(v, cur)


def _flagged(db: Session, t: Tenant, inv: Invoice, intent_id: str) -> bool:
    """Ese cobro ya se marco para revisar (la consulta al volver y el reintento del webhook no repiten la alerta)."""
    from ..models import AuditLog

    rows = db.scalars(
        select(AuditLog).where(AuditLog.tenant_id == t.id, AuditLog.entity == "invoice", AuditLog.entity_id == inv.id, AuditLog.action == "onvo_revisar")
    )
    return any((a.diff or {}).get("intencion") == intent_id for a in rows)


def _invoice_alert(db: Session, t: Tenant, inv: Invoice, title: str, detail: str, red: bool = False) -> None:
    """Aviso a administracion y contabilidad. Rojo: dinero que hay que devolver o revisar (anulada, otra moneda, sobrepago)."""
    from html import escape

    from .mail import notify_roles

    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    quien = c.name if c is not None and c.tenant_id == t.id else "sin cliente"
    estilo = "background:#fde8e8;color:#8a1c1c;border-left:4px solid #b3261e;padding:10px 14px" if red else "padding:4px 0"
    html_ = (
        f'<div style="{estilo}"><b>{escape(title)}</b></div><p>{escape(detail)}</p>'
        f"<p>{escape(inv.number)} · {escape(quien)}. Revisala en Facturas.</p>"
    )
    notify_roles(db, t, ("admin", "contabilidad"), ("ALERTA · " if red else "") + title, html_, "invoice", inv.id)


def _receipt(db: Session, t: Tenant, inv: Invoice, amount: Decimal, intent_id: str) -> None:
    """Recibo al cliente con el saldo que queda (si tiene correo)."""
    from html import escape

    from .documents_send import queue_once
    from .render import money

    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    if c is None or c.tenant_id != t.id or not (c.email or "").strip():
        return
    saldo = Decimal(str(inv.balance))
    resto = f"Saldo pendiente: <b>{money(saldo, inv.currency)}</b>." if saldo > 0 else "La factura quedó pagada en su totalidad. ¡Gracias!"
    html_ = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15131a">
<div style="border-bottom:3px solid #e2233a;padding:12px 0;font-size:18px;font-weight:700">{escape(t.name)}</div>
<p>Recibimos su pago en línea de <b>{money(amount, inv.currency)}</b> para la factura <b>{escape(inv.number)}</b>.</p>
<p>{resto}</p><p style="color:#8a858f;font-size:12px">Referencia del pago: {escape(intent_id)}. Cualquier consulta, responda a este correo.</p></div>"""
    queue_once(db, t, c.email.strip(), f"Recibimos su pago · {inv.number} · {t.name}", html_, "invoice", inv.id, dedupe_key=f"recibo:{inv.id}:{intent_id}")


def _note_attempt(db: Session, t: Tenant, meta: dict, intent_id: str, text: str) -> str:
    """Intento rechazado o en proceso: solo deja rastro en el pedido (la factura no cambia)."""
    from . import store_orders as so

    if not meta.get("order_id") or (meta.get("tenant_id") and str(meta["tenant_id"]) != str(t.id)):
        return f"{text}: sin pedido en metadata"
    try:
        o = db.get(Order, int(meta["order_id"]))
    except (TypeError, ValueError):
        return "metadata con ids inválidos"
    if not o or o.tenant_id != t.id:
        return "pedido no encontrado"
    line = f"{text} (intención {intent_id})"
    if not any(line in (h.get("note") or "") for h in (o.history or [])):
        so._note(o, "onvo", line)
    return f"pedido {o.number}: {line}"


# ---------- eventos del webhook ----------
def handle_event(db: Session, t: Tenant, payload: dict) -> str:
    """Aplica un evento ya autenticado. Lanza OnvoError si ONVO no confirma el pago o no respondio: el evento NO se
    consume y el webhook contesta 503, asi el reintento de ONVO se aplica cuando el pago este confirmado."""
    typ = str(payload.get("type") or "")
    data = payload.get("data") or {}
    oid = str(data.get("id") or "")
    if typ in ("checkout-session.succeeded", "payment-intent.succeeded"):
        key = verify_key(db, t.id)
        if not key:
            raise OnvoError(0, "sin llave secreta para confirmar el pago con ONVO")
        api = OnvoAdapter(key)
        if typ == "checkout-session.succeeded":
            return _apply_session(db, t, api, api.get_checkout(oid), data, "webhook")
        pi = api.get_intent(oid)
        if pi.get("status") != "succeeded":
            raise OnvoError(409, f"ONVO dice que la intención {oid} está «{pi.get('status')}»")
        meta = pi.get("metadata") or data.get("metadata") or {}
        if not (meta.get("order_id") or meta.get("invoice_id")):
            return "intención sin pedido ni factura (la aplica checkout-session.succeeded)"
        return apply_paid(db, t, meta, pi, amount_cents=pi.get("amount"), currency=pi.get("currency"), intent_id=oid, source="webhook")
    if typ == "payment-intent.failed":
        err = (data.get("error") or {}).get("message") or "sin detalle"
        return _note_attempt(db, t, data.get("metadata") or {}, oid, f"ONVO rechazó un intento de pago: {err[:160]}")
    if typ == "payment-intent.deferred":
        return _note_attempt(db, t, data.get("metadata") or {}, oid, "ONVO recibió el pago y espera la confirmación del banco")
    return f"evento {typ or '(sin tipo)'} ignorado"


def _apply_session(db: Session, t: Tenant, api: OnvoAdapter, s: dict, hint: dict, source: str, expect: dict | None = None) -> str:
    """Sesion de Checkout consultada a ONVO. `hint` = lo que trajo el webhook (respaldo si la consulta no trae metadata);
    `expect` = el pedido/factura para el que la abrimos nosotros (sync): manda sobre la metadata y debe coincidir."""
    sid = str(s.get("id") or hint.get("id") or "")
    if s.get("paymentStatus") != "paid":  # no confirmado (falso, o ONVO aun no se actualiza): el evento no se consume
        raise OnvoError(409, f"ONVO dice que la sesión {sid} no está pagada")
    pid = str(s.get("paymentIntentId") or hint.get("paymentIntentId") or "")
    amount, currency = s.get("amountTotal"), s.get("currency")
    if (amount is None or not currency) and pid:
        pi = api.get_intent(pid)
        if pi.get("status") != "succeeded":
            raise OnvoError(409, f"ONVO dice que la intención {pid} está «{pi.get('status')}»")
        amount, currency = (pi.get("amount") if amount is None else amount), currency or pi.get("currency")
    if amount is None:
        return f"sesión {sid} sin monto: no se aplica"
    meta = s.get("metadata") or hint.get("metadata") or {}
    if expect:
        for k, v in expect.items():
            if meta.get(k) and str(meta[k]) != str(v):
                return f"la sesión {sid} es de otro {k}: no se aplica"
        meta = {**meta, **{k: str(v) for k, v in expect.items()}}
    return apply_paid(db, t, meta, s, amount_cents=amount, currency=currency, intent_id=pid or sid, source=source)


# ---------- consulta al volver el comprador (sin esperar el webhook) ----------
SYNC_EVERY = 4.0  # segundos minimos entre consultas a ONVO por la misma sesion (cuida la cuota de la API)
_last_sync: dict[str, float] = {}
_sync_lock = threading.Lock()


def _throttled(session_id: str) -> bool:
    now = time.monotonic()
    with _sync_lock:
        if now - _last_sync.get(session_id, -SYNC_EVERY) < SYNC_EVERY:
            return True
        _last_sync[session_id] = now
        if len(_last_sync) > 20_000:  # higiene: no crecer sin limite
            for k in [k for k, v in _last_sync.items() if now - v > 600][:5_000]:
                _last_sync.pop(k, None)
    return False


AUTO = "consulta automática"  # source del worker (store.sync_card_orders)
AUTO_SESSIONS = timedelta(hours=24)  # consulta automatica: la sesion vigente y las abiertas en este lapso


def unreachable(e: OnvoError) -> bool:
    """ONVO no respondio (red o tiempo agotado: status 0), fallo (5xx), freno por cuota (429) o rechazo la llave
    (401/403): no es una respuesta sobre el pago. Un 404 (sesion que no existe) o el 409 de "no esta pagada" si lo son."""
    return e.status == 0 or e.status >= 500 or e.status in (401, 403, 429)


def _sync(db: Session, t: Tenant, session_id: str | None, expect: dict, source: str = "regreso") -> str | None:
    """None si la sesion no esta pagada o no se pudo consultar. En la consulta automatica, si ONVO no responde se
    relanza el OnvoError: el worker deja de consultar a esa empresa en la corrida."""
    if not session_id or session_id.startswith("sim_") or _throttled(session_id):
        return None
    key = verify_key(db, t.id)
    if not key:
        return None
    api = OnvoAdapter(key)
    try:
        return _apply_session(db, t, api, api.get_checkout(session_id), {"id": session_id}, source, expect)
    except OnvoError as e:
        log.info("sync ONVO %s: %s", session_id, e.message)
        if source == AUTO and unreachable(e):
            raise
        return None


def sync_order(db: Session, t: Tenant, o: Order, source: str = "regreso", now: datetime | None = None) -> str | None:
    """source: "regreso" (el comprador volvio de ONVO: su sesion es la ultima) o AUTO (worker cada 15 min, con espera
    creciente por pedido): la sesion vigente (o.payment_ref) y las abiertas en las ultimas AUTO_SESSIONS, porque "Pagar
    con tarjeta" en Mi pedido abre una nueva cada vez y el pago pudo quedar en una anterior. Una sesion mas vieja la
    revisa onvo.order_payment antes de cancelar (todas, hasta MAX_SESSIONS). AUTO relanza OnvoError si ONVO no responde."""
    if o.payment_kind != "tarjeta" or o.status not in ("nuevo", "en_revision"):
        return None
    if source == "regreso":
        refs = [o.payment_ref]
    else:
        refs = order_sessions(db, o, since=(now or datetime.now(UTC)) - AUTO_SESSIONS)
    for ref in refs:
        res = _sync(db, t, ref, {"order_id": o.id, "tenant_id": t.id}, source)
        if res is not None:
            return res
    return None


MAX_SESSIONS = 10  # sesiones de ONVO que se revisan por pedido (las mas recientes)


def order_sessions(db: Session, o: Order, since: datetime | None = None) -> list[str]:
    """Sesiones de Checkout de ONVO abiertas para el pedido, de la mas reciente a la mas vieja: la de o.payment_ref
    y las que dejo cada card_intent en la bitacora (audit "card_intent"), solo las abiertas desde `since` si se da (la
    vigente va siempre). Sin las simuladas."""
    from ..models import AuditLog

    ids = [o.payment_ref or ""]
    q = select(AuditLog.diff).where(AuditLog.tenant_id == o.tenant_id, AuditLog.entity == "order", AuditLog.entity_id == o.id, AuditLog.action == "card_intent")
    if since is not None:
        q = q.where(AuditLog.at >= since)
    diffs = db.scalars(q.order_by(AuditLog.id.desc()).limit(MAX_SESSIONS))
    ids += [str((d or {}).get("intent") or "") for d in diffs]
    return [s for s in dict.fromkeys(ids) if s and not s.startswith("sim_")][:MAX_SESSIONS]


# intenciones que ONVO da por terminadas sin cobro (cualquier otro estado puede terminar cobrando: no se cancela)
INTENT_NOT_PAID = ("requires_payment_method", "canceled", "cancelled", "failed")
# esperan al comprador (3-D Secure, confirmar): pasado el margen de la tarjeta (store_orders.CARD_GRACE) ya no se
# completan y cuentan como sin pago. "processing" y similares (esperando al banco) siguen pendientes.
INTENT_ABANDONED = ("requires_action", "requires_confirmation")


def order_payment(db: Session, t: Tenant, o: Order) -> str:
    """Antes de cancelar por vencido un pedido con tarjeta (pasado store_orders.CARD_GRACE): ¿ONVO cobro en ALGUNA de
    sus sesiones? Respuesta definitiva o "no se sabe":
    - pagado: ONVO cobro y el pago se aplico ahora;
    - revisar: ONVO dice que cobro pero el pago no se aplico (sesion de otro pedido, sin monto, no cubre el total...):
      queda una nota con clave y el equipo recibe UN aviso; no se cancela ni se vuelve a consultar;
    - sin_pago: ninguna sesion real (simulada o sin sesion), o ONVO responde que ninguna cobro (una sesion abierta sin
      intencion, o con 3-D Secure sin completar, es un abandono a esta altura);
    - pendiente: ONVO tiene un pago en proceso (por ejemplo esperando al banco);
    - sin_respuesta: ONVO no respondio o no hay llave para preguntarle.
    Solo "sin_pago" permite cancelar. No usa el freno por sesion (lo llama el worker, no el comprador)."""
    from . import store_orders as so

    if o.payment_kind != "tarjeta" or o.status not in ("nuevo", "en_revision"):
        return "no_aplica"
    refs = order_sessions(db, o)
    if not refs:
        return "sin_pago"
    key = verify_key(db, t.id)
    if not key:
        return "sin_respuesta"
    api = OnvoAdapter(key)
    pendiente = False
    try:
        for ref in refs:
            s = api.get_checkout(ref)
            if s.get("paymentStatus") == "paid":
                antes = len(o.history or [])
                res = _apply_session(db, t, api, s, {"id": ref}, "vencimiento", {"order_id": o.id, "tenant_id": t.id})
                if o.status != "nuevo":
                    return "pagado"
                o = so.lock_order(db, o)
                if len(o.history or []) == antes:  # nadie lo anoto (sesion de otro pedido, sin monto...): el equipo no se entero
                    so.notify_team(db, t, o, "revisar", detail=f"ONVO reporta pagada la sesión {ref}, pero el pago no se aplicó solo: {res}")
                if not so.has_note(o, so.ONVO_REVIEW_NOTE):
                    so._note(
                        o,
                        "sistema",
                        f"ONVO reporta un cobro que no se aplicó solo ({res}): revisalo a mano. El pedido no se cancela ni se vuelve a consultar.",
                        key=so.ONVO_REVIEW_NOTE,
                    )
                return "revisar"
            pid = str(s.get("paymentIntentId") or "")
            if pid:
                st = str(api.get_intent(pid).get("status") or "")
                if st not in INTENT_NOT_PAID + INTENT_ABANDONED:
                    pendiente = True
    except OnvoError as e:
        log.warning("ONVO no confirmó el pedido %s al vencer: %s", o.number, e.message)
        return "sin_respuesta"
    return "pendiente" if pendiente else "sin_pago"


# ---------- aviso al admin: ONVO no puede entregar sus eventos ----------
ALERT_ENTITY = "onvo_webhook"  # entity del correo: onvo_webhook_401 / onvo_webhook_503 (un cupo diario por tipo)
ONVO_EVENTS = ("checkout-session.succeeded", "payment-intent.succeeded", "payment-intent.failed", "payment-intent.deferred")


def onvo_shaped(secret: str | None, body: bytes) -> bool:
    """El 401 parece de ONVO (con el secreto viejo), no una sonda cualquiera a la URL: trae un secreto con la forma de
    los de ONVO (webhook_secret_...) y un evento de ONVO. Solo entonces vale la pena avisar al admin."""
    import json

    if not (secret or "").startswith("webhook_secret_") or len(secret or "") < 24:
        return False
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("type") in ONVO_EVENTS and isinstance(payload.get("data"), dict)


def _cr_now() -> datetime:
    from ..providers.einvoice import CR

    return datetime.now(CR)


def _queue_admins(db: Session, t: Tenant, subject: str, html_body: str, entity: str) -> int:
    """Encola (pendiente, SIN entregar) un correo a cada admin activo: lo manda el worker de correo (mail.retry_outbox).
    Asi la respuesta del webhook no espera a Resend ni delata con su demora si la empresa tiene ONVO."""
    from ..models import EmailOutbox, TenantUser

    vistos: set[str] = set()
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.active, TenantUser.role_code == "admin")):
        correo = (m.user.email or "").strip().lower()
        if not correo or correo in vistos:
            continue
        vistos.add(correo)
        db.add(EmailOutbox(tenant_id=t.id, to=correo, subject=subject, html=html_body, attachments=[], entity=entity, entity_id=t.id, status="pendiente"))
    db.flush()
    return len(vistos)


def webhook_alert(db: Session, t: Tenant, status: int, detail: str) -> bool:
    """401 (secreto que no coincide) o 503 (no se pudo confirmar con ONVO) en /webhooks/onvo: correo al admin, como
    maximo UNO por tipo y por dia de Costa Rica (despues de las 18:00 la fecha UTC ya es otra): un 401 falso no tapa
    el aviso de un 503 real. Sin ONVO conectado no avisa (una sonda a la URL no es un pago perdido). El cupo se revisa
    otra vez con la empresa bloqueada (dos peticiones a la vez: un solo aviso) y el correo queda encolado, no se
    entrega dentro de la peticion. No hace commit. Devuelve True si aviso."""
    import html

    from ..models import EmailOutbox

    g = gateway(db, t.id)
    if not (verify_key(db, t.id) or (g and g.secret_encrypted)):
        return False
    entity = f"{ALERT_ENTITY}_{status}"
    hoy = _cr_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    def ya() -> bool:
        q = select(EmailOutbox.id).where(EmailOutbox.tenant_id == t.id, EmailOutbox.entity == entity, EmailOutbox.created_at >= hoy).limit(1)
        return db.scalar(q) is not None

    if ya():
        return False
    db.execute(select(Tenant.id).where(Tenant.id == t.id).with_for_update())  # hasta el commit del aviso
    if ya():
        return False
    if status == 401:
        titulo = "ONVO no puede avisar los pagos: el secreto del webhook no coincide"
        que = (
            "El panel rechazó (401) un aviso con formato de ONVO porque el secreto del webhook no coincide. "
            "Si los pagos con tarjeta se están confirmando solos, puede ser un intento ajeno: no hace falta cambiar nada. "
            "Si no, compará el secreto del webhook de Ajustes → Pasarelas → ONVO con el que muestra el panel de ONVO "
            "(Desarrolladores → Webhooks). Mientras tanto los pagos con tarjeta se confirman cuando el comprador vuelve a "
            "la tienda o en la consulta automática."
        )
    else:
        titulo = "ONVO avisó un pago que no se pudo confirmar"
        que = (
            "Llegó un aviso de ONVO que el panel no pudo confirmar consultando a ONVO (503). ONVO lo reintenta solo; "
            "si se repite, revisá el pedido o la factura en el panel de ONVO y la llave en Ajustes → Pasarelas."
        )
    cuerpo = (
        f"<p><b>{html.escape(titulo)}</b></p><p>{html.escape(que)}</p><p style='color:#5a5560;font-size:13px'>Detalle: {html.escape(detail[:300])}</p>"
        "<p style='color:#8a858f;font-size:12px'>Este aviso sale como máximo una vez por día.</p>"
    )
    return _queue_admins(db, t, titulo, cuerpo, entity) > 0


def sync_invoice(db: Session, t: Tenant, inv: Invoice, link: PaymentLink) -> str | None:
    if inv.status == "anulada" or Decimal(str(inv.balance)) <= 0:
        return None
    return _sync(db, t, link.provider_ref, {"invoice_id": inv.id, "tenant_id": t.id})


# ---------- reembolso de un pedido ----------
def order_refundable(o: Order) -> bool:
    """Pagado con tarjeta por ONVO (con intencion conocida) y sin reembolso registrado."""
    ref = o.payment_ref or ""
    paid_by_onvo = any(h.get("status") == "pagado" and h.get("by") == "onvo" for h in (o.history or []))
    refunded = o.status == "reembolsado" or any((h.get("note") or "").startswith("Reembolso ONVO") for h in (o.history or []))
    return o.payment_kind == "tarjeta" and paid_by_onvo and bool(ref) and not ref.startswith("sim_") and not refunded


def refund_order(db: Session, t: Tenant, o: Order, amount: Decimal | None) -> dict:
    if not order_refundable(o):
        raise HTTPException(409, "Este pedido no tiene un pago ONVO para devolver")
    key = verify_key(db, t.id)
    if not key:
        raise HTTPException(503, "ONVO no está conectado: no se puede devolver el dinero desde aquí")
    amt = Decimal(str(amount)) if amount is not None else Decimal(str(o.total))
    try:
        r = OnvoAdapter(key).refund(o.payment_ref, cents(amt), description=f"Pedido {o.number}")
    except OnvoError as e:
        log.warning("ONVO rechazó el reembolso del pedido %s: %s", o.number, e.message)
        raise HTTPException(502, f"ONVO no aceptó el reembolso: {e.message}") from None
    if r.get("status") == "failed":
        raise HTTPException(502, f"ONVO no pudo hacer el reembolso: {r.get('failureReason') or 'sin detalle'}")
    return r
