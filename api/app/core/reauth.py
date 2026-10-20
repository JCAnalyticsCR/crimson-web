"""Reautenticacion con la contrasena del administrador para acciones delicadas (usuarios, credenciales de Hacienda,
consecutivos fiscales, produccion). Cada intento cuenta en el limitador antes de verificar."""

from __future__ import annotations

from fastapi import HTTPException

from .deps import Principal
from .ratelimit import reauth_limiter
from .security import verify_password


def actor_key(p: Principal) -> str:
    return f"{p.tenant.id}:{p.user.id}"


def require_reauth(p: Principal, password: str | None, message: str = "Confirmá con tu contraseña de administrador") -> None:
    reauth_limiter.hit(actor_key(p))  # cada intento cuenta, antes de verificar
    if not password or not verify_password(password, p.user.password_hash):
        raise HTTPException(403, message)  # 403, nunca 401
