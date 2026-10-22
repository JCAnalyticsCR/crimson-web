"""Login (email + password + 2FA), refresh rotativo por cookie httpOnly, logout, /me, TOTP.

Plan 1.10: el limite del login cuenta solo intentos fallidos (por IP, por cuenta + IP y por equipo, con espera
progresiva, y por cuenta desde todas las IPs, que hace esperar a lo sumo un minuto a quien no es de confianza para la
cuenta: un tercero no la bloquea ni deja afuera a la duena, y la respuesta es la misma exista o no la cuenta); el
refresh solo se acepta desde el portal; el 2FA activo no se cambia ni se apaga (ni se fabrican codigos de
recuperacion) sin contrasena y codigo, cada contrasena que se compara con la sesion abierta cuenta en reauth_limiter,
un codigo no se repite (ni con dos pedidos a la vez) y el 2FA es obligatorio para los roles que la empresa defina
(core/twofa.py)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import twofa
from ..core.clientip import ip_key, ip_propia, source
from ..core.config import settings
from ..core.cors import origin_of, split_origins
from ..core.db import get_db
from ..core.deps import CLIENT_ROLES, ROLE_PERMISSIONS, Principal, effective_permissions, get_principal, get_session_principal
from ..core.ratelimit import LoginAttempt, login_limiter, pwreset_email_limiter, pwreset_global_limiter, pwreset_ip_limiter, reauth_limiter, token_limiter
from ..core.reauth import actor_key, require_reauth
from ..core.security import (
    create_access_token,
    hash_password,
    hash_recovery,
    hash_token,
    new_recovery_codes,
    new_refresh_token,
    new_totp_secret,
    password_is_strong,
    read_purpose_token,
    refresh_expiry,
    sign_purpose_token,
    totp_step,
    totp_uri,
    use_recovery_code,
    verify_password,
    verify_totp,
)
from ..models import AuditLog, RefreshToken, Role, Tenant, TenantUser, User
from ..schemas.core import (
    LoginIn,
    MembershipOut,
    MeOut,
    PasswordChangeIn,
    RecoveryCodesIn,
    RecoveryCodesOut,
    ResetConfirmIn,
    ResetRequestIn,
    ResetTokenIn,
    TokenOut,
    TotpDisableIn,
    TotpSetupIn,
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
# Aceptar una invitacion con una cuenta existente (routers/users_admin.py): 6 contrasenas equivocadas bloquean ESE
# paso 15 min (locked_until). El login ya no usa ese bloqueo: un tercero no deja a nadie afuera (core/ratelimit.py).
MAX_FAILED = 6
LOCK_MINUTES = 15
# Cookie de equipo: la recibe el navegador que entro bien (o que uso un enlace de restablecer, que prueba el correo).
# No es una sesion ni sirve para entrar: solo exime de la espera por cuenta (login_limiter, por todas las IPs) cuando
# un tercero la dispara. Firmada; vale para esa cuenta mientras no cambie la contrasena ni se cierren sus sesiones
# (session_version), y tiene su propio limite de fallidos (una robada no da intentos sin limite).
DEVICE_COOKIE = "crimson_device"
DEVICE_DAYS = 180


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


def _device_mark(user: User) -> dict:
    changed = aware(user.password_changed_at)
    return {"sub": str(user.id), "pv": int(changed.timestamp()) if changed else 0, "sv": int(user.session_version or 0)}


def _set_device(resp: Response, user: User) -> None:
    tok = sign_purpose_token("device", {**_device_mark(user), "n": secrets.token_urlsafe(12)}, DEVICE_DAYS)
    resp.set_cookie(
        DEVICE_COOKIE,
        tok,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=DEVICE_DAYS * 86400,
        path="/",
        domain=settings.cookie_domain,
    )


def _device(request: Request, user: User | None) -> str | None:
    """Id del equipo de confianza para ESTA cuenta (la cookie de equipo vigente), o None."""
    raw = request.cookies.get(DEVICE_COOKIE)
    if user is None or not raw or len(raw) > 2048:
        return None
    try:
        d = read_purpose_token("device", raw)
    except jwt.InvalidTokenError:
        return None
    if any(str(d.get(k)) != str(v) for k, v in _device_mark(user).items()):
        return None  # de otra cuenta, o de antes de cambiar la contrasena o de cerrar sus sesiones
    n = d.get("n")
    return n if isinstance(n, str) and n else None


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
    twofa.start_grace(user, membership.tenant, membership.role_code, user.last_login_at)
    db.commit()
    _set_cookie(resp, raw)
    _set_device(resp, user)
    tenant = membership.tenant
    return TokenOut(
        access_token=create_access_token(user.id, tenant.id, membership.role_code, sv=int(user.session_version or 0)),
        expires_in=settings.access_token_minutes * 60,
        tenant=tenant,
        user=user,
        role=membership.role_code,
    )


def client_ip(request: Request) -> str:
    """IP real del cliente: la cabecera firmada del nginx del portal o, sin ella, la que deja uvicorn (que solo cree en
    X-Forwarded-For de redes internas). Ver core/clientip.py."""
    return request.client.host if request.client else "?"


@router.get("/ip-check")
def ip_check(request: Request, response: Response):
    """Sonda del plan 1.10 (sin sesion; en tienda.* nginx no deja pasar /api/auth). Devuelve la IP que el API le
    atribuye a quien llama y que paso con las cabeceras del nginx del portal (core/clientip.py, source):
    - por el panel (https://panel.../api/auth/ip-check): "firmadas" y TU IP publica = todo bien. Con "firmadas" pero
      otra IP (100.64.x o la del edge), el nginx del portal no reconoce al edge (set_real_ip_from en portal/nginx.conf).
      "sin_llave" = el portal no tiene PROXY_SHARED_KEY; "rechazadas" = la llave no coincide entre portal y api (o
      falta en el api: /health dice "proxy_key"); "ninguna" = el edge del API quita las cabeceras propias.
    - directo al API con cabeceras inventadas (IP y llave): "rechazadas" = el edge las deja pasar y el API no les cree;
      la IP tiene que ser tu IP publica (si no, FORWARDED_ALLOW_IPS de api/Dockerfile no cubre al edge).
    Solo la IP de quien pregunta y una palabra: nada que no sepa ya."""
    response.headers["Cache-Control"] = "no-store"
    return {"ip": client_ip(request), "cabeceras": source(request)}


def _known_ip(db: Session, user: User, ip: str, now: datetime) -> bool:
    """Ademas de la cookie de equipo, no espera por los fallidos de la cuenta (todas las IPs) quien llega desde donde
    la duena tiene una sesion ABIERTA: un refresh token vigente (sin revocar ni vencer) emitido a esa IP despues de su
    ultimo cambio de contrasena. Cerrar sesiones o cambiar/restablecer la contrasena quita esas IPs (la de un atacante
    que llego a tener sesion deja de servir). El limite por cuenta + IP sigue aplicando. Quien llama solo la usa con la
    IP propia de la persona (clientip.ip_propia): sin la firma del portal todos llegan con la misma IP."""
    if not ip or ip == "?":
        return False
    q = select(RefreshToken.id).where(RefreshToken.user_id == user.id, RefreshToken.ip == ip, RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now)
    changed = aware(user.password_changed_at)
    if changed is not None:
        # refresh_token no guarda la emision: vence = emision + refresh_token_days (security.refresh_expiry)
        q = q.where(RefreshToken.expires_at > changed + timedelta(days=settings.refresh_token_days))
    return db.scalar(q.limit(1)) is not None


def _trusted(db: Session, request: Request, user: User | None, device: str | None, ip: str, now: datetime) -> bool:
    """De confianza para la cuenta: su cookie de equipo, o la IP propia de una de sus sesiones abiertas."""
    return device is not None or (user is not None and ip_propia(request) and _known_ip(db, user, ip, now))


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
    ip = client_ip(request)
    email = data.email.lower()
    login_limiter.check(email, ip)  # vistazo sin la base: solo frena a quien ya fallo (por IP o por cuenta + IP)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and not user.active:
        user = None  # como si no existiera: mismas respuestas y mismos limites
    now = datetime.now(UTC)
    device = _device(request, user)
    # El intento se reserva (por IP, cuenta + IP, equipo y cuenta desde todas las IPs) ANTES de probar la contrasena:
    # los logins simultaneos no leen todos la espera en 0. Muchos fallidos con la cuenta: quien no es de confianza
    # espera (a lo sumo un minuto) en vez de un bloqueo que un tercero renovaria sin fin; la misma respuesta exista o
    # no la cuenta.
    attempt = login_limiter.begin(email, ip, device, trusted=lambda: _trusted(db, request, user, device, ip, now))
    try:
        return _login_checked(data, request, resp, db, user, ip, now, attempt)
    finally:
        attempt.close()  # si no fallo ni acerto (falta el 2FA, contrasena temporal...), la reserva no cuenta


def _login_checked(data: LoginIn, request: Request, resp: Response, db: Session, user: User | None, ip: str, now: datetime, attempt: LoginAttempt) -> TokenOut:
    """El resto del login, con el intento ya reservado: attempt.fail() en cada fallo que cuenta (tambien adivinar el
    codigo 2FA; nada en la base: el limite no bloquea la cuenta y es igual para un correo que no existe)."""
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales invalidas")
    if user is None:
        attempt.fail()
        raise generic
    if not verify_password(data.password, user.password_hash):
        attempt.fail()
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
    step = None
    if user.totp_enabled:
        if not data.totp_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Se requiere codigo 2FA", headers={"X-2FA": "required"})
        # dos logins a la vez con el mismo codigo (relevo de phishing, alguien que lo vio): la fila queda tomada hasta
        # el commit y el segundo lee el paso (o el codigo de recuperacion) ya usado
        db.refresh(user, with_for_update=True)
        step = totp_step(user.totp_secret or "", data.totp_code, after=user.totp_last_step)
        if step is None:
            # Sin el telefono a mano queda el codigo de recuperacion; se consume recien cuando el login completo pasa.
            quedan = use_recovery_code(user.totp_recovery, data.totp_code)
            if quedan is None:
                if verify_totp(user.totp_secret or "", data.totp_code):
                    # el codigo era bueno pero ya se uso (doble envio o alguien lo vio): no suma como fallido
                    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ese código ya se usó. Esperá a que la app muestre el siguiente.")
                attempt.fail()  # adivinar el codigo tambien cuenta
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Codigo 2FA invalido")
    m = _pick_membership(db, user, data.tenant_slug)
    if quedan is not None:
        user.totp_recovery = quedan
    if step is not None:
        user.totp_last_step = step  # ese codigo ya no sirve otra vez
    attempt.success()
    # los fallidos de la cuenta (todas las IPs) NO se limpian al acertar: la duena entra sin devolverle margen al
    # atacante; se olvidan solos (o al cambiar la contrasena)
    if user.must_change_password:
        set_password(user, data.new_password)
        audit(db, m.tenant_id, user.id, "password_temp_changed", "user", user.id, ip=ip)
    db.add(AuditLog(tenant_id=m.tenant_id, user_id=user.id, at=now, ip=ip, action="login", entity="user", entity_id=user.id))
    return _issue(db, request, resp, user, m)


def _portal_only(request: Request) -> None:
    """La cookie de sesion solo se canjea desde el panel: con otro Origin o desde otro sitio (Sec-Fetch-Site) se
    rechaza aunque el navegador la mande. Sin Origin (herramientas, peticiones del mismo origen) pasa. Los origenes
    son los mismos que reciben CORS con cookies (core/cors.py): en produccion solo el de PUBLIC_BASE_URL (la tienda,
    del mismo sitio que el panel, no aunque figure en CORS_ORIGINS); nunca la landing."""
    if (request.headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origen no permitido")
    origin = request.headers.get("origin")
    if origin is None:
        return
    o = split_origins(list(settings.cors_origins), list(settings.landing_origins), settings.public_base_url, settings.is_prod)
    panel = origin_of(settings.public_base_url)
    allowed = {*o.credentials, *([panel] if panel and panel not in o.landing else [])}
    if origin.strip().rstrip("/").lower() not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origen no permitido")


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, resp: Response, tenant_slug: str | None = None, db: Session = Depends(get_db)):
    _portal_only(request)
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
    from ..core.deps import role_overrides, role_root
    from ..core.permisos import clean as perms_clean

    custom = None if p.role in ROLE_PERMISSIONS else db.scalar(select(Role).where(Role.code == p.role))
    customized = custom is not None or p.role in role_overrides(p.tenant)
    base = role_root(db, p.role) if customized else None
    return MeOut(
        user=p.user,
        tenant=p.tenant,
        role=p.role,
        # rol creado por el admin: su nombre; rol predefinido con permisos cambiados: el menu sale solo de los permisos
        role_label=custom.name if custom is not None else None,
        role_customized=customized,
        role_base=base,
        role_defaults=perms_clean(ROLE_PERMISSIONS[base]) if base in ROLE_PERMISSIONS and base != "admin" else None,
        permissions=p.permissions,
        memberships=[MembershipOut(tenant=m.tenant, role=m.role_code) for m in ms],
        ally_company=ally,
        twofa=twofa.state(p.user, p.tenant, p.role),
    )


@router.post("/password", status_code=204)
def change_password(data: PasswordChangeIn, response: Response, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    # cada intento cuenta (reauth_limiter): con una sesion robada no se prueba la contrasena sin limite
    require_reauth(p, data.current_password, "La contraseña actual no es correcta")
    if not password_is_strong(data.new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Minimo 10 caracteres con letras y numeros")
    p.user.password_hash = hash_password(data.new_password)
    p.user.password_changed_at = datetime.now(UTC)
    p.user.must_change_password = False
    p.user.temp_password_expires_at = None
    db.commit()
    login_limiter.forget_account(p.user.email)  # los fallidos eran contra la anterior
    _set_device(response, p.user)  # las cookies de equipo de antes del cambio ya no valen: este equipo sigue siendo suyo


@router.post("/2fa/setup", response_model=TotpSetupOut)
def totp_setup(data: TotpSetupIn | None = None, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Genera el secreto nuevo, que queda PENDIENTE hasta que /2fa/verify lo confirma: el 2FA que ya estaba sigue
    activo mientras tanto. La primera vez basta la sesion; con el 2FA activo (cambio de telefono) se exige la
    contrasena y el codigo actual (o uno de recuperacion), igual que para apagarlo (/2fa/disable): con la sesion y la
    contrasena solas nadie lo cambia ni lo apaga."""
    u = p.user
    if u.totp_enabled:
        data = data or TotpSetupIn()
        require_reauth(p, data.password, "Confirmá con tu contraseña")  # cada intento cuenta (tambien adivinar el codigo)
        db.refresh(u, with_for_update=True)  # el mismo codigo en dos pedidos a la vez: el segundo lo ve ya usado
        step = totp_step(u.totp_secret or "", data.code or "", after=u.totp_last_step)
        quedan = use_recovery_code(u.totp_recovery, data.code or "") if step is None else None
        if step is None and quedan is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "El código de tu app actual no es válido (o ya se usó)")
        if step is not None:
            u.totp_last_step = step
        if quedan is not None:
            u.totp_recovery = quedan
    secret = new_totp_secret()
    u.totp_pending_secret = secret
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_uri=totp_uri(secret, u.email))


