"""Alcances y responsables (revision 2, puntos 3 y A3): una sola fuente para el levantamiento, la oportunidad y su
proyecto.

Las partidas viven en la oportunidad (ScopeItem.opportunity_id). Un levantamiento sin oportunidad guarda las suyas con
survey_id y al ligarlo a una oportunidad pasan a ella (attach_survey). El proyecto lee las de su oportunidad (o las de
su levantamiento si no tiene). Sin montos: lo economico vive en las solicitudes de precio y los acuerdos.

Quien ve que:
- adentro, quien ve la oportunidad (o el levantamiento) ve todas; las cambia quien edita la oportunidad o el
  levantamiento (routers/scope_items.py).
- el aliado (ally_items) ve las partidas que le tocan (suministra o ejecuta una participacion SUYA visible) y las
  compartidas (shared, por defecto: punto 10 "aportes, responsables, alcances y avances"). De las de otro responsable
  ve el nombre de la empresa responsable y el estado, nunca contactos, solicitudes ni montos de esa empresa.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..models import AllyParticipation, Opportunity, ScopeItem, Survey, User
from ..models.allies import PARTY_KINDS, SCOPE_KINDS, SCOPE_STATES

KIND_LABEL = {
    "mano_obra": "Mano de obra",
    "viaticos": "Viáticos",
    "equipos": "Equipos",
    "materiales": "Materiales",
    "configuracion": "Configuración",
    "obra_civil": "Obra civil",
    "otro": "Otro",
}
STATE_LABEL = {"por_confirmar": "Por confirmar", "confirmado": "Confirmado", "en_curso": "En curso", "completado": "Completado"}
# lo que Andres puso de ejemplo en A3: con "Cargar alcances típicos" se crean asi (por asignar, por confirmar)
TYPICAL = (
    ("mano_obra", "Mano de obra para la instalación"),
    ("viaticos", "Traslado y alimentación"),
    ("equipos", "Equipos (cámaras, grabador, etc.)"),
    ("materiales", "Materiales de instalación"),
    ("obra_civil", "Canalización y soportes"),
)
assert set(KIND_LABEL) == set(SCOPE_KINDS) and set(STATE_LABEL) == set(SCOPE_STATES)


def container_of(survey: Survey | None = None, opportunity: Opportunity | None = None) -> tuple[int | None, int | None]:
    """(opportunity_id, survey_id) de donde viven las partidas: la oportunidad si la hay; si no, el levantamiento."""
    if opportunity is not None:
        return opportunity.id, None
    if survey is not None and survey.opportunity_id:
        return survey.opportunity_id, None
    if survey is not None:
        return None, survey.id
    raise HTTPException(422, "Sin oportunidad ni levantamiento")


def items_where(tenant_id: int, opportunity_id: int | None, survey_id: int | None) -> list:
    if opportunity_id is not None:
        return [ScopeItem.tenant_id == tenant_id, ScopeItem.opportunity_id == opportunity_id]
    return [ScopeItem.tenant_id == tenant_id, ScopeItem.opportunity_id.is_(None), ScopeItem.survey_id == survey_id]


def items_of(db: Session, tenant_id: int, opportunity_id: int | None, survey_id: int | None) -> list[ScopeItem]:
    return list(db.scalars(select(ScopeItem).where(*items_where(tenant_id, opportunity_id, survey_id)).order_by(ScopeItem.position, ScopeItem.id)))


def participations(db: Session, tenant_id: int, opportunity_id: int | None) -> dict[int, AllyParticipation]:
    if opportunity_id is None:
        return {}
    rows = db.scalars(select(AllyParticipation).where(AllyParticipation.tenant_id == tenant_id, AllyParticipation.opportunity_id == opportunity_id))
    return {a.id: a for a in rows}


def party(kind: str, pid: int | None, parts: dict[int, AllyParticipation], mask=None) -> dict:
    """{kind, participation_id, name}. Un "aliado" cuya participacion ya no existe se muestra por asignar. mask(pid)
    devuelve el nombre que puede ver quien mira (el portal del aliado)."""
    if kind == "crimson":
        return {"kind": "crimson", "participation_id": None, "name": "Crimson"}
    if kind == "aliado" and pid is not None and pid in parts:
        name = mask(pid) if mask else parts[pid].name
        return {"kind": "aliado", "participation_id": pid, "name": name}
    return {"kind": "por_asignar", "participation_id": None, "name": "Por asignar"}


def _name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return u.full_name if u else None


def item_out(db: Session, it: ScopeItem, parts: dict[int, AllyParticipation], mask=None, who=None) -> dict:
    return {
        "id": it.id,
        "kind": it.kind,
        "kind_label": KIND_LABEL.get(it.kind, it.kind),
        "description": it.description,
        "quantity": None if it.quantity is None else str(Decimal(str(it.quantity)).normalize()),
        "unit": it.unit,
        "supplier": party(it.supplier_kind, it.supplier_participation_id, parts, mask),
        "executor": party(it.executor_kind, it.executor_participation_id, parts, mask),
        "status": it.status,
        "status_label": STATE_LABEL.get(it.status, it.status),
        "shared": bool(it.shared),
        "notes": it.notes,
        "position": it.position,
        "confirmed_at": it.confirmed_at,
        "confirmed_by": (who or (lambda uid: _name(db, uid)))(it.confirmed_by) if it.confirmed_by else None,
        "updated_at": it.updated_at,
    }


def summary(items: list[ScopeItem], parts: dict[int, AllyParticipation]) -> dict:
    """El mismo resumen en el levantamiento, la oportunidad y el proyecto."""
    por_estado = {k: 0 for k in SCOPE_STATES}
    sin_resp = 0
    for it in items:
        por_estado[it.status] = por_estado.get(it.status, 0) + 1
        sup = party(it.supplier_kind, it.supplier_participation_id, parts)
        exe = party(it.executor_kind, it.executor_participation_id, parts)
        if sup["kind"] == "por_asignar" or exe["kind"] == "por_asignar":
            sin_resp += 1
    return {"total": len(items), "por_estado": por_estado, "por_asignar": sin_resp, "completados": por_estado.get("completado", 0)}


def options(parts: dict[int, AllyParticipation]) -> list[dict]:
    """A quien se puede asignar: Crimson, cada empresa de la oportunidad, o por asignar."""
    out = [{"kind": "crimson", "participation_id": None, "name": "Crimson"}]
    out += [{"kind": "aliado", "participation_id": a.id, "name": a.name} for a in sorted(parts.values(), key=lambda x: x.id)]
    out.append({"kind": "por_asignar", "participation_id": None, "name": "Por asignar"})
    return out


def scope_out(db: Session, tenant_id: int, opportunity_id: int | None, survey_id: int | None) -> dict:
    items = items_of(db, tenant_id, opportunity_id, survey_id)
    parts = participations(db, tenant_id, opportunity_id)
    return {
        "source": {"opportunity_id": opportunity_id, "survey_id": survey_id},
        "items": [item_out(db, it, parts) for it in items],
        "summary": summary(items, parts),
        "options": options(parts),
        "kinds": KIND_LABEL,
        "states": STATE_LABEL,
        "typical": [{"kind": k, "description": d} for k, d in TYPICAL],
    }


def check_party(kind: str, pid: int | None, parts: dict[int, AllyParticipation]) -> tuple[str, int | None]:
    if kind not in PARTY_KINDS:
        raise HTTPException(422, "Responsable desconocido")
    if kind != "aliado":
        return kind, None
    if pid is None or pid not in parts:
        raise HTTPException(422, "Esa empresa no participa en esta oportunidad")
    return "aliado", pid


def release_participation(db: Session, a: AllyParticipation) -> int:
    """La participacion se quita de la oportunidad: lo que suministraba o ejecutaba queda por asignar (por confirmar)."""
    n = 0
    for col_kind, col_pid in (("supplier_kind", "supplier_participation_id"), ("executor_kind", "executor_participation_id")):
        res = db.execute(
            update(ScopeItem)
            .where(ScopeItem.tenant_id == a.tenant_id, getattr(ScopeItem, col_pid) == a.id)
            .values({col_kind: "por_asignar", col_pid: None, "status": "por_confirmar"})
            .execution_options(synchronize_session="fetch")
        )
        n += res.rowcount or 0
    return n


def attach_survey(db: Session, s: Survey, opportunity_id: int) -> int:
    """El levantamiento sin oportunidad se liga a una: sus partidas pasan a la oportunidad (una sola fuente)."""
    res = db.execute(
        update(ScopeItem)
        .where(ScopeItem.tenant_id == s.tenant_id, ScopeItem.survey_id == s.id, ScopeItem.opportunity_id.is_(None))
        .values(opportunity_id=opportunity_id)
        .execution_options(synchronize_session="fetch")
    )
    return res.rowcount or 0


def touch_confirmed(it: ScopeItem, user_id: int) -> None:
    it.confirmed_at, it.confirmed_by = datetime.now(UTC), user_id


# ---------- portal del aliado ----------
def ally_items(db: Session, tenant_id: int, opportunity_id: int, my_part_ids: set[int]) -> list[tuple[ScopeItem, bool]]:
    """(partida, es_mia) que ve el aliado: las que suministra o ejecuta una participacion suya y las compartidas."""
    if not my_part_ids:
        return []
    mine = or_(ScopeItem.supplier_participation_id.in_(my_part_ids), ScopeItem.executor_participation_id.in_(my_part_ids))
    rows = db.scalars(
        select(ScopeItem)
        .where(ScopeItem.tenant_id == tenant_id, ScopeItem.opportunity_id == opportunity_id, or_(mine, ScopeItem.shared.is_(True)))
        .order_by(ScopeItem.position, ScopeItem.id)
    ).all()
    out = []
    for it in rows:
        es_mia = (it.supplier_kind == "aliado" and it.supplier_participation_id in my_part_ids) or (
            it.executor_kind == "aliado" and it.executor_participation_id in my_part_ids
        )
        out.append((it, es_mia))
    return out
