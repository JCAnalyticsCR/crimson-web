"""Plan 1.10 · Limite del login: solo cuentan los intentos fallidos, por IP, por cuenta + IP y por equipo, con espera
progresiva, y por cuenta desde todas las IPs. 25 fallidos desde una IP no bloquean a otra; repartir entre muchas IPs
no da intentos sin limite; y un tercero que lo dispara una y otra vez no deja afuera a la duena: desde sus equipos o
sus sesiones abiertas no espera, y desde uno nuevo espera a lo sumo un minuto. Nunca 423, exista o no la cuenta."""

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.ratelimit import FailureLimiter, LoginLimiter, login_limiter
from app.core.security import hash_password
from app.models import RefreshToken, Tenant, TenantUser, User
from app.routers.auth import DEVICE_COOKIE
from tests.conftest import ADMIN

MALA = "Clave-incorrecta-1"


def _login(client, email, pw, headers=None, **extra):
    return client.post("/auth/login", json={"email": email, "password": pw, **extra}, headers=headers)


def _reloj(monkeypatch):
    """Reloj falso para los contadores del login (no toca el time.monotonic del resto del proceso)."""
    t = [1000.0]
    for lim in (login_limiter.ip, login_limiter.account, login_limiter.total, login_limiter.device):
        monkeypatch.setattr(lim, "clock", lambda: t[0])
    return t


def _atacante(client) -> TestClient:
    """Otro navegador contra la misma app: su propio jar de cookies (sin la cookie de equipo de nadie)."""
    return TestClient(client.app)


def _bajo_ataque(client, email, from_ip, base=200) -> None:
    """Un tercero suma los fallidos de la cuenta (todas las IPs) repartiendo una IP por intento."""
    c = _atacante(client)
    for i in range(LoginLimiter.ACCOUNT_FREE):
        assert _login(c, email, MALA, from_ip(f"203.0.113.{base + i}")).status_code == 401


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


ESPERA_CUENTA = "muchos intentos fallidos con esta cuenta"


def test_todas_las_ips_cuentan_y_un_tercero_no_deja_afuera_a_la_duena(client, db_session, monkeypatch, from_ip):
    t = _reloj(monkeypatch)
    u = _mk_user(db_session, "victima@ejemplo.com")
    casa = from_ip("198.51.100.50")
    assert _login(client, u.email, "Usuario-2026-ok", casa).status_code == 200  # sesion abierta y cookie de equipo
    # ataque repartido: 2 IPs x 5 fallidos = 10 (repartir no da intentos sin limite)
    atacante = _atacante(client)
    for n in range(2):
        for _ in range(5):
            assert _login(atacante, u.email, MALA, from_ip(f"203.0.113.{20 + n}")).status_code == 401
    assert db_session.get(User, u.id).locked_until is None  # nada se bloquea en la base
    # desde un equipo y una IP nuevos se espera (no 423) y mientras tanto no se prueba la contrasena (no suma)
    nuevo = _atacante(client)
    r = _login(nuevo, u.email, "Usuario-2026-ok", from_ip("203.0.113.99"))
    assert (
        r.status_code == 429 and ESPERA_CUENTA in r.json()["detail"] and int(r.headers["Retry-After"]) <= LoginLimiter.ACCOUNT_CAP + 1
    )  # Retry-After redondea hacia arriba
    assert _login(atacante, u.email, MALA, from_ip("203.0.113.98")).status_code == 429
    assert login_limiter.account_failures(u.email) == 10
    # su equipo entra sin esperar aunque cambie de IP (datos moviles), y desde su sesion abierta tambien
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.51")).status_code == 200
    assert _login(nuevo, u.email, "Usuario-2026-ok", casa).status_code == 200
    # desde un equipo nuevo, esperado el minuto, entra
    otro = _atacante(client)
    r = _login(otro, u.email, "Usuario-2026-ok", from_ip("203.0.113.97"))
    assert r.status_code == 429
    t[0] += int(r.headers["Retry-After"])
    assert _login(otro, u.email, "Usuario-2026-ok", from_ip("203.0.113.97")).status_code == 200
    # que la duena entre no le devuelve margen al atacante
    assert login_limiter.account_failures(u.email) == 10


