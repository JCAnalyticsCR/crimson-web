"""Aliados de la oportunidad: participacion, contactos por funcion y solicitudes de costo.

Todo pasa por la oportunidad (mismos permisos y alcance que el embudo: el vendedor solo toca las suyas) y cada
cambio deja una linea en su bitacora. Rutas bajo /opportunity-allies para no chocar con /opportunities/{oid}.
Nada de esto se expone en /cliente/*: el portal del cliente no importa este router ni sus tablas.

Empresa aliada (12:45-13:23): el buscador trae por defecto SOLO empresas aliadas (todos=true: tambien clientes y
proveedores, como antes) y se puede dar de alta un aliado ahi mismo. Sus contactos por funcion viven en la empresa
aliada y se precargan en la participacion. Las participaciones viejas (cliente/proveedor) no dan acceso al portal de
aliados hasta "Convertir en aliado". Al aliado le llegan avisos sin montos.

Revision 2 (Andres, 30/10):
- Puntos 7-9: "Solicitar precio al aliado" crea la PETICION (destinatario, alcance, partidas con cantidad y unidad,
  sitio, fecha limite, inclusiones, exclusiones, adjuntos, responsable interno de seguimiento y como respondera), sin
  precio ni vigencia. El aliado completa la OFERTA despues desde su portal (o quien ve costos registra la que llego por
  fuera: /offer). Estados: borrador, enviado, visto, respondido, aceptado, rechazado y vencido (calculado).
- Punto 11: aceptar guarda el acuerdo como version (AllyAgreement); cada cambio posterior es otra version (/agreement).
- Punto 10: /transparency enciende "trabajar con costos abiertos".
- A2: /relations define Aliado comercial, Responsable de ejecucion y Quien le paga a Crimson (sin "Rol" general)."""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import AllyCompany, AllyCostRequest, AllyParticipation, Customer, Media, Opportunity, Supplier, TenantUser, User
from ..models.allies import ALLY_ROLES, CONTACT_FUNCTIONS, COST_CLOSED, RESPONSE_CHANNELS
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


class ContactIn(BaseModel):
    name: str | None = Field(None, max_length=160)
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)


class AllyIn(BaseModel):
    ally_company_id: int | None = None
    customer_id: int | None = None
    supplier_id: int | None = None
    visible_to_ally: bool = True  # el aliado la ve en su portal (solo con empresa aliada)
    # A2: la interfaz ya no pide un "Rol" general (las relaciones concretas van en /relations). Sin dato: al crear
    # "subcontratista" (participa), al editar se conserva el que tenia
    role: str | None = Field(None, pattern="^(" + "|".join(ALLY_ROLES) + ")$")
    is_origin: bool | None = None  # Aliado comercial (quien origino la oportunidad)
    is_executor: bool | None = None  # Responsable de ejecucion
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


class ReqLineIn(BaseModel):
    concept: str = Field(min_length=1, max_length=200)
    quantity: Decimal | None = Field(None, ge=0)
    unit: str | None = Field(None, max_length=12)
    scope_item_id: int | None = None  # partida de "Alcances y responsables" de la que sale


class CostRequestIn(BaseModel):
    """La PETICION (puntos 8 y 9). status: sin dato = enviado al crear (borrador para guardarla sin enviar); los de
    antes (solicitado, recibido, aprobado) se aceptan como sinonimos. Monto, vigencia, condiciones, exclusiones de la
    oferta y adjuntos de la oferta: compatibilidad, solo quien ve costos (lo normal es que el aliado los complete)."""

    what: str = Field(min_length=2, max_length=300)
    responsible_id: int | None = None  # "Responsable interno de seguimiento"
    due_date: date | None = None
    status: str | None = Field(None, pattern="^(borrador|enviado|visto|respondido|aceptado|rechazado|vencido|solicitado|recibido|aprobado)$")
    scope: str | None = Field(None, max_length=4000)
    site: str | None = Field(None, max_length=300)
    inclusions: str | None = Field(None, max_length=4000)
    req_exclusions: str | None = Field(None, max_length=4000)
    req_lines: list[ReqLineIn] | None = Field(None, max_length=60)
    req_attachment_ids: list[int] | None = Field(None, max_length=10)
    response_channel: str | None = Field(None, pattern="^(" + "|".join(RESPONSE_CHANNELS) + ")$")
    # campos de la oferta: solo los escribe quien ve costos (catalog.costos)
    amount: Decimal | None = Field(None, ge=0)
    currency: str = Field("USD", pattern="^[A-Z]{3}$")
    valid_until: date | None = None
    conditions: str | None = Field(None, max_length=4000)
    exclusions: str | None = Field(None, max_length=4000)
    attachment_ids: list[int] | None = Field(None, max_length=10)


