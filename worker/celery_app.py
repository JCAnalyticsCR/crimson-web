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
    """Tipo de cambio de venta/compra del BCCR (API SDDE con BCCR_TOKEN). Sin token no hace nada."""
    import os

    from app.core.db import SessionLocal
    from app.routers.core import save_bccr_today

    if not os.getenv("BCCR_TOKEN"):
        log.info("BCCR: sin BCCR_TOKEN, se mantiene el tipo de cambio manual")
        return {"skipped": True}
    with SessionLocal() as db:
        fx = save_bccr_today(db)
    log.info("BCCR %s venta=%s compra=%s", fx.date, fx.sell, fx.buy)
    return {"sell": str(fx.sell), "buy": str(fx.buy)}


@celery.task(name="sales.mark_overdue")
def mark_overdue() -> int:
    """Marca vencidas las cotizaciones/facturas cuya fecha paso (los recordatorios por correo llegan en Fase 1.4)."""
    from sqlalchemy import select, update

    from app.core.db import SessionLocal
    from app.models import Invoice, Quote

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
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import EmailOutbox
    from app.services.mail import deliver

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

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Customer, Invoice, Tenant
    from app.services.mail import doc_email_html, queue_email
    from app.services.render import money

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            st = t.settings or {}
            if not st.get("notify_due", True):
                continue
            target = date.today() + timedelta(days=int(st.get("remind_days_before", 3)))
            for inv in db.scalars(
                select(Invoice).where(Invoice.tenant_id == t.id, Invoice.status.in_(("creado", "enviada", "parcial")), Invoice.due_date == target)
            ):
                c = db.get(Customer, inv.customer_id) if inv.customer_id else None
                if c and c.email:
                    queue_email(
                        db,
                        t,
                        c.email,
                        f"Recordatorio: factura {inv.number} vence el {inv.due_date}",
                        doc_email_html(t, "factura", inv.number, money(inv.balance, inv.currency), None, "Le recordamos que su factura vence pronto."),
                        "invoice",
                        inv.id,
                    )
                    n += 1
        db.commit()
    return n


@celery.task(name="sales.daily_close_email")
def daily_close_email() -> int:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant, TenantUser
    from app.services.mail import queue_email
    from app.services.reports import cierre_diario

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            if not (t.settings or {}).get("daily_close_email"):
                continue
            rep = cierre_diario(db, t.id, date.today(), date.today())
            rows = (
                "".join(f"<tr><td>{r[1]}</td><td>{r[2]}</td><td align=right>{r[3]}</td><td align=right>{r[4]}</td></tr>" for r in rep.rows)
                or "<tr><td colspan=4>Sin cobros hoy</td></tr>"
            )
            html = f"<h3>Cierre de caja {date.today()}</h3><table border=0 cellpadding=6><tr><th>Método</th><th>Divisa</th><th>Monto</th><th>Pagos</th></tr>{rows}</table><p><b>Total: {rep.totals['total'] if rep.totals else 0}</b></p>"
            for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.role_code == "admin", TenantUser.active)):
                queue_email(db, t, m.user.email, f"Cierre de caja · {t.name} · {date.today()}", html, "report", 0)
                n += 1
        db.commit()
    return n


@celery.task(name="reception.poll_inboxes")
def poll_inboxes() -> int:
    """Bandejas IMAP activas: importa XML de proveedores. Un buzon con error no detiene a los demas."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant
    from app.services.inbox import config, poll

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            if not config(t).get("enabled"):
                continue
            try:
                res = poll(db, t)
                n += res["nuevos"]
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.warning("bandeja %s: %s", t.slug, e)
    return n


@celery.task(name="support.expire_grants")
def expire_support_grants() -> int:
    from app.core.db import SessionLocal
    from app.routers.support import expire_grants

    with SessionLocal() as db:
        n = expire_grants(db)
        db.commit()
    return n


@celery.task(name="crm.stale_followups")
def stale_followups() -> int:
    """Oportunidad abierta sin seguimiento (5 dias o proxima accion vencida) -> correo a quien la lleva."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant, User
    from app.routers.pipeline import stale_followups as find
    from app.services.mail import queue_email

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            por_persona: dict[int, list] = {}
            for o in find(db, t.id, days=5):
                por_persona.setdefault(o.owner_id or 0, []).append(o)
            for uid, items in por_persona.items():
                u = db.get(User, uid) if uid else None
                if not u or not u.email:
                    continue
                filas = "".join(
                    f"<li><b>{o.number}</b> · {o.title} · {o.status}{' · vence ' + o.next_action_date.isoformat() if o.next_action_date else ''}</li>"
                    for o in items[:20]
                )
                queue_email(
                    db,
                    t,
                    u.email,
                    f"{len(items)} oportunidades esperan seguimiento",
                    f"<p>Estas oportunidades llevan días sin movimiento:</p><ul>{filas}</ul><p>Abrí el panel y registrá el siguiente paso.</p>",
                    "opportunity",
                    items[0].id,
                )
                n += len(items)
        db.commit()
    return n


