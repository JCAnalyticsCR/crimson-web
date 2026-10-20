"""Salud de la plataforma (plan 1.8): latido del worker y /health/deep (503 si se detiene), corridas de tareas por las
señales de Celery con un solo aviso por tarea y por dia, correo diario de Salud y logging separado por nivel."""

import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import EmailOutbox, ExchangeRate, Invoice, TaskRun, Tenant
from app.services import salud as svc
from app.tasks import salud


@pytest.fixture
def worker(db_session, monkeypatch):
    pytest.importorskip("celery")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx(db_session))
    salud._runs.clear()
    return celery_app


def _t(db):
    return db.scalar(select(Tenant).order_by(Tenant.id))


def _alertas(db, prefix):
    return db.scalars(select(EmailOutbox).where(EmailOutbox.entity == "salud", EmailOutbox.dedupe_key.startswith(prefix))).all()


# ---------- latido y /health/deep ----------
def test_sin_latido_o_latido_vencido_da_503(client, db_session):
    r = client.get("/health/deep")
    assert r.status_code == 503 and r.json()["checks"]["beat"]["ok"] is False
    salud.heartbeat(db_session)
    r = client.get("/health/deep")
    assert r.status_code == 200, r.json()
    assert r.json()["checks"]["beat"]["ok"] is True and r.headers["cache-control"] == "no-store"
    # el worker se detiene: a los 15 min sin latido, 503
    for hb in db_session.scalars(select(TaskRun).where(TaskRun.task == "salud.latido")):
        hb.started_at = hb.finished_at = datetime.now(UTC) - timedelta(minutes=16)
    db_session.commit()
    r = client.get("/health/deep")
    assert r.status_code == 503 and r.json()["status"] == "falla" and r.json()["checks"]["beat"]["edad_min"] >= 15