@router.post("/2fa/verify", response_model=RecoveryCodesOut)
def totp_verify(data: TotpVerifyIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Activa (o cambia) el 2FA con el codigo del secreto pendiente y entrega los codigos de recuperacion. Se muestran
    UNA vez: despues solo viven hasheados. Avisa por correo: si no fue la persona, se entera. Cada intento cuenta
    (reauth_limiter): el codigo del secreto pendiente no se adivina sin limite."""
    reauth_limiter.hit(actor_key(p))
    u = p.user
    pending = u.totp_pending_secret
    # compatibilidad: un setup de antes del despliegue dejo el secreto en totp_secret con el 2FA apagado
    secret = pending or (u.totp_secret if not u.totp_enabled else None)
    step = totp_step(secret or "", data.code, after=None if pending else u.totp_last_step)
    if not secret or step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Codigo invalido")
    cambio = bool(u.totp_enabled)
    u.totp_secret, u.totp_enabled, u.totp_pending_secret, u.totp_last_step = secret, True, None, step
    codigos = new_recovery_codes()
    u.totp_recovery = [hash_recovery(c) for c in codigos]
    audit(db, p.tenant.id, u.id, "2fa_change" if cambio else "2fa_on", "user", u.id, ip=p.ip)
    que = "cambió a otro teléfono o app" if cambio else "se activó"
    notify_security(
        db,
        p.tenant,
        u,
        "Cambió tu verificación en dos pasos",
        f"<p>La verificación en dos pasos de tu cuenta de Crimson {que} el {fecha_cr(datetime.now(UTC))}. "
        "Los códigos de recuperación anteriores ya no sirven. Si no fuiste vos, avisale a tu administrador.</p>",
    )
    db.commit()
    return RecoveryCodesOut(codes=codigos)


@router.post("/2fa/recovery-codes", response_model=RecoveryCodesOut)
def totp_recovery_codes(data: RecoveryCodesIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Genera codigos nuevos (los anteriores dejan de servir). Pide la contrasena (cada intento cuenta) y el codigo
    ACTUAL de la app, nunca uno de recuperacion: con la sesion y la contrasena solas nadie se fabrica una llave de
    entrada ni la usa despues en "Cambiar de telefono" para quedarse con el 2FA. Sin la app se usa "Cambiar de
    telefono" con un codigo de recuperacion (al confirmar entrega codigos nuevos). Avisa por correo."""
    u = p.user
    if not u.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "El 2FA no esta activo")
    require_reauth(p, data.password, "Confirmá con tu contraseña")
    db.refresh(u, with_for_update=True)  # el mismo codigo en dos pedidos a la vez: el segundo lo ve ya usado
    step = totp_step(u.totp_secret or "", data.code, after=u.totp_last_step)
    if step is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El código de tu app no es válido (o ya se usó). Si ya no tenés la app, usá «Cambiar de teléfono» con un "
            "código de recuperación: al terminar te da códigos nuevos.",
        )
    u.totp_last_step = step
    codigos = new_recovery_codes()
    u.totp_recovery = [hash_recovery(c) for c in codigos]
    audit(db, p.tenant.id, u.id, "2fa_recovery", "user", u.id, ip=p.ip)
    notify_security(
        db,
        p.tenant,
        u,
        "Se generaron códigos de recuperación nuevos",
        "<p>Se generaron códigos de recuperación nuevos para la verificación en dos pasos de tu cuenta de Crimson el "
        f"{fecha_cr(datetime.now(UTC))}. Los anteriores ya no sirven. Si no fuiste vos, cambiá tu contraseña y avisale "
        "a tu administrador.</p>",
    )
    db.commit()
    return RecoveryCodesOut(codes=codigos)


