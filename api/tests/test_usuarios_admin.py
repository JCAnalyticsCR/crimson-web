"""Ajustes -> Usuarios (reunion 13/10): invitaciones (reenviar, revocar, duplicadas), restablecer contrasenas (enlace y
temporal), editar nombre y correo, cerrar sesiones, reglas de administradores y correos sensibles sin BCC."""

import importlib.util
import json
import re
import secrets
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import jwt
import pyotp
from sqlalchemy import select

from app.core.config import settings
from app.core.db import Base
from app.core.ratelimit import admin_users_limiter
from app.core.security import hash_password, hash_recovery, hash_token
from app.models import AuditLog, EmailOutbox, Invitation, PasswordReset, RefreshToken, Role, Tenant, TenantUser, User
from app.services import backup as bk
from app.services.accounts import issue_reset

ADMIN_PW = "Crimson-2026-seguro"
PW = "Usuario-2026-ok"
NUEVA = "Nueva-clave-2026"


def _mk_user(db, tid, email, role="ventas", pw=PW, **cols):
    u = User(email=email, full_name=cols.pop("full_name", "Usuario de prueba"), password_hash=hash_password(pw), **cols)
    db.add(u)
    db.flush()
    db.add(TenantUser(tenant_id=tid, user_id=u.id, role_code=role))
    db.commit()
    return u


def _login_as(client, email, pw, _headers=None, **extra):
    """Login sin el Authorization del admin (y lo deja como estaba). _headers: p. ej. otra IP (fixture from_ip)."""
    h = client.headers.pop("Authorization", None)
    try:
        return client.post("/auth/login", json={"email": email, "password": pw, **extra}, headers=_headers)
    finally:
        if h:
            client.headers["Authorization"] = h


def _otra_empresa(db, slug="otra-empresa"):
    t = Tenant(slug=slug, name="Otra empresa de prueba", default_currency="CRC", settings={})
    db.add(t)
    db.commit()
    return t


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _token(link):
    return link.rsplit("/", 1)[1]


def _acciones(db, action, entity_id=None):
    q = select(AuditLog).where(AuditLog.action == action)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == entity_id)
    return db.scalars(q).all()


def _invitacion_de(db, tenant, email, role="ventas"):
    """Invitacion interna de otra empresa (como si la hubiera hecho su admin): devuelve el token en claro."""
    raw = secrets.token_urlsafe(32)
    db.add(Invitation(tenant_id=tenant.id, email=email, role_code=role, token_hash=hash_token(raw), expires_at=datetime.now(UTC) + timedelta(days=7)))
    db.commit()
    return raw


def _con_bcc(db, tid):
    t = db.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "bcc": ["contabilidad@ejemplo.com"]}
    db.commit()


# ---------- Invitaciones ----------
def test_reenviar_invitacion_rota_el_token(client, auth, db_session):
    r = client.post("/settings/invitations", json={"email": "Nuevo.Vendedor@ejemplo.com", "role": "ventas"})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    assert r.json()["emailed"] is False and r.json()["email_error"] is None and r.json()["expires_at"]
    iid, viejo = r.json()["id"], _token(r.json()["link"])
    r2 = client.post(f"/settings/invitations/{iid}/resend")
    assert r2.status_code == 200 and r2.headers["cache-control"] == "no-store"
    nuevo = _token(r2.json()["link"])
    assert nuevo != viejo and r2.json()["id"] == iid
    body = {"full_name": "Nuevo Vendedor", "password": PW}
    assert client.post("/settings/invitations/accept", json={"token": viejo, **body}).status_code == 404
    r3 = client.post("/settings/invitations/accept", json={"token": nuevo, **body})
    assert r3.status_code == 200 and r3.json()["existing_account"] is False
    inv = db_session.get(Invitation, iid)
    assert inv.sent_count == 2 and inv.last_sent_at and inv.email == "nuevo.vendedor@ejemplo.com"
    log = _acciones(db_session, "invite_resend", iid)
    assert len(log) == 1 and log[0].diff["envio"] == 2 and log[0].diff["correo"] == "simulado"
    assert _acciones(db_session, "invite_accept", iid)
    assert _login_as(client, "nuevo.vendedor@ejemplo.com", PW).status_code == 200
    # ya aceptada: no se reenvia
    assert client.post(f"/settings/invitations/{iid}/resend").status_code == 409


def test_reenviar_invitacion_vencida(client, auth, db_session):
    iid = client.post("/settings/invitations", json={"email": "vencida@ejemplo.com", "role": "tecnico"}).json()["id"]
    inv = db_session.get(Invitation, iid)
    inv.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    lista = client.get("/settings/users").json()["invitations"]
    x = next(i for i in lista if i["id"] == iid)
    assert x["status"] == "vencida" and x["email_status"] == "simulado" and x["invited_by"]
    r = client.post(f"/settings/invitations/{iid}/resend")
    assert r.status_code == 200
    x = next(i for i in client.get("/settings/users").json()["invitations"] if i["id"] == iid)
    assert x["status"] == "pendiente" and x["sent_count"] == 2
    assert client.post("/settings/invitations/check", json={"token": _token(r.json()["link"])}).json()["role"] == "tecnico"


def test_revocar_invitacion(client, auth, db_session):
    r = client.post("/settings/invitations", json={"email": "revocar@ejemplo.com", "role": "caja"}).json()
    iid, tok = r["id"], _token(r["link"])
    chk = client.post("/settings/invitations/check", json={"token": tok})
    assert chk.status_code == 200 and chk.json()["email"] == "revocar@ejemplo.com" and chk.json()["existing_account"] is False
    assert client.post(f"/settings/invitations/{iid}/revoke").json() == {"ok": True}
    assert client.post("/settings/invitations/accept", json={"token": tok, "full_name": "Revocada", "password": PW}).status_code == 404
    assert client.post("/settings/invitations/check", json={"token": tok}).status_code == 404
    assert all(i["id"] != iid for i in client.get("/settings/users").json()["invitations"])
    assert len(_acciones(db_session, "invite_revoke", iid)) == 1
    inv = db_session.get(Invitation, iid)
    assert inv.revoked_at and inv.revoked_by == auth["user"]["id"]
    # idempotente: sin otra fila de bitacora
    assert client.post(f"/settings/invitations/{iid}/revoke").status_code == 200
    assert len(_acciones(db_session, "invite_revoke", iid)) == 1
    assert client.post(f"/settings/invitations/{iid}/resend").status_code == 409
    # aceptada: se desactiva al usuario, no se revoca la invitacion
    a = client.post("/settings/invitations", json={"email": "aceptada@ejemplo.com", "role": "caja"}).json()
    assert client.post("/settings/invitations/accept", json={"token": _token(a["link"]), "full_name": "Aceptada", "password": PW}).status_code == 200
    assert client.post(f"/settings/invitations/{a['id']}/revoke").status_code == 409


