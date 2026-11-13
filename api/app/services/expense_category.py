"""Categoria automatica por proveedor (reunion con la contadora, 10/11/2026: «este proveedor siempre es medicamentos»,
como QuickBooks).

* Un gasto NUEVO de un proveedor que llega sin categoria toma la de su regla (supplier_category_rule) o, si el
  proveedor todavia no tiene regla, la de su ultimo gasto categorizado. Queda marcado category_auto=True para que se
  vea como automatica y se pueda corregir.
* Cuando una persona pone o cambia la categoria de un gasto con proveedor (al crearlo o al editarlo), la regla del
  proveedor aprende esa categoria y la marca automatica del gasto se quita.
* Multiempresa: la regla, el proveedor, la categoria y el gasto son siempre de la misma empresa; una regla o una
  categoria de otra empresa nunca se aplica.

Funciona en un before_flush de la sesion: cubre todos los caminos que crean o editan gastos (a mano, recepcion de
XML, recurrentes, bancos) sin tocar sus routers. Lo registra models/expense_tax.py (al importar los modelos).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, attributes

from ..models import Expense, ExpenseCategory, Supplier, SupplierCategoryRule

_SKIP = "_expense_category_skip"  # session.info: desactiva el listener (relleno masivo, pruebas)


def _valid_category(db: Session, tenant_id: int, category_id: int | None) -> ExpenseCategory | None:
    if not category_id:
        return None
    cat = db.get(ExpenseCategory, category_id)
    if cat is None or cat.tenant_id != tenant_id or not cat.active:
        return None
    return cat


def rule_for(db: Session, tenant_id: int, supplier_id: int) -> SupplierCategoryRule | None:
    for obj in db.new:  # una regla recien agregada en este mismo flush (dos gastos nuevos del mismo proveedor)
        if isinstance(obj, SupplierCategoryRule) and obj.tenant_id == tenant_id and obj.supplier_id == supplier_id:
            return obj
    return db.scalar(select(SupplierCategoryRule).where(SupplierCategoryRule.tenant_id == tenant_id, SupplierCategoryRule.supplier_id == supplier_id))


def suggest(db: Session, tenant_id: int, supplier_id: int | None, exclude_id: int | None = None) -> tuple[int | None, SupplierCategoryRule | None]:
    """Categoria que le toca a un gasto nuevo del proveedor: (category_id, regla usada o None)."""
    if not supplier_id:
        return None, None
    sup = db.get(Supplier, supplier_id)
    if sup is None or sup.tenant_id != tenant_id:
        return None, None
    rule = rule_for(db, tenant_id, supplier_id)
    if rule is not None and _valid_category(db, tenant_id, rule.category_id):
        return rule.category_id, rule
    q = (
        select(Expense.category_id)
        .where(
            Expense.tenant_id == tenant_id,
            Expense.supplier_id == supplier_id,
            Expense.category_id.is_not(None),
            Expense.status != "anulado",
        )
        .order_by(Expense.date.desc(), Expense.id.desc())
        .limit(5)
    )
    if exclude_id:
        q = q.where(Expense.id != exclude_id)
    for cid in db.scalars(q):
        if _valid_category(db, tenant_id, cid):
            return cid, None
    return None, None


def learn(db: Session, tenant_id: int, supplier_id: int | None, category_id: int | None, user_id: int | None = None, source: str = "aprendida") -> SupplierCategoryRule | None:
    """La regla del proveedor pasa a esta categoria (alta o cambio). Sin proveedor o con categoria ajena: nada."""
    if not supplier_id or not _valid_category(db, tenant_id, category_id):
        return None
    sup = db.get(Supplier, supplier_id)
    if sup is None or sup.tenant_id != tenant_id:
        return None
    rule = rule_for(db, tenant_id, supplier_id)
    if rule is None:
        rule = SupplierCategoryRule(tenant_id=tenant_id, supplier_id=supplier_id, category_id=category_id, source=source, hits=0, updated_by=user_id)
        db.add(rule)
        return rule
    if rule.category_id != category_id or (source == "manual" and rule.source != "manual"):
        rule.category_id = category_id
        rule.source = source
        rule.updated_by = user_id
    return rule


def _changed(obj, attr: str) -> bool:
    return attributes.get_history(obj, attr).has_changes()


def before_flush(db: Session, _ctx, _instances) -> None:
    if db.info.get(_SKIP):
        return
    with db.no_autoflush:
        for obj in list(db.new):
            if not isinstance(obj, Expense) or not obj.tenant_id or not obj.supplier_id:
                continue
            if obj.category_id is None:
                cid, rule = suggest(db, obj.tenant_id, obj.supplier_id)
                if cid:
                    obj.category_id = cid
                    obj.category_auto = True
                    if rule is not None:
                        rule.hits = (rule.hits or 0) + 1
            elif not obj.category_auto:
                learn(db, obj.tenant_id, obj.supplier_id, obj.category_id, obj.created_by)
        for obj in list(db.dirty):
            if not isinstance(obj, Expense) or not obj.tenant_id or not _changed(obj, "category_id"):
                continue
            if _changed(obj, "category_auto") and obj.category_auto:
                continue  # la puso la regla en este mismo cambio: no es una correccion
            if obj.category_auto:
                obj.category_auto = False  # una persona la cambio: deja de ser automatica
            if obj.category_id is not None and obj.status != "anulado":
                learn(db, obj.tenant_id, obj.supplier_id, obj.category_id)


def confirm(db: Session, e: Expense, user_id: int | None = None) -> None:
    """La persona confirma la categoria automatica: deja de verse como automatica y la regla queda como esta."""
    if e.category_auto:
        e.category_auto = False
    if e.category_id:
        learn(db, e.tenant_id, e.supplier_id, e.category_id, user_id)