def test_tercero_con_muchas_ips_repite_y_la_duena_entra_antes_de_un_minuto(client, db_session, monkeypatch, from_ip):
    """El tercero vuelve a disparar la espera apenas vence, durante media hora y con una IP nueva en cada intento: no
    logra mas de un intento por minuto y la duena, desde un equipo y una IP nuevos, entra esperando a lo sumo un minuto."""
    t = _reloj(monkeypatch)
    u = _mk_user(db_session, "asediada@ejemplo.com")
    atacante = _atacante(client)
    inicio, fallidos, i, entro = t[0], 0, 0, False
    while t[0] - inicio < 1800:
        i += 1
        r = _login(atacante, u.email, MALA, from_ip(f"203.0.{113 + i // 250}.{i % 250 + 1}"))
        assert r.status_code in (401, 429)
        if r.status_code == 429:
            assert int(r.headers["Retry-After"]) <= LoginLimiter.ACCOUNT_CAP + 1  # Retry-After redondea hacia arriba
            t[0] += int(r.headers["Retry-After"])
            continue
        fallidos += 1
        if fallidos == 25:  # en plena repeticion, la duena prueba desde un telefono nuevo
            duena = _atacante(client)
            r = _login(duena, u.email, "Usuario-2026-ok", from_ip("198.51.100.80"))
            assert r.status_code == 429 and int(r.headers["Retry-After"]) <= LoginLimiter.ACCOUNT_CAP + 1  # Retry-After redondea hacia arriba
            t[0] += int(r.headers["Retry-After"])
            entro = _login(duena, u.email, "Usuario-2026-ok", from_ip("198.51.100.80")).status_code == 200
    assert entro
    assert fallidos <= LoginLimiter.ACCOUNT_FREE + 31  # unos 60 por hora contra la cuenta, aunque cambie de IP siempre
    assert db_session.get(User, u.id).locked_until is None


def test_la_espera_no_revela_si_la_cuenta_existe(client, db_session, from_ip):
    u = _mk_user(db_session, "existe@ejemplo.com")
    respuestas = []
    for n, email in enumerate((u.email, "no-existe@ejemplo.com")):
        _bajo_ataque(client, email, from_ip, base=100 + 20 * n)
        r = _login(_atacante(client), email, "Usuario-2026-ok", from_ip(f"198.51.100.{90 + n}"))
        respuestas.append((r.status_code, r.json()["detail"]))
    assert respuestas[0] == respuestas[1] and respuestas[0][0] == 429  # mismo codigo y mismo texto; nunca 423


def test_fallidos_de_la_cuenta_se_olvidan_y_una_sola_ip_tarda(client, db_session, monkeypatch, from_ip):
    """Una sola IP tambien puede llegar a los fallidos de la cuenta, pero la espera progresiva la hace tardar; y se
    olvidan 1 h despues del ultimo (ya no se acumulan para siempre)."""
    t = _reloj(monkeypatch)
    u = _mk_user(db_session, "paciente@ejemplo.com")
    ip = from_ip("203.0.113.40")
    atacante = _atacante(client)

    def fallar(n: int) -> int:
        """n fallidos desde esa IP, esperando lo que pida cada 429. Devuelve los segundos esperados."""
        espera = 0
        while n:
            r = _login(atacante, u.email, MALA, ip)
            if r.status_code == 429:
                espera += int(r.headers["Retry-After"])
                t[0] += int(r.headers["Retry-After"])
                continue
            assert r.status_code == 401
            n -= 1
        return espera

    assert fallar(9) >= 450  # 5 seguidos y despues 30, 60, 120 y 240 s
    assert login_limiter.account_failures(u.email) == 9 and not login_limiter.account_flagged(u.email)
    t[0] += 3601  # una hora sin fallidos: la cuenta (y la cuenta + IP) se olvidan
    assert login_limiter.account_failures(u.email) == 0
    fallar(9)
    assert _login(_atacante(client), u.email, "Usuario-2026-ok", from_ip("198.51.100.40")).status_code == 200  # sin espera
    fallar(1)  # el decimo dentro de la hora: desde un equipo nuevo se espera
    assert login_limiter.account_flagged(u.email) > 0 and db_session.get(User, u.id).locked_until is None
    r = _login(_atacante(client), u.email, "Usuario-2026-ok", from_ip("198.51.100.41"))
    assert r.status_code == 429 and ESPERA_CUENTA in r.json()["detail"]


def test_sesion_vieja_no_cuenta_como_ip_conocida(client, db_session, from_ip):
    u = _mk_user(db_session, "vieja@ejemplo.com")
    db_session.add(
        RefreshToken(user_id=u.id, tenant_id=_tid(db_session), token_hash="f" * 64, expires_at=datetime.now(UTC) - timedelta(days=1), ip="198.51.100.60")
    )
    db_session.commit()
    _bajo_ataque(client, u.email, from_ip)
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.60")).status_code == 429


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
    db_session.commit()
    _bajo_ataque(client, u.email, from_ip)
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.61")).status_code == 429  # revocada
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.62")).status_code == 429  # antes del cambio
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.63")).status_code == 200  # abierta y posterior


