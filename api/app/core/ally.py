"""La unica puerta del portal de aliados (/aliado). Ver routers/ally_portal.py y services/ally_scope.py.

- La empresa aliada sale de la membresia (TenantUser.ally_company_id) en el servidor, nunca del navegador.
- Empresa inactiva: 403 en todo. Suspendida: solo inicio, suscripcion y facturas (para que pueda pagar).
- "Ver como este aliado" (admin): cabecera X-View-As-Ally (+ X-View-As-User opcional). Mismo alcance y mismas listas
  blancas que el aliado real, con datos reales y SOLO LECTURA (cualquier escritura: 403). Otro rol que mande la
  cabecera no gana nada: se ignora y sigue la compuerta normal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AllyCompany, AuditLog, TenantUser, User
from .db import get_db
from .deps import ALLY_USER_ROLES, ROLE_PERMISSIONS, Principal, get_session_principal

SUSPENDED_ALLOWED = ("/aliado/inicio", "/aliado/suscripcion", "/aliado/facturas")
PREVIEW_HEADER = "X-View-As-Ally"  # id de la empresa aliada
PREVIEW_USER_HEADER = "X-View-As-User"  # opcional: id del usuario aliado a imitar
PREVIEW_POST_OK = ("/aliado/media/firmar",)  # firmar fotos no escribe nada
READ_METHODS = ("GET", "HEAD", "OPTIONS")
PREVIEW_HOURS = 8  # una activacion de "Ver como" dura una jornada; despues se vuelve a activar (y queda otra constancia)


@dataclass
class AllyCtx:
    p: Principal  # el del usuario aliado (o el sintetico de la vista previa)
    company: AllyCompany
    scope: object  # services.ally_scope.AllyScope (import diferido: el servicio importa modelos pesados)
    preview_by: int | None  # id del admin si es vista previa


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() else None
    except ValueError:
        return None


def _check_preview_audited(db: Session, p: Principal, company_id: int, user_id: int | None) -> None:
    """La vista previa queda SIEMPRE en la bitacora: solo vale si este admin la activo (POST /allies/{id}/preview, que
    deja AuditLog preview_ally) para esa empresa y esa persona en las ultimas PREVIEW_HOURS horas. Sin eso, mandar la
    cabecera a mano (o con un token filtrado) no abre nada."""
    desde = datetime.now(UTC) - timedelta(hours=PREVIEW_HOURS)
    logs = db.scalars(
        select(AuditLog.diff).where(
            AuditLog.tenant_id == p.tenant.id,
            AuditLog.user_id == p.user.id,
            AuditLog.action == "preview_ally",
            AuditLog.entity == "ally_company",
            AuditLog.entity_id == company_id,
            AuditLog.at >= desde,
        )
    ).all()
    if not any((x or {}).get("user_id") == user_id for x in logs):
        raise HTTPException(403, "Activá «Ver como este aliado» desde Administración → Aliados (la vista previa venció o no se activó).")


def _preview(request: Request, p: Principal, db: Session) -> Principal:
    """El admin mira el portal de una empresa aliada (y opcionalmente como una persona de ella). Solo lectura."""
    if request.method not in READ_METHODS and request.url.path not in PREVIEW_POST_OK:
        raise HTTPException(403, "Vista previa: solo lectura. Salí de la vista previa para hacer cambios.")
    cid = _int(request.headers.get(PREVIEW_HEADER))
    company = db.get(AllyCompany, cid) if cid else None
    if not company or company.tenant_id != p.tenant.id:
        raise HTTPException(404, "Empresa aliada no encontrada")
    uid = request.headers.get(PREVIEW_USER_HEADER)
    user, role = p.user, "aliado"
    if uid is not None and str(uid).strip():
        m = db.scalar(
            select(TenantUser).where(
                TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == _int(uid), TenantUser.ally_company_id == company.id, TenantUser.active
            )
        )
        u = db.get(User, m.user_id) if m else None
        if not m or not u or m.role_code not in ALLY_USER_ROLES:
            raise HTTPException(422, "Esa persona no es de esa empresa aliada")
        user, role = u, m.role_code
    _check_preview_audited(db, p, company.id, user.id if user is not p.user else None)
    return Principal(
        user=user,
        tenant=p.tenant,
        role=role,
        permissions=ROLE_PERMISSIONS[role],
        ip=p.ip,
        ally_company_id=company.id,
        preview_by=p.user.id,
    )


def get_ally(request: Request, p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)) -> AllyCtx:
    from ..services.ally_scope import AllyScope

    if p.role == "admin" and p.ally_company_id is None and request.headers.get(PREVIEW_HEADER):
        from .twofa import enforce

        enforce(p.user, p.tenant, p.role)  # la vista previa es del panel interno: misma compuerta de 2FA
        p = _preview(request, p, db)
        company = db.get(AllyCompany, p.ally_company_id)
        return AllyCtx(p, company, AllyScope(db, p, company), p.preview_by)
    if not p.is_ally or not p.ally_company_id or not p.can("aliado", "ver"):
        raise HTTPException(403, "Esta sección es del portal de aliados")
    company = db.get(AllyCompany, p.ally_company_id)
    if not company or company.tenant_id != p.tenant.id or not company.active:
        raise HTTPException(403, "Su empresa aliada no está activa. Escríbanos para revisarlo.")
    if company.suspended_at and not request.url.path.startswith(SUSPENDED_ALLOWED):
        raise HTTPException(403, f"El acceso de {company.name} está suspendido. {company.suspended_reason or 'Revise su suscripción o escríbanos.'}")
    return AllyCtx(p, company, AllyScope(db, p, company), None)


def need(action: str):
    """Depends(get_ally) + permiso del modulo "aliado" (403 si su rol no hace esto, p. ej. el tecnico y el catalogo)."""

    def _dep(ctx: AllyCtx = Depends(get_ally)) -> AllyCtx:
        if not ctx.p.can("aliado", action):
            raise HTTPException(403, "Su usuario no tiene acceso a esta sección")
        return ctx

    return _dep
