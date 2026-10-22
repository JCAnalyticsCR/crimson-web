"""Oportunidades que propone un aliado desde su portal (reunion 21/10, item 16; pregunta abierta 8).

El aliado las crea con POST /aliado/oportunidades (routers/ally_portal.py): nacen sin responsable, con su
participacion visible y, por defecto, "pendiente" de aprobacion. Aqui Crimson (quien ve todo el embudo) las aprueba
(puede asignar el responsable en el mismo paso) o las rechaza con un motivo que el aliado ve. Una rechazada se puede
aprobar despues (vuelve a abrirse). Al aliado que la propuso
le llega un correo (de usted) con la decision. Se monta desde fieldwork.py, como el archivo y la papelera, para no tocar
main.py.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import AllyCompany, Opportunity, TenantUser, User
from ..services.documents import audit

router = APIRouter(tags=["oportunidades"])

LABEL = {"pendiente": "Pendiente de aprobación de Crimson", "aprobada": "Aprobada", "rechazada": "Rechazada"}


def _opp(db: Session, oid: int, p: Principal) -> Opportunity:
    from .pipeline import _own

    return _own(db, oid, p)


def _proposal_out(db: Session, o: Opportunity) -> dict:
    if not o.ally_proposed_by:
        return {"proposal": None}
    c = db.get(AllyCompany, o.ally_proposed_by)
    autor = db.get(User, o.created_by) if o.created_by else None
    return {
        "proposal": {
            "status": o.ally_approval,
            "label": LABEL.get(o.ally_approval or "", o.ally_approval),
            "company": c.name if c else "Empresa aliada",
            "company_id": o.ally_proposed_by,
            "by": autor.full_name if autor else None,
            "created_at": o.created_at,
            "lost_reason": o.lost_reason if o.ally_approval == "rechazada" else None,
        }
    }


@router.get("/opportunities/{oid}/ally-proposal")
def proposal(oid: int, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """Si la oportunidad la propuso un aliado y en que quedo. {"proposal": null} si es de Crimson."""
    return _proposal_out(db, _opp(db, oid, p))


class ApproveIn(BaseModel):
    owner_id: int | None = None  # responsable de Crimson (opcional; se puede asignar despues)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def _decide_guard(db: Session, oid: int, p: Principal, allowed=("pendiente",)) -> Opportunity:
    if not p.can("crm_pipeline", "ver_todo"):
        raise HTTPException(403, "Aprobar oportunidades de aliados es de quien ve todo el embudo")
    o = _opp(db, oid, p)
    if not o.ally_proposed_by:
        raise HTTPException(409, "Esta oportunidad no la propuso un aliado")
    if o.ally_approval not in allowed:
        raise HTTPException(409, f"Ya está resuelta ({LABEL.get(o.ally_approval or '', o.ally_approval)})")
    return o


def _avisar_aliado(db: Session, p: Principal, o: Opportunity, aprobada: bool, motivo: str | None = None) -> None:
    """Correo (de usted) a quien la propuso, si sigue activo en su empresa aliada. Sin montos."""
    from ..services.mail import member_inbox, queue_email

    m = db.scalar(
        select(TenantUser).where(
            TenantUser.tenant_id == p.tenant.id,
            TenantUser.user_id == o.created_by,
            TenantUser.ally_company_id == o.ally_proposed_by,
            TenantUser.active,
        )
    )
    correo = member_inbox(m) if m and m.user and m.user.active else ""
    if not correo:
        return
    esc = html.escape
    base = cfg.public_base_url.rstrip("/")
    if aprobada:
        asunto = f"Crimson aprobó su oportunidad {o.number}"
        cuerpo = f"<p>{esc(p.tenant.name)} aprobó la oportunidad <b>{esc(o.number)} · {esc(o.title)}</b> que usted propuso. Ya puede hacer el levantamiento desde su portal.</p>"
    else:
        asunto = f"Crimson no aprobó su oportunidad {o.number}"
        cuerpo = f"<p>{esc(p.tenant.name)} no aprobó la oportunidad <b>{esc(o.number)} · {esc(o.title)}</b> que usted propuso.</p><p>Motivo: {esc(motivo or '—')}</p>"
    queue_email(db, p.tenant, correo, asunto, cuerpo + f'<p><a href="{base}/oportunidades/{o.id}">Abrir el portal de aliados</a></p>', "opportunity", o.id)


@router.post("/opportunities/{oid}/ally-proposal/approve")
def approve(oid: int, data: ApproveIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    from .pipeline import _log, _out, _reassign

    # una rechazada se puede aprobar despues (Crimson cambio de opinion): si no, no habria camino para desbloquearla,
    # porque el portal no deja levantar sobre una rechazada aunque la reabran desde el embudo
    o = _decide_guard(db, oid, p, ("pendiente", "rechazada"))
    if o.ally_approval == "rechazada" and o.status == "perdida":
        o.status, o.lost_reason = "nuevo", None  # el rechazo la habia cerrado: vuelve a abrirse (closed_at lo borra el listener)
    o.ally_approval = "aprobada"
    if data.owner_id and data.owner_id != o.owner_id:
        _reassign(db, p, o, data.owner_id)
    _log(o, p.user.full_name, "Aprobó la oportunidad propuesta por el aliado")
    audit(db, p.tenant.id, p.user.id, "ally_approve", "opportunity", o.id, {"ally_company_id": o.ally_proposed_by}, ip=p.ip)
    _avisar_aliado(db, p, o, True)
    db.commit()
    return {**_out(db, o), **_proposal_out(db, o)}


@router.post("/opportunities/{oid}/ally-proposal/reject")
def reject(oid: int, data: RejectIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    from .pipeline import _log, _out

    o = _decide_guard(db, oid, p)
    o.ally_approval = "rechazada"
    o.status = "perdida"
    o.lost_reason = f"No aprobada: {data.reason}"[:200]
    _log(o, p.user.full_name, f"Rechazó la oportunidad propuesta por el aliado: {data.reason}")
    audit(db, p.tenant.id, p.user.id, "ally_reject", "opportunity", o.id, {"ally_company_id": o.ally_proposed_by, "motivo": data.reason}, ip=p.ip)
    _avisar_aliado(db, p, o, False, data.reason)
    db.commit()
    return {**_out(db, o), **_proposal_out(db, o)}
