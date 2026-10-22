"""Proyectos (cotizacion aprobada -> ejecucion), activos del cliente y solicitudes de compra.

Aqui se responde la pregunta que hoy es dificil: cuanto dejo realmente cada instalacion. El proyecto guarda el
precio vendido, el costo estimado al cotizar y el costo real (equipos consumidos + mano de obra + viaticos + otros).
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import (
    Customer,
    CustomerAsset,
    Invoice,
    Opportunity,
    Product,
    Project,
    PurchaseRequest,
    PurchaseRequestLine,
    Quote,
    Supplier,
    Survey,
    User,
    WorkOrder,
)
from ..services import inventory as invsvc
from ..services import pricing
from ..services.archive import live
from ..services.documents import audit, local_date, today_fx
from ..services.fx import doc_rate, rate_or_none
from ..services.mail import notify_roles
from ..services.refs import check_refs
from ..services.sequences import next_number
from ..services.totals import d, net_sale

router = APIRouter(tags=["proyectos"])


def sees_project(p: Principal, pr: Project) -> bool:
    """Alcance de proyectos (lista, ficha y ficha 360 del cliente): con projects.ver_todo, todos; si no (el tecnico),
    los que supervisa o donde tiene una orden de trabajo, como responsable o como ayudante."""
    if p.can("projects", "ver_todo") or pr.supervisor_id == p.user.id:
        return True
    return any(o.technician_id == p.user.id or p.user.id in (o.helpers or []) for o in pr.orders)


def _project(db: Session, pid: int, p: Principal) -> Project:
    pr = db.get(Project, pid)
    if not pr or pr.tenant_id != p.tenant.id or not sees_project(p, pr):
        raise HTTPException(404, "Proyecto no encontrado")
    return pr


CLOSED_STATES = ("terminado", "entregado", "facturado")


def consumed_cost(db: Session, pr: Project, fx: Decimal | None = None) -> Decimal:
    """Costo de los equipos y materiales realmente usados en las órdenes de trabajo."""
    total = Decimal(0)
    for o in pr.orders:
        for m in o.materials:
            prod = db.get(Product, m.product_id) if m.product_id else None
            total += pricing.cost_in(db, prod, "CRC", fx) * d(m.quantity)
    return total


SOLUCIONES = {
    "cctv": "CCTV",
    "redes": "Redes / WiFi",
    "acceso": "Control de acceso",
    "asistencia": "Tiempo y asistencia",
    "ups": "Respaldo eléctrico",
    "cableado": "Cableado estructurado",
    "anpr": "ANPR / barreras",
    "otro": "Otro",
}


def project_solution(db: Session, pr: Project) -> str:
    """Tipo de solucion del proyecto: lo dice el levantamiento y, si no hubo, la oportunidad."""
    if pr.survey_id:
        s = db.get(Survey, pr.survey_id)
        if s and s.kind:
            return SOLUCIONES.get(s.kind, s.kind)
    o = db.scalar(select(Opportunity).where(Opportunity.project_id == pr.id))
    if o and o.solution:
        return SOLUCIONES.get(o.solution, o.solution)
    return "Sin clasificar"


def price_crc(db: Session, pr: Project) -> Decimal:
    """Venta sin IVA en colones: Project.price esta en la moneda de su cotizacion (falla J: en USD se comparaba
    dolares contra costos en colones). Con el tipo de cambio guardado en la cotizacion."""
    q = db.get(Quote, pr.quote_id) if pr.quote_id else None
    if q is None or (q.currency or "CRC") == "CRC":
        return d(pr.price)
    # la tasa guardada en la cotizacion; si falta o quedo en 1 (sistema viejo), la del BCCR de su fecha (services/fx)
    return d(pr.price) * doc_rate(db, q.currency, q.fx_sell, q.issue_date)


def ally_cost_of_project(db: Session, pr: Project, fx: Decimal | None = None) -> Decimal:
    """Lo que Crimson le paga a sus aliados en este proyecto: sus precios APROBADOS (sin IVA), en colones. Cuenta
    como costo directo desde que esta aprobado, aunque el pago acordado todavia no se haya devengado."""
    from ..models import AllyCostRequest, AllyParticipation

    total = Decimal(0)
    rows = db.scalars(
        select(AllyCostRequest)
        .join(AllyParticipation, AllyParticipation.id == AllyCostRequest.participation_id)
        .where(AllyParticipation.tenant_id == pr.tenant_id, AllyParticipation.project_id == pr.id, AllyCostRequest.status == "aprobado")
    ).all()
    for r in rows:
        if r.amount is None:
            continue
        if (r.currency or "CRC") == "CRC":
            total += d(r.amount)
        else:
            rate = fx if fx is not None and r.currency == "USD" else today_fx(db, r.currency)[0]
            total += d(r.amount) * d(rate)
    return total


def direct_cost(db: Session, pr: Project, fx: Decimal | None = None) -> Decimal:
    """Costo directo real: materiales consumidos + mano de obra propia + viaticos + otros + aliados (falla K: la
    fila de gerencia lo recalculaba a mano y sin aliados)."""
    fx = fx if fx is not None else rate_or_none(db, "USD")  # None: cada costo en dolares la consulta (o 422 sin tasa)
    return consumed_cost(db, pr, fx) + d(pr.cost_labor) + d(pr.cost_travel) + d(pr.cost_extra) + ally_cost_of_project(db, pr, fx)


def project_commissions(db: Session, pr: Project) -> Decimal:
    """Comisiones, bonos y ajustes devengados del proyecto (no anulados), en colones. El pago acordado y SUS ajustes
    (diferencia de precio, reverso, devolucion: llevan cost_request_id) ya estan en el costo de aliados, que usa el
    precio aprobado vigente: contarlos aqui los restaria dos veces de la utilidad neta."""
    from ..models import Commission
    from ..services.commissions import crc_of

    rows = db.scalars(
        select(Commission).where(
            Commission.tenant_id == pr.tenant_id,
            Commission.project_id == pr.id,
            Commission.status != "anulada",
            Commission.kind.in_(("comision", "bono", "ajuste")),
            Commission.cost_request_id.is_(None),
        )
    ).all()
    return sum((crc_of(db, c) for c in rows), Decimal(0))


def economics(db: Session, pr: Project, fx: Decimal | None = None) -> dict:
    """Lo que costo de verdad el proyecto contra lo que se vendio. Unica fuente: la usan la ficha y el reporte."""
    from ..routers.fieldwork import order_hours, survey_estimate

    fx = fx if fx is not None else rate_or_none(db, "USD")
    materials = consumed_cost(db, pr, fx)
    allies = ally_cost_of_project(db, pr, fx)
    real = materials + d(pr.cost_labor) + d(pr.cost_travel) + d(pr.cost_extra) + allies
    venta = price_crc(db, pr)
    comisiones = project_commissions(db, pr)
    # sin costos registrados el "margen real" daria 100 %: no es ganancia, es que falta registrar.
    # Mientras falten costos o el proyecto no este cerrado, el real es provisional y manda el cotizado.
    sin_costos = real <= 0
    cerrado = pr.status in CLOSED_STATES
    if sin_costos:
        aviso = "Faltan costos por registrar"
    elif not cerrado:
        aviso = "Proyecto en curso: el margen real puede cambiar"
    else:
        aviso = None
    profit = None if sin_costos else (venta - real).quantize(Decimal("0.01"))
    profit_net = None if profit is None else (profit - comisiones).quantize(Decimal("0.01"))
    return {
        "price": pr.price,
        "price_crc": venta.quantize(Decimal("0.01")),
        "cost_planned": pr.cost_planned,
        "cost_materials": materials.quantize(Decimal("0.01")),
        "cost_labor": pr.cost_labor,
        "cost_travel": pr.cost_travel,
        "cost_extra": pr.cost_extra,
        "cost_allies": allies.quantize(Decimal("0.01")),
        "cost_real": real.quantize(Decimal("0.01")),
        "profit": profit,
        "margin_real": None if sin_costos else pricing.margin_of(venta, real),
        "margin_planned": pricing.margin_of(venta, d(pr.cost_planned)),
        "commissions": comisiones.quantize(Decimal("0.01")),
        "profit_net": profit_net,
        "margin_net": None if profit_net is None or venta <= 0 else float((profit_net / venta * 100).quantize(Decimal("0.1"))),
        "sin_costos": sin_costos,
        "provisional": sin_costos or not cerrado,
        "aviso": aviso,
        # persona-horas del registro por jornada de cada OT (las viejas, de inicio a fin)
        "hours": round(sum((order_hours(o)[0] or 0 for o in pr.orders), 0.0), 2),
        # lo que el levantamiento dijo que llevaba la obra: una vez por proyecto, no por OT
        "hours_estimated": survey_estimate(db.get(Survey, pr.survey_id), _tenant_of(db, pr))[0] if pr.survey_id else None,
    }


def _tenant_of(db: Session, pr: Project):
    from ..models import Tenant

    return db.get(Tenant, pr.tenant_id)


def _out(db: Session, pr: Project, p: Principal, full: bool = True) -> dict:
    c = db.get(Customer, pr.customer_id) if pr.customer_id else None
    fin = db.get(Customer, pr.end_customer_id) if pr.end_customer_id else None
    sup = db.get(User, pr.supervisor_id) if pr.supervisor_id else None
    out = {
        "id": pr.id,
        "number": pr.number,
        "name": pr.name,
        "status": pr.status,
        "customer_id": pr.customer_id,
        "customer": c.name if c else None,  # contratante
        "end_customer_id": pr.end_customer_id,
        "end_customer": fin.name if fin else None,
        "opportunity_id": pr.opportunity_id,
        "site": pr.site,
        "scope": pr.scope,
        "supervisor_id": pr.supervisor_id,
        "supervisor": sup.full_name if sup else None,
        "start_date": pr.start_date,
        "end_date": pr.end_date,
        "quote_id": pr.quote_id,
        "survey_id": pr.survey_id,
        "invoice_id": pr.invoice_id,
        "notes": pr.notes,
        "photos": pr.photos,
        "delivered_at": pr.delivered_at,
        "orders_total": sum(1 for o in pr.orders if o.trashed_at is None),
        "orders_done": sum(1 for o in pr.orders if o.status == "finalizada" and o.trashed_at is None),
        "created_at": pr.created_at,
        "archived_at": pr.archived_at,
        "trashed_at": pr.trashed_at,
    }
    if p.sees_costs:
        out["economics"] = economics(db, pr)
    if full:
        from ..services import allies as allysvc

        # aliados heredados de la oportunidad: quien participa y a quien llamar (las solicitudes de costo viven en la oportunidad)
        out["allies"] = [allysvc.ally_out(db, a, p, with_requests=False) for a in allysvc.allies_of(db, p.tenant.id, project_id=pr.id)]
        if p.sees_costs:
            from ..models import AllyParticipation
            from ..services import commissions as comsvc

            # que recibe cada aliado aqui (pago acordado, comision, bonos): informacion de costos
            for a in out["allies"]:
                part = db.get(AllyParticipation, a["id"])
                a["deal"] = comsvc.deal_summary(db, part) if part is not None else None
        out["orders"] = [
            {
                "id": o.id,
                "number": o.number,
                "title": o.title,
                "status": o.status,
                "kind": o.kind,
                "scheduled_at": o.scheduled_at,
                "technician_id": o.technician_id,
                "finished_at": o.finished_at,
                "materials": len(o.materials),
            }
            for o in pr.orders
            if o.trashed_at is None  # lo de la papelera se ve en Archivo, no en la ficha
        ]
        out["assets"] = [
            {"id": a.id, "name": a.name, "serial": a.serial, "location": a.location, "warranty_until": a.warranty_until}
            for a in db.scalars(select(CustomerAsset).where(CustomerAsset.project_id == pr.id, live(CustomerAsset)))
        ]
    return out


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    customer_id: int | None = None
    site: str | None = Field(None, max_length=300)
    scope: str | None = None
    supervisor_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = Field("planificado", pattern="^(planificado|en_curso|pausado|terminado|entregado|facturado|cancelado)$")
    cost_labor: Decimal = Field(Decimal(0), ge=0)
    cost_travel: Decimal = Field(Decimal(0), ge=0)
    cost_extra: Decimal = Field(Decimal(0), ge=0)
    notes: str | None = None
    photos: list = Field(default_factory=list)


# Economia del proyecto que se escribe a mano: solo la toca quien ve costos (catalog.costos)
PROJECT_COST_FIELDS = ("cost_labor", "cost_travel", "cost_extra")
# Cliente y supervisor de ESTA empresa: la ficha responde su nombre (services/refs.py)
PROJECT_REFS = {"customer_id": (Customer, "Cliente no encontrado")}
PROJECT_USERS = {"supervisor_id": "Ese supervisor no es un usuario activo de la empresa"}


@router.get("/projects")
def projects(
    status: str | None = None,
    customer_id: int | None = None,
    limit: int = Query(100, le=300),
    p: Principal = Depends(require("projects", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(Project).where(Project.tenant_id == p.tenant.id, live(Project))
    if customer_id:
        # el formulario del ticket elige el proyecto del cliente (Proyecto opcional)
        stmt = stmt.where(Project.customer_id == customer_id)
    if status == "activos":
        stmt = stmt.where(Project.status.in_(("planificado", "en_curso", "pausado")))
    elif status:
        stmt = stmt.where(Project.status == status)
    rows = db.scalars(stmt.order_by(Project.id.desc()).limit(limit)).all()
    if not p.can("projects", "ver_todo"):
        rows = [pr for pr in rows if sees_project(p, pr)]
    return [_out(db, pr, p, full=False) for pr in rows]


@router.post("/projects", status_code=201)
def project_create(data: ProjectIn, p: Principal = Depends(require("projects", "crear")), db: Session = Depends(get_db)):
    fields = data.model_dump()
    check_refs(db, p.tenant.id, fields, PROJECT_REFS, PROJECT_USERS)
    number, _ = next_number(db, p.tenant.id, "PRO")
    if not p.sees_costs:
        # la economia del proyecto es del admin (Andres 08:07): quien no la ve no la escribe (quedan en 0)
        for k in PROJECT_COST_FIELDS:
            fields.pop(k, None)
    pr = Project(tenant_id=p.tenant.id, number=number, **fields)
    db.add(pr)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "project", pr.id, ip=p.ip)
    db.commit()
    return _out(db, pr, p)


class FromQuoteIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    site: str | None = Field(None, max_length=300)
    supervisor_id: int | None = None
    start_date: date | None = None
    # una oportunidad de "venta" no lleva proyecto; si al final si hay instalacion, se confirma a proposito
    force: bool = False


@router.post("/quotes/{qid}/project", status_code=201)
def project_from_quote(qid: int, data: FromQuoteIn, p: Principal = Depends(require("projects", "crear")), db: Session = Depends(get_db)):
    """El botón más importante: cotización aprobada -> proyecto, sin volver a escribir nada."""
    q = db.get(Quote, qid)
    if not q or q.tenant_id != p.tenant.id:
        raise HTTPException(404, "Cotización no encontrada")
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    check_refs(db, p.tenant.id, data.model_dump(), users=PROJECT_USERS)
    if db.scalar(select(Project).where(Project.quote_id == q.id)):
        raise HTTPException(409, "Esa cotización ya tiene proyecto")
    from ..services.units import check_ready

    check_ready(q, "convertir a proyecto")  # un costo pendiente no se ejecuta como si fuera cero
    ligada = db.scalar(select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.quote_id == q.id))
    if ligada and ligada.kind == "venta" and not data.force:
        raise HTTPException(409, f"La oportunidad {ligada.number} es de solo venta (sin instalación). Si sí lleva instalación, confirmá crear el proyecto.")
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    survey = db.scalar(select(Survey).where(Survey.quote_id == q.id))
    opp = db.scalar(select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.quote_id == q.id))
    fx = rate_or_none(db, "USD")
    cost_planned = Decimal(0)
    for ln in q.lines:
        prod = db.get(Product, ln.product_id) if ln.product_id else None
        cost_planned += pricing.cost_in(db, prod, "CRC", fx) * d(ln.quantity)
    if survey:
        from .fieldwork import labor_costing

        # cotizacion que nacio de un levantamiento: la mano de obra, el transporte y los viaticos del costeo
        # tambien son costo (interno, no el precio que se le cobro al cliente)
        lab = labor_costing(survey, p.tenant)
        cost_planned += lab["labor_cost"] + lab["travel"]
    number, _ = next_number(db, p.tenant.id, "PRO")
    pr = Project(
        tenant_id=p.tenant.id,
        number=number,
        name=data.name or f"{c.name if c else 'Proyecto'} · {q.number}",
        customer_id=q.customer_id,  # contratante: a quien se cotizo
        end_customer_id=opp.end_customer_id if opp else None,  # partes heredadas de la oportunidad
        quote_id=q.id,
        survey_id=survey.id if survey else None,
        opportunity_id=None,
        site=data.site or (opp.site if opp and opp.site else None) or (survey.site if survey else (c.address or {}).get("senas") if c and c.address else None),
        scope=q.external_notes,
        supervisor_id=data.supervisor_id,
        start_date=data.start_date,
        status="planificado",
        price=net_sale(q),  # venta sin IVA: todo margen se calcula sin impuesto
        cost_planned=cost_planned.quantize(Decimal("0.01")),
    )
    db.add(pr)
    db.flush()
    # primera orden de trabajo con los materiales de la cotización ya cargados
    o_number, _ = next_number(db, p.tenant.id, "OT")
    order = WorkOrder(
        tenant_id=p.tenant.id,
        number=o_number,
        project_id=pr.id,
        customer_id=pr.customer_id,
        title=f"Instalación · {pr.name}"[:200],
        kind="instalacion",
        site=pr.site,
        scheduled_at=None,
        tasks=[{"text": ln.name[:120], "done": False} for ln in q.lines][:20],
    )
    from ..models import WorkOrderMaterial

    for ln in q.lines:
        if ln.product_id:
            order.materials.append(WorkOrderMaterial(product_id=ln.product_id, name=ln.name, quantity=0, unit=ln.unit, planned=d(ln.quantity)))
    db.add(order)
    if opp:
        opp.status, opp.project_id = "ganada", pr.id
        pr.opportunity_id = opp.id
        from ..models import AllyParticipation

        # los aliados de la oportunidad pasan al proyecto (la misma participacion, no una copia)
        for a in db.scalars(select(AllyParticipation).where(AllyParticipation.opportunity_id == opp.id)):
            a.project_id = pr.id
    if survey:
        survey.status = "cerrado"
    db.flush()
    from ..services import commissions as comsvc

    comsvc.link_to_project(db, pr)  # lo devengado al aceptar (o cobrado con la factura) entra a la economia del proyecto
    comsvc.on_quote_won(db, q)  # crear el proyecto es aceptacion implicita (reglas "al aceptar la cotizacion")
    faltantes = _reservar(db, p, pr, order)
    audit(db, p.tenant.id, p.user.id, "from_quote", "project", pr.id, {"quote": q.number, "por_comprar": len(faltantes)}, ip=p.ip)
    db.commit()
    out = _out(db, pr, p)
    out["por_comprar"] = faltantes
    return out


def _reservar(db: Session, p: Principal, pr: Project, order: WorkOrder) -> list[dict]:
    """Al abrir el proyecto: lo que hay en bodega queda apartado para el y lo que falta se avisa YA.

    Andres lo explico asi: "los proveedores no dan credito", o sea que el material no llega solo porque el
    proyecto exista; hay que pedirlo y pagarlo. Enterarse el dia de la instalacion es tarde.
    """
    from ..services.availability import availability_many

    # existencia de la funcion unica: lo que hay en bodegas de VENTA (las de garantias no se instalan); lo comprometido
    # se descuenta abajo con la regla de proyectos (reservas formales o lo planificado de otras OT abiertas)
    av = availability_many(db, p.tenant, [m.product_id for m in order.materials if m.product_id], audience="interno")
    levels = {pid: Decimal(str(r.get("own_on_hand") or 0)) for pid, r in av.items()}
    apartados = db.scalars(
        select(WorkOrder).where(
            WorkOrder.tenant_id == p.tenant.id,
            WorkOrder.project_id != pr.id,
            WorkOrder.status.in_(("por_programar", "asignada", "en_sitio", "en_proceso")),  # por_programar: aun sin tecnico
            live(WorkOrder),
        )
    ).all()
    # Reservas formales confirmadas (AC25) mandan donde existen; si un proyecto no tiene reserva de un
    # material se sigue usando el calculo virtual de antes (planificado - usado de sus OT abiertas).
    from ..services.reservations import committed_by_project

    reservado = committed_by_project(db, p.tenant.id)
    comprometido: dict[int, Decimal] = {}
    for o in apartados:
        for m in o.materials:
            if m.product_id and (o.project_id, m.product_id) not in reservado:
                comprometido[m.product_id] = comprometido.get(m.product_id, Decimal(0)) + max(d(m.planned) - d(m.quantity), Decimal(0))
    for (proj, prod_id), qty in reservado.items():
        if proj != pr.id:
            comprometido[prod_id] = comprometido.get(prod_id, Decimal(0)) + qty
    faltan = []
    for m in order.materials:
        if not m.product_id:
            continue
        libre = levels.get(m.product_id, Decimal(0)) - comprometido.get(m.product_id, Decimal(0))
        falta = d(m.planned) - max(libre, Decimal(0))
        if falta > 0:
            a = av.get(m.product_id) or {}
            # solo lo que el proveedor confirma con dato vigente (un numero viejo no sirve para planificar la compra)
            faltan.append({"product_id": m.product_id, "name": m.name, "to_buy": falta, "supplier_stock": a.get("supplier_qty") if a.get("supplier_fresh") else None})
    if faltan:
        req = _new_request(db, p, faltan, reason="proyecto", project_id=pr.id, note=f"Material que falta para arrancar {pr.number}")
        filas = "".join(
            f"<li>{html.escape(x['name'])} · faltan {x['to_buy']:g}"
            + (f" · el proveedor reporta {x['supplier_stock']}" if x.get("supplier_stock") is not None else "")
            + "</li>"
            for x in faltan[:25]
        )
        notify_roles(
            db,
            p.tenant,
            ("admin", "inventario"),
            f"{pr.number}: hay que comprar {len(faltan)} material(es) antes de instalar",
            f"<p>El proyecto <b>{html.escape(pr.name)}</b> arrancó y esto no está en bodega:</p><ul>{filas}</ul>"
            f"<p>Quedó la solicitud de compra <b>{req['number']}</b>. Los proveedores no dan crédito, así que conviene pedirlo hoy.</p>",
            "purchase_request",
            req["id"],
        )
    return faltan


@router.get("/projects/{pid}")
def project_get(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    return _out(db, _project(db, pid, p), p)


@router.put("/projects/{pid}")
def project_update(pid: int, data: ProjectIn, p: Principal = Depends(require("projects", "editar")), db: Session = Depends(get_db)):
    pr = _project(db, pid, p)
    from ..services import bitacora  # solo bitacora: antes/despues de lo que cambio (incluye costos)

    antes = bitacora.snapshot(pr)
    estado_antes = pr.status
    costos_antes = tuple(d(getattr(pr, k)) for k in PROJECT_COST_FIELDS)
    # solo lo que se envia: el portal no manda supervisor_id ni photos y antes cada edicion los borraba
    cambios = data.model_dump(exclude_unset=True)
    check_refs(db, p.tenant.id, cambios, PROJECT_REFS, PROJECT_USERS, current=pr)
    if not p.sees_costs:
        # quien no ve la economia recibe los costos ocultos y mandaria 0: sin esto borraba los costos del proyecto
        for k in PROJECT_COST_FIELDS:
            cambios.pop(k, None)
    from ..services import commissions as comsvc

    if cambios.get("status") == "cancelado" and estado_antes != "cancelado" and not p.can("commissions", "aprobar") and comsvc.project_earned(db, pr):
        # cancelar anula o revierte lo devengado al completarlo (incluso aprobado o pagado): no se salta la aprobacion
        raise HTTPException(
            409,
            "Este proyecto ya devengó comisiones, bonos o pagos acordados al completarse y cancelarlo los anula o revierte. "
            "Lo puede cancelar quien aprueba comisiones.",
        )
    for k, v in cambios.items():
        setattr(pr, k, v)
    if pr.status == "entregado" and not pr.delivered_at:
        pr.delivered_at = datetime.now(UTC)

    # completar devenga (comisiones al completar, bonos, pago acordado); cancelar revierte; costos nuevos recalculan
    # lo que espera la utilidad del proyecto
    snap = bitacora.snapshot(pr)
    comsvc.on_project_status(db, pr, estado_antes)
    if estado_antes != pr.status or costos_antes != tuple(d(getattr(pr, k)) for k in PROJECT_COST_FIELDS):
        comsvc.on_project_costs(db, pr)
    bitacora.audit_change(db, p, "update", "project", pr.id, antes, snap, version=bitacora.next_version(db, p.tenant.id, "project", pr.id))
    db.commit()
    return _out(db, pr, p)


@router.get("/projects/{pid}/requirements")
def requirements(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    """Materiales planificados contra existencias: qué hay en bodega y qué hay que comprar."""
    pr = _project(db, pid, p)
    need: dict[int, dict] = {}
    for o in pr.orders:
        for m in o.materials:
            if not m.product_id:
                continue
            row = need.setdefault(m.product_id, {"product_id": m.product_id, "name": m.name, "planned": Decimal(0), "used": Decimal(0)})
            row["planned"] += d(m.planned)
            row["used"] += d(m.quantity)
    from ..services.availability import availability_many

    # en bodega = bodegas de venta (funcion unica); las de garantias no se instalan
    av = availability_many(db, p.tenant, list(need), audience="interno")
    out = []
    for row in need.values():
        a = av.get(row["product_id"]) or {}
        stock = Decimal(str(a.get("own_on_hand") or 0))
        pending = max(row["planned"] - row["used"], Decimal(0))
        out.append(
            {
                **row,
                "stock": stock,
                "pending": pending,
                "to_buy": max(pending - stock, Decimal(0)),
                "supplier_qty": a.get("supplier_qty"),
                "supplier_fresh": bool(a.get("supplier_fresh")),
            }
        )
    return sorted(out, key=lambda r: -r["to_buy"])


@router.post("/projects/{pid}/purchase-request", status_code=201)
def purchase_from_project(pid: int, p: Principal = Depends(require("purchases", "crear")), db: Session = Depends(get_db)):
    """Lo que falta para el proyecto se convierte en solicitud de compra (los proveedores no dan crédito:
    conviene avisar temprano)."""
    pr = _project(db, pid, p)
    faltan = [r for r in requirements(pid, p, db) if r["to_buy"] > 0]
    if not faltan:
        raise HTTPException(409, "No falta material: todo está en bodega")
    return _new_request(db, p, faltan, reason="proyecto", project_id=pr.id, note=f"Materiales faltantes del proyecto {pr.number}")


def _new_request(db: Session, p: Principal, rows: list[dict], reason: str, project_id: int | None = None, note: str | None = None) -> dict:
    for r in rows:
        prod = db.get(Product, r["product_id"]) if r.get("product_id") else None
        if r.get("product_id") and (not prod or prod.tenant_id != p.tenant.id):
            # nunca el producto (ni el costo) de otra empresa; se valida antes de gastar un consecutivo
            raise HTTPException(404, "Producto no encontrado")
    number, _ = next_number(db, p.tenant.id, "SC")
    req = PurchaseRequest(tenant_id=p.tenant.id, number=number, status="borrador", reason=reason, project_id=project_id, notes=note, created_by=p.user.id)
    total = Decimal(0)
    for r in rows:
        prod = db.get(Product, r["product_id"]) if r.get("product_id") else None
        cost = d(prod.cost) if prod and prod.cost is not None else Decimal(0)
        qty = d(r.get("to_buy") or r.get("quantity") or 0)
        req.lines.append(
            PurchaseRequestLine(product_id=r.get("product_id"), name=r.get("name") or (prod.name if prod else "Material"), quantity=qty, unit_cost=cost)
        )
        total += cost * qty
        if prod and prod.supplier_id and not req.supplier_id:
            req.supplier_id = prod.supplier_id
    req.total_cost = total.quantize(Decimal("0.01"))
    db.add(req)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "purchase_request", req.id, {"reason": reason, "lineas": len(req.lines)}, ip=p.ip)
    db.commit()
    return _req_out(db, req, p)


def _req_out(db: Session, r: PurchaseRequest, p: Principal) -> dict:
    """Solicitud de compra. El costo (unitario y total) es informacion de costos: sin catalog.costos sale vacio
    (Andres 08:07); el supervisor y el contador ven que pedir y cuanto, no a que precio."""
    s = db.get(Supplier, r.supplier_id) if r.supplier_id else None
    pr = db.get(Project, r.project_id) if r.project_id else None
    costos = p.sees_costs
    return {
        "id": r.id,
        "number": r.number,
        "status": r.status,
        "reason": r.reason,
        "supplier_id": r.supplier_id,
        "supplier": s.name if s else None,
        "project_id": r.project_id,
        "project": pr.number if pr else None,
        "total_cost": r.total_cost if costos else None,
        "currency": r.currency,
        "notes": r.notes,
        "created_at": r.created_at,
        "lines": [
            {"id": x.id, "product_id": x.product_id, "name": x.name, "quantity": x.quantity, "unit_cost": x.unit_cost if costos else None} for x in r.lines
        ],
    }


@router.get("/purchase-requests")
def purchase_requests(status: str | None = None, p: Principal = Depends(require("purchases", "ver")), db: Session = Depends(get_db)):
    stmt = select(PurchaseRequest).where(PurchaseRequest.tenant_id == p.tenant.id, live(PurchaseRequest))
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    return [_req_out(db, r, p) for r in db.scalars(stmt.order_by(PurchaseRequest.id.desc()).limit(100))]


class ReqLineIn(BaseModel):
    product_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(Decimal(1), gt=0)
    unit_cost: Decimal = Field(Decimal(0), ge=0)


class ReqIn(BaseModel):
    supplier_id: int | None = None
    project_id: int | None = None
    notes: str | None = None
    lines: list[ReqLineIn] = Field(min_length=1)


@router.post("/purchase-requests", status_code=201)
def purchase_create(data: ReqIn, p: Principal = Depends(require("purchases", "crear")), db: Session = Depends(get_db)):
    # proveedor y proyecto de ESTA empresa: si no, la solicitud mostraria el nombre y el numero de otra
    if data.supplier_id:
        sup = db.get(Supplier, data.supplier_id)
        if not sup or sup.tenant_id != p.tenant.id:
            raise HTTPException(404, "Proveedor no encontrado")
    if data.project_id:
        pr = db.get(Project, data.project_id)
        if not pr or pr.tenant_id != p.tenant.id:
            raise HTTPException(404, "Proyecto no encontrado")
    rows = [{"product_id": x.product_id, "name": x.name, "quantity": x.quantity, "to_buy": x.quantity} for x in data.lines]
    out = _new_request(db, p, rows, reason="manual", project_id=data.project_id, note=data.notes)
    if data.supplier_id:
        r = db.get(PurchaseRequest, out["id"])
        r.supplier_id = data.supplier_id
        db.commit()
        return _req_out(db, r, p)
    return out


class ReqStatusIn(BaseModel):
    status: str = Field(pattern="^(borrador|solicitada|recibida|cancelada)$")
    warehouse_id: int | None = None


@router.patch("/purchase-requests/{rid}")
def purchase_status(rid: int, data: ReqStatusIn, p: Principal = Depends(require("purchases", "crear")), db: Session = Depends(get_db)):
    """Al marcarla recibida entra a inventario con su costo."""
    r = db.get(PurchaseRequest, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Solicitud no encontrada")
    if r.status == "reservada":
        # pedido web sin pagar: la compra nace al cobrar (no se le devuelve nada al proveedor). Se descarta cancelando
        # el pedido (cancelarla aqui dejaba un pedido que despues se paga sin su compra)
        from ..models import Order
        from ..services import store_inventory

        o = db.get(Order, r.order_id) if r.order_id else None
        ped = f"el pedido web {o.number}" if o else "el pedido web"
        solo = " (se cancela solo al vencer el plazo para pagar)" if store_inventory.settings_of(p.tenant).get("cancel_unpaid_on_expiry") else ""
        raise HTTPException(
            409,
            f"Esta solicitud espera el pago de {ped}: pasa a borrador sola cuando se paga. Para descartarla, cancelá ese pedido en Pedidos de la tienda{solo}.",
        )
    if r.status == "recibida" and data.status != "recibida":
        # la entrada ya esta en el kardex: cambiarla de estado y volver a recibirla la sumaba otra vez
        raise HTTPException(409, "La compra ya se recibió y su mercadería entró a bodega. Para devolverla registrá una salida por devolución en Inventarios.")
    if data.status == "recibida" and r.status != "recibida":
        wh = data.warehouse_id or (invsvc.default_warehouse(db, p.tenant.id).id if invsvc.default_warehouse(db, p.tenant.id) else None)
        if not wh:
            raise HTTPException(422, "No hay bodega configurada")
        for ln in r.lines:
            if ln.product_id and d(ln.quantity) > 0:
                invsvc.move(
                    db, p.tenant.id, p.user.id, ln.product_id, wh, "entrada", d(ln.quantity), reference=r.number, note="Compra recibida", unit_cost=ln.unit_cost
                )
        if r.reason == "pedido_web" and r.order_id:
            # lo que se compro para un pedido web queda reservado para ese pedido (no se lo lleva el siguiente cliente)
            from ..services import store_inventory

            store_inventory.reserve_received(db, r, wh, p.user.id)
    r.status = data.status
    audit(db, p.tenant.id, p.user.id, "status", "purchase_request", r.id, {"status": data.status}, ip=p.ip)
    db.commit()
    return _req_out(db, r, p)


# ---------- Activos del cliente ----------
class AssetIn(BaseModel):
    customer_id: int
    project_id: int | None = None
    work_order_id: int | None = None
    product_id: int | None = None
    name: str = Field(min_length=2, max_length=200)
    model: str | None = Field(None, max_length=120)
    serial: str | None = Field(None, max_length=80)
    location: str | None = Field(None, max_length=200)
    ip: str | None = Field(None, max_length=45)
    mac: str | None = Field(None, max_length=32)
    firmware: str | None = Field(None, max_length=40)
    installed_at: date | None = None
    warranty_until: date | None = None
    supplier_id: int | None = None
    purchase_ref: str | None = Field(None, max_length=120)
    status: str = Field("activo", pattern="^(activo|retirado|garantia|reemplazado)$")
    notes: str | None = None
    photos: list = Field(default_factory=list)


# lo que el activo enlaza tiene que ser de esta empresa: la ficha responde el nombre del cliente y el proyecto
ASSET_REFS = {
    "customer_id": (Customer, "Cliente no encontrado"),
    "project_id": (Project, "Proyecto no encontrado"),
    "work_order_id": (WorkOrder, "Orden de trabajo no encontrada"),
    "product_id": (Product, "Producto no encontrado"),
    "supplier_id": (Supplier, "Proveedor no encontrado"),
}


def _asset_out(db: Session, a: CustomerAsset) -> dict:
    c = db.get(Customer, a.customer_id)
    pr = db.get(Project, a.project_id) if a.project_id else None
    return {
        **{
            k: getattr(a, k)
            for k in (
                "id",
                "customer_id",
                "project_id",
                "work_order_id",
                "product_id",
                "name",
                "model",
                "serial",
                "location",
                "ip",
                "mac",
                "firmware",
                "installed_at",
                "warranty_until",
                "supplier_id",
                "purchase_ref",
                "status",
                "notes",
                "photos",
            )
        },
        "customer": c.name if c else None,
        "project": pr.number if pr else None,
        "warranty_days": (a.warranty_until - date.today()).days if a.warranty_until else None,
    }


@router.get("/assets")
def assets(
    customer_id: int | None = None,
    q: str | None = None,
    expiring: bool = False,
    p: Principal = Depends(require("assets", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(CustomerAsset).where(CustomerAsset.tenant_id == p.tenant.id, live(CustomerAsset))
    if customer_id:
        stmt = stmt.where(CustomerAsset.customer_id == customer_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((CustomerAsset.name.ilike(like)) | (CustomerAsset.serial.ilike(like)) | (CustomerAsset.location.ilike(like)))
    rows = db.scalars(stmt.order_by(CustomerAsset.id.desc()).limit(300)).all()
    if expiring:
        rows = [a for a in rows if a.warranty_until and 0 <= (a.warranty_until - date.today()).days <= 45]
    return [_asset_out(db, a) for a in rows]


@router.post("/assets", status_code=201)
def asset_create(data: AssetIn, p: Principal = Depends(require("assets", "crear")), db: Session = Depends(get_db)):
    check_refs(db, p.tenant.id, data.model_dump(), ASSET_REFS)
    a = CustomerAsset(tenant_id=p.tenant.id, **data.model_dump())
    db.add(a)
    db.flush()
    _asset_stock(db, p, a.project_id, a.product_id, "equipo registrado como instalado")
    audit(db, p.tenant.id, p.user.id, "create", "asset", a.id, ip=p.ip)
    db.commit()
    return _asset_out(db, a)


@router.put("/assets/{aid}")
def asset_update(aid: int, data: AssetIn, p: Principal = Depends(require("assets", "editar")), db: Session = Depends(get_db)):
    a = db.get(CustomerAsset, aid)
    if not a or a.tenant_id != p.tenant.id:
        raise HTTPException(404, "Activo no encontrado")
    check_refs(db, p.tenant.id, data.model_dump(), ASSET_REFS, current=a)
    antes = (a.project_id, a.product_id)
    for k, v in data.model_dump().items():
        setattr(a, k, v)
    db.flush()
    _asset_stock(db, p, antes[0], antes[1], "equipo instalado corregido")
    _asset_stock(db, p, a.project_id, a.product_id, "equipo instalado corregido")
    db.commit()
    return _asset_out(db, a)


def _asset_stock(db: Session, p: Principal, project_id: int | None, product_id: int | None, reason: str) -> None:
    """Un equipo instalado en un proyecto salio de bodega: el kardex del proyecto se concilia (nunca dos veces)."""
    if not project_id or not product_id:
        return
    pr = db.get(Project, project_id)
    if pr and pr.tenant_id == p.tenant.id:
        from ..services import project_stock

        project_stock.reconcile(db, pr, [product_id], p.user.id, reason=reason)


# ---------- Informe de entrega ----------
@router.get("/projects/{pid}/report", response_class=HTMLResponse)
def delivery_report(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    """Informe técnico de entrega con fotografías, equipos instalados y tiempos. Imprimible o a PDF."""
    from ..routers.fieldwork import _names, order_hours  # import local: fieldwork ya importa de aqui

    pr = _project(db, pid, p)
    c = db.get(Customer, pr.customer_id) if pr.customer_id else None
    esc = html.escape
    orders = ""
    photos: list[str] = list(pr.photos or [])
    # nombres solo de gente de la empresa del proyecto (un id ajeno en una orden no devuelve el nombre de otra empresa)
    nombres = _names(db, list(pr.orders), pr.tenant_id) if pr.orders else {}
    for o in pr.orders:
        tecnico = nombres.get(o.technician_id) if o.technician_id else None
        tasks = "".join(f"<li>{'☑' if x.get('done') else '☐'} {esc(str(x.get('text', '')))}</li>" for x in (o.tasks or []))
        mats = "".join(f"<tr><td>{esc(m.name)}</td><td class=n>{d(m.quantity):g} {esc(m.unit)}</td></tr>" for m in o.materials if d(m.quantity) > 0)
        photos += list(o.photos or [])
        h_ot, fuente = order_hours(o)
        hrs = "—" if h_ot is None else f"{h_ot} h·persona" if fuente == "registro" else f"{h_ot} h"
        orders += f"""<section class=block><h3>{esc(o.number)} · {esc(o.title)}</h3>
        <div class=m>{esc(tecnico or "Sin técnico")} · {local_date(o.finished_at).strftime("%d/%m/%Y") if o.finished_at else "en curso"} · {hrs}</div>
        {f"<ul class=tasks>{tasks}</ul>" if tasks else ""}
        {f"<table class=t><tr><th>Material utilizado</th><th class=n>Cantidad</th></tr>{mats}</table>" if mats else ""}
        {f"<p class=obs>{esc(o.notes)}</p>" if o.notes else ""}</section>"""
    assets = db.scalars(select(CustomerAsset).where(CustomerAsset.project_id == pr.id, live(CustomerAsset))).all()
    assets_html = "".join(
        f"<tr><td>{esc(a.name)}</td><td class=mono>{esc(a.serial or '—')}</td><td>{esc(a.location or '—')}</td><td>{a.warranty_until or '—'}</td></tr>"
        for a in assets
    )
    from ..services.media_access import sign_urls  # evidencias privadas: enlace firmado al armar el informe

    gal = "".join(f'<img src="{esc(u)}" alt="">' for u in sign_urls(db, p.tenant.id, photos[:24]) if isinstance(u, str))
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><title>Informe {esc(pr.number)}</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;color:#15131a;max-width:820px;margin:28px auto;padding:0 18px}}
h1{{font-size:26px;margin:0}}h3{{font-size:16px;margin:0 0 4px}}.m{{color:#6b6570;font-size:12.5px}}
.brand{{border-left:4px solid #e2233a;padding-left:12px;margin-bottom:20px}}
.block{{border:1px solid #eee;border-radius:12px;padding:14px;margin:12px 0}}
.t{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}}.t td,.t th{{border-bottom:1px solid #eee;padding:6px 0;text-align:left}}
.n{{text-align:right}}.mono{{font-family:ui-monospace,monospace}}.tasks{{font-size:13px;columns:2;padding-left:18px}}
.obs{{font-size:13px;background:#faf7f4;padding:10px;border-radius:8px}}
.gal{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:10px}}
.gal img{{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:8px}}
@media print{{button{{display:none}}.block{{break-inside:avoid}}}}</style></head><body>
<div class=brand><h1>{esc(p.tenant.name)}</h1><div class=m>Informe técnico de entrega · {esc(pr.number)}</div></div>
<h2 style="font-size:20px;margin:0 0 6px">{esc(pr.name)}</h2>
<div class=m>{esc(c.name if c else "")}{" · " + esc(pr.site) if pr.site else ""} · {pr.start_date or ""} a {pr.end_date or pr.delivered_at.date() if pr.delivered_at else ""}</div>
{f"<p>{esc(pr.scope)}</p>" if pr.scope else ""}
{orders}
{f"<section class=block><h3>Equipos instalados</h3><table class=t><tr><th>Equipo</th><th>Serie</th><th>Ubicación</th><th>Garantía</th></tr>{assets_html}</table></section>" if assets_html else ""}
{f"<section class=block><h3>Evidencias</h3><div class=gal>{gal}</div></section>" if gal else ""}
<p class=m>Recibido a satisfacción por: ______________________________  ·  Fecha: ____________</p>
<button onclick=print()>Imprimir</button></body></html>"""


