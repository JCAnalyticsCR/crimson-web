"""IP real del cliente sin tocar la red de Railway (plan 1.10).

El panel y la tienda llegan al API a traves del nginx del portal, que a su vez llama a la URL publica del API: para
uvicorn el cliente es la salida de ese nginx. Antes uvicorn corria con --forwarded-allow-ips='*' y tomaba la primera
direccion de X-Forwarded-For, que la escribe quien quiera (los limites por IP se saltaban cambiando una cabecera).

Ahora:
- nginx manda la IP del cliente en X-Crimson-Client-IP junto con X-Crimson-Proxy-Key = PROXY_SHARED_KEY (variable
  del portal y del api). El API la usa SOLO si la llave coincide (comparacion en tiempo constante) y es una IP valida.
- Sin llave, o si no coincide: request.client.host tal como lo deja uvicorn, que ya no cree en cualquier
  X-Forwarded-For (FORWARDED_ALLOW_IPS = solo redes internas, ver api/Dockerfile).
- Las dos cabeceras se quitan de la peticion: ninguna ruta ni ningun log las ve.
"""

from __future__ import annotations

import hmac
import ipaddress

from .config import settings

CLIENT_IP_HEADER = b"x-crimson-client-ip"
PROXY_KEY_HEADER = b"x-crimson-proxy-key"


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
                # una sola de cada una: repetidas = alguien las esta inyectando, no se cree ninguna
                if len(ips) == 1 and len(keys) == 1 and _key_ok(keys[0]):
                    ip = _ip(ips[0])
                    if ip:
                        scope["client"] = (ip, 0)
        await self.app(scope, receive, send)
