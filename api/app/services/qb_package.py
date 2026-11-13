"""Paquete mensual para la contadora (QuickBooks Online por archivo).

Crimson es la fuente y NUNCA escribe en QuickBooks: arma un ZIP que la contadora revisa e importa ella misma con
Configuración → Importar datos. Lo que va en el ZIP (ver infra/QUICKBOOKS.md):

  1_clientes.csv / 1_clientes_actualizar.csv        contactos nuevos / que cambiaron desde el paquete anterior
  2_proveedores.csv / 2_proveedores_actualizar.csv
  3_facturas_venta[_n].csv                          Invoices: una fila por línea, impuesto por línea (código + monto)
  4_facturas_proveedor[_n].csv                      Bills: una fila por tarifa de IVA, cuenta = la mapeada a la categoría
  control_AAAA-MM.xlsx                              ventas, gastos con IVA por tarifa, cobros, pagos, NC, D-104, ajustes
  respaldos/ventas, respaldos/compras               XML (y PDF) que el sistema tiene guardados
  LEEME.txt

Límites de la importación de QBO: 1000 filas y 100 facturas por archivo (por eso los archivos numerados); las
facturas de una misma factura nunca se parten entre dos archivos.

Idempotencia: cada paquete guarda en accounting_export.manifest la huella de cada documento y contacto que salió. El
paquete siguiente no repite lo ya enviado; lo que cambió o se anuló después sale en la hoja «Ajustes» (se corrige a
mano en QBO, no se reimporta) y lo nuevo con fecha de un mes que ya tuvo paquete sale como «de un mes anterior».
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from tempfile import SpooledTemporaryFile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.timeutil import CR
from ..models import (
    AccountingExport,
    AccountingRoleMapping,
    BankAccount,
    Customer,
    EInvoiceDocument,
    Expense,
    ExpenseCategory,
    Invoice,
    Media,
    Payment,
    ReceivedDocument,
    Supplier,
    SupplierPayment,
    expense_cat_role,
)
from . import journal as js
from .documents import local_date

log = logging.getLogger("crimson.qb_package")

Q2 = Decimal("0.01")
MAX_DOCS_PER_FILE = 100  # QBO: hasta 100 facturas por importación
MAX_ROWS_PER_FILE = 1000  # QBO: hasta 1000 filas por archivo
MAX_BACKUP_BYTES = 60 * 1024 * 1024  # tope de XML/PDF dentro del ZIP (lo demás queda listado en la hoja Respaldos)

CASH_CUSTOMER = "Cliente de contado"
VARIOUS_SUPPLIER = "Proveedores varios"
NON_CREDITABLE_CODE = "IVA no acreditable"

INVOICE_HEADERS = [
    "Invoice no", "Customer", "Invoice Date", "Due Date", "Memo", "Item(Product/Service)", "Item Description",
    "Item Quantity", "Item Rate", "Item Amount", "Item Tax Code", "Item Tax Amount", "Currency Code", "Exchange Rate",
]
BILL_HEADERS = [
    "Bill no", "Supplier", "Bill Date", "Due Date", "Memo", "Account", "Line Description", "Line Amount",
    "Line Tax Code", "Line Tax Amount", "Currency Code", "Exchange Rate",
]
CUSTOMER_HEADERS = ["Name", "Company", "Email", "Phone", "Mobile", "Street", "City", "Province/Region/State", "Country", "Tax ID", "Currency Code", "Notes"]
SUPPLIER_HEADERS = ["Name", "Company", "Email", "Phone", "Tax ID", "Currency Code", "Notes"]

# Producto/servicio de QBO por tipo de ingreso (la contadora los crea una vez, cada uno con su cuenta de ingresos)
REVENUE_ITEMS = {
    "ventas_equipos": "Ventas de equipos",
    "ventas_servicios": "Ventas de servicios",
    "ventas_proyectos": "Ventas de proyectos",
}
PAYMENT_KINDS = ("captura", "devolucion", "reembolso")
METHOD_LABELS = {
    "efectivo": "Efectivo", "tarjeta": "Tarjeta", "transferencia": "Transferencia", "sinpe": "SINPE Móvil",
    "cheque": "Cheque", "onvo": "ONVO (tarjeta en línea)", "paypal": "PayPal", "otro": "Otro",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def D(x) -> Decimal:  # noqa: N802
    if x is None:
        return Decimal(0)
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q2(x) -> Decimal:
    return D(x).quantize(Q2, rounding=ROUND_HALF_UP)


def money(x) -> str:
    return f"{q2(x):.2f}"


def month_bounds(month: str) -> tuple[date, date]:
    y, m = map(int, month.split("-"))
    a = date(y, m, 1)
    b = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return a, b


def _hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:24]


def rate_key(rate, exento: bool = False) -> str:
    """Clave de tarifa: "13", "4", "2", "1", "0", "0.5"... o "exento"."""
    if exento:
        return "exento"
    r = D(rate).normalize()
    s = f"{r:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def tax_code_label(key: str) -> str:
    """Código de impuesto que va en el CSV; en la importación la contadora lo empareja con su código de QBO."""
    return "Exento" if key == "exento" else f"IVA {key}%"


def _rate_sort(key: str) -> tuple:
    return (1, 0.0) if key == "exento" else (0, -float(key))


def _clean(s: str | None, n: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


# ---------------------------------------------------------------------------
# IVA por tarifa
# ---------------------------------------------------------------------------

@dataclass
class TaxPart:
    key: str
    base: Decimal
    tax: Decimal
    creditable: Decimal = Decimal(0)

    @property
    def non_creditable(self) -> Decimal:
        return max(self.tax - self.creditable, Decimal(0))


def _expense_creditable(e: Expense) -> Decimal:
    if e.tax_credit_amount is not None:
        return D(e.tax_credit_amount)
    if e.iva_credit == "no_credito":
        return Decimal(0)
    return D(e.tax_amount)  # credito, bienes_capital y proporcional (la prorrata se aplica en la declaración)


def _get(p, name: str, default=None):
    if isinstance(p, dict):
        return p.get(name, default)
    return getattr(p, name, default)


def expense_parts(db: Session, e: Expense) -> list[TaxPart]:
    """IVA del gasto por tarifa. Usa services/expense_tax.tax_breakdown (desglose línea a línea del XML) cuando
    existe; si no, una sola parte con la tarifa, el subtotal y el IVA del gasto."""
    raw = None
    try:
        from .expense_tax import tax_breakdown  # type: ignore[import-not-found]
    except ImportError:
        tax_breakdown = None
    if tax_breakdown is not None:
        try:
            raw = tax_breakdown(db, e)
        except Exception:  # noqa: BLE001 - un XML raro no tumba el paquete: se usa el gasto completo
            log.exception("desglose de IVA del gasto %s", e.id)
            raw = None
    total_tax = D(e.tax_amount)
    total_cred = _expense_creditable(e)
    if raw:
        parts: list[TaxPart] = []
        for p in raw:
            rate = _get(p, "rate", 0)
            exento = bool(_get(p, "exento", False) or _get(p, "exempt", False) or str(_get(p, "rate_code", "") or "") == "10")
            tax = D(_get(p, "tax", 0))
            acred = _get(p, "acreditable", None)
            if isinstance(acred, bool):
                cred = tax if acred else Decimal(0)
            elif acred is None:
                cred = (tax * total_cred / total_tax) if total_tax else Decimal(0)
            else:
                cred = D(acred)
            parts.append(TaxPart(rate_key(rate, exento), D(_get(p, "base", 0)), tax, min(max(cred, Decimal(0)), tax)))
        return parts
    return [TaxPart(rate_key(e.tax_rate, exento=False), D(e.subtotal), total_tax, min(max(total_cred, Decimal(0)), total_tax))]


def invoice_parts(inv: Invoice) -> list[TaxPart]:
    acc: dict[str, TaxPart] = {}
    for ln in inv.lines:
        if ln.treatment in ("cortesia", "excluido"):
            continue
        k = rate_key(ln.tax_rate, exento=(ln.tax_rate_code == "10"))
        part = acc.setdefault(k, TaxPart(k, Decimal(0), Decimal(0)))
        part.base += D(ln.subtotal)
        part.tax += D(ln.tax_amount)
    return sorted(acc.values(), key=lambda p: _rate_sort(p.key))


# ---------------------------------------------------------------------------
# Qué documentos cuentan
# ---------------------------------------------------------------------------

def invoice_ok(inv: Invoice) -> bool:
    """Factura emitida de verdad: ni anulada, ni rechazada, ni borrador sin enviar."""
    if inv.status == "anulada" or inv.einvoice_status == "rechazada":
        return False
    return (
        inv.einvoice_status in ("aceptada", "emitida_fuera", "importada", "pendiente")
        or inv.sent_at is not None
        or inv.status in ("enviada", "pagada", "parcial", "vencida")
    )


def expense_ok(e: Expense) -> bool:
    return e.status != "anulado"


def payment_ok(p: Payment) -> bool:
    return p.status == "confirmado" and p.kind in PAYMENT_KINDS


def supplier_payment_ok(sp: SupplierPayment) -> bool:
    return sp.status == "registrado"


def nc_ok(nc: EInvoiceDocument) -> bool:
    return nc.doc_type == "NC" and nc.status == "aceptada"


def nc_date(nc: EInvoiceDocument) -> date | None:
    return local_date(nc.emitted_at or nc.created_at)


def expense_is_paid_at_once(e: Expense, applied_ids: set[int]) -> bool:
    """Gasto de contado (o marcado pagado sin pago de CxP aplicado): el pago va con el propio gasto."""
    return (e.purchase_type != "credito" or e.status == "pagado") and e.id not in applied_ids


# huellas: lo que la contadora ve en QBO. Si cambia, el documento sale como ajuste en el paquete siguiente.
def _invoice_fp(inv: Invoice) -> str:
    return _hash([inv.number, inv.issue_date, inv.due_date, inv.customer_id, inv.currency, money(inv.fx_sell), money(inv.total),
                  [(ln.name, str(ln.quantity), money(ln.subtotal), str(ln.tax_rate), ln.tax_rate_code, money(ln.tax_amount), ln.treatment)
                   for ln in inv.lines]])


def _expense_fp(e: Expense) -> str:
    return _hash([e.date, e.supplier_id, e.reference, e.category_id, e.currency, money(e.fx_rate), money(e.subtotal), money(e.tax_amount),
                  money(e.total), e.tax_credit_amount and money(e.tax_credit_amount), e.purchase_type, e.payment_method,
                  e.bank_account_id, getattr(e, "paid_date", None)])


def _payment_fp(p: Payment) -> str:
    return _hash([p.paid_at, p.invoice_id, money(p.amount), p.currency, p.method, p.kind, p.external_ref, p.bank_account_id])


def _sp_fp(sp: SupplierPayment) -> str:
    return _hash([sp.date, sp.supplier_id, money(sp.amount), sp.currency, sp.method, sp.reference, sp.bank_account_id,
                  sorted((a.expense_id, money(a.amount)) for a in sp.applications)])


def _nc_fp(nc: EInvoiceDocument) -> str:
    return _hash([nc.clave, nc.invoice_id, nc.status])


def _customer_fp(c: Customer) -> str:
    return _hash([c.name, c.id_number, c.email, c.phone, c.whatsapp, c.address])


def _supplier_fp(s: Supplier) -> str:
    return _hash([s.name, s.tax_id, s.email, s.phone])


# ---------------------------------------------------------------------------
# Recolección
# ---------------------------------------------------------------------------

@dataclass
class Package:
    tenant_id: int
    month: str
    a: date
    b: date
    invoices: list[Invoice] = field(default_factory=list)
    expenses: list[Expense] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    supplier_payments: list[SupplierPayment] = field(default_factory=list)
    credit_notes: list[EInvoiceDocument] = field(default_factory=list)
    late: set[tuple[str, int]] = field(default_factory=set)  # (tipo, id) con fecha de un mes que ya tuvo paquete
    changes: list[dict] = field(default_factory=list)  # ajustes: cambió o se anuló después del paquete anterior
    customers_new: list[Customer] = field(default_factory=list)
    customers_changed: list[Customer] = field(default_factory=list)
    suppliers_new: list[Supplier] = field(default_factory=list)
    suppliers_changed: list[Supplier] = field(default_factory=list)
    cash_customer: bool = False  # hay facturas sin cliente
    various_supplier: bool = False  # hay gastos sin proveedor
    manifest: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    previous_packages: int = 0

    def count(self) -> int:
        return len(self.invoices) + len(self.expenses) + len(self.payments) + len(self.supplier_payments) + len(self.credit_notes)


def previous_manifest(db: Session, tenant_id: int, month: str) -> tuple[dict, set[str], int]:
    """Lo que ya salió en paquetes de OTROS meses (lo más nuevo gana) y los meses que ya tuvieron paquete.
    Los paquetes del mismo mes no cuentan: regenerar un mes arma el mismo paquete otra vez."""
    merged: dict[str, dict[str, list]] = {}
    months: set[str] = set()
    n = 0
    for ex in db.scalars(
        select(AccountingExport)
        .where(AccountingExport.tenant_id == tenant_id, AccountingExport.kind == "paquete", AccountingExport.month != month)
        .order_by(AccountingExport.created_at, AccountingExport.id)
    ):
        n += 1
        months.add(ex.month)
        for kind, entries in (ex.manifest or {}).items():
            if isinstance(entries, dict):
                merged.setdefault(kind, {}).update(entries)
    return merged, months, n


def _account_names(db: Session, tenant_id: int) -> dict[str, str]:
    return {
        m.role_key: m.account.full_name
        for m in db.scalars(select(AccountingRoleMapping).where(AccountingRoleMapping.tenant_id == tenant_id))
        if m.account is not None
    }


def collect(db: Session, tenant_id: int, month: str) -> Package:
    a, b = month_bounds(month)
    pkg = Package(tenant_id=tenant_id, month=month, a=a, b=b)
    prev, packaged_months, pkg.previous_packages = previous_manifest(db, tenant_id, month)
    start = min([month_bounds(m)[0] for m in packaged_months if m < month] + [a])

    def wanted(kind: str, doc_id: int, d: date | None) -> bool:
        if d is None or d > b:
            return False
        dm = d.strftime("%Y-%m")
        if str(doc_id) in prev.get(kind, {}):
            return False
        if dm == month:
            return True
        if dm < month and dm in packaged_months:
            pkg.late.add((kind, doc_id))
            return True
        return False

    tid = tenant_id
    pkg.invoices = [
        inv for inv in db.scalars(
            select(Invoice).where(Invoice.tenant_id == tid, Invoice.issue_date >= start, Invoice.issue_date <= b).order_by(Invoice.issue_date, Invoice.id)
        )
        if invoice_ok(inv) and wanted("invoice", inv.id, inv.issue_date)
    ]
    pkg.expenses = [
        e for e in db.scalars(select(Expense).where(Expense.tenant_id == tid, Expense.date >= start, Expense.date <= b).order_by(Expense.date, Expense.id))
        if expense_ok(e) and wanted("expense", e.id, e.date)
    ]
    pkg.payments = [
        p for p in db.scalars(select(Payment).where(Payment.tenant_id == tid, Payment.paid_at >= start, Payment.paid_at <= b).order_by(Payment.paid_at, Payment.id))
        if payment_ok(p) and wanted("payment", p.id, p.paid_at)
    ]
    pkg.supplier_payments = [
        sp for sp in db.scalars(
            select(SupplierPayment).where(SupplierPayment.tenant_id == tid, SupplierPayment.date >= start, SupplierPayment.date <= b).order_by(SupplierPayment.date, SupplierPayment.id)
        )
        if supplier_payment_ok(sp) and wanted("supplier_payment", sp.id, sp.date)
    ]
    nc_from = datetime.combine(start - timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    pkg.credit_notes = [
        nc for nc in db.scalars(
            select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == tid, EInvoiceDocument.doc_type == "NC", EInvoiceDocument.created_at >= nc_from)
        )
        if nc_ok(nc) and (d := nc_date(nc)) is not None and d >= start and wanted("credit_note", nc.id, d)
    ]

    # Ajustes: lo que ya salió en un paquete y después cambió, se anuló o desapareció
    _detect_changes(db, pkg, prev)

    # Contactos de los documentos que salen en los CSV de importación
    seen_c: set[int] = set()
    for inv in pkg.invoices:
        if inv.customer_id is None:
            pkg.cash_customer = True
            continue
        if inv.customer_id in seen_c:
            continue
        seen_c.add(inv.customer_id)
        c = db.get(Customer, inv.customer_id)
        if c is None or c.tenant_id != tid:
            continue
        old = prev.get("customer", {}).get(str(c.id))
        if old is None:
            pkg.customers_new.append(c)
        elif old[0] != _customer_fp(c):
            pkg.customers_changed.append(c)
    seen_s: set[int] = set()
    for e in pkg.expenses:
        if e.supplier_id is None:
            pkg.various_supplier = True
            continue
        if e.supplier_id in seen_s:
            continue
        seen_s.add(e.supplier_id)
        s = db.get(Supplier, e.supplier_id)
        if s is None or s.tenant_id != tid:
            continue
        old = prev.get("supplier", {}).get(str(s.id))
        if old is None:
            pkg.suppliers_new.append(s)
        elif old[0] != _supplier_fp(s):
            pkg.suppliers_changed.append(s)
    if pkg.cash_customer and "0" not in prev.get("customer", {}):
        pkg.customers_new.insert(0, Customer(id=0, tenant_id=tid, name=CASH_CUSTOMER))  # sin guardar: solo para el CSV
    if pkg.various_supplier and "0" not in prev.get("supplier", {}):
        pkg.suppliers_new.insert(0, Supplier(id=0, tenant_id=tid, name=VARIOUS_SUPPLIER))

    _build_manifest(db, pkg)
    pkg.issues = _issues(db, pkg)
    return pkg


def _doc_label(kind: str, obj) -> str:
    if kind == "invoice":
        return f"Factura {obj.number}"
    if kind == "expense":
        return f"Gasto #{obj.id} · {_clean(obj.description, 60)}"
    if kind == "payment":
        return f"Cobro #{obj.id}"
    if kind == "supplier_payment":
        return f"Pago a proveedor #{obj.id}"
    if kind == "credit_note":
        return f"Nota de crédito {obj.consecutive or obj.id}"
    return f"{kind} #{getattr(obj, 'id', '')}"


_KIND_MODEL = {
    "invoice": (Invoice, invoice_ok, _invoice_fp, lambda o: o.total, lambda o: o.issue_date),
    "expense": (Expense, expense_ok, _expense_fp, lambda o: o.total, lambda o: o.date),
    "payment": (Payment, payment_ok, _payment_fp, lambda o: o.amount, lambda o: o.paid_at),
    "supplier_payment": (SupplierPayment, supplier_payment_ok, _sp_fp, lambda o: o.amount, lambda o: o.date),
    "credit_note": (EInvoiceDocument, nc_ok, _nc_fp, lambda o: None, nc_date),
}


def _detect_changes(db: Session, pkg: Package, prev: dict) -> None:
    for kind, (model, ok, fp, total_of, date_of) in _KIND_MODEL.items():
        entries = prev.get(kind) or {}
        if not entries:
            continue
        ids = [int(k) for k in entries]
        found = {o.id: o for o in db.scalars(select(model).where(model.tenant_id == pkg.tenant_id, model.id.in_(ids)))}
        for sid, old in entries.items():
            old_hash, old_total, old_label = (list(old) + [None, None, None])[:3]
            if old_hash == "anulado":
                continue  # ya se informó la anulación en un paquete anterior
            obj = found.get(int(sid))
            if obj is None:
                pkg.changes.append({"kind": kind, "id": int(sid), "label": old_label, "reason": "Ya no existe en Crimson", "before": old_total,
                                    "now": None, "date": None, "fp": "anulado"})
                continue
            if not ok(obj):
                pkg.changes.append({"kind": kind, "id": obj.id, "label": _doc_label(kind, obj), "reason": "Anulado en Crimson después del paquete",
                                    "before": old_total, "now": None, "date": date_of(obj), "fp": "anulado", "currency": getattr(obj, "currency", None)})
                continue
            now_fp = fp(obj)
            if now_fp != old_hash:
                now_total = total_of(obj)
                pkg.changes.append({"kind": kind, "id": obj.id, "label": _doc_label(kind, obj), "reason": "Cambió después del paquete",
                                    "before": old_total, "now": money(now_total) if now_total is not None else None, "date": date_of(obj),
                                    "fp": now_fp, "currency": getattr(obj, "currency", None)})


def _build_manifest(db: Session, pkg: Package) -> None:
    man: dict[str, dict[str, list]] = {}

    def put(kind: str, oid: int, fp: str, total, label: str) -> None:
        man.setdefault(kind, {})[str(oid)] = [fp, money(total) if total is not None else None, label]

    for inv in pkg.invoices:
        put("invoice", inv.id, _invoice_fp(inv), inv.total, _doc_label("invoice", inv))
    for e in pkg.expenses:
        put("expense", e.id, _expense_fp(e), e.total, _doc_label("expense", e))
    for p in pkg.payments:
        put("payment", p.id, _payment_fp(p), p.amount, _doc_label("payment", p))
    for sp in pkg.supplier_payments:
        put("supplier_payment", sp.id, _sp_fp(sp), sp.amount, _doc_label("supplier_payment", sp))
    for nc in pkg.credit_notes:
        put("credit_note", nc.id, _nc_fp(nc), None, _doc_label("credit_note", nc))
    for ch in pkg.changes:
        man.setdefault(ch["kind"], {})[str(ch["id"])] = [ch["fp"], ch.get("now"), ch.get("label")]
    for c in pkg.customers_new + pkg.customers_changed:
        put("customer", c.id or 0, _customer_fp(c) if c.id else "contado", None, c.name)
    for s in pkg.suppliers_new + pkg.suppliers_changed:
        put("supplier", s.id or 0, _supplier_fp(s) if s.id else "varios", None, s.name)
    pkg.manifest = man


def _issues(db: Session, pkg: Package) -> list[dict]:
    from .fx import FxMissing, doc_rate

    issues: list[dict] = []
    names = _account_names(db, pkg.tenant_id)
    cats = {c.id: c for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.tenant_id == pkg.tenant_id))}
    sin_cat = [e for e in pkg.expenses if not e.category_id]
    if sin_cat:
        issues.append({"severity": "error", "code": "expense_no_category",
                       "message": f"{len(sin_cat)} gasto(s) sin categoría (p. ej. #{sin_cat[0].id}): asignales una para saber a qué cuenta van"})
    missing: dict[int, int] = {}
    for e in pkg.expenses:
        if e.category_id and expense_cat_role(e.category_id) not in names:
            missing[e.category_id] = missing.get(e.category_id, 0) + 1
    for cid, n in missing.items():
        cname = cats[cid].name if cid in cats else f"#{cid}"
        issues.append({"severity": "error", "code": "category_unmapped",
                       "message": f"La categoría «{cname}» ({n} gasto(s)) no tiene cuenta de QuickBooks: asignala en el paso Mapeo"})
    for kind, docs, cur, stored, when in (
        ("factura", pkg.invoices, lambda o: o.currency, lambda o: o.fx_sell, lambda o: o.issue_date),
        ("gasto", pkg.expenses, lambda o: o.currency, lambda o: o.fx_rate, lambda o: o.date),
    ):
        for o in docs:
            if (cur(o) or "CRC").upper() == "CRC":
                continue
            try:
                doc_rate(db, cur(o), stored(o), when(o))
            except FxMissing:
                issues.append({"severity": "error", "code": "missing_fx",
                               "message": f"{kind.capitalize()} en {cur(o)} sin tipo de cambio ({_doc_label('invoice' if kind == 'factura' else 'expense', o)})"})
                break
    if pkg.various_supplier:
        n = sum(1 for e in pkg.expenses if e.supplier_id is None)
        issues.append({"severity": "warning", "code": "expense_no_supplier",
                       "message": f"{n} gasto(s) sin proveedor: salen a nombre de «{VARIOUS_SUPPLIER}»"})
    if pkg.cash_customer:
        issues.append({"severity": "warning", "code": "invoice_no_customer",
                       "message": f"Hay facturas sin cliente: salen a nombre de «{CASH_CUSTOMER}»"})
    pend = [i for i in pkg.invoices if i.einvoice_status == "pendiente"]
    if pend:
        issues.append({"severity": "warning", "code": "einvoice_pending",
                       "message": f"{len(pend)} factura(s) todavía sin respuesta de Hacienda (p. ej. {pend[0].number})"})
    no_id = [c for c in pkg.customers_new + pkg.customers_changed if c.id and not c.id_number]
    if no_id:
        issues.append({"severity": "warning", "code": "customer_no_tax_id", "message": f"Cliente(s) sin cédula: {', '.join(c.name for c in no_id[:5])}"})
    cust_names = {c.name.strip().lower() for c in pkg.customers_new + pkg.customers_changed}
    dup = [s.name for s in pkg.suppliers_new + pkg.suppliers_changed if s.name.strip().lower() in cust_names]
    if dup:
        issues.append({"severity": "warning", "code": "name_both",
                       "message": f"Mismo nombre como cliente y como proveedor ({', '.join(dup[:3])}): QuickBooks no lo permite; cambiá uno de los dos en QBO antes de importar"})
    usd_c = sorted({(inv.currency or "CRC").upper() for inv in pkg.invoices} - {"CRC"})
    if usd_c:
        issues.append({"severity": "warning", "code": "foreign_currency",
                       "message": f"Hay documentos en {', '.join(usd_c)}: en QBO el cliente o proveedor tiene que tener esa moneda antes de importar"})
    if pkg.late:
        issues.append({"severity": "warning", "code": "late_docs",
                       "message": f"{len(pkg.late)} documento(s) con fecha de un mes que ya tuvo paquete: van en este paquete (revisá el período en QBO)"})
    if pkg.changes:
        issues.append({"severity": "warning", "code": "changes",
                       "message": f"{len(pkg.changes)} documento(s) cambiaron o se anularon después de un paquete anterior: van en la hoja «Ajustes» para corregirlos a mano en QBO"})
    return issues


# ---------------------------------------------------------------------------
# Archivos de importación
# ---------------------------------------------------------------------------

def _chunks(docs: list[list[dict]]) -> list[list[dict]]:
    """Agrupa filas de documentos en archivos de hasta 100 documentos y 1000 filas, sin partir un documento."""
    files: list[list[dict]] = []
    cur: list[dict] = []
    ndocs = 0
    for rows in docs:
        if cur and (ndocs >= MAX_DOCS_PER_FILE or len(cur) + len(rows) > MAX_ROWS_PER_FILE):
            files.append(cur)
            cur, ndocs = [], 0
        cur.extend(rows)
        ndocs += 1
    if cur:
        files.append(cur)
    return files


def _csv(headers: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _fx_text(db: Session, currency: str, stored, on: date) -> str:
    from .fx import FxMissing, doc_rate

    if (currency or "CRC").upper() == "CRC":
        return ""
    try:
        return f"{doc_rate(db, currency, stored, on):.5f}"
    except FxMissing:
        return ""


def _customer_name(db: Session, tenant_id: int, customer_id: int | None) -> str:
    if customer_id is None:
        return CASH_CUSTOMER
    c = db.get(Customer, customer_id)
    return c.name if c is not None and c.tenant_id == tenant_id else CASH_CUSTOMER


def _supplier_name(db: Session, tenant_id: int, supplier_id: int | None) -> str:
    if supplier_id is None:
        return VARIOUS_SUPPLIER
    s = db.get(Supplier, supplier_id)
    return s.name if s is not None and s.tenant_id == tenant_id else VARIOUS_SUPPLIER


def invoice_rows(db: Session, inv: Invoice, fmt: str) -> list[dict]:
    is_project = js._is_project_invoice(db, inv)
    due = inv.due_date or (inv.issue_date + timedelta(days=inv.credit_days or 0))
    head = {
        "Invoice no": (inv.number or str(inv.id))[:21],
        "Customer": _customer_name(db, inv.tenant_id, inv.customer_id),
        "Invoice Date": inv.issue_date.strftime(fmt),
        "Due Date": due.strftime(fmt),
        "Memo": _clean(" · ".join(x for x in (inv.doc_type, inv.consecutive, f"clave {inv.clave}" if inv.clave else "") if x), 1000),
        "Currency Code": (inv.currency or "CRC").upper(),
        "Exchange Rate": _fx_text(db, inv.currency, inv.fx_sell, inv.issue_date),
    }
    rows = []
    for ln in inv.lines:
        if ln.treatment in ("cortesia", "excluido"):
            continue
        amount = q2(ln.subtotal)
        qty, price = D(ln.quantity), D(ln.unit_price)
        exact = qty > 0 and q2(qty * price) == amount
        key = rate_key(ln.tax_rate, exento=(ln.tax_rate_code == "10"))
        rows.append({
            **head,
            "Item(Product/Service)": REVENUE_ITEMS.get(js._revenue_role(db, ln, is_project), "Ventas de servicios"),
            "Item Description": _clean(" — ".join(x for x in (ln.code, ln.name) if x), 4000),
            "Item Quantity": f"{qty.normalize():f}" if exact else "",
            "Item Rate": money(price) if exact else "",
            "Item Amount": money(amount),
            "Item Tax Code": tax_code_label(key),
            "Item Tax Amount": money(ln.tax_amount),
        })
    return rows


def bill_rows(db: Session, e: Expense, fmt: str, account_names: dict[str, str]) -> list[dict]:
    cat = db.get(ExpenseCategory, e.category_id) if e.category_id else None
    account = account_names.get(expense_cat_role(e.category_id)) if e.category_id else None
    account = account or f"[Sin cuenta: {cat.name if cat else 'sin categoría'}]"
    rd = _received_doc(db, e)
    number = e.reference or (rd.consecutive if rd is not None else None) or f"G-{e.id}"
    clave = e.received_clave or (rd.clave if rd is not None else None)
    memo = f"Gasto #{e.id}" + (f" · clave {clave}" if clave else "")
    head = {
        "Bill no": number[:21],
        "Supplier": _supplier_name(db, e.tenant_id, e.supplier_id),
        "Bill Date": e.date.strftime(fmt),
        "Due Date": (e.due_date or e.date).strftime(fmt),
        "Memo": memo,
        "Account": account,
        "Currency Code": (e.currency or "CRC").upper(),
        "Exchange Rate": _fx_text(db, e.currency, e.fx_rate, e.date),
    }
    desc = _clean(e.description, 4000)
    rows: list[dict] = []
    total_lines = Decimal(0)
    for p in expense_parts(db, e):
        label = tax_code_label(p.key)
        if p.tax <= 0 or p.creditable >= p.tax:
            rows.append({**head, "Line Description": desc, "Line Amount": money(p.base), "Line Tax Code": label, "Line Tax Amount": money(p.tax)})
        elif p.creditable <= 0:
            rows.append({**head, "Line Description": f"{desc} ({label} no acreditable)", "Line Amount": money(p.base + p.tax),
                         "Line Tax Code": NON_CREDITABLE_CODE, "Line Tax Amount": "0.00"})
        else:
            base_c = q2(p.base * p.creditable / p.tax)
            rows.append({**head, "Line Description": desc, "Line Amount": money(base_c), "Line Tax Code": label, "Line Tax Amount": money(p.creditable)})
            rows.append({**head, "Line Description": f"{desc} ({label} no acreditable)", "Line Amount": money(p.base - base_c + p.non_creditable),
                         "Line Tax Code": NON_CREDITABLE_CODE, "Line Tax Amount": "0.00"})
        total_lines += q2(p.base) + q2(p.tax)
    other = q2(D(e.total) - total_lines)
    if other > Q2:  # otros cargos del comprobante (servicio, timbres): sin IVA
        rows.append({**head, "Line Description": f"{desc} (otros cargos)", "Line Amount": money(other), "Line Tax Code": "Exento", "Line Tax Amount": "0.00"})
    return rows


def _received_doc(db: Session, e: Expense) -> ReceivedDocument | None:
    if e.received_document_id:
        rd = db.get(ReceivedDocument, e.received_document_id)
        if rd is not None and rd.tenant_id == e.tenant_id:
            return rd
    return db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == e.tenant_id, ReceivedDocument.expense_id == e.id).limit(1))


def _addr(c: Customer) -> dict:
    a = c.address if isinstance(c.address, dict) else {}
    return {
        "Street": _clean(a.get("senas") or a.get("otras_senas") or a.get("direccion"), 500),
        "City": _clean(a.get("canton_nombre") or a.get("canton"), 100),
        "Province/Region/State": _clean(a.get("provincia_nombre") or a.get("provincia"), 100),
        "Country": "Costa Rica" if a else "",
    }


def customer_rows(db: Session, pkg: Package, customers: list[Customer]) -> list[dict]:
    cur_by_c: dict[int | None, set[str]] = {}
    for inv in pkg.invoices:
        cur_by_c.setdefault(inv.customer_id or 0, set()).add((inv.currency or "CRC").upper())
    rows = []
    for c in customers:
        curs = cur_by_c.get(c.id or 0, set())
        rows.append({
            "Name": c.name, "Company": c.name if c.id_type == "juridica" else "", "Email": (c.email or "").split(",")[0].strip(),
            "Phone": c.phone or "", "Mobile": c.whatsapp or "", **(_addr(c) if c.id else {}),
            "Tax ID": c.id_number or "", "Currency Code": next(iter(curs)) if len(curs) == 1 else "",
            "Notes": f"Cédula {c.id_type} {c.id_number}" if c.id and c.id_number else "",
        })
    return rows


def supplier_rows(pkg: Package, suppliers: list[Supplier]) -> list[dict]:
    cur_by_s: dict[int, set[str]] = {}
    for e in pkg.expenses:
        cur_by_s.setdefault(e.supplier_id or 0, set()).add((e.currency or "CRC").upper())
    rows = []
    for s in suppliers:
        curs = cur_by_s.get(s.id or 0, set())
        rows.append({"Name": s.name, "Company": s.name if s.id else "", "Email": s.email or "", "Phone": s.phone or "",
                     "Tax ID": s.tax_id or "", "Currency Code": next(iter(curs)) if len(curs) == 1 else "",
                     "Notes": f"Cédula {s.tax_id}" if s.tax_id else ""})
    return rows


# ---------------------------------------------------------------------------
# Excel de control
# ---------------------------------------------------------------------------

def _crc(db: Session, amount, currency: str | None, stored, on: date | None) -> Decimal | None:
    from .fx import FxMissing, doc_rate

    try:
        return q2(D(amount) * doc_rate(db, currency, stored, on))
    except FxMissing:
        return None


def _bank_name(db: Session, tenant_id: int, bank_id: int | None) -> str:
    if not bank_id:
        return ""
    b = db.get(BankAccount, bank_id)
    return b.name if b is not None and b.tenant_id == tenant_id else ""


def d104_totals(db: Session, tenant_id: int, month: str) -> dict:
    """Totales referenciales del mes en colones para la D-104 (TODO lo del mes, haya salido o no en un paquete)."""
    a, b = month_bounds(month)
    ventas: dict[str, list[Decimal]] = {}
    for inv in db.scalars(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.issue_date >= a, Invoice.issue_date <= b)):
        if not invoice_ok(inv):
            continue
        for p in invoice_parts(inv):
            base = _crc(db, p.base, inv.currency, inv.fx_sell, inv.issue_date) or Decimal(0)
            tax = _crc(db, p.tax, inv.currency, inv.fx_sell, inv.issue_date) or Decimal(0)
            v = ventas.setdefault(p.key, [Decimal(0), Decimal(0)])
            v[0] += base
            v[1] += tax
    nc: dict[str, list[Decimal]] = {}
    for doc in db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == tenant_id, EInvoiceDocument.doc_type == "NC")):
        d = nc_date(doc)
        if not nc_ok(doc) or d is None or not (a <= d <= b):
            continue
        inv = db.get(Invoice, doc.invoice_id)
        if inv is None or inv.tenant_id != tenant_id:
            continue
        for p in invoice_parts(inv):
            v = nc.setdefault(p.key, [Decimal(0), Decimal(0)])
            v[0] += _crc(db, p.base, inv.currency, inv.fx_sell, inv.issue_date) or Decimal(0)
            v[1] += _crc(db, p.tax, inv.currency, inv.fx_sell, inv.issue_date) or Decimal(0)
    compras: dict[str, list[Decimal]] = {}
    for e in db.scalars(select(Expense).where(Expense.tenant_id == tenant_id, Expense.date >= a, Expense.date <= b)):
        if not expense_ok(e):
            continue
        for p in expense_parts(db, e):
            v = compras.setdefault(p.key, [Decimal(0)] * 4)
            v[0] += _crc(db, p.base, e.currency, e.fx_rate, e.date) or Decimal(0)
            v[1] += _crc(db, p.tax, e.currency, e.fx_rate, e.date) or Decimal(0)
            v[2] += _crc(db, p.creditable, e.currency, e.fx_rate, e.date) or Decimal(0)
            v[3] += _crc(db, p.non_creditable, e.currency, e.fx_rate, e.date) or Decimal(0)
    debito = sum((v[1] for v in ventas.values()), Decimal(0)) - sum((v[1] for v in nc.values()), Decimal(0))
    credito = sum((v[2] for v in compras.values()), Decimal(0))
    return {"ventas": ventas, "nc": nc, "compras": compras, "debito": debito, "credito": credito, "neto": debito - credito}


def build_control_xlsx(db: Session, pkg: Package, *, tenant_name: str, generated_by: str | None, fmt_date: str,
                       backups: list[dict] | None = None, draft: bool = False) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    tid = pkg.tenant_id
    names = _account_names(db, tid)
    cats = {c.id: c.name for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.tenant_id == tid))}
    wb = openpyxl.Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="B3122B")
    num = "#,##0.00"

    def sheet(title: str, headers: list[str], first: bool = False):
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        ws.append(headers)
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        for i, h in enumerate(headers, start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(12, min(40, len(h) + 4))
        return ws

    def add(ws, values: list, money_cols: tuple[int, ...] = ()):
        ws.append([float(v) if isinstance(v, Decimal) else v for v in values])
        for c in money_cols:
            ws.cell(row=ws.max_row, column=c).number_format = num

    def obs(kind: str, oid: int) -> str:
        return "De un mes anterior (ya tuvo paquete)" if (kind, oid) in pkg.late else ""

    # --- Resumen ---
    ws = sheet("Resumen", ["Dato", "Valor"], first=True)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 42, 60
    for row in (
        ("Empresa", tenant_name),
        ("Mes", pkg.month),
        ("Estado", "BORRADOR para revisar (todavía no es el paquete)" if draft else "Paquete generado"),
        ("Generado por", generated_by or ""),
        ("Generado el", datetime.now(CR).strftime("%d/%m/%Y %H:%M")),
        ("Facturas de venta", len(pkg.invoices)),
        ("Facturas de proveedor (gastos)", len(pkg.expenses)),
        ("Cobros", len(pkg.payments)),
        ("Pagos a proveedores (CxP)", len(pkg.supplier_payments)),
        ("Notas de crédito", len(pkg.credit_notes)),
        ("Clientes nuevos / cambiados", f"{len(pkg.customers_new)} / {len(pkg.customers_changed)}"),
        ("Proveedores nuevos / cambiados", f"{len(pkg.suppliers_new)} / {len(pkg.suppliers_changed)}"),
        ("Ajustes (cambios después de un paquete anterior)", len(pkg.changes)),
        ("Formato de fecha de los CSV", fmt_date),
        ("Nota", "Crimson no escribe en QuickBooks: la contadora revisa este Excel e importa los CSV (ver LEEME.txt)."),
    ):
        ws.append(list(row))
    for issue in pkg.issues:
        ws.append([("Error" if issue["severity"] == "error" else "Aviso"), issue["message"]])

    # --- Ventas (una fila por factura y tarifa) ---
    ws = sheet("Ventas", ["Fecha", "Número", "Tipo", "Consecutivo", "Clave", "Cliente", "Cédula", "Moneda", "Tipo de cambio", "Tarifa",
                          "Base", "IVA", "Total", "Base ₡", "IVA ₡", "Estado Hacienda", "Observación"])
    for inv in pkg.invoices:
        c = db.get(Customer, inv.customer_id) if inv.customer_id else None
        fx = _fx_text(db, inv.currency, inv.fx_sell, inv.issue_date)
        for p in invoice_parts(inv):
            add(ws, [inv.issue_date.strftime("%d/%m/%Y"), inv.number, inv.doc_type, inv.consecutive or "", inv.clave or "",
                     c.name if c else CASH_CUSTOMER, (c.id_number or "") if c else "", inv.currency, fx, tax_code_label(p.key),
                     q2(p.base), q2(p.tax), q2(p.base + p.tax), _crc(db, p.base, inv.currency, inv.fx_sell, inv.issue_date),
                     _crc(db, p.tax, inv.currency, inv.fx_sell, inv.issue_date), inv.einvoice_status, obs("invoice", inv.id)],
                (11, 12, 13, 14, 15))

    # --- Gastos (una fila por gasto y tarifa) ---
    ws = sheet("Gastos", ["Fecha", "Gasto #", "Proveedor", "Cédula", "Factura", "Clave", "Categoría", "Cuenta QBO", "Moneda", "Tipo de cambio",
                          "Tarifa", "Base", "IVA", "IVA acreditable", "IVA no acreditable", "Total línea", "Base ₡", "IVA ₡", "Acreditable ₡",
                          "Condición IVA", "Compra", "Estado", "Observación"])
    for e in pkg.expenses:
        s = db.get(Supplier, e.supplier_id) if e.supplier_id else None
        fx = _fx_text(db, e.currency, e.fx_rate, e.date)
        account = names.get(expense_cat_role(e.category_id)) if e.category_id else None
        for p in expense_parts(db, e):
            add(ws, [e.date.strftime("%d/%m/%Y"), e.id, s.name if s else VARIOUS_SUPPLIER, (s.tax_id or "") if s else "", e.reference or "",
                     e.received_clave or "", cats.get(e.category_id, "Sin categoría"), account or "SIN CUENTA", e.currency, fx,
                     tax_code_label(p.key), q2(p.base), q2(p.tax), q2(p.creditable), q2(p.non_creditable), q2(p.base + p.tax),
                     _crc(db, p.base, e.currency, e.fx_rate, e.date), _crc(db, p.tax, e.currency, e.fx_rate, e.date),
                     _crc(db, p.creditable, e.currency, e.fx_rate, e.date), e.iva_credit, e.purchase_type, e.status, obs("expense", e.id)],
                (12, 13, 14, 15, 16, 17, 18, 19))

    # --- Cobros ---
    ws = sheet("Cobros", ["Fecha", "Factura", "Cliente", "Tipo", "Medio", "Referencia", "Banco", "Moneda", "Monto", "Monto ₡", "Notas", "Observación"])
    for p in pkg.payments:
        inv = db.get(Invoice, p.invoice_id)
        sign = Decimal(-1) if p.kind in ("devolucion", "reembolso") else Decimal(1)
        fx_src = inv.fx_sell if inv is not None and (inv.currency or "").upper() == (p.currency or "").upper() else None
        add(ws, [p.paid_at.strftime("%d/%m/%Y"), inv.number if inv else "", _customer_name(db, tid, inv.customer_id if inv else None),
                 {"captura": "Cobro", "devolucion": "Devolución", "reembolso": "Reembolso"}.get(p.kind, p.kind), METHOD_LABELS.get(p.method, p.method),
                 p.external_ref or "", _bank_name(db, tid, p.bank_account_id), p.currency, q2(D(p.amount) * sign),
                 _crc(db, D(p.amount) * sign, p.currency, fx_src, p.paid_at), _clean(p.notes, 300), obs("payment", p.id)], (9, 10))

    # --- Pagos a proveedores: pagos de CxP + gastos de contado ---
    ws = sheet("Pagos a proveedores", ["Fecha", "Proveedor", "Origen", "Medio", "Referencia / cheque", "Banco", "Moneda", "Monto", "Monto ₡",
                                       "Facturas pagadas", "Observación"])
    applied: set[int] = set()
    for sp in db.scalars(select(SupplierPayment).where(SupplierPayment.tenant_id == tid, SupplierPayment.status == "registrado")):
        applied.update(a.expense_id for a in sp.applications)
    for sp in pkg.supplier_payments:
        exps = [db.get(Expense, a.expense_id) for a in sp.applications]
        add(ws, [sp.date.strftime("%d/%m/%Y"), _supplier_name(db, tid, sp.supplier_id), f"Pago CxP #{sp.id}", METHOD_LABELS.get(sp.method, sp.method),
                 sp.reference or "", _bank_name(db, tid, sp.bank_account_id), sp.currency, q2(sp.amount),
                 _crc(db, sp.amount, sp.currency, sp.fx_rate, sp.date),
                 ", ".join(f"{x.reference or f'G-{x.id}'} ({money(a.amount)})" for x, a in zip(exps, sp.applications, strict=False) if x is not None),
                 obs("supplier_payment", sp.id)], (8, 9))
    for e in pkg.expenses:
        if not expense_is_paid_at_once(e, applied):
            continue
        paid = getattr(e, "paid_date", None) or e.date
        add(ws, [paid.strftime("%d/%m/%Y"), _supplier_name(db, tid, e.supplier_id), f"Gasto de contado #{e.id}",
                 METHOD_LABELS.get(e.payment_method or "", e.payment_method or ""), e.check_number or e.reference or "",
                 _bank_name(db, tid, e.bank_account_id), e.currency, q2(e.total), _crc(db, e.total, e.currency, e.fx_rate, e.date),
                 e.reference or f"G-{e.id}", obs("expense", e.id)], (8, 9))

    # --- Notas de crédito (no se importan: se registran a mano en QBO) ---
    ws = sheet("Notas de crédito", ["Fecha", "Consecutivo", "Clave", "Factura original", "Cliente", "Moneda", "Total factura original", "Nota"])
    for nc in pkg.credit_notes:
        inv = db.get(Invoice, nc.invoice_id)
        add(ws, [(nc_date(nc) or pkg.a).strftime("%d/%m/%Y"), nc.consecutive, nc.clave or "", inv.number if inv else "",
                 _customer_name(db, tid, inv.customer_id if inv else None), inv.currency if inv else "", q2(inv.total) if inv else None,
                 "QuickBooks no importa notas de crédito: registrala a mano (el monto exacto está en el XML)"], (7,))

    # --- D-104 (referencial) ---
    t = d104_totals(db, tid, pkg.month)
    ws = sheet("D-104 (referencial)", ["Concepto", "Tarifa", "Base ₡", "IVA ₡", "IVA acreditable ₡", "IVA no acreditable ₡"])
    for k in sorted(t["ventas"], key=_rate_sort):
        add(ws, ["Ventas", tax_code_label(k), q2(t["ventas"][k][0]), q2(t["ventas"][k][1]), None, None], (3, 4))
    for k in sorted(t["nc"], key=_rate_sort):
        add(ws, ["Notas de crédito (resta)", tax_code_label(k), -q2(t["nc"][k][0]), -q2(t["nc"][k][1]), None, None], (3, 4))
    for k in sorted(t["compras"], key=_rate_sort):
        v = t["compras"][k]
        add(ws, ["Compras y gastos", tax_code_label(k), q2(v[0]), q2(v[1]), q2(v[2]), q2(v[3])], (3, 4, 5, 6))
    add(ws, ["IVA débito (ventas - NC)", "", None, q2(t["debito"]), None, None], (4,))
    add(ws, ["IVA crédito (acreditable)", "", None, None, q2(t["credito"]), None], (5,))
    add(ws, ["Diferencia (a pagar si es positiva)", "", None, q2(t["neto"]), None, None], (4,))
    ws.append(["Totales de TODO el mes en colones, para cotejar con la D-104. La declaración la prepara y presenta la contadora "
               "(prorrata, bienes de capital y casos especiales no se calculan aquí)."])

    # --- Ajustes ---
    ws = sheet("Ajustes", ["Documento", "Fecha", "Motivo", "Total en el paquete anterior", "Total ahora", "Moneda", "Qué hacer en QuickBooks"])
    for ch in pkg.changes:
        add(ws, [ch.get("label") or f"{ch['kind']} #{ch['id']}", ch["date"].strftime("%d/%m/%Y") if ch.get("date") else "", ch["reason"],
                 ch.get("before"), ch.get("now"), ch.get("currency") or "",
                 "Anularlo o eliminarlo" if ch.get("now") is None else "Editarlo para que coincida (no lo reimportés: se duplicaría)"])
    for kind, oid in sorted(pkg.late):
        ws.append([f"{kind} #{oid}", "", "Documento nuevo con fecha de un mes que ya tuvo paquete (va en los CSV de este paquete)", "", "", "",
                   "Importarlo; revisá que el período no esté cerrado en QBO"])

    # --- Contactos ---
    ws = sheet("Contactos", ["Tipo", "Nombre", "Cédula", "Estado", "Correo", "Teléfono"])
    for c in pkg.customers_new:
        ws.append(["Cliente", c.name, c.id_number or "", "Nuevo", c.email or "", c.phone or ""])
    for c in pkg.customers_changed:
        ws.append(["Cliente", c.name, c.id_number or "", "Cambió (actualizar en QBO)", c.email or "", c.phone or ""])
    for s in pkg.suppliers_new:
        ws.append(["Proveedor", s.name, s.tax_id or "", "Nuevo", s.email or "", s.phone or ""])
    for s in pkg.suppliers_changed:
        ws.append(["Proveedor", s.name, s.tax_id or "", "Cambió (actualizar en QBO)", s.email or "", s.phone or ""])

    # --- Respaldos ---
    ws = sheet("Respaldos", ["Archivo", "Documento", "Incluido"])
    for b in backups or []:
        ws.append([b["path"], b["doc"], "Sí" if b["included"] else b.get("reason", "No")])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Respaldos (XML / PDF guardados)
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "archivo"


def backup_files(db: Session, pkg: Package):
    """Genera (ruta, documento, bytes) de los XML/PDF guardados de los documentos del paquete."""
    from .media_access import key_of
    from .media_store import read

    inv_ids = [i.id for i in pkg.invoices]
    if inv_ids:
        for doc in db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == pkg.tenant_id, EInvoiceDocument.invoice_id.in_(inv_ids))):
            if doc.doc_type == "NC" or not doc.xml_document:
                continue
            base = _safe(f"{doc.doc_type}_{doc.consecutive or doc.id}")
            yield f"respaldos/ventas/{base}.xml", f"{doc.doc_type} {doc.consecutive}", doc.xml_document.encode("utf-8")
            if doc.xml_response:
                yield f"respaldos/ventas/{base}_respuesta_hacienda.xml", f"{doc.doc_type} {doc.consecutive}", doc.xml_response.encode("utf-8")
    for nc in pkg.credit_notes:
        if nc.xml_document:
            base = _safe(f"NC_{nc.consecutive or nc.id}")
            yield f"respaldos/ventas/{base}.xml", f"NC {nc.consecutive}", nc.xml_document.encode("utf-8")
            if nc.xml_response:
                yield f"respaldos/ventas/{base}_respuesta_hacienda.xml", f"NC {nc.consecutive}", nc.xml_response.encode("utf-8")
    for e in pkg.expenses:
        rd = _received_doc(db, e)
        label = f"Gasto #{e.id}"
        if rd is not None:
            base = _safe(f"gasto{e.id}_{rd.consecutive or rd.clave}")
            if rd.xml_document:
                yield f"respaldos/compras/{base}.xml", label, rd.xml_document.encode("utf-8")
            if rd.xml_supplier_response:
                yield f"respaldos/compras/{base}_respuesta_hacienda.xml", label, rd.xml_supplier_response.encode("utf-8")
            if rd.pdf_media_id:
                m = db.get(Media, rd.pdf_media_id)
                if m is not None and m.tenant_id == e.tenant_id:
                    data = read(m)
                    if data:
                        yield f"respaldos/compras/{base}.pdf", label, data
        k = key_of(e.attachment_url)
        if k:
            m = db.scalar(select(Media).where(Media.key == k, Media.tenant_id == e.tenant_id))
            if m is not None:
                data = read(m)
                if data:
                    yield f"respaldos/compras/gasto{e.id}_adjunto_{_safe(m.filename)}", label, data


# ---------------------------------------------------------------------------
# ZIP
# ---------------------------------------------------------------------------

README = """PAQUETE MENSUAL PARA QUICKBOOKS ONLINE — {month}
Empresa: {tenant}
Generado por {who} el {when}

