"""Administracion -> Aliados (lado Crimson): empresas aliadas, sus usuarios, precios de aliado y mensualidad.

Todo con get_principal: un usuario aliado nunca entra aqui (su portal es /aliado). Permisos del modulo "aliados":
ver, crear, editar, usuarios, precios, mensualidad (admin "*"; contabilidad ve y factura la mensualidad).
Precios de aliado y recargo: ademas ven costos (el precio de aliado deja deducir el costo: 105 / 1,05).
Cada escritura queda en la bitacora (audit / audit_change con antes y despues).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import ALLY_USER_ROLES, Principal, get_principal, require
from ..core.ratelimit import admin_users_limiter
from ..models import (
    AllyCompany,
    AllyParticipation,
    AllyProductPrice,
    AuditLog,
    Customer,
    Invitation,
    Opportunity,
    Product,
    Project,
    Recurrence,
    Supplier,
    Tenant,
    TenantUser,
)
from ..models.allies import CONTACT_FUNCTIONS
from ..schemas.sales import DocumentIn, LineInSchema
from ..services import ally_access, ally_pricing
from ..services.archive import live
from ..services.bitacora import audit_change, jsonable
from ..services.documents import audit, today_fx
from ..services.totals import d

router = APIRouter(prefix="/allies", tags=["aliados"])

OPEN_OPP = ("nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion")
EDIT_FIELDS = ("name", "legal_name", "tax_id", "email", "phone", "contacts", "customer_id", "supplier_id", "markup_pct", "max_users", "notes")


# ---------- Ayudantes ----------
def norm_name(s: str | None) -> str:
    """Nombre normalizado para detectar duplicados: minusculas y sin espacios dobles."""
    return " ".join((s or "").lower().split())


def clean_tax(s: str | None) -> str | None:
    s = (s or "").strip()
    return s or None


def find_duplicate(db: Session, tenant_id: int, name: str | None, tax_id: str | None, exclude_id: int | None = None) -> AllyCompany | None:
    """Misma cedula (no vacia) o mismo nombre normalizado."""
    q = select(AllyCompany).where(AllyCompany.tenant_id == tenant_id)
    if exclude_id:
        q = q.where(AllyCompany.id != exclude_id)
    tax = clean_tax(tax_id)
    if tax:
        hit = db.scalar(q.where(AllyCompany.tax_id == tax).limit(1))
        if hit:
            return hit
    n = norm_name(name)
    if n:
        for c in db.scalars(q):
            if norm_name(c.name) == n:
                return c
    return None


def duplicate_response(c: AllyCompany, kind: str | None = None) -> JSONResponse:
    existing = {"id": c.id, "name": c.name}
    if kind:
        existing["kind"] = kind
    return JSONResponse(status_code=409, content={"detail": f"Ya existe el aliado «{c.name}»", "existing": existing})


def company(db: Session, cid: int, p: Principal) -> AllyCompany:
    c = db.get(AllyCompany, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Empresa aliada no encontrada")
    return c


def _ref(db: Session, model, rid: int | None, p: Principal, msg: str):
    if rid is None:
        return None
    obj = db.get(model, rid)
    if not obj or obj.tenant_id != p.tenant.id:
        raise HTTPException(404, msg)
    return obj


def _snapshot(c: AllyCompany) -> dict:
    keys = (*EDIT_FIELDS, "active", "suspended_at", "suspended_reason", "plan_amount", "plan_currency", "plan_mode", "plan_since", "recurrence_id")
    return {k: jsonable(getattr(c, k)) for k in keys}


def common_counts(db: Session, tenant_id: int, ids: list[int]) -> dict[int, dict]:
    """Proyectos en comun y oportunidades abiertas en comun por empresa (dos consultas para toda la lista)."""
    out = {i: {"proyectos": 0, "oportunidades": 0} for i in ids}
    if not ids:
        return out
    for cid, n in db.execute(
        select(AllyParticipation.ally_company_id, func.count(func.distinct(AllyParticipation.project_id)))
        .join(Project, Project.id == AllyParticipation.project_id)
        .where(AllyParticipation.tenant_id == tenant_id, AllyParticipation.ally_company_id.in_(ids), live(Project))
        .group_by(AllyParticipation.ally_company_id)
    ):
        out[cid]["proyectos"] = n
    for cid, n in db.execute(
        select(AllyParticipation.ally_company_id, func.count(func.distinct(AllyParticipation.opportunity_id)))
        .join(Opportunity, Opportunity.id == AllyParticipation.opportunity_id)
        .where(AllyParticipation.tenant_id == tenant_id, AllyParticipation.ally_company_id.in_(ids), Opportunity.status.in_(OPEN_OPP), live(Opportunity))
        .group_by(AllyParticipation.ally_company_id)
    ):
        out[cid]["oportunidades"] = n
    return out


def _plan_brief(db: Session, c: AllyCompany) -> dict:
    st = ally_access.plan_status(db, c) if c.customer_id else {"estado": "al_dia", "vencido": None}
    return {"amount": c.plan_amount, "currency": c.plan_currency, "mode": c.plan_mode, "al_dia": st["estado"] == "al_dia", "activo": bool(c.recurrence_id)}


def list_row(db: Session, c: AllyCompany, counts: dict) -> dict:
    st = ally_access.seats(db, c)
    return {
        "id": c.id,
        "name": c.name,
        "tax_id": c.tax_id,
        "email": c.email,
        "phone": c.phone,
        "state": c.state,
        "suspended_reason": c.suspended_reason,
        "users_active": st["active"],
        "seats_used": st["used"],
        "seats_max": c.max_users,
        "proyectos_en_comun": counts.get("proyectos", 0),
        "oportunidades_abiertas": counts.get("oportunidades", 0),
        "plan": _plan_brief(db, c),
    }


def detail_out(db: Session, c: AllyCompany, p: Principal) -> dict:
    cust = db.get(Customer, c.customer_id) if c.customer_id else None
    sup = db.get(Supplier, c.supplier_id) if c.supplier_id else None
    parts = db.scalars(
        select(AllyParticipation)
        .where(AllyParticipation.tenant_id == p.tenant.id, AllyParticipation.ally_company_id == c.id)
        .order_by(AllyParticipation.id.desc())
    ).all()
    opps = {o.id: o for o in db.scalars(select(Opportunity).where(Opportunity.id.in_({a.opportunity_id for a in parts})))} if parts else {}
    prjs = {x.id: x for x in db.scalars(select(Project).where(Project.id.in_({a.project_id for a in parts if a.project_id})))} if parts else {}
    counts = common_counts(db, p.tenant.id, [c.id]).get(c.id, {})
    out = list_row(db, c, counts)

    def ve_opp(o: Opportunity | None) -> bool:
        # el titulo y el estado de una oportunidad son del embudo: solo quien lo ve, y el vendedor sin "ver_todo" solo
        # las suyas (contabilidad ve Aliados para la mensualidad, no el embudo de los demas)
        if o is None or not p.can("crm_pipeline", "ver"):
            return False
        return p.can("crm_pipeline", "ver_todo") or o.owner_id == p.user.id

    out.update(
        legal_name=c.legal_name,
        contacts=c.contacts or {},
        active=c.active,
        suspended_at=c.suspended_at,
        customer_id=c.customer_id,
        customer={"id": cust.id, "name": cust.name} if cust else None,
        supplier_id=c.supplier_id,
        supplier={"id": sup.id, "name": sup.name} if sup else None,
        max_users=c.max_users,
        notes=c.notes,
        created_at=c.created_at,
        # el recargo es dato de costos: solo quien ve costos
        markup_pct=c.markup_pct if p.sees_costs else None,
        participations=[
            {
                "id": a.id,
                "role": a.role,
                "visible_to_ally": a.visible_to_ally,
                "opportunity": {"id": o.id, "number": o.number, "title": o.title, "status": o.status} if ve_opp(o := opps.get(a.opportunity_id)) else None,
                "project": {"id": pr.id, "number": pr.number, "status": pr.status} if (pr := prjs.get(a.project_id)) else None,
            }
            for a in parts
        ],
    )
    return out


# ---------- Lista y opciones ----------
@router.get("")
def allies_list(
    q: str = "",
    estado: str = Query("activos", pattern="^(activos|suspendidos|inactivos|todos)$"),
    p: Principal = Depends(require("aliados", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AllyCompany.name.ilike(like), AllyCompany.tax_id.ilike(like), AllyCompany.email.ilike(like)))
    if estado == "activos":
        stmt = stmt.where(AllyCompany.active.is_(True), AllyCompany.suspended_at.is_(None))
    elif estado == "suspendidos":
        stmt = stmt.where(AllyCompany.active.is_(True), AllyCompany.suspended_at.is_not(None))
    elif estado == "inactivos":
        stmt = stmt.where(AllyCompany.active.is_(False))
    rows = db.scalars(stmt.order_by(AllyCompany.name).limit(300)).all()
    counts = common_counts(db, p.tenant.id, [c.id for c in rows])
    return [list_row(db, c, counts.get(c.id, {})) for c in rows]


@router.get("/options")
def allies_options(q: str = "", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Selector de empresa aliada (OT, ticket, contrato, reglas de comision): activas y sin suspender, max. 20.
    Soporte: quien crea tickets y ve toda la mesa (el tecnico interno solo atiende los suyos y no asigna empresas)."""
    puede = (
        p.can("crm_pipeline", "ver")
        or p.can("field", "asignar")
        or (p.can("support_desk", "crear") and p.can("support_desk", "ver_todo"))
        or p.can("commissions", "configurar")
        or p.can("aliados", "ver")
    )
    if not puede:
        raise HTTPException(403, "Sin permiso: aliados")
    stmt = select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, AllyCompany.active.is_(True), AllyCompany.suspended_at.is_(None))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AllyCompany.name.ilike(like), AllyCompany.tax_id.ilike(like)))
    return [{"id": c.id, "name": c.name, "tax_id": c.tax_id} for c in db.scalars(stmt.order_by(AllyCompany.name).limit(20))]


