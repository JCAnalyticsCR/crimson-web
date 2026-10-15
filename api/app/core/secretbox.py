"""Cifrado de secretos fiscales (credenciales de Hacienda, contrasena del buzon) con AES-256-GCM.

La llave sale de FISCAL_KEYS (variable de entorno, nunca de la base ni del JWT_SECRET): "kid:base64url32,...". La primera
cifra; todas descifran (rotacion con app.tools.rotate_fiscal_keys). Cada texto cifrado lleva datos asociados
"empresa:ambiente:campo": copiado a otra empresa, ambiente o campo no se descifra.

Formato: v1.<kid>.<base64url(nonce || ciphertext)>. Nunca se registra el texto plano.

Sin FISCAL_KEYS: solo en desarrollo (ENV=local con SQLite) se usa una llave derivada (kid "dev") y se avisa una vez.
En cualquier otro caso available() es False y los endpoints que guardan credenciales responden 503. Las filas cifradas
con "dev" nunca se descifran fuera de esa condicion (un respaldo de desarrollo restaurado en un servidor no expone nada).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import parse_fiscal_keys, settings

log = logging.getLogger("crimson.secretbox")
DEV_KID = "dev"
_warned = {"dev": False}
_logged: set[str] = set()


class SecretUnavailable(Exception):
    """El secreto no se puede descifrar (llave ausente, otra llave, dato alterado). El mensaje es legible y sin secretos."""


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _dev_allowed() -> bool:
    return settings.env == "local" and settings.database_url.startswith("sqlite")


def _dev_key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"crimson-fiscal-dev").derive(settings.jwt_secret.encode())


def _instance_line() -> str:
    """Lo que decide si este proceso puede hablar con produccion de Hacienda (sin secretos): api y worker deben
    coincidir, porque el que envia y consulta es el worker."""
    from ..providers.hacienda.client import instance_fingerprint

    return f"huella de instancia {instance_fingerprint()} · ENV={settings.env} · HACIENDA_PRODUCTION_ALLOWED={settings.hacienda_production_allowed}"


def keys() -> list[tuple[str, bytes]]:
    """Llaves vigentes en orden (la primera cifra). Vacio = no hay con que cifrar."""
    raw = settings.fiscal_keys.get_secret_value() if settings.fiscal_keys else ""
    if raw.strip():
        try:
            ks = parse_fiscal_keys(raw)
        except ValueError as e:
            # mal escrita: las credenciales fiscales quedan sin llave (503) y el servicio sigue arriba
            if "malformed" not in _logged:
                _logged.add("malformed")
                log.error("%s: las credenciales fiscales y la contraseña del buzón quedan sin llave hasta corregirla", e)
            return []
        sig = ",".join(f"{kid}:{hashlib.sha256(k).hexdigest()[:8]}" for kid, k in ks)
        if sig not in _logged:  # solo kid y huella corta: sirve para ver que api y worker tienen la misma
            _logged.add(sig)
            log.info("FISCAL_KEYS cargadas: %s · %s", sig, _instance_line())
        return ks
    if _dev_allowed():
        if not _warned["dev"]:
            _warned["dev"] = True
            log.warning("FISCAL_KEYS sin definir: se usa una llave de DESARROLLO (solo ENV=local con SQLite)")
        return [(DEV_KID, _dev_key())]
    return []


def available() -> bool:
    return bool(keys())


def primary_kid() -> str | None:
    ks = keys()
    return ks[0][0] if ks else None


def aad_for(tenant_id: int, environment: str, field: str) -> str:
    return f"{tenant_id}:{environment}:{field}"


def seal(plain: bytes, aad: str) -> str:
    ks = keys()
    if not ks:
        raise SecretUnavailable("Falta configurar FISCAL_KEYS en el servidor")
    kid, key = ks[0]
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plain, aad.encode())
    return f"v1.{kid}.{_b64e(nonce + ct)}"


def kid_of(token: str | None) -> str | None:
    if not token or not token.startswith("v1."):
        return None
    parts = token.split(".", 2)
    return parts[1] if len(parts) == 3 else None


def unseal(token: str, aad: str) -> bytes:
    parts = (token or "").split(".", 2)
    if len(parts) != 3 or parts[0] != "v1":
        raise SecretUnavailable("Formato de dato cifrado desconocido: vuelva a cargar el dato")
    kid, body = parts[1], parts[2]
    if kid == DEV_KID and not _dev_allowed():
        raise SecretUnavailable("La llave dev no está en FISCAL_KEYS")
    key = dict(keys()).get(kid)
    if key is None:
        raise SecretUnavailable(f"La llave {kid} no está en FISCAL_KEYS")
    try:
        raw = _b64d(body)
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad.encode())
    except Exception as e:  # noqa: BLE001 - InvalidTag, base64 roto: mismo mensaje, sin detalles
        raise SecretUnavailable("No se pudo descifrar: vuelva a cargar el dato") from e


def seal_text(text: str, aad: str) -> str:
    return seal(text.encode("utf-8"), aad)


def unseal_text(token: str, aad: str) -> str:
    return unseal(token, aad).decode("utf-8")
