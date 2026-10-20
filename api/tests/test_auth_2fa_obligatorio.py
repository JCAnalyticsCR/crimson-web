"""Plan 1.10 · 2FA obligatorio para administracion y contabilidad (por defecto), con 7 dias de gracia y pantalla de
configuracion forzada. Configurable por empresa en Ajustes -> Usuarios (twofa_roles, twofa_grace_days)."""

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models import AuditLog, TenantUser, User
from tests.conftest import ADMIN

PW = "Usuario-2026-ok"


def _mk_user(db, tid, email, role):
    u = User(email=email, full_name="Usuario de prueba", password_hash=hash_password(PW))
    db.add(u)
    db.flush()
    db.add(TenantUser(tenant_id=tid, user_id=u.id, role_code=role))
    db.commit()
    return u


def _sesion(client, email, pw=PW) -> TestClient:
    """Otra sesion con el mismo app (otro jar de cookies y otro Authorization)."""
    c = TestClient(client.app)
    r = c.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def _vencer(db_session, uid, dias=8):
    u = db_session.get(User, uid)
    u.twofa_required_since = datetime.now(UTC) - timedelta(days=dias)
    db_session.commit()


def test_admin_tiene_7_dias_de_gracia_y_despues_pantalla_forzada(client, auth, db_session):
    uid = auth["user"]["id"]
    me = client.get("/auth/me").json()
    assert me["twofa"]["required"] is True and me["twofa"]["blocked"] is False
    limite = datetime.fromisoformat(me["twofa"]["deadline"].replace("Z", "+00:00"))
    if limite.tzinfo is None:
        limite = limite.replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < limite - datetime.now(UTC) <= timedelta(days=7)
    assert client.get("/customers").status_code == 200  # en la gracia trabaja normal
    # otro login no reinicia la gracia
    antes = db_session.get(User, uid).twofa_required_since
    assert client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
    assert db_session.get(User, uid).twofa_required_since == antes

    _vencer(db_session, uid)
    r = client.get("/customers")
    assert r.status_code == 403 and r.headers.get("X-2FA-Setup") == "required"
    assert client.get("/settings").status_code == 403
    # /auth sigue abierto: ve su cuenta y configura el 2FA desde la pantalla forzada
    me = client.get("/auth/me").json()
    assert me["twofa"]["blocked"] is True
    sec = client.post("/auth/2fa/setup").json()["secret"]
    assert client.post("/auth/2fa/verify", json={"code": pyotp.TOTP(sec).now()}).status_code == 200
    assert client.get("/customers").status_code == 200
    assert client.get("/auth/me").json()["twofa"] == {"required": True, "deadline": None, "blocked": False}


def test_contabilidad_si_ventas_no(client, auth, db_session):
    tid = auth["tenant"]["id"]
    conta = _mk_user(db_session, tid, "contabilidad.2fa@ejemplo.com", "contabilidad")
    vend = _mk_user(db_session, tid, "ventas.2fa@ejemplo.com", "ventas")
    c1 = _sesion(client, conta.email)
    assert c1.get("/auth/me").json()["twofa"]["required"] is True
    c2 = _sesion(client, vend.email)
    assert c2.get("/auth/me").json()["twofa"] == {"required": False, "deadline": None, "blocked": False}
    assert db_session.get(User, vend.id).twofa_required_since is None
    _vencer(db_session, conta.id)
    _vencer(db_session, vend.id)
    assert c1.get("/expenses").status_code == 403
    assert c2.get("/customers").status_code == 200  # a ventas no se le exige


def test_la_empresa_lo_configura_y_queda_en_la_bitacora(client, auth, db_session):
    tid = auth["tenant"]["id"]
    d = client.get("/settings/users").json()
    assert d["twofa_roles"] == ["admin", "contabilidad"] and d["twofa_grace_days"] == 7
    assert "admin" in d["twofa_role_choices"] and "aliado" not in d["twofa_role_choices"]
    # roles de cliente o aliado no se pueden exigir por aqui (ni roles inventados)
    assert client.put("/settings", json={"twofa_roles": ["aliado"]}).status_code == 422
    assert client.put("/settings", json={"twofa_grace_days": 90}).status_code == 422
    r = client.put("/settings", json={"twofa_roles": ["admin", "contabilidad", "caja"], "twofa_grace_days": 3})
    assert r.status_code == 200
    assert client.get("/settings/users").json()["twofa_roles"] == ["admin", "contabilidad", "caja"]
    log = db_session.scalars(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "permissions").order_by(AuditLog.id.desc())).first()
    assert log is not None
    # el 2FA obligatorio no se cambia por la puerta de atras (PATCH /tenant)
    assert client.patch("/tenant", json={"settings": {"twofa_roles": []}}).status_code == 422
    # caja ahora lo exige, con 3 dias
    cajero = _mk_user(db_session, tid, "caja.2fa@ejemplo.com", "caja")
    c = _sesion(client, cajero.email)
    st = c.get("/auth/me").json()["twofa"]
    assert st["required"] is True
    limite = datetime.fromisoformat(st["deadline"].replace("Z", "+00:00"))
    limite = limite if limite.tzinfo else limite.replace(tzinfo=UTC)
    assert limite - datetime.now(UTC) <= timedelta(days=3)
    # quitar el requisito libera a quien estaba bloqueado
    _vencer(db_session, auth["user"]["id"])
    assert client.get("/customers").status_code == 403
    assert client.put("/settings", json={"twofa_roles": []}).status_code == 403  # bloqueado: ni Ajustes
    u = db_session.get(User, auth["user"]["id"])
    u.twofa_required_since = None
    db_session.commit()
    assert client.put("/settings", json={"twofa_roles": []}).status_code == 200
    _vencer(db_session, auth["user"]["id"])
    assert client.get("/customers").status_code == 200


def test_vista_previa_de_aliado_tambien_pasa_por_la_compuerta(client, auth, db_session):
    _vencer(db_session, auth["user"]["id"])
    r = client.get("/aliado/inicio", headers={"X-View-As-Ally": "1"})
    assert r.status_code == 403 and r.headers.get("X-2FA-Setup") == "required"
