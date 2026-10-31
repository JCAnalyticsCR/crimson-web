"""Cuentas de usuario: restablecer contrasenas, cerrar sesiones y avisos de seguridad.

Funciones sin HTTP que comparten Ajustes -> Usuarios (routers/users_admin.py) y el acceso (routers/auth.py).
Las contrasenas no se ven nunca, ni el admin: se guardan con Argon2 (una via). Aqui no se guarda ni se registra
ninguna contrasena, token o enlace en claro: de los enlaces solo queda el SHA-256 del token.
"""

from __future__ import annotations

import html
import re
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.deps import _role_permissions, effective_permissions
from ..core.ratelimit import login_limiter
from ..core.security import hash_password, hash_token
from ..models import EmailOutbox, PasswordReset, RefreshToken, Tenant, TenantUser, User
from .documents import CR
from .mail import queue_email

TEMP_PASSWORD_HOURS = 48  # contrasena temporal que genera el admin
ADMIN_LINK_HOURS = 24  # enlace por correo que manda el admin
SELF_LINK_MINUTES = 60  # "olvide mi contrasena" (autoservicio)
INVITE_DAYS = 7
# Nombres de los roles en los correos (en la pantalla salen de portal/src/app/roles.ts). Un rol nuevo de otro
# frente que no este aqui sale con su codigo.
ROLE_LABELS = {
    "admin": "Administrador",
    "ventas": "Vendedor",
    "caja": "Caja",
    "supervisor": "Supervisor técnico",
    "tecnico": "Técnico instalador",
    "inventario": "Bodega",
    "contabilidad": "Contabilidad",
    "lectura": "Solo lectura",
    "jefe_ventas": "Jefe de ventas",
}


def role_label(code: str | None, db: Session | None = None) -> str:
    """Nombre del rol para un correo o una lista. Un rol creado por el admin (Ajustes -> Usuarios -> Roles) sale con el
    nombre que le puso («Vendedor senior»), nunca con su codigo interno: con `db` se busca en la tabla role."""
    c = code or ""
    if c in ROLE_LABELS or db is None:
        return ROLE_LABELS.get(c, c)
    from ..models import Role

    name = db.scalar(select(Role.name).where(Role.code == c))
    return name or c


