"""Gastos: IVA por tarifa y categoria automatica por proveedor (reunion con la contadora, 10/11/2026).

GET    /expenses/{eid}/tax-breakdown              desglose del gasto (tarifa, base, IVA, acreditable)
POST   /expenses/{eid}/tax-breakdown/recalcular   lo vuelve a calcular desde el XML (o la tarifa del gasto) y lo guarda
POST   /expenses/{eid}/category/confirmar         la categoria automatica deja de ser automatica (la regla queda)
GET    /expense-category-rules                    reglas proveedor -> categoria
PUT    /expense-category-rules/{supplier_id}      fija la regla de un proveedor
DELETE /expense-category-rules/{supplier_id}      la quita (el proveedor vuelve a tomar la de su ultimo gasto)

Router aparte de routers/ops.py a proposito. Todo filtrado por la empresa de la sesion.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Expense, ExpenseCategory, Supplier, SupplierCategoryRule
from ..services import expense_category as ecat
from ..services import expense_tax
from ..services.bitacora import audit_change

router = APIRouter(tags=["gastos"])


def _expense(db: Session, p: Principal, eid: int) -> Expense:
    e = db.get(Expense, eid)
    if not e or e.tenant_id != p.tenant.id:
        raise HTTPException(404, "Gasto no encontrado")
    return e


def _breakdown_out(db: Session, e: Expense) -> dict:
    parts = expense_tax.tax_breakdown(db, e)
    saved = expense_tax.stored_parts(db, e)
    tot = expense_tax.summarize(parts)
    return {
        "expense_id": e.id,
        "currency": (e.currency or "CRC").upper(),
        "source": "xml" if any(x.source == "xml" for x in parts) else "manual",
        # True = lo guardado al recibir el XML (o con el relleno) y todavia suma el gasto; False = calculado al vuelo
        "stored": bool(saved) and expense_tax.consistent(e, saved),
        "iva_credit": e.iva_credit,
        "parts": [x.out() for x in parts],
        "totals": tot,
        "category_id": e.category_id,
        "category_auto": bool(e.category_auto),
    }


@router.get("/expenses/{eid}/tax-breakdown")
def get_breakdown(eid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    return _breakdown_out(db, _expense(db, p, eid))


@router.post("/expenses/{eid}/tax-breakdown/recalcular")
def recalc_breakdown(eid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    e = _expense(db, p, eid)
    _, changed = expense_tax.sync(db, e)
    db.commit()
    return {**_breakdown_out(db, e), "changed": changed}


@router.post("/expenses/{eid}/category/confirmar")
def confirm_category(eid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    e = _expense(db, p, eid)
    if not e.category_id:
        raise HTTPException(409, "El gasto no tiene categoría")
    was = bool(e.category_auto)
    ecat.confirm(db, e, p.user.id)
    if was:
        audit_change(db, p, "update", "expense", e.id, {"category_auto": True}, {"category_auto": False}, reason="Categoría automática confirmada")
    db.commit()
    return {"expense_id": e.id, "category_id": e.category_id, "category_auto": bool(e.category_auto)}


# ---------------------------------------------------------------- reglas proveedor -> categoria
def _rule_out(r: SupplierCategoryRule, sups: dict, cats: dict) -> dict:
    return {
        "supplier_id": r.supplier_id,
        "supplier_name": sups.get(r.supplier_id),
        "category_id": r.category_id,
        "category_name": cats.get(r.category_id),
        "source": r.source,
        "hits": r.hits or 0,
        "updated_at": r.updated_at,
    }


@router.get("/expense-category-rules")
def list_rules(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    tid = p.tenant.id
    rules = db.scalars(select(SupplierCategoryRule).where(SupplierCategoryRule.tenant_id == tid)).all()
    sups = dict(db.execute(select(Supplier.id, Supplier.name).where(Supplier.tenant_id == tid)).all())
    cats = dict(db.execute(select(ExpenseCategory.id, ExpenseCategory.name).where(ExpenseCategory.tenant_id == tid)).all())
    out = [_rule_out(r, sups, cats) for r in rules]
    return sorted(out, key=lambda r: (r["supplier_name"] or "").lower())


class RuleIn(BaseModel):
    category_id: int


@router.put("/expense-category-rules/{supplier_id}")
def put_rule(supplier_id: int, data: RuleIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    tid = p.tenant.id
    sup = db.get(Supplier, supplier_id)
    if not sup or sup.tenant_id != tid:
        raise HTTPException(404, "Proveedor no encontrado")
    cat = db.get(ExpenseCategory, data.category_id)
    if not cat or cat.tenant_id != tid or not cat.active:
        raise HTTPException(404, "Categoría no encontrada")
    old = ecat.rule_for(db, tid, supplier_id)
    antes = {"category_id": old.category_id, "source": old.source} if old else {}
    rule = ecat.learn(db, tid, supplier_id, cat.id, p.user.id, source="manual")
    db.flush()
    audit_change(db, p, "update" if old else "create", "supplier_category_rule", rule.id, antes, {"category_id": rule.category_id, "source": rule.source}, reason=f"Categoría automática de {sup.name}")
    db.commit()
    return _rule_out(rule, {sup.id: sup.name}, {cat.id: cat.name})


@router.delete("/expense-category-rules/{supplier_id}")
def delete_rule(supplier_id: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    rule = ecat.rule_for(db, p.tenant.id, supplier_id)
    if rule is None:
        raise HTTPException(404, "Ese proveedor no tiene regla")
    antes = {"category_id": rule.category_id, "source": rule.source}
    rid = rule.id
    db.delete(rule)
    audit_change(db, p, "delete", "supplier_category_rule", rid, antes, {}, reason="Regla de categoría quitada")
    db.commit()
    return {"ok": True}

