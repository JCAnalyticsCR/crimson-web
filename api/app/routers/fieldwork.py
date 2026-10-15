"""Levantamientos tecnicos y ordenes de trabajo.

El tecnico levanta en sitio desde el celular (puntos, fotos, materiales) y envia. Administracion ve el costeo
(costos, mano de obra, margen) y con un boton genera la cotizacion. El tecnico nunca ve precios ni costos.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.deps import ALLY_USER_ROLES, CLIENT_ROLES, EXTERNAL_ROLES, Principal, require
from ..models import Customer, Opportunity, Product, Project, Survey, SurveyItem, SurveyObservation, SurveyPoint, User, Warehouse, WorkOrder, WorkOrderMaterial
from ..schemas.sales import DocumentIn, LineInSchema
from ..services import approvals as aprob
from ..services import documents as docsvc
from ..services import equipment_reco, pricing, supplier_catalog
from ..services import inventory as invsvc
from ..services.archive import live
from ..services.documents import audit
from ..services.mail import notify_roles, queue_email
from ..services.refs import check_refs
from ..services.sequences import next_number
from ..services.survey_specs import (
    SPECS,
    TEMPLATE_VERSION,
    common_spec,
    normalize_common,
    normalize_data_status,
    normalize_item,
    normalize_point_data,
    per_unit,
    suggest_materials,
    system_calcs,
)
from ..services.totals import d

router = APIRouter(tags=["campo"])
log = logging.getLogger(__name__)

LABOR_DAY = Decimal(25000)  # costo interno por tecnico por dia (Ajustes: labor_day_cost)
TRAVEL = Decimal(35000)  # transporte estimado por levantamiento (Ajustes: travel_cost)

# Mano de obra por tipo (lo pidio Andres): cada tipo con su costo por persona por dia en Ajustes.
# Mientras no se configuren, civil y contratado cuestan lo mismo que el tecnico (no se inventan tarifas).
LABOR_TYPES: dict[str, tuple[str, str]] = {
    "tecnico": ("Personal técnico", "labor_day_cost"),
    "civil": ("Personal de obra civil", "labor_day_cost_civil"),
    "contratado": ("Personal contratado", "labor_day_cost_contratado"),
}
# Quien revisa lo que el tecnico envia. Andres aun no definio quien aprueba: por defecto admin y
# supervisor (lo que ya se notificaba); se cambia en Ajustes con survey_reviewer_roles sin tocar codigo.
REVIEWER_ROLES = ("admin", "supervisor")


def rates(tenant) -> tuple[Decimal, Decimal]:
    st = tenant.settings or {}
    return d(st.get("labor_day_cost", LABOR_DAY)), d(st.get("travel_cost", TRAVEL))


def labor_rates(tenant) -> dict[str, Decimal]:
    st = tenant.settings or {}
    base = d(st.get("labor_day_cost", LABOR_DAY))
    return {k: d(st.get(key, base)) for k, (_, key) in LABOR_TYPES.items()}


TARIFA_KEYS = ("tecnico", "civil", "contratado", "transport", "per_diem")


def tarifas_survey(s: Survey) -> dict[str, Decimal]:
    """Precio de mano de obra ajustado a mano para ESTE levantamiento (Andres: "que se pueda editar el precio
    de mano de obra"). Solo lo que se cambio; lo demas sale de Ajustes."""
    raw = (s.labor or {}).get("tarifas") or {}
    return {k: d(v) for k, v in raw.items() if k in TARIFA_KEYS and v is not None}


def per_diem(tenant) -> Decimal:
    """Viaticos (alimentacion) por persona por dia de obra. 0 si no esta configurado: antes no existia."""
    return d((tenant.settings or {}).get("per_diem_cost", 0))


def reviewer_roles(tenant) -> tuple[str, ...]:
    roles = [r for r in (tenant.settings or {}).get("survey_reviewer_roles") or [] if r not in EXTERNAL_ROLES]  # nunca un cliente ni un aliado
    return tuple(roles) if roles else REVIEWER_ROLES


def survey_labor(s: Survey) -> dict[str, dict]:
    """Personal requerido por tipo. Levantamientos viejos (sin labor) toman techs/days como personal tecnico."""
    raw = s.labor or {}
    out = {}
    for k in LABOR_TYPES:
        row = raw.get(k) or {}
        if k == "tecnico" and not raw:
            row = {"people": s.techs, "days": s.days}
        out[k] = {"people": int(row.get("people") or 0), "days": d(row.get("days") or 0)}
    return out


def labor_costing(s: Survey, tenant) -> dict:
    """Costo interno de mano de obra, transporte y viaticos del levantamiento (tarifas propias o de Ajustes).
    Unica fuente: la usan el costeo y el costo planificado del proyecto."""
    propias = tarifas_survey(s)
    transport = propias.get("transport", rates(tenant)[1])
    generales = labor_rates(tenant)
    lrates = {k: propias.get(k, v) for k, v in generales.items()}
    types, labor_cost, person_days = [], Decimal(0), Decimal(0)
    for k, v in survey_labor(s).items():
        c = lrates[k] * v["people"] * v["days"]
        labor_cost += c
        person_days += v["people"] * v["days"]
        types.append(
            {
                "key": k,
                "label": LABOR_TYPES[k][0],
                "people": v["people"],
                "days": v["days"],
                "day_cost": lrates[k],
                "day_cost_default": generales[k],
                "custom": k in propias,
                "cost": c.quantize(Decimal("0.01")),
            }
        )
    pd = propias.get("per_diem", per_diem(tenant))
    viaticos = (pd * person_days).quantize(Decimal("0.01"))
    return {"types": types, "labor_cost": labor_cost, "transport": transport, "per_diem": pd, "viaticos": viaticos, "travel": transport + viaticos}


# ---------- Levantamientos ----------
# Las cotizaciones hechas desde un levantamiento antes de la matriz de roles (13/10) guardaban el costo y el margen en
# las notas internas. La migracion vendedor_b4e8c2d61f37 los limpia con ESTE patron (y una prueba lo fija contra el
# texto viejo): si alguien cambia el texto, cambia el patron aqui.
COST_NOTE_RE = re.compile(r" ?Costo estimado ₡[\d.,]+ · margen (?:-?[\d.,]+|None) %\.")


def survey_scope(p: Principal):
    """Quien no ve todo el campo: el tecnico, lo suyo; el vendedor (field.resumen), ademas lo de sus oportunidades
    (13/10 33:30). None = sin filtro (field.ver_todo)."""
    if p.sees_field_all:
        return None
    own = Survey.technician_id == p.user.id
    if p.can("field", "resumen"):
        mias = select(Opportunity.id).where(Opportunity.tenant_id == p.tenant.id, Opportunity.owner_id == p.user.id)
        return or_(own, Survey.opportunity_id.in_(mias))
    return own


def can_see_survey(db: Session, s: Survey, p: Principal) -> bool:
    """El mismo criterio que survey_scope para un levantamiento suelto (ficha, resumen, informe, correo del informe).
    Un levantamiento que se desliga de la oportunidad deja de verse para la vendedora; uno que se liga, aparece."""
    if p.sees_field_all or s.technician_id == p.user.id:
        return True
    if s.technician_id is None and p.can("field", "editar"):
        return True  # el tecnico, como siempre: los que quedaron sin tecnico (datos viejos o importados)
    if p.can("field", "resumen") and s.opportunity_id:
        # el vendedor: solo los de SUS oportunidades, tengan o no tecnico
        o = db.get(Opportunity, s.opportunity_id)
        return bool(o and o.tenant_id == p.tenant.id and o.owner_id == p.user.id)
    return False


def _survey(db: Session, sid: int, p: Principal) -> Survey:
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if not can_see_survey(db, s, p):
        raise HTTPException(404, "Levantamiento no encontrado")
    return s


def solo_resumen(p: Principal) -> bool:
    """La vendedora (field.resumen sin field.editar): ve los levantamientos en resumen, nunca el registro completo
    (data tecnico de cada punto, seccion comun ni de que proveedor sale cada equipo). D6: la API filtra."""
    return p.can("field", "resumen") and not p.can("field", "editar") and not p.sees_field_all


# Etapas en que una oportunidad todavia espera su levantamiento (antes de "levantamiento" y de cotizar): crear uno
# ligado la pasa a "levantamiento"; en una etapa posterior se queda donde esta (el embudo solo avanza).
ESPERA_LEVANTAMIENTO = ("nuevo", "contactado", "requiere_visita")


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt  # SQLite devuelve la hora sin zona (es UTC)


def pending_survey_request(db: Session, o: Opportunity, skip_sid: int | None = None):
    """La solicitud de levantamiento (POST /opportunities/{id}/request-survey) que nadie atendio todavia: la ultima,
    posterior al ultimo levantamiento de la oportunidad (vivo o no), sin uno vivo y con la oportunidad abierta.
    Devuelve su registro de auditoria, o None. Vale en cualquier etapa abierta: pedirlo no devuelve la oportunidad a
    "requiere visita" (advance_status solo avanza) y aun asi alguien la tiene que poder atender."""
    from ..models import AuditLog
    from ..services.archive import is_live

    if o.status in ("ganada", "perdida"):
        return None
    req = db.scalar(
        select(AuditLog)
        .where(AuditLog.tenant_id == o.tenant_id, AuditLog.action == "request_survey", AuditLog.entity == "opportunity", AuditLog.entity_id == o.id)
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if req is None:
        return None
    stmt = select(Survey).where(Survey.tenant_id == o.tenant_id, Survey.opportunity_id == o.id)
    if skip_sid:
        stmt = stmt.where(Survey.id != skip_sid)
    pedido = _utc(req.at)
    for s in db.scalars(stmt):
        if is_live(s) or _utc(s.created_at) >= pedido:
            return None  # ya la atendieron (aunque despues ese levantamiento se haya archivado)
    return req


def attends_survey_requests(p: Principal) -> bool:
    """Quien atiende las solicitudes de levantamiento de cualquier vendedor: el equipo de campo INTERNO que llena
    levantamientos (supervisor, que recibe el aviso, y el tecnico al que se lo pasa: no hay asignacion de oportunidades
    a tecnicos, la solicitud pendiente es su orden de visita). Nunca un aliado: el embudo de Crimson no se abre a otra
    empresa. Solo vale para oportunidades con solicitud pendiente (pending_survey_request): no recorre el embudo."""
    return not p.is_ally and p.can("field", "crear")


def _sees_in_pipeline(p: Principal, o: Opportunity) -> bool:
    """La misma regla que pipeline._own: sin ver_todo, solo las suyas."""
    return p.can("crm_pipeline", "ver") and (p.can("crm_pipeline", "ver_todo") or o.owner_id == p.user.id)


def _linkable_opportunity(db: Session, p: Principal, oid: int, customer_id: int | None = None, skip_sid: int | None = None) -> Opportunity:
    """La oportunidad a la que se liga un levantamiento: de esta empresa, viva y abierta, del mismo cliente, y que
    quien liga la vea en el embudo (pipeline._own: sin ver_todo, solo las suyas) o, para el equipo de campo interno
    (attends_survey_requests), que tenga una solicitud de levantamiento pendiente. Cualquier otra: 404, sin decir si
    existe.
    Ligar decide quien ve el levantamiento (la vendedora de la oportunidad) y de quien es la cotizacion que salga de
    el: no se acepta un id cualquiera (antes POST/PUT /surveys lo guardaban sin mirar: ni empresa, ni cliente, ni si
    ya tenia levantamiento o cotizacion)."""
    from ..services.archive import is_live

    o = db.get(Opportunity, oid)
    if not o or o.tenant_id != p.tenant.id or not is_live(o):
        raise HTTPException(404, "Oportunidad no encontrada")
    if not _sees_in_pipeline(p, o) and not (attends_survey_requests(p) and pending_survey_request(db, o, skip_sid)):
        raise HTTPException(404, "Oportunidad no encontrada")
    if o.status in ("ganada", "perdida"):
        raise HTTPException(409, "La oportunidad ya está cerrada (ganada o perdida)")
    if customer_id and o.customer_id and customer_id != o.customer_id:
        raise HTTPException(409, "El levantamiento y la oportunidad son de clientes distintos")
    return o


def _reviewers(db: Session, tenant) -> list[dict]:
    """Personas que reciben el aviso de "nuevo levantamiento": lo que el tecnico necesita ver al enviar."""
    from ..models import TenantUser

    roles = reviewer_roles(tenant)
    rows = db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.active, TenantUser.role_code.in_(roles)))
    return [{"id": m.user.id, "name": m.user.full_name, "role": m.role_code} for m in rows]


def _opp_ref(db: Session, oid: int | None, tenant_id: int) -> dict | None:
    """Numero y titulo de la oportunidad ligada, para mostrarla sin otra consulta. Solo de esta empresa."""
    o = db.get(Opportunity, oid) if oid else None
    return {"id": o.id, "number": o.number, "title": o.title, "status": o.status} if o and o.tenant_id == tenant_id else None


def _customer_of(db: Session, s: Survey) -> Customer | None:
    c = db.get(Customer, s.customer_id) if s.customer_id else None
    return c if c is not None and c.tenant_id == s.tenant_id else None


def _survey_out(db: Session, s: Survey, full: bool = True) -> dict:
    c = _customer_of(db, s)
    t = db.get(User, s.technician_id) if s.technician_id else None
    sender = db.get(User, s.sent_by) if s.sent_by else None
    visit_ids = [int(x) for x in (s.visit_tech_ids or []) if str(x).isdigit()]
    visitors = [u.full_name for u in (db.get(User, i) for i in visit_ids) if u]
    labor = survey_labor(s)
    out = {
        "id": s.id,
        "number": s.number,
        "kind": s.kind,
        "kind_label": SPECS.get(s.kind, SPECS["otro"])["label"],
        "status": s.status,
        "customer_id": s.customer_id,
        "customer": c.name if c else (s.contact or {}).get("name"),
        "contact": s.contact,
        "site": s.site,
        "opportunity_id": s.opportunity_id,
        "opportunity": _opp_ref(db, s.opportunity_id, s.tenant_id),
        "technician_id": s.technician_id,
        "technician": t.full_name if t else None,
        "visit_date": s.visit_date,
        "techs": s.techs,
        "days": s.days,
        "notes": s.notes,
        "photos": s.photos,
        "quote_id": s.quote_id,
        "points_count": len(s.points),
        "created_at": s.created_at,
        "archived_at": s.archived_at,
        "trashed_at": s.trashed_at,
        "sent_at": s.sent_at,
        "sent_by": sender.full_name if sender else None,
        "visit_tech_ids": visit_ids,
        "visit_techs": visitors,
        "labor": {k: {"label": LABOR_TYPES[k][0], "people": v["people"], "days": v["days"]} for k, v in labor.items()},
        "template_version": s.template_version or 1,
        "received_by": s.received_by,
        "ally_company_id": s.ally_company_id,
        "ally_company": _ally_name(db, s.ally_company_id),
        **_review_out(db, s),
    }
    if s.status == "enviado":
        # "pendiente de revision por": se calcula al leer, asi refleja los roles configurados hoy
        from ..models import Tenant

        tenant = db.get(Tenant, s.tenant_id)
        out["pending_review"] = {"roles": list(reviewer_roles(tenant)), "people": [r["name"] for r in _reviewers(db, tenant)]}
    if full:
        out["common"] = s.common  # seccion comun y bloques de sistema (None en los levantamientos de antes)
        out["points"] = [
            {
                "id": x.id,
                "code": x.code,
                "kind": x.kind or s.kind,
                "label": x.label,
                "data": x.data,
                "data_status": x.data_status,
                "photos": x.photos,
                "notes": x.notes,
            }
            for x in s.points
        ]
        out["items"] = [
            {
                "id": i.id,
                "product_id": i.product_id,
                "name": i.name,
                "quantity": i.quantity,
                "unit": i.unit,
                "note": i.note,
                "kind": i.kind or "material",
                **_item_reco_out(i),
            }
            for i in s.items
        ]
    return out


