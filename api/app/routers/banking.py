"""Conciliacion bancaria: importar el estado de cuenta (CSV/Excel del banco) y casar cada linea con un pago
(creditos) o un gasto (debitos). Automatico por monto + fecha (+ referencia si coincide); manual para el resto."""

from __future__ import annotations

import hashlib
import secrets
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import BankAccount, BankStatementLine, Expense, ExpenseCategory, Invoice, Payment
from ..services import tabular as tb
from ..services.documents import audit
from ..services.totals import d

router = APIRouter(prefix="/banking", tags=["conciliacion"])

TOL_AMOUNT = Decimal("1.00")  # colones de diferencia tolerada (redondeos del banco)
TOL_DAYS = 5

DATE = ("fecha", "fecha_movimiento", "fecha_contable", "fecha_transaccion", "fecha_valor", "date", "fecha_de_movimiento")
DESC = ("descripcion", "concepto", "detalle", "description", "movimiento", "detalle_del_movimiento", "descripcion_del_movimiento")
REF = (
    "referencia",
    "documento",
    "numero_documento",
    "num_documento",
    "no_documento",
    "comprobante",
    "reference",
    "numero_de_referencia",
    "referencia_bancaria",
)
AMOUNT = ("monto", "importe", "amount", "valor", "monto_colones")
CREDIT = ("credito", "creditos", "abono", "abonos", "deposito", "depositos", "entrada", "ingresos", "credit")
DEBIT = ("debito", "debitos", "cargo", "cargos", "retiro", "retiros", "salida", "egresos", "debit")


def _account(db: Session, aid: int, tid: int) -> BankAccount:
    a = db.get(BankAccount, aid)
    if not a or a.tenant_id != tid:
        raise HTTPException(404, "Cuenta bancaria no encontrada")
    return a


def parse_statement(data: bytes, filename: str) -> list[dict]:
    out = []
    for r in tb.read_table(data, filename):
        dt = tb.to_date(tb.pick(r, *DATE))
        amt = tb.num(tb.pick(r, *AMOUNT))
        if amt is None:
            cr, db_ = tb.num(tb.pick(r, *CREDIT)), tb.num(tb.pick(r, *DEBIT))
            if cr is None and db_ is None:
                continue
            amt = (cr or Decimal(0)) - abs(db_ or Decimal(0))
        if not dt or amt is None or amt == 0:
            continue
        out.append(
            {
                "date": dt,
                "amount": amt.quantize(Decimal("0.01")),
                "description": tb.text(tb.pick(r, *DESC)) or "Movimiento",
                "reference": tb.text(tb.pick(r, *REF), 120),
            }
        )
    return out


def _line_out(ln: BankStatementLine, db: Session) -> dict:
    match = None
    if ln.matched_type == "payment" and ln.matched_id:
        pay = db.get(Payment, ln.matched_id)
        if pay:
            inv = db.get(Invoice, pay.invoice_id)
            match = {
                "type": "payment",
                "id": pay.id,
                "label": f"Pago {pay.method} · {inv.number if inv else ''}",
                "amount": pay.amount,
                "date": pay.paid_at,
                "to": f"/facturas/{pay.invoice_id}",
            }
    elif ln.matched_type == "expense" and ln.matched_id:
        e = db.get(Expense, ln.matched_id)
        if e:
            match = {"type": "expense", "id": e.id, "label": f"Gasto · {e.description}", "amount": e.total, "date": e.date, "to": "/contabilidad"}
    return {
        "id": ln.id,
        "date": ln.date,
        "description": ln.description,
        "reference": ln.reference,
        "amount": ln.amount,
        "status": ln.status,
        "matched_by": ln.matched_by,
        "match": match,
        "batch": ln.batch,
    }


def _taken(db: Session, tid: int, kind: str) -> set[int]:
    return set(
        db.scalars(
            select(BankStatementLine.matched_id).where(
                BankStatementLine.tenant_id == tid, BankStatementLine.matched_type == kind, BankStatementLine.status == "conciliado"
            )
        )
    )