@router.post("/projects/{pid}/invoice", status_code=201)
def project_invoice(pid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    """Proyecto entregado -> factura con las líneas de la cotización original."""
    pr = _project(db, pid, p)
    if pr.invoice_id:
        raise HTTPException(409, "El proyecto ya fue facturado")
    if not pr.quote_id:
        raise HTTPException(422, "El proyecto no viene de una cotización; facturá desde Facturas")
    q = db.get(Quote, pr.quote_id)
    from ..services import documents as svc

    ya_convertida = q.status == "convertida"
    inv = db.get(Invoice, q.converted_invoice_id) if ya_convertida else svc.convert_quote(db, p.tenant.id, p.user.id, q, customer_id=pr.customer_id)
    antes = pr.status
    pr.invoice_id, pr.status = inv.id, "facturado"
    # el material sale UNA vez: lo que las ordenes ya sacaron no vuelve a salir y lo que ninguna orden saco sale ahora
    # (antes, con alguna orden aplicada la factura no descontaba nada, y una cotizacion ya convertida descontaba doble)
    from ..services import project_stock

    project_stock.reconcile_all(db, pr, p.user.id, reason="proyecto facturado", invoice=inv)
    from ..services import commissions as comsvc

    comsvc.link_to_project(db, pr)
    comsvc.on_project_status(db, pr, antes)
    audit(db, p.tenant.id, p.user.id, "invoice", "project", pr.id, {"invoice_id": inv.id}, ip=p.ip)
    db.commit()
    return {"invoice_id": inv.id, "number": inv.number}
