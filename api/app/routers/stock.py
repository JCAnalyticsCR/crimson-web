"""Inventario propio enriquecido: reservas formales (AC06/AC25), unidades serializadas y el libro de movimientos.

Solo inventario PROPIO de Crimson. owner/custodian quedan listos para cuando el inventario compartido con
aliados tenga usuarios propios (vacio = la empresa).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, get_principal, require
from ..models import CustomerAsset, Product, Project, StockMovement, StockReservation, StockUnit, User, Warehouse
from ..models.ops import UNIT_STATUSES
from ..services import inventory as invsvc
from ..services import reservations as rsv
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(tags=["inventario"])


def _can_request(p: Principal) -> bool:
    # pedir una reserva: bodega o quien lleva el proyecto. Aprobarla es otra cosa (inventory.aprobar_reserva).
    return p.can("inventory", "crear") or p.can("projects", "editar")


def _res_out(db: Session, r: StockReservation, names: dict | None = None) -> dict:
    prod = db.get(Product, r.product_id)
    w = db.get(Warehouse, r.warehouse_id)
    pr = db.get(Project, r.project_id) if r.project_id else None
    req = db.get(User, r.requested_by) if r.requested_by else None
    apr = db.get(User, r.approved_by) if r.approved_by else None
    exp = rsv._aware(r.expires_at)
    return {
        "id": r.id,
        "product_id": r.product_id,
        "code": prod.code if prod else None,
        "name": prod.name if prod else None,
        "warehouse_id": r.warehouse_id,
        "warehouse": w.name if w else None,
        "quantity": r.quantity,
        "consumed_quantity": r.consumed_quantity,
        "project_id": r.project_id,
        "project": pr.number if pr else None,
        "project_name": pr.name if pr else None,
        "survey_id": r.survey_id,
        "quote_id": r.quote_id,
        "status": r.status,
        "expires_at": r.expires_at,
        "expired": bool(exp and exp <= datetime.now(UTC) and r.status in rsv.OPEN),
        "requested_by": req.full_name if req else None,
        "approved_by": apr.full_name if apr else ("Regla automática" if r.status != "pendiente" and r.approved_at and not r.approved_by else None),
        "approved_at": r.approved_at,
        "reason": r.reason,
        "closed_note": r.closed_note,
        "created_at": r.created_at,
        "origin": r.origin or "interna",  # web = pedido de la tienda (automatica)
        "order_id": r.order_id,
    }


def _reservation(db: Session, rid: int, p: Principal) -> StockReservation:
    r = db.get(StockReservation, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Reserva no encontrada")
    return r


# ---------- Reservas ----------
class ReservationIn(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)
    warehouse_id: int | None = None
    project_id: int | None = None
    survey_id: int | None = None
    quote_id: int | None = None
    reason: str | None = Field(None, max_length=500)
    expires_at: datetime | None = None


@router.get("/stock/reservations")
def reservations(
    status: str | None = None,
    project_id: int | None = None,
    product_id: int | None = None,
    limit: int = Query(200, le=500),
    p: Principal = Depends(require("inventory", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(StockReservation).where(StockReservation.tenant_id == p.tenant.id)
    if status == "abiertas":
        stmt = stmt.where(StockReservation.status.in_(rsv.OPEN))
    elif status:
        stmt = stmt.where(StockReservation.status == status)
    if project_id:
        stmt = stmt.where(StockReservation.project_id == project_id)
    if product_id:
        stmt = stmt.where(StockReservation.product_id == product_id)
    rows = db.scalars(stmt.order_by(StockReservation.id.desc()).limit(limit)).all()
    return {"items": [_res_out(db, r) for r in rows], "can_approve": p.can("inventory", "aprobar_reserva")}


def _create_and_maybe_confirm(db: Session, p: Principal, data: ReservationIn) -> dict:
    r = rsv.create(
        db,
        p.tenant.id,
        p.user.id,
        data.product_id,
        data.quantity,
        data.warehouse_id,
        data.project_id,
        data.survey_id,
        data.quote_id,
        data.reason,
        data.expires_at,
        ip=p.ip,
    )
    db.commit()
    note = None
    if rsv.auto_approves(db, r):
        try:
            rsv.confirm(db, r, None, ip=p.ip)
        except HTTPException as e:
            r = db.get(StockReservation, r.id)
            note = f"Queda pendiente: la regla automática no pudo confirmarla. {e.detail}"
    return {**_res_out(db, r), "note": note}


@router.post("/stock/reservations", status_code=201)
def reservation_create(data: ReservationIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not _can_request(p):
        raise HTTPException(403, "Sin permiso: inventory.crear")
    if data.project_id:
        from .projects import _project

        _project(db, data.project_id, p)  # mismo control de acceso que la ficha del proyecto
    return _create_and_maybe_confirm(db, p, data)


@router.post("/stock/reservations/{rid}/confirm")
def reservation_confirm(rid: int, p: Principal = Depends(require("inventory", "aprobar_reserva")), db: Session = Depends(get_db)):
    """AC25: confirmar es aprobar el retiro. AC06: atomico; si otra confirmacion se llevo la existencia, 409."""
    r = _reservation(db, rid, p)
    rsv.confirm(db, r, p.user.id, ip=p.ip)
    return _res_out(db, r)


class CancelIn(BaseModel):
    note: str | None = Field(None, max_length=300)


@router.post("/stock/reservations/{rid}/cancel")
def reservation_cancel(rid: int, data: CancelIn | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = _reservation(db, rid, p)
    # cancela quien aprueba reservas o quien la pidio
    if not (p.can("inventory", "aprobar_reserva") or (r.requested_by == p.user.id and _can_request(p))):
        raise HTTPException(403, "Sin permiso: inventory.aprobar_reserva")
    rsv.cancel(db, r, p.user.id, (data.note if data else None), ip=p.ip)
    db.commit()
    return _res_out(db, r)


@router.post("/stock/reservations/expire")
def reservations_expire(p: Principal = Depends(require("inventory", "aprobar_reserva")), db: Session = Depends(get_db)):
    """Lo mismo que hace el worker cada hora (util sin worker o para probar)."""
    from ..services.store_inventory import expire_holds

    web = expire_holds(db)  # primero las de pedidos web: dejan nota en el pedido
    return {"expired": web + rsv.expire_due(db)}


# ---------- Reservas desde el proyecto ----------
@router.get("/projects/{pid}/reservations")
def project_reservations(pid: int, p: Principal = Depends(require("projects", "ver")), db: Session = Depends(get_db)):
    from .projects import _project

    pr = _project(db, pid, p)
    rows = db.scalars(
        select(StockReservation).where(StockReservation.tenant_id == p.tenant.id, StockReservation.project_id == pr.id).order_by(StockReservation.id.desc())
    ).all()
    return [_res_out(db, r) for r in rows]


class ProjectReserveIn(BaseModel):
    warehouse_id: int | None = None
    reason: str | None = Field(None, max_length=500)
    expires_at: datetime | None = None


@router.post("/projects/{pid}/reserve", status_code=201)
def project_reserve(pid: int, data: ProjectReserveIn | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Botón "Reservar materiales": pide reserva de lo planificado que falta usar y que no esté ya pedido."""
    from .projects import _project

    if not _can_request(p):
        raise HTTPException(403, "Sin permiso: inventory.crear")
    data = data or ProjectReserveIn()
    pr = _project(db, pid, p)
    need: dict[int, Decimal] = {}
    for o in pr.orders:
        if o.trashed_at is not None or o.status in ("finalizada", "cancelada"):
            continue
        for m in o.materials:
            if m.product_id:
                need[m.product_id] = need.get(m.product_id, Decimal(0)) + max(d(m.planned) - d(m.quantity), Decimal(0))
    ya: dict[int, Decimal] = {}
    for r in db.scalars(
        select(StockReservation).where(StockReservation.tenant_id == p.tenant.id, StockReservation.project_id == pr.id, StockReservation.status.in_(rsv.OPEN))
    ):
        ya[r.product_id] = ya.get(r.product_id, Decimal(0)) + d(r.quantity) - d(r.consumed_quantity)
    created, skipped = [], []
    for pid_, qty in need.items():
        prod = db.get(Product, pid_)
        if not prod or prod.item_type != "producto":
            continue
        falta = qty - ya.get(pid_, Decimal(0))
        if falta <= 0:
            skipped.append({"product_id": pid_, "name": prod.name, "motivo": "ya reservado"})
            continue
        out = _create_and_maybe_confirm(
            db,
            p,
            ReservationIn(
                product_id=pid_,
                quantity=falta,
                warehouse_id=data.warehouse_id,
                project_id=pr.id,
                reason=data.reason or f"Material planificado de {pr.number}",
                expires_at=data.expires_at,
            ),
        )
        created.append(out)
    if not created and not skipped:
        raise HTTPException(409, "El proyecto no tiene materiales planificados pendientes de usar")
    return {"created": created, "skipped": skipped}


