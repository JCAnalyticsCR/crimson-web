"""Clientes, productos, impuestos y categorias. Listados con busqueda + cursor ("Mas resultados")."""

from __future__ import annotations

import logging
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Category, Customer, Product, ProductTax, Supplier, SupplierItem, Tax, Warehouse
from ..schemas.crm import (
    CategoryIn,
    CategoryOut,
    CustomerIn,
    CustomerOut,
    Page,
    ProductIn,
    ProductOut,
    TaxIn,
    TaxOut,
)
from ..services import cabys as cabys_svc
from ..services import pricing
from ..services import store_showcase as _sv_cache
from ..services import supplier_catalog as supcat
from ..services.documents import audit

router = APIRouter(tags=["catalogo"])
log = logging.getLogger("crimson.catalogo")

# Campos de costo del producto: solo los escribe quien los ve (catalog.costos)
COST_FIELDS = {"cost", "cost_currency", "margin_pct"}


def _page(db: Session, stmt, cursor: int | None, limit: int, model, out):
    if cursor:
        stmt = stmt.where(model.id < cursor)
    rows = db.scalars(stmt.order_by(model.id.desc()).limit(limit + 1)).all()
    nxt = rows[limit].id if len(rows) > limit else None
    return Page(items=[out(r) for r in rows[:limit]], next_cursor=nxt)


def _own(db: Session, model, id_: int, tenant_id: int):
    obj = db.get(model, id_)
    if not obj or obj.tenant_id != tenant_id:
        raise HTTPException(404, f"{model.__name__} no encontrado")
    return obj


# ---------- Clientes ----------
@router.get("/customers", response_model=Page)
def customers(
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    p: Principal = Depends(require("crm", "ver")),
    db: Session = Depends(get_db),
):
    from ..services import cartera

    # cartera con dueno (ventas etapa 2): el vendedor solo ve sus clientes
    stmt = select(Customer).where(Customer.tenant_id == p.tenant.id, Customer.active, cartera.customer_scope(p))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.id_number.ilike(like), Customer.email.ilike(like)))
    page = _page(db, stmt, cursor, limit, Customer, lambda c: _customer_out(db, c))
    txt = (q or "").strip()
    if txt and not cursor and not p.sees_all_customers:
        # un cliente SIN dueno solo se encuentra por su cedula o su correo exactos: sale para tomarlo (claim)
        ya = {x.id for x in page.items}
        cand = select(Customer).where(Customer.tenant_id == p.tenant.id, Customer.active, Customer.account_owner_id.is_(None))
        cand = cand.where(or_(func.lower(Customer.email) == txt.lower(), Customer.id_number.ilike(f"%{txt[-4:]}%")))
        for c in db.scalars(cand.limit(50)):
            if c.id not in ya and cartera.exact_match(c, txt):
                out = _customer_out(db, c)
                out.claimable = True
                page.items.insert(0, out)
    return page


def _customer_out(db: Session, c: Customer) -> CustomerOut:
    from ..services.cartera import owner_name

    out = CustomerOut.model_validate(c)
    out.account_owner_name = owner_name(db, c.account_owner_id)
    return out


def _visible_customer(db: Session, p: Principal, cid: int) -> Customer:
    """El cliente si quien mira lo puede ver (cartera con dueno); si no, 404 (lo ajeno no existe)."""
    from ..services import cartera

    c = _own(db, Customer, cid, p.tenant.id)
    if not cartera.can_see(db, p, c):
        raise HTTPException(404, "Customer no encontrado")
    return c


def can_pause_collections(p: Principal) -> bool:
    """Pausar la cobranza de un cliente apaga los recordatorios de TODAS sus facturas (tambien las de otro vendedor y
    el aviso de atraso de la mensualidad de un aliado): es de quien cobra toda la cartera, no de crm.editar. La pausa
    de una sola factura sigue en PUT /invoices/{id}/reminders (sales.enviar sobre las propias)."""
    return p.can("settings", "configurar") or (p.can("sales", "enviar") and p.can("sales", "ver_todo"))