def _twofa_required_somewhere(db: Session, user: User) -> bool:
    """El 2FA es de la persona (no de la empresa): basta que una de sus membresias activas lo exija."""
    ms = db.scalars(select(TenantUser).join(Tenant).where(TenantUser.user_id == user.id, TenantUser.active, Tenant.active)).all()
    return any(twofa.is_required(m.tenant, m.role_code) for m in ms)


@router.post("/2fa/disable", status_code=204)
def totp_disable(data: TotpDisableIn, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)):
    """Apaga el 2FA con la contrasena y el codigo actual de la app (o uno de recuperacion), lo mismo que "Cambiar de
    telefono": si bastara la contrasena, quien tiene la sesion y la contrasena lo apagaria y despues lo configuraria
    con su propia app (setup sin 2FA activo no pide nada). Sin telefono ni codigos lo quita el administrador al
    restablecer la contrasena. Plan 1.10: si su rol (en cualquiera de sus empresas) exige 2FA no se apaga; se cambia
    de telefono."""
    if _twofa_required_somewhere(db, p.user):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tu rol exige la verificación en dos pasos: no se puede desactivar. Si cambiaste de teléfono, usá «Cambiar de teléfono».",
        )
    require_reauth(p, data.password, "Contraseña incorrecta")  # cada intento cuenta (reauth_limiter, tambien el codigo)
    u = p.user
    if u.totp_enabled:
        db.refresh(u, with_for_update=True)  # el mismo codigo en dos pedidos a la vez: el segundo lo ve ya usado
        step = totp_step(u.totp_secret or "", data.code or "", after=u.totp_last_step)
        if step is None and use_recovery_code(u.totp_recovery, data.code or "") is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "El código de tu app (o el de recuperación) no es válido o ya se usó")
        if step is not None:
            u.totp_last_step = step
    p.user.totp_enabled = False
    p.user.totp_secret = None
    p.user.totp_pending_secret = None
    p.user.totp_recovery = []
    audit(db, p.tenant.id, p.user.id, "2fa_off", "user", p.user.id, ip=p.ip)
    notify_security(
        db,
        p.tenant,
        p.user,
        "Cambió tu verificación en dos pasos",
        f"<p>Se desactivó la verificación en dos pasos de tu cuenta de Crimson el {fecha_cr(datetime.now(UTC))}. "
        "Si no fuiste vos, cambiá tu contraseña y avisale a tu administrador.</p>",
    )
    db.commit()