Crimson no escribe en QuickBooks. Revisá el Excel de control y después importá los CSV en este orden
(Configuración ⚙ → Importar datos), mapeando las columnas cuando QuickBooks lo pida:

  1. Clientes        1_clientes.csv  (si existe 1_clientes_actualizar.csv: son clientes que ya están en QBO y cambiaron;
                     actualizalos a mano o importalos con «Sobrescribir»)
  2. Proveedores     2_proveedores.csv  (igual con 2_proveedores_actualizar.csv)
  3. Facturas        3_facturas_venta*.csv  → «Facturas». Opción de impuestos: SIN impuestos incluidos (Exclusive).
                     Formato de fecha: {fmt}. Emparejá los códigos «IVA 13%», «IVA 4%», «IVA 2%», «IVA 1%», «IVA 0%» y
                     «Exento» con tus códigos de impuesto de QBO.
  4. Facturas de     4_facturas_proveedor*.csv → «Facturas de proveedor» (Bills). Mismo formato de fecha y opción de
     proveedor       impuestos. «IVA no acreditable» se empareja con un código de 0 % / sin impuesto: ese IVA ya va
                     sumado al gasto.

No van por importación (QBO no los importa por archivo): cobros, pagos a proveedores y notas de crédito. Están en el
Excel (hojas Cobros, Pagos a proveedores, Notas de crédito) con fecha, medio, referencia y banco para registrarlos o
conciliarlos con el banco. La hoja «Ajustes» lista lo que cambió después de un paquete anterior: se corrige a mano en
QBO (no se reimporta, se duplicaría). La carpeta respaldos/ trae los XML y PDF guardados.

