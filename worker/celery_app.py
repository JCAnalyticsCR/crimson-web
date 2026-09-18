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


@celery.task(name="sales.reminders")
def reminders() -> int:
    """Recordatorio N dias antes del vencimiento (ajuste remind_days_before) y aviso de vencidas, por correo al cliente."""
    from datetime import timedelta
    from app.core.db import SessionLocal
    from app.models import Customer, Invoice, Tenant
    from app.services.mail import doc_email_html, queue_email
    from app.services.render import money
    from sqlalchemy import select

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            st = t.settings or {}
            if not st.get("notify_due", True):
                continue
            target = date.today() + timedelta(days=int(st.get("remind_days_before", 3)))
            for inv in db.scalars(select(Invoice).where(Invoice.tenant_id == t.id, Invoice.status.in_(("creado", "enviada", "parcial")), Invoice.due_date == target)):
                c = db.get(Customer, inv.customer_id) if inv.customer_id else None
                if c and c.email:
                    queue_email(db, t, c.email, f"Recordatorio: factura {inv.number} vence el {inv.due_date}", doc_email_html(t, "factura", inv.number, money(inv.balance, inv.currency), None, "Le recordamos que su factura vence pronto."), "invoice", inv.id)
                    n += 1
        db.commit()
    return n


@celery.task(name="sales.daily_close_email")
def daily_close_email() -> int:
    from app.core.db import SessionLocal
    from app.models import Tenant, TenantUser
    from app.services.mail import queue_email
    from app.services.reports import cierre_diario
    from sqlalchemy import select

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            if not (t.settings or {}).get("daily_close_email"):
                continue
            rep = cierre_diario(db, t.id, date.today(), date.today())
            rows = "".join(f"<tr><td>{r[1]}</td><td>{r[2]}</td><td align=right>{r[3]}</td><td align=right>{r[4]}</td></tr>" for r in rep.rows) or "<tr><td colspan=4>Sin cobros hoy</td></tr>"
            html = f"<h3>Cierre de caja {date.today()}</h3><table border=0 cellpadding=6><tr><th>Método</th><th>Divisa</th><th>Monto</th><th>Pagos</th></tr>{rows}</table><p><b>Total: {rep.totals['total'] if rep.totals else 0}</b></p>"
            for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.role_code == "admin", TenantUser.active)):
                queue_email(db, t, m.user.email, f"Cierre de caja · {t.name} · {date.today()}", html, "report", 0)
                n += 1
        db.commit()
    return n


celery.conf.beat_schedule = {
    "reminders": {"task": "sales.reminders", "schedule": crontab(hour=8, minute=0)},
    "daily-close": {"task": "sales.daily_close_email", "schedule": crontab(hour=21, minute=0)},
    "run-recurrences": {"task": "sales.run_recurrences", "schedule": crontab(hour=5, minute=0)},
    "retry-outbox": {"task": "mail.retry_outbox", "schedule": crontab(minute="*/15")},
    "fx-bccr-daily": {"task": "fx.bccr_daily", "schedule": crontab(hour=6, minute=15)},
    "mark-overdue": {"task": "sales.mark_overdue", "schedule": crontab(hour=0, minute=30)},
}