# ---------- Ajustes de aliados (antes de /{cid}) ----------
ASSETS_SCOPES = ("proyectos", "clientes")


PLAN_MODES = ("fija", "por_usuario")


def plan_defaults(t) -> dict:
    """Mensualidad por defecto de los planes nuevos (plan 1.4): fija por empresa mientras Andres no fije otra cosa
    (sesion 0.11, pregunta 8). El monto queda vacio hasta que se defina: nunca se inventa un precio."""
    st = t.settings or {}
    amount = st.get("ally_plan_default_amount")
    mode = st.get("ally_plan_default_mode")
    return {
        "amount": None if amount in (None, "") else d(amount),
        "currency": st.get("ally_plan_default_currency") or "CRC",
        "mode": mode if mode in PLAN_MODES else "fija",
    }


@router.get("/config")
def config_get(p: Principal = Depends(require("aliados", "ver")), db: Session = Depends(get_db)):
    st = p.tenant.settings or {}
    pid = st.get("ally_plan_product_id")
    prod = db.get(Product, pid) if pid else None
    prod = prod if prod is not None and prod.tenant_id == p.tenant.id else None
    return {
        "markup_pct": ally_pricing.tenant_markup(p.tenant) if p.sees_costs else None,
        "plan_product_id": pid,
        "plan_product": {"id": prod.id, "code": prod.code, "name": prod.name, "cabys_code": prod.cabys_code} if prod else None,
        "plan_default": plan_defaults(p.tenant),
        "assets_scope": st.get("ally_assets_scope", "proyectos"),
    }


class ConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markup_pct: Decimal | None = Field(None, gt=0, le=100)  # 0 % seria vender al costo exacto
    plan_product_id: int | None = None
    assets_scope: str | None = Field(None, pattern="^(proyectos|clientes)$")
    # mensualidad por defecto de los planes nuevos (plan 1.4); null en el monto = sin definir
    plan_default_amount: Decimal | None = Field(None, ge=0)
    plan_default_currency: str | None = Field(None, pattern="^(CRC|USD)$")
    plan_default_mode: str | None = Field(None, pattern="^(fija|por_usuario)$")


@router.put("/config")
def config_put(data: ConfigIn, p: Principal = Depends(require("aliados", "ver")), db: Session = Depends(get_db)):
    sent = data.model_fields_set
    if sent & {"markup_pct", "assets_scope"} and not (p.can("aliados", "precios") and p.sees_costs):
        raise HTTPException(403, "El recargo y el alcance de activos los cambia administración")
    plan_keys = {"plan_product_id", "plan_default_amount", "plan_default_currency", "plan_default_mode"}
    if sent & plan_keys and not p.can("aliados", "mensualidad"):
        raise HTTPException(403, "Sin permiso: aliados.mensualidad")
    st = dict(p.tenant.settings or {})
    llaves = ("ally_markup_pct", "ally_plan_product_id", "ally_assets_scope", "ally_plan_default_amount", "ally_plan_default_currency", "ally_plan_default_mode")
    antes = {k: st.get(k) for k in llaves}
    if "markup_pct" in sent:
        st["ally_markup_pct"] = None if data.markup_pct is None else str(data.markup_pct)
    if "assets_scope" in sent and data.assets_scope:
        st["ally_assets_scope"] = data.assets_scope
    if "plan_product_id" in sent:
        if data.plan_product_id is not None:
            _plan_product(db, p, data.plan_product_id)
        st["ally_plan_product_id"] = data.plan_product_id
    if "plan_default_amount" in sent:
        st["ally_plan_default_amount"] = None if data.plan_default_amount is None else str(data.plan_default_amount)
    if "plan_default_currency" in sent and data.plan_default_currency:
        st["ally_plan_default_currency"] = data.plan_default_currency
    if "plan_default_mode" in sent and data.plan_default_mode:
        st["ally_plan_default_mode"] = data.plan_default_mode
    p.tenant.settings = st  # se mezcla sin pisar otras llaves
    audit_change(db, p, "update", "tenant_settings", p.tenant.id, antes, {k: st.get(k) for k in antes}, extra={"seccion": "aliados"})
    db.commit()
    return config_get(p, db)