def test_sin_firma_valida_ninguna_ip_es_conocida(client, db_session, from_ip):
    """Sin PROXY_SHARED_KEY (o con otra) todo el panel llega con la IP del nginx del portal: esa IP compartida no
    exime de la espera a nadie. Una llamada directa al API (sin cabeceras del portal) si es la IP propia."""
    u = _mk_user(db_session, "compartida@ejemplo.com")
    assert _login(client, u.email, "Usuario-2026-ok").status_code == 200  # sesion abierta desde "testclient"
    client.cookies.clear()  # sin la cookie de equipo: aqui se prueba solo la IP
    _bajo_ataque(client, u.email, from_ip)
    sin_llave = {"X-Crimson-Client-IP": "198.51.100.70"}  # el portal sin la variable: llega la IP, no la llave
    otra_llave = {"X-Crimson-Client-IP": "198.51.100.70", "X-Crimson-Proxy-Key": "z" * 43}
    assert _login(client, u.email, "Usuario-2026-ok", sin_llave).status_code == 429
    assert _login(client, u.email, "Usuario-2026-ok", otra_llave).status_code == 429
    assert _login(client, u.email, "Usuario-2026-ok").status_code == 200


def test_cookie_de_equipo_solo_para_su_cuenta_vigente_y_con_su_limite(client, db_session, from_ip):
    u = _mk_user(db_session, "equipo@ejemplo.com")
    w = _mk_user(db_session, "vecina@ejemplo.com")
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.20")).status_code == 200
    equipo = client.cookies.get(DEVICE_COOKIE)
    assert equipo
    _bajo_ataque(client, u.email, from_ip, base=100)
    _bajo_ataque(client, w.email, from_ip, base=150)
    assert _login(client, u.email, "Usuario-2026-ok", from_ip("198.51.100.21")).status_code == 200  # su equipo, otra IP
    assert _login(client, w.email, "Usuario-2026-ok", from_ip("198.51.100.22")).status_code == 429  # es de otra cuenta
    equipo = client.cookies.get(DEVICE_COOKIE)  # cada entrada la renueva
    falsa = _atacante(client)
    falsa.cookies.set(DEVICE_COOKIE, equipo[:-4] + ("AAAA" if not equipo.endswith("AAAA") else "BBBB"))
    assert _login(falsa, u.email, "Usuario-2026-ok", from_ip("198.51.100.23")).status_code == 429  # firma que no sirve
    # una cookie robada no da intentos sin limite repartidos entre IPs: 5 y despues espera
    robada = _atacante(client)
    robada.cookies.set(DEVICE_COOKIE, equipo)
    for i in range(5):
        assert _login(robada, u.email, MALA, from_ip(f"203.0.113.{30 + i}")).status_code == 401
    r = _login(robada, u.email, "Usuario-2026-ok", from_ip("203.0.113.40"))
    assert r.status_code == 429 and "este equipo" in r.json()["detail"]
    # cerrar sus sesiones (o cambiar la contrasena) deja sin efecto las cookies de equipo de antes
    u = db_session.get(User, u.id)
    u.session_version = int(u.session_version or 0) + 1
    db_session.commit()
    antes = _atacante(client)
    antes.cookies.set(DEVICE_COOKIE, equipo)
    assert _login(antes, u.email, "Usuario-2026-ok", from_ip("198.51.100.24")).status_code == 429


def test_el_enlace_de_restablecer_deja_entrar_aunque_sigan_los_fallidos(client, db_session, from_ip):
    """Sin un equipo suyo y con un tercero disparando la espera: el enlace que llega a su correo deja a ese navegador
    de confianza (la contrasena se pide igual)."""
    from app.services.accounts import issue_reset

    u = _mk_user(db_session, "olvido.ataque@ejemplo.com")
    _bajo_ataque(client, u.email, from_ip, base=100)
    raw, _ = issue_reset(db_session, u, _tid(db_session), "auto", None, timedelta(hours=1), None)
    db_session.commit()
    duena = _atacante(client)
    nueva = "Otra-clave-2026"
    assert duena.post("/auth/password-reset/confirm", json={"token": raw, "new_password": nueva}).status_code == 200
    assert duena.cookies.get(DEVICE_COOKIE)
    assert login_limiter.account_failures(u.email) == 0  # los fallidos eran contra la contrasena anterior
    _bajo_ataque(client, u.email, from_ip, base=150)  # el tercero sigue
    assert _login(_atacante(client), u.email, nueva, from_ip("198.51.100.26")).status_code == 429
    assert _login(duena, u.email, nueva, from_ip("198.51.100.25")).status_code == 200


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
