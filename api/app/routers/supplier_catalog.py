"""Inventarios -> Proveedores: catalogo de cada proveedor separado del catalogo propio.

Lo pidio Andres: "Los inventarios van a ser donde vamos a conectar los APIs de los proveedores; ahi se van a mostrar
los productos que cada proveedor tenga, de una manera categorizada diferente para no mezclarlos con lo que nosotros
tenemos localmente." Hoy entra por Excel (lista de Eurocomp); el conector por API usara el mismo modelo (source=api).

Permisos: ver el catalogo del proveedor = catalog.ver (existencias sin costo). Costos = catalog.costos (la vendedora
nunca ve el costo del proveedor). Importar listas = catalog.crear + catalog.costos. Vincular = catalog.editar.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Category, Product, ProductTax, Supplier, SupplierItem, SupplierListImport, Tax
from ..providers.sirett import SirettError
from ..services import pricing, product_names, supplier_sync
from ..services import supplier_catalog as sc
from ..services import supplier_lists as sl
from ..services import tabular as tb
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(prefix="/supplier-catalog", tags=["proveedores"])

MAX = 5 * 1024 * 1024
API_LABEL = "Conexión por API: pendiente de credenciales (SiReTT)"  # ver supplier_sync.status
COST_KEYS = ("cost", "prev_cost", "cost_crc", "tax_rate")


def _need_costs(p: Principal) -> None:
    if not p.sees_costs:
        raise HTTPException(403, "Sin permiso: catalog.costos")


def _item(db: Session, iid: int, p: Principal) -> SupplierItem:
    it = db.get(SupplierItem, iid)
    if not it or it.tenant_id != p.tenant.id:
        raise HTTPException(404, "Ítem del proveedor no encontrado")
    return it


def _product(db: Session, pid: int, p: Principal) -> Product:
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    return pr


def _item_out(it: SupplierItem, supplier: str | None, prod: Product | None, p: Principal, fx: Decimal) -> dict:
    o = {
        "id": it.id,
        "supplier_id": it.supplier_id,
        "supplier": supplier,
        "code": it.code,
        "name": it.name,
        "description": it.description,
        "brand": it.brand,
        "model": it.model,
        "category": it.category,
        "subcategory": it.subcategory,
        "stock": it.stock,
        "currency": it.currency,
        "list_date": it.list_date,
        "active": it.active,
        "source": it.source,
        "source_file": it.source_file,
        "link_method": it.link_method,
        "linked_at": it.linked_at,
        "product": (
            {
                "id": prod.id,
                "code": prod.code,
                "name": prod.name,
                "active": prod.active,
                **({"price": prod.price, "currency": prod.currency} if p.sees_prices else {}),
            }
            if prod
            else None
        ),
    }
    if p.sees_costs:
        o.update(
            cost=d(it.cost) if it.cost is not None else None,
            prev_cost=d(it.prev_cost) if it.prev_cost is not None else None,
            cost_crc=sc.to_crc(it.cost, it.currency, fx),
            tax_rate=it.tax_rate,
        )
    return o


def _strip(o: dict, p: Principal) -> dict:
    if not p.sees_costs:
        for k in ("cost", "cost_crc", "currency"):
            o.pop(k, None)
    if not p.sees_prices:
        o.pop("sale_price", None)
    return o


# ---------- proveedores ----------
def _api_status(t, s: Supplier, changed_at) -> dict:
    st = supplier_sync.status(t, s)
    if st.get("label") and not st.get("last_change_at") and changed_at is not None:
        st["last_change_at"] = changed_at
    return st


@router.get("/suppliers")
def suppliers(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """Cada proveedor con su catalogo: cuantos items, vinculados, con existencia, categorias y la ultima lista."""
    tid = p.tenant.id
    stats = {
        sid: (n, act, linked, stock)
        for sid, n, act, linked, stock in db.execute(
            select(
                SupplierItem.supplier_id,
                func.count(),
                func.sum(case((SupplierItem.active.is_(True), 1), else_=0)),
                func.count(SupplierItem.product_id),
                func.sum(case((SupplierItem.active.is_(True) & (SupplierItem.stock > 0), 1), else_=0)),
            )
            .where(SupplierItem.tenant_id == tid)
            .group_by(SupplierItem.supplier_id)
        )
    }
    # el ultimo cambio REAL de la lista de cada proveedor (existencia, costo, entro o salio un item): aparte de la
    # ultima consulta. Lo usa la cabecera cuando la sincronizacion todavia no lo anoto (datos de antes del 21/10)
    changed = dict(
        db.execute(select(SupplierItem.supplier_id, func.max(SupplierItem.changed_at)).where(SupplierItem.tenant_id == tid).group_by(SupplierItem.supplier_id)).all()
    )
    cats: dict[int, list[dict]] = {}
    for sid, cat, sub, n in db.execute(
        select(SupplierItem.supplier_id, SupplierItem.category, SupplierItem.subcategory, func.count())
        .where(SupplierItem.tenant_id == tid, SupplierItem.active)
        .group_by(SupplierItem.supplier_id, SupplierItem.category, SupplierItem.subcategory)
    ):
        cats.setdefault(sid, []).append({"category": cat, "subcategory": sub, "count": n})
    last: dict[int, SupplierListImport] = {}
    for imp in db.scalars(select(SupplierListImport).where(SupplierListImport.tenant_id == tid).order_by(SupplierListImport.id)):
        last[imp.supplier_id] = imp
    # productos con este proveedor asignado pero sin item vinculado: su existencia del proveedor no cuenta
    unlinked = dict(
        db.execute(
            select(Product.supplier_id, func.count())
            .where(
                Product.tenant_id == tid,
                Product.active,
                Product.supplier_id.is_not(None),
                ~select(SupplierItem.id).where(SupplierItem.product_id == Product.id).exists(),
            )
            .group_by(Product.supplier_id)
        ).all()
    )
    out = []
    for s in db.scalars(select(Supplier).where(Supplier.tenant_id == tid).order_by(Supplier.name)):
        n, act, linked, stock = stats.get(s.id, (0, 0, 0, 0))
        li = last.get(s.id)
        out.append(
            {
                "id": s.id,
                "name": s.name,
                "items": n or 0,
                "active": int(act or 0),
                "linked": int(linked or 0),
                "in_stock": int(stock or 0),
                "unlinked_products": int(unlinked.get(s.id) or 0),
                "categories": sorted(cats.get(s.id, []), key=lambda c: ((c["category"] or "~").lower(), (c["subcategory"] or "").lower())),
                "last_list": (
                    {"list_date": li.list_date, "source": li.source, "source_file": li.source_file, "imported_at": li.imported_at, "summary": li.summary}
                    if li
                    else None
                ),
                "api": _api_status(p.tenant, s, changed.get(s.id)),
                "last_change_at": changed.get(s.id),
            }
        )
    out.sort(key=lambda s: (s["items"] == 0, s["name"].lower()))
    return out


@router.post("/suppliers/{sid}/sync")
def supplier_sync_now(sid: int, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    """Sincronizar ahora por SiReTT (Eurocomp). Eurocomp admite una consulta del catalogo completo por hora."""
    _need_costs(p)
    s = db.get(Supplier, sid)
    if not s or s.tenant_id != p.tenant.id:
        raise HTTPException(404, "Proveedor no encontrado")
    st = supplier_sync.status(p.tenant, s)
    if st["status"] == "no_aplica":
        raise HTTPException(409, "Este proveedor no tiene conexión por API")
    if st["status"] == "pendiente":
        raise HTTPException(409, "Faltan las credenciales de Eurocomp en el servidor")
    try:
        out = supplier_sync.sync_tenant(db, p.tenant, user_id=p.user.id)
    except SirettError as e:
        raise HTTPException(429 if e.code == 20 else 502, e.message) from None
    except supplier_sync.SyncRefused as e:
        raise HTTPException(409, str(e)) from None
    finally:
        supplier_sync.trim_memory()  # la API no se queda con el pico de la descarga
    return out


@router.get("/imports")
def imports(supplier_id: int | None = None, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    stmt = select(SupplierListImport).where(SupplierListImport.tenant_id == p.tenant.id)
    if supplier_id:
        stmt = stmt.where(SupplierListImport.supplier_id == supplier_id)
    names = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id))}
    return [
        {
            "id": i.id,
            "supplier_id": i.supplier_id,
            "supplier": names.get(i.supplier_id),
            "list_date": i.list_date,
            "source": i.source,
            "source_file": i.source_file,
            "imported_at": i.imported_at,
            "summary": i.summary,
        }
        for i in db.scalars(stmt.order_by(SupplierListImport.id.desc()).limit(50))
    ]


# ---------- items ----------
@router.get("/items")
def items(
    supplier_id: int | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    q: str | None = Query(None, max_length=120),
    linked: str | None = Query(None, pattern="^(si|no)$"),
    estado: str = Query("activos", pattern="^(activos|inactivos|todos)$"),
    stock: str | None = Query(None, pattern="^(con|sin)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require("catalog", "ver")),
    db: Session = Depends(get_db),
):
    """Catalogo del proveedor con filtros. La busqueda ignora tildes (la lista mezcla CAMARA y CÁMARA)."""
    stmt = select(SupplierItem).where(SupplierItem.tenant_id == p.tenant.id)
    if supplier_id:
        stmt = stmt.where(SupplierItem.supplier_id == supplier_id)
    if category is not None:
        stmt = stmt.where(SupplierItem.category == category) if category else stmt.where(SupplierItem.category.is_(None))
    if subcategory is not None:
        stmt = stmt.where(SupplierItem.subcategory == subcategory) if subcategory else stmt.where(SupplierItem.subcategory.is_(None))
    if linked == "si":
        stmt = stmt.where(SupplierItem.product_id.is_not(None))
    elif linked == "no":
        stmt = stmt.where(SupplierItem.product_id.is_(None))
    if estado == "activos":
        stmt = stmt.where(SupplierItem.active)
    elif estado == "inactivos":
        stmt = stmt.where(SupplierItem.active.is_(False))
    if stock == "con":
        stmt = stmt.where(SupplierItem.stock > 0)
    elif stock == "sin":
        stmt = stmt.where((SupplierItem.stock.is_(None)) | (SupplierItem.stock <= 0))
    rows = list(db.scalars(stmt.order_by(SupplierItem.category, SupplierItem.subcategory, SupplierItem.name, SupplierItem.id)))
    words = [w for w in tb.norm(q).split("_") if w] if q else []
    if words:

        def hay(i: SupplierItem) -> str:
            return tb.norm(" ".join(x for x in (i.code, i.name, i.model or "", i.brand or "", i.description or "") if x)).replace("_", "")

        rows = [i for i in rows if all(w in hay(i) for w in words)]
    total = len(rows)
    page = rows[offset : offset + limit]
    fx = sc.usd_rate(db)
    names = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id))}
    pids = {i.product_id for i in page if i.product_id}
    prods = {pr.id: pr for pr in db.scalars(select(Product).where(Product.id.in_(pids)))} if pids else {}
    return {
        "items": [_item_out(i, names.get(i.supplier_id), prods.get(i.product_id), p, fx) for i in page],
        "total": total,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
        "fx": fx if p.sees_costs else None,
    }


def _proposed_code(db: Session, tid: int, it: SupplierItem, supplier: Supplier | None) -> str:
    """Codigo propio sugerido: el modelo del fabricante (universal, no cambia de proveedor) o, sin modelo,
    el codigo del proveedor con prefijo (EUR-AC1104). Nunca choca con uno existente."""
    taken = lambda c: bool(db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tid, Product.code == c)))  # noqa: E731
    pref = re.sub(r"[^A-Z0-9]", "", (supplier.name if supplier else "PRV").upper())[:3] or "PRV"
    base = (it.model or f"{pref}-{it.code}")[:56]
    cand, n = base, 2
    while taken(cand):
        cand, n = f"{base}-{n}", n + 1
    return cand


def _suggest_category(db: Session, tid: int, it: SupplierItem) -> int | None:
    """Categoria propia con el mismo nombre que la subcategoria (o categoria) del proveedor, si existe."""
    for name in (it.subcategory, it.category):
        if not name:
            continue
        c = db.scalar(select(Category).where(Category.tenant_id == tid, func.lower(Category.name) == name.lower()))
        if c:
            return c.id
    return None


@router.get("/items/{iid}")
def item_detail(iid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    it = _item(db, iid, p)
    fx = sc.usd_rate(db)
    sup = db.get(Supplier, it.supplier_id)
    prod = db.get(Product, it.product_id) if it.product_id else None
    out = _item_out(it, sup.name if sup else None, prod, p, fx)
    out["raw"] = it.raw if p.sees_costs else None
    if not it.product_id:
        idx = sc.product_index(db, p.tenant.id)
        out["suggestions"] = sc.suggest(idx, code=it.code, supplier_id=it.supplier_id, model=it.model, name=it.name, limit=6)
    else:
        out["suggestions"] = []
    if p.sees_costs and not it.product_id:
        from ..services.fx import FxMissing

        margin = pricing.default_margin(p.tenant)
        try:
            price = pricing.sale_price(db, d(it.cost), it.currency or "USD", "CRC", margin, fx) if it.cost is not None else None
        except FxMissing:
            price, fx = None, None  # sin tipo de cambio el cajon abre igual: la propuesta queda sin precio (plan 1.12)
        out["proposal"] = {
            "code": _proposed_code(db, p.tenant.id, it, sup),
            "margin_pct": margin,
            "currency": "CRC",
            "price": price,
            "category_id": _suggest_category(db, p.tenant.id, it),
            "fx": fx,
            "policy": pricing.policy(margin),
        }
    return out


class LinkIn(BaseModel):
    product_id: int


@router.post("/items/{iid}/link")
def link_item(iid: int, data: LinkIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Confirma el vinculo item del proveedor -> producto propio. El producto toma costo y existencia del proveedor."""
    it = _item(db, iid, p)
    prod = _product(db, data.product_id, p)
    if not prod.active or prod.item_type != "producto":
        raise HTTPException(422, "Solo se vinculan productos activos (no servicios)")
    fx = sc.usd_rate(db)
    if it.product_id and it.product_id != prod.id:
        sc.unlink(db, it, fx=fx)
    sc.link(db, it, prod, "manual", p.user.id, fx=fx)
    audit(db, p.tenant.id, p.user.id, "link", "supplier_item", it.id, {"code": it.code, "product_id": prod.id, "product": prod.code}, ip=p.ip)
    db.commit()
    sup = db.get(Supplier, it.supplier_id)
    return _item_out(it, sup.name if sup else None, prod, p, fx)


