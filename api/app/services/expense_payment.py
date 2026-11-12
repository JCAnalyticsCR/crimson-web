"""Pago de facturas de proveedor: «Marcar pagada», «Desmarcar» y estado de pago (reunion con la contadora, 10/11).

Reglas
------
* La condicion contado/credito del XML NO decide si esta pagado (el emisor a veces se equivoca). Un gasto queda
  pendiente hasta que alguien confirma el pago con fecha, medio, cuenta o tarjeta y referencia; la condicion del XML
  se guarda como pista (expense.xml_condition) y define el vencimiento si dice credito (fecha + PlazoCredito).
* Corte con Fygaro: CORTE (1/11/2026). Lo anterior no es "pendiente" (estado "anterior"): no inunda cuentas por pagar.
  Igual se puede marcar pagado a mano.
* Marcar pagada = un SupplierPayment por factura con su aplicacion por el saldo (services/journal lo convierte en el
  asiento DR CxP / CR banco por el camino que ya existia). El gasto guarda copia del pago para la lista y la
  conciliacion: paid_date, payment_method, bank_account_id, bank_instrument_id, check_number, payment_reference.
* Desmarcar = anular ese pago (asiento reverso si ya se exporto) con motivo en la bitacora. No se deshace aqui lo que
  pertenece a otro modulo (planilla, comision, gasto creado desde el extracto) ni un pago conciliado con el banco.
* Moneda: la cuenta de la que salio el pago tiene que estar en la moneda de la factura (el motor de asientos no
  convierte entre monedas dentro de un mismo asiento).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..core.timeutil import cr_today
from ..models import (
    BankAccount,
    BankInstrument,
    BankStatementLine,
    Expense,
    ReceivedDocument,
    Supplier,
    SupplierPayment,
    SupplierPaymentApplication,
)
from .bitacora import audit_change, snapshot
from .documents import audit

CORTE = date(2026, 11, 1)  # corte con Fygaro: lo anterior no aparece como pendiente
METHODS = ("transferencia", "sinpe", "efectivo", "tarjeta", "cheque")
METHOD_LABEL = {"transferencia": "Transferencia", "sinpe": "SINPE", "efectivo": "Efectivo", "tarjeta": "Tarjeta", "cheque": "Cheque"}
CENT = Decimal("0.005")
# codigos de CondicionVenta (Hacienda 4.4) que son venta a credito
_CREDITO = {"02", "08", "10"}
BUCKETS = ("corriente", "d1_30", "d31_60", "d61_90", "d90_mas")


def d(v) -> Decimal:
    return Decimal(str(v or 0))


# ---------------------------------------------------------------- estado de pago

def pay_state(e: Expense) -> str:
    """anulado | pagado | anterior (registrado antes del corte) | pendiente."""
    if e.status == "anulado":
        return "anulado"
    if e.status == "pagado":
        return "pagado"
    if e.date and e.date < CORTE:
        return "anterior"
    return "pendiente"


def pay_state_where(state: str):
    """Condicion SQL equivalente a pay_state(e) == state."""
    if state == "pagado":
        return Expense.status == "pagado"
    if state == "anulado":
        return Expense.status == "anulado"
    if state == "anterior":
        return and_(Expense.status == "registrado", Expense.date < CORTE)
    if state == "pendiente":
        return and_(Expense.status == "registrado", Expense.date >= CORTE)
    raise HTTPException(422, "Estado de pago inválido")


def applied_map(db: Session, expense_ids: list[int]) -> dict[int, Decimal]:
    """Monto aplicado (pagos no anulados) por gasto, en la moneda del gasto."""
    if not expense_ids:
        return {}
    rows = db.execute(
        select(SupplierPaymentApplication.expense_id, func.sum(SupplierPaymentApplication.amount))
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentApplication.payment_id)
        .where(SupplierPaymentApplication.expense_id.in_(expense_ids), SupplierPayment.status == "registrado")
        .group_by(SupplierPaymentApplication.expense_id)
    ).all()
    return {r[0]: d(r[1]) for r in rows}


def balance(e: Expense, applied: dict[int, Decimal]) -> Decimal:
    return max(Decimal(0), d(e.total) - applied.get(e.id, Decimal(0)))


def pending_query(tenant_id: int):
    """Gastos pendientes de pago: registrados desde el corte, con monto positivo (una NC recibida no se paga)."""
    return select(Expense).where(Expense.tenant_id == tenant_id, pay_state_where("pendiente"), Expense.total > 0)


def due_of(e: Expense) -> date:
    """Vencimiento: el del gasto; sin el (contado), la fecha de la factura."""
    return e.due_date or e.date


def bucket_of(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "corriente"
    if days_overdue <= 30:
        return "d1_30"
    if days_overdue <= 60:
        return "d31_60"
    if days_overdue <= 90:
        return "d61_90"
    return "d90_mas"


# ---------------------------------------------------------------- pista del XML

def xml_terms(xml: str | bytes | None) -> tuple[str | None, int]:
    """(condicion, dias de plazo) de un comprobante: contado | credito | otro. None si no se puede leer."""
    if not xml:
        return None, 0
    from ..providers.hacienda import xml44

    try:
        root = xml44.parse_xml_bytes(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except Exception:  # noqa: BLE001 - una pista ilegible no frena la recepcion
        return None, 0
    code = (xml44.text(root, "CondicionVenta") or "").strip()
    if not code:
        return None, 0
    code = code.zfill(2)
    try:
        dias = max(0, min(int((xml44.text(root, "PlazoCredito") or "0").strip() or 0), 3650))
    except ValueError:
        dias = 0
    return ("credito" if code in _CREDITO else "contado" if code == "01" else "otro"), dias


def apply_xml_terms(e: Expense, doc: ReceivedDocument) -> None:
    """Gasto recibido por correo: la condicion del XML queda como pista; si dice credito, el gasto es de credito y vence
    a la fecha + PlazoCredito. Nunca lo marca pagado: queda pendiente hasta que alguien confirme el pago."""
    cond, dias = xml_terms(doc.xml_document)
    e.xml_condition = cond
    if cond == "credito":
        e.purchase_type = "credito"
        if e.due_date is None and e.date:
            e.due_date = e.date + timedelta(days=dias)


def hint(e: Expense) -> str | None:
    if e.xml_condition == "contado":
        return "Contado según XML"
    if e.xml_condition == "credito":
        return "Crédito según XML"
    if e.xml_condition == "otro":
        return "Otra condición según XML"
    return None


# ---------------------------------------------------------------- marcar / desmarcar

def _bank(db: Session, tid: int, bank_account_id: int | None) -> BankAccount | None:
    if not bank_account_id:
        return None
    ba = db.get(BankAccount, bank_account_id)
    if ba is None or ba.tenant_id != tid:
        raise HTTPException(404, "Cuenta bancaria no encontrada")
    if not ba.active:
        raise HTTPException(422, f"La cuenta {ba.name} está inactiva")
    return ba


def _instrument(db: Session, tid: int, instrument_id: int | None, ba: BankAccount | None, method: str) -> BankInstrument | None:
    if not instrument_id:
        return None
    ins = db.get(BankInstrument, instrument_id)
    if ins is None or ins.tenant_id != tid:
        raise HTTPException(404, "Chequera o tarjeta no encontrada")
    if ba is None or ins.bank_account_id != ba.id:
        raise HTTPException(422, "La chequera o tarjeta no es de la cuenta indicada")
    if (method == "cheque" and ins.kind != "chequera") or (method == "tarjeta" and ins.kind != "tarjeta_debito"):
        raise HTTPException(422, "La chequera o tarjeta no corresponde al medio de pago")
    if method not in ("cheque", "tarjeta"):
        raise HTTPException(422, "Chequera o tarjeta solo con pago por cheque o tarjeta")
    return ins


def _owned_expenses(db: Session, tid: int, ids: list[int]) -> list[Expense]:
    rows = {e.id: e for e in db.scalars(select(Expense).where(Expense.tenant_id == tid, Expense.id.in_(ids)))}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise HTTPException(404, f"Gasto {missing[0]} no encontrado")
    return [rows[i] for i in ids]


def mark_paid(
    db: Session,
    p,
    expense_ids: list[int],
    *,
    paid_date: date,
    method: str,
    bank_account_id: int | None,
    bank_instrument_id: int | None = None,
    check_number: str | None = None,
    reference: str | None = None,
    note: str | None = None,
    before_invoice_ok: bool = False,
) -> tuple[list[Expense], list[str]]:
    """Marca pagadas una o varias facturas con el mismo pago (fecha, medio, cuenta, referencia). Todo o nada: si una
    falla la validacion no se marca ninguna. Devuelve (gastos, avisos). No hace commit."""
    tid = p.tenant.id
    ids = list(dict.fromkeys(expense_ids))
    if not ids:
        raise HTTPException(422, "Elegí al menos una factura")
    if method not in METHODS:
        raise HTTPException(422, f"Medio de pago inválido; use: {', '.join(METHODS)}")
    if paid_date > cr_today():
        raise HTTPException(422, "La fecha de pago no puede ser futura")
    reference = (reference or "").strip() or None
    check_number = (check_number or "").strip() or None
    if method == "cheque":
        if not (check_number or reference):
            raise HTTPException(422, "Indicá el número de cheque")
        reference = reference or check_number
    elif method in ("transferencia", "sinpe") and not reference:
        raise HTTPException(422, "Indicá el número de referencia del banco: es el que tiene que coincidir con el estado de cuenta")
    if method != "efectivo" and not bank_account_id:
        raise HTTPException(422, "Indicá la cuenta bancaria o tarjeta de la que salió el pago")
    ba = _bank(db, tid, bank_account_id)
    ins = _instrument(db, tid, bank_instrument_id, ba, method)

    expenses = _owned_expenses(db, tid, ids)
    applied = applied_map(db, ids)
    warnings: list[str] = []
    antes_factura: list[Expense] = []
    for e in expenses:
        if e.status == "anulado":
            raise HTTPException(409, f"«{e.description}» está anulado")
        if e.status == "pagado":
            raise HTTPException(409, f"«{e.description}» ya está pagado")
        if d(e.total) <= 0:
            raise HTTPException(422, f"«{e.description}» es una nota de crédito o no tiene monto: no se paga")
        if ba is not None and (ba.currency or "CRC") != (e.currency or "CRC"):
            raise HTTPException(
                422,
                f"La cuenta {ba.name} es en {ba.currency} y «{e.description}» en {e.currency}: registrá el pago desde una cuenta en {e.currency}",
            )
        if balance(e, applied) <= CENT:
            raise HTTPException(409, f"«{e.description}» no tiene saldo pendiente")
        if paid_date < e.date:
            antes_factura.append(e)
    if antes_factura:
        lista = ", ".join(f"«{e.description}» ({e.date.strftime('%d/%m/%Y')})" for e in antes_factura[:3])
        if not before_invoice_ok:
            raise HTTPException(409, f"La fecha de pago es anterior a la factura: {lista}. Confirmá que fue un adelanto para registrarla igual")
        warnings.append(f"Pago anterior a la factura (adelanto): {lista}")

    from .fx import for_date

    for e in expenses:
        antes = snapshot(e)
        monto = balance(e, applied)
        fx_rate, aviso = for_date(db, e.currency, paid_date)
        if aviso:
            warnings.append(aviso)
        pay = SupplierPayment(
            tenant_id=tid,
            supplier_id=e.supplier_id,
            date=paid_date,
            amount=monto,
            currency=e.currency or "CRC",
            fx_rate=fx_rate,
            method=method,
            bank_account_id=ba.id if ba else None,
            reference=reference,
            note=note,
            status="registrado",
            created_by=p.user.id,
        )
        db.add(pay)
        db.flush()
        db.add(SupplierPaymentApplication(payment_id=pay.id, expense_id=e.id, amount=monto))
        e.status = "pagado"
        e.paid_date = paid_date
        e.payment_method = method
        e.bank_account_id = ba.id if ba else None
        e.bank_instrument_id = ins.id if ins else None
        e.check_number = check_number if method == "cheque" else None
        e.payment_reference = reference
        if ins is not None and ins.kind == "chequera" and check_number and check_number.isdigit():
            n = int(check_number)
            if ins.next_check is None or n >= ins.next_check:
                ins.next_check = n + 1
        db.flush()
        sup = db.get(Supplier, e.supplier_id) if e.supplier_id else None
        motivo = f"Marcada pagada: {METHOD_LABEL[method]} del {paid_date.strftime('%d/%m/%Y')}" + (f" · ref. {reference}" if reference else "")
        audit_change(db, p, "update", "expense", e.id, antes, snapshot(e), reason=motivo, company=sup.name if sup else None)
        audit(db, tid, p.user.id, "create", "supplier_payment", pay.id, {"expense_id": e.id, "amount": str(monto), "method": method}, ip=p.ip)
    return expenses, warnings


def _conciliated(db: Session, tid: int, kind: str, oid: int) -> bool:
    return (
        db.scalar(
            select(BankStatementLine.id).where(
                BankStatementLine.tenant_id == tid,
                BankStatementLine.matched_type == kind,
                BankStatementLine.matched_id == oid,
                BankStatementLine.status == "conciliado",
            )
        )
        is not None
    )


def unmark_paid(db: Session, p, e: Expense, reason: str) -> Expense:
    """Deshace «Marcar pagada»: anula el pago (asiento reverso si ya se exporto) y el gasto vuelve a pendiente. El
    motivo queda en la bitacora del gasto y del pago. No hace commit."""
    from .commission_expense import is_commission_ref

    tid = p.tenant.id
    if e.status != "pagado":
        raise HTTPException(409, "El gasto no está marcado como pagado")
    if is_commission_ref(e.reference) and not e.received_document_id:
        raise HTTPException(409, "Este gasto lo registró Comisiones: el pago se deshace desde Comisiones")
    if (e.reference or "").startswith("PLANILLA-"):
        raise HTTPException(409, "Este gasto lo registró Planillas: el pago se deshace desde Planillas")
    if _conciliated(db, tid, "expense", e.id):
        raise HTTPException(409, "El gasto está conciliado con el estado de cuenta: deshacé la conciliación primero")
    pays = list(
        db.scalars(
            select(SupplierPayment)
            .join(SupplierPaymentApplication, SupplierPaymentApplication.payment_id == SupplierPayment.id)
            .where(SupplierPayment.tenant_id == tid, SupplierPaymentApplication.expense_id == e.id, SupplierPayment.status == "registrado")
            .distinct()
        )
    )
    for pay in pays:
        if any(a.expense_id != e.id for a in pay.applications):
            raise HTTPException(409, "Ese pago cubre también otras facturas: anulalo desde el estado de cuenta del proveedor (Cuentas por pagar)")
        if _conciliated(db, tid, "supplier_payment", pay.id):
            raise HTTPException(409, "El pago está conciliado con el estado de cuenta: deshacé la conciliación primero")
    antes = snapshot(e)
    for pay in pays:
        antes_pay = snapshot(pay)
        pay.status = "anulado"
        pay.void_reason = reason[:300]
        pay.voided_by = p.user.id
        pay.voided_at = datetime.now(UTC)
        audit_change(db, p, "update", "supplier_payment", pay.id, antes_pay, snapshot(pay), reason=reason)
    e.status = "registrado"
    e.paid_date = None
    e.payment_reference = None
    e.bank_account_id = None
    e.bank_instrument_id = None
    e.check_number = None
    db.flush()
    sup = db.get(Supplier, e.supplier_id) if e.supplier_id else None
    audit_change(db, p, "update", "expense", e.id, antes, snapshot(e), reason=f"Pago desmarcado: {reason}", company=sup.name if sup else None)
    return e


def has_active_payment(db: Session, expense_id: int) -> bool:
    return (
        db.scalar(
            select(SupplierPaymentApplication.id)
            .join(SupplierPayment, SupplierPayment.id == SupplierPaymentApplication.payment_id)
            .where(SupplierPaymentApplication.expense_id == expense_id, SupplierPayment.status == "registrado")
            .limit(1)
        )
        is not None
    )


# ---------------------------------------------------------------- cuentas por pagar

def pending_rows(db: Session, tid: int, as_of: date, supplier_id: int | None = None, currency: str | None = None) -> list[dict]:
    """Facturas pendientes de pago (desde el corte) con saldo, vencimiento y antiguedad al `as_of`."""
    q = pending_query(tid)
    if supplier_id:
        q = q.where(Expense.supplier_id == supplier_id)
    if currency:
        q = q.where(Expense.currency == currency)
    rows = list(db.scalars(q.order_by(func.coalesce(Expense.due_date, Expense.date), Expense.id)))
    applied = applied_map(db, [e.id for e in rows])
    sids = {e.supplier_id for e in rows if e.supplier_id}
    sups = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.tenant_id == tid, Supplier.id.in_(sids)))} if sids else {}
    out = []
    for e in rows:
        bal = balance(e, applied)
        if bal <= CENT:
            continue
        due = due_of(e)
        days = (as_of - due).days
        out.append(
            {
                "expense_id": e.id,
                "supplier_id": e.supplier_id,
                "supplier_name": sups.get(e.supplier_id) or "(sin proveedor)",
                "description": e.description,
                "reference": e.reference,
                "date": e.date,
                "due_date": due,
                "has_due_date": e.due_date is not None,
                "days_overdue": max(days, 0),
                "age_days": (as_of - e.date).days,
                "bucket": bucket_of(days),
                "currency": e.currency or "CRC",
                "total": float(d(e.total)),
                "applied": float(applied.get(e.id, Decimal(0))),
                "balance": float(bal),
                "purchase_type": e.purchase_type,
                "xml_condition": e.xml_condition,
                "hint": hint(e),
                "received_document_id": e.received_document_id,
            }
        )
    return out


def totals_by_currency(rows: list[dict]) -> dict[str, dict]:
    tot: dict[str, dict] = {}
    for r in rows:
        t = tot.setdefault(r["currency"], {**{b: Decimal(0) for b in BUCKETS}, "total": Decimal(0), "vencido": Decimal(0), "count": 0})
        bal = d(r["balance"])
        t[r["bucket"]] += bal
        t["total"] += bal
        if r["bucket"] != "corriente":
            t["vencido"] += bal
        t["count"] += 1
    return {cur: {k: (float(v) if isinstance(v, Decimal) else v) for k, v in t.items()} for cur, t in sorted(tot.items())}
