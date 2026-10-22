"""Ajustes -> Usuarios (Andres 13/10, 01:56-02:26 y 11:48): usuarios internos, invitaciones, restablecer contrasenas
y cerrar sesiones.

Reglas que no se negocian:
- Las contrasenas no se ven nunca, ni el admin (Argon2, una via). Se restablecen: enlace por correo (24 h, un uso) o
  contrasena temporal generada por el servidor (48 h, se muestra una vez y obliga a crear una propia al entrar).
- Restablecer, cambiar el correo y todo lo que toque el rol admin pide la contrasena del admin que actua. Si es
  incorrecta: 403, nunca 401 (el portal cerraria la sesion del admin).
- Solo un admin toca a otro admin (tambien invitarlo, reenviar, revocar o reemplazar su invitacion); nadie se degrada
  a si mismo y siempre queda al menos un admin activo.
- Un enlace o una temporal que alguien pidio deja de servir si esa persona ya no podria pedirlo (la desactivaron, le
  bajaron el rol, o al destinatario lo ascendieron a admin y quien lo pidio no lo es): se revalida al usarlo.
- Usuarios que tambien entran a otra empresa: su nombre, correo y contrasena no se cambian desde aqui.
- Aqui solo se gestionan usuarios internos: los de clientes, soporte y empresas aliadas tienen su propia pantalla.
- Aceptar una invitacion con una cuenta que ya existe pide la contrasena de esa cuenta (la propia, no una temporal):
  el enlace solo no basta. Al entrar a otra empresa sus enlaces de contrasena vivos dejan de servir.
- Eliminar (reunion 21/10, pedido 21; reunion 2, pedido 28), solo un admin y con su contrasena: quien nunca hizo
  nada (p. ej. una cuenta de prueba) se borra de verdad; quien tiene actividad (documentos o registros a su nombre, o
  bitacora mas alla de su propia cuenta) se ANONIMIZA y desactiva: la cuenta queda como "Usuario eliminado #id" para
  que sus documentos y la bitacora sigan apuntando a alguien, sin nombre, correo, contrasena ni 2FA, y sale de la
  lista. Nunca a si mismo ni al ultimo admin activo. Todo queda en la bitacora, sin contrasenas, tokens ni enlaces.
- Correo de avisos (pedido 22): opcional; las notificaciones internas le llegan ahi en vez de a su correo de entrada.
  Lo cambia el admin con su contrasena y se le avisa a la persona en su correo de entrada.
- Sin correo activo (Resend), cada accion devuelve el enlace para mandarlo por WhatsApp.
"""

from __future__ import annotations

import html
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import twofa
from ..core.clientip import ip_key
from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.deps import ALLY_USER_ROLES, COST_ROLE_CHOICES, Principal, assignable_roles, cost_roles, custom_roles, require
from ..core.ratelimit import admin_users_limiter, login_limiter, token_limiter
from ..core.reauth import actor_key, require_reauth
from ..core.security import MAX_PASSWORD, hash_password, hash_token, new_temp_password, password_is_strong, verify_password
from ..models import EmailOutbox, Invitation, PasswordReset, Tenant, TenantUser, User
from ..services.accounts import (
    ADMIN_LINK_HOURS,
    INVITE_DAYS,
    TEMP_PASSWORD_HOURS,
    aware,
    drop_stale_credentials,
    end_sessions,
    fecha_cr,
    invalidate_resets,
    invite_email_html,
    is_shared,
    issue_reset,
    notify_security,
    reset_email_html,
    revoke_refresh_tokens,
    shared_ids,
)
from ..services.accounts import active_admins as count_active_admins
from ..services.bitacora import audit_change
from ..services.documents import audit
from ..services.mail import mail_configured, queue_email
from .auth import LOCK_MINUTES, MAX_FAILED

router = APIRouter(prefix="/settings", tags=["ajustes"])

ADMIN = require("settings", "configurar")
VER = require("settings", "ver")
# Membresias e invitaciones sin customer_id que NO se gestionan aqui: soporte temporal (su pestana) y usuarios de
# empresas aliadas (se dan de alta y se gestionan desde su empresa aliada; pasarlos a un rol interno les daria todo
# el panel sin el filtro "en comun").
NOT_HERE = ("soporte", *ALLY_USER_ROLES)
ALLY_MSG = "Ese usuario es de una empresa aliada: se gestiona desde su empresa aliada"


# ---------- Ayudantes ----------
def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _base() -> str:
    return cfg.public_base_url.rstrip("/")