def aware(dt: datetime | None) -> datetime | None:
    """SQLite devuelve las fechas sin zona: se marcan como UTC (todo lo que guarda la API esta en UTC)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def fecha_cr(dt: datetime | None) -> str:
    """Fecha y hora de Costa Rica para los correos: 14/10/2026 3:05 p. m."""
    d = aware(dt)
    if d is None:
        return ""
    d = d.astimezone(CR)
    h = d.hour % 12 or 12
    return f"{d:%d/%m/%Y} {h}:{d:%M} {'a. m.' if d.hour < 12 else 'p. m.'}"


def mask_email(email: str | None) -> str:
    """j***@dominio.com: la pagina publica del enlace confirma la cuenta sin exponer el correo completo."""
    local, _, dom = (email or "").partition("@")
    if not dom:
        return "***"
    return f"{local[:1]}***@{dom}"


def is_shared(db: Session, user_id: int, tenant_id: int) -> bool:
    """La cuenta tiene membresia (cualquiera) en OTRA empresa: su nombre, correo y contrasena no se tocan desde aqui."""
    return db.scalar(select(TenantUser.id).where(TenantUser.user_id == user_id, TenantUser.tenant_id != tenant_id).limit(1)) is not None


def shared_ids(db: Session, user_ids: Iterable[int], tenant_id: int) -> set[int]:
    """is_shared para toda la lista de una vez (una consulta)."""
    ids = list(user_ids)
    if not ids:
        return set()
    return set(db.scalars(select(TenantUser.user_id).where(TenantUser.user_id.in_(ids), TenantUser.tenant_id != tenant_id)))


def active_admins(db: Session, tenant_id: int) -> int:
    """Administradores internos activos de la empresa (la cuenta tambien activa)."""
    return int(
        db.scalar(
            select(func.count(TenantUser.id))
            .join(User, User.id == TenantUser.user_id)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.role_code == "admin",
                TenantUser.active,
                TenantUser.customer_id.is_(None),
                TenantUser.ally_company_id.is_(None),
                User.active,
            )
        )
        or 0
    )


def issuer_still_allowed(db: Session, tenant_id: int | None, issuer_id: int | None, user_id: int) -> bool:
    """Quien emitio un enlace de admin o una contrasena temporal todavia podria emitirlo HOY en esa empresa: su cuenta
    y su membresia interna activas, con settings.configurar y, si la persona hoy es admin alli, admin tambien. La
    persona sigue activa alli. Si al que lo pidio lo desactivaron o le bajaron el rol, o a la persona la ascendieron
    a admin y quien lo emitio no lo es, lo que tiene en la mano deja de servir (si no, entraria con esa cuenta)."""
    if not issuer_id or tenant_id is None:
        return False
    roles = dict(
        db.execute(
            select(TenantUser.user_id, TenantUser.role_code)
            .join(User, User.id == TenantUser.user_id)
            .join(Tenant, Tenant.id == TenantUser.tenant_id)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id.in_([issuer_id, user_id]),
                TenantUser.active,
                TenantUser.customer_id.is_(None),
                TenantUser.ally_company_id.is_(None),
                User.active,
                Tenant.active,
            )
        ).all()
    )
    quien, destino = roles.get(issuer_id), roles.get(user_id)
    if quien is None or destino is None:
        return False
    if destino == "admin" and quien != "admin":
        return False
    perms = effective_permissions(db.get(Tenant, tenant_id), quien, _role_permissions(db, quien))
    return "*" in perms or bool({"*", "configurar"} & set(perms.get("settings", [])))


def drop_stale_credentials(db: Session, tenant_id: int, user_id: int) -> int:
    """Tras cambiar el rol de alguien o desactivarlo en una empresa: los enlaces de admin y las temporales que esa
    persona emitio alli, o que otro emitio para ella, y que quien los emitio ya no podria emitir (issuer_still_allowed),
    dejan de servir ya. find_reset y el login igual los revalidan al usarlos; esto los apaga tambien en la lista.
    Devuelve cuantos anulo."""
    now = datetime.now(UTC)
    n = 0
    for pr in db.scalars(
        select(PasswordReset).where(
            PasswordReset.tenant_id == tenant_id,
            PasswordReset.kind == "admin",
            PasswordReset.used_at.is_(None),
            or_(PasswordReset.created_by == user_id, PasswordReset.user_id == user_id),
        )
    ):
        if not issuer_still_allowed(db, tenant_id, pr.created_by, pr.user_id):
            pr.used_at = now
            n += 1
    for u in db.scalars(
        select(User).where(User.must_change_password, User.temp_password_tenant_id == tenant_id, or_(User.temp_password_by == user_id, User.id == user_id))
    ):
        exp = aware(u.temp_password_expires_at)
        if (exp is None or exp > now) and not issuer_still_allowed(db, tenant_id, u.temp_password_by, u.id):
            u.temp_password_expires_at = now
            n += 1
    return n


def temp_password_alive(db: Session, user: User) -> bool:
    """Compuerta del login: la temporal sirve mientras quien la genero podria generarla hoy (ver issuer_still_allowed).
    Las temporales sin quien las genero registrado (de antes de esta columna) solo miran su vencimiento."""
    if user.temp_password_by is None:
        return True
    return issuer_still_allowed(db, user.temp_password_tenant_id, user.temp_password_by, user.id)


def revoke_refresh_tokens(db: Session, user_id: int, tenant_id: int | None = None) -> int:
    """Revoca los refresh tokens vivos (de una empresa o de todas). Devuelve cuantos."""
    now = datetime.now(UTC)
    q = select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if tenant_id is not None:
        q = q.where(RefreshToken.tenant_id == tenant_id)
    n = 0
    for rt in db.scalars(q):
        rt.revoked_at = now
        n += 1
    return n


def end_sessions(db: Session, user: User, tenant_id: int | None = None, bump: bool = True) -> int:
    """Cierra sus sesiones: revoca los refresh tokens y (bump) sube session_version, con lo que los access tokens
    ya emitidos se rechazan en el siguiente request en vez de vivir sus 15 minutos."""
    n = revoke_refresh_tokens(db, user.id, tenant_id)
    if bump:
        user.session_version = int(user.session_version or 0) + 1
    return n


def invalidate_resets(db: Session, user_id: int, tenant_id: int | None = None) -> None:
    """Los enlaces de contrasena sin usar dejan de servir (used_at = ahora): todos, o solo los de una empresa."""
    now = datetime.now(UTC)
    q = select(PasswordReset).where(PasswordReset.user_id == user_id, PasswordReset.used_at.is_(None))
    if tenant_id is not None:
        q = q.where(PasswordReset.tenant_id == tenant_id)
    for pr in db.scalars(q):
        pr.used_at = now


def issue_reset(
    db: Session, user: User, tenant_id: int, kind: str, created_by: int | None, ttl: timedelta, ip: str | None, reset_2fa: bool = False
) -> tuple[str, PasswordReset]:
    """Enlace nuevo de un solo uso. Devuelve (token en claro, fila); de la fila solo queda el hash.
    reset_2fa: el admin pidio quitar la verificacion en dos pasos; se quita recien al usar el enlace."""
    raw = secrets.token_urlsafe(32)
    pr = PasswordReset(
        tenant_id=tenant_id,
        user_id=user.id,
        token_hash=hash_token(raw),
        kind=kind,
        expires_at=datetime.now(UTC) + ttl,
        created_by=created_by,
        ip=(ip or None) and ip[:64],
        reset_2fa=reset_2fa,
    )
    db.add(pr)
    db.flush()
    return raw, pr


def find_reset(db: Session, raw: str | None) -> tuple[PasswordReset, User] | None:
    """Enlace valido: existe, sin usar, no vencido, la cuenta activa y con alguna membresia activa en una empresa activa.
    El que emitio un admin ademas exige que la cuenta siga activa en ESA empresa, que no entre a ninguna otra y que
    quien lo pidio todavia pueda pedirlo (issuer_still_allowed)."""
    if not raw or len(raw) > 200:
        return None
    pr = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(raw)))
    if not pr or pr.used_at or aware(pr.expires_at) <= datetime.now(UTC):
        return None
    user = db.get(User, pr.user_id)
    if not user or not user.active:
        return None
    if pr.kind == "admin":
        # Sin correo el enlace le queda en la mano al admin que lo pidio. Si despues la persona entro a otra empresa
        # (o aqui la desactivaron), usarlo seria restablecer la contrasena de un usuario compartido desde aqui y
        # quedarse con su acceso a la otra empresa: muere. Igual si al que lo pidio lo desactivaron o le bajaron el
        # rol, o a la persona la ascendieron a admin y el que lo pidio no lo es. El de "olvide mi contrasena" (auto)
        # solo llega a su correo.
        if not issuer_still_allowed(db, pr.tenant_id, pr.created_by, user.id) or is_shared(db, user.id, pr.tenant_id):
            return None
    viva = db.scalar(
        select(TenantUser.id).join(Tenant, Tenant.id == TenantUser.tenant_id).where(TenantUser.user_id == user.id, TenantUser.active, Tenant.active).limit(1)
    )
    if viva is None:
        return None
    return pr, user


def set_password(user: User, raw: str) -> None:
    """Contrasena elegida por la persona: limpia la temporal y desbloquea (tambien los fallidos del login por cuenta:
    eran contra la anterior)."""
    user.password_hash = hash_password(raw)
    user.password_changed_at = datetime.now(UTC)
    user.must_change_password = False
    user.temp_password_expires_at = None
    user.temp_password_by = user.temp_password_tenant_id = None
    user.failed_logins = 0
    user.locked_until = None
    login_limiter.forget_account(user.email)


# ---------- Correos ----------
_e = html.escape


SECURITY_NOTE = "Crimson nunca le va a pedir su contraseña por correo, por teléfono ni por WhatsApp."


def _shell(tenant: Tenant, inner: str, *, title: str, preheader: str | None = None, action: tuple[str, str] | None = None) -> str:
    """Diseno base de los correos (email_layout) con la nota de seguridad de las cuentas en el pie."""
    from . import email_layout

    return email_layout.render(tenant, title=title, body=inner, preheader=preheader, action=action, foot=_e(SECURITY_NOTE))


def access_email_html(
    tenant: Tenant, *, title: str, intro_html: str, link: str, label: str, note: str, name: str | None = None
) -> str:
    """Correo con la llave de entrada (invitacion al panel, a los portales de aliados y de clientes, contrasena nueva):
    saludo, que paso, UN boton y el enlace en texto. `intro_html` ya viene escapado; `note` es texto."""
    from . import email_layout as el

    saludo = el.p_html(f"Hola{(' ' + _e(name)) if name else ''}:") if name is not None else ""
    body = saludo + el.p_html(intro_html) + el.p(note, muted=True)
    return el.render(tenant, title=title, body=body, preheader=f"{title}. {note}", action=(link, label), foot=_e(SECURITY_NOTE))


def invite_email_html(tenant: Tenant, role_code: str, link: str, days: int) -> str:
    from sqlalchemy.orm import object_session

    try:
        db = object_session(tenant)  # para el nombre de un rol creado por el admin
    except Exception:  # noqa: BLE001  (una empresa de prueba que no es de la base)
        db = None
    return access_email_html(
        tenant,
        title=f"Lo invitaron a {tenant.name}",
        intro_html=f"Lo invitaron a <b>{_e(tenant.name)}</b> en la plataforma de Crimson como <b>{_e(role_label(role_code, db))}</b>.",
        link=link,
        label="Aceptar invitación",
        note=f"El enlace vence en {days} días y sirve una sola vez. Si no esperaba esta invitación, ignore este correo.",
    )


def reset_email_html(tenant: Tenant, name: str | None, link: str, vence: str) -> str:
    return access_email_html(
        tenant,
        title="Cree su contraseña nueva",
        name=name or "",
        intro_html="Para crear su contraseña nueva de Crimson use este botón.",
        link=link,
        label="Restablecer contraseña",
        note=f"El enlace vence en {vence} y sirve una sola vez. Si no lo pidió, ignore este correo: su contraseña actual sigue igual.",
    )


def notify_security(db: Session, tenant: Tenant, user: User, subject: str, body_html: str, to: str | None = None) -> EmailOutbox:
    """Aviso de un cambio de credenciales (sin enlaces ni contrasenas). Sensible: sin BCC de la empresa."""
    from . import email_layout as el

    body_html = re.sub(r"(?<=[ap]\. m\.)\.", "", body_html)  # "3:05 p. m." + el punto de la frase: un solo punto
    inner = el.p_html(f"Hola {_e(user.full_name or '')}:") + f'<div style="font-family:{el.FONT};font-size:15px;line-height:1.55;color:{el.INK}">{body_html}</div>'
    html_ = _shell(tenant, inner, title=subject.split(" · ")[0], preheader=subject)
    return queue_email(db, tenant, to or user.email, subject, html_, "user_security", user.id)