def _company(db: Session, p: Principal, data: AllyIn, current: AllyParticipation | None = None) -> tuple[int | None, int | None, int | None, str]:
    """(ally_company_id, customer_id, supplier_id, nombre): exactamente una de las tres. Una empresa aliada inactiva no
    entra a una participacion nueva, pero la que ya tenia (current) se sigue editando (papel, alcance, visibilidad)."""
    if sum(1 for x in (data.ally_company_id, data.customer_id, data.supplier_id) if x) != 1:
        raise HTTPException(422, "Elija la empresa aliada")
    if data.ally_company_id:
        a = db.get(AllyCompany, data.ally_company_id)
        if not a or a.tenant_id != p.tenant.id:
            raise HTTPException(404, "Empresa aliada no encontrada")
        if not a.active and not (current is not None and current.ally_company_id == a.id):
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
    from ..services.cartera import customer_scope  # cartera con dueno: el vendedor solo ve sus clientes

    custs = db.scalars(
        select(Customer)
        .where(Customer.tenant_id == p.tenant.id, customer_scope(p), Customer.name.ilike(like), Customer.trashed_at.is_(None))
        .order_by(Customer.name)
        .limit(10)
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


# ---------- Bitacora compartida con el aliado (reunion 2, pedido 15) ----------
@router.get("/opportunities/{oid}/bitacora-aliado")
def shared_log(oid: int, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """Que entradas de la bitacora ve el aliado hoy (por defecto ninguna) y que empresas las verian."""
    from ..services import ally_log

    return ally_log.state(db, _own(db, oid, p))


class SharedLogIn(BaseModel):
    line: str = Field(min_length=1, max_length=4000)  # la entrada tal como sale en la bitacora
    visible: bool


@router.put("/opportunities/{oid}/bitacora-aliado")
def shared_log_set(oid: int, data: SharedLogIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Interruptor "Visible para el aliado" de UNA entrada. La linea tiene que estar hoy en la bitacora."""
    from ..services import ally_log

    o = _own(db, oid, p)
    line = data.line.strip()
    if line not in ally_log.lines_of(o.notes):
        raise HTTPException(404, "Esa entrada ya no está en la bitácora")
    if data.visible and not ally_log.ally_companies(db, o):
        raise HTTPException(409, "Esta oportunidad no tiene un aliado que la vea en su portal")
    if ally_log.set_visible(db, o, line, data.visible, p.user.id):
        audit(db, p.tenant.id, p.user.id, "ally_log_share" if data.visible else "ally_log_hide", "opportunity", o.id, {"entrada": line[:120]}, ip=p.ip)
        db.commit()
    return ally_log.state(db, o)


@router.post("/opportunities/{oid}/allies", status_code=201)
def add_ally(oid: int, data: AllyIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _own(db, oid, p)
    aid, cid, sid, name = _company(db, p, data)
    role = data.role or "subcontratista"
    dup = db.scalar(select(AllyParticipation).where(AllyParticipation.opportunity_id == o.id, AllyParticipation.role == role, _dup_where(aid, cid, sid)))
    if dup:
        if data.role is None:
            raise HTTPException(409, f"{name} ya participa en esta oportunidad")
        raise HTTPException(409, f"{name} ya participa como {ROLE_LABEL[role]}")
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
        role=role,
        scope=data.scope,
        scope_lines=[x.strip()[:200] for x in data.scope_lines if x.strip()],
        contacts=contactos,
        created_by=p.user.id,
    )
    db.add(a)
    db.flush()
    _relations_from_form(db, o, a, data)
    _log(
        o,
        p.user.full_name,
        f"Aliado {name} agregado" + (f" como {ROLE_LABEL[role]}" if data.role else "") + (f" · alcance: {data.scope[:120]}" if data.scope else ""),
    )
    audit(db, p.tenant.id, p.user.id, "ally_add", "opportunity", o.id, {"ally_id": a.id, "name": name, "role": role}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


@router.put("/opportunity-allies/{aid}")
def update_ally(aid: int, data: AllyIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    a, o = _ally(db, aid, p)
    comp_id, cid, sid, name = _company(db, p, data, current=a)
    cambios = []
    otra_empresa = (comp_id, cid, sid) != (a.ally_company_id, a.customer_id, a.supplier_id)
    if otra_empresa and a.requests:
        # las solicitudes (montos, lineas, exclusiones, adjuntos) son de la empresa que respondio: con otra empresa en la
        # participacion, la nueva veria en su portal el precio de la anterior
        raise HTTPException(409, "Esta participación tiene solicitudes de precio: quítela y cree otra para la nueva empresa")
    contactos = _contacts(data)
    if otra_empresa and comp_id and (not contactos or contactos == (a.contacts or {})):
        # los contactos que traia el formulario eran los de la empresa anterior: van los de la nueva
        contactos = dict(db.get(AllyCompany, comp_id).contacts or {})
    if otra_empresa:
        cambios.append(f"empresa {a.name} → {name}")
    role = data.role or a.role
    if role != a.role:
        cambios.append(f"papel {ROLE_LABEL.get(a.role, a.role)} → {ROLE_LABEL[role]}")
    if (data.scope or None) != (a.scope or None) or [x.strip() for x in data.scope_lines if x.strip()] != (a.scope_lines or []):
        cambios.append("alcance")
    if contactos != (a.contacts or {}):
        cambios.append("contactos")
    a.ally_company_id, a.customer_id, a.supplier_id, a.name, a.role = comp_id, cid, sid, name, role
    if "visible_to_ally" in data.model_fields_set and data.visible_to_ally != a.visible_to_ally:
        a.visible_to_ally = data.visible_to_ally  # apagarla le quita el acceso al aliado al instante
        cambios.append("visible en el portal del aliado" if data.visible_to_ally else "oculta en el portal del aliado")
    a.scope = data.scope
    a.scope_lines = [x.strip()[:200] for x in data.scope_lines if x.strip()]
    a.contacts = contactos
    cambios += _relations_from_form(db, o, a, data)
    if cambios:
        _log(o, p.user.full_name, f"Aliado {name} actualizado: {', '.join(cambios)}")
        audit(db, p.tenant.id, p.user.id, "ally_update", "opportunity", o.id, {"ally_id": a.id, "cambios": cambios}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


def _relations_from_form(db: Session, o: Opportunity, a: AllyParticipation, data: AllyIn) -> list[str]:
    """Las casillas "Originó la oportunidad" y "Responsable de ejecución" del formulario del aliado: una sola
    participacion por oportunidad tiene cada una (marcar esta desmarca la anterior)."""
    out = []
    lost: set[int] = set()
    for campo, label in (("is_origin", "aliado comercial"), ("is_executor", "responsable de ejecución")):
        v = getattr(data, campo)
        if v is None or bool(getattr(a, campo)) == v:
            continue
        if v:
            for x in svc.allies_of(db, o.tenant_id, opportunity_id=o.id):
                if x.id != a.id and getattr(x, campo):
                    setattr(x, campo, False)
                    if campo == "is_origin":
                        lost.add(x.id)
        elif campo == "is_origin":
            lost.add(a.id)
        setattr(a, campo, v)
        out.append(label if v else f"ya no es {label}")
    if out:
        db.flush()
        sync_roles(db, o, lost)
    return out


def _misma_empresa(db: Session, a: AllyParticipation, comp: AllyCompany) -> bool:
    """La empresa aliada es el mismo cliente/proveedor de la participacion vieja (ligado o con la misma cedula)."""
    from .ally_admin import clean_tax

    if (a.customer_id and comp.customer_id == a.customer_id) or (a.supplier_id and comp.supplier_id == a.supplier_id):
        return True
    src = db.get(Customer, a.customer_id) if a.customer_id else db.get(Supplier, a.supplier_id) if a.supplier_id else None
    tax = clean_tax(src.id_number if isinstance(src, Customer) else getattr(src, "tax_id", None)) if src is not None else None
    return bool(tax and clean_tax(comp.tax_id) == tax)


class ToAllyIn(BaseModel):
    ally_company_id: int | None = None


@router.post("/opportunity-allies/{aid}/to-ally")
def convert_to_ally(aid: int, data: ToAllyIn | None = None, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Participacion vieja (un cliente o un proveedor) -> empresa aliada. Con id usa esa empresa. Sin id liga SOLO por
    vinculo exacto (la empresa aliada ya ligada a ese cliente/proveedor) o por la misma cedula (no vacia); si no hay
    ninguna, la crea copiando sus datos y ligada a ese cliente/proveedor (es esa misma empresa). Nunca por nombre: una
    empresa aliada con el mismo nombre y otra cedula responde 409 con ella en "existing" para elegirla a mano.

    Este endpoint es del embudo (crm_pipeline): a una empresa aliada que YA existe no le cambia el cliente de facturacion
    ni el proveedor de pagos (customer_id/supplier_id). Eso decide que facturas ve en su portal y se liga en
    Administracion -> Aliados (aliados.editar)."""
    from fastapi.responses import JSONResponse

    from .ally_admin import clean_tax, norm_name

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
        if src is not None and src.tenant_id != p.tenant.id:
            src = None
        tax = clean_tax(src.id_number if isinstance(src, Customer) else getattr(src, "tax_id", None)) if src else None
        if src is not None:
            link = AllyCompany.customer_id == src.id if isinstance(src, Customer) else AllyCompany.supplier_id == src.id
            comp = db.scalar(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, link).order_by(AllyCompany.id).limit(1))
        if comp is None and tax:
            comp = db.scalar(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, AllyCompany.tax_id == tax).limit(1))
        if comp is None:
            nombre = src.name if src else a.name
            n = norm_name(nombre)
            parecida = next((c for c in db.scalars(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id)) if n and norm_name(c.name) == n), None)
            if parecida is not None:
                # mismo nombre, otra cedula (o sin cedula): puede ser otra empresa. Que la elija una persona
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": f"Ya existe la empresa aliada «{parecida.name}» con ese nombre pero no con la misma cédula: elegila a mano si es la misma",
                        "existing": {"id": parecida.id, "name": parecida.name, "kind": "ally"},
                    },
                )
            comp = AllyCompany(
                tenant_id=p.tenant.id,
                name=nombre[:160],
                tax_id=tax,
                email=(getattr(src, "email", None) or "").lower() or None,
                phone=getattr(src, "phone", None),
                contacts=dict(a.contacts or {}),
                # la empresa nueva ES ese cliente/proveedor (sus mismos datos): queda ligada para facturarle o pagarle
                customer_id=src.id if isinstance(src, Customer) else None,
                supplier_id=src.id if isinstance(src, Supplier) else None,
                active=True,
                created_by=p.user.id,
            )
            db.add(comp)
            db.flush()
            audit(db, p.tenant.id, p.user.id, "create", "ally_company", comp.id, {"name": comp.name, "desde": "participación"}, ip=p.ip)
    if not comp.active:
        raise HTTPException(422, "Esa empresa aliada está inactiva")
    if a.requests and not _misma_empresa(db, a, comp):
        # con solicitudes, solo la empresa aliada que ES ese cliente/proveedor (si no, veria en su portal el precio
        # que mando otra)
        raise HTTPException(409, "Esta participación tiene solicitudes de precio de otra empresa: quítela y cree otra para la empresa aliada")
    antes = "cliente" if a.customer_id else "proveedor"
    a.ally_company_id, a.customer_id, a.supplier_id, a.name = comp.id, None, None, comp.name
    _log(o, p.user.full_name, f"Aliado {comp.name} convertido de {antes} a empresa aliada")
    audit(db, p.tenant.id, p.user.id, "ally_convert", "opportunity", o.id, {"ally_id": a.id, "ally_company_id": comp.id}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


@router.delete("/opportunity-allies/{aid}")
def remove_ally(aid: int, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    a, o = _ally(db, aid, p)
    from ..services import commissions as comsvc

    if comsvc.has_live_movements(db, participation=a):
        raise HTTPException(409, "Tiene movimientos de comisión: anulalos primero en Comisiones")
    comsvc.drop_participation_rules(db, a, p)  # su acuerdo economico (reglas por proyecto) se va con la participacion, en la bitacora
    n = len(a.requests)
    from ..services import scope_items

    sueltas = scope_items.release_participation(db, a)  # sus partidas de alcance quedan "por asignar"
    _log(
        o,
        p.user.full_name,
        f"Aliado {a.name} quitado" + (f" (con {n} solicitud(es) de precio)" if n else "") + (f" · {sueltas} alcance(s) quedan por asignar" if sueltas else ""),
    )
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


COST_FIELDS = ("amount", "valid_until", "exclusions", "attachment_ids", "conditions")


def _apply_costs(db: Session, p: Principal, r: AllyCostRequest, data: CostRequestIn) -> bool:
    """Campos de la OFERTA en el formulario de la solicitud (compatibilidad: antes se registraban ahi). Solo con
    catalog.costos y solo los que vienen en el cuerpo: editar la peticion no borra la oferta que mando el aliado."""
    sent = data.model_fields_set
    con_valor = any(getattr(data, k) not in (None, "", []) for k in COST_FIELDS if k in sent)
    if not p.sees_costs:
        if con_valor:
            raise HTTPException(403, "Solo quien ve costos registra el monto, la vigencia, las exclusiones o los adjuntos")
        return False
    antes = (r.amount, r.currency, r.valid_until, r.exclusions, r.attachments, r.conditions)
    if "amount" in sent:
        r.amount = data.amount
    if "currency" in sent or ("amount" in sent and data.amount is not None):
        r.currency = data.currency
    if "valid_until" in sent:
        r.valid_until = data.valid_until
    if "exclusions" in sent:
        r.exclusions = data.exclusions
    if "conditions" in sent:
        r.conditions = data.conditions
    if data.attachment_ids is not None:
        r.attachments = _attachments(db, p, data.attachment_ids)
    return antes != (r.amount, r.currency, r.valid_until, r.exclusions, r.attachments, r.conditions)


def _check_status(p: Principal, nuevo: str) -> None:
    # aceptar o rechazar una oferta es de quien la puede ver (el monto es costo para Crimson)
    if nuevo in COST_CLOSED and not p.sees_costs:
        raise HTTPException(403, "Aceptar o rechazar una oferta es de quien ve costos")


def _fecha(v: date | None) -> str:
    return v.strftime("%d/%m/%Y") if v else "sin fecha"


def _req_lines(db: Session, data: CostRequestIn, o: Opportunity) -> list[dict] | None:
    """Partidas de la peticion (concepto, cantidad, unidad). Una que sale de "Alcances y responsables" tiene que ser de
    esta oportunidad."""
    if data.req_lines is None:
        return None
    from ..models import ScopeItem

    out = []
    for ln in data.req_lines:
        if ln.scope_item_id is not None:
            it = db.get(ScopeItem, ln.scope_item_id)
            if it is None or it.tenant_id != o.tenant_id or it.opportunity_id != o.id:
                raise HTTPException(404, "Partida de alcance no encontrada")
        out.append(
            {"concept": ln.concept.strip(), "quantity": _num_str(ln.quantity), "unit": (ln.unit or "").strip() or None, "scope_item_id": ln.scope_item_id}
        )
    return out


def _num_str(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _apply_petition(db: Session, p: Principal, r: AllyCostRequest, data: CostRequestIn, o: Opportunity) -> list[str]:
    """La peticion: alcance, partidas, sitio, inclusiones, exclusiones, adjuntos y como respondera. Solo lo que viene."""
    sent = data.model_fields_set
    cambios = []
    for k, label in (("scope", "alcance"), ("site", "sitio"), ("inclusions", "inclusiones"), ("req_exclusions", "exclusiones")):
        if k in sent:
            v = (getattr(data, k) or "").strip() or None
            if v != getattr(r, k):
                setattr(r, k, v)
                cambios.append(label)
    if "req_lines" in sent:
        lines = _req_lines(db, data, o) or []
        if lines != (r.req_lines or []):
            r.req_lines = lines
            cambios.append("partidas")
    if data.req_attachment_ids is not None:
        att = _attachments(db, p, data.req_attachment_ids)
        if att != (r.req_attachments or []):
            r.req_attachments = att
            cambios.append("adjuntos")
    if "response_channel" in sent and data.response_channel and data.response_channel != r.response_channel:
        r.response_channel = data.response_channel
        cambios.append(f"responde {svc.CHANNEL_LABEL.get(data.response_channel, data.response_channel)}")
    return cambios


def _send(db: Session, p: Principal, a: AllyParticipation, o: Opportunity, r: AllyCostRequest) -> None:
    """borrador -> enviado: desde ahora el aliado la ve en su portal y le llega el aviso (sin montos)."""
    r.status = "enviado"
    r.sent_at = r.sent_at or datetime.now(UTC)
    if a.ally_company_id and a.visible_to_ally:
        _notify_ally_request(db, p, a, o, r)


def _transition(db: Session, p: Principal, r: AllyCostRequest, o: Opportunity, nuevo: str, *, shared: bool = True, note: str | None = None) -> list[str]:
    """Cambio de estado con sus efectos: enviar avisa al aliado, aceptar guarda el acuerdo (version), rechazar o
    reabrir una aceptada anula el acuerdo vigente. Devuelve lo que va a la bitacora."""
    antes = r.status
    if nuevo == antes:
        return []
    if nuevo == "borrador" and antes != "borrador":
        raise HTTPException(409, "Una solicitud enviada no vuelve a borrador")
    if nuevo == "aceptado" and r.amount is None:
        raise HTTPException(409, "Para aceptarla falta la oferta con el precio (la completa el aliado o se registra la que llegó por fuera)")
    out = [f"{svc.STATE_LABEL[antes].lower()} → {svc.STATE_LABEL[nuevo].lower()}"]
    if nuevo == "enviado" and antes == "borrador":
        _send(db, p, r.participation, o, r)
    else:
        r.status = nuevo
        if nuevo in ("enviado", "visto") and r.sent_at is None:
            r.sent_at = datetime.now(UTC)
    if antes == "aceptado":
        if svc.void_agreement(db, r):
            out.append("acuerdo anulado")
    if nuevo in COST_CLOSED:
        svc.mark_decided(r, p.user.id, note)
    if nuevo == "aceptado":
        g = svc.accept_offer(db, r, p.user.id, shared=shared, note=note)
        out.append(f"acuerdo versión {g.version}")
    return out


def _new_status(data: CostRequestIn) -> str | None:
    return svc.norm_status(data.status) if "status" in data.model_fields_set or data.status is not None else None


@router.post("/opportunity-allies/{aid}/requests", status_code=201)
def add_request(aid: int, data: CostRequestIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """ "Solicitar precio al aliado": crea la PETICION (borrador o enviada). Sin precio ni vigencia: esos los completa
    el aliado en su oferta. (Compatibilidad: quien ve costos todavia puede mandar monto y estado en el mismo cuerpo.)"""
    a, o = _ally(db, aid, p)
    st = _new_status(data) or "enviado"
    _check_status(p, st)
    who = _check_owner(db, p, data.responsible_id) if data.responsible_id else None
    r = AllyCostRequest(
        tenant_id=p.tenant.id,
        participation_id=a.id,
        opportunity_id=o.id,
        what=data.what.strip(),
        responsible_id=data.responsible_id,
        due_date=data.due_date,
        status="borrador",
        site=o.site,
        response_channel="portal" if (a.ally_company_id and a.visible_to_ally) else "correo",
        created_by=p.user.id,
    )
    # primero lo que puede fallar (permisos, adjuntos, partidas): una solicitud a medias no queda en la sesion
    _apply_petition(db, p, r, data, o)
    _apply_costs(db, p, r, data)
    a.requests.append(r)
    db.flush()
    if st != "borrador":
        if st in ("visto", "respondido", "aceptado", "rechazado"):
            r.sent_at = datetime.now(UTC)  # registrada ya avanzada (llego por fuera): igual cuenta como enviada
        if st == "enviado":
            _send(db, p, a, o, r)
        else:
            _transition(db, p, r, o, st)
    from ..services import commissions as comsvc

    # nacer ya aceptada es aceptarla (E5): con el proyecto completado se devenga su pago acordado
    comsvc.on_cost_request_change(db, r, None, None)
    estado = "borrador" if r.status == "borrador" else f"enviada a {a.name}" if r.status == "enviado" else svc.STATE_LABEL[r.status].lower()
    _log(
        o,
        p.user.full_name,
        f"Solicitud de precio a {a.name}: {r.what} · {estado} · seguimiento: {who.full_name if who else 'sin asignar'} · límite {_fecha(r.due_date)}",
    )
    audit(db, p.tenant.id, p.user.id, "cost_request", "opportunity", o.id, {"request_id": r.id, "ally": a.name, "status": r.status}, ip=p.ip)
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
        + (f"<p>Alcance: {e(r.scope)}</p>" if r.scope else "")
        + (f"<p>Antes del {_fecha(r.due_date)}.</p>" if r.due_date else "")
        + f'<p><a href="{base}/oportunidades/{o.id}">Ver la solicitud y responder en el portal de aliados</a></p>'
    )
    n = 0
    from ..services.mail import user_inbox

    for u in rows:
        correo = user_inbox(db, p.tenant.id, u)  # su correo de avisos, si el admin le puso uno (pedido 22)
        if correo:
            queue_email(db, p.tenant, correo, asunto, cuerpo, "ally_cost_request", r.id)
            n += 1
    return n


@router.put("/opportunity-allies/requests/{rid}")
def update_request(rid: int, data: CostRequestIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    r, o = _request(db, rid, p)
    nuevo = _new_status(data)
    if nuevo is not None and nuevo != r.status:
        _check_status(p, nuevo)
    if r.status in COST_CLOSED and not p.sees_costs:
        raise HTTPException(403, "Esa solicitud ya fue resuelta por quien ve costos")
    antes_status, antes_amount = r.status, r.amount
    cambios = []
    if data.responsible_id != r.responsible_id:
        who = _check_owner(db, p, data.responsible_id) if data.responsible_id else None
        cambios.append(f"seguimiento: {who.full_name if who else 'sin asignar'}")
    if data.due_date != r.due_date:
        cambios.append(f"fecha límite {_fecha(data.due_date)}")
    if data.what.strip() != r.what:
        cambios.append(f"qué se pide: {data.what.strip()[:80]}")
    r.what, r.responsible_id, r.due_date = data.what.strip(), data.responsible_id, data.due_date
    cambios += _apply_petition(db, p, r, data, o)
    if (
        r.status == "aceptado"
        and p.sees_costs
        and any(k in data.model_fields_set for k in ("amount", "currency", "valid_until", "exclusions", "conditions"))
        and nuevo in (None, "aceptado")
    ):
        # despues de aceptada, un cambio de precio, moneda, vigencia, condiciones o exclusiones es una version nueva
        vals = {k: getattr(data, k) for k in ("amount", "currency", "valid_until", "exclusions", "conditions") if k in data.model_fields_set}
        if "amount" in vals and vals["amount"] is None:
            raise HTTPException(422, "El acuerdo necesita un precio")
        g = svc.new_agreement_version(db, r, p.user.id, vals)
        if g is not None:
            cambios.append(f"acuerdo versión {g.version}")
    elif _apply_costs(db, p, r, data):
        cambios.append("datos de la oferta")  # el monto no va a la bitacora: la lee quien no ve costos
    if nuevo is not None:
        cambios += _transition(db, p, r, o, nuevo)
    if cambios:
        _log(o, p.user.full_name, f"Solicitud de precio «{r.what}» a {r.participation.name}: {', '.join(cambios)}")
        audit(db, p.tenant.id, p.user.id, "cost_request_update", "opportunity", o.id, {"request_id": r.id, "cambios": cambios}, ip=p.ip)
    from ..services import commissions as comsvc

    # el precio aceptado es el pago acordado del aliado: aceptarlo, cambiarlo o desaceptarlo mueve su libro
    comsvc.on_cost_request_change(db, r, antes_status, antes_amount)
    db.commit()
    return _list(db, o, p)


@router.post("/opportunity-allies/requests/{rid}/send")
def send_request(rid: int, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Enviar el borrador al aliado: desde ahora la ve en su portal (y le llega el aviso)."""
    r, o = _request(db, rid, p)
    if r.status != "borrador":
        raise HTTPException(409, "Esta solicitud ya se envió")
    _send(db, p, r.participation, o, r)
    _log(
        o,
        p.user.full_name,
        f"Solicitud de precio «{r.what}» enviada a {r.participation.name} ({svc.CHANNEL_LABEL.get(r.response_channel, r.response_channel)})",
    )
    audit(db, p.tenant.id, p.user.id, "cost_request_send", "opportunity", o.id, {"request_id": r.id}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


class OfferLineIn(BaseModel):
    concept: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    unit: str | None = Field(None, max_length=12)
    unit_price: Decimal = Field(ge=0)


class OfferIn(BaseModel):
    """La oferta que llego por fuera (correo, WhatsApp, telefono): la registra quien ve costos."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(None, ge=0)
    lines: list[OfferLineIn] | None = Field(None, max_length=60)
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    valid_until: date | None = None
    conditions: str | None = Field(None, max_length=4000)
    exclusions: str | None = Field(None, max_length=4000)
    attachment_ids: list[int] | None = Field(None, max_length=10)
    channel: str | None = Field(None, pattern="^(" + "|".join(RESPONSE_CHANNELS) + ")$")

    @model_validator(mode="after")
    def _uno(self):
        if (self.amount is None) == (not self.lines):
            raise ValueError("Indique un monto o un desglose por líneas (uno de los dos)")
        return self


def offer_total(lines) -> Decimal:
    return sum((Decimal(str(ln.quantity)) * Decimal(str(ln.unit_price)) for ln in lines), Decimal(0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@router.post("/opportunity-allies/requests/{rid}/offer")
def register_offer(rid: int, data: OfferIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Registrar la oferta del aliado que llego por fuera del portal. Solo quien ve costos (es su costo)."""
    r, o = _request(db, rid, p)
    if not p.sees_costs:
        raise HTTPException(403, "Solo quien ve costos registra la oferta del aliado")
    if r.status in COST_CLOSED:
        raise HTTPException(409, "Esta solicitud ya se decidió: para cambiar un acuerdo aceptado use «Nueva versión del acuerdo»")
    if data.lines:
        r.lines = [{"concept": ln.concept, "quantity": str(ln.quantity), "unit": ln.unit or None, "unit_price": str(ln.unit_price)} for ln in data.lines]
        r.amount = offer_total(data.lines)
    else:
        r.lines, r.amount = [], data.amount
    r.currency, r.valid_until, r.conditions, r.exclusions = data.currency, data.valid_until, data.conditions, data.exclusions
    if data.attachment_ids is not None:
        r.attachments = _attachments(db, p, data.attachment_ids)
    if data.channel:
        r.response_channel = data.channel
    r.sent_at = r.sent_at or datetime.now(UTC)
    r.status = "respondido"
    r.responded_by, r.responded_at = p.user.id, datetime.now(UTC)
    _log(
        o,
        p.user.full_name,
        f"Registró la oferta de {r.participation.name} para «{r.what}» (llegó {svc.CHANNEL_LABEL.get(r.response_channel, r.response_channel)})",
    )
    audit(db, p.tenant.id, p.user.id, "cost_request_offer", "opportunity", o.id, {"request_id": r.id}, ip=p.ip)
    db.commit()
    return _list(db, o, p)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(aceptar|rechazar)$")
    note: str | None = Field(None, max_length=400)
    shared: bool = True  # el monto acordado afecta el presupuesto comun: lo ven los demas aliados (punto 10)


@router.post("/opportunity-allies/requests/{rid}/decision")
def decide_request(rid: int, data: DecisionIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Aceptar (queda el acuerdo, version 1) o rechazar la oferta. Es de quien ve costos."""
    r, o = _request(db, rid, p)
    nuevo = "aceptado" if data.action == "aceptar" else "rechazado"
    _check_status(p, nuevo)
    if r.status == nuevo:
        raise HTTPException(409, f"La solicitud ya está {svc.STATE_LABEL[nuevo].lower()}")
    if r.status == "borrador":
        raise HTTPException(409, "Primero enviala al aliado")
    antes_status, antes_amount = r.status, r.amount
    cambios = _transition(db, p, r, o, nuevo, shared=data.shared, note=data.note)
    _log(o, p.user.full_name, f"Oferta de {r.participation.name} para «{r.what}»: {', '.join(cambios)}" + (f" · {data.note}" if data.note else ""))
    audit(db, p.tenant.id, p.user.id, "cost_request_decision", "opportunity", o.id, {"request_id": r.id, "action": data.action}, ip=p.ip)
    from ..services import commissions as comsvc

    comsvc.on_cost_request_change(db, r, antes_status, antes_amount)
    db.commit()
    return _list(db, o, p)


class AgreementIn(BaseModel):
    """Cambio posterior a un acuerdo aceptado: version nueva (solo lo que viene en el cuerpo cambia)."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(None, ge=0)
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    valid_until: date | None = None
    conditions: str | None = Field(None, max_length=4000)
    exclusions: str | None = Field(None, max_length=4000)
    shared: bool | None = None
    note: str | None = Field(None, max_length=400)


@router.post("/opportunity-allies/requests/{rid}/agreement")
def agreement_version(rid: int, data: AgreementIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    r, o = _request(db, rid, p)
    if not p.sees_costs:
        raise HTTPException(403, "Los acuerdos económicos los cambia quien ve costos")
    if r.status != "aceptado":
        raise HTTPException(409, "Esta solicitud no tiene un acuerdo aceptado")
    vals = {k: getattr(data, k) for k in ("amount", "currency", "valid_until", "conditions", "exclusions") if k in data.model_fields_set}
    if "amount" in vals and vals["amount"] is None:
        raise HTTPException(422, "El acuerdo necesita un precio")
    antes_amount = r.amount
    g = svc.new_agreement_version(db, r, p.user.id, vals, note=data.note, shared=data.shared)
    if g is None:
        raise HTTPException(409, "No cambió nada del acuerdo")
    # la bitacora la lee quien no ve costos: dice que cambio, no los montos
    que = [c.split(":")[0] for c in g.changes]
    _log(
        o,
        p.user.full_name,
        f"Acuerdo con {r.participation.name} («{r.what}»): versión {g.version} · cambió {', '.join(que)}" + (f" · {data.note}" if data.note else ""),
    )
    audit(db, p.tenant.id, p.user.id, "ally_agreement", "opportunity", o.id, {"request_id": r.id, "version": g.version, "cambios": g.changes}, ip=p.ip)
    from ..services import commissions as comsvc

    comsvc.on_cost_request_change(db, r, "aceptado", antes_amount)
    db.commit()
    return _list(db, o, p)


@router.get("/opportunities/{oid}/agreements")
def opportunity_agreements(oid: int, p: Principal = Depends(require("crm_pipeline", "ver")), db: Session = Depends(get_db)):
    """Acuerdos aceptados de cada participante con todas sus versiones. Montos solo para quien ve costos."""
    o = _own(db, oid, p)
    return agreements_summary(db, o, p)


def agreements_summary(db: Session, o: Opportunity, p: Principal) -> list[dict]:
    parts = {a.id: a for a in svc.allies_of(db, o.tenant_id, opportunity_id=o.id)}
    by_req: dict[int, list] = {}
    for g in svc.agreements_of(db, opportunity_id=o.id):
        by_req.setdefault(g.request_id, []).append(g)
    out = []
    for rid, versions in by_req.items():
        cur = versions[-1]
        a = parts.get(cur.participation_id)
        item = {
            "request_id": rid,
            "participation_id": cur.participation_id,
            "ally": a.name if a else None,
            "what": cur.what,
            "status": cur.status,
            "version": cur.version,
            "updated_at": cur.created_at,
            "updated_by": svc._name(db, cur.created_by),
        }
        if p.sees_costs:
            item["current"] = svc.agreement_out(db, cur)
            item["versions"] = [svc.agreement_out(db, g) for g in reversed(versions)]
        out.append(item)
    return out


@router.delete("/opportunity-allies/requests/{rid}")
def remove_request(rid: int, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    r, o = _request(db, rid, p)
    if r.status in COST_CLOSED and not p.sees_costs:
        raise HTTPException(403, "Esa solicitud ya fue resuelta por quien ve costos")
    from ..services import commissions as comsvc

    if comsvc.has_live_movements(db, request=r):
        raise HTTPException(409, "Tiene movimientos de comisión: anulalos primero en Comisiones")
    n = len(svc.agreements_of(db, request_id=r.id))
    _log(o, p.user.full_name, f"Solicitud de precio «{r.what}» de {r.participation.name} eliminada" + (f" (con {n} versión(es) de acuerdo)" if n else ""))
    audit(db, p.tenant.id, p.user.id, "cost_request_remove", "opportunity", o.id, {"request_id": r.id, "agreements": n}, ip=p.ip)
    db.delete(r)
    db.commit()
    return _list(db, o, p)


# ---------- A2: relaciones concretas de la oportunidad ----------
class RelationsIn(BaseModel):
    """Aliado comercial (quien origino), Responsable de ejecucion (Crimson o un aliado) y Quien le paga a Crimson.
    Lo que no viene no cambia. payer: "end_customer" (el cliente final), "participation" (la empresa aliada de
    payer_participation_id: Crimson le factura a su cliente ligado, sin duplicar la ficha) o "customer" (otro cliente)."""

    model_config = ConfigDict(extra="forbid")

    origin_participation_id: int | None = None
    executor_participation_id: int | None = None  # None = ejecuta Crimson
    payer: str | None = Field(None, pattern="^(end_customer|participation|customer)$")
    payer_participation_id: int | None = None
    payer_customer_id: int | None = None


def _part_of(db: Session, o: Opportunity, pid: int | None) -> AllyParticipation | None:
    if pid is None:
        return None
    a = db.get(AllyParticipation, pid)
    if not a or a.tenant_id != o.tenant_id or a.opportunity_id != o.id:
        raise HTTPException(404, "Esa empresa no participa en esta oportunidad")
    return a


def _customer_of_participation(db: Session, p: Principal, a: AllyParticipation) -> Customer:
    """El cliente al que Crimson le factura a esta empresa: el que ya tiene ligado (o el de la participacion vieja).
    Si la empresa aliada no tiene, se crea UNA ficha de cliente con sus datos y queda ligada (no se duplica)."""
    if a.customer_id:
        return db.get(Customer, a.customer_id)
    comp = db.get(AllyCompany, a.ally_company_id) if a.ally_company_id else None
    if comp is None:
        raise HTTPException(422, "Esa empresa no es un cliente ni una empresa aliada: elija el cliente a mano")
    if comp.customer_id:
        return db.get(Customer, comp.customer_id)
    if comp.tax_id:
        from .ally_admin import clean_tax

        c = next(
            (
                x
                for x in db.scalars(select(Customer).where(Customer.tenant_id == p.tenant.id, Customer.trashed_at.is_(None)))
                if clean_tax(x.id_number) == clean_tax(comp.tax_id)
            ),
            None,
        )
        if c is not None:
            comp.customer_id = c.id
            return c
    c = Customer(
        tenant_id=p.tenant.id, id_type="juridica", id_number=comp.tax_id, name=(comp.legal_name or comp.name)[:200], email=comp.email, phone=comp.phone
    )
    db.add(c)
    db.flush()
    comp.customer_id = c.id
    audit(db, p.tenant.id, p.user.id, "create", "customer", c.id, {"desde": "empresa aliada (quién le paga a Crimson)", "aliado": comp.id}, ip=p.ip)
    return c


def sync_roles(db: Session, o: Opportunity, lost: set[int] | None = None) -> None:
    """role (compatibilidad) derivado de las relaciones: quien le paga a Crimson = contratante (no gana la regla
    general de comisiones de aliados); el aliado comercial = referido. Solo promueve; baja a "subcontratista" unicamente
    a las participaciones que en ESTE cambio dejaron de ser quien paga o quien origino (lost). Los datos de antes con su
    papel (un "referido" que nadie marco como aliado comercial) no se tocan."""
    parts = svc.allies_of(db, o.tenant_id, opportunity_id=o.id)
    payer = svc.relations_out(db, o)["payer"]["participation_id"]
    taken = {(a.ally_company_id, a.customer_id, a.supplier_id, a.role) for a in parts}
    for a in parts:
        if a.id == payer:
            want = "contratante"
        elif a.is_origin:
            want = "referido"
        elif lost and a.id in lost and a.role in ("contratante", "referido"):
            want = "subcontratista"
        else:
            continue
        if want != a.role and (a.ally_company_id, a.customer_id, a.supplier_id, want) not in taken:
            taken.discard((a.ally_company_id, a.customer_id, a.supplier_id, a.role))
            a.role = want
            taken.add((a.ally_company_id, a.customer_id, a.supplier_id, want))


@router.put("/opportunities/{oid}/relations")
def set_relations(oid: int, data: RelationsIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    o = _own(db, oid, p)
    parts = svc.allies_of(db, p.tenant.id, opportunity_id=o.id)
    sent = data.model_fields_set
    cambios = []
    lost: set[int] = set()
    payer_antes = svc.relations_out(db, o)["payer"]["participation_id"]
    if "origin_participation_id" in sent:
        nueva = _part_of(db, o, data.origin_participation_id)
        for a in parts:
            if a.is_origin and (nueva is None or a.id != nueva.id):
                lost.add(a.id)
            a.is_origin = nueva is not None and a.id == nueva.id
        cambios.append(f"aliado comercial: {nueva.name if nueva else 'ninguno (Crimson)'}")
    if "executor_participation_id" in sent:
        nueva = _part_of(db, o, data.executor_participation_id)
        for a in parts:
            a.is_executor = nueva is not None and a.id == nueva.id
        cambios.append(f"responsable de ejecución: {nueva.name if nueva else 'Crimson'}")
    if data.payer:
        if data.payer == "end_customer":
            if not o.end_customer_id and not o.customer_id:
                raise HTTPException(422, "La oportunidad no tiene cliente final")
            cid = o.end_customer_id or o.customer_id
        elif data.payer == "participation":
            a = _part_of(db, o, data.payer_participation_id)
            if a is None:
                raise HTTPException(422, "Elija la empresa que le paga a Crimson")
            cid = _customer_of_participation(db, p, a).id
        else:
            c = db.get(Customer, data.payer_customer_id) if data.payer_customer_id else None
            if not c or c.tenant_id != p.tenant.id:
                raise HTTPException(404, "Cliente no encontrado")
            cid = c.id
        if cid != o.customer_id:
            from ..services import cartera

            cartera.guard_customer(db, p, cid)
            final = o.end_customer_id or o.customer_id  # el cliente final no cambia por cambiar quien paga
            o.customer_id = cid
            o.end_customer_id = final if final and final != cid else None
            pay = db.get(Customer, cid)
            cambios.append(f"le paga a Crimson: {pay.name}")
    if cambios:
        db.flush()
        if payer_antes is not None:
            lost.add(payer_antes)
        sync_roles(db, o, lost)
        _log(o, p.user.full_name, "Relaciones: " + " · ".join(cambios))
        audit(db, p.tenant.id, p.user.id, "relations", "opportunity", o.id, {"cambios": cambios}, ip=p.ip)
    db.commit()
    return {"relations": svc.relations_out(db, o), "allies": _list(db, o, p)}


class TransparencyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_costs: bool


@router.put("/opportunities/{oid}/transparency")
def set_transparency(oid: int, data: TransparencyIn, p: Principal = Depends(require("crm_pipeline", "editar")), db: Session = Depends(get_db)):
    """Punto 10: "trabajar con costos abiertos". Encendido, los aliados de la oportunidad ven el desglose de la oferta
    original de los demas participantes. Es una decision economica: la toma quien ve costos."""
    o = _own(db, oid, p)
    if not p.sees_costs:
        raise HTTPException(403, "Trabajar con costos abiertos lo decide quien ve costos")
    if bool(o.ally_open_costs) != data.open_costs:
        o.ally_open_costs = data.open_costs
        _log(
            o,
            p.user.full_name,
            "Trabaja con costos abiertos: los aliados ven las ofertas originales de los demás" if data.open_costs else "Ya no trabaja con costos abiertos",
        )
        audit(db, p.tenant.id, p.user.id, "ally_open_costs", "opportunity", o.id, {"open_costs": data.open_costs}, ip=p.ip)
        db.commit()
    return svc.relations_out(db, o)