# ---------- Precios de aliado ----------
def _prices_guard(p: Principal) -> None:
    if not (p.can("aliados", "precios") and p.sees_costs):
        raise HTTPException(403, "Los precios de aliado los ve quien ve costos")


def _price_out(r: AllyProductPrice, prod: Product | None, comp: AllyCompany | None) -> dict:
    return {
        "id": r.id,
        "product_id": r.product_id,
        "product": {"id": prod.id, "code": prod.code, "name": prod.name, "currency": prod.currency, "price": prod.price} if prod else None,
        "ally_company_id": r.ally_company_id,
        "ally_company": comp.name if comp else None,
        "price": r.price,
        "currency": r.currency,
        "note": r.note,
        "updated_at": r.updated_at,
    }


@router.get("/prices")
def prices(
    ally_company_id: int | None = None,
    product_id: int | None = None,
    q: str = "",
    general: bool = False,
    p: Principal = Depends(require("aliados", "precios")),
    db: Session = Depends(get_db),
):
    _prices_guard(p)
    stmt = select(AllyProductPrice).where(AllyProductPrice.tenant_id == p.tenant.id)
    if ally_company_id is not None:
        stmt = stmt.where(AllyProductPrice.ally_company_id == ally_company_id)
    elif general:
        stmt = stmt.where(AllyProductPrice.ally_company_id.is_(None))
    if product_id is not None:
        stmt = stmt.where(AllyProductPrice.product_id == product_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            AllyProductPrice.product_id.in_(select(Product.id).where(Product.tenant_id == p.tenant.id, or_(Product.name.ilike(like), Product.code.ilike(like))))
        )
    rows = db.scalars(stmt.order_by(AllyProductPrice.id.desc()).limit(500)).all()
    prods = {x.id: x for x in db.scalars(select(Product).where(Product.id.in_({r.product_id for r in rows})))} if rows else {}
    comps = {x.id: x for x in db.scalars(select(AllyCompany).where(AllyCompany.id.in_({r.ally_company_id for r in rows if r.ally_company_id})))} if rows else {}
    return [_price_out(r, prods.get(r.product_id), comps.get(r.ally_company_id)) for r in rows]


class PriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    ally_company_id: int | None = None
    price: Decimal = Field(ge=0)
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    note: str | None = Field(None, max_length=200)


@router.put("/prices")
def prices_put(data: list[PriceIn], p: Principal = Depends(require("aliados", "precios")), db: Session = Depends(get_db)):
    """Upsert por lote: un precio por producto y alcance (general o empresa)."""
    _prices_guard(p)
    if len(data) > 200:
        raise HTTPException(422, "Máximo 200 precios por vez")
    out = []
    for it in data:
        prod = _ref(db, Product, it.product_id, p, "Producto no encontrado")
        comp = _ref(db, AllyCompany, it.ally_company_id, p, "Empresa aliada no encontrada")
        q = select(AllyProductPrice).where(AllyProductPrice.tenant_id == p.tenant.id, AllyProductPrice.product_id == prod.id)
        q = q.where(AllyProductPrice.ally_company_id == it.ally_company_id) if it.ally_company_id else q.where(AllyProductPrice.ally_company_id.is_(None))
        r = db.scalar(q.limit(1))
        antes = {"price": jsonable(r.price), "currency": r.currency, "note": r.note} if r else {}
        if r is None:
            r = AllyProductPrice(tenant_id=p.tenant.id, product_id=prod.id, ally_company_id=it.ally_company_id)
            db.add(r)
        r.price, r.currency, r.note, r.updated_by = it.price, it.currency or prod.currency, it.note, p.user.id
        db.flush()
        audit_change(
            db,
            p,
            "update",
            "ally_product_price",
            r.id,
            antes,
            {"price": jsonable(r.price), "currency": r.currency, "note": r.note},
            company=comp.name if comp else "General",
        )
        out.append(_price_out(r, prod, comp))
    db.commit()
    return out


