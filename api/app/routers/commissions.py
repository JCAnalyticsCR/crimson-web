"""Comisiones, ganancias de aliados y "Mis numeros" (economia). El motor vive en services/commissions.py.

Alcance (spec economia §6 + aliado_final R7): los roles aliados NO entran aqui (get_principal los rechaza); lo que
recibe un aliado se le muestra por /aliado/ganancias*. Sin commissions.ver_todo (vendedor) cada quien ve lo suyo y
las reglas que le aplican. Costos y base de utilidad solo para quien ve costos (catalog.costos).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import ALLY_USER_ROLES, CLIENT_ROLES, Principal, require
from ..models import (
    AllyCompany,
    AllyParticipation,
    Commission,
    CommissionRule,
    Expense,
    Invoice,
    Opportunity,
    Project,
    Quote,
    Tenant,
    TenantUser,
    User,
)
from ..models.support import SOLUTIONS, TRANSITIONS
from ..services import bitacora
from ..services import commissions as svc
from ..services.documents import audit, local_date
from ..services.totals import d, net_sale

router = APIRouter(tags=["comisiones"])

STATE_LABEL = {"en_espera": "en espera", "pendiente": "pendiente", "aprobada": "aprobada", "pagada": "pagada", "anulada": "anulada"}


def ally_scope(p: Principal) -> int | None:
    """Empresa aliada del que mira. Los aliados no llegan a este router (R7): queda como defensa inerte."""
    return p.ally_company_id


def _today_cr() -> date:
    return local_date(datetime.now(UTC)) or date.today()


def _not_ally(p: Principal) -> None:
    if ally_scope(p) is not None:
        raise HTTPException(403, "Usá Mis ganancias")


# ---------- 1. Movimientos ----------
@router.get("/commissions")
def commissions(
    status: str | None = None,
    user_id: int | None = None,
    ally_company_id: int | None = None,
    beneficiary: str | None = Query(None, pattern="^(vendedor|aliado)$"),
    kind: str | None = Query(None, pattern="^(comision|bono|pago_acordado|ajuste)$"),
    project_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    q: str | None = Query(None, max_length=60),
    limit: int = Query(300, ge=1, le=300),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require("commissions", "ver")),
    db: Session = Depends(get_db),
):
    w = [Commission.tenant_id == p.tenant.id]
    cid = ally_scope(p)
    if cid is not None:
        if ally_company_id and ally_company_id != cid:
            raise HTTPException(403, "Solo los movimientos de su empresa")
        w.append(Commission.ally_company_id == cid)
    elif not p.can("commissions", "ver_todo"):
        w += [Commission.user_id == p.user.id, Commission.beneficiary == "vendedor"]  # cada quien ve lo suyo
    else:
        if user_id:
            w.append(Commission.user_id == user_id)
        if ally_company_id:
            w.append(Commission.ally_company_id == ally_company_id)
        if beneficiary:
            w.append(Commission.beneficiary == beneficiary)
    if status:
        w.append(Commission.status == status)
    if kind:
        w.append(Commission.kind == kind)
    if project_id:
        w.append(Commission.project_id == project_id)
    if from_:
        w.append(Commission.earned_on >= from_)
    if to:
        w.append(Commission.earned_on <= to)
    if q and q.strip():
        like = f"%{q.strip()}%"
        w.append(
            or_(
                Commission.invoice_id.in_(select(Invoice.id).where(Invoice.tenant_id == p.tenant.id, Invoice.number.ilike(like))),
                Commission.quote_id.in_(select(Quote.id).where(Quote.tenant_id == p.tenant.id, Quote.number.ilike(like))),
                Commission.project_id.in_(select(Project.id).where(Project.tenant_id == p.tenant.id, Project.number.ilike(like))),
                Commission.description.ilike(like),
            )
        )
    # la pagina y los totales salen de SQL (OWASP API4): nunca se carga el libro entero del tenant en memoria
    total_rows = db.scalar(select(func.count()).select_from(Commission).where(*w)) or 0
    pagina = db.scalars(select(Commission).where(*w).order_by(Commission.earned_on.desc(), Commission.id.desc()).limit(limit).offset(offset)).all()
    cfg = svc.settings_of(p.tenant)
    names = svc.Names(db)
    rows = [svc.entry_out(db, c, p, cfg, names) for c in pagina]
    # totales en colones (falla H: antes sumaba dolares y colones como si fueran lo mismo); en_espera es una cantidad.
    # Sin costos ni aprobar, el pago acordado al aliado (su precio) no suma: tampoco se ve fila por fila
    tw = list(w)
    if svc.hides_payout(p):
        tw += [Commission.cost_request_id.is_(None), or_(Commission.kind.is_(None), Commission.kind != "pago_acordado")]
    agg = db.execute(
        select(
            Commission.status,
            Commission.currency,
            func.count(),
            func.coalesce(func.sum(Commission.amount), 0),
            func.coalesce(func.sum(Commission.amount_crc), 0),
            func.coalesce(func.sum(case((Commission.amount_crc.is_(None), Commission.amount), else_=0)), 0),
        )
        .where(*tw)
        .group_by(Commission.status, Commission.currency)
    ).all()
    totals = {"en_espera": 0, "pendiente": Decimal(0), "aprobada": Decimal(0), "pagada": Decimal(0)}
    by_cur: dict[str, dict] = {}
    for st, cur_code, n, monto, monto_crc, sin_crc in agg:
        if st == "en_espera":
            totals["en_espera"] += n
            continue
        if st not in ("pendiente", "aprobada", "pagada"):
            continue
        # filas viejas sin amount_crc: al cambio de hoy (como crc_of)
        totals[st] += d(monto_crc) + d(sin_crc) * svc._rate(db, cur_code)
        cur = by_cur.setdefault(cur_code, {"pendiente": Decimal(0), "aprobada": Decimal(0), "pagada": Decimal(0)})
        cur[st] += d(monto)
    return {
        "rows": rows,
        "totals": {k: (svc.q2(v) if isinstance(v, Decimal) else v) for k, v in totals.items()},
        "by_currency": {k: {s: svc.q2(v) for s, v in x.items()} for k, x in by_cur.items()},
        "total_rows": total_rows,
    }


# ---------- 2. Cambio de estado en lote ----------
class CommissionStatusIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    status: str = Field(pattern="^(pendiente|aprobada|pagada|anulada)$")
    paid_on: date | None = None
    paid_ref: str | None = Field(None, max_length=120)
    expense_id: int | None = None
    reason: str | None = Field(None, max_length=240)


def _why_not(c: Commission, nuevo: str) -> str | None:
    if c.status == nuevo:
        return f"Ya está {STATE_LABEL.get(nuevo, nuevo)}"
    if c.status == "pagada":
        return "Una comisión pagada no cambia de estado: se corrige con un ajuste"
    if c.status == "anulada":
        return "Una comisión anulada no cambia de estado"
    if nuevo not in TRANSITIONS.get(c.status, set()):
        if c.status == "en_espera":
            return "Está en espera de un dato: recalculala o anulala"
        return f"No pasa de {STATE_LABEL.get(c.status, c.status)} a {STATE_LABEL.get(nuevo, nuevo)}"
    return None


@router.patch("/commissions")
def commission_status(data: CommissionStatusIn, p: Principal = Depends(require("commissions", "aprobar")), db: Session = Depends(get_db)):
    if data.status == "anulada" and len((data.reason or "").strip()) < 3:
        raise HTTPException(422, "Indicá el motivo de la anulación")
    if data.expense_id:
        e = db.get(Expense, data.expense_id)
        if not e or e.tenant_id != p.tenant.id:
            raise HTTPException(404, "Gasto no encontrado")
    found = {c.id: c for c in db.scalars(select(Commission).where(Commission.tenant_id == p.tenant.id, Commission.id.in_(data.ids)))}
    now = datetime.now(UTC)
    n, omitidas = 0, []
    for cid in dict.fromkeys(data.ids):
        c = found.get(cid)
        if c is None:
            omitidas.append({"id": cid, "motivo": "No existe"})
            continue
        if not svc.can_touch(p, c):
            omitidas.append({"id": cid, "motivo": "No podés aprobar ni pagar tu propia comisión"})
            continue
        motivo = _why_not(c, data.status)
        if motivo:
            omitidas.append({"id": cid, "motivo": motivo})
            continue
        antes = c.status
        c.status = data.status
        if data.status == "aprobada":
            c.approved_by, c.approved_at = p.user.id, now
        elif data.status == "pendiente":
            c.approved_by, c.approved_at = None, None
        elif data.status == "pagada":
            c.paid_on = data.paid_on or _today_cr()
            c.paid_by, c.paid_ref = p.user.id, (data.paid_ref or "").strip() or None
            if data.expense_id:
                c.expense_id = data.expense_id
            if antes == "pendiente":
                c.approved_by, c.approved_at = p.user.id, now
        elif data.status == "anulada":
            c.void_reason = data.reason.strip()[:240]
            svc.mark_manual_void(c)  # el motor no la vuelve a devengar sola (convertir, crear el proyecto)
        if data.status != "pagada":
            c.paid_on = None  # una pagada no cambia de estado: aqui nunca se borra un pago real
        audit(db, p.tenant.id, p.user.id, "status", "commission", c.id, {"de": antes, "a": data.status, "ref": c.paid_ref, "motivo": c.void_reason}, ip=p.ip)
        n += 1
    if n == 0:
        raise HTTPException(409, omitidas[0]["motivo"] if len(omitidas) == 1 else "Ningún movimiento cambió: " + "; ".join({o["motivo"] for o in omitidas}))
    db.commit()
    return {"actualizadas": n, "omitidas": omitidas}


# ---------- 3-6. Reglas ----------
class RuleIn(BaseModel):
    beneficiary: str = Field("vendedor", pattern="^(vendedor|aliado)$")
    user_id: int | None = None
    ally_company_id: int | None = None
    participation_id: int | None = None
    kind: str = Field("comision", pattern="^(comision|bono_fin|bono_anticipo)$")
    name: str = Field("Comisión", max_length=120)
    base: str = Field("venta", pattern="^(venta|margen)$")
    percent: Decimal = Field(Decimal(0), ge=0, le=100)
    fixed_amount: Decimal | None = Field(None, ge=0)
    fixed_currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    trigger: str = Field("cobro", pattern="^(cobro|cotizacion_aceptada|proyecto_completado)$")
    deal_kind: str | None = Field(None, pattern="^(venta|proyecto)$")
    solution: str | None = Field(None, pattern="^(" + "|".join(SOLUTIONS) + ")$")
    committed_date: date | None = None
    early_days: int | None = Field(None, ge=0, le=365)
    valid_from: date | None = None
    valid_to: date | None = None
    active: bool = True
    notes: str | None = Field(None, max_length=2000)


def internal_member(db: Session, tenant_id: int, uid: int) -> TenantUser | None:
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == uid, TenantUser.active))
    if m is None or m.customer_id or m.ally_company_id or m.role_code in CLIENT_ROLES or m.role_code in ALLY_USER_ROLES or m.role_code == "soporte":
        return None
    return m


def _rule_values(db: Session, p: Principal, data: RuleIn, existing: CommissionRule | None = None) -> dict:
    """Validaciones de 4.2 y normalizacion identica al crear y al editar (la bitacora compara campo por campo).
    La persona se valida solo al elegirla: la regla de alguien que ya se desactivo se puede apagar, cerrar su vigencia
    o anotar sin que el 422 la deje viva."""
    v = data.model_dump()
    if data.beneficiary == "vendedor":
        if data.ally_company_id or data.participation_id:
            raise HTTPException(422, "Una regla de vendedor no lleva empresa aliada ni proyecto de aliado")
        nueva = existing is None or existing.user_id != data.user_id
        if data.user_id and nueva and internal_member(db, p.tenant.id, data.user_id) is None:
            raise HTTPException(422, "Esa persona no es un usuario activo del equipo")
    else:
        if data.user_id:
            raise HTTPException(422, "Una regla de aliado no lleva persona")
        if data.participation_id:
            part = db.get(AllyParticipation, data.participation_id)
            if not part or part.tenant_id != p.tenant.id:
                raise HTTPException(404, "Participación de aliado no encontrada")
            v["ally_company_id"] = part.ally_company_id
        elif data.ally_company_id:
            comp = db.get(AllyCompany, data.ally_company_id)
            if not comp or comp.tenant_id != p.tenant.id:
                raise HTTPException(404, "Empresa aliada no encontrada")
    fijo = data.fixed_amount is not None and data.fixed_amount > 0
    if (data.percent > 0) == fijo:
        raise HTTPException(422, "Indicá un porcentaje o un monto fijo (uno de los dos)")
    if data.kind == "bono_anticipo" and (not data.participation_id or not data.committed_date):
        raise HTTPException(422, "El bono por terminar antes se pacta en el acuerdo de un proyecto, con su fecha comprometida")
    if data.valid_from and data.valid_to and data.valid_to < data.valid_from:
        raise HTTPException(422, "La vigencia termina antes de empezar")
    if data.kind != "comision":
        v["trigger"] = "proyecto_completado"  # los bonos siempre al completar
    v["fixed_amount"] = data.fixed_amount if fijo else None
    v["fixed_currency"] = (data.fixed_currency or "CRC") if fijo else None
    if not fijo:
        v["percent"] = data.percent
    else:
        v["percent"] = Decimal(0)
    if data.kind != "bono_anticipo":
        v["committed_date"], v["early_days"] = None, None
    return v


def rule_out(db: Session, r: CommissionRule, p: Principal, names: svc.Names | None = None) -> dict:
    names = names or svc.Names(db)
    cfg = svc.settings_of(p.tenant)
    hide = r.base == "margen" and svc.hides_margin(p, cfg)
    if r.beneficiary == "aliado":
        quien = names.ally(r.ally_company_id) or ("Todos los aliados" if not r.participation_id else None)
    else:
        quien = names.user(r.user_id) if r.user_id else "Todos los vendedores"
    part = db.get(AllyParticipation, r.participation_id) if r.participation_id else None
    pr = db.get(Project, part.project_id) if part and part.project_id else None
    opp = db.get(Opportunity, part.opportunity_id) if part and part.opportunity_id else None
    return {
        "id": r.id,
        "user_id": r.user_id,
        "user": quien or (part.name if part else None),
        "name": r.name,
        "base": r.base,
        # sobre la utilidad, el % con lo que gana deja sacar la utilidad (y el costo): solo quien ve costos
        "percent": None if hide else r.percent,
        "active": r.active,
        "notes": r.notes,
        "beneficiary": r.beneficiary or "vendedor",
        "ally_company_id": r.ally_company_id,
        "ally": names.ally(r.ally_company_id),
        "participation_id": r.participation_id,
        "project": pr.number if pr else (opp.number if opp else None),
        "kind": r.kind or "comision",
        "trigger": r.trigger or "cobro",
        "deal_kind": r.deal_kind,
        "solution": r.solution,
        "fixed_amount": r.fixed_amount,
        "fixed_currency": r.fixed_currency,
        "committed_date": r.committed_date,
        "early_days": r.early_days,
        "valid_from": r.valid_from,
        "valid_to": r.valid_to,
        "summary": svc.rule_summary(r, names, hide_margin=hide),
        "used": bool(db.scalar(select(Commission.id).where(Commission.tenant_id == r.tenant_id, Commission.rule_id == r.id).limit(1))),
    }


@router.get("/commissions/rules")
def rules(p: Principal = Depends(require("commissions", "ver")), db: Session = Depends(get_db)):
    stmt = select(CommissionRule).where(CommissionRule.tenant_id == p.tenant.id)
    cid = ally_scope(p)
    if cid is not None:
        parts = select(AllyParticipation.id).where(AllyParticipation.tenant_id == p.tenant.id, AllyParticipation.ally_company_id == cid)
        stmt = stmt.where(
            CommissionRule.beneficiary == "aliado",
            or_(CommissionRule.ally_company_id == cid, CommissionRule.participation_id.in_(parts), CommissionRule.ally_company_id.is_(None)),
        )
    elif not p.can("commissions", "ver_todo"):
        # falla F: el vendedor ve su regla y la general que le alcanza; nunca la de otro ni las de aliados
        cfg = svc.settings_of(p.tenant)
        conds = [CommissionRule.user_id == p.user.id]
        if p.role in (cfg["general_rule_roles"] or []):
            conds.append(CommissionRule.user_id.is_(None))
        stmt = stmt.where(CommissionRule.beneficiary == "vendedor", or_(*conds))
    names = svc.Names(db)
    return [rule_out(db, r, p, names) for r in db.scalars(stmt.order_by(CommissionRule.id))]


@router.post("/commissions/rules", status_code=201)
def rule_create(data: RuleIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    r = CommissionRule(tenant_id=p.tenant.id, **_rule_values(db, p, data))
    db.add(r)
    db.flush()
    bitacora.audit_change(db, p, "create", "commission_rule", r.id, {}, bitacora.snapshot(r), version=1, always=True)
    db.commit()
    return {"id": r.id}


@router.put("/commissions/rules/{rid}")
def rule_update(rid: int, data: RuleIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    r = db.get(CommissionRule, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Regla no encontrada")
    antes = bitacora.snapshot(r)
    for k, v in _rule_values(db, p, data, r).items():
        setattr(r, k, v)
    # no es retroactiva: los movimientos ya devengados guardan la foto de la regla (terms)
    bitacora.audit_change(
        db, p, "update", "commission_rule", r.id, antes, bitacora.snapshot(r), version=bitacora.next_version(db, p.tenant.id, "commission_rule", r.id)
    )
    db.commit()
    return {"id": r.id}


@router.delete("/commissions/rules/{rid}")
def rule_delete(rid: int, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    r = db.get(CommissionRule, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Regla no encontrada")
    if db.scalar(select(Commission.id).where(Commission.tenant_id == p.tenant.id, Commission.rule_id == r.id).limit(1)):
        raise HTTPException(409, "Desactivala: ya tiene movimientos")
    bitacora.audit_change(db, p, "delete", "commission_rule", r.id, bitacora.snapshot(r), {}, always=True)
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------- 7. Beneficiarios (falla I: la lista salia de tickets y no traia vendedores) ----------
@router.get("/commissions/beneficiaries")
def beneficiaries(p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    from ..services.accounts import role_label

    cfg = svc.settings_of(p.tenant)
    users = []
    rows = db.execute(
        select(User, TenantUser).join(TenantUser, TenantUser.user_id == User.id).where(TenantUser.tenant_id == p.tenant.id, TenantUser.active, User.active)
    ).all()
    for u, m in rows:
        if internal_member(db, p.tenant.id, u.id) is None:
            continue
        users.append(
            {"id": u.id, "name": u.full_name, "role": m.role_code, "role_label": role_label(m.role_code), "general": m.role_code in cfg["general_rule_roles"]}
        )
    users.sort(key=lambda x: (x["role"] != "ventas", x["name"] or ""))
    allies = [
        {"id": c.id, "name": c.name}
        for c in db.scalars(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, AllyCompany.active).order_by(AllyCompany.name))
    ]
    return {"users": users, "allies": allies}


# ---------- 8. Configuracion ----------
class ConfigIn(BaseModel):
    default_trigger_vendedor: str | None = Field(None, pattern="^(cobro|cotizacion_aceptada|proyecto_completado)$")
    default_trigger_aliado: str | None = Field(None, pattern="^(cobro|cotizacion_aceptada|proyecto_completado)$")
    default_base_vendedor: str | None = Field(None, pattern="^(venta|margen)$")
    default_base_aliado: str | None = Field(None, pattern="^(venta|margen)$")
    ally_payout_trigger: str | None = Field(None, pattern="^(proyecto_completado|cobro)$")
    general_rule_roles: list[str] | None = None
    show_margin_base: bool | None = None
    auto_approve: bool | None = None


@router.get("/commissions/config")
def config_get(p: Principal = Depends(require("commissions", "ver_todo"))):
    return svc.settings_of(p.tenant)


@router.put("/commissions/config")
def config_put(data: ConfigIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    from ..core.deps import ASSIGNABLE_ROLES

    cambios = data.model_dump(exclude_unset=True, exclude_none=True)
    if "general_rule_roles" in cambios:
        malos = [r for r in cambios["general_rule_roles"] if r not in ASSIGNABLE_ROLES]
        if malos:
            raise HTTPException(422, f"Rol no válido para comisionar: {', '.join(malos)}")
        cambios["general_rule_roles"] = list(dict.fromkeys(cambios["general_rule_roles"]))
    t = db.get(Tenant, p.tenant.id)
    antes = svc.settings_of(t)
    settings = dict(t.settings or {})
    settings["commissions"] = {**(settings.get("commissions") or {}), **cambios}
    t.settings = settings
    despues = svc.settings_of(t)
    bitacora.audit_change(db, p, "update", "commission_config", t.id, antes, despues, reason="Comisiones -> Configuración")
    db.commit()
    return despues


# ---------- 9. Tiempo real del vendedor ----------
def _period(from_: date | None, to: date | None) -> tuple[date, date]:
    hoy = _today_cr()
    a = from_ or hoy.replace(day=1)
    b = to or (date(a.year + (a.month // 12), a.month % 12 + 1, 1) - timedelta(days=1) if not from_ else hoy)
    if b < a:
        raise HTTPException(422, "El periodo termina antes de empezar")
    return a, b


@router.get("/commissions/summary")
def summary(
    user_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    p: Principal = Depends(require("commissions", "ver")),
    db: Session = Depends(get_db),
):
    _not_ally(p)
    uid = p.user.id
    if user_id and user_id != p.user.id:
        if not p.can("commissions", "ver_todo"):
            raise HTTPException(403, "Solo podés ver tus comisiones")
        if internal_member(db, p.tenant.id, user_id) is None:
            raise HTTPException(404, "Usuario no encontrado")
        uid = user_id
    a, b = _period(from_, to)
    return svc.summary_for_user(db, p.tenant, uid, a, b, p)


# ---------- 10. Bandeja ----------
def _group_key(c: Commission) -> tuple[str, str]:
    if c.beneficiary == "aliado":
        return (f"a:{c.ally_company_id}" if c.ally_company_id else f"p:{c.participation_id}"), "aliado"
    return f"u:{c.user_id}", "vendedor"


@router.get("/commissions/inbox")
def inbox(p: Principal = Depends(require("commissions", "aprobar")), db: Session = Depends(get_db)):
    cfg = svc.settings_of(p.tenant)
    names = svc.Names(db)
    rows = db.scalars(
        select(Commission)
        .where(Commission.tenant_id == p.tenant.id, Commission.status.in_(("en_espera", "pendiente", "aprobada")))
        .order_by(Commission.earned_on, Commission.id)
    ).all()
    espera, groups = [], {"pendiente": {}, "aprobada": {}}
    for c in rows:
        out = svc.entry_out(db, c, p, cfg, names)
        if c.status == "en_espera":
            espera.append(out)
            continue
        key, ben = _group_key(c)
        g = groups[c.status].setdefault(
            key,
            {
                "key": key,
                "beneficiary": ben,
                "name": out["beneficiary_name"] or (db.get(AllyParticipation, c.participation_id).name if c.participation_id else "—"),
                "count": 0,
                "total_crc": Decimal(0),
                "oldest": c.earned_on,
                "has_adjustments": False,
                "entries": [],
            },
        )
        g["count"] += 1
        g["total_crc"] += svc.crc_of(db, c)
        g["oldest"] = min(g["oldest"], c.earned_on)
        g["has_adjustments"] = g["has_adjustments"] or c.kind == "ajuste"
        g["entries"].append(out)

    def lista(dct):
        out = []
        for g in dct.values():
            g["total_crc"] = svc.q2(g["total_crc"])
            out.append(g)
        return sorted(out, key=lambda g: g["oldest"])

    por_aprobar, por_pagar = lista(groups["pendiente"]), lista(groups["aprobada"])
    return {
        "en_espera": espera,
        "por_aprobar": por_aprobar,
        "por_pagar": por_pagar,
        "totals": {
            "en_espera": len(espera),
            "por_aprobar_crc": svc.q2(sum((g["total_crc"] for g in por_aprobar), Decimal(0))),
            "por_pagar_crc": svc.q2(sum((g["total_crc"] for g in por_pagar), Decimal(0))),
        },
    }


# ---------- 11-12. Recalcular ----------
@router.post("/commissions/entries/{cid}/recalc")
def recalc_entry(cid: int, p: Principal = Depends(require("commissions", "aprobar")), db: Session = Depends(get_db)):
    c = db.get(Commission, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Movimiento no encontrado")
    if c.status not in ("en_espera", "pendiente"):
        raise HTTPException(409, f"Un movimiento {STATE_LABEL.get(c.status, c.status)} no se recalcula")
    svc.recalc(db, c, p.user.id)
    db.commit()
    return svc.entry_out(db, c, p, svc.settings_of(p.tenant))


@router.post("/commissions/recalc-waiting")
def recalc_waiting(p: Principal = Depends(require("commissions", "aprobar")), db: Session = Depends(get_db)):
    n = 0
    for c in db.scalars(select(Commission).where(Commission.tenant_id == p.tenant.id, Commission.status == "en_espera")).all():
        if svc.recalc(db, c, p.user.id) and c.status != "en_espera":
            n += 1
    db.commit()
    sigue = db.scalar(select(func.count()).select_from(Commission).where(Commission.tenant_id == p.tenant.id, Commission.status == "en_espera")) or 0
    return {"recalculadas": n, "siguen_en_espera": sigue}


# ---------- 13. Ajuste o bono manual ----------
class AdjustmentIn(BaseModel):
    beneficiary: str = Field(pattern="^(vendedor|aliado)$")
    user_id: int | None = None
    ally_company_id: int | None = None
    project_id: int | None = None
    amount: Decimal
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    description: str = Field(min_length=3, max_length=240)
    earned_on: date | None = None


@router.post("/commissions/adjustments", status_code=201)
def adjustment(data: AdjustmentIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    if data.amount == 0:
        raise HTTPException(422, "El monto no puede ser cero")
    if data.beneficiary == "vendedor":
        if not data.user_id or internal_member(db, p.tenant.id, data.user_id) is None:
            raise HTTPException(422, "Elegí una persona activa del equipo")
        data.ally_company_id = None
    else:
        comp = db.get(AllyCompany, data.ally_company_id) if data.ally_company_id else None
        if not comp or comp.tenant_id != p.tenant.id:
            raise HTTPException(404, "Empresa aliada no encontrada")
        data.user_id = None
    pr = None
    if data.project_id:
        pr = db.get(Project, data.project_id)
        if not pr or pr.tenant_id != p.tenant.id:
            raise HTTPException(404, "Proyecto no encontrado")
    amount = svc.q2(data.amount)
    fx = svc._rate(db, data.currency)
    c = Commission(
        tenant_id=p.tenant.id,
        user_id=data.user_id,
        beneficiary=data.beneficiary,
        ally_company_id=data.ally_company_id,
        project_id=pr.id if pr else None,
        base="venta",
        base_amount=0,
        percent=0,
        amount=amount,
        currency=data.currency,
        fx=fx,
        amount_crc=svc.q2(amount * fx),
        earned_on=data.earned_on or _today_cr(),
        status="pendiente",
        kind="ajuste" if amount < 0 else "bono",
        trigger="manual",
        source_key=svc.manual_key(),
        terms={"rule": None, "base_sale": "0", "doc": pr.number if pr else None},
        description=data.description.strip()[:240],
    )
    db.add(c)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "commission", c.id, {"kind": c.kind, "monto": str(amount), "moneda": data.currency}, ip=p.ip)
    db.commit()
    return svc.entry_out(db, c, p, svc.settings_of(p.tenant))


# ---------- 14. Aplicar reglas a fechas anteriores ----------
class BackfillIn(BaseModel):
    from_: date = Field(alias="from")
    dry_run: bool = True

    model_config = {"populate_by_name": True}


@router.post("/commissions/backfill")
def backfill(data: BackfillIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    hoy = _today_cr()
    if data.from_ > hoy:
        raise HTTPException(422, "La fecha no puede ser futura")
    if (hoy - data.from_).days > 366:
        raise HTTPException(422, "Se aplica como máximo un año hacia atrás")
    db.commit()  # lo pendiente de la sesion queda fuera de la simulacion
    creados = svc.backfill(db, p.tenant, data.from_)
    names = svc.Names(db)
    por: dict[str, dict] = {}
    for c in creados:
        name = names.user(c.user_id) if c.beneficiary == "vendedor" else (names.ally(c.ally_company_id) or "Aliado")
        f = por.setdefault(name or "—", {"name": name or "—", "count": 0, "total_crc": Decimal(0)})
        f["count"] += 1
        f["total_crc"] += svc.crc_of(db, c)
    out = {
        "creados": len(creados),
        "por_beneficiario": [{**f, "total_crc": svc.q2(f["total_crc"])} for f in por.values()],
        "dry_run": data.dry_run,
    }
    if data.dry_run:
        db.rollback()
    else:
        audit(db, p.tenant.id, p.user.id, "backfill", "commission", None, {"desde": str(data.from_), "creados": len(creados)}, ip=p.ip)
        db.commit()
    return out


# ---------- 15-17. Ganancias de aliados (interno: lo del aliado sale por /aliado/ganancias) ----------
def _company(db: Session, p: Principal, ally_company_id: int | None) -> AllyCompany:
    cid = ally_scope(p)
    if cid is not None:
        if ally_company_id and ally_company_id != cid:
            raise HTTPException(403, "Solo las ganancias de su empresa")
        ally_company_id = cid
    elif not p.can("commissions", "ver_todo"):
        raise HTTPException(403, "Sin permiso: commissions.ver_todo")
    elif not p.sees_costs:
        # "lo que pusiste" y el pago acordado son los precios del aliado: costo de Crimson (spec 0.9 y 08:07)
        raise HTTPException(403, "Las ganancias de los aliados traen sus precios: información de costos")
    if not ally_company_id:
        raise HTTPException(422, "Elegí la empresa aliada")
    comp = db.get(AllyCompany, ally_company_id)
    if not comp or comp.tenant_id != p.tenant.id:
        raise HTTPException(404, "Empresa aliada no encontrada")
    return comp


@router.get("/commissions/earnings")
def earnings(
    ally_company_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    p: Principal = Depends(require("commissions", "ver")),
    db: Session = Depends(get_db),
):
    comp = _company(db, p, ally_company_id)
    return svc.ally_earnings(db, p.tenant, comp.id, from_, to)


@router.get("/commissions/earnings/projects/{project_id}")
def earnings_project(project_id: int, ally_company_id: int | None = None, p: Principal = Depends(require("commissions", "ver")), db: Session = Depends(get_db)):
    comp = _company(db, p, ally_company_id)
    part = db.scalar(
        select(AllyParticipation.id).where(
            AllyParticipation.tenant_id == p.tenant.id, AllyParticipation.ally_company_id == comp.id, AllyParticipation.project_id == project_id
        )
    )
    if part is None:
        raise HTTPException(404, "Proyecto no encontrado")
    return svc.ally_project_earnings(db, p.tenant, comp.id, project_id)


@router.get("/commissions/allies")
def allies(p: Principal = Depends(require("commissions", "ver_todo")), db: Session = Depends(get_db)):
    if not p.sees_costs:
        raise HTTPException(403, "Las ganancias de los aliados traen sus precios: información de costos")
    out = []
    for comp in db.scalars(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id).order_by(AllyCompany.name)):
        e = svc.ally_earnings(db, p.tenant, comp.id)
        ultimo = db.scalar(select(func.max(Commission.earned_on)).where(Commission.tenant_id == p.tenant.id, Commission.ally_company_id == comp.id))
        t = e["totales"]
        out.append(
            {
                "ally_company_id": comp.id,
                "name": comp.name,
                "active": comp.active,
                "proyectos": t["proyectos"],
                "recibido": t["recibido"],
                "por_recibir": t["por_recibir"],
                "proyectado": t["proyectado"],
                "ultimo_movimiento": ultimo,
            }
        )
    return out


# ---------- 18. Mis numeros ----------
@router.get("/commissions/numbers")
def numbers(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    p: Principal = Depends(require("commissions", "ver")),
    db: Session = Depends(get_db),
):
    if not p.can("commissions", "ver_todo") or not p.sees_costs:
        raise HTTPException(403, "Mis números es información de costos")
    hoy = _today_cr()
    b = to or hoy
    a = from_ or date(b.year - 1, b.month, 1)
    if b < a or (b - a).days > 1830:
        raise HTTPException(422, "Periodo no válido (máximo cinco años)")
    return svc.numbers(db, p.tenant, a, b)


# ---------- 19-20. Acuerdo economico de un aliado en un negocio ----------
def _participation(db: Session, p: Principal, pid: int) -> AllyParticipation:
    part = db.get(AllyParticipation, pid)
    if not part or part.tenant_id != p.tenant.id:
        raise HTTPException(404, "Participación de aliado no encontrada")
    return part


def _deal_price(db: Session, part: AllyParticipation) -> tuple[Decimal, str]:
    """Venta sin IVA del negocio para proyectar: la del proyecto, la de la cotizacion o el monto de la oportunidad."""
    pr = db.get(Project, part.project_id) if part.project_id else None
    if pr is not None:
        q = db.get(Quote, pr.quote_id) if pr.quote_id else None
        return d(pr.price), (q.currency if q else "CRC")
    o = db.get(Opportunity, part.opportunity_id) if part.opportunity_id else None
    if o is not None and o.quote_id:
        q = db.get(Quote, o.quote_id)
        if q is not None:
            return net_sale(q), q.currency
    return (d(o.amount), o.currency or "CRC") if o is not None else (Decimal(0), "CRC")


def deal_out(db: Session, p: Principal, part: AllyParticipation) -> dict:
    own = svc.participation_rules(db, part)
    names = svc.Names(db)
    price, cur = _deal_price(db, part)
    pagos = [{"id": r.id, "what": r.what, "amount": r.amount, "currency": r.currency, "status": r.status} for r in part.requests if r.status == "aprobado"]
    pago_crc = sum((svc.convert(db, d(r["amount"] or 0), r["currency"], "CRC") for r in pagos), Decimal(0))
    general = None
    if own["comision"] is None:
        pr = db.get(Project, part.project_id) if part.project_id else None
        opp = db.get(Opportunity, part.opportunity_id) if part.opportunity_id else None
        deal = (
            svc.deal_of(db, project=pr)
            if pr is not None
            else svc.Deal(part.tenant_id, opp, None, None, None, opp.kind if opp else "proyecto", opp.solution if opp else None, None)
        )
        g = svc._pick(svc._rules(db, part.tenant_id), svc.settings_of(p.tenant), deal, _today_cr(), ("aliado", f"a:{part.id}", None, None, part))
        if g is not None:
            general = {"id": g.id, "summary": f"{svc.rule_summary(g)}", "rule": g}
    com_rule = own["comision"] or (general["rule"] if general else None)
    comision = Decimal(0)
    if com_rule is not None:
        amt = svc._amount_for(db, com_rule.fixed_amount, com_rule.fixed_currency, com_rule.percent, price, Decimal(1), cur)
        if com_rule.base == "margen" and not svc._is_fixed(com_rule):
            from ..services import pricing

            amt = price * pricing.default_margin(p.tenant) / 100 * d(com_rule.percent) / 100
        comision = svc.convert(db, amt, cur, "CRC")
    bonos, extra = Decimal(0), Decimal(0)
    fin, ant = own["bono_fin"], own["bono_anticipo"]
    if fin is not None:
        amt, bcur = svc._bonus_amount(db, fin, price, cur)
        bonos += svc.convert(db, amt, bcur, "CRC")
    if ant is not None:
        amt, bcur = svc._bonus_amount(db, ant, price, cur)
        extra = svc.convert(db, amt, bcur, "CRC")
    movs = db.scalars(
        select(Commission).where(Commission.tenant_id == p.tenant.id, Commission.participation_id == part.id).order_by(Commission.earned_on, Commission.id)
    ).all()
    cfg = svc.settings_of(p.tenant)
    com = own["comision"]
    return {
        "participation": {
            "id": part.id,
            "name": part.name,
            "role": part.role,
            "project_id": part.project_id,
            "opportunity_id": part.opportunity_id,
            "ally_company_id": part.ally_company_id,
        },
        "pago_acordado": pagos,
        "commission": {
            "rule_id": com.id,
            "base": com.base,
            "percent": com.percent if not svc._is_fixed(com) else None,
            "fixed_amount": com.fixed_amount,
            "currency": com.fixed_currency or cur,
            "trigger": com.trigger,
        }
        if com is not None
        else None,
        "bonus_complete": {
            "rule_id": fin.id,
            "amount": fin.fixed_amount,
            "percent": fin.percent if not svc._is_fixed(fin) else None,
            "currency": fin.fixed_currency or cur,
        }
        if fin is not None
        else None,
        "bonus_early": {
            "rule_id": ant.id,
            "amount": ant.fixed_amount,
            "percent": ant.percent if not svc._is_fixed(ant) else None,
            "currency": ant.fixed_currency or cur,
            "committed_date": ant.committed_date,
            "early_days": ant.early_days if ant.early_days is not None else 1,
        }
        if ant is not None
        else None,
        "general_rule": {"id": general["id"], "summary": general["summary"]} if general else None,
        "projection": {
            "pago_acordado": svc.q2(pago_crc),
            "comision": svc.q2(comision),
            "bonos": svc.q2(bonos),
            "bono_anticipo": svc.q2(extra),
            "total": svc.q2(pago_crc + comision + bonos),
            "currency": "CRC",
            "base_price": svc.q2(svc.convert(db, price, cur, "CRC")),
        },
        "movimientos": [svc.entry_out(db, c, p, cfg, names) for c in movs],
    }


@router.get("/commissions/deals/{participation_id}")
def deal_get(participation_id: int, p: Principal = Depends(require("commissions", "ver_todo")), db: Session = Depends(get_db)):
    if not p.sees_costs:
        raise HTTPException(403, "El acuerdo económico es información de costos")
    return deal_out(db, p, _participation(db, p, participation_id))


class DealCommissionIn(BaseModel):
    base: str = Field("venta", pattern="^(venta|margen)$")
    percent: Decimal | None = Field(None, ge=0, le=100)
    fixed_amount: Decimal | None = Field(None, ge=0)
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    trigger: str = Field("cobro", pattern="^(cobro|cotizacion_aceptada|proyecto_completado)$")


class DealBonusIn(BaseModel):
    amount: Decimal | None = Field(None, ge=0)
    percent: Decimal | None = Field(None, ge=0, le=100)
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")


class DealEarlyIn(DealBonusIn):
    committed_date: date
    early_days: int | None = Field(1, ge=0, le=365)


class DealIn(BaseModel):
    commission: DealCommissionIn | None = None
    bonus_complete: DealBonusIn | None = None
    bonus_early: DealEarlyIn | None = None


def _set_rule(db: Session, p: Principal, part: AllyParticipation, kind: str, existing: CommissionRule | None, values: dict | None) -> None:
    if values is None:
        if existing is None:
            return
        antes = bitacora.snapshot(existing)
        if db.scalar(select(Commission.id).where(Commission.tenant_id == p.tenant.id, Commission.rule_id == existing.id).limit(1)):
            existing.active = False  # con movimientos no se borra: queda como historial
            bitacora.audit_change(db, p, "update", "commission_rule", existing.id, antes, bitacora.snapshot(existing))
        else:
            bitacora.audit_change(db, p, "delete", "commission_rule", existing.id, antes, {}, always=True)
            db.delete(existing)
        return
    fijo = values.get("fixed_amount") is not None and d(values["fixed_amount"]) > 0
    pct = d(values.get("percent") or 0)
    if fijo == (pct > 0):
        raise HTTPException(422, "Indicá un porcentaje o un monto fijo (uno de los dos)")
    nombre = {"comision": "Acuerdo del proyecto", "bono_fin": "Bono por completar", "bono_anticipo": "Bono por terminar antes"}[kind]
    data = {
        "beneficiary": "aliado",
        "user_id": None,
        "ally_company_id": part.ally_company_id,
        "participation_id": part.id,
        "kind": kind,
        "name": f"{nombre} · {part.name}"[:120],
        "base": values.get("base") or "venta",
        "percent": Decimal(0) if fijo else pct,
        "fixed_amount": d(values["fixed_amount"]) if fijo else None,
        "fixed_currency": (values.get("currency") or "CRC") if fijo else None,
        "trigger": values.get("trigger") or "cobro" if kind == "comision" else "proyecto_completado",
        "committed_date": values.get("committed_date") if kind == "bono_anticipo" else None,
        "early_days": values.get("early_days") if kind == "bono_anticipo" else None,
        "active": True,
    }
    if existing is None:
        r = CommissionRule(tenant_id=p.tenant.id, **data)
        db.add(r)
        db.flush()
        bitacora.audit_change(db, p, "create", "commission_rule", r.id, {}, bitacora.snapshot(r), version=1, always=True)
    else:
        antes = bitacora.snapshot(existing)
        for k, v in data.items():
            setattr(existing, k, v)
        bitacora.audit_change(db, p, "update", "commission_rule", existing.id, antes, bitacora.snapshot(existing))


@router.put("/commissions/deals/{participation_id}")
def deal_put(participation_id: int, data: DealIn, p: Principal = Depends(require("commissions", "configurar")), db: Session = Depends(get_db)):
    part = _participation(db, p, participation_id)
    own = svc.participation_rules(db, part)
    com = data.commission.model_dump() if data.commission else None
    if com is not None:
        com["fixed_amount"], com["currency"] = com.get("fixed_amount"), com.get("currency")
    fin = data.bonus_complete.model_dump() if data.bonus_complete else None
    if fin is not None:
        fin["fixed_amount"] = fin.pop("amount")
    ant = data.bonus_early.model_dump() if data.bonus_early else None
    if ant is not None:
        ant["fixed_amount"] = ant.pop("amount")
    _set_rule(db, p, part, "comision", own["comision"], com)
    _set_rule(db, p, part, "bono_fin", own["bono_fin"], fin)
    _set_rule(db, p, part, "bono_anticipo", own["bono_anticipo"], ant)
    db.commit()
    return deal_out(db, p, part)
