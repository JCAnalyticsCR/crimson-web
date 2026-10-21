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
from app.models import EmailOutbox, ExchangeRate, Invoice, TaskRun, Tenant, TenantUser, User
from app.services import salud as svc
from app.tasks import salud

TOKEN = "token-de-salud-de-prueba"
H = {"X-Health-Token": TOKEN}  # el detalle de /health/deep solo con el token


@pytest.fixture(autouse=True)
def _detalle_y_sin_cache(monkeypatch):
    """Las pruebas leen el detalle (con el token) y cambian datos entre llamadas: sin el guardado de 30 s."""
    monkeypatch.setenv("HEALTH_TOKEN", TOKEN)
    monkeypatch.setattr(svc, "DEEP_CACHE_SECONDS", 0)
    monkeypatch.delenv("OPS_TENANT", raising=False)


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
    r = client.get("/health/deep", headers=H)
    assert r.status_code == 503 and r.json()["checks"]["beat"]["ok"] is False
    salud.heartbeat(db_session)
    r = client.get("/health/deep", headers=H)
    assert r.status_code == 200, r.json()
    assert r.json()["checks"]["beat"]["ok"] is True and r.headers["cache-control"] == "no-store"
    # el worker se detiene: a los 15 min sin latido, 503
    for hb in db_session.scalars(select(TaskRun).where(TaskRun.task == "salud.latido")):
        hb.started_at = hb.finished_at = datetime.now(UTC) - timedelta(minutes=16)
    db_session.commit()
    r = client.get("/health/deep", headers=H)
    assert r.status_code == 503 and r.json()["status"] == "falla" and r.json()["checks"]["beat"]["edad_min"] >= 15


def test_publico_solo_el_estado(client, db_session, monkeypatch):
    """Sin el token (o con uno malo, o sin HEALTH_TOKEN definido) solo el estado y el ENV: nada de nombres de
    configuracion, conteos ni modos. El monitor externo de 0.8 es de palabra clave y busca "env":"production" (lo
    mismo que ya publica /health) en el texto crudo. En el 503 la palabra no sale: el monitor cae aunque el proveedor
    ignore el codigo HTTP y solo mire la palabra."""
    salud.heartbeat(db_session)
    clave = f'"env":"{settings.env}"'  # tal cual la busca el monitor: sin espacios
    for headers in ({}, {"X-Health-Token": "otro"}):
        r = client.get("/health/deep", headers=headers)
        assert r.status_code == 200 and set(r.json()) == {"status", "env", "at"}
        assert clave in r.text and clave in client.get("/health").text
    assert "checks" in client.get("/health/deep", headers=H).json()
    monkeypatch.delenv("HEALTH_TOKEN")
    assert set(client.get("/health/deep", headers=H).json()) == {"status", "env", "at"}
    # el 503 tambien llega sin detalle
    for hb in db_session.scalars(select(TaskRun).where(TaskRun.task == "salud.latido")):
        hb.started_at = hb.finished_at = datetime.now(UTC) - timedelta(minutes=16)
    db_session.commit()
    r = client.get("/health/deep")
    assert r.status_code == 503 and r.json()["status"] == "falla" and "checks" not in r.json()
    assert set(r.json()) == {"status", "at"} and clave not in r.text  # sin la palabra clave del monitor
    assert svc.public_view({"status": "falla", "env": "production", "at": "x"}) == {"status": "falla", "at": "x"}
    assert svc.public_view({"status": "aviso", "env": "production", "at": "x"})["env"] == "production"  # 200: si sale


def test_resultado_guardado_y_limite_por_ip(client, db_session, monkeypatch):
    """Cada pasada cuesta consultas y descifrar la llave de ONVO: se guarda 30 s y una rafaga recibe 429."""
    monkeypatch.setattr(svc, "DEEP_CACHE_SECONDS", 30)
    llamadas = []
    real = svc.deep
    monkeypatch.setattr(svc, "deep", lambda db: llamadas.append(1) or real(db))
    salud.heartbeat(db_session)
    codes = [client.get("/health/deep").status_code for _ in range(121)]
    assert len(llamadas) == 1
    assert codes[:120] == [200] * 120 and codes[120] == 429


