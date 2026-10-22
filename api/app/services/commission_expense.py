"""Comision pagada -> gasto en Contabilidad (reunion 2, pedido 2).

Marcar pagada una comision de vendedor registra sola un gasto en la categoria "Comisiones" (la crea si no existe),
con la referencia COMISION-{id} y el tipo de cambio con que se devengo (el gasto en colones es el mismo monto que el
libro de comisiones). Asi sale en el estado de resultados y Andres ve cuanto le cuestan las comisiones a la empresa.

- Una sola vez: la comision apunta su gasto (Commission.expense_id); marcarla otra vez, o deshacer el pago y volver a
  pagarla, reutiliza ese mismo gasto (nunca dos gastos por una comision).
- Deshacer el pago (POST /commissions/undo-payment, solo el admin) anula el gasto: no se borra, queda "anulado" con
  su bitacora, y deja de contar en el estado de resultados.
- Si al pagar se vincula un gasto que ya existia (expense_id en el PATCH), no se crea otro.
- Aliados: no. Su factura entra por Recepcion y ese comprobante ya es el gasto (registrarlo aqui lo duplicaria).
- Un ajuste negativo pagado (descontado de un pago) registra un gasto negativo: el neto de comisiones queda bien.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Commission, Expense, ExpenseCategory, User
from .documents import audit

CATEGORY = "Comisiones"


def ref_of(c: Commission) -> str:
    return f"COMISION-{c.id}"


def applies(c: Commission) -> bool:
    """Solo las de vendedor: lo del aliado (comision, bono, pago acordado) entra por su factura en Recepcion."""
    from .commissions import is_payout

    return (c.beneficiary or "vendedor") == "vendedor" and not is_payout(c)


def category(db: Session, tenant_id: int) -> ExpenseCategory:
    cat = db.scalar(select(ExpenseCategory).where(ExpenseCategory.tenant_id == tenant_id, func.lower(ExpenseCategory.name) == CATEGORY.lower()))
    if cat is None:
        cat = ExpenseCategory(tenant_id=tenant_id, name=CATEGORY, pnl_group="operativo")
        db.add(cat)
        db.flush()
    return cat


def _ours(c: Commission, e: Expense | None) -> bool:
    return e is not None and e.tenant_id == c.tenant_id and e.reference == ref_of(c)


def _fill(db: Session, c: Commission, e: Expense) -> None:
    from .commissions import KIND_LABEL

    who = db.get(User, c.user_id).full_name if c.user_id and db.get(User, c.user_id) else "vendedor"
    amount = Decimal(str(c.amount or 0))
    kind = KIND_LABEL.get(c.kind or "comision", "Comisión")
    concept = c.description or (c.terms or {}).get("doc") or ""
    desc = f"{kind} de {who}" + (f" · {concept}" if concept else "")
    if amount < 0:
        desc = f"Descuento de comisión de {who}" + (f" · {concept}" if concept else "")
    e.description = desc[:300]
    e.date = c.paid_on
    e.currency = c.currency or "CRC"
    e.fx_rate = c.fx if c.fx and Decimal(str(c.fx)) > 0 else 1
    e.subtotal = amount
    e.tax_rate = 0
    e.tax_amount = 0
    e.total = amount
    e.iva_credit = "no_credito"
    e.tax_credit_amount = Decimal(0)
    e.status = "pagado"
    e.notes = f"Registrado al marcar pagada la comisión n.º {c.id}" + (f" · pago: {c.paid_ref}" if c.paid_ref else "")


def record_paid(db: Session, c: Commission, user_id: int | None, ip: str | None = None) -> Expense | None:
    """Gasto de una comision recien pagada. Idempotente: devuelve el que ya tiene (reactivandolo si se habia anulado)."""
    if not applies(c) or c.status != "pagada":
        return None
    e = db.get(Expense, c.expense_id) if c.expense_id else None
    if e is not None and not _ours(c, e):
        return e  # vinculado a mano a un gasto que ya existia: ese es el gasto
    if e is None:
        e = db.scalar(select(Expense).where(Expense.tenant_id == c.tenant_id, Expense.reference == ref_of(c)))
    nuevo = e is None
    if nuevo:
        e = Expense(tenant_id=c.tenant_id, category_id=category(db, c.tenant_id).id, reference=ref_of(c), created_by=user_id, description="")
    elif e.category_id is None:
        e.category_id = category(db, c.tenant_id).id
    antes = e.status if not nuevo else None
    _fill(db, c, e)  # antes de agregarlo: las consultas de _fill no lo vacian a medio llenar
    if nuevo:
        db.add(e)
    db.flush()
    c.expense_id = e.id
    audit(
        db,
        c.tenant_id,
        user_id,
        "create" if nuevo else "update",
        "expense",
        e.id,
        {"comision": c.id, "de": antes, "a": "pagado", "monto": str(e.total), "moneda": e.currency},
        ip=ip,
    )
    return e


def void_for(db: Session, c: Commission, user_id: int | None, reason: str, ip: str | None = None) -> Expense | None:
    """Pago deshecho: el gasto que se registro solo se anula (no se borra). Un gasto vinculado a mano no se toca."""
    e = db.get(Expense, c.expense_id) if c.expense_id else None
    if not _ours(c, e) or e.status == "anulado":
        return None
    antes = e.status
    e.status = "anulado"
    e.notes = ((e.notes or "") + f"\nAnulado: se deshizo el pago de la comisión ({reason})")[:2000]
    audit(db, c.tenant_id, user_id, "status", "expense", e.id, {"comision": c.id, "de": antes, "a": "anulado", "motivo": reason[:240]}, ip=ip)
    return e


def backfill(db: Session, tenant_id: int, user_id: int | None, a=None, b=None, ip: str | None = None) -> int:
    """Comisiones de vendedor ya pagadas sin gasto (pagadas antes de este registro): les crea el suyo."""
    w = [Commission.tenant_id == tenant_id, Commission.status == "pagada", Commission.expense_id.is_(None)]
    if a is not None:
        w.append(Commission.paid_on >= a)
    if b is not None:
        w.append(Commission.paid_on <= b)
    n = 0
    for c in db.scalars(select(Commission).where(*w).order_by(Commission.id)):
        if applies(c) and c.paid_on and record_paid(db, c, user_id, ip) is not None:
            n += 1
    return n
