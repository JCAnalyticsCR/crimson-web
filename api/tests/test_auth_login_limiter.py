"""Plan 1.10 · Limite del login: solo cuentan los intentos fallidos, por IP y por cuenta + IP, con espera progresiva.
25 fallidos desde una IP no bloquean a otra, y un tercero no puede bloquear la cuenta desde una sola IP."""

from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select

from app.core.ratelimit import login_limiter
from app.core.security import hash_password
from app.models import RefreshToken, Tenant, TenantUser, User
from tests.conftest import ADMIN

MALA = "Clave-incorrecta-1"


def _login(client, email, pw, headers=None, **extra):
    return client.post("/auth/login", json={"email": email, "password": pw, **extra}, headers=headers)


def _reloj(monkeypatch):
    """Reloj falso para las dos esperas del login (no toca el time.monotonic del resto del proceso)."""
    t = [1000.0]
    monkeypatch.setattr(login_limiter.ip, "clock", lambda: t[0])
    monkeypatch.setattr(login_limiter.account, "clock", lambda: t[0])
    return t


def test_entrar_bien_no_gasta_cupo(client):
    for _ in range(30):  # antes, el intento 21 de la misma IP daba 429 aunque todos fueran buenos
        assert _login(client, *ADMIN).status_code == 200


def test_25_fallidos_desde_una_ip_no_bloquean_a_otra(client, db_session, from_ip):
    atacante = from_ip("203.0.113.10")
    codes = [_login(client, ADMIN[0], MALA, atacante).status_code for _ in range(25)]
    assert codes[:5] == [401] * 5 and set(codes[5:]) == {429}  # desde el 6.o espera
    # la cuenta no quedo bloqueada en la base: el tercero aporta como mucho 3 fallidos
    u = db_session.scalar(select(User).where(User.email == ADMIN[0]))
    assert u.locked_until is None and u.failed_logins <= 3
    assert _login(client, *ADMIN, from_ip("198.51.100.30")).status_code == 200
    # y el mismo atacante sigue esperando aunque ahora acierte la contrasena
    assert _login(client, *ADMIN, atacante).status_code == 429


def test_espera_progresiva_y_se_olvida_al_acertar(client, monkeypatch, from_ip):
    t = _reloj(monkeypatch)
    ip = from_ip("203.0.113.11")
    for _ in range(5):
        assert _login(client, ADMIN[0], MALA, ip).status_code == 401
    r = _login(client, ADMIN[0], MALA, ip)
    assert r.status_code == 429 and 25 <= int(r.headers["Retry-After"]) <= 31  # 30 s despues del 5.o fallido
    t[0] += 31
    assert _login(client, ADMIN[0], MALA, ip).status_code == 401  # 6.o fallido: ahora espera 60 s
    t[0] += 31
    r = _login(client, ADMIN[0], MALA, ip)
    assert r.status_code == 429 and 25 <= int(r.headers["Retry-After"]) <= 31
    t[0] += 30
    assert _login(client, *ADMIN, ip).status_code == 200  # acierta: la cuenta + IP se olvida
    for _ in range(5):
        assert _login(client, ADMIN[0], MALA, ip).status_code == 401


def test_por_ip_contra_muchas_cuentas(client, from_ip):
    ip = from_ip("203.0.113.12")
    for i in range(20):  # cuentas que no existen tambien cuentan (adivinar correos)
        assert _login(client, f"nadie{i}@ejemplo.com", MALA, ip).status_code == 401
    r = _login(client, *ADMIN, ip)
    assert r.status_code == 429 and "conexión" in r.json()["detail"]
    assert _login(client, *ADMIN, from_ip("198.51.100.31")).status_code == 200


def _tid(db) -> int:
    return db.scalar(select(Tenant.id).where(Tenant.slug == "crimson"))


def _mk_user(db, email):
    tid = _tid(db)
    u = User(email=email, full_name="Usuario de prueba", password_hash=hash_password("Usuario-2026-ok"))
    db.add(u)
    db.flush()
    db.add(TenantUser(tenant_id=tid, user_id=u.id, role_code="ventas"))
    db.commit()
    return u


def test_bloqueo_de_cuenta_solo_con_varias_ips_y_no_deja_afuera_a_la_duena(client, db_session, from_ip):
    u = _mk_user(db_session, "victima@ejemplo.com")
    casa = from_ip("198.51.100.50")
    assert _login(client, u.email, "Usuario-2026-ok", casa).status_code == 200  # ya entro desde su casa
    # ataque distribuido: 4 IPs x 3 fallidos = 12 -> la cuenta se bloquea 15 min
    for n in range(4):
        for _ in range(3):
            assert _login(client, u.email, MALA, from_ip(f"203.0.113.{20 + n}")).status_code == 401
    u = db_session.get(User, u.id)
    assert u.locked_until is not None
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("203.0.113.99")).status_code == 423  # desde otro lado no
    assert _login(client, u.email, "Usuario-2026-ok", casa).status_code == 200  # desde donde ya entro, si
    assert db_session.get(User, u.id).locked_until is None


def test_sesion_vieja_no_cuenta_como_ip_conocida(client, db_session, from_ip):
    u = _mk_user(db_session, "vieja@ejemplo.com")
    db_session.add(
        RefreshToken(user_id=u.id, tenant_id=_tid(db_session), token_hash="f" * 64, expires_at=datetime.now(UTC) - timedelta(days=1), ip="198.51.100.60")
    )
    u.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.commit()
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.60")).status_code == 423


def test_codigo_2fa_equivocado_tambien_cuenta(client, auth, from_ip):
    sec = client.post("/auth/2fa/setup").json()["secret"]
    assert client.post("/auth/2fa/verify", json={"code": pyotp.TOTP(sec).now()}).status_code == 200
    client.headers.pop("Authorization", None)
    ip = from_ip("203.0.113.13")
    malos = {pyotp.TOTP(sec).at(datetime.now(UTC) + timedelta(seconds=s)) for s in (-30, 0, 30)}
    codigo_malo = next(f"{i:06d}" for i in range(1_000_000) if f"{i:06d}" not in malos)
    codes = [_login(client, *ADMIN, ip, totp_code=codigo_malo).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
