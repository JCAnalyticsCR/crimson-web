"""Buzon de recepcion por UID: nunca marca nada en el servidor, un commit por correo, arriendo entre lectores,
evidencia (.eml) solo cuando algo fallo, reproceso, limites y configuracion por entorno."""

import io
import ssl
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import SecretStr

from tests.hacienda_utils import FakeImapUid, clave, email_with, fe_xml, role_headers

HOY = date.today()
FECHA = f"{HOY.isoformat()}T10:00:00-06:00"


def cl(n: int) -> str:
    return clave(fecha=HOY.strftime("%d%m%y"), consecutivo=f"0010000101{n:010d}")


def factura(n: int) -> bytes:
    return fe_xml(cl(n), fecha=FECHA, consecutivo=f"0010000101{n:010d}").encode()


def correo(n: int, **kw) -> bytes:
    return email_with([(f"factura-{n}.xml", factura(n), "xml")], subject=f"Factura {n}", **kw)


def _ajustes(client):
    r = client.put("/settings/inbox", json={"enabled": True, "host": "imap.ejemplo.com", "user": "facturas@ejemplo.com", "password": "clave-app-123"})
    assert r.status_code == 200 and r.json()["source"] == "ajustes"


def _tenant(db_session, auth):
    from app.models import Tenant

    return db_session.get(Tenant, auth["tenant"]["id"])


def test_tres_correos_sin_marcar_y_segunda_corrida(client, auth, db_session):
    from app.models import ReceptionMailbox, ReceptionMessage
    from app.services.inbox import poll

    _ajustes(client)
    sin_adjuntos = email_with([], subject="Boletín de ofertas")
    fake = FakeImapUid([correo(1), correo(2), sin_adjuntos], password="clave-app-123")
    t = _tenant(db_session, auth)
    res = poll(db_session, t, connect=lambda: fake)
    assert res["messages"] == 3 and res["nuevos"] == 2 and fake.stored == []
    assert fake.searches[0].startswith("SINCE ")  # primera corrida: solo los ultimos N dias
    mb = db_session.query(ReceptionMailbox).one()
    assert mb.last_uid == 3 and mb.uidvalidity == 1 and mb.locked_until is None
    msgs = db_session.query(ReceptionMessage).order_by(ReceptionMessage.uid).all()
    assert [m.status for m in msgs] == ["procesado", "procesado", "sin_comprobantes"]
    assert all(m.raw_eml is None for m in msgs)  # bien procesado o sin comprobantes: no se guarda el correo crudo
    # segunda corrida: el servidor devuelve el ultimo aunque no haya nuevos; no se duplica
    res = poll(db_session, t, connect=lambda: fake)
    assert res["messages"] == 0 and fake.searches[-1] == "UID 4:*"
    assert db_session.query(ReceptionMessage).count() == 3
    # el que no trae comprobantes no se lista por defecto
    assert client.get("/reception/messages").json()["total"] == 2
    assert client.get("/reception/messages", params={"all": 1}).json()["total"] == 3


def test_correo_que_revienta_queda_con_eml_y_se_reprocesa(client, auth, db_session, monkeypatch):
    from app.models import ReceptionMessage
    from app.services import reception_intake
    from app.services.inbox import poll

    _ajustes(client)
    real = reception_intake.ingest_files

    def falla(db, tenant, files, **kw):
        if any(b"0000000012" in data for _, data in files):
            raise RuntimeError("se cayo el parser")
        return real(db, tenant, files, **kw)

    monkeypatch.setattr(reception_intake, "ingest_files", falla)
    fake = FakeImapUid([correo(11), correo(12), correo(13)], password="clave-app-123")
    res = poll(db_session, _tenant(db_session, auth), connect=lambda: fake)
    assert res["nuevos"] == 2 and res["messages"] == 3
    malo = db_session.query(ReceptionMessage).filter(ReceptionMessage.status == "error").one()
    assert malo.raw_eml and malo.uid == 2
    monkeypatch.setattr(reception_intake, "ingest_files", real)
    r = client.post(f"/reception/messages/{malo.id}/reprocess")
    assert r.status_code == 200 and r.json()["message"]["status"] == "procesado" and len(r.json()["nuevos"]) == 1


