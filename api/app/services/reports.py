"""Reportes (plan 3.6): cada uno devuelve columnas + filas; el router los sirve como JSON o Excel."""

from __future__ import annotations

import re
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


# ---------- Contabilidad y tributario (facturacion): todo en colones ----------
def _to_crc(amount, currency: str | None, fx) -> Decimal:
    return _crc(amount, currency or "CRC", fx)


def _orate(db: Session, o) -> Decimal:
    """Tasa de un documento guardado (factura: fx_sell; gasto y comprobante recibido: fx_rate). Si le falta o quedo en
    1 (el sistema viejo caia a 1 sin tasa), la del BCCR de su fecha (services/fx.doc_rate); sin ninguna, FxMissing."""
    from .fx import doc_rate

    stored = o.fx_sell if hasattr(o, "fx_sell") else getattr(o, "fx_rate", None)
    on = getattr(o, "issue_date", None) or getattr(o, "date", None)
    return doc_rate(db, o.currency, stored, on)


def _q2(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


def _last_responses(db: Session, ids: list[int]) -> dict:
    from ..models import ReceptionResponse

    out = {}
    if not ids:
        return out
    for r in db.scalars(select(ReceptionResponse).where(ReceptionResponse.received_document_id.in_(ids)).order_by(ReceptionResponse.attempt)):
        out[r.received_document_id] = r
    return out


def _sign(doc) -> Decimal:
    return Decimal(-1) if doc.doc_type == "NC" else Decimal(1)


def _reception_credit(doc) -> Decimal | None:
    """IVA acreditable de una recepcion respondida (divisa del documento, positivo). None = proporcional."""
    if (doc.iva_condition or "credito") == "proporcional":
        return None
    if doc.credit_amount is not None:
        return Decimal(str(doc.credit_amount))
    if (doc.iva_condition or "credito") in ("credito", "bienes_capital"):
        return Decimal(str(doc.iva_total if doc.iva_total is not None else doc.tax_total or 0))
    return Decimal(0)


def _expense_credit(e) -> Decimal | None:
    """Credito de un gasto sin comprobante ligado (filas viejas "credito" sin tax_credit_amount -> el IVA)."""
    if e.iva_credit == "proporcional":
        return None
    if e.tax_credit_amount is not None:
        return Decimal(str(e.tax_credit_amount))
    return Decimal(str(e.tax_amount or 0)) if e.iva_credit in ("credito", "bienes_capital") else Decimal(0)


def iva_breakdown(db: Session, tid: int, a: date, b: date, prorrata_pct=None) -> dict:
    """D13: el credito de IVA sale de las recepciones respondidas (el mensaje receptor es el respaldo legal), no de los
    gastos. Los gastos ligados a una recepcion no aportan credito propio (sin doble conteo); los gastos manuales sin
    comprobante van en su propia linea con aviso (art. 11). Todo en colones."""
    from ..models import EInvoiceDocument, ReceivedDocument
    from .documents import local_date

    # ---- debito: facturas del periodo (las anuladas por NC siguen en su mes; la NC resta en el suyo)
    deb = Decimal(0)
    nc_inv = {
        d.invoice_id: d
        for d in db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == tid, EInvoiceDocument.doc_type == "NC", EInvoiceDocument.status == "aceptada"))
    }
    for inv in db.scalars(select(Invoice).where(Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b)):
        if inv.einvoice_status == "rechazada":
            continue
        if inv.status == "anulada" and inv.id not in nc_inv:
            continue
        deb += _to_crc(inv.tax_total, inv.currency, _orate(db, inv))
    for inv_id, nc in nc_inv.items():
        when = local_date(nc.created_at)  # en UTC en cualquier motor; la NC resta en el mes en que se emitio
        if when and a <= when <= b:
            inv = db.get(Invoice, inv_id)
            if inv:
                deb -= _to_crc(inv.tax_total, inv.currency, _orate(db, inv))

    # ---- credito de comprobantes electronicos respondidos
    docs = db.scalars(
        select(ReceivedDocument).where(
            ReceivedDocument.tenant_id == tid,
            ReceivedDocument.issue_date >= a,
            ReceivedDocument.issue_date <= b,
            ReceivedDocument.action.in_(("aceptada", "parcial")),
            ReceivedDocument.status.in_(("respondido", "historico")),
        )
    ).all()
    last = _last_responses(db, [d.id for d in docs])
    xml_c = por_confirmar = sin_respaldo = prop = Decimal(0)
    sin_confirmar: list[dict] = []
    for d in docs:
        r = last.get(d.id)
        if r is not None and r.status in ("rechazado", "error"):
            continue
        fx = _orate(db, d)
        credit = _reception_credit(d)
        if credit is None:  # proporcional
            if d.supplier_hacienda_status != "rechazado":
                prop += _sign(d) * _to_crc(d.iva_total if d.iva_total is not None else d.tax_total, d.currency, fx)
            continue
        amount = _sign(d) * _to_crc(credit, d.currency, fx)
        if d.supplier_hacienda_status == "rechazado":
            sin_respaldo += amount  # Hacienda se lo rechazo al proveedor: sin validez, no entra al "a pagar"
            continue
        xml_c += amount
        confirmed = d.supplier_hacienda_status == "aceptado" or (r is not None and r.status in ("aceptado", "historico") and r.mode == "produccion")
        if not confirmed:
            por_confirmar += amount
            sin_confirmar.append({"id": d.id, "issuer_name": d.issuer_name, "clave": d.clave, "credit_crc": _q2(amount)})

    # ---- gastos manuales (sin comprobante electronico vinculado)
    manual = Decimal(0)
    for e in db.scalars(
        select(Expense).where(
            Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado", Expense.received_document_id.is_(None)
        )
    ):
        c = _expense_credit(e)
        if c is None:
            prop += _to_crc(e.tax_amount, e.currency, _orate(db, e))
        else:
            manual += _to_crc(c, e.currency, _orate(db, e))
    pct = Decimal(str(prorrata_pct)) if prorrata_pct not in (None, "") else None
    cred_prorrata = (prop * pct / Decimal(100)) if pct is not None else Decimal(0)
    credito = xml_c + manual
    return {
        "debito_crc": _q2(deb),
        "credito_xml_crc": _q2(xml_c),
        "credito_por_confirmar_crc": _q2(por_confirmar),
        "credito_sin_respaldo_crc": _q2(sin_respaldo),
        "credito_manual_crc": _q2(manual),
        "credito_crc": _q2(credito),
        "proporcional_crc": _q2(prop),
        "prorrata_pct": pct,
        "credito_prorrata_crc": _q2(cred_prorrata),
        "a_pagar_crc": _q2(deb - credito - cred_prorrata),
        "sin_confirmar": sin_confirmar[:50],
    }