def test_invitar_correo_existente_y_duplicadas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _mk_user(db_session, tid, "ya.miembro@ejemplo.com")
    r = client.post("/settings/invitations", json={"email": "YA.MIEMBRO@ejemplo.com", "role": "ventas"})
    assert r.status_code == 409 and "ya es usuario" in r.json()["detail"]
    primera = client.post("/settings/invitations", json={"email": "dos.veces@ejemplo.com", "role": "ventas"}).json()
    segunda = client.post("/settings/invitations", json={"email": "dos.veces@ejemplo.com", "role": "lectura"}).json()
    assert client.post("/settings/invitations/check", json={"token": _token(primera["link"])}).status_code == 404
    assert client.post("/settings/invitations/check", json={"token": _token(segunda["link"])}).status_code == 200
    mismas = [i for i in client.get("/settings/users").json()["invitations"] if i["email"] == "dos.veces@ejemplo.com"]
    assert [i["id"] for i in mismas] == [segunda["id"]] and mismas[0]["role"] == "lectura"
    sup = _acciones(db_session, "invite_superseded", primera["id"])
    assert len(sup) == 1 and sup[0].diff == {"reemplazada_por": segunda["id"]}
    # soporte pendiente para ese correo: se gestiona en Acceso de soporte
    g = client.post("/settings/support", json={"email": "soporte.pendiente@ejemplo.com", "hours": 2, "reason": "Revisar un cierre"})
    assert g.status_code == 201
    r = client.post("/settings/invitations", json={"email": "soporte.pendiente@ejemplo.com", "role": "ventas"})
    assert r.status_code == 409 and "soporte" in r.json()["detail"]


def test_aliados_no_se_gestionan_aqui(client, auth, db_session):
    """Usuarios e invitaciones de empresas aliadas: no salen en la lista ni se editan desde aqui (pasarlos a un rol
    interno les daria todo el panel sin el filtro "en comun")."""
    tid = auth["tenant"]["id"]
    al = _mk_user(db_session, tid, "aliado.uno@ejemplo.com", role="aliado")
    tec = _mk_user(db_session, tid, "tecnico.aliado@ejemplo.com", role="tecnico_aliado")
    inv = Invitation(
        tenant_id=tid, email="aliado.pendiente@ejemplo.com", role_code="aliado", token_hash="a" * 64, expires_at=datetime.now(UTC) + timedelta(days=3)
    )
    db_session.add(inv)
    db_session.commit()
    d = client.get("/settings/users").json()
    assert not {al.id, tec.id} & {u["id"] for u in d["users"]}
    assert all(i["id"] != inv.id for i in d["invitations"])
    for uid in (al.id, tec.id):
        r = client.patch(f"/settings/users/{uid}", json={"role": "ventas"})
        assert r.status_code == 409 and "aliada" in r.json()["detail"]
        assert client.patch(f"/settings/users/{uid}", json={"role": "admin", "admin_password": ADMIN_PW}).status_code == 409
        assert client.patch(f"/settings/users/{uid}", json={"active": False}).status_code == 409
        assert client.post(f"/settings/users/{uid}/reset-password", json={"mode": "temporal", "admin_password": ADMIN_PW}).status_code == 409
        assert client.post(f"/settings/users/{uid}/sessions/revoke").status_code == 409
    roles = {m.user_id: m.role_code for m in db_session.scalars(select(TenantUser).where(TenantUser.user_id.in_([al.id, tec.id])))}
    assert roles == {al.id: "aliado", tec.id: "tecnico_aliado"}
    # invitarlos como internos tampoco
    r = client.post("/settings/invitations", json={"email": "aliado.uno@ejemplo.com", "role": "ventas"})
    assert r.status_code == 409 and "aliada" in r.json()["detail"]
    r = client.post("/settings/invitations", json={"email": "aliado.pendiente@ejemplo.com", "role": "ventas"})
    assert r.status_code == 409 and "aliada" in r.json()["detail"]
    # su invitacion no se reenvia ni se revoca desde aqui
    assert client.post(f"/settings/invitations/{inv.id}/resend").status_code == 404
    assert client.post(f"/settings/invitations/{inv.id}/revoke").status_code == 404
    assert db_session.get(Invitation, inv.id).revoked_at is None
    # y una invitacion interna nueva no la reemplaza (son de otra pantalla)
    assert client.post("/settings/invitations", json={"email": "otra.persona@ejemplo.com", "role": "ventas"}).status_code == 201
    assert db_session.get(Invitation, inv.id).revoked_at is None


def test_invitaciones_de_otra_empresa_404(client, auth, db_session):
    otra = _otra_empresa(db_session)
    inv = Invitation(tenant_id=otra.id, email="ajena@ejemplo.com", role_code="ventas", token_hash="f" * 64, expires_at=datetime.now(UTC) + timedelta(days=3))
    db_session.add(inv)
    db_session.commit()
    assert client.post(f"/settings/invitations/{inv.id}/resend").status_code == 404
    assert client.post(f"/settings/invitations/{inv.id}/revoke").status_code == 404
    assert db_session.get(Invitation, inv.id).revoked_at is None


# ---------- Restablecer contrasena ----------
def test_reset_temporal_flujo_completo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    u = _mk_user(db_session, tid, "temporal@ejemplo.com")
    tok = _login_as(client, u.email, PW).json()["access_token"]
    assert client.get("/auth/me", headers=_h(tok)).status_code == 200
    r = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "temporal", "admin_password": ADMIN_PW})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    out = r.json()
    temp = out["temp_password"]
    assert re.fullmatch(r"[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}", temp)
    assert out["mode"] == "temporal" and out["email"] == u.email and out["login_url"].endswith("/login") and out["expires_at"]
    # todas sus sesiones cerradas: el access token viejo muere ya y los refresh quedan revocados
    assert client.get("/auth/me", headers=_h(tok)).status_code == 401
    rts = db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id)).all()
    assert rts and all(rt.revoked_at for rt in rts)
    assert _login_as(client, u.email, PW).status_code == 401
    r = _login_as(client, u.email, temp)
    assert r.status_code == 401 and r.headers["x-password-change"] == "required"
    assert _login_as(client, u.email, temp, new_password="corta").status_code == 422
    assert _login_as(client, u.email, temp, new_password=temp).status_code == 422
    assert db_session.get(User, u.id).must_change_password is True
    assert _login_as(client, u.email, temp, new_password=NUEVA).status_code == 200
    assert _login_as(client, u.email, NUEVA).status_code == 200
    assert _login_as(client, u.email, temp).status_code == 401
    u = db_session.get(User, u.id)
    assert u.must_change_password is False and u.temp_password_expires_at is None and u.password_changed_at
    assert _acciones(db_session, "password_reset", u.id) and _acciones(db_session, "password_temp_changed", u.id)
    # la temporal no queda en ningun lado
    for row in db_session.scalars(select(AuditLog)):
        assert temp not in json.dumps([row.diff, row.before, row.after, row.reason], default=str)
    for m in db_session.scalars(select(EmailOutbox)):
        assert temp not in (m.html or "")
    aviso = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "user_security", EmailOutbox.entity_id == u.id)).all()
    assert aviso and aviso[0].to == u.email


