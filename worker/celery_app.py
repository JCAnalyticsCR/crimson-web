"""Worker Celery + beat. Se ejecuta con el mismo codigo de api/ (PYTHONPATH=../api).

Tareas planificadas (plan 6.2): tipo de cambio BCCR diario, recordatorios de vencimiento,
cierre de caja diario, emision fiscal en contingencia (Fase 2), conciliacion ONVO (Fase 3).
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging as celery_setup_logging

from app.core.config import settings
from app.core.logging import setup_logging
from app.tasks import fiscal as _tareas_fiscales  # noqa: F401  cola "Por emitir" (plan 1.7)
from app.tasks.salud import connect_signals  # tambien registra las tareas salud.* (shared_task)

log = logging.getLogger("crimson.worker")
# plan 1.8: antes de las lineas de arranque de abajo (kid de FISCAL_KEYS, configuracion faltante), que salen al importar
# este modulo y antes de la señal de Celery: sin esto el INFO se perdia y el WARNING salia sin nivel ni logger. La
# señal lo vuelve a aplicar (idempotente) con el nivel de la linea de comando.
setup_logging()


@celery_setup_logging.connect
def _logging(loglevel=None, **_kw) -> None:
    """Plan 1.8: INFO a stdout y WARNING+ a stderr (Celery mandaba todo a stderr y Railway lo marcaba como error).
    Con esta señal conectada Celery no toca el logging ni redirige stdout."""
    from app.core.logging import setup_logging

    setup_logging(loglevel)


connect_signals()  # plan 1.8: cada corrida queda en task_run y un fallo avisa (uno por tarea y por dia)

celery = Celery("crimson", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(timezone="America/Costa_Rica", enable_utc=True, task_acks_late=True, worker_prefetch_multiplier=1)
# Un solo beat (plan 1.2): horario y candado en Redis (celery-redbeat). Durante un despliegue conviven dos workers con
# su beat embebido: el nuevo espera el candado y ninguna tarea sale dos veces; el horario ya no vive en /tmp. Si un
# contenedor muere sin soltarlo, el candado vence solo a los 5 x el intervalo maximo del beat (que lo renueva en cada
# vuelta). En el worker la opcion es --scheduler (su -S es --statedb); aqui queda como valor por defecto.
BEAT_MAX_INTERVAL = 60
celery.conf.update(
    beat_scheduler="redbeat.RedBeatScheduler",
    beat_max_loop_interval=BEAT_MAX_INTERVAL,
    redbeat_redis_url=settings.redis_url,
    redbeat_lock_timeout=5 * BEAT_MAX_INTERVAL,
)

import app.tasks.catalogo  # noqa: E402,F401  publicacion automatica del catalogo de Eurocomp (catalog.auto_catalog)
import app.tasks.margen  # noqa: E402,F401  guardian de margen (plan 1.12): margin.guard y margin.daily_summary


def _log_fiscal_keys() -> None:
    """Solo kid y sha256(llave)[:8] de FISCAL_KEYS (deben ser los mismos que registra la API)."""
    from app.core import secretbox

    secretbox.keys()


_log_fiscal_keys()

# Tareas por area (api/app/tasks/<area>.py, registradas con shared_task): aqui solo se importan y se agendan.
import app.tasks.cobranza  # noqa: E402,F401  sales.mark_overdue, sales.reminders, cobranza.resumen_semanal (plan 1.3)
import app.tasks.mensualidades  # noqa: E402,F401  mensualidades.vista_previa (plan 1.4)
import app.tasks.tienda  # noqa: E402,F401  tareas de la tienda (recordatorio de pago, consulta a ONVO): plan 1.5


def _log_missing_config() -> None:
    """WARNING con los NOMBRES de la configuracion que falta (RESEND_API_KEY, MAIL_FROM, PUBLIC_BASE_URL, BCCR_TOKEN,
    FISCAL_KEYS): sin ellas el worker arranca igual y lo automatico no le llega a nadie."""
    from app.core.envcheck import log_missing_config

    log_missing_config(log)


_log_missing_config()


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


def _run_tool(module: str, timeout: int) -> dict:
    """Lanza python -m <module> en un proceso hijo con subprocess.run y devuelve la ultima linea JSON.
    El SO recupera TODA la RAM del hijo al terminar: el worker no crece con el catalogo.
    Si el proceso falla, levanta RuntimeError con el final de stderr."""
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=None,  # hereda el cwd del worker (donde PYTHONPATH=../api esta seteado)
    )
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip()[-500:]
        raise RuntimeError(f"{module} fallo (codigo {result.returncode}): {stderr_tail}")
    last_line = (result.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        return json.loads(last_line)
    except Exception:  # noqa: BLE001
        return {"raw": last_line}


@celery.task(name="suppliers.sync_eurocomp")
def suppliers_sync_eurocomp() -> dict:
    """Catalogo completo de Eurocomp en proceso hijo (lunes 03:17). El SO recupera la RAM al terminar.
    Sin credenciales no hace nada; un fallo queda en el estado que ve el catalogo de proveedores."""
    return _run_tool("app.tools.eurocomp_sync", timeout=3600)


@celery.task(name="suppliers.eurocomp_stock")
def suppliers_eurocomp_stock() -> dict:
    """Cada 6 horas: existencia y costo de lo que Crimson ya vende, en proceso hijo."""
    return _run_tool("app.tools.eurocomp_stock", timeout=900)


@celery.task(name="store.rebuild_snapshot")
def rebuild_snapshot(tenant_id: int) -> dict:
    """Rearma el snapshot de la tienda para una empresa. Se encola con countdown 30 s para debounce:
    si llegan varios cambios seguidos solo se ejecuta el ultimo. El snapshot es el JSON gz que sirve
    el endpoint /snapshot sin tocar ningun producto ORM por peticion."""
    from app.core.db import SessionLocal
    from app.services import store_snapshot

    with SessionLocal() as db:
        store_snapshot.rebuild(db, tenant_id)
    return {"tenant_id": tenant_id, "ok": True}


@celery.task(name="sales.run_recurrences")
def run_recurrences() -> int:
    from app.core.db import SessionLocal
    from app.routers.ops import run_due_recurrences

    with SessionLocal() as db:
        return run_due_recurrences(db)


@celery.task(name="mail.retry_outbox")
def retry_outbox() -> dict:
    """Reintentos con espera creciente y tope (app/tasks/correo.py); nunca sin adjuntos ni invitaciones."""
    from app.core.db import SessionLocal
    from app.tasks.correo import retry_outbox as run

    with SessionLocal() as db:
        return run(db)


@celery.task(name="sales.daily_close_email")
def daily_close_email() -> int:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.timeutil import cr_today
    from app.models import Tenant, TenantUser
    from app.services.mail import member_inbox, queue_email
    from app.services.reports import cierre_diario

    n = 0
    hoy = cr_today()  # corre a las 21:00 CR = 03:00 UTC del dia siguiente: date.today() reportaba "mañana"
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            if not (t.settings or {}).get("daily_close_email"):
                continue
            rep = cierre_diario(db, t.id, hoy, hoy)
            rows = (
                "".join(f"<tr><td>{r[1]}</td><td>{r[2]}</td><td align=right>{r[3]}</td><td align=right>{r[4]}</td></tr>" for r in rep.rows)
                or "<tr><td colspan=4>Sin cobros hoy</td></tr>"
            )
            html = f"<h3>Cierre de caja {hoy}</h3><table border=0 cellpadding=6><tr><th>Método</th><th>Divisa</th><th>Monto</th><th>Pagos</th></tr>{rows}</table><p><b>Total: {rep.totals['total'] if rep.totals else 0}</b></p>"
            for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.role_code == "admin", TenantUser.active)):
                correo = member_inbox(m)  # su correo de avisos si el admin le puso uno
                if not correo:
                    continue
                # un solo cierre por dia y persona aunque la tarea corra dos veces
                queue_email(db, t, correo, f"Cierre de caja · {t.name} · {hoy}", html, "report", 0, dedupe_key=f"cierre:{hoy}:{m.user_id}")
                n += 1
        db.commit()
    return n


@celery.task(name="reception.poll_inboxes")
def poll_inboxes() -> int:
    """Buzones de recepcion (entorno o Ajustes): cada correo se confirma solo; arriendo tomado -> se salta."""
    from app.core.db import SessionLocal
    from app.services.hacienda_tasks import poll_inboxes as run

    with SessionLocal() as db:
        return run(db)


@celery.task(name="hacienda.send_pending")
def hacienda_send_pending() -> dict:
    """Mensajes receptor y comprobantes por enviar (reclamo atomico: dos workers no mandan lo mismo) + entregas al cliente."""
    from app.core.db import SessionLocal
    from app.services.hacienda_sync import send_pending

    with SessionLocal() as db:
        return send_pending(db)


@celery.task(name="hacienda.poll_status")
def hacienda_poll_status() -> dict:
    """Consulta lo enviado (1, 2, 5, 10, 20, 30 min y luego cada hora) y recupera filas atascadas en "enviando"."""
    from app.core.db import SessionLocal
    from app.services.hacienda_sync import poll_status

    with SessionLocal() as db:
        return poll_status(db)


@celery.task(name="hacienda.daily_alerts")
def hacienda_daily_alerts() -> int:
    from app.core.db import SessionLocal
    from app.services.hacienda_tasks import daily_alerts

    with SessionLocal() as db:
        return daily_alerts(db)


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
    from app.services.mail import queue_email, user_inbox

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            por_persona: dict[int, list] = {}
            for o in find(db, t.id, days=5):
                por_persona.setdefault(o.owner_id or 0, []).append(o)
            for uid, items in por_persona.items():
                u = db.get(User, uid) if uid else None
                correo = user_inbox(db, t.id, u) if u else ""
                if not correo:
                    continue
                filas = "".join(
                    f"<li><b>{o.number}</b> · {o.title} · {o.status}{' · vence ' + o.next_action_date.isoformat() if o.next_action_date else ''}</li>"
                    for o in items[:20]
                )
                queue_email(
                    db,
                    t,
                    correo,
                    f"{len(items)} oportunidades esperan seguimiento",
                    f"<p>Estas oportunidades tienen la próxima acción para hoy, vencida o llevan días sin movimiento:</p><ul>{filas}</ul><p>Abra el panel y registre el siguiente paso.</p>",
                    "opportunity",
                    items[0].id,
                )
                n += len(items)
        db.commit()
    return n


@celery.task(name="inventory.health_check")
def inventory_health_check() -> dict:
    """Cada manana: documentos contra kardex. Si algo descuadra, correo a los administradores con la lista."""
    import html

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant
    from app.services import inventory_health
    from app.services.mail import notify_roles

    out = {}
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            try:
                r = inventory_health.check(db, t.id)
            except Exception:  # noqa: BLE001  una empresa con datos raros no frena a las demas
                log.exception("salud del inventario (empresa %s)", t.id)
                continue
            out[t.id] = r["total"]
            if not r["total"]:
                continue
            filas = "".join(
                f"<li><b>{html.escape(i['doc'])}</b> · {html.escape(i['product'])}: {html.escape(i['detail'])}</li>" for i in r["issues"][:25]
            )
            mas = f"<p>… y {r['total'] - 25} más.</p>" if r["total"] > 25 else ""
            notify_roles(
                db,
                t,
                ("admin",),
                f"Inventario: {r['total']} descuadre(s) para revisar",
                f"<p>La revisión de esta mañana encontró diferencias entre los documentos y el kardex:</p><ul>{filas}</ul>{mas}"
                "<p>Se corrigen desde Inventarios → Salud del inventario.</p>",
                "inventory",
                0,
            )
            db.commit()
    return out


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


@celery.task(name="inventory.expire_reservations")
def expire_reservations() -> int:
    """Reservas de material vencidas (pendientes o confirmadas) -> vencida: liberan lo comprometido.
    Primero las de pedidos web sin pagar (dejan nota en el pedido y, si Ajustes lo pide, lo cancelan)."""
    from app.core.db import SessionLocal
    from app.services.reservations import expire_due
    from app.services.store_inventory import expire_holds

    with SessionLocal() as db:
        n = expire_holds(db)
        n += expire_due(db)
    if n:
        log.info("Reservas: %s vencida(s)", n)
    return n


@celery.task(name="store.back_in_stock")
def back_in_stock() -> int:
    """Tienda: "Avisarme cuando haya" -> correo cuando el producto vuelve a estar disponible."""
    from app.core.db import SessionLocal
    from app.services.store_inventory import back_in_stock as notify

    with SessionLocal() as db:
        return notify(db)


@celery.task(name="assets.warranty_alerts")
def warranty_alerts() -> int:
    """Equipo instalado que cumple 11 meses -> aviso de garantia proxima a vencer."""
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Customer, CustomerAsset, Tenant, TenantUser
    from app.services.mail import member_inbox, queue_email

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
                correo = member_inbox(m)  # su correo de avisos si el admin le puso uno
                if not correo:
                    continue
                queue_email(db, t, correo, f"{len(avisos)} garantías vencen en los próximos 45 días", f"<ul>{filas}</ul>", "asset", avisos[0].id)
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
    """Atrasos de SLA (primera respuesta Y resolucion) -> aviso a admin/supervisor y al tecnico asignado.
    Es la promesa que Crimson le puede hacer a Hikvision: sin medirla, "soporte nivel 1" no significa nada.
    Cada ticket avisa una sola vez por plazo (ver app/services/sla.avisar_atrasos)."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant
    from app.services.sla import avisar_atrasos

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)):
            n += avisar_atrasos(db, t)
        db.commit()
    return n


