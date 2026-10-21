"""Plan 1.10 · IP real del cliente sin tocar la red: cabecera firmada del nginx del portal, uvicorn que ya no cree en
cualquier X-Forwarded-For, comprobantes de pago limitados por pedido y el nginx (HSTS de un ano, API en la tienda)."""

import re
from pathlib import Path

from sqlalchemy import select

from app.models import AuditLog
from tests.conftest import ADMIN

ROOT = Path(__file__).resolve().parents[2]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SLUG = "/public/store/crimson"


def _login_ip(db_session) -> str | None:
    return db_session.scalars(select(AuditLog.ip).where(AuditLog.action == "login").order_by(AuditLog.id.desc())).first()


def test_cabecera_firmada_da_la_ip_real_en_la_bitacora(client, db_session, from_ip):
    r = client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, headers=from_ip("203.0.113.45"))
    assert r.status_code == 200
    assert _login_ip(db_session) == "203.0.113.45"


def test_sin_la_llave_la_cabecera_no_cuenta(client, db_session, from_ip):
    from_ip("203.0.113.1")  # la llave existe, pero quien llama no la conoce
    h = {"X-Crimson-Client-IP": "198.51.100.77", "X-Crimson-Proxy-Key": "x" * 43}
    assert client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, headers=h).status_code == 200
    assert _login_ip(db_session) == "testclient"  # la del que conecta (el TestClient), no la que escribio
    # sin llave configurada en el API tampoco
    from app.core.config import settings

    settings.proxy_shared_key = None
    h2 = {"X-Crimson-Client-IP": "198.51.100.78", "X-Crimson-Proxy-Key": ""}
    assert client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, headers=h2).status_code == 200
    assert _login_ip(db_session) == "testclient"


def test_ip_invalida_o_cabeceras_repetidas_se_ignoran(client, db_session, from_ip):
    h = from_ip("no-es-una-ip")
    assert client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, headers=h).status_code == 200
    assert _login_ip(db_session) == "testclient"
    buena = from_ip("203.0.113.9")
    dobles = [("X-Crimson-Client-IP", "203.0.113.9"), ("X-Crimson-Client-IP", "198.51.100.1"), ("X-Crimson-Proxy-Key", buena["X-Crimson-Proxy-Key"])]
    assert client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, headers=dobles).status_code == 200
    assert _login_ip(db_session) == "testclient"


def test_sonda_dice_si_las_cabeceras_llegaron_y_valieron(client, from_ip):
    """La sonda de antes de desplegar: el edge del API deja pasar las cabeceras propias y el API solo cree en las
    firmadas. Sin sesion, sin la llave en la respuesta y sin cache."""
    h = from_ip("203.0.113.60")
    r = client.get("/auth/ip-check", headers=h)
    assert r.status_code == 200 and r.json() == {"ip": "203.0.113.60", "cabeceras": "firmadas"}
    assert r.headers["cache-control"] == "no-store"
    assert h["X-Crimson-Proxy-Key"] not in r.text
    inventadas = client.get("/auth/ip-check", headers={"X-Crimson-Client-IP": "198.51.100.5", "X-Crimson-Proxy-Key": "y" * 43})
    assert inventadas.json() == {"ip": "testclient", "cabeceras": "rechazadas"}
    # el portal sin PROXY_SHARED_KEY manda la IP pero no la llave (nginx no manda una cabecera vacia): "sin_llave",
    # para no confundirlo con una llave distinta entre servicios
    assert client.get("/auth/ip-check", headers={"X-Crimson-Client-IP": "198.51.100.5"}).json() == {"ip": "testclient", "cabeceras": "sin_llave"}
    assert client.get("/auth/ip-check").json() == {"ip": "testclient", "cabeceras": "ninguna"}


