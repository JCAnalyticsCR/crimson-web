"""Configuracion que el worker necesita para que lo automatico le llegue a alguien.

El worker lee varias variables "sueltas" con os.getenv (RESEND_API_KEY, MAIL_FROM, BCCR_TOKEN) ademas de las de
Settings. Si falta una, el worker arranca igual y todo queda "simulado" o se salta en silencio. Al arrancar se registran
en WARNING solo los NOMBRES que faltan (nunca valores), y el latido del worker los lleva a /health/deep.
"""

from __future__ import annotations

import logging
import os

# en el orden en que se revisan; ninguna es secreta como nombre
WORKER_CONFIG = ("RESEND_API_KEY", "MAIL_FROM", "PUBLIC_BASE_URL", "BCCR_TOKEN", "FISCAL_KEYS")


def _env(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def missing_config() -> list[str]:
    """Nombres de la configuracion del worker que no estan definidos. FISCAL_KEYS y PUBLIC_BASE_URL tambien cuentan si
    vienen del .env local (Settings); las demas solo existen como variable de entorno."""
    from .config import Settings, settings

    fiscal = settings.fiscal_keys is not None and bool(settings.fiscal_keys.get_secret_value().strip())
    base_url = _env("PUBLIC_BASE_URL") or settings.public_base_url != Settings.model_fields["public_base_url"].default
    present = {
        "RESEND_API_KEY": _env("RESEND_API_KEY"),
        "MAIL_FROM": _env("MAIL_FROM"),
        "PUBLIC_BASE_URL": base_url,
        "BCCR_TOKEN": _env("BCCR_TOKEN"),
        "FISCAL_KEYS": fiscal,
    }
    return [n for n in WORKER_CONFIG if not present[n]]


def log_missing_config(logger: logging.Logger | None = None) -> list[str]:
    """Una linea WARNING con los nombres que faltan (o nada si esta completa)."""
    faltan = missing_config()
    if faltan:
        (logger or logging.getLogger("crimson.config")).warning(
            "Configuracion del worker incompleta, faltan: %s (sin RESEND_API_KEY los correos quedan 'simulado'; sin BCCR_TOKEN no hay tipo de cambio automatico)",
            ", ".join(faltan),
        )
    return faltan
