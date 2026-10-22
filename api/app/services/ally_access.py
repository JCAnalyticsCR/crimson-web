"""Acceso de las empresas aliadas: invitaciones al portal de aliados, cupos de usuarios y mensualidad.

Espejo de services/client_access.py para el portal de aliados (aliado_final §7.1 y §9.2):
- Una persona tiene UNA membresia por empresa (uq_tenant_user): un correo interno, de un cliente o de otra empresa
  aliada nunca se convierte en usuario de esta empresa aliada (409). Se desactiva y se invita otro correo.
- Cupos: usuarios activos + invitaciones vivas no pasan de max_users (NULL = sin tope).
- Mensualidad: una Recurrence de factura al cliente de facturacion del aliado. Modo "por_usuario": la cantidad de la
  linea sigue a los usuarios activos (refresh_plan_quantity). Nunca auto_charge ni suspension automatica.
"""

from __future__ import annotations

import html
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.deps import ALLY_USER_ROLES
from ..core.security import hash_token
from ..models import AllyCompany, Invitation, Invoice, Recurrence, Tenant, TenantUser, User
from .accounts import access_email_html
from .mail import queue_email
from .totals import d, net_sale

INVITE_DAYS = 7
ROLE_LABEL = {"aliado": "Aliado comercial", "tecnico_aliado": "Técnico de aliado"}
# marca de las facturas de mensualidad (DocumentIn.external_order de la recurrencia): asi se distinguen de las demas
PLAN_ORDER = "MENSUALIDAD-PLATAFORMA"
# mensualidad con 30 dias de atraso -> propuesta de suspension en Aprobaciones (la confirma una persona, plan 1.4)
SUSPEND_DAYS = 30


def is_emitted(inv: Invoice) -> bool:
    """La factura ya le llego al aliado (services/invoice_states.is_visible: el mismo criterio que el portal del
    cliente). Un borrador "creado", como el que deja la recurrencia de la mensualidad mientras espera la aprobacion,
    no se ve, no se descarga, no cuenta como atraso y no se descuenta de lo que recibe."""
    from .invoice_states import is_visible

    return is_visible(inv)


def mora_where(tenant_id: int, today: date, days: int = SUSPEND_DAYS, start: date | None = None) -> list:
    """Empresas aliadas activas y sin suspender con una mensualidad por cobrar vencida hace `days` dias o mas.
    start: fecha de corte de cartera (collections_start_date, la misma de los recordatorios): lo emitido antes pudo
    pagarse por fuera y no es motivo para proponer una suspension. Sin fecha de corte cuenta todo lo enviado."""
    from .invoice_states import cobrable_where

    atrasadas = select(Invoice.customer_id).where(
        Invoice.tenant_id == tenant_id,
        Invoice.external_order == PLAN_ORDER,
        cobrable_where(),
        Invoice.due_date.is_not(None),
        Invoice.due_date <= today - timedelta(days=days),
        *([Invoice.issue_date >= start] if start else []),
    )
    return [
        AllyCompany.tenant_id == tenant_id,
        AllyCompany.active.is_(True),
        AllyCompany.suspended_at.is_(None),
        AllyCompany.customer_id.is_not(None),
        AllyCompany.customer_id.in_(atrasadas),
    ]


def _aware(dt: datetime | None) -> datetime | None:
    return dt if not dt or dt.tzinfo else dt.replace(tzinfo=UTC)


def active_members(db: Session, company: AllyCompany) -> list[TenantUser]:
    """Usuarios activos de la empresa aliada (membresia y cuenta activas)."""
    return list(
        db.scalars(
            select(TenantUser)
            .join(User, User.id == TenantUser.user_id)
            .where(TenantUser.tenant_id == company.tenant_id, TenantUser.ally_company_id == company.id, TenantUser.active, User.active)
        )
    )


def live_invitations(db: Session, company: AllyCompany, exclude_email: str | None = None) -> list[Invitation]:
    now = datetime.now(UTC)
    q = select(Invitation).where(
        Invitation.tenant_id == company.tenant_id,
        Invitation.ally_company_id == company.id,
        Invitation.accepted_at.is_(None),
        Invitation.revoked_at.is_(None),
        Invitation.expires_at > now,
    )
    if exclude_email:
        q = q.where(func.lower(Invitation.email) != exclude_email.lower())
    return list(db.scalars(q.order_by(Invitation.id.desc())))


def seats(db: Session, company: AllyCompany, exclude_email: str | None = None) -> dict:
    """Cupos: {used, max, active, pending}. used = activos + invitaciones vivas."""
    act = len(active_members(db, company))
    pend = len(live_invitations(db, company, exclude_email))
    return {"used": act + pend, "max": company.max_users, "active": act, "pending": pend}


