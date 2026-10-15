"""URLs fijas en https del IdP y la API de recepcion de Hacienda. No se lee token_endpoint del discovery: anuncia http://."""

from __future__ import annotations

IDP = "https://idp.comprobanteselectronicos.go.cr/auth/realms"
ENDPOINTS = {
    "pruebas": {
        "token": f"{IDP}/rut-stag/protocol/openid-connect/token",
        "logout": f"{IDP}/rut-stag/protocol/openid-connect/logout",
        "client_id": "api-stag",
        "api": "https://api.comprobanteselectronicos.go.cr/recepcion-sandbox/v1/",
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
PRODUCTION_HOSTS = ("realms/rut/", "/recepcion/v1/")


def for_env(environment: str) -> dict:
    if environment not in ENDPOINTS:
        raise ValueError(f"Ambiente de Hacienda desconocido: {environment}")
    return ENDPOINTS[environment]
