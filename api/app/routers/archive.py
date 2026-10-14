"""Archivo y papelera (ver services/archive.py para las reglas).

Se monta dentro del router de campo (fieldwork.router.include_router) para no tocar main.py mientras
otro frente trabaja ahi; las rutas son las mismas que si se montara aparte.

Permisos: archivar, papelera y restaurar = "editar" del modulo de cada cosa (solicitudes de compra: "crear"; tickets
y contratos: ademas "ver_todo"); borrar ya (purga manual) = solo admin. GET .../check responde, antes de confirmar
el basurero de una lista, si se puede y si no, por que y la alternativa (archivar o desactivar).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, get_principal
from ..services import archive as svc

router = APIRouter(tags=["archivo"])


def _can(p: Principal, kind: str, action: str) -> bool:
    """Permiso del modulo de ese tipo (algunos tipos piden otra accion o, ademas, ver todo el modulo)."""
    _, module, _ = svc.KINDS[kind]
    if action == "editar":
        action = svc.ACTION.get(kind, "editar")
    if kind in svc.NEEDS_ALL and not p.can(module, "ver_todo"):
        return False
    return p.can(module, action)


def _load(db: Session, p: Principal, kind: str, oid: int, action: str):
    model, module, label = svc.resolve(kind)
    if not _can(p, kind, action):
        raise HTTPException(403, "Sin permiso")
    obj = db.get(model, oid)
    if not obj or obj.tenant_id != p.tenant.id:
        raise HTTPException(404, f"{label} no encontrado")
    # el tecnico (sin field.ver_todo) solo toca lo suyo, igual que en las pantallas de campo
    if module == "field" and not p.sees_field_all:
        own = obj.technician_id == p.user.id or p.user.id in (getattr(obj, "helpers", None) or [])
        if not own:
            raise HTTPException(404, f"{label} no encontrado")
    return obj


def _state(obj) -> dict:
    return {"id": obj.id, "number": svc.ref_of(obj), "archived_at": obj.archived_at, "trashed_at": obj.trashed_at}


@router.get("/archive")
def archive_list(
    state: Literal["archivado", "papelera"] = "archivado",
    kind: str | None = Query(None),
    p: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Vista "Archivo": pestañas Archivados / Próximos a borrar. Solo los tipos cuyo modulo el usuario puede ver."""
    kinds = [k for k in svc.KINDS if _can(p, k, "ver") and (not kind or k == kind)]
    if kind and kind not in svc.KINDS:
        svc.resolve(kind)
    if not kinds:
        raise HTTPException(403, "Sin permiso")
    rows = svc.listing(db, p.tenant, kinds, state)
    if not p.sees_field_all:
        # un tecnico no ve levantamientos ni ordenes ajenas en el archivo
        from ..models import Survey, WorkOrder

        def mine(r):
            if r["kind"] == "survey":
                return db.get(Survey, r["id"]).technician_id == p.user.id
            if r["kind"] == "work_order":
                o = db.get(WorkOrder, r["id"])
                return o.technician_id == p.user.id or p.user.id in (o.helpers or [])
            return True

        rows = [r for r in rows if mine(r)]
    return {"purge_days": svc.purge_days(p.tenant), "rows": rows, "is_admin": p.role == "admin"}


@router.get("/archive/{kind}/{oid}/check")
def do_check(kind: str, oid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Antes de confirmar el basurero: ¿puede ir a la papelera? si no, por que y que se puede hacer en su lugar."""
    obj = _load(db, p, kind, oid, "editar")
    return svc.preflight(db, kind, obj, p.tenant)


@router.post("/archive/{kind}/{oid}/archive")
def do_archive(kind: str, oid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    obj = _load(db, p, kind, oid, "editar")
    svc.archive(db, kind, obj, p.user.id, p.ip)
    db.commit()
    return _state(obj)


@router.post("/archive/{kind}/{oid}/trash")
def do_trash(kind: str, oid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    obj = _load(db, p, kind, oid, "editar")
    svc.trash(db, kind, obj, p.user.id, p.ip)
    db.commit()
    out = _state(obj)
    out["purge_at"] = svc.purge_at(obj, p.tenant)
    return out


@router.post("/archive/{kind}/{oid}/restore")
def do_restore(kind: str, oid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    obj = _load(db, p, kind, oid, "editar")
    svc.restore(db, kind, obj, p.user.id, p.ip)
    db.commit()
    return _state(obj)


@router.delete("/archive/{kind}/{oid}")
def do_purge(kind: str, oid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Borrar ya, sin esperar los N dias. Solo el administrador y solo desde la papelera."""
    if p.role != "admin":
        raise HTTPException(403, "Solo el administrador borra definitivamente")
    obj = _load(db, p, kind, oid, "editar")
    svc.purge(db, kind, obj, p.user.id, p.ip)
    db.commit()
    return {"ok": True}
