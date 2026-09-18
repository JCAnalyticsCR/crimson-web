"""Cifrado de secretos por tenant (client secret de pasarelas, credenciales fiscales) con Fernet derivado del JWT_SECRET."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from .config import settings


def _f() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(("tenant-secrets:" + settings.jwt_secret).encode()).digest())
    return Fernet(key)


def encrypt(raw: str) -> str:
    return _f().encrypt(raw.encode()).decode()


def decrypt(token: str) -> str:
    return _f().decrypt(token.encode()).decode()


def mask(raw: str | None) -> str | None:
    if not raw:
        return None
    return "•" * 8 + raw[-4:]