@celery.task(name="inventory.low_stock_requests")
def low_stock_requests() -> int:
    """Inventario bajo el minimo -> solicitud de compra en borrador (los proveedores no dan credito)."""
    from decimal import Decimal

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Product, PurchaseRequest, PurchaseRequestLine, Tenant
    from app.services.inventory import low_stock
    from app.services.sequences import next_number

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            faltantes = low_stock(db, t.id)
            if not faltantes:
                continue
            abierta = db.scalar(
                select(PurchaseRequest).where(PurchaseRequest.tenant_id == t.id, PurchaseRequest.reason == "stock_bajo", PurchaseRequest.status == "borrador")
            )
            if abierta:
                continue
            number, _ = next_number(db, t.id, "SC")
            req = PurchaseRequest(
                tenant_id=t.id, number=number, reason="stock_bajo", status="borrador", notes="Generada automáticamente por stock bajo el mínimo"
            )
            total = Decimal(0)
            for f in faltantes:
                prod = db.get(Product, f["product_id"])
                falta = Decimal(str(max(prod.min_stock * 2 - float(f["quantity"]), 1)))
                cost = Decimal(str(prod.cost or 0))
                req.lines.append(PurchaseRequestLine(product_id=prod.id, name=prod.name, quantity=falta, unit_cost=cost))
                total += cost * falta
                if prod.supplier_id and not req.supplier_id:
                    req.supplier_id = prod.supplier_id
            req.total_cost = total
            db.add(req)
            n += len(faltantes)
        db.commit()
    return n


@celery.task(name="assets.warranty_alerts")
def warranty_alerts() -> int:
    """Equipo instalado que cumple 11 meses -> aviso de garantia proxima a vencer."""
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Customer, CustomerAsset, Tenant, TenantUser
    from app.services.mail import queue_email

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            limite = date.today() + timedelta(days=45)
            avisos = [
                a
                for a in db.scalars(
                    select(CustomerAsset).where(CustomerAsset.tenant_id == t.id, CustomerAsset.warranty_until.is_not(None), CustomerAsset.status == "activo")
                )
                if a.warranty_until and date.today() <= a.warranty_until <= limite
            ]
            if not avisos:
                continue
            filas = "".join(
                f"<li>{a.name} · {a.serial or 's/n'} · {db.get(Customer, a.customer_id).name if db.get(Customer, a.customer_id) else ''} · vence {a.warranty_until}</li>"
                for a in avisos[:30]
            )
            for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.role_code.in_(("admin", "supervisor")), TenantUser.active)):
                queue_email(db, t, m.user.email, f"{len(avisos)} garantías vencen en los próximos 45 días", f"<ul>{filas}</ul>", "asset", avisos[0].id)
                n += 1
        db.commit()
    return n