@router.delete("/prices/{rid}", status_code=204)
def prices_delete(rid: int, p: Principal = Depends(require("aliados", "precios")), db: Session = Depends(get_db)):
    r = db.get(AllyProductPrice, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Precio no encontrado")
    audit(db, p.tenant.id, p.user.id, "delete", "ally_product_price", r.id, {"producto": r.product_id, "empresa": r.ally_company_id}, ip=p.ip)
    db.delete(r)
    db.commit()


@router.get("/price-preview")
def price_preview(product_id: int, ally_company_id: int | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Costo · recargo · precio aliado · publico de un producto (solo quien ve costos)."""
    if not p.sees_costs:
        raise HTTPException(403, "Solo quien ve costos")
    prod = _ref(db, Product, product_id, p, "Producto no encontrado")
    comp = _ref(db, AllyCompany, ally_company_id, p, "Empresa aliada no encontrada")
    fx = today_fx(db, "USD")[0]
    precio, fuente, estado = ally_pricing.ally_price(db, p.tenant, prod, comp, fx=fx)
    return {
        "product_id": prod.id,
        "cost": prod.cost,
        "cost_currency": prod.cost_currency,
        "markup_pct": ally_pricing.markup_for(p.tenant, comp),
        "fuente": fuente,
        "precio_aliado": precio,
        "precio_publico": prod.price,
        "currency": prod.currency,
        "estado": estado,
    }


# ---------- Alta ----------
class ContactIn(BaseModel):
    name: str | None = Field(None, max_length=160)
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)


def contacts_clean(v: dict | None) -> dict:
    out = {}
    for k, c in (v or {}).items():
        c = c.model_dump() if isinstance(c, BaseModel) else dict(c or {})
        if any(c.get(x) for x in ("name", "email", "phone")):
            out[k] = {x: (c.get(x) or None) for x in ("name", "email", "phone")}
    return out


class CompanyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(None, min_length=2, max_length=160)
    legal_name: str | None = Field(None, max_length=200)
    tax_id: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    contacts: dict[str, ContactIn] | None = None
    customer_id: int | None = None
    supplier_id: int | None = None
    markup_pct: Decimal | None = Field(None, gt=0, le=100)  # 0 % seria vender al costo exacto
    max_users: int | None = Field(None, ge=1, le=10000)
    notes: str | None = Field(None, max_length=4000)

    @field_validator("contacts")
    @classmethod
    def _funciones(cls, v):
        malas = [k for k in (v or {}) if k not in CONTACT_FUNCTIONS]
        if malas:
            raise ValueError(f"Función de contacto desconocida: {', '.join(malas)}")
        return v


@router.post("", status_code=201)
def ally_create(data: CompanyIn, p: Principal = Depends(require("aliados", "crear")), db: Session = Depends(get_db)):
    if data.markup_pct is not None and not (p.can("aliados", "precios") and p.sees_costs):
        raise HTTPException(403, "El recargo del aliado lo pone quien ve costos")
    cust = _ref(db, Customer, data.customer_id, p, "Cliente no encontrado")
    sup = _ref(db, Supplier, data.supplier_id, p, "Proveedor no encontrado")
    name, tax, email, phone = data.name, data.tax_id, str(data.email).lower() if data.email else None, data.phone
    src = cust or sup
    if not name and src is not None:  # "Crear desde un cliente/proveedor": copia sus datos
        name = src.name[:160]
        tax = tax or (cust.id_number if cust else sup.tax_id)
        email = email or ((src.email or "").lower() or None)
        phone = phone or src.phone
    if not name:
        raise HTTPException(422, "Escribí el nombre del aliado o elegí el cliente o proveedor de donde sale")
    tax = clean_tax(tax)
    dup = find_duplicate(db, p.tenant.id, name, tax)
    if dup:
        return duplicate_response(dup)
    c = AllyCompany(
        tenant_id=p.tenant.id,
        name=name,
        legal_name=data.legal_name,
        tax_id=tax,
        email=email,
        phone=phone,
        contacts=contacts_clean(data.contacts),
        customer_id=cust.id if cust else None,
        supplier_id=sup.id if sup else None,
        markup_pct=data.markup_pct,
        max_users=data.max_users,
        notes=data.notes,
        active=True,
        created_by=p.user.id,
    )
    db.add(c)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "ally_company", c.id, {"name": c.name}, ip=p.ip)
    db.commit()
    return detail_out(db, c, p)


@router.get("/{cid}")
def ally_get(cid: int, p: Principal = Depends(require("aliados", "ver")), db: Session = Depends(get_db)):
    return detail_out(db, company(db, cid, p), p)


@router.put("/{cid}")
def ally_update(cid: int, data: CompanyIn, p: Principal = Depends(require("aliados", "editar")), db: Session = Depends(get_db)):
    c = company(db, cid, p)
    sent = data.model_fields_set
    if "markup_pct" in sent and not (p.can("aliados", "precios") and p.sees_costs):
        raise HTTPException(403, "El recargo del aliado lo pone quien ve costos")
    if "name" in sent and not data.name:
        raise HTTPException(422, "El nombre no se puede borrar")
    if "customer_id" in sent:
        _ref(db, Customer, data.customer_id, p, "Cliente no encontrado")
    if "supplier_id" in sent:
        _ref(db, Supplier, data.supplier_id, p, "Proveedor no encontrado")
    nuevo_nombre = data.name if "name" in sent else c.name
    nueva_tax = clean_tax(data.tax_id) if "tax_id" in sent else c.tax_id
    if "name" in sent or "tax_id" in sent:
        dup = find_duplicate(db, p.tenant.id, nuevo_nombre, nueva_tax, exclude_id=c.id)
        if dup:
            return duplicate_response(dup)
    antes = _snapshot(c)
    for k in EDIT_FIELDS:
        if k not in sent:
            continue
        v = getattr(data, k)
        if k == "contacts":
            v = contacts_clean(v)
        elif k == "tax_id":
            v = nueva_tax
        elif k == "email":
            v = str(v).lower() if v else None
        setattr(c, k, v)
    audit_change(db, p, "update", "ally_company", c.id, antes, _snapshot(c), company=c.name)
    db.commit()
    return detail_out(db, c, p)


class SuspendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=240)


@router.post("/{cid}/suspend")
def ally_suspend(cid: int, data: SuspendIn, p: Principal = Depends(require("aliados", "editar")), db: Session = Depends(get_db)):
    c = company(db, cid, p)
    antes = _snapshot(c)
    c.suspended_at, c.suspended_reason = datetime.now(UTC), data.reason.strip()
    audit_change(db, p, "suspend", "ally_company", c.id, antes, _snapshot(c), company=c.name, always=True)
    db.commit()
    return detail_out(db, c, p)


@router.post("/{cid}/reactivate")
def ally_reactivate(cid: int, p: Principal = Depends(require("aliados", "editar")), db: Session = Depends(get_db)):
    c = company(db, cid, p)
    antes = _snapshot(c)
    c.suspended_at, c.suspended_reason = None, None
    audit_change(db, p, "reactivate", "ally_company", c.id, antes, _snapshot(c), company=c.name, always=True)
    db.commit()
    return detail_out(db, c, p)


@router.post("/{cid}/deactivate")
def ally_deactivate(cid: int, p: Principal = Depends(require("aliados", "editar")), db: Session = Depends(get_db)):
    c = company(db, cid, p)
    antes = _snapshot(c)
    c.active = False  # sus usuarios reciben 403 en el portal desde el siguiente request
    audit_change(db, p, "deactivate", "ally_company", c.id, antes, _snapshot(c), company=c.name, always=True)
    db.commit()
    return detail_out(db, c, p)


@router.post("/{cid}/activate")
def ally_activate(cid: int, p: Principal = Depends(require("aliados", "editar")), db: Session = Depends(get_db)):
    c = company(db, cid, p)
    antes = _snapshot(c)
    c.active = True
    audit_change(db, p, "activate", "ally_company", c.id, antes, _snapshot(c), company=c.name, always=True)
    db.commit()
    return detail_out(db, c, p)


# ---------- Usuarios del aliado ----------
@router.get("/{cid}/users")
def ally_users(cid: int, p: Principal = Depends(require("aliados", "ver")), db: Session = Depends(get_db)):
    return ally_access.users_out(db, company(db, cid, p))


class AllyInviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    role: str = Field("tecnico_aliado", pattern="^(" + "|".join(ALLY_USER_ROLES) + ")$")
    name: str | None = Field(None, max_length=120)


@router.post("/{cid}/invitations", status_code=201)
def ally_invite(cid: int, data: AllyInviteIn, p: Principal = Depends(require("aliados", "usuarios")), db: Session = Depends(get_db)):
    admin_users_limiter.hit(f"{p.tenant.id}:{p.user.id}")  # cada invitacion es un correo: mismo tope que las internas
    c = company(db, cid, p)
    inv = ally_access.invite_ally_user(db, p.tenant, c, str(data.email), data.role, p.user.id, name=data.name)
    audit(db, p.tenant.id, p.user.id, "invite", "ally_company", c.id, {"email": str(data.email).lower(), "role": data.role}, ip=p.ip)
    db.commit()
    return inv


@router.post("/{cid}/invitations/{iid}/revoke")
def ally_invite_revoke(cid: int, iid: int, p: Principal = Depends(require("aliados", "usuarios")), db: Session = Depends(get_db)):
    c = company(db, cid, p)
    inv = db.get(Invitation, iid)
    if not inv or inv.tenant_id != p.tenant.id or inv.ally_company_id != c.id:
        raise HTTPException(404, "Invitación no encontrada")
    if inv.accepted_at:
        raise HTTPException(409, "Ya fue aceptada: desactivá al usuario")
    if not inv.revoked_at:
        now = datetime.now(UTC)
        inv.revoked_at, inv.revoked_by = now, p.user.id
        inv.expires_at = min(ally_access._aware(inv.expires_at), now)
        audit(db, p.tenant.id, p.user.id, "invite_revoke", "invitation", inv.id, {"email": inv.email}, ip=p.ip)
        db.commit()
    return {"ok": True}


class AllyMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = Field(None, pattern="^(" + "|".join(ALLY_USER_ROLES) + ")$")
    active: bool | None = None


def set_member_active(db: Session, p: Principal, c: AllyCompany, m: TenantUser, active: bool) -> None:
    """Activar/desactivar un usuario aliado: cupos, sesiones y cantidad de la mensualidad por usuario."""
    from ..services.accounts import revoke_refresh_tokens

    if active and not m.active:
        st = ally_access.seats(db, c)
        if c.max_users is not None and st["used"] >= c.max_users:
            raise HTTPException(409, f"Su plan permite {c.max_users} usuarios")
    m.active = active
    if not active:
        revoke_refresh_tokens(db, m.user_id, p.tenant.id)
    ally_access.refresh_plan_quantity(db, c)


@router.patch("/{cid}/users/{uid}")
def ally_member(cid: int, uid: int, data: AllyMemberIn, p: Principal = Depends(require("aliados", "usuarios")), db: Session = Depends(get_db)):
    """Rol (siempre de aliado) y activo. Nunca un rol interno ni otra empresa."""
    c = company(db, cid, p)
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid, TenantUser.ally_company_id == c.id))
    if not m:
        raise HTTPException(404, "Usuario no encontrado")
    antes = {"role": m.role_code, "active": m.active}
    if data.role:
        m.role_code = data.role
    if data.active is not None and data.active != m.active:
        set_member_active(db, p, c, m, data.active)
    audit_change(db, p, "permissions", "user", uid, antes, {"role": m.role_code, "active": m.active}, company=c.name)
    db.commit()
    return ally_access.users_out(db, c)


# ---------- "Ver como este aliado" ----------
class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int | None = None


@router.post("/{cid}/preview")
def ally_preview(cid: int, data: PreviewIn | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Solo el administrador (rol real). Deja constancia; desde ahi el portal manda X-View-As-Ally (solo lectura)."""
    if p.role != "admin":
        raise HTTPException(403, "La vista previa de aliados es solo para el administrador")
    c = company(db, cid, p)
    uid = data.user_id if data else None
    if uid is not None and not db.scalar(
        select(TenantUser.id).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid, TenantUser.ally_company_id == c.id)
    ):
        raise HTTPException(422, "Esa persona no es de esa empresa aliada")
    db.add(
        AuditLog(
            tenant_id=p.tenant.id,
            user_id=p.user.id,
            at=datetime.now(UTC),
            ip=p.ip,
            action="preview_ally",
            entity="ally_company",
            entity_id=c.id,
            diff={"user_id": uid},
        )
    )
    db.commit()
    users = db.scalars(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.ally_company_id == c.id)).all()
    from .ally_portal import ganancias_disponibles

    return {
        "company": {
            "id": c.id,
            "name": c.name,
            "state": c.state,
            "suspended_reason": c.suspended_reason if c.suspended_at else None,
            "features": {"ganancias": ganancias_disponibles()},
        },
        "users": [{"id": m.user.id, "name": m.user.full_name, "role": m.role_code, "active": m.active} for m in users],
    }