def test_correo_gigante_no_se_descarga(client, auth, db_session):
    from app.models import ReceptionMessage
    from app.services.inbox import poll

    _ajustes(client)
    fake = FakeImapUid([correo(21), correo(22)], password="clave-app-123")
    fake.sizes[1] = 30 * 1024 * 1024
    res = poll(db_session, _tenant(db_session, auth), connect=lambda: fake)
    assert 1 not in fake.fetched_bodies and 2 in fake.fetched_bodies and res["nuevos"] == 1
    m = db_session.query(ReceptionMessage).filter(ReceptionMessage.uid == 1).one()
    assert m.status == "error" and "25 MB" in m.error and m.raw_eml is None


def test_arriendo(client, auth, db_session):
    from app.models import ReceptionMailbox
    from app.services.inbox import MailboxBusy, poll

    _ajustes(client)
    t = _tenant(db_session, auth)
    fake = FakeImapUid([correo(31)], password="clave-app-123")
    poll(db_session, t, connect=lambda: fake)  # crea el buzon
    mb = db_session.query(ReceptionMailbox).one()
    mb.locked_until, mb.locked_by = datetime.now(UTC) + timedelta(minutes=5), "otro-proceso"
    db_session.commit()
    with pytest.raises(MailboxBusy):
        poll(db_session, t, connect=lambda: fake)
    assert client.post("/reception/inbox/run").status_code == 409
    mb.locked_until = datetime.now(UTC) - timedelta(minutes=1)  # arriendo vencido (proceso muerto): se toma
    db_session.commit()
    poll(db_session, t, connect=lambda: fake)
    db_session.refresh(mb)
    assert mb.locked_until is None


def test_uid_ya_registrado_por_otro_proceso(client, auth, db_session):
    from app.models import ReceptionMailbox, ReceptionMessage
    from app.services.inbox import poll

    _ajustes(client)
    t = _tenant(db_session, auth)
    fake = FakeImapUid([correo(41)], password="clave-app-123")
    poll(db_session, t, connect=lambda: FakeImapUid([], password="clave-app-123"))
    mb = db_session.query(ReceptionMailbox).one()
    mb.uidvalidity = 1
    db_session.add(ReceptionMessage(tenant_id=t.id, mailbox_id=mb.id, source="imap", uid=1, uidvalidity=1, sha256="x" * 64, status="procesado", size=1))
    db_session.commit()
    poll(db_session, t, connect=lambda: fake)
    db_session.refresh(mb)
    assert mb.last_uid == 1
    assert db_session.query(ReceptionMessage).count() == 1 and db_session.query(ReceptionMessage).filter(ReceptionMessage.status == "error").count() == 0


def test_uidvalidity_nueva_no_duplica(client, auth, db_session):
    from app.models import ReceptionMessage
    from app.services.inbox import poll

    _ajustes(client)
    t = _tenant(db_session, auth)
    m1 = correo(51)
    poll(db_session, t, connect=lambda: FakeImapUid([m1], password="clave-app-123", uidvalidity=1))
    poll(db_session, t, connect=lambda: FakeImapUid([m1], password="clave-app-123", uidvalidity=2))
    assert db_session.query(ReceptionMessage).count() == 1