class BulkLink(BaseModel):
    item_id: int
    product_id: int


class BulkLinksIn(BaseModel):
    links: list[BulkLink] = Field(default_factory=list, max_length=500)


@router.post("/links")
def link_many(data: BulkLinksIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Confirmar en bloque las sugerencias revisadas (cada una con su producto)."""
    fx = sc.usd_rate(db)
    done = 0
    for ln in data.links:
        it = _item(db, ln.item_id, p)
        prod = _product(db, ln.product_id, p)
        if not prod.active or prod.item_type != "producto":
            continue
        if it.product_id and it.product_id != prod.id:
            sc.unlink(db, it, fx=fx)
        sc.link(db, it, prod, "manual", p.user.id, fx=fx)
        done += 1
    audit(db, p.tenant.id, p.user.id, "link", "supplier_item", None, {"links": [(x.item_id, x.product_id) for x in data.links][:200], "done": done}, ip=p.ip)
    db.commit()
    return {"linked": done}


@router.delete("/items/{iid}/link")
def unlink_item(iid: int, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    it = _item(db, iid, p)
    if not it.product_id:
        raise HTTPException(409, "El ítem no está vinculado")
    pid = it.product_id
    sc.unlink(db, it)
    audit(db, p.tenant.id, p.user.id, "unlink", "supplier_item", it.id, {"code": it.code, "product_id": pid}, ip=p.ip)
    db.commit()
    sup = db.get(Supplier, it.supplier_id)
    return _item_out(it, sup.name if sup else None, None, p, sc.usd_rate(db))


class AddIn(BaseModel):
    code: str | None = Field(None, max_length=60)
    name: str | None = Field(None, max_length=200)
    margin_pct: Decimal | None = Field(None, ge=0, le=95)
    currency: str = Field("CRC", pattern="^(CRC|USD)$")
    category_id: int | None = None


@router.post("/items/{iid}/catalog", status_code=201)
def add_to_catalog(iid: int, data: AddIn, p: Principal = Depends(require("catalog", "crear")), db: Session = Depends(get_db)):
    """Agregar a mi catalogo: crea el producto propio con el costo del proveedor y el precio de venta con la politica
    de margen SOBRE LA VENTA vigente (precio = costo / (1 - margen)). El CABYS queda vacio para completarlo."""
    _need_costs(p)
    it = _item(db, iid, p)
    if it.product_id:
        raise HTTPException(409, "Este ítem ya está vinculado a un producto de tu catálogo")
    if it.cost is None or d(it.cost) <= 0:
        raise HTTPException(422, "El ítem no tiene precio en la lista del proveedor")
    sup = db.get(Supplier, it.supplier_id)
    code = (data.code or "").strip() or _proposed_code(db, p.tenant.id, it, sup)
    if db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == p.tenant.id, Product.code == code)):
        raise HTTPException(409, "Ya existe un producto con ese código")
    if data.category_id:
        c = db.get(Category, data.category_id)
        if not c or c.tenant_id != p.tenant.id:
            raise HTTPException(404, "Categoría no encontrada")
    fx = sc.usd_rate(db)
    margin = d(data.margin_pct) if data.margin_pct is not None else pricing.default_margin(p.tenant)
    price = pricing.sale_price(db, d(it.cost), it.currency or "USD", data.currency, margin, fx)
    specs = "\n".join((it.description or "").splitlines()[1:]).strip() or None
    prod = Product(
        tenant_id=p.tenant.id,
        code=code,
        name=(data.name or product_names.tidy(it.name))[:200],  # legible en la tienda; el original queda en la factura
        item_type="producto",
        unit="Unid",
        price=price,
        currency=data.currency,
        cost=it.cost,
        cost_currency=it.currency or "USD",
        margin_pct=margin,
        brand=it.brand,
        model=it.model,
        supplier_id=it.supplier_id,
        supplier_sku=it.code,
        supplier_stock=it.stock,
        supplier_updated_at=sc.list_datetime(it.list_date),
        description_invoice=it.name[:2000],
        description_store=specs,
        category_id=data.category_id,
        show_on_web=False,
        cabys_code=None,
    )
    db.add(prod)
    db.flush()
    iva = db.scalar(select(Tax).where(Tax.tenant_id == p.tenant.id, Tax.rate_code == "08", Tax.active))
    if iva:
        prod.taxes.append(ProductTax(tax_id=iva.id))
    sc.link(db, it, prod, "creado", p.user.id, fx=fx)
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "create",
        "product",
        prod.id,
        {
            "desde": "proveedor",
            "supplier_item": it.id,
            "code": code,
            "cost": str(it.cost),
            "currency": it.currency,
            "margin_pct": str(margin),
            "price": str(price),
        },
        ip=p.ip,
    )
    db.commit()
    return {
        "product_id": prod.id,
        "code": prod.code,
        "name": prod.name,
        "price": prod.price,
        "currency": prod.currency,
        "margin_pct": margin,
        "cost": prod.cost,
    }


@router.get("/suggestions")
def suggestions(
    supplier_id: int | None = None, limit: int = Query(100, ge=1, le=500), p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)
):
    """Items sin vincular que tienen un candidato en el catalogo propio (modelo parecido o nombre): para confirmar."""
    stmt = select(SupplierItem).where(SupplierItem.tenant_id == p.tenant.id, SupplierItem.active, SupplierItem.product_id.is_(None))
    if supplier_id:
        stmt = stmt.where(SupplierItem.supplier_id == supplier_id)
    idx = sc.product_index(db, p.tenant.id)
    taken = {pid for (pid,) in db.execute(select(SupplierItem.product_id).where(SupplierItem.tenant_id == p.tenant.id, SupplierItem.product_id.is_not(None)))}
    out = []
    for it in db.scalars(stmt.order_by(SupplierItem.category, SupplierItem.name)):
        sug = sc.suggest(idx, code=it.code, supplier_id=it.supplier_id, model=it.model, name=it.name, limit=3)
        if sug:
            for s in sug:
                s["already_linked"] = s["product_id"] in taken
            out.append({"item_id": it.id, "code": it.code, "name": it.name, "model": it.model, "suggestions": sug})
        if len(out) >= limit:
            break
    return out


# ---------- importar lista ----------
@router.post("/import")
async def import_list(
    commit: bool = False,
    supplier_id: int | None = None,
    supplier: str | None = Query(None, max_length=160),
    list_date: date | None = None,
    currency: str = Query("USD", pattern="^(USD|CRC)$"),
    brand: str | None = Query(None, max_length=60),
    mapping: str | None = Query(None, max_length=2000),
    file: UploadFile = File(...),
    p: Principal = Depends(require("catalog", "crear")),
    db: Session = Depends(get_db),
):
    """Sube la lista del proveedor. commit=false: vista previa (nuevos / actualizados / sin cambios / dudosos /
    se inactivan / vinculos automaticos). commit=true: aplica. Mismo archivo dos veces = sin cambios."""
    _need_costs(p)
    data = await file.read(MAX + 1)
    if len(data) > MAX:
        raise HTTPException(413, "Archivo muy grande (máximo 5 MB)")
    try:
        maps = json.loads(mapping) if mapping else None
        if maps is not None and not isinstance(maps, dict):
            raise ValueError
    except ValueError as e:
        raise HTTPException(422, "Mapeo de columnas inválido") from e
    ld = list_date or date.today()
    if ld > date.today():
        raise HTTPException(422, "La fecha de la lista no puede ser futura")
    try:
        parsed = sl.parse(data, file.filename or "", maps, default_currency=currency, default_brand=(brand or "").strip() or None)
        if len(parsed.rows) > 10000:
            raise sl.ListError("Máximo 10 000 filas por lista")
        sup, new_sup = sl.resolve_supplier(db, p.tenant.id, supplier_id, supplier)
        res = sl.apply_list(db, p.tenant, sup, parsed, list_date=ld, source="excel", source_file=file.filename, user_id=p.user.id)
    except sl.ListError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    res["supplier"]["new"] = new_sup
    res["commit"] = commit
    if commit:
        audit(db, p.tenant.id, p.user.id, "import", "supplier_list", sup.id, {"file": file.filename, "list_date": ld.isoformat(), **res["summary"]}, ip=p.ip)
        db.commit()
        # la lista copia el costo a lo vinculado: el guardian de margen revisa ya, sin esperar a la red diaria (su
        # propio commit; un error suyo no deshace la lista)
        from ..models import Tenant
        from ..services import margin_guard

        res["margin_guard"] = margin_guard.run_safe(db, db.get(Tenant, p.tenant.id), reason="lista de proveedor")
    else:
        db.rollback()
        if new_sup:
            res["supplier"]["id"] = None
    return res


# ---------- ofertas y busqueda ----------
@router.get("/offers")
def offers(product_id: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """Ofertas de proveedores vinculadas a un producto propio (ficha del producto)."""
    prod = _product(db, product_id, p)
    rows = [_strip(o, p) for o in sc.offers_for_product(db, prod)]
    return {"product_id": prod.id, "offers": rows}


@router.get("/search")
def search(
    q: str = Query(..., min_length=2, max_length=200),
    category: str | None = Query(None, max_length=80),
    limit: int = Query(10, ge=1, le=50),
    p: Principal = Depends(require("catalog", "ver")),
    db: Session = Depends(get_db),
):
    """Lo mismo que usa el levantamiento (services.supplier_catalog.search_offers), para probarlo desde el portal."""
    rows = sc.search_offers(db, p.tenant.id, terms=q.split(), category=category, limit=limit)
    return [_strip(o, p) for o in rows]
