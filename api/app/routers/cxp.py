"""Cuentas por pagar: pagos a proveedor, antigüedad, estado de cuenta, próximos vencimientos."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import BankAccount, Expense, Supplier, SupplierPayment, SupplierPaymentApplication
from ..services.bitacora import audit_change, snapshot
from ..services.documents import audit
from ..services.fx import FxMissing, for_date

router = APIRouter(prefix="/cxp", tags=["cuentas_por_pagar"])

METHODS = ("transferencia", "sinpe", "cheque", "tarjeta", "efectivo")


# ---------- helpers ----------

def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _applied_map(db: Session, expense_ids: list[int]) -> dict[int, Decimal]:
    """Monto aplicado (suma de aplicaciones de pagos no anulados) por expense_id."""
    if not expense_ids:
        return {}
    rows = db.execute(
        select(SupplierPaymentApplication.expense_id, func.sum(SupplierPaymentApplication.amount))
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentApplication.payment_id)
        .where(
            SupplierPaymentApplication.expense_id.in_(expense_ids),
            SupplierPayment.status == "registrado",
        )
        .group_by(SupplierPaymentApplication.expense_id)
    ).all()
    return {r[0]: _d(r[1]) for r in rows}


def _expense_balance(e: Expense, applied_map: dict[int, Decimal]) -> Decimal:
    """Saldo del gasto en su propia moneda (total − aplicado)."""
    return max(Decimal(0), _d(e.total) - applied_map.get(e.id, Decimal(0)))


def _supplier(db: Session, sid: int, tid: int) -> Supplier:
    s = db.get(Supplier, sid)
    if not s or s.tenant_id != tid:
        raise HTTPException(404, "Proveedor no encontrado")
    return s


def _payment(db: Session, pid: int, tid: int) -> SupplierPayment:
    p = db.get(SupplierPayment, pid)
    if not p or p.tenant_id != tid:
        raise HTTPException(404, "Pago no encontrado")
    return p


def _open_expenses(db: Session, tid: int, supplier_id: int | None = None) -> list[Expense]:
    """Gastos de crédito con saldo > 0 (no anulados, no completamente pagados)."""
    q = select(Expense).where(
        Expense.tenant_id == tid,
        Expense.purchase_type == "credito",
        Expense.status != "anulado",
    )
    if supplier_id is not None:
        q = q.where(Expense.supplier_id == supplier_id)
    rows = list(db.scalars(q.order_by(Expense.due_date.nullsfirst(), Expense.date)))
    if not rows:
        return []
    am = _applied_map(db, [e.id for e in rows])
    return [e for e in rows if _expense_balance(e, am) > 0]


def _payment_out(pay: SupplierPayment, db: Session) -> dict:
    sup = db.get(Supplier, pay.supplier_id)
    return {
        "id": pay.id,
        "supplier_id": pay.supplier_id,
        "supplier_name": sup.name if sup else None,
        "date": pay.date,
        "amount": pay.amount,
        "currency": pay.currency,
        "fx_rate": pay.fx_rate,
        "method": pay.method,
        "bank_account_id": pay.bank_account_id,
        "reference": pay.reference,
        "note": pay.note,
        "status": pay.status,
        "void_reason": pay.void_reason,
        "voided_at": pay.voided_at,
        "created_at": pay.created_at,
        "applications": [
            {"id": a.id, "expense_id": a.expense_id, "amount": a.amount}
            for a in pay.applications
        ],
    }


# ---------- Schemas ----------

class ApplicationIn(BaseModel):
    expense_id: int
    amount: Decimal = Field(gt=0)


class PaymentIn(BaseModel):
    supplier_id: int
    date: date
    amount: Decimal = Field(gt=0)
    currency: str = Field("CRC", pattern="^(CRC|USD|EUR)$")
    method: str
    bank_account_id: int | None = None
    reference: str | None = Field(None, max_length=120)
    note: str | None = None
    applications: list[ApplicationIn] = Field(default_factory=list)

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        if v not in METHODS:
            raise ValueError(f"Método inválido; use: {', '.join(METHODS)}")
        return v


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


# ---------- Endpoints ----------

@router.get("/payments")
def list_payments(
    supplier_id: int | None = None,
    status: str | None = None,
    from_: date | None = None,
    to: date | None = None,
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    q = select(SupplierPayment).where(SupplierPayment.tenant_id == p.tenant.id)
    if supplier_id:
        q = q.where(SupplierPayment.supplier_id == supplier_id)
    if status:
        q = q.where(SupplierPayment.status == status)
    if from_:
        q = q.where(SupplierPayment.date >= from_)
    if to:
        q = q.where(SupplierPayment.date <= to)
    return [_payment_out(pay, db) for pay in db.scalars(q.order_by(SupplierPayment.date.desc(), SupplierPayment.id.desc()))]


@router.get("/payments/{pid}")
def get_payment(pid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    return _payment_out(_payment(db, pid, p.tenant.id), db)


@router.post("/payments", status_code=201)
def create_payment(data: PaymentIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    sup = _supplier(db, data.supplier_id, p.tenant.id)

    # tipo de cambio del servicio único
    fx_rate, _warn = for_date(db, data.currency, data.date)

    # validar banco si se indica
    if data.bank_account_id:
        ba = db.get(BankAccount, data.bank_account_id)
        if not ba or ba.tenant_id != p.tenant.id:
            raise HTTPException(404, "Cuenta bancaria no encontrada")

    # validar aplicaciones: sin sobrepago, sólo gastos de crédito de la empresa y del proveedor
    total_apps = Decimal(0)
    if data.applications:
        eids = [a.expense_id for a in data.applications]
        expenses_map = {
            e.id: e
            for e in db.scalars(
                select(Expense).where(
                    Expense.id.in_(eids),
                    Expense.tenant_id == p.tenant.id,
                )
            )
        }
        am = _applied_map(db, eids)
        for app in data.applications:
            e = expenses_map.get(app.expense_id)
            if not e:
                raise HTTPException(404, f"Gasto {app.expense_id} no encontrado")
            if e.purchase_type != "credito":
                raise HTTPException(422, f"El gasto {app.expense_id} es de contado; sólo se pueden pagar gastos de crédito")
            if e.status == "anulado":
                raise HTTPException(422, f"El gasto {app.expense_id} está anulado")
            saldo = _expense_balance(e, am)
            if _d(app.amount) > saldo + Decimal("0.005"):  # tolerancia de centavo para FP
                raise HTTPException(422, f"Sobrepago en gasto {app.expense_id}: saldo {saldo}, aplicado {app.amount}")
            total_apps += _d(app.amount)

    pay = SupplierPayment(
        tenant_id=p.tenant.id,
        supplier_id=data.supplier_id,
        date=data.date,
        amount=float(data.amount),
        currency=data.currency,
        fx_rate=float(fx_rate),
        method=data.method,
        bank_account_id=data.bank_account_id,
        reference=data.reference,
        note=data.note,
        status="registrado",
        created_by=p.user.id,
    )
    db.add(pay)
    db.flush()

    # crear aplicaciones y actualizar estado de los gastos
    if data.applications:
        am = _applied_map(db, [a.expense_id for a in data.applications])
        for app in data.applications:
            db.add(SupplierPaymentApplication(payment_id=pay.id, expense_id=app.expense_id, amount=float(app.amount)))
            db.flush()
            # recalcular saldo después de insertar
            am_post = _applied_map(db, [app.expense_id])
            e = expenses_map[app.expense_id]
            if _expense_balance(e, am_post) <= Decimal("0.005"):
                e.status = "pagado"

    audit(db, p.tenant.id, p.user.id, "create", "supplier_payment", pay.id, {"supplier": sup.name, "amount": float(data.amount), "method": data.method}, ip=p.ip)
    db.commit()
    db.refresh(pay)
    return _payment_out(pay, db)


@router.post("/payments/{pid}/anular", status_code=200)
def void_payment(pid: int, data: VoidIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    pay = _payment(db, pid, p.tenant.id)
    if pay.status == "anulado":
        raise HTTPException(409, "El pago ya está anulado")

    antes = snapshot(pay)
    pay.status = "anulado"
    pay.void_reason = data.reason
    pay.voided_by = p.user.id
    pay.voided_at = datetime.now(UTC)

    # devolver saldo: los gastos cubiertos por este pago vuelven a "registrado"
    eids = [a.expense_id for a in pay.applications]
    if eids:
        for e in db.scalars(select(Expense).where(Expense.id.in_(eids))):
            if e.status == "pagado" and e.purchase_type == "credito":
                # verificar si todavía tiene saldo > 0 después de anular
                am = _applied_map(db, [e.id])  # aún incluye este pago porque no se ha flusheado
                # el pago que se anula aporta sus aplicaciones; las restamos manualmente
                anulado_amount = next((a.amount for a in pay.applications if a.expense_id == e.id), 0)
                saldo_sin_este = _d(e.total) - (am.get(e.id, Decimal(0)) - _d(anulado_amount))
                if saldo_sin_este > Decimal("0.005"):
                    e.status = "registrado"

    audit_change(db, p, "update", "supplier_payment", pay.id, antes, snapshot(pay), reason=data.reason)
    db.commit()
    return _payment_out(pay, db)


# ---------- Saldo de un gasto ----------

@router.get("/expenses/{eid}/balance")
def expense_balance(eid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e or e.tenant_id != p.tenant.id:
        raise HTTPException(404, "Gasto no encontrado")
    if e.purchase_type != "credito":
        return {"expense_id": eid, "purchase_type": e.purchase_type, "balance": 0, "total": e.total, "applied": 0}
    am = _applied_map(db, [eid])
    applied = am.get(eid, Decimal(0))
    balance = max(Decimal(0), _d(e.total) - applied)
    # pagos que aplican a este gasto
    payments = list(
        db.scalars(
            select(SupplierPayment)
            .join(SupplierPaymentApplication, SupplierPaymentApplication.payment_id == SupplierPayment.id)
            .where(SupplierPaymentApplication.expense_id == eid, SupplierPayment.tenant_id == p.tenant.id)
            .order_by(SupplierPayment.date)
        )
    )
    return {
        "expense_id": eid,
        "purchase_type": e.purchase_type,
        "currency": e.currency,
        "total": float(e.total),
        "applied": float(applied),
        "balance": float(balance),
        "payments": [
            {
                "payment_id": pay.id,
                "date": pay.date,
                "method": pay.method,
                "status": pay.status,
                "amount": next((float(a.amount) for a in pay.applications if a.expense_id == eid), 0),
            }
            for pay in payments
        ],
    }


# ---------- Antigüedad de saldos por proveedor ----------

def _aging_buckets(today: date, e: Expense, balance: Decimal) -> dict:
    due = e.due_date or e.date
    days = (today - due).days
    return {
        "corriente": float(balance) if days <= 0 else 0,
        "d1_30": float(balance) if 1 <= days <= 30 else 0,
        "d31_60": float(balance) if 31 <= days <= 60 else 0,
        "d61_90": float(balance) if 61 <= days <= 90 else 0,
        "d90_mas": float(balance) if days > 90 else 0,
    }


@router.get("/aging")
def aging(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Antigüedad de saldos por proveedor (corriente, 1-30, 31-60, 61-90, >90 días)."""
    today = date.today()
    open_exps = _open_expenses(db, p.tenant.id)
    if not open_exps:
        return {"rows": [], "totals": {"total": 0, "vencido": 0, "corriente": 0, "d1_30": 0, "d31_60": 0, "d61_90": 0, "d90_mas": 0}}

    am = _applied_map(db, [e.id for e in open_exps])
    sups = {s.id: s for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id))}

    # agrupar por (supplier_id, currency)
    by_sup: dict[tuple, dict] = {}
    for e in open_exps:
        bal = _expense_balance(e, am)
        if bal <= 0:
            continue
        key = (e.supplier_id, e.currency)
        if key not in by_sup:
            sup = sups.get(e.supplier_id)
            by_sup[key] = {
                "supplier_id": e.supplier_id,
                "supplier_name": sup.name if sup else "(sin proveedor)",
                "currency": e.currency,
                "corriente": Decimal(0),
                "d1_30": Decimal(0),
                "d31_60": Decimal(0),
                "d61_90": Decimal(0),
                "d90_mas": Decimal(0),
                "total": Decimal(0),
            }
        buckets = _aging_buckets(today, e, bal)
        row = by_sup[key]
        row["corriente"] += Decimal(str(buckets["corriente"]))
        row["d1_30"] += Decimal(str(buckets["d1_30"]))
        row["d31_60"] += Decimal(str(buckets["d31_60"]))
        row["d61_90"] += Decimal(str(buckets["d61_90"]))
        row["d90_mas"] += Decimal(str(buckets["d90_mas"]))
        row["total"] += bal

    rows = sorted(by_sup.values(), key=lambda r: -float(r["total"]))
    # serializar
    out = []
    for r in rows:
        vencido = r["d1_30"] + r["d31_60"] + r["d61_90"] + r["d90_mas"]
        out.append({
            "supplier_id": r["supplier_id"],
            "supplier_name": r["supplier_name"],
            "currency": r["currency"],
            "corriente": float(r["corriente"]),
            "d1_30": float(r["d1_30"]),
            "d31_60": float(r["d31_60"]),
            "d61_90": float(r["d61_90"]),
            "d90_mas": float(r["d90_mas"]),
            "vencido": float(vencido),
            "total": float(r["total"]),
        })

    totals = {
        "total": sum(r["total"] for r in out),
        "vencido": sum(r["vencido"] for r in out),
        "corriente": sum(r["corriente"] for r in out),
        "d1_30": sum(r["d1_30"] for r in out),
        "d31_60": sum(r["d31_60"] for r in out),
        "d61_90": sum(r["d61_90"] for r in out),
        "d90_mas": sum(r["d90_mas"] for r in out),
    }
    return {"rows": out, "totals": totals}