def iva(db: Session, tid: int, a: date, b: date) -> Report:
    from ..models import Tenant

    t = db.get(Tenant, tid)
    x = iva_breakdown(db, tid, a, b, (t.settings or {}).get("iva_prorrata_pct") if t else None)
    rows = [
        ["IVA débito (ventas)", x["debito_crc"]],
        ["IVA crédito de facturas electrónicas aceptadas", x["credito_xml_crc"]],
        ["   · de ellas, sin respuesta de Hacienda al proveedor (por confirmar)", x["credito_por_confirmar_crc"]],
        ["IVA crédito de gastos sin factura electrónica vinculada (sin comprobante aceptado no hay crédito)", x["credito_manual_crc"]],
        ["IVA crédito (compras, genera crédito)", x["credito_crc"]],
        ["Excluido: comprobantes rechazados por Hacienda al proveedor", x["credito_sin_respaldo_crc"]],
        ["IVA compras proporcional (prorrata)", x["proporcional_crc"]],
        [f"Crédito por prorrata ({x['prorrata_pct']} %)" if x["prorrata_pct"] is not None else "Crédito por prorrata (sin porcentaje)", x["credito_prorrata_crc"]],
        ["IVA a pagar (débito − crédito)", x["a_pagar_crc"]],
    ]
    return Report("iva", "IVA · borrador D-104 (en colones)", ["Concepto", "Monto ₡"], rows, {"a_pagar": x["a_pagar_crc"]})


def gastos(db: Session, tid: int, a: date, b: date) -> Report:
    from ..models import Supplier

    rows = db.scalars(
        select(Expense).where(Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado").order_by(Expense.date)
    ).all()
    sups = {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.tenant_id == tid))}
    out = []
    for e in rows:
        c = _expense_credit(e)
        out.append(
            [
                e.date.isoformat(),
                sups.get(e.supplier_id, ""),
                e.description,
                e.currency,
                e.fx_rate,
                e.subtotal,
                e.tax_amount,
                e.total,
                _q2(_to_crc(e.total, e.currency, _orate(db, e))),
                c if c is not None else "prorrata",
                e.iva_credit,
                e.reference or "",
                e.status,
            ]
        )
    return Report(
        "gastos",
        "Gastos",
        ["Fecha", "Proveedor", "Descripción", "Divisa", "TC", "Subtotal", "IVA", "Total", "Total ₡", "IVA acreditable", "Crédito IVA", "Referencia", "Estado"],
        out,
        {"total": sum((_to_crc(e.total, e.currency, _orate(db, e)) for e in rows), Decimal(0)).quantize(Decimal("0.01")), "n": len(rows)},
    )


