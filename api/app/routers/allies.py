"""Aliados de la oportunidad: participacion, contactos por funcion y solicitudes de costo.

Todo pasa por la oportunidad (mismos permisos y alcance que el embudo: el vendedor solo toca las suyas) y cada
cambio deja una linea en su bitacora. Rutas bajo /opportunity-allies para no chocar con /opportunities/{oid}.
Nada de esto se expone en /cliente/*: el portal del cliente no importa este router ni sus tablas.

Empresa aliada (12:45-13:23): el buscador trae por defecto SOLO empresas aliadas (todos=true: tambien clientes y
proveedores, como antes) y se puede dar de alta un aliado ahi mismo. Sus contactos por funcion viven en la empresa
aliada y se precargan en la participacion. Las participaciones viejas (cliente/proveedor) no dan acceso al portal de
aliados hasta "Convertir en aliado". Al aliado le llegan avisos sin montos."""

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import AllyCompany, AllyCostRequest, AllyParticipation, Customer, Media, Opportunity, Supplier, TenantUser, User
from ..models.allies import ALLY_ROLES, CONTACT_FUNCTIONS, COST_STATES
from ..services import allies as svc
from ..services.documents import audit
from .media import public_url
from .pipeline import _check_owner, _log, _own

router = APIRouter(tags=["aliados"])

ROLE_LABEL = {
    "contratante": "contratante",
    "referido": "referido",
    "subcontratista": "subcontratista",
    "suministro": "suministro",
    "configuracion": "configuración",
    "software": "software",
}
STATE_LABEL = {"solicitado": "solicitado", "recibido": "recibido", "aprobado": "aprobado", "rechazado": "rechazado"}


class ContactIn(BaseModel):
    name: str | None = Field(None, max_length=160)
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)


class AllyIn(BaseModel):
    ally_company_id: int | None = None
    customer_id: int | None = None
    supplier_id: int | None = None
    visible_to_ally: bool = True  # el aliado la ve en su portal (solo con empresa aliada)
    role: str = Field("subcontratista", pattern="^(" + "|".join(ALLY_ROLES) + ")$")
    scope: str | None = Field(None, max_length=4000)
    scope_lines: list[str] = Field(default_factory=list, max_length=60)
    contacts: dict[str, ContactIn] = Field(default_factory=dict)

    @field_validator("contacts")
    @classmethod
    def _funciones(cls, v: dict) -> dict:
        malas = [k for k in v if k not in CONTACT_FUNCTIONS]
        if malas:
            raise ValueError(f"Función de contacto desconocida: {', '.join(malas)}")
        return v


class CostRequestIn(BaseModel):
    what: str = Field(min_length=2, max_length=300)
    responsible_id: int | None = None
    due_date: date | None = None
    status: str = Field("solicitado", pattern="^(" + "|".join(COST_STATES) + ")$")
    # campos de costo: solo los escribe quien ve costos (catalog.costos)
    amount: Decimal | None = Field(None, ge=0)
    currency: str = Field("USD", pattern="^[A-Z]{3}$")
    valid_until: date | None = None
    exclusions: str | None = Field(None, max_length=4000)
    attachment_ids: list[int] | None = Field(None, max_length=10)


def _company(db: Session, p: Principal, data: AllyIn) -> tuple[int | None, int | None, int | None, str]:
    """(ally_company_id, customer_id, supplier_id, nombre): exactamente una de las tres."""
    if sum(1 for x in (data.ally_company_id, data.customer_id, data.supplier_id) if x) != 1:
        raise HTTPException(422, "Elegí la empresa aliada")
    if data.ally_company_id:
        a = db.get(AllyCompany, data.ally_company_id)
        if not a or a.tenant_id != p.tenant.id:
            raise HTTPException(404, "Empresa aliada no encontrada")
        if not a.active:
            raise HTTPException(422, "Esa empresa aliada está inactiva")
        return a.id, None, None, a.name
    if data.customer_id:
        c = db.get(Customer, data.customer_id)
        if not c or c.tenant_id != p.tenant.id:
            raise HTTPException(404, "Cliente no encontrado")
        return None, c.id, None, c.name
    s = db.get(Supplier, data.supplier_id)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Proveedor no encontrado")
    return None, None, s.id, s.name


def _ally(db: Session, aid: int, p: Principal) -> tuple[AllyParticipation, Opportunity]:
    a = db.get(AllyParticipation, aid)
    if not a or a.tenant_id != p.tenant.id:
        raise HTTPException(404, "Aliado no encontrado")
    return a, _own(db, a.opportunity_id, p)  # mismo alcance que la oportunidad


