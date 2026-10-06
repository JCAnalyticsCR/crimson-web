"""Completar CABYS en bloque: productos sin codigo, sugerencia por producto y confirmacion humana.

Produccion tiene ~218 productos reales sin CABYS y sin CABYS no se factura. El sistema sugiere; una persona
confirma (uno a uno o "aceptar seleccionadas"). Nada se asigna solo. Cada asignacion queda auditada.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Category, Product, ProductTax, Tax
from ..services import cabys as cabys_svc
from ..services import cabys_suggest
from ..services import inventory as invsvc
from ..services.documents import audit

router = APIRouter(tags=["catalogo"])

MAX_SUGGEST = 40  # por llamada: el portal pide por grupos para no dejar esperando a nadie
MAX_ASSIGN = 300


def _sin_cabys(tenant_id: int):
    return [Product.tenant_id == tenant_id, Product.active, or_(Product.cabys_code.is_(None), Product.cabys_code == "")]


@router.get("/cabys/pending")
def pending(
    category_id: int | None = None,
    q: str | None = Query(None, max_length=80),
    limit: int = Query(500, ge=1, le=1000),
    p: Principal = Depends(require("catalog", "ver")),
    db: Session = Depends(get_db),
):
    """Productos activos sin CABYS. Primero los que tienen existencias (los que se van a facturar ya)."""
    stmt = select(Product).where(*_sin_cabys(p.tenant.id))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.brand.ilike(like), Product.model.ilike(like)))
    rows = db.scalars(stmt).all()
    cats = {c.id: c.name for c in db.scalars(select(Category).where(Category.tenant_id == p.tenant.id))}
    stock: dict[int, Decimal] = {}
    for lv in invsvc.stock_levels(db, p.tenant.id):
        stock[lv["product_id"]] = stock.get(lv["product_id"], Decimal(0)) + lv["quantity"]
    items = [
        {
            "id": x.id,
            "code": x.code,
            "name": x.name,
            "brand": x.brand,
            "model": x.model,
            "item_type": x.item_type,
            "category_id": x.category_id,
            "category": cats.get(x.category_id) if x.category_id else None,
            "own_stock": stock.get(x.id, Decimal(0)),
            "supplier_stock": x.supplier_stock,
            "tax_rate": float(x.taxes[0].tax.rate) if x.taxes else None,
        }
        for x in rows
    ]
    items.sort(key=lambda r: (-(r["own_stock"] > 0), -((r["supplier_stock"] or 0) > 0), (r["category"] or "~").lower(), r["name"].lower()))
    grupos: dict[str, dict] = {}
    for r in items:
        g = grupos.setdefault(str(r["category_id"] or 0), {"id": r["category_id"], "name": r["category"] or "Sin categoría", "count": 0, "with_stock": 0})
        g["count"] += 1
        g["with_stock"] += 1 if r["own_stock"] > 0 else 0
    return {
        "total": len(items),
        "with_stock": sum(1 for r in items if r["own_stock"] > 0),
        "items": items[:limit],
        "categories": sorted(grupos.values(), key=lambda g: (-g["with_stock"], -g["count"], g["name"])),
    }


class SuggestIn(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=MAX_SUGGEST)


def _tax_by_rate(db: Session, tenant_id: int, rate) -> Tax | None:
    if rate is None:
        return None
    for t in db.scalars(select(Tax).where(Tax.tenant_id == tenant_id, Tax.active).order_by(Tax.id)):
        if Decimal(str(t.rate)) == Decimal(str(rate)):
            return t
    return None


@router.post("/cabys/suggest")
def suggest(data: SuggestIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Sugerencia por producto (codigo, descripcion oficial, IVA). Solo sugiere: no guarda nada."""
    cats = {c.id: c.name for c in db.scalars(select(Category).where(Category.tenant_id == p.tenant.id))}
    rates = {str(Decimal(str(t.rate)).normalize()) for t in db.scalars(select(Tax).where(Tax.tenant_id == p.tenant.id, Tax.active))}
    out, caido = [], None
    for pid in dict.fromkeys(data.product_ids):
        pr = db.get(Product, pid)
        if not pr or pr.tenant_id != p.tenant.id:
            out.append({"product_id": pid, "error": "Producto no encontrado"})
            continue
        if caido:  # si Hacienda no contesto, no se sigue esperando timeout por cada producto
            out.append({"product_id": pid, "error": caido})
            continue
        try:
            s = cabys_suggest.sugerir(pr.name, cats.get(pr.category_id), pr.brand, pr.model, pr.item_type)
        except cabys_svc.CabysUnavailable:
            caido = "El buscador CABYS de Hacienda no respondió. Intentá de nuevo en un momento."
            out.append({"product_id": pid, "error": caido})
            continue
        for r in [s["suggestion"], *s["alternatives"]]:
            if r is not None:
                r["tax_configured"] = r.get("tax_rate") is None or str(Decimal(str(r["tax_rate"])).normalize()) in rates
        out.append({"product_id": pid, **s, "error": None if s["suggestion"] else "Sin coincidencias: buscá a mano con otra palabra."})
    return {"items": out}


