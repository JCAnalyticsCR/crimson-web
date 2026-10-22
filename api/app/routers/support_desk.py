"""Mesa de soporte: tickets de clientes, contratos de mantenimiento y comisiones.

Un ticket puede nacer de tres lados: lo abre alguien de Crimson, lo genera el worker cuando toca un
mantenimiento preventivo, o lo dispara una garantia por vencer. Si hay que ir al sitio, el ticket
genera una orden de trabajo y se reusa todo lo de campo en lugar de inventar un flujo paralelo.

Ojo: "support.py" es otra cosa (el acceso temporal de soporte de JC Analytics a la cuenta). Esto es
el soporte que Crimson le da a SUS clientes.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import CLIENT_ROLES, Principal, require, require_any, roles_like
from ..models import (
    AllyCompany,
    AllyParticipation,
    AuditLog,
    Customer,
    CustomerAsset,
    EmailOutbox,
    MaintenanceContract,
    Project,
    SupportNote,
    SupportTicket,
    TenantUser,
    User,
    WorkOrder,
)
from ..services import sla as slasvc
from ..services.archive import live
from ..services.documents import audit
from ..services.mail import notify_roles, queue_email, user_inbox
from ..services.refs import check_refs
from ..services.sequences import next_number
from ..services.totals import d

router = APIRouter(tags=["soporte"])

OPEN_STATES = slasvc.ABIERTOS  # una sola definicion (services/sla.py)
# Los SLA ya no viven aqui: salen de services/sla.py (contrato > ajustes de la empresa > defecto).
# SLA_HORAS queda como el defecto de primera respuesta por compatibilidad con quien lo lea.
SLA_HORAS = {k: v["respuesta"] for k, v in slasvc.SLA_DEFECTO.items()}
ORIGEN_SLA = {
    "contrato": "Contrato",
    "empresa": "Ajustes de la empresa",
    "defecto": "Valores por defecto",
    "preventivo": "Mantenimiento preventivo",
}


def _own(db: Session, tid: int, p: Principal) -> SupportTicket:
    t = db.get(SupportTicket, tid)
    if not t or t.tenant_id != p.tenant.id:
        raise HTTPException(404, "Ticket no encontrado")
    # mismo alcance que la lista (ticket_scope): sin ver_todo, solo los asignados a mi o abiertos por mi. Un ticket
    # sin responsable NO es de todos: si no, con ids seguidos se lee (notas internas incluidas) y se "toma"
    if not p.can("support_desk", "ver_todo") and p.user.id not in (t.assigned_to, t.opened_by):
        raise HTTPException(404, "Ticket no encontrado")
    return t


def _writable(t: SupportTicket) -> None:
    """Lo que esta en la papelera o el archivo se lee (el detalle se abre desde Papelera) pero no se modifica."""
    if t.trashed_at:
        raise HTTPException(409, "El ticket está en la papelera: restauralo para modificarlo")
    if t.archived_at:
        raise HTTPException(409, "El ticket está archivado: restauralo para modificarlo")


# Quien atiende soporte: los mismos que salen en meta.agents (el responsable de un ticket)
AGENT_ROLES = ("admin", "supervisor", "tecnico")  # y los roles creados a partir de esos (roles_like)
ESTADO_LABEL = {
    "nuevo": "Nuevo",
    "asignado": "Asignado",
    "en_proceso": "En proceso",
    "esperando_cliente": "Esperando al cliente",
    "resuelto": "Resuelto",
    "cerrado": "Cerrado",
}
CAMPO_LABEL = {
    "subject": "asunto",
    "customer_id": "cliente",
    "contact": "contacto",
    "asset_id": "equipo",
    "project_id": "proyecto",
    "kind": "tipo",
    "channel": "canal",
    "level": "nivel",
    "priority": "prioridad",
    "description": "descripción",
    "billable": "cobro",
    "tags": "etiquetas",
}
_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic")


def _fecha_cr(dt: datetime | None) -> str:
    """15 oct 9:00 a. m. (hora de Costa Rica)."""
    if not dt:
        return ""
    x = _aware(dt).astimezone(slasvc.CR)
    h = x.hour % 12 or 12
    return f"{x.day} {_MESES[x.month - 1]} {h}:{x.minute:02d} {'a. m.' if x.hour < 12 else 'p. m.'}"


def _norm(txt: str) -> str:
    """Nombre en minusculas, sin tildes ni espacios de mas (clave n: de un aliado sin ficha)."""
    base = unicodedata.normalize("NFKD", txt or "")
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).lower().split())


def _ally_key(a: AllyParticipation) -> str:
    if a.ally_company_id:
        return f"a:{a.ally_company_id}"
    if a.customer_id:
        return f"c:{a.customer_id}"
    if a.supplier_id:
        return f"s:{a.supplier_id}"
    return f"n:{_norm(a.name)}"


def _ally_map(db: Session, tenant_id: int, project_ids: set[int] | None = None) -> dict[int, list[dict]]:
    """project_id -> [{key, name, role}] en UNA consulta.

    La "empresa aliada" de un ticket se DERIVA de su proyecto (participacion de esa empresa) o, con la clave a:<id>,
    ademas de la asignacion explicita del ticket a la empresa aliada (SupportTicket.ally_company_id)."""
    if project_ids is not None and not project_ids:
        return {}
    stmt = select(AllyParticipation).where(AllyParticipation.tenant_id == tenant_id, AllyParticipation.project_id.is_not(None))
    if project_ids is not None:
        stmt = stmt.where(AllyParticipation.project_id.in_(project_ids))
    out: dict[int, list[dict]] = {}
    for a in db.scalars(stmt.order_by(AllyParticipation.id)):
        lst = out.setdefault(a.project_id, [])
        key = _ally_key(a)
        if all(x["key"] != key for x in lst):
            lst.append({"key": key, "name": a.name, "role": a.role})
    return out


ALLY_KEY = re.compile(r"^(?:[csa]:\d+|n:.{1,160})$")


def _client_user_ids(db: Session, tenant_id: int, user_ids) -> set[int]:
    """De esos usuarios, los que son del portal del cliente (su nota es "Cliente respondió"). Una consulta."""
    ids = {i for i in user_ids if i}
    if not ids:
        return set()
    return set(
        db.scalars(select(TenantUser.user_id).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id.in_(ids), TenantUser.role_code.in_(CLIENT_ROLES)))
    )


def _ctx(db: Session, p: Principal, rows: list[SupportTicket], ahora: datetime | None = None) -> dict:
    """Todo lo que _out necesita de otras tablas, precalculado para la lista (una consulta por mapa: nada de N+1)."""
    uids: set[int] = set()
    pids: set[int] = set()
    for t in rows:
        uids.update(x for x in (t.opened_by, t.assigned_to) if x)
        uids.update(n.user_id for n in t.notes if n.user_id)
        if t.project_id:
            pids.add(t.project_id)
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(uids))).all()) if uids else {}
    projects = dict(db.execute(select(Project.id, Project.number).where(Project.tenant_id == p.tenant.id, Project.id.in_(pids))).all()) if pids else {}
    return {
        "ahora": ahora or datetime.now(UTC),
        "users": users,
        "projects": projects,
        "clientes": _client_user_ids(db, p.tenant.id, uids),
        "aliados": _ally_map(db, p.tenant.id, _ally_projects(db, p, pids)),
        "empresas": _ally_names(db, p.tenant.id, {t.ally_company_id for t in rows if t.ally_company_id}),
    }


def _ally_names(db: Session, tenant_id: int, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(AllyCompany.id, AllyCompany.name).where(AllyCompany.tenant_id == tenant_id, AllyCompany.id.in_(ids))).all())


def _ally_projects(db: Session, p: Principal, pids: set[int]) -> set[int]:
    """De esos proyectos, de cuales puede ver las empresas aliadas: con support_desk.ver_todo (como meta.allies) o
    projects.ver_todo, todos; si no, solo los de su alcance de proyectos (sees_project). Dos consultas, sin N+1."""
    if not pids or p.can("support_desk", "ver_todo") or p.can("projects", "ver_todo"):
        return pids
    from .projects import sees_project

    return {pr.id for pr in db.scalars(select(Project).where(Project.tenant_id == p.tenant.id, Project.id.in_(pids))) if sees_project(p, pr)}


def _check_project(db: Session, p: Principal, project_id: int | None) -> None:
    """El proyecto de un ticket tiene que ser uno que esta persona ve (si no, con un id ajeno leeria su numero y sus
    empresas aliadas en la respuesta). check_refs ya garantizo que es de esta empresa."""
    if not project_id or p.can("projects", "ver_todo"):
        return
    from .projects import sees_project

    pr = db.get(Project, project_id)
    if not pr or pr.tenant_id != p.tenant.id or not sees_project(p, pr):
        raise HTTPException(404, "Proyecto no encontrado")


def _history(db: Session, t: SupportTicket) -> list[dict]:
    """Lo que paso con el ticket (estado, responsable, proximo paso, ediciones) desde la bitacora, mas viejo primero."""
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.tenant_id == t.tenant_id, AuditLog.entity == "support_ticket", AuditLog.entity_id == t.id)
            .order_by(AuditLog.id.desc())
            .limit(100)
        )
    )[::-1]
    ids = {r.user_id for r in rows if r.user_id}
    for r in rows:  # "Asigno a ..." nombra a la persona: tambien en la misma consulta
        if r.action == "assign" and (r.diff or {}).get("a"):
            ids.add((r.diff or {})["a"])
    names = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()) if ids else {}
    out = []
    for r in rows:
        dd = r.diff or {}
        if r.action == "create":
            label = "Abrió el ticket"
        elif r.action == "create_portal":
            label = "Abrió el ticket desde el portal del cliente"
        elif r.action == "status":
            label = f"Cambió el estado a {ESTADO_LABEL.get(dd.get('status'), dd.get('status') or '?')}"
        elif r.action == "assign":
            label = f"Asignó a {names.get(dd.get('a'), 'otra persona')}" if dd.get("a") else "Quitó el responsable"
        elif r.action == "next_step":
            if dd.get("next_step") or dd.get("next_step_at"):
                cuando = _fecha_cr(datetime.fromisoformat(dd["next_step_at"])) if dd.get("next_step_at") else ""
                label = f"Próximo paso: {dd.get('next_step') or 'Seguimiento'}" + (f" · {cuando}" if cuando else "")
            else:
                label = "Quitó el próximo paso"
        elif r.action == "update":
            campos = [CAMPO_LABEL.get(c, c) for c in dd.get("campos") or []]
            label = f"Editó: {', '.join(campos)}" if campos else "Editó el ticket"
        elif r.action == "work_order":
            label = f"Creó la orden {dd.get('work_order') or ''}".strip()
        else:
            continue  # montos, mantenimientos y demas no son seguimiento
        out.append({"at": r.at, "user": names.get(r.user_id) if r.user_id else "Sistema", "action": r.action, "label": label})
    return out


def _out(db: Session, t: SupportTicket, full: bool = True, p: Principal | None = None, ctx: dict | None = None) -> dict:
    """Ticket para el EQUIPO interno. El portal del cliente tiene su propio serializador (client_portal.py) y nunca
    usa este: aqui viajan notas internas (last_follow, notes) y la bitacora.
    El monto (lo que se le cobra al cliente) es precio: el tecnico no lo ve (Andres 22:07-22:23)."""
    if ctx is None:
        ctx = (
            _ctx(db, p, [t])
            if p is not None
            else {
                "ahora": datetime.now(UTC),
                "users": {},
                "projects": {},
                "clientes": set(),
                "aliados": {},
                "empresas": _ally_names(db, t.tenant_id, {t.ally_company_id} - {None}),
            }
        )
    ahora = ctx["ahora"]
    users = ctx["users"]

    def nombre(uid: int | None) -> str | None:
        if not uid:
            return None
        if uid not in users:
            u = db.get(User, uid)
            users[uid] = u.full_name if u else None
        return users[uid]

    c = db.get(Customer, t.customer_id) if t.customer_id else None
    a = db.get(CustomerAsset, t.asset_id) if t.asset_id else None
    venc = slasvc.vencimientos(t, ahora)
    vencido, resolucion_vencida = venc["respuesta"], venc["resolucion"]
    s = t.sla or {}
    sem = slasvc.semaforo(t, ahora)
    abierto = slasvc.es_abierto(t)
    humanas = [n for n in t.notes if n.user_id]
    ult = humanas[-1] if humanas else None
    if t.project_id and t.project_id not in ctx["projects"]:
        pr = db.get(Project, t.project_id)
        ctx["projects"][t.project_id] = pr.number if pr else None
    out = {
        "id": t.id,
        "number": t.number,
        "subject": t.subject,
        "kind": t.kind,
        "channel": t.channel,
        "level": t.level,
        "priority": t.priority,
        "status": t.status,
        "customer_id": t.customer_id,
        "customer": c.name if c else (t.contact or {}).get("name"),
        "asset_id": t.asset_id,
        "asset": a.name if a else None,
        "project_id": t.project_id,
        "project": ctx["projects"].get(t.project_id) if t.project_id else None,
        "contract_id": t.contract_id,
        "work_order_id": t.work_order_id,
        "invoice_id": t.invoice_id,
        "assigned_to": t.assigned_to,
        "assigned": nombre(t.assigned_to),
        "opened_by": t.opened_by,
        "opened_by_name": nombre(t.opened_by),
        "due_at": t.due_at,
        "first_reply_at": t.first_reply_at,
        "resolved_at": t.resolved_at,
        "closed_at": t.closed_at,
        "resolve_due_at": t.resolve_due_at,
        "sla_vencido": vencido,
        "resolucion_vencida": resolucion_vencida,
        "fuera_de_tiempo": vencido or resolucion_vencida,
        # semaforo de la bandeja (services/sla.py): el portal recalcula color y texto cada 30 s con vence_at/ventana_min
        "semaforo": sem,
        "grupo": slasvc.grupo(t, ahora, sem),
        "next_step": t.next_step,
        "next_step_at": t.next_step_at,
        "next_step_vencido": bool(abierto and t.next_step_at and _aware(t.next_step_at) < ahora),
        "last_follow": {
            "at": ult.created_at,
            "user": nombre(ult.user_id),
            "body": ult.body[:140],
            "internal": ult.internal,
            "from_client": ult.user_id in ctx["clientes"],
        }
        if ult
        else None,
        "last_activity_at": slasvc.ultimo_movimiento(t),
        "allies": ctx["aliados"].get(t.project_id, []) if t.project_id else [],
        "ally_company_id": t.ally_company_id,
        "ally_company": ctx.get("empresas", {}).get(t.ally_company_id) if t.ally_company_id else None,
        # que SLA aplico y de donde salio, para mostrarlo en el ticket
        "sla": {
            "respuesta_h": s.get("respuesta"),
            "resolucion_h": s.get("resolucion"),
            "origen": s.get("origen"),
            "origen_label": ORIGEN_SLA.get(s.get("origen"), "Sin SLA registrado"),
            "contrato": s.get("contrato"),
        }
        if s
        else None,
        "hours": t.hours,
        "billable": t.billable,
        "amount": t.amount if p is None or p.sees_prices else None,
        "tags": t.tags,
        "created_at": t.created_at,
        "archived_at": t.archived_at,
        "trashed_at": t.trashed_at,
    }
    if full:
        out["description"] = t.description
        out["contact"] = t.contact or {}
        out["solution"] = t.solution
        out["photos"] = t.photos
        out["notes"] = [
            {
                "id": n.id,
                "body": n.body,
                "internal": n.internal,
                "share_ally": bool(n.share_ally),
                "photos": n.photos,
                "user": nombre(n.user_id) if n.user_id else "Sistema",
                "from_client": n.user_id in ctx["clientes"],
                "created_at": n.created_at,
            }
            for n in t.notes
        ]
        out["history"] = _history(db, t)
        # la visita sin tecnico se destaca en el detalle ("por programar")
        wo = db.get(WorkOrder, t.work_order_id) if t.work_order_id else None
        out["work_order_status"] = wo.status if wo is not None and wo.tenant_id == t.tenant_id else None
    return out


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    customer_id: int | None = None
    contact: dict = Field(default_factory=dict)
    asset_id: int | None = None
    project_id: int | None = None
    kind: str = Field("soporte", pattern="^(soporte|garantia|mantenimiento|visita|instalacion|consulta)$")
    channel: str | None = Field(None, max_length=20)
    level: int = Field(1, ge=1, le=3)
    priority: str = Field("media", pattern="^(baja|media|alta|critica)$")
    description: str | None = None
    assigned_to: int | None = None
    billable: bool = False
    tags: list = Field(default_factory=list)
    # empresa aliada que lo atiende. Sin dato: la del responsable si es de un aliado; PUT solo la cambia si viene
    ally_company_id: int | None = None


# Lo que el ticket enlaza es de ESTA empresa: la ficha responde sus nombres y el aviso va al correo del asignado
TICKET_REFS = {
    "customer_id": (Customer, "Cliente no encontrado"),
    "asset_id": (CustomerAsset, "Activo no encontrado"),
    "project_id": (Project, "Proyecto no encontrado"),
}
TICKET_USERS = {"assigned_to": "Ese responsable no es un usuario activo de la empresa"}


def ticket_scope(p: Principal, mine: bool = False) -> list:
    """Tickets que esta persona puede ver: todos con support_desk.ver_todo, si no los suyos."""
    out = [SupportTicket.tenant_id == p.tenant.id, live(SupportTicket)]  # lo archivado o en la papelera no cuenta
    if mine or not p.can("support_desk", "ver_todo"):
        out.append(or_(SupportTicket.assigned_to == p.user.id, SupportTicket.opened_by == p.user.id))
    return out


PENDIENTES_SOPORTE = ("abiertos", "fuera_de_tiempo")
ORDENES = ("recientes", "urgencia")
MAX_URGENCIA = 1000  # tope duro de filas que se ordenan en Python
PRIO_RANGO = {"critica": 0, "alta": 1, "media": 2, "baja": 3}


def soporte_resumen(db: Session, p: Principal) -> dict[str, int]:
    """Lo que cuentan el inicio y la pantalla de tickets: mismo alcance, misma definicion (services/sla.py)."""
    rows = db.scalars(select(SupportTicket).where(*ticket_scope(p), SupportTicket.status.in_(slasvc.ABIERTOS))).all()
    return slasvc.resumen(rows)


def _ts(dt: datetime | None) -> float:
    return _aware(dt).timestamp() if dt else float("inf")


def _urgencia(t: SupportTicket, ahora: datetime) -> tuple:
    """Orden de la bandeja: por grupo y, dentro del grupo, lo mas urgente primero."""
    sem = slasvc.semaforo(t, ahora)
    g = slasvc.grupo(t, ahora, sem)
    rango = slasvc.GRUPOS.index(g)
    if g in ("vencidos", "por_vencer"):
        clave: tuple = (sem["restante_min"] if sem["restante_min"] is not None else float("inf"),)
    elif g == "seguimiento_hoy":
        clave = (_ts(t.next_step_at),)
    elif g in ("esperando", "en_tiempo"):
        clave = (_ts(sem["vence_at"]), PRIO_RANGO.get(t.priority, 9))
    else:
        clave = (-t.id,)
    return (rango, *clave, -t.id)


@router.get("/tickets")
def tickets(
    status: str | None = None,
    kind: str | None = None,
    customer_id: int | None = None,
    mine: bool = False,
    q: str | None = None,
    pendiente: str | None = None,
    filtro: str | None = None,
    assigned: str | None = None,
    priority: str | None = Query(None, pattern="^(baja|media|alta|critica)$"),
    ally: str | None = None,
    orden: str = "recientes",
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("support_desk", "ver")),
    db: Session = Depends(get_db),
):
    if pendiente and pendiente not in PENDIENTES_SOPORTE:
        raise HTTPException(422, "Filtro de pendientes desconocido")
    if filtro and filtro not in slasvc.FILTROS:
        raise HTTPException(422, "Filtro desconocido")
    if orden not in ORDENES:
        raise HTTPException(422, "Orden desconocido")
    if ally and not ALLY_KEY.match(ally):
        raise HTTPException(422, "Empresa aliada inválida")
    ahora = datetime.now(UTC)
    stmt = select(SupportTicket).where(*ticket_scope(p, mine))
    tablero = status == "tablero"
    if tablero:
        # vista Tablero: abiertos + resueltos de la ultima semana (la fecha se mira abajo, en Python)
        stmt = stmt.where(SupportTicket.status.in_((*slasvc.ABIERTOS, "resuelto")))
    elif status == "abiertos" or pendiente or filtro:
        stmt = stmt.where(SupportTicket.status.in_(slasvc.ABIERTOS))
        if status and status != "abiertos":
            # los contadores son siempre sobre abiertos, pero un estado elegido se respeta ("Esperando al cliente" +
            # "Vencidos" = los vencidos que esperan al cliente; un estado cerrado no trae nada)
            stmt = stmt.where(SupportTicket.status == status)
    elif status:
        stmt = stmt.where(SupportTicket.status == status)
    if kind:
        stmt = stmt.where(SupportTicket.kind == kind)
    if customer_id:
        stmt = stmt.where(SupportTicket.customer_id == customer_id)
    if priority:
        stmt = stmt.where(SupportTicket.priority == priority)
    if assigned == "me":
        stmt = stmt.where(SupportTicket.assigned_to == p.user.id)
    elif assigned == "none":
        stmt = stmt.where(SupportTicket.assigned_to.is_(None))
    elif assigned:
        if not assigned.isdigit():
            raise HTTPException(422, "Responsable inválido")
        stmt = stmt.where(SupportTicket.assigned_to == int(assigned))
    if ally:
        pids = [pid for pid, lst in _ally_map(db, p.tenant.id).items() if any(x["key"] == ally for x in lst)]
        if ally.startswith("a:"):
            # empresa aliada: los asignados a ella o de un proyecto en el que participa
            stmt = stmt.where(or_(SupportTicket.ally_company_id == int(ally[2:]), SupportTicket.project_id.in_(pids)))
        else:
            stmt = stmt.where(SupportTicket.project_id.in_(pids))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(SupportTicket.subject.ilike(like), SupportTicket.number.ilike(like)))
    stmt = stmt.order_by(SupportTicket.id.desc())
    if pendiente == "fuera_de_tiempo" or filtro or tablero or orden == "urgencia":
        # el vencimiento y el semaforo se calculan en Python (mismo criterio que el conteo): sin limite en SQL
        rows = list(db.scalars(stmt.limit(MAX_URGENCIA)))
        if pendiente == "fuera_de_tiempo":
            rows = [t for t in rows if slasvc.fuera_de_tiempo(t, ahora)]
        if filtro:
            rows = [t for t in rows if slasvc.cumple(t, filtro, ahora)]
        if tablero:
            semana = ahora - timedelta(days=7)
            rows = [t for t in rows if t.status != "resuelto" or (t.resolved_at and _aware(t.resolved_at) >= semana)]
        if orden == "urgencia":
            rows.sort(key=lambda t: _urgencia(t, ahora))
        rows = rows[:limit]
    else:
        rows = list(db.scalars(stmt.limit(limit)))
    ctx = _ctx(db, p, rows, ahora)
    return [_out(db, t, full=False, p=p, ctx=ctx) for t in rows]


@router.post("/tickets", status_code=201)
def ticket_create(data: TicketIn, p: Principal = Depends(require("support_desk", "crear")), db: Session = Depends(get_db)):
    check_refs(db, p.tenant.id, data.model_dump(), TICKET_REFS, TICKET_USERS)
    _check_project(db, p, data.project_id)
    from ..services.ally_scope import check_ally_assignment

    aliada = check_ally_assignment(db, p.tenant.id, ally_company_id=data.ally_company_id, user_ids=[data.assigned_to], project_id=data.project_id)
    number, _ = next_number(db, p.tenant.id, "TCK")
    t = SupportTicket(tenant_id=p.tenant.id, number=number, opened_by=p.user.id, ally_company_id=aliada, **data.model_dump(exclude={"ally_company_id"}))
    t.status = "asignado" if t.assigned_to else "nuevo"
    slasvc.aplicar(db, p.tenant, t, datetime.now(UTC))
    db.add(t)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "support_ticket", t.id, {"prioridad": t.priority}, ip=p.ip)
    _avisar_asignado(db, p, t, nuevo=True)
    db.commit()
    return _out(db, t, p=p)


@router.get("/tickets/{tid}")
def ticket_get(tid: int, p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    return _out(db, _own(db, tid, p), p=p)


@router.put("/tickets/{tid}")
def ticket_update(tid: int, data: TicketIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    t = _own(db, tid, p)
    _writable(t)
    check_refs(db, p.tenant.id, data.model_dump(), TICKET_REFS, TICKET_USERS, current=t)
    if data.project_id != t.project_id:
        _check_project(db, p, data.project_id)
    antes = t.assigned_to
    estado_antes = t.status
    clave_sla = (t.priority, t.customer_id)
    if "ally_company_id" in data.model_fields_set or data.assigned_to != t.assigned_to or data.project_id != t.project_id:
        # la empresa aliada solo cambia si viene (spec 7.5 y 14.18: un portal viejo, o el formulario que solo la manda si
        # cambio, no la borra). Si no viene se valida la que tenia contra el responsable nuevo, como en /assign: dejarlo
        # "Sin asignar (lo reparte la empresa aliada)" o pasarselo a alguien de Crimson la conserva; uno de otra
        # empresa aliada da 422; un ticket sin empresa toma la de su tecnico aliado
        from ..services.ally_scope import check_ally_assignment

        explicita = data.ally_company_id if "ally_company_id" in data.model_fields_set else t.ally_company_id
        # solo se exige activo a quien entra: cambiarle el proyecto a un ticket cuyo tecnico aliado ya estaba (y hoy esta
        # desactivado) no se traba
        t.ally_company_id = check_ally_assignment(
            db,
            p.tenant.id,
            ally_company_id=explicita,
            user_ids=[data.assigned_to],
            project_id=data.project_id,
            current=t.ally_company_id,
            existing={t.assigned_to},
        )
    campos = []
    for k, v in data.model_dump(exclude={"ally_company_id"}).items():
        if k in ("contact", "tags") and not v:
            continue  # el portal manda {} / []: no borrar los datos de quien reporto desde la web
        if k == "asset_id" and k not in data.model_fields_set:
            continue  # el formulario no lleva el equipo: editar (o el autoguardado al cerrar) no lo quita
        if getattr(t, k) != v and k != "assigned_to":
            campos.append(k)
        setattr(t, k, v)
    if (t.priority, t.customer_id) != clave_sla or not t.sla:
        # cambio la prioridad o el cliente: el plazo se recalcula desde que entro el ticket, no desde ahora
        slasvc.aplicar(db, p.tenant, t)
    if t.assigned_to and t.status == "nuevo":
        t.status = "asignado"
    if campos:
        audit(db, p.tenant.id, p.user.id, "update", "support_ticket", t.id, {"campos": campos}, ip=p.ip)
    if t.assigned_to != antes:
        audit(db, p.tenant.id, p.user.id, "assign", "support_ticket", t.id, {"de": antes, "a": t.assigned_to}, ip=p.ip)
    if t.status != estado_antes:
        audit(db, p.tenant.id, p.user.id, "status", "support_ticket", t.id, {"status": t.status}, ip=p.ip)
    if t.assigned_to and t.assigned_to != antes:
        _avisar_asignado(db, p, t)
    db.commit()
    return _out(db, t, p=p)


def _aplicar_paso(db: Session, p: Principal, t: SupportTicket, texto: str | None, cuando: datetime | None, quitar: bool = False) -> None:
    """Proximo paso del seguimiento (reglas de la bandeja). Cada cambio queda en la bitacora (historial del ticket)."""
    if quitar:
        texto, cuando = None, None
    texto = (texto or "").strip() or None
    if texto is None and cuando is not None:
        texto = "Seguimiento"
    # la fecha que el ticket ya tiene no se vuelve a validar: si el paso esta atrasado y solo se cambia el texto (el
    # compositor manda la fecha de siempre), la nota no se rechaza por una fecha que nadie toco
    misma_fecha = cuando is not None and t.next_step_at is not None and _aware(cuando).timestamp() == _aware(t.next_step_at).timestamp()
    if texto is not None or cuando is not None:
        if not slasvc.es_abierto(t):
            raise HTTPException(409, "El ticket está resuelto o cerrado: reabrilo para planificar un seguimiento")
        if cuando is not None and not misma_fecha and _aware(cuando) < datetime.now(UTC) - timedelta(minutes=10):
            raise HTTPException(422, "La fecha del próximo paso ya pasó")
    cuando = _aware(cuando).astimezone(UTC) if cuando else None  # en UTC: SQLite guarda la hora sin zona
    if texto == t.next_step and (cuando.timestamp() if cuando else None) == (_aware(t.next_step_at).timestamp() if t.next_step_at else None):
        return
    t.next_step, t.next_step_at = texto, cuando
    audit(
        db, p.tenant.id, p.user.id, "next_step", "support_ticket", t.id, {"next_step": texto, "next_step_at": cuando.isoformat() if cuando else None}, ip=p.ip
    )


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    internal: bool = False
    share_ally: bool = False  # nota interna compartida con la empresa aliada del ticket (el cliente no la ve)
    photos: list = Field(default_factory=list)
    hours: Decimal | None = Field(None, ge=0)
    status: str | None = Field(None, pattern="^(nuevo|asignado|en_proceso|esperando_cliente|resuelto|cerrado)$")
    # proximo paso en la misma transaccion; sin estos campos el actual se conserva
    next_step: str | None = Field(None, max_length=200)
    next_step_at: datetime | None = None
    clear_next_step: bool = False


@router.post("/tickets/{tid}/notes", status_code=201)
def ticket_note(tid: int, data: NoteIn, p: Principal = Depends(require_any("support_desk", "editar", "seguir")), db: Session = Depends(get_db)):
    """Cada respuesta queda con hora y autor: eso es lo que después se le enseña al cliente.
    Con support_desk.seguir (sin editar) se anota y se planifica el proximo paso, pero no se cambia el estado."""
    t = _own(db, tid, p)
    if data.status and not p.can("support_desk", "editar"):
        raise HTTPException(403, "Sin permiso para cambiar el estado")
    _writable(t)
    if data.clear_next_step or data.next_step or data.next_step_at:
        _aplicar_paso(db, p, t, data.next_step, data.next_step_at, data.clear_next_step)
    compartir = bool(data.share_ally and data.internal and t.ally_company_id)
    t.notes.append(
        SupportNote(
            tenant_id=t.tenant_id,
            user_id=p.user.id,
            body=data.body,
            internal=data.internal,
            photos=data.photos,
            share_ally=compartir,
            share_ally_company_id=t.ally_company_id if compartir else None,  # solo esa empresa la lee
        )
    )
    if not data.internal and not t.first_reply_at:
        t.first_reply_at = datetime.now(UTC)  # el reloj del SLA para la primera respuesta
    if data.hours is not None:
        t.hours = d(t.hours) + d(data.hours)
    if data.status and data.status != t.status:
        _mover(db, p, t, data.status)
    db.commit()
    return _out(db, t, p=p)


class NextStepIn(BaseModel):
    next_step: str | None = Field(None, max_length=200)
    next_step_at: datetime | None = None


@router.put("/tickets/{tid}/next-step")
def ticket_next_step(tid: int, data: NextStepIn, p: Principal = Depends(require_any("support_desk", "editar", "seguir")), db: Session = Depends(get_db)):
    """Proximo paso sin nota (ambos nulos = quitarlo)."""
    t = _own(db, tid, p)
    _writable(t)
    _aplicar_paso(db, p, t, data.next_step, data.next_step_at, quitar=not (data.next_step or data.next_step_at))
    db.commit()
    return _out(db, t, p=p)


class AssignIn(BaseModel):
    assigned_to: int | None = None


@router.post("/tickets/{tid}/assign")
def ticket_assign(tid: int, data: AssignIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    """Responsable del ticket ("Tomar" = asignarmelo). Gana el ultimo; el historial guarda cada cambio."""
    t = _own(db, tid, p)
    _writable(t)
    if data.assigned_to is not None:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == data.assigned_to, TenantUser.active))
        u = db.get(User, data.assigned_to) if m else None
        aliado = bool(m and m.ally_company_id)
        if not m or not u or not u.active or (not aliado and m.role_code not in roles_like(db, p.tenant, AGENT_ROLES, admin=True)):
            raise HTTPException(422, "Esa persona no atiende soporte")
        if aliado and data.assigned_to != t.assigned_to:
            # un usuario de una empresa aliada: el ticket queda asignado a su empresa (que participe en el proyecto)
            from ..services.ally_scope import check_ally_assignment

            t.ally_company_id = check_ally_assignment(
                db, p.tenant.id, ally_company_id=t.ally_company_id, user_ids=[data.assigned_to], project_id=t.project_id, current=t.ally_company_id
            )
    antes = t.assigned_to
    if antes != data.assigned_to:
        t.assigned_to = data.assigned_to
        estado = t.status
        if data.assigned_to and t.status == "nuevo":
            t.status = "asignado"
        elif not data.assigned_to and t.status == "asignado":
            t.status = "nuevo"
        audit(db, p.tenant.id, p.user.id, "assign", "support_ticket", t.id, {"de": antes, "a": data.assigned_to}, ip=p.ip)
        if t.status != estado:
            audit(db, p.tenant.id, p.user.id, "status", "support_ticket", t.id, {"status": t.status}, ip=p.ip)
        if data.assigned_to and data.assigned_to != p.user.id:
            _avisar_asignado(db, p, t)
    db.commit()
    return _out(db, t, p=p)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(nuevo|asignado|en_proceso|esperando_cliente|resuelto|cerrado)$")
    solution: str | None = None
    hours: Decimal | None = Field(None, ge=0)
    billable: bool | None = None
    amount: Decimal | None = Field(None, ge=0)


def _mover(db: Session, p: Principal, t: SupportTicket, status: str) -> None:
    """Cambio de estado. Volver a un estado abierto es Reabrir (se limpian resuelto y cerrado: el SLA vuelve a
    medirse contra lo prometido). Resolver o cerrar limpia el proximo paso (reabrir no lo restaura)."""
    ahora = datetime.now(UTC)
    t.status = status
    if status in slasvc.ABIERTOS:
        t.resolved_at = None
        t.closed_at = None
    if status == "resuelto" and not t.resolved_at:
        t.resolved_at = ahora
    if status == "cerrado":
        t.resolved_at = t.resolved_at or ahora
        t.closed_at = ahora
    if status in ("resuelto", "cerrado") and (t.next_step or t.next_step_at):
        t.next_step, t.next_step_at = None, None
        audit(db, p.tenant.id, p.user.id, "next_step", "support_ticket", t.id, {"next_step": None, "next_step_at": None}, ip=p.ip)
    audit(db, p.tenant.id, p.user.id, "status", "support_ticket", t.id, {"status": status}, ip=p.ip)


@router.patch("/tickets/{tid}")
def ticket_status(tid: int, data: StatusIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    t = _own(db, tid, p)
    _writable(t)
    if data.status in ("resuelto", "cerrado") and not (data.solution or t.solution):
        raise HTTPException(422, "Escribí qué se hizo antes de cerrar el ticket")
    if data.solution is not None:
        t.solution = data.solution
    if data.hours is not None:
        t.hours = data.hours
    if data.billable is not None:
        t.billable = data.billable
    if data.amount is not None and p.sees_prices:
        # el tecnico no ve el monto: el portal le mandaria 0 al resolver y lo borraria. El monto lo pone quien ve precios
        t.amount = data.amount
    _mover(db, p, t, data.status)
    db.commit()
    return _out(db, t, p=p)


def can_charge(p: Principal) -> bool:
    """Quien pone el monto de un ticket cobrable (13/10, casos borde): el tecnico marca horas y "se cobra", la cifra
    la pone quien ve precios y todo el soporte y lleva el soporte o el dinero: admin, supervisor y contador.
    Solo lectura ve el monto pero no lo escribe."""
    return p.sees_prices and p.can("support_desk", "ver_todo") and (p.can("support_desk", "editar") or p.can("accounting", "editar"))


class AmountIn(BaseModel):
    amount: Decimal = Field(ge=0)


@router.patch("/tickets/{tid}/amount")
def ticket_amount(tid: int, data: AmountIn, p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    """Monto de un ticket cobrable, en cualquier estado: el que resolvio el tecnico queda en 0 hasta que se cargue aqui."""
    if not can_charge(p):
        raise HTTPException(403, "El monto de un ticket lo pone administración o contabilidad")
    t = _own(db, tid, p)
    if not t.billable:
        raise HTTPException(409, "Este ticket no se cobra (garantía o contrato): no lleva monto")
    antes = str(t.amount) if t.amount is not None else None
    t.amount = data.amount
    audit(db, p.tenant.id, p.user.id, "amount", "support_ticket", t.id, {"antes": antes, "despues": str(data.amount)}, ip=p.ip)
    db.commit()
    return _out(db, t, p=p)


class VisitIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    technician_id: int | None = None
    scheduled_at: datetime | None = None
    site: str | None = Field(None, max_length=300)


@router.post("/tickets/{tid}/work-order", status_code=201)
def ticket_to_work_order(tid: int, data: VisitIn, p: Principal = Depends(require("field", "asignar")), db: Session = Depends(get_db)):
    """Hay que ir al sitio: el ticket se convierte en orden de trabajo y no se escribe nada de nuevo."""
    from .fieldwork import _eligible  # import local: el router de campo es pesado y solo se usa aqui

    t = _own(db, tid, p)
    _writable(t)
    if t.work_order_id:
        raise HTTPException(409, "Este ticket ya tiene una orden de trabajo")
    # el tecnico es del equipo de la empresa (mismo criterio que asignar en Ordenes de trabajo): un id ajeno, un
    # cliente del portal o alguien de ventas no recibe la orden ni su nombre sale en el informe de entrega
    if data.technician_id is not None and not _eligible(db, p.tenant.id, data.technician_id):
        raise HTTPException(422, "Esa persona no puede recibir órdenes")
    from ..services.ally_scope import check_ally_assignment, notify_ally_work

    # la OT hereda la empresa aliada del ticket (y un tecnico aliado tiene que ser de esa empresa). Con un tecnico de
    # Crimson la visita es de Crimson: no hereda (si no, un ticket de un aliado suspendido no podria generar la visita)
    interno = data.technician_id is not None and (
        db.scalar(
            select(TenantUser.id).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == data.technician_id, TenantUser.ally_company_id.is_not(None))
        )
        is None
    )
    aliada = (
        None if interno else check_ally_assignment(db, p.tenant.id, ally_company_id=t.ally_company_id, user_ids=[data.technician_id], project_id=t.project_id)
    )
    number, _ = next_number(db, p.tenant.id, "OT")
    o = WorkOrder(
        tenant_id=p.tenant.id,
        number=number,
        ally_company_id=aliada,
        title=data.title or f"{t.number} · {t.subject}",
        kind="soporte" if t.kind in ("soporte", "garantia", "consulta") else "mantenimiento",
        customer_id=t.customer_id,
        project_id=t.project_id,
        site=data.site,
        scheduled_at=data.scheduled_at,
        technician_id=data.technician_id,
        notes=t.description,
    )
    from ..services import avisos_venta as avisos
    from .fieldwork import POR_PROGRAMAR

    if o.technician_id is None and o.ally_company_id is None:
        o.status = POR_PROGRAMAR  # nadie la va a hacer todavia: queda destacada hasta que alguien la programe
    db.add(o)
    db.flush()
    t.work_order_id = o.id
    if t.status == "nuevo":
        t.status = "asignado"
    audit(db, p.tenant.id, p.user.id, "work_order", "support_ticket", t.id, {"work_order": o.number}, ip=p.ip)
    # como al crear la orden en Ordenes de trabajo: el tecnico aliado recibe su aviso (notify_ally_work ignora a los
    # internos) y el de Crimson el suyo (ot_asignada ignora a los aliados)
    notify_ally_work(db, p.tenant, {o.technician_id}, o.number, o.title, o.scheduled_at, entity_id=o.id)
    avisos.ot_asignada(db, p.tenant, o, [o.technician_id], p.user.id)
    db.commit()
    return {"work_order_id": o.id, "number": o.number, "status": o.status}


@router.get("/tickets/meta/config")
def ticket_meta(p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    agentes = [
        {"id": m.user.id, "name": m.user.full_name, "role": m.role_code}
        for m in db.scalars(
            select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.active, TenantUser.role_code.in_(roles_like(db, p.tenant, AGENT_ROLES, admin=True)))
        )
    ]
    ahora = datetime.now(UTC)
    # mismo alcance que la lista y la misma funcion que cada filtro (services/sla.py): el contador = las tarjetas
    abiertos = db.scalars(select(SupportTicket).where(*ticket_scope(p), SupportTicket.status.in_(slasvc.ABIERTOS))).all()
    res = slasvc.resumen(abiertos, ahora)
    resumen = {"abiertos": res["abiertos"], **{f: sum(1 for t in abiertos if slasvc.cumple(t, f, ahora)) for f in slasvc.FILTROS}}
    resumen["asignados_a_mi"] = sum(1 for t in abiertos if t.assigned_to == p.user.id)
    allies: list[dict] = []
    if p.can("support_desk", "ver_todo"):
        # aliados con tickets (de cualquier estado) para el selector; "abiertos" cuenta solo los abiertos. Un ticket
        # cuenta una vez por empresa aunque este asignado a ella y ademas sea de un proyecto donde participa
        filas = db.execute(
            select(SupportTicket.project_id, SupportTicket.ally_company_id, SupportTicket.status).where(
                *ticket_scope(p), or_(SupportTicket.project_id.is_not(None), SupportTicket.ally_company_id.is_not(None))
            )
        ).all()
        amap = _ally_map(db, p.tenant.id, {pid for pid, _, _ in filas if pid})
        nombres = _ally_names(db, p.tenant.id, {cid for _, cid, _ in filas if cid})
        cuenta: dict[str, dict] = {}
        for pid, cid, st in filas:
            llaves = {x["key"]: x["name"] for x in amap.get(pid, [])} if pid else {}
            if cid and nombres.get(cid):
                llaves[f"a:{cid}"] = nombres[cid]
            for key, name in llaves.items():
                row = cuenta.setdefault(key, {"key": key, "name": name, "abiertos": 0})
                row["abiertos"] += 1 if st in slasvc.ABIERTOS else 0
        allies = sorted(cuenta.values(), key=lambda r: _norm(r["name"]))
    return {
        "kinds": ["soporte", "garantia", "mantenimiento", "visita", "instalacion", "consulta"],
        "states": ["nuevo", "asignado", "en_proceso", "esperando_cliente", "resuelto", "cerrado"],
        "priorities": ["baja", "media", "alta", "critica"],
        "channels": ["whatsapp", "correo", "llamada", "presencial", "portal", "web"],
        "sla_horas": {k: v["respuesta"] for k, v in slasvc.sla_tenant(p.tenant).items()},
        "sla": slasvc.sla_tenant(p.tenant),
        "agents": agentes,
        "abiertos": res["abiertos"],
        "fuera_de_tiempo": res["fuera_de_tiempo"],
        "resumen": resumen,
        "allies": allies,
        "umbral_ambar": slasvc.UMBRAL_AMBAR,
        "sin_movimiento_h": slasvc.SIN_MOVIMIENTO_H,
        "can_see_all": p.can("support_desk", "ver_todo"),
        "can_charge": can_charge(p),  # pone el monto de un ticket cobrable (PATCH /tickets/{id}/amount)
    }


@router.get("/tickets/meta/agents")
def ticket_agents(ally_company_id: int | None = None, p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    """Responsables posibles: el equipo interno que atiende soporte y, con ally_company_id, los usuarios activos de esa
    empresa aliada."""
    out = [
        {"id": m.user.id, "name": m.user.full_name, "role": m.role_code, "company": None}
        for m in db.scalars(
            select(TenantUser).where(
                TenantUser.tenant_id == p.tenant.id,
                TenantUser.active,
                TenantUser.role_code.in_(roles_like(db, p.tenant, AGENT_ROLES, admin=True)),
                TenantUser.ally_company_id.is_(None),
            )
        )
        if m.user.active
    ]
    if ally_company_id:
        c = db.get(AllyCompany, ally_company_id)
        if not c or c.tenant_id != p.tenant.id:
            raise HTTPException(404, "Empresa aliada no encontrada")
        for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.active, TenantUser.ally_company_id == c.id)):
            if m.user.active:
                out.append({"id": m.user.id, "name": m.user.full_name, "role": m.role_code, "company": c.name})
    return out


AVISO_ASIGNADO_MIN = 30  # no se repite el correo de asignacion al mismo responsable del mismo ticket antes de esto


def _ya_avisado(db: Session, t: SupportTicket, correo: str) -> bool:
    """Ya se le aviso a esa persona de este ticket hace poco: alternar el responsable no encola correos sin tope."""
    desde = datetime.now(UTC) - timedelta(minutes=AVISO_ASIGNADO_MIN)
    return (
        db.scalar(
            select(EmailOutbox.id)
            .where(
                EmailOutbox.tenant_id == t.tenant_id,
                EmailOutbox.entity == "support_ticket",
                EmailOutbox.entity_id == t.id,
                EmailOutbox.to == correo,
                EmailOutbox.created_at >= desde,
            )
            .limit(1)
        )
        is not None
    )


def _avisar_asignado(db: Session, p: Principal, t: SupportTicket, nuevo: bool = False) -> None:
    u = db.get(User, t.assigned_to) if t.assigned_to else None
    cuerpo = (
        f"<p><b>{html.escape(t.subject)}</b> · prioridad {t.priority}</p>"
        f"<p>Cliente: {html.escape((db.get(Customer, t.customer_id).name if t.customer_id else '') or 'sin ficha')}</p>"
        f"{f'<p>{html.escape(t.description)}</p>' if t.description else ''}"
        f"<p>Primera respuesta antes de {(t.sla or {}).get('respuesta', SLA_HORAS.get(t.priority, 8))} h"
        f" · resolucion antes de {(t.sla or {}).get('resolucion', '?')} h.</p>"
    )
    correo = user_inbox(db, p.tenant.id, u) if u else ""  # su correo de avisos, si tiene (pedido 22)
    if correo:
        if not nuevo and _ya_avisado(db, t, correo):
            return  # el responsable va y vuelve (Tomar, selector, autoguardado del PUT): un aviso basta
        queue_email(db, p.tenant, correo, f"Ticket {t.number} · {t.subject[:60]}", cuerpo, "support_ticket", t.id)
    elif nuevo:
        notify_roles(db, p.tenant, ("admin", "supervisor"), f"Ticket {t.number} sin asignar · {t.subject[:50]}", cuerpo, "support_ticket", t.id)


# ---------- Contratos de mantenimiento ----------
class ContractIn(BaseModel):
    customer_id: int
    project_id: int | None = None
    name: str = Field(min_length=2, max_length=200)
    kind: str = Field("mantenimiento", pattern="^(mantenimiento|soporte|renting|licencia)$")
    every_months: int = Field(6, ge=1, le=60)
    next_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    amount: Decimal = Field(Decimal(0), ge=0)
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    scope: str | None = None
    active: bool = True
    notes: str | None = None
    # SLA propio: {"critica": {"respuesta": 2, "resolucion": 8}, ...}. Vacio/None = el de la empresa
    sla: dict | None = None
    # lo ejecuta una empresa aliada (opcional); PUT solo la cambia si viene
    ally_company_id: int | None = None

    @field_validator("sla")
    @classmethod
    def _sla(cls, v):
        if v in (None, {}):
            return None
        n = slasvc.normalizar(v)
        if not n:
            raise ValueError("SLA invalido: horas de respuesta y resolucion entre 1 y 2160 por prioridad")
        return n


def _contract_out(db: Session, c: MaintenanceContract, p: Principal | None = None) -> dict:
    """Contrato. El tecnico lo ve (fechas, alcance) sin el monto ni la divisa: es precio, no lo ve (D5)."""
    precio = p is None or p.sees_prices
    cl = db.get(Customer, c.customer_id)
    pr = db.get(Project, c.project_id) if c.project_id else None
    dias = (c.next_date - date.today()).days if c.next_date else None
    return {
        "id": c.id,
        "number": c.number,
        "name": c.name,
        "kind": c.kind,
        "customer_id": c.customer_id,
        "customer": cl.name if cl else None,
        "project": pr.number if pr else None,
        "every_months": c.every_months,
        "next_date": c.next_date,
        "last_done": c.last_done,
        "start_date": c.start_date,
        "end_date": c.end_date,
        "amount": c.amount if precio else None,
        "currency": c.currency if precio else None,
        "scope": c.scope,
        "active": c.active,
        "notes": c.notes,
        "sla": c.sla,
        "dias_para_la_proxima": dias,
        "ally_company_id": c.ally_company_id,
        "ally_company": _ally_names(db, c.tenant_id, {c.ally_company_id}).get(c.ally_company_id) if c.ally_company_id else None,
    }


CONTRACT_REFS = {"customer_id": (Customer, "Cliente no encontrado"), "project_id": (Project, "Proyecto no encontrado")}


def _contract_admin(p: Principal) -> None:
    """Crear, editar o programar un contrato pide ver toda la mesa (mismo criterio que el Archivo: un tecnico atiende
    sus tickets pero no maneja contratos). Su formulario lleva el monto, que el tecnico no ve."""
    if not p.can("support_desk", "ver_todo"):
        raise HTTPException(403, "Los contratos los maneja administracion o el supervisor")


@router.get("/contracts")
def contracts(active: bool | None = None, p: Principal = Depends(require("support_desk", "ver")), db: Session = Depends(get_db)):
    stmt = select(MaintenanceContract).where(MaintenanceContract.tenant_id == p.tenant.id, live(MaintenanceContract))
    if active is not None:
        stmt = stmt.where(MaintenanceContract.active.is_(active))
    return [_contract_out(db, c, p) for c in db.scalars(stmt.order_by(MaintenanceContract.next_date.is_(None), MaintenanceContract.next_date))]


@router.post("/contracts", status_code=201)
def contract_create(data: ContractIn, p: Principal = Depends(require("support_desk", "crear")), db: Session = Depends(get_db)):
    _contract_admin(p)
    check_refs(db, p.tenant.id, data.model_dump(), CONTRACT_REFS)
    from ..services.ally_scope import check_ally_assignment

    aliada = (
        check_ally_assignment(db, p.tenant.id, ally_company_id=data.ally_company_id, user_ids=[], project_id=data.project_id) if data.ally_company_id else None
    )
    number, _ = next_number(db, p.tenant.id, "CTR")
    c = MaintenanceContract(tenant_id=p.tenant.id, number=number, ally_company_id=aliada, **data.model_dump(exclude={"ally_company_id"}))
    c.next_date = c.next_date or c.start_date or date.today()
    db.add(c)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "maintenance_contract", c.id, ip=p.ip)
    db.commit()
    return _contract_out(db, c, p)


@router.put("/contracts/{cid}")
def contract_update(cid: int, data: ContractIn, p: Principal = Depends(require("support_desk", "editar")), db: Session = Depends(get_db)):
    _contract_admin(p)
    c = db.get(MaintenanceContract, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Contrato no encontrado")
    check_refs(db, p.tenant.id, data.model_dump(), CONTRACT_REFS, current=c)
    if "ally_company_id" in data.model_fields_set:
        from ..services.ally_scope import check_ally_assignment

        c.ally_company_id = (
            check_ally_assignment(db, p.tenant.id, ally_company_id=data.ally_company_id, user_ids=[], project_id=data.project_id, current=c.ally_company_id)
            if data.ally_company_id
            else None
        )
    for k, v in data.model_dump(exclude={"ally_company_id"}).items():
        setattr(c, k, v)
    db.commit()
    return _contract_out(db, c, p)


def due_contracts(db: Session, tenant_id: int, dias: int = 0) -> list[MaintenanceContract]:
    """Contratos a los que ya les toca (o les toca dentro de N dias)."""
    limite = date.today() + timedelta(days=dias)
    return [
        c
        for c in db.scalars(select(MaintenanceContract).where(MaintenanceContract.tenant_id == tenant_id, MaintenanceContract.active))
        if c.next_date and c.next_date <= limite and (not c.end_date or c.end_date >= date.today())
    ]


def open_maintenance(db: Session, c: MaintenanceContract) -> SupportTicket:
    """Abre el ticket del mantenimiento y programa el siguiente. Lo usa el worker y el boton "hacer ahora"."""
    number, _ = next_number(db, c.tenant_id, "TCK")
    t = SupportTicket(
        tenant_id=c.tenant_id,
        number=number,
        customer_id=c.customer_id,
        project_id=c.project_id,
        contract_id=c.id,
        ally_company_id=c.ally_company_id,  # lo ejecuta la empresa aliada del contrato
        kind="mantenimiento",
        subject=f"Mantenimiento preventivo · {c.name}",
        description=c.scope,
        priority="media",
        status="nuevo",
        billable=d(c.amount) > 0,
        amount=c.amount,
        # preventivo programado: no es una emergencia, se agenda dentro de la semana (no usa el SLA de incidencias)
        due_at=datetime.now(UTC) + timedelta(days=7),
        sla={"origen": "preventivo", "respuesta": 24 * 7, "resolucion": None, "contrato": c.number, "contrato_id": c.id},
    )
    db.add(t)
    base = c.next_date or date.today()
    c.last_done = base
    mes = base.month - 1 + c.every_months
    c.next_date = date(base.year + mes // 12, mes % 12 + 1, min(base.day, 28))
    return t


@router.post("/contracts/{cid}/ticket", status_code=201)
def contract_ticket(cid: int, p: Principal = Depends(require("support_desk", "crear")), db: Session = Depends(get_db)):
    _contract_admin(p)
    c = db.get(MaintenanceContract, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Contrato no encontrado")
    t = open_maintenance(db, c)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "maintenance", "support_ticket", t.id, {"contrato": c.number}, ip=p.ip)
    db.commit()
    return _out(db, t, p=p)


# ---------- Comisiones ----------
# Se mudaron a routers/commissions.py (motor en services/commissions.py). Alias por si otro codigo lo importa.
from ..services.commissions import on_payment as accrue_for_payment  # noqa: E402,F401
