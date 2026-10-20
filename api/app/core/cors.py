"""CORS por origen y por ruta (plan 1.10).

- El portal (CORS_ORIGINS, en produccion https://panel.crimsoncr.com) es el unico origen con cookies
  (Access-Control-Allow-Credentials), en cualquier ruta. En produccion el panel llama al API por su propio nginx
  (mismo origen), asi que esto cubre sobre todo el desarrollo local.
- La landing (LANDING_ORIGINS) llama al API directo y solo para reportar una incidencia o pedir acceso al portal del
  cliente: sin cookies y solo en esas rutas. En cualquier otra ruta su origen no recibe ninguna cabecera CORS.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware

# rutas que la landing puede llamar desde el navegador (sin cookies)
LANDING_PATHS = ("/public/incidents", "/public/access-requests")
PORTAL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# X-View-As-Ally / X-View-As-User: "Ver como este aliado" del admin (solo lectura, core/ally.py)
PORTAL_HEADERS = ["Authorization", "Content-Type", "X-Requested-With", "X-View-As-Ally", "X-View-As-User"]


def landing_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in LANDING_PATHS)


class SplitCORSMiddleware:
    def __init__(self, app, portal_origins: list[str], landing_origins: list[str]):
        self.portal_origins = list(dict.fromkeys(portal_origins))
        self.landing_only = {o for o in landing_origins if o not in self.portal_origins}
        self.portal = CORSMiddleware(
            app, allow_origins=self.portal_origins, allow_credentials=True, allow_methods=PORTAL_METHODS, allow_headers=PORTAL_HEADERS
        )
        self.landing = CORSMiddleware(
            app, allow_origins=sorted(self.landing_only), allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"]
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.landing_only and landing_path(scope.get("path") or ""):
            origin = next((v for k, v in scope.get("headers") or [] if k == b"origin"), b"").decode("latin1")
            if origin in self.landing_only:
                await self.landing(scope, receive, send)
                return
        await self.portal(scope, receive, send)