# ---------- Mensualidad ----------
def _plan_product(db: Session, p: Principal, pid: int | None) -> Product:
    prod = db.get(Product, pid) if pid else None
    if not prod or prod.tenant_id != p.tenant.id or prod.item_type != "servicio" or not prod.cabys_code:
        raise HTTPException(422, "Elegí el servicio «Mensualidad de la plataforma» con su CABYS (Productos y servicios)")
    return prod


def plan_out(db: Session, c: AllyCompany) -> dict:
    r = db.get(Recurrence, c.recurrence_id) if c.recurrence_id else None
    lines = ((r.template or {}).get("lines") or []) if r else []
    cust = db.get(Customer, c.customer_id) if c.customer_id else None
    st = ally_access.plan_status(db, c)
    return {
        "plan": {
            "amount": c.plan_amount,
            "currency": c.plan_currency,
            "mode": c.plan_mode,
            "quantity": d(lines[0].get("quantity")) if lines else None,
            "product_id": lines[0].get("product_id") if lines else None,
            "next_date": r.next_date,
            "auto_send": r.auto_send,
            "active": r.active,
            "since": c.plan_since,
            "recurrence_id": r.id,
            # plan 1.4: la primera factura la revisa una persona; despues salen solas
            "first_run_requires_approval": r.first_run_requires_approval,
            "last_send_status": r.last_send_status,
            "last_send_note": r.last_send_note,
            "last_invoice_id": r.last_invoice_id,
        }
        if r
        else None,
        "customer": {"id": cust.id, "name": cust.name} if cust else None,
        # con que arranca el formulario de un plan nuevo (Ajustes -> Aliados)
        "defaults": plan_defaults(db.get(Tenant, c.tenant_id)),
        **st,
        "facturas": [
            {
                "id": i.id,
                "number": i.number,
                "issue_date": i.issue_date,
                "due_date": i.due_date,
                "total": i.total,
                "balance": i.balance,
                "status": i.status,
                "currency": i.currency,
                "sent_at": i.sent_at,
            }
            for i in ally_access.plan_invoices(db, c)
        ],
    }


