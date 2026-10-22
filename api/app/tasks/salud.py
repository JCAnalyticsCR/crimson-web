"""Salud de la plataforma del lado del worker (plan 1.8).

- Corridas: las señales de Celery (task_prerun, task_postrun, task_failure) dejan cada corrida en task_run (inicio,
  fin, ok, resumen, error). Un reintento programado (autoretry) no es una falla: queda ok=None con su hora de fin y
  {"reintento": true}; la falla es solo el estado final (task_failure). Un fallo manda correo a quien opera la
  plataforma, como maximo uno por tarea y por dia de Costa Rica (dedupe_key). Registrar nunca rompe la tarea: un error
  aqui solo va al log.
- Latido (salud.latido, cada 5 min): el worker deja su fila con los NOMBRES de la configuracion que le falta y su
  ENV. El beat, al despachar el latido, deja la suya (salud.beat, señal after_task_publish): asi el beat se mide aparte
  y una tarea larga que deja el latido en la cola no da un 503 falso. Si el contenedor se detiene, /health/deep da 503
  en menos de 15 min.
- Correo diario de Salud (salud.correo_diario, 07:00 CR): las revisiones de /health/deep, las tareas que fallaron en
  24 h y el correo de la empresa operadora, a quien opera la plataforma. Los administradores de las demas empresas
  reciben solo el bloque de la suya (conteos filtrados por empresa; nunca errores de tareas ni conteos globales).
  Purga las corridas de mas de 30 dias.

Quien opera la plataforma: los administradores de la empresa OPS_TENANT (slug; si no esta, la primera empresa activa) y
OPS_ALERT_EMAILS (correos separados por coma). Variables de entorno; nunca en el codigo.
Los textos de error no llevan parametros de URL, llaves, correos, numeros largos (cedulas) ni el DETAIL de Postgres.

Las dos tareas (salud.latido, salud.correo_diario) son shared_task: worker/celery_app.py solo importa este modulo y
pone sus entradas en el beat (regla de integracion 4).
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.timeutil import cr_today
from ..models import EmailOutbox, TaskRun, Tenant
from ..services import mail
from ..services import salud as svc

log = logging.getLogger("crimson.salud")

HEARTBEAT_TASK = svc.HEARTBEAT_TASK
BEAT_TASK = svc.BEAT_TASK
SELF_RECORDED = frozenset({HEARTBEAT_TASK})  # deja su propia fila (no depende de las señales)
KEEP = timedelta(days=30)
KEEP_HEARTBEAT = timedelta(days=2)
STUCK = timedelta(hours=1)  # corrida sin terminar: el proceso murio a media tarea
SUMMARY_MAX = 2000

_runs: dict[str, int] = {}  # task_id de Celery -> TaskRun.id, en el proceso que corre la tarea


def _session() -> Session:
    from ..core import db as dbmod  # en tiempo de llamada: las pruebas cambian SessionLocal

    return dbmod.SessionLocal()


# ---------- textos sin secretos ----------
_QUERY = re.compile(r"(https?://[^\s'\"?#]+)\?[^\s'\"]*")
_SECRET = re.compile(r"(?i)(bearer\s+|(?:token|key|secret|password|passwd|pin|signature|sig)\s*[=:]\s*)[^\s&'\",;]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{9,}(?!\d)")  # cedulas (9 a 12 digitos), telefonos con prefijo, cuentas


def sanitize(msg: str) -> str:
    """Sin parametros de URL (ahi viajan tokens), valores de llaves o contraseñas, correos ni numeros largos; recortado."""
    msg = _QUERY.sub(r"\1?…", msg or "")
    msg = _SECRET.sub(lambda m: m.group(1) + "***", msg)
    msg = _EMAIL.sub("[correo]", msg)
    msg = _LONG_DIGITS.sub("[número]", msg)
    return msg[:500]


def error_text(exc: BaseException | None) -> str:
    """Tipo y mensaje del error, sin datos de clientes. De la base, solo los tipos y el nombre de la restriccion: el
    mensaje de psycopg trae el DETAIL de Postgres con los valores de la fila (y SQLAlchemy, los parametros)."""
    if exc is None:
        return "Error desconocido"
    from sqlalchemy.exc import SQLAlchemyError

    if isinstance(exc, SQLAlchemyError):
        orig = getattr(exc, "orig", None)
        if orig is None:
            return type(exc).__name__
        cons = getattr(getattr(orig, "diag", None), "constraint_name", None)
        return sanitize(f"{type(exc).__name__} ({type(orig).__name__}{f', restricción {cons}' if cons else ''})")
    return sanitize(f"{type(exc).__name__}: {exc}")


def _summary(retval) -> dict | None:
    if retval is None:
        return None
    data = retval if isinstance(retval, dict) else {"resultado": retval}
    try:
        raw = json.dumps(data, default=str)
    except (TypeError, ValueError):
        raw = json.dumps({"resultado": str(retval)})
    if len(raw) > SUMMARY_MAX:
        return {"resultado": raw[: SUMMARY_MAX - 20] + "…", "recortado": True}
    return json.loads(raw)


def ops_emails() -> list[str]:
    out: list[str] = []
    for e in (os.getenv("OPS_ALERT_EMAILS") or "").split(","):
        e = e.strip().lower()
        if "@" in e and e not in out:
            out.append(e)
    return out


def _tenants(db: Session) -> list[Tenant]:
    return list(db.scalars(select(Tenant).where(Tenant.active).order_by(Tenant.id)))


def operator_tenant(db: Session) -> Tenant | None:
    """La empresa que opera la plataforma: OPS_TENANT (slug) o, si no esta o no existe, la primera empresa activa."""
    tenants = _tenants(db)
    slug = (os.getenv("OPS_TENANT") or "").strip().lower()
    return next((t for t in tenants if slug and (t.slug or "").lower() == slug), tenants[0] if tenants else None)


def _send_ops(db: Session, subject: str, body: str, key: str) -> int:
    """Avisos de la plataforma (fallas de tareas, el correo de Salud con sus revisiones globales): solo a quien la
    opera, los administradores de la empresa operadora y OPS_ALERT_EMAILS (con esa empresa). Nunca a las demas
    empresas: el cuerpo trae conteos de todas y textos de error. Cada destinatario lleva su dedupe_key."""
    t = operator_tenant(db)
    if t is None:
        return 0
    n = mail.notify_roles(db, t, ("admin",), subject, body, "salud", 0, dedupe_key=key)
    for e in ops_emails():
        mail.queue_email(db, t, e, subject, body, "salud", 0, dedupe_key=f"{key}|{e}")
        n += 1
    return n


# ---------- corridas (señales de Celery) ----------
def on_prerun(task_id=None, task=None, **_kw) -> None:
    name = getattr(task, "name", None)
    if not name or not task_id or name in SELF_RECORDED:
        return
    try:
        with _session() as db:
            r = TaskRun(task=name, task_id=task_id, started_at=datetime.now(UTC))
            db.add(r)
            db.commit()
            _runs[task_id] = r.id
    except Exception:  # noqa: BLE001
        log.exception("no se pudo registrar el inicio de %s", name)


def on_failure(task_id=None, exception=None, sender=None, **_kw) -> None:
    name = getattr(sender, "name", None) or "desconocida"
    err = error_text(exception)
    try:
        with _session() as db:
            rid = _runs.get(task_id) if task_id else None
            r = db.get(TaskRun, rid) if rid else None
            if r is None:
                r = TaskRun(task=name, task_id=task_id, started_at=datetime.now(UTC))
                db.add(r)
            r.ok, r.finished_at, r.error = False, datetime.now(UTC), err
            db.commit()
            alert_failure(db, name, err)
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("no se pudo registrar la falla de %s", name)


def on_postrun(task_id=None, task=None, retval=None, state=None, **_kw) -> None:
    rid = _runs.pop(task_id, None) if task_id else None
    if rid is None:
        return
    try:
        with _session() as db:
            r = db.get(TaskRun, rid)
            if r is None:
                return
            r.finished_at = r.finished_at or datetime.now(UTC)
            if state == "SUCCESS":
                r.ok, r.summary = True, _summary(retval)
            elif state == "RETRY":
                # autoretry (fx.bccr_daily): no es una falla, la tarea vuelve a correr; si al final falla, lo dice
                # task_failure. ok=None con hora de fin = reintento (sin hora de fin = en curso)
                exc = getattr(retval, "exc", None)
                motivo = error_text(exc) if isinstance(exc, BaseException) else sanitize(str(retval or ""))
                r.ok, r.summary = None, {"reintento": True}
                r.error = r.error or f"Reintento programado: {motivo}"[:500]
            else:
                r.ok = False
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("no se pudo registrar el fin de %s", getattr(task, "name", "?"))


def on_publish(sender=None, **_kw) -> None:
    """En el proceso del beat: al despachar salud.latido deja la fila salud.beat. Asi /health/deep sabe que el beat
    vive aunque el latido espere en la cola detras de una tarea larga. Nunca rompe el despacho."""
    if sender != HEARTBEAT_TASK:
        return
    try:
        with _session() as db:
            now = datetime.now(UTC)
            db.add(TaskRun(task=BEAT_TASK, started_at=now, finished_at=now, ok=True))
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("no se pudo registrar el despacho del latido")


def connect_signals() -> None:
    """Lo llama worker/celery_app.py. dispatch_uid: importar dos veces no registra dos veces."""
    from celery.signals import after_task_publish, task_failure, task_postrun, task_prerun

    task_prerun.connect(on_prerun, weak=False, dispatch_uid="crimson.salud.prerun")
    task_postrun.connect(on_postrun, weak=False, dispatch_uid="crimson.salud.postrun")
    task_failure.connect(on_failure, weak=False, dispatch_uid="crimson.salud.failure")
    after_task_publish.connect(on_publish, weak=False, dispatch_uid="crimson.salud.publish")


def alert_failure(db: Session, task: str, err: str) -> int:
    """Correo de "una tarea fallo" a quien opera la plataforma, como maximo uno por tarea y por dia de Costa Rica (por
    destinatario)."""
    hoy = cr_today()
    subject = f"Falló una tarea automática: {task}"
    body = (
        f"<p>La tarea <b>{html.escape(task)}</b> falló hoy ({hoy}).</p>"
        f"<p style='font-family:monospace;color:#7a1020'>{html.escape(err)}</p>"
        "<p>Se avisa una sola vez por día por tarea; las siguientes fallas quedan en las corridas del worker y en el "
        "correo diario de Salud.</p>"
    )
    return _send_ops(db, subject, body, f"salud:fallo:{task}:{hoy}")


# ---------- latido ----------
def heartbeat(db: Session, now: datetime | None = None) -> dict:
    """Fila del latido con lo que el worker ve: nombres de la configuracion que falta y su ENV. Hace commit."""
    from ..core.envcheck import missing_config

    now = now or datetime.now(UTC)
    faltan = missing_config()
    summary = {"faltan": faltan, "env": settings.env, "correo": "RESEND_API_KEY" not in faltan}
    db.add(TaskRun(task=HEARTBEAT_TASK, started_at=now, finished_at=now, ok=True, summary=summary))
    db.commit()
    return summary


# ---------- correo diario ----------
LABELS = {
    "db": "Base de datos",
    "beat": "Latido del worker y del beat",
    "correo_worker": "Correo configurado en el worker",
    "config_worker": "Configuración del worker",
    "env": "Entorno",
    "eurocomp_existencias": "Existencias de Eurocomp",
    "correo_fallido": "Correos fallidos (24 h)",
    "tipo_cambio": "Tipo de cambio",
    "onvo": "Pago con tarjeta (ONVO)",
    "por_emitir": "Facturas por emitir de más de 24 h",
}


def _badge(c: dict) -> str:
    if c["ok"]:
        return "<b style='color:#1d7a3a'>OK</b>"
    return "<b style='color:#b3122a'>FALLA</b>" if c["critico"] else "<b style='color:#9a6a00'>AVISO</b>"


def _detail(c: dict) -> str:
    return ", ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in c.items() if k not in ("ok", "critico")) or "—"


def _runs_24h(db: Session, now: datetime) -> list[dict]:
    desde = now - timedelta(hours=24)
    out: dict[str, dict] = {}
    q = select(TaskRun).where(TaskRun.started_at >= desde, TaskRun.task.not_in((HEARTBEAT_TASK, BEAT_TASK))).order_by(TaskRun.started_at)
    for r in db.scalars(q):
        x = out.setdefault(r.task, {"task": r.task, "total": 0, "fallas": 0, "sin_terminar": 0, "reintentos": 0, "error": None})
        x["total"] += 1
        started = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=UTC)
        if r.ok is False:
            x["fallas"] += 1
            x["error"] = r.error
        elif r.ok is None and r.finished_at is not None:
            x["reintentos"] += 1  # reintento programado: la corrida siguiente dice como termino
        elif r.ok is None and now - started > STUCK:
            x["sin_terminar"] += 1
    return sorted(out.values(), key=lambda x: (-x["fallas"], -x["sin_terminar"], x["task"]))


def _tenant_block(db: Session, t: Tenant, now: datetime) -> str:
    """Lo de una sola empresa: sus correos de 24 h y su cola "Por emitir" (los "fallido", por la hora en que se dejaron)."""
    desde = now - timedelta(hours=24)
    rows = db.execute(
        select(EmailOutbox.status, func.count()).where(EmailOutbox.tenant_id == t.id, EmailOutbox.created_at >= desde).group_by(EmailOutbox.status)
    ).all()
    por = dict(rows)
    por["fallido"] = svc.check_outbox(db, now, t.id)["fallidos_24h"]
    pe = svc.check_por_emitir(db, now, t.id)["mas_de_24h"]
    correo = " · ".join(f"{k}: {por.get(k, 0)}" for k in ("enviado", "error", "fallido", "simulado"))
    return f"<h4 style='margin:16px 0 4px'>{html.escape(t.name)}</h4><p>Correos de las últimas 24 h: {correo}<br>Facturas por emitir con más de 24 h: {pe}</p>"


def _cta_correos() -> str:  # el boton principal del correo de Salud (email_layout): la bitacora de correos
    from ..services.email_layout import cta

    return cta("/ajustes?tab=correo", "Revisar los correos en Ajustes")


def daily_report(db: Session, now: datetime | None = None) -> int:
    """Arma y manda el correo de Salud (uno por dia y destinatario) y purga corridas viejas. Hace commit.
    Quien opera la plataforma recibe las revisiones, las tareas y el bloque de la empresa operadora; los administradores
    de cada una de las demas empresas, solo el bloque de la suya."""
    now = now or datetime.now(UTC)
    hoy = cr_today()
    rep = svc.deep(db, now=now, today=hoy)
    estado = {"ok": "Todo en orden", "aviso": "Con avisos", "falla": "FALLA"}[rep["status"]]
    filas = "".join(f"<tr><td>{_badge(c)}</td><td>{LABELS.get(k, k)}</td><td>{html.escape(_detail(c))}</td></tr>" for k, c in rep["checks"].items())
    tareas = _runs_24h(db, now)
    malas = [x for x in tareas if x["fallas"] or x["sin_terminar"]]
    if malas:
        filas_t = "".join(
            f"<tr><td>{html.escape(x['task'])}</td><td align=right>{x['fallas']}/{x['total']}</td><td align=right>{x['sin_terminar']}</td>"
            f"<td>{html.escape((x['error'] or '')[:200])}</td></tr>"
            for x in malas
        )
        bloque_t = f"<table border=0 cellpadding=4><tr><th>Tarea</th><th>Fallas</th><th>Sin terminar</th><th>Último error</th></tr>{filas_t}</table>"
    else:
        bloque_t = f"<p>Ninguna tarea falló ({sum(x['total'] for x in tareas)} corridas de {len(tareas)} tareas).</p>"
    # el asunto ya es el titulo del correo (email_layout): aqui solo el estado, sin repetirlo en un <h3>
    body = (
        f"<p>Estado general: <b>{html.escape(estado)}</b>.</p>"
        f"<table border=0 cellpadding=4>{filas}</table>"
        f"<h4 style='margin:16px 0 4px'>Tareas automáticas (24 h)</h4>{bloque_t}"
    )
    op = operator_tenant(db)
    n = _send_ops(db, f"Salud de la plataforma · {hoy} · {estado}", body + (_tenant_block(db, op, now) if op else "") + _cta_correos(), f"salud:diario:{hoy}")
    for t in _tenants(db):
        if op is not None and t.id == op.id:
            continue
        n += mail.notify_roles(
            db, t, ("admin",), f"Salud de su empresa · {t.name} · {hoy}", _tenant_block(db, t, now) + _cta_correos(), "salud", 0, dedupe_key=f"salud:empresa:{hoy}"
        )
    db.commit()
    purge(db, now)
    return n


def purge(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    a = db.execute(delete(TaskRun).where(TaskRun.started_at < now - KEEP)).rowcount or 0
    b = db.execute(delete(TaskRun).where(TaskRun.task.in_((HEARTBEAT_TASK, BEAT_TASK)), TaskRun.started_at < now - KEEP_HEARTBEAT)).rowcount or 0
    db.commit()
    return a + b


# ---------- tareas del worker (las pone en el beat worker/celery_app.py) ----------
@shared_task(name="salud.latido")
def latido() -> dict:
    """Latido del beat y del worker (con los nombres de la configuracion que le falta): /health/deep lo mira."""
    with _session() as db:
        return heartbeat(db)


@shared_task(name="salud.correo_diario")
def correo_diario() -> int:
    """Correo de Salud a quien opera la plataforma (y su bloque a cada empresa); purga las corridas de mas de 30 dias."""
    with _session() as db:
        return daily_report(db)
