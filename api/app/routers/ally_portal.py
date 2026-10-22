"""Portal de aliados (/aliado): lo que ve un usuario de una empresa aliada (roles aliado y tecnico_aliado).

Aislamiento (las reglas que no se negocian, aliado_final §4):
- La empresa aliada sale SIEMPRE de la membresia del usuario en el servidor (core/ally.py), nunca del navegador. Ningun
  endpoint recibe un ally_company_id, customer_id ni tenant_id; las entradas usan extra="forbid".
- Todo pasa por services/ally_scope.py (deny by default): lo que no esta EN COMUN responde 404 ("... no encontrado"),
  sin confirmar que existe. 403 solo para "su rol no hace esto" o empresa suspendida/inactiva.
- Salidas armadas a mano, campo por campo (listas blancas). Levantamiento y OT reutilizan el formulario interno y por
  eso se PROYECTAN por claves permitidas (ALLY_*_KEYS) desde fieldwork._survey_out/_order_out: una clave nueva que
  agregue otro frente queda fuera sola. Nunca salen costos, margen, utilidad, precio de venta del proyecto, montos de
  la oportunidad, cotizaciones o facturas al cliente, bitacora interna, notas internas no compartidas, montos de
  contratos, proveedores de Crimson, IP/MAC/firmware de activos, otros aliados ni el % de recargo.
- El aliado nunca entra a un endpoint interno: get_principal lo rechaza (core/deps.py). Las escrituras usan las
  funciones publicas de fieldwork/support_desk DESPUES del control de alcance de aqui.
- "Ver como este aliado" (admin, X-View-As-Ally): mismas reglas y datos reales, solo lectura (core/ally.py).
"""

from __future__ import annotations

import hashlib
import html
import importlib
import importlib.util
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.ally import AllyCtx, get_ally, need
from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.ratelimit import SlidingWindow, ally_assign_limiter, ally_invite_limiter, ally_media_limiter, ally_notify_limiter, ally_ticket_limiter
from ..models import (
    AllyCostRequest,
    AllyParticipation,
    Category,
    Customer,
    CustomerAsset,
    Invitation,
    Invoice,
    MaintenanceContract,
    Media,
    Opportunity,
    Product,
    Project,
    SupportNote,
    SupportTicket,
    Survey,
    TenantUser,
    User,
    WorkOrder,
)
from ..models.allies import COST_OPEN
from ..services import ally_access, ally_pricing
from ..services import media_access as ma
from ..services import sla as slasvc
from ..services.archive import live
from ..services.documents import audit, local_date
from ..services.email_layout import cta
from ..services.fx import rate_or_none
from ..services.mail import notify_roles
from ..services.render import render_html, render_pdf
from ..services.sequences import next_number
from ..services.survey_specs import system_calcs
from ..services.totals import d
from . import fieldwork as fw
from .media import MAX_IMAGE, MAX_PDF, public_url, sniff

router = APIRouter(prefix="/aliado", tags=["portal de aliados"], dependencies=[Depends(get_ally)])

OPEN_TICKET = slasvc.ABIERTOS
# informe preliminar del levantamiento (item 19): arma un PDF con todas las fotos, tope por persona. Aqui y no en
# core/ratelimit.py para no tocar un archivo compartido
ally_report_limiter = SlidingWindow(limit=30, seconds=600)
OPP_CLOSED = ("ganada", "perdida")
# oportunidad propuesta por el aliado (item 16): mientras Crimson no la aprueba no se levanta sobre ella
APPROVAL_BLOCKS = ("pendiente", "rechazada")
APPROVAL_LABEL = {"pendiente": "Pendiente de aprobación de Crimson", "aprobada": "Aprobada por Crimson", "rechazada": "No aprobada por Crimson"}
MAX_PHOTOS = 8
TICKET_KINDS = ("soporte", "garantia", "mantenimiento", "visita", "consulta")
# cancelar es de Crimson. pause = "Terminar jornada" (cierra el tiempo corriendo de SU equipo)
ORDER_ACTIONS = ("arrive", "start", "pause", "progress", "finish")

# Proyeccion por lista blanca de lo interno (fieldwork) que reutiliza el formulario de campo
ALLY_SURVEY_KEYS = (
    "id", "number", "kind", "kind_label", "status", "customer_id", "customer", "contact", "site", "opportunity_id", "opportunity",
    "technician_id", "technician", "visit_date", "techs", "days", "notes", "points_count", "created_at", "sent_at", "sent_by",
    "labor", "template_version", "received_by", "review_status", "review_label", "review_round", "open_observations",
)  # fmt: skip
ALLY_SURVEY_FULL_KEYS = ("common",)
ALLY_POINT_KEYS = ("id", "code", "kind", "label", "data", "data_status", "notes")
ALLY_ITEM_KEYS = ("id", "product_id", "name", "quantity", "unit", "note", "kind")
ALLY_ORDER_KEYS = (
    "id", "number", "title", "kind", "status", "site", "scheduled_at", "customer_id", "customer", "project_id", "project",
    "technician_id", "technician", "helpers", "arrived_at", "started_at", "finished_at", "hours", "tasks", "notes", "customer_signature",
)  # fmt: skip
ALLY_MATERIAL_KEYS = ("id", "product_id", "name", "quantity", "unit", "planned")


# ---------- Ayudantes ----------
def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if not dt or dt.tzinfo else dt.replace(tzinfo=UTC)


def _pick(src: dict, keys) -> dict:
    return {k: src.get(k) for k in keys}


def _sign(ctx: AllyCtx, urls) -> list:
    return ma.sign_urls(ctx.scope.db, ctx.p.tenant.id, list(urls or []))


def _first(name: str | None) -> str | None:
    return (name or "").split()[0] if name else None


def _user_name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return u.full_name if u else None


def _url_of(x) -> str:
    return str(x.get("url") if isinstance(x, dict) else x or "")


def _fotos_del_equipo(db: Session, ctx: AllyCtx, urls, existing=()) -> list:
    """Cada foto tiene que ser una que ya estaba en ese registro, o un archivo que subio alguien de SU equipo
    (patron client_portal._fotos_propias): nada de URL externas ni de la biblioteca de Crimson."""
    urls = list(urls or [])
    if len(urls) > 60:
        raise HTTPException(422, "Demasiadas fotografías")
    ya = {ma.base_url(_url_of(x)) for x in existing or []}
    team = ctx.scope.team_user_ids()
    out = []
    for x in urls:
        u = ma.base_url(_url_of(x))
        if u and u in ya:
            out.append(u if not isinstance(x, dict) else {**x, "url": u})
            continue
        key = ma.key_of(u)
        m = db.scalar(select(Media).where(Media.key == key, Media.tenant_id == ctx.p.tenant.id, Media.created_by.in_(team))) if key and team else None
        if not m:
            raise HTTPException(422, "Una de las fotografías no es válida. Vuelva a subirla.")
        nueva = public_url(m)
        out.append(nueva if not isinstance(x, dict) else {**x, "url": nueva})
    return out


def _attachments(db: Session, ctx: AllyCtx, ids: list[int] | None) -> list[dict]:
    out = []
    team = ctx.scope.team_user_ids()
    for mid in dict.fromkeys(ids or []):
        m = db.get(Media, mid)
        if not m or m.tenant_id != ctx.p.tenant.id or m.created_by not in team:
            raise HTTPException(404, "Adjunto no encontrado")
        out.append({"id": m.id, "url": public_url(m), "filename": m.filename})
    return out


def _product(db: Session, ctx: AllyCtx, pid: int | None) -> Product | None:
    if pid is None:
        return None
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != ctx.p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    return pr


def _team_member(ctx: AllyCtx, uid: int | None) -> TenantUser:
    m = next((x for x in ctx.scope.active_team() if x.user_id == uid), None)
    if m is None:
        raise HTTPException(422, "Ese técnico no es de su empresa")
    return m


# ---------- Salidas: oportunidad, solicitud, proyecto ----------
def _my_parts(db: Session, ctx: AllyCtx, opportunity_id: int | None = None, project_id: int | None = None) -> list[AllyParticipation]:
    q = ctx.scope.participations()
    q = q.where(AllyParticipation.opportunity_id == opportunity_id) if opportunity_id is not None else q.where(AllyParticipation.project_id == project_id)
    return list(db.scalars(q.order_by(AllyParticipation.id)))


def _request_out(db: Session, ctx: AllyCtx, r: AllyCostRequest) -> dict:
    return {
        "id": r.id,
        "participation_id": r.participation_id,
        "opportunity_id": r.opportunity_id,
        "what": r.what,
        "due_date": r.due_date,
        "status": r.status,
        "source": r.source or "crimson",
        "amount": r.amount,
        "currency": r.currency,
        "valid_until": r.valid_until,
        "exclusions": r.exclusions,
        "lines": r.lines or [],
        "attachments": _sign(ctx, r.attachments or []),
        "responded_at": r.responded_at,
        "responsible": _first(_user_name(db, r.responsible_id)),
        "can_respond": r.status in COST_OPEN and ctx.preview_by is None,
        "created_at": r.created_at,
    }


def _requests_of(db: Session, ctx: AllyCtx, parts: list[AllyParticipation]) -> list[dict]:
    return [_request_out(db, ctx, r) for a in parts for r in a.requests]


def _customer_name(db: Session, cid: int | None, contact: dict | None = None) -> str | None:
    c = db.get(Customer, cid) if cid else None
    return c.name if c else (contact or {}).get("name")


def _opp_out(db: Session, ctx: AllyCtx, o: Opportunity, full: bool = False) -> dict:
    parts = _my_parts(db, ctx, opportunity_id=o.id)
    pr = None
    if o.project_id:
        pr = db.scalar(select(Project).where(Project.id == o.project_id, *ctx.scope.projects()))
    owner = db.get(User, o.owner_id) if o.owner_id else None
    out = {
        "id": o.id,
        "number": o.number,
        "title": o.title,
        "kind": o.kind or "proyecto",
        "status": o.status,
        "solution": o.solution,
        "customer": _customer_name(db, o.customer_id, o.contact),
        "end_customer": _customer_name(db, o.end_customer_id),
        "site": o.site,
        "crimson_owner": owner.full_name if owner else None,
        "my_roles": sorted({a.role for a in parts}),
        "pending_requests": sum(1 for a in parts for r in a.requests if r.status == "solicitado"),
        "project": {"id": pr.id, "number": pr.number, "status": pr.status} if pr else None,
        "created_at": o.created_at,
        # propuesta por un aliado (item 16): solo la propia; de otra empresa no dice nada
        "approval": o.ally_approval if o.ally_proposed_by == ctx.company.id else None,
        "approval_label": APPROVAL_LABEL.get(o.ally_approval) if o.ally_proposed_by == ctx.company.id else None,
        "proposed_by_me": o.ally_proposed_by == ctx.company.id,
        # el motivo con el que Crimson no la aprobo (lo pidio como "lo ve el aliado"): solo a quien la propuso
        "approval_reason": o.lost_reason if o.ally_proposed_by == ctx.company.id and o.ally_approval == "rechazada" else None,
    }
    if full:
        contact = o.contact or {}
        out["contact"] = {"name": contact.get("name"), "phone": contact.get("phone")}
        out["participations"] = [
            {"id": a.id, "role": a.role, "scope": a.scope, "scope_lines": a.scope_lines or [], "contacts": a.contacts or {}} for a in parts
        ]
        out["requests"] = _requests_of(db, ctx, parts)
        out["can_survey"] = (
            (o.kind or "proyecto") != "venta"
            and o.status not in OPP_CLOSED
            and o.ally_approval not in APPROVAL_BLOCKS
            and ctx.p.can("aliado", "levantamientos_crear")
        )
        out.update(_opp_extra(db, ctx, o, pr))
    return out


# pregunta 8: el total sale solo de una cotizacion que Crimson ya le envio al cliente (no un borrador "creado")
QUOTE_SHOWN = ("enviada", "convertida", "vencida")


def _opp_extra(db: Session, ctx: AllyCtx, o: Opportunity, pr: Project | None) -> dict:
    """Reunion 2 (21/10), pedido 14: el detalle "muy basico" ahora trae la etapa con su proxima accion y fecha, los
    seguimientos que Crimson compartio (pedido 15), los levantamientos con su tecnico y la ejecucion del proyecto en
    comun (pedido 17). Sin montos de la oportunidad, probabilidad, costos ni margenes. El total de la cotizacion solo
    si Crimson lo habilito para ESTA empresa (pregunta abierta 8: por defecto no)."""
    from ..models import Quote
    from ..services import ally_log

    team = ctx.scope.team_user_ids()
    surveys = db.scalars(select(Survey).where(Survey.opportunity_id == o.id, *ctx.scope.surveys()).order_by(Survey.id.desc())).all()
    out: dict = {
        "next_action": o.next_action,
        "next_action_date": o.next_action_date,
        "seguimientos": ally_log.for_ally(db, o, ctx.company),
        "surveys": [
            {
                "id": s.id,
                "number": s.number,
                "kind": s.kind,
                "status": s.status,
                "review_status": s.review_status,
                "visit_date": s.visit_date,
                "technician": _user_name(db, s.technician_id) if s.technician_id in team else None,
                "sent_at": s.sent_at,
            }
            for s in surveys
        ],
        "cotizacion": None,
        "ejecucion": _ejecucion(db, ctx, pr) if pr else None,
    }
    if ctx.company.show_quote_total and o.quote_id:
        q = db.get(Quote, o.quote_id)
        if q is not None and q.tenant_id == ctx.p.tenant.id and q.status in QUOTE_SHOWN:
            out["cotizacion"] = {"number": q.number, "status": q.status, "total": q.total, "currency": q.currency}
    return out


