"""Plan 1.10 · 2FA: con el 2FA activo, /auth/2fa/setup exige contrasena y codigo actual; el 2FA anterior sigue activo
hasta que /2fa/verify confirma el nuevo; aviso por correo; un codigo usado no se repite.
Reproduce qa-juicio/seguridad_2fa_check.py: con solo la sesion ya no se apaga el 2FA."""

from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select

from app.models import AuditLog, EmailOutbox, User
from tests.conftest import ADMIN


def _codigo(secret: str, segundos: int = 0) -> str:
    return pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=segundos))


def _activar(client) -> str:
    sec = client.post("/auth/2fa/setup").json()["secret"]
    r = client.post("/auth/2fa/verify", json={"code": _codigo(sec)})
    assert r.status_code == 200 and len(r.json()["codes"]) == 10
    return sec


def _login(client, **extra):
    h = client.headers.pop("Authorization", None)
    try:
        return client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1], **extra})
    finally:
        if h:
            client.headers["Authorization"] = h


def _avisos(db_session, uid):
    return db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "user_security", EmailOutbox.entity_id == uid)).all()


def test_con_solo_la_sesion_no_se_apaga_el_2fa(client, auth, db_session):
    sec = _activar(client)
    assert _login(client).headers.get("X-2FA") == "required"
    # la reproduccion: setup con solo la sesion (sin contrasena ni codigo)
    r = client.post("/auth/2fa/setup")
    assert r.status_code == 403
    u = db_session.get(User, auth["user"]["id"])
    assert u.totp_enabled is True and u.totp_secret == sec and u.totp_pending_secret is None
    sin = _login(client)
    assert sin.status_code == 401 and sin.headers.get("X-2FA") == "required"  # el 2FA sigue exigido
    # contrasena sin codigo, o codigo sin contrasena: tampoco
    assert client.post("/auth/2fa/setup", json={"password": ADMIN[1]}).status_code == 403
    assert client.post("/auth/2fa/setup", json={"code": _codigo(sec, 30)}).status_code == 403
    assert client.post("/auth/2fa/setup", json={"password": "otra-cosa-123", "code": _codigo(sec, 30)}).status_code == 403
    assert db_session.get(User, auth["user"]["id"]).totp_pending_secret is None


def test_cambio_de_telefono_no_apaga_el_anterior_hasta_confirmar(client, auth, db_session):
    uid = auth["user"]["id"]
    sec = _activar(client)
    avisos_antes = len(_avisos(db_session, uid))
    r = client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": _codigo(sec, 30)})
    assert r.status_code == 200
    nuevo = r.json()["secret"]
    assert nuevo != sec
    u = db_session.get(User, uid)
    assert u.totp_enabled is True and u.totp_secret == sec and u.totp_pending_secret == nuevo
    # mientras no se confirme, la app vieja sigue siendo la que vale para entrar
    assert _login(client, totp_code=_codigo(nuevo, -30)).status_code == 401
    # un codigo equivocado no confirma nada
    assert client.post("/auth/2fa/verify", json={"code": "000000" if _codigo(nuevo) != "000000" else "111111"}).status_code == 400
    assert db_session.get(User, uid).totp_secret == sec
    r = client.post("/auth/2fa/verify", json={"code": _codigo(nuevo)})
    assert r.status_code == 200 and len(r.json()["codes"]) == 10
    u = db_session.get(User, uid)
    assert u.totp_secret == nuevo and u.totp_pending_secret is None and u.totp_enabled is True
    # la app vieja ya no sirve; la nueva si (ventana siguiente: la de la confirmacion ya se uso)
    assert _login(client, totp_code=_codigo(sec, 30)).status_code == 401
    assert _login(client, totp_code=_codigo(nuevo, 30)).status_code == 200
    # correo "cambio tu verificacion en dos pasos" y constancia en la bitacora
    avisos = _avisos(db_session, uid)
    assert len(avisos) == avisos_antes + 1 and "verificación en dos pasos" in avisos[-1].subject
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "2fa_change", AuditLog.entity_id == uid)).first() is not None


def test_cambio_con_codigo_de_recuperacion(client, auth, db_session):
    """Sin la app vieja a mano: contrasena + un codigo de recuperacion (que se consume)."""
    sec = client.post("/auth/2fa/setup").json()["secret"]
    codes = client.post("/auth/2fa/verify", json={"code": _codigo(sec)}).json()["codes"]
    r = client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": codes[0]})
    assert r.status_code == 200
    assert len(db_session.get(User, auth["user"]["id"]).totp_recovery) == 9
    assert client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": codes[0]}).status_code == 403  # ya se uso


def test_un_codigo_usado_no_se_repite(client, auth, db_session):
    sec = _activar(client)
    codigo = _codigo(sec, 30)  # la ventana siguiente (la actual ya la uso la activacion)
    assert _login(client, totp_code=codigo).status_code == 200
    otra = _login(client, totp_code=codigo)
    assert otra.status_code == 401 and "ya se usó" in otra.json()["detail"]
    # repetir el codigo no suma como intento fallido ni traba a la cuenta
    assert db_session.get(User, auth["user"]["id"]).failed_logins == 0
    # el codigo de la activacion tampoco sirve para entrar
    u = db_session.get(User, auth["user"]["id"])
    assert u.totp_last_step is not None


def test_apagar_avisa_por_correo_y_pide_contrasena(client, auth, db_session):
    uid = auth["user"]["id"]
    _activar(client)
    n = len(_avisos(db_session, uid))
    assert n >= 1  # la activacion tambien avisa
    assert client.post("/auth/2fa/disable", json={"password": "otra-cosa-123"}).status_code == 400
    assert client.post("/auth/2fa/disable", json={"password": ADMIN[1]}).status_code == 204
    u = db_session.get(User, uid)
    assert u.totp_enabled is False and u.totp_secret is None and u.totp_pending_secret is None
    assert len(_avisos(db_session, uid)) == n + 1
    assert _login(client).status_code == 200


def test_primera_vez_basta_la_sesion_y_setup_repetido_no_rompe(client, auth, db_session):
    """Sin 2FA activo (la pantalla forzada) no se pide contrasena; pedir otro secreto reemplaza el pendiente."""
    a = client.post("/auth/2fa/setup")
    b = client.post("/auth/2fa/setup", json={})
    assert a.status_code == 200 and b.status_code == 200
    viejo, nuevo = a.json()["secret"], b.json()["secret"]
    assert db_session.get(User, auth["user"]["id"]).totp_pending_secret == nuevo  # el ultimo reemplaza al anterior
    if _codigo(viejo) not in {_codigo(nuevo, s) for s in (-30, 0, 30)}:  # (coincidencia de 1 en un millon)
        assert client.post("/auth/2fa/verify", json={"code": _codigo(viejo)}).status_code == 400
    assert client.post("/auth/2fa/verify", json={"code": _codigo(nuevo)}).status_code == 200
    # ya activo: verify sin un setup nuevo no hace nada
    assert client.post("/auth/2fa/verify", json={"code": _codigo(nuevo, 30)}).status_code == 400


def test_setup_de_antes_del_despliegue_se_puede_confirmar(client, auth, db_session):
    """Compatibilidad: el setup viejo dejaba el secreto en totp_secret con el 2FA apagado."""
    sec = pyotp.random_base32()
    u = db_session.get(User, auth["user"]["id"])
    u.totp_secret, u.totp_enabled = sec, False
    db_session.commit()
    assert client.post("/auth/2fa/verify", json={"code": _codigo(sec)}).status_code == 200
    assert db_session.get(User, auth["user"]["id"]).totp_enabled is True
