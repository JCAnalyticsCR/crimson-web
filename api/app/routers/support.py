"""Acceso de soporte auditado: el administrador concede acceso de solo lectura por horas a un correo.

- Si la persona no tiene cuenta, recibe una invitacion (rol "soporte"); si ya la tiene, se le agrega la membresia.
- Al vencer o revocarse, la membresia se desactiva y get_principal rechaza cualquier token vigente.
- Cada request de un usuario de soporte queda en la bitacora (ver core/deps.py).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import ROLE_PERMISSIONS, Principal, require
from ..core.security import hash_token
from ..models import AuditLog, Invitation, Role, SupportGrant, TenantUser, User
from ..services.documents import audit

router = APIRouter(prefix="/settings/support", tags=["ajustes"])


def ensure_support_role(db: Session) -> None:
    if not db.scalar(select(Role).where(Role.code == "soporte")):
        db.add(Role(code="soporte", name="Soporte (temporal)", permissions=ROLE_PERMISSIONS["soporte"]))
        db.flush()


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def expire_grants(db: Session, tenant_id: int | None = None) -> int:
    """Desactiva membresias de soporte sin concesion vigente. Idempotente; lo llama el worker y cada listado."""
    now = datetime.now(UTC)
    q = select(TenantUser).where(TenantUser.role_code == "soporte", TenantUser.active)
    if tenant_id:
        q = q.where(TenantUser.tenant_id == tenant_id)
    n = 0
    for m in db.scalars(q):
        user = db.get(User, m.user_id)
        live = db.scalar(
            select(SupportGrant).where(
                SupportGrant.tenant_id == m.tenant_id, SupportGrant.email == user.email, SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > now
            )
        )
        if not live:
            m.active = False
            n += 1
    return n


def _out(g: SupportGrant, db: Session) -> dict:
    now = datetime.now(UTC)
    status = "revocado" if g.revoked_at else ("vencido" if aware(g.expires_at) <= now else "activo")
    user = db.scalar(select(User).where(User.email == g.email))
    actions = 0
    if user:
        actions = len(db.scalars(select(AuditLog.id).where(AuditLog.tenant_id == g.tenant_id, AuditLog.user_id == user.id, AuditLog.at >= g.created_at)).all())
    return {
        "id": g.id,
        "email": g.email,
        "reason": g.reason,
        "expires_at": g.expires_at,
        "revoked_at": g.revoked_at,
        "created_at": g.created_at,
        "status": status,
        "actions": actions,
    }


class GrantIn(BaseModel):
    email: EmailStr
    hours: int = Field(24, ge=1, le=168)
    reason: str = Field(min_length=5, max_length=300)


@router.get("")
def grants(p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    expire_grants(db, p.tenant.id)
    db.commit()
    return [_out(g, db) for g in db.scalars(select(SupportGrant).where(SupportGrant.tenant_id == p.tenant.id).order_by(SupportGrant.id.desc()).limit(50))]


@router.post("", status_code=201)
def grant(data: GrantIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    email = str(data.email).lower()
    if email == p.user.email:
        raise HTTPException(422, "No puede concederse soporte a sí mismo")
    ensure_support_role(db)
    user = db.scalar(select(User).where(User.email == email))
    if user:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == user.id))
        if m and m.role_code != "soporte":
            raise HTTPException(409, "Esa persona ya es usuaria de la empresa con otro rol")
    g = SupportGrant(tenant_id=p.tenant.id, email=email, reason=data.reason, granted_by=p.user.id, expires_at=datetime.now(UTC) + timedelta(hours=data.hours))
    db.add(g)
    link = None
    if user:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == user.id))
        if m:
            m.active = True
        else:
            db.add(TenantUser(tenant_id=p.tenant.id, user_id=user.id, role_code="soporte"))
    else:
        raw = secrets.token_urlsafe(32)
        db.add(Invitation(tenant_id=p.tenant.id, email=email, role_code="soporte", token_hash=hash_token(raw), expires_at=g.expires_at, invited_by=p.user.id))
        link = f"{settings.public_base_url.rstrip('/')}/invitacion/{raw}"
    db.flush()
    audit(db, p.tenant.id, p.user.id, "support_grant", "support_grant", g.id, {"email": email, "hours": data.hours, "reason": data.reason}, ip=p.ip)
    db.commit()
    return {**_out(g, db), "invite_link": link}


@router.post("/{gid}/revoke")
def revoke(gid: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    g = db.get(SupportGrant, gid)
    if not g or g.tenant_id != p.tenant.id:
        raise HTTPException(404, "Concesion no encontrada")
    g.revoked_at = datetime.now(UTC)
    db.flush()
    expire_grants(db, p.tenant.id)
    audit(db, p.tenant.id, p.user.id, "support_revoke", "support_grant", g.id, ip=p.ip)
    db.commit()
    return _out(g, db)


@router.get("/{gid}/log")
def grant_log(gid: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    g = db.get(SupportGrant, gid)
    if not g or g.tenant_id != p.tenant.id:
        raise HTTPException(404, "Concesion no encontrada")
    user = db.scalar(select(User).where(User.email == g.email))
    if not user:
        return []
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == g.tenant_id, AuditLog.user_id == user.id, AuditLog.at >= g.created_at)
        .order_by(AuditLog.id.desc())
        .limit(300)
    )
    return [{"at": r.at, "action": r.action, "entity": r.entity, "entity_id": r.entity_id, "ip": r.ip, "diff": r.diff} for r in rows]