@celery.task(name="support.maintenance_due")
def maintenance_due() -> int:
    """Mantenimiento preventivo: "este cliente requiere mantenimiento cada 6 meses". Cuando toca, el ticket
    se abre solo y el contrato reprograma su proxima fecha. Nadie tiene que acordarse."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant
    from app.routers.support_desk import due_contracts, open_maintenance
    from app.services.mail import notify_roles

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            abiertos = []
            for c in due_contracts(db, t.id):
                ticket = open_maintenance(db, c)
                db.flush()
                abiertos.append((c, ticket))
                n += 1
            if abiertos:
                filas = "".join(f"<li>{tk.number} · {c.name} · cada {c.every_months} meses</li>" for c, tk in abiertos)
                notify_roles(
                    db, t, ("admin", "supervisor"), f"{len(abiertos)} mantenimiento(s) para programar", f"<ul>{filas}</ul>", "support_ticket", abiertos[0][1].id
                )
        db.commit()
    return n


@celery.task(name="support.sla_breaches")
def sla_breaches() -> int:
    """Ticket sin primera respuesta dentro del plazo -> aviso. Es la promesa que Crimson le puede hacer
    a Hikvision: sin medirla, "soporte nivel 1" no significa nada."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import SupportTicket, Tenant
    from app.routers.support_desk import OPEN_STATES
    from app.services.mail import notify_roles

    n = 0
    ahora = datetime.now(UTC)
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            vencidos = [
                x
                for x in db.scalars(select(SupportTicket).where(SupportTicket.tenant_id == t.id, SupportTicket.status.in_(OPEN_STATES)))
                if x.due_at and not x.first_reply_at and (x.due_at if x.due_at.tzinfo else x.due_at.replace(tzinfo=UTC)) < ahora
            ]
            if not vencidos:
                continue
            filas = "".join(f"<li>{x.number} · {x.subject} · prioridad {x.priority}</li>" for x in vencidos[:20])
            notify_roles(
                db, t, ("admin", "supervisor"), f"{len(vencidos)} ticket(s) sin primera respuesta", f"<ul>{filas}</ul>", "support_ticket", vencidos[0].id
            )
            n += len(vencidos)
        db.commit()
    return n


@celery.task(name="crm.stale_quotes")
def stale_quotes() -> int:
    """Cotizacion enviada hace 5 dias sin respuesta -> se le avisa a quien la hizo. Textual de Andres:
    "cotizacion lleva cinco días sin seguimiento, que se le avise a la vendedora"."""
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Customer, Quote, Tenant, User
    from app.services.mail import queue_email

    n = 0
    corte = date.today() - timedelta(days=5)
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            frias = db.scalars(select(Quote).where(Quote.tenant_id == t.id, Quote.status == "enviada", Quote.issue_date <= corte)).all()
            por_persona: dict[int, list] = {}
            for q in frias:
                por_persona.setdefault(q.created_by or 0, []).append(q)
            for uid, items in por_persona.items():
                u = db.get(User, uid) if uid else None
                if not u or not u.email:
                    continue
                filas = "".join(
                    f"<li><b>{q.number}</b> · {(db.get(Customer, q.customer_id).name if q.customer_id else 'sin cliente')} · {q.currency} {q.total:,.0f} · enviada el {q.issue_date}</li>"
                    for q in items[:20]
                )
                queue_email(
                    db,
                    t,
                    u.email,
                    f"{len(items)} cotizaciones sin respuesta",
                    f"<p>Llevan más de 5 días enviadas:</p><ul>{filas}</ul><p>Una llamada a tiempo es la diferencia.</p>",
                    "quote",
                    items[0].id,
                )
                n += len(items)
        db.commit()
    return n


celery.conf.beat_schedule = {
    "poll-inboxes": {"task": "reception.poll_inboxes", "schedule": crontab(minute="*/15")},
    "expire-support": {"task": "support.expire_grants", "schedule": crontab(minute=5)},
    "stale-followups": {"task": "crm.stale_followups", "schedule": crontab(hour=7, minute=30, day_of_week="mon-fri")},
    "low-stock-requests": {"task": "inventory.low_stock_requests", "schedule": crontab(hour=6, minute=45)},
    "warranty-alerts": {"task": "assets.warranty_alerts", "schedule": crontab(hour=7, minute=0, day_of_week="mon")},
    "maintenance-due": {"task": "support.maintenance_due", "schedule": crontab(hour=6, minute=30)},
    "sla-breaches": {"task": "support.sla_breaches", "schedule": crontab(minute=0)},
    "stale-quotes": {"task": "crm.stale_quotes", "schedule": crontab(hour=7, minute=45, day_of_week="mon-fri")},
    "reminders": {"task": "sales.reminders", "schedule": crontab(hour=8, minute=0)},
    "daily-close": {"task": "sales.daily_close_email", "schedule": crontab(hour=21, minute=0)},
    "run-recurrences": {"task": "sales.run_recurrences", "schedule": crontab(hour=5, minute=0)},
    "retry-outbox": {"task": "mail.retry_outbox", "schedule": crontab(minute="*/15")},
    "fx-bccr-daily": {"task": "fx.bccr_daily", "schedule": crontab(hour=6, minute=15)},
    "mark-overdue": {"task": "sales.mark_overdue", "schedule": crontab(hour=0, minute=30)},
}
