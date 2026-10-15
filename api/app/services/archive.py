"""Archivo y papelera de levantamientos, proyectos, oportunidades y ordenes de trabajo, y (papelera2) de tickets,
contratos, activos, solicitudes de compra, clientes, productos, proveedores y cupones.

Dos pasos, los dos reversibles hasta la purga:
- Archivar: sale de las listas, contadores, embudo y reportes, pero se conserva para siempre.
- Papelera ("proximos a borrar"): igual que archivado, y ademas el worker lo borra definitivamente despues de
  N dias (tenant.settings.archive_purge_days, 30 por defecto). Mientras tanto se restaura.

Lo fiscal NO entra aqui: facturas y cotizaciones tienen "anular" y la factura es un comprobante electronico
que Hacienda ya conoce; borrarlo romperia el consecutivo y la trazabilidad que exige la ley. Por la misma
razon no se manda a la papelera nada que cuelgue de una factura (proyecto facturado) ni de una cotizacion
convertida en factura.

Papelera2 (Andres: "que en cada apartado se puedan borrar"): lo maestro (clientes, productos, proveedores) solo va a
la papelera si nada lo usa; si tiene documentos, movimientos o pedidos se ofrece archivarlo/desactivarlo en su lugar,
que lo saca de listas y busquedas sin perder la historia. Los modelos con "active" lo apagan al archivar o mandar a
la papelera (asi desaparecen de todas las busquedas que ya filtran por active) y lo prenden al restaurar.
Tampoco entra lo que se excluye o se anula en vez de borrarse: pagos, notas de credito, gastos, movimientos de
inventario, cargas de combustible, pedidos web y la bitacora.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AllyCompany,
    AllyParticipation,
    Coupon,
    Customer,
    CustomerAsset,
    Expense,
    Invoice,
    InvoiceLine,
    MaintenanceContract,
    Opportunity,
    Order,
    OrderLine,
    Product,
    Project,
    PurchaseRequest,
    PurchaseRequestLine,
    Quote,
    QuoteLine,
    Recurrence,
    StockLock,
    StockMovement,
    StockReservation,
    StockUnit,
    StoreStockAlert,
    Supplier,
    SupportTicket,
    Survey,
    SurveyItem,
    Tenant,
    TenantUser,
    User,
    WorkOrder,
    WorkOrderMaterial,
)
from .documents import audit

log = logging.getLogger(__name__)
DEFAULT_PURGE_DAYS = 30
OPEN_ORDER = ("asignada", "en_sitio", "en_proceso")
IN_PROGRESS_ORDER = ("en_sitio", "en_proceso")

# tipo -> (modelo, modulo de permisos, nombre para mensajes)
KINDS: dict[str, tuple[type, str, str]] = {
    "survey": (Survey, "field", "Levantamiento"),
    "project": (Project, "projects", "Proyecto"),
    "opportunity": (Opportunity, "crm_pipeline", "Oportunidad"),
    "work_order": (WorkOrder, "field", "Orden de trabajo"),
    # papelera2
    "ticket": (SupportTicket, "support_desk", "Ticket de soporte"),
    "contract": (MaintenanceContract, "support_desk", "Contrato de mantenimiento"),
    "asset": (CustomerAsset, "assets", "Activo de cliente"),
    "purchase": (PurchaseRequest, "purchases", "Solicitud de compra"),
    "customer": (Customer, "crm", "Cliente"),
    "product": (Product, "catalog", "Producto"),
    "supplier": (Supplier, "catalog", "Proveedor"),
    "coupon": (Coupon, "catalog", "Cupón"),
}
# Accion del modulo que hace falta (por defecto "editar"). Las solicitudes de compra las maneja quien las crea:
# el supervisor pide materiales (purchases.crear) y es quien borra la duplicada.
ACTION = {"purchase": "crear"}
# Ademas, ver todo el modulo: un tecnico atiende sus tickets pero no borra tickets ni contratos.
NEEDS_ALL = {"ticket", "contract"}
# Accion del modulo para VERLO en el Archivo (por defecto "ver"). Los cupones son de quien administra la tienda:
# con catalog.ver (tecnico, aliado) no se listan sus codigos, igual que GET /coupons.
VIEW_ACTION = {"coupon": "editar"}
# Modelos con "active": archivar/papelera lo apagan y restaurar lo prende (ver docstring).
ACTIVE_FLAG = {"customer", "product", "contract", "coupon"}
# Orden de la purga del worker: primero lo que cuelga de otros, al final lo maestro.
PURGE_ORDER = ("work_order", "survey", "opportunity", "ticket", "purchase", "asset", "contract", "coupon", "project", "product", "supplier", "customer")
# Tipos que se piden a veces pero que la regla fiscal (o la trazabilidad) excluye: se responde 409 explicando por que
FISCAL = {
    "invoice": "Las facturas son comprobantes electrónicos: no se archivan ni se borran. Si hubo un error, anulala (nota de crédito).",
    "quote": "Las cotizaciones no van a la papelera: anulala. Si ya se convirtió en factura, la factura es un comprobante fiscal y se conserva.",
    "credit_note": "Las notas de crédito son comprobantes electrónicos: no se borran.",
    "payment": "Los pagos no se borran: quedan ligados a su factura y al cierre de caja. Si hubo un error, anulá la factura con nota de crédito.",
    "expense": "Los gastos no se borran: cambiá su estado a «anulado» y queda la constancia.",
    "stock_movement": "Los movimientos de inventario no se borran: el kardex es la historia. Corregí con un movimiento de ajuste.",
    "fuel": "Las cargas de combustible no se borran: marcala como excluida (con el motivo) y deja de contar.",
    "order": "Los pedidos web no se borran: cancelalo o reembolsalo desde el pedido.",
    "audit": "La bitácora no se borra ni se edita.",
}


def live(model):
    """Filtro de 'activo': ni archivado ni en papelera. Todas las listas y contadores lo usan."""
    return and_(model.archived_at.is_(None), model.trashed_at.is_(None))


def is_live(obj) -> bool:
    return obj.archived_at is None and obj.trashed_at is None


def purge_days(tenant: Tenant) -> int:
    try:
        n = int((tenant.settings or {}).get("archive_purge_days", DEFAULT_PURGE_DAYS))
    except (TypeError, ValueError):
        n = DEFAULT_PURGE_DAYS
    return max(n, 1)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def purge_at(obj, tenant: Tenant) -> datetime | None:
    return _aware(obj.trashed_at) + timedelta(days=purge_days(tenant)) if obj.trashed_at else None


def title_of(obj) -> str:
    return getattr(obj, "title", None) or getattr(obj, "subject", None) or getattr(obj, "name", None) or getattr(obj, "site", None) or ""


def ref_of(obj) -> str:
    """Como se nombra en los mensajes: el consecutivo, el codigo o el nombre."""
    return getattr(obj, "number", None) or getattr(obj, "code", None) or getattr(obj, "name", None) or f"#{obj.id}"


def status_of(obj) -> str | None:
    st = getattr(obj, "status", None)
    if st is None and hasattr(obj, "active"):
        st = "activo" if obj.active else "inactivo"
    return st


def resolve(kind: str) -> tuple[type, str, str]:
    if kind in FISCAL:
        raise HTTPException(409, FISCAL[kind])
    if kind not in KINDS:
        raise HTTPException(404, "Tipo desconocido")
    return KINDS[kind]


# ---------- Reglas de integridad ----------
def _quote_blocks(db: Session, quote_id: int | None) -> str | None:
    if not quote_id:
        return None
    q = db.get(Quote, quote_id)
    if q and q.status == "convertida":
        return f"su cotización {q.number} ya se aprobó y se convirtió en factura"
    return None


def blockers(db: Session, kind: str, obj, to: str) -> list[str]:
    """Dependencias vivas que impiden archivar (to='archive') o mandar a la papelera (to='trash').
    Archivar es mas permisivo: solo se frena lo que esta en operacion (trabajo en campo abierto)."""
    out: list[str] = []
    if kind == "project":
        orders = [o for o in obj.orders if o.trashed_at is None]
        abiertas = [o.number for o in orders if o.status in OPEN_ORDER]
        if abiertas:
            out.append(f"tiene órdenes de trabajo abiertas ({', '.join(abiertas[:5])})")
        if to == "trash":
            if obj.invoice_id or obj.status == "facturado":
                out.append("ya está facturado (la factura es un comprobante fiscal)")
            if any(o.stock_applied for o in orders):
                out.append("sus órdenes ya descontaron inventario")
            if db.scalar(select(MaintenanceContract.id).where(MaintenanceContract.project_id == obj.id, MaintenanceContract.active)):
                out.append("tiene un contrato de mantenimiento activo")
    elif kind == "work_order":
        if obj.status in IN_PROGRESS_ORDER:
            out.append("el técnico la está ejecutando (en sitio / en proceso)")
        if to == "trash" and obj.stock_applied:
            out.append("ya descontó inventario")
    elif kind == "opportunity" and to == "trash":
        why = _quote_blocks(db, obj.quote_id)
        if why:
            out.append(why)
        if obj.project_id:
            pr = db.get(Project, obj.project_id)
            if pr and pr.trashed_at is None:
                out.append(f"tiene el proyecto {pr.number}")
    elif kind == "survey" and to == "trash":
        why = _quote_blocks(db, obj.quote_id)
        if why:
            out.append(why)
        pr = db.scalar(select(Project).where(Project.survey_id == obj.id, Project.trashed_at.is_(None)))
        if pr:
            out.append(f"el proyecto {pr.number} salió de este levantamiento")
    elif to == "trash" and kind in PAPELERA2:
        out.extend(_ref_blockers(db, kind, obj))
    return out


# ---------- papelera2: quien usa a quien ----------
PAPELERA2 = {"ticket", "contract", "asset", "purchase", "customer", "product", "supplier", "coupon"}


def _refs(kind: str) -> list[tuple]:
    """(columna que apunta, singular, plural, solo_lo_vivo).
    solo_lo_vivo: lo que esta en la papelera no frena (se suelta al purgar, ver _detach). Lo maestro (clientes,
    productos, proveedores) cuenta todo, tambien lo que esta en la papelera: un cliente con un contrato en la
    papelera espera a que el contrato se borre; el worker purga primero lo que cuelga y al final lo maestro."""
    return {
        "customer": [
            (Quote.customer_id, "cotización", "cotizaciones", False),
            (Invoice.customer_id, "factura", "facturas", False),
            (Order.customer_id, "pedido", "pedidos", False),
            (Recurrence.customer_id, "recurrencia", "recurrencias", False),
            (Opportunity.customer_id, "oportunidad", "oportunidades", False),
            (Opportunity.end_customer_id, "oportunidad como cliente final", "oportunidades como cliente final", False),
            (Survey.customer_id, "levantamiento", "levantamientos", False),
            (Project.customer_id, "proyecto", "proyectos", False),
            (Project.end_customer_id, "proyecto como cliente final", "proyectos como cliente final", False),
            (WorkOrder.customer_id, "orden de trabajo", "órdenes de trabajo", False),
            (SupportTicket.customer_id, "ticket de soporte", "tickets de soporte", False),
            (MaintenanceContract.customer_id, "contrato de mantenimiento", "contratos de mantenimiento", False),
            (CustomerAsset.customer_id, "equipo instalado", "equipos instalados", False),
            (AllyParticipation.customer_id, "participación como aliado", "participaciones como aliado", False),
            (TenantUser.customer_id, "usuario del portal de clientes", "usuarios del portal de clientes", False),
            # la empresa aliada que se factura a este cliente (la empresa aliada no va a la papelera: se desactiva)
            (AllyCompany.customer_id, "empresa aliada (facturación)", "empresas aliadas (facturación)", False),
        ],
        "product": [
            (QuoteLine.product_id, "línea de cotización", "líneas de cotizaciones", False),
            (InvoiceLine.product_id, "línea de factura", "líneas de facturas", False),
            (OrderLine.product_id, "línea de pedido", "líneas de pedidos", False),
            (StockMovement.product_id, "movimiento de inventario", "movimientos de inventario", False),
            (StockUnit.product_id, "serie en inventario", "series en inventario", False),
            (StockReservation.product_id, "reserva de inventario", "reservas de inventario", False),
            (StockLock.product_id, "apartado de inventario", "apartados de inventario", False),
            (SurveyItem.product_id, "material de levantamiento", "materiales de levantamientos", False),
            (WorkOrderMaterial.product_id, "material de orden de trabajo", "materiales de órdenes de trabajo", False),
            (CustomerAsset.product_id, "equipo instalado", "equipos instalados", False),
            (PurchaseRequestLine.product_id, "línea de solicitud de compra", "líneas de solicitudes de compra", False),
        ],
        "supplier": [
            (Product.supplier_id, "producto", "productos", False),
            (Expense.supplier_id, "gasto", "gastos", False),
            (CustomerAsset.supplier_id, "equipo instalado", "equipos instalados", False),
            (PurchaseRequest.supplier_id, "solicitud de compra", "solicitudes de compra", False),
            (AllyParticipation.supplier_id, "participación como aliado", "participaciones como aliado", False),
            (AllyCompany.supplier_id, "empresa aliada (pagos)", "empresas aliadas (pagos)", False),
        ],
        "asset": [
            (SupportTicket.asset_id, "ticket de soporte", "tickets de soporte", True),
            (StockUnit.customer_asset_id, "serie de inventario instalada", "series de inventario instaladas", False),
        ],
        "contract": [
            (SupportTicket.contract_id, "ticket de soporte", "tickets de soporte", True),
        ],
    }.get(kind, [])


def _count(db: Session, col, oid: int, only_live: bool) -> int:
    model = col.class_
    stmt = select(func.count()).select_from(model).where(col == oid)
    if only_live and hasattr(model, "trashed_at"):
        stmt = stmt.where(model.trashed_at.is_(None))
    return db.scalar(stmt) or 0


def _ref_blockers(db: Session, kind: str, obj) -> list[str]:
    out: list[str] = []
    if kind == "ticket":
        if obj.invoice_id:
            inv = db.get(Invoice, obj.invoice_id)
            out.append(f"ya se cobró con la factura {inv.number}" if inv else "ya se cobró con una factura")
        if obj.work_order_id:
            o = db.get(WorkOrder, obj.work_order_id)
            if o and o.trashed_at is None and o.status in OPEN_ORDER:
                out.append(f"tiene la orden de trabajo {o.number} abierta")
        return out
    if kind == "purchase":
        if obj.status == "recibida":
            out.append("ya se recibió: la mercadería entró al inventario")
        if obj.order_id:
            o = db.get(Order, obj.order_id)
            out.append(f"la generó el pedido web {o.number}: se maneja desde el pedido" if o else "la generó un pedido web: se maneja desde el pedido")
        return out
    if kind == "coupon":
        used = obj.uses or db.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == obj.tenant_id, Order.coupon_code == obj.code)) or 0
        if used:
            out.append(f"ya se usó en {used} pedido{'s' if used != 1 else ''}")
        return out
    parts = []
    for col, one, many, only_live in _refs(kind):
        n = _count(db, col, obj.id, only_live)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if parts:
        out.append("tiene " + (", ".join(parts[:-1]) + " y " + parts[-1] if len(parts) > 1 else parts[0]))
    return out


def check(db: Session, kind: str, obj, to: str) -> None:
    b = blockers(db, kind, obj, to)
    if b:
        accion = "archivar" if to == "archive" else "mandar a la papelera"
        raise HTTPException(409, f"No se puede {accion} {ref_of(obj)}: " + "; ".join(b) + ".")


def alternative(kind: str) -> dict:
    """Lo que se ofrece cuando no puede ir a la papelera: archivar (o desactivar, si el modelo tiene "active")."""
    if kind in ACTIVE_FLAG:
        return {
            "action": "archive",
            "label": "Desactivar",
            "hint": "Podés desactivarlo: deja de aparecer en listas, búsquedas y ventas, y se conserva con toda su historia. Se reactiva desde Archivo.",
        }
    return {
        "action": "archive",
        "label": "Archivar",
        "hint": "Podés archivarlo: sale de las listas y se conserva completo, con toda su historia. Se restaura desde Archivo.",
    }


def preflight(db: Session, kind: str, obj, tenant: Tenant) -> dict:
    """Lo que el portal muestra antes de confirmar el basurero: si puede ir a la papelera, por que no y la alternativa."""
    trash_b = [] if obj.trashed_at else blockers(db, kind, obj, "trash")
    archive_b = None if obj.archived_at or obj.trashed_at else blockers(db, kind, obj, "archive")
    ref, title = ref_of(obj), title_of(obj)
    return {
        "kind": kind,
        "id": obj.id,
        "number": ref,
        "title": title if title != ref else "",
        "status": status_of(obj),
        "purge_days": purge_days(tenant),
        "archived": obj.archived_at is not None,
        "trashed": obj.trashed_at is not None,
        "trash": trash_b,
        "archive": archive_b,
        "alt": alternative(kind) if trash_b and archive_b == [] else None,
    }


# ---------- Acciones ----------
def archive(db: Session, kind: str, obj, user_id: int, ip=None) -> None:
    if obj.trashed_at:
        raise HTTPException(409, "Está en la papelera: restauralo primero")
    if obj.archived_at:
        return
    check(db, kind, obj, "archive")
    obj.archived_at, obj.archived_by = datetime.now(UTC), user_id
    if kind in ACTIVE_FLAG:
        obj.active = False
    audit(db, obj.tenant_id, user_id, "archive", kind, obj.id, {"number": ref_of(obj)}, ip=ip)


def trash(db: Session, kind: str, obj, user_id: int, ip=None) -> None:
    if obj.trashed_at:
        return
    check(db, kind, obj, "trash")
    obj.trashed_at, obj.trashed_by = datetime.now(UTC), user_id
    if kind in ACTIVE_FLAG:
        obj.active = False
    audit(db, obj.tenant_id, user_id, "trash", kind, obj.id, {"number": ref_of(obj)}, ip=ip)


def restore(db: Session, kind: str, obj, user_id: int, ip=None) -> None:
    """Vuelve a las listas activas (sale del archivo y de la papelera)."""
    was = "papelera" if obj.trashed_at else ("archivo" if obj.archived_at else None)
    if not was:
        return
    obj.archived_at = obj.archived_by = obj.trashed_at = obj.trashed_by = None
    if kind in ACTIVE_FLAG:
        obj.active = True
    audit(db, obj.tenant_id, user_id, "restore", kind, obj.id, {"number": ref_of(obj), "desde": was}, ip=ip)


def _detach(db: Session, kind: str, obj) -> None:
    """Suelta las referencias de registros que NO son suyos (se conservan, solo pierden el enlace)."""
    if kind == "survey":
        db.execute(update(Project).where(Project.survey_id == obj.id).values(survey_id=None))
    elif kind == "opportunity":
        from ..models import AllyCostRequest, AllyParticipation
        from . import commissions

        # el acuerdo economico de cada aliado (reglas por participacion) se va con el: los movimientos que usaron esas
        # reglas (anulados, o de un proyecto ya borrado) sueltan rule_id antes del borrado. Sin esto, la cascada de
        # fk_crule_participation choca con commission.rule_id (NO ACTION) y la purga revienta (IntegrityError)
        for part in db.scalars(select(AllyParticipation).where(AllyParticipation.opportunity_id == obj.id)).all():
            commissions.drop_participation_rules(db, part)
        # aliados y solicitudes de costo son de la oportunidad: se van con ella (tambien en SQLite sin cascada)
        db.execute(delete(AllyCostRequest).where(AllyCostRequest.opportunity_id == obj.id))
        db.execute(delete(AllyParticipation).where(AllyParticipation.opportunity_id == obj.id))
        db.execute(update(Survey).where(Survey.opportunity_id == obj.id).values(opportunity_id=None))
        db.execute(update(Project).where(Project.opportunity_id == obj.id).values(opportunity_id=None))
    elif kind == "project":
        order_ids = [o.id for o in obj.orders]
        db.execute(update(Opportunity).where(Opportunity.project_id == obj.id).values(project_id=None))
        from ..models import AllyParticipation

        db.execute(update(AllyParticipation).where(AllyParticipation.project_id == obj.id).values(project_id=None))
        db.execute(update(CustomerAsset).where(CustomerAsset.project_id == obj.id).values(project_id=None))
        db.execute(update(PurchaseRequest).where(PurchaseRequest.project_id == obj.id).values(project_id=None))
        db.execute(update(SupportTicket).where(SupportTicket.project_id == obj.id).values(project_id=None))
        db.execute(update(MaintenanceContract).where(MaintenanceContract.project_id == obj.id).values(project_id=None))
        if order_ids:
            db.execute(update(CustomerAsset).where(CustomerAsset.work_order_id.in_(order_ids)).values(work_order_id=None))
            db.execute(update(SupportTicket).where(SupportTicket.work_order_id.in_(order_ids)).values(work_order_id=None))
    elif kind == "work_order":
        db.execute(update(CustomerAsset).where(CustomerAsset.work_order_id == obj.id).values(work_order_id=None))
        db.execute(update(SupportTicket).where(SupportTicket.work_order_id == obj.id).values(work_order_id=None))
    elif kind == "asset":
        # tickets en la papelera que apuntaban al equipo (los vivos frenan antes)
        db.execute(update(SupportTicket).where(SupportTicket.asset_id == obj.id).values(asset_id=None))
    elif kind == "contract":
        db.execute(update(SupportTicket).where(SupportTicket.contract_id == obj.id).values(contract_id=None))
    elif kind == "product":
        # avisos de "avisame cuando haya" de la tienda: son del producto
        db.execute(delete(StoreStockAlert).where(StoreStockAlert.product_id == obj.id))


def purge(db: Session, kind: str, obj, user_id: int | None, ip=None) -> None:
    """Borrado definitivo. Solo desde la papelera. Borra en cascada lo suyo (puntos y materiales del
    levantamiento, ordenes del proyecto con sus materiales) y deja en la auditoria que se borro, quien lo
    mando a la papelera y cuando, y quien lo purgo (None = el worker por vencimiento)."""
    if not obj.trashed_at:
        raise HTTPException(409, "Solo se borra definitivamente lo que está en la papelera")
    check(db, kind, obj, "trash")  # algo pudo engancharse mientras estaba en la papelera
    who = db.get(User, obj.trashed_by) if obj.trashed_by else None
    snapshot = {
        "number": ref_of(obj),
        "title": title_of(obj),
        "status": status_of(obj),
        "trashed_at": _aware(obj.trashed_at).isoformat(),
        "trashed_by": obj.trashed_by,
        "trashed_by_name": who.full_name if who else None,
        "archived_at": _aware(obj.archived_at).isoformat() if obj.archived_at else None,
        "purged_by": user_id,
        "auto": user_id is None,
    }
    if kind == "project":
        snapshot["work_orders"] = [o.number for o in obj.orders]
    if kind == "survey":
        snapshot["points"] = len(obj.points)
        snapshot["items"] = len(obj.items)
    audit(db, obj.tenant_id, user_id, "purge", kind, obj.id, snapshot, ip=ip)
    _detach(db, kind, obj)
    db.delete(obj)
    db.flush()


def purge_expired(db: Session, now: datetime | None = None) -> int:
    """Tarea del worker: purga lo que cumplio los N dias en la papelera, empresa por empresa.
    Lo que quedo bloqueado por una dependencia nueva se salta (queda en la papelera, visible)."""
    now = now or datetime.now(UTC)
    n = 0
    for tenant in db.scalars(select(Tenant)):
        limit = now - timedelta(days=purge_days(tenant))
        # primero lo que depende (ordenes) y al final los proyectos, para no chocar con cascadas
        for kind in PURGE_ORDER:
            model = KINDS[kind][0]
            rows = db.scalars(select(model).where(model.tenant_id == tenant.id, model.trashed_at.is_not(None), model.trashed_at <= limit)).all()
            for obj in rows:
                oid = obj.id
                try:
                    with db.begin_nested():
                        purge(db, kind, obj, None)
                    n += 1
                except HTTPException:
                    continue
                except IntegrityError:
                    # una referencia que _detach no suelta: queda en la papelera y la corrida sigue con lo demas
                    log.exception("purga: %s %s de la empresa %s no se pudo borrar", kind, oid, tenant.id)
                    continue
    db.commit()
    return n


def listing(db: Session, tenant: Tenant, kinds: list[str], state: str) -> list[dict]:
    now = datetime.now(UTC)
    out = []
    users: dict[int, str | None] = {}

    def name(uid):
        if uid is None:
            return None
        if uid not in users:
            u = db.get(User, uid)
            users[uid] = u.full_name if u else None
        return users[uid]

    for kind in kinds:
        model, _, label = KINDS[kind]
        cond = model.trashed_at.is_not(None) if state == "papelera" else and_(model.archived_at.is_not(None), model.trashed_at.is_(None))
        for obj in db.scalars(select(model).where(model.tenant_id == tenant.id, cond).limit(300)):
            pa = purge_at(obj, tenant)
            out.append(
                {
                    "kind": kind,
                    "kind_label": label,
                    "id": obj.id,
                    "number": ref_of(obj),
                    "title": title_of(obj),
                    "status": status_of(obj),
                    "archived_at": obj.archived_at,
                    "archived_by": name(obj.archived_by),
                    "trashed_at": obj.trashed_at,
                    "trashed_by": name(obj.trashed_by),
                    "purge_at": pa,
                    "days_left": max(0, math.ceil((pa - now).total_seconds() / 86400)) if pa else None,
                }
            )
    out.sort(key=lambda r: _aware(r["trashed_at"] or r["archived_at"]), reverse=True)
    return out