def _item_reco_out(i: SurveyItem) -> dict:
    """Equipo elegido con la recomendacion: que linea generica cubre y de donde sale. Sin costos (lo ve el tecnico)."""
    rec = i.recommendation if isinstance(i.recommendation, dict) else None
    if not rec and not i.supplier_item_id:
        return {}
    rec = rec or {}
    return {
        "recommended_for": rec.get("line"),
        "supplier_item_id": i.supplier_item_id,
        "supplier": rec.get("supplier"),
        "offer_source": rec.get("source") or ("proveedor" if i.supplier_item_id else "propio"),
    }


REVIEW_LABEL = {None: "Sin enviar", "pendiente": "En revisión", "aprobado": "Revisión aprobada", "devuelto": "Devuelto con observaciones"}


def _review_out(db: Session, s: Survey) -> dict:
    """Estado de la revision del supervisor y sus observaciones (las de todas las rondas, abiertas primero)."""
    rev = db.get(User, s.reviewed_by) if s.reviewed_by else None
    names: dict[int, str] = {}

    def who(uid):
        if not uid:
            return None
        if uid not in names:
            u = db.get(User, uid)
            names[uid] = u.full_name if u else "—"
        return names[uid]

    obs = sorted(s.observations, key=lambda o: (o.resolved_at is not None, -o.review_round, o.id))
    return {
        "review_status": s.review_status,
        "review_label": REVIEW_LABEL.get(s.review_status, s.review_status),
        "reviewed_by": rev.full_name if rev else None,
        "reviewed_at": s.reviewed_at,
        "review_round": s.review_round or 0,
        "open_observations": sum(1 for o in s.observations if o.resolved_at is None),
        "observations": [
            {
                "id": o.id,
                "round": o.review_round,
                "point_code": o.point_code,
                "text": o.text,
                "created_by": who(o.created_by),
                "created_at": o.created_at,
                "resolved": o.resolved_at is not None,
                "resolved_at": o.resolved_at,
                "resolved_by": who(o.resolved_by),
                "resolution": o.resolution,
            }
            for o in obs
        ],
    }


class PointIn(BaseModel):
    id: int | None = None
    code: str = Field(min_length=1, max_length=20)
    # tipo del punto si es distinto del levantamiento (puerta de acceso en uno de CCTV); sin dato = el del levantamiento
    kind: str | None = Field(None, pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    label: str | None = Field(None, max_length=160)
    data: dict = Field(default_factory=dict)
    # estado de cada campo de data (confirmado|estimado|no_aplica|pendiente); sin dato = como siempre
    data_status: dict | None = None
    photos: list = Field(default_factory=list)
    notes: str | None = None


class ItemIn(BaseModel):
    id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(1), ge=0)
    unit: str = Field("Unid", max_length=10)
    note: str | None = Field(None, max_length=200)
    kind: str | None = Field(None, pattern="^(equipo|material|servicio)$")  # sin dato se clasifica por el nombre


class LaborIn(BaseModel):
    people: int = Field(0, ge=0, le=200)
    days: Decimal = Field(Decimal(0), ge=0, le=365)


class SurveyIn(BaseModel):
    kind: str = Field("cctv", pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    customer_id: int | None = None
    # solo al crear (validada con _linkable_opportunity). Despues se liga o desliga con POST /surveys/{id}/opportunity:
    # PUT la ignora (el autoguardado manda la que tenia al abrir y no debe deshacer un ligado reciente)
    opportunity_id: int | None = None
    contact: dict = Field(default_factory=dict)
    site: str | None = Field(None, max_length=300)
    visit_date: date | None = None
    # compatibilidad: clientes viejos mandan techs/days; si viene labor, manda labor
    techs: int = Field(2, ge=0, le=200)
    days: Decimal = Field(Decimal(1), ge=0, le=365)
    labor: dict[str, LaborIn] | None = None  # tecnico | civil | contratado
    visit_tech_ids: list[int] = Field(default_factory=list)  # quienes hicieron la visita (informativo)
    notes: str | None = None
    photos: list = Field(default_factory=list)
    points: list[PointIn] = Field(default_factory=list)
    items: list[ItemIn] = Field(default_factory=list)
    # seccion comun (§4) y bloques de sistema; si el cliente no los manda no se tocan (portal viejo)
    common: dict | None = None
    received_by: str | None = Field(None, max_length=160)


def _labor_payload(data: SurveyIn) -> dict:
    """Guarda labor con todos los tipos; techs/days quedan espejados al personal tecnico."""
    if data.labor is None:
        src = {"tecnico": LaborIn(people=data.techs, days=data.days)}
    else:
        bad = set(data.labor) - set(LABOR_TYPES)
        if bad:
            raise HTTPException(422, f"Tipo de personal desconocido: {', '.join(sorted(bad))}")
        src = data.labor
    return {k: {"people": (src.get(k) or LaborIn()).people, "days": str((src.get(k) or LaborIn()).days)} for k in LABOR_TYPES}


def _check_customer(db: Session, p: Principal | int, data: SurveyIn, s: Survey | None = None) -> None:
    """El cliente del levantamiento es de esta empresa (si no, la ficha diria el nombre de un cliente ajeno)."""
    from ..services.refs import check_refs

    tid = p if isinstance(p, int) else p.tenant.id
    check_refs(db, tid, {"customer_id": data.customer_id}, refs={"customer_id": (Customer, "Cliente no encontrado")}, current=s)


def _apply_fields(s: Survey, data: SurveyIn, db: Session, tenant_id: int, valid_user_ids: set[int] | None = None) -> None:
    labor = _labor_payload(data)
    # la oportunidad no se toca aqui (ver SurveyIn.opportunity_id) y el cliente tiene que ser de esta empresa
    exclude = {"points", "items", "labor", "techs", "days", "visit_tech_ids", "common", "received_by", "opportunity_id", "customer_id"}
    for k, v in data.model_dump(exclude=exclude).items():
        setattr(s, k, v)
    _check_customer(db, tenant_id, data, s)
    s.customer_id = data.customer_id
    # solo si vienen: un cliente que no los conoce no borra lo que otro guardo
    if "common" in data.model_fields_set:
        try:
            s.common = normalize_common(data.common)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    if "received_by" in data.model_fields_set:
        s.received_by = (data.received_by or "").strip() or None
    tarifas = (s.labor or {}).get("tarifas")  # las pone el costeo; el tecnico al guardar no las ve ni las borra
    s.labor = {**labor, "tarifas": tarifas} if tarifas else labor
    s.techs, s.days = labor["tecnico"]["people"], d(labor["tecnico"]["days"])
    # solo usuarios de esta empresa (o, desde el portal de aliados, de su equipo)
    from ..models import TenantUser

    validos = valid_user_ids if valid_user_ids is not None else set(db.scalars(select(TenantUser.user_id).where(TenantUser.tenant_id == tenant_id)))
    s.visit_tech_ids = [i for i in dict.fromkeys(data.visit_tech_ids) if i in validos]


def apply_survey_payload(db: Session, s: Survey, data: SurveyIn, tenant_id: int, valid_user_ids: set[int] | None = None) -> None:
    """Lo que manda el formulario del levantamiento: campos, seccion comun, personal, puntos y materiales. Conserva lo
    del costeo (unit_cost, recomendacion, tarifas). Sin control de alcance: quien llama ya lo hizo (el endpoint interno
    con _survey; el portal de aliados con su AllyScope). valid_user_ids=None: cualquier usuario de la empresa."""
    _apply_fields(s, data, db, tenant_id, valid_user_ids)
    _apply_children(s, data)


@router.get("/field/specs")
def specs(_: Principal = Depends(require("field", "ver"))):
    """Formularios por tipo de levantamiento: el portal los dibuja desde aquí."""
    return {
        k: {
            "label": v["label"],
            "point_prefix": v["point_prefix"],
            "point_label": v["point_label"],
            "fields": v["fields"],
            "materials": v["materials"],
            "template_version": TEMPLATE_VERSION,
        }
        for k, v in SPECS.items()
    }


@router.get("/field/specs/common")
def specs_common(_: Principal = Depends(require("field", "ver"))):
    """Seccion comun del levantamiento, bloques de sistema, estados por campo y version de la plantilla."""
    return common_spec()


@router.get("/field/technicians")
def technicians(
    project_id: int | None = None,
    opportunity_id: int | None = None,
    ally_company_id: int | None = None,
    p: Principal = Depends(require("field", "ver")),
    db: Session = Depends(get_db),
):
    """A quien se le puede asignar una orden. El supervisor no entra a Ajustes, asi que la lista vive aqui.
    Sin parametros: solo el equipo interno (como siempre). Con project_id (u opportunity_id, para levantamientos): ademas
    los usuarios activos de las empresas aliadas que participan ahi; con ally_company_id: solo los de esa empresa."""
    from ..models import AllyCompany, AllyParticipation, TenantUser

    rows = db.scalars(
        select(TenantUser).where(
            TenantUser.tenant_id == p.tenant.id,
            TenantUser.active,
            TenantUser.role_code.in_(("tecnico", "supervisor", "admin")),
            TenantUser.ally_company_id.is_(None),
            TenantUser.customer_id.is_(None),
        )
    )
    internos = [{"id": m.user.id, "name": m.user.full_name, "role": m.role_code, "ally_company_id": None, "company": None} for m in rows]
    if project_id is None and opportunity_id is None and ally_company_id is None:
        return [{k: v for k, v in x.items() if k in ("id", "name", "role")} for x in internos]
    q = select(AllyParticipation.ally_company_id).where(AllyParticipation.tenant_id == p.tenant.id, AllyParticipation.ally_company_id.is_not(None))
    if project_id is not None:
        q = q.where(AllyParticipation.project_id == project_id)
    if opportunity_id is not None:
        q = q.where(AllyParticipation.opportunity_id == opportunity_id)
    empresas = set(db.scalars(q))
    if ally_company_id is not None:
        empresas &= {ally_company_id}
    comps = {
        c.id: c for c in db.scalars(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, AllyCompany.id.in_(empresas), AllyCompany.active.is_(True)))
    }
    aliados = []
    if comps:
        for m in db.scalars(
            select(TenantUser).where(
                TenantUser.tenant_id == p.tenant.id, TenantUser.active, TenantUser.ally_company_id.in_(comps), TenantUser.role_code.in_(ALLY_USER_ROLES)
            )
        ):
            if m.user.active:
                aliados.append(
                    {
                        "id": m.user.id,
                        "name": m.user.full_name,
                        "role": m.role_code,
                        "ally_company_id": m.ally_company_id,
                        "company": comps[m.ally_company_id].name,
                    }
                )
    return ([] if ally_company_id is not None else internos) + sorted(aliados, key=lambda x: (x["company"], x["name"]))


def _ally_name(db: Session, cid: int | None) -> str | None:
    if not cid:
        return None
    from ..models import AllyCompany

    c = db.get(AllyCompany, cid)
    return c.name if c else None


