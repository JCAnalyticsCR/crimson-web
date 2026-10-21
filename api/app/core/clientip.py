"""IP real del cliente sin tocar la red de Railway (plan 1.10).

El panel y la tienda llegan al API a traves del nginx del portal, que a su vez llama a la URL publica del API: para
uvicorn el cliente es la salida de ese nginx. Antes uvicorn corria con --forwarded-allow-ips='*' y tomaba la primera
direccion de X-Forwarded-For, que la escribe quien quiera (los limites por IP se saltaban cambiando una cabecera).

Ahora:
- nginx manda la IP del cliente en X-Crimson-Client-IP junto con X-Crimson-Proxy-Key = PROXY_SHARED_KEY (variable
  del portal y del api). El API la usa SOLO si la llave coincide (comparacion en tiempo constante) y es una IP valida.
- Sin llave, o si no coincide: request.client.host tal como lo deja uvicorn, que ya no cree en cualquier
  X-Forwarded-For (FORWARDED_ALLOW_IPS = solo redes internas, ver api/Dockerfile). OJO: por el panel y la tienda eso es
  la IP de salida del nginx del portal, LA MISMA para todos: los limites por IP pasan a ser globales. Por eso la llave
  es obligatoria en produccion (aviso al arrancar y en /health) y el login no da por "conocida" una IP que no es propia
  de la persona (ip_propia).
- Las dos cabeceras se quitan de la peticion: ninguna ruta ni ningun log las ve.
- Sonda (GET /auth/ip-check, sin sesion): dice que IP ve el API y que paso con las cabeceras. Asi se verifica, antes de
  confiar en los limites por IP, que el edge del API no quita las cabeceras propias y que la llave es la misma.
- Limitadores: ip_key() agrupa una IPv6 en su red /64 (la bitacora guarda la IP completa).
"""

from __future__ import annotations

import hmac
import ipaddress

from .config import settings

CLIENT_IP_HEADER = b"x-crimson-client-ip"
PROXY_KEY_HEADER = b"x-crimson-proxy-key"
SOURCE_KEY = "crimson_client_ip"


def _ip(raw: bytes) -> str | None:
    try:
        s = raw.decode("latin1").strip()
        return str(ipaddress.ip_address(s)) if 0 < len(s) <= 45 else None
    except ValueError:
        return None


def _key_ok(raw: bytes) -> bool:
    secret = settings.proxy_shared_key
    if secret is None:
        return False
    expected = secret.get_secret_value().strip().encode()
    return bool(expected) and hmac.compare_digest(raw.strip(), expected)


class ClientIPMiddleware:
    """ASGI puro y por fuera de todo: el resto (limitadores, bitacora, request_log) ya ve la IP buena."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            ips: list[bytes] = []
            keys: list[bytes] = []
            rest = []
            for k, v in scope.get("headers") or []:
                if k == CLIENT_IP_HEADER:
                    ips.append(v)
                elif k == PROXY_KEY_HEADER:
                    keys.append(v)
                else:
                    rest.append((k, v))
            if ips or keys:
                scope = dict(scope)
                scope["headers"] = rest
                if ips and not keys:
                    # el nginx del portal sin PROXY_SHARED_KEY (con la variable vacia no manda la llave) o alguien
                    # escribio solo la IP: no se cree
                    src = "sin_llave"
                else:
                    src = "rechazadas"
                    # una sola de cada una: repetidas = alguien las esta inyectando, no se cree ninguna
                    if len(ips) == 1 and len(keys) == 1 and _key_ok(keys[0]):
                        ip = _ip(ips[0])
                        if ip:
                            scope["client"] = (ip, 0)
                            src = "firmadas"
                # para la sonda (GET /auth/ip-check) y el login: llegaron las cabeceras y si valieron. Nunca la llave.
                scope["state"] = {**(scope.get("state") or {}), SOURCE_KEY: src}
        await self.app(scope, receive, send)


def source(request) -> str:
    """Que paso con las cabeceras del nginx del portal:
    - firmadas: IP con la llave buena (la IP es la del cliente).
    - sin_llave: llego la IP sin llave (el portal no tiene PROXY_SHARED_KEY, o alguien la escribio).
    - rechazadas: llego una llave que no sirve (distinta entre portal y api, falta en el api, o repetida).
    - ninguna: no llego ninguna (no paso por el nginx del portal, o el edge del API las quita)."""
    return (request.scope.get("state") or {}).get(SOURCE_KEY, "ninguna")


def ip_propia(request) -> bool:
    """La IP que ve el API es la de la persona: firmada por el portal, o una llamada directa al API (sin cabeceras del
    portal; la IP la deja uvicorn desde el edge). Con 'sin_llave' o 'rechazadas' la peticion vino por el nginx del portal
    sin firma valida y la IP es la salida de ese nginx, compartida por todos."""
    return source(request) in ("firmadas", "ninguna")


def ip_key(ip: str | None) -> str:
    """Clave de los limitadores por IP. La IPv4 tal cual; una IPv6, su red /64 (quien tiene un /64 estrena direccion en
    cada peticion y se saltaria el limite); una IPv4 escrita como IPv6 (::ffff:a.b.c.d), la IPv4. Lo que no es una IP
    ("testclient", "?") queda igual. La bitacora guarda la IP completa: esto es solo para contar."""
    if not ip:
        return "?"
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 6:
        if a.ipv4_mapped is not None:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)