def test_tarea_larga_no_da_503_falso_pero_un_contenedor_detenido_si(db_session):
    """Un solo proceso: el latido espera detras de una tarea larga (catalogo de Eurocomp). Vale mientras el beat siga
    despachando (fila salud.beat) y el worker trabaje; si el contenedor se detuvo, el beat tampoco despacha."""
    now = datetime.now(UTC)
    db_session.add(TaskRun(task="salud.latido", started_at=now - timedelta(minutes=40), finished_at=now - timedelta(minutes=40), ok=True))
    larga = TaskRun(task="suppliers.sync_eurocomp", started_at=now - timedelta(minutes=35))  # en curso
    db_session.add(larga)
    db_session.commit()
    assert svc.check_beat(db_session, now)["ok"] is False  # sin señal del beat no alcanza con la corrida en curso
    db_session.add(TaskRun(task="salud.beat", started_at=now - timedelta(minutes=2), finished_at=now - timedelta(minutes=2), ok=True))
    db_session.commit()
    c = svc.check_beat(db_session, now)
    assert c["ok"] is True and c["critico"] is True and "ocupado" in c["detalle"]
    # recien termino la larga: el latido sale en unos minutos; sigue vivo
    larga.finished_at, larga.ok = now - timedelta(minutes=1), True
    db_session.commit()
    assert svc.check_beat(db_session, now)["ok"] is True
    # el contenedor se detuvo: 16 min despues ni el beat despacha ni el worker trabaja
    assert svc.check_beat(db_session, now + timedelta(minutes=16))["ok"] is False
    # una corrida "en curso" de mas de 1 h es un proceso que murio a media tarea, no un worker ocupado
    larga.finished_at, larga.ok, larga.started_at = None, None, now - timedelta(hours=2)
    db_session.commit()
    assert svc.check_beat(db_session, now)["ok"] is False


def test_el_beat_deja_su_fila_al_despachar_el_latido(worker, db_session):
    salud.on_publish(sender="hacienda.send_pending")
    assert not db_session.scalars(select(TaskRun).where(TaskRun.task == "salud.beat")).all()
    salud.on_publish(sender="salud.latido")
    r = db_session.scalar(select(TaskRun).where(TaskRun.task == "salud.beat"))
    assert r is not None and r.ok is True and r.finished_at is not None
    # no es una tarea: no sale en las corridas del correo de Salud
    assert not [x for x in salud._runs_24h(db_session, datetime.now(UTC)) if x["task"] == "salud.beat"]


