"""Clientes, productos, impuestos y categorias. Listados con busqueda + cursor ("Mas resultados")."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Category, Customer, Product, ProductTax, Tax
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

router = APIRouter(tags=["catalogo"])


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
    stmt = select(Customer).where(Customer.tenant_id == p.tenant.id, Customer.active)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.id_number.ilike(like), Customer.email.ilike(like)))
    return _page(db, stmt, cursor, limit, Customer, CustomerOut.model_validate)


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(data: CustomerIn, p: Principal = Depends(require("crm", "crear")), db: Session = Depends(get_db)):
    c = Customer(tenant_id=p.tenant.id, **data.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/customers/{cid}", response_model=CustomerOut)
def get_customer(cid: int, p: Principal = Depends(require("crm", "ver")), db: Session = Depends(get_db)):
    return _own(db, Customer, cid, p.tenant.id)


@router.put("/customers/{cid}", response_model=CustomerOut)
def update_customer(cid: int, data: CustomerIn, p: Principal = Depends(require("crm", "editar")), db: Session = Depends(get_db)):
    c = _own(db, Customer, cid, p.tenant.id)
    for k, v in data.model_dump(exclude_unset=True).items():  # lo que no se envia no se borra
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


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
    db.commit()
    db.refresh(c)
    return c


# ---------- Productos ----------
def _product_out(pr: Product, p: Principal | None = None) -> ProductOut:
    o = ProductOut.model_validate(pr)
    o.tax_ids = [t.tax_id for t in pr.taxes]
    o.tax_rate = float(pr.taxes[0].tax.rate) if pr.taxes else None
    if p is not None and not p.sees_prices:  # el tecnico ve el catalogo sin plata
        o.price, o.cost, o.margin_pct = 0, None, None
    return o


@router.get("/products", response_model=Page)
def products(
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    p: Principal = Depends(require("catalog", "ver")),
    db: Session = Depends(get_db),
):
    stmt = select(Product).where(Product.tenant_id == p.tenant.id, Product.active)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.cabys_code.ilike(like)))
    return _page(db, stmt, cursor, limit, Product, lambda pr: _product_out(pr, p))


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(data: ProductIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == p.tenant.id, Product.code == data.code)):
        raise HTTPException(409, "Ya existe un producto con ese codigo")
    pr = Product(tenant_id=p.tenant.id, **data.model_dump(exclude={"tax_ids"}))
    for tid in data.tax_ids:
        _own(db, Tax, tid, p.tenant.id)
        pr.taxes.append(ProductTax(tax_id=tid))
    db.add(pr)
    db.commit()
    db.refresh(pr)
    return _product_out(pr, p)


@router.get("/products/{pid}", response_model=ProductOut)
def get_product(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return _product_out(_own(db, Product, pid, p.tenant.id), p)


class WebIn(BaseModel):
    show_on_web: bool


@router.patch("/products/{pid}/web", response_model=ProductOut)
def toggle_web(pid: int, data: WebIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Publica o quita el producto de la tienda desde la lista, sin abrir la ficha."""
    pr = _own(db, Product, pid, p.tenant.id)
    pr.show_on_web = data.show_on_web
    db.commit()
    db.refresh(pr)
    return _product_out(pr, p)


@router.get("/products/{pid}/availability")
def availability(pid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """Existencias propias por bodega + disponibilidad del proveedor (lo que ve el cliente en la tienda)."""
    from ..services import inventory as invsvc

    pr = _own(db, Product, pid, p.tenant.id)
    levels = [lv for lv in invsvc.stock_levels(db, p.tenant.id, product_id=pid)]
    own = sum((lv["quantity"] for lv in levels), Decimal(0))
    return {
        "product_id": pid,
        "own_stock": own,
        "min_stock": pr.min_stock,
        "supplier_stock": pr.supplier_stock,
        "supplier_updated_at": pr.supplier_updated_at,
        "label": store_availability(pr, own)["label"],
    }


def store_availability(pr: Product, own: Decimal) -> dict:
    """Etiqueta de disponibilidad para la tienda: propio, bajo pedido o agotado."""
    if pr.item_type != "producto":
        return {"state": "servicio", "label": "Servicio", "own": 0}
    if own > 0:
        return {"state": "en_bodega", "label": f"Disponible ({own:g} en bodega)", "own": own}
    if (pr.supplier_stock or 0) > 0:
        return {"state": "bajo_pedido", "label": "Bajo pedido (3-5 días hábiles)", "own": 0}
    return {"state": "agotado", "label": "Consultar disponibilidad", "own": 0}


@router.put("/products/{pid}", response_model=ProductOut)
def update_product(pid: int, data: ProductIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    pr = _own(db, Product, pid, p.tenant.id)
    if data.code != pr.code and db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == p.tenant.id, Product.code == data.code)):
        raise HTTPException(409, "Ya existe un producto con ese codigo")
    for k, v in data.model_dump(exclude={"tax_ids"}, exclude_unset=True).items():  # lo que no se envia no se borra
        setattr(pr, k, v)
    if "tax_ids" in data.model_fields_set:
        pr.taxes.clear()
        db.flush()
        for tid in data.tax_ids:
            _own(db, Tax, tid, p.tenant.id)
            pr.taxes.append(ProductTax(tax_id=tid))
    db.commit()
    db.refresh(pr)
    return _product_out(pr, p)
