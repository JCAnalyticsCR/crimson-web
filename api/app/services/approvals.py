"""Bandeja de Aprobaciones: todo lo que espera un visto bueno, en un solo lugar.

Andres (13/10, 21:05-21:42): "se va a enviar a la oficina… no se quien es la oficina ni donde reciben eso" ->
"tendria que ser como otra categoria aqui… aprobaciones". No crea flujos nuevos: junta los que ya existen.
Cada seccion exige el MISMO permiso que la accion real (needs) y usa la misma definicion que su lista.
Lo mas antiguo primero: lo que mas espera, arriba."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import (
    AccessRequest,
    AllyCompany,
    AllyCostRequest,
    Commission,
    Customer,
    Invoice,
    Opportunity,
    PayrollRun,
    Product,
    Project,
    Quote,
    Recurrence,
    StockReservation,
    Survey,
    User,
    Warehouse,
)
from .archive import live
from .survey_specs import SPECS


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    hint: str
    needs: tuple[str, ...]  # "modulo.accion": hacen falta TODAS (las que exige la accion real)
    to: str  # lista completa en el portal
    model: type
    where: Callable[[Principal], list]
    order: Callable[[], list]
    item: Callable[[Session, Principal, object], dict]


def allowed(p: Principal, s: Section) -> bool:
    return all(p.can(*n.split(".", 1)) for n in s.needs)


# ---------- helpers ----------
def _name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return u.full_name if u else None


def _row(id, ref, title, subtitle=None, who=None, since=None, badge=None, amount=None, currency=None, to="") -> dict:
    return {
        "id": id,
        "ref": ref,
        "title": title,
        "subtitle": subtitle,
        "who": who,
        "since": since,
        "badge": badge,
        "amount": amount,
        "currency": currency,
        "to": to,
    }


def _num(v) -> str:
    """3.000 -> "3", 2.50 -> "2.5": cantidades limpias, sin ceros de la columna Numeric."""
    n = Decimal(str(v or 0))
    return str(int(n)) if n == n.to_integral_value() else f"{n.normalize():f}"


def _customer(db: Session, customer_id: int | None, contact: dict | None = None) -> str:
    c = db.get(Customer, customer_id) if customer_id else None
    return (c.name if c else None) or (contact or {}).get("name") or "Sin cliente"


# ---------- filtros publicos (los usa tambien la lista de levantamientos: fieldwork.surveys ?bandeja=) ----------
def survey_review_where(p: Principal) -> list:
    """Enviados esperando revision (incluye los de antes de la revision: review_status None)."""
    w = [
        Survey.tenant_id == p.tenant.id,
        live(Survey),
        Survey.status == "enviado",
        or_(Survey.review_status.is_(None), Survey.review_status == "pendiente"),
    ]
    if not p.sees_field_all:
        w.append(Survey.technician_id == p.user.id)
    return w


def survey_costing_where(p: Principal) -> list:
    """Revision aprobada, sin cotizacion: falta costear."""
    w = [
        Survey.tenant_id == p.tenant.id,
        live(Survey),
        Survey.status == "enviado",
        Survey.review_status == "aprobado",
        Survey.quote_id.is_(None),
    ]
    if not p.sees_field_all:
        w.append(Survey.technician_id == p.user.id)
    return w


# ---------- secciones ----------
def _survey_subtitle(s: Survey) -> str:
    kind = SPECS.get(s.kind, SPECS["otro"])["label"]
    return f"{kind} · {s.site}" if s.site else kind


def _survey_review_item(db: Session, p: Principal, s: Survey) -> dict:
    return _row(
        s.id,
        s.number,
        _customer(db, s.customer_id, s.contact),
        _survey_subtitle(s),
        who=_name(db, s.sent_by) or _name(db, s.technician_id),
        since=s.sent_at,
        badge="Reenviado tras observaciones" if (s.review_round or 0) > 0 else None,
        to=f"/levantamientos?id={s.id}",
    )


def _survey_costing_item(db: Session, p: Principal, s: Survey) -> dict:
    return _row(
        s.id,
        s.number,
        _customer(db, s.customer_id, s.contact),
        _survey_subtitle(s),
        who=_name(db, s.reviewed_by),
        since=s.reviewed_at,
        to=f"/levantamientos?id={s.id}&costear=1",
    )


def _quotes_where(p: Principal) -> list:
    from ..routers.sales import pending_where  # import local: misma definicion que la tarjeta del inicio

    return pending_where("cotizaciones_por_aprobar", p)


def _quote_item(db: Session, p: Principal, q: Quote) -> dict:
    precio = p.sees_prices  # el monto solo a quien ya ve precios de venta
    return _row(
        q.id,
        q.number,
        _customer(db, q.customer_id),
        (q.approval_reason or "")[:160] or None,
        who=_name(db, q.created_by),
        since=q.updated_at,
        amount=q.total if precio else None,
        currency=q.currency if precio else None,
        to=f"/cotizaciones/{q.id}",
    )


def _ally_where(p: Principal) -> list:
    from .allies import cost_pending_where  # import local, como el resto de usos de allies

    # Solo "recibido": "solicitado" espera al aliado, no a nosotros
    return [*cost_pending_where(p), AllyCostRequest.status == "recibido"]


def _ally_item(db: Session, p: Principal, r: AllyCostRequest) -> dict:
    o = db.get(Opportunity, r.opportunity_id)
    costo = p.sees_costs  # lo que cobra el aliado es costo para Crimson
    return _row(
        r.id,
        o.number if o else f"OPP-{r.opportunity_id}",
        r.participation.name if r.participation else "Aliado",
        r.what,
        who=_name(db, r.responsible_id),
        since=r.updated_at,
        amount=r.amount if costo else None,
        currency=r.currency if costo else None,
        to=f"/oportunidades?id={r.opportunity_id}",
    )


def _reservation_where(p: Principal) -> list:
    return [StockReservation.tenant_id == p.tenant.id, StockReservation.status == "pendiente", StockReservation.origin == "interna"]


def _reservation_item(db: Session, p: Principal, r: StockReservation) -> dict:
    prod = db.get(Product, r.product_id)
    wh = db.get(Warehouse, r.warehouse_id)
    sub = f"{_num(r.quantity)} {prod.unit if prod and prod.unit else ''}".strip() + f" · {wh.name if wh else 'Bodega'}"
    pr = db.get(Project, r.project_id) if r.project_id else None
    if pr:
        sub += f" · {pr.number} {pr.name}"
    return _row(
        r.id,
        f"RES-{r.id}",
        prod.name if prod else "Producto",
        sub,
        who=_name(db, r.requested_by),
        since=r.created_at,
        to="/inventario?tab=reservas",
    )


def _access_where(p: Principal) -> list:
    return [AccessRequest.tenant_id == p.tenant.id, AccessRequest.status == "pendiente"]


def _access_item(db: Session, p: Principal, a: AccessRequest) -> dict:
    return _row(a.id, f"ACC-{a.id}", a.company or a.name, f"{a.name} · {a.email}", since=a.created_at, to="/accesos-clientes")


def _payroll_where(p: Principal) -> list:
    return [PayrollRun.tenant_id == p.tenant.id, PayrollRun.status == "borrador"]


def _payroll_item(db: Session, p: Principal, r: PayrollRun) -> dict:
    n = len(r.lines or [])
    periodo = f"{r.period_start:%d/%m}–{r.period_end:%d/%m/%Y}"
    # sin montos: el visto bueno se da en Planillas, viendo el detalle
    return _row(
        r.id,
        f"PL-{r.id}",
        f"Planilla {r.frequency} {periodo}",
        f"{n} colaborador{'es' if n != 1 else ''}",
        since=r.created_at,
        to="/planillas?tab=planillas",
    )


def _commission_where(p: Principal) -> list:
    """Misma cuenta que Inicio (services/commissions.approvals_count): pendientes + en espera."""
    w = [Commission.tenant_id == p.tenant.id, Commission.status.in_(("pendiente", "en_espera"))]
    if not p.can("commissions", "ver_todo"):
        w.append(Commission.user_id == p.user.id)  # misma regla que la lista de comisiones
    return w


_COMMISSION_BASE = {"venta": "Sobre venta", "margen": "Sobre margen"}


def _commission_item(db: Session, p: Principal, c: Commission) -> dict:
    from .commissions import KIND_LABEL, hides_payout, is_payout

    kind = c.kind or "comision"
    if kind == "comision":
        sub = _COMMISSION_BASE.get(c.base, c.base)
        # Sobre margen, el % junto al monto deja despejar la utilidad (monto / %), y con el total de la factura el costo:
        # sin catalog.costos no se muestra, igual que base_amount en /commissions.
        if c.base != "margen" or p.sees_costs:
            sub = f"{sub} · {Decimal(str(c.percent or 0)).normalize():f} %"
    else:
        # bono, pago acordado o ajuste: no tienen base ni %; el concepto dice de que es
        sub = KIND_LABEL.get(kind, kind) + (f" · {c.description}" if c.description else "")
    if c.status == "en_espera":
        sub = f"En espera · {c.hold_reason}" if c.hold_reason else "En espera de un dato"
    if c.beneficiary == "aliado":
        from ..models import AllyCompany, AllyParticipation

        comp = db.get(AllyCompany, c.ally_company_id) if c.ally_company_id else None
        part = db.get(AllyParticipation, c.participation_id) if comp is None and c.participation_id else None
        quien = comp.name if comp else (part.name if part else "Aliado")
    else:
        quien = _name(db, c.user_id) or "Usuario"
    oculto = is_payout(c) and hides_payout(p)
    return _row(
        c.id,
        f"COM-{c.id}",
        quien,
        sub[:200],
        since=c.earned_on,
        amount=None if oculto else c.amount,
        currency=None if oculto else c.currency,
        to="/comisiones?tab=bandeja",
    )


# ---------- precios por revisar (guardian de margen, plan 1.12) ----------
def _price_review_where(p: Principal) -> list:
    return [Product.tenant_id == p.tenant.id, Product.margin_flagged_at.is_not(None), Product.active, Product.show_on_web]


def _pct(v) -> str:
    return f"{Decimal(str(v or 0)).normalize():f}".replace(".", ",")


def _price_review_item(db: Session, p: Principal, prod: Product) -> dict:
    from urllib.parse import quote

    from .margin_guard import dudoso_txt, money_txt, review

    r = review(p.tenant, prod)
    sub = f"Margen {_pct(r['margin_pct'])} % (mínimo {_pct(r['min_pct'])} %) · precio actual {money_txt(r['price'], r['currency'])}"
    if r["low_variants"]:
        sub += f" · {r['low_variants']} variante(s) con precio propio bajo el mínimo"
    if r["dudoso"]:
        sub += f" · dudoso: {dudoso_txt(r)} antes de aplicar"
    badge = "Fuera de venta" if r["held"] else ("Frenado: sigue a la venta" if r["over_cap"] else "Solo informe: sigue a la venta")
    row = _row(
        prod.id,
        prod.code,
        prod.name,
        sub[:300],
        since=prod.margin_flagged_at,
        badge=badge + (" · dudoso" if r["dudoso"] else ""),
        amount=r["suggested"],  # el precio sugerido (sin IVA): la seccion exige ver costos
        currency=r["currency"],
        to=f"/productos?q={quote(prod.code, safe='')}&ficha={prod.id}",  # abre la ficha para verificar el costo
    )
    que = (("el precio y " if r["base_low"] else "") + f"{r['low_variants']} variante(s)") if r["low_variants"] else "el precio"
    # el unico pendiente que se resuelve aqui mismo: aplicar el precio sugerido con un clic (nunca se reprecia solo).
    # body = el precio que se muestra: si el sugerido de hoy es otro, la API responde 409 y no aplica otro monto
    row["action"] = {
        "label": "Aplicar",
        "path": f"/approvals/prices/{prod.id}/apply",
        "body": {"price": r["suggested"]},
        "confirm": f"¿Cambiar {que} de {prod.name} a {money_txt(r['suggested'], r['currency'])} sin IVA y volver a publicarlo?",
    }
    return row


# ---------- mensualidades (plan 1.4) ----------
# enviando: la factura quedo guardada y el envio automatico no termino (el worker se cayo entre los dos pasos o un error
# interno en "Generar ahora"); no se reintenta solo porque el correo pudo haber salido: una persona lo revisa y reenvia
PLAN_SEND_PENDING = ("por_aprobar", "enviando", "fallida", "sin_correo")
_PLAN_BADGE = {
    "por_aprobar": "Primera del plan: revisala",
    "enviando": "Envío interrumpido: revisá y reenviá",
    "fallida": "No salió: reenviar",
    "sin_correo": "Cliente sin correo",
}


def _plan_send_where(p: Principal) -> list:
    """Ultima factura de una recurrencia que espera "Aprobar y enviar" (la primera del plan) o que no salio. Con saldo:
    una que ya se pago por otra via (pago registrado a mano) no se le vuelve a cobrar al cliente."""
    pend = select(Recurrence.last_invoice_id).where(
        Recurrence.tenant_id == p.tenant.id,
        Recurrence.kind == "factura",
        Recurrence.last_invoice_id.is_not(None),
        Recurrence.last_send_status.in_(PLAN_SEND_PENDING),
    )
    return [
        Invoice.tenant_id == p.tenant.id,
        Invoice.id.in_(pend),
        Invoice.status.not_in(("anulada", "pagada")),
        Invoice.balance > 0,
        Invoice.sent_at.is_(None),
    ]


def _plan_send_item(db: Session, p: Principal, inv: Invoice) -> dict:
    from ..models import Tenant
    from .hacienda_config import hacienda_mode

    r = db.scalar(select(Recurrence).where(Recurrence.tenant_id == inv.tenant_id, Recurrence.last_invoice_id == inv.id))
    precio = p.sees_prices
    badge = _PLAN_BADGE.get(r.last_send_status) if r else None
    if r is not None and r.last_send_status == "por_aprobar" and not r.first_run_requires_approval:
        badge = "Por aprobar: revisala"  # no es la primera: la genero alguien sin permiso de envio o falta emitirla
    row = _row(
        inv.id,
        inv.number,
        _customer(db, inv.customer_id),
        ((r.name if r else "") + (f" · {r.last_send_note}" if r and r.last_send_note else ""))[:200] or None,
        since=inv.created_at,
        badge=badge,
        amount=inv.total if precio else None,
        currency=inv.currency if precio else None,
        to=f"/facturas/{inv.id}",
    )
    # Hacienda en produccion: sin comprobante aceptado no se manda desde aqui; se abre la factura para emitirla (la
    # entrega fiscal le manda el XML al cliente y la marca enviada)
    sin_emitir = inv.einvoice_status != "aceptada" and hacienda_mode(db, db.get(Tenant, inv.tenant_id)) == "produccion"
    if r is not None and not sin_emitir:
        # unica accion que se hace desde la bandeja: la persona ya reviso la factura (abrirla queda a un clic)
        row["action"] = {"label": "Aprobar y enviar" if r.last_send_status == "por_aprobar" else "Reenviar", "path": f"/recurrences/{r.id}/approve-send"}
    return row


def _ally_mora_where(p: Principal) -> list:
    from ..tasks.cobranza import config  # import local: la fecha de corte de cartera es la de la secuencia de cobro
    from .ally_access import mora_where
    from .invoice_states import cr_today

    return mora_where(p.tenant.id, cr_today(), start=config(p.tenant)["start_date"])


def _ally_mora_item(db: Session, p: Principal, c: AllyCompany) -> dict:
    from datetime import timedelta

    from .ally_access import SUSPEND_DAYS, plan_status
    from .invoice_states import cr_today

    hoy = cr_today()
    v = plan_status(db, c, today=hoy).get("vencido") or {}
    dias = int(v.get("dias") or SUSPEND_DAYS)
    return _row(
        c.id,
        f"ALI-{c.id}",
        c.name,
        f"Mensualidad con {dias} días de atraso. Propuesta: suspender el acceso al portal (lo decide una persona).",
        since=(hoy - timedelta(days=max(dias - SUSPEND_DAYS, 0))).isoformat(),
        badge="Proponer suspensión",
        amount=v.get("monto"),
        currency=v.get("moneda"),
        to=f"/aliados/{c.id}",
    )


SECTIONS: tuple[Section, ...] = (
    Section(
        "levantamientos_por_revisar",
        "Levantamientos por revisar",
        "Los envían los técnicos desde el sitio. Aprobalos o devolvelos con observaciones antes del costeo.",
        ("field.revisar",),
        "/levantamientos?bandeja=por_revisar",
        Survey,
        survey_review_where,
        lambda: [Survey.sent_at.is_(None), Survey.sent_at, Survey.id],
        _survey_review_item,
    ),
    Section(
        "levantamientos_por_costear",
        "Levantamientos aprobados, por costear",
        "Revisión aprobada: falta ponerles costo y generar la cotización.",
        ("catalog.costos", "sales.crear"),
        "/levantamientos?bandeja=por_costear",
        Survey,
        survey_costing_where,
        lambda: [Survey.reviewed_at, Survey.id],
        _survey_costing_item,
    ),
    Section(
        "cotizaciones_por_aprobar",
        "Cotizaciones por aprobar",
        "Superan el descuento, el monto o el margen permitido; el vendedor espera tu aprobación.",
        ("sales.aprobar",),
        "/cotizaciones?pendiente=cotizaciones_por_aprobar",
        Quote,
        _quotes_where,
        lambda: [Quote.updated_at, Quote.id],
        _quote_item,
    ),
    Section(
        "costos_aliados",
        "Costos de aliados recibidos",
        "El aliado ya mandó su costo: aprobalo o rechazalo en la oportunidad.",
        ("catalog.costos", "crm_pipeline.editar"),
        "/oportunidades?pendiente=costos_aliados",
        AllyCostRequest,
        _ally_where,
        lambda: [AllyCostRequest.updated_at, AllyCostRequest.id],
        _ally_item,
    ),
    Section(
        "reservas_material",
        "Reservas de material",
        "Pedidos de bodega o de proyectos que esperan confirmación.",
        ("inventory.aprobar_reserva",),
        "/inventario?tab=reservas",
        StockReservation,
        _reservation_where,
        lambda: [StockReservation.created_at, StockReservation.id],
        _reservation_item,
    ),
    Section(
        "accesos_portal",
        "Solicitudes de acceso al portal de clientes",
        "Personas que pidieron entrar al portal de clientes.",
        ("portal_clientes.aprobar",),
        "/accesos-clientes",
        AccessRequest,
        _access_where,
        lambda: [AccessRequest.created_at, AccessRequest.id],
        _access_item,
    ),
    Section(
        "planillas_por_aprobar",
        "Planillas en borrador",
        "Calculadas y sin aprobar.",
        ("payroll.aprobar",),
        "/planillas?tab=planillas",
        PayrollRun,
        _payroll_where,
        lambda: [PayrollRun.period_end, PayrollRun.id],
        _payroll_item,
    ),
    Section(
        "comisiones_por_aprobar",
        "Comisiones por aprobar",
        "Comisiones, bonos y pagos acordados a aliados que esperan aprobación (o un dato, si están en espera) antes de pagarse.",
        ("commissions.aprobar",),
        "/comisiones?tab=bandeja",
        Commission,
        _commission_where,
        lambda: [Commission.earned_on, Commission.id],
        _commission_item,
    ),
    Section(
        "precios_por_revisar",
        "Precios por revisar",
        "Productos de la tienda que quedaron bajo el margen mínimo por el costo del proveedor o el tipo de cambio. "
        "Los que dicen «Fuera de venta» se muestran como «Consultar» hasta que apliqués un precio; el sistema nunca "
        "cambia un precio solo.",
        ("catalog.costos", "catalog.editar"),
        "/aprobaciones?seccion=precios_por_revisar",  # la seccion entera (la bandeja corta en 50)
        Product,
        _price_review_where,
        lambda: [Product.margin_flagged_at, Product.id],
        _price_review_item,
    ),
    Section(
        "mensualidades_por_aprobar",
        "Mensualidades por aprobar",
        "La primera factura de cada plan (y las que no salieron) espera tu visto bueno. Revisala y tocá «Aprobar y enviar»: "
        "sale con su PDF y enlace de pago, y desde la siguiente salen solas.",
        ("sales.recurrencias", "sales.enviar"),
        "/recurrencias",
        Invoice,
        _plan_send_where,
        lambda: [Invoice.created_at, Invoice.id],
        _plan_send_item,
    ),
    Section(
        "aliados_en_mora",
        "Aliados con la mensualidad atrasada",
        "30 días o más de atraso. El sistema no suspende a nadie: abrí el aliado y decidí (Datos → Acceso al portal).",
        ("aliados.editar",),
        "/aliados",
        AllyCompany,
        _ally_mora_where,
        lambda: [AllyCompany.name, AllyCompany.id],
        _ally_mora_item,
    ),
)


# ---------- salida ----------
def _count(db: Session, p: Principal, s: Section) -> int:
    return db.scalar(select(func.count()).select_from(s.model).where(*s.where(p))) or 0


def summary(db: Session, p: Principal) -> dict:
    """Solo conteos (numero del menu). Las secciones permitidas van aunque esten en 0."""
    secs = [{"key": s.key, "label": s.label, "needs": list(s.needs), "to": s.to, "count": _count(db, p, s)} for s in SECTIONS if allowed(p, s)]
    return {"total": sum(x["count"] for x in secs), "sections": secs}


def full(db: Session, p: Principal, limit: int = 20, only: str | None = None) -> dict:
    """only = la clave de una seccion: solo esa ("Ver todos" de la bandeja, que corta en 50 por seccion)."""
    out = []
    for s in SECTIONS:
        if not allowed(p, s) or (only and s.key != only):
            continue
        rows = db.scalars(select(s.model).where(*s.where(p)).order_by(*s.order()).limit(limit)).all()
        sec = {
            "key": s.key,
            "label": s.label,
            "hint": s.hint,
            "needs": list(s.needs),
            "to": s.to,
            "count": _count(db, p, s),
            "items": [s.item(db, p, r) for r in rows],
        }
        if s.key == "levantamientos_por_revisar":
            from ..routers.fieldwork import _reviewers  # import local: el router importa este servicio

            # "¿quien lo recibe?": a quien le llega el aviso de cada levantamiento enviado
            sec["reviewers"] = [f"{r['name']} ({r['role']})" for r in _reviewers(db, p.tenant)]
        if s.key == "precios_por_revisar":
            # la ultima revision del guardian quedo frenada (posible error de datos): la bandeja ofrece destrabarla
            last = (p.tenant.settings or {}).get("margin_guard_last") or {}
            sec["brake"] = {"reason": last["frenada"], "frenados": last.get("frenados", 0), "at": last.get("at")} if last.get("frenada") else None
        out.append(sec)
    return {"total": sum(x["count"] for x in out), "sections": out}