def _ejecucion(db: Session, ctx: AllyCtx, pr: Project) -> dict:
    """Pedido 17: la ejecucion del proyecto en comun. Las OT de su equipo (se abren) y las de Crimson (solo el
    resumen), con quien va a cada una: de su equipo el nombre completo, de Crimson el nombre de pila. Las de otra
    empresa aliada no salen. Sin precios, horas de costo ni materiales."""
    rows = db.scalars(
        select(WorkOrder)
        .where(
            WorkOrder.tenant_id == ctx.p.tenant.id,
            WorkOrder.project_id == pr.id,
            *fw._live_orders(),
            or_(WorkOrder.ally_company_id.is_(None), WorkOrder.ally_company_id == ctx.company.id),
        )
        .order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at, WorkOrder.id)
    ).all()
    names = fw._names(db, list(rows), ctx.p.tenant.id) if rows else {}
    team = ctx.scope.team_user_ids()

    def quien(uid: int | None) -> str | None:
        n = names.get(uid) if uid else None
        return n if uid in team else _first(n)

    orders = []
    for x in rows:
        mia = x.ally_company_id == ctx.company.id
        gente = [n for n in (quien(i) for i in dict.fromkeys([x.technician_id, *(x.helpers or [])]) if i) if n]
        orders.append(
            {
                "id": x.id if mia else None,  # las de Crimson no se abren desde el portal
                "number": x.number,
                "title": x.title,
                "kind": x.kind,
                "status": x.status,
                "scheduled_at": x.scheduled_at,
                "finished_at": x.finished_at,
                "team": "aliado" if mia else "crimson",
                "technicians": gente,
            }
        )
    return {
        "project": {"id": pr.id, "number": pr.number, "name": pr.name, "status": pr.status, "start_date": pr.start_date, "end_date": pr.end_date},
        "orders": orders,
        "total": len(orders),
        "finalizadas": sum(1 for x in rows if x.status == "finalizada"),
    }


def _orders_of_project(db: Session, ctx: AllyCtx, pid: int) -> list[WorkOrder]:
    return list(db.scalars(select(WorkOrder).where(WorkOrder.project_id == pid, *ctx.scope.work_orders()).order_by(WorkOrder.id)))


def _project_out(db: Session, ctx: AllyCtx, pr: Project, full: bool = False) -> dict:
    parts = _my_parts(db, ctx, project_id=pr.id)
    orders = _orders_of_project(db, ctx, pr.id)
    out = {
        "id": pr.id,
        "number": pr.number,
        "name": pr.name,
        "status": pr.status,
        "customer": _customer_name(db, pr.customer_id),
        "end_customer": _customer_name(db, pr.end_customer_id),
        "site": pr.site,
        "start_date": pr.start_date,
        "end_date": pr.end_date,
        "delivered_at": pr.delivered_at,
        "orders_total": len(orders),
        "orders_done": sum(1 for x in orders if x.status == "finalizada"),
        "my_roles": sorted({a.role for a in parts}),
    }
    if full:
        names = fw._names(db, orders, ctx.p.tenant.id) if orders else {}
        team = ctx.scope.team_user_ids()
        out["scope"] = pr.scope
        out["my_scope_lines"] = sorted({x for a in parts for x in (a.scope_lines or [])})
        out["my_participations"] = [{"id": a.id, "role": a.role} for a in parts]  # para elegir el papel al enviar precios
        out["orders"] = [
            {
                "id": x.id,
                "number": x.number,
                "title": x.title,
                "kind": x.kind,
                "status": x.status,
                "scheduled_at": x.scheduled_at,
                # de Crimson, solo el nombre de pila (como en los tickets y en la OT)
                "technician": names.get(x.technician_id) if x.technician_id in team else _first(names.get(x.technician_id)),
            }
            for x in orders
        ]
        out["requests"] = _requests_of(db, ctx, parts)
        econ = _economia()
        out["lo_que_recibo"] = econ.ally_project_earnings(db, ctx.p.tenant, ctx.company.id, pr.id, for_ally=True) if econ else None
        out["assets_count"] = db.scalar(select(func.count()).select_from(CustomerAsset).where(CustomerAsset.project_id == pr.id, *ctx.scope.assets())) or 0
        out["ejecucion"] = _ejecucion(db, ctx, pr)  # pedido 17: tambien lo que hace Crimson en el proyecto
    return out


# ---------- Salidas: levantamiento y OT (proyeccion por lista blanca) ----------
def _who_for_ally(db: Session, ctx: AllyCtx, uid: int | None) -> str | None:
    """Autor visto por el aliado: el nombre si es de su equipo; si no, "Crimson" (sin nombres de revisores)."""
    if not uid:
        return None
    if uid in ctx.scope.team_user_ids():
        return _user_name(db, uid)
    return "Crimson"


def _survey_is_ours(ctx: AllyCtx, s: Survey) -> bool:
    """Lo puede cambiar: asignado a su empresa, o de Crimson en comun con el tecnico a cargo de su equipo (item 18). Uno
    de Crimson donde su gente solo visito o lo envio lo ve, pero no lo cambia."""
    return s.ally_company_id == ctx.company.id or s.technician_id in ctx.scope.team_user_ids()


def _survey_can_edit(ctx: AllyCtx, s: Survey) -> bool:
    if ctx.preview_by is not None or s.status in ("cotizado", "cerrado") or not _survey_is_ours(ctx, s):
        return False
    return not (s.status == "enviado" and s.review_status == "pendiente")


def _survey_ally_out(db: Session, ctx: AllyCtx, s: Survey, full: bool = False) -> dict:
    raw = fw._survey_out(db, s, full=full)
    out = _pick(raw, ALLY_SURVEY_KEYS)
    team = ctx.scope.team_user_ids()
    out["photos"] = _sign(ctx, s.photos or [])
    out["sent_by"] = _who_for_ally(db, ctx, s.sent_by)
    if s.technician_id not in team:
        # uno de Crimson que el aliado solo ve (item 18): de su tecnico, el nombre de pila y sin su id interno
        out["technician"], out["technician_id"] = _first(out.get("technician")), None
    out["visit_tech_ids"] = [i for i in raw.get("visit_tech_ids") or [] if i in team]
    out["visit_techs"] = [n for n in (_user_name(db, i) for i in out["visit_tech_ids"]) if n]
    obs = sorted(s.observations, key=lambda o: (o.resolved_at is not None, -o.review_round, o.id))
    out["observations"] = [
        {
            "id": o.id,
            "round": o.review_round,
            "point_code": o.point_code,
            "text": o.text,
            "created_by": _who_for_ally(db, ctx, o.created_by) or "Crimson",
            "created_at": o.created_at,
            "resolved": o.resolved_at is not None,
            "resolved_at": o.resolved_at,
            "resolved_by": _who_for_ally(db, ctx, o.resolved_by),
            "resolution": o.resolution,
        }
        for o in obs
    ]
    out["pending_review"] = {"label": "En revisión de Crimson"} if s.status == "enviado" else None
    out["can_edit"] = _survey_can_edit(ctx, s)
    if full:
        out.update(_pick(raw, ALLY_SURVEY_FULL_KEYS))
        out["points"] = [{**_pick(x, ALLY_POINT_KEYS), "photos": _sign(ctx, x.get("photos") or [])} for x in raw.get("points") or []]
        out["items"] = [_pick(x, ALLY_ITEM_KEYS) for x in raw.get("items") or []]
    return out


def _order_ally_out(db: Session, ctx: AllyCtx, o: WorkOrder, full: bool = False, names: dict | None = None) -> dict:
    raw = fw._order_out(db, o, full=full, names=names, tenant=ctx.p.tenant)
    out = _pick(raw, ALLY_ORDER_KEYS)
    out["photos"] = _sign(ctx, o.photos or [])
    team = ctx.scope.team_user_ids()

    def quien(uid: int | None, nombre: str | None) -> str | None:
        # personal de Crimson en una cuadrilla mixta: solo el nombre de pila (como en los tickets)
        return nombre if uid in team else _first(nombre)

    out["technician"] = quien(o.technician_id, out.get("technician"))
    out["crew"] = [{"id": c["id"], "name": quien(c["id"], c["name"]), "role": c["role"]} for c in raw.get("crew") or []]
    out["running"] = [{"user_id": r["user_id"], "user": quien(r["user_id"], r["user"]), "started_at": r["started_at"]} for r in raw.get("running") or []]
    # tiempo de SU equipo corriendo desde hace mas de fw.MAX_CORRIENDO_H: al terminar la jornada o finalizar se indica
    # la hora real en que terminaron (fin_jornada)
    limite = _now() - timedelta(hours=fw.MAX_CORRIENDO_H)
    out["stale"] = any(e.ended_at is None and e.user_id in team and _aware(e.started_at) < limite for e in o.time_entries)
    out["can_act"] = ctx.preview_by is None and o.status not in fw.CERRADAS
    if full:
        out["materials"] = [_pick(m, ALLY_MATERIAL_KEYS) for m in raw.get("materials") or []]
    return out


# ---------- Salidas: ticket, contrato, activo ----------
def _note_visible(n: SupportNote, team: set[int], company_id: int) -> bool:
    """Publicas, las de su equipo y las internas que Crimson compartio con ESTA empresa (no con la que tenia antes el
    ticket)."""
    return (not n.internal) or (bool(n.share_ally) and n.share_ally_company_id == company_id) or (n.user_id in team)


def _assigned_name(db: Session, uid: int | None, team: set[int]) -> str | None:
    """Responsable visto por el aliado: el nombre completo si es de su equipo; de Crimson, solo el nombre de pila."""
    if not uid:
        return None
    return _user_name(db, uid) if uid in team else _first(_user_name(db, uid))


def _ticket_out(db: Session, ctx: AllyCtx, t: SupportTicket, full: bool = False) -> dict:
    team = ctx.scope.team_user_ids()
    venc = slasvc.vencimientos(t, _now())
    pr = db.get(Project, t.project_id) if t.project_id else None
    a = db.get(CustomerAsset, t.asset_id) if t.asset_id else None
    out = {
        "id": t.id,
        "number": t.number,
        "subject": t.subject,
        "kind": t.kind,
        "priority": t.priority,
        "status": t.status,
        "level": t.level,
        "customer": _customer_name(db, t.customer_id, t.contact),
        "project": pr.number if pr and pr.tenant_id == t.tenant_id else None,
        "asset": {"name": a.name, "model": a.model, "serial": a.serial, "location": a.location} if a and a.tenant_id == t.tenant_id else None,
        "assigned_to": t.assigned_to,
        "assigned": _assigned_name(db, t.assigned_to, team),
        "due_at": t.due_at,
        "resolve_due_at": t.resolve_due_at,
        "first_reply_at": t.first_reply_at,
        "resolved_at": t.resolved_at,
        "sla_vencido": venc["respuesta"],
        "resolucion_vencida": venc["resolucion"],
        "fuera_de_tiempo": venc["respuesta"] or venc["resolucion"],
        "created_at": t.created_at,
        "can_act": ctx.preview_by is None and t.status != "cerrado",
    }
    if full:
        contact = t.contact or {}
        out["description"] = t.description
        out["contact"] = {"name": contact.get("name"), "phone": contact.get("phone")}
        out["photos"] = _sign(ctx, t.photos or [])
        out["solution"] = t.solution
        out["hours"] = t.hours
        notas = []
        for n in t.notes:
            if not _note_visible(n, team, ctx.company.id):
                continue
            mio = n.user_id in team
            autor = _user_name(db, n.user_id) if mio else (f"{_first(_user_name(db, n.user_id)) or 'Equipo'} · Crimson" if n.user_id else "Crimson")
            notas.append(
                {
                    "id": n.id,
                    "body": n.body,
                    "author": autor,
                    "team": "aliado" if mio else "crimson",
                    "internal": n.internal,
                    "created_at": n.created_at,
                    "photos": _sign(ctx, n.photos or []),
                }
            )
        out["notes"] = notas
    return out


def _contract_out(db: Session, ctx: AllyCtx, c: MaintenanceContract) -> dict:
    pr = db.get(Project, c.project_id) if c.project_id else None
    abiertos = db.scalars(
        select(SupportTicket).where(SupportTicket.contract_id == c.id, SupportTicket.status.in_(OPEN_TICKET), *ctx.scope.tickets()).order_by(SupportTicket.id)
    ).all()
    s = c.sla or {}
    team = ctx.scope.team_user_ids()
    return {
        "id": c.id,
        "number": c.number,
        "name": c.name,
        "kind": c.kind,
        "customer": _customer_name(db, c.customer_id),
        "project": pr.number if pr else None,
        "every_months": c.every_months,
        "next_date": c.next_date,
        "last_done": c.last_done,
        "dias_para_la_proxima": (c.next_date - date.today()).days if c.next_date else None,
        "scope": c.scope,
        "active": c.active,
        "sla": {k: {"respuesta": v.get("respuesta"), "resolucion": v.get("resolucion")} for k, v in s.items() if isinstance(v, dict)} or None,
        "tickets_abiertos": [{"id": t.id, "number": t.number, "status": t.status, "assigned": _assigned_name(db, t.assigned_to, team)} for t in abiertos],
        # integracion de estado de equipos bloqueada (18:00): el lugar queda marcado
        "device_status": None,
        "device_status_label": "Estado de equipos: pendiente de conexión",
    }


def _asset_out(db: Session, ctx: AllyCtx, a: CustomerAsset) -> dict:
    hoy = date.today()
    pr = db.get(Project, a.project_id) if a.project_id else None
    return {
        "id": a.id,
        "name": a.name,
        "model": a.model,
        "serial": a.serial,
        "location": a.location,
        "installed_at": a.installed_at,
        "warranty_until": a.warranty_until,
        "warranty_days": (a.warranty_until - hoy).days if a.warranty_until else None,
        "status": a.status,
        "customer": _customer_name(db, a.customer_id),
        "project": pr.number if pr else None,
        "photos": _sign(ctx, a.photos or []),
    }