def test_reset_temporal_vencida(client, auth, db_session):
    u = _mk_user(db_session, auth["tenant"]["id"], "vence.temporal@ejemplo.com")
    temp = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "temporal", "admin_password": ADMIN_PW}).json()["temp_password"]
    u = db_session.get(User, u.id)
    u.temp_password_expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    antes = u.failed_logins
    r = _login_as(client, u.email, temp, new_password=NUEVA)
    assert r.status_code == 401 and "venció" in r.json()["detail"]
    assert db_session.get(User, u.id).failed_logins == antes


def test_reset_requiere_contrasena_del_admin(client, auth, db_session):
    u = _mk_user(db_session, auth["tenant"]["id"], "reauth@ejemplo.com")
    assert client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "temporal"}).status_code == 422
    r = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "temporal", "admin_password": "otra-cosa-2026"})
    assert r.status_code == 403  # nunca 401: el portal cerraria la sesion del admin
    assert client.get("/auth/me").status_code == 200
    assert db_session.get(User, u.id).must_change_password is False


def test_reset_enlace_y_confirmacion(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _con_bcc(db_session, tid)
    u = _mk_user(db_session, tid, "restablecer@ejemplo.com")
    tok = _login_as(client, u.email, PW).json()["access_token"]
    r = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    out = r.json()
    assert out["mode"] == "enlace" and out["emailed"] is False and "/restablecer/" in out["link"]
    raw = out["link"].rsplit("/restablecer/", 1)[1]
    mail = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "password_reset").order_by(EmailOutbox.id.desc())).first()
    assert mail.bcc is None and raw not in mail.html and "/restablecer/[oculto]" in mail.html
    # el enlace no cambia nada hasta que se usa
    assert client.get("/auth/me", headers=_h(tok)).status_code == 200
    assert client.get("/settings/users").json()["users"] and any(x["reset_link_expires_at"] for x in client.get("/settings/users").json()["users"])
    chk = client.post("/auth/password-reset/check", json={"token": raw})
    assert chk.status_code == 200 and chk.json()["email"] == "r***@ejemplo.com"
    assert client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": "corta"}).status_code == 422
    assert client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": PW}).status_code == 422  # la misma de antes
    ok = client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": NUEVA})
    assert ok.status_code == 200 and ok.json() == {"ok": True, "email": u.email}
    assert client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": "Otra-clave-2026"}).status_code == 404
    assert client.get("/auth/me", headers=_h(tok)).status_code == 401  # se cerraron sus sesiones
    assert _login_as(client, u.email, NUEVA).status_code == 200
    done = _acciones(db_session, "password_reset_done", u.id)
    assert len(done) == 1 and done[0].diff == {"origen": "admin"} and done[0].tenant_id == tid
    assert _acciones(db_session, "password_reset_link", u.id)
    # pedir un segundo enlace invalida el primero
    l1 = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW}).json()["link"]
    l2 = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW}).json()["link"]
    assert client.post("/auth/password-reset/check", json={"token": _token(l1)}).status_code == 404
    assert client.post("/auth/password-reset/check", json={"token": _token(l2)}).status_code == 200


def test_reset_enlace_vencido_404(client, auth, db_session):
    u = _mk_user(db_session, auth["tenant"]["id"], "enlace.vencido@ejemplo.com")
    link = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW}).json()["link"]
    pr = db_session.scalars(select(PasswordReset).where(PasswordReset.user_id == u.id)).one()
    assert pr.token_hash != _token(link) and pr.kind == "admin"
    pr.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert client.post("/auth/password-reset/check", json={"token": _token(link)}).status_code == 404
    assert client.post("/auth/password-reset/confirm", json={"token": _token(link), "new_password": NUEVA}).status_code == 404
    assert client.post("/auth/password-reset/check", json={"token": "x" * 43}).status_code == 404


def test_reset_quita_2fa(client, auth, db_session):
    tid = auth["tenant"]["id"]
    u = _mk_user(
        db_session, tid, "perdio.telefono@ejemplo.com", totp_enabled=True, totp_secret=pyotp.random_base32(), totp_recovery=[hash_recovery("ABCD-2345")]
    )
    r = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "temporal", "reset_2fa": True, "admin_password": ADMIN_PW})
    assert r.status_code == 200
    u = db_session.get(User, u.id)
    assert u.totp_enabled is False and u.totp_secret is None and u.totp_recovery == []
    assert _acciones(db_session, "2fa_reset", u.id)
    assert _acciones(db_session, "password_reset", u.id)[-1].diff["2fa_reiniciado"] is True

    # con enlace NO se quita al pedirlo (la contrasena vieja sigue sirviendo hasta usarlo): se quita al usarlo
    w = _mk_user(db_session, tid, "enlace.2fa@ejemplo.com", totp_enabled=True, totp_secret=pyotp.random_base32(), totp_recovery=[hash_recovery("ABCD-2345")])
    r = client.post(f"/settings/users/{w.id}/reset-password", json={"mode": "enlace", "reset_2fa": True, "admin_password": ADMIN_PW})
    assert r.status_code == 200
    assert db_session.get(User, w.id).totp_enabled is True and not _acciones(db_session, "2fa_reset", w.id)
    diff = _acciones(db_session, "password_reset_link", w.id)[-1].diff
    assert diff["2fa_reiniciado"] is False and diff["2fa_se_quita_al_usarlo"] is True
    sigue = _login_as(client, w.email, PW)
    assert sigue.status_code == 401 and sigue.headers["x-2fa"] == "required"  # la cuenta no quedo con un solo factor
    assert client.post("/auth/password-reset/confirm", json={"token": _token(r.json()["link"]), "new_password": NUEVA}).status_code == 200
    w = db_session.get(User, w.id)
    assert w.totp_enabled is False and w.totp_secret is None and w.totp_recovery == []
    hecho = _acciones(db_session, "2fa_reset", w.id)
    assert len(hecho) == 1 and hecho[0].diff == {"origen": "enlace", "pedido_por": auth["user"]["id"]}
    assert _login_as(client, w.email, NUEVA).status_code == 200  # ya sin codigo

    secreto = pyotp.random_base32()
    v = _mk_user(db_session, tid, "conserva.2fa@ejemplo.com", totp_enabled=True, totp_secret=secreto, totp_recovery=[hash_recovery("ABCD-2345")])
    temp = client.post(f"/settings/users/{v.id}/reset-password", json={"mode": "temporal", "admin_password": ADMIN_PW}).json()["temp_password"]
    assert db_session.get(User, v.id).totp_enabled is True
    r = _login_as(client, v.email, temp)
    assert r.status_code == 401 and r.headers["x-password-change"] == "required"
    r = _login_as(client, v.email, temp, new_password=NUEVA)
    assert r.status_code == 401 and r.headers["x-2fa"] == "required"
    # el codigo de recuperacion no se consume si el login no termina
    assert _login_as(client, v.email, temp, new_password="corta", totp_code="ABCD-2345").status_code == 422
    assert db_session.get(User, v.id).totp_recovery == [hash_recovery("ABCD-2345")]
    codigo = pyotp.TOTP(secreto).now()
    assert _login_as(client, v.email, temp, new_password=NUEVA, totp_code=codigo).status_code == 200
    # plan 1.10: el codigo que se acaba de usar no sirve otra vez; el de la ventana siguiente si
    assert _login_as(client, v.email, NUEVA, totp_code=codigo).status_code == 401
    assert _login_as(client, v.email, NUEVA, totp_code=pyotp.TOTP(secreto).at(datetime.now(UTC) + timedelta(seconds=30))).status_code == 200