# ---------- Unidades serializadas ----------
class SerializedIn(BaseModel):
    serialized: bool


@router.patch("/products/{pid}/serialized")
def product_serialized(pid: int, data: SerializedIn, p: Principal = Depends(require("inventory", "editar")), db: Session = Depends(get_db)):
    """Opcional por producto: llevar el control por numero de serie."""
    prod = db.get(Product, pid)
    if not prod or prod.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    prod.serialized = data.serialized
    audit(db, p.tenant.id, p.user.id, "serialized", "product", prod.id, {"serialized": data.serialized}, ip=p.ip)
    db.commit()
    return {"id": prod.id, "serialized": prod.serialized}


class UnitsIn(BaseModel):
    product_id: int
    warehouse_id: int | None = None
    serials: list[str] = Field(min_length=1, max_length=500)
    condition: str = Field("nuevo", pattern="^(nuevo|usado|reacondicionado|danado)$")
    location: str | None = Field(None, max_length=160)
    notes: str | None = None
    # sumar tambien la entrada al inventario (si la compra no se registro ya por otra via)
    register_entry: bool = False
    unit_cost: Decimal | None = None
    reference: str | None = Field(None, max_length=120)


def _unit_out(db: Session, u: StockUnit) -> dict:
    prod = db.get(Product, u.product_id)
    w = db.get(Warehouse, u.warehouse_id) if u.warehouse_id else None
    pr = db.get(Project, u.project_id) if u.project_id else None
    return {
        "id": u.id,
        "product_id": u.product_id,
        "code": prod.code if prod else None,
        "name": prod.name if prod else None,
        "serial": u.serial,
        "warehouse_id": u.warehouse_id,
        "warehouse": w.name if w else None,
        "status": u.status,
        "project_id": u.project_id,
        "project": pr.number if pr else None,
        "condition": u.condition,
        "location": u.location,
        "owner": u.owner,
        "custodian": u.custodian,
        "customer_asset_id": u.customer_asset_id,
        "notes": u.notes,
        "received_at": u.received_at,
    }