# ---------- Inicio y mi dia ----------
def _tech_orders(db: Session, ctx: AllyCtx, uid_filter: int | None = None) -> list[WorkOrder]:
    """OT de hoy: para el tecnico las suyas (is_today_for_tech); para el aliado las de todo su equipo con la misma regla."""
    rows = db.scalars(select(WorkOrder).where(*ctx.scope.work_orders()).order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at, WorkOrder.id)).all()
    if ctx.scope.is_tech:
        return [o for o in rows if ctx.scope.is_today_for_tech(o)]
    hoy = ctx.scope.today
    out = []
    for o in rows:
        abierta = o.status in ("asignada", "en_sitio", "en_proceso") and (o.scheduled_at is None or local_date(o.scheduled_at) <= hoy)
        fin_hoy = o.status == "finalizada" and o.finished_at is not None and local_date(o.finished_at) == hoy
        if abierta or fin_hoy:
            out.append(o)
    return out


def _my_day(db: Session, ctx: AllyCtx) -> dict:
    orders = _tech_orders(db, ctx)
    names = fw._names(db, orders, ctx.p.tenant.id) if orders else {}
    # solo lo que es trabajo suyo: uno de Crimson que su gente solo visito no es pendiente del aliado (item 18)
    surveys_q = select(Survey).where(*ctx.scope.own_surveys())
    if not ctx.scope.is_tech:
        surveys_q = surveys_q.where(or_(Survey.status == "borrador", Survey.review_status == "devuelto"))
    surveys = db.scalars(surveys_q.order_by(Survey.id.desc()).limit(50)).all()
    tq = select(SupportTicket).where(*ctx.scope.tickets())
    if not ctx.scope.is_tech:
        tq = tq.where(SupportTicket.status.in_(OPEN_TICKET))
    tickets = [t for t in db.scalars(tq.order_by(SupportTicket.id.desc()).limit(200)) if ctx.scope.ticket_visible(t)]
    return {
        "ordenes": [_order_ally_out(db, ctx, o, names=names) for o in orders],
        "levantamientos": [_survey_ally_out(db, ctx, s) for s in surveys],
        "tickets": [_ticket_out(db, ctx, t) for t in tickets],
    }


def _tech_home(db: Session, ctx: AllyCtx) -> dict:
    """Reunion 2, pedido 21: el tecnico entraba a "Nada asignado para hoy". Ademas de lo de hoy (_my_day: sus
    ordenes de hoy, sus levantamientos en borrador o devueltos y sus tickets) ve sus proximas ordenes (sin abrirlas
    hasta el dia), las oportunidades en comun de su empresa en resumen (sin montos, sin detalle) y quien de su
    empresa le asigna trabajo, para que el inicio vacio diga a quien acudir."""
    uid = ctx.p.user.id
    hoy = ctx.scope.today
    from ..core.timeutil import cr_day_bounds

    manana = cr_day_bounds(hoy)[1]  # desde manana en hora de Costa Rica: las vencidas de la empresa no cuentan
    # sin limit antes del filtro por tecnico (ayudantes es JSON): la lista ya viene acotada a futuro y se corta a 20
    rows = db.scalars(
        select(WorkOrder)
        .where(*ctx.scope.work_orders(), WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")), WorkOrder.scheduled_at >= manana)
        .order_by(WorkOrder.scheduled_at, WorkOrder.id)
    ).all()
    proximas = [
        {
            "number": o.number,
            "title": o.title,
            "customer": _customer_name(db, o.customer_id),
            "site": o.site,
            "scheduled_at": o.scheduled_at,
            "status": o.status,
        }
        for o in rows
        if (o.technician_id == uid or uid in (o.helpers or [])) and local_date(o.scheduled_at) > hoy
    ][:20]
    # cualquier levantamiento suyo, en cualquier estado (el scope del tecnico deja solo borradores y devueltos);
    # solo se usa el id de la oportunidad para la marca
    mis_opps = set(
        db.scalars(
            select(Survey.opportunity_id).where(
                Survey.tenant_id == ctx.p.tenant.id,
                Survey.ally_company_id == ctx.company.id,
                Survey.technician_id == uid,
                live(Survey),
                Survey.opportunity_id.is_not(None),
            )
        )
    )
    opps = db.scalars(
        select(Opportunity).where(*ctx.scope.company_opportunities(), Opportunity.status.not_in(OPP_CLOSED)).order_by(Opportunity.id.desc()).limit(30)
    ).all()
    oportunidades = [
        {
            "number": o.number,
            "title": o.title,
            "customer": _customer_name(db, o.end_customer_id or o.customer_id, o.contact),
            "site": o.site,
            "status": o.status,
            "solution": o.solution,
            "mine": o.id in mis_opps,  # tiene un levantamiento suyo ahi
        }
        for o in opps
    ]
    asignan = sorted({m.user.full_name for m in ctx.scope.active_team() if m.role_code == "aliado" and m.user_id != uid and m.user.full_name})
    return {"proximas": proximas, "oportunidades": oportunidades, "asignan": asignan}


@router.get("/inicio")
def inicio(ctx: AllyCtx = Depends(need("ver")), db: Session = Depends(get_db)):
    c = ctx.company
    base = {
        "company": {"name": c.name, "state": c.state, "suspended_reason": c.suspended_reason if c.suspended_at else None},
        "role": ctx.p.role,
        "preview": ctx.preview_by is not None,
    }
    if c.suspended_at and ctx.preview_by is None:
        return base  # suspendida: solo su suscripcion y facturas
    if ctx.scope.is_tech:
        return {**base, "hoy": _my_day(db, ctx), "tecnico": _tech_home(db, ctx)}
    sc = ctx.scope
    opps = db.scalars(select(Opportunity).where(*sc.opportunities(), Opportunity.status.not_in(OPP_CLOSED))).all()
    pend = db.scalars(
        select(AllyCostRequest).where(
            AllyCostRequest.participation_id.in_(sc.participations().with_only_columns(AllyParticipation.id)),
            AllyCostRequest.status == "solicitado",
            AllyCostRequest.opportunity_id.in_(select(Opportunity.id).where(*sc.opportunities())),
        )
    ).all()
    proximas = sorted([r.due_date for r in pend if r.due_date])
    tickets = db.scalars(select(SupportTicket).where(*sc.tickets(), SupportTicket.status.in_(OPEN_TICKET))).all()
    ahora = _now()
    hoy = ctx.scope.today
    contratos = db.scalars(select(MaintenanceContract).where(*sc.contracts(), MaintenanceContract.active.is_(True))).all()
    out = {
        **base,
        "counters": {
            "oportunidades_abiertas": len(opps),
            "precios_pendientes": len(pend),
            "precio_mas_proximo": proximas[0] if proximas else None,
            "proyectos_activos": db.scalar(
                select(func.count()).select_from(Project).where(*sc.projects(), Project.status.in_(("planificado", "en_curso", "pausado")))
            )
            or 0,
            "ot_hoy_equipo": len(_tech_orders(db, ctx)),
            "tickets_abiertos": len(tickets),
            "tickets_fuera_de_tiempo": sum(1 for t in tickets if slasvc.fuera_de_tiempo(t, ahora)),
            "levantamientos_devueltos": db.scalar(select(func.count()).select_from(Survey).where(*sc.own_surveys(), Survey.review_status == "devuelto")) or 0,
            # lo que el aliado puede hacer ya: levantamientos suyos (que puede cambiar) todavia sin enviar a Crimson
            "levantamientos_por_hacer": db.scalar(select(func.count()).select_from(Survey).where(*sc.own_surveys(), Survey.status == "borrador")) or 0,
            "mantenimientos_30_dias": sum(1 for k in contratos if k.next_date and k.next_date <= hoy + timedelta(days=30)),
        },
        "precios_pedidos": [_request_out(db, ctx, r) for r in sorted(pend, key=lambda r: (r.due_date is None, r.due_date or date.max, r.id))[:10]],
        "hoy": _my_day(db, ctx)["ordenes"],
        "dinero": None,
    }
    econ = _economia()
    if econ:
        t = econ.ally_earnings(db, ctx.p.tenant, c.id, None, None, for_ally=True).get("totales") or {}
        out["dinero"] = {"recibido": t.get("recibido"), "por_recibir": t.get("por_recibir"), "proyectado": t.get("proyectado"), "moneda": "CRC"}
    return out


@router.get("/mi-dia")
def mi_dia(ctx: AllyCtx = Depends(need("ver")), db: Session = Depends(get_db)):
    return _my_day(db, ctx)


@router.get("/tecnicos")
def tecnicos(ctx: AllyCtx = Depends(need("ver"))):
    """A quien puede asignar trabajo: su equipo activo (el tecnico, solo el)."""
    rows = ctx.scope.active_team()
    if ctx.scope.is_tech:
        rows = [m for m in rows if m.user_id == ctx.p.user.id]
    return [{"id": m.user.id, "name": m.user.full_name, "role": m.role_code, "active": True} for m in rows]


# ---------- Oportunidades y precios ----------
@router.get("/oportunidades")
def oportunidades(
    estado: str = Query("abiertas", pattern="^(abiertas|cerradas|todas)$"),
    q: str = "",
    ctx: AllyCtx = Depends(need("oportunidades")),
    db: Session = Depends(get_db),
):
    stmt = select(Opportunity).where(*ctx.scope.opportunities())
    if estado == "abiertas":
        stmt = stmt.where(Opportunity.status.not_in(OPP_CLOSED))
    elif estado == "cerradas":
        stmt = stmt.where(Opportunity.status.in_(OPP_CLOSED))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Opportunity.title.ilike(like), Opportunity.number.ilike(like), Opportunity.site.ilike(like)))
    return [_opp_out(db, ctx, o) for o in db.scalars(stmt.order_by(Opportunity.id.desc()).limit(200))]


@router.get("/oportunidades/{oid}")
def oportunidad(oid: int, ctx: AllyCtx = Depends(need("oportunidades")), db: Session = Depends(get_db)):
    o = ctx.scope.one(Opportunity, oid, ctx.scope.opportunities(), "Oportunidad")
    return _opp_out(db, ctx, o, full=True)


class AllyContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(None, max_length=160)
    phone: str | None = Field(None, max_length=40)
    email: EmailStr | None = None


class AllyOppIn(BaseModel):
    """Oportunidad que propone el aliado (item 16). Sin cliente de Crimson (no ve la cartera): el cliente va como
    contacto y Crimson lo liga al aprobarla. Sin montos."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=200)
    kind: str = Field("proyecto", pattern="^(proyecto|venta)$")
    solution: str | None = Field(None, pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    site: str | None = Field(None, max_length=300)
    customer_name: str = Field(min_length=2, max_length=160)  # quien recibe la solucion
    contact: AllyContactIn = Field(default_factory=AllyContactIn)
    role: str = Field("subcontratista", pattern="^(referido|subcontratista|suministro|configuracion|software)$")
    description: str | None = Field(None, max_length=4000)


def approval_required(tenant) -> bool:
    """Pregunta abierta 8 (21/10): la oportunidad que crea un aliado espera la aprobacion de Crimson. Es el default;
    Administracion -> Aliados -> Ajustes la puede apagar (PUT /allies/config opportunity_approval = false, que guarda
    tenant.settings["ally_opportunity_approval"]): entonces queda aprobada al crearla."""
    return (tenant.settings or {}).get("ally_opportunity_approval", True) is not False


@router.post("/oportunidades", status_code=201)
def oportunidad_crear(data: AllyOppIn, ctx: AllyCtx = Depends(need("oportunidades")), db: Session = Depends(get_db)):
    """El aliado propone una oportunidad "por si a Crimson se le pasa" (21/10, 1:37). Nace con su participacion visible
    (su papel), sin responsable de Crimson y, por defecto, pendiente de aprobacion: al admin le llega el aviso y la
    aprueba o la rechaza desde la oportunidad. Mientras tanto el aliado la ve con ese estado y no puede levantar."""
    from ..models import AllyParticipation as Part
    from .pipeline import _log

    _tope_avisos(ctx)
    t = ctx.p.tenant
    pendiente = approval_required(t)
    number, _ = next_number(db, t.id, "OPO")
    contacto = {k: v for k, v in {"name": data.customer_name, "phone": data.contact.phone, "email": data.contact.email}.items() if v}
    if data.contact.name and data.contact.name != data.customer_name:
        contacto["contact_name"] = data.contact.name
    o = Opportunity(
        tenant_id=t.id,
        number=number,
        title=data.title,
        kind=data.kind,
        solution=data.solution,
        site=data.site,
        contact=contacto,
        source="aliado",
        status="nuevo",
        amount=0,
        owner_id=None,
        created_by=ctx.p.user.id,
        ally_approval="pendiente" if pendiente else "aprobada",
        ally_proposed_by=ctx.company.id,
    )
    db.add(o)
    db.flush()
    db.add(
        Part(
            tenant_id=t.id,
            opportunity_id=o.id,
            ally_company_id=ctx.company.id,
            name=ctx.company.name,
            role=data.role,
            visible_to_ally=True,
            contacts={},
            scope_lines=[],
            created_by=ctx.p.user.id,
        )
    )
    quien = f"{ctx.p.user.full_name} ({ctx.company.name})"
    _log(o, quien, f"propuso la oportunidad desde el portal de aliados{': ' + data.description if data.description else ''}"[:1500])
    audit(db, t.id, ctx.p.user.id, "ally_propose", "opportunity", o.id, {"ally_company_id": ctx.company.id, "pendiente": pendiente}, ip=ctx.p.ip)
    base = cfg.public_base_url.rstrip("/")
    esc = html.escape
    notify_roles(
        db,
        t,
        ("admin",),
        f"Oportunidad propuesta por {ctx.company.name}: {number}" + (" · pendiente de su aprobación" if pendiente else ""),
        f"<p>{esc(quien)} propuso la oportunidad <b>{esc(number)} · {esc(data.title)}</b> desde el portal de aliados.</p>"
        f"<p>Cliente: {esc(data.customer_name)}<br>Sitio: {esc(data.site or '—')}<br>Papel del aliado: {esc(data.role)}</p>"
        + (f"<p>{esc(data.description)}</p>" if data.description else "")
        + ("<p>Revísela y apruébela o recházela desde la oportunidad. Mientras tanto el aliado la ve como pendiente.</p>" if pendiente else "")
        + f'<p><a href="{base}/oportunidades?id={o.id}">Abrir la oportunidad</a></p>',
        "opportunity",
        o.id,
        like=False,
    )
    db.commit()
    return _opp_out(db, ctx, o, full=True)


@router.get("/solicitudes")
def solicitudes(pendientes: bool = False, ctx: AllyCtx = Depends(need("precios")), db: Session = Depends(get_db)):
    parts = {a.id: a for a in db.scalars(ctx.scope.participations())}
    if not parts:
        return []
    q = select(AllyCostRequest).where(
        AllyCostRequest.participation_id.in_(list(parts)), AllyCostRequest.opportunity_id.in_(select(Opportunity.id).where(*ctx.scope.opportunities()))
    )
    if pendientes:
        q = q.where(AllyCostRequest.status == "solicitado")
    rows = db.scalars(q.order_by(AllyCostRequest.due_date.is_(None), AllyCostRequest.due_date, AllyCostRequest.id.desc()).limit(300)).all()
    out = []
    for r in rows:
        o = db.get(Opportunity, r.opportunity_id)
        item = _request_out(db, ctx, r)
        item["opportunity"] = {"id": o.id, "number": o.number, "title": o.title} if o else None
        item["role"] = parts[r.participation_id].role
        out.append(item)
    return out


class QuoteLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    concept: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    unit: str | None = Field(None, max_length=10)
    unit_price: Decimal = Field(ge=0)


class AllyQuoteIn(BaseModel):
    """Respuesta del aliado a una solicitud: monto O desglose por lineas (el total se calcula aqui)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: Decimal | None = Field(None, ge=0)
    lines: list[QuoteLineIn] | None = Field(None, max_length=60)
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    valid_until: date | None = None
    exclusions: str | None = Field(None, max_length=4000)
    attachment_ids: list[int] | None = Field(None, max_length=10)

    @model_validator(mode="after")
    def _uno(self):
        if (self.amount is None) == (not self.lines):
            raise ValueError("Indique un monto o un desglose por líneas (uno de los dos)")
        return self


