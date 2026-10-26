"""CORS por origen y por ruta (plan 1.10).

- Con cookies (Access-Control-Allow-Credentials) solo el panel, en cualquier ruta. En produccion es UNICAMENTE el origen
  de PUBLIC_BASE_URL (https://panel.crimsoncr.com), aunque CORS_ORIGINS traiga otros: la tienda (tienda.*) es del mismo
  sitio que el panel y la cookie de sesion viaja en sus peticiones, asi que un script en la tienda (un XSS o una
  etiqueta de analitica) con cookies leeria un token del panel. Los demas de CORS_ORIGINS van sin cookies (ERROR al
  arrancar, main.py). Fuera de produccion (desarrollo local) con cookies van todos los de CORS_ORIGINS. En produccion el
  panel llama al API por su propio nginx (mismo origen), asi que esto cubre sobre todo el desarrollo local.
- La landing (LANDING_ORIGINS) llama al API directo y solo para reportar una incidencia o pedir acceso al portal del
  cliente, y para LEER la ficha publica de un producto de la tienda (precio vigente de sus destacados, 26/10): sin
  cookies y solo en esas rutas (la ficha, solo GET). En cualquier otra ruta su origen no recibe ninguna cabecera CORS.
- /auth/refresh usa la misma lista de origenes con cookies (routers/auth.py, _portal_only).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware

# rutas que la landing puede llamar desde el navegador (sin cookies)
LANDING_PATHS = ("/public/incidents", "/public/access-requests")
# lectura del catalogo publico (lo mismo que muestra la tienda a cualquiera): solo GET
LANDING_READ = re.compile(r"^/public/store/[a-z0-9-]+/products/\d+$")
PORTAL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# X-View-As-Ally / X-View-As-User: "Ver como este aliado" del admin (solo lectura, core/ally.py)
PORTAL_HEADERS = ["Authorization", "Content-Type", "X-Requested-With", "X-View-As-Ally", "X-View-As-User"]


def landing_path(path: str, method: str = "POST") -> bool:
    if method in ("GET", "HEAD") and LANDING_READ.match(path):
        return True
    return any(path == p or path.startswith(p + "/") for p in LANDING_PATHS)


def origin_of(url: str | None) -> str | None:
    """scheme://host[:puerto] en minusculas (como lo manda el navegador en Origin), o None si no es una URL."""
    if not url:
        return None
    u = urlsplit(url.strip())
    return f"{u.scheme}://{u.netloc}".lower() if u.scheme and u.netloc else None


def _clean(origins: list[str]) -> list[str]:
    return list(dict.fromkeys(o.strip().rstrip("/").lower() for o in origins if o and o.strip()))


@dataclass(frozen=True)
class Origins:
    credentials: tuple[str, ...]  # con cookies: el panel
    plain: tuple[str, ...]  # de CORS_ORIGINS pero sin cookies (en produccion, los que no son el panel)
    landing: tuple[str, ...]  # LANDING_ORIGINS: sin cookies y solo en LANDING_PATHS
    ignored: tuple[str, ...]  # "*" o una landing en CORS_ORIGINS: se tratan como landing / nada


def split_origins(cors_origins: list[str], landing_origins: list[str], public_base_url: str | None, prod: bool) -> Origins:
    landing = [o for o in _clean(landing_origins) if o != "*"]
    portal = _clean(cors_origins)
    # un origen que tambien figura en LANDING_ORIGINS (la landing que quedo en CORS_ORIGINS de antes) se trata como
    # landing; y "*" con cookies seria cualquier sitio: se ignora
    ignored = [o for o in portal if o == "*" or o in landing]
    portal = [o for o in portal if o not in ignored]
    if prod:
        panel = origin_of(public_base_url)
        cred = [panel] if panel and panel not in landing else []
        plain = [o for o in portal if o not in cred]
    else:
        cred, plain = portal, []
    return Origins(tuple(cred), tuple(plain), tuple(landing), tuple(ignored))


def log_origins(o: Origins) -> None:
    log = logging.getLogger("crimson.config")
    if o.ignored:
        log.warning("CORS_ORIGINS: sin cookies para %s (comodin o landing)", ", ".join(o.ignored)[:200])
    if o.plain:
        log.error(
            "CORS_ORIGINS: en produccion solo el panel (PUBLIC_BASE_URL) va con cookies; sin cookies para %s. "
            "Dejar CORS_ORIGINS='[\"<origen del panel>\"]' (api/README.md, Acceso seguro).",
            ", ".join(o.plain)[:300],
        )


class SplitCORSMiddleware:
    def __init__(self, app, origins: Origins):
        self.landing_only = set(origins.landing)
        self.plain_only = set(origins.plain)
        self.portal = CORSMiddleware(
            app, allow_origins=list(origins.credentials), allow_credentials=True, allow_methods=PORTAL_METHODS, allow_headers=PORTAL_HEADERS
        )
        self.plain = CORSMiddleware(
            app, allow_origins=sorted(self.plain_only), allow_credentials=False, allow_methods=PORTAL_METHODS, allow_headers=PORTAL_HEADERS
        )
        self.landing = CORSMiddleware(
            app, allow_origins=sorted(self.landing_only), allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"]
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (self.landing_only or self.plain_only):
            origin = next((v for k, v in scope.get("headers") or [] if k == b"origin"), b"").decode("latin1")
            if origin in self.landing_only and landing_path(scope.get("path") or "", scope.get("method") or ""):
                await self.landing(scope, receive, send)
                return
            if origin in self.plain_only:
                await self.plain(scope, receive, send)
                return
        await self.portal(scope, receive, send)