def _reminders_guard(p: Principal) -> None:
    if not can_pause_collections(p):
        raise HTTPException(403, "Sin permiso: pausar la cobranza de un cliente es de quien envía y ve toda la cartera (sales.enviar y sales.ver_todo)")


def _duplicate_customer(db: Session, tenant_id: int, id_number: str | None, email: str | None) -> Customer | None:
    """Cliente activo de la empresa con la misma cedula (comparada sin guiones, espacios ni puntos) o el mismo correo.
    Lo usan el alta de clientes y la importacion de quien no edita clientes existentes (pedido 23)."""
    import re

    ced = re.sub(r"[^0-9A-Za-z]", "", id_number or "").upper()
    correo = (email or "").strip().lower()
    if not ced and not correo:
        return None
    conds = []
    if correo:
        conds.append(func.lower(func.trim(Customer.email)) == correo)
    if ced:
        # normalizada de los dos lados: "3-101-123456", "3101123456" y "3 101 123456" son la misma cedula (fisica,
        # juridica o DIMEX), se haya guardado como se haya guardado
        guardada = func.upper(func.replace(func.replace(func.replace(func.trim(Customer.id_number), "-", ""), " ", ""), ".", ""))
        conds.append(guardada == ced)
    return db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.active, or_(*conds)).order_by(Customer.id).limit(1))


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(data: CustomerIn, p: Principal = Depends(require("crm", "crear")), db: Session = Depends(get_db)):
    from ..services.documents import audit

    if data.no_reminders:
        _reminders_guard(p)
    from ..services import cartera

    if not p.sees_all_customers:
        # cartera con dueno: el mismo cliente (cedula, correo o telefono) de otro vendedor no se duplica
        otro = cartera.find_duplicate(db, p.tenant.id, data.id_number, data.email, data.phone or data.whatsapp)
        if otro is not None and otro.account_owner_id and otro.account_owner_id != p.user.id:
            return JSONResponse(status_code=409, content={"detail": cartera.busy_message(db, otro)})
        if otro is not None and otro.account_owner_id is None and not (cartera.norm_id(data.id_number) or (data.email or "").strip()):
            # coincide solo por telefono con un cliente sin dueno: se toma buscandolo por cedula o correo exactos
            return JSONResponse(status_code=409, content={"detail": "Ya existe un cliente con ese teléfono. Búsquelo por su cédula o su correo exactos para tomarlo."})
    if not p.can("crm", "editar"):
        dup = _duplicate_customer(db, p.tenant.id, data.id_number, data.email)
        if dup is not None:
            # quien no edita clientes (el vendedor, pedido 23) no arma una segunda ficha con la misma cedula o el mismo
            # correo y su propio correo: la factura y los enlaces de pago le llegarian a el. Se usa la ficha existente.
            return JSONResponse(
                status_code=409,
                content={"detail": f"Ya existe el cliente «{dup.name}» con esa cédula o ese correo: use esa ficha", "existing": {"id": dup.id, "name": dup.name}},
            )
    c = Customer(tenant_id=p.tenant.id, **data.model_dump())
    if not p.sees_all_customers:
        c.account_owner_id = p.user.id  # el cliente que crea un vendedor queda en su cartera
    db.add(c)
    db.flush()
    if c.no_reminders:
        audit(db, p.tenant.id, p.user.id, "reminders", "customer", c.id, {"antes": False, "despues": True}, ip=p.ip)
    if c.account_owner_id:
        audit(db, p.tenant.id, p.user.id, "account_owner", "customer", c.id, before={"account_owner_id": None}, after={"account_owner_id": c.account_owner_id}, reason="Lo creó su vendedor", ip=p.ip)
    db.commit()
    db.refresh(c)
    return _customer_out(db, c)