class AllyOfferIn(AllyQuoteIn):
    participation_id: int | None = None
    what: str = Field(min_length=2, max_length=300)


def _apply_quote(db: Session, ctx: AllyCtx, r: AllyCostRequest, data: AllyQuoteIn) -> None:
    if data.lines:
        total = sum((ln.quantity * ln.unit_price for ln in data.lines), Decimal(0))
        r.lines = [{"concept": ln.concept, "quantity": str(ln.quantity), "unit": ln.unit or None, "unit_price": str(ln.unit_price)} for ln in data.lines]
        r.amount = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        r.lines = []
        r.amount = data.amount
    r.currency = data.currency
    r.valid_until = data.valid_until
    r.exclusions = data.exclusions
    if data.attachment_ids is not None:
        r.attachments = _attachments(db, ctx, data.attachment_ids)
    r.status = "recibido"
    r.responded_by, r.responded_at = ctx.p.user.id, _now()


def _notify_offer(db: Session, ctx: AllyCtx, o: Opportunity, r: AllyCostRequest, texto: str) -> None:
    """Correo SIN montos a quien aprueba y ve costos (plan maestro 1.6): lo que cobra el aliado es costo y la vendedora
    no lo aprueba. La bitacora de la oportunidad (que la vendedora si ve) dice que llego."""
    from ..services import avisos_venta
    from .pipeline import _log

    _log(o, ctx.company.name, texto)
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "ally_offer", "opportunity", o.id, {"request_id": r.id, "ally_company_id": ctx.company.id}, ip=ctx.p.ip)
    avisos_venta.precio_aliado(db, ctx.p.tenant, o, r, ctx.company.name, texto)


def _tope_avisos(ctx: AllyCtx) -> None:
    """Cada precio, respuesta o envio de levantamiento avisa por correo a la oficina: tope por persona (como los tickets)."""
    ally_notify_limiter.hit(f"{ctx.p.tenant.id}:{ctx.p.user.id}")


def _tope_asignar(ctx: AllyCtx) -> None:
    """Cada reasignacion puede escribirle a su tecnico ("Tiene un trabajo nuevo"): tope por persona. Ademas
    notify_ally_work no repite el aviso del mismo trabajo a la misma persona (alternar A -> B -> A no inunda)."""
    ally_assign_limiter.hit(f"{ctx.p.tenant.id}:{ctx.p.user.id}")


@router.put("/solicitudes/{rid}")
def responder(rid: int, data: AllyQuoteIn, ctx: AllyCtx = Depends(need("precios")), db: Session = Depends(get_db)):
    _tope_avisos(ctx)
    r = db.scalar(
        select(AllyCostRequest).where(
            AllyCostRequest.id == rid,
            AllyCostRequest.participation_id.in_(ctx.scope.participations().with_only_columns(AllyParticipation.id)),
            AllyCostRequest.opportunity_id.in_(select(Opportunity.id).where(*ctx.scope.opportunities())),
        )
    )
    if r is None:
        raise HTTPException(404, "Solicitud no encontrada")
    if r.status not in COST_OPEN:
        raise HTTPException(409, "Crimson ya resolvió esta solicitud")
    _apply_quote(db, ctx, r, data)
    o = db.get(Opportunity, r.opportunity_id)
    _notify_offer(db, ctx, o, r, f"respondió la solicitud «{r.what}»")
    db.commit()
    return _request_out(db, ctx, r)


def _offer(db: Session, ctx: AllyCtx, o: Opportunity, parts: list[AllyParticipation], data: AllyOfferIn) -> dict:
    if not parts:
        raise HTTPException(404, "Oportunidad no encontrada")
    if data.participation_id is None:
        if len(parts) > 1:
            raise HTTPException(422, "Elegí en qué papel envía el precio")
        a = parts[0]
    else:
        a = next((x for x in parts if x.id == data.participation_id), None)
        if a is None:
            raise HTTPException(422, "Elegí en qué papel envía el precio")
    r = AllyCostRequest(
        tenant_id=ctx.p.tenant.id,
        participation_id=a.id,
        opportunity_id=o.id,
        what=data.what.strip(),
        responsible_id=o.owner_id,
        source="aliado",
        created_by=ctx.p.user.id,
    )
    _apply_quote(db, ctx, r, data)
    a.requests.append(r)
    db.flush()
    _notify_offer(db, ctx, o, r, f"envió su precio: «{r.what}»")
    db.commit()
    return _request_out(db, ctx, r)


@router.post("/oportunidades/{oid}/precios", status_code=201)
def oportunidad_precios(oid: int, data: AllyOfferIn, ctx: AllyCtx = Depends(need("precios")), db: Session = Depends(get_db)):
    _tope_avisos(ctx)
    o = ctx.scope.one(Opportunity, oid, ctx.scope.opportunities(), "Oportunidad")
    return _offer(db, ctx, o, _my_parts(db, ctx, opportunity_id=o.id), data)


# ---------- Proyectos ----------
@router.get("/proyectos")
def proyectos(estado: str = Query("activos", pattern="^(activos|todos)$"), ctx: AllyCtx = Depends(need("proyectos")), db: Session = Depends(get_db)):
    stmt = select(Project).where(*ctx.scope.projects())
    if estado == "activos":
        stmt = stmt.where(Project.status.not_in(("cancelado", "facturado", "entregado")))
    return [_project_out(db, ctx, pr) for pr in db.scalars(stmt.order_by(Project.id.desc()).limit(200))]


@router.get("/proyectos/{pid}")
def proyecto(pid: int, ctx: AllyCtx = Depends(need("proyectos")), db: Session = Depends(get_db)):
    return _project_out(db, ctx, ctx.scope.one(Project, pid, ctx.scope.projects(), "Proyecto"), full=True)


@router.post("/proyectos/{pid}/precios", status_code=201)
def proyecto_precios(pid: int, data: AllyOfferIn, ctx: AllyCtx = Depends(need("precios")), db: Session = Depends(get_db)):
    _tope_avisos(ctx)
    pr = ctx.scope.one(Project, pid, ctx.scope.projects(), "Proyecto")
    parts = _my_parts(db, ctx, project_id=pr.id)
    o = db.scalar(select(Opportunity).where(Opportunity.id == parts[0].opportunity_id, *ctx.scope.opportunities())) if parts else None
    if o is None:
        raise HTTPException(404, "Proyecto no encontrado")
    return _offer(db, ctx, o, [a for a in parts if a.opportunity_id == o.id], data)


@router.get("/clientes")
def clientes(ctx: AllyCtx = Depends(need("activos")), db: Session = Depends(get_db)):
    """Solo nombres de los clientes en comun y cuantos proyectos en comun tienen."""
    ids = set(db.scalars(ctx.scope.customer_ids()))
    if not ids:
        return []
    prjs = db.scalars(select(Project).where(*ctx.scope.projects())).all()
    out = []
    for c in db.scalars(select(Customer).where(Customer.tenant_id == ctx.p.tenant.id, Customer.id.in_(ids)).order_by(Customer.name)):
        out.append({"id": c.id, "name": c.name, "proyectos": sum(1 for pr in prjs if c.id in (pr.customer_id, pr.end_customer_id))})
    return out


# ---------- Levantamientos ----------
@router.get("/levantamientos/specs")
def survey_specs(ctx: AllyCtx = Depends(need("levantamientos"))):
    return fw.specs(None)


@router.get("/levantamientos/specs/common")
def survey_specs_common(ctx: AllyCtx = Depends(need("levantamientos"))):
    return fw.specs_common(None)


@router.get("/levantamientos")
def levantamientos(
    status: str | None = None,
    review: str | None = Query(None, pattern="^(pendiente|aprobado|devuelto)$"),
    ctx: AllyCtx = Depends(need("levantamientos")),
    db: Session = Depends(get_db),
):
    stmt = select(Survey).where(*ctx.scope.surveys())
    if status:
        stmt = stmt.where(Survey.status == status)
    if review:
        stmt = stmt.where(Survey.review_status == review)
    return [_survey_ally_out(db, ctx, s) for s in db.scalars(stmt.order_by(Survey.id.desc()).limit(200))]


class AllyPointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    code: str = Field(min_length=1, max_length=20)
    kind: str | None = Field(None, pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    label: str | None = Field(None, max_length=160)
    data: dict = Field(default_factory=dict)
    data_status: dict | None = None
    photos: list = Field(default_factory=list)
    notes: str | None = None


class AllyItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(1), ge=0)
    unit: str = Field("Unid", max_length=10)
    note: str | None = Field(None, max_length=200)
    kind: str | None = Field(None, pattern="^(equipo|material|servicio)$")


