"""Restablecer la contraseña de un usuario del portal de aliados (lo hace el administrador desde su ficha):
«enlace», «temporal» o «definir» (la que escribe el administrador). Ver routers/ally_admin.ally_reset_password."""

from sqlalchemy import select

from app.models import AuditLog, EmailOutbox
from tests.aliados_helpers import ADMIN_PW, PW, aliado, interno, login, usuario

NUEVA = "Clave-definida-2026"
PROPIA = "Propia-clave-2026"
EMAIL = "tec.clave@aliado-clave.ejemplo.com"


def _adm(auth) -> dict:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def _empresa(client, auth, email: str = EMAIL, role: str = "tecnico_aliado", nombre: str = "Aliado Clave S.A.", cedula: str = "3-101-700001"):
    ally = aliado(client, _adm(auth), nombre, cedula)
    usuario(client, _adm(auth), ally, email, role)
    users = client.get(f"/allies/{ally}/users", headers=_adm(auth)).json()["users"]
    return ally, next(u["id"] for u in users if u["email"] == email)


def _reset(client, auth, ally: int, uid: int, admin_password: str = ADMIN_PW, **body):
    return client.post(f"/allies/{ally}/users/{uid}/reset-password", json={"admin_password": admin_password, **body}, headers=_adm(auth))


def test_definir_la_clave_y_el_aliado_debe_crear_la_propia(client, auth, db_session):
    ally, uid = _empresa(client, auth)
    r = _reset(client, auth, ally, uid, mode="definir", new_password=NUEVA)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    out = r.json()
    assert out["mode"] == "definir" and out["email"] == EMAIL and out["expires_at"]
    assert "temp_password" not in out and NUEVA not in r.text  # la clave no vuelve en la respuesta

    # la clave anterior ya no entra (sesiones cerradas) y la nueva pide crear una propia
    assert client.post("/auth/login", json={"email": EMAIL, "password": PW}).status_code == 401
    pide = client.post("/auth/login", json={"email": EMAIL, "password": NUEVA})
    assert pide.status_code == 401 and pide.headers.get("X-Password-Change") == "required"
    ok = client.post("/auth/login", json={"email": EMAIL, "password": NUEVA, "new_password": PROPIA})
    assert ok.status_code == 200, ok.text
    assert client.post("/auth/login", json={"email": EMAIL, "password": PROPIA}).status_code == 200

    # el aviso de seguridad no lleva la clave
    avisos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "user_security")).all()
    assert avisos and all(NUEVA not in m.html and PROPIA not in m.html for m in avisos)
    assert any("definió una contraseña" in m.html for m in avisos)
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "password_reset", AuditLog.entity_id == uid)).first()


def test_definir_exige_clave_fuerte(client, auth, db_session):
    ally, uid = _empresa(client, auth, email="fuerte.clave@aliado-clave.ejemplo.com")
    assert _reset(client, auth, ally, uid, mode="definir").status_code == 422
    assert _reset(client, auth, ally, uid, mode="definir", new_password="corta").status_code == 422
    assert _reset(client, auth, ally, uid, mode="definir", new_password="solotextolargo").status_code == 422
    # ninguno de los intentos cambió la clave: sigue entrando con la de antes
    assert client.post("/auth/login", json={"email": "fuerte.clave@aliado-clave.ejemplo.com", "password": PW}).status_code == 200


def test_contrasena_de_administrador_obligatoria(client, auth, db_session):
    ally, uid = _empresa(client, auth, email="reauth.clave@aliado-clave.ejemplo.com")
    r = _reset(client, auth, ally, uid, admin_password="no-es-la-mia", mode="definir", new_password=NUEVA)
    assert r.status_code == 403
    assert client.post("/auth/login", json={"email": "reauth.clave@aliado-clave.ejemplo.com", "password": PW}).status_code == 200


def test_temporal_se_muestra_una_vez_y_obliga_a_cambiarla(client, auth, db_session):
    ally, uid = _empresa(client, auth, email="temp.clave@aliado-clave.ejemplo.com")
    r = _reset(client, auth, ally, uid, mode="temporal")
    assert r.status_code == 200, r.text
    temp = r.json()["temp_password"]
    assert temp and r.json()["login_url"].endswith("/login")
    pide = client.post("/auth/login", json={"email": "temp.clave@aliado-clave.ejemplo.com", "password": temp})
    assert pide.status_code == 401 and pide.headers.get("X-Password-Change") == "required"
    assert client.post("/auth/login", json={"email": "temp.clave@aliado-clave.ejemplo.com", "password": temp, "new_password": PROPIA}).status_code == 200


def test_enlace_por_correo_no_cambia_la_clave_hasta_usarlo(client, auth, db_session):
    ally, uid = _empresa(client, auth, email="enlace.clave@aliado-clave.ejemplo.com")
    r = _reset(client, auth, ally, uid, mode="enlace")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "enlace" and "temp_password" not in out
    # mientras el enlace no se use, la clave actual sigue sirviendo
    assert client.post("/auth/login", json={"email": "enlace.clave@aliado-clave.ejemplo.com", "password": PW}).status_code == 200
    mail = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "password_reset").order_by(EmailOutbox.id.desc())).first()
    assert mail is not None and mail.bcc is None
    if not out["emailed"]:
        raw = out["link"].rsplit("/restablecer/", 1)[1]
        assert client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": NUEVA}).status_code == 200
        assert client.post("/auth/login", json={"email": "enlace.clave@aliado-clave.ejemplo.com", "password": NUEVA}).status_code == 200


def test_solo_administrador_y_solo_usuarios_de_su_empresa(client, auth, db_session):
    ally1, uid1 = _empresa(client, auth, email="uno.clave@aliado-clave.ejemplo.com", nombre="Aliado Uno Clave", cedula="3-101-700011")
    ally2, uid2 = _empresa(client, auth, email="dos.clave@aliado-clave.ejemplo.com", nombre="Aliado Dos Clave", cedula="3-101-700012")
    # un usuario de la otra empresa no se toca desde esta ficha
    assert _reset(client, auth, ally1, uid2, mode="definir", new_password=NUEVA).status_code == 404
    # un usuario interno sin Ajustes → Configurar no restablece claves de aliados
    tid = auth["tenant"]["id"]
    conta = interno(client, db_session, tid, "conta.clave@ejemplo.com", "contabilidad")
    r = client.post(f"/allies/{ally1}/users/{uid1}/reset-password", json={"mode": "temporal", "admin_password": PW}, headers=conta)
    assert r.status_code == 403
    assert client.post("/auth/login", json={"email": "uno.clave@aliado-clave.ejemplo.com", "password": PW}).status_code == 200


def test_usuario_desactivado_primero_hay_que_activarlo(client, auth, db_session):
    ally, uid = _empresa(client, auth, email="inactivo.clave@aliado-clave.ejemplo.com")
    assert client.patch(f"/allies/{ally}/users/{uid}", json={"active": False}, headers=_adm(auth)).status_code == 200
    r = _reset(client, auth, ally, uid, mode="definir", new_password=NUEVA)
    assert r.status_code == 409