# ---------- Resumen ejecutivo ----------

@router.get("/summary")
def summary(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Total por pagar, vencido y vence en los próximos 7 días (en CRC)."""
    from ..services.fx import doc_rate

    today = date.today()
    week = date.fromordinal(today.toordinal() + 7)
    open_exps = _open_expenses(db, p.tenant.id)
    if not open_exps:
        return {"total_crc": 0, "vencido_crc": 0, "semana_crc": 0, "count": 0}

    am = _applied_map(db, [e.id for e in open_exps])
    total_crc = vencido_crc = semana_crc = Decimal(0)
    for e in open_exps:
        bal = _expense_balance(e, am)
        if bal <= 0:
            continue
        try:
            rate = doc_rate(db, e.currency, e.fx_rate, e.date)
        except FxMissing:
            rate = Decimal(1)
        bal_crc = (bal * rate).quantize(Decimal("0.01"))
        total_crc += bal_crc
        due = e.due_date or e.date
        if due < today:
            vencido_crc += bal_crc
        elif due <= week:
            semana_crc += bal_crc

    return {
        "total_crc": float(total_crc),
        "vencido_crc": float(vencido_crc),
        "semana_crc": float(semana_crc),
        "count": len(open_exps),
    }


# ---------- Próximos vencimientos (7 días) ----------

@router.get("/upcoming")
def upcoming(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    today = date.today()
    week = date.fromordinal(today.toordinal() + 7)
    q = select(Expense).where(
        Expense.tenant_id == p.tenant.id,
        Expense.purchase_type == "credito",
        Expense.status != "anulado",
    ).order_by(Expense.due_date.nullsfirst(), Expense.date)
    rows = list(db.scalars(q))
    if not rows:
        return []
    am = _applied_map(db, [e.id for e in rows])
    sups = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.tenant_id == p.tenant.id))}
    out = []
    for e in rows:
        bal = _expense_balance(e, am)
        if bal <= 0:
            continue
        due = e.due_date or e.date
        if due > week:
            continue
        out.append({
            "expense_id": e.id,
            "supplier_id": e.supplier_id,
            "supplier_name": sups.get(e.supplier_id, "(sin proveedor)"),
            "description": e.description,
            "due_date": due,
            "currency": e.currency,
            "balance": float(bal),
            "overdue": due < today,
        })
    return out


# ---------- Estado de cuenta de un proveedor ----------

@router.get("/suppliers/{sid}/statement")
def supplier_statement(sid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    sup = _supplier(db, sid, p.tenant.id)
    today = date.today()

    # gastos de crédito
    expenses = list(db.scalars(
        select(Expense).where(
            Expense.tenant_id == p.tenant.id,
            Expense.supplier_id == sid,
            Expense.purchase_type == "credito",
            Expense.status != "anulado",
        ).order_by(Expense.date)
    ))
    am = _applied_map(db, [e.id for e in expenses]) if expenses else {}

    exp_out = []
    for e in expenses:
        bal = _expense_balance(e, am)
        due = e.due_date or e.date
        exp_out.append({
            "expense_id": e.id,
            "date": e.date,
            "due_date": due,
            "description": e.description,
            "currency": e.currency,
            "total": float(e.total),
            "applied": float(am.get(e.id, Decimal(0))),
            "balance": float(bal),
            "status": e.status,
            "overdue": due < today and bal > 0,
        })

    # pagos
    payments = list(db.scalars(
        select(SupplierPayment).where(
            SupplierPayment.tenant_id == p.tenant.id,
            SupplierPayment.supplier_id == sid,
        ).order_by(SupplierPayment.date)
    ))

    pay_out = [_payment_out(pay, db) for pay in payments]

    total_balance = sum(r["balance"] for r in exp_out)
    total_vencido = sum(r["balance"] for r in exp_out if r["overdue"])

    return {
        "supplier": {"id": sup.id, "name": sup.name, "tax_id": sup.tax_id},
        "expenses": exp_out,
        "payments": pay_out,
        "total_balance": total_balance,
        "total_vencido": total_vencido,
    }


# ---------- Gastos abiertos de un proveedor (para el modal de registro de pago) ----------

@router.get("/suppliers/{sid}/open-expenses")
def supplier_open_expenses(sid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    _supplier(db, sid, p.tenant.id)
    open_exps = _open_expenses(db, p.tenant.id, supplier_id=sid)
    am = _applied_map(db, [e.id for e in open_exps]) if open_exps else {}
    return [
        {
            "expense_id": e.id,
            "date": e.date,
            "due_date": e.due_date or e.date,
            "description": e.description,
            "currency": e.currency,
            "total": float(e.total),
            "balance": float(_expense_balance(e, am)),
        }
        for e in open_exps
    ]
