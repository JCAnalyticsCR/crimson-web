"""Motor de asientos de diario para exportar a QuickBooks Online.

Reglas de diseño
----------------
* UN ASIENTO POR DOCUMENTO.  Número estable JE-<tipo>-<id> (uq_jentry_source).
* IDEMPOTENTE.  upsert_entry() reemplaza las líneas existentes si el documento
  cambió.  Si el mes ya se exportó se crea/reemplaza el asiento de tipo
  "ajuste" fechado en el primer mes abierto.
* BALANCEADO EXACTO.  sum(debe) == sum(haber) a 2 decimales.  La diferencia de
  redondeo (< 0.01 por línea) va a la cuenta de rol "redondeo".
* DECIMAL SIEMPRE.  Ningún float pasa por la aritmética; todo se convierte con D().
* GENERADOR DE PAGO A PROVEEDOR.  La función supplier_payment_lines() acepta
  la interfaz simple (fecha, proveedor, monto, moneda, fx, banco) para que el
  frente "Cuentas por pagar" (SupplierPayment) la enchufue sin cambiar el motor.
  Hoy se alimenta de los Expense con purchase_type='credito' y status='pagado'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AccountingPeriod,
    Commission,
    Customer,
    EInvoiceDocument,
    Expense,
    Invoice,
    InvoiceLine,
    JournalEntry,
    JournalLine,
    Payment,
    PayrollRun,
    Product,
    Project,
    StockMovement,
    Supplier,
    bank_role,
    expense_cat_role,
)

Q2 = Decimal("0.01")


def D(x) -> Decimal:  # noqa: N802
    """Convierte cualquier número a Decimal con 2 decimales."""
    if isinstance(x, Decimal):
        return x.quantize(Q2, rounding=ROUND_HALF_UP)
    return Decimal(str(x or 0)).quantize(Q2, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Tipos internos
# ---------------------------------------------------------------------------

@dataclass
class _Line:
    role_key: str
    side: str        # "debe" | "haber"
    amount: Decimal  # positivo
    currency: str = "CRC"
    fx: Decimal | None = None
    description: str = ""
    name: str = ""
    position: int = 0


@dataclass
class _Entry:
    source_type: str
    source_id: int
    tenant_id: int
    date: date
    description: str
    currency: str = "CRC"
    lines: list[_Line] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers de balanceo
# ---------------------------------------------------------------------------

def _balance(lines: list[_Line]) -> Decimal:
    debe = sum((ln.amount for ln in lines if ln.side == "debe"), Decimal(0))
    haber = sum((ln.amount for ln in lines if ln.side == "haber"), Decimal(0))
    return (debe - haber).quantize(Q2, rounding=ROUND_HALF_UP)


def _add_rounding(lines: list[_Line]) -> None:
    """Agrega una línea de redondeo si debe ≠ haber (diferencia < 0.02)."""
    diff = _balance(lines)
    if diff == 0:
        return
    # diff > 0 → sobran débitos → haber para equilibrar; y viceversa
    lines.append(_Line(
        role_key="redondeo",
        side="haber" if diff > 0 else "debe",
        amount=abs(diff),
        description="Diferencia de redondeo",
        position=len(lines),
    ))


def _number(source_type: str, source_id: int) -> str:
    prefixes = {
        "invoice": "INV", "credit_note": "NC", "payment": "PMT",
        "expense": "EXP", "supplier_payment": "SPY", "payroll": "PAY",
        "commission": "COM", "cogs": "CGS",
    }
    return f"JE-{prefixes.get(source_type, source_type.upper())}-{source_id}"


# ---------------------------------------------------------------------------
# Clasificación de ingresos por línea
# ---------------------------------------------------------------------------

_PRODUCT_CACHE: dict[int, str] = {}  # product_id -> "equipos"|"servicios"


def _revenue_role(db: Session, line: InvoiceLine, is_project: bool) -> str:
    if is_project:
        return "ventas_proyectos"
    if line.product_id is None:
        return "ventas_servicios"
    cached = _PRODUCT_CACHE.get(line.product_id)
    if cached is None:
        p = db.get(Product, line.product_id)
        cached = "ventas_servicios" if (p and p.item_type == "servicio") else "ventas_equipos"
        _PRODUCT_CACHE[line.product_id] = cached
    return cached


def _iva_role(tax_rate: float) -> str:
    r = int(round(tax_rate))
    return f"iva_por_pagar_{r}"


# ---------------------------------------------------------------------------
# Rol CxC/CxP según moneda del documento
# ---------------------------------------------------------------------------

def _cxc_role(currency: str) -> str:
    return "cxc_clientes_usd" if currency.upper() == "USD" else "cxc_clientes_crc"


def _cxp_role(currency: str) -> str:
    return "cxp_proveedores_usd" if currency.upper() == "USD" else "cxp_proveedores_crc"


# ---------------------------------------------------------------------------
# Rol de medio de pago
# ---------------------------------------------------------------------------

def _payment_method_role(method: str, bank_account_id: int | None) -> str:
    if bank_account_id:
        return bank_role(bank_account_id)
    mapping = {
        "efectivo": "caja_efectivo",
        "sinpe": "sinpe_por_cobrar",
        "transferencia": "sinpe_por_cobrar",
        "tarjeta": "tarjeta_por_cobrar",
        "onvo": "onvo_por_cobrar",
        "paypal": "onvo_por_cobrar",
    }
    return mapping.get(method.lower(), "caja_efectivo")


# ---------------------------------------------------------------------------
# Generadores de asientos (funciones puras)
# ---------------------------------------------------------------------------

def build_invoice_entry(db: Session, invoice: Invoice) -> _Entry | None:
    """Factura / TE / FEE: CxC vs ventas por tipo + IVA por tarifa."""
    if invoice.status == "anulada":
        return None
    if invoice.einvoice_status == "rechazada":
        return None

    lines: list[_Line] = []
    currency = invoice.currency.upper()
    fx = D(invoice.fx_sell) if currency == "USD" else None

    # Cuentas por cobrar (o efectivo si contado y hay banco; simplificamos a CxC siempre)
    cxc = _cxc_role(currency)
    lines.append(_Line(
        role_key=cxc,
        side="debe",
        amount=D(invoice.total),
        currency=currency,
        fx=fx,
        description=f"Factura {invoice.number}",
        position=0,
    ))

    # Detectar si es factura de proyecto
    is_project = _is_project_invoice(db, invoice)

    # Agrupar líneas por (revenue_role, tax_rate)
    revenue: dict[tuple[str, int], Decimal] = {}
    iva: dict[int, Decimal] = {}
    for ln in invoice.lines:
        if ln.treatment in ("cortesia", "excluido"):
            continue
        role = _revenue_role(db, ln, is_project)
        rate = int(round(float(ln.tax_rate)))
        revenue[(role, rate)] = revenue.get((role, rate), Decimal(0)) + D(ln.subtotal)
        iva[rate] = iva.get(rate, Decimal(0)) + D(ln.tax_amount)

    pos = 1
    for (role, _rate), amt in revenue.items():
        if amt == 0:
            continue
        lines.append(_Line(
            role_key=role,
            side="haber",
            amount=amt,
            currency=currency,
            fx=fx,
            description=f"Venta {invoice.number}",
            position=pos,
        ))
        pos += 1

    # Descuento en cabecera (si aplica)
    disc = D(invoice.discount_total)
    if disc > 0:
        lines.append(_Line(
            role_key="descuentos",
            side="debe",
            amount=disc,
            currency=currency,
            fx=fx,
            description=f"Descuento {invoice.number}",
            position=pos,
        ))
        pos += 1

    # IVA por tarifa
    for rate, amt in iva.items():
        if amt == 0:
            continue
        lines.append(_Line(
            role_key=_iva_role(rate),
            side="haber",
            amount=amt,
            currency=currency,
            fx=fx,
            description=f"IVA {rate}% {invoice.number}",
            position=pos,
        ))
        pos += 1

    _add_rounding(lines)

    customer_name = _customer_name(db, invoice.customer_id)
    for ln in lines:
        if not ln.name:
            ln.name = customer_name

    return _Entry(
        source_type="invoice",
        source_id=invoice.id,
        tenant_id=invoice.tenant_id,
        date=invoice.issue_date,
        description=f"Factura {invoice.number}",
        currency=currency,
        lines=lines,
    )


def build_credit_note_entry(db: Session, nc: EInvoiceDocument) -> _Entry | None:
    """Nota de crédito (NC aceptada): inverso de la factura original."""
    if nc.doc_type != "NC":
        return None
    if nc.status != "aceptada":
        return None
    invoice = db.get(Invoice, nc.invoice_id)
    if invoice is None:
        return None

    # Invertir el asiento de la factura
    inv_entry = build_invoice_entry(db, invoice)
    if inv_entry is None:
        return None

    from .documents import local_date
    nc_date = local_date(nc.created_at) or invoice.issue_date

    nc_lines: list[_Line] = []
    for i, ln in enumerate(inv_entry.lines):
        nc_lines.append(_Line(
            role_key=ln.role_key,
            side="haber" if ln.side == "debe" else "debe",
            amount=ln.amount,
            currency=ln.currency,
            fx=ln.fx,
            description=ln.description.replace(invoice.number, f"NC/{invoice.number}"),
            name=ln.name,
            position=i,
        ))

    return _Entry(
        source_type="credit_note",
        source_id=nc.id,
        tenant_id=invoice.tenant_id,
        date=nc_date,
        description=f"Nota de crédito NC/{invoice.number}",
        currency=invoice.currency.upper(),
        lines=nc_lines,
    )


def build_payment_entry(db: Session, payment: Payment) -> _Entry | None:
    """Cobro: banco/medio vs CxC; diferencial cambiario si USD a distinto tipo de cambio."""
    if payment.status not in ("confirmado",):
        return None
    if payment.kind in ("devolucion", "reembolso", "void"):
        return None  # TODO: devoluciones como asiento propio
    invoice = db.get(Invoice, payment.invoice_id)
    if invoice is None:
        return None

    lines: list[_Line] = []
    pmt_currency = payment.currency.upper()
    inv_currency = invoice.currency.upper()
    pmt_amount = D(payment.amount)
    tip = D(payment.tip)

    method_role = _payment_method_role(payment.method, payment.bank_account_id)

    # Lado débito: banco o medio de pago
    total_received = pmt_amount + tip
    # Determinar fx del pago
    fx_pmt: Decimal | None = None
    if pmt_currency == "USD":
        # Intentamos el fx del banco (usamos el guardado en la factura como referencia)
        fx_pmt = D(invoice.fx_sell) if D(invoice.fx_sell) > 1 else None

    lines.append(_Line(
        role_key=method_role,
        side="debe",
        amount=total_received,
        currency=pmt_currency,
        fx=fx_pmt,
        description=f"Cobro {invoice.number}",
        position=0,
    ))

    # Lado crédito: CxC
    cxc = _cxc_role(inv_currency)
    lines.append(_Line(
        role_key=cxc,
        side="haber",
        amount=pmt_amount,
        currency=inv_currency,
        fx=D(invoice.fx_sell) if inv_currency == "USD" else None,
        description=f"Cobro {invoice.number}",
        position=1,
    ))

    # Propina (si aplica) → otros ingresos
    if tip > 0:
        lines.append(_Line(
            role_key="otros_ingresos",
            side="haber",
            amount=tip,
            description=f"Propina {invoice.number}",
            position=2,
        ))

    # Diferencial cambiario: si el pago es en USD y la factura en USD pero el cobro
    # se hace a un tipo de cambio distinto al de la factura, hay ganancia/pérdida.
    # (Simplificado: si ambas monedas son iguales, no hay diferencial directo aquí.)

    _add_rounding(lines)

    customer_name = _customer_name(db, invoice.customer_id)
    for ln in lines:
        if not ln.name:
            ln.name = customer_name

    return _Entry(
        source_type="payment",
        source_id=payment.id,
        tenant_id=invoice.tenant_id,
        date=payment.paid_at,
        description=f"Cobro factura {invoice.number}",
        currency=pmt_currency,
        lines=lines,
    )


def build_expense_entry(db: Session, expense: Expense) -> _Entry | None:
    """Gasto / factura de proveedor.

    purchase_type='credito'  → DR gasto+IVA / CR CxP
    purchase_type='contado'  → DR gasto+IVA / CR banco (o CxP si sin banco)
    status='anulado'         → None
    """
    if expense.status == "anulado":
        return None

    lines: list[_Line] = []
    currency = expense.currency.upper()
    fx = D(expense.fx_rate) if currency == "USD" and D(expense.fx_rate) > 1 else None

    # IVA acreditable / no acreditable
    if expense.tax_credit_amount is not None:
        iva_cred = D(expense.tax_credit_amount)
    else:
        iva_cred = Decimal(0)
    iva_no_cred = D(expense.tax_amount) - iva_cred
    if iva_no_cred < 0:
        iva_no_cred = Decimal(0)

    # Cuenta del gasto (por categoría o genérica)
    if expense.category_id:
        gasto_role = expense_cat_role(expense.category_id)
    else:
        gasto_role = "otros_ingresos"  # sin categoría → mapear manualmente

    subtotal = D(expense.subtotal)
    total_gasto = subtotal + iva_no_cred  # lo que va al gasto (IVA no acreditable ES gasto)

    pos = 0
    if total_gasto > 0:
        lines.append(_Line(
            role_key=gasto_role,
            side="debe",
            amount=total_gasto,
            currency=currency,
            fx=fx,
            description=expense.description[:200],
            position=pos,
        ))
        pos += 1

    if iva_cred > 0:
        lines.append(_Line(
            role_key="iva_acreditable",
            side="debe",
            amount=iva_cred,
            currency=currency,
            fx=fx,
            description=f"IVA acreditable – {expense.description[:100]}",
            position=pos,
        ))
        pos += 1

    # Contrapartida
    if expense.purchase_type == "credito":
        contra_role = _cxp_role(currency)
    elif expense.bank_account_id:
        contra_role = bank_role(expense.bank_account_id)
    else:
        contra_role = _cxp_role(currency)

    lines.append(_Line(
        role_key=contra_role,
        side="haber",
        amount=D(expense.total),
        currency=currency,
        fx=fx,
        description=expense.description[:200],
        position=pos,
    ))

    _add_rounding(lines)

    supplier_name = _supplier_name(db, expense.supplier_id)
    for ln in lines:
        if not ln.name:
            ln.name = supplier_name

    return _Entry(
        source_type="expense",
        source_id=expense.id,
        tenant_id=expense.tenant_id,
        date=expense.date,
        description=expense.description[:300],
        currency=currency,
        lines=lines,
    )


def supplier_payment_lines(
    *,
    supplier_name: str,
    amount: Decimal,
    currency: str,
    fx: Decimal | None,
    bank_account_id: int | None,
) -> list[_Line]:
    """Genera las líneas de un pago a proveedor (interfaz simple para enchufar SupplierPayment).

    Devuelve la lista de líneas sin crear la entrada; el llamador decide el
    source_type / source_id cuando integre SupplierPayment.
    """
    currency = currency.upper()
    contra_role = bank_role(bank_account_id) if bank_account_id else "caja_efectivo"
    lines = [
        _Line(
            role_key=_cxp_role(currency),
            side="debe",
            amount=amount,
            currency=currency,
            fx=fx,
            description="Pago a proveedor",
            name=supplier_name,
            position=0,
        ),
        _Line(
            role_key=contra_role,
            side="haber",
            amount=amount,
            currency=currency,
            fx=fx,
            description="Pago a proveedor",
            name=supplier_name,
            position=1,
        ),
    ]
    _add_rounding(lines)
    return lines


def build_supplier_payment_entry(db: Session, expense: Expense) -> _Entry | None:
    """Pago de un gasto de crédito (purchase_type='credito', status='pagado').

    El frente "Cuentas por pagar" (SupplierPayment) llamará a supplier_payment_lines()
    con su propia interfaz cuando esté listo. Hoy alimentamos desde gastos marcados pagados.
    """
    if expense.purchase_type != "credito":
        return None
    if expense.status != "pagado":
        return None

    currency = expense.currency.upper()
    fx = D(expense.fx_rate) if currency == "USD" and D(expense.fx_rate) > 1 else None
    supplier_name = _supplier_name(db, expense.supplier_id)
    bank_acc_id = expense.bank_account_id  # puede ser None

    lines = supplier_payment_lines(
        supplier_name=supplier_name,
        amount=D(expense.total),
        currency=currency,
        fx=fx,
        bank_account_id=bank_acc_id,
    )

    # La fecha de pago: usamos expense.date (cuando se marcó pagado no queda en el modelo;
    # usamos la fecha del gasto como aproximación hasta que SupplierPayment tenga paid_date)
    return _Entry(
        source_type="supplier_payment",
        source_id=expense.id,
        tenant_id=expense.tenant_id,
        date=expense.date,
        description=f"Pago a proveedor: {expense.description[:200]}",
        currency=currency,
        lines=lines,
    )


def build_payroll_entry(db: Session, run: PayrollRun) -> _Entry | None:
    """Planilla: sueldos + cargas patronales + provisiones DR / retenciones + banco CR."""
    if run.status not in ("aprobada", "pagada"):
        return None

    lines: list[_Line] = []
    gross = D(run.gross)
    ccss_worker = D(run.ccss_worker)
    income_tax = D(run.income_tax)
    other_ded = D(run.other_deductions)
    net = D(run.net)
    ccss_employer = D(run.ccss_employer)
    provisions = D(run.provisions)

    pos = 0
    if gross > 0:
        lines.append(_Line(role_key="sueldos", side="debe", amount=gross,
                           description="Sueldos brutos", position=pos))
        pos += 1
    if ccss_employer > 0:
        lines.append(_Line(role_key="cargas_ccss_patronal", side="debe", amount=ccss_employer,
                           description="CCSS patronal", position=pos))
        pos += 1
    if provisions > 0:
        # Repartir provisions 50/50 aguinaldo/vacaciones si no hay detalle
        aguinaldo = (provisions / 2).quantize(Q2, rounding=ROUND_HALF_UP)
        vacaciones = provisions - aguinaldo
        if aguinaldo > 0:
            lines.append(_Line(role_key="provision_aguinaldo", side="debe", amount=aguinaldo,
                               description="Provisión aguinaldo", position=pos))
            pos += 1
        if vacaciones > 0:
            lines.append(_Line(role_key="provision_vacaciones", side="debe", amount=vacaciones,
                               description="Provisión vacaciones", position=pos))
            pos += 1

    if ccss_worker > 0:
        lines.append(_Line(role_key="ret_ccss_obrera", side="haber", amount=ccss_worker,
                           description="CCSS obrera retenida", position=pos))
        pos += 1
    if income_tax > 0:
        lines.append(_Line(role_key="ret_renta", side="haber", amount=income_tax,
                           description="Impuesto al salario retenido", position=pos))
        pos += 1
    if other_ded > 0:
        lines.append(_Line(role_key="ret_otras", side="haber", amount=other_ded,
                           description="Otras deducciones", position=pos))
        pos += 1
    if net > 0:
        # El neto va al banco si hay cuenta bancaria vinculada, si no a CxP (planilla por pagar)
        bank_acc = None
        if run.expense_id:
            exp = db.get(Expense, run.expense_id)
            if exp and exp.bank_account_id:
                bank_acc = exp.bank_account_id
        banco_role = bank_role(bank_acc) if bank_acc else "cxp_proveedores_crc"
        lines.append(_Line(role_key=banco_role, side="haber", amount=net,
                           description="Neto pagado en planilla", position=pos))
        pos += 1

    _add_rounding(lines)

    period_label = f"{run.period_start.isoformat()} – {run.period_end.isoformat()}"
    return _Entry(
        source_type="payroll",
        source_id=run.id,
        tenant_id=run.tenant_id,
        date=run.period_end,
        description=f"Planilla {period_label}",
        lines=lines,
    )


def build_commission_entry(db: Session, commission: Commission) -> _Entry | None:
    """Comisión pagada: comisiones_gasto DR / comisiones_por_pagar y banco CR."""
    if commission.status != "pagada":
        return None

    amount = D(commission.amount)
    if amount <= 0:
        return None

    lines: list[_Line] = []
    bank_acc_id: int | None = None
    if commission.expense_id:
        exp = db.get(Expense, commission.expense_id)
        if exp and exp.bank_account_id:
            bank_acc_id = exp.bank_account_id

    lines.append(_Line(role_key="comisiones_gasto", side="debe", amount=amount,
                       description="Gasto de comisión", position=0))
    if bank_acc_id:
        lines.append(_Line(role_key=bank_role(bank_acc_id), side="haber", amount=amount,
                           description="Pago de comisión", position=1))
    else:
        lines.append(_Line(role_key="comisiones_por_pagar", side="haber", amount=amount,
                           description="Comisión por pagar", position=1))

    paid_on = commission.paid_on or (
        commission.updated_at.date() if hasattr(commission, "updated_at") and commission.updated_at else date.today()
    )

    return _Entry(
        source_type="commission",
        source_id=commission.id,
        tenant_id=commission.tenant_id,
        date=paid_on,
        description=f"Comisión pagada – {commission.description or 'comisión'}",
        lines=lines,
    )


def build_cogs_entry(db: Session, invoice: Invoice) -> _Entry | None:
    """Costo de ventas: inventario CR / costo_ventas DR para los movimientos de la factura."""
    movements = db.scalars(
        select(StockMovement).where(
            StockMovement.tenant_id == invoice.tenant_id,
            StockMovement.invoice_id == invoice.id,
            StockMovement.kind == "venta",
            StockMovement.unit_cost.isnot(None),
        )
    ).all()
    if not movements:
        return None

    total_cost = sum((D(abs(m.quantity)) * D(m.unit_cost) for m in movements), Decimal(0))
    if total_cost == 0:
        return None

    lines = [
        _Line(role_key="costo_ventas", side="debe", amount=total_cost,
              description=f"Costo ventas {invoice.number}", position=0),
        _Line(role_key="inventario", side="haber", amount=total_cost,
              description=f"Inventario {invoice.number}", position=1),
    ]

    return _Entry(
        source_type="cogs",
        source_id=invoice.id,
        tenant_id=invoice.tenant_id,
        date=invoice.issue_date,
        description=f"Costo de ventas {invoice.number}",
        lines=lines,
    )


# ---------------------------------------------------------------------------
# Helpers de nombres
# ---------------------------------------------------------------------------

def _customer_name(db: Session, customer_id: int | None) -> str:
    if not customer_id:
        return ""
    c = db.get(Customer, customer_id)
    return c.name if c else ""


def _supplier_name(db: Session, supplier_id: int | None) -> str:
    if not supplier_id:
        return ""
    s = db.get(Supplier, supplier_id)
    return s.name if s else ""


def _is_project_invoice(db: Session, invoice: Invoice) -> bool:
    from sqlalchemy import exists as sa_exists

    from ..models import Opportunity
    if invoice.quote_id:
        has_proj = db.scalar(
            select(sa_exists().where(Project.tenant_id == invoice.tenant_id, Project.quote_id == invoice.quote_id))
        )
        has_opp = db.scalar(
            select(sa_exists().where(
                Opportunity.tenant_id == invoice.tenant_id,
                Opportunity.quote_id == invoice.quote_id,
                Opportunity.kind == "proyecto",
            ))
        )
        if has_proj or has_opp:
            return True
    has_proj_inv = db.scalar(
        select(sa_exists().where(Project.tenant_id == invoice.tenant_id, Project.invoice_id == invoice.id))
    )
    return bool(has_proj_inv)


# ---------------------------------------------------------------------------
# Persistencia idempotente
# ---------------------------------------------------------------------------

def _first_open_period_date(db: Session, tenant_id: int, after_month: str) -> date:
    """Fecha del primer día del primer mes abierto posterior a after_month."""
    row = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.tenant_id == tenant_id,
            AccountingPeriod.status == "abierto",
            AccountingPeriod.month > after_month,
        ).order_by(AccountingPeriod.month)
    )
    if row:
        y, m = map(int, row.month.split("-"))
        return date(y, m, 1)
    # No hay periodo abierto creado: usar mes siguiente a today
    today = date.today()
    if after_month >= today.strftime("%Y-%m"):
        m2 = today.month % 12 + 1
        y2 = today.year + (1 if m2 == 1 else 0)
        return date(y2, m2, 1)
    y, m = map(int, after_month.split("-"))
    m2 = m % 12 + 1
    y2 = y + (1 if m2 == 1 else 0)
    return date(y2, m2, 1)


def _month_is_closed(db: Session, tenant_id: int, month: str) -> bool:
    period = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.tenant_id == tenant_id,
            AccountingPeriod.month == month,
        )
    )
    return period is not None and period.status == "exportado"


def upsert_entry(db: Session, entry: _Entry) -> JournalEntry | None:
    """Persiste el asiento de forma idempotente.

    Si ya existe (uq_jentry_source) y no está exportado, reemplaza sus líneas.
    Si está exportado, genera un asiento de AJUSTE en el primer mes abierto.
    Si entry es None, devuelve None sin tocar la base.
    """
    if entry is None:
        return None

    doc_month = entry.date.strftime("%Y-%m")

    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.tenant_id == entry.tenant_id,
            JournalEntry.source_type == entry.source_type,
            JournalEntry.source_id == entry.source_id,
            JournalEntry.kind == "normal",
        )
    )

    if existing and existing.export_id:
        # Ya exportado → crear asiento de ajuste en el primer mes abierto
        adj_date = _first_open_period_date(db, entry.tenant_id, doc_month)
        adj_month = adj_date.strftime("%Y-%m")

        # ¿Ya existe un ajuste?
        adj_number = _number(entry.source_type, entry.source_id) + "-ADJ"
        adj_existing = db.scalar(
            select(JournalEntry).where(
                JournalEntry.tenant_id == entry.tenant_id,
                JournalEntry.source_type == entry.source_type,
                JournalEntry.source_id == entry.source_id,
                JournalEntry.kind == "ajuste",
            )
        )
        if adj_existing and adj_existing.export_id:
            return adj_existing  # el ajuste ya se exportó, no tocar

        if adj_existing:
            je = adj_existing
            for jl in list(je.lines):
                db.delete(jl)
            db.flush()
        else:
            je = JournalEntry(
                tenant_id=entry.tenant_id,
                source_type=entry.source_type,
                source_id=entry.source_id,
                number=adj_number,
                kind="ajuste",
                adjusts_month=doc_month,
            )
            db.add(je)

        je.date = adj_date
        je.month = adj_month
        je.description = f"[Ajuste] {entry.description}"
        je.currency = entry.currency
        je.generated_at = datetime.now(UTC)
    else:
        # Upsert normal
        is_closed = _month_is_closed(db, entry.tenant_id, doc_month)
        if is_closed:
            adj_date = _first_open_period_date(db, entry.tenant_id, doc_month)
            adj_month = adj_date.strftime("%Y-%m")
            kind = "ajuste"
            adjusts_month = doc_month
            eff_date = adj_date
            eff_month = adj_month
        else:
            kind = "normal"
            adjusts_month = None
            eff_date = entry.date
            eff_month = doc_month

        if existing:
            je = existing
            for jl in list(je.lines):
                db.delete(jl)
            db.flush()
        else:
            je = JournalEntry(
                tenant_id=entry.tenant_id,
                source_type=entry.source_type,
                source_id=entry.source_id,
                number=_number(entry.source_type, entry.source_id),
                kind=kind,  # necesario para satisfacer la UK (tenant, source_type, source_id, kind)
            )
            db.add(je)

        je.date = eff_date
        je.month = eff_month
        je.description = entry.description
        je.currency = entry.currency
        je.kind = kind
        je.adjusts_month = adjusts_month
        je.generated_at = datetime.now(UTC)

    db.flush()

    # Escribir líneas
    for i, ln in enumerate(entry.lines):
        jl = JournalLine(
            entry_id=je.id,
            role_key=ln.role_key,
            side=ln.side,
            amount=float(ln.amount),
            currency=ln.currency,
            fx=float(ln.fx) if ln.fx else None,
            description=ln.description,
            name=ln.name,
            position=i,
        )
        db.add(jl)

    db.flush()
    return je


def regenerate_all(db: Session, tenant_id: int, month: str) -> dict:
    """Regenera (o genera por primera vez) todos los asientos del mes indicado.

    Retorna contadores. Los asientos exportados no se tocan.
    """
    from datetime import timedelta

    y, m = map(int, month.split("-"))
    a = date(y, m, 1)
    # último día del mes
    if m == 12:
        b = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        b = date(y, m + 1, 1) - timedelta(days=1)

    created = 0
    skipped = 0
    errors: list[str] = []

    def _try(fn, *args):
        nonlocal created, skipped, errors
        try:
            entry = fn(db, *args)
            if entry is None:
                skipped += 1
                return
            je = upsert_entry(db, entry)
            if je:
                created += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(str(exc))
            skipped += 1

    from ..models import Commission, EInvoiceDocument, Expense, Invoice, Payment, PayrollRun

    # Facturas
    for inv in db.scalars(
        select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.issue_date >= a, Invoice.issue_date <= b)
    ):
        _try(build_invoice_entry, inv)
        _try(build_cogs_entry, inv)

    # Notas de crédito
    for nc in db.scalars(
        select(EInvoiceDocument).where(
            EInvoiceDocument.tenant_id == tenant_id,
            EInvoiceDocument.doc_type == "NC",
        )
    ):
        from .documents import local_date
        nc_date = local_date(nc.created_at)
        if nc_date and a <= nc_date <= b:
            _try(build_credit_note_entry, nc)

    # Cobros
    for pmt in db.scalars(
        select(Payment).where(Payment.tenant_id == tenant_id, Payment.paid_at >= a, Payment.paid_at <= b)
    ):
        _try(build_payment_entry, pmt)

    # Gastos
    for exp in db.scalars(
        select(Expense).where(Expense.tenant_id == tenant_id, Expense.date >= a, Expense.date <= b)
    ):
        _try(build_expense_entry, exp)
        if exp.purchase_type == "credito" and exp.status == "pagado":
            _try(build_supplier_payment_entry, exp)

    # Planillas
    for run in db.scalars(
        select(PayrollRun).where(PayrollRun.tenant_id == tenant_id, PayrollRun.period_end >= a, PayrollRun.period_end <= b)
    ):
        _try(build_payroll_entry, run)

    # Comisiones pagadas
    for comm in db.scalars(
        select(Commission).where(
            Commission.tenant_id == tenant_id,
            Commission.status == "pagada",
            Commission.paid_on >= a,
            Commission.paid_on <= b,
        )
    ):
        _try(build_commission_entry, comm)

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}