def candidates(db: Session, ln: BankStatementLine, acct: BankAccount, strict: bool = True) -> list[dict]:
    """Posibles contrapartidas. strict=True (automatico): mismo monto y +/-5 dias. strict=False: +/-30 dias."""
    days = TOL_DAYS if strict else 30
    lo, hi = ln.date - timedelta(days=days), ln.date + timedelta(days=days)
    amount = d(ln.amount)
    out = []
    if amount > 0:
        taken = _taken(db, ln.tenant_id, "payment")
        q = select(Payment).where(
            Payment.tenant_id == ln.tenant_id,
            Payment.status == "confirmado",
            Payment.kind == "captura",
            Payment.currency == acct.currency,
            Payment.paid_at >= lo,
            Payment.paid_at <= hi,
        )
        for pay in db.scalars(q):
            if pay.id in taken or (pay.bank_account_id and pay.bank_account_id != acct.id):
                continue
            diff = abs(d(pay.amount) - amount)
            if strict and diff > TOL_AMOUNT:
                continue
            inv = db.get(Invoice, pay.invoice_id)
            ref_hit = bool(ln.reference and pay.external_ref and (ln.reference in pay.external_ref or pay.external_ref in ln.reference))
            out.append(
                {
                    "type": "payment",
                    "id": pay.id,
                    "label": f"Pago {pay.method} · {inv.number if inv else ''}",
                    "amount": pay.amount,
                    "date": pay.paid_at,
                    "diff": diff,
                    "days": abs((pay.paid_at - ln.date).days),
                    "ref": ref_hit,
                }
            )
    else:
        taken = _taken(db, ln.tenant_id, "expense")
        q = select(Expense).where(
            Expense.tenant_id == ln.tenant_id, Expense.status != "anulado", Expense.currency == acct.currency, Expense.date >= lo, Expense.date <= hi
        )
        for e in db.scalars(q):
            if e.id in taken or (e.bank_account_id and e.bank_account_id != acct.id):
                continue
            diff = abs(d(e.total) + amount)
            if strict and diff > TOL_AMOUNT:
                continue
            ref_hit = bool(ln.reference and e.reference and (ln.reference in e.reference or e.reference in ln.reference))
            out.append(
                {
                    "type": "expense",
                    "id": e.id,
                    "label": f"Gasto · {e.description}",
                    "amount": e.total,
                    "date": e.date,
                    "diff": diff,
                    "days": abs((e.date - ln.date).days),
                    "ref": ref_hit,
                }
            )
    out.sort(key=lambda c: (not c["ref"], c["diff"], c["days"]))
    return out


def auto_match(db: Session, acct: BankAccount) -> int:
    n = 0
    pending = db.scalars(
        select(BankStatementLine).where(BankStatementLine.bank_account_id == acct.id, BankStatementLine.status == "pendiente").order_by(BankStatementLine.date)
    ).all()
    for ln in pending:
        cands = candidates(db, ln, acct, strict=True)
        if not cands:
            continue
        best = cands[0]
        # solo casar si no hay empate ambiguo (dos pagos iguales el mismo dia sin referencia)
        if len(cands) > 1 and not best["ref"] and cands[1]["diff"] == best["diff"] and cands[1]["days"] == best["days"]:
            continue
        ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "conciliado", best["type"], best["id"], "auto"
        db.flush()
        n += 1
    return n


@router.get("/accounts")
def accounts(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    out = []
    for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant.id, BankAccount.active).order_by(BankAccount.name)):
        counts = dict(
            db.execute(select(BankStatementLine.status, func.count()).where(BankStatementLine.bank_account_id == a.id).group_by(BankStatementLine.status)).all()
        )
        last = db.scalar(select(func.max(BankStatementLine.date)).where(BankStatementLine.bank_account_id == a.id))
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "bank": a.bank,
                "currency": a.currency,
                "number": a.number,
                "pending": counts.get("pendiente", 0),
                "reconciled": counts.get("conciliado", 0),
                "ignored": counts.get("ignorado", 0),
                "last_date": last,
            }
        )
    return out


@router.get("/{aid}/lines")
def lines(aid: int, status: str | None = None, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    _account(db, aid, p.tenant.id)
    q = select(BankStatementLine).where(BankStatementLine.bank_account_id == aid)
    if status:
        q = q.where(BankStatementLine.status == status)
    return [_line_out(ln, db) for ln in db.scalars(q.order_by(BankStatementLine.date.desc(), BankStatementLine.id.desc()).limit(500))]


@router.post("/{aid}/import", status_code=201)
async def import_statement(aid: int, file: UploadFile = File(...), p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    acct = _account(db, aid, p.tenant.id)
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Archivo muy grande (maximo 5 MB)")
    try:
        rows = parse_statement(data, file.filename or "")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"No se pudo leer el archivo: {e}") from e
    if not rows:
        raise HTTPException(422, "No se encontraron movimientos. El archivo necesita columnas de fecha y monto (o credito/debito).")
    batch = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2)
    seen: Counter = Counter()
    existing = set(db.scalars(select(BankStatementLine.fingerprint).where(BankStatementLine.bank_account_id == aid)))
    added = dup = 0
    for r in rows:
        base = f"{aid}|{r['date']}|{r['amount']}|{r['description']}|{r['reference'] or ''}"
        seen[base] += 1
        fp = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        if fp in existing:
            dup += 1
            continue
        db.add(BankStatementLine(tenant_id=p.tenant.id, bank_account_id=aid, batch=batch, fingerprint=fp, imported_at=datetime.now(UTC), **r))
        added += 1
    db.flush()
    matched = auto_match(db, acct)
    audit(db, p.tenant.id, p.user.id, "import", "bank_statement", aid, {"added": added, "duplicates": dup, "matched": matched}, ip=p.ip)
    db.commit()
    return {"added": added, "duplicates": dup, "matched": matched, "batch": batch}


