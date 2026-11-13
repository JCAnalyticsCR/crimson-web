"""Libro de bancos y conciliacion (reunion con la contadora, 10/11/2026).

Cada cuenta (BAC colones, cuenta en dolares, caja) arranca con su saldo inicial a la fecha de corte
(BankAccount.opening_balance / opening_date; la empresa arranco en la plataforma el 1/11/2026). Desde ahi el saldo
segun Crimson se mueve con:

- cobros (Payment confirmado: captura suma; devolucion y reembolso restan; la propina tambien entra),
- gastos pagados (Expense "pagado"; fecha real del pago si existe Expense.paid_date). Los gastos de credito que se
  pagaron con un pago a proveedor NO cuentan: cuenta el pago a proveedor (si no, saldrian dos veces),
- pagos a proveedor (SupplierPayment registrado),
- planilla: su gasto guarda el costo (bruto + CCSS patronal) pero del banco sale el NETO a los colaboradores; las
  cargas y la renta se pagan aparte (otro gasto). Por eso aqui la planilla cuenta por su neto,
- comisiones pagadas (su gasto automatico COMISION-, cuando tiene cuenta),
- movimientos propios de la cuenta (BankMovement: transferencias entre cuentas, intereses, cargos, ajustes).

Solo cuenta lo que esta en la divisa de la cuenta y asignado a ella. La unica caja activa de una divisa recibe ademas
lo pagado o cobrado en efectivo sin cuenta asignada.

El saldo segun el banco sale del estado de cuenta importado (saldo inicial + lineas desde la fecha de corte) y, si el
archivo trae la columna de saldo, tambien se muestra el que reporta el banco.

Conciliar = casar cada linea del estado de cuenta con un movimiento de Crimson: mismo monto, fecha cercana y, si hay,
la misma referencia. Automatico solo con monto exacto, +/-3 dias y sin empates; lo demas a mano.

No es contabilidad completa: es control de saldos y conciliacion. Decimal siempre.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import (
    BankAccount,
    BankMovement,
    BankStatementImport,
    BankStatementLine,
    Customer,
    Expense,
    Invoice,
    Payment,
    PayrollRun,
    Supplier,
    SupplierPayment,
    SupplierPaymentApplication,
)
from . import tabular as tb

CENT = Decimal("0.01")
AUTO_DAYS = 3  # conciliacion automatica: fecha +/-3 dias
CANDIDATE_DAYS = 45  # sugerencias para casar a mano
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_STATEMENT_ROWS = 5000
MAX_ABS_AMOUNT = Decimal("99999999999999.99")  # cabe en Numeric(16, 2)
TYPES = ("payment", "expense", "supplier_payment", "movement")
MOVEMENT_KINDS = ("transferencia", "deposito", "interes", "cargo", "ajuste", "otro")
MOVEMENT_LABEL = {
    "transferencia": "Transferencia",
    "deposito": "Depósito",
    "interes": "Intereses",
    "cargo": "Cargo bancario",
    "ajuste": "Ajuste",
    "otro": "Movimiento",
}


class StatementError(ValueError):
    """Error del archivo o del mapeo, con un mensaje para la persona."""


def q2(x) -> Decimal:
    return (x if isinstance(x, Decimal) else Decimal(str(x or 0))).quantize(CENT)


# ------------------------------------------------------------------ movimientos segun Crimson


@dataclass
class Entry:
    type: str  # payment | expense | supplier_payment | movement
    id: int
    date: date
    amount: Decimal  # + entra a la cuenta, - sale
    label: str  # Cobro, Gasto, Pago a proveedor, Planilla (neto), Comision, Transferencia...
    document: str | None
    counterparty: str | None
    reference: str | None
    to: str | None  # enlace del portal
    bank_account_id: int | None
    currency: str

    @property
    def key(self) -> tuple[str, int]:
        return (self.type, self.id)

    def out(self) -> dict:
        o = asdict(self)
        o["amount"] = str(q2(self.amount))
        return o


def eff_expense_date(e: Expense) -> date:
    return getattr(e, "paid_date", None) or e.date


def default_cash_id(db: Session, tenant_id: int, currency: str) -> int | None:
    """La caja que recibe el efectivo sin cuenta: solo si hay UNA caja activa en esa divisa (con dos, nadie adivina)."""
    ids = db.scalars(
        select(BankAccount.id).where(BankAccount.tenant_id == tenant_id, BankAccount.kind == "caja", BankAccount.active, BankAccount.currency == currency)
    ).all()
    return ids[0] if len(ids) == 1 else None


def _names(db: Session, model, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(model.id, model.name).where(model.id.in_(ids))).all())


def _payment_entries(db: Session, pays: list[Payment]) -> list[Entry]:
    invs = {i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_({p.invoice_id for p in pays})))} if pays else {}
    custs = _names(db, Customer, {i.customer_id for i in invs.values() if i.customer_id})
    out = []
    for p in pays:
        sign = Decimal(-1) if p.kind in ("devolucion", "reembolso") else Decimal(1)
        inv = invs.get(p.invoice_id)
        total = abs(q2(p.amount)) + abs(q2(p.tip))
        label = {"devolucion": "Devolución", "reembolso": "Reembolso"}.get(p.kind, "Cobro")
        out.append(
            Entry(
                type="payment",
                id=p.id,
                date=p.paid_at,
                amount=sign * total,
                label=f"{label} · {p.method}",
                document=inv.number if inv else None,
                counterparty=custs.get(inv.customer_id) if inv and inv.customer_id else None,
                reference=p.external_ref,
                to=f"/facturas/{p.invoice_id}",
                bank_account_id=p.bank_account_id,
                currency=p.currency,
            )
        )
    return out


def _expense_entries(db: Session, exps: list[Expense]) -> list[Entry]:
    sups = _names(db, Supplier, {e.supplier_id for e in exps if e.supplier_id})
    runs = {r.expense_id: r for r in db.scalars(select(PayrollRun).where(PayrollRun.expense_id.in_({e.id for e in exps})))} if exps else {}
    out = []
    for e in exps:
        run = runs.get(e.id)
        ref = (e.reference or "").upper()
        if run is not None:
            amount, label = -q2(run.net), "Planilla (neto pagado)"
            doc = f"Planilla {run.period_start.strftime('%d/%m')}–{run.period_end.strftime('%d/%m/%Y')}"
            to = "/planillas"
        else:
            amount = -q2(e.total)
            label = "Comisión" if ref.startswith("COMISION-") else "Gasto"
            doc, to = e.description, f"/contabilidad/contable?expense={e.id}"
        out.append(
            Entry(
                type="expense",
                id=e.id,
                date=eff_expense_date(e),
                amount=amount,
                label=label,
                document=doc,
                counterparty=sups.get(e.supplier_id) if e.supplier_id else None,
                reference=e.reference or e.check_number,
                to=to,
                bank_account_id=e.bank_account_id,
                currency=e.currency,
            )
        )
    return out


def _supplier_payment_entries(db: Session, sps: list[SupplierPayment]) -> list[Entry]:
    sups = _names(db, Supplier, {s.supplier_id for s in sps})
    return [
        Entry(
            type="supplier_payment",
            id=s.id,
            date=s.date,
            amount=-q2(s.amount),
            label=f"Pago a proveedor · {s.method}",
            document=None,
            counterparty=sups.get(s.supplier_id),
            reference=s.reference,
            to=f"/contabilidad/contable/cuentas-por-pagar?supplier={s.supplier_id}",
            bank_account_id=s.bank_account_id,
            currency=s.currency,
        )
        for s in sps
    ]


def _movement_entries(db: Session, mvs: list[BankMovement], currency: dict[int, str]) -> list[Entry]:
    return [
        Entry(
            type="movement",
            id=m.id,
            date=m.date,
            amount=q2(m.amount),
            label=MOVEMENT_LABEL.get(m.kind, "Movimiento"),
            document=m.description,
            counterparty=None,
            reference=m.reference,
            to=None,
            bank_account_id=m.bank_account_id,
            currency=currency.get(m.bank_account_id, ""),
        )
        for m in mvs
    ]


def _applied_expense_ids(tenant_id: int):
    """Gastos cubiertos por un pago a proveedor vigente: sale el pago, no el gasto."""
    return (
        select(SupplierPaymentApplication.expense_id)
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentApplication.payment_id)
        .where(SupplierPayment.tenant_id == tenant_id, SupplierPayment.status == "registrado")
    )


def _owner(col, acct: BankAccount, cash_default: bool, method_col):
    """Asignado a la cuenta; y si es la caja por defecto, tambien el efectivo sin cuenta."""
    if cash_default:
        return or_(col == acct.id, and_(col.is_(None), method_col == "efectivo"))
    return col == acct.id


def ledger_entries(db: Session, acct: BankAccount, to: date | None = None) -> tuple[list[Entry], list[str]]:
    """Movimientos de la cuenta desde la fecha de corte (hasta `to`), en orden. Devuelve tambien avisos."""
    tid, cur, cut = acct.tenant_id, acct.currency, acct.opening_date
    cash = acct.kind == "caja" and default_cash_id(db, tid, cur) == acct.id
    warnings: list[str] = []

    pq = select(Payment).where(
        Payment.tenant_id == tid,
        _owner(Payment.bank_account_id, acct, cash, Payment.method),
        Payment.status == "confirmado",
        Payment.kind.in_(("captura", "devolucion", "reembolso")),
    )
    eq = select(Expense).where(
        Expense.tenant_id == tid,
        _owner(Expense.bank_account_id, acct, cash, Expense.payment_method),
        Expense.status == "pagado",
        Expense.id.not_in(_applied_expense_ids(tid)),
    )
    sq = select(SupplierPayment).where(
        SupplierPayment.tenant_id == tid, _owner(SupplierPayment.bank_account_id, acct, cash, SupplierPayment.method), SupplierPayment.status == "registrado"
    )
    mq = select(BankMovement).where(BankMovement.tenant_id == tid, BankMovement.bank_account_id == acct.id, BankMovement.status == "registrado")
    if cut:
        pq, sq, mq = pq.where(Payment.paid_at >= cut), sq.where(SupplierPayment.date >= cut), mq.where(BankMovement.date >= cut)
        paid_col = getattr(Expense, "paid_date", None)
        eq = eq.where(or_(Expense.date >= cut, paid_col >= cut)) if paid_col is not None else eq.where(Expense.date >= cut)
    entries = (
        _payment_entries(db, list(db.scalars(pq)))
        + _expense_entries(db, list(db.scalars(eq)))
        + _supplier_payment_entries(db, list(db.scalars(sq)))
        + _movement_entries(db, list(db.scalars(mq)), {acct.id: cur})
    )
    other = [e for e in entries if e.currency != cur and e.bank_account_id == acct.id]
    if other:
        warnings.append(f"{len(other)} movimiento(s) asignados a esta cuenta están en otra divisa y no suman al saldo: revisá la cuenta elegida.")
    entries = [e for e in entries if e.currency == cur and (not cut or e.date >= cut) and (to is None or e.date <= to)]
    entries.sort(key=lambda e: (e.date, 0 if e.amount > 0 else 1, TYPES.index(e.type), e.id))
    return entries, warnings


def matched_keys(db: Session, tenant_id: int) -> dict[tuple[str, int], int]:
    """(tipo, id) de Crimson -> linea del estado de cuenta que lo concilia."""
    rows = db.execute(
        select(BankStatementLine.matched_type, BankStatementLine.matched_id, BankStatementLine.id).where(
            BankStatementLine.tenant_id == tenant_id, BankStatementLine.status == "conciliado", BankStatementLine.matched_id.is_not(None)
        )
    ).all()
    return {(t, i): lid for t, i, lid in rows}


def book_ledger(db: Session, acct: BankAccount, date_from: date | None = None, date_to: date | None = None) -> dict:
    """Libro de la cuenta con saldo corrido. Con rango: saldo al inicio del rango, movimientos del rango y saldo final."""
    entries, warnings = ledger_entries(db, acct, date_to)
    matched = matched_keys(db, acct.tenant_id)
    bal = q2(acct.opening_balance)
    start = bal
    rows = []
    for e in entries:
        bal += e.amount
        if date_from and e.date < date_from:
            start = bal
            continue
        rows.append({**e.out(), "balance": str(bal), "line_id": matched.get(e.key)})
    if not acct.opening_date:
        warnings.insert(0, "Definí la fecha de corte y el saldo inicial: sin fecha de corte cuenta todo lo registrado.")
    return {
        "opening_balance": str(q2(acct.opening_balance)),
        "opening_date": acct.opening_date,
        "period_start_balance": str(start),
        "closing_balance": str(bal),
        "inflows": str(sum((Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) > 0), Decimal(0))),
        "outflows": str(sum((Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) < 0), Decimal(0))),
        "entries": rows,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ saldo segun el banco


def _lines_q(acct: BankAccount, to: date | None = None):
    q = select(BankStatementLine).where(BankStatementLine.tenant_id == acct.tenant_id, BankStatementLine.bank_account_id == acct.id)
    if acct.opening_date:
        q = q.where(BankStatementLine.date >= acct.opening_date)
    if to:
        q = q.where(BankStatementLine.date <= to)
    return q


def bank_balance(db: Session, acct: BankAccount, to: date | None = None) -> tuple[Decimal, Decimal | None, date | None]:
    """(saldo calculado = inicial + lineas no ignoradas, saldo que reporta el banco en su ultima linea, fecha)."""
    lines = db.scalars(_lines_q(acct, to).order_by(BankStatementLine.date, BankStatementLine.id)).all()
    total = q2(acct.opening_balance) + sum((q2(ln.amount) for ln in lines if ln.status != "ignorado"), Decimal(0))
    last = lines[-1] if lines else None
    reported = None
    if last is not None:
        with_bal = [ln for ln in lines if ln.date == last.date and ln.balance is not None]
        reported = q2(with_bal[-1].balance) if with_bal else None
    return q2(total), reported, last.date if last else None


# ------------------------------------------------------------------ lectura del estado de cuenta

SYN: dict[str, tuple[str, ...]] = {
    "date": (
        "fecha",
        "fecha_movimiento",
        "fecha_de_movimiento",
        "fecha_contable",
        "fecha_transaccion",
        "fecha_de_transaccion",
        "fecha_valor",
        "fecha_mov",
        "f_contable",
        "date",
    ),
    "description": (
        "descripcion",
        "concepto",
        "detalle",
        "detalle_del_movimiento",
        "descripcion_del_movimiento",
        "movimiento",
        "description",
        "glosa",
    ),
    "reference": (
        "referencia",
        "documento",
        "numero_documento",
        "numero_de_documento",
        "num_documento",
        "no_documento",
        "n_documento",
        "comprobante",
        "numero_de_referencia",
        "no_referencia",
        "referencia_bancaria",
        "reference",
        "doc",
    ),
    "amount": ("monto", "importe", "monto_transaccion", "monto_del_movimiento", "amount", "valor", "monto_colones", "monto_dolares"),
    "credit": ("credito", "creditos", "abono", "abonos", "deposito", "depositos", "entrada", "entradas", "ingresos", "credit", "haber"),
    "debit": ("debito", "debitos", "cargo", "cargos", "retiro", "retiros", "salida", "salidas", "egresos", "debit", "debe"),
    "balance": ("saldo", "saldo_final", "saldo_disponible", "saldo_contable", "saldo_actual", "balance"),
    "type": ("tipo", "tipo_movimiento", "tipo_de_movimiento", "debito_credito", "d_c", "dc", "naturaleza"),
}
FIELDS = tuple(SYN)
MONTHS = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "ago": 8, "aug": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12,
}  # fmt: skip


def _norm(s: object) -> str:
    return tb.norm(s)


def _field_of(header: str) -> str | None:
    h = _norm(header)
    if not h:
        return None
    for f, syn in SYN.items():
        if h in syn:
            return f
    for f, syn in SYN.items():  # "debitos_crc", "fecha_de_movimiento_dd_mm"
        if any(h.startswith(s + "_") for s in syn if len(s) >= 4):
            return f
    return None


def read_statement_grid(data: bytes, filename: str) -> list[list]:
    """Celdas crudas del archivo con los topes del proyecto (tamano, filas, bombas XLSX en services/tabular)."""
    if len(data) > MAX_FILE_BYTES:
        raise StatementError("Archivo muy grande (máximo 5 MB). Dividilo por mes o guardalo como CSV.")
    if not data.strip():
        raise StatementError("El archivo está vacío.")
    name = (filename or "").lower()
    if data[:4] == b"\xd0\xcf\x11\xe0" or name.endswith(".xls"):
        raise StatementError("Excel viejo (.xls): abrilo y guardalo como .xlsx o CSV.")
    if name.endswith((".pdf", ".ofx", ".qif")) or data[:4] == b"%PDF":
        raise StatementError("Ese formato no se lee: descargá el estado de cuenta en Excel (.xlsx) o CSV.")
    try:
        return [r for _, r in tb.read_grid(data, filename, max_rows=MAX_STATEMENT_ROWS)]
    except tb.TableTooLarge as e:
        raise StatementError(f"Archivo muy grande: {e}") from e
    except StatementError:
        raise
    except Exception as e:  # noqa: BLE001  (csv/openpyxl dicen cosas tecnicas: se resume)
        raise StatementError(f"No se pudo leer el archivo ({type(e).__name__}). Probá guardarlo como CSV o .xlsx.") from e


def detect_mapping(grid: list[list]) -> dict:
    """Fila de encabezados (la que mas columnas conocidas tiene entre las primeras 25) y columna de cada campo."""
    best_i, best_score = None, 1
    for i, row in enumerate(grid[:25]):
        score = len({f for c in row if (f := _field_of(c)) is not None})
        if score > best_score:
            best_i, best_score = i, score
    if best_i is None:
        best_i = next((i for i, r in enumerate(grid[:15]) if sum(1 for c in r if isinstance(c, str) and c.strip()) >= 2), 0)
    mapping: dict = {"header_row": best_i, **{f: None for f in FIELDS}}
    for ci, c in enumerate(grid[best_i] if grid else []):
        f = _field_of(c)
        if f and mapping[f] is None:
            mapping[f] = ci
    if mapping["amount"] is not None and (mapping["debit"] is not None or mapping["credit"] is not None):
        mapping["amount"] = None  # debito y credito separados mandan sobre una columna "monto" generica
    mapping["date_order"] = _guess_date_order(grid[best_i + 1 :], mapping["date"])
    return mapping


def _date_parts(v) -> tuple[int, int, int] | None:
    s = str(v).strip().split(" ")[0].split("T")[0]
    m = re.fullmatch(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _guess_date_order(rows: list[list], col: int | None) -> str:
    if col is None:
        return "dmy"
    for r in rows[:300]:
        v = r[col] if col < len(r) else None
        if isinstance(v, (datetime, date)) or v is None:
            continue
        p = _date_parts(v)
        if not p:
            continue
        a, b, _ = p
        if a > 31:
            return "ymd"
        if a > 12:
            return "dmy"
        if b > 12:
            return "mdy"
    return "dmy"  # Costa Rica: dd/mm/aaaa


def parse_date(v, order: str = "dmy") -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.match(r"^(\d{1,2})[\s/\-.]+([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?[\s/\-.,]+(\d{2,4})", s)
    if m:
        mon = MONTHS.get(unicodedata.normalize("NFKD", m.group(2)).encode("ascii", "ignore").decode().lower()[:3])
        y = int(m.group(3))
        if mon:
            try:
                return date(y + 2000 if y < 100 else y, mon, int(m.group(1)))
            except ValueError:
                return None
    p = _date_parts(s)
    if not p:
        return None
    a, b, c = p
    if a > 31 or order == "ymd":
        y, mo, dd = a, b, c
    elif order == "mdy":
        mo, dd, y = a, b, c
    else:
        dd, mo, y = a, b, c
    if y < 100:
        y += 2000
    try:
        return date(y, mo, dd)
    except ValueError:
        return None


_DEBIT_WORDS = ("d", "db", "deb", "debito", "cargo", "retiro", "salida", "egreso", "-")
_CREDIT_WORDS = ("c", "cr", "cred", "credito", "abono", "deposito", "entrada", "ingreso", "+")


def _cell(row: list, col: int | None):
    if col is None or col >= len(row):
        return None
    v = row[col]
    return None if v is None or (isinstance(v, str) and not v.strip()) else v


def clean_mapping(raw: dict | None, grid: list[list]) -> dict:
    """Mapeo de la persona (o el detectado), validado contra las columnas que hay."""
    det = detect_mapping(grid)
    if not raw:
        m = det
    else:
        m = {"header_row": det["header_row"], **{f: None for f in FIELDS}, "date_order": det["date_order"]}
        hr = raw.get("header_row", det["header_row"])
        if not isinstance(hr, int) or hr < 0 or hr >= max(len(grid), 1):
            raise StatementError("La fila de encabezados no existe en el archivo.")
        m["header_row"] = hr
        width = max((len(r) for r in grid[: hr + 50]), default=0)
        for f in FIELDS:
            v = raw.get(f)
            if v in (None, "", -1):
                continue
            if not isinstance(v, int) or not 0 <= v < max(width, 1):
                raise StatementError(f"La columna elegida para «{f}» no existe en el archivo.")
            m[f] = v
        order = raw.get("date_order") or det["date_order"]
        if order not in ("dmy", "mdy", "ymd"):
            raise StatementError("Formato de fecha inválido (dd/mm/aaaa, mm/dd/aaaa o aaaa-mm-dd).")
        m["date_order"] = order
        used = [m[f] for f in FIELDS if m[f] is not None]
        if len(used) != len(set(used)):
            raise StatementError("Una misma columna no puede usarse para dos campos.")
    if m["date"] is None:
        raise StatementError("Elegí la columna de la fecha.")
    if m["amount"] is None and m["debit"] is None and m["credit"] is None:
        raise StatementError("Elegí la columna del monto (con signo) o las de débito y crédito.")
    return m


def parse_statement(grid: list[list], mapping: dict) -> dict:
    """Movimientos del archivo segun el mapeo. Lineas sin fecha (titulos, totales) se saltan; fechas o montos que no
    se entienden quedan como errores con su numero de fila para que la persona los vea en la vista previa."""
    rows, errors = [], []
    skipped = 0
    statement_opening = None
    order = mapping.get("date_order", "dmy")
    for i, r in enumerate(grid[mapping["header_row"] + 1 :], start=mapping["header_row"] + 2):
        raw_date = _cell(r, mapping["date"])
        desc = tb.text(_cell(r, mapping["description"])) if mapping["description"] is not None else None
        bal = tb.num(_cell(r, mapping["balance"])) if mapping["balance"] is not None else None
        if mapping["amount"] is not None:
            amt = tb.num(_cell(r, mapping["amount"]))
            t = _norm(_cell(r, mapping["type"])) if mapping["type"] is not None else ""
            if amt is not None and t:
                if t in _DEBIT_WORDS or t.startswith(("deb", "cargo", "retiro")):
                    amt = -abs(amt)
                elif t in _CREDIT_WORDS or t.startswith(("cred", "abono", "deposito")):
                    amt = abs(amt)
        else:
            cr, dbt = tb.num(_cell(r, mapping["credit"])), tb.num(_cell(r, mapping["debit"]))
            amt = None if cr is None and dbt is None else (cr or Decimal(0)) - abs(dbt or Decimal(0))
        if raw_date is None:
            skipped += 1  # titulo, total o linea de continuacion
            continue
        dt = parse_date(raw_date, order)
        if dt is None:
            if amt is None and bal is None:
                skipped += 1  # otro encabezado repetido o texto suelto
            else:
                errors.append({"row": i, "error": f"Fecha que no se entiende: «{str(raw_date)[:40]}»"})
            continue
        if amt is None or amt == 0:
            if bal is not None and desc and "saldo" in _norm(desc):
                statement_opening = q2(bal)  # "Saldo inicial / anterior" con fecha
            skipped += 1
            continue
        if abs(amt) > MAX_ABS_AMOUNT:
            errors.append({"row": i, "error": "Monto fuera de rango"})
            continue
        rows.append(
            {
                "row": i,
                "date": dt,
                "amount": q2(amt),
                "description": (desc or "Movimiento")[:300],
                "reference": tb.text(_cell(r, mapping["reference"]), 120) if mapping["reference"] is not None else None,
                "balance": q2(bal) if bal is not None else None,
            }
        )
    return {"rows": rows, "errors": errors, "skipped": skipped, "statement_opening": statement_opening}


def fingerprint_rows(acct_id: int, rows: list[dict]) -> list[str]:
    """Huella por linea (la misma que usa /banking/import: un archivo importado por cualquiera de las dos vias no se
    duplica). Dos lineas identicas en el mismo archivo se distinguen por su numero de aparicion."""
    seen: Counter = Counter()
    out = []
    for r in rows:
        base = f"{acct_id}|{r['date']}|{r['amount']}|{r['description']}|{r['reference'] or ''}"
        seen[base] += 1
        out.append(hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest())
    return out


def _chronological(rows: list[dict]) -> list[dict]:
    """Los bancos listan del mas nuevo al mas viejo o al reves: se ordena manteniendo el orden del dia."""
    if len(rows) > 1 and rows[0]["date"] > rows[-1]["date"]:
        return list(reversed(rows))
    return rows


def analyze(db: Session, acct: BankAccount, data: bytes, filename: str, raw_mapping: dict | None) -> dict:
    """Todo lo de la vista previa (no guarda nada)."""
    grid = read_statement_grid(data, filename)
    if not grid:
        raise StatementError("El archivo no tiene filas con datos.")
    mapping = clean_mapping(raw_mapping, grid)
    parsed = parse_statement(grid, mapping)
    rows = parsed["rows"]
    fps = fingerprint_rows(acct.id, rows)
    existing = set(
        db.scalars(select(BankStatementLine.fingerprint).where(BankStatementLine.tenant_id == acct.tenant_id, BankStatementLine.bank_account_id == acct.id))
    )
    cut = acct.opening_date
    for r, fp in zip(rows, fps, strict=True):
        r["fingerprint"] = fp
        r["duplicate"] = fp in existing
        r["before_cutoff"] = bool(cut and r["date"] < cut)
    chrono = _chronological(rows)
    closing = next((r["balance"] for r in reversed(chrono) if r["balance"] is not None), None)
    continuity = None
    first = next((r for r in chrono if not r["before_cutoff"]), None)
    if first is not None and first["balance"] is not None:
        file_start = q2(first["balance"] - first["amount"])
        expected, _, _ = bank_balance(db, acct, first["date"] - timedelta(days=1))
        continuity = {"date": first["date"], "file": str(file_start), "crimson": str(expected), "ok": file_start == expected}
    header = grid[mapping["header_row"]] if grid else []
    width = max((len(r) for r in grid[mapping["header_row"] : mapping["header_row"] + 50]), default=0)
    body = grid[mapping["header_row"] + 1 : mapping["header_row"] + 4]
    columns = [
        {
            "index": c,
            "header": tb.text(header[c], 80) if c < len(header) and header[c] is not None else f"Columna {c + 1}",
            "samples": [tb.text(r[c], 40) for r in body if c < len(r) and r[c] is not None][:3],
        }
        for c in range(width)
    ]
    new = [r for r in rows if not r["duplicate"] and not r["before_cutoff"]]
    return {
        "mapping": mapping,
        "columns": columns,
        "header_rows": [[tb.text(c, 40) for c in r[:12]] for r in grid[:8]],
        "rows": rows,
        "errors": parsed["errors"],
        "skipped": parsed["skipped"],
        "total": len(rows),
        "new": len(new),
        "duplicates": sum(1 for r in rows if r["duplicate"]),
        "before_cutoff": sum(1 for r in rows if r["before_cutoff"]),
        "credits": q2(sum((r["amount"] for r in new if r["amount"] > 0), Decimal(0))),
        "debits": q2(sum((r["amount"] for r in new if r["amount"] < 0), Decimal(0))),
        "date_from": min((r["date"] for r in rows), default=None),
        "date_to": max((r["date"] for r in rows), default=None),
        "closing_balance": closing,
        "statement_opening": parsed["statement_opening"],
        "continuity": continuity,
    }


def save_statement(db: Session, acct: BankAccount, user_id: int | None, data: bytes, filename: str, raw_mapping: dict | None) -> dict:
    an = analyze(db, acct, data, filename, raw_mapping)
    if an["errors"]:
        raise StatementError(f"Hay {len(an['errors'])} fila(s) con errores: corregí el mapeo o el archivo (ver vista previa).")
    if not an["rows"]:
        raise StatementError("No se encontraron movimientos: revisá la fila de encabezados y las columnas de fecha y monto.")
    now = datetime.now(UTC)
    imp = BankStatementImport(
        tenant_id=acct.tenant_id,
        bank_account_id=acct.id,
        filename=(filename or "estado.csv")[:200],
        sha256=hashlib.sha256(data).hexdigest(),
        mapping=an["mapping"],
        date_from=an["date_from"],
        date_to=an["date_to"],
        rows=an["total"],
        added=0,
        duplicates=an["duplicates"],
        closing_balance=an["closing_balance"],
        created_by=user_id,
        created_at=now,
    )
    db.add(imp)
    db.flush()
    batch = f"imp-{imp.id}"
    added = 0
    for r in _chronological(an["rows"]):  # ids en orden cronologico: el saldo del banco del ultimo dia es el de la ultima
        if r["duplicate"] or r["before_cutoff"]:
            continue
        db.add(
            BankStatementLine(
                tenant_id=acct.tenant_id,
                bank_account_id=acct.id,
                date=r["date"],
                description=r["description"],
                reference=r["reference"],
                amount=r["amount"],
                balance=r["balance"],
                batch=batch,
                fingerprint=r["fingerprint"],
                status="pendiente",
                imported_at=now,
                import_id=imp.id,
            )
        )
        added += 1
    imp.added = added
    db.flush()
    matched = auto_match(db, acct)
    return {
        "import_id": imp.id,
        "added": added,
        "duplicates": an["duplicates"],
        "before_cutoff": an["before_cutoff"],
        "matched": matched,
        "closing_balance": an["closing_balance"],
        "continuity": an["continuity"],
    }


# ------------------------------------------------------------------ conciliacion


def _alnum(s: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper())


def ref_hit(ln: BankStatementLine, e: Entry) -> bool:
    """La referencia de Crimson aparece en la referencia o en la descripcion del banco (o al reves), 4+ caracteres.
    Los bancos alargan la referencia (FT2611051234567) o la meten en la descripcion (SINPE 12345678 ...)."""
    mine = _alnum(e.reference)
    if len(mine) < 4:
        return False
    theirs = _alnum(ln.reference)
    if theirs and len(theirs) >= 4 and (mine in theirs or theirs in mine):
        return True
    return mine in _alnum(ln.description)


def _score(ln: BankStatementLine, e: Entry) -> tuple[bool, int]:
    return ref_hit(ln, e), abs((e.date - ln.date).days)


def auto_match(db: Session, acct: BankAccount) -> int:
    """Casa solo lo seguro: mismo monto exacto, misma direccion, fecha +/-3 dias. La referencia que coincide gana;
    si quedan dos candidatos igual de buenos (dos cobros iguales el mismo dia sin referencia) no se casa ninguno."""
    pending = db.scalars(
        _lines_q(acct).where(BankStatementLine.status == "pendiente").order_by(BankStatementLine.date, BankStatementLine.id)
    ).all()
    if not pending:
        return 0
    entries, _ = ledger_entries(db, acct)
    taken = matched_keys(db, acct.tenant_id)
    free = [e for e in entries if e.key not in taken]
    by_amount: dict[Decimal, list[Entry]] = {}
    for e in free:
        by_amount.setdefault(q2(e.amount), []).append(e)
    pairs = []
    for ln in pending:
        for e in by_amount.get(q2(ln.amount), []):
            hit, days = _score(ln, e)
            if days <= AUTO_DAYS:
                pairs.append(((not hit, days), ln, e))
    pairs.sort(key=lambda p: (p[0], p[1].date, p[1].id, p[2].id))
    per_line: dict[int, list] = {}
    per_entry: dict[tuple[str, int], list] = {}
    for score, ln, e in pairs:
        per_line.setdefault(ln.id, []).append((score, e.key))
        per_entry.setdefault(e.key, []).append((score, ln.id))
    used_lines: set[int] = set()
    used_entries: set[tuple[str, int]] = set()
    n = 0
    for score, ln, e in pairs:
        if ln.id in used_lines or e.key in used_entries:
            continue
        # empate: otro candidato libre igual de bueno para la misma linea, u otra linea libre igual de buena para el mismo
        tie = any(s == score and k != e.key and k not in used_entries for s, k in per_line[ln.id]) or any(
            s == score and lid != ln.id and lid not in used_lines for s, lid in per_entry[e.key]
        )
        if tie:
            continue
        ln.status, ln.matched_type, ln.matched_id, ln.matched_by = "conciliado", e.type, e.id, "auto"
        used_lines.add(ln.id)
        used_entries.add(e.key)
        n += 1
    db.flush()
    return n


def load_entry(db: Session, tenant_id: int, typ: str, oid: int) -> tuple[object, Entry] | None:
    """El objeto de Crimson y su movimiento (sin filtrar por cuenta). None si no existe o es de otro tenant."""
    model = {"payment": Payment, "expense": Expense, "supplier_payment": SupplierPayment, "movement": BankMovement}.get(typ)
    obj = db.get(model, oid) if model else None
    if obj is None or obj.tenant_id != tenant_id:
        return None
    if typ == "payment":
        ent = _payment_entries(db, [obj])[0]
    elif typ == "expense":
        ent = _expense_entries(db, [obj])[0]
    elif typ == "supplier_payment":
        ent = _supplier_payment_entries(db, [obj])[0]
    else:
        acct = db.get(BankAccount, obj.bank_account_id)
        ent = _movement_entries(db, [obj], {obj.bank_account_id: acct.currency if acct else ""})[0]
    return obj, ent


def entry_problem(db: Session, acct: BankAccount, typ: str, obj, ent: Entry) -> str | None:
    """Por que este movimiento no puede ser de la cuenta (None = puede)."""
    if typ == "payment" and (obj.status != "confirmado" or obj.kind not in ("captura", "devolucion", "reembolso")):
        return "El cobro no está confirmado"
    if typ == "expense":
        if obj.status != "pagado":
            return "El gasto no está marcado como pagado"
        applied = db.scalar(_applied_expense_ids(acct.tenant_id).where(SupplierPaymentApplication.expense_id == obj.id).limit(1))
        if applied:
            return "Ese gasto se pagó con un pago a proveedor: casá el pago a proveedor"
    if typ in ("supplier_payment", "movement") and obj.status != "registrado":
        return "El movimiento está anulado"
    if ent.currency != acct.currency:
        return f"Está en {ent.currency} y la cuenta es en {acct.currency}"
    if ent.bank_account_id not in (None, acct.id):
        return "Está registrado en otra cuenta"
    if typ == "movement" and ent.bank_account_id != acct.id:
        return "Es un movimiento de otra cuenta"
    return None


def candidates(db: Session, ln: BankStatementLine, acct: BankAccount) -> list[dict]:
    """Sugerencias para casar a mano: lo de la cuenta y lo que no tiene cuenta asignada (al casarlo queda en esta),
    misma direccion y divisa, +/-45 dias. Primero monto exacto, despues referencia, despues cercania."""
    lo, hi = ln.date - timedelta(days=CANDIDATE_DAYS), ln.date + timedelta(days=CANDIDATE_DAYS)
    taken = matched_keys(db, acct.tenant_id)
    tid, cur = acct.tenant_id, acct.currency
    own, _ = ledger_entries(db, acct)
    pool = {e.key: e for e in own}
    pq = select(Payment).where(
        Payment.tenant_id == tid, Payment.bank_account_id.is_(None), Payment.status == "confirmado", Payment.currency == cur, Payment.paid_at.between(lo, hi)
    )
    eq = select(Expense).where(
        Expense.tenant_id == tid,
        Expense.bank_account_id.is_(None),
        Expense.status == "pagado",
        Expense.currency == cur,
        Expense.id.not_in(_applied_expense_ids(tid)),
        Expense.date.between(lo - timedelta(days=60), hi),
    )
    sq = select(SupplierPayment).where(
        SupplierPayment.tenant_id == tid,
        SupplierPayment.bank_account_id.is_(None),
        SupplierPayment.status == "registrado",
        SupplierPayment.currency == cur,
        SupplierPayment.date.between(lo, hi),
    )
    pays = [p for p in db.scalars(pq) if p.kind in ("captura", "devolucion", "reembolso")]
    for e in _payment_entries(db, pays) + _expense_entries(db, list(db.scalars(eq))) + _supplier_payment_entries(db, list(db.scalars(sq))):
        pool.setdefault(e.key, e)
    amount = q2(ln.amount)
    out = []
    for e in pool.values():
        if e.key in taken or (e.amount > 0) != (amount > 0) or not lo <= e.date <= hi:
            continue
        hit, days = _score(ln, e)
        diff = q2(abs(e.amount - amount))
        out.append({**e.out(), "diff": str(diff), "days": days, "ref": hit, "exact": diff == 0, "unassigned": e.bank_account_id is None})
    out.sort(key=lambda c: (not c["exact"], not c["ref"], Decimal(c["diff"]), c["days"]))
    return out[:30]


def describe_matches(db: Session, tenant_id: int, lines: list[BankStatementLine]) -> dict[int, dict]:
    out = {}
    for ln in lines:
        if ln.status == "conciliado" and ln.matched_type in TYPES and ln.matched_id:
            found = load_entry(db, tenant_id, ln.matched_type, ln.matched_id)
            if found:
                out[ln.id] = found[1].out()
    return out


def line_out(ln: BankStatementLine, match: dict | None = None) -> dict:
    return {
        "id": ln.id,
        "date": ln.date,
        "description": ln.description,
        "reference": ln.reference,
        "amount": str(q2(ln.amount)),
        "balance": str(q2(ln.balance)) if ln.balance is not None else None,
        "status": ln.status,
        "matched_by": ln.matched_by,
        "matched_type": ln.matched_type,
        "matched_id": ln.matched_id,
        "match": match,
        "diff": str(q2(abs(Decimal(match["amount"]) - q2(ln.amount)))) if match else None,
        "import_id": ln.import_id,
    }


def reconciliation(db: Session, acct: BankAccount, as_of: date | None = None) -> dict:
    """Saldo segun el banco contra saldo segun Crimson a una fecha, y lo que falta casar de cada lado.

    (banco - pendientes del banco) - (Crimson - pendientes de Crimson) = diferencia sin explicar: lo casado con montos
    distintos o con fechas a ambos lados del corte. Cero = conciliado."""
    if as_of is None:
        last = db.scalar(select(BankStatementLine.date).where(BankStatementLine.bank_account_id == acct.id).order_by(BankStatementLine.date.desc()).limit(1))
        as_of = last or date.today()
    bank, reported, last_date = bank_balance(db, acct, as_of)
    entries, warnings = ledger_entries(db, acct, as_of)
    book = q2(acct.opening_balance) + sum((e.amount for e in entries), Decimal(0))
    taken = matched_keys(db, acct.tenant_id)
    lines = db.scalars(_lines_q(acct, as_of).order_by(BankStatementLine.date, BankStatementLine.id)).all()
    pend_lines = [ln for ln in lines if ln.status == "pendiente"]
    pend_entries = [e for e in entries if e.key not in taken]
    pb = sum((q2(ln.amount) for ln in pend_lines), Decimal(0))
    pe = sum((e.amount for e in pend_entries), Decimal(0))
    unexplained = q2((bank - pb) - (book - pe))
    if reported is not None and reported != bank:
        warnings.append(
            f"El banco reporta un saldo de {reported} al {last_date.strftime('%d/%m/%Y')} y lo importado suma {bank}: falta un estado de cuenta o el saldo inicial no es el del banco."
        )
    return {
        "as_of": as_of,
        "opening_balance": str(q2(acct.opening_balance)),
        "opening_date": acct.opening_date,
        "bank_balance": str(bank),
        "bank_reported_balance": str(reported) if reported is not None else None,
        "last_statement_date": last_date,
        "book_balance": str(q2(book)),
        "difference": str(q2(bank - book)),
        "pending_bank": {
            "count": len(pend_lines),
            "credits": str(q2(sum((q2(ln.amount) for ln in pend_lines if ln.amount > 0), Decimal(0)))),
            "debits": str(q2(sum((q2(ln.amount) for ln in pend_lines if ln.amount < 0), Decimal(0)))),
            "total": str(q2(pb)),
            "items": [line_out(ln) for ln in pend_lines[-300:]],
        },
        "pending_book": {
            "count": len(pend_entries),
            "inflows": str(q2(sum((e.amount for e in pend_entries if e.amount > 0), Decimal(0)))),
            "outflows": str(q2(sum((e.amount for e in pend_entries if e.amount < 0), Decimal(0)))),
            "total": str(q2(pe)),
            "items": [e.out() for e in pend_entries[-300:]],
        },
        "matched_count": sum(1 for ln in lines if ln.status == "conciliado"),
        "ignored_count": sum(1 for ln in lines if ln.status == "ignorado"),
        "unexplained": str(unexplained),
        "reconciled": unexplained == 0 and not pend_lines and not pend_entries,
        "warnings": warnings,
    }


def account_summary(db: Session, acct: BankAccount) -> dict:
    entries, _ = ledger_entries(db, acct, date.today())
    book_today = q2(acct.opening_balance) + sum((e.amount for e in entries), Decimal(0))
    bank, reported, last = bank_balance(db, acct)
    book_at_last = q2(acct.opening_balance) + sum((e.amount for e in entries if last and e.date <= last), Decimal(0)) if last else None
    taken = matched_keys(db, acct.tenant_id)
    pending_lines = sum(1 for ln in db.scalars(_lines_q(acct).where(BankStatementLine.status == "pendiente")))
    return {
        "id": acct.id,
        "name": acct.name,
        "bank": acct.bank,
        "currency": acct.currency,
        "number": acct.number,
        "kind": acct.kind or "banco",
        "active": acct.active,
        "opening_balance": str(q2(acct.opening_balance)),
        "opening_date": acct.opening_date,
        "book_balance": str(q2(book_today)),
        "bank_balance": str(bank) if last else None,
        "bank_reported_balance": str(reported) if reported is not None else None,
        "last_statement_date": last,
        "book_balance_at_statement": str(q2(book_at_last)) if book_at_last is not None else None,
        "difference": str(q2(bank - book_at_last)) if book_at_last is not None else None,
        "pending_lines": pending_lines,
        "unmatched_entries": sum(1 for e in entries if e.key not in taken),
        "cash_default": acct.kind == "caja" and default_cash_id(db, acct.tenant_id, acct.currency) == acct.id,
    }
