"""Correo confiable (plan 1.2): deduplicacion por llave, espera creciente, "fallido" a los 6 intentos con aviso al
dueño del documento, y los correos con adjuntos nunca se reenvian sin ellos."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import EmailOutbox, Invoice, Tenant, TenantUser, User
from app.services import mail
from app.tasks.correo import MAX_AGE, retry_outbox


class Resend:
    """Resend en memoria: acepta todo salvo las direcciones en `malas` (422) o todo si `caido`."""

    def __init__(self):
        self.malas: set[str] = set()
        self.caido = False
        self.envios: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.envios.append(json)
        req = httpx.Request("POST", url)
        if self.caido or json["to"][0] in self.malas:
            return httpx.Response(422, json={"message": "correo inválido"}, request=req)
        return httpx.Response(200, json={"id": f"re_{len(self.envios)}"}, request=req)

    def a(self, addr: str) -> list[dict]:
        return [e for e in self.envios if e["to"][0] == addr]


@pytest.fixture
def resend(monkeypatch):
    r = Resend()
    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(mail.httpx, "post", r.post)
    return r


@pytest.fixture
def t(db_session):
    return db_session.scalar(select(Tenant).order_by(Tenant.id))


def _vendedora(db, t, email="vendedora@ejemplo.com"):
    u = User(email=email, full_name="Vendedora", password_hash="x")
    db.add(u)
    db.flush()
    db.add(TenantUser(tenant_id=t.id, user_id=u.id, role_code="ventas"))
    db.flush()
    return u


def _factura(db, t, seller_id=None, number="FEC-000901"):
    inv = Invoice(tenant_id=t.id, number=number, issue_date=date(2026, 10, 1), seller_id=seller_id)
    db.add(inv)
    db.flush()
    return inv


def _avisos(db, t):
    return db.scalars(select(EmailOutbox).where(EmailOutbox.tenant_id == t.id, EmailOutbox.entity == "outbox")).all()


# ---------- deduplicacion ----------
def test_la_misma_llave_devuelve_el_mismo_correo_y_no_sale_dos_veces(db_session, t, resend):
    a = mail.queue_email(db_session, t, "cliente@ejemplo.com", "Recordatorio", "<p>x</p>", "invoice", 1, dedupe_key="rem:1:D0")
    b = mail.queue_email(db_session, t, "cliente@ejemplo.com", "Recordatorio", "<p>x</p>", "invoice", 1, dedupe_key="rem:1:D0")
    assert a.id == b.id and len(resend.envios) == 1
    # sin llave (correo manual) no hay deduplicacion
    mail.queue_email(db_session, t, "cliente@ejemplo.com", "Manual", "<p>x</p>", "invoice", 1)
    mail.queue_email(db_session, t, "cliente@ejemplo.com", "Manual", "<p>x</p>", "invoice", 1)
    assert len(resend.envios) == 3
    # la llave es por empresa: otra empresa puede usar la misma
    otra = Tenant(slug="otra-empresa", name="Otra empresa")
    db_session.add(otra)
    db_session.flush()
    c = mail.queue_email(db_session, otra, "cliente@ejemplo.com", "Recordatorio", "<p>x</p>", "invoice", 1, dedupe_key="rem:1:D0")
    assert c.id != a.id and len(resend.envios) == 4


def test_llave_larga_se_acorta_sin_perder_unicidad():
    base = "aviso:" + "x" * 300
    k1, k2 = mail.dedupe(base + "a"), mail.dedupe(base + "b")
    assert len(k1) <= 200 and len(k2) <= 200 and k1 != k2
    assert mail.dedupe("  ") is None and mail.dedupe("corta") == "corta"


def test_notify_roles_con_llave_no_repite_a_nadie(db_session, t, resend):
    n1 = mail.notify_roles(db_session, t, ("admin",), "Aviso", "<p>x</p>", "inventory", 0, dedupe_key="inv:2026-10-20")
    n2 = mail.notify_roles(db_session, t, ("admin",), "Aviso", "<p>x</p>", "inventory", 0, dedupe_key="inv:2026-10-20")
    assert n1 == n2 >= 1
    assert len(resend.envios) == n1  # la segunda vuelta devolvio los mismos correos


# ---------- espera creciente y tope ----------
def test_espera_creciente():
    assert [mail.retry_wait(n) for n in range(1, 6)] == [timedelta(minutes=m) for m in (15, 30, 60, 120, 240)]


def test_direccion_mala_termina_en_fallido_a_los_6_intentos_con_aviso_al_vendedor(db_session, t, resend):
    v = _vendedora(db_session, t)
    inv = _factura(db_session, t, seller_id=v.id)
    resend.malas.add("malo@ejemplo.com")
    m = mail.queue_email(db_session, t, "malo@ejemplo.com", f"Recordatorio {inv.number}", "<p>x</p>", "invoice", inv.id)
    db_session.commit()
    assert m.status == "error" and m.attempts == 1
    t0 = datetime.now(UTC)
    assert abs((m.next_attempt_at.replace(tzinfo=UTC) - t0) - timedelta(minutes=15)) < timedelta(minutes=1)
    # antes de su hora no se toca
    assert retry_outbox(db_session, now=t0 + timedelta(minutes=5))["reintentos"] == 0
    assert len(resend.a("malo@ejemplo.com")) == 1
    reloj = t0
    for _ in range(5):
        reloj = m.next_attempt_at.replace(tzinfo=UTC) + timedelta(seconds=1)
        retry_outbox(db_session, now=reloj)
        db_session.refresh(m)
    assert len(resend.a("malo@ejemplo.com")) == 6  # 6 intentos en ~8 h, no 96 al dia
    assert m.status == "fallido" and m.attempts == 6 and m.next_attempt_at is None
    assert "6 veces" in m.error
    # ya no se reintenta nunca
    retry_outbox(db_session, now=reloj + timedelta(days=1))
    assert len(resend.a("malo@ejemplo.com")) == 6
    # un solo aviso, a la vendedora responsable, con el numero del documento
    avisos = _avisos(db_session, t)
    assert [a.to for a in avisos] == ["vendedora@ejemplo.com"] and inv.number in avisos[0].html


def test_un_reintento_que_sale_queda_enviado(db_session, t, resend):
    resend.caido = True
    m = mail.queue_email(db_session, t, "cliente@ejemplo.com", "Aviso", "<p>x</p>", "customer", 1)
    db_session.commit()
    assert m.status == "error"
    resend.caido = False
    out = retry_outbox(db_session, now=datetime.now(UTC) + timedelta(minutes=16))
    db_session.refresh(m)
    assert out["enviados"] == 1 and m.status == "enviado" and m.attempts == 2 and m.next_attempt_at is None


def test_documento_con_adjuntos_no_se_reenvia_sin_ellos(db_session, t, resend):
    v = _vendedora(db_session, t)
    inv = _factura(db_session, t, seller_id=v.id)
    resend.caido = True
    m = mail.queue_email(db_session, t, "cliente@ejemplo.com", f"Factura {inv.number}", "<p>x</p>", "invoice", inv.id, files=[(f"{inv.number}.pdf", b"%PDF")])
    db_session.commit()
    assert m.status == "error" and m.attachments == [{"name": f"{inv.number}.pdf"}]
    resend.caido = False
    retry_outbox(db_session, now=datetime.now(UTC) + timedelta(seconds=5))
    db_session.refresh(m)
    assert m.status == "fallido" and "adjuntos" in m.error
    assert len(resend.a("cliente@ejemplo.com")) == 1  # el unico envio fue el original, con el PDF
    assert [a.to for a in _avisos(db_session, t)] == ["vendedora@ejemplo.com"]


def test_aviso_agrupado_por_dueño_y_sin_dueño_va_a_los_administradores(db_session, t, resend):
    v = _vendedora(db_session, t)
    a = _factura(db_session, t, seller_id=v.id, number="FEC-000911")
    b = _factura(db_session, t, seller_id=v.id, number="FEC-000912")
    sin = _factura(db_session, t, number="FEC-000913")  # sin vendedor ni quien la tecleo
    resend.caido = True
    for inv in (a, b, sin):
        mail.queue_email(db_session, t, "cliente@ejemplo.com", inv.number, "<p>x</p>", "invoice", inv.id, files=[("x.pdf", b"%PDF")])
    db_session.commit()
    resend.caido = False
    out = retry_outbox(db_session, now=datetime.now(UTC) + timedelta(seconds=5))
    assert out["fallidos"] == 3
    avisos = {x.to: x for x in _avisos(db_session, t)}
    assert set(avisos) == {"vendedora@ejemplo.com", "admin@crimsonapp.com"}
    assert "FEC-000911" in avisos["vendedora@ejemplo.com"].html and "FEC-000912" in avisos["vendedora@ejemplo.com"].html
    assert "FEC-000913" in avisos["admin@crimsonapp.com"].html and "FEC-000911" not in avisos["admin@crimsonapp.com"].html


def test_un_aviso_que_falla_no_genera_otro_aviso(db_session, t, resend):
    """Proveedor caido todo el dia: los avisos de "no salio" tambien fallan, pero no hay una cadena de avisos."""
    inv = _factura(db_session, t)
    resend.caido = True
    mail.queue_email(db_session, t, "cliente@ejemplo.com", inv.number, "<p>x</p>", "invoice", inv.id, files=[("x.pdf", b"%PDF")])
    db_session.commit()
    reloj = datetime.now(UTC) + timedelta(seconds=5)
    retry_outbox(db_session, now=reloj)
    for _ in range(8):
        reloj += timedelta(hours=5)
        retry_outbox(db_session, now=reloj)
    avisos = _avisos(db_session, t)
    assert len(avisos) == 1 and avisos[0].status == "fallido"


def test_viejos_de_antes_del_cambio_no_salen(db_session, t, resend):
    """Filas en "error" de antes de los reintentos (0 intentos, sin hora): si llevan mas de 24 h se dejan, sin aviso."""
    viejo = EmailOutbox(tenant_id=t.id, to="cliente@ejemplo.com", subject="Recordatorio viejo", html="<p>x</p>", entity="invoice", entity_id=1, status="error")
    viejo.created_at = datetime.now(UTC) - MAX_AGE - timedelta(hours=1)
    reciente = EmailOutbox(tenant_id=t.id, to="otro@ejemplo.com", subject="Reciente", html="<p>x</p>", entity="customer", entity_id=1, status="error")
    db_session.add_all([viejo, reciente])
    db_session.commit()
    out = retry_outbox(db_session)
    db_session.refresh(viejo)
    db_session.refresh(reciente)
    assert viejo.status == "fallido" and out["vencidos"] == 1 and not resend.a("cliente@ejemplo.com")
    assert reciente.status == "enviado"
    assert not _avisos(db_session, t)


def test_invitaciones_y_enlaces_de_contraseña_no_se_reintentan(db_session, t, resend):
    resend.caido = True
    m = mail.queue_email(db_session, t, "nuevo@ejemplo.com", "Invitación", "<p>/invitacion/abcdefghijklmnopqrstu</p>", "invitation", 1)
    db_session.commit()
    resend.caido = False
    retry_outbox(db_session, now=datetime.now(UTC) + timedelta(days=1))
    db_session.refresh(m)
    assert m.status == "error" and len(resend.a("nuevo@ejemplo.com")) == 1


def test_el_mas_viejo_primero(db_session, t, resend):
    resend.caido = True
    a = mail.queue_email(db_session, t, "a@ejemplo.com", "A", "<p>x</p>", "customer", 1)
    b = mail.queue_email(db_session, t, "b@ejemplo.com", "B", "<p>x</p>", "customer", 2)
    a.created_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()
    resend.caido = False
    retry_outbox(db_session, now=datetime.now(UTC) + timedelta(minutes=16), limit=1)
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.status == "enviado" and b.status == "error"


def test_el_worker_usa_el_reintento_nuevo(db_session, monkeypatch):
    celery_app = _celery_app()
    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx(db_session))
    out = celery_app.retry_outbox()
    assert set(out) >= {"reintentos", "fallidos"}


# ---------- un solo beat ----------
def test_un_solo_beat_con_candado_en_redis():
    """redbeat: horario y candado en Redis; el candado vence a los 5 x el intervalo maximo (lo renueva cada vuelta)."""
    import os

    celery_app = _celery_app()
    conf = celery_app.celery.conf
    assert conf.beat_scheduler == "redbeat.RedBeatScheduler"
    assert conf.redbeat_lock_timeout == 5 * conf.beat_max_loop_interval
    assert conf.redbeat_redis_url
    dockerfile = open(os.path.join(_root(), "worker", "Dockerfile"), encoding="utf-8").read()
    cmd = next(x for x in dockerfile.splitlines() if x.startswith("CMD"))
    # en "celery worker" -S es --statedb: el planificador va con --scheduler; el horario ya no vive en /tmp
    assert "--scheduler=redbeat.RedBeatScheduler" in cmd and "--schedule=" not in cmd and '"-S"' not in cmd


def test_dos_beats_despachan_una_sola_vez():
    """Con un Redis real (REDBEAT_TEST_REDIS_URL): el segundo beat no consigue el candado mientras el primero vive."""
    import os

    url = os.environ.get("REDBEAT_TEST_REDIS_URL")
    if not url:
        pytest.skip("sin Redis de prueba (REDBEAT_TEST_REDIS_URL)")
    from celery import Celery
    from redbeat.schedulers import RedBeatScheduler, acquire_distributed_beat_lock, get_redis

    conf = _celery_app().celery.conf
    app = Celery("prueba-beat", broker=url)  # app aparte: redbeat guarda su configuracion en la app
    app.conf.update(
        redbeat_redis_url=url,
        redbeat_key_prefix=f"prueba-{os.getpid()}:",
        redbeat_lock_timeout=conf.redbeat_lock_timeout,
        beat_max_loop_interval=conf.beat_max_loop_interval,
    )
    a = RedBeatScheduler(app=app, lazy=True)
    b = RedBeatScheduler(app=app, lazy=True)
    acquire_distributed_beat_lock(sender=type("S", (), {"scheduler": a})())
    try:
        lock = get_redis(app).lock(b.lock_key, timeout=b.lock_timeout)
        assert lock.acquire(blocking=False) is False  # el segundo espera el candado: nada sale dos veces
    finally:
        a.close()


def _root():
    import os

    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _celery_app():
    import os
    import sys

    pytest.importorskip("celery")
    sys.path.insert(0, os.path.join(_root(), "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    return celery_app


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False