Límites de QuickBooks: hasta 100 facturas y 1000 filas por archivo (por eso puede haber varios archivos numerados).
"""


def build_zip(db: Session, pkg: Package, *, tenant_name: str, generated_by: str, fmt_key: str, fmt: str) -> tuple[SpooledTemporaryFile, int]:
    """Arma el ZIP en un archivo temporal (en disco si pasa de 8 MB). Devuelve (archivo al inicio, filas de importación)."""
    names = _account_names(db, pkg.tenant_id)
    out = SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115 - lo cierra la respuesta al terminar de enviarlo
    rows_total = 0
    backups: list[dict] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        def write_set(prefix: str, headers: list[str], docs: list[list[dict]]) -> None:
            nonlocal rows_total
            files = _chunks([d for d in docs if d])
            for i, rows in enumerate(files, start=1):
                suffix = f"_{i}" if len(files) > 1 else ""
                zf.writestr(f"{prefix}{suffix}.csv", _csv(headers, rows))
                rows_total += len(rows)

        if pkg.customers_new:
            zf.writestr("1_clientes.csv", _csv(CUSTOMER_HEADERS, customer_rows(db, pkg, pkg.customers_new)))
        if pkg.customers_changed:
            zf.writestr("1_clientes_actualizar.csv", _csv(CUSTOMER_HEADERS, customer_rows(db, pkg, pkg.customers_changed)))
        if pkg.suppliers_new:
            zf.writestr("2_proveedores.csv", _csv(SUPPLIER_HEADERS, supplier_rows(pkg, pkg.suppliers_new)))
        if pkg.suppliers_changed:
            zf.writestr("2_proveedores_actualizar.csv", _csv(SUPPLIER_HEADERS, supplier_rows(pkg, pkg.suppliers_changed)))
        write_set("3_facturas_venta", INVOICE_HEADERS, [invoice_rows(db, inv, fmt) for inv in pkg.invoices])
        write_set("4_facturas_proveedor", BILL_HEADERS, [bill_rows(db, e, fmt, names) for e in pkg.expenses])

        used = 0
        for path, doc, data in backup_files(db, pkg):
            if used + len(data) > MAX_BACKUP_BYTES:
                backups.append({"path": path, "doc": doc, "included": False, "reason": "No: el paquete llegó al tope de tamaño"})
                continue
            zf.writestr(path, data)
            used += len(data)
            backups.append({"path": path, "doc": doc, "included": True})

        zf.writestr(f"control_{pkg.month}.xlsx", build_control_xlsx(db, pkg, tenant_name=tenant_name, generated_by=generated_by,
                                                                    fmt_date=fmt_key, backups=backups))
        zf.writestr("LEEME.txt", README.format(month=pkg.month, tenant=tenant_name, who=generated_by, fmt=fmt_key,
                                               when=datetime.now(CR).strftime("%d/%m/%Y %H:%M")).encode("utf-8"))
    out.seek(0)
    return out, rows_total