class AssignItem(BaseModel):
    product_id: int
    code: str = Field(min_length=1, max_length=20)  # el formato se valida por fila, para reportarlo junto al resto


class AssignIn(BaseModel):
    items: list[AssignItem] = Field(min_length=1, max_length=MAX_ASSIGN)
    apply_tax: bool = True  # poner el IVA que dice el CABYS como impuesto del producto


@router.post("/cabys/assign")
def assign(data: AssignIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Guarda en bloque lo que una persona confirmo. Cada codigo se valida (13 digitos y que exista en el CABYS);
    lo invalido se reporta y no se guarda, lo valido si. Queda quien y cuando, en el producto y en la bitacora."""
    now = datetime.now(UTC)
    results, saved = [], 0
    caido = False
    for it in data.items:
        code = "".join(ch for ch in it.code if ch.isdigit())
        res = {"product_id": it.product_id, "code": code, "ok": False}
        results.append(res)
        pr = db.get(Product, it.product_id)
        if not pr or pr.tenant_id != p.tenant.id:
            res["error"] = "Producto no encontrado"
            continue
        if len(code) != 13 or code != it.code.strip():
            res["error"] = "El código CABYS lleva exactamente 13 dígitos"
            continue
        if caido:
            res["error"] = "No se pudo validar con Hacienda; no se guardó"
            continue
        try:
            row = cabys_svc.find_code(code)
        except cabys_svc.CabysUnavailable:
            caido = True
            res["error"] = "No se pudo validar con Hacienda; no se guardó"
            continue
        if not row:
            res["error"] = "Ese código no existe en el CABYS de Hacienda"
            continue
        antes = pr.cabys_code
        pr.cabys_code, pr.cabys_description = code, row["description"][:300]
        pr.cabys_set_by, pr.cabys_set_at = p.user.id, now
        res.update(ok=True, description=row["description"], tax_rate=row.get("tax_rate"), tax_applied=False)
        if data.apply_tax and row.get("tax_rate") is not None:
            tax = _tax_by_rate(db, p.tenant.id, row["tax_rate"])
            if tax is None:
                res["warning"] = f"IVA {row['tax_rate']:g}% no está configurado en Ajustes; el impuesto del producto no se cambió"
            elif [t.tax_id for t in pr.taxes] != [tax.id]:
                pr.taxes.clear()
                db.flush()
                pr.taxes.append(ProductTax(tax_id=tax.id))
                res["tax_applied"] = True
        audit(
            db,
            p.tenant.id,
            p.user.id,
            "cabys_assign",
            "product",
            pr.id,
            {"de": antes, "a": code, "descripcion": row["description"][:120], "iva": row.get("tax_rate"), "iva_aplicado": res["tax_applied"]},
            ip=p.ip,
        )
        saved += 1
    if saved:
        db.commit()
    # parcial a proposito: lo valido se guarda y cada fila con error vuelve con su motivo para corregirla
    return {"saved": saved, "errors": len(results) - saved, "results": results}
