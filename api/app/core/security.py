"""Hash de contrasenas (Argon2), JWT de acceso, refresh tokens rotativos y TOTP."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import settings

_ph = PasswordHasher()


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False


def password_is_strong(raw: str) -> bool:
    """Minimo 10 caracteres con letras y numeros. Nunca aceptar passwords triviales en cuentas reales."""
    return len(raw) >= 10 and any(c.isdigit() for c in raw) and any(c.isalpha() for c in raw)


def create_access_token(user_id: int, tenant_id: int, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def new_refresh_token() -> tuple[str, str]:
    """Devuelve (token en claro para la cookie, hash para la BD)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_days)


# --- 2FA TOTP ---
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Crimson")


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# --- Tokens firmados de proposito especifico (enlaces de pago, invitaciones) ---
def sign_purpose_token(purpose: str, data: dict, days: int) -> str:
    now = datetime.now(UTC)
    payload = {"typ": purpose, "iat": now, "exp": now + timedelta(days=days), **data}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def read_purpose_token(purpose: str, token: str) -> dict:
    payload = decode_token(token)
    if payload.get("typ") != purpose:
        raise jwt.InvalidTokenError("proposito incorrecto")
    return payload


def new_recovery_codes(n: int = 10) -> list[str]:
    """Codigos de un solo uso para cuando se pierde el telefono. Se muestran UNA vez y se guardan hasheados.
    Formato en dos bloques de cuatro para poder dictarlos por telefono sin equivocarse."""
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin I, O, 0 ni 1: se confunden al leerlos
    codigos = []
    for _ in range(n):
        crudo = "".join(secrets.choice(alfabeto) for _ in range(8))
        codigos.append(f"{crudo[:4]}-{crudo[4:]}")
    return codigos


def normalize_recovery(code: str) -> str:
    return code.strip().upper().replace(" ", "").replace("-", "")


def hash_recovery(code: str) -> str:
    return hash_token(normalize_recovery(code))


def use_recovery_code(stored: list, code: str) -> list | None:
    """Si el codigo existe lo consume y devuelve la lista sin el. None = no era valido.
    Un codigo sirve una sola vez: usarlo lo borra."""
    objetivo = hash_recovery(code)
    if objetivo not in (stored or []):
        return None
    return [h for h in stored if h != objetivo]
