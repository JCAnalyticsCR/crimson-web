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


# Largo maximo de cualquier contrasena que entra por la API: Argon2 sobre una cadena de 1 MB es CPU regalada.
MAX_PASSWORD = 200


def create_access_token(user_id: int, tenant_id: int, role: str, sv: int = 0) -> str:
    """sv = user.session_version al emitirlo: cerrar las sesiones de alguien sube la version y este token muere."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "typ": "access",
        "sv": sv,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, leeway: int = 0) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm], leeway=leeway)


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


def totp_step(secret: str, code: str, after: int | None = None) -> int | None:
    """Paso de tiempo (ventana de 30 s) en que el codigo es valido, con un paso de tolerancia a cada lado; None si no
    sirve. `after` = ultimo paso ya usado por la cuenta: un codigo de ese paso o anterior no se acepta otra vez
    (quien lo vio por encima del hombro o en un registro no lo puede repetir)."""
    code = (code or "").strip()
    if not secret or len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now = totp.timecode(datetime.now(UTC))
    for step in (now - 1, now, now + 1):
        if after is not None and step <= after:
            continue
        if secrets.compare_digest(totp.generate_otp(step), code):
            return step
    return None


# --- Tokens firmados de proposito especifico (enlaces de pago, invitaciones) ---
def sign_purpose_token(purpose: str, data: dict, days: int) -> str:
    now = datetime.now(UTC)
    payload = {"typ": purpose, "iat": now, "exp": now + timedelta(days=days), **data}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def read_purpose_token(purpose: str, token: str, leeway: int = 0) -> dict:
    """`leeway` (segundos): tolerancia de reloj para iat/exp. PyJWT rechaza un token con iat en el futuro, asi que un
    enlace firmado por otro contenedor con el reloj un poco adelantado (worker vs api) fallaba al abrirlo enseguida."""
    payload = decode_token(token, leeway)
    if payload.get("typ") != purpose:
        raise jwt.InvalidTokenError("proposito incorrecto")
    return payload


_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # sin I, O, l, o, 0, 1


def new_temp_password() -> str:
    """12 caracteres al azar (~69 bits) en tres bloques para dictarla o pegarla. Siempre letras y numeros.
    La genera el servidor (el admin nunca elige la contrasena de otro), se muestra una vez y obliga a cambiarla."""
    while True:
        raw = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))
        if any(c.isdigit() for c in raw) and any(c.isalpha() for c in raw):
            return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


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