# ---------- Restablecer contrasena con enlace (publico; nunca 401: el portal cerraria la sesion) ----------
@router.post("/password-reset/check")
def password_reset_check(data: ResetTokenIn, request: Request, db: Session = Depends(get_db)):
    token_limiter.hit(f"reset:{ip_key(client_ip(request))}")
    found = find_reset(db, data.token)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El enlace venció o ya se usó")
    pr, user = found
    return {"email": mask_email(user.email), "expires_at": pr.expires_at}


@router.post("/password-reset/confirm")
def password_reset_confirm(data: ResetConfirmIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    token_limiter.hit(f"reset:{ip_key(ip)}")
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
        user.totp_enabled, user.totp_secret, user.totp_recovery, user.totp_pending_secret = False, None, [], None
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
    # el enlace llego a su correo: este navegador queda de confianza para la cuenta (aunque un tercero este disparando
    # la espera por fallidos, entra sin esperar; la contrasena y el 2FA se piden igual)
    _set_device(response, user)
    return {"ok": True, "email": user.email}


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(data: ResetRequestIn, request: Request, db: Session = Depends(get_db)):
    """Olvide mi contrasena. La respuesta es SIEMPRE la misma (exista o no la cuenta) y nunca trae el enlace.
    Sin correo activo no se hace nada: el enlace solo sirve si llega por correo. Riesgo residual aceptado: la
    diferencia de tiempo entre existe y no existe (el envio a Resend tarda), mitigada por los limites."""
    ip = client_ip(request)
    pwreset_ip_limiter.hit(ip_key(ip))
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
def roles(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    # matriz de roles internos (vista previa del admin) con lo que la empresa cambio (Ajustes -> Usuarios -> Roles y
    # Quien ve costos); los de cliente tienen su propia interfaz. Aliado y tecnico de aliado salen aqui: aparecen en
    # "Ver como". Los roles que creo el admin van al final, con su nombre (labels).
    from ..core.deps import _role_permissions, custom_roles, role_overrides, role_root
    from ..core.permisos import clean as perms_clean

    m = {k: effective_permissions(p.tenant, k, v) for k, v in ROLE_PERMISSIONS.items() if k not in CLIENT_ROLES}
    propios = custom_roles(db, p.tenant)
    for r in propios:
        m[r.code] = effective_permissions(p.tenant, r.code, _role_permissions(db, r.code))
    customized = sorted({*role_overrides(p.tenant), *(r.code for r in propios)})
    # rol cambiado o creado: su predefinido de origen y los permisos de fabrica de ese (el menu fijo mas lo agregado)
    bases = {c: role_root(db, c) for c in customized}
    defaults = {c: perms_clean(ROLE_PERMISSIONS[b]) for c, b in bases.items() if b in ROLE_PERMISSIONS and b != "admin"}
    return {"roles": list(m.keys()), "matrix": m, "labels": {r.code: r.name for r in propios}, "customized": customized, "bases": bases, "defaults": defaults}