class AllySurveyEditIn(BaseModel):
    """Lo mismo que SurveyIn del formulario interno, sin cliente (sale de la oportunidad) ni oportunidad."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field("cctv", pattern="^(cctv|redes|acceso|asistencia|ups|cableado|anpr|otro)$")
    contact: dict = Field(default_factory=dict)
    site: str | None = Field(None, max_length=300)
    visit_date: date | None = None
    techs: int = Field(2, ge=0, le=200)
    days: Decimal = Field(Decimal(1), ge=0, le=365)
    labor: dict[str, fw.LaborIn] | None = None
    visit_tech_ids: list[int] = Field(default_factory=list)
    technician_id: int | None = None
    notes: str | None = None
    photos: list = Field(default_factory=list)
    points: list[AllyPointIn] = Field(default_factory=list)
    items: list[AllyItemIn] = Field(default_factory=list)
    common: dict | None = None
    received_by: str | None = Field(None, max_length=160)


class AllySurveyIn(AllySurveyEditIn):
    opportunity_id: int | None = None
    project_id: int | None = None


def _to_survey_in(data: AllySurveyEditIn, customer_id: int | None, s: Survey | None = None, team: set[int] | None = None) -> fw.SurveyIn:
    """Cuerpo del portal -> SurveyIn interno. Al GUARDAR (s), lo que el formulario del aliado no manda se conserva: el
    personal requerido (labor/techs/days, que Crimson usa para costear) y quienes hicieron la visita. De esa lista, los
    de Crimson (que el aliado no ve) quedan siempre; los de su equipo son los que mande."""
    payload = data.model_dump(exclude={"technician_id", "opportunity_id", "project_id"}, exclude_unset=False)
    sent = data.model_fields_set
    if "common" not in sent:
        payload.pop("common", None)
    if "received_by" not in sent:
        payload.pop("received_by", None)
    if s is not None:
        if not ({"labor", "techs", "days"} & sent):
            # el que ya tiene, sin tarifas (esas las conserva apply_survey_payload)
            payload["labor"] = {k: {"people": v["people"], "days": v["days"]} for k, v in fw.survey_labor(s).items()}
        team = team or set()
        previos = [int(i) for i in (s.visit_tech_ids or []) if str(i).isdigit()]
        propios = list(data.visit_tech_ids) if "visit_tech_ids" in sent else [i for i in previos if i in team]
        payload["visit_tech_ids"] = [*(i for i in previos if i not in team), *propios]
    return fw.SurveyIn(**payload, customer_id=customer_id)


def _survey_photos_check(db: Session, ctx: AllyCtx, data: AllySurveyEditIn, s: Survey | None) -> None:
    ya = list(s.photos or []) if s else []
    if s:
        for pt in s.points:
            ya += list(pt.photos or [])
    data.photos = _fotos_del_equipo(db, ctx, data.photos, ya)
    for pt in data.points:
        pt.photos = _fotos_del_equipo(db, ctx, pt.photos, ya)
    for it in data.items:
        _product(db, ctx, it.product_id)


@router.post("/levantamientos", status_code=201)
def levantamiento_crear(data: AllySurveyIn, ctx: AllyCtx = Depends(need("levantamientos_crear")), db: Session = Depends(get_db)):
    if bool(data.opportunity_id) == bool(data.project_id):
        raise HTTPException(422, "Elegí la oportunidad o el proyecto en común")
    if data.project_id:
        pr = ctx.scope.one(Project, data.project_id, ctx.scope.projects(), "Proyecto")
        if not pr.opportunity_id:
            raise HTTPException(422, "Este proyecto no tiene oportunidad ligada; pídale el levantamiento a Crimson")
        o = db.get(Opportunity, pr.opportunity_id)
        if o is None or o.tenant_id != ctx.p.tenant.id:
            raise HTTPException(422, "Este proyecto no tiene oportunidad ligada; pídale el levantamiento a Crimson")
    else:
        o = ctx.scope.one(Opportunity, data.opportunity_id, ctx.scope.opportunities(), "Oportunidad")
        if o.status in OPP_CLOSED:
            raise HTTPException(409, "La oportunidad ya está cerrada")
        if o.ally_approval in APPROVAL_BLOCKS:
            raise HTTPException(409, "La oportunidad espera la aprobación de Crimson: el levantamiento se hace cuando la aprueben")
    tec = data.technician_id or ctx.p.user.id
    if tec != ctx.p.user.id:
        # «¿Quién lo hace?»: dejarselo a otro de su equipo es asignar (y le escribe "Tiene un trabajo nuevo")
        if not ctx.p.can("aliado", "asignar"):
            raise HTTPException(403, "Asignar el levantamiento a otro técnico lo hace el aliado")
        _tope_asignar(ctx)
    _team_member(ctx, tec)
    _survey_photos_check(db, ctx, data, None)
    customer_id = o.end_customer_id or o.customer_id  # nunca del cuerpo
    # sin sitio ni contacto en el cuerpo, los de la oportunidad (la que propuso el aliado trae el cliente como contacto)
    if not (data.site or "").strip() and o.site:
        data.site = o.site
    if not data.contact and o.contact:
        oc = o.contact or {}
        data.contact = {k: v for k, v in {"name": oc.get("contact_name") or oc.get("name"), "phone": oc.get("phone"), "email": oc.get("email")}.items() if v}
    number, _ = next_number(db, ctx.p.tenant.id, "LEV")
    s = Survey(tenant_id=ctx.p.tenant.id, number=number, technician_id=tec, template_version=fw.TEMPLATE_VERSION, ally_company_id=ctx.company.id)
    fw.apply_survey_payload(db, s, _to_survey_in(data, customer_id), ctx.p.tenant.id, valid_user_ids=ctx.scope.team_user_ids())
    s.opportunity_id = o.id
    db.add(s)
    db.flush()
    if o.status in fw.ESPERA_LEVANTAMIENTO:
        o.status = "levantamiento"
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "create", "survey", s.id, {"aliado": ctx.company.id}, ip=ctx.p.ip)
    if tec != ctx.p.user.id:
        # el tecnico elegido al crearlo se entera como en una reasignacion (la pista del alta lo promete)
        _avisar_reasignacion(db, ctx, s, None)
    db.commit()
    return _survey_ally_out(db, ctx, s, full=True)


def _my_survey(db: Session, ctx: AllyCtx, sid: int) -> Survey:
    return ctx.scope.one(Survey, sid, ctx.scope.surveys(), "Levantamiento")


def _my_survey_rw(db: Session, ctx: AllyCtx, sid: int) -> Survey:
    """El levantamiento para cambiarlo (guardar, enviar, observaciones, asignar, sugerencias): ademas de verlo, tiene que
    ser suyo (_survey_is_ours)."""
    s = _my_survey(db, ctx, sid)
    if not _survey_is_ours(ctx, s):
        raise HTTPException(409, "Este levantamiento lo lleva Crimson: puede verlo, pero no cambiarlo")
    return s


@router.get("/levantamientos/{sid}")
def levantamiento(sid: int, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)):
    return _survey_ally_out(db, ctx, _my_survey(db, ctx, sid), full=True)


@router.put("/levantamientos/{sid}")
def levantamiento_guardar(sid: int, data: AllySurveyEditIn, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)):
    s = _my_survey_rw(db, ctx, sid)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    if s.status == "enviado" and s.review_status == "pendiente":
        raise HTTPException(409, "Está en revisión de Crimson")
    nuevo_tec = data.technician_id if data.technician_id is not None and data.technician_id != s.technician_id else None
    if nuevo_tec is not None:
        if not ctx.p.can("aliado", "asignar"):
            raise HTTPException(403, "Reasignar el levantamiento lo hace el aliado")
        _tope_asignar(ctx)
        _team_member(ctx, nuevo_tec)
    _survey_photos_check(db, ctx, data, s)
    from ..services import bitacora

    antes = bitacora.survey_state(s)  # antes de cambiar el tecnico: la bitacora registra tambien la reasignacion
    antes_tec = s.technician_id
    if nuevo_tec is not None:
        s.technician_id = nuevo_tec
    team = ctx.scope.team_user_ids()
    # validos: su equipo y los de Crimson que ya estaban en la visita (se conservan; uno nuevo de afuera no entra)
    validos = team | {int(i) for i in (s.visit_tech_ids or []) if str(i).isdigit()}
    fw.apply_survey_payload(db, s, _to_survey_in(data, s.customer_id, s, team), ctx.p.tenant.id, valid_user_ids=validos)
    bitacora.audit_survey_update(db, ctx.p, s, antes, company=ctx.company.name)
    if nuevo_tec is not None:
        # el formulario reasigna por aqui (AllySurvey.tsx): lo mismo que PATCH /asignar (auditoria y aviso al tecnico)
        _avisar_reasignacion(db, ctx, s, antes_tec)
    if s.review_status == "aprobado" and not s.quote_id:
        s.review_status = "pendiente"  # lo aprobado ya no es lo mismo: Crimson lo vuelve a revisar
        audit(db, ctx.p.tenant.id, ctx.p.user.id, "review_reset", "survey", s.id, {"motivo": "editado por el aliado despues de aprobado"}, ip=ctx.p.ip)
    db.commit()
    return _survey_ally_out(db, ctx, s, full=True)


@router.get("/levantamientos/{sid}/sistema")
def levantamiento_sistema(sid: int, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)):
    s = _my_survey(db, ctx, sid)
    out = system_calcs(s.kind, list(s.points), s.common)
    out["survey_template_version"] = s.template_version or 1
    return out


# Lo que el aliado ve de cada sugerencia de materiales: que, cuanto y por que. Nunca candidatos de catalogo o de
# proveedor (traen proveedor, existencias y, segun el rol, costo o precio): "sin precios" (item 19)
ALLY_SUGGEST_KEYS = (
    "name", "quantity", "unit", "kind", "reason", "action", "existing_quantity", "add_quantity", "item_id", "matched_name",
    "missing", "estimated", "warnings", "alternatives", "reference",
)  # fmt: skip


@router.post("/levantamientos/{sid}/sugerir")
def levantamiento_sugerir(sid: int, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)):
    """«Sugerir materiales» del aliado (21/10, 1:50: "el aliado deberia poderlo hacer, sin precios"): la misma sugerencia
    que el levantamiento interno a partir de sus puntos, sin equipos recomendados, proveedores, costos ni precios. No
    cambia nada: el aliado elige y agrega con /sugerir/agregar."""
    from ..services.survey_specs import suggest_materials

    s = _my_survey_rw(db, ctx, sid)
    lines = suggest_materials(s.kind, list(s.points), list(s.items), common=s.common)
    return [{k: x[k] for k in ALLY_SUGGEST_KEYS if k in x} for x in lines]


class AllySuggestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0, le=100000)
    unit: str = Field("Unid", max_length=10)
    kind: str | None = Field(None, pattern="^(equipo|material|servicio)$")
    item_id: int | None = None


@router.post("/levantamientos/{sid}/sugerir/agregar")
def levantamiento_sugerir_agregar(
    sid: int, data: list[AllySuggestionIn] = Body(..., max_length=200), ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)
):
    """Agrega o suma las sugerencias elegidas (como el interno: nunca borra ni duplica). Solo en lo que puede editar.
    Como el guardado (levantamiento_guardar): queda en la bitacora y, si ya estaba aprobado, vuelve a revision."""
    from ..services import bitacora

    s = _my_survey_rw(db, ctx, sid)
    if not _survey_can_edit(ctx, s):
        raise HTTPException(409, "Está en revisión de Crimson" if s.status == "enviado" else "El levantamiento ya fue cotizado")
    antes = bitacora.survey_state(s)
    added, summed = fw.apply_suggestions(s, [fw.SuggestionIn(**x.model_dump()) for x in data])
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "suggest_apply", "survey", s.id, {"agregados": added, "sumados": summed, "aliado": ctx.company.id}, ip=ctx.p.ip)
    if added or summed:
        bitacora.audit_survey_update(db, ctx.p, s, antes, company=ctx.company.name)
        if s.review_status == "aprobado" and not s.quote_id:
            s.review_status = "pendiente"  # lo aprobado ya no es lo mismo: Crimson lo vuelve a revisar
            audit(db, ctx.p.tenant.id, ctx.p.user.id, "review_reset", "survey", s.id, {"motivo": "sugerencias del aliado tras aprobarlo"}, ip=ctx.p.ip)
    db.commit()
    return {"added": added, "summed": summed, "survey": _survey_ally_out(db, ctx, s, full=True)}


@router.get("/levantamientos/{sid}/informe.pdf")
def levantamiento_informe(sid: int, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)):
    """Informe preliminar del levantamiento para el aliado: el mismo de Crimson, que por regla no lleva ni un monto (ni
    costos, ni precios, ni tarifas: services/survey_report.py). Sin WeasyPrint, el HTML imprimible (X-PDF-Fallback)."""
    from fastapi.responses import HTMLResponse

    from ..services.survey_report import render_survey_html

    s = _my_survey(db, ctx, sid)
    # cada informe firma y arma todas las fotos (WeasyPrint): tope por persona, como las fotos y los tickets
    ally_report_limiter.hit(f"{ctx.p.tenant.id}:{ctx.p.user.id}")
    team = ctx.scope.team_user_ids()
    # visitantes como en el resto del portal: su equipo con nombre completo; de Crimson, solo el nombre de pila
    page = render_survey_html(db, s, ctx.p.tenant, visitor_name=lambda uid, name: name if uid in team else _first(name))
    pdf = render_pdf(page)
    if pdf is None:
        return HTMLResponse(page, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Informe-{s.number}.pdf"'})


@router.post("/levantamientos/{sid}/enviar")
def levantamiento_enviar(sid: int, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)):
    _tope_avisos(ctx)
    s = _my_survey_rw(db, ctx, sid)
    if s.status == "enviado" and s.review_status == "pendiente":
        # ya esta en la cola de revision: reenviarlo solo volveria a escribirles a los revisores
        raise HTTPException(409, "Ya está en revisión de Crimson")
    fw.send_survey(db, ctx.p.tenant, s, ctx.p.user, ctx.p.ip, sender_label=f"{ctx.p.user.full_name} ({ctx.company.name})")
    db.commit()
    out = _survey_ally_out(db, ctx, s, full=True)
    out["notified"] = [{"name": "Crimson"}]  # sin nombres de los revisores
    return out


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = True
    resolution: str | None = Field(None, max_length=400)


@router.post("/levantamientos/{sid}/observaciones/{obs_id}")
def levantamiento_observacion(
    sid: int, obs_id: int, data: ResolveIn | None = None, ctx: AllyCtx = Depends(need("levantamientos")), db: Session = Depends(get_db)
):
    s = _my_survey_rw(db, ctx, sid)
    data = data or ResolveIn()
    fw.resolve_observation(db, s, obs_id, ctx.p.user, data.resolution, resolved=data.resolved, ip=ctx.p.ip)
    db.commit()
    return _survey_ally_out(db, ctx, s, full=True)


class AssignTechIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technician_id: int


def _avisar_reasignacion(db: Session, ctx: AllyCtx, s: Survey, antes: int | None) -> None:
    """Tecnico del levantamiento cambiado desde el portal (PATCH /asignar y el PUT del formulario): queda en la
    auditoria y el tecnico nuevo recibe "Tiene un trabajo nuevo" (spec 7.5). Se llama despues de validar todo: el
    correo sale al encolarlo."""
    from ..services.ally_scope import notify_ally_work

    audit(db, ctx.p.tenant.id, ctx.p.user.id, "assign", "survey", s.id, {"de": antes, "a": s.technician_id}, ip=ctx.p.ip)
    if antes != s.technician_id:
        notify_ally_work(db, ctx.p.tenant, {s.technician_id}, s.number, f"Levantamiento {s.site or ''}".strip(), s.visit_date, entity_id=s.id)


@router.patch("/levantamientos/{sid}/asignar")
def levantamiento_asignar(sid: int, data: AssignTechIn, ctx: AllyCtx = Depends(need("asignar")), db: Session = Depends(get_db)):
    _tope_asignar(ctx)
    s = _my_survey_rw(db, ctx, sid)
    if s.status in ("cotizado", "cerrado"):
        raise HTTPException(409, "El levantamiento ya fue cotizado")
    _team_member(ctx, data.technician_id)
    antes = s.technician_id
    s.technician_id = data.technician_id
    _avisar_reasignacion(db, ctx, s, antes)
    db.commit()
    return _survey_ally_out(db, ctx, s)


# ---------- Ordenes de trabajo ----------
def _my_order(db: Session, ctx: AllyCtx, oid: int) -> WorkOrder:
    o = ctx.scope.one(WorkOrder, oid, ctx.scope.work_orders(), "Orden de trabajo")
    if ctx.scope.is_tech and not ctx.scope.is_today_for_tech(o):
        raise HTTPException(404, "Orden de trabajo no encontrada")
    return o


@router.get("/ordenes")
def ordenes(
    estado: str = Query("pendientes", pattern="^(pendientes|finalizadas|todas)$"), ctx: AllyCtx = Depends(need("ordenes")), db: Session = Depends(get_db)
):
    stmt = select(WorkOrder).where(*ctx.scope.work_orders())
    if estado == "pendientes":
        stmt = stmt.where(WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")))
    elif estado == "finalizadas":
        stmt = stmt.where(WorkOrder.status == "finalizada")
    stmt = stmt.order_by(WorkOrder.scheduled_at.is_(None), WorkOrder.scheduled_at, WorkOrder.id.desc())
    if ctx.scope.is_tech:
        rows = [o for o in db.scalars(stmt) if ctx.scope.is_today_for_tech(o)]  # sin limit antes del filtro
    else:
        rows = list(db.scalars(stmt.limit(300)))
    names = fw._names(db, rows, ctx.p.tenant.id) if rows else {}
    return [_order_ally_out(db, ctx, o, names=names) for o in rows]


@router.get("/ordenes/{oid}")
def orden(oid: int, ctx: AllyCtx = Depends(need("ordenes")), db: Session = Depends(get_db)):
    return _order_ally_out(db, ctx, _my_order(db, ctx, oid), full=True)


class AllyMaterialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(0), ge=0)
    unit: str = Field("Unid", max_length=10)
    planned: Decimal = Field(Decimal(0), ge=0)


class AllyProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[dict] | None = None
    photos: list | None = None
    notes: str | None = None
    materials: list[AllyMaterialIn] | None = None
    customer_signature: str | None = Field(None, max_length=160)
    quantities: dict[int, Decimal] | None = None
    removed: list[int] | None = None
    agregados: list[AllyMaterialIn] | None = Field(None, max_length=60)  # lineas nuevas (se suman, no reemplazan)
    # terminar jornada / finalizar con tiempo de su equipo corriendo de mas (se olvidaron de terminar la jornada): la
    # hora real en que terminaron. Sin esto la orden quedaria trabada hasta que Crimson corrija el tiempo
    fin_jornada: datetime | None = None

    @field_validator("quantities")
    @classmethod
    def _no_neg(cls, v):
        if v and any(x < 0 for x in v.values()):
            raise ValueError("Las cantidades no pueden ser negativas")
        return v


class OrderAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technician_id: int | None = None
    helpers: list[int] = Field(default_factory=list)


@router.patch("/ordenes/{oid}/asignar")
def orden_asignar(oid: int, body: OrderAssignIn, ctx: AllyCtx = Depends(need("asignar")), db: Session = Depends(get_db)):
    """Reasigna entre los tecnicos activos de SU equipo (nunca uno interno ni de otro aliado). En una cuadrilla mixta
    (Crimson asigno la orden a la empresa con personal suyo) el personal de Crimson no lo toca el aliado: sus ayudantes
    se conservan tal cual y, si el tecnico principal es de Crimson, la reasignacion la hace Crimson."""
    from ..services.ally_scope import notify_ally_work

    _tope_asignar(ctx)  # cada cambio puede escribirle a su tecnico (y notify_ally_work no repite el mismo aviso)
    o = _my_order(db, ctx, oid)
    if o.status in fw.CERRADAS:
        raise HTTPException(409, f"La orden está {o.status}; ya no se modifica")
    team = ctx.scope.team_user_ids()
    antes = fw._crew(o)
    if o.technician_id and o.technician_id not in team and body.technician_id != o.technician_id:
        raise HTTPException(409, "El técnico a cargo de esta orden es de Crimson: la reasignación la hace Crimson")
    if body.technician_id is not None and body.technician_id != o.technician_id and body.technician_id not in team:
        raise HTTPException(422, "Ese técnico no es de su empresa")
    # solo se valida a quien entra (de su equipo y activo); quien ya estaba se conserva aunque hoy este inactivo
    for uid in [body.technician_id, *(body.helpers or [])]:
        if uid is not None and uid not in antes:
            _team_member(ctx, uid)
    de_crimson = [i for i in (o.helpers or []) if i not in team]
    propios = [i for i in (body.helpers or []) if i in team]
    o.technician_id = body.technician_id
    o.helpers = [i for i in dict.fromkeys([*de_crimson, *propios]) if i != o.technician_id]
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "assign", "work_order", o.id, {"technician_id": o.technician_id, "helpers": o.helpers}, ip=ctx.p.ip)
    notify_ally_work(db, ctx.p.tenant, fw._crew(o) - antes, o.number, o.title, o.scheduled_at, entity_id=o.id)
    db.commit()
    return _order_ally_out(db, ctx, o, full=True)


def _cerrar_jornada_vieja(db: Session, ctx: AllyCtx, o: WorkOrder, fin: datetime) -> int:
    """Tiempo de SU equipo que quedo corriendo de mas (se olvidaron de terminar la jornada): se cierra a la hora real que
    indica el aliado. Mismas reglas que una correccion interna: despues del inicio, no a futuro, y una jornada no pasa
    de fw.MAX_CORRIENDO_H. El tiempo de Crimson lo corrige Crimson."""
    team = ctx.scope.team_user_ids()
    now = _now()
    fin = fin.astimezone(UTC) if fin.tzinfo else fin.replace(tzinfo=UTC)
    limite = now - timedelta(hours=fw.MAX_CORRIENDO_H)
    viejas = [e for e in o.time_entries if e.ended_at is None and e.user_id in team and _aware(e.started_at) < limite]
    if fin > now + timedelta(minutes=5):
        raise HTTPException(422, "La hora en que terminaron no puede ser a futuro")
    for e in viejas:
        inicio = _aware(e.started_at)
        if fin <= inicio:
            raise HTTPException(422, "La hora en que terminaron tiene que ser después de la hora de inicio")
        if fin - inicio > timedelta(hours=fw.MAX_CORRIENDO_H):
            raise HTTPException(422, f"Una jornada no pasa de {fw.MAX_CORRIENDO_H} horas: indicá la hora real en que terminaron ese día")
    for e in viejas:
        e.ended_at = fin
        e.minutes = fw._mins(e.started_at, fin)
    if viejas:
        audit(
            db,
            ctx.p.tenant.id,
            ctx.p.user.id,
            "time_close_aliado",
            "work_order",
            o.id,
            {"entries": [e.id for e in viejas], "ended_at": fin.isoformat()},
            ip=ctx.p.ip,
        )
    return len(viejas)


@router.post("/ordenes/{oid}/{accion}")
def orden_accion(oid: int, accion: str, data: AllyProgressIn | None = None, ctx: AllyCtx = Depends(need("ordenes")), db: Session = Depends(get_db)):
    """Llegue / iniciar / terminar jornada / guardar avance / finalizar. Iniciar y terminar jornada abren y cierran el
    tiempo de SU equipo (el personal de Crimson de una cuadrilla mixta lleva el suyo). El material va por id (quantities,
    removed) y lo nuevo en "agregados": nunca se reescribe la lista (pisaria lo que otro anoto mientras tanto)."""
    o = _my_order(db, ctx, oid)
    if accion not in ORDER_ACTIONS:
        raise HTTPException(404, "Acción no soportada")
    if o.status in fw.CERRADAS:
        raise HTTPException(409, f"La orden está {o.status}; ya no se modifica")
    data = data or AllyProgressIn()
    team = ctx.scope.team_user_ids()
    photos = _fotos_del_equipo(db, ctx, data.photos, o.photos or []) if data.photos is not None else None
    for m in [*(data.materials or []), *(data.agregados or [])]:
        _product(db, ctx, m.product_id)
    if data.fin_jornada is not None and accion not in ("pause", "finish"):
        raise HTTPException(422, "La hora en que terminaron solo se indica al terminar la jornada o al finalizar")
    workers = None
    if accion == "start":
        activos = {m.user_id for m in ctx.scope.active_team()}
        workers = sorted(i for i in fw._crew(o) if i in activos)
        if not workers:
            raise HTTPException(409, "Asigná un técnico de su equipo antes de iniciar")
    materials = data.materials
    if materials is not None and not materials and o.materials:
        materials = None  # una lista vacia no borra lo planificado: para quitar una linea se manda su id en "removed"
    progress = fw.ProgressIn(
        tasks=data.tasks,
        photos=photos,
        notes=data.notes,
        materials=[fw.MaterialIn(**m.model_dump()) for m in materials] if materials is not None else None,
        customer_signature=data.customer_signature,
        quantities=data.quantities,
        removed=data.removed,
        workers=workers,
    )
    if data.fin_jornada is not None:
        _cerrar_jornada_vieja(db, ctx, o, data.fin_jornada)
    if accion == "pause":
        corriendo = sorted({e.user_id for e in o.time_entries if e.ended_at is None and e.user_id in team})
        if not corriendo:
            if data.fin_jornada is None:
                raise HTTPException(409, "No hay tiempo corriendo de su equipo")
            accion = "progress"  # ya se cerro con la hora indicada: solo se guarda lo demas
        progress.workers = corriendo or None
    if data.agregados and not o.stock_applied:
        from ..models import WorkOrderMaterial

        for m in data.agregados:
            o.materials.append(WorkOrderMaterial(product_id=m.product_id, name=m.name, quantity=m.quantity, unit=m.unit, planned=m.planned))
        db.flush()
    fw.apply_order_progress(db, o, progress, accion, ctx.p, ctx.p.tenant)
    db.commit()
    return _order_ally_out(db, ctx, o, full=True)


# ---------- Soporte ----------
def _my_ticket(db: Session, ctx: AllyCtx, tid: int) -> SupportTicket:
    t = ctx.scope.one(SupportTicket, tid, ctx.scope.tickets(), "Ticket")
    if not ctx.scope.ticket_visible(t):
        raise HTTPException(404, "Ticket no encontrado")
    return t


@router.get("/tickets")
def tickets(estado: str = Query("abiertos", pattern="^(abiertos|todos)$"), ctx: AllyCtx = Depends(need("tickets")), db: Session = Depends(get_db)):
    stmt = select(SupportTicket).where(*ctx.scope.tickets())
    if estado == "abiertos":
        stmt = stmt.where(SupportTicket.status.in_((*OPEN_TICKET, "resuelto") if ctx.scope.is_tech else OPEN_TICKET))
    rows = [t for t in db.scalars(stmt.order_by(SupportTicket.id.desc()).limit(300)) if ctx.scope.ticket_visible(t)]
    return [_ticket_out(db, ctx, t) for t in rows]


class AllyTicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: int
    subject: str = Field(min_length=3, max_length=200)
    kind: str = Field("soporte", pattern="^(" + "|".join(TICKET_KINDS) + ")$")
    priority: str = Field("media", pattern="^(baja|media|alta|critica)$")
    description: str | None = Field(None, max_length=4000)
    asset_id: int | None = None
    photos: list[str] = Field(default_factory=list, max_length=MAX_PHOTOS)


@router.post("/tickets", status_code=201)
def ticket_crear(data: AllyTicketIn, ctx: AllyCtx = Depends(need("tickets_crear")), db: Session = Depends(get_db)):
    ally_ticket_limiter.hit(f"{ctx.p.tenant.id}:{ctx.p.user.id}")  # cada ticket avisa por correo a la oficina
    pr = ctx.scope.one(Project, data.project_id, ctx.scope.projects(), "Proyecto")
    if data.asset_id is not None:
        ctx.scope.one(CustomerAsset, data.asset_id, ctx.scope.assets(), "Equipo")
    fotos = _fotos_del_equipo(db, ctx, data.photos)
    number, _ = next_number(db, ctx.p.tenant.id, "TCK")
    t = SupportTicket(
        tenant_id=ctx.p.tenant.id,
        number=number,
        customer_id=pr.end_customer_id or pr.customer_id,  # del proyecto, nunca del cuerpo
        project_id=pr.id,
        asset_id=data.asset_id,
        contact={"name": ctx.p.user.full_name, "origen": "portal_aliado"},
        kind=data.kind,
        channel="portal",
        level=1,
        priority=data.priority,
        status="nuevo",
        subject=data.subject,
        description=data.description,
        photos=fotos,
        tags=["aliado"],
        opened_by=ctx.p.user.id,
        ally_company_id=ctx.company.id,
        assigned_to=None,
    )
    slasvc.aplicar(db, ctx.p.tenant, t, _now())
    db.add(t)
    db.flush()
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "create", "support_ticket", t.id, {"prioridad": t.priority, "aliado": ctx.company.id}, ip=ctx.p.ip)
    avisar_ticket_aliado(db, ctx.p.tenant, ctx.company, pr, t)
    db.commit()
    return _ticket_out(db, ctx, t, full=True)


def avisar_ticket_aliado(db: Session, tenant, company, pr: Project, t: SupportTicket) -> None:
    """Aviso a administracion y supervision: una empresa aliada abrio un ticket (tambien lo usa la vista previa)."""
    e = html.escape
    notify_roles(
        db,
        tenant,
        ("admin", "supervisor"),
        f"Ticket del aliado {t.number} · {company.name[:40]}",
        f"<p><b>{e(company.name)}</b> abrió <b>{e(t.number)}</b> en el proyecto {e(pr.number)}.</p><p>{e(t.subject)} · prioridad {e(t.priority)}</p>"
        + cta(f"/tickets?id={t.id}", f"Abrir el ticket {t.number}"),
        "support_ticket",
        t.id,
    )


@router.get("/tickets/{tid}")
def ticket(tid: int, ctx: AllyCtx = Depends(need("tickets")), db: Session = Depends(get_db)):
    return _ticket_out(db, ctx, _my_ticket(db, ctx, tid), full=True)


class AllyNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=4000)
    photos: list[str] = Field(default_factory=list, max_length=MAX_PHOTOS)
    visible_al_cliente: bool = False
    hours: Decimal | None = Field(None, ge=0, le=24)


@router.post("/tickets/{tid}/notas", status_code=201)
def ticket_nota(tid: int, data: AllyNoteIn, ctx: AllyCtx = Depends(need("tickets")), db: Session = Depends(get_db)):
    t = _my_ticket(db, ctx, tid)
    if t.status == "cerrado":
        raise HTTPException(409, "Este ticket ya está cerrado")
    fotos = _fotos_del_equipo(db, ctx, data.photos)
    internal = not data.visible_al_cliente  # por defecto: Crimson la ve, el cliente final no
    t.notes.append(SupportNote(tenant_id=t.tenant_id, user_id=ctx.p.user.id, body=data.body, internal=internal, photos=fotos))
    if not internal and not t.first_reply_at:
        t.first_reply_at = _now()
    if data.hours is not None:
        t.hours = d(t.hours) + d(data.hours)
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "note_aliado", "support_ticket", t.id, {"interna": internal}, ip=ctx.p.ip)
    db.commit()
    return _ticket_out(db, ctx, t, full=True)


class AllyTicketPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    solution: str | None = Field(None, max_length=4000)
    hours: Decimal | None = Field(None, ge=0, le=1000)
    assigned_to: int | None = None


@router.patch("/tickets/{tid}")
def ticket_editar(tid: int, data: AllyTicketPatchIn, ctx: AllyCtx = Depends(need("tickets")), db: Session = Depends(get_db)):
    from .support_desk import _avisar_asignado, _mover

    if data.status == "cerrado":
        raise HTTPException(403, "Cerrar el ticket lo hace Crimson")
    if data.status is not None and data.status not in ("en_proceso", "esperando_cliente", "resuelto"):
        raise HTTPException(422, "Estado inválido")
    t = _my_ticket(db, ctx, tid)
    if t.status == "cerrado":
        raise HTTPException(409, "Este ticket ya está cerrado")
    if "assigned_to" in data.model_fields_set:
        if not ctx.p.can("aliado", "asignar"):
            raise HTTPException(403, "Reasignar el ticket lo hace el aliado")
        antes = t.assigned_to
        if antes and antes not in ctx.scope.team_user_ids() and data.assigned_to != antes:
            # lo atiende alguien de Crimson: el aliado no se lo quita
            raise HTTPException(409, "Este ticket lo atiende Crimson: la reasignación la hace Crimson")
        if data.assigned_to is not None and data.assigned_to != antes:
            _team_member(ctx, data.assigned_to)
        if antes != data.assigned_to:
            t.assigned_to = data.assigned_to
            if t.assigned_to and t.status == "nuevo":
                t.status = "asignado"
            audit(db, ctx.p.tenant.id, ctx.p.user.id, "assign", "support_ticket", t.id, {"de": antes, "a": t.assigned_to}, ip=ctx.p.ip)
            if t.assigned_to:
                _avisar_asignado(db, ctx.p, t)
    if data.solution is not None:
        t.solution = data.solution
    if data.hours is not None:
        t.hours = data.hours
    if data.status and data.status != t.status:
        if data.status == "resuelto" and not (data.solution or t.solution):
            raise HTTPException(422, "Escribí qué se hizo antes de resolver el ticket")
        _mover(db, ctx.p, t, data.status)
    db.commit()
    return _ticket_out(db, ctx, t, full=True)


# ---------- Mantenimientos y activos ----------
@router.get("/mantenimientos")
def mantenimientos(ctx: AllyCtx = Depends(need("mantenimientos")), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(MaintenanceContract).where(*ctx.scope.contracts()).order_by(MaintenanceContract.next_date.is_(None), MaintenanceContract.next_date)
    )
    return [_contract_out(db, ctx, c) for c in rows]


@router.get("/activos")
def activos(customer_id: int | None = None, q: str = "", ctx: AllyCtx = Depends(need("activos")), db: Session = Depends(get_db)):
    stmt = select(CustomerAsset).where(*ctx.scope.assets())
    if customer_id is not None:
        if customer_id not in set(db.scalars(ctx.scope.customer_ids())):
            return []
        stmt = stmt.where(CustomerAsset.customer_id == customer_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(CustomerAsset.name.ilike(like), CustomerAsset.serial.ilike(like), CustomerAsset.model.ilike(like)))
    return [_asset_out(db, ctx, a) for a in db.scalars(stmt.order_by(CustomerAsset.location, CustomerAsset.name).limit(500))]


# ---------- Catalogo e inventario ----------
def _main_image(pr: Product) -> str | None:
    imgs = [x for x in pr.images or [] if isinstance(x, dict) and x.get("url")]
    main = next((x for x in imgs if x.get("main")), imgs[0] if imgs else None)
    return main["url"] if main else None


def _availability(db: Session, ctx: AllyCtx, prods) -> dict[int, dict]:
    """La funcion unica con audiencia "aliado": propio libre y "bajo pedido: N" con su fecha, sin nombrar al
    proveedor ni costos (regla de aliados)."""
    from ..services.availability import availability_many

    return availability_many(db, ctx.p.tenant, prods, audience="aliado")


ALLY_AVAIL_KEYS = ("state", "label", "en_bodega", "bajo_pedido_cantidad", "actualizado_al", "cambio_al", "dato_vigente", "plazo")


def _catalog_out(db: Session, ctx: AllyCtx, pr: Product, fx: Decimal | None, fixed: dict, av: dict, cats: dict, full: bool = False) -> dict:
    precio, _fuente, estado = ally_pricing.ally_price(db, ctx.p.tenant, pr, ctx.company, fx=fx, fixed=fixed)
    a = av.get(pr.id) or {}
    tax = d(pr.taxes[0].tax.rate) if pr.taxes else None
    out = {
        "id": pr.id,
        "code": pr.code,
        "name": pr.name,
        "brand": pr.brand,
        "model": pr.model,
        "item_type": pr.item_type,
        "unit": pr.unit,
        "category": cats.get(pr.category_id),
        "description": pr.description_store,
        "datasheet_url": pr.datasheet_url,
        "tax_rate": tax,
        "currency": pr.currency,
        "precio_publico": pr.price,
        "precio_publico_con_iva": ally_pricing.with_tax(d(pr.price), pr),
        "precio_aliado": precio,
        "precio_aliado_con_iva": ally_pricing.with_tax(precio, pr),
        "precio_aliado_estado": estado,
        "disponibilidad": a.get("label"),
        "disponibilidad_detalle": {k: a.get(k) for k in ALLY_AVAIL_KEYS},
    }
    if full:
        out["images"] = [{"url": x.get("url"), "main": bool(x.get("main"))} for x in pr.images or [] if isinstance(x, dict) and x.get("url")]
    else:
        out["image"] = _main_image(pr)
    return out


def _cats(db: Session, tenant_id: int) -> dict[int, str]:
    return dict(db.execute(select(Category.id, Category.name).where(Category.tenant_id == tenant_id)).all())


@router.get("/catalogo")
def catalogo(
    q: str = "",
    category_id: int | None = None,
    brand: str | None = None,
    cursor: int | None = None,
    limit: int = Query(30, ge=1, le=100),
    ctx: AllyCtx = Depends(need("catalogo")),
    db: Session = Depends(get_db),
):
    stmt = select(Product).where(Product.tenant_id == ctx.p.tenant.id, Product.active.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.model.ilike(like), Product.brand.ilike(like)))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if cursor:
        stmt = stmt.where(Product.id < cursor)
    rows = db.scalars(stmt.order_by(Product.id.desc()).limit(limit + 1)).all()
    nxt = rows[limit].id if len(rows) > limit else None
    rows = rows[:limit]
    fx = rate_or_none(db, "USD")  # un solo tipo de cambio por request (None: "a consultar" lo que lo necesite)
    fixed = ally_pricing.fixed_prices(db, ctx.p.tenant.id, [r.id for r in rows], ctx.company.id)
    av = _availability(db, ctx, rows)
    cats = _cats(db, ctx.p.tenant.id)
    return {"items": [_catalog_out(db, ctx, pr, fx, fixed, av, cats) for pr in rows], "next_cursor": nxt}


@router.get("/catalogo/meta")
def catalogo_meta(ctx: AllyCtx = Depends(need("catalogo")), db: Session = Depends(get_db)):
    marcas = db.scalars(
        select(Product.brand)
        .where(Product.tenant_id == ctx.p.tenant.id, Product.active.is_(True), Product.brand.is_not(None))
        .distinct()
        .order_by(Product.brand)
    ).all()
    return {
        "categories": [{"id": c.id, "name": c.name} for c in db.scalars(select(Category).where(Category.tenant_id == ctx.p.tenant.id).order_by(Category.name))],
        "brands": [b for b in marcas if b],
    }


@router.get("/catalogo/{pid}")
def catalogo_ficha(pid: int, ctx: AllyCtx = Depends(need("catalogo")), db: Session = Depends(get_db)):
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != ctx.p.tenant.id or not pr.active:
        raise HTTPException(404, "Producto no encontrado")
    fx = rate_or_none(db, "USD")
    fixed = ally_pricing.fixed_prices(db, ctx.p.tenant.id, [pr.id], ctx.company.id)
    return _catalog_out(db, ctx, pr, fx, fixed, _availability(db, ctx, [pr]), _cats(db, ctx.p.tenant.id), full=True)


@router.get("/productos/buscar")
def productos_buscar(q: str = "", ctx: AllyCtx = Depends(need("ver")), db: Session = Depends(get_db)):
    """Para anotar materiales en el levantamiento o la OT: sin precio para nadie."""
    stmt = select(Product).where(Product.tenant_id == ctx.p.tenant.id, Product.active.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.model.ilike(like)))
    return [
        {"id": pr.id, "code": pr.code, "name": pr.name, "unit": pr.unit, "item_type": pr.item_type} for pr in db.scalars(stmt.order_by(Product.name).limit(20))
    ]


@router.get("/inventario")
def inventario(q: str = "", con_existencia: bool = True, ctx: AllyCtx = Depends(need("inventario")), db: Session = Depends(get_db)):
    """Solo cantidades (20:01-20:14), con la funcion unica: disponible = libre en bodegas de venta (las de garantias
    nunca) menos reservas, por bodega; y "bajo pedido: N" con su fecha, sin nombrar al proveedor."""
    tid = ctx.p.tenant.id
    stmt = select(Product).where(Product.tenant_id == tid, Product.active.is_(True), Product.item_type == "producto")
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.model.ilike(like)))
    prods = db.scalars(stmt.order_by(Product.name).limit(1000)).all()
    from ..services.availability import availability_many, sort_key

    av = availability_many(db, ctx.p.tenant, prods, audience="aliado", by_warehouse=True)
    rows = []
    for pr in sorted(prods, key=lambda x: sort_key(av.get(x.id))):  # primero lo que hay en bodega
        a = av.get(pr.id) or {}
        total = Decimal(str(a.get("en_bodega") or 0))
        bajo = Decimal(str(a.get("bajo_pedido_cantidad") or 0))
        if con_existencia and total <= 0 and bajo <= 0:
            continue
        rows.append(
            {
                "product_id": pr.id,
                "code": pr.code,
                "name": pr.name,
                "unit": pr.unit,
                "disponible_total": total,
                "bodegas": sorted([b for b in a.get("bodegas") or [] if b.get("nombre")], key=lambda b: b["nombre"]),
                "bajo_pedido": bajo > 0,
                "bajo_pedido_cantidad": bajo,
                "actualizado_al": a.get("actualizado_al"),
                "cambio_al": a.get("cambio_al"),
                "estado": a.get("state"),
                "etiqueta": a.get("label"),
            }
        )
        if len(rows) >= 300:
            break
    return {"rows": rows}


# ---------- Dinero del aliado (economia) y mensualidad ----------
def _economia():
    """El modulo de economia (services/commissions) con su contrato para aliados; None si todavia no esta."""
    try:
        if importlib.util.find_spec("app.services.commissions") is None:
            return None
        mod = importlib.import_module("app.services.commissions")
    except Exception:  # noqa: BLE001 - un modulo a medio integrar no debe tumbar el portal
        return None
    return mod if hasattr(mod, "ally_earnings") and hasattr(mod, "ally_project_earnings") else None


def ganancias_disponibles() -> bool:
    return _economia() is not None


@router.get("/ganancias")
def ganancias(desde: date | None = None, hasta: date | None = None, ctx: AllyCtx = Depends(need("ganancias")), db: Session = Depends(get_db)):
    econ = _economia()
    if econ is None:
        raise HTTPException(503, "Disponible pronto")
    hasta = hasta or date.today()
    if desde is None:  # 24 meses con el actual (spec economia 9.8)
        y, m = divmod(hasta.year * 12 + hasta.month - 1 - 23, 12)
        desde = date(y, m + 1, 1)
    # solo lo que Crimson le comparte (visible_to_ally y proyectos vivos), como el resto de /aliado/*
    out = econ.ally_earnings(db, ctx.p.tenant, ctx.company.id, desde, hasta, for_ally=True)
    fee = ally_access.fee_by_month(db, ctx.company, desde, hasta)
    meses = out.get("por_mes") or []
    vistos = {m.get("mes") for m in meses}
    for k in sorted(fee):
        if k not in vistos:
            meses.append({"mes": k, "devengado": Decimal(0), "recibido": Decimal(0)})
    for m in meses:
        m["mensualidad"] = fee.get(m.get("mes"), Decimal(0))
        m["neto"] = (d(m.get("devengado")) - d(m["mensualidad"])).quantize(Decimal("0.01"))
    out["por_mes"] = sorted(meses, key=lambda m: m.get("mes") or "")
    tot = out.setdefault("totales", {})
    tot["mensualidad"] = sum(fee.values(), Decimal(0)).quantize(Decimal("0.01"))
    tot["neto"] = (d(tot.get("devengado")) - tot["mensualidad"]).quantize(Decimal("0.01"))
    out["nota"] = "Montos sin IVA. 'Neto' es lo que le deja la alianza después de la mensualidad de la plataforma."
    return out


@router.get("/ganancias/proyectos/{pid}")
def ganancias_proyecto(pid: int, ctx: AllyCtx = Depends(need("ganancias")), db: Session = Depends(get_db)):
    pr = ctx.scope.one(Project, pid, ctx.scope.projects(), "Proyecto")
    econ = _economia()
    if econ is None:
        raise HTTPException(503, "Disponible pronto")
    return econ.ally_project_earnings(db, ctx.p.tenant, ctx.company.id, pr.id, for_ally=True)


def _emitida(inv: Invoice) -> bool:
    """La factura ya le llego al aliado (services/ally_access.is_emitted): el mismo criterio para facturas, suscripcion
    y el descuento de la mensualidad en /aliado/ganancias. Un borrador "creado" no se ve ni se descarga."""
    return ally_access.is_emitted(inv)


def _factura_visible(c, inv: Invoice) -> bool:
    if not c.customer_id or inv.tenant_id != c.tenant_id or inv.customer_id != c.customer_id:
        return False
    return _emitida(inv)


def _estado(inv: Invoice) -> str:
    """Estado real para el aliado (plan 1.4): por pagar, vencida, abonada o pagada."""
    from ..services.invoice_states import cr_today

    if inv.status == "pagada" or d(inv.balance) <= 0:
        return "pagada"
    if (inv.due_date and inv.due_date < cr_today()) or inv.status == "vencida":
        return "vencida"
    return "abonada" if inv.status == "parcial" else "por_pagar"


def _invoice_out(inv: Invoice) -> dict:
    estado = _estado(inv)
    return {
        "id": inv.id,
        "number": inv.number,
        "issue_date": inv.issue_date,
        "due_date": inv.due_date,
        "total": inv.total,
        "balance": inv.balance,
        "status": inv.status,
        "estado": estado,
        "pagable": estado != "pagada",  # con saldo: "Pagar en línea" (POST /aliado/facturas/{id}/pagar)
        "currency": inv.currency,
        "mensualidad": inv.external_order == ally_access.PLAN_ORDER,
    }


@router.get("/suscripcion")
def suscripcion(ctx: AllyCtx = Depends(need("suscripcion")), db: Session = Depends(get_db)):
    from ..models import Recurrence

    c = ctx.company
    r = db.get(Recurrence, c.recurrence_id) if c.recurrence_id else None
    lines = ((r.template or {}).get("lines") or []) if r else []
    facturas = [i for i in ally_access.plan_invoices(db, c) if _emitida(i)]
    # el atraso tambien sale de lo emitido: un borrador que Crimson no ha enviado no le cuenta como vencido
    st = ally_access.plan_status(db, c, visible=_emitida)
    return {
        "plan": {
            "monto": c.plan_amount,
            "moneda": c.plan_currency,
            "modo": c.plan_mode,
            "cantidad": d(lines[0].get("quantity")) if lines else None,
            "frecuencia": "mensual",
            "proxima_fecha": r.next_date,
            "activa": r.active,
        }
        if r
        else None,
        "estado": st["estado"],
        "vencido": st["vencido"],
        "facturas": [_invoice_out(i) for i in facturas],
        "seats": {k: v for k, v in ally_access.seats(db, c).items() if k in ("used", "max")},
    }


@router.get("/facturas")
def facturas(ctx: AllyCtx = Depends(need("suscripcion")), db: Session = Depends(get_db)):
    c = ctx.company
    if not c.customer_id:
        return []
    rows = db.scalars(
        select(Invoice)
        .where(Invoice.tenant_id == ctx.p.tenant.id, Invoice.customer_id == c.customer_id, Invoice.status != "anulada")
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        .limit(200)
    ).all()
    return [_invoice_out(i) for i in rows if _factura_visible(c, i)]


@router.get("/facturas/{iid}/pdf")
def factura_pdf(iid: int, ctx: AllyCtx = Depends(need("suscripcion")), db: Session = Depends(get_db)):
    inv = db.get(Invoice, iid)
    if inv is None or not _factura_visible(ctx.company, inv):
        raise HTTPException(404, "Factura no encontrada")
    html_doc = render_html(db, inv, ctx.p.tenant)
    pdf = render_pdf(html_doc)
    if ctx.preview_by is None:
        audit(db, ctx.p.tenant.id, ctx.p.user.id, "download_aliado", "invoice", inv.id, ip=ctx.p.ip)
        db.commit()
    if pdf is None:
        return HTMLResponse(html_doc, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{inv.number}.pdf"'})


@router.post("/facturas/{iid}/pagar")
def factura_pagar(iid: int, ctx: AllyCtx = Depends(need("suscripcion")), db: Session = Depends(get_db)):
    """Enlace de pago de una factura que ya le llego al aliado y tiene saldo (el mismo del correo; se crea si vencio)."""
    from ..services.documents import get_or_create_payment_link

    inv = db.get(Invoice, iid)
    if inv is None or not _factura_visible(ctx.company, inv):
        raise HTTPException(404, "Factura no encontrada")
    if d(inv.balance) <= 0:
        raise HTTPException(409, "Esta factura no tiene saldo por pagar")
    if ctx.preview_by is not None:
        raise HTTPException(409, "En la vista previa no se generan enlaces de pago")
    link = get_or_create_payment_link(db, ctx.p.tenant.id, inv)
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "paylink_aliado", "invoice", inv.id, ip=ctx.p.ip)
    db.commit()
    return {"url": link.url, "expires_at": link.expires_at}


# ---------- Mi equipo ----------
@router.get("/usuarios")
def usuarios(ctx: AllyCtx = Depends(need("usuarios")), db: Session = Depends(get_db)):
    out = ally_access.users_out(db, ctx.company)
    for u in out["users"]:
        u["me"] = u["id"] == ctx.p.user.id
    out["plan_por_usuario"] = ctx.company.plan_mode == "por_usuario" and bool(ctx.company.recurrence_id)
    return out


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    name: str | None = Field(None, max_length=120)  # se ignora: no va en el correo (ver invitar)
    role: str | None = None  # se ignora: el aliado solo invita tecnicos


INVITE_SENT = "Si ese correo puede recibir acceso, le llegará la invitación. Si no le llega, pídale el enlace a Crimson."


@router.post("/usuarios", status_code=202)
def invitar(data: InviteIn, ctx: AllyCtx = Depends(need("usuarios")), db: Session = Depends(get_db)):
    """El aliado invita a su gente, siempre como tecnico_aliado y siempre a SU empresa.

    Misma respuesta pase lo que pase con el correo (202): el portal no confirma si ese correo es de alguien de Crimson,
    de un cliente o de otro aliado. Nunca devuelve el enlace (con el token, el aliado podria aceptar por otra persona y
    quedarse con su correo): le llega por correo y, si no sale, lo entrega Crimson. Sin el nombre en el correo (texto
    libre de un usuario externo con la marca de la empresa). Con tope por empresa y dia."""
    ally_invite_limiter.hit(f"{ctx.p.tenant.id}:{ctx.company.id}")
    email = str(data.email).strip().lower()
    st = ally_access.seats(db, ctx.company, exclude_email=email)
    if ctx.company.max_users is not None and st["used"] >= ctx.company.max_users:
        # antes de mirar el correo: el tope no dice nada de quien es
        raise HTTPException(409, f"Su plan permite {ctx.company.max_users} usuarios")
    ok = True
    try:
        ally_access.invite_ally_user(db, ctx.p.tenant, ctx.company, email, "tecnico_aliado", ctx.p.user.id, by_ally=True)
    except HTTPException as ex:
        if ex.status_code != 409 or "plan permite" in str(ex.detail):
            raise
        ok = False
    audit(db, ctx.p.tenant.id, ctx.p.user.id, "invite_aliado", "ally_company", ctx.company.id, {"email": email, "creada": ok}, ip=ctx.p.ip)
    db.commit()
    return {"ok": True, "detail": INVITE_SENT}


@router.post("/usuarios/invitaciones/{iid}/revocar")
def invitacion_revocar(iid: int, ctx: AllyCtx = Depends(need("usuarios")), db: Session = Depends(get_db)):
    inv = db.get(Invitation, iid)
    if not inv or inv.tenant_id != ctx.p.tenant.id or inv.ally_company_id != ctx.company.id or inv.accepted_at:
        raise HTTPException(404, "Invitación no encontrada")
    if not inv.revoked_at:
        now = _now()
        inv.revoked_at, inv.revoked_by = now, ctx.p.user.id
        inv.expires_at = min(_aware(inv.expires_at), now)
        audit(db, ctx.p.tenant.id, ctx.p.user.id, "invite_revoke", "invitation", inv.id, ip=ctx.p.ip)
        db.commit()
    return {"ok": True}


class UsuarioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


@router.patch("/usuarios/{uid}")
def usuario(uid: int, data: UsuarioIn, ctx: AllyCtx = Depends(need("usuarios")), db: Session = Depends(get_db)):
    from .ally_admin import set_member_active

    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == ctx.p.tenant.id, TenantUser.user_id == uid, TenantUser.ally_company_id == ctx.company.id))
    if not m:
        raise HTTPException(404, "Usuario no encontrado")
    if uid == ctx.p.user.id:
        raise HTTPException(409, "No podés desactivarte a vos mismo")
    if m.role_code != "tecnico_aliado":
        raise HTTPException(403, "Ese usuario lo administra Crimson. Escríbanos si necesita cambiarlo.")
    if m.active != data.active:
        set_member_active(db, ctx.p, ctx.company, m, data.active)
        audit(db, ctx.p.tenant.id, ctx.p.user.id, "update_aliado", "ally_company_user", uid, {"active": data.active}, ip=ctx.p.ip)
    db.commit()
    return {"ok": True}


# ---------- Archivos ----------
@router.post("/media", status_code=201)
async def media_subir(file: UploadFile = File(...), ctx: AllyCtx = Depends(need("ver")), db: Session = Depends(get_db)):
    """Foto o PDF privado del equipo del aliado. El dedupe es SOLO contra lo que subio esta misma persona: subir los
    mismos bytes que una foto de Crimson crea un registro propio (si no, sus fotos no validarian)."""
    ally_media_limiter.hit(f"{ctx.p.tenant.id}:{ctx.p.user.id}")
    data = await file.read(MAX_PDF + 1)
    kind = sniff(data)
    if not kind:
        raise HTTPException(415, "Formato no permitido: use una foto PNG, JPG, WEBP o GIF, o un PDF")
    limit = MAX_PDF if kind == "application/pdf" else MAX_IMAGE
    if len(data) > limit:
        raise HTTPException(413, f"Archivo muy grande (máximo {limit // (1024 * 1024)} MB)")
    digest = hashlib.sha256(data).hexdigest()
    m = db.scalar(
        select(Media).where(
            Media.tenant_id == ctx.p.tenant.id,
            Media.sha256 == digest,
            Media.created_by == ctx.p.user.id,
            Media.visibility == "private",
            Media.module == "field",
        )
    )
    if m is None:
        # tope por empresa aliada (los archivos se guardan en la base): lo que subio todo su equipo
        tope = max(cfg.ally_media_quota_mb, 0) * 1024 * 1024
        usado = db.scalar(
            select(func.coalesce(func.sum(Media.size), 0)).where(Media.tenant_id == ctx.p.tenant.id, Media.created_by.in_(ctx.scope.team_user_ids()))
        )
        if int(usado or 0) + len(data) > tope:
            raise HTTPException(413, f"Su empresa llegó al tope de archivos ({cfg.ally_media_quota_mb} MB). Escríbanos para ampliarlo.")
        name = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "._- " else "_" for ch in (file.filename or "archivo"))[:120] or "archivo"
        m = Media(
            tenant_id=ctx.p.tenant.id,
            key=secrets.token_urlsafe(18),
            filename=name,
            content_type=kind,
            size=len(data),
            sha256=digest,
            data=data,
            created_by=ctx.p.user.id,
            visibility="private",
            module="field",
        )
        db.add(m)
        db.flush()
        audit(db, ctx.p.tenant.id, ctx.p.user.id, "upload_aliado", "media", m.id, {"type": kind, "size": len(data)}, ip=ctx.p.ip)
        db.commit()
    url = public_url(m)
    return {
        "id": m.id,
        "key": m.key,
        "url": url,
        "filename": m.filename,
        "content_type": m.content_type,
        "size": m.size,
        "visibility": "private",
        "signed_url": ma.with_signature(url, m.key),
    }


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(default_factory=list, max_length=200)


@router.post("/media/firmar")
def media_firmar(data: SignIn, ctx: AllyCtx = Depends(need("ver")), db: Session = Depends(get_db)):
    """Como /media/sign, pero solo firma lo publico y lo privado que subio alguien de SU equipo. Lo demas no vuelve.
    Las fotos privadas de Crimson le llegan ya firmadas en las salidas de los registros que ve."""
    keys = {k for k in (ma.key_of(u) for u in data.urls) if k}
    rows = {m.key: m for m in db.scalars(select(Media).where(Media.tenant_id == ctx.p.tenant.id, Media.key.in_(keys)))} if keys else {}
    team = ctx.scope.team_user_ids()
    out: dict[str, str] = {}
    exp_min: int | None = None
    for u in data.urls:
        k = ma.key_of(u)
        m = rows.get(k) if k else None
        if not m:
            continue
        if m.visibility != "private":
            out[u] = ma.base_url(u)
        elif m.created_by in team:
            exp, s = ma.sign(m.key)
            out[u] = f"{ma.base_url(u)}?e={exp}&s={s}"
            exp_min = exp if exp_min is None else min(exp_min, exp)
    return {"urls": out, "expires_at": exp_min}