def invite_ally_user(
    db: Session,
    tenant: Tenant,
    company: AllyCompany,
    email: str,
    role: str,
    invited_by: int | None,
    name: str | None = None,
    by_ally: bool = False,
) -> dict:
    """Invitacion al portal de aliados ligada a ESA empresa. Devuelve {id, link, emailed, expires_at}: sin Resend el
    correo queda simulado y la pantalla de Crimson ofrece el enlace para mandarlo por WhatsApp, sin afirmar que llego.

    - Un correo con otro acceso en la empresa (cuenta interna, de un cliente o de otra empresa aliada) o con una
      invitacion viva de otro alcance (interna, de un cliente o de otra empresa aliada) -> 409: nunca se anula la
      invitacion de otro ni se le gana el correo a su dueno.
    - by_ally (la manda un usuario aliado desde su portal): solo tecnicos de su empresa; a quien administra Crimson (un
      gerente, aunque este desactivado) no lo reactiva una invitacion del aliado."""
    if role not in ALLY_USER_ROLES:
        raise HTTPException(422, "Rol de aliado inválido")
    if company.tenant_id != tenant.id:
        raise HTTPException(404, "Empresa aliada no encontrada")
    if not company.active:
        raise HTTPException(409, "Esa empresa aliada está inactiva")
    email = email.strip().lower()
    u = db.scalar(select(User).where(User.email == email))
    if u:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == u.id))
        # una cuenta interna, de un cliente o de OTRA empresa aliada nunca se convierte en usuario de esta
        if m and m.ally_company_id != company.id:
            raise HTTPException(409, "Ese correo ya tiene otro acceso en la empresa. Use otro correo.")
        if m and m.active:
            raise HTTPException(409, "Esa persona ya tiene acceso")
        if m and by_ally and m.role_code != "tecnico_aliado":
            raise HTTPException(409, "Ese usuario lo administra Crimson. Escríbanos si necesita cambiarlo.")
    ahora = datetime.now(UTC)
    vivas = list(
        db.scalars(
            select(Invitation).where(
                Invitation.tenant_id == tenant.id,
                func.lower(Invitation.email) == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > ahora,
            )
        )
    )
    if any(i.ally_company_id != company.id for i in vivas):
        # invitacion viva interna, de un cliente o de otra empresa aliada: no se anula ni se reemplaza desde aqui
        raise HTTPException(409, "Ese correo ya tiene otro acceso en la empresa. Use otro correo.")
    st = seats(db, company, exclude_email=email)  # la invitacion viva de este mismo correo se reemplaza abajo
    if company.max_users is not None and st["used"] >= company.max_users:
        raise HTTPException(409, f"Su plan permite {company.max_users} usuarios")
    # una sola invitacion viva por correo EN ESTA EMPRESA: la anterior deja de servir
    for old in vivas:
        old.expires_at = ahora
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        tenant_id=tenant.id,
        email=email,
        role_code=role,
        token_hash=hash_token(raw),
        expires_at=ahora + timedelta(days=INVITE_DAYS),
        invited_by=invited_by,
        ally_company_id=company.id,
        sent_count=1,
        last_sent_at=ahora,
    )
    db.add(inv)
    db.flush()
    link = f"{settings.public_base_url.rstrip('/')}/invitacion/{raw}"
    e = html.escape
    m_out = queue_email(
        db,
        tenant,
        email,
        f"Su acceso al portal de aliados de {tenant.name}",
        access_email_html(
            tenant,
            title="Su acceso al portal de aliados",
            name=name or "",
            intro_html=f"{e(tenant.name)} le dio acceso al portal de aliados como {e(ROLE_LABEL[role])} de <b>{e(company.name)}</b>. "
            "Ahí ve los proyectos y trabajos que llevan en común.",
            link=link,
            label="Entrar al portal",
            note=f"Con el botón crea su contraseña y entra. El enlace vence en {INVITE_DAYS} días y sirve una sola vez.",
            vos=False,
        ),
        "invitation",
        inv.id,
    )
    return {"id": inv.id, "link": link, "emailed": m_out.status == "enviado", "expires_at": inv.expires_at}


def invited_by_ally(db: Session, inv: Invitation) -> bool:
    """La invitacion la mando un usuario aliado desde su portal (no Crimson)."""
    if not inv.invited_by:
        return False
    return (
        db.scalar(
            select(TenantUser.id).where(TenantUser.tenant_id == inv.tenant_id, TenantUser.user_id == inv.invited_by, TenantUser.ally_company_id.is_not(None))
        )
        is not None
    )