@router.get("/stock/units")
def units(
    product_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    project_id: int | None = None,
    p: Principal = Depends(require("inventory", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(StockUnit).where(StockUnit.tenant_id == p.tenant.id)
    if product_id:
        stmt = stmt.where(StockUnit.product_id == product_id)
    if status:
        stmt = stmt.where(StockUnit.status == status)
    if project_id:
        stmt = stmt.where(StockUnit.project_id == project_id)
    if q:
        stmt = stmt.where(StockUnit.serial.ilike(f"%{q.strip()}%"))
    return [_unit_out(db, u) for u in db.scalars(stmt.order_by(StockUnit.id.desc()).limit(500))]


@router.post("/stock/units", status_code=201)
def units_create(data: UnitsIn, p: Principal = Depends(require("inventory", "crear")), db: Session = Depends(get_db)):
    """Alta de seriales al recibir. El serial es unico por empresa."""
    prod = db.get(Product, data.product_id)
    if not prod or prod.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    if prod.item_type != "producto":
        raise HTTPException(422, "Los servicios no llevan inventario")
    wh = db.get(Warehouse, data.warehouse_id) if data.warehouse_id else invsvc.default_warehouse(db, p.tenant.id)
    if not wh or wh.tenant_id != p.tenant.id:
        raise HTTPException(422, "No hay bodega configurada")
    serials = []
    for s in data.serials:
        s = s.strip()
        if s and s not in serials:
            serials.append(s[:80])
    if not serials:
        raise HTTPException(422, "Indicá al menos un número de serie")
    dup = db.scalars(select(StockUnit.serial).where(StockUnit.tenant_id == p.tenant.id, StockUnit.serial.in_(serials))).all()
    if dup:
        raise HTTPException(409, "Ya existen estas series: " + ", ".join(sorted(dup)[:10]))
    now = datetime.now(UTC)
    if not prod.serialized:
        prod.serialized = True  # dar de alta series marca el producto como serializado
    rows = [
        StockUnit(
            tenant_id=p.tenant.id,
            product_id=prod.id,
            serial=s,
            warehouse_id=wh.id,
            status="disponible",
            condition=data.condition,
            location=data.location,
            notes=data.notes,
            received_at=now,
            created_by=p.user.id,
        )
        for s in serials
    ]
    db.add_all(rows)
    if data.register_entry:
        invsvc.move(
            db,
            p.tenant.id,
            p.user.id,
            prod.id,
            wh.id,
            "entrada",
            Decimal(len(serials)),
            data.reference,
            "Alta de seriales",
            data.unit_cost,
            movement_type="compra",
            location=data.location,
        )
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "Alguna de las series ya existe") from e
    audit(db, p.tenant.id, p.user.id, "units", "product", prod.id, {"series": len(serials), "entrada": data.register_entry}, ip=p.ip)
    db.commit()
    return [_unit_out(db, u) for u in rows]


class UnitPatch(BaseModel):
    status: str | None = None
    warehouse_id: int | None = None
    project_id: int | None = None
    condition: str | None = Field(None, pattern="^(nuevo|usado|reacondicionado|danado)$")
    location: str | None = Field(None, max_length=160)
    notes: str | None = None
    # al instalar: datos del activo del cliente que se crea o liga
    asset_location: str | None = Field(None, max_length=200)
    warranty_until: date | None = None


@router.patch("/stock/units/{uid}")
def unit_update(uid: int, data: UnitPatch, p: Principal = Depends(require("inventory", "editar")), db: Session = Depends(get_db)):
    u = db.get(StockUnit, uid)
    if not u or u.tenant_id != p.tenant.id:
        raise HTTPException(404, "Unidad no encontrada")
    fields = data.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in UNIT_STATUSES:
        raise HTTPException(422, "Estado no válido")
    if fields.get("warehouse_id"):
        w = db.get(Warehouse, fields["warehouse_id"])
        if not w or w.tenant_id != p.tenant.id:
            raise HTTPException(404, "Inventario no encontrado")
    if fields.get("project_id"):
        pr = db.get(Project, fields["project_id"])
        if not pr or pr.tenant_id != p.tenant.id:
            raise HTTPException(404, "Proyecto no encontrado")
    if fields.get("status", u.status) == "instalado" and not u.customer_asset_id and not fields.get("project_id", u.project_id):
        raise HTTPException(422, "Para marcarla instalada indicá el proyecto")
    antes, antes_proyecto = u.status, u.project_id
    for k in ("status", "warehouse_id", "project_id", "condition", "location", "notes"):
        if k in fields:
            setattr(u, k, fields[k])
    if u.status == "instalado" and not u.customer_asset_id:
        _link_asset(db, p, u, data)
    # una serie entregada o instalada en un proyecto salio de bodega (y si vuelve, entra): se concilia el proyecto
    if antes != u.status or antes_proyecto != u.project_id:
        from ..services import project_stock

        for pid in {antes_proyecto, u.project_id} - {None}:
            pr = db.get(Project, pid)
            if pr and pr.tenant_id == p.tenant.id:
                project_stock.reconcile(db, pr, [u.product_id], p.user.id, reason=f"serie {u.serial}", warehouse_id=u.warehouse_id)
    audit(db, p.tenant.id, p.user.id, "unit", "stock_unit", u.id, {"de": antes, "a": u.status}, ip=p.ip)
    db.commit()
    return _unit_out(db, u)


def _link_asset(db: Session, p: Principal, u: StockUnit, data: UnitPatch) -> None:
    """Instalado en un proyecto -> activo del cliente (reusa el que ya tenga esa serie)."""
    if not u.project_id:
        raise HTTPException(422, "Para marcarla instalada indicá el proyecto")
    pr = db.get(Project, u.project_id)
    customer_id = pr.end_customer_id or pr.customer_id
    if not customer_id:
        raise HTTPException(422, "El proyecto no tiene cliente; no se puede registrar el equipo instalado")
    a = db.scalar(
        select(CustomerAsset).where(
            CustomerAsset.tenant_id == p.tenant.id, CustomerAsset.customer_id == customer_id, CustomerAsset.serial == u.serial, CustomerAsset.trashed_at.is_(None)
        )
    )
    if not a:
        prod = db.get(Product, u.product_id)
        a = CustomerAsset(
            tenant_id=p.tenant.id,
            customer_id=customer_id,
            project_id=pr.id,
            product_id=u.product_id,
            name=prod.name if prod else "Equipo",
            model=prod.model if prod else None,
            serial=u.serial,
            location=data.asset_location,
            installed_at=date.today(),
            warranty_until=data.warranty_until,
            supplier_id=prod.supplier_id if prod else None,
            status="activo",
            photos=[],
        )
        db.add(a)
        db.flush()
        audit(db, p.tenant.id, p.user.id, "create", "asset", a.id, {"desde": "serie", "serial": u.serial}, ip=p.ip)
    u.customer_asset_id = a.id


# ---------- Salud del inventario ----------
@router.get("/stock/health")
def stock_health(days: int = Query(180, ge=7, le=730), p: Principal = Depends(require("inventory", "ver")), db: Session = Depends(get_db)):
    """Descuadres entre los documentos (facturas, proyectos, pedidos web) y el kardex, y existencias negativas."""
    from ..services import inventory_health

    return inventory_health.check(db, p.tenant.id, days)


class HealthFixIn(BaseModel):
    kind: str = Field(pattern="^(factura|proyecto|pedido|garantia|negativo|pedido_adelantado)$")
    ref_id: int
    # existencia negativa: bodega, motivo del ajuste (obligatorio, queda en el kardex) y forzar si hay compra sin recibir
    warehouse_id: int | None = None
    motivo: str | None = Field(None, max_length=240)
    force: bool = False


@router.post("/stock/health/fix")
def stock_health_fix(data: HealthFixIn, p: Principal = Depends(require("inventory", "editar")), db: Session = Depends(get_db)):
    """Corrige un descuadre con la conciliacion de su documento: escribe solo la diferencia, con documento y usuario.
    Existencia negativa: ajuste documentado (motivo obligatorio) que deja la bodega en cero."""
    from ..services import inventory_health

    n = inventory_health.fix(db, p.tenant, data.kind, data.ref_id, p.user.id, warehouse_id=data.warehouse_id, motivo=data.motivo, force=data.force)
    info = {"documento": data.kind, "movimientos": n}
    if data.kind == "negativo":
        info.update(bodega=data.warehouse_id, motivo=data.motivo, forzado=data.force)
    audit(db, p.tenant.id, p.user.id, "health_fix", "stock", data.ref_id, info, ip=p.ip)
    db.commit()
    return {"movimientos": n}


CR = ZoneInfo("America/Costa_Rica")


# ---------- Libro de movimientos / kardex (con los datos enriquecidos) ----------
KIND_LABEL = {
    "entrada": "Entrada",
    "salida": "Salida",
    "venta": "Venta",
    "devolucion": "Devolución",
    "ajuste": "Ajuste",
    "transferencia_out": "Traslado (sale)",
    "transferencia_in": "Traslado (entra)",
    "garantia": "Garantía",
}


@router.get("/stock/movements")
def movements(
    product_id: int | None = None,
    project_id: int | None = None,
    warehouse_id: int | None = None,
    user_id: int | None = None,
    kind: str | None = Query(None, max_length=30),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=80),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require("inventory", "ver")),
    db: Session = Depends(get_db),
):
    """Kardex: cada entrada y salida con quien la hizo, cuando y de que documento viene (factura, proyecto, orden,
    pedido web, compra). Filtrado por un producto devuelve ademas el saldo despues de cada movimiento."""
    from ..models import Invoice, Order

    stmt = select(StockMovement).where(StockMovement.tenant_id == p.tenant.id)
    if product_id:
        stmt = stmt.where(StockMovement.product_id == product_id)
    if project_id:
        stmt = stmt.where(StockMovement.project_id == project_id)
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    if user_id:
        stmt = stmt.where(StockMovement.created_by == user_id)
    if kind:
        stmt = stmt.where(StockMovement.kind == kind)
    if date_from:
        stmt = stmt.where(StockMovement.at >= datetime.combine(date_from, time.min, tzinfo=CR))
    if date_to:
        stmt = stmt.where(StockMovement.at <= datetime.combine(date_to, time.max, tzinfo=CR))
    if q and q.strip():
        like = f"%{q.strip()}%"
        ids = select(Product.id).where(Product.tenant_id == p.tenant.id, or_(Product.code.ilike(like), Product.name.ilike(like)))
        stmt = stmt.where(or_(StockMovement.product_id.in_(ids), StockMovement.reference.ilike(like), StockMovement.serial.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(StockMovement.at.desc(), StockMovement.id.desc()).offset(offset).limit(limit)).all()
    prods = {x.id: x for x in db.scalars(select(Product).where(Product.id.in_({m.product_id for m in rows})))} if rows else {}
    whs = {w.id: w.name for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == p.tenant.id))}
    users = {u.id: (u.full_name or u.email) for u in db.scalars(select(User).where(User.id.in_({m.created_by for m in rows if m.created_by})))} if rows else {}
    invs = {i.id: i.number for i in db.scalars(select(Invoice).where(Invoice.id.in_({m.invoice_id for m in rows if m.invoice_id})))} if rows else {}
    projs = {x.id: x.number for x in db.scalars(select(Project).where(Project.id.in_({m.project_id for m in rows if m.project_id})))} if rows else {}
    orders = {o.id: o.number for o in db.scalars(select(Order).where(Order.id.in_({m.order_id for m in rows if m.order_id})))} if rows else {}
    # saldo despues de cada movimiento (solo con un producto): el total de hoy menos lo posterior
    saldo = None
    if product_id and rows:
        base = select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.tenant_id == p.tenant.id, StockMovement.product_id == product_id)
        if warehouse_id:
            base = base.where(StockMovement.warehouse_id == warehouse_id)
        newest = rows[0]
        later = base.where(or_(StockMovement.at > newest.at, (StockMovement.at == newest.at) & (StockMovement.id > newest.id)))
        saldo = Decimal(str(db.scalar(base) or 0)) - Decimal(str(db.scalar(later) or 0))
    out = []
    for m in rows:
        pr = prods.get(m.product_id)
        row = {
            "id": m.id,
            "at": m.at,
            "product_id": m.product_id,
            "code": pr.code if pr else None,
            "name": pr.name if pr else None,
            "warehouse_id": m.warehouse_id,
            "warehouse": whs.get(m.warehouse_id),
            "kind": m.kind,
            "kind_label": KIND_LABEL.get(m.kind, m.kind),
            "movement_type": m.movement_type,
            "quantity": m.quantity,
            "balance": saldo,
            "reference": m.reference,
            "note": m.note,
            "user_id": m.created_by,
            "user": users.get(m.created_by) if m.created_by else "Sistema",
            "invoice_id": m.invoice_id,
            "invoice_number": invs.get(m.invoice_id),
            "project_id": m.project_id,
            "project_number": projs.get(m.project_id),
            "order_id": m.order_id,
            "order_number": orders.get(m.order_id),
            "serial": m.serial,
            "voucher_media": m.voucher_media,
            "owner": m.owner,
            "custodian": m.custodian,
            "location": m.location,
        }
        if saldo is not None:
            saldo -= Decimal(str(m.quantity))
        if p.sees_costs:
            row["unit_cost"] = m.unit_cost
            row["transfer_price"] = m.transfer_price
        out.append(row)
    return {"total": total, "items": out}