def test_sin_llave_en_produccion_avisa_al_arrancar_y_en_health(client, monkeypatch, caplog):
    """Sin PROXY_SHARED_KEY los limites por IP del panel y la tienda son globales: no se calla."""
    import logging

    from app.core.config import settings
    from app.main import create_app

    assert client.get("/health").json()["proxy_key"] == "falta"
    monkeypatch.setattr(settings, "env", "production")
    with caplog.at_level(logging.ERROR, logger="crimson"):
        create_app()
    assert any("PROXY_SHARED_KEY" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


def test_con_llave_health_lo_dice_y_no_hay_error(client, from_ip, monkeypatch, caplog):
    import logging

    from app.core.config import settings
    from app.main import create_app

    from_ip("203.0.113.2")
    assert client.get("/health").json()["proxy_key"] == "ok"
    monkeypatch.setattr(settings, "env", "production")
    with caplog.at_level(logging.ERROR, logger="crimson"):
        create_app()
    assert not any("PROXY_SHARED_KEY" in r.getMessage() for r in caplog.records)


def test_ip_key_agrupa_ipv6_por_red_64():
    from app.core.clientip import ip_key

    assert ip_key("203.0.113.7") == "203.0.113.7"
    assert ip_key("2001:db8:1:2::1") == ip_key("2001:db8:1:2:ffff:eeee:dddd:cccc") == "2001:db8:1:2::/64"
    assert ip_key("2001:db8:1:3::1") != ip_key("2001:db8:1:2::1")
    assert ip_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert ip_key("testclient") == "testclient" and ip_key(None) == "?"


def test_tienda_cuenta_ipv6_por_su_red(client, auth, from_ip):
    """Mi pedido (10 busquedas cada 10 min por IP): estrenar direccion dentro del mismo /64 no da cupo nuevo."""
    codes = [
        client.post(f"{SLUG}/orders/lookup", json={"number": "NO-EXISTE", "email": "x@ejemplo.com"}, headers=from_ip(f"2001:db8:9:9::{i + 1:x}")).status_code
        for i in range(11)
    ]
    assert codes[-1] == 429 and 429 not in codes[:-1]


def test_llave_corta_se_ignora_sin_tumbar_el_arranque():
    from app.core.config import Settings

    assert Settings(proxy_shared_key="corta").proxy_shared_key is None
    assert Settings(proxy_shared_key="k" * 40).proxy_shared_key is not None


async def _scope_client(middleware_cls, client, headers, **kw):
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope.get("client")
        seen["headers"] = dict(scope.get("headers") or [])

    mw = middleware_cls(app, **kw)
    await mw({"type": "http", "client": client, "headers": headers, "scheme": "http"}, None, None)
    return seen


def test_uvicorn_ya_no_cree_en_cualquier_x_forwarded_for():
    """FORWARDED_ALLOW_IPS del Dockerfile: solo redes internas (el edge de Railway llega desde 100.64.0.0/10) y se toma
    la ultima direccion no interna. Antes era '*' y valia la primera, la que escribe el cliente."""
    import asyncio

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    docker = (ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "--forwarded-allow-ips='*'" not in docker and "--no-access-log" in docker
    trusted = re.search(r'ENV FORWARDED_ALLOW_IPS="([^"]+)"', docker).group(1)
    xff = [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.50")]  # 6.6.6.6 lo escribio el cliente; el edge agrego la real
    via_edge = asyncio.run(_scope_client(ProxyHeadersMiddleware, ("100.64.0.3", 5000), xff, trusted_hosts=trusted))
    assert via_edge["client"][0] == "203.0.113.50"
    directo = asyncio.run(_scope_client(ProxyHeadersMiddleware, ("198.51.100.9", 5000), xff, trusted_hosts=trusted))
    assert directo["client"][0] == "198.51.100.9"  # quien no es el edge no elige su IP


def test_middleware_quita_las_cabeceras_internas(from_ip):
    import asyncio

    from app.core.clientip import ClientIPMiddleware

    h = from_ip("203.0.113.8")
    raw = [(b"x-crimson-client-ip", h["X-Crimson-Client-IP"].encode()), (b"x-crimson-proxy-key", h["X-Crimson-Proxy-Key"].encode()), (b"accept", b"*/*")]
    seen = asyncio.run(_scope_client(ClientIPMiddleware, ("100.64.0.3", 5000), raw))
    assert seen["client"] == ("203.0.113.8", 0)
    assert set(seen["headers"]) == {b"accept"}  # la llave no llega a ninguna ruta ni log


# ---------- comprobantes de pago: por pedido y por IP ----------
def _shop(client):
    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    cfg = {
        "methods": {
            "sinpe": {"active": True, "phone": "8888-7777", "holder": "Empresa de prueba SA"},
            "transferencia": {"active": False},
            "tarjeta": {"active": False},
            "efectivo": {"active": False},
        },
        "pickup": {"address": "Oficina de prueba", "hours": "L-V 8-17"},
    }
    assert client.put("/store/checkout", json=cfg).status_code == 200
    return client.get(f"{SLUG}/products").json()


def _order(client, prod_id, key):
    body = {
        "lines": [{"product_id": prod_id, "quantity": 1}],
        "contact": {"name": "Cliente Prueba", "email": "cliente@ejemplo.com", "phone": "8888-0000", "id_type": "fisica", "id_number": "1-1111-0111"},
        "delivery": {"type": "envio", "province": "San José", "canton": "Escazú", "district": "San Rafael", "address": "200 m norte del parque"},
        "shipping_method": "Envío GAM",
        "payment_method": "sinpe",
        "accept_terms": True,
        "idempotency_key": key,
    }
    r = client.post(f"{SLUG}/checkout", json=body)
    assert r.status_code == 201, r.text
    return r.json()["track_token"]


def test_comprobante_11_de_otro_pedido_entra(client, auth):
    prods = _shop(client)
    a = _order(client, prods[0]["id"], "ck-proof-000A")
    b = _order(client, prods[0]["id"], "ck-proof-000B")
    codes = [client.post(f"{SLUG}/orders/{a}/proof", files={"file": (f"a{i}.png", PNG + bytes([i]), "image/png")}).status_code for i in range(10)]
    assert 429 not in codes  # los 10 de la hora (los que pasan de 5 por pedido dan 409, pero cuentan)
    r = client.post(f"{SLUG}/orders/{a}/proof", files={"file": ("a11.png", PNG + b"\x0b", "image/png")})
    assert r.status_code == 429 and r.headers.get("Retry-After")
    # el comprobante numero 11 de la hora, pero de OTRO pedido, entra
    assert client.post(f"{SLUG}/orders/{b}/proof", files={"file": ("b.png", PNG + b"b", "image/png")}).status_code == 201


def test_comprobante_por_ip_no_traba_a_otra_persona(client, auth, from_ip):
    prods = _shop(client)
    a = _order(client, prods[0]["id"], "ck-proof-000C")
    otro = from_ip("203.0.113.66")
    for i in range(10):
        client.post(f"{SLUG}/orders/{a}/proof", files={"file": (f"c{i}.png", PNG + bytes([i]), "image/png")}, headers=otro)
    assert client.post(f"{SLUG}/orders/{a}/proof", files={"file": ("c.png", PNG + b"z", "image/png")}, headers=otro).status_code == 429
    # quien tiene el enlace desde otra conexion no queda trabado por el que abuso (409: ya hay 5 comprobantes)
    assert client.post(f"{SLUG}/orders/{a}/proof", files={"file": ("d.png", PNG + b"d", "image/png")}).status_code != 429


# ---------- nginx del portal ----------
NGINX = (ROOT / "portal" / "nginx.conf").read_text(encoding="utf-8")


def _bloques(texto: str, inicio: str = "location"):
    """(cabecera, cuerpo) de cada bloque `location ... { ... }` contando llaves (adentro puede haber un if { })."""
    for m in re.finditer(rf"(?m)^\s*{inicio}([^{{;]*)\{{", texto):
        nivel, i = 1, m.end()
        while nivel:
            nivel += {"{": 1, "}": -1}.get(texto[i], 0)
            i += 1
        yield m.group(1).strip(), texto[m.end() : i - 1]


def _location(nombre: str) -> str:
    return next(c for cab, c in _bloques(NGINX) if cab == nombre)


def test_nginx_hsts_de_un_ano_en_todas_las_respuestas():
    hsts = re.findall(r'Strict-Transport-Security "([^"]+)"', NGINX)
    assert hsts and all(v == "max-age=31536000" for v in hsts)
    # cada location con add_header propio repite el HSTS (un add_header en location anula los del server)
    con_headers = [(cab, cuerpo) for cab, cuerpo in _bloques(NGINX) if "add_header" in cuerpo]
    assert len(con_headers) >= 4
    for cab, cuerpo in con_headers:
        assert "Strict-Transport-Security" in cuerpo, cab


def test_nginx_manda_la_ip_firmada_al_api():
    api = _location("/api/")
    assert "proxy_set_header X-Crimson-Client-IP $remote_addr;" in api
    assert 'proxy_set_header X-Crimson-Proxy-Key "${PROXY_SHARED_KEY}";' in api
    assert "real_ip_header X-Forwarded-For;" in NGINX and "real_ip_recursive on;" in NGINX
    assert "set_real_ip_from 100.64.0.0/10;" in NGINX
    docker = (ROOT / "portal" / "Dockerfile").read_text(encoding="utf-8")
    assert "${PROXY_SHARED_KEY}" in docker and 'PROXY_SHARED_KEY=""' in docker
    # en Railway, sin llave (o corta) el portal avisa con ERROR al arrancar
    assert "/docker-entrypoint.d/15-crimson-proxy-key.sh" in docker and "chmod +x /docker-entrypoint.d/15-crimson-proxy-key.sh" in docker
    assert '"${#PROXY_SHARED_KEY}" -lt 32' in docker and "RAILWAY_ENVIRONMENT" in docker


def test_nginx_verifica_el_certificado_del_api():
    """La llave viaja en cada /api/: el tramo nginx -> API (URL publica HTTPS) verifica certificado y nombre. A nivel de
    server, asi cubre /api/ y las rutas de SEO; ninguna location lo apaga."""
    server = next(c for _, c in _bloques(NGINX, "server"))
    afuera = re.split(r"(?m)^\s*location\s", server, maxsplit=1)[0]  # lo del server antes de la primera location
    for d in ("proxy_ssl_verify on;", "proxy_ssl_verify_depth 3;", "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;"):
        assert d in afuera, d
    assert "proxy_ssl_verify off" not in NGINX


def test_nginx_en_la_tienda_solo_pasa_la_api_publica():
    regla = re.search(r'map "\$is_store:\$uri" \$store_api_blocked \{[^}]*"~([^"]+)" 1;', NGINX).group(1)
    bloqueado = re.compile(regla)
    for uri in ("/api/docs", "/api/openapi.json", "/api/auth/login", "/api/auth/refresh", "/api/settings", "/api/v1/products", "/api/webhooks/onvo/1"):
        assert bloqueado.search(f"1:{uri}"), uri
        assert not bloqueado.search(f":{uri}"), uri  # en el panel todo sigue igual
    for uri in ("/api/public/store/crimson", "/api/public/store-domain", "/api/public/pay/x", "/api/media/f/abc"):
        assert not bloqueado.search(f"1:{uri}"), uri
    assert "if ($store_api_blocked) { return 404; }" in _location("/api/")
