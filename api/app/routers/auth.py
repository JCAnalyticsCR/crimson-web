"""Login (email + password + 2FA opcional), refresh rotativo por cookie httpOnly, logout, /me, TOTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import CLIENT_ROLES, ROLE_PERMISSIONS, Principal, effective_permissions, get_principal, get_session_principal
from ..core.ratelimit import login_limiter, pwreset_email_limiter, pwreset_global_limiter, pwreset_ip_limiter, token_limiter
from ..core.security import (
    create_access_token,
    hash_password,
    hash_recovery,
    hash_token,
    new_recovery_codes,
    new_refresh_token,
    new_totp_secret,
    password_is_strong,
    refresh_expiry,
    totp_uri,
    use_recovery_code,
    verify_password,
    verify_totp,
)
from ..models import AuditLog, RefreshToken, Tenant, TenantUser, User
from ..schemas.core import (
    LoginIn,
    MembershipOut,
    MeOut,
    PasswordChangeIn,
    PasswordOnlyIn,
    RecoveryCodesOut,
    ResetConfirmIn,
    ResetRequestIn,
    ResetTokenIn,
    TokenOut,
    TotpSetupOut,
    TotpVerifyIn,
)
from ..services.accounts import (
    SELF_LINK_MINUTES,
    aware,
    end_sessions,
    fecha_cr,
    find_reset,
    invalidate_resets,
    issue_reset,
    mask_email,
    notify_security,
    reset_email_html,
    set_password,
    temp_password_alive,
)
from ..services.documents import audit
from ..services.mail import mail_configured, queue_email

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
        access_token=create_access_token(user.id, tenant.id, membership.role_code, sv=int(user.session_version or 0)),
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
    # Nada se modifica antes de que pasen todas las validaciones (salvo contar el intento fallido): una mutacion
    # previa a un raise quedaria pendiente en la sesion de BD y se guardaria en el siguiente commit.
    login_limiter.hit(client_ip(request))
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales invalidas")
    if not user or not user.active:
        raise generic
    now = datetime.now(UTC)
    locked = aware(user.locked_until)
    if locked and locked > now:
        raise HTTPException(status.HTTP_423_LOCKED, "Cuenta bloqueada temporalmente por intentos fallidos")
    if not verify_password(data.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        db.commit()
        raise generic
    # Compuerta de contrasena temporal (Ajustes -> Usuarios -> Restablecer): antes del 2FA, asi el codigo de la app
    # se escribe una sola vez al final. La contrasena era correcta: estas respuestas no suman intentos fallidos.
    if user.must_change_password:
        exp = aware(user.temp_password_expires_at)
        if exp and exp < now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "La contraseña temporal venció. Pedile al administrador que te genere otra.")
        if not temp_password_alive(db, user):
            # quien la genero ya no podria generarla (lo desactivaron, le bajaron el rol o a esta cuenta la ascendieron)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "La contraseña temporal ya no es válida. Pedile a un administrador que te genere otra.")
        if not data.new_password:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Creá tu contraseña nueva para continuar", headers={"X-Password-Change": "required"})
        if not password_is_strong(data.new_password):
            raise HTTPException(422, "Mínimo 10 caracteres con letras y números")
        if data.new_password == data.password:
            raise HTTPException(422, "La contraseña nueva no puede ser la temporal")
    quedan = None
    if user.totp_enabled:
        if not data.totp_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Se requiere codigo 2FA", headers={"X-2FA": "required"})
        if not verify_totp(user.totp_secret or "", data.totp_code):
            # Sin el telefono a mano queda el codigo de recuperacion; se consume recien cuando el login completo pasa.
            quedan = use_recovery_code(user.totp_recovery, data.totp_code)
            if quedan is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Codigo 2FA invalido")
    m = _pick_membership(db, user, data.tenant_slug)
    ip = request.client.host if request.client else None
    if quedan is not None:
        user.totp_recovery = quedan
    user.failed_logins, user.locked_until = 0, None
    if user.must_change_password:
        set_password(user, data.new_password)
        audit(db, m.tenant_id, user.id, "password_temp_changed", "user", user.id, ip=ip)
    db.add(AuditLog(tenant_id=m.tenant_id, user_id=user.id, at=now, ip=ip, action="login", entity="user", entity_id=user.id))
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
    if user.must_change_password:  # defensa en profundidad: restablecer ya revoco sus refresh tokens
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Debe crear una contraseña nueva")
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
def logout_all(request: Request, resp: Response, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Cierra la sesion en todos los dispositivos: revoca todos los refresh tokens del usuario.
    Los access tokens ya emitidos vencen solos en pocos minutos."""
    now = datetime.now(UTC)
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == p.user.id, RefreshToken.revoked_at.is_(None))):
        rt.revoked_at = now
    db.add(
        AuditLog(
            tenant_id=p.tenant.id,
            user_id=p.user.id,
            at=now,
            ip=request.client.host if request.client else None,
            action="logout_all",
            entity="user",
            entity_id=p.user.id,
        )
    )
    db.commit()
    resp.delete_cookie(COOKIE, path="/", domain=settings.cookie_domain)


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    ms = db.scalars(select(TenantUser).join(Tenant).where(TenantUser.user_id == p.user.id, TenantUser.active, Tenant.active)).all()
    ally = None
    if p.ally_company_id:
        from ..models import AllyCompany
        from .ally_portal import ganancias_disponibles

        c = db.get(AllyCompany, p.ally_company_id)
        if c is not None and c.tenant_id == p.tenant.id:
            ally = {
                "id": c.id,
                "name": c.name,
                "state": c.state,
                "suspended_reason": c.suspended_reason if c.suspended_at else None,
                "features": {"ganancias": ganancias_disponibles()},
            }
    return MeOut(
        user=p.user,
        tenant=p.tenant,
        role=p.role,
        permissions=p.permissions,
        memberships=[MembershipOut(tenant=m.tenant, role=m.role_code) for m in ms],
        ally_company=ally,
    )


