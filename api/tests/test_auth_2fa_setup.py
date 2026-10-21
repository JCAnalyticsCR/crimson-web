"""Plan 1.10 · 2FA: con el 2FA activo, /auth/2fa/setup exige contrasena y codigo actual; el 2FA anterior sigue activo
hasta que /2fa/verify confirma el nuevo; aviso por correo; un codigo usado no se repite (ni con dos pedidos a la vez).
Reproduce qa-juicio/seguridad_2fa_check.py: con solo la sesion ya no se apaga el 2FA, y con la sesion y la contrasena
(sin el telefono) tampoco se lo queda otra persona fabricando codigos de recuperacion."""

from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select

from app.core.ratelimit import login_limiter
from app.core.security import hash_password
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
    assert login_limiter.account_failures(ADMIN[0]) == 0
    # el codigo de la activacion tampoco sirve para entrar
    u = db_session.get(User, auth["user"]["id"])
    assert u.totp_last_step is not None


def test_apagar_avisa_por_correo_y_pide_contrasena_y_codigo(client, auth, db_session):
    uid = auth["user"]["id"]
    sec = _activar(client)
    n = len(_avisos(db_session, uid))
    assert n >= 1  # la activacion tambien avisa
    # admin: su rol lo exige (por defecto), no se apaga ni con la contrasena; se cambia de telefono
    r = client.post("/auth/2fa/disable", json={"password": ADMIN[1]})
    assert r.status_code == 409 and "Cambiar de teléfono" in r.json()["detail"]
    assert db_session.get(User, uid).totp_enabled is True
    # la empresa deja de exigirlo (aflojarlo pide la contrasena del admin): ya se puede apagar (con la contrasena)
    assert client.put("/settings", json={"twofa_roles": ["contabilidad"], "admin_password": ADMIN[1]}).status_code == 200
    n = len(_avisos(db_session, uid))  # el aviso a los admins de la politica nueva
    codigo = _codigo(sec, 30)  # la ventana siguiente: la actual la uso la activacion
    assert client.post("/auth/2fa/disable", json={"password": "otra-cosa-123", "code": codigo}).status_code == 403
    # con la contrasena sola (o un codigo inventado) no se apaga: es el mismo requisito que cambiar de telefono
    assert client.post("/auth/2fa/disable", json={"password": ADMIN[1]}).status_code == 403
    assert client.post("/auth/2fa/disable", json={"password": ADMIN[1], "code": "ABCD-2345"}).status_code == 403
    assert db_session.get(User, uid).totp_enabled is True
    assert client.post("/auth/2fa/disable", json={"password": ADMIN[1], "code": codigo}).status_code == 204
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


def test_obligatoria_en_otra_empresa_tampoco_se_apaga(client, auth, db_session):
    """El 2FA es de la persona: si en alguna de sus empresas su rol lo exige, no se apaga desde ninguna."""
    from app.models import Tenant, TenantUser

    sec = pyotp.random_base32()
    u = User(email="dos.empresas@ejemplo.com", full_name="Persona de prueba", password_hash=hash_password("Usuario-2026-ok"))
    u.totp_secret, u.totp_enabled = sec, True
    db_session.add(u)
    otra = Tenant(slug="otra-empresa-2fa", name="Otra empresa de prueba")
    db_session.add(otra)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=auth["tenant"]["id"], user_id=u.id, role_code="ventas"))
    db_session.add(TenantUser(tenant_id=otra.id, user_id=u.id, role_code="contabilidad"))
    db_session.commit()
    from fastapi.testclient import TestClient

    c = TestClient(client.app)
    r = c.post("/auth/login", json={"email": u.email, "password": "Usuario-2026-ok", "totp_code": _codigo(sec), "tenant_slug": auth["tenant"]["slug"]})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    assert c.get("/auth/me").json()["twofa"]["required"] is False  # en esta empresa es de ventas
    assert c.post("/auth/2fa/disable", json={"password": "Usuario-2026-ok"}).status_code == 409
    assert db_session.get(User, u.id).totp_enabled is True


def test_sin_el_telefono_no_se_fabrican_codigos_ni_se_cambia_el_2fa(client, auth, db_session):
    """La cadena con la sesion y la contrasena pero sin el telefono: pedir codigos de recuperacion nuevos (para usar uno
    en "Cambiar de telefono" y despues confirmar con la app del atacante) ya no funciona."""
    uid = auth["user"]["id"]
    sec = _activar(client)
    viejos = list(db_session.get(User, uid).totp_recovery)
    # 1. codigos nuevos con solo la contrasena: no
    assert client.post("/auth/2fa/recovery-codes", json={"password": ADMIN[1]}).status_code == 403
    # ni con un codigo inventado, ni con uno de recuperacion en lugar del de la app
    assert client.post("/auth/2fa/recovery-codes", json={"password": ADMIN[1], "code": "ABCD-2345"}).status_code == 403
    # 2. y sin un codigo de recuperacion real, "Cambiar de telefono" tampoco
    assert client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": "ABCD-2345"}).status_code == 403
    # 3. sin secreto pendiente, confirmar con la app del atacante no cambia nada
    atacante = pyotp.random_base32()
    assert client.post("/auth/2fa/verify", json={"code": _codigo(atacante)}).status_code == 400
    u = db_session.get(User, uid)
    assert u.totp_enabled and u.totp_secret == sec and u.totp_pending_secret is None and u.totp_recovery == viejos