def test_el_latido_lleva_los_nombres_de_la_configuracion_que_falta(client, db_session, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("BCCR_TOKEN", "token-de-prueba")
    monkeypatch.delenv("OPS_ALERT_EMAILS", raising=False)
    s = salud.heartbeat(db_session)
    assert "RESEND_API_KEY" in s["faltan"] and "BCCR_TOKEN" not in s["faltan"] and s["correo"] is False
    # sin OPS_ALERT_EMAILS JC no recibe las fallas ni el correo de Salud: sale en el latido (aviso, no critico)
    assert "OPS_ALERT_EMAILS" in s["faltan"]
    body = client.get("/health/deep", headers=H).json()
    assert {"RESEND_API_KEY", "OPS_ALERT_EMAILS"} <= set(body["checks"]["config_worker"]["faltan"])
    assert body["checks"]["config_worker"]["critico"] is False
    assert "token-de-prueba" not in str(body)  # solo nombres, nunca valores
    # fuera de produccion el correo del worker es un aviso; en produccion es critico
    assert client.get("/health/deep").status_code == 200
    monkeypatch.setattr(settings, "env", "production")
    r = client.get("/health/deep", headers=H)
    assert r.status_code == 503 and r.json()["checks"]["correo_worker"]["ok"] is False
    monkeypatch.setenv("OPS_ALERT_EMAILS", "operacion@ejemplo.com")
    assert "OPS_ALERT_EMAILS" not in salud.heartbeat(db_session)["faltan"]


def test_el_arranque_del_worker_avisa_si_falta_ops_alert_emails(monkeypatch, caplog):
    from app.core.envcheck import log_missing_config

    monkeypatch.delenv("OPS_ALERT_EMAILS", raising=False)
    with caplog.at_level(logging.WARNING, logger="crimson.config"):
        assert "OPS_ALERT_EMAILS" in log_missing_config()
    assert any("OPS_ALERT_EMAILS" in r.getMessage() for r in caplog.records)


def test_health_deep_sin_datos_sensibles(client, auth, db_session):
    salud.heartbeat(db_session)
    t = _t(db_session)
    db_session.add(EmailOutbox(tenant_id=t.id, to="cliente-secreto@ejemplo.com", subject="Factura", html="x", status="fallido"))
    db_session.commit()
    body = client.get("/health/deep", headers=H).json()
    assert body["checks"]["correo_fallido"] == {"ok": False, "critico": False, "fallidos_24h": 1}
    texto = str(body)
    assert "@" not in texto and "cliente-secreto" not in texto and "secret" not in texto.lower()


def test_avisos_de_eurocomp_tipo_de_cambio_y_por_emitir(db_session):
    t = _t(db_session)
    now = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
    assert svc.check_eurocomp(db_session, now) == {"ok": True, "critico": False, "aplica": False}
    # solo el catalogo semanal o un error de credenciales (nada vinculado, integracion pospuesta): no hay existencias
    # que refrescar, no es un aviso diario
    t.settings = {**(t.settings or {}), "supplier_sync": {"eurocomp": {"last_ok_at": (now - timedelta(days=3)).isoformat(), "last_error": "credenciales"}}}
    db_session.commit()
    assert svc.check_eurocomp(db_session, now) == {"ok": True, "critico": False, "aplica": False}
    # el refresco de existencias se intento y nunca salio bien: eso si
    t.settings = {**t.settings, "supplier_sync": {"eurocomp": {"last_stock_try_at": (now - timedelta(hours=1)).isoformat()}}}
    db_session.commit()
    assert svc.check_eurocomp(db_session, now)["detalle"] == "existencias nunca refrescadas"
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


def test_onvo_tarjeta_apagada_no_avisa_y_encendida_sin_llave_live_si(db_session, monkeypatch):
    """Tarjeta apagada en Mi Tienda es una decision (no un aviso diario); encendida en produccion sin llave live, la
    tienda no la ofrece y se pierde la venta; una llave live fuera de produccion cobraria dinero real."""
    t = _t(db_session)
    monkeypatch.setattr(settings, "onvo_secret_key", "onvo_test_secret_key_" + "x" * 24)  # llave de entorno de prueba
    monkeypatch.setattr(settings, "env", "production")
    c = svc.check_onvo(db_session)
    assert c == {"ok": True, "critico": False, "modo": "test", "tarjeta": "apagada"}
    store = (t.settings or {}).get("store") or {}
    t.settings = {**(t.settings or {}), "store": {**store, "checkout": {**(store.get("checkout") or {}), "methods": {"tarjeta": {"active": True}}}}}
    db_session.commit()
    c = svc.check_onvo(db_session)
    assert c["ok"] is False and c["tarjeta"] == "activa" and c["modo"] == "test"
    monkeypatch.setattr(settings, "onvo_secret_key", "onvo_live_secret_key_" + "x" * 24)
    assert svc.check_onvo(db_session)["ok"] is True
    monkeypatch.setattr(settings, "env", "staging")
    assert svc.check_onvo(db_session)["ok"] is False  # live fuera de produccion
    assert "onvo_live" not in str(svc.check_onvo(db_session))  # nunca la llave


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
    from celery.signals import after_task_publish, task_failure, task_postrun, task_prerun

    assert any("crimson.salud.prerun" in str(k) for k, _ in [(r[0], r[1]) for r in task_prerun.receivers])
    assert any("crimson.salud.publish" in str(r[0]) for r in after_task_publish.receivers)
    assert task_postrun.receivers and task_failure.receivers
    beat = worker.celery.conf.beat_schedule
    assert beat["salud-latido"]["task"] == "salud.latido" and beat["salud-correo-diario"]["task"] == "salud.correo_diario"
    assert worker.celery.tasks["salud.latido"]()["env"] == settings.env


def test_sanitize():
    s = salud.sanitize("GET https://x.ejemplo.com/a/b?token=SECRETO&b=2 fallo; password=otro key: valor Bearer abc.def")
    assert "SECRETO" not in s and "otro" not in s and "valor" not in s and "abc.def" not in s
    assert "https://x.ejemplo.com/a/b" in s
    s = salud.sanitize("no salio para cliente.uno@empresa-a.co.cr (cedula 3101123456) factura FEC-000901")
    assert "@" not in s and "3101123456" not in s and "FEC-000901" in s


def test_error_de_la_base_sin_el_detalle_de_la_fila():
    """psycopg trae el DETAIL de Postgres con los valores (y SQLAlchemy los parametros): de la base solo van los tipos y
    el nombre de la restriccion."""
    from sqlalchemy.exc import IntegrityError

    class UniqueViolation(Exception):
        class diag:  # noqa: N801  como psycopg
            constraint_name = "uq_customer_email"

    e = IntegrityError(
        "INSERT INTO customer (email) VALUES (%(email)s)",
        {"email": "cliente@empresa-a.com"},
        UniqueViolation('duplicate key value violates unique constraint "uq_customer_email"\nDETAIL: Key (email)=(cliente@empresa-a.com) already exists.'),
    )
    txt = salud.error_text(e)
    assert txt == "IntegrityError (UniqueViolation, restricción uq_customer_email)"
    assert "@" not in txt and "DETAIL" not in txt


def test_en_produccion_la_base_no_muestra_parametros(monkeypatch):
    from app.core.db import make_engine

    monkeypatch.setattr(settings, "env", "production")
    eng = make_engine("sqlite://")
    assert eng.hide_parameters is True
    eng.dispose()


def test_un_reintento_programado_no_es_una_falla(worker, db_session):
    """fx.bccr_daily (autoretry): falla una vez, se reintenta y sale bien. El correo de Salud no la cuenta como falla."""
    from celery.exceptions import Retry

    tarea = type("T", (), {"name": "fx.bccr_daily"})()
    salud.on_prerun(task_id="t-1", task=tarea)
    salud.on_postrun(task_id="t-1", task=tarea, retval=Retry(exc=RuntimeError("BCCR caido para x@ejemplo.com"), when=60), state="RETRY")
    salud.on_prerun(task_id="t-1", task=tarea)  # el reintento llega con el mismo id
    salud.on_postrun(task_id="t-1", task=tarea, retval={"sell": "510"}, state="SUCCESS")
    runs = db_session.scalars(select(TaskRun).where(TaskRun.task == "fx.bccr_daily").order_by(TaskRun.id)).all()
    assert [r.ok for r in runs] == [None, True] and runs[0].finished_at and runs[0].summary == {"reintento": True}
    assert "RuntimeError" in runs[0].error and "@" not in runs[0].error
    x = next(x for x in salud._runs_24h(db_session, datetime.now(UTC) + timedelta(hours=2)) if x["task"] == "fx.bccr_daily")
    assert x["fallas"] == 0 and x["sin_terminar"] == 0 and x["reintentos"] == 1


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
    assert worker.celery.tasks["salud.correo_diario"]() == 2
    correos = _alertas(db_session, "salud:diario:")
    assert sorted(c.to for c in correos) == ["admin@crimsonapp.com", "operacion@ejemplo.com"]
    html = correos[0].html
    assert "sales.run_recurrences" in html and "dato raro" in html and "Latido del worker" in html and "Correos de las últimas 24 h" in html
    # corre dos veces el mismo dia: no se repite
    worker.celery.tasks["salud.correo_diario"]()
    assert len(_alertas(db_session, "salud:diario:")) == 2
    tareas = {r.task for r in db_session.scalars(select(TaskRun))}
    assert "viejo" not in tareas and "salud.latido" in tareas  # se purgo lo viejo; el latido reciente queda
    assert db_session.scalar(select(TaskRun).where(TaskRun.task == "salud.latido", TaskRun.started_at < now - timedelta(days=2))) is None


# ---------- varias empresas ----------
def _otra_empresa(db, slug="empresa-b", admin="admin-b@ejemplo.com"):
    b = Tenant(slug=slug, name="Empresa B")
    db.add(b)
    db.flush()
    u = User(email=admin, full_name="Admin B", password_hash="x")
    db.add(u)
    db.flush()
    db.add(TenantUser(tenant_id=b.id, user_id=u.id, role_code="admin"))
    db.commit()
    return b


def test_lo_de_la_plataforma_solo_le_llega_a_quien_la_opera(worker, db_session, monkeypatch):
    """Con dos empresas, el admin de la B no recibe el texto de error de una tarea ni los conteos globales: solo el
    bloque de su empresa. Lo de la plataforma va a la empresa operadora (OPS_TENANT o la primera) y a OPS_ALERT_EMAILS."""
    monkeypatch.setenv("OPS_ALERT_EMAILS", "operacion@ejemplo.com")
    a = _t(db_session)
    b = _otra_empresa(db_session)
    db_session.add(EmailOutbox(tenant_id=a.id, to="cliente@empresa-a.com", subject="Factura", html="x", status="fallido"))
    db_session.commit()
    salud.alert_failure(db_session, "prueba.global", "IntegrityError (UniqueViolation, restricción uq_x)")
    db_session.commit()
    alertas = _alertas(db_session, "salud:fallo:prueba.global:")
    assert sorted(x.to for x in alertas) == ["admin@crimsonapp.com", "operacion@ejemplo.com"] and all(x.tenant_id == a.id for x in alertas)
    salud.heartbeat(db_session)
    salud.daily_report(db_session)
    de_b = db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == "admin-b@ejemplo.com")).all()
    assert len(de_b) == 1 and de_b[0].tenant_id == b.id and de_b[0].subject.startswith("Salud de su empresa")
    assert "Empresa B" in de_b[0].html and "Tareas automáticas" not in de_b[0].html and "Latido" not in de_b[0].html
    assert html_of(db_session, "admin@crimsonapp.com", "salud:diario:").count("<h4") == 2  # tareas + su propia empresa
    assert "Empresa B" not in html_of(db_session, "operacion@ejemplo.com", "salud:diario:")
    # OPS_TENANT elige la operadora
    monkeypatch.setenv("OPS_TENANT", "empresa-b")
    assert salud.operator_tenant(db_session).id == b.id
    salud.alert_failure(db_session, "prueba.otra", "RuntimeError: x")
    assert sorted(x.to for x in _alertas(db_session, "salud:fallo:prueba.otra:")) == ["admin-b@ejemplo.com", "operacion@ejemplo.com"]


