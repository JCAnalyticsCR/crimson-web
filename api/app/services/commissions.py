"""Economia: motor de comisiones, bonos y pagos acordados a aliados (libro unico `commission`).

Andres (22/09): "la mayoria de mis negocios se van a basar en comisionar"; "venta cobrada -> comision". (13/10):
reglas por vendedor y por aliado, acuerdo por proyecto con bonos, aprobacion manual ("yo aqui le voy a poder
aprobar"), el aliado ve lo que recibe y su rentabilidad en el tiempo, y el ve "sus numeros".

Toda la logica vive aqui; los routers solo llaman. Eventos que devengan (cada uno idempotente por source_key):
- on_payment: cobro confirmado (captura) -> comision proporcional sin IVA con tope por factura; devolucion o
  reembolso -> ajuste negativo proporcional. Autorizaciones, voids y pagos sin confirmar no hacen nada.
- on_quote_acceptance / on_quote_won / on_quote_void: reglas "al aceptar la cotizacion".
- on_project_status: primera vez terminado/entregado/facturado -> comisiones "al completar", bonos y pago acordado;
  cancelado -> reverso. on_project_costs recalcula lo que espera la utilidad del proyecto.
- on_cost_request_change: el precio aprobado del aliado (su pago acordado) cambia.
- on_invoice_void: factura anulada -> se anula o revierte lo devengado.

Defaults (Comisiones -> Configuracion): vendedor y aliado sobre la venta sin IVA al cobrar; pago acordado del aliado
al completar el proyecto; aprobacion manual; ninguna regla con % creada sola. Montos en Decimal siempre.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AllyCompany,
    AllyCostRequest,
    AllyParticipation,
    Commission,
    CommissionRule,
    Customer,
    Invoice,
    Opportunity,
    Payment,
    Product,
    Project,
    Quote,
    QuoteAcceptance,
    Survey,
    Tenant,
    TenantUser,
    User,
    WorkOrder,
)
from ..models.support import DEAL_KINDS
from .documents import audit, local_date, today_fx
from .totals import d, net_sale

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
CLOSED = ("terminado", "entregado", "facturado")
OPEN_PROJECT = ("planificado", "en_curso", "pausado")
LIVE = ("en_espera", "pendiente", "aprobada", "pagada")  # todo lo que no esta anulado
HOLD_PROJECT = "Se calcula cuando el proyecto cierre con sus costos"

COMMISSION_DEFAULTS = {
    "default_trigger_vendedor": "cobro",
    "default_trigger_aliado": "cobro",
    "default_base_vendedor": "venta",
    "default_base_aliado": "venta",
    "ally_payout_trigger": "proyecto_completado",  # pago acordado: proyecto_completado | cobro
    "general_rule_roles": ["ventas"],  # a quien alcanza una regla de vendedor sin persona
    "show_margin_base": False,  # mostrar la base de utilidad al beneficiario sin acceso a costos
    "auto_approve": False,  # True: nace "aprobada" (solo si Andres lo decide)
}
TRIGGER_LABEL = {
    "cobro": "al cobrar",
    "cotizacion_aceptada": "al aceptar la cotización",
    "proyecto_completado": "al completar el proyecto",
    "manual": "manual",
}
KIND_LABEL = {"comision": "Comisión", "bono": "Bono", "pago_acordado": "Pago acordado", "ajuste": "Ajuste"}
RULE_KIND_LABEL = {"comision": "Comisión", "bono_fin": "Bono por completar", "bono_anticipo": "Bono por terminar antes"}
SOLUTION_LABEL = {
    "cctv": "CCTV",
    "redes": "Redes / WiFi",
    "acceso": "Control de acceso",
    "asistencia": "Tiempo y asistencia",
    "ups": "Respaldo eléctrico",
    "cableado": "Cableado estructurado",
    "anpr": "ANPR / barreras",
    "otro": "Otro",
}


def q2(v) -> Decimal:
    return d(v).quantize(CENT)


def settings_of(tenant) -> dict:
    raw = (getattr(tenant, "settings", None) or {}).get("commissions") or {}
    return {**COMMISSION_DEFAULTS, **{k: v for k, v in raw.items() if k in COMMISSION_DEFAULTS}}


def _cfg(db: Session, tenant_id: int) -> dict:
    return settings_of(db.get(Tenant, tenant_id))


def _fecha(v) -> date:
    """paid_at es date en un pago manual y datetime cuando viene de la pasarela."""
    if isinstance(v, datetime):
        return local_date(v) or date.today()
    return v or date.today()


def _num(v) -> str:
    """5 -> "5", 7.5 -> "7,5" (como se escribe en Costa Rica)."""
    n = d(v).normalize()
    s = f"{n:f}"
    return s.replace(".", ",")


def _rate(db: Session, currency: str | None) -> Decimal:
    return Decimal(1) if not currency or currency == "CRC" else d(today_fx(db, currency)[0])


def convert(db: Session, amount, from_cur: str | None, to_cur: str | None) -> Decimal:
    """Monto de una moneda a otra con el tipo de cambio de hoy (colones como puente)."""
    amount = d(amount)
    from_cur, to_cur = from_cur or "CRC", to_cur or "CRC"
    if from_cur == to_cur:
        return amount
    return amount * _rate(db, from_cur) / _rate(db, to_cur)


def crc_of(db: Session, c: Commission) -> Decimal:
    """Equivalente en colones de un movimiento: el que se guardo al devengar o, en filas viejas, al cambio de hoy."""
    if c.amount_crc is not None:
        return d(c.amount_crc)
    return d(c.amount) * _rate(db, c.currency)


def _doc_crc(db: Session, doc, amount) -> Decimal:
    """Monto de un documento en colones con SU tipo de cambio (como los reportes). Si le falta o quedo en 1 (el
    sistema viejo caia a 1 sin tasa), el del BCCR de su fecha (services/fx.doc_rate)."""
    from .fx import doc_rate

    if doc is None or (doc.currency or "CRC") == "CRC":
        return d(amount)
    return d(amount) * doc_rate(db, doc.currency, doc.fx_sell, getattr(doc, "issue_date", None))


# ---------- 5.1 El negocio de un documento ----------
@dataclass
class Deal:
    tenant_id: int
    opportunity: Opportunity | None
    quote: Quote | None
    project: Project | None
    invoice: Invoice | None
    kind: str  # venta | proyecto
    solution: str | None  # codigo de SOLUTIONS
    seller_id: int | None

    @property
    def number(self) -> str:
        for doc in (self.invoice, self.project, self.quote, self.opportunity):
            if doc is not None:
                return doc.number
        return ""

    @property
    def price_currency(self) -> str:
        """Moneda de Project.price: la de su cotizacion."""
        return self.quote.currency if self.quote is not None else "CRC"


def deal_of(db: Session, *, invoice: Invoice | None = None, quote: Quote | None = None, project: Project | None = None) -> Deal:
    from .ownership import opportunity_seller

    inv, q, pr = invoice, quote, project
    tid = (inv or q or pr).tenant_id
    if q is None and inv is not None and inv.quote_id:
        q = db.get(Quote, inv.quote_id)
    if pr is None and inv is not None:
        pr = db.scalar(select(Project).where(Project.tenant_id == tid, Project.invoice_id == inv.id))
    if pr is None and q is not None:
        pr = db.scalar(select(Project).where(Project.tenant_id == tid, Project.quote_id == q.id))
    if q is None and pr is not None and pr.quote_id:
        q = db.get(Quote, pr.quote_id)
    opp = None
    if q is not None:
        opp = db.scalar(select(Opportunity).where(Opportunity.tenant_id == tid, Opportunity.quote_id == q.id))
    if opp is None and pr is not None:
        opp = db.get(Opportunity, pr.opportunity_id) if pr.opportunity_id else None
        if opp is None:
            opp = db.scalar(select(Opportunity).where(Opportunity.tenant_id == tid, Opportunity.project_id == pr.id))
    kind = opp.kind if opp is not None and opp.kind in DEAL_KINDS else ("proyecto" if pr is not None else "venta")
    sv = None
    if pr is not None and pr.survey_id:
        sv = db.get(Survey, pr.survey_id)
    elif q is not None:
        sv = db.scalar(select(Survey).where(Survey.tenant_id == tid, Survey.quote_id == q.id))
    solution = sv.kind if sv is not None and sv.kind else (opp.solution if opp is not None and opp.solution else None)
    # vendedor responsable del documento (services/ownership.py): la factura hereda el de su cotizacion
    if inv is not None:
        seller = inv.seller_id
    elif q is not None:
        seller = q.seller_id
    else:
        seller = opportunity_seller(db, opp) if opp is not None else None
    return Deal(tid, opp, q, pr, inv, kind, solution, seller)


def participations_of(db: Session, deal: Deal) -> list[AllyParticipation]:
    conds = []
    if deal.opportunity is not None:
        conds.append(AllyParticipation.opportunity_id == deal.opportunity.id)
    if deal.project is not None:
        conds.append(AllyParticipation.project_id == deal.project.id)
    if not conds:
        return []
    return list(db.scalars(select(AllyParticipation).where(AllyParticipation.tenant_id == deal.tenant_id, or_(*conds)).order_by(AllyParticipation.id)))


def member_role(db: Session, tenant_id: int, user_id: int | None) -> str | None:
    """Rol interno del vendedor (None si no es del equipo: cliente, aliado o sin membresia)."""
    from ..core.deps import ALLY_USER_ROLES, CLIENT_ROLES

    if not user_id:
        return None
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id))
    if m is None or m.customer_id or m.ally_company_id or m.role_code in CLIENT_ROLES or m.role_code in ALLY_USER_ROLES:
        return None
    return m.role_code


# ---------- 5.2 La regla que manda ----------
def _in_range(r: CommissionRule, on: date) -> bool:
    return (r.valid_from is None or r.valid_from <= on) and (r.valid_to is None or on <= r.valid_to)


def best_rule(
    rules: list[CommissionRule],
    *,
    beneficiary: str,
    kind: str,
    user_id: int | None = None,
    user_role: str | None = None,
    participation: AllyParticipation | None = None,
    deal: Deal,
    on: date,
    general_roles: list[str],
) -> CommissionRule | None:
    """Gana la mas especifica: 8 participacion + 4 persona/empresa + 2 solucion + 1 tipo de negocio; empate, la mas
    reciente. Nunca se suman dos reglas del mismo tipo para el mismo beneficiario."""
    best, best_score = None, -1
    for r in rules:
        if not r.active or (r.beneficiary or "vendedor") != beneficiary or (r.kind or "comision") != kind or not _in_range(r, on):
            continue
        if r.deal_kind not in (None, deal.kind) or r.solution not in (None, deal.solution):
            continue
        score = 0
        if beneficiary == "vendedor":
            if r.user_id is not None:
                if r.user_id != user_id:
                    continue
                score += 4
            elif user_id is None or user_role not in (general_roles or []):
                continue  # la regla general solo alcanza a los roles configurados (el admin no se comisiona solo)
        else:
            if participation is None:
                continue
            if r.participation_id is not None:
                if r.participation_id != participation.id:
                    continue
                score += 12
            else:
                if participation.ally_company_id is None:
                    continue  # participacion vieja sin empresa: solo el acuerdo del proyecto
                if r.ally_company_id is not None:
                    if r.ally_company_id != participation.ally_company_id:
                        continue
                    score += 4
                elif participation.role == "contratante":
                    continue  # quien contrata a Crimson no comisiona con la regla de todos los aliados
        if r.solution:
            score += 2
        if r.deal_kind:
            score += 1
        if score > best_score or (score == best_score and best is not None and (r.id or 0) > (best.id or 0)):
            best, best_score = r, score
    return best


def trigger_of(rule: CommissionRule, deal: Deal) -> str:
    """Cuando se devenga la regla en ESTE negocio. Sin proyecto, "al completar" se gana al cobrar; sin cotizacion,
    "al aceptar" tambien (una venta de mostrador nunca se acepta)."""
    t = (rule.trigger or "cobro") if (rule.kind or "comision") == "comision" else "proyecto_completado"
    if t == "proyecto_completado" and deal.project is None:
        return "cobro"
    if t == "cotizacion_aceptada" and deal.quote is None:
        return "cobro"
    return t


def _rules(db: Session, tenant_id: int) -> list[CommissionRule]:
    return list(db.scalars(select(CommissionRule).where(CommissionRule.tenant_id == tenant_id, CommissionRule.active)))


def _is_fixed(rule) -> bool:
    return rule is not None and rule.fixed_amount is not None and d(rule.fixed_amount) > 0


def rule_terms(rule: CommissionRule | None) -> dict | None:
    if rule is None:
        return None
    return {
        "id": rule.id,
        "name": rule.name,
        "base": rule.base,
        "percent": str(d(rule.percent)),
        "fixed_amount": str(d(rule.fixed_amount)) if rule.fixed_amount is not None else None,
        "fixed_currency": rule.fixed_currency,
        "trigger": rule.trigger,
        "kind": rule.kind,
    }


def _amount_for(db: Session, fixed_amount, fixed_currency, percent, base_amount: Decimal, share: Decimal, currency: str) -> Decimal:
    """% de la base, o el monto fijo repartido segun la parte del negocio que cubre este evento."""
    if fixed_amount is not None and d(fixed_amount) > 0:
        return convert(db, d(fixed_amount), fixed_currency or "CRC", currency) * min(max(share, Decimal(0)), Decimal(1))
    return d(base_amount) * d(percent) / 100


def _rule_amount(db: Session, rule, base_amount: Decimal, share: Decimal, currency: str) -> Decimal:
    return _amount_for(db, rule.fixed_amount, rule.fixed_currency, rule.percent, base_amount, share, currency)


MARGIN_CONCEPT = "Comisión sobre la utilidad"


def describe(rule, trigger: str, doc: str) -> str:
    """Concepto legible del movimiento. Nunca lleva montos de base. Sobre la utilidad tampoco lleva el %: con el monto
    deja despejar la utilidad (monto / %) y el costo, y el concepto lo ven la vendedora, contabilidad y el aliado
    (spec 6, falla F). El % queda en `percent`, que entry_out solo muestra a quien ve costos."""
    if _is_fixed(rule):
        que = "Monto fijo"
        sobre = {"cobro": " según lo cobrado", "cotizacion_aceptada": " al aceptar", "proyecto_completado": " al completar"}.get(trigger, "")
        return f"{que}{sobre} · {doc}"
    pct = _num(rule.percent)
    if rule.base == "margen":
        return f"{MARGIN_CONCEPT} · {doc}"
    if trigger == "cobro":
        return f"{pct} % de lo cobrado sin IVA · {doc}"
    if trigger == "cotizacion_aceptada":
        return f"{pct} % de la venta sin IVA al aceptar · {doc}"
    return f"{pct} % de la venta del proyecto · {doc}"


# ---------- 5.3 Devengar ----------
def _exists(db: Session, tenant_id: int, key: str) -> bool:
    return db.scalar(select(Commission.id).where(Commission.tenant_id == tenant_id, Commission.source_key == key)) is not None


def _fresh_key(db: Session, tenant_id: int, key: str) -> str:
    """La clave misma si no se ha usado; si ya existe (se anulo o se revirtio y se vuelve a devengar), key:re{n} libre.
    El movimiento viejo queda de historial."""
    if not _exists(db, tenant_id, key):
        return key
    n = (db.scalar(select(func.count()).select_from(Commission).where(Commission.tenant_id == tenant_id, Commission.source_key.like(f"{key}:re%"))) or 0) + 1
    while _exists(db, tenant_id, f"{key}:re{n}"):
        n += 1
    return f"{key}:re{n}"


def _is_reversal(c: Commission) -> bool:
    return (c.source_key or "").startswith("rev:")


def _still_earned(rows) -> bool:
    """Hay un movimiento de base (no ajuste) vivo sin su reverso: lo devengado sigue en pie. Uno pagado y revertido
    (pagada + rev:{id}) neto da 0 y ya no cuenta."""
    revertidos = {c.reverses_id for c in rows if _is_reversal(c) and c.status != "anulada"}
    return any(c.kind != "ajuste" and c.status != "anulada" and c.id not in revertidos for c in rows)


MANUAL_VOID = "manual_void"


def mark_manual_void(c: Commission) -> None:
    """Anulada a mano en la bandeja (PATCH /commissions): una decision de contabilidad o del admin, no del motor. El
    motor no la vuelve a devengar sola al convertir o crear el proyecto (solo una aceptacion nueva lo hace)."""
    c.terms = {**(c.terms or {}), MANUAL_VOID: True}


def _manually_voided(rows) -> bool:
    """El ultimo movimiento de base (no ajuste ni reverso) del grupo lo anulo una persona."""
    base = [c for c in rows if c.kind != "ajuste" and not _is_reversal(c)]
    if not base:
        return False
    last = max(base, key=lambda c: c.id)
    return last.status == "anulada" and bool((last.terms or {}).get(MANUAL_VOID))


def _fx_for(db: Session, currency: str, doc) -> Decimal:
    if currency == "CRC":
        return Decimal(1)
    if doc is not None and doc.currency == currency and d(doc.fx_sell) > 0:
        return d(doc.fx_sell)
    return _rate(db, currency)


def _accrue(
    db: Session,
    *,
    deal: Deal,
    source_key: str,
    beneficiary: str,
    kind: str,
    trigger: str,
    base: str,
    base_sale,
    base_amount,
    percent,
    amount,
    currency: str,
    earned_on: date,
    description: str,
    user_id: int | None = None,
    participation: AllyParticipation | None = None,
    rule: CommissionRule | None = None,
    hold_reason: str | None = None,
    cost_request: AllyCostRequest | None = None,
    payment: Payment | None = None,
    status: str | None = None,
    reverses_id: int | None = None,
    ally_company_id: int | None = None,
) -> Commission | None:
    if _exists(db, deal.tenant_id, source_key):
        return None  # idempotente: un webhook reintentado o un backfill repetido no duplica
    amount = q2(amount)
    if amount == 0 and not hold_reason:
        return None
    cfg = _cfg(db, deal.tenant_id)
    st = status or ("en_espera" if hold_reason else ("aprobada" if cfg["auto_approve"] else "pendiente"))
    doc = deal.invoice or deal.quote
    fx = _fx_for(db, currency, doc)
    c = Commission(
        tenant_id=deal.tenant_id,
        user_id=user_id if beneficiary == "vendedor" else None,
        beneficiary=beneficiary,
        ally_company_id=ally_company_id if ally_company_id is not None else (participation.ally_company_id if participation is not None else None),
        participation_id=participation.id if participation is not None else None,
        invoice_id=deal.invoice.id if deal.invoice is not None else None,
        payment_id=payment.id if payment is not None else None,
        quote_id=deal.quote.id if deal.quote is not None else None,
        project_id=deal.project.id if deal.project is not None else None,
        opportunity_id=deal.opportunity.id if deal.opportunity is not None else None,
        cost_request_id=cost_request.id if cost_request is not None else None,
        rule_id=rule.id if rule is not None else None,
        base=base,
        base_amount=q2(base_amount or 0),
        percent=q2(percent or 0),
        amount=amount,
        currency=currency,
        earned_on=earned_on,
        status=st,
        kind=kind,
        trigger=trigger,
        source_key=source_key,
        fx=fx,
        amount_crc=q2(amount * fx),
        terms={"rule": rule_terms(rule), "base_sale": str(q2(base_sale or 0)), "doc": deal.number},
        description=(description or "")[:240],
        hold_reason=(hold_reason or None) and hold_reason[:240],
        reverses_id=reverses_id,
    )
    if st == "aprobada":
        c.approved_at = datetime.now(UTC)
    db.add(c)
    db.flush()
    return c


def _beneficiaries(db: Session, deal: Deal, cfg: dict) -> list[tuple[str, str, int | None, str | None, AllyParticipation | None]]:
    """(beneficiario, clave, user_id, rol, participacion): el vendedor del negocio y cada aliado que participa."""
    out = []
    role = member_role(db, deal.tenant_id, deal.seller_id)
    if deal.seller_id and role is not None and not _web_order_without_commission(db, deal):
        out.append(("vendedor", f"v:{deal.seller_id}", deal.seller_id, role, None))
    for part in participations_of(db, deal):
        out.append(("aliado", f"a:{part.id}", None, None, part))
    return out


def _web_order_without_commission(db: Session, deal: Deal) -> bool:
    """Factura de un pedido web asignado a un vendedor (21/10, items 24/42) con "comisiona como venta propia" apagado
    en Mi Tienda: la venta es suya (la ve en sus Facturas) pero no le genera comision."""
    if deal.invoice is None or deal.quote is not None:
        return False  # un pedido web no tiene cotizacion
    from . import store_orders

    tenant = db.get(Tenant, deal.tenant_id)
    if tenant is None or store_orders.web_orders_commission(tenant):
        return False
    return store_orders.is_web_order_invoice(db, deal.invoice.id, deal.tenant_id)


def _pick(rules, cfg, deal, on, ben, kind="comision"):
    beneficiary, _key, uid, role, part = ben
    return best_rule(
        rules,
        beneficiary=beneficiary,
        kind=kind,
        user_id=uid,
        user_role=role,
        participation=part,
        deal=deal,
        on=on,
        general_roles=cfg["general_rule_roles"],
    )


def _base_sale_of(c: Commission) -> Decimal:
    if c.terms and c.terms.get("base_sale") is not None:
        return d(c.terms["base_sale"])
    return d(c.base_amount) if c.base == "venta" else Decimal(0)  # filas viejas sin terms


def _sale_filter(beneficiary: str, part: AllyParticipation | None) -> list:
    """Lo que ya cobro "la venta" sea quien sea el vendedor: si la cotizacion o la factura cambia de vendedor, el nuevo
    no vuelve a cobrar lo que ya devengo el anterior (la venta comisiona una sola vez). Cada aliado por su participacion."""
    if beneficiary == "vendedor":
        return [Commission.beneficiary == "vendedor"]
    return [Commission.participation_id == part.id]


def _payment_commissioned(db: Session, payment: Payment, ignore_void: bool = False) -> bool:
    """El cobro ya comisiono al vendedor (en cualquier estado y a cualquier usuario). Los movimientos viejos llevan la
    clave cobro:{pago}:v:{quien hizo la factura}; el vendedor responsable puede ser otro y su clave no choca.
    ignore_void: al reasignar el vendedor de la factura (pedido web, store_orders.assign_seller) la comision ANULADA
    del anterior no cuenta: se anulo justamente para que el cobro comisione al nuevo."""
    return (
        db.scalar(
            select(Commission.id)
            .where(
                Commission.tenant_id == payment.tenant_id,
                Commission.payment_id == payment.id,
                Commission.beneficiary == "vendedor",
                Commission.trigger == "cobro",
                Commission.kind == "comision",
                *((Commission.status != "anulada",) if ignore_void else ()),
            )
            .limit(1)
        )
        is not None
    )


def _sold_base(db: Session, inv: Invoice, beneficiary: str, uid, part) -> Decimal:
    """Venta sin IVA ya comisionada al cobrar en esta factura (tope de la falla B), neta de devoluciones. Del vendedor
    cuenta lo de cualquier vendedor (_sale_filter): cambiar de vendedor no reinicia el tope."""
    rows = db.scalars(
        select(Commission).where(
            Commission.tenant_id == inv.tenant_id,
            Commission.invoice_id == inv.id,
            Commission.status != "anulada",
            Commission.trigger == "cobro",
            Commission.kind.in_(("comision", "ajuste")),
            *_sale_filter(beneficiary, part),
        )
    ).all()
    # las devoluciones del pago acordado (grupo pago:{rid}) llevan participation_id pero no son de esta comision: si
    # contaran, una devolucion bajaria dos veces el tope y el siguiente cobro comisionaria de mas (falla B)
    return sum(
        (_base_sale_of(c) for c in rows if not c.cost_request_id and (c.kind == "comision" or (c.source_key or "").startswith("dev:"))),
        Decimal(0),
    )


# ---------- 5.7 Base utilidad ----------
def _doc_cost(db: Session, doc) -> tuple[Decimal, list[str]]:
    """Costo de Crimson de las lineas con producto (normal y cortesia; aportado y excluido no son costo de Crimson)."""
    from . import pricing
    from .fx import rate_or_none

    fx = rate_or_none(db, "USD")  # None: solo un costo en otra moneda la consulta (sin ninguna tasa, 422 claro)
    cost, faltan = Decimal(0), []
    for ln in doc.lines:
        if not ln.product_id or (ln.treatment or "normal") not in ("normal", "cortesia"):
            continue
        prod = db.get(Product, ln.product_id)
        if prod is None or prod.cost is None:
            faltan.append(ln.name)
            continue
        cost += pricing.cost_in(db, prod, doc.currency, fx) * d(ln.quantity)
    return cost, list(dict.fromkeys(faltan))


def _doc_margin(db: Session, doc, base_sale: Decimal, with_labor: bool = False) -> tuple[Decimal | None, str | None]:
    cost, faltan = _doc_cost(db, doc)
    if faltan:
        return None, ("Falta el costo de: " + ", ".join(faltan))[:240]
    if with_labor and isinstance(doc, Quote):
        sv = db.scalar(select(Survey).where(Survey.tenant_id == doc.tenant_id, Survey.quote_id == doc.id))
        if sv is not None:
            from ..routers.fieldwork import labor_costing

            lab = labor_costing(sv, db.get(Tenant, doc.tenant_id))
            cost += convert(db, d(lab["labor_cost"]) + d(lab["travel"]), "CRC", doc.currency)
    net = net_sale(doc)
    if net <= 0:
        return ZERO, None
    return base_sale * (net - cost) / net, None


def margin_base(db: Session, deal: Deal, base_sale: Decimal, origen: str = "cobro") -> tuple[Decimal | None, str | None]:
    """Utilidad que le toca a esta base de venta: antes de comisiones y despues de pagar al aliado."""
    if origen == "cotizacion_aceptada" and deal.quote is not None:
        return _doc_margin(db, deal.quote, base_sale, with_labor=True)
    pr = deal.project
    if pr is not None:
        from ..routers.projects import economics

        if pr.status not in CLOSED:
            return None, HOLD_PROJECT
        e = economics(db, pr)
        if e["sin_costos"]:
            return None, HOLD_PROJECT
        price_crc = d(e["price_crc"])
        if price_crc <= 0:
            return ZERO, None
        return base_sale * d(e["profit"]) / price_crc, None
    doc = deal.invoice or deal.quote
    if doc is None:
        return None, "Sin documento de venta"
    return _doc_margin(db, doc, base_sale)


# ---------- 5.4 E1 · Cobro ----------
def on_payment(db: Session, payment: Payment, reassign: bool = False) -> list[Commission]:
    """Desde documents.add_payment con el pago confirmado. Captura devenga; devolucion/reembolso revierten.
    reassign: recalculo por cambio de vendedor de la factura (las comisiones anuladas del cobro no lo bloquean)."""
    if payment is None or payment.status != "confirmado" or not payment.invoice_id:
        return []
    inv = db.get(Invoice, payment.invoice_id)
    if inv is None:
        return []
    if payment.kind == "captura":
        return _on_capture(db, payment, inv, ignore_void=reassign)
    if payment.kind in ("devolucion", "reembolso"):
        return _on_refund(db, payment, inv)
    return []  # autorizacion, void, reautorizacion: no es plata cobrada


def _paid_in_doc(db: Session, payment: Payment, inv: Invoice) -> Decimal:
    return convert(db, d(payment.amount), payment.currency or inv.currency, inv.currency)


def _on_capture(db: Session, payment: Payment, inv: Invoice, only: set[str] | None = None, ignore_void: bool = False) -> list[Commission]:
    total, net = d(inv.total), net_sale(inv)
    if inv.status == "anulada" or total <= 0 or net <= 0:
        return []
    deal = deal_of(db, invoice=inv)
    cfg = _cfg(db, inv.tenant_id)
    rules = _rules(db, inv.tenant_id)
    raw = _paid_in_doc(db, payment, inv) * net / total
    on = _fecha(payment.paid_at)
    out: list[Commission] = []
    bens = _beneficiaries(db, deal, cfg)
    for ben in bens:
        beneficiary, key, uid, _role, part = ben
        rule = _pick(rules, cfg, deal, on, ben)
        if rule is None or trigger_of(rule, deal) != "cobro":
            continue
        if beneficiary == "vendedor" and _payment_commissioned(db, payment, ignore_void):
            continue  # backfill de un pago viejo: ya comisiono a quien hizo la factura
        ya = _sold_base(db, inv, beneficiary, uid, part)
        base_sale = max(Decimal(0), min(raw, net - ya))
        if base_sale <= 0:
            continue  # sobrepago: lo que pasa de la venta sin IVA no comisiona (falla B)
        hold = None
        if rule.base == "margen":
            base_amount, hold = margin_base(db, deal, base_sale)
            if hold is None and (base_amount is None or base_amount <= 0):
                continue
        else:
            base_amount = base_sale
        amount = ZERO if hold else _rule_amount(db, rule, base_amount, base_sale / net, inv.currency)
        c = _accrue(
            db,
            deal=deal,
            source_key=f"cobro:{payment.id}:{key}",
            beneficiary=beneficiary,
            user_id=uid,
            participation=part,
            rule=rule,
            kind="comision",
            trigger="cobro",
            base=rule.base,
            base_sale=base_sale,
            base_amount=ZERO if hold else base_amount,
            percent=0 if _is_fixed(rule) else rule.percent,
            amount=amount,
            currency=inv.currency,
            earned_on=on,
            description=describe(rule, "cobro", inv.number),
            hold_reason=hold,
            payment=payment,
        )
        if c is not None:
            out.append(c)
    if cfg["ally_payout_trigger"] == "cobro" or deal.project is None:
        venta = convert(db, d(deal.project.price), deal.price_currency, inv.currency) if deal.project is not None else net
        for _b, _k, _u, _r, part in [b for b in bens if b[0] == "aliado"]:
            for r in part.requests:
                if r.status != "aprobado" or d(r.amount) <= 0 or venta <= 0:
                    continue
                ya = _payout_accrued(db, r)
                parte = min(d(r.amount) * raw / venta, d(r.amount) - ya)
                if parte <= 0:
                    continue
                c = _accrue(
                    db,
                    deal=deal,
                    source_key=f"cobro:{payment.id}:pago:{r.id}",
                    beneficiary="aliado",
                    participation=part,
                    kind="pago_acordado",
                    trigger="cobro",
                    base="venta",
                    base_sale=raw,
                    base_amount=d(r.amount),
                    percent=0,
                    amount=parte,
                    currency=r.currency,
                    earned_on=on,
                    description=f"Pago acordado: {r.what[:150]} · {inv.number}",
                    cost_request=r,
                    payment=payment,
                )
                if c is not None:
                    out.append(c)
    return out


def _payout_accrued(db: Session, r: AllyCostRequest) -> Decimal:
    v = db.scalar(
        select(func.coalesce(func.sum(Commission.amount), 0)).where(
            Commission.tenant_id == r.tenant_id, Commission.cost_request_id == r.id, Commission.status != "anulada"
        )
    )
    return d(v)


def _group_key(c: Commission) -> str:
    if c.cost_request_id and c.kind in ("pago_acordado", "ajuste"):
        return f"pago:{c.cost_request_id}"
    if c.beneficiary == "aliado":
        return f"a:{c.participation_id}"
    return f"v:{c.user_id}"


def _shrink_held(db: Session, held: list[Commission], ref_held: Decimal, payment: Payment, inv: Invoice) -> None:
    """Lo que sigue en espera (utilidad sin costos o proyecto abierto) no tiene monto que revertir: la devolucion le
    baja la base de venta guardada, asi el recalculo y el tope de la factura ya no cuentan la plata devuelta. Si se
    devolvio todo, se anula. Cada fila anota el pago para que reprocesar (backfill) no lo descuente dos veces."""
    held_sale = sum((_base_sale_of(c) for c in held), Decimal(0))
    if held_sale <= 0 or ref_held <= 0:
        return
    for c in held:
        s = _base_sale_of(c)
        if s <= 0:
            continue
        nuevo = q2(max(Decimal(0), s - ref_held * s / held_sale))
        t = dict(c.terms or {})
        t["base_sale"] = str(nuevo)
        t["devoluciones"] = [*(t.get("devoluciones") or []), payment.id]
        antes = {"base_sale": str(q2(s)), "status": c.status}
        c.terms = t
        if nuevo <= 0:
            c.status, c.void_reason, c.hold_reason = "anulada", f"Se devolvió lo cobrado · {inv.number}"[:240], None
        audit(db, c.tenant_id, None, "refund", "commission", c.id, before=antes, after={"base_sale": str(nuevo), "status": c.status, "pago": payment.id})


def _on_refund(db: Session, payment: Payment, inv: Invoice) -> list[Commission]:
    """Devolucion o reembolso: ajuste negativo a la tasa efectiva de cada beneficiario, nunca mas de lo devengado.
    La plata devuelta se reparte en proporcion entre lo calculado (ajuste dev:) y lo que espera (baja su base)."""
    total, net = d(inv.total), net_sale(inv)
    if total <= 0:
        return []
    deal = deal_of(db, invoice=inv)
    ref_sale = _paid_in_doc(db, payment, inv) * net / total
    on = _fecha(payment.paid_at)
    movs = db.scalars(
        select(Commission)
        .where(
            Commission.tenant_id == inv.tenant_id,
            Commission.invoice_id == inv.id,
            Commission.status != "anulada",
            Commission.trigger == "cobro",
            Commission.earned_on <= on,  # reprocesar (backfill) no le cobra la devolucion a cobros posteriores
        )
        .order_by(Commission.id)
    ).all()
    groups: dict[str, list[Commission]] = {}
    for c in movs:
        groups.setdefault(_group_key(c), []).append(c)
    out = []
    for k, rows in groups.items():
        held = [c for c in rows if c.kind != "ajuste" and c.status == "en_espera"]
        base_rows = [c for c in rows if c.kind != "ajuste" and c.status != "en_espera"]
        held_sale = sum((_base_sale_of(c) for c in held), Decimal(0))
        sale = sum((_base_sale_of(c) for c in base_rows), Decimal(0))
        devuelto = -sum((_base_sale_of(c) for c in rows if (c.source_key or "").startswith("dev:")), Decimal(0))
        vivo = held_sale + max(sale - devuelto, Decimal(0))
        if vivo <= 0:
            continue
        ref = min(ref_sale, vivo)  # devolver un sobrepago no baja el tope por debajo de 0
        ref_held = ref * held_sale / vivo
        if not any(payment.id in ((c.terms or {}).get("devoluciones") or []) for c in held):
            _shrink_held(db, held, ref_held, payment, inv)
        ref_done = ref - ref_held
        earned = sum((d(c.amount) for c in base_rows), Decimal(0))
        neto = sum((d(c.amount) for c in rows), Decimal(0))
        if ref_done <= 0 or sale <= 0 or neto <= 0:
            continue
        ajuste = -min(ref_done * earned / sale, neto)
        if q2(ajuste) == 0:
            continue
        last = rows[-1]
        part = db.get(AllyParticipation, last.participation_id) if last.participation_id else None
        req = db.get(AllyCostRequest, last.cost_request_id) if last.cost_request_id else None
        c = _accrue(
            db,
            deal=deal,
            source_key=f"dev:{payment.id}:{k}",
            beneficiary=last.beneficiary,
            user_id=last.user_id,
            participation=part,
            ally_company_id=last.ally_company_id,
            kind="ajuste",
            trigger="cobro",
            base=last.base,
            base_sale=-ref_done,
            base_amount=0,
            percent=0,
            amount=ajuste,
            currency=last.currency,
            earned_on=on,
            description=f"Ajuste por devolución · {inv.number}",
            cost_request=req,
            payment=payment,
            status="pendiente",
            reverses_id=last.id,
        )
        if c is not None:
            out.append(c)
    return out


# ---------- 5.8 Reverso o anulacion ----------
def reverse_or_void(db: Session, movs, motivo: str, user_id: int | None = None) -> int:
    """Lo no pagado se anula; lo pagado se corrige con un ajuste negativo pendiente. Nunca se borra nada."""
    n = 0
    for c in movs:
        if _is_reversal(c):
            continue  # rev:{id} ya corrige uno pagado (que sigue vivo): anularlo o revertirlo volveria a deber lo pagado
        if c.status in ("en_espera", "pendiente", "aprobada"):
            antes = c.status
            c.status, c.void_reason = "anulada", motivo[:240]
            audit(db, c.tenant_id, user_id, "status", "commission", c.id, {"de": antes, "a": "anulada", "motivo": motivo[:240]})
            n += 1
        elif c.status == "pagada":
            key = f"rev:{c.id}"
            if _exists(db, c.tenant_id, key):
                continue
            r = Commission(
                tenant_id=c.tenant_id,
                user_id=c.user_id,
                beneficiary=c.beneficiary,
                ally_company_id=c.ally_company_id,
                participation_id=c.participation_id,
                invoice_id=c.invoice_id,
                quote_id=c.quote_id,
                project_id=c.project_id,
                opportunity_id=c.opportunity_id,
                cost_request_id=c.cost_request_id,
                rule_id=c.rule_id,
                base=c.base,
                base_amount=0,
                percent=0,
                amount=-d(c.amount),
                currency=c.currency,
                fx=c.fx,
                amount_crc=-crc_of(db, c),
                earned_on=date.today(),
                status="pendiente",
                kind="ajuste",
                trigger=c.trigger,
                source_key=key,
                terms={"rule": (c.terms or {}).get("rule"), "base_sale": "0", "doc": (c.terms or {}).get("doc")},
                description=f"Reverso: {motivo}"[:240],
                reverses_id=c.id,
            )
            db.add(r)
            db.flush()
            audit(db, c.tenant_id, user_id, "reverse", "commission", c.id, {"ajuste": r.id, "motivo": motivo[:240]})
            n += 1
    return n


def _live(db: Session, tenant_id: int, *where) -> list[Commission]:
    return list(db.scalars(select(Commission).where(Commission.tenant_id == tenant_id, Commission.status != "anulada", *where).order_by(Commission.id)))


# ---------- E6 · Factura anulada ----------
def on_invoice_void(db: Session, inv: Invoice, user_id: int | None = None) -> int:
    """Lo devengado con la factura y, si viene de una cotizacion, lo devengado "al aceptar" esa cotizacion: nacio sin
    factura (invoice_id null) y una cotizacion convertida ya no se anula (sales.void_quote da 409), asi que anular la
    factura (o su NC) es el unico camino para revertirlo (falla E)."""
    cond = Commission.invoice_id == inv.id
    if inv.quote_id:
        cond = or_(cond, and_(Commission.quote_id == inv.quote_id, Commission.trigger == "cotizacion_aceptada"))
    return reverse_or_void(db, _live(db, inv.tenant_id, cond), f"Factura {inv.number} anulada", user_id)


# ---------- E2 · Cotizacion aceptada ----------
def _accepted_count(db: Session, q: Quote) -> int:
    return db.scalar(select(func.count()).select_from(QuoteAcceptance).where(QuoteAcceptance.quote_id == q.id, QuoteAcceptance.status == "aceptada")) or 0


def _accrue_quote(db: Session, q: Quote, n: int, on: date, only_missing: bool) -> list[Commission]:
    base_sale = net_sale(q)
    if base_sale <= 0:
        return []
    deal = deal_of(db, quote=q)
    cfg = _cfg(db, q.tenant_id)
    rules = _rules(db, q.tenant_id)
    out = []
    for ben in _beneficiaries(db, deal, cfg):
        beneficiary, key, uid, _role, part = ben
        rule = _pick(rules, cfg, deal, on, ben)
        if rule is None or trigger_of(rule, deal) != "cotizacion_aceptada":
            continue
        if only_missing:
            grupo = list(
                db.scalars(
                    select(Commission)
                    .where(Commission.tenant_id == q.tenant_id, Commission.quote_id == q.id, Commission.trigger == "cotizacion_aceptada", *_sale_filter(beneficiary, part))
                    .order_by(Commission.id)
                )
            )
            # aceptar y despues convertir devenga una sola vez (uno pagado y revertido ya no cuenta), aunque la cotizacion
            # haya cambiado de vendedor entre la aceptacion y la conversion; y lo que contabilidad anulo a mano no vuelve solo
            if _still_earned([c for c in grupo if c.status != "anulada"]) or _manually_voided(grupo):
                continue
        hold = None
        if rule.base == "margen":
            base_amount, hold = margin_base(db, deal, base_sale, "cotizacion_aceptada")
            if hold is None and (base_amount is None or base_amount <= 0):
                continue
        else:
            base_amount = base_sale
        sk = f"cot:{q.id}:{key}:{n}"
        if only_missing:
            # aceptar, deshacer (anulada o revertida) y convertir: la conversion es aceptacion implicita y devenga con clave nueva
            sk = _fresh_key(db, q.tenant_id, sk)
        c = _accrue(
            db,
            deal=deal,
            source_key=sk,
            beneficiary=beneficiary,
            user_id=uid,
            participation=part,
            rule=rule,
            kind="comision",
            trigger="cotizacion_aceptada",
            base=rule.base,
            base_sale=base_sale,
            base_amount=ZERO if hold else base_amount,
            percent=0 if _is_fixed(rule) else rule.percent,
            amount=ZERO if hold else _rule_amount(db, rule, base_amount, Decimal(1), q.currency),
            currency=q.currency,
            earned_on=on,
            description=describe(rule, "cotizacion_aceptada", q.number),
            hold_reason=hold,
        )
        if c is not None:
            out.append(c)
    return out


def on_quote_acceptance(db: Session, q: Quote, antes: str | None, despues: str, decided_on: date | None = None) -> list[Commission]:
    """Desde sales.quote_acceptance. Cada aceptacion nueva (tras deshacer) tiene su propia clave. Una "aceptada" sobre
    otra ya aceptada (otra version, doble envio desde dos pestanas) solo completa lo que falte: nunca duplica."""
    if despues == "aceptada":
        db.flush()
        return _accrue_quote(db, q, _accepted_count(db, q), decided_on or date.today(), only_missing=antes == "aceptada")
    if antes == "aceptada":
        motivo = "El cliente deshizo la aceptación" if despues == "pendiente" else "El cliente rechazó la cotización"
        reverse_or_void(db, _live(db, q.tenant_id, Commission.quote_id == q.id, Commission.trigger == "cotizacion_aceptada"), f"{motivo} · {q.number}")
    return []


def on_quote_won(db: Session, q: Quote) -> list[Commission]:
    """Convertir a factura o a proyecto es aceptacion implicita: devenga si nadie tiene un movimiento vivo."""
    db.flush()
    return _accrue_quote(db, q, _accepted_count(db, q), date.today(), only_missing=True)


def on_quote_void(db: Session, q: Quote, user_id: int | None = None) -> int:
    return reverse_or_void(
        db, _live(db, q.tenant_id, Commission.quote_id == q.id, Commission.trigger == "cotizacion_aceptada"), f"Cotización {q.number} anulada", user_id
    )


# ---------- E3 · Proyecto completado ----------
def _bonus_amount(db: Session, rule, price: Decimal, currency: str) -> tuple[Decimal, str]:
    if _is_fixed(rule):
        return d(rule.fixed_amount), rule.fixed_currency or "CRC"
    return price * d(rule.percent) / 100, currency


def early_ok(rule, on: date) -> bool:
    if rule is None or rule.committed_date is None:
        return False
    days = rule.early_days if rule.early_days is not None else 1
    return on <= rule.committed_date - timedelta(days=days)


def _accrue_payout(db: Session, deal: Deal, part: AllyParticipation, r: AllyCostRequest, on: date) -> Commission | None:
    if r.status != "aprobado" or d(r.amount) <= 0:
        return None
    # cuenta el neto, no que exista una fila: pagado y revertido (pagada + rev:) neto da 0 y al re-aprobar se debe otra vez
    falta = d(r.amount) - _payout_accrued(db, r)
    if q2(falta) <= 0:
        return None  # ya se le debe (o ya se le pago)
    # se desaprobo (quedo anulado o revertido) y se volvio a aprobar: un movimiento nuevo, el viejo queda de historial
    key = _fresh_key(db, r.tenant_id, f"pago:{r.id}")
    doc = deal.project.number if deal.project is not None else deal.number
    return _accrue(
        db,
        deal=deal,
        source_key=key,
        beneficiary="aliado",
        participation=part,
        kind="pago_acordado",
        trigger="proyecto_completado",
        base="venta",
        base_sale=d(deal.project.price) if deal.project is not None else ZERO,
        base_amount=d(r.amount),
        percent=0,
        amount=falta,
        currency=r.currency,
        earned_on=on,
        description=f"Pago acordado: {r.what[:150]} · {doc}",
        cost_request=r,
    )


def _family_key(db: Session, tenant_id: int, key: str) -> str | None:
    """Para completar otra vez un proyecto que se cancelo: None si lo devengado con esa clave (o sus key:re{n}) sigue
    en pie; si todo quedo anulado o revertido, una clave libre (lo viejo queda de historial)."""
    fam = list(
        db.scalars(select(Commission).where(Commission.tenant_id == tenant_id, or_(Commission.source_key == key, Commission.source_key.like(f"{key}:re%"))))
    )
    if fam:
        revs = db.scalars(select(Commission).where(Commission.tenant_id == tenant_id, Commission.reverses_id.in_([c.id for c in fam]))).all()
        if _still_earned(fam + list(revs)):
            return None
    return _fresh_key(db, tenant_id, key)


def _accrue_project(db: Session, pr: Project, again: bool = False) -> list[Commission]:
    """again: completar desde on_project_status. Si el proyecto ya se habia completado y se cancelo (todo quedo anulado o
    revertido), devenga otra vez con clave nueva. El backfill no lo usa: ahi la clave existente manda (idempotente)."""
    deal = deal_of(db, project=pr)
    cfg = _cfg(db, pr.tenant_id)
    rules = _rules(db, pr.tenant_id)
    on = local_date(pr.completed_at) or date.today()
    price, cur = d(pr.price), deal.price_currency
    out: list[Commission | None] = []
    bens = _beneficiaries(db, deal, cfg)

    def sk(base: str) -> str | None:
        return _family_key(db, pr.tenant_id, base) if again else base

    for ben in bens:
        beneficiary, key, uid, _role, part = ben
        suf = f"v:{uid}" if beneficiary == "vendedor" else str(part.id)
        rule = _pick(rules, cfg, deal, on, ben)
        if rule is not None and trigger_of(rule, deal) == "proyecto_completado" and price > 0 and (fin_key := sk(f"fin:{pr.id}:{key}")) is not None:
            hold = None
            if rule.base == "margen":
                base_amount, hold = margin_base(db, deal, price, "proyecto_completado")
                skip = hold is None and (base_amount is None or base_amount <= 0)
            else:
                base_amount, skip = price, False
            if not skip:
                out.append(
                    _accrue(
                        db,
                        deal=deal,
                        source_key=fin_key,
                        beneficiary=beneficiary,
                        user_id=uid,
                        participation=part,
                        rule=rule,
                        kind="comision",
                        trigger="proyecto_completado",
                        base=rule.base,
                        base_sale=price,
                        base_amount=ZERO if hold else base_amount,
                        percent=0 if _is_fixed(rule) else rule.percent,
                        amount=ZERO if hold else _rule_amount(db, rule, base_amount, Decimal(1), cur),
                        currency=cur,
                        earned_on=on,
                        description=describe(rule, "proyecto_completado", pr.number),
                        hold_reason=hold,
                    )
                )
        fin = _pick(rules, cfg, deal, on, ben, "bono_fin")
        bono_key = sk(f"bono_fin:{pr.id}:{suf}") if fin is not None else None
        if fin is not None and bono_key is not None:
            amt, bcur = _bonus_amount(db, fin, price, cur)
            out.append(
                _accrue(
                    db,
                    deal=deal,
                    source_key=bono_key,
                    beneficiary=beneficiary,
                    user_id=uid,
                    participation=part,
                    rule=fin,
                    kind="bono",
                    trigger="proyecto_completado",
                    base="venta",
                    base_sale=price,
                    base_amount=0 if _is_fixed(fin) else price,
                    percent=0 if _is_fixed(fin) else fin.percent,
                    amount=amt,
                    currency=bcur,
                    earned_on=on,
                    description=f"Bono por completar · {pr.number}",
                )
            )
        ant = _pick(rules, cfg, deal, on, ben, "bono_anticipo")
        ant_key = sk(f"bono_ant:{pr.id}:{suf}") if ant is not None and early_ok(ant, on) else None
        if ant is not None and ant_key is not None:
            amt, bcur = _bonus_amount(db, ant, price, cur)
            out.append(
                _accrue(
                    db,
                    deal=deal,
                    source_key=ant_key,
                    beneficiary=beneficiary,
                    user_id=uid,
                    participation=part,
                    rule=ant,
                    kind="bono",
                    trigger="proyecto_completado",
                    base="venta",
                    base_sale=price,
                    base_amount=0 if _is_fixed(ant) else price,
                    percent=0 if _is_fixed(ant) else ant.percent,
                    amount=amt,
                    currency=bcur,
                    earned_on=on,
                    description=f"Bono por terminar antes del {ant.committed_date.strftime('%d/%m/%Y')} · {pr.number}",
                )
            )
    if cfg["ally_payout_trigger"] == "proyecto_completado":
        for _b, _k, _u, _r, part in [b for b in bens if b[0] == "aliado"]:
            for r in part.requests:
                out.append(_accrue_payout(db, deal, part, r, on))
    return [c for c in out if c is not None]


def on_project_status(db: Session, pr: Project, antes: str | None) -> list[Commission]:
    """Desde projects.project_update, project_invoice y fieldwork (ultima OT). No usa permisos del que dispara:
    un tecnico que cierra la ultima OT tambien completa el proyecto."""
    out: list[Commission] = []
    if pr.status in CLOSED and pr.completed_at is None:
        pr.completed_at = datetime.now(UTC)
        db.flush()
        out = _accrue_project(db, pr, again=True)
    elif pr.status == "cancelado" and antes != "cancelado":
        reverse_or_void(
            db, _live(db, pr.tenant_id, Commission.project_id == pr.id, Commission.trigger == "proyecto_completado"), f"Proyecto {pr.number} cancelado"
        )
        # ya no esta completado: si vuelve a cerrarse se completa otra vez y devenga con claves nuevas (lo anulado o
        # revertido queda de historial), en vez de quedarse sin comisiones para siempre
        pr.completed_at = None
    if pr.status in CLOSED and antes not in CLOSED:
        # lo cobrado con el proyecto abierto quedo en espera de su utilidad (HOLD_PROJECT): al cerrar se recalcula, lo
        # cierre quien lo cierre (ultima OT, facturar el proyecto o editarlo)
        on_project_costs(db, pr)
    return out


def link_to_project(db: Session, pr: Project) -> int:
    """Lo devengado antes de que existiera el proyecto (al aceptar la cotizacion, cobros de la factura convertida antes
    de crear el proyecto) nacio con project_id NULL: sin esto no entraba en la economia del proyecto, en los reportes de
    rentabilidad ni en lo ya ganado de la proyeccion del aliado. Se llama al crear el proyecto y al facturarlo."""
    conds = []
    if pr.quote_id:
        conds.append(Commission.quote_id == pr.quote_id)
        q = db.get(Quote, pr.quote_id)
        if q is not None and q.converted_invoice_id:
            conds.append(Commission.invoice_id == q.converted_invoice_id)
    if pr.invoice_id:
        conds.append(Commission.invoice_id == pr.invoice_id)
    if not conds:
        return 0
    n = 0
    for c in db.scalars(select(Commission).where(Commission.tenant_id == pr.tenant_id, Commission.project_id.is_(None), or_(*conds))):
        c.project_id = pr.id
        if c.opportunity_id is None and pr.opportunity_id:
            c.opportunity_id = pr.opportunity_id
        n += 1
    if n:
        db.flush()
    return n


def project_earned(db: Session, pr: Project) -> bool:
    """Lo devengado al completar el proyecto (comision, bonos, pago acordado) sigue en pie: cancelarlo lo anula o lo
    revierte. projects.project_update exige commissions.aprobar para eso."""
    return _still_earned(_live(db, pr.tenant_id, Commission.project_id == pr.id, Commission.trigger == "proyecto_completado"))


def on_project_costs(db: Session, pr: Project) -> int:
    """E4: los movimientos que esperan (o ya calcularon) la utilidad del proyecto se recalculan. Aprobados no."""
    n = 0
    for c in db.scalars(
        select(Commission).where(
            Commission.tenant_id == pr.tenant_id, Commission.project_id == pr.id, Commission.base == "margen", Commission.status.in_(("en_espera", "pendiente"))
        )
    ).all():
        if recalc(db, c):
            n += 1
    return n


def _deal_of_entry(db: Session, c: Commission) -> Deal | None:
    if c.invoice_id:
        inv = db.get(Invoice, c.invoice_id)
        if inv is not None:
            return deal_of(db, invoice=inv)
    if c.project_id:
        pr = db.get(Project, c.project_id)
        if pr is not None:
            return deal_of(db, project=pr)
    if c.quote_id:
        q = db.get(Quote, c.quote_id)
        if q is not None:
            return deal_of(db, quote=q)
    return None


def recalc(db: Session, c: Commission, user_id: int | None = None) -> bool:
    """Recalcula un movimiento en espera o pendiente con la regla y la base de venta GUARDADAS (la regla vigente al
    devengar manda). Sale de espera -> pendiente; sin utilidad -> anulado con motivo."""
    if c.status not in ("en_espera", "pendiente") or c.base != "margen" or c.kind != "comision":
        return False
    deal = _deal_of_entry(db, c)
    if deal is None:
        return False
    t = c.terms or {}
    rule = t.get("rule") or {}
    base_sale = d(t.get("base_sale") or 0)
    base_amount, hold = margin_base(db, deal, base_sale, c.trigger)
    antes = {"status": c.status, "amount": str(c.amount), "hold_reason": c.hold_reason}
    if hold:
        if c.hold_reason == hold:
            return False
        c.hold_reason = hold[:240]
    elif base_amount is None or base_amount <= 0:
        c.status, c.void_reason, c.hold_reason = "anulada", "El negocio no dejó utilidad", None
    else:
        if c.trigger == "cobro" and deal.invoice is not None and net_sale(deal.invoice) > 0:
            share = base_sale / net_sale(deal.invoice)
        else:
            share = Decimal(1)
        amount = q2(_amount_for(db, rule.get("fixed_amount"), rule.get("fixed_currency"), rule.get("percent") or 0, base_amount, share, c.currency))
        c.base_amount, c.amount = q2(base_amount), amount
        c.amount_crc = q2(amount * d(c.fx or _fx_for(db, c.currency, deal.invoice or deal.quote)))
        c.hold_reason = None
        if c.status == "en_espera":
            c.status = "pendiente"
    despues = {"status": c.status, "amount": str(c.amount), "hold_reason": c.hold_reason}
    if antes == despues:
        return False
    audit(db, c.tenant_id, user_id, "recalc", "commission", c.id, before=antes, after=despues)
    return True


# ---------- E5 · Precio del aliado ----------
def _participation_invoice(db: Session, part: AllyParticipation) -> Invoice | None:
    """La factura del negocio de la participacion (la del proyecto o la de su cotizacion convertida)."""
    pr = db.get(Project, part.project_id) if part.project_id else None
    if pr is not None and pr.invoice_id:
        return db.get(Invoice, pr.invoice_id)
    q = db.get(Quote, pr.quote_id) if pr is not None and pr.quote_id else None
    if q is None and part.opportunity_id:
        opp = db.get(Opportunity, part.opportunity_id)
        q = db.get(Quote, opp.quote_id) if opp is not None and opp.quote_id else None
    if q is not None and q.converted_invoice_id:
        return db.get(Invoice, q.converted_invoice_id)
    return None


def _paid_sale(db: Session, inv: Invoice) -> Decimal:
    """Venta sin IVA ya cobrada en la factura (capturas confirmadas menos devoluciones y reembolsos), en su moneda."""
    total, net = d(inv.total), net_sale(inv)
    if total <= 0 or net <= 0:
        return ZERO
    s = Decimal(0)
    for pay in db.scalars(
        select(Payment).where(
            Payment.tenant_id == inv.tenant_id,
            Payment.invoice_id == inv.id,
            Payment.status == "confirmado",
            Payment.kind.in_(("captura", "devolucion", "reembolso")),
        )
    ):
        v = _paid_in_doc(db, pay, inv) * net / total
        s += v if pay.kind == "captura" else -v
    return max(s, Decimal(0))


def _accrue_paid_payout(db: Session, part: AllyParticipation, r: AllyCostRequest, cfg: dict) -> Commission | None:
    """Precio aprobado DESPUES de cobrar, cuando el pago acordado se devenga al cobrar (venta sin proyecto o
    ally_payout_trigger = cobro): devenga ya la parte cobrada (precio x cobrado sin IVA / venta), con la misma cuenta
    que _on_capture. Los cobros siguientes completan el resto; nunca pasa del precio (spec E5)."""
    if r.status != "aprobado" or d(r.amount) <= 0:
        return None
    inv = _participation_invoice(db, part)
    if inv is None or inv.status == "anulada":
        return None
    deal = deal_of(db, invoice=inv)
    if not (cfg["ally_payout_trigger"] == "cobro" or deal.project is None):
        return None  # se devenga al completar el proyecto
    venta = convert(db, d(deal.project.price), deal.price_currency, inv.currency) if deal.project is not None else net_sale(inv)
    cobrado = _paid_sale(db, inv)
    if venta <= 0 or cobrado <= 0:
        return None
    parte = min(d(r.amount) * cobrado / venta, d(r.amount) - _payout_accrued(db, r))
    if parte <= 0:
        return None
    key = f"pago:{r.id}:cobrado"
    if _exists(db, r.tenant_id, key):
        # se desaprobo (quedo anulado o revertido) y se volvio a aprobar: un movimiento nuevo
        n = db.scalar(select(func.count()).select_from(Commission).where(Commission.tenant_id == r.tenant_id, Commission.source_key.like(f"{key}%")))
        key = f"{key}:re{n}"
    return _accrue(
        db,
        deal=deal,
        source_key=key,
        beneficiary="aliado",
        participation=part,
        kind="pago_acordado",
        trigger="cobro",
        base="venta",
        base_sale=cobrado,
        base_amount=d(r.amount),
        percent=0,
        amount=parte,
        currency=r.currency,
        earned_on=date.today(),
        description=f"Pago acordado: {r.what[:150]} · {inv.number}",
        cost_request=r,
    )


def on_cost_request_change(db: Session, r: AllyCostRequest, antes_status: str | None, antes_amount) -> list[Commission]:
    part = r.participation or db.get(AllyParticipation, r.participation_id)
    movs = _live(db, r.tenant_id, Commission.cost_request_id == r.id)
    if antes_status == "aprobado" and r.status != "aprobado":
        reverse_or_void(db, movs, f"Precio del aliado «{r.what[:80]}» ya no está aprobado")
        return []
    if r.status != "aprobado" or part is None:
        return []
    pr = db.get(Project, part.project_id) if part.project_id else None
    cfg = _cfg(db, r.tenant_id)
    if antes_status != "aprobado":
        if pr is not None and pr.completed_at is not None and cfg["ally_payout_trigger"] == "proyecto_completado":
            c = _accrue_payout(db, deal_of(db, project=pr), part, r, date.today())  # aprobado despues de completar
            return [c] if c is not None else []
        c = _accrue_paid_payout(db, part, r, cfg)  # se devenga al cobrar: lo ya cobrado no vuelve a pasar por _on_capture
        return [c] if c is not None else []
    if d(antes_amount) == d(r.amount):
        return []
    fin = next((c for c in movs if c.kind == "pago_acordado" and c.trigger == "proyecto_completado"), None)
    if fin is not None and fin.status in ("pendiente", "en_espera") and len(movs) == 1:
        antes = {"amount": str(fin.amount)}
        fin.amount, fin.base_amount = q2(r.amount), q2(r.amount)
        fin.currency = r.currency
        fin.amount_crc = q2(d(r.amount) * _rate(db, r.currency))
        audit(db, r.tenant_id, None, "update", "commission", fin.id, before=antes, after={"amount": str(fin.amount)})
        return []
    if not movs:
        return []
    base = movs[0]
    deal = _deal_of_entry(db, base) or (deal_of(db, project=pr) if pr is not None else None)
    if deal is None:
        return []
    objetivo = d(r.amount)
    if base.trigger == "cobro" and deal.invoice is not None:
        # devengado al cobrar ("pago contra pago"): la diferencia solo cubre la parte ya cobrada; el resto sale en los
        # cobros siguientes (_on_capture con el precio nuevo)
        inv = deal.invoice
        venta = convert(db, d(deal.project.price), deal.price_currency, inv.currency) if deal.project is not None else net_sale(inv)
        cobrado = sum((_base_sale_of(c) for c in movs if c.kind == "pago_acordado" or (c.source_key or "").startswith("dev:")), Decimal(0))
        if venta > 0:
            objetivo = min(objetivo, objetivo * max(cobrado, Decimal(0)) / venta)
    diff = objetivo - sum((d(c.amount) for c in movs), Decimal(0))
    if q2(diff) == 0:
        return []
    n = sum(1 for c in movs if (c.source_key or "").startswith(f"pago:{r.id}:dif:")) + 1
    c = _accrue(
        db,
        deal=deal,
        source_key=f"pago:{r.id}:dif:{n}",
        beneficiary="aliado",
        participation=part,
        kind="ajuste",
        trigger=base.trigger,
        base="venta",
        base_sale=0,
        base_amount=0,
        percent=0,
        amount=diff,
        currency=r.currency,
        earned_on=date.today(),
        description=f"Ajuste del pago acordado: {r.what[:120]}",
        cost_request=r,
        status="pendiente",
        reverses_id=base.id,
    )
    return [c] if c is not None else []


def has_live_movements(db: Session, *, request: AllyCostRequest | None = None, participation: AllyParticipation | None = None) -> bool:
    w = []
    if request is not None:
        w.append(Commission.cost_request_id == request.id)
        tid = request.tenant_id
    else:
        w.append(Commission.participation_id == participation.id)
        tid = participation.tenant_id
    return db.scalar(select(Commission.id).where(Commission.tenant_id == tid, Commission.status != "anulada", *w).limit(1)) is not None


def drop_participation_rules(db: Session, part: AllyParticipation, p=None) -> None:
    """Antes de quitar la participacion: sus reglas por proyecto se van con ella. Los movimientos anulados que las
    usaron conservan la foto de la regla (terms) y sueltan el enlace. Cada regla borrada queda en la bitacora (como
    rule_delete): el acuerdo economico lo pacto administracion y quien quita al aliado puede ser la vendedora."""
    from . import bitacora

    ids = list(db.scalars(select(CommissionRule.id).where(CommissionRule.tenant_id == part.tenant_id, CommissionRule.participation_id == part.id)))
    if not ids:
        return
    for c in db.scalars(select(Commission).where(Commission.tenant_id == part.tenant_id, Commission.rule_id.in_(ids))):
        c.rule_id = None
    motivo = f"Se quitó el aliado {part.name} del negocio"
    for r in db.scalars(select(CommissionRule).where(CommissionRule.id.in_(ids))):
        if p is not None:
            bitacora.audit_change(db, p, "delete", "commission_rule", r.id, bitacora.snapshot(r), {}, reason=motivo, always=True)
        else:
            audit(db, r.tenant_id, None, "delete", "commission_rule", r.id, {"motivo": motivo})
        db.delete(r)
    db.flush()


# ---------- Contadores y visibilidad ----------
def approvals_count(db: Session, p) -> int:
    """Pendientes + en espera del tenant (los usa Inicio). Sin ver_todo, solo los propios."""
    w = [Commission.tenant_id == p.tenant.id, Commission.status.in_(("pendiente", "en_espera"))]
    if not p.can("commissions", "ver_todo"):
        w.append(Commission.user_id == p.user.id)
    return db.scalar(select(func.count()).select_from(Commission).where(*w)) or 0


def hides_margin(p, cfg: dict) -> bool:
    return not p.sees_costs and not cfg.get("show_margin_base")


def is_payout(c: Commission) -> bool:
    """Pago acordado al aliado o su ajuste (diferencia de precio, reverso, devolucion): su monto es el precio del
    aliado, que para Crimson es costo directo."""
    return c.kind == "pago_acordado" or bool(c.cost_request_id)


def hides_payout(p) -> bool:
    """El precio del aliado solo sale con catalog.costos (spec 0.9 y 08:07) o a quien lo aprueba y paga en la bandeja
    (contabilidad). Lectura (socio, auditor) ve el resto del libro sin esos montos."""
    return not p.sees_costs and not p.can("commissions", "aprobar")


def safe_description(c: Commission, hide_margin: bool) -> str | None:
    """Concepto sin el % de una comision sobre la utilidad (filas devengadas antes de que describe() lo quitara)."""
    txt = c.description
    if hide_margin and c.base == "margen" and (c.kind or "comision") == "comision" and txt and "%" in txt:
        doc = (c.terms or {}).get("doc")
        return f"{MARGIN_CONCEPT} · {doc}" if doc else MARGIN_CONCEPT
    return txt


class Names:
    """Cache de nombres para listas (usuarios, aliados, documentos)."""

    def __init__(self, db: Session):
        self.db = db
        self._c: dict = {}

    def get(self, model, oid, attr):
        if not oid:
            return None
        k = (model, oid)
        if k not in self._c:
            o = self.db.get(model, oid)
            self._c[k] = getattr(o, attr, None) if o is not None else None
        return self._c[k]

    def user(self, uid):
        return self.get(User, uid, "full_name")

    def ally(self, aid):
        return self.get(AllyCompany, aid, "name")


def can_touch(p, c: Commission) -> bool:
    """Segregacion de funciones (falla G): nadie aprueba ni paga lo suyo, salvo el admin."""
    return p.role == "admin" or c.user_id != p.user.id


def entry_out(db: Session, c: Commission, p, cfg: dict, names: Names | None = None) -> dict:
    names = names or Names(db)
    hide = c.base == "margen" and hides_margin(p, cfg)
    payout = is_payout(c)
    # en el pago acordado la base es el precio completo del aliado (costo de Crimson); el monto lo ve quien lo paga
    hide_base = hide or (payout and not p.sees_costs)
    hide_amount = payout and hides_payout(p)
    ben_name = names.user(c.user_id) if c.beneficiary == "vendedor" else names.ally(c.ally_company_id)
    return {
        "id": c.id,
        "user_id": c.user_id,
        "user": names.user(c.user_id),
        "invoice": names.get(Invoice, c.invoice_id, "number"),
        "invoice_id": c.invoice_id,
        "base": c.base,
        # sobre margen, la base es la utilidad y el % la deja deducir: solo quien ve costos (o si la empresa lo habilita)
        "base_amount": None if hide_base else c.base_amount,
        "percent": None if hide else c.percent,
        "amount": None if hide_amount else c.amount,
        "currency": c.currency,
        "earned_on": c.earned_on,
        "status": c.status,
        "paid_on": c.paid_on,
        "beneficiary": c.beneficiary or "vendedor",
        "beneficiary_name": ben_name,
        "ally_company_id": c.ally_company_id,
        "kind": c.kind or "comision",
        "trigger": c.trigger or "cobro",
        "description": safe_description(c, hide),
        "project_id": c.project_id,
        "project": names.get(Project, c.project_id, "number"),
        "quote_id": c.quote_id,
        "quote": names.get(Quote, c.quote_id, "number"),
        "participation_id": c.participation_id,
        "amount_crc": None if hide_amount else q2(crc_of(db, c)),
        "hold_reason": c.hold_reason,
        "approved_at": c.approved_at,
        "approved_by": names.user(c.approved_by),
        "paid_ref": c.paid_ref,
        "void_reason": c.void_reason,
        "reverses_id": c.reverses_id,
        "can_approve": bool(p.can("commissions", "aprobar") and TRANSITIONS_OK(c.status) and can_touch(p, c)),
    }


def TRANSITIONS_OK(status: str) -> bool:  # noqa: N802 - se lee como constante
    from ..models.support import TRANSITIONS

    return bool(TRANSITIONS.get(status))


def ally_entry_out(c: Commission, names: Names) -> dict:
    """Lo que ve el aliado de un movimiento: su monto y su concepto. Nunca la factura de Crimson al cliente, la base
    ni el %, ni montos de venta (aliado_final §4, regla 4)."""
    return {
        "id": c.id,
        "earned_on": c.earned_on,
        "kind": c.kind or "comision",
        "kind_label": KIND_LABEL.get(c.kind or "comision", c.kind),
        "description": _ally_description(c, names),
        "amount": c.amount,
        "currency": c.currency,
        "status": c.status,
        "paid_on": c.paid_on,
        "project_id": c.project_id,
        "project": names.get(Project, c.project_id, "number"),
        "hold_reason": c.hold_reason,
    }


def _ally_description(c: Commission, names: Names) -> str:
    """El concepto para el aliado, sin texto interno de Crimson (aliado_final regla 4): nunca el % (con el monto
    despejaria la venta al cliente, lo cobrado o la utilidad) ni el numero de factura o de cotizacion. La comision se
    arma de cero (tipo + cuando + proyecto); bonos, pago acordado y ajustes conservan su concepto sin esos numeros."""
    kind = c.kind or "comision"
    pr = names.get(Project, c.project_id, "number")
    if kind == "comision":
        txt = f"{KIND_LABEL['comision']} {TRIGGER_LABEL.get(c.trigger or 'cobro', '')}".strip()
    else:
        txt = c.description or KIND_LABEL.get(kind, "")
        for num in (names.get(Invoice, c.invoice_id, "number"), names.get(Quote, c.quote_id, "number")):
            if num:
                txt = txt.replace(f" · {num}", "").replace(f" {num}", "").replace(num, "")
        if "%" in txt:  # un ajuste manual o una fila vieja con el %: solo el tipo
            txt = KIND_LABEL.get(kind, kind)
    if pr and pr not in txt:
        txt = f"{txt} · {pr}"
    return txt


# ---------- Reglas en texto ----------
def rule_summary(rule: CommissionRule, names: Names | None = None, hide_margin: bool = False) -> str:
    if (rule.kind or "comision") != "comision":
        que = RULE_KIND_LABEL[rule.kind]
        if _is_fixed(rule):
            que += f" · {rule.fixed_currency or 'CRC'} {_num(rule.fixed_amount)}"
        else:
            que += f" · {_num(rule.percent)} % de la venta del proyecto"
        if rule.kind == "bono_anticipo" and rule.committed_date:
            que += f" · antes del {rule.committed_date.strftime('%d/%m/%Y')}"
        partes = [que]
    else:
        if _is_fixed(rule):
            cuanto = f"{rule.fixed_currency or 'CRC'} {_num(rule.fixed_amount)} fijo"
        elif rule.base == "margen" and hide_margin:
            cuanto = "Un % de la utilidad (confidencial)"
        else:
            cuanto = f"{_num(rule.percent)} %"
        if rule.base == "margen":
            sobre = "" if cuanto.startswith("Un %") else " de la utilidad"
        else:
            sobre = " de lo cobrado sin IVA" if rule.trigger == "cobro" else " de la venta sin IVA"
        partes = [f"{cuanto}{sobre}", TRIGGER_LABEL.get(rule.trigger or "cobro", rule.trigger)]
    if rule.solution:
        partes.append(f"solo {SOLUTION_LABEL.get(rule.solution, rule.solution)}")
    if rule.deal_kind:
        partes.append("solo venta de equipo" if rule.deal_kind == "venta" else "solo proyectos")
    if rule.valid_from or rule.valid_to:
        desde = rule.valid_from.strftime("%d/%m/%Y") if rule.valid_from else "siempre"
        hasta = rule.valid_to.strftime("%d/%m/%Y") if rule.valid_to else "sin fin"
        partes.append(f"vigente {desde} → {hasta}")
    return " · ".join(partes)


def rules_for_user(db: Session, tenant, user_id: int, role: str | None) -> list[CommissionRule]:
    cfg = settings_of(tenant)
    stmt = select(CommissionRule).where(CommissionRule.tenant_id == tenant.id, CommissionRule.beneficiary == "vendedor")
    conds = [CommissionRule.user_id == user_id]
    if role in (cfg["general_rule_roles"] or []):
        conds.append(CommissionRule.user_id.is_(None))
    return list(db.scalars(stmt.where(or_(*conds)).order_by(CommissionRule.id)))


# ---------- 5.10 Tiempo real del vendedor ----------
def _month_key(v: date) -> str:
    return v.strftime("%Y-%m")


def _months_back(end: date, n: int) -> list[str]:
    y, m, out = end.year, end.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(out))


def month_range(a: date, b: date, cap: int = 60) -> list[str]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month) and len(out) < cap:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _estimate_margin_pct(db: Session, deal: Deal, tenant) -> Decimal:
    """Margen estimado (fraccion) para proyecciones sobre utilidad: el planificado del proyecto o el de Ajustes."""
    from . import pricing

    pr = deal.project
    if pr is not None and d(pr.price) > 0 and d(pr.cost_planned) > 0:
        from ..routers.projects import price_crc

        venta = price_crc(db, pr)  # pr.price va en la moneda de la cotizacion; cost_planned en colones (falla J)
        if venta > 0:
            return (venta - d(pr.cost_planned)) / venta
    doc = deal.invoice or deal.quote
    if doc is not None:
        cost, faltan = _doc_cost(db, doc)
        net = net_sale(doc)
        if not faltan and net > 0 and cost > 0:
            return (net - cost) / net
    return pricing.default_margin(tenant) / 100


def _projected(db: Session, tenant, rule, base_sale: Decimal, deal: Deal, currency: str, share: Decimal) -> tuple[Decimal, bool]:
    """(monto, estimado) de una regla sobre una base de venta futura."""
    if rule.base == "margen" and not _is_fixed(rule):
        return base_sale * _estimate_margin_pct(db, deal, tenant) * d(rule.percent) / 100, True
    return _rule_amount(db, rule, base_sale, share, currency), False


def summary_for_user(db: Session, tenant, user_id: int, a: date, b: date, viewer=None) -> dict:
    from ..routers.pipeline import OPEN_STATES
    from .archive import live

    cfg = settings_of(tenant)
    rules = _rules(db, tenant.id)
    role = member_role(db, tenant.id, user_id)
    u = db.get(User, user_id)
    mine = list(
        db.scalars(
            select(Commission).where(
                Commission.tenant_id == tenant.id, Commission.beneficiary == "vendedor", Commission.user_id == user_id, Commission.status != "anulada"
            )
        )
    )
    ganado = {"total": ZERO, "en_espera": 0, "pendiente": ZERO, "aprobada": ZERO, "pagada": ZERO}
    pagado = ZERO
    for c in mine:
        if a <= c.earned_on <= b:
            v = crc_of(db, c)
            ganado["total"] += v
            if c.status == "en_espera":
                ganado["en_espera"] += 1
            else:
                ganado[c.status] += v
        if c.status == "pagada" and c.paid_on and a <= c.paid_on <= b:
            pagado += crc_of(db, c)
    # por cobrar: lo que ganaria si le pagan sus facturas abiertas
    por_cobrar, n_fact, est_cobrar = Decimal(0), 0, False
    for inv in db.scalars(
        select(Invoice).where(Invoice.tenant_id == tenant.id, Invoice.seller_id == user_id, Invoice.status != "anulada", Invoice.balance > 0).limit(300)
    ):
        total, net = d(inv.total), net_sale(inv)
        if total <= 0 or net <= 0:
            continue
        deal = deal_of(db, invoice=inv)
        ben = ("vendedor", f"v:{user_id}", user_id, role, None)
        rule = _pick(rules, cfg, deal, date.today(), ben)
        if rule is None or trigger_of(rule, deal) != "cobro":
            continue
        raw = d(inv.balance) * net / total
        base_sale = max(Decimal(0), min(raw, net - _sold_base(db, inv, "vendedor", user_id, None)))
        if base_sale <= 0:
            continue
        amt, est = _projected(db, tenant, rule, base_sale, deal, inv.currency, base_sale / net)
        por_cobrar += _doc_crc(db, inv, amt)
        n_fact += 1
        est_cobrar = est_cobrar or est
    # proyectos en curso con regla al completar
    en_curso, n_pr, est_pr = Decimal(0), 0, False
    for pr in db.scalars(
        select(Project)
        .join(Quote, Quote.id == Project.quote_id)
        .where(Project.tenant_id == tenant.id, Project.status.in_(OPEN_PROJECT), Quote.seller_id == user_id, live(Project))
        .limit(200)
    ):
        deal = deal_of(db, project=pr)
        ben = ("vendedor", f"v:{user_id}", user_id, role, None)
        rule = _pick(rules, cfg, deal, date.today(), ben)
        tot = Decimal(0)
        if rule is not None and trigger_of(rule, deal) == "proyecto_completado":
            amt, est = _projected(db, tenant, rule, d(pr.price), deal, deal.price_currency, Decimal(1))
            tot += convert(db, amt, deal.price_currency, "CRC")
            est_pr = est_pr or est
        fin = _pick(rules, cfg, deal, date.today(), ben, "bono_fin")
        if fin is not None:
            amt, bcur = _bonus_amount(db, fin, d(pr.price), deal.price_currency)
            tot += convert(db, amt, bcur, "CRC")
        if tot > 0:
            en_curso += tot
            n_pr += 1
    # cotizaciones enviadas sin aceptar con regla al aceptar
    si_aceptan, n_q = Decimal(0), 0
    for q in db.scalars(
        select(Quote)
        .where(Quote.tenant_id == tenant.id, Quote.seller_id == user_id, Quote.status == "enviada", Quote.acceptance_status == "pendiente")
        .limit(200)
    ):
        deal = deal_of(db, quote=q)
        ben = ("vendedor", f"v:{user_id}", user_id, role, None)
        rule = _pick(rules, cfg, deal, date.today(), ben)
        if rule is None or trigger_of(rule, deal) != "cotizacion_aceptada":
            continue
        amt, _est = _projected(db, tenant, rule, net_sale(q), deal, q.currency, Decimal(1))
        si_aceptan += _doc_crc(db, q, amt)
        n_q += 1
    # embudo: oportunidades abiertas x probabilidad x su regla
    embudo, n_o, est_emb = Decimal(0), 0, False
    for o in db.scalars(
        select(Opportunity).where(Opportunity.tenant_id == tenant.id, Opportunity.owner_id == user_id, Opportunity.status.in_(OPEN_STATES), live(Opportunity))
    ):
        if d(o.amount) <= 0:
            continue
        deal = Deal(tenant.id, o, None, None, None, o.kind if o.kind in DEAL_KINDS else "proyecto", o.solution, user_id)
        ben = ("vendedor", f"v:{user_id}", user_id, role, None)
        rule = _pick(rules, cfg, deal, date.today(), ben)
        if rule is None:
            continue
        base_sale = convert(db, d(o.amount), o.currency or "CRC", "CRC") * Decimal(o.probability or 0) / 100
        amt, est = _projected(db, tenant, rule, base_sale, deal, "CRC", Decimal(o.probability or 0) / 100)
        if amt > 0:
            embudo += amt
            n_o += 1
            est_emb = True  # la probabilidad ya es una estimacion
    hoy = date.today()
    meses = _months_back(hoy, 12)
    hist = {k: {"mes": k, "ganado": ZERO, "pagado": ZERO} for k in meses}
    for c in mine:
        k = _month_key(c.earned_on)
        if k in hist:
            hist[k]["ganado"] += crc_of(db, c)
        if c.status == "pagada" and c.paid_on and _month_key(c.paid_on) in hist:
            hist[_month_key(c.paid_on)]["pagado"] += crc_of(db, c)
    hide = viewer is not None and hides_margin(viewer, cfg)
    reglas = []
    for r in rules_for_user(db, tenant, user_id, role):
        if not r.active:
            continue
        reglas.append({"id": r.id, "summary": _sentence(r, hide), "kind": r.kind, "general": r.user_id is None})
    return {
        "user": {"id": user_id, "name": u.full_name if u else None},
        "periodo": {"from": a, "to": b},
        "ganado": {k: (q2(v) if isinstance(v, Decimal) else v) for k, v in ganado.items()},
        "pagado": q2(pagado),
        "por_cobrar": {"total": q2(por_cobrar), "facturas": n_fact, "estimado": est_cobrar},
        "en_curso": {"total": q2(en_curso), "proyectos": n_pr, "estimado": est_pr},
        "si_aceptan": {"total": q2(si_aceptan), "cotizaciones": n_q},
        "embudo": {"total": q2(embudo), "oportunidades": n_o, "estimado": est_emb},
        "historial": [{"mes": h["mes"], "ganado": q2(h["ganado"]), "pagado": q2(h["pagado"])} for h in hist.values()],
        "reglas": reglas,
        "moneda": "CRC",
        "actualizado": datetime.now(UTC),
    }


def _sentence(r: CommissionRule, hide: bool) -> str:
    """'5 % de lo cobrado sin IVA, se devenga al cobrar' (para la vendedora)."""
    s = rule_summary(r, hide_margin=hide)
    if (r.kind or "comision") == "comision":
        partes = s.split(" · ")
        if len(partes) >= 2:
            partes[1] = "se devenga " + partes[1]
            return partes[0] + ", " + " · ".join(partes[1:])
    return s


# ---------- 5.10 Ganancias del aliado ----------
def _company_parts(db: Session, tenant_id: int, cid: int, for_ally: bool = False) -> list[AllyParticipation]:
    """Participaciones de la empresa. Para el portal del aliado (for_ally) solo las que Crimson le comparte
    (visible_to_ally), igual que services/ally_scope.py (aliado_final R5, deny by default)."""
    w = [AllyParticipation.tenant_id == tenant_id, AllyParticipation.ally_company_id == cid]
    if for_ally:
        w.append(AllyParticipation.visible_to_ally.is_(True))
    return list(db.scalars(select(AllyParticipation).where(*w).order_by(AllyParticipation.id)))


def _client_name(db: Session, pr: Project) -> str | None:
    cid = pr.end_customer_id or pr.customer_id
    c = db.get(Customer, cid) if cid else None
    return c.name if c else None


def _project_projection(db: Session, tenant, pr: Project, parts: list[AllyParticipation], movs: list[Commission], rules, cfg) -> dict:
    """Lo que el aliado todavia puede recibir en un proyecto (en colones): pagos acordados aprobados sin devengar,
    la comision por su regla sobre la venta (proyectos abiertos) y los bonos posibles."""
    out = {"pago_acordado": ZERO, "comision": ZERO, "bonos": ZERO, "bono_anticipo": None}
    if pr.status == "cancelado":
        # cancelado: lo devengado se anulo o revirtio y el resto nunca llega; nada queda por proyectar
        out["total"] = ZERO
        return out
    deal = deal_of(db, project=pr)
    hoy = date.today()
    abierto = pr.status in OPEN_PROJECT and pr.completed_at is None
    for part in parts:
        for r in part.requests:
            if r.status == "aprobado" and d(r.amount) > 0:
                falta = d(r.amount) - _payout_accrued(db, r)
                if falta > 0:
                    out["pago_acordado"] += convert(db, falta, r.currency, "CRC")
        if not abierto:
            continue
        ben = ("aliado", f"a:{part.id}", None, None, part)
        rule = _pick(rules, cfg, deal, hoy, ben)
        if rule is not None:
            ya = sum((crc_of(db, c) for c in movs if c.participation_id == part.id and c.kind == "comision"), Decimal(0))
            amt, _est = _projected(db, tenant, rule, d(pr.price), deal, deal.price_currency, Decimal(1))
            out["comision"] += max(Decimal(0), convert(db, amt, deal.price_currency, "CRC") - ya)
        fin = _pick(rules, cfg, deal, hoy, ben, "bono_fin")
        if fin is not None:
            amt, bcur = _bonus_amount(db, fin, d(pr.price), deal.price_currency)
            out["bonos"] += convert(db, amt, bcur, "CRC")
        ant = _pick(rules, cfg, deal, hoy, ben, "bono_anticipo")
        if ant is not None and ant.committed_date and hoy < ant.committed_date:
            amt, bcur = _bonus_amount(db, ant, d(pr.price), deal.price_currency)
            out["bonos"] += convert(db, amt, bcur, "CRC")
            out["bono_anticipo"] = {"monto": q2(amt), "moneda": bcur, "fecha": ant.committed_date, "posible": early_ok(ant, hoy)}
    for k in ("pago_acordado", "comision", "bonos"):
        out[k] = q2(out[k])
    out["total"] = q2(out["pago_acordado"] + out["comision"] + out["bonos"])
    return out


def _what_you_put(parts: list[AllyParticipation]) -> list[dict]:
    return [
        {"what": r.what, "amount": r.amount, "currency": r.currency, "status": r.status}
        for part in parts
        for r in part.requests
        if r.status in ("aprobado", "recibido") and r.amount is not None
    ]


def ally_earnings(db: Session, tenant, ally_company_id: int, desde: date | None = None, hasta: date | None = None, for_ally: bool = False) -> dict:
    """Lo que recibe la empresa aliada: totales, 24 meses y cada proyecto en comun. Nunca el precio de venta de
    Crimson, sus costos ni nada de otros aliados. Montos sin IVA, en colones. for_ally (portal /aliado/ganancias):
    solo proyectos compartidos y vivos; lo ya devengado de una participacion oculta sigue en los totales, sin fila."""
    cfg = settings_of(tenant)
    rules = _rules(db, tenant.id)
    names = Names(db)
    movs = list(
        db.scalars(
            select(Commission)
            .where(Commission.tenant_id == tenant.id, Commission.ally_company_id == ally_company_id, Commission.status != "anulada")
            .order_by(Commission.earned_on, Commission.id)
        )
    )

    def en_periodo(f) -> bool:
        return f is not None and (desde is None or f >= desde) and (hasta is None or f <= hasta)

    devengado = sum((crc_of(db, c) for c in movs if en_periodo(c.earned_on)), Decimal(0))
    recibido = sum((crc_of(db, c) for c in movs if c.status == "pagada" and en_periodo(c.paid_on)), Decimal(0))
    por_recibir = sum((crc_of(db, c) for c in movs if c.status in ("pendiente", "aprobada")), Decimal(0))
    en_revision = sum(1 for c in movs if c.status == "en_espera")
    parts = _company_parts(db, tenant.id, ally_company_id, for_ally)
    by_project: dict[int, list[AllyParticipation]] = {}
    for part in parts:
        if part.project_id:
            pr = db.get(Project, part.project_id)
            if pr is None or pr.trashed_at is not None or (for_ally and pr.archived_at is not None):
                continue
            by_project.setdefault(part.project_id, []).append(part)
    proyectos, proyectado = [], Decimal(0)
    for pid, pparts in by_project.items():
        pr = db.get(Project, pid)
        pm = [c for c in movs if c.project_id == pid]
        proj = _project_projection(db, tenant, pr, pparts, pm, rules, cfg)
        proyectado += proj["total"]

        def suma(kinds, rows=pm):
            return q2(sum((crc_of(db, c) for c in rows if c.kind in kinds), Decimal(0)))

        ant = proj["bono_anticipo"]
        proyectos.append(
            {
                "project_id": pr.id,
                "number": pr.number,
                "name": pr.name,
                "status": pr.status,
                "cliente": _client_name(db, pr),
                "lo_que_pusiste": _what_you_put(pparts),
                "pago_acordado": suma(("pago_acordado",)),
                "comision": suma(("comision",)),
                "bonos": suma(("bono",)),
                "ajustes": suma(("ajuste",)),
                "total": suma(("pago_acordado", "comision", "bono", "ajuste")),
                "devengado": suma(("pago_acordado", "comision", "bono", "ajuste")),
                "pagado": q2(sum((crc_of(db, c) for c in pm if c.status == "pagada"), Decimal(0))),
                "por_recibir": q2(sum((crc_of(db, c) for c in pm if c.status in ("pendiente", "aprobada")), Decimal(0))),
                "proyectado": proj["total"],
                "committed_date": ant["fecha"] if ant else None,
                "bono_anticipo_posible": ant if ant and ant["posible"] else None,
                "completed_at": pr.completed_at,
            }
        )
    proyectos.sort(key=lambda x: x["project_id"], reverse=True)
    total_hist = sum((crc_of(db, c) for c in movs), Decimal(0))
    n_proj = len(proyectos)
    horas = 0.0
    if by_project:
        from ..routers.fieldwork import order_hours

        for o in db.scalars(
            select(WorkOrder).where(WorkOrder.tenant_id == tenant.id, WorkOrder.project_id.in_(list(by_project)), WorkOrder.ally_company_id == ally_company_id)
        ):
            horas += order_hours(o)[0] or 0
    pagos = [(c.paid_on - c.earned_on).days for c in movs if c.status == "pagada" and c.paid_on and c.earned_on]
    hasta_m = hasta or date.today()
    meses = month_range(desde, hasta_m) if desde else _months_back(hasta_m, 24)
    por_mes = {k: {"mes": k, "devengado": ZERO, "pagado": ZERO, "recibido": ZERO, "acumulado": ZERO, "proyectos_completados": 0} for k in meses}
    for c in movs:
        k = _month_key(c.earned_on)
        if k in por_mes:
            por_mes[k]["devengado"] += crc_of(db, c)
        if c.status == "pagada" and c.paid_on:
            k2 = _month_key(c.paid_on)
            if k2 in por_mes:
                por_mes[k2]["pagado"] += crc_of(db, c)
                por_mes[k2]["recibido"] += crc_of(db, c)
    for pid in by_project:
        pr = db.get(Project, pid)
        if pr is not None and pr.completed_at is not None:
            k = _month_key(local_date(pr.completed_at))
            if k in por_mes:
                por_mes[k]["proyectos_completados"] += 1
    acum = sum((crc_of(db, c) for c in movs if meses and _month_key(c.earned_on) < meses[0]), Decimal(0))
    filas = []
    for k in meses:
        f = por_mes[k]
        acum += f["devengado"]
        filas.append({**f, "devengado": q2(f["devengado"]), "pagado": q2(f["pagado"]), "recibido": q2(f["recibido"]), "acumulado": q2(acum)})
    return {
        "moneda": "CRC",
        "empresa": {"id": ally_company_id, "name": names.ally(ally_company_id)},
        "totales": {
            "recibido": q2(recibido),
            "por_recibir": q2(por_recibir),
            "en_revision": en_revision,
            "proyectado": q2(proyectado),
            "devengado": q2(devengado),
            "proyectos": n_proj,
            "promedio_por_proyecto": q2(total_hist / n_proj) if n_proj else None,
            "ingreso_por_hora": q2(total_hist / Decimal(str(horas))) if horas > 0 else None,
            "horas": round(horas, 1),
            "dias_promedio_pago": round(sum(pagos) / len(pagos)) if pagos else None,
        },
        "por_mes": filas,
        "proyectos": proyectos,
        "nota": "Montos sin IVA. Para cobrar, enviá tu factura electrónica a Crimson por el monto aprobado.",
    }


def ally_project_earnings(db: Session, tenant, ally_company_id: int, project_id: int, for_ally: bool = False) -> dict:
    """ "Lo que recibo en este proyecto": sus precios, sus movimientos, lo proyectado y el bono por anticipo."""
    cfg = settings_of(tenant)
    names = Names(db)
    pr = db.get(Project, project_id)
    parts = [x for x in _company_parts(db, tenant.id, ally_company_id, for_ally) if x.project_id == project_id]
    movs = list(
        db.scalars(
            select(Commission)
            .where(
                Commission.tenant_id == tenant.id,
                Commission.ally_company_id == ally_company_id,
                Commission.project_id == project_id,
                Commission.status != "anulada",
            )
            .order_by(Commission.earned_on, Commission.id)
        )
    )
    proj = (
        _project_projection(db, tenant, pr, parts, movs, _rules(db, tenant.id), cfg)
        if pr is not None
        else {"pago_acordado": ZERO, "comision": ZERO, "bonos": ZERO, "total": ZERO, "bono_anticipo": None}
    )
    return {
        "project_id": project_id,
        "project": {
            "id": pr.id,
            "number": pr.number,
            "name": pr.name,
            "status": pr.status,
            "cliente": _client_name(db, pr),
            "completed_at": pr.completed_at,
        }
        if pr is not None
        else None,
        "moneda": "CRC",
        "totales": {
            "recibido": q2(sum((crc_of(db, c) for c in movs if c.status == "pagada"), Decimal(0))),
            "por_recibir": q2(sum((crc_of(db, c) for c in movs if c.status in ("pendiente", "aprobada")), Decimal(0))),
            "proyectado": proj["total"],
            "devengado": q2(sum((crc_of(db, c) for c in movs), Decimal(0))),
        },
        "lo_que_pusiste": _what_you_put(parts),
        "movimientos": [ally_entry_out(c, names) for c in movs],
        "proyectado": {k: proj[k] for k in ("pago_acordado", "comision", "bonos", "total")},
        "bono_anticipo": proj["bono_anticipo"],
    }


# ---------- 7.16 Mis numeros ----------
def numbers(db: Session, tenant, a: date, b: date) -> dict:
    """Los numeros de Andres: cobrado, facturado, proyectos cerrados con su costo directo, comisiones y lo que dejo
    cada vendedor y cada alianza. En colones (facturas en USD con su tipo de cambio). Sin gastos generales."""
    from ..routers.projects import economics

    tid = tenant.id
    meses = month_range(a, b)
    keys = (
        "cobrado_sin_iva",
        "facturado_sin_iva",
        "venta_proyectos",
        "costo_directo",
        "utilidad_bruta",
        "comisiones_vendedores",
        "comisiones_aliados",
        "pagos_aliados",
        "utilidad_despues_comisiones",
    )
    M = {k: {**{x: Decimal(0) for x in keys}, "mes": k, "proyectos_cerrados": 0, "proyectos_sin_costos": 0} for k in meses}
    vend: dict[int, dict] = {}
    ali: dict[int, dict] = {}

    def V(uid):  # noqa: N802
        return vend.setdefault(
            uid,
            {
                "user_id": uid,
                "name": None,
                "facturado_sin_iva": Decimal(0),
                "cobrado_sin_iva": Decimal(0),
                "comisiones": Decimal(0),
                "proyectos_cerrados": 0,
                "utilidad_bruta_proyectos": Decimal(0),
            },
        )

    def A(cid):  # noqa: N802
        return ali.setdefault(
            cid,
            {
                "ally_company_id": cid,
                "name": None,
                "proyectos": 0,
                "venta_proyectos": Decimal(0),
                "costo_proyectos": Decimal(0),
                "pagado_al_aliado": Decimal(0),
                "comisiones_bonos": Decimal(0),
            },
        )

    for inv in db.scalars(select(Invoice).where(Invoice.tenant_id == tid, Invoice.status != "anulada", Invoice.issue_date >= a, Invoice.issue_date <= b)):
        k = _month_key(inv.issue_date)
        if k in M:
            v = _doc_crc(db, inv, net_sale(inv))
            M[k]["facturado_sin_iva"] += v
            if inv.seller_id:
                V(inv.seller_id)["facturado_sin_iva"] += v
    for pay in db.scalars(
        select(Payment).where(
            Payment.tenant_id == tid,
            Payment.status == "confirmado",
            Payment.kind.in_(("captura", "devolucion", "reembolso")),
            Payment.paid_at >= a,
            Payment.paid_at <= b,
            Payment.invoice_id.is_not(None),
        )
    ):
        inv = db.get(Invoice, pay.invoice_id)
        if inv is None or d(inv.total) <= 0:
            continue
        k = _month_key(_fecha(pay.paid_at))
        if k not in M:
            continue
        v = _doc_crc(db, inv, _paid_in_doc(db, pay, inv) * net_sale(inv) / d(inv.total))
        v = v if pay.kind == "captura" else -v
        M[k]["cobrado_sin_iva"] += v
        if inv.seller_id:
            V(inv.seller_id)["cobrado_sin_iva"] += v
    from .fx import rate_or_none

    fx = rate_or_none(db, "USD")
    for pr in db.scalars(select(Project).where(Project.tenant_id == tid, Project.completed_at.is_not(None), Project.trashed_at.is_(None))):
        if pr.status == "cancelado":
            continue
        cierre = local_date(pr.completed_at)
        k = _month_key(cierre) if cierre and a <= cierre <= b else None
        if k not in M:
            continue
        e = economics(db, pr, fx)
        M[k]["proyectos_cerrados"] += 1
        price_crc = d(e["price_crc"])
        deal = deal_of(db, project=pr)
        seller = deal.seller_id
        if e["sin_costos"]:
            M[k]["proyectos_sin_costos"] += 1
        else:
            M[k]["venta_proyectos"] += price_crc
            M[k]["costo_directo"] += d(e["cost_real"])
            M[k]["utilidad_bruta"] += price_crc - d(e["cost_real"])
            if seller:
                V(seller)["utilidad_bruta_proyectos"] += price_crc - d(e["cost_real"])
        if seller:
            V(seller)["proyectos_cerrados"] += 1
        for cid in {x.ally_company_id for x in participations_of(db, deal) if x.ally_company_id}:
            f = A(cid)
            f["proyectos"] += 1
            if not e["sin_costos"]:
                f["venta_proyectos"] += price_crc
                f["costo_proyectos"] += d(e["cost_real"])
    for c in db.scalars(
        select(Commission).where(Commission.tenant_id == tid, Commission.status != "anulada", Commission.earned_on >= a, Commission.earned_on <= b)
    ):
        k = _month_key(c.earned_on)
        if k not in M:
            continue
        v = crc_of(db, c)
        if is_payout(c):
            # pago acordado y sus ajustes: ya estan en el costo directo (precio aprobado del aliado), no se restan otra vez
            M[k]["pagos_aliados"] += v
            if c.ally_company_id:
                A(c.ally_company_id)["pagado_al_aliado"] += v
        elif c.beneficiary == "aliado":
            M[k]["comisiones_aliados"] += v
            if c.ally_company_id:
                A(c.ally_company_id)["comisiones_bonos"] += v
        else:
            M[k]["comisiones_vendedores"] += v
            if c.user_id:
                V(c.user_id)["comisiones"] += v
    filas = []
    tot = {**{x: Decimal(0) for x in keys}, "proyectos_cerrados": 0, "proyectos_sin_costos": 0}
    for k in meses:
        f = M[k]
        f["utilidad_despues_comisiones"] = f["utilidad_bruta"] - f["comisiones_vendedores"] - f["comisiones_aliados"]
        for x in keys:
            tot[x] += f[x]
        tot["proyectos_cerrados"] += f["proyectos_cerrados"]
        tot["proyectos_sin_costos"] += f["proyectos_sin_costos"]
        filas.append(_money_row(f, keys))
    names = Names(db)
    por_vendedor = []
    for uid, f in vend.items():
        f["name"] = names.user(uid)
        por_vendedor.append(_money_row(f, ("facturado_sin_iva", "cobrado_sin_iva", "comisiones", "utilidad_bruta_proyectos")))
    por_aliado = []
    for cid, f in ali.items():
        f["name"] = names.ally(cid)
        f["utilidad_crimson"] = f["venta_proyectos"] - f["costo_proyectos"] - f["comisiones_bonos"]
        f["margen"] = float(f["utilidad_crimson"] / f["venta_proyectos"] * 100) if f["venta_proyectos"] > 0 else 0.0
        f.pop("costo_proyectos")
        por_aliado.append(_money_row(f, ("venta_proyectos", "pagado_al_aliado", "comisiones_bonos", "utilidad_crimson")))
    por_vendedor.sort(key=lambda x: -x["cobrado_sin_iva"])
    por_aliado.sort(key=lambda x: -x["venta_proyectos"])
    return {
        "periodo": {"from": a, "to": b},
        "meses": filas,
        "totales": _money_row(tot, keys),
        "por_vendedor": por_vendedor,
        "por_aliado": por_aliado,
        "moneda": "CRC",
    }


def _money_row(f: dict, keys) -> dict:
    out = dict(f)
    for x in keys:
        out[x] = q2(out[x])
    if "venta_proyectos" in out and "utilidad_despues_comisiones" in out:
        v = d(out["venta_proyectos"])
        out["margen_neto"] = float(d(out["utilidad_despues_comisiones"]) / v * 100) if v > 0 else 0.0
    return out


# ---------- 7.10 Aplicar reglas a fechas anteriores ----------
def backfill(db: Session, tenant, desde: date) -> list[Commission]:
    """Reprocesa en orden cronologico cobros, devoluciones, cotizaciones aceptadas o convertidas y proyectos
    completados desde `desde`. Idempotente por source_key: repetirlo no crea nada."""
    tid = tenant.id
    creados: list[Commission] = []
    eventos: list[tuple[date, int, str, object]] = []
    for pay in db.scalars(
        select(Payment).where(
            Payment.tenant_id == tid,
            Payment.status == "confirmado",
            Payment.kind.in_(("captura", "devolucion", "reembolso")),
            Payment.paid_at >= desde,
            Payment.invoice_id.is_not(None),
        )
    ):
        eventos.append((_fecha(pay.paid_at), 0, "pago", pay))
    # crear el proyecto desde la cotizacion no cambia su estado, pero es aceptacion implicita (on_quote_won)
    proyecto_de = {
        qid: local_date(creado)
        for qid, creado in db.execute(select(Project.quote_id, Project.created_at).where(Project.tenant_id == tid, Project.quote_id.is_not(None)))
    }
    for q in db.scalars(select(Quote).where(Quote.tenant_id == tid, Quote.status != "anulada")):
        acc = db.scalars(
            select(QuoteAcceptance).where(QuoteAcceptance.quote_id == q.id, QuoteAcceptance.status == "aceptada").order_by(QuoteAcceptance.id)
        ).all()
        if q.acceptance_status == "aceptada" and acc and (acc[-1].decided_on or date.today()) >= desde:
            eventos.append((acc[-1].decided_on or date.today(), 1, "cot", q))
            continue
        # se gano el dia que primero se convirtio a factura o a proyecto
        ganada = [f for f in (local_date(q.updated_at) if q.status == "convertida" else None, proyecto_de.get(q.id)) if f is not None]
        if ganada and min(ganada) >= desde:
            eventos.append((min(ganada), 1, "won", q))
    for pr in db.scalars(select(Project).where(Project.tenant_id == tid, Project.completed_at.is_not(None))):
        if local_date(pr.completed_at) >= desde and pr.status in CLOSED:
            eventos.append((local_date(pr.completed_at), 2, "fin", pr))
    eventos.sort(key=lambda e: (e[0], e[1], getattr(e[3], "id", 0)))
    for _f, _o, tipo, obj in eventos:
        if tipo == "pago":
            creados += on_payment(db, obj)
        elif tipo == "cot":
            creados += _accrue_quote(db, obj, _accepted_count(db, obj), _f, only_missing=False)
        elif tipo == "won":
            creados += _accrue_quote(db, obj, _accepted_count(db, obj), _f, only_missing=True)
        else:
            creados += _accrue_project(db, obj)
    return creados


def manual_key() -> str:
    return f"manual:{uuid.uuid4().hex}"


# ---------- Acuerdo economico de un aliado en un negocio (7.14) ----------
def participation_rules(db: Session, part: AllyParticipation) -> dict[str, CommissionRule | None]:
    """Las reglas propias de la participacion por tipo (la mas reciente activa)."""
    out: dict[str, CommissionRule | None] = {"comision": None, "bono_fin": None, "bono_anticipo": None}
    for r in db.scalars(
        select(CommissionRule)
        .where(CommissionRule.tenant_id == part.tenant_id, CommissionRule.participation_id == part.id, CommissionRule.active)
        .order_by(CommissionRule.id)
    ):
        out[r.kind or "comision"] = r
    return out


def deal_summary(db: Session, part: AllyParticipation) -> dict:
    """Lectura liviana para la tarjeta del proyecto (solo quien ve costos): que recibe el aliado aqui."""
    own = participation_rules(db, part)
    pago = sum((convert(db, d(r.amount), r.currency, "CRC") for r in part.requests if r.status == "aprobado" and r.amount), Decimal(0))
    com = own["comision"]
    if com is None:
        tenant = db.get(Tenant, part.tenant_id)
        cfg = settings_of(tenant)
        pr = db.get(Project, part.project_id) if part.project_id else None
        opp = db.get(Opportunity, part.opportunity_id) if part.opportunity_id else None
        deal = (
            deal_of(db, project=pr)
            if pr is not None
            else Deal(part.tenant_id, opp, None, None, None, (opp.kind if opp else "proyecto"), opp.solution if opp else None, None)
        )
        com = _pick(_rules(db, part.tenant_id), cfg, deal, date.today(), ("aliado", f"a:{part.id}", None, None, part))
    bonos = [rule_summary(r) for r in (own["bono_fin"], own["bono_anticipo"]) if r is not None]
    return {
        "pago_acordado_crc": q2(pago),
        "commission_summary": rule_summary(com) if com is not None else None,
        "commission_general": own["comision"] is None and com is not None,
        "bonus_summary": " · ".join(bonos) if bonos else None,
        "committed_date": own["bono_anticipo"].committed_date if own["bono_anticipo"] is not None else None,
    }
