"""Worker Celery + beat. Se ejecuta con el mismo codigo de api/ (PYTHONPATH=../api).

Tareas planificadas (plan 6.2): tipo de cambio BCCR diario, recordatorios de vencimiento,
cierre de caja diario, emision fiscal en contingencia (Fase 2), conciliacion ONVO (Fase 3).
"""

from __future__ import annotations

import logging
from datetime import date

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

log = logging.getLogger("crimson.worker")
celery = Celery("crimson", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(timezone="America/Costa_Rica", enable_utc=True, task_acks_late=True, worker_prefetch_multiplier=1)


@celery.task(name="fx.bccr_daily", autoretry_for=(Exception,), retry_backoff=60, max_retries=5)
def fx_bccr_daily() -> dict:
    """Consulta el tipo de cambio de venta/compra del BCCR y lo guarda para hoy (fallback: ultimo conocido)."""
    from app.providers.fx.bccr import fetch_today
    from app.core.db import SessionLocal
    from app.models import ExchangeRate
    from sqlalchemy import select

    sell, buy = fetch_today()
    with SessionLocal() as db:
        fx = db.scalar(select(ExchangeRate).where(ExchangeRate.date == date.today(), ExchangeRate.currency == "USD"))
        if fx is None:
            fx = ExchangeRate(date=date.today(), currency="USD")
            db.add(fx)
        fx.sell, fx.buy, fx.source = sell, buy, "bccr"
        db.commit()
    log.info("BCCR %s venta=%s compra=%s", date.today(), sell, buy)
    return {"sell": str(sell), "buy": str(buy)}


@celery.task(name="sales.mark_overdue")
def mark_overdue() -> int:
    """Marca vencidas las cotizaciones/facturas cuya fecha paso (los recordatorios por correo llegan en Fase 1.4)."""
    from app.core.db import SessionLocal
    from app.models import Invoice, Quote
    from sqlalchemy import select, update

    n = 0
    with SessionLocal() as db:
        for model, states in ((Quote, ("creado", "enviada")), (Invoice, ("creado", "parcial"))):
            ids = db.scalars(select(model.id).where(model.status.in_(states), model.due_date < date.today())).all()
            if ids:
                db.execute(update(model).where(model.id.in_(ids)).values(status="vencida"))
                n += len(ids)
        db.commit()
    return n


@celery.task(name="sales.run_recurrences")
def run_recurrences() -> int:
    from app.core.db import SessionLocal
    from app.routers.ops import run_due_recurrences

    with SessionLocal() as db:
        return run_due_recurrences(db)


@celery.task(name="mail.retry_outbox")
def retry_outbox() -> int:
    from app.core.db import SessionLocal
    from app.models import EmailOutbox
    from app.services.mail import deliver
    from sqlalchemy import select

    n = 0
    with SessionLocal() as db:
        for m in db.scalars(select(EmailOutbox).where(EmailOutbox.status.in_(("pendiente", "error"))).limit(50)):
            deliver(m)
            n += 1
        db.commit()
    return n


celery.conf.beat_schedule = {
    "run-recurrences": {"task": "sales.run_recurrences", "schedule": crontab(hour=5, minute=0)},
    "retry-outbox": {"task": "mail.retry_outbox", "schedule": crontab(minute="*/15")},
    "fx-bccr-daily": {"task": "fx.bccr_daily", "schedule": crontab(hour=6, minute=15)},
    "mark-overdue": {"task": "sales.mark_overdue", "schedule": crontab(hour=0, minute=30)},
}
