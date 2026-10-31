"""Alcances y responsables (revision 2, puntos 3 y A3): las mismas partidas desde el levantamiento, la oportunidad y el
proyecto (services/scope_items.py).

- /opportunities/{oid}/scope[-items]: mismos permisos y alcance que el embudo (crm_pipeline; el vendedor, las suyas).
- /surveys/{sid}/scope[-items]: quien ve el levantamiento lo lee; quien lo edita (field.editar) define el alcance. Con
  oportunidad ligada son las partidas de ESA oportunidad; sin oportunidad, las del levantamiento.
- /projects/{pid}/scope: el resumen del proyecto (solo lectura): el de su oportunidad.
Sin montos. Cada cambio con oportunidad deja una linea en su bitacora.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Opportunity, Project, ScopeItem, Survey
from ..models.allies import PARTY_KINDS, SCOPE_KINDS, SCOPE_STATES
from ..services import scope_items as svc
from ..services.documents import audit

router = APIRouter(tags=["alcances"])

_KINDS = "^(" + "|".join(SCOPE_KINDS) + ")$"
_PARTY = "^(" + "|".join(PARTY_KINDS) + ")$"
_STATES = "^(" + "|".join(SCOPE_STATES) + ")$"


class ScopeItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field("otro", pattern=_KINDS)
    description: str = Field(min_length=2, max_length=400)  # que se necesita
    quantity: Decimal | None = Field(None, ge=0, le=Decimal("9999999999"))
    unit: str | None = Field(None, max_length=12)
    supplier_kind: str = Field("por_asignar", pattern=_PARTY)
    supplier_participation_id: int | None = None
    executor_kind: str = Field("por_asignar", pattern=_PARTY)
    executor_participation_id: int | None = None
    status: str | None = Field(None, pattern=_STATES)  # sin dato: por confirmar (o como estaba)
    shared: bool = True
    notes: str | None = Field(None, max_length=2000)
    position: int | None = Field(None, ge=0, le=10000)


def _label(it: ScopeItem) -> str:
    return f"{svc.KIND_LABEL.get(it.kind, it.kind)} · {it.description[:60]}"


def _apply(db: Session, p: Principal, it: ScopeItem, data: ScopeItemIn, parts: dict) -> list[str]:
    cambios = []
    sk, sp = svc.check_party(data.supplier_kind, data.supplier_participation_id, parts)
    ek, ep = svc.check_party(data.executor_kind, data.executor_participation_id, parts)
    nuevo_resp = (sk, sp, ek, ep) != (it.supplier_kind, it.supplier_participation_id, it.executor_kind, it.executor_participation_id)
    if it.id is not None and nuevo_resp:
        sup = svc.party(sk, sp, parts)["name"]
        exe = svc.party(ek, ep, parts)["name"]
        cambios.append(f"suministra {sup}, ejecuta {exe}")
    for k in ("kind", "description", "unit", "notes"):
        v = getattr(data, k)
        v = (v.strip() or None) if isinstance(v, str) else v
        if v != getattr(it, k):
            setattr(it, k, v)
            if it.id is not None and k in ("kind", "description"):
                cambios.append("qué se necesita")
    q = data.quantity
    if (q is None) != (it.quantity is None) or (q is not None and it.quantity is not None and Decimal(str(q)) != Decimal(str(it.quantity))):
        it.quantity = q
        if it.id is not None:
            cambios.append("cantidad")
    it.supplier_kind, it.supplier_participation_id, it.executor_kind, it.executor_participation_id = sk, sp, ek, ep
    if data.shared != bool(it.shared) and it.id is not None:
        cambios.append("compartida con los aliados" if data.shared else "solo la ve su responsable")
    it.shared = data.shared
    if data.position is not None:
        it.position = data.position
    estado = data.status
    if nuevo_resp and it.id is not None and (estado is None or estado == it.status) and it.status != "por_confirmar":
        estado = "por_confirmar"  # otro responsable: le toca confirmarlo a el
    if estado is not None and estado != it.status:
        if it.id is not None:
            cambios.append(f"estado {svc.STATE_LABEL[it.status].lower()} → {svc.STATE_LABEL[estado].lower()}")
        it.status = estado
        if estado == "confirmado":
            svc.touch_confirmed(it, p.user.id)
    it.updated_by = p.user.id
    return [c for i, c in enumerate(cambios) if c not in cambios[:i]]


def _log_opp(db: Session, opportunity_id: int | None, p: Principal, text: str) -> None:
    if opportunity_id is None:
        return
    from .pipeline import _log

    o = db.get(Opportunity, opportunity_id)
    if o is not None:
        _log(o, p.user.full_name, text)


def _create(db: Session, p: Principal, oid: int | None, sid: int | None, data: ScopeItemIn) -> None:
    parts = svc.participations(db, p.tenant.id, oid)
    pos = data.position
    if pos is None:
        pos = (db.scalar(select(func.max(ScopeItem.position)).where(*svc.items_where(p.tenant.id, oid, sid))) or 0) + 1
    it = ScopeItem(tenant_id=p.tenant.id, opportunity_id=oid, survey_id=sid, status="por_confirmar", position=pos, created_by=p.user.id, description="")
    _apply(db, p, it, data, parts)
    if it.status == "confirmado" and it.confirmed_at is None:
        svc.touch_confirmed(it, p.user.id)
    db.add(it)
    db.flush()
    sup, exe = (
        svc.party(it.supplier_kind, it.supplier_participation_id, parts)["name"],
        svc.party(it.executor_kind, it.executor_participation_id, parts)["name"],
    )
    _log_opp(db, oid, p, f"Alcance «{_label(it)}»: suministra {sup}, ejecuta {exe}")
    audit(db, p.tenant.id, p.user.id, "create", "scope_item", it.id, {"opportunity_id": oid, "survey_id": sid}, ip=p.ip)


def _item(db: Session, p: Principal, iid: int, oid: int | None, sid: int | None) -> ScopeItem:
    it = db.get(ScopeItem, iid)
    if it is None or it.tenant_id != p.tenant.id:
        raise HTTPException(404, "Partida no encontrada")
    ok = it.opportunity_id == oid if oid is not None else (it.opportunity_id is None and it.survey_id == sid)
    if not ok:
        raise HTTPException(404, "Partida no encontrada")
    return it


def _update(db: Session, p: Principal, iid: int, oid: int | None, sid: int | None, data: ScopeItemIn) -> None:
    it = _item(db, p, iid, oid, sid)
    cambios = _apply(db, p, it, data, svc.participations(db, p.tenant.id, oid))
    if cambios:
        _log_opp(db, oid, p, f"Alcance «{_label(it)}»: {', '.join(cambios)}")
        audit(db, p.tenant.id, p.user.id, "update", "scope_item", it.id, {"cambios": cambios}, ip=p.ip)


def _delete(db: Session, p: Principal, iid: int, oid: int | None, sid: int | None) -> None:
    it = _item(db, p, iid, oid, sid)
    _log_opp(db, oid, p, f"Alcance «{_label(it)}» quitado")
    audit(db, p.tenant.id, p.user.id, "delete", "scope_item", it.id, {"opportunity_id": oid, "survey_id": sid}, ip=p.ip)
    db.delete(it)


# ---------- desde la oportunidad ----------
def _opp(db: Session, oid: int, p: Principal) -> Opportunity:
    from .pipeline import _own

    return _own(db, oid, p)


@router.get("/opportunities/{oid}/scope")
def opp_scope(oid: int, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    o = _opp(db, oid, p)
    return svc.scope_out(db, p.tenant.id, o.id, None)


@router.post("/opportunities/{oid}/scope-items", status_code=201)
def opp_scope_add(oid: int, data: ScopeItemIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _opp(db, oid, p)
    _create(db, p, o.id, None, data)
    db.commit()
    return svc.scope_out(db, p.tenant.id, o.id, None)


@router.put("/opportunities/{oid}/scope-items/{iid}")
def opp_scope_put(oid: int, iid: int, data: ScopeItemIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _opp(db, oid, p)
    _update(db, p, iid, o.id, None, data)
    db.commit()
    return svc.scope_out(db, p.tenant.id, o.id, None)


@router.delete("/opportunities/{oid}/scope-items/{iid}")
def opp_scope_del(oid: int, iid: int, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _opp(db, oid, p)
    _delete(db, p, iid, o.id, None)
    db.commit()
    return svc.scope_out(db, p.tenant.id, o.id, None)


# ---------- desde el levantamiento ----------
def _survey(db: Session, sid: int, p: Principal) -> Survey:
    from .fieldwork import _survey as get_survey

    return get_survey(db, sid, p)


def _survey_container(s: Survey) -> tuple[int | None, int | None]:
    return svc.container_of(survey=s)


@router.get("/surveys/{sid}/scope")
def survey_scope(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    oid, ssid = _survey_container(s)
    out = svc.scope_out(db, p.tenant.id, oid, ssid)
    out["can_edit"] = p.can("field", "editar")
    return out


@router.post("/surveys/{sid}/scope-items", status_code=201)
def survey_scope_add(sid: int, data: ScopeItemIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    oid, ssid = _survey_container(s)
    _create(db, p, oid, ssid if oid is None else s.id, data)
    db.commit()
    out = svc.scope_out(db, p.tenant.id, oid, ssid)
    out["can_edit"] = True
    return out


@router.put("/surveys/{sid}/scope-items/{iid}")
def survey_scope_put(sid: int, iid: int, data: ScopeItemIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    oid, ssid = _survey_container(s)
    _update(db, p, iid, oid, ssid, data)
    db.commit()
    out = svc.scope_out(db, p.tenant.id, oid, ssid)
    out["can_edit"] = True
    return out


@router.delete("/surveys/{sid}/scope-items/{iid}")
def survey_scope_del(sid: int, iid: int, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    oid, ssid = _survey_container(s)
    _delete(db, p, iid, oid, ssid)
    db.commit()
    out = svc.scope_out(db, p.tenant.id, oid, ssid)
    out["can_edit"] = True
    return out


# ---------- desde el proyecto (solo lectura: se edita en la oportunidad o el levantamiento) ----------
@router.get("/projects/{pid}/scope")
def project_scope(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    from .projects import _project

    pr: Project = _project(db, pid, p)
    oid = pr.opportunity_id
    if oid is None:
        o = db.scalar(select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.project_id == pr.id).limit(1))
        oid = o.id if o else None
    sid = None if oid is not None else pr.survey_id
    if oid is None and sid is None:
        return {
            "source": {"opportunity_id": None, "survey_id": None},
            "items": [],
            "summary": svc.summary([], {}),
            "options": [],
            "kinds": svc.KIND_LABEL,
            "states": svc.STATE_LABEL,
            "typical": [],
        }
    return svc.scope_out(db, p.tenant.id, oid, sid)
