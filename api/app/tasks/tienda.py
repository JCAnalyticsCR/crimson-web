"""Tareas del worker de la tienda en linea (plan maestro 1.5: pedidos web sin perdidas).

worker/celery_app.py solo importa este modulo y agenda las tareas por nombre en beat_schedule. Cada tarea abre su
propia sesion (app.core.db.SessionLocal) y hace commit por pedido: un pedido con datos raros no frena a los demas.

- store.payment_reminders: recordatorio al cliente antes de que venza el plazo para pagar (SINPE / transferencia),
  con las instrucciones y el boton para subir el comprobante. Uno por pedido (dedupe_key y store_orders.REMINDER_NOTE).
  Solo de tiendas que responden (empresa activa y tienda publicada: store_orders.store_live).
- store.sync_card_orders: cada 15 min le pregunta a ONVO por los pedidos con tarjeta todavia "nuevo" y con sesion
  abierta, por la sesion vigente y las abiertas en las ultimas 24 h (onvo.sync_order): un pago cobrado sin webhook
  queda pagado aunque el comprador no vuelva a la tienda ni haya pagado en la ultima sesion que abrio. Acotada: espera
  creciente por pedido (store_orders.ONVO_SYNC_NOTE), SYNC_PER_RUN pedidos y SYNC_BUDGET segundos por corrida (los
  consultados hace mas tiempo primero) y, si ONVO no responde, el resto de esa empresa espera a la proxima corrida.
El vencimiento y la cancelacion de pedidos sin pagar siguen en inventory.expire_reservations
(store_inventory.expire_holds).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Order, Tenant

log = logging.getLogger("crimson.tienda")

REMIND_BEFORE = timedelta(hours=24)  # el recordatorio sale 24 h antes del vencimiento
SYNC_DAYS = 7  # pedidos con tarjeta que se le consultan a ONVO (una sesion vieja no se paga)
SYNC_PER_RUN = 100  # pedidos que se le consultan a ONVO por corrida (cada uno: hasta MAX_CARD_SESSIONS llamadas)
SYNC_BUDGET = 600.0  # segundos por corrida: termina antes de la siguiente (cada 15 min) aunque ONVO este lento


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def payment_reminders(db: Session, now: datetime | None = None) -> int:
    """Pedidos "nuevo" de SINPE o transferencia a 24 h (o menos) de vencer -> recordatorio al cliente, una vez.
    Con un plazo corto espera a la mitad: asi no llega pegado al correo de "Recibimos tu pedido"."""
    from ..services import store_orders as so

    now = now or datetime.now(UTC)
    ids = db.scalars(
        select(Order.id).where(
            Order.channel == "tienda",
            Order.status == "nuevo",
            Order.payment_kind.in_(so.NEEDS_PROOF),
            Order.expires_at.is_not(None),
            Order.expires_at > now,
            Order.expires_at <= now + REMIND_BEFORE,
        )
    ).all()
    n = 0
    tenants: dict[int, Tenant | None] = {}
    for oid in ids:
        try:
            o = db.get(Order, oid, with_for_update=True, populate_existing=True)  # dos corridas a la vez: un solo correo
            if not o or o.status != "nuevo" or so.has_note(o, so.REMINDER_NOTE):
                continue
            if _aware(o.expires_at) - now > now - _aware(o.created_at):
                continue
            t = tenants.setdefault(o.tenant_id, db.get(Tenant, o.tenant_id))
            if t is None or not so.store_live(t):
                continue  # empresa suspendida o tienda sin publicar: el enlace daria 404 y no se podria pagar
            if so.remind_unpaid(db, t, o):
                n += 1
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("recordatorio de pago del pedido %s", oid)
    return n


def sync_card_orders(db: Session, now: datetime | None = None, budget: float = SYNC_BUDGET) -> int:
    """Pedidos con tarjeta en "nuevo" con sesion de ONVO abierta (ultimos SYNC_DAYS dias): consulta a ONVO y aplica
    el pago si ya cobro. Devuelve cuantos quedaron pagados.
    Cada consulta sin pago espera mas para la siguiente (15 min, 30 min, 1 h... tope 6 h: store_orders.wait_due); por
    corrida van como mucho SYNC_PER_RUN pedidos y SYNC_BUDGET segundos, primero los nunca consultados y despues los
    consultados hace mas tiempo. Si ONVO no responde (red, 5xx, 429, llave rechazada), el pedido cuenta el intento y
    el resto de esa empresa no se consulta en esta corrida (como onvo_down en store_inventory.expire_holds)."""
    from ..providers.payments import OnvoError
    from ..services import onvo
    from ..services import store_orders as so

    now = now or datetime.now(UTC)
    started = time.monotonic()
    rows = db.execute(
        select(Order.id, Order.tenant_id, Order.history).where(
            Order.channel == "tienda",
            Order.status == "nuevo",
            Order.payment_kind == "tarjeta",
            Order.payment_ref.is_not(None),
            Order.payment_ref.not_like("sim_%"),
            Order.created_at >= now - timedelta(days=SYNC_DAYS),
        )
    ).all()
    due: list[tuple[datetime, int, int]] = []
    for oid, tid, hist in rows:
        if so.wait_entry(hist, so.ONVO_REVIEW_NOTE) is not None:
            continue  # "revisar" (ONVO cobro y no se aplico): lo resuelve una persona
        w = so.wait_entry(hist, so.ONVO_SYNC_NOTE)
        if so.wait_due(w, now):
            due.append((so.last_check(w) or datetime.min.replace(tzinfo=UTC), oid, tid))
    due.sort()
    n = consulted = 0
    tenants: dict[int, Tenant | None] = {}
    onvo_down: set[int] = set()  # empresas cuyo ONVO no respondio en esta corrida
    for _, oid, tid in due:
        if consulted >= SYNC_PER_RUN or time.monotonic() - started >= budget:
            log.info("ONVO: consulta automatica cortada (%s pedido(s) consultados); el resto va en la proxima corrida", consulted)
            break
        if tid in onvo_down:
            continue
        try:
            o = db.get(Order, oid, populate_existing=True)  # sin candado: la consulta a ONVO no bloquea la fila
            if not o or o.status != "nuevo" or so.has_note(o, so.ONVO_REVIEW_NOTE):
                continue
            t = tenants.setdefault(tid, db.get(Tenant, tid))
            if t is None or not t.active:
                continue
            consulted += 1
            try:
                onvo.sync_order(db, t, o, onvo.AUTO, now=now)
            except OnvoError as e:
                db.rollback()
                onvo_down.add(tid)
                log.warning("ONVO no respondio la consulta del pedido %s: %s; su empresa espera a la proxima corrida", oid, e.message)
            if _counted(db, oid, now):
                n += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("consulta a ONVO del pedido %s", oid)
            try:
                _counted(db, oid, now)  # un pedido que falla igual espera: no se consulta en cada corrida
            except Exception:  # noqa: BLE001
                db.rollback()
    return n


def _counted(db: Session, oid: int, now: datetime) -> bool:
    """Despues de consultar: si sigue "nuevo", cuenta el intento (espera creciente). Hace commit. True si ya no esta
    "nuevo" (lo pago esta consulta o un webhook a la vez)."""
    from ..services import store_orders as so

    o = db.get(Order, oid, with_for_update=True, populate_existing=True)
    if o is not None and o.status == "nuevo":
        so.onvo_sync_wait(o, now)
    db.commit()
    return o is not None and o.status != "nuevo"


# ---------- tareas de Celery (las agenda worker/celery_app.py) ----------
@shared_task(name="store.payment_reminders")
def payment_reminders_task() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return payment_reminders(db)


@shared_task(name="store.sync_card_orders")
def sync_card_orders_task() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        n = sync_card_orders(db)
    if n:
        log.info("ONVO: %s pedido(s) con tarjeta confirmados por consulta", n)
    return n
