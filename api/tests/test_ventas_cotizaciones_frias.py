"""Cotizaciones frias (plan maestro 1.6, app/tasks/ventas.py): "cotizacion lleva cinco dias sin seguimiento, que se le
avise a la vendedora". Cuenta desde el ULTIMO envio, nunca avisa de las que el cliente ya acepto o rechazo (ni de las
convertidas o con proyecto), le llega a la vendedora correcta (no a quien la tecleo) y no se repite antes de 5 dias."""

import os
import sys
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models import AuditLog, EmailOutbox, Opportunity, Quote, QuoteVersion, Tenant, TenantUser, User
from app.services.documents import CR, local_date
from app.tasks import ventas
from tests.test_campo import _login

BASE = "https://panel.ejemplo.com"
AHORA = datetime.now(UTC)  # la bitacora guarda la hora real: las corridas se simulan alrededor de ahora


def _avisos(db, to: str) -> list[EmailOutbox]:
    db.expire_all()
    return list(db.scalars(select(EmailOutbox).where(EmailOutbox.to == to, EmailOutbox.subject.contains("sin respuesta del cliente")).order_by(EmailOutbox.id)))


def _enviada(client, db, h, cust_id: int, hace_dias: float, **extra) -> dict:
    """Cotizacion enviada (marcada a mano) cuyo ultimo envio fue hace `hace_dias`."""
    q = client.post(
        "/quotes", headers=h, json={"customer_id": cust_id, "lines": [{"name": "Cámara IP", "quantity": 1, "unit_price": 50000, "tax_rate": 13}], **extra}
    )
    assert q.status_code == 201, q.text
    q = q.json()
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=h).status_code == 200
    v = db.scalar(select(QuoteVersion).where(QuoteVersion.quote_id == q["id"]).order_by(QuoteVersion.version.desc()))
    v.sent_at = AHORA - timedelta(days=hace_dias)
    db.commit()
    return q


def test_cotizaciones_frias_a_la_vendedora_correcta_y_sin_repetir(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, "vendedora-fria@ejemplo.com", "ventas")
    _login(client, db_session, tid, "otra-vendedora@ejemplo.com", "ventas")
    otra_id = db_session.scalar(select(User).where(User.email == "otra-vendedora@ejemplo.com")).id
    cust = client.get("/customers").json()["items"][0]
    t = db_session.get(Tenant, tid)

    fria = _enviada(client, db_session, ven, cust["id"], 6)
    reciente = _enviada(client, db_session, ven, cust["id"], 2)
    aceptada = _enviada(client, db_session, ven, cust["id"], 9)
    rechazada = _enviada(client, db_session, ven, cust["id"], 9)
    con_proyecto = _enviada(client, db_session, ven, cust["id"], 9)
    # la tecleo el admin pero es de la otra vendedora: el aviso es para ella, no para quien la tecleo
    ajena = _enviada(client, db_session, dict(client.headers), cust["id"], 7, seller_id=otra_id)
    for q, st in ((aceptada, "aceptada"), (rechazada, "rechazada")):
        r = client.post(f"/quotes/{q['id']}/acceptance", json={"status": st, "contact_name": "Cliente", "channel": "correo"}, headers=ven)
        assert r.status_code == 200, r.text
    assert client.post(f"/quotes/{con_proyecto['id']}/project", json={}).status_code == 201

    # reenviada hace poco (version nueva): cuenta desde el ultimo envio, no desde la fecha de emision
    reenviada = _enviada(client, db_session, ven, cust["id"], 10)
    db_session.add(
        QuoteVersion(
            tenant_id=tid,
            quote_id=reenviada["id"],
            version=2,
            content_hash="x" * 64,
            snapshot={},
            channel="whatsapp",
            sent_at=AHORA - timedelta(days=1),
        )
    )
    db_session.commit()

    frias = {q.id for q, _ in ventas.cotizaciones_frias(db_session, tid, AHORA)}
    assert fria["id"] in frias and ajena["id"] in frias
    for q in (reciente, aceptada, rechazada, con_proyecto, reenviada):
        assert q["id"] not in frias, q["number"]

    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA) == 2
    db_session.commit()
    mios = _avisos(db_session, "vendedora-fria@ejemplo.com")
    assert len(mios) == 1 and fria["number"] in mios[0].html and f"{BASE}/cotizaciones/{fria['id']}" in mios[0].html
    assert ajena["number"] not in mios[0].html and aceptada["number"] not in mios[0].html
    assert "hace 6 días" in mios[0].html
    suyos = _avisos(db_session, "otra-vendedora@ejemplo.com")
    assert len(suyos) == 1 and ajena["number"] in suyos[0].html
    assert _avisos(db_session, auth["user"]["email"]) == []  # quien la tecleo no recibe el de otra
    # queda en la bitacora de cada cotizacion (es lo que evita repetir)
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == ventas.ACCION_FRIA, AuditLog.entity_id == fria["id"])) is not None

    # la corrida de manana no repite; a los 5 dias vuelve a avisar si sigue fria
    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA + timedelta(days=1)) == 0
    db_session.commit()
    assert len(_avisos(db_session, "vendedora-fria@ejemplo.com")) == 1
    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA + timedelta(days=5, minutes=1)) >= 2
    db_session.commit()
    assert len(_avisos(db_session, "vendedora-fria@ejemplo.com")) == 2


