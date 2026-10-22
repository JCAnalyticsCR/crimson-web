"""Verificacion en dos pasos obligatoria por rol (plan 1.10).

Por defecto la exigen administracion y contabilidad (ven y mueven el dinero), con 7 dias de gracia contados desde la
primera vez que la persona entra con ese rol sin 2FA. La empresa lo ajusta en Ajustes -> Usuarios (twofa_roles y
twofa_grace_days en tenant.settings). Vencida la gracia, el panel interno responde 403 con la cabecera X-2FA-Setup y
el portal muestra la pantalla de configuracion forzada; /auth (configurar el 2FA, Mi cuenta, salir) sigue abierto.

El acceso de soporte temporal (rol "soporte", Ajustes -> Acceso de soporte) la exige SIEMPRE y sin gracia, en toda
empresa: es una cuenta externa que lee contabilidad, ventas, pagos e inventario, puede tener concesiones en varias
empresas y sus concesiones duran menos que cualquier gracia. No es configurable por empresa.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

DEFAULT_ROLES = ("admin", "contabilidad")
ALWAYS_ROLES = ("soporte",)  # siempre, sin gracia y en toda empresa (ver arriba)
DEFAULT_GRACE_DAYS = 7
MAX_GRACE_DAYS = 30
HEADER = "X-2FA-Setup"
BLOCKED_MSG = "Tu rol exige la verificación en dos pasos y venció el plazo para activarla. Activala para seguir usando el panel."


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def role_choices(db=None, tenant=None) -> list[str]:
    """Roles a los que se les puede exigir: los internos que se asignan en Ajustes (no clientes, aliados ni soporte) y,
    con la empresa, tambien los que creo su administrador (Ajustes -> Usuarios -> Roles; reunion 21/10, pedido 20)."""
    from .deps import ASSIGNABLE_ROLES, assignable_roles

    if db is not None and tenant is not None:
        return assignable_roles(db, tenant)
    return list(ASSIGNABLE_ROLES)


def _own_custom(tenant, code: str) -> bool:
    """Codigo de un rol creado por el admin de ESTA empresa (routers/roles_admin.py los arma como t{empresa}_...)."""
    tid = getattr(tenant, "id", None)
    return tid is not None and isinstance(code, str) and code.startswith(f"t{tid}_")


def required_roles(tenant) -> tuple[str, ...]:
    """Roles que exigen 2FA en esta empresa. Un rol creado a partir de uno que la exige la hereda al crearse (queda en
    la lista y el admin la ve marcada); quitarsela despues es aflojar la politica (pide la contrasena)."""
    raw = (getattr(tenant, "settings", None) or {}).get("twofa_roles")
    if raw is None:
        return DEFAULT_ROLES
    ok = set(role_choices())
    return tuple(r for r in raw if r in ok or _own_custom(tenant, r))


def grace_days(tenant) -> int:
    raw = (getattr(tenant, "settings", None) or {}).get("twofa_grace_days", DEFAULT_GRACE_DAYS)
    try:
        return max(0, min(MAX_GRACE_DAYS, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_GRACE_DAYS


def is_required(tenant, role: str) -> bool:
    return role in ALWAYS_ROLES or role in required_roles(tenant)


def state(user, tenant, role: str, now: datetime | None = None) -> dict:
    """{required, deadline, blocked} para /auth/me y la compuerta. deadline = None mientras la gracia no arranca (y
    siempre para soporte, que no tiene gracia)."""
    req = is_required(tenant, role)
    if not req or user.totp_enabled:
        return {"required": req, "deadline": None, "blocked": False}
    if role in ALWAYS_ROLES:  # sin gracia (sin plazo que mostrar): antes de ver cualquier dato de la empresa
        return {"required": True, "deadline": None, "blocked": True}
    since = _aware(user.twofa_required_since)
    now = now or datetime.now(UTC)
    deadline = since + timedelta(days=grace_days(tenant)) if since else None
    return {"required": True, "deadline": deadline, "blocked": bool(deadline and deadline <= now)}


def start_grace(user, tenant, role: str, now: datetime) -> None:
    """Al emitir una sesion (login o refresh): la primera vez que su rol le exige 2FA empieza a correr la gracia.
    No se reinicia: cambiar de empresa o de rol no le regala otra semana. Soporte no tiene gracia: no la arranca."""
    if role not in ALWAYS_ROLES and is_required(tenant, role) and not user.totp_enabled and user.twofa_required_since is None:
        user.twofa_required_since = now


def enforce(user, tenant, role: str) -> None:
    if state(user, tenant, role)["blocked"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, BLOCKED_MSG, headers={HEADER: "required"})
