"""Dependencias: usuario actual, tenant activo y permisos por modulo x accion."""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Role, Tenant, TenantUser, User
from .db import get_db
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

# Roles predefinidos (plan 5.5). "*" = todo.
ROLE_PERMISSIONS: dict[str, dict[str, list[str]]] = {
    "admin": {"*": ["*"]},
    # Vendedor: cotiza, factura, cobra y atiende clientes. No anula, no ve contabilidad ni reportes de la empresa;
    # su dashboard se limita a lo que el mismo creo (ver /dashboard).
    "ventas": {
        "dashboard": ["ver"],
        "crm": ["ver", "crear", "editar"],
        "catalog": ["ver"],
        "sales": ["ver", "crear", "editar", "enviar"],
        "payments": ["ver", "crear"],
        "events": ["ver", "crear", "editar", "checkin"],
    },
    "caja": {
        "dashboard": ["ver"],
        "crm": ["ver", "crear"],
        "catalog": ["ver"],
        "sales": ["ver", "crear"],
        "payments": ["ver", "crear", "editar"],
        "events": ["ver", "checkin"],
        "reports": ["ver", "exportar"],
    },
    "inventario": {"dashboard": ["ver"], "catalog": ["ver", "crear", "editar"], "inventory": ["ver", "crear", "editar", "exportar"]},
    "contabilidad": {
        "dashboard": ["ver"],
        "sales": ["ver", "exportar"],
        "payments": ["ver", "exportar"],
        "accounting": ["ver", "crear", "editar", "exportar"],
        "payroll": ["ver", "crear", "editar", "aprobar", "configurar"],
        "reports": ["ver", "exportar"],
    },
    "lectura": {"dashboard": ["ver"], "crm": ["ver"], "catalog": ["ver"], "sales": ["ver"], "payments": ["ver"], "events": ["ver"], "reports": ["ver"]},
    # Acceso temporal concedido por el admin (Ajustes -> Soporte): solo lectura, cada request queda auditado
    "soporte": {
        "dashboard": ["ver"],
        "crm": ["ver"],
        "catalog": ["ver"],
        "sales": ["ver"],
        "payments": ["ver"],
        "inventory": ["ver"],
        "accounting": ["ver"],
        "events": ["ver"],
        "reports": ["ver"],
        "settings": ["ver"],
    },
}
ASSIGNABLE_ROLES = [r for r in ROLE_PERMISSIONS if r != "soporte"]


@dataclass
class Principal:
    user: User
    tenant: Tenant
    role: str
    permissions: dict[str, list[str]]
    ip: str | None = None

    def can(self, module: str, action: str) -> bool:
        p = self.permissions
        if "*" in p:
            return True
        acts = p.get(module, [])
        return "*" in acts or action in acts


def _role_permissions(db: Session, code: str) -> dict:
    """Roles predefinidos: manda el codigo (asi los permisos nuevos llegan a empresas ya creadas).
    Roles personalizados: los permisos guardados en la tabla role."""
    if code in ROLE_PERMISSIONS:
        return ROLE_PERMISSIONS[code]
    r = db.scalar(select(Role).where(Role.code == code))
    return r.permissions if r and r.permissions else {}


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesion expirada") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalido") from e
    if payload.get("typ") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalido")
    user = db.get(User, int(payload["sub"]))
    tenant = db.get(Tenant, int(payload["tid"]))
    if not user or not user.active or not tenant or not tenant.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Cuenta inactiva")
    m = db.scalar(select(TenantUser).where(TenantUser.user_id == user.id, TenantUser.tenant_id == tenant.id, TenantUser.active))
    if not m:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sin acceso a esta empresa")
    ip = request.client.host if request.client else None
    if m.role_code == "soporte":
        _support_gate(db, user, tenant, request, ip)
    return Principal(user=user, tenant=tenant, role=m.role_code, permissions=_role_permissions(db, m.role_code), ip=ip)


def _support_gate(db: Session, user: User, tenant: Tenant, request: Request, ip: str | None) -> None:
    """Soporte: exige concesion vigente y deja constancia de cada request (tambien lecturas)."""
    from datetime import UTC, datetime

    from ..models import AuditLog, SupportGrant

    now = datetime.now(UTC)
    live = db.scalar(
        select(SupportGrant).where(
            SupportGrant.tenant_id == tenant.id, SupportGrant.email == user.email, SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > now
        )
    )
    if not live:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "El acceso de soporte vencio o fue revocado")
    db.add(AuditLog(tenant_id=tenant.id, user_id=user.id, at=now, ip=ip, action=f"soporte {request.method}", entity=request.url.path[:40], entity_id=live.id))
    db.commit()


def require(module: str, action: str):
    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.can(module, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Sin permiso: {module}.{action}")
        return p

    return _dep