def test_no_se_restablece_propio_soporte_cliente_otra_empresa_compartido(client, auth, db_session):
    tid = auth["tenant"]["id"]
    body = {"mode": "temporal", "admin_password": ADMIN_PW}
    assert client.post(f"/settings/users/{auth['user']['id']}/reset-password", json=body).status_code == 409
    # soporte temporal
    g = client.post("/settings/support", json={"email": "soporte.reset@ejemplo.com", "hours": 2, "reason": "Revisar un cierre"}).json()
    assert client.post("/settings/invitations/accept", json={"token": _token(g["invite_link"]), "full_name": "Soporte", "password": PW}).status_code == 200
    sop = db_session.scalar(select(User).where(User.email == "soporte.reset@ejemplo.com"))
    r = client.post(f"/settings/users/{sop.id}/reset-password", json=body)
    assert r.status_code == 409 and "Acceso de soporte" in r.json()["detail"]
    assert client.patch(f"/settings/users/{sop.id}", json={"role": "admin", "admin_password": ADMIN_PW}).status_code == 409
    # usuario del portal de clientes
    cid = client.get("/customers").json()["items"][0]["id"]
    cli = User(email="cliente.reset@ejemplo.com", full_name="Cliente", password_hash=hash_password(PW))
    db_session.add(cli)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=cli.id, role_code="cliente_usuario", customer_id=cid))
    otra = _otra_empresa(db_session)
    ajeno = _mk_user(db_session, otra.id, "ajeno.reset@ejemplo.com")
    assert client.post(f"/settings/users/{cli.id}/reset-password", json=body).status_code == 404
    assert client.post(f"/settings/users/{ajeno.id}/reset-password", json=body).status_code == 404
    assert client.post(f"/settings/users/{ajeno.id}/sessions/revoke").status_code == 404
    # compartido con otra empresa
    comp = _mk_user(db_session, tid, "compartido.reset@ejemplo.com")
    db_session.add(TenantUser(tenant_id=otra.id, user_id=comp.id, role_code="ventas"))
    db_session.commit()
    r = client.post(f"/settings/users/{comp.id}/reset-password", json=body)
    assert r.status_code == 409 and "otra empresa" in r.json()["detail"]
    assert client.patch(f"/settings/users/{comp.id}", json={"full_name": "Otro Nombre"}).status_code == 409
    assert client.patch(f"/settings/users/{comp.id}", json={"role": "caja"}).json() == {"ok": True}  # rol y activo si
    # inactivo
    ina = _mk_user(db_session, tid, "inactivo.reset@ejemplo.com")
    assert client.patch(f"/settings/users/{ina.id}", json={"active": False}).json() == {"ok": True}
    r = client.post(f"/settings/users/{ina.id}/reset-password", json=body)
    assert r.status_code == 409 and "desactivado" in r.json()["detail"]
    assert not db_session.scalars(select(PasswordReset)).all()


# ---------- Editar ----------
def test_editar_nombre_y_correo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    u = _mk_user(db_session, tid, "editar@ejemplo.com")
    # enlace de contrasena pedido antes del cambio: se mando al correo VIEJO
    viejo = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW}).json()["link"]
    assert client.post("/auth/password-reset/check", json={"token": _token(viejo)}).status_code == 200
    assert client.patch(f"/settings/users/{u.id}", json={"full_name": "  Nombre Nuevo  "}).json() == {"ok": True}
    log = _acciones(db_session, "user_profile", u.id)
    assert len(log) == 1 and log[0].before == {"full_name": "Usuario de prueba"} and log[0].after == {"full_name": "Nombre Nuevo"}
    assert not _acciones(db_session, "permissions", u.id)  # sin cambio de rol no hay fila de permisos
    _mk_user(db_session, tid, "ocupado@ejemplo.com")
    assert client.patch(f"/settings/users/{u.id}", json={"email": "ocupado@ejemplo.com", "admin_password": ADMIN_PW}).status_code == 409
    assert client.patch(f"/settings/users/{u.id}", json={"email": "correo.nuevo@ejemplo.com"}).status_code == 403
    inv = client.post("/settings/invitations", json={"email": "correo.nuevo@ejemplo.com", "role": "ventas"}).json()
    assert client.patch(f"/settings/users/{u.id}", json={"email": "Correo.Nuevo@ejemplo.com", "admin_password": ADMIN_PW}).json() == {"ok": True}
    assert _login_as(client, "correo.nuevo@ejemplo.com", PW).status_code == 200
    assert _login_as(client, "editar@ejemplo.com", PW).status_code == 401
    # quien tenga el buzon viejo ya no puede usar ese enlace
    assert client.post("/auth/password-reset/check", json={"token": _token(viejo)}).status_code == 404
    assert client.post("/auth/password-reset/confirm", json={"token": _token(viejo), "new_password": NUEVA}).status_code == 404
    avisos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "user_security", EmailOutbox.entity_id == u.id)).all()
    assert {m.to for m in avisos} == {"editar@ejemplo.com", "correo.nuevo@ejemplo.com"} and all(m.bcc is None for m in avisos)
    assert _acciones(db_session, "user_profile", u.id)[-1].after == {"email": "correo.nuevo@ejemplo.com"}
    # la invitacion pendiente para el correo nuevo quedo revocada
    assert db_session.get(Invitation, inv["id"]).revoked_at is not None
    assert client.post("/settings/invitations/check", json={"token": _token(inv["link"])}).status_code == 404


def test_rol_admin_pide_contrasena(client, auth, db_session):
    tid = auth["tenant"]["id"]
    v = _mk_user(db_session, tid, "ascenso@ejemplo.com")
    assert client.patch(f"/settings/users/{v.id}", json={"role": "admin"}).status_code == 403
    assert db_session.get(TenantUser, db_session.scalar(select(TenantUser.id).where(TenantUser.user_id == v.id))).role_code == "ventas"
    assert client.patch(f"/settings/users/{v.id}", json={"role": "admin", "admin_password": ADMIN_PW}).json() == {"ok": True}
    # quitarle el admin a otro admin (y desactivarlo) tambien la pide
    assert client.patch(f"/settings/users/{v.id}", json={"role": "ventas"}).status_code == 403
    assert client.patch(f"/settings/users/{v.id}", json={"active": False}).status_code == 403
    assert client.patch(f"/settings/users/{v.id}", json={"role": "ventas", "admin_password": ADMIN_PW}).json() == {"ok": True}
    # nadie se quita el admin a si mismo
    assert client.patch(f"/settings/users/{auth['user']['id']}", json={"role": "ventas", "admin_password": ADMIN_PW}).status_code == 409


