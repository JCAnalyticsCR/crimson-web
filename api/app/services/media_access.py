"""Archivos publicos y privados.

public: lo de siempre (tienda, logo, eventos, fotos que sube el cliente en su portal). /api/media/f/<llave> abierto.
private: evidencia interna (levantamientos, OT, proyectos, contabilidad, tickets). La URL guardada en los registros
es la misma de siempre (/api/media/f/<llave>, sin firma: nada cambia en como se guardan las fotos), pero el archivo
solo se entrega con ?e=<vence>&s=<firma>. La firma es HMAC-SHA256 de "llave.vence" con un secreto de config; la
pide el portal (POST /media/sign, que valida empresa y permiso del modulo de origen) o la pone el servidor al
armar un PDF o informe que embebe fotos privadas.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Media

# Modulos que generan evidencia privada. El permiso "<modulo>.ver" es el que deja ver (firmar) sus archivos.
PRIVATE_MODULES = ("field", "projects", "accounting", "support_desk", "assets", "purchases")

# La vida se redondea a ventanas de 10 minutos: dos pedidos seguidos devuelven la misma URL y el navegador
# reutiliza la imagen de su cache en vez de bajarla otra vez.
_BUCKET = 600


def _secret() -> bytes:
    if settings.media_url_secret:
        return settings.media_url_secret.encode()
    # derivada: si alguien ve una firma de archivo no aprende nada del secreto de los JWT
    return hmac.new(settings.jwt_secret.encode(), b"crimson-media-url-v1", hashlib.sha256).digest()


def _sig(key: str, exp: int) -> str:
    mac = hmac.new(_secret(), f"{key}.{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def sign(key: str, ttl_seconds: int | None = None, now: float | None = None) -> tuple[int, str]:
    ttl = ttl_seconds if ttl_seconds is not None else settings.media_url_minutes * 60
    t = int(now if now is not None else time.time())
    exp = ((t + ttl) // _BUCKET + 1) * _BUCKET
    return exp, _sig(key, exp)


def verify(key: str, exp: int | None, sig: str | None, now: float | None = None) -> str:
    """'ok' | 'missing' | 'bad' | 'expired'. La firma se compara en tiempo constante antes de mirar la fecha."""
    if not exp or not sig:
        return "missing"
    if not hmac.compare_digest(_sig(key, int(exp)), sig):
        return "bad"
    if int(exp) < int(now if now is not None else time.time()):
        return "expired"
    return "ok"


def key_of(url: str | None) -> str | None:
    if not isinstance(url, str) or "/media/f/" not in url:
        return None
    k = url.rsplit("/media/f/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    return k or None


def base_url(url: str) -> str:
    return url.split("?", 1)[0]


def with_signature(url: str, key: str, ttl_seconds: int | None = None) -> str:
    exp, s = sign(key, ttl_seconds)
    return f"{base_url(url)}?e={exp}&s={s}"


def can_view(p, m: Media) -> bool:
    """Quien puede pedir la firma de un archivo privado: quien lo subio, o quien ve el modulo de origen.
    Sin modulo (algo que se paso a privado a mano) solo el administrador."""
    if m.visibility != "private":
        return True
    if m.created_by and m.created_by == p.user.id:
        return True
    if "*" in p.permissions:
        return True
    return bool(m.module) and p.can(m.module, "ver")


def sign_urls(db: Session, tenant_id: int, urls, ttl_seconds: int | None = None) -> list:
    """Para PDF, informes y respuestas que ya pasaron el control de permisos del registro: las URL de archivos
    privados de esta empresa salen firmadas; las publicas, externas o de otra empresa salen tal cual.
    Acepta string o {"url": ...} (misma forma que la lista de entrada)."""
    items = list(urls or [])
    keys = {k for k in (key_of(x.get("url") if isinstance(x, dict) else x) for x in items) if k}
    if not keys:
        return items
    private = set(db.scalars(select(Media.key).where(Media.tenant_id == tenant_id, Media.key.in_(keys), Media.visibility == "private")))
    out = []
    for x in items:
        u = x.get("url") if isinstance(x, dict) else x
        k = key_of(u)
        if k and k in private:
            signed = with_signature(u, k, ttl_seconds)
            x = {**x, "url": signed} if isinstance(x, dict) else signed
        out.append(x)
    return out


def email_ttl() -> int:
    return settings.media_email_url_days * 86400