def estado_resultados(db: Session, tid: int, a: date, b: date) -> Report:
    """Ventas y gastos en colones. Gasto = gasto aplicable (total - IVA acreditable): con condicion 04 el IVA no
    acreditable ES gasto, y los impuestos que no son IVA tambien (subtotal lo subestimaba)."""
    from ..models import Tenant

    ventas = Decimal(0)
    for inv in db.scalars(select(Invoice).where(Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b, Invoice.status != "anulada")):
        ventas += _to_crc(Decimal(str(inv.subtotal or 0)) - Decimal(str(inv.discount_total or 0)), inv.currency, _orate(db, inv))
    t = db.get(Tenant, tid)
    pct = (t.settings or {}).get("iva_prorrata_pct") if t else None
    gastos_ = Decimal(0)
    for e in db.scalars(select(Expense).where(Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado")):
        c = _expense_credit(e)
        if c is None:
            c = Decimal(str(e.tax_amount or 0)) * Decimal(str(pct or 0)) / Decimal(100)
        gastos_ += _to_crc(Decimal(str(e.total or 0)) - c, e.currency, _orate(db, e))
    ventas, gastos_ = _q2(ventas), _q2(gastos_)
    return Report(
        "resultados",
        "Estado de resultados (sin IVA acreditable, en colones)",
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
                [
                    p.code,
                    p.name,
                    w.name if w else "",
                    lv["quantity"],
                    p.min_stock,
                    p.price,
                    p.currency,
                    # revision y proveedor son equipos de clientes en garantia: no son existencia de venta
                    ("BAJO" if lv["quantity"] <= p.min_stock else "OK") if (w is None or w.kind == "venta") else "Garantía",
                ]
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


# ---------- Sesion 4: ordenes, recepciones, propinas, D-151, planilla, conciliacion ----------
def _crc(amount, currency: str, fx) -> Decimal:
    """Monto en colones con la tasa ya resuelta (_orate / services/fx). Nunca multiplica por 1 a otra moneda."""
    from .fx import FxMissing

    v = Decimal(str(amount or 0))
    if currency == "CRC":
        return v
    rate = Decimal(str(fx or 0))
    if rate <= 0:
        raise FxMissing(currency)
    return v * rate


def ordenes(db: Session, tid: int, a: date, b: date) -> Report:
    from datetime import datetime as dt

    from ..models import Order

    lo, hi = dt.combine(a, dt.min.time()), dt.combine(b, dt.max.time())
    rows = db.scalars(select(Order).where(Order.tenant_id == tid, Order.created_at >= lo, Order.created_at <= hi).order_by(Order.id)).all()
    return Report(
        "ordenes",
        "Órdenes",
        ["Fecha", "Orden", "Canal", "Cliente", "Envío", "Pago", "Estado", "Total", "Comprobante"],
        [
            [
                o.created_at.date().isoformat(),
                o.number,
                o.channel,
                (o.contact or {}).get("name", ""),
                o.shipping_method or "",
                o.payment_method or "",
                o.status,
                o.total,
                o.invoice_id or "",
            ]
            for o in rows
        ],
        {"Total": sum((Decimal(str(o.total)) for o in rows if o.status != "cancelado"), Decimal(0)), "Órdenes": len(rows)},
    )


def propinas(db: Session, tid: int, a: date, b: date) -> Report:
    rows = db.scalars(
        select(Payment).where(Payment.tenant_id == tid, Payment.paid_at >= a, Payment.paid_at <= b, Payment.tip > 0).order_by(Payment.paid_at)
    ).all()
    invs = {i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_({r.invoice_id for r in rows})))} if rows else {}
    return Report(
        "propinas",
        "Propinas",
        ["Fecha", "Factura", "Método", "Monto cobrado", "Propina"],
        [[r.paid_at.isoformat(), invs[r.invoice_id].number if r.invoice_id in invs else "", r.method, r.amount, r.tip] for r in rows],
        {"Propinas": sum((Decimal(str(r.tip)) for r in rows), Decimal(0))},
    )


def recepciones(db: Session, tid: int, a: date, b: date) -> Report:
    from ..models import ReceivedDocument

    rows = db.scalars(
        select(ReceivedDocument)
        .where(ReceivedDocument.tenant_id == tid, ReceivedDocument.issue_date >= a, ReceivedDocument.issue_date <= b)
        .order_by(ReceivedDocument.issue_date)
    ).all()
    out_ = ("rechazada",)
    vivas = [r for r in rows if r.action not in out_ and r.status not in ("descartado", "ajeno", "invalido")]
    return Report(
        "recepciones",
        "Recepciones",
        [
            "Fecha",
            "Tipo",
            "Emisor",
            "Cédula",
            "Consecutivo",
            "Moneda",
            "Subtotal",
            "IVA",
            "Total",
            "Actividad",
            "Condición IVA",
            "IVA acreditable",
            "Gasto aplicable",
            "Respuesta",
            "Estado",
            "Hacienda",
            "Respuesta del proveedor",
        ],
        [
            [
                r.issue_date.isoformat() if r.issue_date else "",
                r.doc_type,
                r.issuer_name or "",
                r.issuer_id or "",
                r.consecutive or "",
                r.currency,
                r.subtotal,
                r.tax_total,
                r.total,
                r.activity_code or "",
                r.iva_condition if r.action else "",
                r.credit_amount if r.credit_amount is not None else "",
                r.applicable_amount if r.applicable_amount is not None else "",
                r.action or "pendiente",
                r.status,
                r.hacienda_status,
                r.supplier_hacienda_status,
            ]
            for r in rows
        ],
        {
            "IVA ₡": sum((_sign(r) * _to_crc(r.tax_total, r.currency, _orate(db, r)) for r in vivas), Decimal(0)).quantize(Decimal("0.01")),
            "Total ₡": sum((_sign(r) * _to_crc(r.total, r.currency, _orate(db, r)) for r in vivas), Decimal(0)).quantize(Decimal("0.01")),
        },
    )


D151_THRESHOLD = Decimal(2_500_000)
D151_SPECIFIC = Decimal(50_000)
SPECIFIC_WORDS = ("alquiler", "honorario", "comision", "servicios profesionales", "interes")


def d151(db: Session, tid: int, a: date, b: date) -> Report:
    """D-270 / D-151: operaciones SIN comprobante electronico (borrador). Desde DGT-R-071-2019 lo respaldado por
    comprobantes electronicos no se informa, y el D-270 (MH-DGT-RES-0055-2025) reporta justo lo no respaldado. Por eso
    quedan fuera las compras recibidas por XML y los gastos ligados a ellas. Umbrales y periodicidad: a confirmar con
    contabilidad; se muestran los montos sin filtrar, con la columna "Supera umbral D-151"."""
    from ..models import ExpenseCategory, Supplier

    rows: list[list] = []
    buys: dict[tuple, Decimal] = {}
    specific: dict[tuple, tuple[Decimal, str]] = {}
    cats = {c.id: c for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.tenant_id == tid))}
    for e in db.scalars(
        select(Expense).where(
            Expense.tenant_id == tid,
            Expense.date >= a,
            Expense.date <= b,
            Expense.status != "anulado",
            Expense.supplier_id.is_not(None),
            Expense.received_document_id.is_(None),
            Expense.received_clave.is_(None),
        )
    ):
        s = db.get(Supplier, e.supplier_id)
        if not s or not s.tax_id:
            continue
        k = (s.tax_id, s.name)
        amt = _to_crc(e.subtotal, e.currency, _orate(db, e))
        cat = cats.get(e.category_id)
        kind = cat.d151_kind if cat and cat.d151_kind else None
        if kind is None:
            text = ((cat.name if cat else "") + " " + e.description).lower()
            kind = next((w for w in SPECIFIC_WORDS if w in text), None)
        if kind:
            prev = specific.get(k, (Decimal(0), kind))
            specific[k] = (prev[0] + amt, prev[1])
        else:
            buys[k] = buys.get(k, Decimal(0)) + amt
    for (idn, name), amt in sorted(buys.items(), key=lambda x: -x[1]):
        rows.append(["Compras (proveedores) sin comprobante electrónico", idn, name, "", amt.quantize(Decimal("0.01")), "Sí" if amt > D151_THRESHOLD else "No"])
    for (idn, name), (amt, kind) in sorted(specific.items(), key=lambda x: -x[1][0]):
        rows.append(["Gastos específicos", idn, name, kind.replace("_", " "), amt.quantize(Decimal("0.01")), "Sí" if amt > D151_SPECIFIC else "No"])
    return Report(
        "d151",
        "D-270 / D-151: operaciones sin comprobante electrónico (borrador)",
        ["Sección", "Identificación", "Nombre", "Tipo de gasto", "Monto ₡", "Supera umbral D-151"],
        rows,
        {"Registros": len(rows)},
    )


