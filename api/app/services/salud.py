"""Salud de la plataforma (plan 1.8): lo que mira GET /health/deep y lo que resume el correo diario de Salud.

Cada revision devuelve {"ok": bool, "critico": bool, ...datos sin informacion sensible} (nada de montos, nombres,
correos ni llaves: el endpoint es publico para el monitor externo). Si una revision critica falla, /health/deep da 503.

Criticas (la plataforma no esta funcionando): la base de datos, el latido del beat y el worker (menos de 15 min) y,
en produccion, el correo configurado en el worker (sin RESEND_API_KEY ningun aviso le llega a nadie).
Avisos (se ven en el correo de Salud y en el JSON, sin tumbar el monitor): existencias de Eurocomp de mas de 8 h,
correos "fallido" en 24 h, tipo de cambio de mas de 2 dias habiles, ENV distinto de produccion, la tienda ofreciendo
tarjeta sin llave ONVO live en produccion (o una llave live fuera de produccion) y la cola "Por emitir" con facturas de
mas de 24 h.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import EmailOutbox, ExchangeRate, Invoice, TaskRun, Tenant

HEARTBEAT_TASK = "salud.latido"
HEARTBEAT_MAX = timedelta(minutes=15)
EUROCOMP_STOCK_MAX = timedelta(hours=8)  # se refresca cada 6 h (05, 11, 17, 23 CR): 2 h de holgura
FX_MAX_BUSINESS_DAYS = 2
POR_EMITIR_MAX = timedelta(hours=24)
# "Por emitir" (plan 1.7): facturas pagadas o enviadas que no tienen comprobante electronico
POR_EMITIR_STATES = ("enviada", "pagada", "parcial")


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse(iso: str | None) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(iso)) if iso else None
    except ValueError:
        return None


def business_days_between(a: date, b: date) -> int:
    """Dias habiles (lunes a viernes) despues de `a` hasta `b` inclusive."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def last_heartbeat(db: Session) -> TaskRun | None:
    return db.scalar(
        select(TaskRun).where(TaskRun.task == HEARTBEAT_TASK, TaskRun.ok.is_(True)).order_by(TaskRun.started_at.desc(), TaskRun.id.desc()).limit(1)
    )


# ---------- revisiones ----------
def check_beat(db: Session, now: datetime) -> dict:
    hb = last_heartbeat(db)
    if hb is None:
        return {"ok": False, "critico": True, "detalle": "sin latido del worker"}
    age = now - (_aware(hb.finished_at or hb.started_at) or now)
    return {"ok": age < HEARTBEAT_MAX, "critico": True, "edad_min": int(age.total_seconds() // 60)}


def check_worker_config(db: Session) -> tuple[dict, dict]:
    """(correo del worker, configuracion del worker), segun el ultimo latido (lo que el worker ve, no el api)."""
    hb = last_heartbeat(db)
    s = (hb.summary or {}) if hb else {}
    faltan = list(s.get("faltan") or [])
    if hb is None:
        return {"ok": False, "critico": False, "detalle": "sin latido"}, {"ok": False, "critico": False, "detalle": "sin latido"}
    correo = {"ok": "RESEND_API_KEY" not in faltan, "critico": settings.is_prod}
    config = {"ok": not faltan, "critico": False, "faltan": faltan}
    if s.get("env") and s.get("env") != settings.env:
        config.update(ok=False, worker_env=s.get("env"))  # api y worker con ENV distinto
    return correo, config


def check_env() -> dict:
    return {"ok": settings.is_prod, "critico": False, "valor": settings.env}


def check_eurocomp(db: Session, now: datetime) -> dict:
    edades = []
    for t in db.scalars(select(Tenant).where(Tenant.active)):
        st = ((t.settings or {}).get("supplier_sync") or {}).get("eurocomp") or {}
        if not st:
            continue
        last = _parse(st.get("last_stock_at"))
        edades.append(now - last if last else None)
    if not edades:
        return {"ok": True, "critico": False, "aplica": False}
    if any(e is None for e in edades):
        return {"ok": False, "critico": False, "detalle": "existencias nunca refrescadas"}
    peor = max(edades)
    return {"ok": peor < EUROCOMP_STOCK_MAX, "critico": False, "edad_h": round(peor.total_seconds() / 3600, 1)}


def check_outbox(db: Session, now: datetime, tenant_id: int | None = None) -> dict:
    q = select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "fallido", EmailOutbox.created_at >= now - timedelta(hours=24))
    if tenant_id is not None:
        q = q.where(EmailOutbox.tenant_id == tenant_id)
    n = db.scalar(q) or 0
    return {"ok": n == 0, "critico": False, "fallidos_24h": n}


def check_fx(db: Session, today: date) -> dict:
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.currency == "USD", ExchangeRate.date <= today).order_by(ExchangeRate.date.desc()).limit(1))
    if fx is None:
        return {"ok": False, "critico": False, "detalle": "sin tipo de cambio"}
    habiles = business_days_between(fx.date, today)
    return {"ok": habiles <= FX_MAX_BUSINESS_DAYS, "critico": False, "fecha": fx.date.isoformat(), "fuente": fx.source, "dias_habiles": habiles}


