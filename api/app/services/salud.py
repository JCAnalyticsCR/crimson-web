"""Salud de la plataforma (plan 1.8): lo que mira GET /health/deep y lo que resume el correo diario de Salud.

Cada revision devuelve {"ok": bool, "critico": bool, ...datos sin informacion sensible} (nada de montos, nombres,
correos ni llaves). Si una revision critica falla, /health/deep da 503. Sin X-Health-Token (HEALTH_TOKEN) el endpoint
solo dice el estado y el ENV (public_view, lo mismo que ya publica /health): el monitor de palabra clave de 0.8 busca
`"env":"production"`; el detalle es un mapa de la configuracion y no es para cualquiera en internet.
El resultado se guarda DEEP_CACHE_SECONDS (DeepCache): el monitor pasa cada pocos minutos y cada revision cuesta
consultas (y descifrar la llave de ONVO de cada empresa).

Criticas (la plataforma no esta funcionando): la base de datos, el latido del beat y el worker (menos de 15 min) y,
en produccion, el correo configurado en el worker (sin RESEND_API_KEY ningun aviso le llega a nadie). El worker tiene
un solo proceso: si una tarea larga (catalogo de Eurocomp, buzon IMAP) deja el latido esperando en la cola, cuenta como
vivo mientras el beat siga despachando (su fila salud.beat) y el worker este trabajando (una corrida en curso de menos
de 1 h o una que termino hace menos de 15 min).
Avisos (se ven en el correo de Salud y en el JSON, sin tumbar el monitor): existencias de Eurocomp de mas de 8 h,
correos "fallido" en 24 h, tipo de cambio de mas de 2 dias habiles, ENV distinto de produccion, la tienda ofreciendo
tarjeta sin llave ONVO live en produccion (o una llave live fuera de produccion) y la cola "Por emitir" con facturas de
mas de 24 h.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from threading import Lock

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import EmailOutbox, ExchangeRate, Invoice, TaskRun, Tenant

HEARTBEAT_TASK = "salud.latido"
BEAT_TASK = "salud.beat"  # fila que deja el beat al despachar el latido (no es una tarea): el beat, medido aparte
HEARTBEAT_MAX = timedelta(minutes=15)
BUSY_MAX = timedelta(hours=1)  # una corrida sin terminar de mas de 1 h ya no es "ocupado": el proceso murio
DEEP_CACHE_SECONDS = 30
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


def _last(db: Session, task: str) -> TaskRun | None:
    return db.scalar(select(TaskRun).where(TaskRun.task == task, TaskRun.ok.is_(True)).order_by(TaskRun.started_at.desc(), TaskRun.id.desc()).limit(1))


def last_heartbeat(db: Session) -> TaskRun | None:
    return _last(db, HEARTBEAT_TASK)


def _age(r: TaskRun | None, now: datetime) -> timedelta | None:
    return None if r is None else now - (_aware(r.finished_at or r.started_at) or now)


_NOT_TASKS = (HEARTBEAT_TASK, BEAT_TASK)


def _worker_busy(db: Session, now: datetime) -> bool:
    """El worker esta trabajando: una corrida en curso de menos de BUSY_MAX o una que termino hace menos de 15 min."""
    running = db.scalar(
        select(TaskRun.id)
        .where(TaskRun.task.not_in(_NOT_TASKS), TaskRun.ok.is_(None), TaskRun.finished_at.is_(None), TaskRun.started_at >= now - BUSY_MAX)
        .limit(1)
    )
    if running is not None:
        return True
    return db.scalar(select(TaskRun.id).where(TaskRun.task.not_in(_NOT_TASKS), TaskRun.finished_at >= now - HEARTBEAT_MAX).limit(1)) is not None


# ---------- revisiones ----------
def check_beat(db: Session, now: datetime) -> dict:
    hb_age = _age(last_heartbeat(db), now)
    if hb_age is not None and hb_age < HEARTBEAT_MAX:
        return {"ok": True, "critico": True, "edad_min": int(hb_age.total_seconds() // 60)}
    out = {"ok": False, "critico": True}
    out.update({"edad_min": int(hb_age.total_seconds() // 60)} if hb_age is not None else {"detalle": "sin latido del worker"})
    # el latido puede estar esperando detras de una tarea larga (un solo proceso): vale si el beat sigue despachando y
    # el worker trabaja. Si el contenedor se detuvo, el beat (embebido) tampoco despacha: 503 en menos de 15 min.
    beat_age = _age(_last(db, BEAT_TASK), now)
    if beat_age is not None and beat_age < HEARTBEAT_MAX and _worker_busy(db, now):
        out.update(ok=True, detalle="worker ocupado: el latido espera en la cola y el beat sigue despachando")
    return out


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
        # solo si el refresco de existencias corrio alguna vez: el catalogo semanal (last_ok_at) o un error de
        # credenciales no lo son, y sin productos vinculados (o con la integracion pospuesta) no hay nada que refrescar
        if not (st.get("last_stock_at") or st.get("last_stock_try_at")):
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
    """Correos que en las ultimas 24 h quedaron "fallido" (por la hora en que se dejaron, updated_at: los que vencieron
    en la cola tienen created_at de mas de 24 h). Los "reemplazados" (el vendedor ya lo reenvio) no cuentan."""
    q = select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "fallido", EmailOutbox.updated_at >= now - timedelta(hours=24))
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


def public_view(rep: dict) -> dict:
    """Lo que ve cualquiera sin X-Health-Token: el estado, el ENV y la hora. El monitor externo de 0.8 es de palabra
    clave (`"env":"production"`, el mismo dato que ya publica /health) y un 503 cuenta como caida."""
    return {"status": rep["status"], "env": rep["env"], "at": rep["at"]}


class DeepCache:
    """deep() guardado unos segundos. Una sola consulta a la vez: una rafaga espera el resultado en vez de recalcularlo."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._lock = Lock()
        self._at: float | None = None
        self._value: dict | None = None

    def get(self, compute) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._value is None or self._at is None or now - self._at >= self.seconds:
                self._value, self._at = compute(), now
            return self._value