def compras(db: Session, tid: int, a: date, b: date) -> Report:
    """Libro de compras: comprobantes aceptados o parciales + gastos manuales con proveedor, con su respaldo."""
    from ..models import ReceivedDocument, Supplier

    docs = db.scalars(
        select(ReceivedDocument)
        .where(
            ReceivedDocument.tenant_id == tid,
            ReceivedDocument.issue_date >= a,
            ReceivedDocument.issue_date <= b,
            ReceivedDocument.action.in_(("aceptada", "parcial")),
            ReceivedDocument.status.in_(("respondido", "historico")),
        )
        .order_by(ReceivedDocument.issue_date)
    ).all()
    last = _last_responses(db, [d.id for d in docs])
    rows = []
    tot = {"base": Decimal(0), "iva": Decimal(0), "cred": Decimal(0), "apl": Decimal(0)}
    for d in docs:
        r = last.get(d.id)
        fx, s = _orate(db, d), _sign(d)
        aceptado = d.supplier_hacienda_status == "aceptado" or (r is not None and r.mode == "produccion" and r.status in ("aceptado", "historico"))
        respaldo = "XML aceptado" if aceptado else ("XML rechazado al proveedor" if d.supplier_hacienda_status == "rechazado" else "XML sin respuesta de Hacienda")
        base = s * _to_crc(Decimal(str(d.total or 0)) - Decimal(str(d.tax_total or 0)), d.currency, fx)
        ivac = s * _to_crc(d.tax_total, d.currency, fx)
        cred = s * _to_crc(d.credit_amount or 0, d.currency, fx)
        apl = s * _to_crc(d.applicable_amount or 0, d.currency, fx)
        rows.append([d.issue_date.isoformat() if d.issue_date else "", d.issuer_name or "", d.issuer_id or "", d.consecutive or d.clave, d.doc_type, d.currency, fx, _q2(base), _q2(ivac), _q2(cred), _q2(apl), d.iva_condition, d.activity_code or "", respaldo])
        tot["base"] += base
        tot["iva"] += ivac
        tot["cred"] += cred
        tot["apl"] += apl
    sups = {x.id: x for x in db.scalars(select(Supplier).where(Supplier.tenant_id == tid))}
    for e in db.scalars(
        select(Expense)
        .where(Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado", Expense.supplier_id.is_not(None), Expense.received_document_id.is_(None))
        .order_by(Expense.date)
    ):
        sup = sups.get(e.supplier_id)
        c = _expense_credit(e) or Decimal(0)
        base = _to_crc(e.subtotal, e.currency, _orate(db, e))
        ivac = _to_crc(e.tax_amount, e.currency, _orate(db, e))
        cred = _to_crc(c, e.currency, _orate(db, e))
        apl = _to_crc(Decimal(str(e.total or 0)) - c, e.currency, _orate(db, e))
        rows.append([e.date.isoformat(), sup.name if sup else "", sup.tax_id if sup else "", e.reference or e.description[:40], "Gasto", e.currency, e.fx_rate, _q2(base), _q2(ivac), _q2(cred), _q2(apl), e.iva_credit, "", "Sin XML"])
        tot["base"] += base
        tot["iva"] += ivac
        tot["cred"] += cred
        tot["apl"] += apl
    return Report(
        "compras",
        "Libro de compras",
        ["Fecha", "Proveedor", "Cédula", "Documento", "Tipo", "Moneda", "TC", "Base ₡", "IVA ₡", "IVA acreditable ₡", "Gasto aplicable ₡", "Condición", "Actividad", "Respaldo"],
        rows,
        {"Base ₡": _q2(tot["base"]), "IVA ₡": _q2(tot["iva"]), "IVA acreditable ₡": _q2(tot["cred"]), "Gasto aplicable ₡": _q2(tot["apl"])},
    )


def tax_summary(db: Session, tenant, month: str) -> dict:
    """Panel tributario del mes (GET /accounting/tax-summary). Sin costos ni margenes (D10)."""
    from datetime import timedelta

    from ..models import EInvoiceDocument, FiscalCredential, ReceivedDocument, ReceptionMailbox, ReceptionResponse
    from . import hacienda_config as hc
    from .reception_calc import business_days_left

    y, m = (int(x) for x in month.split("-"))
    a = date(y, m, 1)
    b = (a.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    tid = tenant.id
    x = iva_breakdown(db, tid, a, b, (tenant.settings or {}).get("iva_prorrata_pct"))
    sin_confirmar = x.pop("sin_confirmar")
    today = date.today()
    del_mes = db.scalars(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tid, ReceivedDocument.issue_date >= a, ReceivedDocument.issue_date <= b)).all()
    pend = db.scalars(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tid, ReceivedDocument.status == "por_revisar")).all()
    pronto, vencidas = [], 0
    for d in sorted(pend, key=lambda d: (d.deadline or date.max)):
        left = business_days_left(today, d.deadline)
        if left is not None and left < 0:
            vencidas += 1
        if left is not None and left <= 2:
            pronto.append({"id": d.id, "issuer_name": d.issuer_name, "total": d.total, "currency": d.currency, "deadline": d.deadline, "business_days_left": left})
    last_ids = select(func.max(ReceptionResponse.id)).where(ReceptionResponse.tenant_id == tid).group_by(ReceptionResponse.received_document_id)
    last = db.scalars(select(ReceptionResponse).where(ReceptionResponse.id.in_(last_ids))).all()
    docs_by_id = {d.id: d for d in db.scalars(select(ReceivedDocument).where(ReceivedDocument.id.in_([r.received_document_id for r in last] or [0])))}
    rech = [
        {"id": r.received_document_id, "issuer_name": docs_by_id[r.received_document_id].issuer_name if r.received_document_id in docs_by_id else None, "detail": r.hacienda_detail or r.last_error}
        for r in last
        if r.status == "rechazado"
    ]
    emit_docs = db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == tid, EInvoiceDocument.mode.in_(("pruebas", "produccion")))).all()
    prof = hc.profile(db, tenant)
    env = prof.environment if prof.environment in ("pruebas", "produccion") else "pruebas"
    cred = db.scalar(select(FiscalCredential).where(FiscalCredential.tenant_id == tid, FiscalCredential.environment == env))
    mbox = db.scalar(select(ReceptionMailbox).where(ReceptionMailbox.tenant_id == tid).order_by(ReceptionMailbox.last_run_at.desc().nulls_last()))
    return {
        "month": f"{y:04d}-{m:02d}",
        "iva": x,
        "credito_por_confirmar": sin_confirmar,
        "recepciones": {
            "recibidas": len(del_mes),
            "por_revisar": len(pend),
            "vencen_pronto": pronto[:30],
            "vencidas": vencidas,
            "respondidas": sum(1 for d in del_mes if d.status in ("respondido", "historico")),
            "total_crc": _q2(sum((_sign(d) * _to_crc(d.total, d.currency, _orate(db, d)) for d in del_mes if d.status not in ("descartado", "ajeno", "invalido") and d.action != "rechazada"), Decimal(0))),
        },
        "mensajes": {"rechazados": rech[:30], "errores": sum(1 for r in last if r.status == "error"), "por_enviar": sum(1 for r in last if r.status == "por_enviar")},
        "emision": {
            "pendientes": sum(1 for d in emit_docs if d.status in ("por_enviar", "enviando", "enviado", "procesando")),
            "rechazadas": sum(1 for d in emit_docs if d.status == "rechazada"),
            "aceptadas": sum(1 for d in emit_docs if d.status == "aceptada"),
        },
        "hacienda": {
            "mode": hc.hacienda_mode(db, tenant),
            "environment": prof.environment,
            "cert_not_after": cred.cert_not_after if cred else None,
            "cert_days_left": hc.cert_days_left(cred),
            "imap_last_run_at": mbox.last_run_at if mbox else None,
        },
    }