def test_cotizacion_fria_sin_versiones_y_vendedora_inactiva(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, "vendedora-baja@ejemplo.com", "ventas")
    cust = client.get("/customers").json()["items"][0]
    t = db_session.get(Tenant, tid)
    q = _enviada(client, db_session, ven, cust["id"], 8)
    # de antes de las versiones: cuenta su fecha de emision
    db_session.execute(QuoteVersion.__table__.delete().where(QuoteVersion.quote_id == q["id"]))
    db_session.get(Quote, q["id"]).issue_date = (AHORA - timedelta(days=10)).date()
    # la vendedora ya no esta: la lista va a administracion para reasignarla
    uid = db_session.scalar(select(User).where(User.email == "vendedora-baja@ejemplo.com")).id
    db_session.scalar(select(TenantUser).where(TenantUser.user_id == uid)).active = False
    db_session.commit()
    assert q["id"] in {x.id for x, _ in ventas.cotizaciones_frias(db_session, tid, AHORA)}
    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA) == 1
    db_session.commit()
    assert _avisos(db_session, "vendedora-baja@ejemplo.com") == []
    admin = _avisos(db_session, auth["user"]["email"])
    assert len(admin) == 1 and q["number"] in admin[0].html and "ya no está activa" in admin[0].html


def test_cotizacion_fria_cuenta_dias_de_costa_rica(client, auth, db_session):
    """Borde de las 18:00-24:00 CR: un envio de las 8:30 p. m. CR ya es del dia siguiente en UTC. Los 5 dias (y los 5
    sin repetir) se cuentan en el calendario de Costa Rica, a la hora del beat (07:45 CR)."""
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, "vendedora-noche@ejemplo.com", "ventas")
    cust = client.get("/customers").json()["items"][0]
    t = db_session.get(Tenant, tid)
    hoy = local_date(datetime.now(UTC))
    beat = datetime.combine(hoy, time(7, 45), tzinfo=CR)

    def _enviada_el(dia: date, hora: time) -> dict:
        q = _enviada(client, db_session, ven, cust["id"], 0)
        v = db_session.scalar(select(QuoteVersion).where(QuoteVersion.quote_id == q["id"]))
        v.sent_at = datetime.combine(dia, hora, tzinfo=CR).astimezone(UTC)
        db_session.commit()
        return q

    noche = _enviada_el(hoy - timedelta(days=5), time(20, 30))  # en UTC seria hace 4 dias
    casi = _enviada_el(hoy - timedelta(days=4), time(23, 59))
    frias = {q.id for q, _ in ventas.cotizaciones_frias(db_session, tid, beat)}
    assert noche["id"] in frias and casi["id"] not in frias

    assert ventas.avisar_cotizaciones_frias(db_session, t, beat) == 1
    db_session.commit()
    mios = _avisos(db_session, "vendedora-noche@ejemplo.com")
    assert len(mios) == 1 and noche["number"] in mios[0].html and "hace 5 días" in mios[0].html
    # avisada hoy: la corrida del dia 4 no la repite (pero ya avisa la de las 23:59); la del dia 5 si, aunque corra antes
    assert ventas.avisar_cotizaciones_frias(db_session, t, beat + timedelta(days=4)) == 1
    db_session.commit()
    assert noche["number"] not in _avisos(db_session, "vendedora-noche@ejemplo.com")[-1].html
    # (la bitacora guarda la hora real: las dos quedaron avisadas hoy, asi que el dia 5 vuelven las dos)
    assert ventas.avisar_cotizaciones_frias(db_session, t, beat + timedelta(days=5, seconds=-30)) == 2
    db_session.commit()
    assert noche["number"] in _avisos(db_session, "vendedora-noche@ejemplo.com")[-1].html