# crm.stale_quotes (cotizaciones frias a su vendedora, plan maestro 1.6) vive en app/tasks/ventas.py (shared_task)
import app.tasks.ventas  # noqa: E402,F401


@celery.task(name="archive.purge_trash")
def purge_trash() -> int:
    """Borra definitivamente lo que cumplio los N dias en la papelera (archive_purge_days por empresa).
    Cada purga queda en la auditoria con quien lo mando a la papelera y cuando."""
    from app.core.db import SessionLocal
    from app.services.archive import purge_expired

    with SessionLocal() as db:
        n = purge_expired(db)
    log.info("Papelera: %s registro(s) purgado(s)", n)
    return n


celery.conf.beat_schedule = {
    "purge-trash": {"task": "archive.purge_trash", "schedule": crontab(hour=3, minute=20)},
    "poll-inboxes": {"task": "reception.poll_inboxes", "schedule": crontab(minute="*/15")},
    "hacienda-send-pending": {"task": "hacienda.send_pending", "schedule": crontab(minute="*/2")},
    "hacienda-poll-status": {"task": "hacienda.poll_status", "schedule": crontab(minute="*/5")},
    "hacienda-daily-alerts": {"task": "hacienda.daily_alerts", "schedule": crontab(hour=7, minute=10)},
    "expire-support": {"task": "support.expire_grants", "schedule": crontab(minute=5)},
    "stale-followups": {"task": "crm.stale_followups", "schedule": crontab(hour=7, minute=30, day_of_week="mon-fri")},
    "low-stock-requests": {"task": "inventory.low_stock_requests", "schedule": crontab(hour=6, minute=45)},
    "inventory-health": {"task": "inventory.health_check", "schedule": crontab(hour=6, minute=50)},
    # cada 5 min: las reservas de pago con tarjeta duran 30 min
    "expire-reservations": {"task": "inventory.expire_reservations", "schedule": crontab(minute="*/5")},
    "back-in-stock": {"task": "store.back_in_stock", "schedule": crontab(minute=25)},
    "store-payment-reminders": {"task": "store.payment_reminders", "schedule": crontab(minute="8,23,38,53")},
    "store-sync-card-orders": {"task": "store.sync_card_orders", "schedule": crontab(minute="4,19,34,49")},
    "warranty-alerts": {"task": "assets.warranty_alerts", "schedule": crontab(hour=7, minute=0, day_of_week="mon")},
    "maintenance-due": {"task": "support.maintenance_due", "schedule": crontab(hour=6, minute=30)},
    "sla-breaches": {"task": "support.sla_breaches", "schedule": crontab(minute=0)},
    "stale-quotes": {"task": "crm.stale_quotes", "schedule": crontab(hour=7, minute=45, day_of_week="mon-fri")},
    "reminders": {"task": "sales.reminders", "schedule": crontab(hour=8, minute=0)},
    "daily-close": {"task": "sales.daily_close_email", "schedule": crontab(hour=21, minute=0)},
    "run-recurrences": {"task": "sales.run_recurrences", "schedule": crontab(hour=5, minute=0)},
    "retry-outbox": {"task": "mail.retry_outbox", "schedule": crontab(minute="*/15")},
    "fx-bccr-daily": {"task": "fx.bccr_daily", "schedule": crontab(hour=6, minute=15)},
    # catalogo completo (34 MB) los lunes de madrugada; existencias de lo vinculado cada 6 horas. Minuto 17: lejos del
    # :00 de las demas tareas y antes del :25 de back-in-stock (avisos de "volvio a haber")
    "suppliers-sync-eurocomp": {"task": "suppliers.sync_eurocomp", "schedule": crontab(day_of_week=1, hour=3, minute=17)},
    "suppliers-eurocomp-stock": {"task": "suppliers.eurocomp_stock", "schedule": crontab(hour="5,11,17,23", minute=17)},
    "mark-overdue": {"task": "sales.mark_overdue", "schedule": crontab(hour=0, minute=30)},
    # plan 1.8: latido cada 5 min (/health/deep da 503 si pasan 15 sin latido) y correo de Salud a las 07:00
    "salud-latido": {"task": "salud.latido", "schedule": crontab(minute="*/5")},
    "salud-correo-diario": {"task": "salud.correo_diario", "schedule": crontab(hour=7, minute=0)},
    "fiscal-por-emitir": {"task": "fiscal.por_emitir_diario", "schedule": crontab(hour=8, minute=30)},
    # despues de la tasa del BCCR (6:15) y del refresco de Eurocomp (5:17), que ya corren el guardian
    "margin-daily-summary": {"task": "margin.daily_summary", "schedule": crontab(hour=7, minute=5)},
    "cobranza-resumen-semanal": {"task": "cobranza.resumen_semanal", "schedule": crontab(hour=7, minute=0, day_of_week="mon")},
    "mensualidades-vista-previa": {"task": "mensualidades.vista_previa", "schedule": crontab(hour=7, minute=20)},
}