def check_onvo(db: Session) -> dict:
    """Modo de la llave ONVO activa de cada empresa (nunca la llave) y si la tienda ofrece tarjeta.
    La tarjeta apagada en Mi Tienda es una decision, no un problema (asi no avisa todos los dias). En produccion avisa
    si la tienda tiene la tarjeta encendida sin una llave live (el cliente no la ve: venta perdida); fuera de
    produccion, ante cualquier llave live (cada pago cobraria dinero real)."""
    from . import onvo
    from .store_orders import checkout_cfg

    modos, bad, tarjeta = [], False, False
    for t in db.scalars(select(Tenant).where(Tenant.active)):
        modo = onvo.status(db, t).get("mode")  # llave de la pasarela activa (o la de entorno de esa empresa)
        on = bool((checkout_cfg(t)["methods"].get("tarjeta") or {}).get("active"))
        tarjeta = tarjeta or on
        if modo:
            modos.append(modo)
        bad = bad or ((on and modo != "live") if settings.is_prod else modo == "live")
    if not modos:
        modo = "sin_llave"
    else:
        modo = "live" if all(m == "live" for m in modos) else ("test" if all(m == "test" for m in modos) else "mixto")
    return {"ok": not bad, "critico": False, "modo": modo, "tarjeta": "activa" if tarjeta else "apagada"}


def check_por_emitir(db: Session, now: datetime, tenant_id: int | None = None) -> dict:
    q = (
        select(func.count())
        .select_from(Invoice)
        .where(Invoice.einvoice_status == "sin_emitir", Invoice.status.in_(POR_EMITIR_STATES), Invoice.created_at < now - POR_EMITIR_MAX)
    )
    if tenant_id is not None:
        q = q.where(Invoice.tenant_id == tenant_id)
    n = db.scalar(q) or 0
    return {"ok": n == 0, "critico": False, "mas_de_24h": n}


def deep(db: Session, now: datetime | None = None, today: date | None = None) -> dict:
    """Todas las revisiones. status: ok | aviso | falla (falla = alguna critica; el endpoint responde 503)."""
    from ..core.timeutil import cr_today

    now = now or datetime.now(UTC)
    today = today or cr_today()
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001  sin base no hay nada mas que revisar
        return {"status": "falla", "env": settings.env, "checks": {"db": {"ok": False, "critico": True}}, "at": now.isoformat()}
    correo, config = check_worker_config(db)
    checks = {
        "db": {"ok": True, "critico": True},
        "beat": check_beat(db, now),
        "correo_worker": correo,
        "config_worker": config,
        "env": check_env(),
        "eurocomp_existencias": check_eurocomp(db, now),
        "correo_fallido": check_outbox(db, now),
        "tipo_cambio": check_fx(db, today),
        "onvo": check_onvo(db),
        "por_emitir": check_por_emitir(db, now),
    }
    if any(c["critico"] and not c["ok"] for c in checks.values()):
        status = "falla"
    elif all(c["ok"] for c in checks.values()):
        status = "ok"
    else:
        status = "aviso"
    return {"status": status, "env": settings.env, "checks": checks, "at": now.isoformat()}
