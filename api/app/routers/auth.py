"""Login (email + password + 2FA opcional), refresh rotativo por cookie httpOnly, logout, /me, TOTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import ROLE_PERMISSIONS, Principal, get_principal
from ..core.ratelimit import login_limiter
from ..core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    new_totp_secret,
    password_is_strong,
    refresh_expiry,
    totp_uri,
    verify_password,
    verify_totp,
)
from ..models import AuditLog, RefreshToken, Tenant, TenantUser, User
from ..schemas.core import (
    LoginIn,
    MembershipOut,
    MeOut,
    PasswordChangeIn,
    TokenOut,
    TotpSetupOut,
    TotpVerifyIn,
)

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "crimson_refresh"
MAX_FAILED = 6
LOCK_MINUTES = 15


def _set_cookie(resp: Response, raw: str) -> None:
    resp.set_cookie(
        COOKIE,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_days * 86400,
        path="/",
        domain=settings.cookie_domain,
    )


def _issue(db: Session, request: Request, resp: Response, user: User, membership: TenantUser) -> TokenOut:
    raw, h = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            tenant_id=membership.tenant_id,
            token_hash=h,
            expires_at=refresh_expiry(),
            user_agent=(request.headers.get("user-agent") or "")[:300],
            ip=request.client.host if request.client else None,
        )
    )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    _set_cookie(resp, raw)
    tenant = membership.tenant
    return TokenOut(
        access_token=create_access_token(user.id, tenant.id, membership.role_code),
        expires_in=settings.access_token_minutes * 60,
        tenant=tenant,
        user=user,
        role=membership.role_code,
    )


def client_ip(request: Request) -> str:
    """IP real detras del proxy de Railway/nginx (uvicorn corre con --proxy-headers)."""
    return request.client.host if request.client else "?"


def _pick_membership(db: Session, user: User, slug: str | None) -> TenantUser:
    q = select(TenantUser).join(Tenant).where(TenantUser.user_id == user.id, TenantUser.active, Tenant.active)
    if slug:
        q = q.where(Tenant.slug == slug)
    m = db.scalars(q.order_by(TenantUser.id)).first()
    if not m:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "El usuario no pertenece a ninguna empresa activa")
    return m


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    login_limiter.hit(client_ip(request))
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales invalidas")
    if not user or not user.active:
        raise generic
    now = datetime.now(UTC)
    locked = user.locked_until.replace(tzinfo=UTC) if user.locked_until and user.locked_until.tzinfo is None else user.locked_until
    if locked and locked > now:
        raise HTTPException(status.HTTP_423_LOCKED, "Cuenta bloqueada temporalmente por intentos fallidos")
    if not verify_password(data.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        db.commit()
        raise generic
    if user.totp_enabled:
        if not data.totp_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Se requiere codigo 2FA", headers={"X-2FA": "required"})
        if not verify_totp(user.totp_secret or "", data.totp_code):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Codigo 2FA invalido")
    user.failed_logins, user.locked_until = 0, None
    m = _pick_membership(db, user, data.tenant_slug)
    db.add(
        AuditLog(
            tenant_id=m.tenant_id, user_id=user.id, at=now, ip=request.client.host if request.client else None, action="login", entity="user", entity_id=user.id
        )
    )
    return _issue(db, request, resp, user, m)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, resp: Response, tenant_slug: str | None = None, db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sin sesion")
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    now = datetime.now(UTC)
    if not rt or rt.revoked_at or rt.expires_at.replace(tzinfo=UTC) < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesion invalida")
    rt.revoked_at = now  # rotacion: el token usado muere
    user = db.get(User, rt.user_id)
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Cuenta inactiva")
    m = (
        _pick_membership(db, user, tenant_slug)
        if tenant_slug
        else db.scalar(select(TenantUser).where(TenantUser.user_id == user.id, TenantUser.tenant_id == rt.tenant_id, TenantUser.active))
    )
    if not m:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sin acceso a la empresa")
    return _issue(db, request, resp, user, m)


@router.post("/logout", status_code=204)
def logout(request: Request, resp: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE)
    if raw:
        rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
        if rt and not rt.revoked_at:
            rt.revoked_at = datetime.now(UTC)
            db.commit()
    resp.delete_cookie(COOKIE, path="/", domain=settings.cookie_domain)


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, resp: Response, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Cierra la sesion en todos los dispositivos: revoca todos los refresh tokens del usuario.
    Los access tokens ya emitidos vencen solos en pocos minutos."""
    now = datetime.now(UTC)
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == p.user.id, RefreshToken.revoked_at.is_(None))):
        rt.revoked_at = now
    db.add(AuditLog(tenant_id=p.tenant.id, user_id=p.user.id, at=now, ip=request.client.host if request.client else None, action="logout_all", entity="user", entity_id=p.user.id))
    db.commit()
    resp.delete_cookie(COOKIE, path="/", domain=settings.cookie_domain)


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ms = db.scalars(select(TenantUser).join(Tenant).where(TenantUser.user_id == p.user.id, TenantUser.active, Tenant.active)).all()
    return MeOut(
        user=p.user,
        tenant=p.tenant,
        role=p.role,
        permissions=p.permissions,
        memberships=[MembershipOut(tenant=m.tenant, role=m.role_code) for m in ms],
    )


@router.post("/password", status_code=204)
def change_password(data: PasswordChangeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not verify_password(data.current_password, p.user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contrasena actual incorrecta")
    if not password_is_strong(data.new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Minimo 10 caracteres con letras y numeros")
    p.user.password_hash = hash_password(data.new_password)
    db.commit()


@router.post("/2fa/setup", response_model=TotpSetupOut)
def totp_setup(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    secret = new_totp_secret()
    p.user.totp_secret = secret
    p.user.totp_enabled = False
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_uri=totp_uri(secret, p.user.email))


@router.post("/2fa/verify", status_code=204)
def totp_verify(data: TotpVerifyIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.user.totp_secret or not verify_totp(p.user.totp_secret, data.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Codigo invalido")
    p.user.totp_enabled = True
    db.commit()


@router.get("/roles")
def roles(_: Principal = Depends(get_principal)):
    return {"roles": list(ROLE_PERMISSIONS.keys()), "matrix": ROLE_PERMISSIONS}
