"""URLs fijas en https del IdP y la API de recepcion de Hacienda. No se lee token_endpoint del discovery: anuncia http://."""

from __future__ import annotations

from urllib.parse import urlsplit

IDP = "https://idp.comprobanteselectronicos.go.cr/auth/realms"
ENDPOINTS = {
    "pruebas": {
        "token": f"{IDP}/rut-stag/protocol/openid-connect/token",
        "logout": f"{IDP}/rut-stag/protocol/openid-connect/logout",
        "client_id": "api-stag",
        # 05/11 (verificado en produccion con el token de pruebas): la ruta del anexo
        # https://api.comprobanteselectronicos.go.cr/recepcion-sandbox/v1/ responde 403 IncompleteSignatureException del
        # gateway de AWS a cualquier consulta, asi que ningun comprobante de Pruebas llegaba. La que Hacienda atiende es
        # la del host api-sandbox (la misma consulta responde 200).
        "api": "https://api-sandbox.comprobanteselectronicos.go.cr/recepcion/v1/",
    },
    "produccion": {
        "token": f"{IDP}/rut/protocol/openid-connect/token",
        "logout": f"{IDP}/rut/protocol/openid-connect/logout",
        "client_id": "api-prod",
        "api": "https://api.comprobanteselectronicos.go.cr/recepcion/v1/",
    },
}
# consulta publica de actividades economicas registradas (para sugerir en Ajustes)
ACTIVITIES_URL = "https://api.hacienda.go.cr/fe/ae"

# Deteccion de produccion por host exacto (no por subcadena: la API de pruebas tambien lleva /recepcion/v1/).
PRODUCTION_API_HOST = "api.comprobanteselectronicos.go.cr"  # todo este host es produccion, incluida la ruta muerta recepcion-sandbox
IDP_HOST = "idp.comprobanteselectronicos.go.cr"
PRODUCTION_REALM = "/auth/realms/rut/"


def is_production_url(url: str) -> bool:
    """True si la URL habla con el ambiente de PRODUCCION de Hacienda (API o realm rut del IdP)."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return True  # guarda de seguridad: una URL que no se puede leer se trata como produccion (falla cerrada)
    host = (parts.hostname or "").lower()
    if host == PRODUCTION_API_HOST:
        return True
    if host == IDP_HOST:
        return (parts.path or "").lower().startswith(PRODUCTION_REALM)
    return False


def _self_check() -> None:
    """Autocomprobacion al importar: ninguna URL de pruebas es de produccion y todas las de produccion lo son. Si alguien
    cambia ENDPOINTS mal, falla el arranque (y antes test_hacienda_cliente.py)."""
    for k in ("token", "logout", "api"):
        if is_production_url(ENDPOINTS["pruebas"][k]):
            raise RuntimeError(f"ENDPOINTS['pruebas']['{k}'] apunta a produccion de Hacienda")
        if not is_production_url(ENDPOINTS["produccion"][k]):
            raise RuntimeError(f"ENDPOINTS['produccion']['{k}'] no se reconoce como produccion de Hacienda")


_self_check()


def for_env(environment: str) -> dict:
    if environment not in ENDPOINTS:
        raise ValueError(f"Ambiente de Hacienda desconocido: {environment}")
    return ENDPOINTS[environment]