@router.get("/surveys")
def surveys(
    status: str | None = None,
    review: str | None = Query(None, pattern="^(pendiente|aprobado|devuelto)$"),
    # filtro de la bandeja de Aprobaciones: la MISMA definicion que cuenta la bandeja (services/approvals.py)
    bandeja: str | None = Query(None, pattern="^(por_revisar|por_costear)$"),
    limit: int = Query(80, le=200),
    p: Principal = Depends(require("field", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(Survey).where(Survey.tenant_id == p.tenant.id, live(Survey))
    sc = survey_scope(p)
    if sc is not None:
        stmt = stmt.where(sc)
    if status:
        stmt = stmt.where(Survey.status == status)
    if review:
        stmt = stmt.where(Survey.review_status == review)
    if bandeja == "por_revisar":
        stmt = stmt.where(*aprob.survey_review_where(p))
    elif bandeja == "por_costear":
        stmt = stmt.where(*aprob.survey_costing_where(p))
    return [_survey_out(db, s, full=False) for s in db.scalars(stmt.order_by(Survey.id.desc()).limit(limit))]


@router.post("/surveys", status_code=201)
def survey_create(data: SurveyIn, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    # antes de gastar un consecutivo: el cliente, y la oportunidad (decide quien ve el levantamiento y de quien es su
    # cotizacion)
    _check_customer(db, p, data)
    o = _linkable_opportunity(db, p, data.opportunity_id, customer_id=data.customer_id) if data.opportunity_id else None
    number, _ = next_number(db, p.tenant.id, "LEV")
    s = Survey(tenant_id=p.tenant.id, number=number, technician_id=p.user.id, template_version=TEMPLATE_VERSION)
    apply_survey_payload(db, s, data, p.tenant.id)
    if o:
        s.opportunity_id = o.id
        if not s.customer_id and o.customer_id:
            s.customer_id = o.customer_id  # el de la oportunidad: sin el, cotizar despues falla con "asigná un cliente"
    db.add(s)
    db.flush()
    if o and o.status in ESPERA_LEVANTAMIENTO:
        o.status = "levantamiento"
    audit(db, p.tenant.id, p.user.id, "create", "survey", s.id, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


def _apply_children(s: Survey, data: SurveyIn) -> None:
    points = []
    for pt in data.points:
        pk = pt.kind or data.kind
        try:
            clean = normalize_point_data(pk, pt.data)
            status = normalize_data_status(pt.data_status)
        except ValueError as e:
            raise HTTPException(422, f"{pt.code}: {e}") from e
        # se guarda None cuando es el tipo del levantamiento: asi cambiar el tipo del levantamiento arrastra sus puntos
        points.append(
            SurveyPoint(code=pt.code, kind=pk if pk != data.kind else None, label=pt.label, data=clean, data_status=status, photos=pt.photos, notes=pt.notes)
        )
    s.points.clear()
    s.points.extend(points)
    # el costo escrito en el costeo vive en la linea: el tecnico reescribe la lista al guardar y no debe borrarlo
    prev = {i.id: i for i in s.items if i.id}
    s.items.clear()
    usados: set[int] = set()
    for it in data.items:
        old = prev.get(it.id) if it.id else None
        if it.id and old is None:
            # id que ya no existe: cada guardado renumera las lineas y el portal guardaba dos veces sin recargar
            # (Costear y mover el margen). La misma linea se reconoce por producto y nombre: no pierde su costo.
            old = next((o for o in prev.values() if o.id not in usados and o.product_id == it.product_id and (o.name or "").strip() == it.name.strip()), None)
        if old is not None:
            usados.add(old.id)
        keep_cost = old.unit_cost if old is not None and old.product_id == it.product_id else None
        kind, unit = normalize_item(it.name, it.kind or (old.kind if old is not None else None), it.unit)
        # el equipo elegido con la recomendacion (y su oferta de proveedor) sigue mientras la linea sea la misma
        same = old is not None and old.product_id == it.product_id and (it.product_id is not None or (old.name or "").strip() == it.name.strip())
        s.items.append(
            SurveyItem(
                product_id=it.product_id,
                name=it.name,
                quantity=it.quantity,
                unit=unit,
                note=it.note,
                kind=kind,
                unit_cost=keep_cost,
                supplier_item_id=old.supplier_item_id if same else None,
                supplier_cost=old.supplier_cost if same else None,
                supplier_currency=old.supplier_currency if same else None,
                recommendation=old.recommendation if same else None,
            )
        )


@router.get("/surveys/from-opportunity/{oid}")
def survey_from_opportunity(oid: int, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    """Lo que el borrador de un levantamiento toma de su oportunidad (enlace del aviso «Solicitar levantamiento»):
    cliente, sitio y solucion. Para quien la ve en el embudo o, sin verla (supervisor sin ver_todo, tecnico), si tiene
    una solicitud pendiente (_linkable_opportunity); un aliado nunca. Sin montos."""
    o = _linkable_opportunity(db, p, oid)
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    c = c if c is not None and c.tenant_id == p.tenant.id else None
    # un lead sin cliente: el nombre del contacto solo a quien ve la oportunidad (es un dato del lead, no del cliente)
    contacto = (o.contact or {}).get("name") if _sees_in_pipeline(p, o) else None
    return {
        "id": o.id,
        "number": o.number,
        "title": o.title,
        "customer_id": c.id if c else None,
        "customer": c.name if c else contacto,
        "site": o.site,
        "solution": o.solution,
    }


@router.get("/surveys/{sid}")
def survey_get(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    if solo_resumen(p):
        return _summary_out(db, s, p)  # la vendedora: el mismo resumen que /summary, nunca el registro completo
    return _survey_out(db, s)


@router.put("/surveys/{sid}")
def survey_update(sid: int, data: SurveyIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado") and not p.sees_field_all:
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    from ..services import bitacora  # bitacora con antes/despues; no cambia como se guardan los puntos

    antes = bitacora.survey_state(s)
    apply_survey_payload(db, s, data, p.tenant.id)
    c = db.get(Customer, s.customer_id) if s.customer_id else None
    bitacora.audit_survey_update(db, p, s, antes, company=c.name if c else (s.contact or {}).get("name"))
    if s.review_status == "aprobado" and not s.quote_id and not p.can("field", "revisar"):
        # lo aprobado ya no es lo mismo: el supervisor tiene que volver a verlo antes del costeo
        s.review_status = "pendiente"
        audit(db, p.tenant.id, p.user.id, "review_reset", "survey", s.id, {"motivo": "editado despues de aprobado"}, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


class SurveyAssignIn(BaseModel):
    technician_id: int
    ally_company_id: int | None = None


@router.post("/surveys/{sid}/assign")
def survey_assign(sid: int, data: SurveyAssignIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    """Asigna el levantamiento a un tecnico (interno o de una empresa aliada que participa en la oportunidad). Con un
    tecnico aliado el levantamiento queda a cargo de su empresa (lo ve en su portal); con uno interno vuelve a Crimson."""
    from ..services.ally_scope import check_ally_assignment, notify_ally_work

    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    _check_crew(db, p.tenant.id, [data.technician_id])
    aliada = check_ally_assignment(
        db, p.tenant.id, ally_company_id=data.ally_company_id, user_ids=[data.technician_id], opportunity_id=s.opportunity_id if s.opportunity_id else None
    )
    if aliada is not None and not s.opportunity_id:
        raise HTTPException(422, "Ligá el levantamiento a la oportunidad en común antes de asignarlo a un aliado")
    antes = {"technician_id": s.technician_id, "ally_company_id": s.ally_company_id}
    s.technician_id, s.ally_company_id = data.technician_id, aliada
    audit(db, p.tenant.id, p.user.id, "assign", "survey", s.id, {"de": antes, "a": {"technician_id": s.technician_id, "ally_company_id": aliada}}, ip=p.ip)
    if data.technician_id != antes["technician_id"]:
        u = db.get(User, data.technician_id)
        if u and u.email and aliada is None:
            queue_email(
                db,
                p.tenant,
                u.email,
                f"Tiene un levantamiento nuevo: {s.number}",
                f"<p>Se le asignó el levantamiento <b>{html.escape(s.number)}</b> ({html.escape(s.site or 'sin sitio')}).</p>",
                "survey",
                s.id,
            )
        notify_ally_work(db, p.tenant, {data.technician_id}, s.number, f"Levantamiento {s.site or ''}".strip(), s.visit_date, entity_id=s.id)
    db.commit()
    return _survey_out(db, s)


class LinkOppIn(BaseModel):
    opportunity_id: int | None = None  # None = desligar


@router.post("/surveys/{sid}/opportunity")
def survey_link_opportunity(sid: int, data: LinkOppIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Liga un levantamiento ya hecho a una oportunidad (o lo desliga).

    Andres: "hice el levantamiento en el sitio antes de registrar la oportunidad; quiero poder asignarlo
    despues". Funciona aunque el levantamiento ya este cotizado, que es el caso normal: se visita, se cotiza
    y recien ahi se registra la oportunidad. Al ligar, la oportunidad hereda lo que el levantamiento ya
    avanzo (cliente, cotizacion, monto y etapa) y queda anotado en su bitacora."""
    from ..models import Quote
    from ..services.documents import CR
    from ..services.ownership import opportunity_seller
    from .pipeline import sync_seller_from_opportunity

    s = _survey(db, sid, p)
    stamp = datetime.now(CR).strftime("%d/%m/%Y %H:%M")
    anterior = db.get(Opportunity, s.opportunity_id) if s.opportunity_id else None

    if data.opportunity_id is None:
        if s.ally_company_id is not None:
            # el levantamiento de un aliado vive en la oportunidad en comun (survey_assign lo exige): sin ella el aliado
            # lo seguiria viendo con datos que ya no tiene en comun. Primero se le pasa a Crimson
            raise HTTPException(422, "Este levantamiento está a cargo de una empresa aliada: asignalo a Crimson antes de desligarlo")
        if anterior:
            anterior.notes = f"{stamp} · {p.user.full_name}: se desligo el levantamiento {s.number}\n{anterior.notes or ''}".strip()
        s.opportunity_id = None
        audit(db, p.tenant.id, p.user.id, "unlink_opportunity", "survey", s.id, {"opportunity_id": anterior.id if anterior else None}, ip=p.ip)
        db.commit()
        return _survey_out(db, s)

    # sin ver_todo, solo una oportunidad propia (como en el embudo) o una que espera levantamiento
    o = _linkable_opportunity(db, p, data.opportunity_id, customer_id=s.customer_id, skip_sid=s.id)
    if s.ally_company_id is not None:
        # el alcance del aliado en levantamientos es ally_company_id: moverlo a una oportunidad donde su empresa no
        # participa le dejaria ver (y editar) el numero, titulo y estado de esa oportunidad y su cliente. Como al cambiar
        # el proyecto de una OT o de un ticket, la empresa tiene que participar en la nueva (si no, 422)
        from ..services.ally_scope import check_ally_assignment

        check_ally_assignment(
            db,
            p.tenant.id,
            ally_company_id=s.ally_company_id,
            user_ids=[s.technician_id],
            opportunity_id=o.id,
            current=s.ally_company_id,
            existing={s.technician_id},
        )
    if o.quote_id and s.quote_id and o.quote_id != s.quote_id:
        raise HTTPException(409, "La oportunidad ya tiene otra cotización ligada")
    if s.quote_id:
        # ligar le pasa la cotizacion abierta al responsable de la oportunidad (y su comision): si es de otro vendedor,
        # eso es reasignar la venta, que es de quien ve todo el embudo o asigna vendedores (no del supervisor)
        cq, nuevo = db.get(Quote, s.quote_id), opportunity_seller(db, o)
        mueve = cq is not None and cq.status not in ("convertida", "anulada") and nuevo is not None and cq.seller_id != nuevo
        if mueve and not (p.can("crm_pipeline", "ver_todo") or p.can("sales", "asignar")):
            raise HTTPException(409, f"La cotización {cq.number} es de otro vendedor: ligarla a {o.number} la reasigna y eso lo hace administración.")

    # lo que uno tiene y el otro no, se completa
    if not s.customer_id and o.customer_id:
        s.customer_id = o.customer_id
    elif s.customer_id and not o.customer_id:
        o.customer_id = s.customer_id

    antes = o.status
    orden = ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion")
    if s.quote_id:
        q = db.get(Quote, s.quote_id)
        o.quote_id = s.quote_id
        sync_seller_from_opportunity(db, o)  # la cotizacion (si no esta convertida) pasa al responsable de la oportunidad
        if q and q.status != "anulada":
            o.amount = d(q.total)  # mismo criterio que el resto: el monto sigue a la cotizacion
        destino = "enviada" if q and q.status in ("enviada", "aprobada") else "cotizando"
    else:
        destino = "levantamiento"
    if o.status in orden and orden.index(o.status) < orden.index(destino):
        o.status = destino  # solo avanza: nunca devuelve una oportunidad que ya estaba mas adelante

    if anterior and anterior.id != o.id:
        anterior.notes = f"{stamp} · {p.user.full_name}: el levantamiento {s.number} se movio a {o.number}\n{anterior.notes or ''}".strip()
    s.opportunity_id = o.id
    o.notes = f"{stamp} · {p.user.full_name}: se ligo el levantamiento {s.number}\n{o.notes or ''}".strip()
    audit(db, p.tenant.id, p.user.id, "link_opportunity", "survey", s.id, {"opportunity_id": o.id}, ip=p.ip)
    if o.status != antes:
        audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": antes, "a": o.status}, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


@router.post("/surveys/{sid}/suggest")
def survey_suggest(
    sid: int,
    candidates: bool = False,
    margin: Decimal | None = Query(None, ge=0, lt=100),
    p: Principal = Depends(require("field", "editar")),
    db: Session = Depends(get_db),
):
    """Propone materiales y equipos a partir de los puntos, sus observaciones y lo ya cargado. NO modifica
    el levantamiento: el portal muestra la lista con el motivo de cada una y el usuario elige (ver /apply).

    candidates=true ("Recomendar equipos"): cada linea trae ademas `candidates` (hasta 3 productos reales del
    catalogo propio o de proveedor, el primero "recommended" si cumple) y `recommend` (requisitos, equipo en uso),
    y se suma la linea de discos calculada con el almacenamiento. Sin el parametro la respuesta es la de siempre.
    Costos solo para quien los ve; el tecnico ve que equipo es y si hay existencias."""
    s = _survey(db, sid, p)
    lines = suggest_materials(s.kind, list(s.points), list(s.items), common=s.common)
    if not candidates:
        return lines
    try:
        return equipment_reco.recommend(db, p.tenant, s, lines, sees_costs=p.sees_costs, sees_prices=p.sees_prices, margin_pct=margin)
    except Exception:  # noqa: BLE001 - la sugerencia de siempre nunca se cae por la recomendacion
        log.exception("recomendacion de equipos fallo (levantamiento %s)", s.id)
        db.rollback()
        return lines


@router.get("/surveys/{sid}/system")
def survey_system(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """Calculos del sistema (lectores, controladoras, credenciales, almacenamiento, PoE) con su formula. Son
    estimaciones para revisar: no cambian la sugerencia ni el levantamiento. Sin costos ni precios."""
    s = _survey(db, sid, p)
    out = system_calcs(s.kind, list(s.points), s.common)
    out["survey_template_version"] = s.template_version or 1
    return out


class SuggestionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    unit: str = Field("Unid", max_length=10)
    kind: str | None = Field(None, pattern="^(equipo|material|servicio)$")
    product_id: int | None = None
    note: str | None = Field(None, max_length=200)
    item_id: int | None = None  # linea existente a la que se suma (la sugerencia la cruzo por familia de material)


@router.post("/surveys/{sid}/suggest/apply")
def survey_suggest_apply(sid: int, data: list[SuggestionIn], p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Agrega las sugerencias elegidas a lo que ya estaba. Si el material ya existe (mismo producto o mismo
    nombre) suma la cantidad en vez de duplicar la linea. Nunca borra nada."""
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    added, summed = 0, 0
    for sg in data:
        match = next((it for it in s.items if sg.item_id and it.id == sg.item_id), None) or next(
            (it for it in s.items if (sg.product_id and it.product_id == sg.product_id) or (it.name or "").strip().lower() == sg.name.strip().lower()),
            None,
        )
        if match:
            add = sg.quantity
            per = per_unit(match)
            if per != 1 and ((match.recommendation or {}).get("line") or "").strip().lower() == sg.name.strip().lower():
                add = (sg.quantity / per).to_integral_value(rounding=ROUND_CEILING)  # la sugerencia va en metros/TB; el equipo en bobinas/discos
            match.quantity = d(match.quantity) + add
            summed += 1
        else:
            kind, unit = normalize_item(sg.name, sg.kind, sg.unit)
            s.items.append(SurveyItem(product_id=sg.product_id, name=sg.name, quantity=sg.quantity, unit=unit, note=sg.note, kind=kind))
            added += 1
    audit(db, p.tenant.id, p.user.id, "suggest_apply", "survey", s.id, {"agregados": added, "sumados": summed}, ip=p.ip)
    db.commit()
    return {"added": added, "summed": summed, "survey": _survey_out(db, s)}


class ApplyProductIn(BaseModel):
    """ "Usar este": la linea generica de la sugerencia y el producto (propio o de proveedor) elegido para cubrirla.
    El costo y los datos de la oferta se recalculan en el servidor; del navegador solo viene la eleccion."""

    line: str = Field(min_length=1, max_length=200)  # nombre de la linea generica ("Cámara Bullet 4 MP")
    quantity: Decimal = Field(gt=0, le=100000)  # unidades del producto elegido
    item_id: int | None = None  # linea ya cargada que se reemplaza (la sugerencia la cruzo)
    product_id: int | None = None
    supplier_item_id: int | None = None
    code: str | None = Field(None, max_length=80)  # para volver a encontrar la oferta del proveedor
    note: str | None = Field(None, max_length=200)


@router.post("/surveys/{sid}/suggest/apply-product")
def survey_apply_product(sid: int, data: ApplyProductIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Crea o actualiza la linea del levantamiento con el equipo real elegido. Si ya habia una linea para esa
    sugerencia (generica o con otro equipo) se reemplaza ahi mismo; nunca se duplica ni se borra otra cosa."""
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    if not data.product_id and not data.supplier_item_id:
        raise HTTPException(422, "Elegí un producto del catálogo o una oferta del proveedor")
    it = next((x for x in s.items if data.item_id and x.id == data.item_id), None)
    if data.item_id and it is None:
        raise HTTPException(404, "Esa línea no pertenece a este levantamiento")
    lines = suggest_materials(s.kind, list(s.points), list(s.items), common=s.common)
    try:
        line, c = equipment_reco.resolve_choice(
            db,
            p.tenant,
            s,
            lines,
            data.line,
            product_id=data.product_id,
            supplier_item_id=None if data.product_id else data.supplier_item_id,
            code=data.code,
        )
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if it is None:
        it = next((x for x in s.items if ((x.recommendation or {}).get("line") or "").strip().lower() == line["name"].strip().lower()), None)
    proveedor = c["source"] == "proveedor" and not c["product_id"]
    rec = {
        "line": line["name"],
        "line_unit": line.get("unit"),
        "per_unit": str(c["per_unit"]),
        "source": c["source"],
        "supplier": c.get("supplier"),
        "code": c.get("code"),
        "model": c.get("model"),
        "brand": c.get("brand"),
        "reason": c.get("reason_plain"),
        "score": c.get("score"),
        "recommended": bool(c.get("meets")),
        "chosen_by": p.user.id,
        "chosen_at": datetime.now(UTC).isoformat(),
    }
    kind = line.get("kind") if line.get("kind") in ("equipo", "material") else None
    kind, unit = normalize_item(c["name"] or line["name"], kind, c.get("unit") or "Unid")
    nuevo = it is None
    if nuevo:
        it = SurveyItem(name=(c["name"] or line["name"])[:200], quantity=data.quantity)
        s.items.append(it)
    elif it.product_id != c["product_id"] or it.supplier_item_id != (c["supplier_item_id"] if proveedor else None):
        it.unit_cost = None  # el costo escrito a mano era del equipo anterior
    it.product_id = c["product_id"]
    it.name = (c["name"] or line["name"])[:200]
    it.quantity = data.quantity
    it.unit = unit
    it.kind = kind
    if data.note is not None:
        it.note = data.note or None
    it.supplier_item_id = c["supplier_item_id"] if proveedor else None
    it.supplier_cost = c.get("cost") if proveedor else None
    it.supplier_currency = (c.get("currency") or "USD") if proveedor else None
    it.recommendation = rec
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "recommend_apply",
        "survey",
        s.id,
        {
            "linea": line["name"],
            "producto": c["product_id"],
            "oferta_proveedor": c["supplier_item_id"] if proveedor else None,
            "cantidad": str(data.quantity),
            "nuevo": nuevo,
        },
        ip=p.ip,
    )
    db.commit()
    full = _survey_out(db, s)
    return {"item": next(x for x in full["items"] if x["id"] == it.id), "created": nuevo, "survey": full}


@router.post("/surveys/{sid}/send")
def survey_send(sid: int, p: Principal = Depends(require("field", "crear")), db: Session = Depends(get_db)):
    """El técnico envía el levantamiento a revisión (supervisor o administrador) y ve a quién le llegó el aviso."""
    s = _survey(db, sid, p)
    notified = send_survey(db, p.tenant, s, p.user, p.ip)
    db.commit()
    out = _survey_out(db, s)
    out["notified"] = notified
    return out


def send_survey(db: Session, tenant, s: Survey, sender: User, ip: str | None, sender_label: str | None = None) -> list[dict]:
    """Validaciones del envio, paso a revision, aviso a los revisores de hoy y bitacora. Devuelve a quien se aviso.
    No hace commit. sender_label: como se nombra al remitente en el aviso ("Nombre (Empresa aliada)")."""
    if not s.points and not s.items:
        raise HTTPException(422, "Agregá al menos un punto o material antes de enviar")
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    abiertas = [o for o in s.observations if o.resolved_at is None]
    if abiertas:
        raise HTTPException(
            409,
            f"Marcá como resueltas las {len(abiertas)} observación(es) del supervisor antes de reenviar."
            if len(abiertas) > 1
            else "Marcá como resuelta la observación del supervisor antes de reenviar.",
        )
    reenvio = s.review_status == "devuelto"
    s.status = "enviado"
    s.review_status = "pendiente"  # todo envio (o reenvio tras observaciones) vuelve a la cola de revision
    s.sent_at = datetime.now(UTC)
    s.sent_by = sender.id
    cliente = db.get(Customer, s.customer_id).name if s.customer_id else (s.contact or {}).get("name") or "sin cliente"
    spec = SPECS.get(s.kind, SPECS["otro"])
    filas = "".join(f"<li>{html.escape(x.code)} · {html.escape(x.label or '')}</li>" for x in s.points[:20])
    labor = survey_labor(s)
    personal = " · ".join(f"{LABOR_TYPES[k][0]}: {v['people']} × {v['days']:g} día(s)" for k, v in labor.items() if v["people"])
    roles = reviewer_roles(tenant)
    # Andres (13/10, 21:05) no sabia quien recibia lo enviado ni donde: el aviso lleva directo al levantamiento
    # y a la bandeja de Aprobaciones. s.id es entero: no necesita escape.
    base = cfg.public_base_url.rstrip("/")
    notify_roles(
        db,
        tenant,
        roles,
        f"{'Levantamiento corregido' if reenvio else 'Nuevo levantamiento'} {s.number} · {cliente}",
        f"<p><b>{html.escape(spec['label'])}</b> levantado por {html.escape(sender_label or sender.full_name)}.</p>"
        f"<p>Cliente: {html.escape(cliente)}<br>Sitio: {html.escape(s.site or '—')}<br>"
        f"{len(s.points)} {html.escape(spec['point_label'].lower())}(s) · {len(s.items)} materiales y equipos<br>{html.escape(personal or 'sin personal')}</p>"
        f"<ul>{filas}</ul><p>{'El técnico resolvió las observaciones. ' if reenvio else ''}Abrí el levantamiento para revisarlo: aprobalo o devolvelo con observaciones antes del costeo.</p>"
        f'<p><a href="{base}/levantamientos?id={s.id}">Abrir el levantamiento</a> · '
        f'<a href="{base}/aprobaciones">Ver todo lo pendiente en Aprobaciones</a></p>',
        "survey",
        s.id,
    )
    notified = [r for r in _reviewers(db, tenant)]
    audit(
        db,
        tenant.id,
        sender.id,
        "send",
        "survey",
        s.id,
        {"puntos": len(s.points), "materiales": len(s.items), "notificados": [r["name"] for r in notified]},
        ip=ip,
    )
    return notified


class ObservationIn(BaseModel):
    point_code: str | None = Field(None, max_length=20)  # sin dato = observacion general
    text: str = Field(min_length=1, max_length=2000)


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(aprobar|devolver)$")
    observations: list[ObservationIn] = Field(default_factory=list, max_length=60)
    note: str | None = Field(None, max_length=2000)  # comentario general (al devolver cuenta como observacion)


@router.post("/surveys/{sid}/review")
def survey_review(sid: int, data: ReviewIn, p: Principal = Depends(require("field", "revisar")), db: Session = Depends(get_db)):
    """Supervisor (o admin): aprueba lo que el tecnico envio o lo devuelve con observaciones por punto o generales."""
    s = _survey(db, sid, p)
    if s.status != "enviado" or s.review_status not in (None, "pendiente"):
        raise HTTPException(409, f"El levantamiento no está esperando revisión ({REVIEW_LABEL.get(s.review_status, s.review_status)})")
    now = datetime.now(UTC)
    codes = {x.code for x in s.points}
    obs = [o for o in data.observations if o.text.strip()]
    if data.note and data.note.strip():
        obs.append(ObservationIn(text=data.note.strip()))
    bad = sorted({o.point_code for o in obs if o.point_code and o.point_code not in codes})
    if bad:
        raise HTTPException(422, f"Punto(s) que no existen en el levantamiento: {', '.join(bad)}")
    if data.action == "devolver" and not obs:
        raise HTTPException(422, "Para devolver el levantamiento escribí al menos una observación")
    s.review_round = (s.review_round or 0) + 1
    s.reviewed_by, s.reviewed_at = p.user.id, now
    if data.action == "aprobar":
        s.review_status = "aprobado"
        audit(db, p.tenant.id, p.user.id, "review_approve", "survey", s.id, {"ronda": s.review_round, "nota": data.note}, ip=p.ip)
        db.commit()
        return _survey_out(db, s)
    for o in obs:
        s.observations.append(
            SurveyObservation(review_round=s.review_round, point_code=o.point_code or None, text=o.text.strip(), created_by=p.user.id, created_at=now)
        )
    s.review_status, s.status = "devuelto", "borrador"  # vuelve a la mano del tecnico (aparece en su dia)
    _notify_returned(db, p, s, obs)
    audit(db, p.tenant.id, p.user.id, "review_return", "survey", s.id, {"ronda": s.review_round, "observaciones": len(obs)}, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


def _notify_returned(db: Session, p: Principal, s: Survey, obs: list[ObservationIn]) -> list[str]:
    """Avisa al tecnico (y a quien lo envio, si es otro) que el levantamiento volvio con observaciones."""
    destinos: list[str] = []
    for uid in dict.fromkeys(x for x in (s.technician_id, s.sent_by) if x):
        u = db.get(User, uid)
        correo = (u.email or "").strip().lower() if u else ""
        if correo and correo not in destinos:
            destinos.append(correo)
    filas = "".join(f"<li>{html.escape(o.point_code or 'General')}: {html.escape(o.text)}</li>" for o in obs[:30])
    base = cfg.public_base_url.rstrip("/")
    cuerpo = (
        f"<p>{html.escape(p.user.full_name)} revisó el levantamiento <b>{html.escape(s.number)}</b> y lo devolvió con "
        f"{len(obs)} observación(es):</p><ul>{filas}</ul><p>Corregí, marcá cada observación como resuelta y reenvialo a revisión.</p>"
        f'<p><a href="{base}/levantamientos?id={s.id}">Abrir el levantamiento</a></p>'
    )
    for correo in destinos:
        queue_email(db, p.tenant, correo, f"Levantamiento {s.number} devuelto con observaciones", cuerpo, "survey", s.id)
    return destinos


class ResolveIn(BaseModel):
    resolved: bool = True
    resolution: str | None = Field(None, max_length=400)  # que se corrigio (o por que no aplica)


@router.post("/surveys/{sid}/observations/{oid}")
def survey_observation_resolve(sid: int, oid: int, data: ResolveIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """El tecnico marca una observacion como resuelta (o la reabre). Con todas resueltas puede reenviar."""
    s = _survey(db, sid, p)
    resolve_observation(db, s, oid, p.user, data.resolution, resolved=data.resolved, ip=p.ip)
    db.commit()
    return _survey_out(db, s)


def resolve_observation(
    db: Session, s: Survey, obs_id: int, user: User, resolution: str | None, resolved: bool = True, ip: str | None = None
) -> SurveyObservation:
    """Marca resuelta (o reabre) una observacion DE ESTE levantamiento (otra: 404). No hace commit."""
    o = next((x for x in s.observations if x.id == obs_id), None)
    if not o:
        raise HTTPException(404, "Observación no encontrada")
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    if resolved:
        o.resolved_at, o.resolved_by, o.resolution = datetime.now(UTC), user.id, (resolution or "").strip() or None
    else:
        o.resolved_at, o.resolved_by, o.resolution = None, None, None
    audit(db, s.tenant_id, user.id, "observation_resolve" if resolved else "observation_reopen", "survey", s.id, {"observacion": o.id}, ip=ip)
    return o


@router.get("/surveys/{sid}/costing")
def survey_costing(sid: int, margin: Decimal | None = None, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Costeo para administración: equipos, materiales, mano de obra por tipo, viáticos, precio sugerido y margen."""
    if not p.sees_costs:
        raise HTTPException(403, "Tu rol no ve costos")
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    fx = docsvc.today_fx(db, "USD")[0]
    margin_pct = d(margin) if margin is not None else pricing.default_margin(p.tenant)
    labor_day, transport = rates(p.tenant)
    propias = tarifas_survey(s)
    transport = propias.get("transport", transport)
    lines, cost_items, price_items = [], Decimal(0), Decimal(0)
    groups = {k: {"cost": Decimal(0), "price": Decimal(0)} for k in ("equipo", "material", "servicio")}
    for it in s.items:
        prod = db.get(Product, it.product_id) if it.product_id else None
        qty = d(it.quantity)
        unit_cost = pricing.cost_in(db, prod, "CRC", fx) if prod else Decimal(0)
        source = "catalogo" if unit_cost > 0 else None
        supplier = None
        if unit_cost <= 0 and it.unit_cost is not None and d(it.unit_cost) > 0:
            unit_cost, source = d(it.unit_cost), "levantamiento"  # escrito a mano en este costeo
        if unit_cost <= 0 and it.supplier_cost is not None and d(it.supplier_cost) > 0:
            # oferta de proveedor elegida con la recomendacion: su costo en colones con el tipo de cambio del dia
            cur = it.supplier_currency or "USD"
            rate = Decimal(1) if cur == "CRC" else (fx if cur == "USD" else docsvc.today_fx(db, cur)[0])
            unit_cost, source = d(it.supplier_cost) * d(rate), "proveedor"
            supplier = (it.recommendation or {}).get("supplier")
        if unit_cost <= 0 and prod is not None:
            unit_cost, supplier, source = _best_offer_cost(db, prod, unit_cost, source)
        # precio de lista si el producto lo tiene; si no, sale del costo con el margen objetivo
        price_from_cost = not (prod and d(prod.price) > 0)
        unit_price = pricing.sale_price(db, unit_cost, "CRC", "CRC", margin_pct) if price_from_cost else d(prod.price)
        kind = it.kind or "material"
        lines.append(
            {
                "item_id": it.id,
                "product_id": it.product_id,
                "name": it.name if not prod else prod.name,
                "kind": kind,
                "quantity": qty,
                "unit": it.unit,
                "unit_cost": unit_cost.quantize(Decimal("0.01")),
                "cost": (unit_cost * qty).quantize(Decimal("0.01")),
                "unit_price": d(unit_price).quantize(Decimal("0.01")),
                "price": (d(unit_price) * qty).quantize(Decimal("0.01")),
                "has_cost": unit_cost > 0,
                "cost_source": source,  # catalogo | levantamiento | proveedor | None
                "price_from_cost": price_from_cost,
                "supplier": supplier,
                "recommended_for": (it.recommendation or {}).get("line") if isinstance(it.recommendation, dict) else None,
            }
        )
        cost_items += unit_cost * qty
        price_items += d(unit_price) * qty
        g = groups.setdefault(kind, {"cost": Decimal(0), "price": Decimal(0)})
        g["cost"] += unit_cost * qty
        g["price"] += d(unit_price) * qty
    orden = {"equipo": 0, "material": 1, "servicio": 2}
    lines.sort(key=lambda x: orden.get(x["kind"], 1))  # equipos, materiales y servicios, como en el levantamiento

    # mano de obra por tipo de personal
    lab = labor_costing(s, p.tenant)
    types, labor_cost, pd, viaticos, travel = lab["types"], lab["labor_cost"], lab["per_diem"], lab["viaticos"], lab["travel"]
    cost_total = cost_items + labor_cost + travel
    # C desconocido no es 0: con lineas sin costo el costo total y el margen son provisionales
    missing = [line["name"] for line in lines if not line["has_cost"]]
    labor_price = pricing.sale_price(db, labor_cost + travel, "CRC", "CRC", margin_pct)
    suggested = price_items + labor_price
    return {
        "survey": _survey_out(db, s, full=False),
        "lines": lines,
        "groups": {k: {"cost": v["cost"].quantize(Decimal("0.01")), "price": v["price"].quantize(Decimal("0.01"))} for k, v in groups.items()},
        "labor": {
            "techs": s.techs,
            "days": s.days,
            "day_cost": labor_day,
            "types": types,
            "cost": labor_cost.quantize(Decimal("0.01")),
            "transport": transport,
            "transport_default": rates(p.tenant)[1],
            "transport_custom": "transport" in propias,
            "per_diem": pd,
            "per_diem_default": per_diem(p.tenant),
            "per_diem_custom": "per_diem" in propias,
            "viaticos": viaticos,
            "travel": travel,
            "price": labor_price,
        },
        "cost_total": cost_total.quantize(Decimal("0.01")),
        "price_suggested": suggested.quantize(Decimal("0.01")),
        "margin_pct": pricing.margin_of(suggested, cost_total),
        "margin_target": margin_pct,
        # la politica vigente nombrada sin ambiguedad: margen sobre la venta, con su recargo equivalente
        "pricing_policy": pricing.policy(margin_pct, suggested, cost_total),
        "fx": fx,
        "missing_cost": missing,
        "cost_complete": not missing,
        "margin_provisional": bool(missing),
        "can_save_catalog": p.can("catalog", "editar"),
    }


def _best_offer_cost(db: Session, prod: Product, unit_cost: Decimal, source: str | None) -> tuple[Decimal, str | None, str | None]:
    """Producto sin costo propio: la mejor oferta del catalogo de proveedores para ese producto (si la hay).
    Si no hay o falla, todo sigue como antes (sin costo y margen provisional)."""
    try:
        off = supplier_catalog.best_offer_for_product(db, prod)
    except Exception:  # noqa: BLE001 - el costeo nunca se cae por el catalogo de proveedores
        log.exception("best_offer_for_product fallo (producto %s)", prod.id)
        return unit_cost, None, source
    c = d(off.get("cost_crc")) if off and off.get("cost_crc") is not None else Decimal(0)
    if c > 0:
        return c, off.get("supplier"), "proveedor"
    return unit_cost, None, source


class TarifasIn(BaseModel):
    """Colones sin IVA. null = volver a la tarifa de Ajustes."""

    tecnico: Decimal | None = Field(None, ge=0, le=10_000_000)
    civil: Decimal | None = Field(None, ge=0, le=10_000_000)
    contratado: Decimal | None = Field(None, ge=0, le=10_000_000)
    transport: Decimal | None = Field(None, ge=0, le=100_000_000)
    per_diem: Decimal | None = Field(None, ge=0, le=10_000_000)


@router.put("/surveys/{sid}/labor-rates")
def survey_labor_rates(
    sid: int, data: TarifasIn, margin: Decimal | None = None, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)
):
    """Ajusta el precio de la mano de obra solo para este levantamiento, sin tocar la tarifa general.
    Es costo: solo quien ve costos."""
    if not p.sees_costs:
        raise HTTPException(403, "Tu rol no ve costos")
    s = _survey(db, sid, p)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    tarifas = {k: str(v) for k, v in data.model_dump().items() if v is not None}
    labor = dict(s.labor or {})
    if not labor:
        labor = {k: {"people": v["people"], "days": str(v["days"])} for k, v in survey_labor(s).items()}
    if tarifas:
        labor["tarifas"] = tarifas
    else:
        labor.pop("tarifas", None)
    s.labor = labor  # se reasigna entero: el JSON no detecta cambios internos
    audit(db, p.tenant.id, p.user.id, "labor_rates", "survey", s.id, tarifas, ip=p.ip)
    db.commit()
    return survey_costing(sid, margin, p, db)  # con el margen que el usuario tiene puesto en el costeo


class CostIn(BaseModel):
    item_id: int
    unit_cost: Decimal = Field(ge=0)  # colones, sin IVA
    save_to_catalog: bool = False


@router.post("/surveys/{sid}/costs")
def survey_costs(sid: int, data: list[CostIn], margin: Decimal | None = None, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Costo escrito a mano en el costeo para las lineas sin costo de proveedor. Opcionalmente se guarda en el
    producto del catalogo (en colones) para no tener que escribirlo en el proximo levantamiento."""
    if not p.sees_costs:
        raise HTTPException(403, "Tu rol no ve costos")
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if s.quote_id:
        raise HTTPException(409, "Este levantamiento ya generó una cotización")
    items = {i.id: i for i in s.items}
    saved = []
    for c in data:
        it = items.get(c.item_id)
        if it is None:
            raise HTTPException(404, f"Línea {c.item_id} no pertenece a este levantamiento")
        it.unit_cost = c.unit_cost
        if c.save_to_catalog:
            if not p.can("catalog", "editar"):
                raise HTTPException(403, "Sin permiso para editar el catálogo (catalog.editar)")
            prod = db.get(Product, it.product_id) if it.product_id else None
            if prod is None or prod.tenant_id != p.tenant.id:
                raise HTTPException(422, f"{it.name}: la línea no está enlazada a un producto del catálogo")
            # el costo del costeo esta en colones: se guarda asi, sin inventar un tipo de cambio
            prod.cost, prod.cost_currency = c.unit_cost, "CRC"
            saved.append(prod.id)
    audit(db, p.tenant.id, p.user.id, "costs", "survey", s.id, {"lineas": len(data), "catalogo": saved}, ip=p.ip)
    db.commit()
    return survey_costing(sid, margin, p, db)


class ToQuoteIn(BaseModel):
    margin: Decimal | None = Field(None, ge=0, le=95)
    labor_label: str = Field("Instalación y configuración", max_length=200)
    include_labor: bool = True
    notes: str | None = None
    # cotizar sin revision aprobada: solo con field.saltar_revision (admin) y confirmado a proposito en el portal
    skip_review: bool = False


@router.post("/surveys/{sid}/quote", status_code=201)
def survey_to_quote(sid: int, data: ToQuoteIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Convierte el levantamiento en cotización: una línea por material y una de mano de obra. No se reescribe nada."""
    s = db.get(Survey, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Levantamiento no encontrado")
    if s.quote_id:
        raise HTTPException(409, "Este levantamiento ya generó una cotización")
    if not s.customer_id:
        raise HTTPException(422, "Asigná un cliente al levantamiento antes de cotizar")
    o = db.get(Opportunity, s.opportunity_id) if s.opportunity_id else None
    o = o if o is not None and o.tenant_id == p.tenant.id else None
    if o and o.customer_id and o.customer_id != s.customer_id:
        # la cotizacion seria de la vendedora de esa oportunidad y pisaria su monto: tienen que ser del mismo cliente
        raise HTTPException(409, f"El levantamiento es de otro cliente que la oportunidad {o.number}: corregí el cliente o desligalo antes de cotizar")
    if s.review_status != "aprobado":
        estado = REVIEW_LABEL.get(s.review_status, s.review_status).lower()
        if not data.skip_review:
            extra = " Como administrador podés cotizar sin revisión confirmándolo." if p.can("field", "saltar_revision") else ""
            raise HTTPException(409, f"El levantamiento necesita la revisión aprobada del supervisor antes de cotizar (estado: {estado}).{extra}")
        if not p.can("field", "saltar_revision"):
            raise HTTPException(403, "Sin permiso: field.saltar_revision")
        audit(db, p.tenant.id, p.user.id, "review_skip", "survey", s.id, {"estado": s.review_status}, ip=p.ip)
    costing = survey_costing(sid, data.margin, p, db)
    lines = [
        LineInSchema(
            product_id=x["product_id"],
            name=x["name"],
            quantity=d(x["quantity"]),
            unit_price=d(x["unit_price"]),
            unit=x["unit"],
            # sin costo ni precio de lista no se cotiza en 0 callado: queda pendiente y bloquea enviar/convertir
            treatment="pendiente" if d(x["unit_price"]) <= 0 else "normal",
        )
        for x in costing["lines"]
        if d(x["quantity"]) > 0
    ]
    if data.include_labor:
        lines.append(
            LineInSchema(
                name=f"{data.labor_label} · "
                + (" · ".join(f"{t['label']} {t['people']} × {d(t['days']):g} día(s)" for t in costing["labor"]["types"] if t["people"]) or "sin personal"),
                quantity=Decimal(1),
                unit_price=d(costing["labor"]["price"]),
                unit="Sp",
                treatment="pendiente" if d(costing["labor"]["price"]) <= 0 else "normal",
            )
        )
    if not lines:
        raise HTTPException(422, "El levantamiento no tiene materiales con cantidad")
    payload = DocumentIn(
        customer_id=s.customer_id,
        currency="CRC",
        external_notes=data.notes
        or f"Según levantamiento técnico {s.number} ({SPECS.get(s.kind, SPECS['otro'])['label']}) en {s.site or 'sitio del cliente'}.",
        # sin cifras de costo ni margen: las notas internas las lee todo el que ve la cotizacion o la factura
        # (contador, caja, solo lectura). El costo queda en la bitacora (audit de abajo).
        internal_notes=f"Levantamiento {s.number}.",
        lines=lines,
    )
    from ..services.ownership import opportunity_seller

    # la cotizacion es de la vendedora de la oportunidad (la costea la oficina, pero la venta es de ella)
    q = docsvc.create_quote(db, p.tenant.id, p.user.id, payload, seller_id=opportunity_seller(db, o) or p.user.id)
    s.quote_id, s.status = q.id, "cotizado"
    if o:
        from .pipeline import advance_status, sync_seller_from_opportunity

        # solo avanza: una oportunidad ya "enviada" o en negociacion no vuelve a "cotizando"
        o.quote_id, o.amount = q.id, d(q.total)
        sync_seller_from_opportunity(db, o)
        advance_status(o, "cotizando")
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "quote",
        "survey",
        s.id,
        {"quote_id": q.id, "cost": str(costing["cost_total"]), "margin_pct": str(costing["margin_pct"])},
        ip=p.ip,
    )
    if o and q.seller_id == o.owner_id and q.seller_id != p.user.id:
        _notify_quote_ready(db, p, o, q)
    db.commit()
    return {"quote_id": q.id, "number": q.number, "total": q.total, "cost_total": costing["cost_total"], "margin_pct": costing["margin_pct"]}


def _notify_quote_ready(db: Session, p: Principal, o: Opportunity, q) -> None:
    """Le avisa a la vendedora que su cotizacion salio (sin montos: el correo no es lugar para cifras internas).
    Sin servicio de correo queda "simulado" como el resto; nunca frena la cotizacion."""
    from ..core.config import settings

    u = db.get(User, o.owner_id)
    correo = (u.email or "").strip() if u and u.active else ""
    if not correo:
        return
    enlace = f"{settings.public_base_url}/cotizaciones/{q.id}"
    cuerpo = (
        f"<p>La cotización <b>{html.escape(q.number)}</b> de tu oportunidad <b>{html.escape(o.number)}</b> · {html.escape(o.title)} "
        f"ya está lista, con las líneas del levantamiento.</p>"
        f'<p><a href="{html.escape(enlace)}">Abrir la cotización</a> para revisarla y enviarla al cliente.</p>'
    )
    queue_email(db, p.tenant, correo, f"Tu cotización {q.number} está lista · {o.number}", cuerpo, "quote", q.id)


# ---------- Resumen para la vendedora (solo lectura, sin un solo costo) ----------
_PASOS = {
    "borrador": "El técnico todavía lo está llenando.",
    "devuelto": "El supervisor lo devolvió con observaciones al técnico.",
    "revision": "El supervisor lo está revisando. Después administración lo costea y sale tu cotización.",
    "costeo": "Aprobado. Administración lo está costeando.",
    "cotizado": "Ya tiene cotización.",
    "cerrado": "El levantamiento está cerrado.",
}
_ORDEN_ITEMS = {"equipo": 0, "material": 1, "servicio": 2}


def _paso(s: Survey) -> str:
    if s.status == "cerrado":
        return "cerrado"
    if s.status == "cotizado" or s.quote_id:
        return "cotizado"
    if s.review_status == "devuelto":
        return "devuelto"
    if s.status == "enviado":
        return "costeo" if s.review_status == "aprobado" else "revision"
    return "borrador"


def _photo_urls(v) -> list[str]:
    """Solo URLs http(s) o rutas del sitio (nada de data: ni esquemas raros), como el informe preliminar."""
    out = []
    for x in v or []:
        u = x.get("url") if isinstance(x, dict) else x
        if isinstance(u, str) and (u.startswith(("http://", "https://", "/"))):
            out.append(u)
    return out


@router.get("/surveys/{sid}/summary")
def survey_summary(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """Levantamiento en resumen (13/10 33:16-33:40): lo que la vendedora necesita para hablar con su cliente.
    Mismo orden que el informe preliminar: notas, equipos y materiales, personal y puntos con fotos. NUNCA costos,
    precios, tarifas, transporte, viaticos ni el proveedor (D3): la prueba recorre esta respuesta buscandolos."""
    return _summary_out(db, _survey(db, sid, p), p)


def _summary_out(db: Session, s: Survey, p: Principal) -> dict:
    """El resumen de un levantamiento que ya paso el control del registro (_survey). Tambien es lo que GET
    /surveys/{id} le responde a la vendedora (solo_resumen)."""
    from ..models import Quote
    from ..services.media_access import sign_urls
    from ..services.ownership import owns_doc

    c = _customer_of(db, s)
    t = db.get(User, s.technician_id) if s.technician_id else None
    o = db.get(Opportunity, s.opportunity_id) if s.opportunity_id else None
    spec = SPECS.get(s.kind, SPECS["otro"])
    paso = _paso(s)

    def fotos(v) -> list[str]:
        return sign_urls(db, p.tenant.id, _photo_urls(v))  # ya paso el control del registro (_survey)

    def num(v) -> str:
        n = d(v)
        return str(int(n)) if n == n.to_integral_value() else f"{n.normalize():f}"

    items = sorted(s.items, key=lambda i: (_ORDEN_ITEMS.get(i.kind or "material", 1), i.id or 0))
    puntos = [
        {
            "code": x.code,
            "label": x.label,
            "kind_label": SPECS.get(x.kind or s.kind, spec)["point_label"],
            "notes": x.notes,
            "photos": fotos(x.photos),
        }
        for x in s.points
    ]
    pendientes = []
    if s.status not in ("cotizado", "cerrado"):
        pendientes = [
            {
                "name": x["name"],
                "quantity": num(x.get("add_quantity") if x.get("action") == "sumar" else x["quantity"]),
                "unit": x.get("unit"),
                "kind": x.get("kind"),
                "reason": x.get("reason"),
            }
            for x in suggest_materials(s.kind, list(s.points), list(s.items), common=s.common)
            if x.get("action") in ("nuevo", "sumar")
        ]
    cot = None
    if s.quote_id:
        q = db.get(Quote, s.quote_id)
        if q and q.tenant_id == p.tenant.id:
            # la de otro vendedor solo se nombra: sin id no se abre (y el detalle igual responde 404)
            cot = {"id": q.id, "number": q.number, "status": q.status} if owns_doc(q, p) else {"number": q.number, "status": q.status}
    return {
        "id": s.id,
        "number": s.number,
        "kind": s.kind,
        "kind_label": spec["label"],
        "customer": c.name if c else (s.contact or {}).get("name"),
        "site": s.site,
        "visit_date": s.visit_date,
        "technician": t.full_name if t else None,
        "opportunity": {"id": o.id, "number": o.number, "title": o.title} if o and o.tenant_id == p.tenant.id else None,
        "status": s.status,
        "review_label": REVIEW_LABEL.get(s.review_status, s.review_status),
        "paso": paso,
        "paso_texto": _PASOS[paso],
        "notes": s.notes,
        "equipos": [{"name": i.name, "quantity": num(i.quantity), "unit": i.unit, "kind": i.kind or "material", "note": i.note} for i in items],
        "personal": [
            {"label": LABOR_TYPES[k][0], "people": v["people"], "days": num(v["days"])} for k, v in survey_labor(s).items() if v["people"] and d(v["days"]) > 0
        ],
        "puntos": puntos,
        "fotos": fotos(s.photos),
        "sugerencias_pendientes": pendientes,
        "cotizacion": cot,
    }


# ---------- Ordenes de trabajo ----------
HORAS_JORNADA = 8  # horas por jornada para el estimado (Ajustes: tenant.settings["horas_jornada"])
MAX_CORRIENDO_H = 16  # una entrada abierta mas de esto es un olvido: hay que corregirla
CREW_ROLES = ("tecnico", "supervisor", "admin")  # quien puede recibir una orden (los de /field/technicians)
CERRADAS = ("finalizada", "cancelada")
_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic")


def _order(db: Session, oid: int, p: Principal) -> WorkOrder:
    o = db.get(WorkOrder, oid)
    if not o or o.tenant_id != p.tenant.id:
        raise HTTPException(404, "Orden de trabajo no encontrada")
    if not p.sees_field_all and o.technician_id != p.user.id and p.user.id not in (o.helpers or []):
        raise HTTPException(404, "Orden de trabajo no encontrada")
    return o


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    """Horas trabajadas. SQLite devuelve fechas sin zona y la recien escrita si la trae: se igualan antes de restar."""
    if not start or not end:
        return None
    if (start.tzinfo is None) != (end.tzinfo is None):
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    return round((end - start).total_seconds() / 3600, 2)


def _utc(dt: datetime | None) -> datetime | None:
    """Todo en UTC: lo que llega con otra zona (-06:00 del portal) se convierte antes de guardarlo (SQLite guarda la
    hora sin zona) y lo que SQLite devuelve sin zona ya es UTC."""
    if dt is None:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _fecha_cr(dt: datetime | None) -> str:
    """13 oct 8:05 a. m. (hora de Costa Rica, UTC-6 todo el ano)."""
    if not dt:
        return ""
    x = _utc(dt).astimezone(timezone(timedelta(hours=-6)))
    h = x.hour % 12 or 12
    return f"{x.day} {_MESES[x.month - 1]} {h}:{x.minute:02d} {'a. m.' if x.hour < 12 else 'p. m.'}"


def _mins(start: datetime, end: datetime) -> int:
    return max(int((_utc(end) - _utc(start)).total_seconds() // 60), 0)


def order_hours(o: WorkOrder) -> tuple[float | None, str | None]:
    """(persona-horas, fuente). Con entradas: suma de minutos cerrados / 60, fuente "registro".
    Sin entradas: _hours(started_at, finished_at), fuente "legado" (o (None, None))."""
    if o.time_entries:
        return round(sum(e.minutes or 0 for e in o.time_entries if e.ended_at is not None) / 60, 2), "registro"
    h = _hours(o.started_at, o.finished_at)
    return (h, "legado") if h is not None else (None, None)


def horas_jornada(tenant) -> Decimal:
    return d((getattr(tenant, "settings", None) or {}).get("horas_jornada") or HORAS_JORNADA)


def survey_estimate(s: Survey | None, tenant) -> tuple[float | None, str | None]:
    """Persona-horas que el levantamiento dijo que lleva la obra: sum(personas x dias) x horas de jornada."""
    if s is None:
        return None, None
    partes, persona_dias = [], Decimal(0)
    for k, v in survey_labor(s).items():
        if v["people"] and v["days"]:
            persona_dias += v["people"] * v["days"]
            partes.append(f"{LABOR_TYPES[k][0]} {v['people']} × {float(v['days']):g} día(s)")
    if persona_dias <= 0:
        return None, None
    return float(persona_dias * horas_jornada(tenant)), " · ".join(partes)


def order_estimate(db: Session, o: WorkOrder, tenant) -> tuple[float | None, str | None]:
    """Si la OT es de un proyecto con levantamiento: el estimado del levantamiento y su etiqueta
    ("Personal técnico 2 × 2 día(s) · ..."). Si no, (None, None)."""
    pr = _ref(db, Project, o.project_id, o.tenant_id)
    if not pr or not pr.survey_id:
        return None, None
    s = db.get(Survey, pr.survey_id)
    return survey_estimate(s if s and s.tenant_id == o.tenant_id else None, tenant)


def _ref(db: Session, model, rid: int | None, tenant_id: int):
    """Ficha enlazada a la orden (cliente, proyecto), solo si es de la misma empresa: un id ajeno que haya quedado en
    una orden vieja no devuelve nombres ni el levantamiento de otra empresa, ni se le escribe."""
    obj = db.get(model, rid) if rid else None
    return obj if obj is not None and obj.tenant_id == tenant_id else None


# Lo que la orden enlaza es de ESTA empresa (como TICKET_REFS): sin esto un proyecto ajeno se terminaria al finalizar
WORK_ORDER_REFS = {
    "customer_id": (Customer, "Cliente no encontrado"),
    "project_id": (Project, "Proyecto no encontrado"),
    "warehouse_id": (Warehouse, "Bodega no encontrada"),
}


def _crew(o: WorkOrder) -> set[int]:
    """Equipo de la orden: tecnico y ayudantes."""
    return {i for i in [o.technician_id, *(o.helpers or [])] if i}


def _crew_member():
    """Rol que recibe ordenes: tecnico, supervisor o admin del equipo interno, o un usuario de una empresa aliada
    (siempre ligado a su empresa; que la empresa participe en el proyecto lo valida check_ally_assignment)."""
    from ..models import TenantUser

    interno = TenantUser.role_code.in_(CREW_ROLES) & TenantUser.ally_company_id.is_(None) & TenantUser.customer_id.is_(None)
    aliado = TenantUser.role_code.in_(ALLY_USER_ROLES) & TenantUser.ally_company_id.is_not(None)
    return or_(interno, aliado)


def _eligible(db: Session, tenant_id: int, user_id: int | None) -> bool:
    """Usuario activo de la empresa con rol tecnico, supervisor o admin (o de una empresa aliada)."""
    from ..models import TenantUser

    if not user_id:
        return False
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id, TenantUser.active, _crew_member()))
    u = db.get(User, user_id) if m else None
    return bool(m and u and u.active)


def _check_crew(db: Session, tenant_id: int, ids) -> None:
    """Quien recibe la orden (tecnico o ayudante) es del equipo de la empresa: sin esto un id ajeno quedaria en la
    orden, su nombre saldria en la respuesta y se le podria abrir tiempo."""
    for uid in ids:
        if uid and not _eligible(db, tenant_id, uid):
            raise HTTPException(422, "Esa persona no puede recibir órdenes")


def _activos(db: Session, tenant_id: int, ids) -> set[int]:
    """De estos, quienes hoy pueden recibir ordenes (lo mismo que _eligible, en una sola consulta)."""
    from ..models import TenantUser

    ids = {i for i in ids if i}
    if not ids:
        return set()
    stmt = (
        select(TenantUser.user_id)
        .join(User, User.id == TenantUser.user_id)
        .where(TenantUser.tenant_id == tenant_id, TenantUser.user_id.in_(ids), TenantUser.active, _crew_member(), User.active)
    )
    return set(db.scalars(stmt))


def _member(db: Session, tenant_id: int, user_id: int) -> bool:
    """Es (o fue) del equipo interno de la empresa: alguien que ya esta en la orden aunque hoy este inactivo."""
    from ..models import TenantUser

    return (
        db.scalar(select(TenantUser.id).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id, TenantUser.role_code.not_in(CLIENT_ROLES)))
        is not None
    )


def _names(db: Session, orders: list[WorkOrder], tenant_id: int | None = None) -> dict[int, str]:
    """Nombres de todo el que aparece en estas ordenes (tecnico, ayudantes, tiempo): una consulta y solo de gente de
    la empresa (un id ajeno que haya quedado en una orden vieja no devuelve el nombre de otra empresa)."""
    from ..models import TenantUser

    ids: set[int] = set()
    for o in orders:
        ids |= _crew(o)
        for e in o.time_entries:
            ids.update(x for x in (e.user_id, e.created_by) if x)
    if not ids:
        return {}
    tid = tenant_id if tenant_id is not None else orders[0].tenant_id
    stmt = select(User.id, User.full_name).join(TenantUser, TenantUser.user_id == User.id).where(TenantUser.tenant_id == tid, User.id.in_(ids))
    return dict(db.execute(stmt).all())


def _order_out(db: Session, o: WorkOrder, full: bool = True, names: dict[int, str] | None = None, tenant=None) -> dict:
    """Orden de trabajo. Sin dinero en ningun lado: el tecnico no ve precios ni costos (Andres 22:07)."""
    c = _ref(db, Customer, o.customer_id, o.tenant_id)
    pr = _ref(db, Project, o.project_id, o.tenant_id)
    names = names if names is not None else _names(db, [o])
    now = datetime.now(UTC)
    hours, source = order_hours(o)
    abiertas = [e for e in o.time_entries if e.ended_at is None]
    out = {
        "id": o.id,
        "number": o.number,
        "title": o.title,
        "kind": o.kind,
        "status": o.status,
        "archived_at": o.archived_at,
        "trashed_at": o.trashed_at,
        "site": o.site,
        "scheduled_at": o.scheduled_at,
        "customer_id": o.customer_id,
        "customer": c.name if c else None,
        "project_id": o.project_id,
        "project": pr.number if pr else None,
        "technician_id": o.technician_id,
        "technician": names.get(o.technician_id) if o.technician_id else None,
        "helpers": o.helpers,
        "arrived_at": o.arrived_at,
        "started_at": o.started_at,
        "finished_at": o.finished_at,
        "tasks": o.tasks,
        "photos": o.photos,
        "notes": o.notes,
        "customer_signature": o.customer_signature,
        "warehouse_id": o.warehouse_id,
        "stock_applied": o.stock_applied,
        "ally_company_id": o.ally_company_id,
        "ally_company": _ally_name(db, o.ally_company_id),
        # persona-horas del registro por jornada; sin registro, de inicio a fin como antes ("legado")
        "hours": hours,
        "hours_source": source,
        "running": [{"id": e.id, "user_id": e.user_id, "user": names.get(e.user_id), "started_at": e.started_at} for e in abiertas],
        "stale": any(_utc(e.started_at) < now - timedelta(hours=MAX_CORRIENDO_H) for e in abiertas),
        "crew": [
            {"id": i, "name": names.get(i), "role": "tecnico" if i == o.technician_id else "ayudante"}
            for i in dict.fromkeys([o.technician_id, *(o.helpers or [])])
            if i
        ],
    }
    if full:
        out["materials"] = [
            {"id": m.id, "product_id": m.product_id, "name": m.name, "quantity": m.quantity, "unit": m.unit, "planned": m.planned} for m in o.materials
        ]
        if tenant is None:
            from ..models import Tenant

            tenant = db.get(Tenant, o.tenant_id)
        est, label = order_estimate(db, o, tenant)
        out["hours_estimated"] = est
        out["hours_estimated_label"] = label
        out["time_entries"] = [
            {
                "id": e.id,
                "user_id": e.user_id,
                "user": names.get(e.user_id),
                "started_at": e.started_at,
                "ended_at": e.ended_at,
                "minutes": e.minutes if e.ended_at is not None else _mins(e.started_at, now),
                "kind": e.kind,
                "note": e.note,
                "created_by": e.created_by,
                "created_by_name": names.get(e.created_by) if e.created_by else None,
            }
            for e in sorted(o.time_entries, key=lambda x: _utc(x.started_at))
        ]
        # solo en el detalle (una consulta): el portal no preselecciona para "Iniciar jornada" a quien ya no puede
        # recibir ordenes y deja quitarlo de la asignacion aunque ya no salga en /field/technicians
        activos = _activos(db, o.tenant_id, _crew(o))
        for c in out["crew"]:
            c["active"] = c["id"] in activos
    return out


class MaterialIn(BaseModel):
    id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(0), ge=0)
    unit: str = Field("Unid", max_length=10)
    planned: Decimal = Field(Decimal(0), ge=0)


class WorkOrderIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    kind: str = Field("instalacion", pattern="^(instalacion|visita|mantenimiento|soporte)$")
    project_id: int | None = None
    customer_id: int | None = None
    site: str | None = Field(None, max_length=300)
    scheduled_at: datetime | None = None
    technician_id: int | None = None
    helpers: list[int] = Field(default_factory=list)
    tasks: list[dict] = Field(default_factory=list)
    notes: str | None = None
    warehouse_id: int | None = None
    materials: list[MaterialIn] = Field(default_factory=list)
    # empresa aliada que ejecuta la orden. Sin dato: la de sus tecnicos aliados (si hay); PUT solo la cambia si viene
    ally_company_id: int | None = None


class WorkOrderEditIn(WorkOrderIn):
    """PUT: solo lo que se cambio (el formulario manda la diferencia con lo que abrio; lo que no viene no se toca)."""

    title: str | None = Field(None, min_length=2, max_length=200)


def _live_orders():
    """Ordenes activas: ni ellas ni su proyecto estan archivados o en la papelera."""
    return (live(WorkOrder), or_(WorkOrder.project_id.is_(None), WorkOrder.project_id.in_(select(Project.id).where(live(Project)))))


@router.get("/work-orders")
def work_orders(
    status: str | None = None,
    day: date | None = None,
    project_id: int | None = None,
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("field", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == p.tenant.id, *_live_orders())
    if status == "pendientes":
        stmt = stmt.where(WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")))
    elif status:
        stmt = stmt.where(WorkOrder.status == status)
    if project_id:
        stmt = stmt.where(WorkOrder.project_id == project_id)
    rows = db.scalars(stmt.order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at, WorkOrder.id.desc()).limit(limit)).all()
    if not p.sees_field_all:
        rows = [o for o in rows if o.technician_id == p.user.id or p.user.id in (o.helpers or [])]
    if day:
        rows = [o for o in rows if o.scheduled_at and o.scheduled_at.date() == day]
    names = _names(db, rows)
    return [_order_out(db, o, full=False, names=names) for o in rows]


@router.post("/work-orders", status_code=201)
def order_create(data: WorkOrderIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    check_refs(db, p.tenant.id, data.model_dump(), WORK_ORDER_REFS)  # antes de gastar el consecutivo
    _check_crew(db, p.tenant.id, [data.technician_id, *data.helpers])
    from ..services.ally_scope import check_ally_assignment, notify_ally_work

    aliada = check_ally_assignment(
        db, p.tenant.id, ally_company_id=data.ally_company_id, user_ids=[data.technician_id, *data.helpers], project_id=data.project_id
    )
    number, _ = next_number(db, p.tenant.id, "OT")
    o = WorkOrder(tenant_id=p.tenant.id, number=number, ally_company_id=aliada, **data.model_dump(exclude={"materials", "ally_company_id"}))
    for m in data.materials:
        o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))
    if o.project_id:
        pr = _ref(db, Project, o.project_id, p.tenant.id)
        if pr:
            o.customer_id = o.customer_id or pr.customer_id
            o.site = o.site or pr.site
    db.add(o)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "work_order", o.id, ip=p.ip)
    notify_ally_work(db, p.tenant, _crew(o), o.number, o.title, o.scheduled_at, entity_id=o.id)
    db.commit()
    return _order_out(db, o, tenant=p.tenant)


@router.get("/work-orders/{oid}")
def order_get(oid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    return _order_out(db, _order(db, oid, p), tenant=p.tenant)


@router.put("/work-orders/{oid}")
def order_update(oid: int, data: WorkOrderEditIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    """El formulario solo PLANIFICA: lo que el tecnico ya anoto como usado se conserva (filas por id) y lo que el
    cuerpo no trae no se toca (el portal manda solo lo que cambio: si el tecnico marco una tarea mientras el
    supervisor cambiaba el sitio, no se pisa)."""
    o = _order(db, oid, p)
    if o.stock_applied:
        raise HTTPException(409, "La orden ya descontó inventario; no se puede reescribir")
    # ally_company_id fuera del setattr masivo: un portal viejo que no lo manda no lo borra
    cambios = data.model_dump(exclude_unset=True, exclude={"materials", "ally_company_id"})
    if cambios.get("title", "") is None:
        cambios.pop("title")  # el titulo no se borra
    check_refs(db, p.tenant.id, cambios, WORK_ORDER_REFS, current=o)
    # solo se valida a quien entra (un ayudante que ya estaba y hoy esta inactivo no frena la edicion)
    nuevos = [cambios["technician_id"]] if cambios.get("technician_id") and cambios["technician_id"] != o.technician_id else []
    nuevos += [h for h in cambios.get("helpers") or [] if h not in (o.helpers or [])]
    _check_crew(db, p.tenant.id, nuevos)
    # empresa aliada: la que venga; si no viene, la que tenia (ver _reassign_ally; cambiar el proyecto la revalida)
    _reassign_ally(db, p, o, cambios, explicita="ally_company_id" in data.model_fields_set, ally_company_id=data.ally_company_id)
    for k, v in cambios.items():
        setattr(o, k, v)
    if "materials" in data.model_fields_set:
        actuales = {m.id: m for m in o.materials}
        vistos: set[int] = set()
        for m in data.materials:
            if m.id is not None and (m.id not in actuales or m.id in vistos):
                # la fila ya no esta (el tecnico la quito mientras el formulario seguia abierto) o vino repetida: no
                # se recrea con el "usado" viejo de este formulario
                continue
            fila = actuales.get(m.id) if m.id is not None else None
            if fila is not None:
                # misma fila: cambia lo planificado; el "usado" es del tecnico y se conserva
                fila.product_id, fila.name, fila.unit, fila.planned = m.product_id, m.name, m.unit, m.planned
                vistos.add(m.id)
            else:
                o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))
        for mid, fila in actuales.items():
            if mid not in vistos:
                o.materials.remove(fila)
    db.commit()
    return _order_out(db, o, tenant=p.tenant)


def _reassign_ally(db: Session, p: Principal, o: WorkOrder, cambios: dict, *, explicita: bool = False, ally_company_id: int | None = None) -> None:
    """Empresa aliada de la orden al editarla o asignarla (spec 7.4). Avisa a quien entra de un aliado.

    - Si la mandan (explicita), esa: se valida contra el equipo y el proyecto.
    - Si no viene, se pasa la que ya tenia: un PUT o una asignacion rapida que no la trae no la borra. La OT asignada solo
      a la EMPRESA (sin tecnico: la reparte su gerente) sigue en su portal aunque se reprograme o se le mande el mismo
      equipo; un tecnico de otra empresa aliada da 422 (cambiar de empresa se hace mandandola).
    - Unica excepcion: un cambio REAL del equipo hacia uno solo de Crimson (al menos una persona y ninguna de una
      empresa aliada) devuelve la orden a Crimson. Quitar a todos no: la orden sigue siendo de la empresa.
    Sin empresa explicita, sin cambio real de equipo y sin cambio de proyecto no hay nada que recalcular."""
    from ..models import TenantUser
    from ..services.ally_scope import check_ally_assignment, notify_ally_work

    tec = cambios.get("technician_id", o.technician_id)
    ayu = cambios.get("helpers", o.helpers) or []
    pid = cambios.get("project_id", o.project_id)
    antes = _crew(o)
    despues = {i for i in [tec, *ayu] if i}
    if not explicita and despues == antes and pid == o.project_id:
        return
    cid = ally_company_id if explicita else o.ally_company_id
    if not explicita and despues != antes and despues and cid is not None:
        aliados = db.scalar(
            select(TenantUser.id).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id.in_(despues), TenantUser.ally_company_id.is_not(None)).limit(1)
        )
        if aliados is None:
            cid = None  # equipo nuevo solo de Crimson: la orden vuelve a Crimson
    # solo se exige activo a quien entra: un ayudante aliado que ya estaba y hoy esta desactivado no traba la edicion
    o.ally_company_id = check_ally_assignment(
        db, p.tenant.id, ally_company_id=cid, user_ids=[tec, *ayu], project_id=pid, current=o.ally_company_id, existing=antes
    )
    nuevos = despues - antes
    if nuevos:
        notify_ally_work(db, p.tenant, nuevos, o.number, cambios.get("title") or o.title, cambios.get("scheduled_at", o.scheduled_at), entity_id=o.id)


class OrderAssignIn(BaseModel):
    technician_id: int | None = None
    helpers: list[int] | None = None
    scheduled_at: datetime | None = None


@router.patch("/work-orders/{oid}/assign")
def order_assign(oid: int, data: OrderAssignIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    """Asignacion rapida desde el detalle: tecnico, ayudantes y fecha (solo lo enviado). Sin tecnico la orden no le
    aparece a nadie en "Mi dia"."""
    o = _order(db, oid, p)
    if o.status in CERRADAS:
        raise HTTPException(409, f"La orden está {o.status}; ya no se modifica")
    cambios = data.model_dump(exclude_unset=True)
    # como en el PUT, solo se valida a quien entra: un ayudante que ya estaba y hoy esta inactivo (el portal lo manda
    # de vuelta en "helpers") no traba la asignacion; quitarlo tambien vale
    nuevos = [cambios["technician_id"]] if cambios.get("technician_id") and cambios["technician_id"] != o.technician_id else []
    nuevos += [h for h in cambios.get("helpers") or [] if h not in (o.helpers or [])]
    _check_crew(db, p.tenant.id, nuevos)
    # sin empresa en el cuerpo: la orden conserva la suya (la tarjeta manda tecnico y ayudantes aunque solo cambie la fecha)
    _reassign_ally(db, p, o, cambios)
    if "technician_id" in cambios:
        o.technician_id = cambios["technician_id"]
    if "helpers" in cambios:
        o.helpers = [i for i in dict.fromkeys(cambios["helpers"] or []) if i != o.technician_id]
    elif "technician_id" in cambios and o.technician_id in (o.helpers or []):
        o.helpers = [i for i in o.helpers if i != o.technician_id]
    if "scheduled_at" in cambios:
        o.scheduled_at = _utc(cambios["scheduled_at"])
    diff = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in cambios.items()}
    audit(db, p.tenant.id, p.user.id, "assign", "work_order", o.id, diff, ip=p.ip)
    db.commit()
    return _order_out(db, o, tenant=p.tenant)


# ---------- Tiempo por jornada y por persona ----------
class TimeIn(BaseModel):
    user_id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    minutes: int | None = Field(None, ge=1, le=1440)
    note: str | None = Field(None, max_length=200)


def _time_rules(db: Session, o: WorkOrder, p: Principal, data: TimeIn, entry=None) -> tuple[int, datetime, datetime | None]:
    """Valida una entrada manual (nueva o corregida). Devuelve (persona, inicio, fin). Un fin None solo vale para
    corregir el inicio de una entrada que sigue corriendo."""
    supervisa = p.can("field", "asignar")
    uid = data.user_id or (entry.user_id if entry is not None else p.user.id)
    if uid != p.user.id and not supervisa:
        raise HTTPException(403, "El tiempo de otra persona lo registra el supervisor")
    # quien entra al equipo tiene que poder recibir ordenes; quien ya esta, al menos ser de la empresa (una orden
    # vieja con un id ajeno no sirve para abrirle tiempo a nadie de afuera)
    if not (_member(db, p.tenant.id, uid) if uid in _crew(o) else _eligible(db, p.tenant.id, uid)):
        raise HTTPException(422, "Esa persona no puede recibir órdenes")
    inicio = _utc(data.started_at)
    if data.ended_at is not None:
        fin = _utc(data.ended_at)
    elif data.minutes is not None:
        fin = inicio + timedelta(minutes=data.minutes)
    elif entry is not None and entry.ended_at is None:
        fin = None  # sigue corriendo: solo se corrige la hora de inicio
    else:
        raise HTTPException(422, "Indicá hasta qué hora o cuántos minutos")
    now = datetime.now(UTC)
    if fin is not None and fin <= inicio:
        raise HTTPException(422, "La hora de fin tiene que ser después del inicio")
    if fin is not None and fin - inicio > timedelta(hours=24):
        raise HTTPException(422, "Una entrada no puede pasar de 24 horas: registrá cada jornada aparte")
    if inicio > now + timedelta(minutes=5) or (fin is not None and fin > now + timedelta(minutes=5)):
        raise HTTPException(422, "No se registra tiempo a futuro")
    hasta = fin or now
    for e in o.time_entries:
        if e is entry or e.user_id != uid:
            continue
        e_fin = _utc(e.ended_at) if e.ended_at is not None else now
        if inicio < e_fin and _utc(e.started_at) < hasta:
            nombre = db.get(User, uid)
            raise HTTPException(409, f"Ya hay tiempo de {nombre.full_name if nombre else 'esa persona'} en ese rango")
    if uid not in _crew(o):
        o.helpers = [*(o.helpers or []), uid]  # el supervisor le registra tiempo: queda como ayudante
    return uid, inicio, fin


def _time_entry(db: Session, o: WorkOrder, eid: int, p: Principal):
    e = next((x for x in o.time_entries if x.id == eid), None)
    if e is None:
        raise HTTPException(404, "Entrada de tiempo no encontrada")
    if not p.can("field", "asignar"):
        if e.user_id != p.user.id:
            raise HTTPException(403, "Solo podés corregir tu propio tiempo")
        if o.status in CERRADAS:
            raise HTTPException(409, f"La orden está {o.status}; el tiempo lo corrige el supervisor")
    return e


def _entry_snap(e) -> dict:
    return {
        "user_id": e.user_id,
        "started_at": _utc(e.started_at).isoformat(),
        "ended_at": _utc(e.ended_at).isoformat() if e.ended_at else None,
        "minutes": e.minutes,
        "note": e.note,
    }


# Antes que POST /work-orders/{oid}/{action}: si no, Starlette la toma como action="time" ("Acción no soportada")
@router.post("/work-orders/{oid}/time", status_code=201)
def order_time_add(oid: int, data: TimeIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Tiempo manual: un olvido o una correccion. El tecnico solo el suyo; el supervisor el de cualquiera del equipo.
    Queda marcado "manual" y en la bitacora."""
    o = _order(db, oid, p)
    if o.status in CERRADAS and not p.can("field", "asignar"):
        raise HTTPException(409, f"La orden está {o.status}; el tiempo lo corrige el supervisor")
    uid, inicio, fin = _time_rules(db, o, p, data)
    from ..models import WorkOrderTime

    e = WorkOrderTime(
        tenant_id=o.tenant_id, user_id=uid, started_at=inicio, ended_at=fin, minutes=_mins(inicio, fin), kind="manual", note=data.note, created_by=p.user.id
    )
    o.time_entries.append(e)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "time_add", "work_order", o.id, {"entry": e.id, **_entry_snap(e)}, ip=p.ip)
    db.commit()
    out = _order_out(db, o, tenant=p.tenant)
    out["entry_id"] = e.id
    return out


@router.put("/work-orders/{oid}/time/{eid}")
def order_time_edit(oid: int, eid: int, data: TimeIn, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    o = _order(db, oid, p)
    e = _time_entry(db, o, eid, p)
    antes = _entry_snap(e)
    uid, inicio, fin = _time_rules(db, o, p, data, entry=e)
    e.user_id, e.started_at, e.ended_at, e.note = uid, inicio, fin, data.note if data.note is not None else e.note
    e.minutes = _mins(inicio, fin) if fin is not None else None
    audit(db, p.tenant.id, p.user.id, "time_edit", "work_order", o.id, {"entry": e.id}, ip=p.ip, before=antes, after=_entry_snap(e))
    db.commit()
    return _order_out(db, o, tenant=p.tenant)


@router.delete("/work-orders/{oid}/time/{eid}", status_code=204)
def order_time_delete(oid: int, eid: int, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    o = _order(db, oid, p)
    e = _time_entry(db, o, eid, p)
    audit(db, p.tenant.id, p.user.id, "time_delete", "work_order", o.id, {"entry": e.id}, ip=p.ip, before=_entry_snap(e))
    o.time_entries.remove(e)
    db.commit()


class ProgressIn(BaseModel):
    tasks: list[dict] | None = None
    photos: list | None = None
    notes: str | None = None
    # lista completa (compatibilidad): reemplaza las filas. El detalle manda quantities/removed (ids estables)
    materials: list[MaterialIn] | None = None
    customer_signature: str | None = Field(None, max_length=160)
    workers: list[int] | None = None  # start/pause: quienes trabajan (por defecto todo el equipo)
    quantities: dict[int, Annotated[Decimal, Field(ge=0)]] | None = None  # id de material -> usado
    removed: list[int] | None = None  # ids de material que no se usaron


ACTIONS = ("arrive", "start", "pause", "progress", "finish", "cancel")


def _close_running(o: WorkOrder, now: datetime, workers: set[int] | None = None) -> int:
    n = 0
    for e in o.time_entries:
        if e.ended_at is None and (workers is None or e.user_id in workers):
            e.ended_at = now
            e.minutes = _mins(e.started_at, now)
            n += 1
    return n


@router.post("/work-orders/{oid}/{action}")
def order_action(oid: int, action: str, data: ProgressIn | None = None, p: Principal = Depends(require("field", "editar")), db: Session = Depends(get_db)):
    """Pantalla del técnico: llegué / iniciar jornada / terminar jornada / avanzar / finalizar. Al finalizar descuenta
    el material usado. Iniciar abre una entrada de tiempo por persona; terminar y finalizar las cierran."""
    o = _order(db, oid, p)
    if action not in ACTIONS:
        raise HTTPException(404, "Acción no soportada")
    apply_order_progress(db, o, data or ProgressIn(), action, p, p.tenant)
    db.commit()
    return _order_out(db, o, tenant=p.tenant)


def apply_order_progress(db: Session, o: WorkOrder, data: ProgressIn, action: str, actor: Principal, tenant) -> None:
    """Llegué / iniciar / pausar / avanzar / finalizar / cancelar sobre una orden YA autorizada por quien llama (el
    endpoint interno con _order, el portal de aliados con su AllyScope). Al finalizar descuenta el material una sola vez,
    termina el proyecto si era la ultima orden y avisa a administracion. No hace commit."""
    p = actor
    if o.status in CERRADAS:
        raise HTTPException(409, f"La orden está {o.status}; ya no se modifica")
    now = datetime.now(UTC)
    quienes = set(data.workers) if data.workers else None

    # validaciones antes de tocar nada (un 409/403 no deja la orden a medias)
    if action in ("pause", "finish", "cancel"):
        corriendo = [e for e in o.time_entries if e.ended_at is None and (action != "pause" or quienes is None or e.user_id in quienes)]
        viejas = [e for e in corriendo if _utc(e.started_at) < now - timedelta(hours=MAX_CORRIENDO_H)]
        if viejas:
            raise HTTPException(409, f"Hay tiempo corriendo desde {_fecha_cr(min(e.started_at for e in viejas))}: corregí la hora real antes de seguir")
        if action == "pause" and not corriendo:
            raise HTTPException(409, "No hay tiempo corriendo")
    if action == "start":
        equipo = _crew(o)
        # sin "workers" trabaja el equipo que hoy puede recibir ordenes (un ayudante dado de baja no abre jornada)
        trabajan = quienes if quienes is not None else {uid for uid in equipo if _eligible(db, tenant.id, uid)}
        if not trabajan:
            raise HTTPException(409, "Asigná un técnico antes de iniciar")
        fuera = trabajan - equipo
        if fuera and not p.can("field", "asignar"):
            raise HTTPException(403, "Solo quien asigna puede sumar gente a la orden")
        bajas = sorted(uid for uid in trabajan if not _eligible(db, tenant.id, uid))
        if bajas:
            # del equipo pero dado de baja (inactivo o cambio de rol): se dice quien. El nombre sale de _names, que solo
            # devuelve gente de esta empresa
            nombres = _names(db, [o])
            nombre = next((nombres[u] for u in bajas if u in equipo and nombres.get(u)), None)
            raise HTTPException(
                422, f"{nombre} ya no puede recibir órdenes: quitalo de quienes trabajan hoy" if nombre else "Esa persona no puede recibir órdenes"
            )

    if data.tasks is not None:
        o.tasks = data.tasks
    if data.photos is not None:
        o.photos = data.photos
    if data.notes is not None:
        o.notes = data.notes
    if data.customer_signature is not None:
        o.customer_signature = data.customer_signature
    if not o.stock_applied:
        if data.materials is not None:
            o.materials.clear()
            for m in data.materials:
                o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))
        if data.quantities:
            for m in o.materials:
                if m.id in data.quantities:
                    m.quantity = data.quantities[m.id]
        if data.removed:
            for m in [x for x in o.materials if x.id in set(data.removed)]:
                o.materials.remove(m)

    if action == "arrive":
        o.arrived_at, o.status = o.arrived_at or now, "en_sitio"
    elif action == "start":
        if fuera:
            o.helpers = [*(o.helpers or []), *sorted(fuera)]
        from ..models import WorkOrderTime

        ya = {e.user_id for e in o.time_entries if e.ended_at is None}
        for uid in sorted(trabajan - ya):
            o.time_entries.append(WorkOrderTime(tenant_id=o.tenant_id, user_id=uid, started_at=now, kind="reloj", created_by=p.user.id))
        o.started_at, o.status = o.started_at or now, "en_proceso"
    elif action == "pause":
        _close_running(o, now, quienes)
    elif action == "progress":
        pass
    elif action == "finish":
        # Acuerdo con Andres en la reunion del 22/09: no se cierra un trabajo con "instale 5 camaras" en una
        # nota. Si un material estaba planificado hay que anotar cuanto se uso; si no se uso, se quita la linea.
        # Sin eso no queda respaldo de lo que realmente paso en el sitio.
        sin_anotar = [m.name for m in o.materials if d(m.planned) > 0 and d(m.quantity) <= 0]
        if sin_anotar:
            raise HTTPException(
                422,
                "Anotá cuánto usaste de: " + ", ".join(sin_anotar[:6]) + ("…" if len(sin_anotar) > 6 else "") + ". Si no se usó, quitá la línea.",
            )
        _close_running(o, now)  # despues de validar: un 422 no deja nada cerrado
        if not o.started_at:
            o.started_at = now
        o.finished_at, o.status = now, "finalizada"
        consumed = _apply_materials(db, o, p)
        audit(db, tenant.id, p.user.id, "finish", "work_order", o.id, {"materiales": consumed}, ip=p.ip)
        if o.project_id:
            pr = _ref(db, Project, o.project_id, o.tenant_id)  # nunca se termina el proyecto de otra empresa
            if pr and all(x.status in CERRADAS for x in pr.orders if x.trashed_at is None) and pr.status != "terminado":
                pr.status = "terminado"
                horas = sum((order_hours(x)[0] or 0 for x in pr.orders), 0.0)
                notify_roles(
                    db,
                    tenant,
                    ("admin", "contabilidad"),
                    f"Proyecto {pr.number} terminado · listo para facturación",
                    f"<p><b>{html.escape(pr.name)}</b> quedó terminado.</p>"
                    f"<p>{len(pr.orders)} orden(es) de trabajo · {round(horas, 1)} horas de campo.</p>"
                    "<p>El informe técnico de entrega ya se puede imprimir desde la ficha del proyecto.</p>",
                    "project",
                    pr.id,
                )
    elif action == "cancel":
        _close_running(o, now)
        o.status = "cancelada"


def _apply_materials(db: Session, o: WorkOrder, p: Principal) -> int:
    """Descuenta del inventario lo realmente utilizado (una sola vez por orden)."""
    if o.stock_applied:
        return 0
    wh = o.warehouse_id or (invsvc.default_warehouse(db, o.tenant_id).id if invsvc.default_warehouse(db, o.tenant_id) else None)
    n = 0
    for m in o.materials:
        if not m.product_id or d(m.quantity) <= 0 or not wh:
            continue
        invsvc.move(db, o.tenant_id, p.user.id, m.product_id, wh, "salida", -d(m.quantity), reference=o.number, note=f"Orden {o.number}")
        n += 1
    o.stock_applied = True
    # reservas formales del proyecto: lo usado las consume (no cambia el descuento de arriba)
    from ..services.reservations import consume_for_order

    consume_for_order(db, o)
    return n


@router.get("/work-orders/meta/today")
def my_day(p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """Pantalla de inicio del técnico: lo de hoy y lo pendiente."""
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == p.tenant.id, WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")), *_live_orders())
    rows = db.scalars(stmt.order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at)).all()
    if not p.sees_field_all:
        rows = [o for o in rows if o.technician_id == p.user.id or p.user.id in (o.helpers or [])]
    today = date.today()
    names = _names(db, rows)
    return {
        "today": [_order_out(db, o, full=False, names=names) for o in rows if o.scheduled_at and o.scheduled_at.date() == today],
        "next": [_order_out(db, o, full=False, names=names) for o in rows if not o.scheduled_at or o.scheduled_at.date() != today][:10],
        "surveys": [
            _survey_out(db, s, full=False)
            for s in db.scalars(
                select(Survey)
                .where(Survey.tenant_id == p.tenant.id, Survey.status == "borrador", Survey.technician_id == p.user.id, live(Survey))
                .order_by(Survey.id.desc())
                .limit(5)
            )
        ],
    }


# ---------- Informe preliminar (para el cliente, antes de cotizar) ----------
@router.get("/surveys/{sid}/report.pdf")
def survey_report(sid: int, p: Principal = Depends(require("field", "ver")), db: Session = Depends(get_db)):
    """PDF sin un solo monto (ver services/survey_report.py). Sin WeasyPrint (Windows local) devuelve el HTML
    imprimible con X-PDF-Fallback, igual que las cotizaciones."""
    from fastapi.responses import HTMLResponse, Response

    from ..services.render import render_pdf
    from ..services.survey_report import render_survey_html

    s = _survey(db, sid, p)
    page = render_survey_html(db, s, p.tenant)
    pdf = render_pdf(page)
    if pdf is None:
        return HTMLResponse(page, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Informe-{s.number}.pdf"'})


class ReportSendIn(BaseModel):
    to: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=2000)


@router.post("/surveys/{sid}/report/email")
def survey_report_send(sid: int, data: ReportSendIn, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    """Manda el informe preliminar al cliente (el informe va en el cuerpo del correo). Igual que las cotizaciones:
    solo cuenta como enviado si el proveedor acepto el correo; simulado o error no."""
    from ..services.mail import queue_email
    from ..services.survey_report import render_survey_html

    s = _survey(db, sid, p)
    c = db.get(Customer, s.customer_id) if s.customer_id else None
    to = (data.to or (c.email if c else None) or (s.contact or {}).get("email") or "").strip()
    if not to or "@" not in to:
        raise HTTPException(422, "El cliente no tiene correo; indicá uno")
    from ..services.media_access import email_ttl  # fotos privadas: el cliente abre el correo dias despues

    page = render_survey_html(db, s, p.tenant, ttl=email_ttl())
    if data.message:
        page = page.replace("<body>", f'<body><p style="white-space:pre-wrap">{html.escape(data.message)}</p>', 1)
    m = queue_email(db, p.tenant, to, f"Informe preliminar {s.number} · {p.tenant.name}", page, "survey", s.id)
    sent = m.status == "enviado"
    audit(db, p.tenant.id, p.user.id, "send_report", "survey", s.id, {"to": to, "mail": m.status}, ip=p.ip)
    db.commit()
    if m.status == "simulado":
        note = "El correo NO salió: no hay servicio de correo configurado (falta la llave de Resend). No se registró como enviado."
    elif m.status == "error":
        note = f"El correo NO salió ({(m.error or 'error del proveedor')[:160]}). No se registró como enviado."
    else:
        note = None
    return {"status": m.status, "sent": sent, "to": to, "note": note}


# Archivo y papelera: se monta aqui para no tocar main.py (ver routers/archive.py)
from .archive import router as _archive_router  # noqa: E402

router.include_router(_archive_router)
