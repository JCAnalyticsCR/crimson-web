"""Plan 1.10 · Limite del login: solo cuentan los intentos fallidos, por IP y por cuenta + IP, con espera progresiva,
y por cuenta desde todas las IPs (bloquea la cuenta). 25 fallidos desde una IP no bloquean a otra; repartir entre
muchas IPs no da intentos sin limite; el bloqueo no deja afuera a la duena desde sus sesiones abiertas."""

from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select

from app.core.ratelimit import FailureLimiter, login_limiter
from app.core.security import hash_password
from app.models import RefreshToken, Tenant, TenantUser, User
from tests.conftest import ADMIN

MALA = "Clave-incorrecta-1"


def _login(client, email, pw, headers=None, **extra):
    return client.post("/auth/login", json={"email": email, "password": pw, **extra}, headers=headers)


def _reloj(monkeypatch):
    """Reloj falso para los contadores del login (no toca el time.monotonic del resto del proceso)."""
    t = [1000.0]
    for lim in (login_limiter.ip, login_limiter.account, login_limiter.total):
        monkeypatch.setattr(lim, "clock", lambda: t[0])
    return t


def test_entrar_bien_no_gasta_cupo(client):
    for _ in range(30):  # antes, el intento 21 de la misma IP daba 429 aunque todos fueran buenos
        assert _login(client, *ADMIN).status_code == 200


def test_25_fallidos_desde_una_ip_no_bloquean_a_otra(client, db_session, from_ip):
    atacante = from_ip("203.0.113.10")
    codes = [_login(client, ADMIN[0], MALA, atacante).status_code for _ in range(25)]
    assert codes[:5] == [401] * 5 and set(codes[5:]) == {429}  # desde el 6.o espera
    # la cuenta no quedo bloqueada: los 429 no llegan a probar la contrasena ni cuentan
    u = db_session.scalar(select(User).where(User.email == ADMIN[0]))
    assert u.locked_until is None and login_limiter.account_failures(ADMIN[0]) == 5
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


def test_todas_las_ips_cuentan_y_el_bloqueo_no_deja_afuera_a_la_duena(client, db_session, from_ip):
    u = _mk_user(db_session, "victima@ejemplo.com")
    casa = from_ip("198.51.100.50")
    assert _login(client, u.email, "Usuario-2026-ok", casa).status_code == 200  # tiene una sesion abierta desde su casa
    # ataque repartido: 2 IPs x 5 fallidos = 10 -> la cuenta se bloquea 15 min (repartir no da intentos sin limite)
    for n in range(2):
        for _ in range(5):
            assert _login(client, u.email, MALA, from_ip(f"203.0.113.{20 + n}")).status_code == 401
    u = db_session.get(User, u.id)
    assert u.locked_until is not None
    # desde una IP nueva no entra ni con la contrasena buena, y no se prueba la contrasena (no suma)
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("203.0.113.99")).status_code == 423
    assert _login(client, u.email, MALA, from_ip("203.0.113.98")).status_code == 423
    assert login_limiter.account_failures(u.email) == 0  # el conteo arranca de cero con el bloqueo
    assert _login(client, u.email, "Usuario-2026-ok", casa).status_code == 200  # desde su sesion abierta, si
    # que la duena entre no le devuelve margen al atacante: el bloqueo sigue para los demas
    assert db_session.get(User, u.id).locked_until is not None
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("203.0.113.99")).status_code == 423


def test_fallidos_de_la_cuenta_se_olvidan_y_una_sola_ip_tarda(client, db_session, monkeypatch, from_ip):
    """Una sola IP tambien puede bloquear la cuenta, pero la espera progresiva la hace tardar; y los fallidos de la
    cuenta se olvidan 1 h despues del ultimo (ya no se acumulan para siempre)."""
    t = _reloj(monkeypatch)
    u = _mk_user(db_session, "paciente@ejemplo.com")
    ip = from_ip("203.0.113.40")

    def fallar(n: int) -> int:
        """n fallidos desde esa IP, esperando lo que pida cada 429. Devuelve los segundos esperados."""
        espera = 0
        while n:
            r = _login(client, u.email, MALA, ip)
            if r.status_code == 429:
                espera += int(r.headers["Retry-After"])
                t[0] += int(r.headers["Retry-After"])
                continue
            assert r.status_code == 401
            n -= 1
        return espera

    assert fallar(9) >= 450  # 5 seguidos y despues 30, 60, 120 y 240 s
    assert login_limiter.account_failures(u.email) == 9 and db_session.get(User, u.id).locked_until is None
    t[0] += 3601  # una hora sin fallidos: la cuenta (y la cuenta + IP) se olvidan
    assert login_limiter.account_failures(u.email) == 0
    fallar(9)
    assert db_session.get(User, u.id).locked_until is None
    fallar(1)  # el decimo dentro de la hora bloquea
    assert db_session.get(User, u.id).locked_until is not None


