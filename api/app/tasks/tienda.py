"""Tareas del worker de la tienda en linea (plan maestro 1.5: pedidos web sin perdidas).

worker/celery_app.py solo importa este modulo y agenda las tareas por nombre en beat_schedule. Cada tarea abre su
propia sesion (app.core.db.SessionLocal) y hace commit por pedido: un pedido con datos raros no frena a los demas.

- store.payment_reminders: recordatorio al cliente antes de que venza el plazo para pagar (SINPE / transferencia),
  con las instrucciones y el boton para subir el comprobante. Uno por pedido (store_orders.REMINDER_NOTE).
- store.sync_card_orders: cada 15 min le pregunta a ONVO por los pedidos con tarjeta todavia "nuevo" y con sesion
  abierta (onvo.sync_order, respeta SYNC_EVERY): un pago cobrado sin webhook queda pagado aunque el comprador no
  vuelva a la tienda.
El vencimiento y la cancelacion de pedidos sin pagar siguen en inventory.expire_reservations
(store_inventory.expire_holds).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Order, Tenant

log = logging.getLogger("crimson.tienda")

REMIND_BEFORE = timedelta(hours=24)  # el recordatorio sale 24 h antes del vencimiento
SYNC_DAYS = 7  # pedidos con tarjeta que se le consultan a ONVO (una sesion vieja no se paga)


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
    for oid in ids:
        try:
            o = db.get(Order, oid, with_for_update=True, populate_existing=True)  # dos corridas a la vez: un solo correo
            if not o or o.status != "nuevo" or so.has_note(o, so.REMINDER_NOTE):
                continue
            if _aware(o.expires_at) - now > now - _aware(o.created_at):
                continue
            if so.remind_unpaid(db, db.get(Tenant, o.tenant_id), o):
                n += 1
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("recordatorio de pago del pedido %s", oid)
    return n


def sync_card_orders(db: Session, now: datetime | None = None) -> int:
    """Pedidos con tarjeta en "nuevo" con sesion de ONVO abierta (ultimos SYNC_DAYS dias): consulta a ONVO y aplica
    el pago si ya cobro. Devuelve cuantos quedaron pagados."""
    from ..services import onvo

    now = now or datetime.now(UTC)
    rows = db.scalars(
        select(Order.id).where(
            Order.channel == "tienda",
            Order.status == "nuevo",
            Order.payment_kind == "tarjeta",
            Order.payment_ref.is_not(None),
            Order.payment_ref.not_like("sim_%"),
            Order.created_at >= now - timedelta(days=SYNC_DAYS),
        )
    ).all()
    n = 0
    tenants: dict[int, Tenant | None] = {}
    for oid in rows:
        try:
            o = db.get(Order, oid)
            if not o or o.status != "nuevo":
                continue
            t = tenants.setdefault(o.tenant_id, db.get(Tenant, o.tenant_id))
            if t is None or not t.active:
                continue
            onvo.sync_order(db, t, o)
            if o.status != "nuevo":
                n += 1
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("consulta a ONVO del pedido %s", oid)
    return n


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
