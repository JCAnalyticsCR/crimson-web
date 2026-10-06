"""Archivo y papelera de levantamientos, proyectos, oportunidades y ordenes de trabajo.

Dos pasos, los dos reversibles hasta la purga:
- Archivar: sale de las listas, contadores, embudo y reportes, pero se conserva para siempre.
- Papelera ("proximos a borrar"): igual que archivado, y ademas el worker lo borra definitivamente despues de
  N dias (tenant.settings.archive_purge_days, 30 por defecto). Mientras tanto se restaura.

Lo fiscal NO entra aqui: facturas y cotizaciones tienen "anular" y la factura es un comprobante electronico
que Hacienda ya conoce; borrarlo romperia el consecutivo y la trazabilidad que exige la ley. Por la misma
razon no se manda a la papelera nada que cuelgue de una factura (proyecto facturado) ni de una cotizacion
convertida en factura.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, delete, select, update
from sqlalchemy.orm import Session

from ..models import (
    CustomerAsset,
    MaintenanceContract,
    Opportunity,
    Project,
    PurchaseRequest,
    Quote,
    SupportTicket,
    Survey,
    Tenant,
    User,
    WorkOrder,
)
from .documents import audit

DEFAULT_PURGE_DAYS = 30
OPEN_ORDER = ("asignada", "en_sitio", "en_proceso")
IN_PROGRESS_ORDER = ("en_sitio", "en_proceso")

# tipo -> (modelo, modulo de permisos, nombre para mensajes)
KINDS: dict[str, tuple[type, str, str]] = {
    "survey": (Survey, "field", "Levantamiento"),
    "project": (Project, "projects", "Proyecto"),
    "opportunity": (Opportunity, "crm_pipeline", "Oportunidad"),
    "work_order": (WorkOrder, "field", "Orden de trabajo"),
}
# Tipos que se piden a veces pero que la regla fiscal excluye: se responde 409 explicando por que
FISCAL = {
    "invoice": "Las facturas son comprobantes electrónicos: no se archivan ni se borran. Si hubo un error, anulala (nota de crédito).",
    "quote": "Las cotizaciones no van a la papelera: anulala. Si ya se convirtió en factura, la factura es un comprobante fiscal y se conserva.",
}


def live(model):
    """Filtro de 'activo': ni archivado ni en papelera. Todas las listas y contadores lo usan."""
    return and_(model.archived_at.is_(None), model.trashed_at.is_(None))


def is_live(obj) -> bool:
    return obj.archived_at is None and obj.trashed_at is None


def purge_days(tenant: Tenant) -> int:
    try:
        n = int((tenant.settings or {}).get("archive_purge_days", DEFAULT_PURGE_DAYS))
    except (TypeError, ValueError):
        n = DEFAULT_PURGE_DAYS
    return max(n, 1)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def purge_at(obj, tenant: Tenant) -> datetime | None:
    return _aware(obj.trashed_at) + timedelta(days=purge_days(tenant)) if obj.trashed_at else None


def title_of(obj) -> str:
    return getattr(obj, "title", None) or getattr(obj, "name", None) or getattr(obj, "site", None) or ""


def resolve(kind: str) -> tuple[type, str, str]:
    if kind in FISCAL:
        raise HTTPException(409, FISCAL[kind])
    if kind not in KINDS:
        raise HTTPException(404, "Tipo desconocido")
    return KINDS[kind]


# ---------- Reglas de integridad ----------
def _quote_blocks(db: Session, quote_id: int | None) -> str | None:
    if not quote_id:
        return None
    q = db.get(Quote, quote_id)
    if q and q.status == "convertida":
        return f"su cotización {q.number} ya se aprobó y se convirtió en factura"
    return None


def blockers(db: Session, kind: str, obj, to: str) -> list[str]:
    """Dependencias vivas que impiden archivar (to='archive') o mandar a la papelera (to='trash').
    Archivar es mas permisivo: solo se frena lo que esta en operacion (trabajo en campo abierto)."""
    out: list[str] = []
    if kind == "project":
        orders = [o for o in obj.orders if o.trashed_at is None]
        abiertas = [o.number for o in orders if o.status in OPEN_ORDER]
        if abiertas:
            out.append(f"tiene órdenes de trabajo abiertas ({', '.join(abiertas[:5])})")
        if to == "trash":
            if obj.invoice_id or obj.status == "facturado":
                out.append("ya está facturado (la factura es un comprobante fiscal)")
            if any(o.stock_applied for o in orders):
                out.append("sus órdenes ya descontaron inventario")
            if db.scalar(select(MaintenanceContract.id).where(MaintenanceContract.project_id == obj.id, MaintenanceContract.active)):
                out.append("tiene un contrato de mantenimiento activo")
    elif kind == "work_order":
        if obj.status in IN_PROGRESS_ORDER:
            out.append("el técnico la está ejecutando (en sitio / en proceso)")
        if to == "trash" and obj.stock_applied:
            out.append("ya descontó inventario")
    elif kind == "opportunity" and to == "trash":
        why = _quote_blocks(db, obj.quote_id)
        if why:
            out.append(why)
        if obj.project_id:
            pr = db.get(Project, obj.project_id)
            if pr and pr.trashed_at is None:
                out.append(f"tiene el proyecto {pr.number}")
    elif kind == "survey" and to == "trash":
        why = _quote_blocks(db, obj.quote_id)
        if why:
            out.append(why)
        pr = db.scalar(select(Project).where(Project.survey_id == obj.id, Project.trashed_at.is_(None)))
        if pr:
            out.append(f"el proyecto {pr.number} salió de este levantamiento")
    return out


def check(db: Session, kind: str, obj, to: str) -> None:
    b = blockers(db, kind, obj, to)
    if b:
        accion = "archivar" if to == "archive" else "mandar a la papelera"
        raise HTTPException(409, f"No se puede {accion} {obj.number}: " + "; ".join(b) + ".")


# ---------- Acciones ----------
def archive(db: Session, kind: str, obj, user_id: int, ip=None) -> None:
    if obj.trashed_at:
        raise HTTPException(409, "Está en la papelera: restauralo primero")
    if obj.archived_at:
        return
    check(db, kind, obj, "archive")
    obj.archived_at, obj.archived_by = datetime.now(UTC), user_id
    audit(db, obj.tenant_id, user_id, "archive", kind, obj.id, {"number": obj.number}, ip=ip)


def trash(db: Session, kind: str, obj, user_id: int, ip=None) -> None:
    if obj.trashed_at:
        return
    check(db, kind, obj, "trash")
    obj.trashed_at, obj.trashed_by = datetime.now(UTC), user_id
    audit(db, obj.tenant_id, user_id, "trash", kind, obj.id, {"number": obj.number}, ip=ip)


def restore(db: Session, kind: str, obj, user_id: int, ip=None) -> None:
    """Vuelve a las listas activas (sale del archivo y de la papelera)."""
    was = "papelera" if obj.trashed_at else ("archivo" if obj.archived_at else None)
    if not was:
        return
    obj.archived_at = obj.archived_by = obj.trashed_at = obj.trashed_by = None
    audit(db, obj.tenant_id, user_id, "restore", kind, obj.id, {"number": obj.number, "desde": was}, ip=ip)


def _detach(db: Session, kind: str, obj) -> None:
    """Suelta las referencias de registros que NO son suyos (se conservan, solo pierden el enlace)."""
    if kind == "survey":
        db.execute(update(Project).where(Project.survey_id == obj.id).values(survey_id=None))
    elif kind == "opportunity":
        from ..models import AllyCostRequest, AllyParticipation

        # aliados y solicitudes de costo son de la oportunidad: se van con ella (tambien en SQLite sin cascada)
        db.execute(delete(AllyCostRequest).where(AllyCostRequest.opportunity_id == obj.id))
        db.execute(delete(AllyParticipation).where(AllyParticipation.opportunity_id == obj.id))
        db.execute(update(Survey).where(Survey.opportunity_id == obj.id).values(opportunity_id=None))
        db.execute(update(Project).where(Project.opportunity_id == obj.id).values(opportunity_id=None))
    elif kind == "project":
        order_ids = [o.id for o in obj.orders]
        db.execute(update(Opportunity).where(Opportunity.project_id == obj.id).values(project_id=None))
        from ..models import AllyParticipation

        db.execute(update(AllyParticipation).where(AllyParticipation.project_id == obj.id).values(project_id=None))
        db.execute(update(CustomerAsset).where(CustomerAsset.project_id == obj.id).values(project_id=None))
        db.execute(update(PurchaseRequest).where(PurchaseRequest.project_id == obj.id).values(project_id=None))
        db.execute(update(SupportTicket).where(SupportTicket.project_id == obj.id).values(project_id=None))
        db.execute(update(MaintenanceContract).where(MaintenanceContract.project_id == obj.id).values(project_id=None))
        if order_ids:
            db.execute(update(CustomerAsset).where(CustomerAsset.work_order_id.in_(order_ids)).values(work_order_id=None))
            db.execute(update(SupportTicket).where(SupportTicket.work_order_id.in_(order_ids)).values(work_order_id=None))
    elif kind == "work_order":
        db.execute(update(CustomerAsset).where(CustomerAsset.work_order_id == obj.id).values(work_order_id=None))
        db.execute(update(SupportTicket).where(SupportTicket.work_order_id == obj.id).values(work_order_id=None))


def purge(db: Session, kind: str, obj, user_id: int | None, ip=None) -> None:
    """Borrado definitivo. Solo desde la papelera. Borra en cascada lo suyo (puntos y materiales del
    levantamiento, ordenes del proyecto con sus materiales) y deja en la auditoria que se borro, quien lo
    mando a la papelera y cuando, y quien lo purgo (None = el worker por vencimiento)."""
    if not obj.trashed_at:
        raise HTTPException(409, "Solo se borra definitivamente lo que está en la papelera")
    check(db, kind, obj, "trash")  # algo pudo engancharse mientras estaba en la papelera
    who = db.get(User, obj.trashed_by) if obj.trashed_by else None
    snapshot = {
        "number": obj.number,
        "title": title_of(obj),
        "status": obj.status,
        "trashed_at": _aware(obj.trashed_at).isoformat(),
        "trashed_by": obj.trashed_by,
        "trashed_by_name": who.full_name if who else None,
        "archived_at": _aware(obj.archived_at).isoformat() if obj.archived_at else None,
        "purged_by": user_id,
        "auto": user_id is None,
    }
    if kind == "project":
        snapshot["work_orders"] = [o.number for o in obj.orders]
    if kind == "survey":
        snapshot["points"] = len(obj.points)
        snapshot["items"] = len(obj.items)
    audit(db, obj.tenant_id, user_id, "purge", kind, obj.id, snapshot, ip=ip)
    _detach(db, kind, obj)
    db.delete(obj)
    db.flush()


def purge_expired(db: Session, now: datetime | None = None) -> int:
    """Tarea del worker: purga lo que cumplio los N dias en la papelera, empresa por empresa.
    Lo que quedo bloqueado por una dependencia nueva se salta (queda en la papelera, visible)."""
    now = now or datetime.now(UTC)
    n = 0
    for tenant in db.scalars(select(Tenant)):
        limit = now - timedelta(days=purge_days(tenant))
        # primero lo que depende (ordenes) y al final los proyectos, para no chocar con cascadas
        for kind in ("work_order", "survey", "opportunity", "project"):
            model = KINDS[kind][0]
            rows = db.scalars(select(model).where(model.tenant_id == tenant.id, model.trashed_at.is_not(None), model.trashed_at <= limit)).all()
            for obj in rows:
                try:
                    with db.begin_nested():
                        purge(db, kind, obj, None)
                    n += 1
                except HTTPException:
                    continue
    db.commit()
    return n


def listing(db: Session, tenant: Tenant, kinds: list[str], state: str) -> list[dict]:
    now = datetime.now(UTC)
    out = []
    users: dict[int, str | None] = {}

    def name(uid):
        if uid is None:
            return None
        if uid not in users:
            u = db.get(User, uid)
            users[uid] = u.full_name if u else None
        return users[uid]

    for kind in kinds:
        model, _, label = KINDS[kind]
        cond = model.trashed_at.is_not(None) if state == "papelera" else and_(model.archived_at.is_not(None), model.trashed_at.is_(None))
        for obj in db.scalars(select(model).where(model.tenant_id == tenant.id, cond).limit(300)):
            pa = purge_at(obj, tenant)
            out.append(
                {
                    "kind": kind,
                    "kind_label": label,
                    "id": obj.id,
                    "number": obj.number,
                    "title": title_of(obj),
                    "status": obj.status,
                    "archived_at": obj.archived_at,
                    "archived_by": name(obj.archived_by),
                    "trashed_at": obj.trashed_at,
                    "trashed_by": name(obj.trashed_by),
                    "purge_at": pa,
                    "days_left": max(0, math.ceil((pa - now).total_seconds() / 86400)) if pa else None,
                }
            )
    out.sort(key=lambda r: _aware(r["trashed_at"] or r["archived_at"]), reverse=True)
    return out