def planilla(db: Session, tid: int, a: date, b: date) -> Report:
    from ..models import PayrollRun

    runs = db.scalars(
        select(PayrollRun).where(PayrollRun.tenant_id == tid, PayrollRun.period_end >= a, PayrollRun.period_end <= b).order_by(PayrollRun.period_end)
    ).all()
    rows = []
    for r in runs:
        for ln in sorted(r.lines, key=lambda x: x.employee_name):
            rows.append(
                [
                    f"{r.period_start.isoformat()} a {r.period_end.isoformat()}",
                    r.status,
                    ln.employee_name,
                    ln.gross,
                    ln.ccss_worker,
                    ln.income_tax,
                    ln.other_deductions,
                    ln.net,
                    ln.ccss_employer,
                    ln.provisions,
                ]
            )
    live = [r for r in runs if r.status != "borrador"]
    return Report(
        "planilla",
        "Planilla",
        ["Periodo", "Estado", "Colaborador", "Bruto", "CCSS obrero", "Impuesto renta", "Otras ded.", "Neto", "CCSS patronal", "Provisiones"],
        rows,
        {
            "Bruto": sum((Decimal(str(r.gross)) for r in live), Decimal(0)),
            "Neto": sum((Decimal(str(r.net)) for r in live), Decimal(0)),
            "CCSS patronal": sum((Decimal(str(r.ccss_employer)) for r in live), Decimal(0)),
        },
    )