def test_invitar_admin_pide_contrasena(client, auth, db_session):
    """Invitar como admin es crear un admin: pide la contrasena igual que ascender con PATCH (una sesion robada
    sin la contrasena no se deja un admin propio). Reenviar esa invitacion devuelve un enlace vivo: tambien."""
    body = {"email": "nuevo.admin@ejemplo.com", "role": "admin"}
    assert client.post("/settings/invitations", json=body).status_code == 403
    assert client.post("/settings/invitations", json={**body, "admin_password": "otra-cosa-2026"}).status_code == 403
    assert client.get("/auth/me").status_code == 200  # 403, nunca 401
    assert not db_session.scalars(select(Invitation).where(Invitation.email == "nuevo.admin@ejemplo.com")).all()
    r = client.post("/settings/invitations", json={**body, "admin_password": ADMIN_PW})
    assert r.status_code == 201
    iid = r.json()["id"]
    assert client.post(f"/settings/invitations/{iid}/resend").status_code == 403
    assert client.post(f"/settings/invitations/{iid}/resend", json={"admin_password": "otra-cosa-2026"}).status_code == 403
    assert db_session.get(Invitation, iid).sent_count == 1
    assert client.post(f"/settings/invitations/{iid}/resend", json={"admin_password": ADMIN_PW}).status_code == 200
    # los demas roles no la piden (InviteTech manda solo correo y rol)
    assert client.post("/settings/invitations", json={"email": "sin.clave@ejemplo.com", "role": "tecnico"}).status_code == 201


def test_rol_personalizado_no_toca_admins(client, auth, db_session):
    tid = auth["tenant"]["id"]
    db_session.add(Role(code="gerente", name="Gerente", permissions={"settings": ["ver", "configurar"]}))
    db_session.commit()
    g = _mk_user(db_session, tid, "gerente@ejemplo.com", role="gerente")
    h = _h(_login_as(client, g.email, PW).json()["access_token"])
    admin_id = auth["user"]["id"]
    assert client.patch(f"/settings/users/{admin_id}", json={"full_name": "Otro Nombre"}, headers=h).status_code == 403
    assert client.post(f"/settings/users/{admin_id}/reset-password", json={"mode": "temporal", "admin_password": PW}, headers=h).status_code == 403
    assert client.post(f"/settings/users/{admin_id}/sessions/revoke", headers=h).status_code == 403
    v = _mk_user(db_session, tid, "vend.gerente@ejemplo.com")
    assert client.patch(f"/settings/users/{v.id}", json={"role": "admin", "admin_password": PW}, headers=h).status_code == 403
    assert client.patch(f"/settings/users/{v.id}", json={"role": "caja"}, headers=h).json() == {"ok": True}
    # tampoco crea un admin por invitacion (ni reenvia o revoca la que hizo un admin, que le daria el enlace)
    r = client.post("/settings/invitations", json={"email": "admin.del.gerente@ejemplo.com", "role": "admin", "admin_password": PW}, headers=h)
    assert r.status_code == 403 and "administrador" in r.json()["detail"]
    assert not db_session.scalars(select(Invitation).where(Invitation.email == "admin.del.gerente@ejemplo.com")).all()
    iid = client.post("/settings/invitations", json={"email": "admin.pendiente@ejemplo.com", "role": "admin", "admin_password": ADMIN_PW}).json()["id"]
    assert client.post(f"/settings/invitations/{iid}/resend", json={"admin_password": PW}, headers=h).status_code == 403
    assert client.post(f"/settings/invitations/{iid}/revoke", headers=h).status_code == 403
    inv = db_session.get(Invitation, iid)
    assert inv.sent_count == 1 and inv.revoked_at is None
    # ni la reemplaza (que es revocarla) invitando de nuevo a ese correo con otro rol o poniendole ese correo a un usuario
    r = client.post("/settings/invitations", json={"email": "admin.pendiente@ejemplo.com", "role": "ventas"}, headers=h)
    assert r.status_code == 403 and "invitación de administrador" in r.json()["detail"]
    r = client.patch(f"/settings/users/{v.id}", json={"email": "admin.pendiente@ejemplo.com", "admin_password": PW}, headers=h)
    assert r.status_code == 403 and "invitación de administrador" in r.json()["detail"]
    assert db_session.get(Invitation, iid).revoked_at is None and not _acciones(db_session, "invite_superseded", iid)
    assert db_session.get(User, v.id).email == "vend.gerente@ejemplo.com"
    assert len(db_session.scalars(select(Invitation).where(Invitation.email == "admin.pendiente@ejemplo.com")).all()) == 1
    # invitar a un rol que no es admin si puede
    assert client.post("/settings/invitations", json={"email": "caja.del.gerente@ejemplo.com", "role": "caja"}, headers=h).status_code == 201
    # un admin si la reemplaza
    assert client.post("/settings/invitations", json={"email": "admin.pendiente@ejemplo.com", "role": "ventas"}).status_code == 201
    assert db_session.get(Invitation, iid).revoked_at is not None and _acciones(db_session, "invite_superseded", iid)