def _request(db: Session, rid: int, p: Principal) -> tuple[AllyCostRequest, Opportunity]:
    r = db.get(AllyCostRequest, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Solicitud no encontrada")
    return r, _own(db, r.opportunity_id, p)


def _list(db: Session, o: Opportunity, p: Principal) -> list[dict]:
    return [svc.ally_out(db, a, p) for a in svc.allies_of(db, p.tenant.id, opportunity_id=o.id)]


def _contacts(data: AllyIn) -> dict:
    return {k: v.model_dump() for k, v in data.contacts.items() if any((v.name, v.email, v.phone))}


def _dup_where(aid: int | None, cid: int | None, sid: int | None):
    if aid:
        return AllyParticipation.ally_company_id == aid
    return AllyParticipation.customer_id == cid if cid else AllyParticipation.supplier_id == sid


def _ally_hit(a: AllyCompany) -> dict:
    out = {"kind": "ally", "id": a.id, "name": a.name, "hint": "Aliado", "tax_id": a.tax_id}
    if a.suspended_at:
        out["suspended"] = True
    return out


@router.get("/opportunity-allies/companies")
def companies(q: str = "", todos: bool = False, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """Buscador de empresas para ligar un aliado. Por defecto SOLO empresas aliadas activas (12:45 "lo ocupo que salgan
    solo los aliados"), con nombre y cedula. todos=true: ademas clientes y proveedores (solo nombre), como antes."""
    like = f"%{q.strip()}%"
    allies = db.scalars(
        select(AllyCompany)
        .where(AllyCompany.tenant_id == p.tenant.id, AllyCompany.active.is_(True), or_(AllyCompany.name.ilike(like), AllyCompany.tax_id.ilike(like)))
        .order_by(AllyCompany.name)
        .limit(10)
    )
    hits = [_ally_hit(a) for a in allies]
    if not todos:
        return hits
    custs = db.scalars(
        select(Customer).where(Customer.tenant_id == p.tenant.id, Customer.name.ilike(like), Customer.trashed_at.is_(None)).order_by(Customer.name).limit(10)
    )
    sups = db.scalars(
        select(Supplier)
        .where(Supplier.tenant_id == p.tenant.id, Supplier.name.ilike(like), Supplier.archived_at.is_(None), Supplier.trashed_at.is_(None))
        .order_by(Supplier.name)
        .limit(10)
    )
    return (
        hits
        + [{"kind": "customer", "id": c.id, "name": c.name, "hint": "Cliente"} for c in custs]
        + [{"kind": "supplier", "id": s.id, "name": s.name, "hint": "Proveedor"} for s in sups]
    )


class QuickAllyIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    tax_id: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    contacts: dict[str, ContactIn] = Field(default_factory=dict)

    @field_validator("contacts")
    @classmethod
    def _funciones(cls, v: dict) -> dict:
        malas = [k for k in v if k not in CONTACT_FUNCTIONS]
        if malas:
            raise ValueError(f"Función de contacto desconocida: {', '.join(malas)}")
        return v


@router.post("/opportunity-allies/companies", status_code=201)
def company_quick_create(data: QuickAllyIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Alta rapida de un aliado desde la oportunidad (13:10 "si es un aliado nuevo, poder agregarlo ahi"). Sin usuarios
    ni mensualidad: eso es de Administracion -> Aliados."""
    from .ally_admin import clean_tax, contacts_clean, duplicate_response, find_duplicate

    name = " ".join(data.name.split())
    tax = clean_tax(data.tax_id)
    dup = find_duplicate(db, p.tenant.id, name, tax)
    if dup:
        return duplicate_response(dup, kind="ally")
    a = AllyCompany(
        tenant_id=p.tenant.id,
        name=name,
        tax_id=tax,
        email=str(data.email).lower() if data.email else None,
        phone=data.phone,
        contacts=contacts_clean(data.contacts),
        active=True,
        created_by=p.user.id,
    )
    db.add(a)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "ally_company", a.id, {"name": a.name, "desde": "oportunidad"}, ip=p.ip)
    db.commit()
    return _ally_hit(a)


@router.get("/opportunity-allies/pending")
def pending(p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """Solicitudes de costo pendientes: la misma definicion que la tarjeta del inicio (svc.cost_pending_where)."""
    rows = db.scalars(
        select(AllyCostRequest).where(*svc.cost_pending_where(p)).order_by(AllyCostRequest.due_date.is_(None), AllyCostRequest.due_date, AllyCostRequest.id)
    ).all()
    out = []
    for r in rows:
        o = db.get(Opportunity, r.opportunity_id)
        item = svc.request_out(db, r, p)
        item.update(ally=r.participation.name, opportunity_number=o.number, opportunity_title=o.title)
        out.append(item)
    return out


@router.get("/opportunities/{oid}/allies")
def list_allies(oid: int, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    return _list(db, _own(db, oid, p), p)


@router.post("/opportunities/{oid}/allies", status_code=201)
def add_ally(oid: int, data: AllyIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _own(db, oid, p)
    aid, cid, sid, name = _company(db, p, data)
    dup = db.scalar(select(AllyParticipation).where(AllyParticipation.opportunity_id == o.id, AllyParticipation.role == data.role, _dup_where(aid, cid, sid)))
    if dup:
        raise HTTPException(409, f"{name} ya participa como {ROLE_LABEL[data.role]}")
    contactos = _contacts(data)
    if aid and not contactos:
        contactos = dict(db.get(AllyCompany, aid).contacts or {})  # los detalles viven "por detras del aliado" (13:10)
    a = AllyParticipation(
        tenant_id=p.tenant.id,
        opportunity_id=o.id,
        project_id=o.project_id,  # si la oportunidad ya tiene proyecto, el aliado lo comparte
        ally_company_id=aid,
        customer_id=cid,
        supplier_id=sid,
        visible_to_ally=data.visible_to_ally if aid else True,
        name=name,
        role=data.role,
        scope=data.scope,
        scope_lines=[x.strip()[:200] for x in data.scope_lines if x.strip()],
        contacts=contactos,
        created_by=p.user.id,
    )
    db.add(a)
    db.flush()
    _log(o, p.user.full_name, f"Aliado {name} agregado como {ROLE_LABEL[data.role]}" + (f" · alcance: {data.scope[:120]}" if data.scope else ""))
    audit(db, p.tenant.id, p.user.id, "ally_add", "opportunity", o.id, {"ally_id": a.id, "name": name, "role": data.role}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


@router.put("/opportunity-allies/{aid}")
def update_ally(aid: int, data: AllyIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    a, o = _ally(db, aid, p)
    comp_id, cid, sid, name = _company(db, p, data)
    cambios = []
    if (comp_id, cid, sid) != (a.ally_company_id, a.customer_id, a.supplier_id):
        cambios.append(f"empresa {a.name} → {name}")
    if data.role != a.role:
        cambios.append(f"papel {ROLE_LABEL.get(a.role, a.role)} → {ROLE_LABEL[data.role]}")
    if (data.scope or None) != (a.scope or None) or [x.strip() for x in data.scope_lines if x.strip()] != (a.scope_lines or []):
        cambios.append("alcance")
    if _contacts(data) != (a.contacts or {}):
        cambios.append("contactos")
    a.ally_company_id, a.customer_id, a.supplier_id, a.name, a.role = comp_id, cid, sid, name, data.role
    if "visible_to_ally" in data.model_fields_set and data.visible_to_ally != a.visible_to_ally:
        a.visible_to_ally = data.visible_to_ally  # apagarla le quita el acceso al aliado al instante
        cambios.append("visible en el portal del aliado" if data.visible_to_ally else "oculta en el portal del aliado")
    a.scope = data.scope
    a.scope_lines = [x.strip()[:200] for x in data.scope_lines if x.strip()]
    a.contacts = _contacts(data)
    if cambios:
        _log(o, p.user.full_name, f"Aliado {name} actualizado: {', '.join(cambios)}")
        audit(db, p.tenant.id, p.user.id, "ally_update", "opportunity", o.id, {"ally_id": a.id, "cambios": cambios}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


class ToAllyIn(BaseModel):
    ally_company_id: int | None = None


@router.post("/opportunity-allies/{aid}/to-ally")
def convert_to_ally(aid: int, data: ToAllyIn | None = None, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Participacion vieja (un cliente o un proveedor) -> empresa aliada. Con id usa esa empresa; sin id busca la
    empresa aliada ligada a ese cliente/proveedor o con su cedula o nombre, y si no existe la crea copiando sus datos."""
    from .ally_admin import clean_tax, find_duplicate

    a, o = _ally(db, aid, p)
    if a.ally_company_id:
        raise HTTPException(409, "Esta participación ya es de una empresa aliada")
    comp = None
    if data and data.ally_company_id:
        comp = db.get(AllyCompany, data.ally_company_id)
        if not comp or comp.tenant_id != p.tenant.id:
            raise HTTPException(404, "Empresa aliada no encontrada")
    else:
        src = db.get(Customer, a.customer_id) if a.customer_id else db.get(Supplier, a.supplier_id) if a.supplier_id else None
        tax = clean_tax(src.id_number if isinstance(src, Customer) else getattr(src, "tax_id", None)) if src else None
        link = AllyCompany.customer_id == a.customer_id if a.customer_id else AllyCompany.supplier_id == a.supplier_id
        comp = db.scalar(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, link).limit(1)) if src else None
        comp = comp or find_duplicate(db, p.tenant.id, src.name if src else a.name, tax)
        if comp is None:
            comp = AllyCompany(
                tenant_id=p.tenant.id,
                name=(src.name if src else a.name)[:160],
                tax_id=tax,
                email=(getattr(src, "email", None) or "").lower() or None,
                phone=getattr(src, "phone", None),
                contacts=dict(a.contacts or {}),
                customer_id=a.customer_id,
                supplier_id=a.supplier_id,
                active=True,
                created_by=p.user.id,
            )
            db.add(comp)
            db.flush()
            audit(db, p.tenant.id, p.user.id, "create", "ally_company", comp.id, {"name": comp.name, "desde": "participación"}, ip=p.ip)
        else:
            # la empresa aliada recuerda de que cliente/proveedor salio (para facturarle o pagarle)
            if a.customer_id and not comp.customer_id:
                comp.customer_id = a.customer_id
            if a.supplier_id and not comp.supplier_id:
                comp.supplier_id = a.supplier_id
    if not comp.active:
        raise HTTPException(422, "Esa empresa aliada está inactiva")
    antes = "cliente" if a.customer_id else "proveedor"
    a.ally_company_id, a.customer_id, a.supplier_id, a.name = comp.id, None, None, comp.name
    _log(o, p.user.full_name, f"Aliado {comp.name} convertido de {antes} a empresa aliada")
    audit(db, p.tenant.id, p.user.id, "ally_convert", "opportunity", o.id, {"ally_id": a.id, "ally_company_id": comp.id}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


@router.delete("/opportunity-allies/{aid}")
def remove_ally(aid: int, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    a, o = _ally(db, aid, p)
    n = len(a.requests)
    _log(o, p.user.full_name, f"Aliado {a.name} quitado" + (f" (con {n} solicitud(es) de costo)" if n else ""))
    audit(db, p.tenant.id, p.user.id, "ally_remove", "opportunity", o.id, {"ally_id": a.id, "name": a.name, "requests": n}, ip=p.ip)
    db.delete(a)
    db.commit()
    return _list(db, o, p)


def _attachments(db: Session, p: Principal, ids: list[int]) -> list[dict]:
    out = []
    for mid in dict.fromkeys(ids):
        m = db.get(Media, mid)
        if not m or m.tenant_id != p.tenant.id:
            raise HTTPException(404, "Adjunto no encontrado")
        out.append({"id": m.id, "url": public_url(m), "filename": m.filename})
    return out


def _apply_costs(db: Session, p: Principal, r: AllyCostRequest, data: CostRequestIn) -> bool:
    """Monto, vigencia, exclusiones y adjuntos: solo con catalog.costos. Para el resto quedan como estaban."""
    if not p.sees_costs:
        if data.amount is not None or data.valid_until or data.exclusions or data.attachment_ids:
            raise HTTPException(403, "Solo quien ve costos registra el monto, la vigencia, las exclusiones o los adjuntos")
        return False
    antes = (r.amount, r.currency, r.valid_until, r.exclusions, r.attachments)
    r.amount = data.amount
    r.currency = data.currency
    r.valid_until = data.valid_until
    r.exclusions = data.exclusions
    if data.attachment_ids is not None:
        r.attachments = _attachments(db, p, data.attachment_ids)
    return antes != (r.amount, r.currency, r.valid_until, r.exclusions, r.attachments)


def _check_status(p: Principal, nuevo: str) -> None:
    # aprobar o rechazar un costo es de quien lo puede ver
    if nuevo in ("aprobado", "rechazado") and not p.sees_costs:
        raise HTTPException(403, "Aprobar o rechazar un costo es de quien ve costos")


def _fecha(v: date | None) -> str:
    return v.strftime("%d/%m/%Y") if v else "sin fecha"


@router.post("/opportunity-allies/{aid}/requests", status_code=201)
def add_request(aid: int, data: CostRequestIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    a, o = _ally(db, aid, p)
    _check_status(p, data.status)
    who = _check_owner(db, p, data.responsible_id) if data.responsible_id else None
    r = AllyCostRequest(
        tenant_id=p.tenant.id,
        participation_id=a.id,
        opportunity_id=o.id,
        what=data.what.strip(),
        responsible_id=data.responsible_id,
        due_date=data.due_date,
        status=data.status,
        created_by=p.user.id,
    )
    _apply_costs(db, p, r, data)
    a.requests.append(r)
    db.flush()
    _log(o, p.user.full_name, f"Solicitud de costo a {a.name}: {r.what} · responsable {who.full_name if who else 'sin asignar'} · para {_fecha(r.due_date)}")
    audit(db, p.tenant.id, p.user.id, "cost_request", "opportunity", o.id, {"request_id": r.id, "ally": a.name}, ip=p.ip)
    if a.ally_company_id and a.visible_to_ally:
        _notify_ally_request(db, p, a, o, r)
    db.commit()
    return _list(db, o, p)


def _notify_ally_request(db: Session, p: Principal, a: AllyParticipation, o: Opportunity, r: AllyCostRequest) -> int:
    """Aviso a los usuarios "aliado" activos de esa empresa: que se pide, de que oportunidad y para cuando. Sin montos."""
    from ..services.mail import queue_email

    rows = db.scalars(
        select(User)
        .join(TenantUser, TenantUser.user_id == User.id)
        .where(
            TenantUser.tenant_id == p.tenant.id,
            TenantUser.ally_company_id == a.ally_company_id,
            TenantUser.role_code == "aliado",
            TenantUser.active,
            User.active,
        )
    ).all()
    e = html.escape
    base = cfg.public_base_url.rstrip("/")
    asunto = f"Crimson le pide precios: {r.what[:60]} · {o.title[:60]}"
    cuerpo = (
        f"<p>{e(p.tenant.name)} le pide precios para <b>{e(o.title)}</b>:</p><blockquote>{e(r.what)}</blockquote>"
        + (f"<p>Antes del {_fecha(r.due_date)}.</p>" if r.due_date else "")
        + f'<p><a href="{base}/oportunidades/{o.id}">Responder en el portal de aliados</a></p>'
    )
    n = 0
    for u in rows:
        if u.email:
            queue_email(db, p.tenant, u.email, asunto, cuerpo, "ally_cost_request", r.id)
            n += 1
    return n


@router.put("/opportunity-allies/requests/{rid}")
def update_request(rid: int, data: CostRequestIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    r, o = _request(db, rid, p)
    if data.status != r.status:
        _check_status(p, data.status)
    if r.status in ("aprobado", "rechazado") and not p.sees_costs:
        raise HTTPException(403, "Esa solicitud ya fue resuelta por quien ve costos")
    cambios = []
    if data.status != r.status:
        cambios.append(f"{STATE_LABEL[r.status]} → {STATE_LABEL[data.status]}")
    if data.responsible_id != r.responsible_id:
        who = _check_owner(db, p, data.responsible_id) if data.responsible_id else None
        cambios.append(f"responsable {who.full_name if who else 'sin asignar'}")
    if data.due_date != r.due_date:
        cambios.append(f"fecha límite {_fecha(data.due_date)}")
    if data.what.strip() != r.what:
        cambios.append(f"qué se pidió: {data.what.strip()[:80]}")
    r.what, r.responsible_id, r.due_date, r.status = data.what.strip(), data.responsible_id, data.due_date, data.status
    if _apply_costs(db, p, r, data):
        cambios.append("datos del costo")  # el monto no va a la bitacora: la lee quien no ve costos
    if cambios:
        _log(o, p.user.full_name, f"Solicitud de costo «{r.what}» de {r.participation.name}: {', '.join(cambios)}")
        audit(db, p.tenant.id, p.user.id, "cost_request_update", "opportunity", o.id, {"request_id": r.id, "cambios": cambios}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


@router.delete("/opportunity-allies/requests/{rid}")
def remove_request(rid: int, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    r, o = _request(db, rid, p)
    if r.status in ("aprobado", "rechazado") and not p.sees_costs:
        raise HTTPException(403, "Esa solicitud ya fue resuelta por quien ve costos")
    _log(o, p.user.full_name, f"Solicitud de costo «{r.what}» de {r.participation.name} eliminada")
    audit(db, p.tenant.id, p.user.id, "cost_request_remove", "opportunity", o.id, {"request_id": r.id}, ip=p.ip)
    db.delete(r)
    db.commit()
    return _list(db, o, p)