def users_out(db: Session, company: AllyCompany) -> dict:
    """Usuarios (activos e inactivos), invitaciones vivas y cupos de una empresa aliada."""
    rows = db.scalars(select(TenantUser).where(TenantUser.tenant_id == company.tenant_id, TenantUser.ally_company_id == company.id)).all()
    rows = sorted(rows, key=lambda m: (not m.active, (m.user.full_name or "").lower()))
    return {
        "users": [
            {
                "id": m.user.id,
                "name": m.user.full_name,
                "email": m.user.email,
                "role": m.role_code,
                "active": bool(m.active and m.user.active),
                "last_login": m.user.last_login_at,
            }
            for m in rows
        ],
        "invitations": [{"id": i.id, "email": i.email, "role": i.role_code, "expires_at": i.expires_at} for i in live_invitations(db, company)],
        "seats": {k: v for k, v in seats(db, company).items() if k in ("used", "max")},
    }


def refresh_plan_quantity(db: Session, company: AllyCompany) -> None:
    """Mensualidad por usuario: la cantidad de la linea = usuarios activos (minimo 1). Fija: no hace nada."""
    if company.plan_mode != "por_usuario" or not company.recurrence_id:
        return
    r = db.get(Recurrence, company.recurrence_id)
    if r is None or r.tenant_id != company.tenant_id:
        return
    db.flush()  # la membresia recien aceptada o desactivada cuenta
    q = max(len(active_members(db, company)), 1)
    tpl = dict(r.template or {})
    lines = [dict(x) for x in tpl.get("lines") or []]
    if not lines:
        return
    lines[0]["quantity"] = str(q)
    tpl["lines"] = lines
    r.template = tpl  # JSON: se reasigna para que SQLAlchemy lo note


def plan_invoices(db: Session, company: AllyCompany, limit: int = 60) -> list[Invoice]:
    """Facturas de mensualidad (no anuladas) al cliente de facturacion del aliado, la mas reciente primero."""
    if not company.customer_id:
        return []
    return list(
        db.scalars(
            select(Invoice)
            .where(
                Invoice.tenant_id == company.tenant_id,
                Invoice.customer_id == company.customer_id,
                Invoice.external_order == PLAN_ORDER,
                Invoice.status != "anulada",
            )
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
            .limit(limit)
        )
    )


def plan_status(db: Session, company: AllyCompany, today: date | None = None, visible=None) -> dict:
    """al_dia, o vencida con monto (saldo de las vencidas, por moneda dominante) y dias del atraso mas viejo.
    visible: filtro opcional de facturas (el portal del aliado solo cuenta las emitidas)."""
    from .invoice_states import cr_today, is_cobrable

    today = today or cr_today()  # fecha de Costa Rica: desde las 18:00 CR la de UTC ya es la de manana
    # por cobrar (enviada y con saldo, plan 1.3): un borrador que el aliado no recibio nunca cuenta como atraso
    vencidas = [i for i in plan_invoices(db, company, limit=500) if is_cobrable(i) and i.due_date and i.due_date < today and (visible is None or visible(i))]
    if not vencidas:
        return {"estado": "al_dia", "vencido": None}
    moneda = vencidas[0].currency
    monto = sum((d(i.balance) for i in vencidas if i.currency == moneda), Decimal(0))
    dias = max((today - i.due_date).days for i in vencidas)
    return {"estado": "vencida", "vencido": {"monto": monto.quantize(Decimal("0.01")), "moneda": moneda, "dias": dias}}


def fee_by_month(db: Session, company: AllyCompany, desde: date | None = None, hasta: date | None = None) -> dict[str, Decimal]:
    """Mensualidad facturada por mes ("YYYY-MM"), sin IVA y en colones con el tipo de cambio de cada factura. Solo las
    emitidas (is_emitted): el mismo criterio que /aliado/suscripcion y /aliado/facturas, para que el "neto" de
    /aliado/ganancias nunca descuente un borrador que el aliado no ve."""
    out: dict[str, Decimal] = {}
    for inv in plan_invoices(db, company, limit=1000):
        if not is_emitted(inv):
            continue
        if (desde and inv.issue_date < desde) or (hasta and inv.issue_date > hasta):
            continue
        monto = net_sale(inv) * (d(inv.fx_sell) if inv.currency != "CRC" else Decimal(1))
        k = inv.issue_date.strftime("%Y-%m")
        out[k] = (out.get(k, Decimal(0)) + monto).quantize(Decimal("0.01"))
    return out