def test_enlace_y_temporal_mueren_si_quien_los_pidio_pierde_la_autoridad(client, auth, db_session):
    """Sin correo el enlace (o la temporal) le queda en la mano a quien lo pidio. Si despues lo desactivan o le bajan
    el rol, o a la persona la ascienden a admin y quien lo pidio no lo es, deja de servir: si no, entraria con esa
    cuenta (incluso como admin, sin pasar por _guard_admin)."""
    tid = auth["tenant"]["id"]
    # (a) un admin pide un enlace y una temporal para otras dos admins (el aviso queda "simulado") y despues lo desactivan
    a2 = _mk_user(db_session, tid, "admin.dos@ejemplo.com", role="admin")
    h2 = _h(_login_as(client, a2.email, PW).json()["access_token"])
    b = _mk_user(db_session, tid, "admin.tres@ejemplo.com", role="admin")
    c = _mk_user(db_session, tid, "admin.cuatro@ejemplo.com", role="admin")
    link = client.post(f"/settings/users/{b.id}/reset-password", json={"mode": "enlace", "admin_password": PW}, headers=h2).json()["link"]
    temp = client.post(f"/settings/users/{c.id}/reset-password", json={"mode": "temporal", "admin_password": PW}, headers=h2).json()["temp_password"]
    assert client.post("/auth/password-reset/check", json={"token": _token(link)}).status_code == 200
    assert client.patch(f"/settings/users/{a2.id}", json={"active": False, "admin_password": ADMIN_PW}).json() == {"ok": True}
    assert client.post("/auth/password-reset/check", json={"token": _token(link)}).status_code == 404
    assert client.post("/auth/password-reset/confirm", json={"token": _token(link), "new_password": NUEVA}).status_code == 404
    assert _login_as(client, b.email, PW).status_code == 200  # su contrasena sigue igual
    assert _login_as(client, c.email, temp, new_password=NUEVA).status_code == 401
    assert db_session.get(User, c.id).must_change_password is True
    assert _acciones(db_session, "password_reset_invalidated", a2.id)[-1].diff == {"anulados": 2}
    # (b) un rol personalizado con settings.configurar restablece a dos vendedores y despues un admin los asciende
    db_session.add(Role(code="gerente", name="Gerente", permissions={"settings": ["ver", "configurar"]}))
    db_session.commit()
    g = _mk_user(db_session, tid, "gerente.tres@ejemplo.com", role="gerente")
    hg = _h(_login_as(client, g.email, PW).json()["access_token"])
    v = _mk_user(db_session, tid, "asciende.enlace@ejemplo.com")
    w = _mk_user(db_session, tid, "asciende.temporal@ejemplo.com")
    link_v = client.post(f"/settings/users/{v.id}/reset-password", json={"mode": "enlace", "admin_password": PW}, headers=hg).json()["link"]
    temp_w = client.post(f"/settings/users/{w.id}/reset-password", json={"mode": "temporal", "admin_password": PW}, headers=hg).json()["temp_password"]
    for uid in (v.id, w.id):
        assert client.patch(f"/settings/users/{uid}", json={"role": "admin", "admin_password": ADMIN_PW}).json() == {"ok": True}
    assert client.post("/auth/password-reset/confirm", json={"token": _token(link_v), "new_password": NUEVA}).status_code == 404
    assert _login_as(client, w.email, temp_w, new_password=NUEVA).status_code == 401
    assert db_session.get(User, w.id).must_change_password is True
    # (c) aunque la fila siga viva (aqui le quitan settings.configurar al rol), se revalida al usarla
    x = _mk_user(db_session, tid, "vend.revalida@ejemplo.com")
    y = _mk_user(db_session, tid, "vend.revalida.dos@ejemplo.com")
    link_x = client.post(f"/settings/users/{x.id}/reset-password", json={"mode": "enlace", "admin_password": PW}, headers=hg).json()["link"]
    temp_y = client.post(f"/settings/users/{y.id}/reset-password", json={"mode": "temporal", "admin_password": PW}, headers=hg).json()["temp_password"]
    assert client.post("/auth/password-reset/check", json={"token": _token(link_x)}).status_code == 200  # con vendedores si puede
    db_session.scalar(select(Role).where(Role.code == "gerente")).permissions = {"settings": ["ver"]}
    db_session.commit()
    assert client.post("/auth/password-reset/check", json={"token": _token(link_x)}).status_code == 404
    r = _login_as(client, y.email, temp_y, new_password=NUEVA)
    assert r.status_code == 401 and "ya no es válida" in r.json()["detail"]
    assert db_session.get(User, y.id).failed_logins == 0  # la contrasena era correcta: no suma intentos


def test_desactivar_revoca_sesiones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    u = _mk_user(db_session, tid, "desactivar@ejemplo.com")
    tok = _login_as(client, u.email, PW).json()["access_token"]
    assert client.patch(f"/settings/users/{u.id}", json={"active": False, "reason": "Ya no trabaja aquí"}).json() == {"ok": True}
    rts = db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.tenant_id == tid)).all()
    assert rts and all(rt.revoked_at for rt in rts)
    assert client.get("/auth/me", headers=_h(tok)).status_code in (401, 403)
    assert _acciones(db_session, "permissions", u.id)[-1].after == {"active": False}
    # se reactiva sin perder nada (no se borran usuarios)
    assert client.patch(f"/settings/users/{u.id}", json={"active": True}).json() == {"ok": True}
    assert _login_as(client, u.email, PW).status_code == 200


def test_cerrar_sesiones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    u = _mk_user(db_session, tid, "sesiones@ejemplo.com")
    tok = _login_as(client, u.email, PW).json()["access_token"]
    _login_as(client, u.email, PW)
    r = client.post(f"/settings/users/{u.id}/sessions/revoke")
    assert r.status_code == 200 and r.json() == {"ok": True, "revoked": 2}
    assert client.get("/auth/me", headers=_h(tok)).status_code == 401
    assert all(rt.revoked_at for rt in db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id)))
    assert client.get("/auth/me").status_code == 200  # el admin sigue
    assert _acciones(db_session, "sessions_revoke", u.id)[0].diff == {"sesiones": 2}
    assert client.post(f"/settings/users/{auth['user']['id']}/sessions/revoke").status_code == 409
    assert _login_as(client, u.email, PW).status_code == 200  # puede volver a entrar