@router.post("/{aid}/auto")
def run_auto(aid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    acct = _account(db, aid, p.tenant.id)
    n = auto_match(db, acct)
    db.commit()
    return {"matched": n}


def _line(db: Session, lid: int, tid: int) -> tuple[BankStatementLine, BankAccount]:
    ln = db.get(BankStatementLine, lid)
    if not ln or ln.tenant_id != tid:
        raise HTTPException(404, "Movimiento no encontrado")
    return ln, db.get(BankAccount, ln.bank_account_id)


@router.get("/lines/{lid}/candidates")
def line_candidates(lid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    ln, acct = _line(db, lid, p.tenant.id)
    return candidates(db, ln, acct, strict=False)[:25]


class MatchIn(BaseModel):
    type: str = Field(pattern="^(payment|expense)$")
    id: int


@router.post("/lines/{lid}/match")
def match(lid: int, data: MatchIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    ln, acct = _line(db, lid, p.tenant.id)
    if ln.status == "conciliado":
        raise HTTPException(409, "El movimiento ya esta conciliado")
    if not any(c["type"] == data.type and c["id"] == data.id for c in candidates(db, ln, acct, strict=False)):
        raise HTTPException(422, "Esa contrapartida no corresponde a este movimiento (signo, divisa, fecha o ya conciliada)")
    ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "conciliado", data.type, data.id, "manual"
    audit(db, p.tenant.id, p.user.id, "match", "bank_line", ln.id, data.model_dump(), ip=p.ip)
    db.commit()
    return _line_out(ln, db)


@router.post("/lines/{lid}/unmatch")
def unmatch(lid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    ln, _ = _line(db, lid, p.tenant.id)
    ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "pendiente", None, None, None
    audit(db, p.tenant.id, p.user.id, "unmatch", "bank_line", ln.id, ip=p.ip)
    db.commit()
    return _line_out(ln, db)


@router.post("/lines/{lid}/ignore")
def ignore(lid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    ln, _ = _line(db, lid, p.tenant.id)
    if ln.status == "conciliado":
        raise HTTPException(409, "Primero deshaga la conciliacion")
    ln.status = "pendiente" if ln.status == "ignorado" else "ignorado"
    db.commit()
    return _line_out(ln, db)


class ExpenseFromLineIn(BaseModel):
    category_id: int | None = None
    description: str | None = None
    tax_rate: Decimal = Field(Decimal(0), ge=0, le=13)


@router.post("/lines/{lid}/expense", status_code=201)
def expense_from_line(lid: int, data: ExpenseFromLineIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    """Comisiones, cargos bancarios o pagos sin factura: crea el gasto desde el debito y lo concilia."""
    ln, acct = _line(db, lid, p.tenant.id)
    if d(ln.amount) >= 0 or ln.status == "conciliado":
        raise HTTPException(409, "Solo debitos pendientes")
    if data.category_id:
        cat = db.get(ExpenseCategory, data.category_id)
        if not cat or cat.tenant_id != p.tenant.id:
            raise HTTPException(404, "Categoria no encontrada")
    total = -d(ln.amount)
    sub = (total / (1 + data.tax_rate / 100)).quantize(Decimal("0.00001"))
    e = Expense(
        tenant_id=p.tenant.id,
        category_id=data.category_id,
        description=(data.description or ln.description)[:300],
        date=ln.date,
        currency=acct.currency,
        subtotal=sub,
        tax_rate=data.tax_rate,
        tax_amount=total - sub,
        total=total,
        iva_credit="credito" if data.tax_rate > 0 else "no_credito",
        bank_account_id=acct.id,
        reference=ln.reference,
        status="pagado",
        created_by=p.user.id,
    )
    db.add(e)
    db.flush()
    ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "conciliado", "expense", e.id, "manual"
    audit(db, p.tenant.id, p.user.id, "expense_from_line", "bank_line", ln.id, {"expense_id": e.id}, ip=p.ip)
    db.commit()
    return _line_out(ln, db)


def summary(db: Session, tid: int, a: date, b: date) -> list[list]:
    """Filas para el reporte de conciliacion."""
    rows = []
    for ln in db.scalars(
        select(BankStatementLine)
        .where(BankStatementLine.tenant_id == tid, BankStatementLine.date >= a, BankStatementLine.date <= b)
        .order_by(BankStatementLine.date)
    ):
        acct = db.get(BankAccount, ln.bank_account_id)
        rows.append(
            [
                ln.date.isoformat(),
                acct.name if acct else "",
                ln.description,
                ln.reference or "",
                ln.amount,
                ln.status,
                ln.matched_type or "",
                ln.matched_by or "",
            ]
        )
    return rows