def conciliacion(db: Session, tid: int, a: date, b: date) -> Report:
    from ..routers.banking import summary

    rows = summary(db, tid, a, b)
    return Report(
        "conciliacion",
        "Conciliación bancaria",
        ["Fecha", "Cuenta", "Descripción", "Referencia", "Monto", "Estado", "Casado con", "Cómo"],
        rows,
        {"Pendientes": sum(1 for r in rows if r[5] == "pendiente"), "Conciliados": sum(1 for r in rows if r[5] == "conciliado")},
    )


def rentabilidad(db: Session, tid: int, a: date, b: date) -> Report:
    """En qué tipo de trabajo gana Crimson: venta, costo real y margen por proyecto cerrado."""
    from ..models import Project
    from ..routers.projects import economics
    from .documents import local_date
    from .fx import rate_or_none

    # Archivar un trabajo terminado es ordenar, no borrarlo: su margen sigue contando. Solo la papelera sale.
    fx = rate_or_none(db, "USD")  # None: cada costo en dolares la consulta (sin ninguna tasa, 422 claro)
    rows, price_total, cost_total, comm_total, provisionales = [], Decimal(0), Decimal(0), Decimal(0), 0
    cerrados = db.scalars(
        select(Project)
        .where(Project.tenant_id == tid, Project.status.in_(("terminado", "entregado", "facturado")), Project.trashed_at.is_(None))
        .order_by(Project.id)
    ).all()
    for pr in cerrados:
        cierre = pr.end_date or local_date(pr.delivered_at) or local_date(pr.created_at)
        if not (a <= cierre <= b):
            continue
        e = economics(db, pr, fx)
        rows.append(
            [
                pr.number,
                pr.name,
                pr.status,
                cierre.isoformat(),
                e["price_crc"],  # en colones: un proyecto cotizado en USD ya no se compara contra costos en CRC
                e["cost_materials"],
                e["cost_labor"],
                e["cost_travel"],
                e["cost_extra"],
                e["cost_real"],
                e["profit"],
                e["margin_real"],
                e["margin_planned"],
                e["hours"],
                e["aviso"] or "",
                # economia (spec 8.7): aliados (ya dentro del costo real), comisiones y utilidad neta se agregan AL
                # FINAL; ninguna columna existente cambia de posicion (exportes y lecturas por indice)
                e["cost_allies"],
                e["commissions"],
                e["profit_net"],
            ]
        )
        if e["sin_costos"]:
            provisionales += 1  # sin costos no es ganancia: fuera de los totales
            continue
        price_total += Decimal(str(e["price_crc"] or 0))
        cost_total += Decimal(str(e["cost_real"]))
        comm_total += Decimal(str(e["commissions"] or 0))
    # primero los que menos dejaron (ahi esta lo que hay que corregir); los provisionales al final
    rows.sort(key=lambda r: (r[11] is None, r[11] or 0))
    return Report(
        "rentabilidad",
        "Rentabilidad por proyecto",
        [
            "Proyecto",
            "Nombre",
            "Estado",
            "Cierre",
            "Venta sin IVA",
            "Equipos",
            "Mano de obra",
            "Viáticos",
            "Otros",
            "Costo real",
            "Utilidad",
            "Margen real %",
            "Margen cotizado %",
            "Horas",
            "Provisional",
            "Aliados",
            "Comisiones",
            "Utilidad neta",
        ],
        rows,
        {
            "Venta sin IVA": price_total,
            "Costo real": cost_total,
            "Utilidad": price_total - cost_total,
            "Comisiones": comm_total,
            "Utilidad neta": price_total - cost_total - comm_total,
            "Provisionales sin costos (fuera del total)": provisionales,
        },
    )