def test_reenviado_zip_y_xxe(client, auth, db_session):
    from email.mime.message import MIMEMessage
    from email.mime.multipart import MIMEMultipart

    from app.models import ReceptionMessage
    from app.services.inbox import poll

    _ajustes(client)
    interno = email_with([("f.xml", factura(61), "xml")])
    import email as _email

    fwd = MIMEMultipart()
    fwd["Subject"] = "RV: factura"
    fwd.attach(MIMEMessage(_email.message_from_bytes(interno)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("factura.xml", factura(62))
    con_zip = email_with([("todo.zip", buf.getvalue(), "zip")])
    xxe = email_with([("malo.xml", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>', "xml")])
    fake = FakeImapUid([fwd.as_bytes(), con_zip, xxe], password="clave-app-123")
    res = poll(db_session, _tenant(db_session, auth), connect=lambda: fake)
    assert res["nuevos"] == 2 and any("DTD" in e for e in res["errores"])
    malo = db_session.query(ReceptionMessage).filter(ReceptionMessage.uid == 3).one()
    assert malo.status == "error" and malo.raw_eml


def test_ssl_verificado_entorno_y_apagado(client, auth, db_session, monkeypatch):
    import imaplib

    from app.core.config import settings
    from app.services import inbox

    capt = {}

    class Capt:
        def __init__(self, host, port, **kw):
            capt.update(kw, host=host, port=port)

        def __getattr__(self, name):
            raise RuntimeError("solo se prueba la conexion")

    t = _tenant(db_session, auth)
    assert inbox.mailbox_config(db_session, t) is None  # sin nada: apagado
    with pytest.raises(ValueError):
        inbox.poll(db_session, t)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Capt)
    _ajustes(client)
    cfg = inbox.mailbox_config(db_session, t)
    assert cfg.source == "ajustes"
    inbox._connect(cfg)
    ctx = capt["ssl_context"]
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    # el de entorno manda sobre Ajustes
    monkeypatch.setattr(settings, "reception_imap_host", "imap.servidor.example")
    monkeypatch.setattr(settings, "reception_imap_user", "facturas@servidor.example")
    monkeypatch.setattr(settings, "reception_imap_password", SecretStr("otra-clave"))
    cfg = inbox.mailbox_config(db_session, t)
    assert cfg.source == "env" and cfg.host == "imap.servidor.example" and "otra-clave" not in repr(cfg)
    assert client.get("/settings/inbox").json()["source"] == "env"
    st = client.get("/reception/inbox/status").json()
    assert st["source"] == "env" and st["mailbox"] == "facturas@servidor.example@imap.servidor.example/INBOX"


def test_revisar_ahora_contabilidad_y_limitador(client, auth, db_session, monkeypatch):
    from app.services import inbox

    _ajustes(client)
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.buzon@ejemplo.com", "contabilidad")
    monkeypatch.setattr(inbox, "_connect", lambda cfg: FakeImapUid([correo(71)], password="clave-app-123"))
    r = client.post("/reception/inbox/run", headers=conta)
    assert r.status_code == 200 and r.json()["nuevos"] == 1  # antes era solo admin
    codes = [client.post("/reception/inbox/run", headers=conta).status_code for _ in range(4)]
    assert codes[-1] == 429


def test_password_del_buzon_con_secretbox_y_sin_fuga(client, auth, db_session):
    _ajustes(client)
    t = _tenant(db_session, auth)
    db_session.refresh(t)
    enc = t.settings["inbox"]["password_enc"]
    assert enc.startswith("v1.") and "clave-app-123" not in enc
    s = client.get("/settings").json()
    assert "password_enc" not in str(s) and "password_enc" not in str(client.put("/settings", json={"quote_valid_days": 16}).json())


def test_paso_de_ajustes_a_entorno_no_duplica(client, auth, db_session, monkeypatch):
    """§6.4 paso 4 (runbook §15 paso 6): al pasar el buzon a variables de entorno hay otra fila de buzon que empieza en 0
    y vuelve a bajar los ultimos N dias; el sha256 se busca en TODA la empresa y no se duplica nada."""
    from app.core.config import settings
    from app.models import ReceptionMailbox, ReceptionMessage
    from app.services import inbox

    _ajustes(client)
    t = _tenant(db_session, auth)
    msgs = [correo(81), correo(82)]
    inbox.poll(db_session, t, connect=lambda: FakeImapUid(msgs, password="clave-app-123"))
    assert db_session.query(ReceptionMessage).count() == 2
    monkeypatch.setattr(settings, "reception_imap_host", "imap.ejemplo.com")
    monkeypatch.setattr(settings, "reception_imap_user", "facturas@ejemplo.com")
    monkeypatch.setattr(settings, "reception_imap_password", SecretStr("clave-app-123"))
    assert inbox.mailbox_config(db_session, t).source == "env"
    res = inbox.poll(db_session, t, connect=lambda: FakeImapUid(msgs, password="clave-app-123"))
    assert db_session.query(ReceptionMailbox).count() == 2  # otra llave (env:), empezo en 0
    assert res["messages"] == 0 and res["nuevos"] == 0 and db_session.query(ReceptionMessage).count() == 2
    env_box = db_session.query(ReceptionMailbox).filter(ReceptionMailbox.key.startswith("env:")).one()
    assert env_box.last_uid == 2  # avanzo sin registrar de nuevo