def _no_store(response: Response) -> None:
    """Respuestas con un secreto (contrasena temporal, enlace): que ningun cache ni proxy las guarde."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _esc(s: str | None) -> str:
    return html.escape(s or "")


def _actor_key(p: Principal) -> str:
    return actor_key(p)


def _member(db: Session, p: Principal, uid: int) -> TenantUser:
    """Membresia interna de la empresa. Usuarios del portal de clientes o de una empresa aliada: 404 (se manejan en
    Accesos de clientes y en Administracion -> Aliados). Soporte temporal: 409 (tiene su vencimiento y se gestiona en
    su pestana). Rol aliado sin empresa (dato viejo): 409 (su empresa aliada)."""
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid))
    if not m or m.customer_id or m.ally_company_id:
        raise HTTPException(404, "Usuario no encontrado")
    if m.role_code == "soporte":
        raise HTTPException(409, "Ese acceso se gestiona en Ajustes → Acceso de soporte")
    if m.role_code in ALLY_USER_ROLES:
        raise HTTPException(409, ALLY_MSG)
    return m


def _guard_admin(p: Principal, m: TenantUser, new_role: str | None = None) -> None:
    """Un rol personalizado con settings.configurar no toca administradores ni crea uno."""
    if (m.role_code == "admin" or new_role == "admin") and p.role != "admin":
        raise HTTPException(403, "Solo un administrador puede cambiar a otro administrador")


def _guard_admin_invite(p: Principal, role_code: str) -> None:
    """Invitar como admin (o reenviar o revocar esa invitacion) es crear un admin: solo otro admin."""
    if role_code == "admin" and p.role != "admin":
        raise HTTPException(403, "Solo un administrador puede invitar a otro administrador")


def _reauth(p: Principal, password: str | None) -> None:
    require_reauth(p, password)  # core/reauth.py (mismo limitador y mismo 403)


def _member_by_email(db: Session, tenant_id: int, email: str) -> TenantUser | None:
    u = db.scalar(select(User).where(User.email == email))
    if not u:
        return None
    return db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == u.id))


def _pending_internal(db: Session, tenant_id: int, email: str, exclude_id: int | None = None) -> list[Invitation]:
    """Invitaciones internas sin aceptar ni revocar de ese correo (vencidas incluidas: tambien salen en la lista)."""
    q = select(Invitation).where(
        Invitation.tenant_id == tenant_id,
        func.lower(Invitation.email) == email,
        Invitation.customer_id.is_(None),
        Invitation.ally_company_id.is_(None),
        Invitation.role_code.not_in(NOT_HERE),
        Invitation.accepted_at.is_(None),
        Invitation.revoked_at.is_(None),
    )
    if exclude_id is not None:
        q = q.where(Invitation.id != exclude_id)
    return list(db.scalars(q))


def _revoke_inv(inv: Invitation, by: int | None, now: datetime) -> None:
    inv.revoked_at, inv.revoked_by = now, by
    if aware(inv.expires_at) > now:  # asi lo que solo mire el vencimiento tambien la rechaza
        inv.expires_at = now


def _guard_admin_supersede(db: Session, p: Principal, email: str) -> None:
    """Reemplazar una invitacion de admin pendiente (invitando de nuevo a ese correo o poniendoselo a un usuario) es
    revocarla: solo otro admin, igual que revocarla directamente (_guard_admin_invite)."""
    if p.role != "admin" and any(i.role_code == "admin" for i in _pending_internal(db, p.tenant.id, email)):
        raise HTTPException(403, "Ese correo tiene una invitación de administrador pendiente: solo un administrador puede reemplazarla")


def _supersede(db: Session, tenant_id: int, email: str, actor_id: int | None, ip: str | None, diff: dict, exclude_id: int | None = None) -> int:
    now = datetime.now(UTC)
    n = 0
    for old in _pending_internal(db, tenant_id, email, exclude_id):
        _revoke_inv(old, actor_id, now)
        audit(db, tenant_id, actor_id, "invite_superseded", "invitation", old.id, diff, ip=ip)
        n += 1
    return n


def _invitation(db: Session, p: Principal, iid: int) -> Invitation:
    """Invitacion interna de la empresa (las del portal de clientes, de soporte y de aliados se manejan en sus pantallas)."""
    inv = db.get(Invitation, iid)
    if not inv or inv.tenant_id != p.tenant.id or inv.customer_id or inv.ally_company_id or inv.role_code in NOT_HERE:
        raise HTTPException(404, "Invitación no encontrada")
    return inv


def _live_invitation(db: Session, token: str) -> Invitation | None:
    if not token or len(token) > 200:
        return None
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    if not inv or inv.accepted_at or inv.revoked_at or aware(inv.expires_at) < datetime.now(UTC):
        return None
    return inv


def _send_invite(db: Session, tenant: Tenant, inv: Invitation, raw: str) -> tuple[str, EmailOutbox]:
    link = f"{_base()}/invitacion/{raw}"
    m = queue_email(
        db, tenant, inv.email, f"Invitación a {tenant.name} en Crimson", invite_email_html(tenant, inv.role_code, link, INVITE_DAYS), "invitation", inv.id
    )
    return link, m


def avisar_contrasena_temporal(db: Session, tenant: Tenant, u: User, admin: str, now: datetime) -> None:
    """Aviso de seguridad: el administrador le puso una contrasena temporal (nunca la lleva el correo)."""
    notify_security(
        db,
        tenant,
        u,
        f"Se restableció su contraseña · {tenant.name}",
        f"<p>El administrador {_esc(admin)} restableció su contraseña de Crimson el {fecha_cr(now)}. "
        "Le va a pasar una contraseña temporal; al entrar va a crear una propia. Si no lo esperaba, avísele.</p>",
    )


def _invite_out(inv: Invitation, link: str, m: EmailOutbox) -> dict:
    # emailed dice si el correo salio de verdad: sin llave de Resend queda "simulado" y la pantalla no puede
    # decir "le llego un correo". El enlace se devuelve siempre para mandarlo por WhatsApp (InviteTech lo usa).
    return {"id": inv.id, "link": link, "emailed": m.status == "enviado", "email_error": m.error if m.status == "error" else None, "expires_at": inv.expires_at}


# ---------- Lista ----------
@router.get("/users")
def users(p: Principal = Depends(VER), db: Session = Depends(get_db)):
    tid, now = p.tenant.id, datetime.now(UTC)
    # los usuarios del portal del cliente se administran desde Accesos de clientes, los de soporte en su pestana y
    # los de empresas aliadas desde su empresa aliada
    internos = db.scalars(select(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.customer_id.is_(None), TenantUser.ally_company_id.is_(None))).all()
    rows = [m for m in internos if m.role_code not in NOT_HERE and not is_deleted(m.user)]
    support_members = sum(1 for m in internos if m.role_code == "soporte" and m.active)
    ids = [m.user_id for m in rows]
    shared = shared_ids(db, ids, tid)
    resets: dict[int, datetime] = {}
    if ids:
        for pr in db.scalars(
            select(PasswordReset)
            .where(PasswordReset.tenant_id == tid, PasswordReset.user_id.in_(ids), PasswordReset.used_at.is_(None), PasswordReset.expires_at > now)
            .order_by(PasswordReset.id)
        ):
            resets[pr.user_id] = pr.expires_at  # el mas reciente gana
    rows.sort(key=lambda m: (not m.active, (m.user.full_name or "").lower()))

    def locked(u: User):
        lu = aware(u.locked_until)
        return u.locked_until if lu and lu > now else None

    def intentos(u: User):
        """Muchos fallidos al entrar (todas las IPs): desde un equipo que no es suyo se espera entre intentos."""
        s = login_limiter.account_flagged(u.email or "")
        return now + timedelta(seconds=s) if s else None

    miembros = {(m.user.email or "").lower() for m in internos}
    inv = [
        i
        for i in db.scalars(
            select(Invitation)
            .where(
                Invitation.tenant_id == tid,
                Invitation.customer_id.is_(None),
                Invitation.ally_company_id.is_(None),
                Invitation.role_code.not_in(NOT_HERE),
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now - timedelta(days=30),
            )
            .order_by(Invitation.id.desc())
        )
        if i.email.lower() not in miembros  # p. ej. una invitacion vieja de alguien que ya entro por otra
    ]
    correos: dict[int, EmailOutbox] = {}
    quienes: dict[int, str] = {}
    if inv:
        for m in db.scalars(
            select(EmailOutbox)
            .where(EmailOutbox.tenant_id == tid, EmailOutbox.entity == "invitation", EmailOutbox.entity_id.in_([i.id for i in inv]))
            .order_by(EmailOutbox.id)
        ):
            correos[m.entity_id] = m
        by = {i.invited_by for i in inv if i.invited_by}
        if by:
            quienes = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(by)))}

    def inv_out(i: Invitation) -> dict:
        m = correos.get(i.id)
        return {
            "id": i.id,
            "email": i.email,
            "role": i.role_code,
            "expires_at": i.expires_at,
            "status": "pendiente" if aware(i.expires_at) > now else "vencida",
            "sent_count": i.sent_count or 1,
            "last_sent_at": i.last_sent_at,
            "invited_by": quienes.get(i.invited_by) if i.invited_by else None,
            "email_status": m.status if m else None,
            "email_error": m.error if m and m.status == "error" else None,
        }

    return {
        "users": [
            {
                "id": m.user.id,
                "email": m.user.email,
                "name": m.user.full_name,
                "role": m.role_code,
                "active": m.active,
                "totp": m.user.totp_enabled,
                "last_login": m.user.last_login_at,
                "is_self": m.user_id == p.user.id,
                "shared": m.user_id in shared,
                "must_change_password": bool(m.user.must_change_password),
                "temp_password_expires_at": m.user.temp_password_expires_at if m.user.must_change_password else None,
                "locked_until": locked(m.user),
                "login_failures_until": intentos(m.user),
                "reset_link_expires_at": resets.get(m.user_id),
                "password_changed_at": m.user.password_changed_at,
                "member_since": m.created_at,
                # correo de avisos: suele ser un buzon personal o externo; solo lo ve quien lo cambia (el admin)
                "notify_email": m.notify_email if p.can("settings", "configurar") else None,
            }
            for m in rows
        ],
        "invitations": [inv_out(i) for i in inv],
        "roles": assignable_roles(db, p.tenant),
        # nombres de los roles que creo el admin (los predefinidos los nombra el portal)
        "role_labels": {r.code: r.name for r in custom_roles(db, p.tenant)},
        # roles que piden la contrasena al asignarlos o invitar con ellos (como el de administrador)
        "reauth_roles": [c for c in assignable_roles(db, p.tenant) if _sensitive_role(db, p.tenant, c)],
        # "Quien ve costos": lo habilitado hoy y los roles que se pueden habilitar (el resto nunca)
        "cost_roles": list(cost_roles(p.tenant)),
        "cost_role_choices": list(COST_ROLE_CHOICES),
        # 2FA obligatorio (core/twofa.py): roles que lo exigen, dias de gracia y los roles que se pueden elegir
        "twofa_roles": list(twofa.required_roles(p.tenant)),
        "twofa_grace_days": twofa.grace_days(p.tenant),
        "twofa_role_choices": twofa.role_choices(db, p.tenant),  # tambien los roles que creo el admin
        "mail_configured": mail_configured(),
        "support_members": support_members,
    }


def _sensitive_role(db: Session, tenant, code: str | None) -> bool:
    """Rol que pide la contrasena al asignarlo o invitar con el, como el de administrador (con una sesion robada no se
    crea un "casi administrador" sin reautenticar): un rol creado en Ajustes -> Roles copiado del administrador o con
    acceso a Ajustes, contabilidad, planillas, usuarios de aliados o aprobar accesos de clientes (core/permisos.SENSITIVE),
    y un predefinido al que la empresa le AGREGO algo de eso (darle planillas a "ventas"; lo que trae de fabrica, como la
    contabilidad del Contador, no la pide). El administrador se trata aparte."""
    from ..core.deps import ROLE_PERMISSIONS, _role_permissions, effective_permissions, role_root
    from ..core.permisos import sensitive_hits

    if not code or code == "admin":
        return False
    if code in ROLE_PERMISSIONS:
        fabrica = ROLE_PERMISSIONS[code]
        return bool(sensitive_hits(effective_permissions(tenant, code, fabrica)) - sensitive_hits(fabrica))
    if role_root(db, code) == "admin":
        return True
    return bool(sensitive_hits(_role_permissions(db, code)))


# ---------- Invitaciones ----------
class TokenIn(BaseModel):
    token: str = Field(max_length=200)


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "lectura"
    # solo para invitar como admin: crear un administrador pide la contrasena de quien actua, igual que ascender a
    # alguien con PATCH (InviteTech y los demas roles no la mandan)
    admin_password: str | None = Field(None, max_length=MAX_PASSWORD)


class ResendIn(BaseModel):
    admin_password: str | None = Field(None, max_length=MAX_PASSWORD)  # invitaciones de admin o de un rol sensible


@router.post("/invitations", status_code=201)
def invite(data: InviteIn, response: Response, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    admin_users_limiter.hit(_actor_key(p))
    if data.role not in assignable_roles(db, p.tenant):
        raise HTTPException(422, "Rol invalido")
    _guard_admin_invite(p, data.role)
    if data.role == "admin" or _sensitive_role(db, p.tenant, data.role):
        _reauth(p, data.admin_password)
    tid = p.tenant.id
    email = str(data.email).strip().lower()
    m = _member_by_email(db, tid, email)
    if m:
        if m.customer_id:
            raise HTTPException(409, "Ese correo ya tiene acceso al portal de clientes.")
        if m.role_code == "soporte":
            raise HTTPException(409, "Esa persona tiene un acceso de soporte técnico; se gestiona en Acceso de soporte.")
        if m.ally_company_id or m.role_code in ALLY_USER_ROLES:
            raise HTTPException(409, "Esa persona es usuaria de una empresa aliada: se gestiona desde su empresa aliada.")
        raise HTTPException(409, "Ese correo ya es usuario de la empresa. Si está inactivo, activalo desde la lista.")
    now = datetime.now(UTC)
    ajena = db.scalar(
        select(Invitation.role_code).where(
            Invitation.tenant_id == tid,
            func.lower(Invitation.email) == email,
            Invitation.customer_id.is_(None),
            Invitation.role_code.in_(NOT_HERE),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
    )
    if ajena == "soporte":
        raise HTTPException(409, "Esa persona tiene un acceso de soporte técnico; se gestiona en Acceso de soporte.")
    if ajena:
        raise HTTPException(409, "Esa persona tiene una invitación de una empresa aliada pendiente: se gestiona desde su empresa aliada.")
    _guard_admin_supersede(db, p, email)  # la nueva reemplaza a las anteriores (abajo): una de admin solo la toca un admin
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        tenant_id=tid,
        email=email,
        role_code=data.role,
        token_hash=hash_token(raw),
        expires_at=now + timedelta(days=INVITE_DAYS),
        invited_by=p.user.id,
        sent_count=1,
        last_sent_at=now,
    )
    db.add(inv)
    db.flush()
    # una sola invitacion viva por correo: las anteriores dejan de servir (Andres tenia duplicadas)
    _supersede(db, tid, email, p.user.id, p.ip, {"reemplazada_por": inv.id}, exclude_id=inv.id)
    link, m_out = _send_invite(db, p.tenant, inv, raw)
    audit(
        db,
        tid,
        p.user.id,
        "invite",
        "invitation",
        inv.id,
        {"email": inv.email, "role": inv.role_code},
        ip=p.ip,
        after={"email": inv.email, "role": inv.role_code},
    )
    db.commit()
    _no_store(response)
    return _invite_out(inv, link, m_out)


@router.post("/invitations/check", tags=["publico"])
def invite_check(data: TokenIn, request: Request, db: Session = Depends(get_db)):
    """Pagina publica /invitacion/:token: a que empresa y con que rol, y si el correo ya tiene cuenta."""
    token_limiter.hit(f"inv:{ip_key(_ip(request))}")
    inv = _live_invitation(db, data.token)
    if not inv:
        raise HTTPException(404, "Invitación inválida, vencida o revocada")
    t = db.get(Tenant, inv.tenant_id)
    existe = db.scalar(select(User.id).where(User.email == inv.email.lower())) is not None
    return {"email": inv.email, "tenant": t.name if t else "", "role": inv.role_code, "existing_account": existe, "expires_at": inv.expires_at}


@router.post("/invitations/{iid}/resend")
def invite_resend(iid: int, response: Response, data: ResendIn | None = None, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    """Reenviar rota el token (el enlace anterior deja de servir) y renueva el vencimiento: sirve para las vencidas.
    Una invitacion de admin (o de un rol sensible, _sensitive_role) devuelve un enlace vivo para entrar con ese rol: pide
    la contrasena de quien reenvia, como al invitar."""
    inv = _invitation(db, p, iid)
    _guard_admin_invite(p, inv.role_code)
    if inv.accepted_at:
        raise HTTPException(409, "Ya fue aceptada")
    if inv.revoked_at:
        raise HTTPException(409, "Fue revocada: creá una nueva")
    if _member_by_email(db, p.tenant.id, inv.email.lower()):
        raise HTTPException(409, "Ese correo ya es usuario de la empresa")
    admin_users_limiter.hit(_actor_key(p))
    if inv.role_code == "admin" or _sensitive_role(db, p.tenant, inv.role_code):  # el enlace vivo entra con ese rol
        _reauth(p, data.admin_password if data else None)
    now = datetime.now(UTC)
    raw = secrets.token_urlsafe(32)
    inv.token_hash = hash_token(raw)
    inv.expires_at = now + timedelta(days=INVITE_DAYS)
    inv.sent_count = int(inv.sent_count or 1) + 1
    inv.last_sent_at = now
    db.flush()
    link, m = _send_invite(db, p.tenant, inv, raw)
    audit(db, p.tenant.id, p.user.id, "invite_resend", "invitation", inv.id, {"envio": inv.sent_count, "correo": m.status}, ip=p.ip)
    db.commit()
    _no_store(response)
    return _invite_out(inv, link, m)


@router.post("/invitations/{iid}/revoke")
def invite_revoke(iid: int, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    inv = _invitation(db, p, iid)
    _guard_admin_invite(p, inv.role_code)
    if inv.accepted_at:
        raise HTTPException(409, "Ya fue aceptada: desactivá al usuario")
    if inv.revoked_at:
        return {"ok": True}  # idempotente: sin otra fila de bitacora
    _revoke_inv(inv, p.user.id, datetime.now(UTC))
    audit(db, p.tenant.id, p.user.id, "invite_revoke", "invitation", inv.id, {"email": inv.email}, ip=p.ip)
    db.commit()
    return {"ok": True}


class AcceptIn(BaseModel):
    token: str = Field(max_length=200)
    # Cuenta nueva: nombre y contrasena nueva, obligatorios. Cuenta que ya existe: password es la contrasena de esa
    # cuenta (prueba que quien acepta es su duena) y full_name se ignora (ver accept).
    full_name: str | None = Field(None, max_length=160)
    password: str | None = Field(None, max_length=MAX_PASSWORD)


@router.post("/invitations/accept", tags=["publico"])
def accept(data: AcceptIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _ip(request)
    token_limiter.hit(f"inv:{ip_key(ip)}")
    inv = _live_invitation(db, data.token)
    if not inv:
        raise HTTPException(404, "Invitación inválida, vencida o revocada")
    company = None
    if inv.ally_company_id:
        # invitacion al portal de aliados: la empresa tiene que seguir activa (antes de crear la cuenta)
        from ..models import AllyCompany

        company = db.get(AllyCompany, inv.ally_company_id)
        if not company or company.tenant_id != inv.tenant_id or not company.active:
            raise HTTPException(409, "Esa empresa aliada ya no está activa. Escríbanos.")
    now = datetime.now(UTC)
    u = db.scalar(select(User).where(User.email == inv.email.lower()))
    existing = u is not None
    if not u:
        nombre = (data.full_name or "").strip()
        if len(nombre) < 2:
            raise HTTPException(422, "Escribí tu nombre completo")
        if not data.password or not password_is_strong(data.password):
            raise HTTPException(422, "Mínimo 10 caracteres con letras y números")
        u = User(email=inv.email.lower(), full_name=nombre, password_hash=hash_password(data.password), password_changed_at=now)
        db.add(u)
        db.flush()
    else:
        # Cuenta que ya existe (otra empresa): NO se toca su nombre ni su contrasena (cambiarla con un enlace de
        # invitacion permitiria tomar una cuenta ajena) y quien acepta prueba con esa contrasena que es su duena. El
        # enlace solo no basta: el admin que invita siempre lo recibe, y con el pegaria a su empresa la cuenta de
        # otra persona (veria sus datos de seguridad y le trabaria la gestion en su empresa). Los intentos cuentan
        # y bloquean como en el login. Respuestas 403/423, nunca 401.
        if not data.password:
            raise HTTPException(422, "Escribí la contraseña de tu cuenta para aceptar")
        locked = aware(u.locked_until)
        if locked and locked > now:
            raise HTTPException(423, "Cuenta bloqueada temporalmente por intentos fallidos")
        if not verify_password(data.password, u.password_hash):
            u.failed_logins = int(u.failed_logins or 0) + 1
            if u.failed_logins >= MAX_FAILED:
                u.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                u.failed_logins = 0
            db.commit()  # nada mas cambio todavia: solo el intento fallido
            raise HTTPException(
                403, "Esa no es la contraseña de tu cuenta. Si no la recordás, pedile al administrador de la empresa donde ya entrás que la restablezca."
            )
        if u.must_change_password:
            # Una contrasena temporal la genero (y la conoce) un admin de la otra empresa: no prueba que quien acepta
            # sea la duena, y aceptar con ella le dejaria a ese admin una cuenta que tambien entra aqui. Primero la
            # persona entra y crea la suya (el login ya revisa el vencimiento de la temporal).
            exp = aware(u.temp_password_expires_at)
            if exp and exp < now:
                raise HTTPException(
                    409, "Tu contraseña temporal venció: pedile al administrador de tu empresa que te genere otra y creá la tuya antes de aceptar."
                )
            raise HTTPException(409, "Entrá primero con tu contraseña temporal y creá la tuya; después aceptá la invitación.")
    m = db.scalar(select(TenantUser).where(TenantUser.user_id == u.id, TenantUser.tenant_id == inv.tenant_id))
    if inv.ally_company_id:
        # portal de aliados: la membresia queda atada a ESA empresa aliada. Una cuenta interna, de un cliente o de otra
        # empresa aliada nunca se convierte por aceptar un enlace (una persona = una membresia por empresa).
        if m and m.ally_company_id != inv.ally_company_id:
            raise HTTPException(409, "Este correo ya tiene otro acceso en la empresa. Escríbanos para revisarlo.")
        if m and m.role_code != inv.role_code:
            from ..services.ally_access import invited_by_ally

            # el rol de alguien que ya existe solo lo cambia Crimson: una invitacion del portal del aliado (siempre de
            # tecnico) no le baja el rol a un gerente ni reactiva a quien Crimson desactivo
            if invited_by_ally(db, inv):
                raise HTTPException(409, "Ese usuario lo administra Crimson. Escríbanos para revisarlo.")
        if m:
            m.role_code, m.active = inv.role_code, True
        else:
            db.add(TenantUser(tenant_id=inv.tenant_id, user_id=u.id, role_code=inv.role_code, ally_company_id=inv.ally_company_id))
    elif inv.customer_id:
        # invitacion al portal del cliente: la membresia queda atada a ESE cliente. Nunca se convierte una
        # cuenta interna en cuenta de cliente (ni al reves) por aceptar un enlace.
        if m and (m.customer_id != inv.customer_id or m.ally_company_id):
            raise HTTPException(409, "Este correo ya tiene otro acceso en la empresa. Escríbanos para revisarlo.")
        if m:
            m.role_code, m.active = inv.role_code, True
        else:
            db.add(TenantUser(tenant_id=inv.tenant_id, user_id=u.id, role_code=inv.role_code, customer_id=inv.customer_id))
    elif not m:
        db.add(TenantUser(tenant_id=inv.tenant_id, user_id=u.id, role_code=inv.role_code))
    if existing:
        # desde ahora es una cuenta compartida (su contrasena ya no se restablece desde la otra empresa): los enlaces
        # vivos, como uno que un admin de alla tenga en la mano porque no habia correo, dejan de servir
        invalidate_resets(db, u.id)
    inv.accepted_at = now
    db.flush()
    if company is not None:
        from ..services.ally_access import refresh_plan_quantity

        refresh_plan_quantity(db, company)  # mensualidad por usuario: un usuario mas
    if not inv.customer_id and not inv.ally_company_id:
        # las demas invitaciones internas del mismo correo ya no tienen sentido
        _supersede(db, inv.tenant_id, inv.email.lower(), u.id, ip, {"reemplazada_por": inv.id}, exclude_id=inv.id)
    cerrada = _close_browser_session(db, request, response, now)
    audit(
        db, inv.tenant_id, u.id, "invite_accept", "invitation", inv.id, {"email": inv.email, "role": inv.role_code, "cuenta_existente": existing, "cerro_sesion_anterior": cerrada}, ip=ip
    )
    db.commit()
    return {"ok": True, "email": u.email, "existing_account": existing, "previous_session_closed": cerrada}


def _close_browser_session(db: Session, request: Request, response: Response, now: datetime) -> bool:
    """Reunion 2, pedido 27: aceptar una invitacion en un navegador con otra sesion abierta entraba con la sesion vieja
    (cargo "Ventas" en vez de la cuenta nueva). La sesion de ESE navegador (su cookie) se cierra al aceptar: despues se
    entra con la cuenta de la invitacion. Las demas sesiones de esa persona en otros equipos no se tocan."""
    from ..models import RefreshToken
    from .auth import COOKIE

    raw = request.cookies.get(COOKIE)
    if not raw:
        return False
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if rt is not None and rt.revoked_at is None:
        rt.revoked_at = now
    response.delete_cookie(COOKIE, path="/", domain=cfg.cookie_domain)
    return True


# ---------- Editar un usuario ----------
class MemberIn(BaseModel):
    role: str | None = None
    active: bool | None = None
    full_name: str | None = Field(None, min_length=2, max_length=160)
    email: EmailStr | None = None
    # correo de avisos (pedido 22): "" lo quita (los avisos vuelven a su correo de entrada)
    notify_email: EmailStr | Literal[""] | None = None
    reason: str | None = Field(None, max_length=500)  # opcional: queda en la bitacora
    admin_password: str | None = Field(None, max_length=MAX_PASSWORD)

    @field_validator("full_name", "notify_email", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


@router.patch("/users/{uid}")
def member_update(uid: int, data: MemberIn, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    tid = p.tenant.id
    m = _member(db, p, uid)
    if uid == p.user.id and (data.active is False or (data.role and data.role != "admin")):
        raise HTTPException(409, "No podés quitarte el acceso de administrador a vos mismo")
    u = m.user
    role_change = bool(data.role) and data.role != m.role_code
    active_change = data.active is not None and data.active != m.active
    name_change = data.full_name is not None and data.full_name != u.full_name
    new_email = str(data.email).strip().lower() if data.email else None
    email_change = new_email is not None and new_email != (u.email or "").lower()
    new_notify = (str(data.notify_email).strip().lower() or None) if data.notify_email is not None else m.notify_email
    if new_notify and new_notify == (new_email or u.email or "").lower():
        new_notify = None  # el mismo de entrada: no hace falta guardarlo aparte
    notify_change = data.notify_email is not None and new_notify != m.notify_email
    if role_change and data.role not in assignable_roles(db, p.tenant):
        raise HTTPException(422, "Rol invalido")
    _guard_admin(p, m, data.role if role_change else None)
    if (name_change or email_change) and is_shared(db, uid, tid):
        raise HTTPException(409, "Este usuario también entra a otra empresa: su nombre y correo no se cambian desde aquí")
    if (
        email_change
        or (notify_change and new_notify)  # desviar los avisos a otro buzon pide la contrasena; quitarlo no
        or (role_change and "admin" in (m.role_code, data.role))
        or (role_change and _sensitive_role(db, p.tenant, data.role))  # un rol con contabilidad, planillas, Ajustes o usuarios agregados
        or (active_change and m.role_code == "admin")
    ):
        _reauth(p, data.admin_password)
    if email_change:
        if db.scalar(select(User.id).where(User.email == new_email, User.id != uid)) is not None:
            raise HTTPException(409, "Ese correo ya lo usa otra cuenta")
        _guard_admin_supersede(db, p, new_email)  # el cambio revoca las invitaciones pendientes de ese correo (abajo)
        admin_users_limiter.hit(_actor_key(p))
    if m.role_code == "admin" and m.active:
        pierde = (role_change and data.role != "admin") or (active_change and data.active is False)
        if pierde and count_active_admins(db, tid) <= 1:
            raise HTTPException(409, "Debe quedar al menos un administrador activo")

    antes = {"role": m.role_code, "active": m.active}
    if role_change:
        m.role_code = data.role
    if active_change:
        m.active = data.active
    audit_change(db, p, "permissions", "user", uid, antes, {"role": m.role_code, "active": m.active}, reason=data.reason)
    perfil = {"full_name": u.full_name, "email": u.email}
    old_email = u.email
    if name_change:
        u.full_name = data.full_name
    if email_change:
        u.email = new_email
    audit_change(db, p, "user_profile", "user", uid, perfil, {"full_name": u.full_name, "email": u.email}, reason=data.reason)
    if notify_change:
        antes_avisos = m.notify_email
        m.notify_email = new_notify
        audit_change(db, p, "user_profile", "user", uid, {"correo_de_avisos": antes_avisos}, {"correo_de_avisos": new_notify}, reason=data.reason)
    db.flush()
    if active_change and data.active is False:
        # desactivar corta el acceso ya (get_session_principal relee la membresia) y sus sesiones de esta empresa
        revoke_refresh_tokens(db, uid, tid)
        # sus enlaces de contrasena: todos si ya no entra a ninguna empresa; si sigue en otra, los que salieron de aqui
        sigue = db.scalar(select(TenantUser.id).where(TenantUser.user_id == uid, TenantUser.active).limit(1)) is not None
        invalidate_resets(db, uid, tid if sigue else None)
    if role_change or active_change:
        # Lo que esta persona tiene en la mano (enlaces de admin y temporales que pidio para otros) y lo que otro pidio
        # para ella deja de servir si quien lo pidio ya no podria pedirlo: lo desactivaron o le bajaron el rol, o a
        # ella la ascendieron a admin y quien lo pidio no lo es (si no, entraria como admin sin pasar por _guard_admin).
        anulados = drop_stale_credentials(db, tid, uid)
        if anulados:
            audit(db, tid, p.user.id, "password_reset_invalidated", "user", uid, {"anulados": anulados}, ip=p.ip)
    if email_change:
        # los enlaces de contrasena vivos se mandaron al correo VIEJO (que suele ser justo el que ya no es de esa
        # persona): dejan de servir. Si hace falta, el admin pide uno nuevo y va al correo nuevo.
        invalidate_resets(db, uid)
        cuerpo = (
            f"<p>Su correo para entrar a Crimson ({_esc(p.tenant.name)}) cambió de <b>{_esc(old_email)}</b> a <b>{_esc(new_email)}</b>. "
            f"Lo cambió {_esc(p.user.full_name)} el {fecha_cr(datetime.now(UTC))}.</p>"
            "<p>Si no lo pidió, avísele a su administrador.</p>"
        )
        for destino in dict.fromkeys([old_email, new_email]):
            notify_security(db, p.tenant, u, "Cambió su correo para entrar a Crimson", cuerpo, to=destino)
        # una invitacion pendiente para el correo nuevo ya no tiene sentido: esa persona ya es usuaria
        _supersede(db, tid, new_email, p.user.id, p.ip, {"correo_de_usuario": uid})
    if notify_change:
        # la persona se entera en su correo de entrada (y en el nuevo buzon, si hay): nadie le desvia los avisos callado
        destino = _esc(new_notify) if new_notify else "su correo de entrada"
        cuerpo = (
            f"<p>Desde ahora los avisos internos de Crimson ({_esc(p.tenant.name)}) le llegan a <b>{destino}</b>. "
            f"Lo cambió {_esc(p.user.full_name)} el {fecha_cr(datetime.now(UTC))}.</p>"
            "<p>Su correo para entrar no cambia. Si no lo pidió, avísele a su administrador.</p>"
        )
        for destino_correo in dict.fromkeys([u.email, *([new_notify] if new_notify else [])]):
            notify_security(db, p.tenant, u, "Cambió su correo de avisos en Crimson", cuerpo, to=destino_correo)
    db.commit()
    return {"ok": True}


# ---------- Restablecer contrasena ----------
class ResetIn(BaseModel):
    mode: Literal["enlace", "temporal"]
    reset_2fa: bool = False
    admin_password: str = Field(max_length=MAX_PASSWORD)


@router.post("/users/{uid}/reset-password")
def reset_password(uid: int, data: ResetIn, response: Response, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    tid = p.tenant.id
    m = _member(db, p, uid)
    if uid == p.user.id:
        raise HTTPException(409, "Para tu propia cuenta usá Mi cuenta → Cambiar contraseña")
    _guard_admin(p, m)
    u = m.user
    if not m.active or not u.active:
        raise HTTPException(409, "El usuario está desactivado: activalo primero")
    if is_shared(db, uid, tid):
        raise HTTPException(409, "Este usuario también entra a otra empresa: su contraseña no se restablece desde aquí")
    admin_users_limiter.hit(_actor_key(p))
    _reauth(p, data.admin_password)
    now = datetime.now(UTC)
    # perdio el telefono: sin quitar el 2FA quedaria fuera aunque tenga contrasena nueva
    quita_2fa = bool(data.reset_2fa and u.totp_enabled)
    sin_2fa = False

    if data.mode == "temporal":
        if quita_2fa:
            # la temporal cambia la contrasena y cierra sesiones en este mismo paso: se puede quitar ya
            u.totp_enabled, u.totp_secret, u.totp_recovery, u.totp_pending_secret = False, None, [], None
            sin_2fa = True
            audit(db, tid, p.user.id, "2fa_reset", "user", uid, ip=p.ip)
        temp = new_temp_password()
        u.password_hash = hash_password(temp)
        u.must_change_password = True
        u.temp_password_expires_at = now + timedelta(hours=TEMP_PASSWORD_HOURS)
        u.temp_password_by, u.temp_password_tenant_id = p.user.id, tid  # el login la revalida (temp_password_alive)
        u.password_changed_at = now
        u.failed_logins, u.locked_until = 0, None
        login_limiter.forget_account(u.email)
        invalidate_resets(db, uid)
        end_sessions(db, u)  # todas sus sesiones y session_version + 1
        audit(
            db,
            tid,
            p.user.id,
            "password_reset",
            "user",
            uid,
            {"modo": "temporal", "vence": u.temp_password_expires_at.isoformat(), "2fa_reiniciado": sin_2fa},
            ip=p.ip,
        )
        avisar_contrasena_temporal(db, p.tenant, u, p.user.full_name, now)
        expira = u.temp_password_expires_at
        db.commit()
        _no_store(response)
        return {"mode": "temporal", "temp_password": temp, "expires_at": expira, "email": u.email, "name": u.full_name, "login_url": f"{_base()}/login"}

    # enlace: no cambia la contrasena actual ni cierra sesiones hasta que se use (si el admin sospecha robo de
    # cuenta usa "temporal" o "Cerrar sesiones"). Por lo mismo el 2FA tampoco se quita ahora: mientras el enlace
    # no se use, la contrasena vieja sigue sirviendo y quitarlo dejaria la cuenta con un solo factor. Queda
    # marcado en el enlace y se quita al usarlo (/auth/password-reset/confirm).
    invalidate_resets(db, uid)
    raw, pr = issue_reset(db, u, tid, "admin", p.user.id, timedelta(hours=ADMIN_LINK_HOURS), p.ip, reset_2fa=quita_2fa)
    link = f"{_base()}/restablecer/{raw}"
    mail = queue_email(
        db, p.tenant, u.email, f"Cree su nueva contraseña · {p.tenant.name}", reset_email_html(p.tenant, u.full_name, link, "24 horas"), "password_reset", pr.id
    )
    u.failed_logins, u.locked_until = 0, None
    login_limiter.forget_account(u.email)  # como el desbloqueo de siempre: el admin lo pidio
    audit(
        db,
        tid,
        p.user.id,
        "password_reset_link",
        "user",
        uid,
        {"correo": mail.status, "vence": pr.expires_at.isoformat(), "2fa_reiniciado": False, "2fa_se_quita_al_usarlo": quita_2fa},
        ip=p.ip,
    )
    emailed = mail.status == "enviado"
    out = {
        "mode": "enlace",
        "emailed": emailed,
        "email_error": mail.error if mail.status == "error" else None,
        "link": None if emailed else link,
        "expires_at": pr.expires_at,
    }
    db.commit()
    _no_store(response)
    # el enlace solo vuelve al admin cuando el correo NO salio (respaldo por WhatsApp)
    return out


# ---------- Cerrar sesiones ----------
@router.post("/users/{uid}/sessions/revoke")
def sessions_revoke(uid: int, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    tid = p.tenant.id
    m = _member(db, p, uid)
    if uid == p.user.id:
        raise HTTPException(409, "Para tu cuenta usá Mi cuenta → Cerrar en todos los dispositivos")
    _guard_admin(p, m)
    admin_users_limiter.hit(_actor_key(p))
    if is_shared(db, uid, tid):
        # no se tocan sus sesiones en la otra empresa; los access tokens de aqui mueren solos en <= 15 min
        n = revoke_refresh_tokens(db, uid, tid)
    else:
        n = end_sessions(db, m.user)
    audit(db, tid, p.user.id, "sessions_revoke", "user", uid, {"sesiones": n}, ip=p.ip)
    db.commit()
    return {"ok": True, "revoked": n}


# ---------- Eliminar un usuario (reunion 21/10, pedido 21; reunion 2, pedido 28) ----------
DELETED_DOMAIN = "eliminado.invalid"


def _correo_eliminado(uid: int) -> str:
    return f"eliminado.{uid}.{secrets.token_hex(4)}@{DELETED_DOMAIN}"


def email_digest(correo: str) -> str:
    """Huella CON CLAVE del correo para la bitacora (que no se borra): HMAC-SHA256 con una llave derivada del secreto del
    servidor. Un sha256 sin sal se revierte probando correos del dominio de la empresa; esta no, sin el secreto.
    bootstrap.extra_invites compara con la misma funcion."""
    import hashlib
    import hmac

    from ..core.config import settings

    llave = hashlib.sha256(b"crimson:huella-correo-eliminado:" + settings.jwt_secret.encode()).digest()
    return hmac.new(llave, (correo or "").strip().lower().encode(), hashlib.sha256).hexdigest()


def _iniciales(nombre: str | None) -> str:
    partes = [x for x in (nombre or "").split() if x[:1].isalpha()]
    return " ".join(f"{x[0].upper()}." for x in partes[:3]) or "—"


def _enmascarar(correo: str) -> str:
    local, _, dom = correo.partition("@")
    return f"{local[:2]}***@{dom}" if dom else "***"


def is_deleted(u: User) -> bool:
    """Cuenta eliminada que se conserva como rastro (anonimizada o nombrada por la bitacora): no sale en las listas."""
    return (u.email or "").lower().endswith("@" + DELETED_DOMAIN)


def _anonimizar(db: Session, m: TenantUser, now: datetime) -> None:
    """Con actividad no se borra la fila (documentos, pagos, comisiones y bitacora apuntan a ella): se le quita todo
    lo personal y el acceso. La membresia queda inactiva (las comisiones y reportes viejos siguen viendo su rol) y sin
    correo de avisos; sus sesiones y enlaces mueren."""
    from ..models import RefreshToken

    u = m.user
    u.email = _correo_eliminado(u.id)
    u.full_name = f"Usuario eliminado #{u.id}"
    u.active = False
    u.password_hash = hash_password(secrets.token_urlsafe(32))
    u.must_change_password, u.temp_password_expires_at, u.temp_password_by, u.temp_password_tenant_id = False, None, None, None
    u.totp_enabled, u.totp_secret, u.totp_recovery, u.totp_pending_secret = False, None, [], None
    u.failed_logins, u.locked_until = 0, None
    u.session_version = int(u.session_version or 0) + 1
    m.active = False
    m.notify_email = None
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id)):
        db.delete(rt)
    for pr in db.scalars(select(PasswordReset).where(PasswordReset.user_id == u.id)):
        db.delete(pr)


# Columnas que apuntan a un usuario y NO son actividad: su membresia, sus sesiones y enlaces de contrasena, y quien le
# genero una temporal. La bitacora se mira aparte (ver _actividad).
_NO_ES_ACTIVIDAD = {
    ("tenant_user", "user_id"),
    ("refresh_token", "user_id"),
    ("password_reset", "user_id"),
    ("password_reset", "created_by"),
    ("user", "temp_password_by"),
    ("invitation", "revoked_by"),
}
# Nombres visibles de lo que suele frenar el borrado (el resto sale con el nombre de la tabla)
_ETIQUETAS = {
    "quote": "Cotizaciones",
    "invoice": "Facturas",
    "payment": "Pagos registrados",
    "opportunity": "Oportunidades",
    "customer_note": "Notas de clientes",
    "commission": "Comisiones",
    "commission_rule": "Reglas de comisión",
    "survey": "Levantamientos",
    "work_order": "Órdenes de trabajo",
    "work_order_time": "Horas en órdenes de trabajo",
    "support_ticket": "Tickets de soporte",
    "support_note": "Notas en tickets",
    "stock_movement": "Movimientos de inventario",
    "stock_reservation": "Reservas de material",
    "purchase_request": "Solicitudes de compra",
    "expense": "Gastos",
    "project": "Proyectos",
    "invitation": "Invitaciones enviadas",
    "quote_version": "Cotizaciones enviadas",
    "quote_acceptance": "Aceptaciones registradas",
    "warranty_case": "Garantías",
    "media": "Archivos subidos",
    "audit_log": "Cambios en la bitácora",
}


def _columnas_de_usuario() -> list:
    """Toda columna que guarda un id de usuario: llaves foraneas a user.id y los *_by enteros sin llave (archived_by...)."""
    import sqlalchemy as sa

    from ..core.db import Base

    out = []
    for t in Base.metadata.sorted_tables:
        for c in t.columns:
            if (t.name, c.name) in _NO_ES_ACTIVIDAD or t.name == "audit_log":
                continue
            fk = any(f.column.table.name == "user" and f.column.name == "id" for f in c.foreign_keys)
            if fk or (c.name.endswith("_by") and isinstance(c.type, sa.Integer)):
                out.append(c)
    return out


def _actividad(db: Session, uid: int) -> list[dict]:
    """Lo que esta a nombre del usuario: documentos y registros (cualquier columna que lo nombre) y lo que hizo segun la
    bitacora, sin contar lo de su propia cuenta (entrar, aceptar la invitacion, cambiar su contrasena o su 2FA)."""
    from sqlalchemy import and_, not_, or_

    from ..models import AuditLog

    cuenta: dict[str, int] = {}
    for c in _columnas_de_usuario():
        n = db.scalar(select(func.count()).select_from(c.table).where(c == uid)) or 0
        if n:
            cuenta[c.table.name] = cuenta.get(c.table.name, 0) + int(n)
    propio = or_(and_(AuditLog.entity == "user", AuditLog.entity_id == uid), AuditLog.action == "invite_accept")
    n = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.user_id == uid, not_(propio))) or 0
    if n:
        cuenta["audit_log"] = int(n)
    return [{"what": _ETIQUETAS.get(k, k), "count": v} for k, v in sorted(cuenta.items(), key=lambda x: -x[1])]


def _guard_delete(db: Session, p: Principal, uid: int) -> TenantUser:
    if p.role != "admin":
        raise HTTPException(403, "Solo un administrador elimina usuarios")
    m = _member(db, p, uid)
    if uid == p.user.id:
        raise HTTPException(409, "No puede eliminar su propia cuenta")
    if is_shared(db, uid, p.tenant.id):
        raise HTTPException(409, "Este usuario también entra a otra empresa: aquí solo se puede desactivar")
    if m.role_code == "admin" and m.active and count_active_admins(db, p.tenant.id) <= 1:
        raise HTTPException(409, "Debe quedar al menos un administrador activo")
    return m


@router.get("/users/{uid}/activity")
def user_activity(uid: int, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    """¿Se puede eliminar y como? mode "borrar" (sin actividad: se borra de verdad) o "anonimizar" (con actividad: se
    conserva lo que hizo a nombre de "Usuario eliminado"). reason: por que no se puede."""
    m = _member(db, p, uid)
    act = _actividad(db, uid)
    motivo = None
    if p.role != "admin":
        motivo = "Solo un administrador elimina usuarios"
    elif uid == p.user.id:
        motivo = "No puede eliminar su propia cuenta"
    elif is_shared(db, uid, p.tenant.id):
        motivo = "También entra a otra empresa: aquí solo se puede desactivar"
    elif m.role_code == "admin" and m.active and count_active_admins(db, p.tenant.id) <= 1:
        motivo = "Es el único administrador activo"
    return {"can_delete": motivo is None, "reason": motivo, "activity": act, "mode": "anonimizar" if act else "borrar"}


class DeleteIn(BaseModel):
    admin_password: str = Field(max_length=MAX_PASSWORD)
    reason: str | None = Field(None, max_length=500)


@router.post("/users/{uid}/delete")
def user_delete(uid: int, data: DeleteIn, p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    """Elimina a un usuario. Sin actividad (p. ej. una cuenta de prueba): la cuenta se borra; si la bitacora ya la nombra
    (entro alguna vez) y esa tabla no admite borrados, se deja inactiva, sin acceso y con el correo liberado. Con
    actividad: se anonimiza y desactiva (_anonimizar). En los dos casos el correo queda libre para invitarlo de nuevo."""
    from ..models import AuditLog, RefreshToken

    m = _guard_delete(db, p, uid)
    admin_users_limiter.hit(_actor_key(p))
    _reauth(p, data.admin_password)
    act = _actividad(db, uid)
    u = m.user
    correo = u.email or ""
    # en la bitacora (que no se borra) no queda nada que identifique a la persona: correo enmascarado, iniciales y una
    # huella con clave del correo (bootstrap.extra_invites la usa para no volver a invitar una cuenta eliminada)
    antes = {"email": _enmascarar(correo), "email_hmac": email_digest(correo), "name": _iniciales(u.full_name), "role": m.role_code, "active": m.active}
    tid, now = p.tenant.id, datetime.now(UTC)
    for inv in _pending_internal(db, tid, correo.lower()):
        _revoke_inv(inv, p.user.id, now)
    if act:
        _anonimizar(db, m, now)
        db.flush()
        detalle = {a["what"]: a["count"] for a in act[:8]}
        audit(db, tid, p.user.id, "user_delete", "user", uid, {**antes, "modo": "anonimizada", "actividad": detalle}, ip=p.ip, before=antes, reason=data.reason)
        login_limiter.forget_account(correo)
        db.commit()
        return {"ok": True, "mode": "anonimizada"}
    db.delete(m)
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == uid)):
        db.delete(rt)
    for pr in db.scalars(select(PasswordReset).where(PasswordReset.user_id == uid)):
        db.delete(pr)
    db.flush()
    en_bitacora = db.scalar(select(AuditLog.id).where(AuditLog.user_id == uid).limit(1)) is not None
    if en_bitacora:
        # la bitacora no se toca (en Postgres no admite UPDATE ni DELETE): la cuenta queda como rastro, sin acceso
        u.email = _correo_eliminado(uid)
        u.full_name = f"Usuario eliminado #{uid}"  # como con historial: no queda su nombre ni su correo
        u.active = False
        u.password_hash = hash_password(secrets.token_urlsafe(32))
        u.must_change_password, u.temp_password_expires_at = False, None
        u.totp_enabled, u.totp_secret, u.totp_recovery, u.totp_pending_secret = False, None, [], None
        u.session_version = int(u.session_version or 0) + 1
        modo = "anonimizada (la bitácora la nombra)"
    else:
        db.delete(u)
        modo = "borrada"
    db.flush()
    audit(db, tid, p.user.id, "user_delete", "user", uid, {**antes, "modo": modo}, ip=p.ip, before=antes, reason=data.reason)
    login_limiter.forget_account(correo)
    db.commit()
    return {"ok": True, "mode": "anonimizada" if en_bitacora else "borrada"}