def rentabilidad_tipo(db: Session, tid: int, a: date, b: date) -> Report:
    """La pregunta de Andres: en que tipo de trabajo gana Crimson. CCTV puede dejar 24 % y redes 31 %,
    y hasta hoy eso solo se sabia por intuicion."""
    from ..models import Project
    from ..routers.projects import economics, project_solution
    from .documents import local_date
    from .fx import rate_or_none

    fx = rate_or_none(db, "USD")
    por_tipo: dict[str, dict] = {}
    provisionales = 0
    cerrados = db.scalars(
        select(Project).where(Project.tenant_id == tid, Project.status.in_(("terminado", "entregado", "facturado")), Project.trashed_at.is_(None))
    ).all()
    for pr in cerrados:
        cierre = pr.end_date or local_date(pr.delivered_at) or local_date(pr.created_at)
        if not (a <= cierre <= b):
            continue
        e = economics(db, pr, fx)
        if e["sin_costos"]:
            provisionales += 1  # sin costos registrados no cuenta como ganancia
            continue
        fila = por_tipo.setdefault(
            project_solution(db, pr), {"n": 0, "venta": Decimal(0), "costo": Decimal(0), "horas": 0.0, "aliados": Decimal(0), "comisiones": Decimal(0)}
        )
        fila["n"] += 1
        fila["venta"] += Decimal(str(e["price_crc"] or 0))
        fila["costo"] += Decimal(str(e["cost_real"]))
        fila["horas"] += e["hours"]
        fila["aliados"] += Decimal(str(e["cost_allies"] or 0))
        fila["comisiones"] += Decimal(str(e["commissions"] or 0))
    rows = []
    for tipo, f in por_tipo.items():
        utilidad = f["venta"] - f["costo"]
        rows.append(
            [
                tipo,
                f["n"],
                f["venta"],
                f["costo"],
                utilidad,
                _margen(f["venta"], f["costo"]),
                round(f["horas"], 1),
                f["aliados"],
                f["comisiones"],
                utilidad - f["comisiones"],
            ]
        )
    rows.sort(key=lambda r: -r[5])  # primero donde mas se gana
    venta = sum((Decimal(str(r[2])) for r in rows), Decimal(0))
    costo = sum((Decimal(str(r[3])) for r in rows), Decimal(0))
    return Report(
        "rentabilidad_tipo",
        "Rentabilidad por tipo de solución",
        ["Solución", "Proyectos", "Venta sin IVA", "Costo real", "Utilidad", "Margen %", "Horas", "Aliados", "Comisiones", "Utilidad neta"],
        rows,
        {
            "Venta sin IVA": venta,
            "Costo real": costo,
            "Utilidad": venta - costo,
            "Margen %": _margen(venta, costo),
            "Provisionales sin costos (fuera del total)": provisionales,
        },
    )


def _margen(venta: Decimal, costo: Decimal) -> float:
    from ..services import pricing

    return pricing.margin_of(venta, costo)


