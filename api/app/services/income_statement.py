"""Estado de resultados mensual (reunion con la contadora, 10/11/2026): "cuanto recibi y cuanto gane" mes a mes, sin
volver la plataforma un sistema contable (la contabilidad formal sigue en QuickBooks). Columnas por mes + total, todo
en colones y en Decimal.

Base devengada: cada venta cuenta en el mes de su factura y cada gasto en el mes de su fecha, se cobre o se pague
cuando sea. No es un flujo de caja.

Ingresos (sin IVA), por categoria con la misma regla que los asientos para QuickBooks (services/journal._revenue_role):
- factura de un proyecto (el proyecto la apunta, nace de la cotizacion de un proyecto o su oportunidad es "proyecto")
  -> Proyectos, completa;
- si no, cada linea por su producto: servicio -> Servicios; producto -> Equipos y productos; linea sin producto del
  catalogo -> Servicios (como en QuickBooks).
- Cada categoria va antes del descuento global; el descuento global va en su propia linea, restando.
- Que factura cuenta: la regla del borrador D-104 (services/profit_loss.invoices_of): la rechazada por Hacienda no
  cuenta; la anulada sin nota de credito aceptada no cuenta; la anulada por NC sigue en su mes y la NC resta su venta
  sin IVA en el mes en que se emitio (linea "Notas de credito").

Gastos = gasto aplicable (total - IVA acreditable; con condicion 04 el IVA ES gasto), por categoria de gasto
(ExpenseCategory), en "Costo de ventas" o "Gastos de operacion" segun ExpenseCategory.pnl_group (o por el nombre,
services/profit_loss.group_of). Anulados fuera. La planilla y las comisiones de vendedor ya son gastos: pagar una
planilla registra el gasto "Planilla" (bruto + CCSS patronal) y pagar una comision registra el gasto "Comisiones"
(services/commission_expense.py), asi que salen como sus categorias, sin contarse dos veces.

Dolares: cada documento con el tipo de cambio que guardo (factura: fx_sell; gasto: fx_rate); si no lo guardo, el del
BCCR de su fecha (services/profit_loss.Converter). Nunca se suma un dolar como un colon.

Costo de inventario (kardex): el costo registrado en las salidas por venta (StockMovement "venta" con unit_cost). Es
informacion de costo de producto: solo la ve quien ve costos (Principal.sees_costs); y es solo de referencia, no se
resta, porque las compras de equipo ya cuentan como gasto en "Costo de ventas" (restarlo las contaria dos veces).

Utilidad neta = antes del impuesto sobre la renta (la plataforma no lo calcula).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Expense, ExpenseCategory, Invoice, PayrollRun, Product, StockMovement
from . import profit_loss as pl

MAX_MONTHS = 24
Q2 = Decimal("0.01")
ZERO = Decimal(0)

INCOME_KEYS = ("equipos", "servicios", "proyectos")
INCOME_LABEL = {"equipos": "Equipos y productos", "servicios": "Servicios", "proyectos": "Proyectos"}

NOTA_BASE = (
    "Base devengada: cada venta cuenta en el mes de su factura y cada gasto en el mes de su fecha, aunque se cobre o se"
    " pague en otro mes (no es un flujo de caja). Montos sin IVA, en colones: lo facturado o gastado en dólares, con el"
    " tipo de cambio guardado en cada documento. No incluye anulados ni rechazados por Hacienda; una nota de crédito"
    " resta en el mes en que se emitió. La planilla y las comisiones cuentan cuando se registran como pagadas."
    " Utilidad antes del impuesto sobre la renta. La contabilidad formal sigue en QuickBooks."
)


def q2(x) -> Decimal:
    return pl.d(x).quantize(Q2)


def month_key(x: date) -> str:
    return f"{x.year:04d}-{x.month:02d}"


def parse_month(s: str) -> date:
    y, m = (int(v) for v in s.split("-"))
    return date(y, m, 1)


def month_range(a: date, b: date) -> list[str]:
    """Meses de a a b (los dos el dia 1), inclusive."""
    out, cur = [], date(a.year, a.month, 1)
    while cur <= b:
        out.append(month_key(cur))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def last_day(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


@dataclass
class Row:
    key: str
    label: str
    kind: str = "line"  # section | line | subtotal | result
    values: dict[str, Decimal] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def add(self, month: str, amount: Decimal) -> None:
        self.values[month] = self.values.get(month, ZERO) + amount

    def total(self) -> Decimal:
        return sum(self.values.values(), ZERO)

    def out(self, months: list[str]) -> dict:
        if self.kind == "section":
            return {"key": self.key, "label": self.label, "kind": "section", **self.extra}
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "values": [str(q2(self.values.get(m, ZERO))) for m in months],
            "total": str(q2(self.total())),
            **self.extra,
        }


def _sum_rows(key: str, label: str, kind: str, rows: list[Row], months: list[str], sign: int = 1) -> Row:
    r = Row(key, label, kind)
    for m in months:
        r.values[m] = sign * sum((x.values.get(m, ZERO) for x in rows), ZERO)
    return r


def _diff(key: str, label: str, a: Row, b: Row, months: list[str]) -> Row:
    return Row(key, label, "result", {m: a.values.get(m, ZERO) - b.values.get(m, ZERO) for m in months})


def _income(db: Session, tid: int, a: date, b: date, conv: pl.Converter) -> tuple[dict[str, Row], Row, Row, int]:
    """Ingresos por categoria (antes del descuento global), descuentos y notas de credito, por mes."""
    sales = pl.invoices_of(db, tid, a, b)
    proyectos = pl._project_invoice_ids(db, tid, [s.inv for s in sales])  # noqa: SLF001 (misma regla que journal)
    pids = {ln.product_id for s in sales for ln in s.inv.lines if ln.product_id}
    kinds = dict(db.execute(select(Product.id, Product.item_type).where(Product.id.in_(pids))).all()) if pids else {}
    cats = {k: Row(k, INCOME_LABEL[k]) for k in INCOME_KEYS}
    descuentos = Row("descuentos", "Descuentos")
    ncs = Row("notas_credito", "Notas de crédito (anulaciones y devoluciones)")
    facturas = 0
    for s in sales:
        inv, mk = s.inv, month_key(s.on)
        rate_args = (inv.currency, inv.fx_sell, inv.issue_date)  # la tasa siempre es la de la factura
        if s.sign < 0:
            ncs.add(mk, -conv(pl.net_of(inv), *rate_args))
            continue
        facturas += 1
        gross = conv(inv.subtotal, *rate_args)  # subtotal = antes del descuento global (services/totals)
        disc = conv(inv.discount_total, *rate_args)
        if disc:
            descuentos.add(mk, -disc)
        if inv.id in proyectos:
            cats["proyectos"].add(mk, gross)
            continue
        # la linea guardada ya trae el descuento global (factor): se reparte el subtotal en proporcion a las lineas
        lines = [ln for ln in inv.lines if pl.d(ln.subtotal) != 0]
        base = sum((pl.d(ln.subtotal) for ln in lines), ZERO)
        if not lines or base == 0:
            cats["servicios"].add(mk, gross)
            continue
        repartido = ZERO
        for i, ln in enumerate(lines):
            part = gross - repartido if i == len(lines) - 1 else gross * pl.d(ln.subtotal) / base
            repartido += part
            kind = kinds.get(ln.product_id) if ln.product_id else None
            cats["equipos" if kind and kind != "servicio" else "servicios"].add(mk, part)
    return cats, descuentos, ncs, facturas


def _expenses(db: Session, tid: int, a: date, b: date, conv: pl.Converter, prorrata_pct) -> tuple[dict[str, dict], bool, int]:
    """Gasto aplicable por grupo y categoria, por mes. Devuelve (grupos, alguno_por_nombre, cuantos)."""
    cats = {c.id: c for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.tenant_id == tid))}
    groups: dict[str, dict] = {"costo_ventas": {}, "operativo": {}}
    auto_any = False
    n = 0
    q = select(Expense).where(Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado")
    for e in db.scalars(q):
        n += 1
        cat = cats.get(e.category_id)
        g, auto = pl.group_of(cat)
        auto_any = auto_any or (auto and cat is not None)
        credit = pl._expense_credit(e, prorrata_pct)  # noqa: SLF001 (la regla del IVA acreditable, una sola)
        amount = conv(pl.d(e.total) - credit, e.currency, e.fx_rate, e.date)
        k = cat.id if cat is not None else None
        if k not in groups[g]:
            groups[g][k] = Row(
                f"cat_{k or 'sin'}",
                cat.name if cat is not None else pl.NO_CATEGORY,
                extra={"category_id": k, "auto": auto if cat is not None else False},
            )
        groups[g][k].add(month_key(e.date), amount)
    return groups, auto_any, n


def _kardex_cost(db: Session, tid: int, a: date, b: date) -> Row:
    """Costo registrado en las salidas por venta de las facturas del periodo (por el mes de la factura)."""
    r = Row("costo_inventario", "Costo del inventario vendido (kardex, referencia)")
    q = (
        select(StockMovement.quantity, StockMovement.unit_cost, Invoice.issue_date)
        .join(Invoice, Invoice.id == StockMovement.invoice_id)
        .where(
            StockMovement.tenant_id == tid,
            Invoice.tenant_id == tid,
            StockMovement.kind == "venta",
            StockMovement.unit_cost.isnot(None),
            Invoice.issue_date >= a,
            Invoice.issue_date <= b,
            Invoice.status != "anulada",
            Invoice.einvoice_status != "rechazada",
        )
    )
    for qty, cost, on in db.execute(q):
        r.add(month_key(on), -pl.d(qty) * pl.d(cost))  # la salida por venta es negativa
    return r


def _avisos(db: Session, tid: int, a: date, b: date, auto_any: bool, sin_categoria: bool) -> list[str]:
    out: list[str] = []
    sin_gasto = pl.unpaid_commission_expenses(db, tid, a, b)
    if sin_gasto:
        out.append(f"{sin_gasto} comisión(es) pagada(s) en el periodo sin gasto registrado: registralas en Comisiones para que cuenten aquí.")
    planillas = db.scalars(
        select(PayrollRun).where(PayrollRun.tenant_id == tid, PayrollRun.status == "aprobada", PayrollRun.period_end >= a, PayrollRun.period_end <= b)
    ).all()
    if planillas:
        out.append(f"{len(planillas)} planilla(s) aprobada(s) del periodo todavía sin pagar: no cuentan hasta registrarlas como pagadas.")
    if auto_any:
        out.append("Algunas categorías se ubicaron por su nombre (costo de ventas u operación): elegí su grupo en Contabilidad → Contable → Categorías.")
    if sin_categoria:
        out.append("Hay gastos sin categoría: asignales una para que el detalle sea útil.")
    return out


def build(db: Session, tid: int, first: date, last: date, sees_costs: bool) -> dict:
    """first y last: el dia 1 del primer y del ultimo mes."""
    from ..models import Tenant

    months = month_range(first, last)
    if not months:
        raise ValueError("rango vacío")
    if len(months) > MAX_MONTHS:
        raise ValueError(f"máximo {MAX_MONTHS} meses")
    a, b = first, last_day(last)
    conv = pl.Converter(db, "CRC")
    t = db.get(Tenant, tid)
    pct = (t.settings or {}).get("iva_prorrata_pct") if t else None

    cats, descuentos, ncs, n_facturas = _income(db, tid, a, b, conv)
    ingresos_rows = [cats[k] for k in INCOME_KEYS] + [x for x in (descuentos, ncs) if x.values]
    ingresos = _sum_rows("total_ingresos", "Total de ingresos (sin IVA)", "subtotal", ingresos_rows, months)

    groups, auto_any, n_gastos = _expenses(db, tid, a, b, conv, pct)

    def ordered(g: str) -> list[Row]:
        return sorted(groups[g].values(), key=lambda r: (r.extra["category_id"] is None, -r.total(), r.label.lower()))

    costo_rows, oper_rows = ordered("costo_ventas"), ordered("operativo")
    costo = _sum_rows("total_costo_ventas", "Total costo de ventas", "subtotal", costo_rows, months)
    bruta = _diff("utilidad_bruta", "Utilidad bruta", ingresos, costo, months)
    oper = _sum_rows("total_gastos_operacion", "Total gastos de operación", "subtotal", oper_rows, months)
    neta = _diff("utilidad_neta", "Utilidad neta (antes del impuesto sobre la renta)", bruta, oper, months)

    rows: list[Row] = [Row("sec_ingresos", "Ingresos", "section"), *ingresos_rows, ingresos]
    rows += [Row("sec_costo_ventas", "Costo de ventas", "section"), *costo_rows, costo, bruta]
    rows += [Row("sec_gastos_operacion", "Gastos de operación", "section"), *oper_rows, oper, neta]

    def pct_of(part: Row) -> list[str | None]:
        out = []
        for m in [*months, None]:
            v = ingresos.total() if m is None else ingresos.values.get(m, ZERO)
            p = part.total() if m is None else part.values.get(m, ZERO)
            out.append(str((p / v * 100).quantize(Decimal("0.1"))) if v else None)
        return out

    kardex = _kardex_cost(db, tid, a, b) if sees_costs else None
    sin_categoria = any(r.extra.get("category_id") is None for r in costo_rows + oper_rows)
    return {
        "from": months[0],
        "to": months[-1],
        "months": months,
        "currency": "CRC",
        "basis": "devengado",
        "note": NOTA_BASE,
        "rows": [r.out(months) for r in rows],
        "margins": {"bruto_pct": pct_of(bruta), "neto_pct": pct_of(neta)},
        # solo quien ve costos; null para los demas (no es "cero": es que no le toca verlo)
        "inventory_cost": kardex.out(months) if kardex is not None and kardex.values else None,
        "sees_costs": sees_costs,
        "counts": {"facturas": n_facturas, "gastos": n_gastos},
        "warnings": _avisos(db, tid, a, b, auto_any, sin_categoria),
        "summary": {
            "ingresos": str(q2(ingresos.total())),
            "costo_ventas": str(q2(costo.total())),
            "utilidad_bruta": str(q2(bruta.total())),
            "gastos_operacion": str(q2(oper.total())),
            "utilidad_neta": str(q2(neta.total())),
        },
    }


# ---------------------------------------------------------------- Excel
MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic")


def month_label(mk: str) -> str:
    y, m = mk.split("-")
    return f"{MONTHS_ES[int(m) - 1]} {y}"


def to_xlsx(data: dict, tenant_name: str) -> bytes:
    """Una hoja: concepto, un mes por columna y el total. Secciones con banda, subtotales en negrita, numeros como
    numeros con formato de colones. Los nombres de categoria van como texto literal (nunca formulas)."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from .reports import FORMULA_PREFIXES

    money = '"₡"#,##0.00;[Red]-"₡"#,##0.00'
    dark = PatternFill("solid", fgColor="15131A")
    soft = PatternFill("solid", fgColor="F3EEF0")
    thin = Side(style="thin", color="8A858F")
    months = data["months"]
    ncols = len(months) + 2

    def lit(cell):
        if isinstance(cell.value, str) and cell.value[:1] in FORMULA_PREFIXES:
            cell.data_type = "s"
            cell.quotePrefix = True

    wb = Workbook()
    ws = wb.active
    ws.title = "Estado de resultados"
    ws.column_dimensions["A"].width = 50
    for i in range(2, ncols + 1):
        ws.column_dimensions[get_column_letter(i)].width = 16
    ws["A1"] = tenant_name
    lit(ws["A1"])
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Estado de resultados mensual"
    ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = f"De {month_label(months[0])} a {month_label(months[-1])} · en colones (₡) · base devengada"
    ws["A3"].font = Font(color="8A858F")
    ws["A4"] = data["note"]
    ws["A4"].font = Font(color="8A858F", italic=True, size=9)
    ws["A4"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=max(ncols, 4))
    ws.row_dimensions[4].height = 54
    r = 6
    for c, val in enumerate(["Concepto", *[month_label(m) for m in months], "Total"], start=1):
        cell = ws.cell(r, c, val)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = dark
        cell.alignment = Alignment(horizontal="left" if c == 1 else "right")
    r += 1

    def put(label: str, values: list, total, kind: str, fmt: str = money):
        nonlocal r
        cell = ws.cell(r, 1, label)
        lit(cell)
        cell.alignment = Alignment(indent=0 if kind != "line" else 1)
        for c, v in enumerate([*values, total], start=2):
            if v is None:
                continue
            x = ws.cell(r, c, float(Decimal(v)) if fmt == money else float(Decimal(v)) / 100)
            x.number_format = fmt
        if kind in ("subtotal", "result"):
            for c in range(1, ncols + 1):
                ws.cell(r, c).font = Font(bold=True)
                ws.cell(r, c).fill = soft
                ws.cell(r, c).border = Border(top=thin)
        r += 1

    for row in data["rows"]:
        if row["kind"] == "section":
            if r > 7:
                r += 1
            ws.cell(r, 1, row["label"]).font = Font(bold=True, size=11)
            r += 1
            continue
        put(row["label"], row["values"], row["total"], row["kind"])
    r += 1
    m = data["margins"]
    put("Margen bruto (% de los ingresos)", m["bruto_pct"][:-1], m["bruto_pct"][-1], "line", "0.0%")
    put("Margen neto (% de los ingresos)", m["neto_pct"][:-1], m["neto_pct"][-1], "line", "0.0%")
    if data.get("inventory_cost"):
        k = data["inventory_cost"]
        r += 1
        put(k["label"], k["values"], k["total"], "line")
        ws.cell(r, 1, "Referencia: no se resta (las compras de equipo ya cuentan en «Costo de ventas»).").font = Font(color="8A858F", italic=True, size=9)
        r += 1
    if data["warnings"]:
        r += 1
        for w in data["warnings"]:
            ws.cell(r, 1, "⚠ " + w).font = Font(color="B00020", italic=True, size=9)
            r += 1
    ws.freeze_panes = "B7"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