@router.get("/{cid}/plan")
def plan_get(cid: int, p: Principal = Depends(require("aliados", "mensualidad")), db: Session = Depends(get_db)):
    return plan_out(db, company(db, cid, p))


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(ge=0)
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    mode: str = Field("fija", pattern="^(fija|por_usuario)$")
    next_date: date
    auto_send: bool = True
    active: bool = True
    product_id: int | None = None
    crear_cliente: bool = False
    # la primera factura espera "Aprobar y enviar" (por defecto en planes nuevos); None al actualizar = no cambia
    first_run_requires_approval: bool | None = None


@router.put("/{cid}/plan")
def plan_put(cid: int, data: PlanIn, p: Principal = Depends(require("aliados", "mensualidad")), db: Session = Depends(get_db)):
    """Crea o actualiza la recurrencia que factura la mensualidad. El worker la genera y, con auto_send, la envia con
    su PDF y enlace de pago; la primera de cada plan espera "Aprobar y enviar" en Aprobaciones (plan 1.4). Nunca cobra
    sola con tarjeta ni suspende al aliado."""
    c = company(db, cid, p)
    antes = _snapshot(c)
    if not c.customer_id and not data.crear_cliente:
        raise HTTPException(422, "Ligá o creá el cliente de facturación del aliado")
    # el servicio se valida antes de crear nada (un 422 no deja un cliente a medias)
    prod = _plan_product(db, p, data.product_id or (p.tenant.settings or {}).get("ally_plan_product_id"))
    if not c.customer_id:
        cust = Customer(tenant_id=p.tenant.id, id_type="juridica", id_number=c.tax_id, name=(c.legal_name or c.name)[:200], email=c.email, phone=c.phone)
        db.add(cust)
        db.flush()
        audit(db, p.tenant.id, p.user.id, "create", "customer", cust.id, {"desde": "empresa aliada", "aliado": c.id}, ip=p.ip)
        c.customer_id = cust.id
    c.plan_mode = data.mode
    qty = 1 if data.mode == "fija" else max(len(ally_access.active_members(db, c)), 1)
    template = DocumentIn(
        currency=data.currency,
        external_order=ally_access.PLAN_ORDER,
        lines=[LineInSchema(product_id=prod.id, name=f"Acceso a la plataforma · {c.name}"[:200], quantity=Decimal(qty), unit_price=data.amount)],
    ).model_dump(mode="json", exclude={"customer_id"})
    DocumentIn(**template)  # validar antes de guardar
    from .ops import needs_rearm, schedule_of, send_flags, send_terms, set_send_approval

    r = db.get(Recurrence, c.recurrence_id) if c.recurrence_id else None
    nueva = r is None or r.tenant_id != p.tenant.id
    if nueva:
        r = Recurrence(tenant_id=p.tenant.id, kind="factura", frequency="mensual", runs=0, first_run_requires_approval=True, auto_send=True)
        db.add(r)
    envio_antes = {**send_flags(r), "terms": None if nueva else send_terms(db, p.tenant.id, r.customer_id, r.template)}
    agenda = None if nueva else schedule_of(r)
    r.name = f"Mensualidad aliado · {c.name}"[:160]
    r.customer_id = c.customer_id
    r.template = template
    r.frequency = "mensual"
    r.next_date = data.next_date
    r.auto_send = data.auto_send
    r.active = data.active
    r.auto_charge = False  # nunca se cobra solo
    # otro cliente, otra moneda u otro monto, o quien no envia facturas adelanto el cobro o volvio a encender el envio:
    # la proxima vuelve a esperar "Aprobar y enviar"; quitar la revision es de quien envia facturas (contabilidad tiene
    # aliados.mensualidad pero no sales.enviar)
    terms = send_terms(db, p.tenant.id, r.customer_id, r.template)
    set_send_approval(p, r, data.first_run_requires_approval, rearm=not nueva and needs_rearm(p, terms != envio_antes["terms"], agenda, r))
    db.flush()
    c.recurrence_id = r.id
    c.plan_amount, c.plan_currency = data.amount, data.currency
    c.plan_since = c.plan_since or data.next_date
    envio = {"antes": {k: v for k, v in envio_antes.items() if k != "terms"}, "despues": send_flags(r)}
    audit_change(db, p, "plan", "ally_company", c.id, antes, _snapshot(c), company=c.name, always=True, extra={"envio": envio})
    db.commit()
    return plan_out(db, c)