def test_oportunidad_cerrada_no_deja_fria_su_cotizacion(client, auth, db_session):
    """Perder (o ganar) la oportunidad en el embudo no toca la cotizacion, que sigue "enviada" y "pendiente": no es
    una cotizacion sin seguimiento y avisarla cada 5 dias seria fatiga de alertas."""
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, "vendedora-cerrada@ejemplo.com", "ventas")
    cust = client.get("/customers").json()["items"][0]
    abierta, perdida, ganada = (_enviada(client, db_session, ven, cust["id"], 9) for _ in range(3))
    for q, st in ((abierta, "negociacion"), (perdida, "perdida"), (ganada, "ganada")):
        o = client.post("/opportunities", json={"title": f"Oportunidad {st}", "customer_id": cust["id"]}, headers=ven).json()
        fila = db_session.get(Opportunity, o["id"])
        fila.quote_id, fila.status = q["id"], st
    db_session.commit()
    frias = {q.id for q, _ in ventas.cotizaciones_frias(db_session, tid, AHORA)}
    assert abierta["id"] in frias and perdida["id"] not in frias and ganada["id"] not in frias


def test_reenvio_sin_cambios_cuenta_como_ultimo_envio(client, auth, db_session):
    """Reenviar la misma cotizacion (misma huella) no crea version ni toca sent_at: queda como "resend_version" en la
    bitacora. Es seguimiento: cuenta como el ultimo envio."""
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, "vendedora-reenvio@ejemplo.com", "ventas")
    cust = client.get("/customers").json()["items"][0]
    t = db_session.get(Tenant, tid)
    q = _enviada(client, db_session, ven, cust["id"], 9)
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=ven).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(QuoteVersion).where(QuoteVersion.quote_id == q["id"])) == 1
    reenvio = db_session.scalar(select(AuditLog).where(AuditLog.action == "resend_version", AuditLog.entity_id == q["id"]))
    reenvio.at = AHORA - timedelta(days=4)  # el reenvio fue al dia 5 del primer envio (hace 9)
    db_session.commit()
    assert q["id"] not in {x.id for x, _ in ventas.cotizaciones_frias(db_session, tid, AHORA)}
    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA) == 0
    # a los 5 dias del reenvio si esta fria, y el correo cuenta desde el reenvio
    assert q["id"] in {x.id for x, _ in ventas.cotizaciones_frias(db_session, tid, AHORA + timedelta(days=1))}
    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA + timedelta(days=1)) == 1
    db_session.commit()
    mios = _avisos(db_session, "vendedora-reenvio@ejemplo.com")
    assert len(mios) == 1 and "hace 5 días" in mios[0].html


def test_vendedora_que_ya_no_ve_la_cotizacion_no_recibe_montos(client, auth, db_session):
    """Le cambiaron el rol (p. ej. a tecnico, que no ve cotizaciones ni precios): el total y los enlaces no le llegan;
    la lista va a administracion para reasignarla."""
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, "vendedora-tecnico@ejemplo.com", "ventas")
    cust = client.get("/customers").json()["items"][0]
    t = db_session.get(Tenant, tid)
    q = _enviada(client, db_session, ven, cust["id"], 7)
    ella = db_session.scalar(select(User).where(User.email == "vendedora-tecnico@ejemplo.com")).id
    db_session.scalar(select(TenantUser).where(TenantUser.user_id == ella, TenantUser.tenant_id == tid)).role_code = "tecnico"
    db_session.commit()
    assert ventas.avisar_cotizaciones_frias(db_session, t, AHORA) >= 1
    db_session.commit()
    assert _avisos(db_session, "vendedora-tecnico@ejemplo.com") == []
    admin = _avisos(db_session, auth["user"]["email"])
    assert len(admin) == 1 and q["number"] in admin[0].html and "ya no las puede ver" in admin[0].html


def test_worker_programa_y_corre_las_cotizaciones_frias(db_session, monkeypatch):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pytest.importorskip("celery")
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    assert "crm.stale_quotes" in celery_app.celery.tasks
    assert celery_app.celery.conf.beat_schedule["stale-quotes"]["task"] == "crm.stale_quotes"
    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx(db_session))
    assert isinstance(ventas.stale_quotes(), int)


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False