@router.post("/password", status_code=204)
def change_password(data: PasswordChangeIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    if not verify_password(data.current_password, p.user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contrasena actual incorrecta")
    if not password_is_strong(data.new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Minimo 10 caracteres con letras y numeros")
    p.user.password_hash = hash_password(data.new_password)
    p.user.password_changed_at = datetime.now(UTC)
    p.user.must_change_password = False
    p.user.temp_password_expires_at = None
    db.commit()


@router.post("/2fa/setup", response_model=TotpSetupOut)
def totp_setup(p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    secret = new_totp_secret()
    p.user.totp_secret = secret
    p.user.totp_enabled = False
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_uri=totp_uri(secret, p.user.email))


@router.post("/2fa/verify", response_model=RecoveryCodesOut)
def totp_verify(data: TotpVerifyIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Activa el 2FA y entrega los codigos de recuperacion. Se muestran UNA vez: despues solo viven hasheados."""
    if not p.user.totp_secret or not verify_totp(p.user.totp_secret, data.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Codigo invalido")
    p.user.totp_enabled = True
    codigos = new_recovery_codes()
    p.user.totp_recovery = [hash_recovery(c) for c in codigos]
    audit(db, p.tenant.id, p.user.id, "2fa_on", "user", p.user.id, ip=p.ip)
    db.commit()
    return RecoveryCodesOut(codes=codigos)


@router.post("/2fa/recovery-codes", response_model=RecoveryCodesOut)
def totp_recovery_codes(data: PasswordOnlyIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Genera codigos nuevos (los anteriores dejan de servir). Pide la contrasena: si alguien te deja
    la sesion abierta, no deberia poder fabricarse una llave de entrada permanente."""
    if not p.user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "El 2FA no esta activo")
    if not verify_password(data.password, p.user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contrasena incorrecta")
    codigos = new_recovery_codes()
    p.user.totp_recovery = [hash_recovery(c) for c in codigos]
    audit(db, p.tenant.id, p.user.id, "2fa_recovery", "user", p.user.id, ip=p.ip)
    db.commit()
    return RecoveryCodesOut(codes=codigos)


@router.post("/2fa/disable", status_code=204)
def totp_disable(data: PasswordOnlyIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Apaga el 2FA con la contrasena. Sin esto, perder el telefono significaba entrar a la base de datos."""
    if not verify_password(data.password, p.user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contrasena incorrecta")
    p.user.totp_enabled = False
    p.user.totp_secret = None
    p.user.totp_recovery = []
    audit(db, p.tenant.id, p.user.id, "2fa_off", "user", p.user.id, ip=p.ip)
    db.commit()


# ---------- Restablecer contrasena con enlace (publico; nunca 401: el portal cerraria la sesion) ----------
@router.post("/password-reset/check")
def password_reset_check(data: ResetTokenIn, request: Request, db: Session = Depends(get_db)):
    token_limiter.hit(f"reset:{client_ip(request)}")
    found = find_reset(db, data.token)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El enlace venció o ya se usó")
    pr, user = found
    return {"email": mask_email(user.email), "expires_at": pr.expires_at}


@router.post("/password-reset/confirm")
def password_reset_confirm(data: ResetConfirmIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    token_limiter.hit(f"reset:{ip}")
    found = find_reset(db, data.token)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El enlace venció o ya se usó")
    pr, user = found
    if not password_is_strong(data.new_password):
        raise HTTPException(422, "Mínimo 10 caracteres con letras y números")
    if verify_password(data.new_password, user.password_hash):
        raise HTTPException(422, "Usá una contraseña distinta a la anterior")
    now = datetime.now(UTC)
    set_password(user, data.new_password)
    pr.used_at = now
    invalidate_resets(db, user.id)  # los demas enlaces vivos tambien mueren
    end_sessions(db, user)  # por seguridad se cierran todas sus sesiones
    audit(db, pr.tenant_id, user.id, "password_reset_done", "user", user.id, {"origen": pr.kind}, ip=ip)
    # El 2FA se mantiene, salvo que el admin haya pedido quitarlo (perdio el telefono): se quita aqui, al usar el
    # enlace, y no al pedirlo (mientras tanto la contrasena vieja seguia sirviendo).
    sin_2fa = bool(pr.reset_2fa and user.totp_enabled)
    if sin_2fa:
        user.totp_enabled, user.totp_secret, user.totp_recovery = False, None, []
        audit(db, pr.tenant_id, user.id, "2fa_reset", "user", user.id, {"origen": "enlace", "pedido_por": pr.created_by}, ip=ip)
    tenant = db.get(Tenant, pr.tenant_id)
    if tenant:
        extra = " También se quitó la verificación en dos pasos: activala de nuevo en Mi cuenta." if sin_2fa else ""
        notify_security(
            db,
            tenant,
            user,
            "Tu contraseña de Crimson cambió",
            f"<p>Tu contraseña de Crimson se cambió el {fecha_cr(now)}.{extra} Si no fuiste vos, avisale a tu administrador.</p>",
        )
    db.commit()
    return {"ok": True, "email": user.email}


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(data: ResetRequestIn, request: Request, db: Session = Depends(get_db)):
    """Olvide mi contrasena. La respuesta es SIEMPRE la misma (exista o no la cuenta) y nunca trae el enlace.
    Sin correo activo no se hace nada: el enlace solo sirve si llega por correo. Riesgo residual aceptado: la
    diferencia de tiempo entre existe y no existe (el envio a Resend tarda), mitigada por los limites."""
    ip = client_ip(request)
    pwreset_ip_limiter.hit(ip)
    pwreset_global_limiter.hit("global")
    ok = {"ok": True}
    email = str(data.email).strip().lower()
    try:
        pwreset_email_limiter.hit(hash_token(email))
    except HTTPException:
        return ok  # pasado el limite de ese correo se ignora en silencio (no revela nada)
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.active or not mail_configured():
        return ok
    m = db.scalars(select(TenantUser).join(Tenant).where(TenantUser.user_id == user.id, TenantUser.active, Tenant.active).order_by(TenantUser.id)).first()
    if not m:
        return ok
    invalidate_resets(db, user.id)
    raw, pr = issue_reset(db, user, m.tenant_id, "auto", None, timedelta(minutes=SELF_LINK_MINUTES), ip)
    link = f"{settings.public_base_url.rstrip('/')}/restablecer/{raw}"
    queue_email(
        db,
        m.tenant,
        user.email,
        f"Creá tu nueva contraseña · {m.tenant.name}",
        reset_email_html(m.tenant, user.full_name, link, "1 hora"),
        "password_reset",
        pr.id,
    )
    audit(db, m.tenant_id, user.id, "password_reset_request", "user", user.id, ip=ip)
    db.commit()
    return ok


@router.get("/roles")
def roles(p: Principal = Depends(get_principal)):
    # matriz de roles internos (vista previa del admin) con lo que la empresa habilito (Ajustes -> Quien ve costos);
    # los de cliente tienen su propia interfaz. Aliado y tecnico de aliado salen aqui: aparecen en "Ver como".
    m = {k: effective_permissions(p.tenant, k, v) for k, v in ROLE_PERMISSIONS.items() if k not in CLIENT_ROLES}
    return {"roles": list(m.keys()), "matrix": m}