def test_sesion_vieja_no_cuenta_como_ip_conocida(client, db_session, from_ip):
    u = _mk_user(db_session, "vieja@ejemplo.com")
    db_session.add(
        RefreshToken(user_id=u.id, tenant_id=_tid(db_session), token_hash="f" * 64, expires_at=datetime.now(UTC) - timedelta(days=1), ip="198.51.100.60")
    )
    u.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.commit()
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.60")).status_code == 423


def test_sesion_cerrada_o_de_antes_del_cambio_de_contrasena_no_es_ip_conocida(client, db_session, from_ip):
    """La IP de un atacante que llego a tener sesion deja de servir al cerrar sesiones o cambiar la contrasena."""
    from app.core.config import settings

    u = _mk_user(db_session, "cerro@ejemplo.com")
    vence = datetime.now(UTC) + timedelta(days=settings.refresh_token_days)
    db_session.add(
        RefreshToken(user_id=u.id, tenant_id=_tid(db_session), token_hash="a" * 64, expires_at=vence, ip="198.51.100.61", revoked_at=datetime.now(UTC))
    )
    db_session.add(RefreshToken(user_id=u.id, tenant_id=_tid(db_session), token_hash="b" * 64, expires_at=vence - timedelta(hours=2), ip="198.51.100.62"))
    db_session.add(RefreshToken(user_id=u.id, tenant_id=_tid(db_session), token_hash="c" * 64, expires_at=vence, ip="198.51.100.63"))
    u.password_changed_at = datetime.now(UTC) - timedelta(hours=1)  # el token "b" se emitio antes del cambio
    u.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.commit()
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.61")).status_code == 423  # revocada
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.62")).status_code == 423  # antes del cambio
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.63")).status_code == 200  # abierta y posterior


def test_sin_firma_valida_ninguna_ip_es_conocida(client, db_session, from_ip):
    """Sin PROXY_SHARED_KEY (o con otra) todo el panel llega con la IP del nginx del portal: esa IP compartida no
    exime del bloqueo a nadie. Una llamada directa al API (sin cabeceras del portal) si es la IP propia."""
    from_ip("203.0.113.1")  # la llave existe en el API
    u = _mk_user(db_session, "compartida@ejemplo.com")
    assert _login(client, u.email, "Usuario-2026-ok").status_code == 200  # sesion abierta desde "testclient"
    u.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db_session.commit()
    sin_llave = {"X-Crimson-Client-IP": "198.51.100.70"}  # el portal sin la variable: llega la IP, no la llave
    otra_llave = {"X-Crimson-Client-IP": "198.51.100.70", "X-Crimson-Proxy-Key": "z" * 43}
    assert _login(client, u.email, "Usuario-2026-ok", sin_llave).status_code == 423
    assert _login(client, u.email, "Usuario-2026-ok", otra_llave).status_code == 423
    assert _login(client, u.email, "Usuario-2026-ok").status_code == 200


def test_ipv6_cuenta_por_su_red_64(client, from_ip):
    """Quien tiene un /64 estrena direccion en cada peticion: el limite por IP es por la red, no por la direccion."""
    for i in range(20):
        assert _login(client, f"nadie6-{i}@ejemplo.com", MALA, from_ip(f"2001:db8:1:2::{i + 1:x}")).status_code == 401
    r = _login(client, *ADMIN, from_ip("2001:db8:1:2:ffff::9"))
    assert r.status_code == 429 and "conexión" in r.json()["detail"]
    assert _login(client, *ADMIN, from_ip("2001:db8:1:3::1")).status_code == 200  # otra red /64


def test_failure_limiter_tiene_tope_de_claves():
    lim = FailureLimiter(free=1, base=1, cap=1, forget=10_000, max_keys=10)
    t = [0.0]
    lim.clock = lambda: t[0]
    for i in range(25):
        t[0] += 1
        lim.fail(f"k{i}")
    assert len(lim.state) <= 10
    assert lim.count("k24") == 1 and lim.count("k0") == 0  # se olvidan las de fallo mas viejo; la reciente queda


def test_codigo_2fa_equivocado_tambien_cuenta(client, auth, from_ip):
    sec = client.post("/auth/2fa/setup").json()["secret"]
    assert client.post("/auth/2fa/verify", json={"code": pyotp.TOTP(sec).now()}).status_code == 200
    client.headers.pop("Authorization", None)
    ip = from_ip("203.0.113.13")
    malos = {pyotp.TOTP(sec).at(datetime.now(UTC) + timedelta(seconds=s)) for s in (-30, 0, 30)}
    codigo_malo = next(f"{i:06d}" for i in range(1_000_000) if f"{i:06d}" not in malos)
    codes = [_login(client, *ADMIN, ip, totp_code=codigo_malo).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert login_limiter.account_failures(ADMIN[0]) == 5  # adivinar el codigo suma al bloqueo de la cuenta