def comisiones(db: Session, tid: int, a: date, b: date, hide_payouts: bool = False) -> Report:
    """Comisiones del periodo (contabilidad lo baja a Excel para la planilla): cada movimiento no anulado devengado
    en el periodo, con su beneficiario, concepto, estado y referencia de pago. `hide_payouts`: sin el pago acordado
    a los aliados ni sus ajustes (su precio es costo de Crimson) para quien no ve costos ni los paga."""
    from ..models import AllyCompany, Commission, Project, Quote, User
    from .commissions import KIND_LABEL, crc_of, is_payout

    rows, tot = [], {"pendiente": Decimal(0), "aprobada": Decimal(0), "pagada": Decimal(0)}
    nombres: dict = {}

    def nombre(model, oid, attr):
        if not oid:
            return None
        k = (model, oid)
        if k not in nombres:
            o = db.get(model, oid)
            nombres[k] = getattr(o, attr, None) if o is not None else None
        return nombres[k]

    for c in db.scalars(
        select(Commission)
        .where(Commission.tenant_id == tid, Commission.status != "anulada", Commission.earned_on >= a, Commission.earned_on <= b)
        .order_by(Commission.earned_on, Commission.id)
    ):
        if hide_payouts and is_payout(c):
            continue
        quien = nombre(User, c.user_id, "full_name") if c.beneficiary != "aliado" else nombre(AllyCompany, c.ally_company_id, "name")
        doc = nombre(Invoice, c.invoice_id, "number") or nombre(Quote, c.quote_id, "number")
        crc = crc_of(db, c).quantize(Decimal("0.01"))
        rows.append(
            [
                c.earned_on.isoformat(),
                quien or "—",
                "Aliado" if c.beneficiary == "aliado" else "Vendedor",
                KIND_LABEL.get(c.kind or "comision", c.kind),
                c.description or "",
                doc or "",
                nombre(Project, c.project_id, "number") or "",
                c.status,
                c.amount,
                c.currency,
                crc,
                c.paid_ref or "",
            ]
        )
        if c.status in tot:
            tot[c.status] += crc
    return Report(
        "comisiones",
        "Comisiones del periodo",
        [
            "Fecha",
            "Beneficiario",
            "Tipo de beneficiario",
            "Tipo",
            "Concepto",
            "Documento",
            "Proyecto",
            "Estado",
            "Monto",
            "Moneda",
            "Monto CRC",
            "Referencia de pago",
        ],
        rows,
        {"Pendiente (CRC)": tot["pendiente"], "Aprobada (CRC)": tot["aprobada"], "Pagada (CRC)": tot["pagada"]},
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
    "ordenes": ordenes,
    "recepciones": recepciones,
    "propinas": propinas,
    "d151": d151,
    "compras": compras,
    "planilla": planilla,
    "conciliacion": conciliacion,
    "rentabilidad": rentabilidad,
    "rentabilidad_tipo": rentabilidad_tipo,
    "comisiones": comisiones,
}
CATALOG = [
    ("facturacion", "Facturación", "Todas las facturas del periodo con totales y saldo"),
    ("pendientes", "Facturas pendientes", "Por cobrar, con días de atraso"),
    ("impuesto", "Impuesto facturado", "Base e IVA por tarifa"),
    ("resultados", "Estado de resultados", "Ventas netas menos gastos"),
    ("cierre", "Cierre diario", "Cobros por día y método"),
    ("transacciones", "Transacciones", "Cada pago con método, tipo y referencia"),
    ("gastos", "Gastos", "Egresos registrados en contabilidad"),
    ("iva", "IVA", "Débito, crédito (de facturas electrónicas y de gastos sin XML) y prorrata · borrador D-104 en colones"),
    ("inventario", "Inventario", "Existencias por ubicación y alertas"),
    ("productos", "Venta de productos", "Ranking de productos y servicios"),
    ("movimientos", "Movimientos", "Ledger de inventario"),
    ("ordenes", "Órdenes", "Pedidos de la tienda y del punto de venta"),
    ("recepciones", "Recepciones", "Comprobantes de proveedores recibidos, su respuesta y el estado ante Hacienda"),
    ("propinas", "Propinas", "Propinas cobradas por pago"),
    ("d151", "D-270 / D-151", "Operaciones sin comprobante electrónico (borrador): compras y gastos específicos"),
    ("compras", "Libro de compras", "Comprobantes aceptados y gastos con proveedor, con su respaldo"),
    ("planilla", "Planilla", "Salarios, cargas sociales e impuesto por colaborador"),
    ("conciliacion", "Conciliación bancaria", "Movimientos del banco y con qué se casaron"),
    ("rentabilidad", "Rentabilidad por proyecto", "Cuánto dejó cada instalación: venta contra costo real"),
    ("rentabilidad_tipo", "Rentabilidad por tipo de solución", "En qué tipo de trabajo gana Crimson: CCTV, redes, acceso…"),
    ("comisiones", "Comisiones del periodo", "Comisiones, bonos y pagos a aliados devengados, con su estado y referencia de pago"),
]


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def to_xlsx(rep: Report, a: date, b: date, tenant_name: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\[\]:*?/\\]", "-", rep.title)[:30]  # Excel no admite []:*?/\ en el nombre de la hoja
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
        # Inyeccion de formulas: nombres y descripciones vienen de XML recibidos por correo sin autenticar (un "proveedor"
        # llamado =HYPERLINK(...) o una formula DDE). openpyxl convierte en formula todo texto que empiece por "=": cualquier
        # texto con un prefijo peligroso se escribe como texto literal (y con quotePrefix, para que Excel no lo evalue al editar).
        for c in ws[ws.max_row]:
            if isinstance(c.value, str) and c.value[:1] in FORMULA_PREFIXES:
                c.data_type = "s"
                c.quotePrefix = True
    for i, col in enumerate(rep.columns, 1):
        width = max(len(str(col)), *(len(str(r[i - 1])) for r in rep.rows[:200])) if rep.rows else len(col)
        ws.column_dimensions[get_column_letter(i)].width = min(48, width + 3)
    ws.freeze_panes = "A5"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
