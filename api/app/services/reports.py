"""Reportes (plan 3.6): cada uno devuelve columnas + filas; el router los sirve como JSON o Excel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, Expense, Invoice, InvoiceLine, Payment, Product, StockMovement, Warehouse
from .inventory import stock_levels


@dataclass
class Report:
    key: str
    title: str
    columns: list[str]
    rows: list[list]
    totals: dict | None = None


def _cust(db: Session, cid: int | None) -> str:
    if not cid:
        return "—"
    c = db.get(Customer, cid)
    return c.name if c else "—"


def facturacion(db: Session, tid: int, a: date, b: date) -> Report:
    rows = db.scalars(
        select(Invoice)
        .where(Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b, Invoice.status != "anulada")
        .order_by(Invoice.issue_date, Invoice.id)
    ).all()
    return Report(
        "facturacion",
        "Facturación",
        ["Fecha", "Número", "Cliente", "Divisa", "Subtotal", "Descuento", "Impuesto", "Total", "Saldo", "Estado"],
        [
            [i.issue_date.isoformat(), i.number, _cust(db, i.customer_id), i.currency, i.subtotal, i.discount_total, i.tax_total, i.total, i.balance, i.status]
            for i in rows
        ],
        {"total": sum((Decimal(str(i.total)) for i in rows), Decimal(0)), "saldo": sum((Decimal(str(i.balance)) for i in rows), Decimal(0)), "n": len(rows)},
    )


def pendientes(db: Session, tid: int, a: date, b: date) -> Report:
    rows = db.scalars(
        select(Invoice)
        .where(Invoice.tenant_id == tid, Invoice.status.in_(("creado", "parcial", "vencida", "enviada")), Invoice.balance > 0)
        .order_by(Invoice.due_date)
    ).all()
    today = date.today()
    return Report(
        "pendientes",
        "Facturas pendientes",
        ["Número", "Cliente", "Emisión", "Vence", "Días vencida", "Divisa", "Total", "Saldo", "Estado"],
        [
            [
                i.number,
                _cust(db, i.customer_id),
                i.issue_date.isoformat(),
                i.due_date.isoformat() if i.due_date else "",
                max(0, (today - i.due_date).days) if i.due_date else 0,
                i.currency,
                i.total,
                i.balance,
                i.status,
            ]
            for i in rows
        ],
        {"saldo": sum((Decimal(str(i.balance)) for i in rows), Decimal(0)), "n": len(rows)},
    )


def transacciones(db: Session, tid: int, a: date, b: date) -> Report:
    rows = db.scalars(select(Payment).where(Payment.tenant_id == tid, Payment.paid_at >= a, Payment.paid_at <= b).order_by(Payment.paid_at, Payment.id)).all()
    inv = {i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_([p.invoice_id for p in rows] or [0])))}
    return Report(
        "transacciones",
        "Transacciones",
        ["Fecha", "Factura", "Cliente", "Método", "Tipo", "Referencia", "Divisa", "Monto", "Propina", "Proveedor", "Estado"],
        [
            [
                p.paid_at.isoformat(),
                inv[p.invoice_id].number if p.invoice_id in inv else "",
                _cust(db, inv[p.invoice_id].customer_id) if p.invoice_id in inv else "",
                p.method,
                p.kind,
                p.external_ref or "",
                p.currency,
                p.amount,
                p.tip,
                p.provider,
                p.status,
            ]
            for p in rows
        ],
        {"capturado": sum((Decimal(str(p.amount)) for p in rows if p.kind == "captura" and p.status == "confirmado"), Decimal(0)), "n": len(rows)},
    )


def cierre_diario(db: Session, tid: int, a: date, b: date) -> Report:
    q = (
        select(Payment.paid_at, Payment.method, Payment.currency, func.sum(Payment.amount), func.count())
        .where(Payment.tenant_id == tid, Payment.paid_at >= a, Payment.paid_at <= b, Payment.kind == "captura", Payment.status == "confirmado")
        .group_by(Payment.paid_at, Payment.method, Payment.currency)
        .order_by(Payment.paid_at, Payment.method)
    )
    rows = [[r[0].isoformat(), r[1], r[2], r[3], r[4]] for r in db.execute(q)]
    return Report(
        "cierre", "Cierre diario", ["Fecha", "Método", "Divisa", "Monto", "Pagos"], rows, {"total": sum((Decimal(str(r[3])) for r in rows), Decimal(0))}
    )


def impuesto_facturado(db: Session, tid: int, a: date, b: date) -> Report:
    q = (
        select(InvoiceLine.tax_rate, func.sum(InvoiceLine.subtotal), func.sum(InvoiceLine.tax_amount), func.count())
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b, Invoice.status != "anulada")
        .group_by(InvoiceLine.tax_rate)
        .order_by(InvoiceLine.tax_rate.desc())
    )
    rows = [[f"{Decimal(str(r[0])).normalize()}%", r[1], r[2], r[3]] for r in db.execute(q)]
    return Report(
        "impuesto",
        "Impuesto facturado (por tarifa)",
        ["Tarifa", "Base imponible", "IVA", "Líneas"],
        rows,
        {"iva": sum((Decimal(str(r[2])) for r in rows), Decimal(0))},
    )


def iva(db: Session, tid: int, a: date, b: date) -> Report:
    deb = db.scalar(
        select(func.coalesce(func.sum(Invoice.tax_total), 0)).where(
            Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b, Invoice.status != "anulada"
        )
    )
    cred = db.scalar(
        select(func.coalesce(func.sum(Expense.tax_amount), 0)).where(
            Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado", Expense.iva_credit == "credito"
        )
    )
    prop = db.scalar(
        select(func.coalesce(func.sum(Expense.tax_amount), 0)).where(
            Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado", Expense.iva_credit == "proporcional"
        )
    )
    d, c, p = Decimal(str(deb)), Decimal(str(cred)), Decimal(str(prop))
    rows = [
        ["IVA débito (ventas)", d],
        ["IVA crédito (compras, genera crédito)", c],
        ["IVA compras proporcional (prorrata)", p],
        ["IVA a pagar (débito − crédito)", d - c],
    ]
    return Report("iva", "IVA · borrador D-104", ["Concepto", "Monto"], rows, {"a_pagar": d - c})


def gastos(db: Session, tid: int, a: date, b: date) -> Report:
    rows = db.scalars(
        select(Expense).where(Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado").order_by(Expense.date)
    ).all()
    return Report(
        "gastos",
        "Gastos",
        ["Fecha", "Descripción", "Divisa", "Subtotal", "IVA", "Total", "Crédito IVA", "Referencia", "Estado"],
        [[e.date.isoformat(), e.description, e.currency, e.subtotal, e.tax_amount, e.total, e.iva_credit, e.reference or "", e.status] for e in rows],
        {"total": sum((Decimal(str(e.total)) for e in rows), Decimal(0)), "n": len(rows)},
    )


def estado_resultados(db: Session, tid: int, a: date, b: date) -> Report:
    ventas = Decimal(
        str(
            db.scalar(
                select(func.coalesce(func.sum(Invoice.subtotal - Invoice.discount_total), 0)).where(
                    Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b, Invoice.status != "anulada"
                )
            )
        )
    )
    gastos_ = Decimal(
        str(
            db.scalar(
                select(func.coalesce(func.sum(Expense.subtotal), 0)).where(
                    Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado"
                )
            )
        )
    )
    return Report(
        "resultados",
        "Estado de resultados (sin IVA)",
        ["Concepto", "Monto"],
        [["Ventas netas", ventas], ["Gastos", gastos_], ["Resultado", ventas - gastos_]],
        {"resultado": ventas - gastos_},
    )


def inventario(db: Session, tid: int, a: date, b: date) -> Report:
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == tid, Product.item_type == "producto"))}
    whs = {w.id: w for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == tid))}
    rows = []
    for lv in stock_levels(db, tid):
        p, w = prods.get(lv["product_id"]), whs.get(lv["warehouse_id"])
        if p:
            rows.append(
                [p.code, p.name, w.name if w else "", lv["quantity"], p.min_stock, p.price, p.currency, "BAJO" if lv["quantity"] <= p.min_stock else "OK"]
            )
    return Report("inventario", "Inventario", ["Código", "Producto", "Inventario", "Cantidad", "Mínimo", "Precio", "Divisa", "Alerta"], rows, {"n": len(rows)})


def venta_productos(db: Session, tid: int, a: date, b: date) -> Report:
    q = (
        select(InvoiceLine.code, InvoiceLine.name, func.sum(InvoiceLine.quantity), func.sum(InvoiceLine.subtotal), func.count(func.distinct(Invoice.id)))
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b, Invoice.status != "anulada")
        .group_by(InvoiceLine.code, InvoiceLine.name)
        .order_by(func.sum(InvoiceLine.subtotal).desc())
    )
    rows = [[r[0] or "", r[1], r[2], r[3], r[4]] for r in db.execute(q)]
    return Report("productos", "Venta de productos", ["Código", "Producto", "Cantidad", "Venta neta", "Facturas"], rows, {"n": len(rows)})


def movimientos(db: Session, tid: int, a: date, b: date) -> Report:
    rows = db.scalars(select(StockMovement).where(StockMovement.tenant_id == tid).order_by(StockMovement.at.desc()).limit(500)).all()
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == tid))}
    whs = {w.id: w for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == tid))}
    return Report(
        "movimientos",
        "Movimientos de inventario",
        ["Fecha", "Producto", "Inventario", "Tipo", "Cantidad", "Referencia"],
        [
            [
                m.at.strftime("%Y-%m-%d %H:%M"),
                prods[m.product_id].name if m.product_id in prods else "",
                whs[m.warehouse_id].name if m.warehouse_id in whs else "",
                m.kind,
                m.quantity,
                m.reference or "",
            ]
            for m in rows
        ],
    )


REPORTS = {
    "facturacion": facturacion,
    "pendientes": pendientes,
    "transacciones": transacciones,
    "cierre": cierre_diario,
    "impuesto": impuesto_facturado,
    "iva": iva,
    "gastos": gastos,
    "resultados": estado_resultados,
    "inventario": inventario,
    "productos": venta_productos,
    "movimientos": movimientos,
}
CATALOG = [
    ("facturacion", "Facturación", "Todas las facturas del periodo con totales y saldo"),
    ("pendientes", "Facturas pendientes", "Por cobrar, con días de atraso"),
    ("impuesto", "Impuesto facturado", "Base e IVA por tarifa"),
    ("resultados", "Estado de resultados", "Ventas netas menos gastos"),
    ("cierre", "Cierre diario", "Cobros por día y método"),
    ("transacciones", "Transacciones", "Cada pago con método, tipo y referencia"),
    ("gastos", "Gastos", "Egresos registrados en contabilidad"),
    ("iva", "IVA", "Débito, crédito y prorrata · borrador D-104"),
    ("inventario", "Inventario", "Existencias por ubicación y alertas"),
    ("productos", "Venta de productos", "Ranking de productos y servicios"),
    ("movimientos", "Movimientos", "Ledger de inventario"),
]


def to_xlsx(rep: Report, a: date, b: date, tenant_name: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = rep.title[:30]
    ws["A1"] = f"{tenant_name} · {rep.title}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Periodo {a.isoformat()} a {b.isoformat()}"
    ws["A2"].font = Font(color="8A858F")
    ws.append([])
    ws.append(rep.columns)
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="15131A")
        c.alignment = Alignment(vertical="center")
    for r in rep.rows:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in r])
    for i, col in enumerate(rep.columns, 1):
        width = max(len(str(col)), *(len(str(r[i - 1])) for r in rep.rows[:200])) if rep.rows else len(col)
        ws.column_dimensions[get_column_letter(i)].width = min(48, width + 3)
    ws.freeze_panes = "A5"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
