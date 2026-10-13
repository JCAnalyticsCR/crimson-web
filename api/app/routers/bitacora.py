"""Bitacora (Ajustes -> Bitacora, solo administracion): quien hizo que, cuando, desde donde y que cambio.

Solo lectura: la tabla audit_log no se edita ni se borra (en Postgres lo impide un trigger, ver la migracion p1seg).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import AuditLog, TenantUser, User
from ..services.documents import CR

router = APIRouter(prefix="/settings/audit", tags=["ajustes"])

ADMIN = require("settings", "configurar")


def _row(a: AuditLog, names: dict[int, str]) -> dict:
    return {
        "id": a.id,
        "at": a.at if a.at is None or a.at.tzinfo else a.at.replace(tzinfo=UTC),  # SQLite la devuelve sin zona
        "user_id": a.user_id,
        "user": names.get(a.user_id) if a.user_id else None,
        "ip": a.ip,
        "action": a.action,
        "entity": a.entity,
        "entity_id": a.entity_id,
        "diff": a.diff,
        "before": a.before,
        "after": a.after,
        "reason": a.reason,
        "version": a.version,
        "company": a.company,
    }


def _local_start(d: date) -> datetime:
    """Medianoche de Costa Rica (en UTC, como se guarda `at`): el filtro es por dia calendario de aqui."""
    return datetime.combine(d, time.min, tzinfo=CR).astimezone(UTC)


@router.get("")
def audit_list(
    entity: str | None = Query(None, max_length=40),
    entity_id: int | None = None,
    action: str | None = Query(None, max_length=40),
    user_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    company: str | None = Query(None, max_length=160),
    before_id: int | None = None,  # paginacion: lo anterior a este id
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(ADMIN),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).where(AuditLog.tenant_id == p.tenant.id)
    if entity:
        q = q.where(AuditLog.entity == entity)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == entity_id)
    if action:
        q = q.where(AuditLog.action == action)
    if user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    if from_:
        q = q.where(AuditLog.at >= _local_start(from_))
    if to:
        q = q.where(AuditLog.at < _local_start(to + timedelta(days=1)))
    if company:
        q = q.where(AuditLog.company.ilike(f"%{company}%"))
    if before_id:
        q = q.where(AuditLog.id < before_id)
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    ids = {a.user_id for a in rows if a.user_id}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return {"rows": [_row(a, names) for a in rows], "next_before_id": rows[-1].id if more and rows else None}


@router.get("/facets")
def audit_facets(p: Principal = Depends(ADMIN), db: Session = Depends(get_db)):
    """Opciones de los filtros: entidades y acciones que existen en la bitacora de esta empresa y sus usuarios."""
    ents = sorted(x for x in db.scalars(select(distinct(AuditLog.entity)).where(AuditLog.tenant_id == p.tenant.id)) if x)
    acts = sorted(x for x in db.scalars(select(distinct(AuditLog.action)).where(AuditLog.tenant_id == p.tenant.id)) if x)
    users = db.scalars(select(User).join(TenantUser, TenantUser.user_id == User.id).where(TenantUser.tenant_id == p.tenant.id).order_by(User.full_name)).all()
    return {"entities": ents, "actions": acts, "users": [{"id": u.id, "name": u.full_name, "email": u.email} for u in users]}
