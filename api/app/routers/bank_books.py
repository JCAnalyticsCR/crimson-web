"""Bancos: saldo inicial con fecha de corte, libro de movimientos con saldo corrido, estados de cuenta (vista previa e
importacion con mapeo de columnas), movimientos propios de la cuenta y conciliacion contra el banco.

La logica vive en services/bank_books.py. Las cuentas, chequeras y tarjetas siguen en /banking (routers/banking.py);
este router suma lo que pidio la contadora el 10/11/2026. Todo por tenant y con permisos de Contabilidad.
"""

from __future__ import annotations

import json
import secrets
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import BankAccount, BankMovement, BankStatementImport, BankStatementLine, Expense, Payment, SupplierPayment
from ..services import bank_books as bb
from ..services.documents import audit

router = APIRouter(prefix="/bank-books", tags=["bancos"])


def _account(db: Session, aid: int, tid: int) -> BankAccount:
    a = db.get(BankAccount, aid)
    if not a or a.tenant_id != tid:
        raise HTTPException(404, "Cuenta no encontrada")
    return a


def _line(db: Session, lid: int, tid: int) -> tuple[BankStatementLine, BankAccount]:
    ln = db.get(BankStatementLine, lid)
    if not ln or ln.tenant_id != tid:
        raise HTTPException(404, "Movimiento del estado de cuenta no encontrado")
    return ln, db.get(BankAccount, ln.bank_account_id)


def _money(x) -> str | None:
    return None if x is None else str(bb.q2(x))


# ------------------------------------------------------------------ cuentas y saldo inicial


@router.get("/accounts")
def accounts(all: int = 0, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):  # noqa: A002
    q = select(BankAccount).where(BankAccount.tenant_id == p.tenant.id)
    if not all:
        q = q.where(BankAccount.active)
    return [bb.account_summary(db, a) for a in db.scalars(q.order_by(BankAccount.name))]


class OpeningIn(BaseModel):
    opening_balance: Decimal = Field(max_digits=16, decimal_places=2)
    opening_date: date
    kind: str = Field("banco", pattern="^(banco|caja)$")