def test_codigos_nuevos_con_el_codigo_de_la_app_avisan_y_lo_gastan(client, auth, db_session):
    uid = auth["user"]["id"]
    sec = _activar(client)
    n = len(_avisos(db_session, uid))
    codigo = _codigo(sec, 30)  # la ventana siguiente: la actual la uso la activacion
    assert client.post("/auth/2fa/recovery-codes", json={"password": "otra-cosa-123", "code": codigo}).status_code == 403
    r = client.post("/auth/2fa/recovery-codes", json={"password": ADMIN[1], "code": codigo})
    assert r.status_code == 200 and len(r.json()["codes"]) == 10
    avisos = _avisos(db_session, uid)
    assert len(avisos) == n + 1 and "códigos de recuperación" in avisos[-1].subject
    # ese codigo ya se uso: no sirve otra vez (ni para codigos ni para cambiar de telefono ni para entrar)
    assert client.post("/auth/2fa/recovery-codes", json={"password": ADMIN[1], "code": codigo}).status_code == 403
    assert client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": codigo}).status_code == 403
    assert _login(client, totp_code=codigo).status_code == 401
    # los codigos nuevos sirven en "Cambiar de telefono" (perdio el telefono despues de generarlos)
    assert client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": r.json()["codes"][0]}).status_code == 200


def test_contrasena_con_la_sesion_abierta_tiene_limite(client, auth, db_session):
    """Con una sesion robada, /2fa/disable, /2fa/recovery-codes, /auth/password y /2fa/setup no son un oraculo
    ilimitado de la contrasena: comparten el limite de reautenticacion (10 cada 15 min)."""
    assert client.put("/settings", json={"twofa_roles": [], "admin_password": ADMIN[1]}).status_code == 200
    sec = _activar(client)
    malos = [
        lambda: client.post("/auth/2fa/disable", json={"password": "otra-cosa-123"}),
        lambda: client.post("/auth/2fa/recovery-codes", json={"password": "otra-cosa-123", "code": "000000"}),
        lambda: client.post("/auth/password", json={"current_password": "otra-cosa-123", "new_password": "Nueva-clave-2026"}),
        lambda: client.post("/auth/2fa/setup", json={"password": "otra-cosa-123", "code": "000000"}),
    ]
    codes = [malos[i % 4]().status_code for i in range(8)]  # el PUT de arriba y la confirmacion del 2FA ya contaron 2
    assert set(codes) == {403}
    r = client.post("/auth/2fa/disable", json={"password": ADMIN[1]})
    assert r.status_code == 429 and r.headers.get("Retry-After")
    assert db_session.get(User, auth["user"]["id"]).totp_secret == sec  # ni con la buena, pasado el limite


def test_confirmar_el_secreto_pendiente_tiene_limite(client, auth):
    client.post("/auth/2fa/setup")
    codes = [client.post("/auth/2fa/verify", json={"code": "000000"}).status_code for _ in range(11)]
    assert codes[-1] == 429 and set(codes[:-1]) <= {400, 200}


def test_el_mismo_codigo_toma_la_fila_antes_de_compararlo(client, auth, db_session, monkeypatch):
    """Dos logins (o dos cambios) a la vez con el mismo codigo: la fila del usuario se lee con FOR UPDATE antes de
    comparar el paso, asi el segundo espera al commit del primero y ve el codigo ya usado. SQLite no lo dibuja: se
    comprueba que se pide."""
    sec = _activar(client)
    pedidos = []
    original = db_session.refresh

    def espia(obj, *a, **kw):
        pedidos.append((type(obj).__name__, kw.get("with_for_update")))
        return original(obj, *a, **kw)

    monkeypatch.setattr(db_session, "refresh", espia)
    assert _login(client, totp_code=_codigo(sec, 30)).status_code == 200
    assert ("User", True) in pedidos
    u = db_session.get(User, auth["user"]["id"])
    u.totp_last_step = None  # la unica ventana siguiente ya la gasto el login
    db_session.commit()
    pedidos.clear()
    assert client.post("/auth/2fa/setup", json={"password": ADMIN[1], "code": _codigo(sec, 30)}).status_code == 200
    assert ("User", True) in pedidos


def test_sesion_y_contrasena_no_apagan_el_2fa_para_quedarselo(client, auth, db_session):
    """La cadena: con la sesion y la contrasena (sin el telefono) apagar el 2FA y configurarlo con la app del atacante.
    Desactivar pide lo mismo que "Cambiar de telefono"; con un codigo de recuperacion real si se puede."""
    uid = auth["user"]["id"]
    assert client.put("/settings", json={"twofa_roles": [], "admin_password": ADMIN[1]}).status_code == 200
    sec = client.post("/auth/2fa/setup").json()["secret"]
    codes = client.post("/auth/2fa/verify", json={"code": _codigo(sec)}).json()["codes"]
    assert client.post("/auth/2fa/disable", json={"password": ADMIN[1]}).status_code == 403
    assert client.post("/auth/2fa/setup").status_code == 403  # sigue activo: setup pide contrasena y codigo
    u = db_session.get(User, uid)
    assert u.totp_enabled and u.totp_secret == sec and u.totp_pending_secret is None
    # la duena sin la app: un codigo de recuperacion sirve (y ya no sirve otra vez)
    assert client.post("/auth/2fa/disable", json={"password": ADMIN[1], "code": codes[0]}).status_code == 204
    assert db_session.get(User, uid).totp_enabled is False