def test_limite_de_acciones_del_admin(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(admin_users_limiter, "limit", 2)
    assert client.post("/settings/invitations", json={"email": "limite1@ejemplo.com", "role": "ventas"}).status_code == 201
    assert client.post("/settings/invitations", json={"email": "limite2@ejemplo.com", "role": "ventas"}).status_code == 201
    assert client.post("/settings/invitations", json={"email": "limite3@ejemplo.com", "role": "ventas"}).status_code == 429


def test_lista_de_usuarios(client, auth, db_session, monkeypatch, from_ip):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    tid = auth["tenant"]["id"]
    v = _mk_user(db_session, tid, "lista@ejemplo.com", full_name="Ana Lista")
    # soporte aceptado (cuenta como miembro de soporte) y uno pendiente (no sale en invitaciones)
    g = client.post("/settings/support", json={"email": "soporte.lista@ejemplo.com", "hours": 2, "reason": "Revisar un cierre"}).json()
    assert client.post("/settings/invitations/accept", json={"token": _token(g["invite_link"]), "full_name": "Soporte", "password": PW}).status_code == 200
    client.post("/settings/support", json={"email": "soporte.pendiente2@ejemplo.com", "hours": 2, "reason": "Revisar un cierre"})
    # invitacion vieja de alguien que ya es miembro (la de la captura): se oculta
    db_session.add(
        Invitation(tenant_id=tid, email="lista@ejemplo.com", role_code="ventas", token_hash="e" * 64, expires_at=datetime.now(UTC) + timedelta(days=2))
    )
    db_session.commit()
    pend = client.post("/settings/invitations", json={"email": "pendiente.lista@ejemplo.com", "role": "supervisor"}).json()
    d = client.get("/settings/users").json()
    assert d["mail_configured"] is False and d["support_members"] == 1
    assert "soporte" not in [u["role"] for u in d["users"]] and "soporte.lista@ejemplo.com" not in [u["email"] for u in d["users"]]
    assert [i["email"] for i in d["invitations"]] == ["pendiente.lista@ejemplo.com"] and d["invitations"][0]["id"] == pend["id"]
    yo = next(u for u in d["users"] if u["is_self"])
    assert yo["id"] == auth["user"]["id"] and yo["role"] == "admin"
    x = next(u for u in d["users"] if u["id"] == v.id)
    for k in (
        "must_change_password",
        "temp_password_expires_at",
        "locked_until",
        "reset_link_expires_at",
        "password_changed_at",
        "member_since",
        "shared",
        "is_self",
    ):
        assert k in x, k
    assert x["shared"] is False and x["locked_until"] is None and x["is_self"] is False
    assert {"roles", "cost_roles", "cost_role_choices"} <= set(d)
    # bloqueado por intentos: se ve y restablecer lo desbloquea. Desde 1.10 una sola IP no bloquea la cuenta (aporta
    # como mucho 3 fallidos): el bloqueo es de un ataque desde varias IPs
    for n in range(4):
        for _ in range(3):
            assert _login_as(client, v.email, "Clave-incorrecta-1", _headers=from_ip(f"203.0.113.{n + 1}")).status_code == 401
    assert next(u for u in client.get("/settings/users").json()["users"] if u["id"] == v.id)["locked_until"]
    client.post(f"/settings/users/{v.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW})
    x = next(u for u in client.get("/settings/users").json()["users"] if u["id"] == v.id)
    assert x["locked_until"] is None and x["reset_link_expires_at"]
    assert _login_as(client, v.email, PW).status_code == 200


def test_aceptar_con_cuenta_existente(client, auth, db_session):
    tid = auth["tenant"]["id"]
    otra = _otra_empresa(db_session)
    u = _mk_user(db_session, otra.id, "existente@ejemplo.com", full_name="Nombre Original")
    creada = client.post("/settings/invitations", json={"email": "existente@ejemplo.com", "role": "ventas"}).json()
    tok = _token(creada["link"])
    chk = client.post("/settings/invitations/check", json={"token": tok}).json()
    assert chk["existing_account"] is True and chk["tenant"] and chk["role"] == "ventas"

    def miembro():
        return db_session.scalar(select(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.user_id == u.id))

    # el enlace solo no basta: el admin que invita siempre lo tiene y pegaria a su empresa la cuenta de otra persona
    r = client.post("/settings/invitations/accept", json={"token": tok})
    assert r.status_code == 422 and "contraseña" in r.json()["detail"]
    r = client.post("/settings/invitations/accept", json={"token": tok, "full_name": "Otro Nombre", "password": "No-es-la-suya-2026"})
    assert r.status_code == 403  # nunca 401
    assert miembro() is None and db_session.get(User, u.id).failed_logins == 1
    assert db_session.get(Invitation, creada["id"]).accepted_at is None
    r = client.post("/settings/invitations/accept", json={"token": tok, "full_name": "Otro Nombre", "password": PW})
    assert r.status_code == 200 and r.json()["existing_account"] is True
    u = db_session.get(User, u.id)
    assert u.full_name == "Nombre Original" and miembro().role_code == "ventas"  # su nombre no cambia
    assert _login_as(client, "existente@ejemplo.com", PW).status_code == 200  # su contrasena de siempre
    # los intentos fallidos bloquean como en el login
    w = _mk_user(db_session, otra.id, "bloqueo.invitacion@ejemplo.com")
    tok_w = _token(client.post("/settings/invitations", json={"email": w.email, "role": "caja"}).json()["link"])
    for _ in range(6):
        assert client.post("/settings/invitations/accept", json={"token": tok_w, "password": "No-es-la-suya-2026"}).status_code == 403
    assert db_session.get(User, w.id).locked_until is not None
    assert client.post("/settings/invitations/accept", json={"token": tok_w, "password": PW}).status_code == 423
    # cuenta nueva: nombre y contrasena obligatorios
    tok2 = _token(client.post("/settings/invitations", json={"email": "sin.nombre@ejemplo.com", "role": "ventas"}).json()["link"])
    r = client.post("/settings/invitations/accept", json={"token": tok2, "password": PW})
    assert r.status_code == 422 and "nombre" in r.json()["detail"]
    assert client.post("/settings/invitations/accept", json={"token": tok2, "full_name": "Sin Nombre"}).status_code == 422


def test_enlace_del_admin_muere_si_entra_a_otra_empresa(client, auth, db_session):
    """Sin correo el admin de A tiene el enlace en la mano: si la persona entra despues a B, ya no le sirve (A no
    restablece la contrasena de un usuario compartido ni se queda con su acceso a B)."""
    tid = auth["tenant"]["id"]
    otra = _otra_empresa(db_session)
    u = _mk_user(db_session, tid, "se.va.a.otra@ejemplo.com")
    link = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW}).json()["link"]
    assert client.post("/auth/password-reset/check", json={"token": _token(link)}).status_code == 200
    r = client.post("/settings/invitations/accept", json={"token": _invitacion_de(db_session, otra, u.email), "password": PW})
    assert r.status_code == 200 and r.json()["existing_account"] is True
    assert client.post("/auth/password-reset/check", json={"token": _token(link)}).status_code == 404
    assert client.post("/auth/password-reset/confirm", json={"token": _token(link), "new_password": NUEVA}).status_code == 404
    assert _login_as(client, u.email, PW, tenant_slug=otra.slug).status_code == 200  # su contrasena sigue igual
    # aunque una fila quedara viva (aceptar ya las anula), un enlace de admin de un usuario compartido no sirve; el
    # de "olvide mi contrasena" (solo llega a su propio correo) si
    u = db_session.get(User, u.id)
    raw_admin, _ = issue_reset(db_session, u, tid, "admin", auth["user"]["id"], timedelta(hours=24), None)
    db_session.commit()
    assert client.post("/auth/password-reset/check", json={"token": raw_admin}).status_code == 404
    assert client.post("/auth/password-reset/confirm", json={"token": raw_admin, "new_password": NUEVA}).status_code == 404
    raw_auto, _ = issue_reset(db_session, u, tid, "auto", None, timedelta(hours=1), None)
    db_session.commit()
    assert client.post("/auth/password-reset/check", json={"token": raw_auto}).status_code == 200


def test_desactivar_anula_los_enlaces_de_esta_empresa(client, auth, db_session):
    """Desactivar aqui a alguien que sigue entrando a otra empresa anula los enlaces que salieron de aqui."""
    tid = auth["tenant"]["id"]
    otra = _otra_empresa(db_session)
    u = _mk_user(db_session, tid, "desactivar.enlace@ejemplo.com")
    raw_aqui, pr_aqui = issue_reset(db_session, u, tid, "admin", auth["user"]["id"], timedelta(hours=24), None)
    _, pr_alla = issue_reset(db_session, u, otra.id, "auto", None, timedelta(hours=1), None)
    db_session.add(TenantUser(tenant_id=otra.id, user_id=u.id, role_code="ventas"))
    db_session.commit()
    assert client.patch(f"/settings/users/{u.id}", json={"active": False}).json() == {"ok": True}
    assert db_session.get(PasswordReset, pr_aqui.id).used_at is not None
    assert db_session.get(PasswordReset, pr_alla.id).used_at is None  # lo suyo de la otra empresa no se toca
    assert client.post("/auth/password-reset/check", json={"token": raw_aqui}).status_code == 404