@router.put("/accounts/{aid}/opening")
def set_opening(aid: int, data: OpeningIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Saldo inicial a la fecha de corte (lo que decia el banco ese dia) y si la cuenta es banco o caja."""
    from ..services.bitacora import audit_change, snapshot

    a = _account(db, aid, p.tenant.id)
    antes = snapshot(a)
    a.opening_balance, a.opening_date, a.kind = bb.q2(data.opening_balance), data.opening_date, data.kind
    audit_change(db, p, "update", "bank_account", a.id, antes, snapshot(a))
    db.commit()
    return bb.account_summary(db, a)


# ------------------------------------------------------------------ libro y conciliacion


@router.get("/accounts/{aid}/ledger")
def ledger(
    aid: int,
    date_from: date | None = None,
    date_to: date | None = None,
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    a = _account(db, aid, p.tenant.id)
    return {"account": bb.account_summary(db, a), **bb.book_ledger(db, a, date_from, date_to)}


@router.get("/accounts/{aid}/reconciliation")
def reconciliation(aid: int, as_of: date | None = None, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    a = _account(db, aid, p.tenant.id)
    return bb.reconciliation(db, a, as_of)


@router.get("/accounts/{aid}/lines")
def lines(
    aid: int,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    a = _account(db, aid, p.tenant.id)
    q = select(BankStatementLine).where(BankStatementLine.tenant_id == p.tenant.id, BankStatementLine.bank_account_id == a.id)
    if status:
        q = q.where(BankStatementLine.status == status)
    if date_from:
        q = q.where(BankStatementLine.date >= date_from)
    if date_to:
        q = q.where(BankStatementLine.date <= date_to)
    rows = db.scalars(q.order_by(BankStatementLine.date.desc(), BankStatementLine.id.desc()).limit(1000)).all()
    matches = bb.describe_matches(db, p.tenant.id, rows)
    return [bb.line_out(ln, matches.get(ln.id)) for ln in rows]


@router.post("/accounts/{aid}/auto-match")
def auto_match(aid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    a = _account(db, aid, p.tenant.id)
    n = bb.auto_match(db, a)
    audit(db, p.tenant.id, p.user.id, "auto_match", "bank_account", a.id, {"matched": n}, ip=p.ip)
    db.commit()
    return {"matched": n}


@router.get("/lines/{lid}/candidates")
def line_candidates(lid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    ln, a = _line(db, lid, p.tenant.id)
    return bb.candidates(db, ln, a)


class MatchIn(BaseModel):
    type: str = Field(pattern="^(payment|expense|supplier_payment|movement)$")
    id: int


@router.post("/lines/{lid}/match")
def match(lid: int, data: MatchIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Casar a mano. Si el movimiento de Crimson no tenia cuenta, queda asignado a esta (el dinero entro o salio aqui)."""
    ln, a = _line(db, lid, p.tenant.id)
    if ln.status != "pendiente":
        raise HTTPException(409, "El movimiento ya está conciliado o ignorado")
    found = bb.load_entry(db, p.tenant.id, data.type, data.id)
    if not found:
        raise HTTPException(404, "Movimiento de Crimson no encontrado")
    obj, ent = found
    if problem := bb.entry_problem(db, a, data.type, obj, ent):
        raise HTTPException(422, problem)
    if (ent.amount > 0) != (bb.q2(ln.amount) > 0):
        raise HTTPException(422, "Un ingreso del banco se casa con un ingreso de Crimson y una salida con una salida")
    other = bb.matched_keys(db, p.tenant.id).get(ent.key)
    if other is not None:
        raise HTTPException(409, "Ese movimiento de Crimson ya está conciliado con otra línea del banco")
    assigned = False
    if isinstance(obj, (Payment, Expense, SupplierPayment)) and obj.bank_account_id is None:
        obj.bank_account_id, assigned = a.id, True
    ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "conciliado", data.type, data.id, "manual"
    diff = bb.q2(abs(ent.amount - bb.q2(ln.amount)))
    audit(db, p.tenant.id, p.user.id, "match", "bank_line", ln.id, {**data.model_dump(), "diff": str(diff), "assigned_account": assigned}, ip=p.ip)
    db.commit()
    return {**bb.line_out(ln, ent.out()), "assigned_account": assigned}


@router.post("/lines/{lid}/unmatch")
def unmatch(lid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    ln, _ = _line(db, lid, p.tenant.id)
    if ln.status != "conciliado":
        raise HTTPException(409, "La línea no está conciliada")
    before = {"type": ln.matched_type, "id": ln.matched_id, "by": ln.matched_by}
    ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "pendiente", None, None, None
    audit(db, p.tenant.id, p.user.id, "unmatch", "bank_line", ln.id, before, ip=p.ip)
    db.commit()
    return bb.line_out(ln)


@router.post("/lines/{lid}/ignore")
def ignore(lid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Ignorar (o reactivar) una linea que no se concilia: no suma al saldo segun el banco."""
    ln, _ = _line(db, lid, p.tenant.id)
    if ln.status == "conciliado":
        raise HTTPException(409, "Primero deshacé la conciliación")
    ln.status = "pendiente" if ln.status == "ignorado" else "ignorado"
    audit(db, p.tenant.id, p.user.id, "ignore" if ln.status == "ignorado" else "unignore", "bank_line", ln.id, ip=p.ip)
    db.commit()
    return bb.line_out(ln)


# ------------------------------------------------------------------ movimientos propios de la cuenta


class MovementIn(BaseModel):
    date: date
    amount: Decimal = Field(max_digits=16, decimal_places=2)  # + entra, - sale
    kind: str = Field(pattern="^(deposito|interes|cargo|ajuste|otro)$")
    description: str = Field(min_length=1, max_length=300)
    reference: str | None = Field(None, max_length=120)


def _movement_out(m: BankMovement) -> dict:
    return {
        "id": m.id,
        "bank_account_id": m.bank_account_id,
        "date": m.date,
        "amount": _money(m.amount),
        "kind": m.kind,
        "description": m.description,
        "reference": m.reference,
        "transfer_group": m.transfer_group,
        "status": m.status,
    }


@router.post("/accounts/{aid}/movements", status_code=201)
def movement_create(aid: int, data: MovementIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    a = _account(db, aid, p.tenant.id)
    if data.amount == 0:
        raise HTTPException(422, "El monto no puede ser cero")
    m = BankMovement(tenant_id=p.tenant.id, bank_account_id=a.id, created_by=p.user.id, status="registrado", **{**data.model_dump(), "amount": bb.q2(data.amount)})
    db.add(m)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "bank_movement", m.id, {"amount": str(m.amount), "kind": m.kind}, ip=p.ip)
    db.commit()
    return _movement_out(m)


class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    date: date
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)  # sale de la cuenta origen (en su divisa)
    amount_to: Decimal | None = Field(None, gt=0, max_digits=16, decimal_places=2)  # entra al destino (otra divisa)
    description: str | None = Field(None, max_length=300)
    reference: str | None = Field(None, max_length=120)


@router.post("/transfers", status_code=201)
def transfer(data: TransferIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    """Entre cuentas propias (deposito de caja al banco, compra de dolares): sale de una y entra en la otra."""
    src, dst = _account(db, data.from_account_id, p.tenant.id), _account(db, data.to_account_id, p.tenant.id)
    if src.id == dst.id:
        raise HTTPException(422, "Elegí dos cuentas distintas")
    if src.currency != dst.currency and data.amount_to is None:
        raise HTTPException(422, f"Las cuentas son de distinta divisa: indicá cuánto entra en {dst.currency}")
    amount_to = data.amount_to if src.currency != dst.currency else data.amount
    group = "trf-" + secrets.token_hex(6)
    desc = (data.description or f"Transferencia {src.name} → {dst.name}")[:300]
    out = []
    for acct, amt in ((src, -bb.q2(data.amount)), (dst, bb.q2(amount_to))):
        m = BankMovement(
            tenant_id=p.tenant.id,
            bank_account_id=acct.id,
            date=data.date,
            amount=amt,
            kind="transferencia",
            description=desc,
            reference=data.reference,
            transfer_group=group,
            status="registrado",
            created_by=p.user.id,
        )
        db.add(m)
        out.append(m)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "transfer", "bank_movement", out[0].id, {"group": group, "from": src.id, "to": dst.id, "amount": str(data.amount)}, ip=p.ip)
    db.commit()
    return [_movement_out(m) for m in out]


@router.post("/movements/{mid}/void")
def movement_void(mid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Anular (las dos patas si es transferencia). No se puede si alguna esta conciliada con el banco."""
    m = db.get(BankMovement, mid)
    if not m or m.tenant_id != p.tenant.id:
        raise HTTPException(404, "Movimiento no encontrado")
    if m.status == "anulado":
        raise HTTPException(409, "Ya está anulado")
    group = [m]
    if m.transfer_group:
        group = list(db.scalars(select(BankMovement).where(BankMovement.tenant_id == p.tenant.id, BankMovement.transfer_group == m.transfer_group)))
    matched = bb.matched_keys(db, p.tenant.id)
    if any(("movement", x.id) in matched for x in group):
        raise HTTPException(409, "Está conciliado con el banco: primero deshacé la conciliación")
    for x in group:
        x.status = "anulado"
    audit(db, p.tenant.id, p.user.id, "void", "bank_movement", m.id, {"ids": [x.id for x in group]}, ip=p.ip)
    db.commit()
    return [_movement_out(x) for x in group]


class MovementFromLineIn(BaseModel):
    kind: str = Field(pattern="^(deposito|interes|cargo|ajuste|otro)$")
    description: str | None = Field(None, max_length=300)


@router.post("/lines/{lid}/movement", status_code=201)
def movement_from_line(lid: int, data: MovementFromLineIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    """Intereses, cargos sin factura o ajustes que solo existen en el banco: se registran y quedan conciliados."""
    ln, a = _line(db, lid, p.tenant.id)
    if ln.status != "pendiente":
        raise HTTPException(409, "Solo líneas pendientes")
    m = BankMovement(
        tenant_id=p.tenant.id,
        bank_account_id=a.id,
        date=ln.date,
        amount=bb.q2(ln.amount),
        kind=data.kind,
        description=(data.description or ln.description)[:300],
        reference=ln.reference,
        status="registrado",
        created_by=p.user.id,
    )
    db.add(m)
    db.flush()
    ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "conciliado", "movement", m.id, "manual"
    audit(db, p.tenant.id, p.user.id, "movement_from_line", "bank_line", ln.id, {"movement_id": m.id}, ip=p.ip)
    db.commit()
    _, ent = bb.load_entry(db, p.tenant.id, "movement", m.id)
    return bb.line_out(ln, ent.out())


# ------------------------------------------------------------------ estados de cuenta


def _mapping(raw: str | None) -> dict | None:
    if not raw:
        return None
    if len(raw) > 4000:
        raise HTTPException(422, "Mapeo de columnas inválido")
    try:
        m = json.loads(raw)
    except ValueError as e:
        raise HTTPException(422, "Mapeo de columnas inválido") from e
    if not isinstance(m, dict):
        raise HTTPException(422, "Mapeo de columnas inválido")
    return m


async def _read(file: UploadFile) -> bytes:
    data = await file.read(bb.MAX_FILE_BYTES + 1)
    if len(data) > bb.MAX_FILE_BYTES:
        raise HTTPException(413, "Archivo muy grande (máximo 5 MB). Dividilo por mes o guardalo como CSV.")
    return data


def _row_out(r: dict) -> dict:
    return {
        "row": r["row"],
        "date": r["date"],
        "amount": _money(r["amount"]),
        "description": r["description"],
        "reference": r["reference"],
        "balance": _money(r["balance"]),
        "duplicate": r["duplicate"],
        "before_cutoff": r["before_cutoff"],
    }


@router.post("/accounts/{aid}/statements/preview")
async def statement_preview(
    aid: int,
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    p: Principal = Depends(require("accounting", "crear")),
    db: Session = Depends(get_db),
):
    """Lee el archivo y muestra lo que se guardaria (no guarda nada). `mapping` (JSON) corrige las columnas."""
    a = _account(db, aid, p.tenant.id)
    data = await _read(file)
    try:
        an = bb.analyze(db, a, data, file.filename or "", _mapping(mapping))
    except bb.StatementError as e:
        raise HTTPException(422, str(e)) from e
    return {
        **{k: an[k] for k in ("mapping", "columns", "header_rows", "errors", "skipped", "total", "new", "duplicates", "before_cutoff", "date_from", "date_to", "continuity")},
        "credits": _money(an["credits"]),
        "debits": _money(an["debits"]),
        "closing_balance": _money(an["closing_balance"]),
        "statement_opening": _money(an["statement_opening"]),
        "rows": [_row_out(r) for r in an["rows"][:500]],
        "rows_truncated": len(an["rows"]) > 500,
    }


@router.post("/accounts/{aid}/statements", status_code=201)
async def statement_import(
    aid: int,
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    p: Principal = Depends(require("accounting", "crear")),
    db: Session = Depends(get_db),
):
    """Guarda las lineas nuevas (las repetidas y las anteriores a la fecha de corte se omiten) y concilia lo seguro."""
    a = _account(db, aid, p.tenant.id)
    data = await _read(file)
    try:
        res = bb.save_statement(db, a, p.user.id, data, file.filename or "", _mapping(mapping))
    except bb.StatementError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    audit(db, p.tenant.id, p.user.id, "import", "bank_statement", a.id, {k: res[k] for k in ("import_id", "added", "duplicates", "matched")}, ip=p.ip)
    db.commit()
    return {**res, "closing_balance": _money(res["closing_balance"])}


@router.get("/accounts/{aid}/statements")
def statements(aid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    a = _account(db, aid, p.tenant.id)
    q = select(BankStatementImport).where(BankStatementImport.tenant_id == p.tenant.id, BankStatementImport.bank_account_id == a.id)
    return [
        {
            "id": i.id,
            "filename": i.filename,
            "date_from": i.date_from,
            "date_to": i.date_to,
            "rows": i.rows,
            "added": i.added,
            "duplicates": i.duplicates,
            "closing_balance": _money(i.closing_balance),
            "created_at": i.created_at,
        }
        for i in db.scalars(q.order_by(BankStatementImport.id.desc()).limit(200))
    ]


@router.delete("/statements/{iid}")
def statement_undo(iid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Deshacer una importacion (archivo equivocado o de otra cuenta). Si alguna linea ya esta conciliada, se pide
    deshacer esas conciliaciones primero."""
    imp = db.get(BankStatementImport, iid)
    if not imp or imp.tenant_id != p.tenant.id:
        raise HTTPException(404, "Importación no encontrada")
    lines_ = db.scalars(select(BankStatementLine).where(BankStatementLine.tenant_id == p.tenant.id, BankStatementLine.import_id == imp.id)).all()
    conc = sum(1 for ln in lines_ if ln.status == "conciliado")
    if conc:
        raise HTTPException(409, f"{conc} línea(s) de este archivo ya están conciliadas: deshacé esas conciliaciones primero")
    for ln in lines_:
        db.delete(ln)
    audit(db, p.tenant.id, p.user.id, "undo_import", "bank_statement", imp.bank_account_id, {"import_id": imp.id, "lines": len(lines_), "file": imp.filename}, ip=p.ip)
    db.delete(imp)
    db.commit()
    return {"deleted": len(lines_)}