@router.get("/customers/{cid}", response_model=CustomerOut)
def get_customer(cid: int, p: Principal = Depends(require("crm", "ver")), db: Session = Depends(get_db)):
    c = _visible_customer(db, p, cid)
    out = _customer_out(db, c)
    if p.can("aliados", "ver"):
        from ..models import AllyCompany

        a = db.scalar(select(AllyCompany).where(AllyCompany.tenant_id == p.tenant.id, AllyCompany.customer_id == c.id).limit(1))
        out.ally_company = {"id": a.id, "name": a.name} if a else None
    return out


@router.put("/customers/{cid}", response_model=CustomerOut)
def update_customer(cid: int, data: CustomerIn, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    from ..services.documents import audit

    c = _visible_customer(db, p, cid)
    cambios = data.model_dump(exclude_unset=True)  # lo que no se envia no se borra
    pausa = "no_reminders" in cambios and bool(cambios["no_reminders"]) != bool(c.no_reminders)
    if pausa:
        _reminders_guard(p)  # el formulario completo reenvia el valor de siempre: solo se exige si cambia
    for k, v in cambios.items():
        setattr(c, k, v)
    if pausa:
        audit(db, p.tenant.id, p.user.id, "reminders", "customer", c.id, {"antes": not c.no_reminders, "despues": bool(c.no_reminders)}, ip=p.ip)
    db.commit()
    db.refresh(c)
    return _customer_out(db, c)


# ---------- Cartera con dueno (ventas etapa 2) ----------
class OwnerIn(BaseModel):
    owner_id: int | None = None  # None = sin vendedor
    reason: str | None = None


@router.put("/customers/{cid}/owner", response_model=CustomerOut)
def set_customer_owner(cid: int, data: OwnerIn, p: Principal = Depends(require("crm", "cartera")), db: Session = Depends(get_db)):
    """El admin o el jefe de ventas cambian el vendedor de la cuenta: queda en la bitacora y se le avisa al nuevo."""
    from ..services import cartera

    c = _own(db, Customer, cid, p.tenant.id)
    cartera.set_owner(db, p, c, data.owner_id, data.reason)
    db.commit()
    db.refresh(c)
    return _customer_out(db, c)


class ClaimIn(BaseModel):
    query: str  # la cedula o el correo exactos con que lo encontro


@router.post("/customers/{cid}/claim", response_model=CustomerOut)
def claim_customer(cid: int, data: ClaimIn, p: Principal = Depends(require("crm", "ver")), db: Session = Depends(get_db)):
    """Un vendedor toma un cliente SIN dueno que encontro por su cedula o su correo exactos."""
    from ..services import cartera
    from ..services.ownership import eligible_seller

    c = _own(db, Customer, cid, p.tenant.id)
    if not c.active or not cartera.exact_match(c, data.query):
        raise HTTPException(404, "Customer no encontrado")
    if c.account_owner_id == p.user.id:
        return _customer_out(db, c)
    if c.account_owner_id:
        raise HTTPException(409, cartera.busy_message(db, c))
    if not p.can("sales", "crear") or not eligible_seller(db, p.tenant.id, p.user.id):
        raise HTTPException(403, "Solo un vendedor toma clientes para su cartera")
    cartera.ensure_owner(db, p.tenant.id, c, p.user.id, p.user.id, "Lo tomó buscando su cédula o correo", ip=p.ip)
    db.commit()
    db.refresh(c)
    return _customer_out(db, c)


# ---------- Impuestos ----------
@router.get("/taxes", response_model=list[TaxOut])
def taxes(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return db.scalars(select(Tax).where(Tax.tenant_id == p.tenant.id, Tax.active).order_by(Tax.rate.desc())).all()


@router.post("/taxes", response_model=TaxOut, status_code=201)
def create_tax(data: TaxIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    t = Tax(tenant_id=p.tenant.id, **data.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ---------- Categorias ----------
@router.get("/categories", response_model=list[CategoryOut])
def categories(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return db.scalars(select(Category).where(Category.tenant_id == p.tenant.id).order_by(Category.name)).all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(data: CategoryIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    c = Category(tenant_id=p.tenant.id, **data.model_dump())
    db.add(c)
    _sv_cache.bump_catalog_version(db, p.tenant)
    db.commit()
    db.refresh(c)
    return c


# ---------- Productos ----------
def _product_out(pr: Product, p: Principal | None = None) -> ProductOut:
    o = ProductOut.model_validate(pr)
    o.tax_ids = [t.tax_id for t in pr.taxes]
    o.tax_rate = float(pr.taxes[0].tax.rate) if pr.taxes else None
    if p is not None and not p.sees_prices:  # el tecnico ve el catalogo sin plata
        o.price = 0
    if p is not None and not p.sees_costs:  # la vendedora cotiza con el precio, no con el costo
        o.cost, o.margin_pct, o.margin_hold = None, None, None
    return o


def _with_costs(db: Session, p: Principal, prods: list[Product]) -> list[ProductOut]:
    """Lista/ficha con el origen del costo (proveedor vinculado o propio, con fecha) y el margen real sobre la
    venta. Solo para quien ve costos: a los demas no les llega ni el campo."""
    info = supcat.cost_info(db, p.tenant, prods) if p.sees_costs and prods else {}
    if p.sees_costs and prods:
        from ..services import ally_pricing
        from ..services.fx import rate_or_none

        fx = rate_or_none(db, "USD")  # sin tasa la lista sigue; el precio de aliado en otra moneda queda "a consultar"
        fijos = ally_pricing.fixed_prices(db, p.tenant.id, [x.id for x in prods], None)
    out = []
    for pr in prods:
        o = _product_out(pr, p)
        for k, v in (info.get(pr.id) or {}).items():
            setattr(o, k, v)
        if p.sees_costs:
            precio, fuente, _estado = ally_pricing.ally_price(db, p.tenant, pr, None, fx=fx, fixed=fijos)
            o.ally_price, o.ally_price_source = (float(precio) if precio is not None else None), fuente
        out.append(o)
    return out


@router.get("/products", response_model=Page)
def products(
    q: str | None = None,
    item_type: str | None = None,
    category_id: int | None = None,
    brand: str | None = None,
    supplier_id: int | None = None,
    web: bool | None = None,
    sin_cabys: bool = False,
    warehouse_id: int | None = None,
    proveedor: str | None = Query(None, pattern="^(con|sin|vinculado|sin_vincular)$"),
    ids: str | None = Query(None, max_length=1000),  # "1,2,3": las fotos de las lineas de un documento en un solo viaje
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    p: Principal = Depends(require("catalog", "ver")),
    db: Session = Depends(get_db),
):
    """El buscador por texto no alcanza con 200 productos del proveedor: se filtra por tipo, categoria,
    marca, proveedor, si esta en la tienda y si le falta el CABYS (lo que bloquea facturar).
    proveedor=con|sin: con proveedor = vinculado a un item de un catalogo de proveedor o con proveedor asignado.
    proveedor=vinculado: con item del catalogo de proveedores (su existencia cuenta); sin_vincular: tiene proveedor
    asignado pero ningun item vinculado (su existencia del proveedor NO cuenta: hay que vincularlo).
    Cada fila trae `availability` (services/availability, audiencia interno): propio, proveedor y fecha."""
    stmt = select(Product).where(Product.tenant_id == p.tenant.id, Product.active)
    if ids:
        wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:100]
        stmt = stmt.where(Product.id.in_(wanted or [0]))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Product.name.ilike(like), Product.code.ilike(like), Product.cabys_code.ilike(like), Product.model.ilike(like), Product.supplier_sku.ilike(like))
        )
    if item_type:
        stmt = stmt.where(Product.item_type == item_type)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if supplier_id:
        stmt = stmt.where(Product.supplier_id == supplier_id)
    if web is not None:
        stmt = stmt.where(Product.show_on_web.is_(web))
    if sin_cabys:
        stmt = stmt.where(or_(Product.cabys_code.is_(None), Product.cabys_code == ""))
    if warehouse_id:
        stmt = stmt.where(Product.warehouse_id == warehouse_id)
    if proveedor:
        linked = select(SupplierItem.id).where(SupplierItem.product_id == Product.id).exists()
        con = or_(Product.supplier_id.is_not(None), linked)
        stmt = stmt.where(
            {"con": con, "sin": ~con, "vinculado": linked, "sin_vincular": Product.supplier_id.is_not(None) & ~linked}[proveedor]
        )
    page = _page(db, stmt, cursor, limit, Product, lambda pr: pr)
    prods = page.items
    page.items = _with_costs(db, p, prods)
    _with_availability(db, p, prods, page.items)
    return page


def _with_availability(db: Session, p: Principal, prods: list[Product], outs: list[ProductOut]) -> None:
    """Disponibilidad de la funcion unica en cada fila (propio / proveedor / actualizado). Nunca lleva costos."""
    from ..services import availability as avsvc

    av = avsvc.availability_many(db, p.tenant, prods, audience="interno")
    for pr, o in zip(prods, outs, strict=True):
        o.availability = av.get(pr.id)


@router.get("/products/meta/filters")
def product_filters(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """Las opciones que existen de verdad en el catalogo, no una lista fija."""
    marcas = db.scalars(
        select(Product.brand).where(Product.tenant_id == p.tenant.id, Product.active, Product.brand.is_not(None)).distinct().order_by(Product.brand)
    ).all()
    sin_cabys = db.scalar(
        select(func.count())
        .select_from(Product)
        .where(Product.tenant_id == p.tenant.id, Product.active, or_(Product.cabys_code.is_(None), Product.cabys_code == ""))
    )
    return {
        "brands": [b for b in marcas if b],
        "categories": [{"id": c.id, "name": c.name} for c in db.scalars(select(Category).where(Category.tenant_id == p.tenant.id).order_by(Category.name))],
        "suppliers": [
            {"id": s.id, "name": s.name}
            for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id, Supplier.archived_at.is_(None), Supplier.trashed_at.is_(None)).order_by(Supplier.name))
        ],
        "sin_cabys": sin_cabys,
        # la bodega de origen se muestra en el catalogo aunque el rol no tenga inventario.ver
        "warehouses": [
            {"id": w.id, "name": w.name}
            for w in db.scalars(
                select(Warehouse).where(Warehouse.tenant_id == p.tenant.id, Warehouse.active, Warehouse.kind == "venta").order_by(Warehouse.is_default.desc(), Warehouse.id)
            )
        ],
    }


@router.get("/cabys")
def cabys(
    q: str | None = Query(None, max_length=120),
    codigo: str | None = Query(None, max_length=20),
    top: int = Query(20, ge=1, le=50),
    _: Principal = Depends(require("catalog", "ver")),
):
    """Proxy al buscador CABYS de Hacienda (por descripcion o por codigo de 13 digitos)."""
    if not codigo and len((q or "").strip()) < 3:
        raise HTTPException(422, "Escriba al menos 3 letras para buscar en el CABYS")
    try:
        return cabys_svc.search(q=q, codigo=codigo, top=top)
    except cabys_svc.CabysUnavailable as e:
        raise HTTPException(502, "El buscador CABYS de Hacienda no respondió. Intentá de nuevo o escribí el código de 13 dígitos.") from e


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(data: ProductIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == p.tenant.id, Product.code == data.code)):
        raise HTTPException(409, "Ya existe un producto con ese codigo")
    if data.warehouse_id:
        _own(db, Warehouse, data.warehouse_id, p.tenant.id)
    fields = data.model_dump(exclude={"tax_ids"})
    if not p.sees_costs:
        # quien no ve costos tampoco los escribe: el producto nace sin costo y el admin lo carga o lo vincula
        for k in COST_FIELDS:
            fields.pop(k, None)
    if not p.can("catalog", "publicar"):
        fields["show_on_web"] = False  # publicar en la tienda es solo del administrador (pedido 21/10)
    pr = Product(tenant_id=p.tenant.id, **fields)
    for tid in data.tax_ids:
        _own(db, Tax, tid, p.tenant.id)
        pr.taxes.append(ProductTax(tax_id=tid))
    db.add(pr)
    _sv_cache.bump_catalog_version(db, p.tenant)
    db.commit()
    db.refresh(pr)
    return _product_out(pr, p)


@router.get("/products/{pid}", response_model=ProductOut)
def get_product(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    pr = _own(db, Product, pid, p.tenant.id)
    out = _with_costs(db, p, [pr])
    _with_availability(db, p, [pr], out)
    return out[0]


class WebIn(BaseModel):
    show_on_web: bool


@router.patch("/products/{pid}/web", response_model=ProductOut)
def toggle_web(pid: int, data: WebIn, p: Principal = Depends(require("catalog", "publicar")), db: Session = Depends(get_db)):
    """Publica o quita el producto de la tienda desde la lista, sin abrir la ficha. Solo el administrador (catalog.publicar
    no lo tiene ningun otro rol): ventas e inventario editan la ficha pero no deciden que se vende en linea."""
    pr = _own(db, Product, pid, p.tenant.id)
    antes = bool(pr.show_on_web)
    pr.show_on_web = data.show_on_web
    _margin_recheck(db, p, pr)
    _sv_cache.bump_catalog_version(db, p.tenant)
    if antes != bool(data.show_on_web):
        audit(
            db, p.tenant.id, p.user.id, "publish" if data.show_on_web else "unpublish", "product", pr.id, {"code": pr.code}, ip=p.ip,
            before={"show_on_web": antes}, after={"show_on_web": bool(data.show_on_web)},
        )  # fmt: skip
    db.commit()
    db.refresh(pr)
    return _product_out(pr, p)


class PriceIn(BaseModel):
    price: Decimal = Field(ge=0, le=Decimal("999999999"))


@router.patch("/products/{pid}/price", response_model=ProductOut)
def set_price(pid: int, data: PriceIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Precio de venta sin IVA desde la lista (sin abrir la ficha). Mismo permiso que editar la ficha (catalog.editar) y
    hay que ver el precio (catalog.precios). El precio que pone una persona nunca lo toca la sincronizacion de Eurocomp
    (apply_list y auto_catalog solo mueven costo y existencias). Queda en la bitacora con el antes y el despues."""
    if not p.sees_prices:
        raise HTTPException(403, "Sin permiso: catalog.precios")
    pr = _own(db, Product, pid, p.tenant.id)
    antes = pr.price
    pr.price = data.price
    if Decimal(str(antes or 0)) != data.price:
        _margin_recheck(db, p, pr)
        _sv_cache.bump_catalog_version(db, p.tenant)
        audit(
            db, p.tenant.id, p.user.id, "update", "product", pr.id, {"code": pr.code, "campo": "precio"}, ip=p.ip,
            before={"price": str(antes) if antes is not None else None}, after={"price": str(data.price)},
        )  # fmt: skip
    db.commit()
    db.refresh(pr)
    out = _with_costs(db, p, [pr])
    _with_availability(db, p, [pr], out)
    return out[0]


class PriceAdjustIn(BaseModel):
    """Ajuste de precios en bloque: +/- un porcentaje o un margen objetivo sobre la venta, para lo filtrado o marcado."""

    mode: str = Field(pattern="^(pct|margen)$")
    value: Decimal = Field(ge=Decimal("-90"), le=Decimal("500"))
    ids: list[int] | None = Field(None, max_length=500)
    q: str | None = Field(None, max_length=120)
    category_id: int | None = None
    brand: str | None = Field(None, max_length=60)
    supplier_id: int | None = None
    web: bool | None = None
    confirm: bool = False


ADJUST_MAX = 500


def _round_price(v: Decimal, currency: str) -> Decimal:
    if currency == "CRC":
        return (v / pricing.ROUND_TO).quantize(Decimal(1), rounding=ROUND_CEILING) * pricing.ROUND_TO
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _adjust_products(db: Session, p: Principal, data: PriceAdjustIn) -> list[Product]:
    stmt = select(Product).where(Product.tenant_id == p.tenant.id, Product.active)
    if data.ids:
        stmt = stmt.where(Product.id.in_(data.ids))
    if data.q:
        like = f"%{data.q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.model.ilike(like), Product.supplier_sku.ilike(like)))
    if data.category_id:
        cats = list(db.scalars(select(Category).where(Category.tenant_id == p.tenant.id)))
        keep, todo = {data.category_id}, [data.category_id]
        while todo:  # la categoria y sus hijas: "+10 % a Electrodomesticos" incluye refrigeradoras y lavadoras
            cur = todo.pop()
            for c in cats:
                if c.parent_id == cur and c.id not in keep:
                    keep.add(c.id)
                    todo.append(c.id)
        stmt = stmt.where(Product.category_id.in_(keep))
    if data.brand:
        stmt = stmt.where(Product.brand == data.brand)
    if data.supplier_id:
        stmt = stmt.where(Product.supplier_id == data.supplier_id)
    if data.web is not None:
        stmt = stmt.where(Product.show_on_web.is_(data.web))
    return list(db.scalars(stmt.order_by(Product.name).limit(ADJUST_MAX + 1)))


@router.post("/products/price-adjust")
def price_adjust(data: PriceAdjustIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Vista previa (confirm=false) y aplicacion (confirm=true) del ajuste en bloque. Solo quien ve costos (el admin):
    la vista previa muestra costo y margen. "pct": precio x (1 + valor/100). "margen": precio = costo / (1 - margen),
    redondeado como la politica de precios (centena hacia arriba en colones); sin costo no se toca. Una sola entrada
    en la bitacora con cada producto, su precio anterior y el nuevo."""
    from ..services.fx import FxMissing

    if not p.sees_costs:
        raise HTTPException(403, "Sin permiso: catalog.costos")
    if data.mode == "margen" and not (Decimal(0) < data.value < Decimal(95)):
        raise HTTPException(422, "El margen objetivo va de 0 % a 95 %")
    prods = _adjust_products(db, p, data)
    if not prods:
        raise HTTPException(422, "Ningún producto coincide con el filtro")
    if len(prods) > ADJUST_MAX:
        raise HTTPException(422, f"El filtro abarca más de {ADJUST_MAX} productos: acótelo (categoría, marca o búsqueda)")
    fx = supcat.usd_rate(db)
    rows, cambios = [], []
    for pr in prods:
        antes = Decimal(str(pr.price or 0))
        nuevo, motivo = None, None
        if data.mode == "pct":
            if antes <= 0:
                motivo = "Sin precio: use el margen objetivo o póngale precio"
            else:
                nuevo = _round_price(antes * (1 + data.value / 100), pr.currency or "CRC")
        elif pr.cost is None or Decimal(str(pr.cost)) <= 0:
            motivo = "Sin costo: no se puede calcular el margen"
        else:
            try:
                nuevo = pricing.sale_price(db, Decimal(str(pr.cost)), pr.cost_currency or "USD", pr.currency or "CRC", data.value, fx)
            except FxMissing:
                motivo = "Sin tipo de cambio del día"
        if nuevo is not None and nuevo == antes:
            motivo, nuevo = "Ya tiene ese precio", None
        cost_crc = supcat.to_crc(pr.cost, pr.cost_currency, fx) if pr.cost is not None else None
        m_before, m_after = (
            pricing.margin_of(v_crc, cost_crc) if (v_crc := supcat.to_crc(v, pr.currency, fx) if v is not None else None) and cost_crc else None
            for v in (antes, nuevo)
        )
        rows.append({
            "id": pr.id, "code": pr.code, "name": pr.name, "currency": pr.currency, "price_before": antes, "price_after": nuevo,
            "cost_crc": cost_crc, "margin_before": m_before, "margin_after": m_after, "skip": motivo,
        })  # fmt: skip
        if nuevo is not None:
            cambios.append((pr, antes, nuevo))
    if data.confirm and cambios:
        for pr, _a, nuevo in cambios:
            pr.price = nuevo
            _margin_recheck(db, p, pr)
        _sv_cache.bump_catalog_version(db, p.tenant)
        filtro = data.model_dump(exclude={"confirm", "mode", "value"}, exclude_none=True)
        audit(
            db, p.tenant.id, p.user.id, "price_adjust", "product", None,
            {"modo": data.mode, "valor": str(data.value), "filtro": filtro, "n": len(cambios),
             "cambios": [(pr.id, pr.code, str(a), str(n)) for pr, a, n in cambios]},
            ip=p.ip,
        )  # fmt: skip
        db.commit()
    return {"items": rows, "changes": len(cambios), "skipped": len(rows) - len(cambios), "applied": bool(data.confirm and cambios)}


def _margin_recheck(db: Session, p: Principal, pr: Product) -> None:
    """Producto en Precios por revisar (guardian de margen, plan 1.12) editado a mano: si su precio ya cumple el margen
    minimo vuelve a la venta ya. Despublicarlo y volverlo a publicar NO lo libera (sigue fuera si sigue bajo el minimo).
    Guardar el producto nunca falla por el guardian."""
    from ..services import margin_guard

    try:
        margin_guard.recheck(db, p.tenant, pr)
    except Exception:  # noqa: BLE001
        log.exception("guardian de margen al editar el producto %s", pr.id)


@router.get("/products/{pid}/availability")
def availability(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """La funcion unica (services/availability): propio libre/reservado por bodega de venta (las de garantias no
    cuentan), proveedor con nombre y fecha, y lo que ve el cliente en la tienda (label_publico)."""
    from ..services import availability as avsvc

    pr = _own(db, Product, pid, p.tenant.id)
    row = avsvc.one(db, p.tenant, pr, audience="interno", by_warehouse=True)
    pub = avsvc.one(db, p.tenant, pr, audience="publico")
    return {
        **row,
        "own_stock": row.get("own_on_hand"),
        "min_stock": pr.min_stock,
        "supplier_stock": row.get("supplier_qty"),
        "label_publico": pub.get("label"),
        "state_publico": pub.get("state"),
    }


def qty_label(v) -> str:
    """Cantidad para leer en es-CR: Decimal("3.000") -> "3" (con :g salia "3.000", que se lee tres mil), 2.5 -> "2,5"."""
    from ..services.availability import qty_text

    return qty_text(v)


@router.put("/products/{pid}", response_model=ProductOut)
def update_product(pid: int, data: ProductIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    pr = _own(db, Product, pid, p.tenant.id)
    if data.code != pr.code and db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == p.tenant.id, Product.code == data.code)):
        raise HTTPException(409, "Ya existe un producto con ese codigo")
    if data.warehouse_id:
        _own(db, Warehouse, data.warehouse_id, p.tenant.id)
    # lo que no se envia no se borra. Quien no ve costos recibe cost=None en la ficha y lo devolveria asi: sin este
    # filtro, cada guardado de bodega borraba el costo del producto (Andres 08:07: el costo es solo del admin)
    exclude = {"tax_ids"} | (set() if p.sees_costs else COST_FIELDS) | (set() if p.can("catalog", "publicar") else {"show_on_web"})
    for k, v in data.model_dump(exclude=exclude, exclude_unset=True).items():
        setattr(pr, k, v)
    if "tax_ids" in data.model_fields_set:
        pr.taxes.clear()
        db.flush()
        for tid in data.tax_ids:
            _own(db, Tax, tid, p.tenant.id)
            pr.taxes.append(ProductTax(tax_id=tid))
    _margin_recheck(db, p, pr)
    _sv_cache.bump_catalog_version(db, p.tenant)
    db.commit()
    db.refresh(pr)
    return _product_out(pr, p)