def test_el_latido_lleva_los_nombres_de_la_configuracion_que_falta(client, db_session, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("BCCR_TOKEN", "token-de-prueba")
    s = salud.heartbeat(db_session)
    assert "RESEND_API_KEY" in s["faltan"] and "BCCR_TOKEN" not in s["faltan"] and s["correo"] is False
    body = client.get("/health/deep").json()
    assert "RESEND_API_KEY" in body["checks"]["config_worker"]["faltan"]
    assert "token-de-prueba" not in str(body)  # solo nombres, nunca valores
    # fuera de produccion el correo del worker es un aviso; en produccion es critico
    assert client.get("/health/deep").status_code == 200
    monkeypatch.setattr(settings, "env", "production")
    r = client.get("/health/deep")
    assert r.status_code == 503 and r.json()["checks"]["correo_worker"]["ok"] is False


def test_health_deep_sin_datos_sensibles(client, auth, db_session):
    salud.heartbeat(db_session)
    t = _t(db_session)
    db_session.add(EmailOutbox(tenant_id=t.id, to="cliente-secreto@ejemplo.com", subject="Factura", html="x", status="fallido"))
    db_session.commit()
    body = client.get("/health/deep").json()
    assert body["checks"]["correo_fallido"] == {"ok": False, "critico": False, "fallidos_24h": 1}
    texto = str(body)
    assert "@" not in texto and "cliente-secreto" not in texto and "secret" not in texto.lower()


def test_avisos_de_eurocomp_tipo_de_cambio_y_por_emitir(db_session):
    t = _t(db_session)
    now = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
    assert svc.check_eurocomp(db_session, now) == {"ok": True, "critico": False, "aplica": False}
    t.settings = {**(t.settings or {}), "supplier_sync": {"eurocomp": {"last_stock_at": (now - timedelta(hours=9)).isoformat()}}}
    db_session.commit()
    c = svc.check_eurocomp(db_session, now)
    assert c["ok"] is False and c["edad_h"] == 9.0
    t.settings = {**t.settings, "supplier_sync": {"eurocomp": {"last_stock_at": (now - timedelta(hours=2)).isoformat()}}}
    db_session.commit()
    assert svc.check_eurocomp(db_session, now)["ok"] is True
    # tipo de cambio: viernes -> martes son 2 dias habiles (ok); -> miercoles, 3 (vieja)
    for fx in db_session.scalars(select(ExchangeRate)):  # la semilla trae la tasa de hoy
        db_session.delete(fx)
    db_session.add(ExchangeRate(date=date(2026, 10, 16), currency="USD", sell=Decimal("510"), buy=Decimal("500"), source="manual"))
    db_session.commit()
    assert svc.check_fx(db_session, date(2026, 10, 20))["ok"] is True
    assert svc.check_fx(db_session, date(2026, 10, 21))["ok"] is False
    # por emitir: pagada, sin comprobante y con mas de 24 h
    inv = Invoice(tenant_id=t.id, number="FEC-000990", issue_date=date(2026, 10, 18), status="pagada", einvoice_status="sin_emitir")
    inv.created_at = now - timedelta(hours=30)
    db_session.add(inv)
    db_session.commit()
    assert svc.check_por_emitir(db_session, now)["mas_de_24h"] == 1
    inv.einvoice_status = "aceptada"
    db_session.commit()
    assert svc.check_por_emitir(db_session, now)["ok"] is True


# ---------- corridas y fallas ----------
def test_tarea_que_falla_queda_registrada_y_avisa_una_sola_vez_por_dia(worker, db_session, monkeypatch):
    monkeypatch.setenv("OPS_ALERT_EMAILS", "operacion@ejemplo.com, ")

    @worker.celery.task(name="prueba.falla")
    def falla():
        raise RuntimeError("se cayo https://api.ejemplo.com/x?token=abc123 con Bearer xyz789")

    falla.apply()
    falla.apply()
    runs = db_session.scalars(select(TaskRun).where(TaskRun.task == "prueba.falla")).all()
    assert len(runs) == 2 and all(r.ok is False and r.finished_at for r in runs)
    assert "RuntimeError" in runs[0].error and "abc123" not in runs[0].error and "xyz789" not in runs[0].error
    alertas = _alertas(db_session, "salud:fallo:prueba.falla:")
    destinos = sorted(a.to for a in alertas)
    assert destinos == ["admin@crimsonapp.com", "operacion@ejemplo.com"]  # una por destinatario, aunque fallo dos veces
    assert "abc123" not in alertas[0].html


def test_tarea_que_sale_bien_deja_su_resumen(worker, db_session):
    @worker.celery.task(name="prueba.bien")
    def bien():
        return {"enviados": 3}

    bien.apply()
    r = db_session.scalar(select(TaskRun).where(TaskRun.task == "prueba.bien"))
    assert r.ok is True and r.summary == {"enviados": 3} and r.finished_at is not None
    assert not _alertas(db_session, "salud:fallo:")


def test_el_worker_registra_las_señales_y_programa_latido_y_correo(worker):
    from celery.signals import task_failure, task_postrun, task_prerun

    assert any("crimson.salud.prerun" in str(k) for k, _ in [(r[0], r[1]) for r in task_prerun.receivers])
    assert task_postrun.receivers and task_failure.receivers
    beat = worker.celery.conf.beat_schedule
    assert beat["salud-latido"]["task"] == "salud.latido" and beat["salud-correo-diario"]["task"] == "salud.correo_diario"
    assert worker.salud_latido()["env"] == settings.env


def test_sanitize():
    s = salud.sanitize("GET https://x.ejemplo.com/a/b?token=SECRETO&b=2 fallo; password=otro key: valor Bearer abc.def")
    assert "SECRETO" not in s and "otro" not in s and "valor" not in s and "abc.def" not in s
    assert "https://x.ejemplo.com/a/b" in s


# ---------- correo diario ----------
def test_correo_diario_a_admin_y_operacion_una_vez_y_purga(worker, db_session, monkeypatch):
    monkeypatch.setenv("OPS_ALERT_EMAILS", "operacion@ejemplo.com")
    now = datetime.now(UTC)
    salud.heartbeat(db_session)
    db_session.add_all(
        [
            TaskRun(task="sales.run_recurrences", started_at=now - timedelta(hours=2), finished_at=now, ok=False, error="ValueError: dato raro"),
            TaskRun(task="fx.bccr_daily", started_at=now - timedelta(hours=3), finished_at=now, ok=True),
            TaskRun(task="viejo", started_at=now - timedelta(days=31), finished_at=now - timedelta(days=31), ok=True),
            TaskRun(task="salud.latido", started_at=now - timedelta(days=3), finished_at=now - timedelta(days=3), ok=True),
        ]
    )
    db_session.commit()
    assert worker.salud_correo_diario() == 2
    correos = _alertas(db_session, "salud:diario:")
    assert sorted(c.to for c in correos) == ["admin@crimsonapp.com", "operacion@ejemplo.com"]
    html = correos[0].html
    assert "sales.run_recurrences" in html and "dato raro" in html and "Latido del worker" in html and "Correos de las últimas 24 h" in html
    # corre dos veces el mismo dia: no se repite
    worker.salud_correo_diario()
    assert len(_alertas(db_session, "salud:diario:")) == 2
    tareas = {r.task for r in db_session.scalars(select(TaskRun))}
    assert "viejo" not in tareas and "salud.latido" in tareas  # se purgo lo viejo; el latido reciente queda
    assert db_session.scalar(select(TaskRun).where(TaskRun.task == "salud.latido", TaskRun.started_at < now - timedelta(days=2))) is None


# ---------- logging ----------
def test_logging_info_a_stdout_y_warning_a_stderr(capsys):
    from app.core.logging import setup_logging

    setup_logging("INFO")
    setup_logging("INFO")  # idempotente: no duplica lineas
    lg = logging.getLogger("crimson.prueba")
    lg.info("linea-info")
    lg.warning("linea-warning")
    out, err = capsys.readouterr()
    assert out.count("linea-info") == 1 and "linea-warning" not in out
    assert err.count("linea-warning") == 1 and "linea-info" not in err
    assert "INFO crimson.prueba" in out and "WARNING crimson.prueba" in err
    uv = logging.getLogger("uvicorn.error")
    assert uv.propagate and not uv.handlers


def test_recurrencia_que_falla_deja_la_traza(db_session, caplog, monkeypatch):
    from app.models import Recurrence
    from app.routers import ops

    t = _t(db_session)
    r = Recurrence(tenant_id=t.id, name="Mensualidad", kind="factura", frequency="mensual", next_date=date(2026, 1, 1), template={}, active=True)
    db_session.add(r)
    db_session.commit()

    def boom(*a, **k):
        raise ValueError("plantilla rota")

    monkeypatch.setattr(ops, "run_recurrence", boom)
    with caplog.at_level(logging.ERROR, logger="crimson.recurrences"):
        ops.run_due_recurrences(db_session, today=date(2026, 1, 2))
    rec = [x for x in caplog.records if x.name == "crimson.recurrences"]
    assert rec and rec[0].exc_info is not None


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False