def test_aceptar_con_temporal_pide_crear_la_propia(client, auth, db_session):
    """La temporal la genero (y la conoce) el admin de la otra empresa: no prueba que quien acepta sea la duena."""
    tid = auth["tenant"]["id"]
    otra = _otra_empresa(db_session)
    u = _mk_user(db_session, tid, "temporal.invitada@ejemplo.com")
    temp = client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "temporal", "admin_password": ADMIN_PW}).json()["temp_password"]
    tok = _invitacion_de(db_session, otra, u.email)

    def miembro():
        return db_session.scalar(select(TenantUser).where(TenantUser.tenant_id == otra.id, TenantUser.user_id == u.id))

    r = client.post("/settings/invitations/accept", json={"token": tok, "password": temp})
    assert r.status_code == 409 and "temporal" in r.json()["detail"]
    assert miembro() is None and db_session.get(User, u.id).failed_logins == 0
    # vencida tampoco sirve (el login ya la rechazaba; aceptar no puede ser mas flojo)
    u = db_session.get(User, u.id)
    u.temp_password_expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    r = client.post("/settings/invitations/accept", json={"token": tok, "password": temp})
    assert r.status_code == 409 and "venció" in r.json()["detail"]
    assert miembro() is None
    assert db_session.scalar(select(Invitation).where(Invitation.tenant_id == otra.id)).accepted_at is None
    # entra con la temporal vigente, crea la suya y recien ahi acepta
    u.temp_password_expires_at = datetime.now(UTC) + timedelta(hours=1)
    db_session.commit()
    assert _login_as(client, u.email, temp, new_password=NUEVA).status_code == 200
    r = client.post("/settings/invitations/accept", json={"token": tok, "password": NUEVA})
    assert r.status_code == 200 and miembro().role_code == "ventas"


def test_token_sin_sv_sigue_valido(client, auth):
    now = datetime.now(UTC)
    payload = {"sub": str(auth["user"]["id"]), "tid": auth["tenant"]["id"], "role": "admin", "iat": now, "exp": now + timedelta(minutes=10), "typ": "access"}
    viejo = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert client.get("/auth/me", headers=_h(viejo)).status_code == 200


def test_correos_sensibles_sin_bcc_ni_enlace_guardado(client, auth, db_session):
    _con_bcc(db_session, auth["tenant"]["id"])
    r = client.post("/settings/invitations", json={"email": "bcc@ejemplo.com", "role": "ventas"}).json()
    m = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "invitation", EmailOutbox.entity_id == r["id"])).one()
    assert m.bcc is None and _token(r["link"]) not in m.html and "/invitacion/[oculto]" in m.html
    assert "Vendedor" in m.html and "ventas" not in m.html  # el rol con su nombre, no el codigo
    assert client.post("/settings/mail-test").status_code == 200
    prueba = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "mail_test")).one()
    assert prueba.bcc == "contabilidad@ejemplo.com"


def test_respaldo_sin_password_reset(client, auth, db_session, tmp_path):
    u = _mk_user(db_session, auth["tenant"]["id"], "respaldo.reset@ejemplo.com")
    client.post(f"/settings/users/{u.id}/reset-password", json={"mode": "enlace", "admin_password": ADMIN_PW})
    assert db_session.scalars(select(PasswordReset)).all()
    r = client.get("/settings/data-export")
    assert r.status_code == 200
    names = set(zipfile.ZipFile(BytesIO(r.content)).namelist())
    assert "data/password_reset.json" not in names and "data/user.json" in names
    zpath = tmp_path / "todo.zip"
    bk.write_to_path(db_session.connection(), Base.metadata, zpath)
    db_session.commit()
    assert "data/password_reset.json" not in zipfile.ZipFile(zpath).namelist()


def _migracion():
    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("usuarios_*.py"))
    spec = importlib.util.spec_from_file_location("usuarios_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migracion_borra_enlaces_ya_guardados(client, auth, db_session):
    """Las invitaciones guardadas antes del despliegue tenian el enlace en claro (y el respaldo exporta la tabla)."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    tid = auth["tenant"]["id"]
    tok = "T" * 43
    vieja = EmailOutbox(
        tenant_id=tid,
        to="vieja@ejemplo.com",
        subject="Invitación",
        html=f'<a href="https://app.ejemplo.com/invitacion/{tok}">Aceptar</a>',
        entity="invitation",
        entity_id=1,
        status="simulado",
    )
    otro = EmailOutbox(
        tenant_id=tid, to="cliente@ejemplo.com", subject="Cotización", html=f"<p>/invitacion/{tok}</p>", entity="quote", entity_id=1, status="simulado"
    )
    db_session.add_all([vieja, otro])
    db_session.commit()
    mod = _migracion()
    mod.op = Operations(MigrationContext.configure(db_session.connection()))
    mod._redact_outbox()
    db_session.commit()
    db_session.expire_all()
    html = db_session.get(EmailOutbox, vieja.id).html
    assert tok not in html and "/invitacion/[oculto]" in html
    assert db_session.get(EmailOutbox, otro.id).html == f"<p>/invitacion/{tok}</p>"  # solo los correos sensibles


def test_olvide_mi_contrasena(client, db_session, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    tid = db_session.scalar(select(Tenant.id).where(Tenant.slug == "crimson"))
    u = _mk_user(db_session, tid, "olvido@ejemplo.com")
    r1 = client.post("/auth/password-reset/request", json={"email": "no.existe@ejemplo.com"})
    r2 = client.post("/auth/password-reset/request", json={"email": "olvido@ejemplo.com"})
    assert r1.status_code == r2.status_code == 202 and r1.json() == r2.json() == {"ok": True}
    assert not db_session.scalars(select(PasswordReset)).all()  # sin correo activo no se hace nada

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "msg_prueba"}

    monkeypatch.setenv("RESEND_API_KEY", "re_prueba_no_real")
    monkeypatch.setattr("app.services.mail.httpx.post", lambda *a, **k: _Resp())
    r3 = client.post("/auth/password-reset/request", json={"email": "OLVIDO@ejemplo.com"})
    assert r3.status_code == 202 and r3.json() == {"ok": True}
    prs = db_session.scalars(select(PasswordReset).where(PasswordReset.user_id == u.id)).all()
    assert len(prs) == 1 and prs[0].kind == "auto" and prs[0].created_by is None
    vence = prs[0].expires_at if prs[0].expires_at.tzinfo else prs[0].expires_at.replace(tzinfo=UTC)
    assert timedelta(minutes=55) < vence - datetime.now(UTC) <= timedelta(minutes=60)
    mail = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "password_reset")).one()
    assert mail.status == "enviado" and "/restablecer/[oculto]" in mail.html and not re.search(r"/restablecer/[A-Za-z0-9_\-]{16,}", mail.html)
    assert _acciones(db_session, "password_reset_request", u.id)
    assert client.post("/auth/password-reset/request", json={"email": "olvido@ejemplo.com"}).status_code == 202  # 3.º del correo
    assert len(db_session.scalars(select(PasswordReset).where(PasswordReset.user_id == u.id)).all()) == 2
    assert client.post("/auth/password-reset/request", json={"email": "olvido@ejemplo.com"}).status_code == 202  # 4.º: se ignora
    assert len(db_session.scalars(select(PasswordReset).where(PasswordReset.user_id == u.id)).all()) == 2
    assert client.post("/auth/password-reset/request", json={"email": "olvido@ejemplo.com"}).status_code == 429  # 6.º desde la IP