def html_of(db, to, prefix):
    return next(x.html for x in _alertas(db, prefix) if x.to == to)


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


def test_el_worker_configura_el_logging_antes_de_sus_lineas_de_arranque(worker):
    """El WARNING de configuracion faltante y el INFO de FISCAL_KEYS salen al importar el modulo, antes de la señal
    setup_logging de Celery: sin esto salian por el lastResort de Python (sin nivel ni logger) o se perdian."""
    import inspect

    src = inspect.getsource(worker)
    assert src.index("\nsetup_logging()") < src.index("\n_log_fiscal_keys()") < src.index("\n_log_missing_config()")
    assert any(getattr(h, "_crimson_handler", False) for h in logging.getLogger().handlers)


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


# ---------- monitor sintetico de la tienda (scripts/monitor/tienda_check.py) ----------
def _tienda_check():
    import importlib.util

    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "monitor", "tienda_check.py"))
    spec = importlib.util.spec_from_file_location("tienda_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_monitor_de_la_tienda_muestra_rotativa():
    tc = _tienda_check()
    locs = list(range(10))
    assert tc.sample(locs, 3, now=0) == [0, 1, 2] and tc.sample(locs, 3, now=1800) == [3, 4, 5]
    assert tc.sample(locs, 3, now=1800 * 3) == [9, 0, 1]  # da la vuelta: en unas horas pasa por todo el sitemap
    assert tc.sample(locs[:2], 5) == [0, 1] and tc.sample([], 3) == []


def test_monitor_de_la_tienda_detecta_basura_gzip_pagina_en_blanco_y_pago_simulado(monkeypatch):
    import json

    tc = _tienda_check()
    base, deep = "https://tienda.ejemplo.com", "https://panel.ejemplo.com/api/health/deep"
    ok = b'<html><head><title>Producto</title></head><body><div id="root"></div><script type="module" src="/assets/index.js"></script></body></html>'
    urls = [f"{base}/p/{i}" for i in range(8)]
    pages = {
        f"{base}/sitemap.xml": (200, ("<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>").encode(), {}),
        f"{base}/": (200, ok, {}),
        f"{base}/catalogo": (200, ok, {}),
        f"{base}/api/public/store/tienda/checkout-config": (200, json.dumps({"methods": [{"code": "sinpe", "simulated": False}]}).encode(), {}),
        deep: (200, b'{"status": "ok"}', {}),
        **{u: (200, ok, {}) for u in urls},
    }
    monkeypatch.setattr(tc, "get", lambda url, timeout=25.0: pages.get(url, (404, b"no existe", {})))
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert tc.run(base, "tienda", 3, deep) == []
    # /catalogo con el fragmento SSI comprimido pegado y sin la app, pago simulado y /health/deep en 503
    pages[f"{base}/catalogo"] = (200, b"<html><head></head><body>\x1f\x8b\x08\x00\x00</body></html>", {})
    pages[f"{base}/api/public/store/tienda/checkout-config"] = (200, json.dumps({"methods": [{"code": "tarjeta", "simulated": True}]}).encode(), {})
    pages[deep] = (503, b'{"status": "falla"}', {})
    fails = tc.run(base, "tienda", 3, deep)
    texto = " | ".join(fails)
    assert "1f8b" in texto and "sin <title>" in texto and "/assets/" in texto
    assert "metodos simulados ['tarjeta']" in texto and "health/deep: estado 503 (falla)" in texto
    assert tc.main(["--base", base, "--slug", "tienda", "--sample", "3", "--deep", deep]) == 1


def test_monitor_de_la_tienda_descomprime_con_la_cabecera_en_minuscula(monkeypatch):
    """El get real contra un servidor local: un proxy puede mandar `content-encoding: gzip` en minuscula. Los nombres
    de cabecera no distinguen mayusculas; si el monitor los buscara tal cual, el cuerpo quedaria comprimido y cada
    pagina saldria con "1f8b", "sin <title>" y sin la app (falsa alarma cada 30 min). Tambien en un 404 (HTTPError)."""
    import gzip
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    tc = _tienda_check()
    ok = b'<html><head><title>Producto</title></head><body><script type="module" src="/assets/index.js"></script></body></html>'

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            rutas = {"/minuscula": (200, "content-encoding"), "/normal": (200, "Content-Encoding")}
            code, name = rutas.get(self.path, (404, "content-encoding"))  # /falta: 404 comprimido, en minuscula
            body = gzip.compress(ok if code == 200 else b"no existe")
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.send_header(name, "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    # sin proxies del sistema: la peticion va directo al servidor local
    monkeypatch.setattr(tc.urllib.request, "urlopen", urllib.request.build_opener(urllib.request.ProxyHandler({})).open)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        for ruta in ("/minuscula", "/normal"):
            st, body, headers = tc.get(base + ruta)
            assert st == 200 and body == ok and headers["content-encoding"] == "gzip"
            assert tc.check_page(base + ruta) == []
        st, body, headers = tc.get(base + "/falta")
        assert st == 404 and body == b"no existe" and headers["content-encoding"] == "gzip"
    finally:
        srv.shutdown()
        srv.server_close()
        hilo.join(timeout=5)


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False


def test_migracion_de_corridas_sube_y_baja():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.core.db import Base

    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("salud_*_corridas_de_tareas.py"))
    spec = importlib.util.spec_from_file_location("salud_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        mod.op = Operations(MigrationContext.configure(conn))
        mod.downgrade()
        assert "task_run" not in sa.inspect(conn).get_table_names()
        mod.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("task_run")}
        assert {"task", "task_id", "started_at", "finished_at", "ok", "summary", "error"} <= cols
    eng.dispose()
