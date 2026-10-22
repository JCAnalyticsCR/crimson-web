"""Estado de resultados completo (reunion 2, pedido 1): ingresos por tipo y por vendedor, costo de ventas y gastos por
categoria (comisiones incluidas), IVA cobrado y pagado, utilidad bruta y neta. Todo en UNA moneda base (colones por
defecto, o dolares).

Tipo de cambio: el que la plataforma ya usa para cada documento. Si el documento guardo su tasa (factura: fx_sell;
gasto: fx_rate) manda esa; si no (o quedo en 1, lo que dejaba el sistema viejo), la del BCCR de su fecha
(services/fx.doc_rate). Para pasar a dolares un documento en colones se usa la tasa del dolar que el documento guardo
(una factura en colones tambien guarda el tipo de cambio del dia) y, si no, la del BCCR de su fecha. Nunca se suma un
dolar como un colon: sin ninguna tasa cargada sale FxMissing (422 claro).

Ingresos = venta sin IVA de las facturas del periodo (subtotal - descuento global) con la regla del borrador D-104
(services/reports.iva_breakdown): las rechazadas por Hacienda no cuentan; una anulada por una NC aceptada sigue en
su mes y la NC resta su venta e IVA en el mes en que se emitio (un mes cerrado no cambia despues del cierre); una
anulada sin NC aceptada no cuenta. Tipo: si la factura es de un proyecto (el proyecto la apunta, nace de la cotizacion del proyecto o la
oportunidad es "proyecto") todo va a Proyectos; si no, cada linea por el tipo de su producto (producto -> Equipos y
productos, servicio -> Servicios; sin producto del catalogo -> Otros). El descuento global se reparte en proporcion.

Gastos = gasto aplicable (total - IVA acreditable): con condicion 04 el IVA no acreditable ES gasto. Cada categoria
cae en "Costo de ventas" o en "Gastos de operacion" segun ExpenseCategory.pnl_group; sin elegir, por su nombre
(compras, equipos, materiales, mercaderia, inventario, subcontratos -> costo de ventas). Las comisiones pagadas entran
como gasto de su categoria "Comisiones" (services/commission_expense.py).

Utilidad neta = antes del impuesto sobre la renta (la plataforma no lo calcula).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Expense, ExpenseCategory, Invoice, Opportunity, Product, Project, Tenant, User

BASES = ("CRC", "USD")
TYPE_LABEL = {
    "equipos": "Equipos y productos",
    "servicios": "Servicios",
    "proyectos": "Proyectos",
    "otros": "Otros ingresos (líneas sin producto del catálogo)",
}
TYPE_ORDER = ("equipos", "servicios", "proyectos", "otros")
GROUP_LABEL = {"costo_ventas": "Costo de ventas", "operativo": "Gastos de operación"}
# por nombre, cuando la categoria no dice a que grupo va (pnl_group vacio)
COST_WORDS = ("compra", "equipos y materiales", "material", "mercader", "inventario", "costo de venta", "subcontrat")
NO_CATEGORY = "Sin categoría"
NO_SELLER = "Sin vendedor (tienda en línea, API o recurrencias)"
Q2 = Decimal("0.01")


def d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


def q2(x: Decimal) -> Decimal:
    return d(x).quantize(Q2)


def group_of(cat: ExpenseCategory | None) -> tuple[str, bool]:
    """(grupo, automatico). automatico=True: salio del nombre porque la categoria no lo eligio."""
    if cat is not None and cat.pnl_group in GROUP_LABEL:
        return cat.pnl_group, False
    name = (cat.name if cat is not None else "").lower()
    if name and any(w in name for w in COST_WORDS):
        return "costo_ventas", True
    return "operativo", True


def is_commission_category(cat: ExpenseCategory | None) -> bool:
    return cat is not None and cat.name.strip().lower() == "comisiones"


class Converter:
    """Monto de un documento a la moneda base con la tasa del documento (ver el docstring del modulo). Cachea las
    consultas de tasa por (moneda, fecha): un mes con cientos de facturas no hace cientos de consultas iguales."""

    def __init__(self, db: Session, base: str = "CRC"):
        if base not in BASES:
            raise ValueError(base)
        self.db, self.base = db, base
        self._cache: dict[tuple[str, date | None], Decimal] = {}

    def _bccr(self, cur: str, on: date | None) -> Decimal:
        from .fx import fx

        k = (cur, on)
        if k not in self._cache:
            self._cache[k] = fx(self.db, cur, on).tasa
        return self._cache[k]

    def rate_to_crc(self, currency: str | None, stored, on: date | None) -> Decimal:
        cur = (currency or "CRC").upper()
        if cur == "CRC":
            return Decimal(1)
        r = d(stored)
        return r if r > 1 else self._bccr(cur, on)

    def __call__(self, amount, currency: str | None, stored, on: date | None) -> Decimal:
        cur = (currency or "CRC").upper()
        v = d(amount)
        if cur == self.base:
            return v
        crc = v * self.rate_to_crc(cur, stored, on)
        if self.base == "CRC":
            return crc
        # a dolares: la tasa del dolar que guardo el documento (en uno en colones, fx_sell/fx_rate es la del dia)
        usd = d(stored) if cur == "CRC" and d(stored) > 1 else self._bccr(self.base, on)
        return crc / usd


@dataclass
class Line:
    key: str
    label: str
    amount: Decimal = Decimal(0)
    extra: dict = field(default_factory=dict)

    def out(self) -> dict:
        return {"key": self.key, "label": self.label, "amount": q2(self.amount), **self.extra}


def _project_invoice_ids(db: Session, tid: int, invs: list[Invoice]) -> set[int]:
    """Facturas de un proyecto: el proyecto la apunta, nace de la cotizacion de un proyecto o su oportunidad es
    "proyecto" (la misma regla que services/commissions.deal_of, en lote)."""
    ids = {i.id for i in invs}
    qids = {i.quote_id for i in invs if i.quote_id}
    out: set[int] = set()
    if not ids:
        return out
    out |= {x for x in db.scalars(select(Project.invoice_id).where(Project.tenant_id == tid, Project.invoice_id.in_(ids))) if x}
    if qids:
        proj_q = {x for x in db.scalars(select(Project.quote_id).where(Project.tenant_id == tid, Project.quote_id.in_(qids))) if x}
        opp_q = {
            x
            for x in db.scalars(
                select(Opportunity.quote_id).where(Opportunity.tenant_id == tid, Opportunity.quote_id.in_(qids), Opportunity.kind == "proyecto")
            )
            if x
        }
        out |= {i.id for i in invs if i.quote_id and i.quote_id in (proj_q | opp_q)}
    return out


@dataclass
class Sale:
    """Una factura en el periodo con su signo: +1 en su mes de emision, -1 en el mes de la NC que la anulo. `on` es la
    fecha del movimiento (la de la factura o la de la NC); la tasa siempre es la de la factura."""

    inv: Invoice
    sign: int
    on: date


def invoices_of(db: Session, tid: int, a: date, b: date) -> list[Sale]:
    """Las ventas del periodo con la misma regla que el IVA debito del D-104 (services/reports.iva_breakdown)."""
    from ..models import EInvoiceDocument
    from .documents import local_date

    nc_inv = {
        x.invoice_id: x
        for x in db.scalars(
            select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == tid, EInvoiceDocument.doc_type == "NC", EInvoiceDocument.status == "aceptada")
        )
    }
    out: list[Sale] = []
    for inv in db.scalars(
        select(Invoice).where(Invoice.tenant_id == tid, Invoice.issue_date >= a, Invoice.issue_date <= b).order_by(Invoice.issue_date, Invoice.id)
    ):
        if inv.einvoice_status == "rechazada":
            continue
        if inv.status == "anulada" and inv.id not in nc_inv:
            continue
        out.append(Sale(inv, 1, inv.issue_date))
    for inv_id, nc in nc_inv.items():
        when = local_date(nc.created_at)  # la NC resta en el mes en que se emitio
        if when and a <= when <= b:
            inv = db.get(Invoice, inv_id)
            if inv is not None and inv.tenant_id == tid:
                out.append(Sale(inv, -1, when))
    return out


def net_of(inv: Invoice) -> Decimal:
    return d(inv.subtotal) - d(inv.discount_total)


def income(db: Session, tid: int, a: date, b: date, conv: Converter, invs: list[Sale] | None = None) -> dict:
    """Ingresos (sin IVA) por tipo y por vendedor, e IVA cobrado, en la moneda base (las NC del periodo restan)."""
    sales = invoices_of(db, tid, a, b) if invs is None else invs
    plain = [s.inv for s in sales]
    proyectos = _project_invoice_ids(db, tid, plain)
    pids = {ln.product_id for i in plain for ln in i.lines if ln.product_id}
    kinds = dict(db.execute(select(Product.id, Product.item_type).where(Product.id.in_(pids))).all()) if pids else {}
    por_tipo = {k: Line(k, TYPE_LABEL[k]) for k in TYPE_ORDER}
    vendedores: dict[int | None, Line] = {}
    total = iva = Decimal(0)
    n_docs = 0
    for s in sales:
        inv, sign = s.inv, s.sign
        net = sign * conv(net_of(inv), inv.currency, inv.fx_sell, inv.issue_date)
        iva += sign * conv(inv.tax_total, inv.currency, inv.fx_sell, inv.issue_date)
        total += net
        n_docs += 1 if sign > 0 else 0
        sk = inv.seller_id
        if sk not in vendedores:
            vendedores[sk] = Line(str(sk or "sin"), NO_SELLER if sk is None else "", extra={"seller_id": sk, "invoices": 0})
        vendedores[sk].amount += net
        vendedores[sk].extra["invoices"] += 1 if sign > 0 else 0
        if inv.id in proyectos:
            por_tipo["proyectos"].amount += net
            continue
        lines = [ln for ln in inv.lines if d(ln.subtotal) != 0]
        gross = sum((d(ln.subtotal) for ln in lines), Decimal(0))
        if not lines or gross == 0:
            por_tipo["otros"].amount += net
            continue
        repartido = Decimal(0)
        for i, ln in enumerate(lines):
            # el ultimo se lleva el redondeo: la suma por tipo es exactamente la venta neta de la factura
            part = net - repartido if i == len(lines) - 1 else net * d(ln.subtotal) / gross
            repartido += part
            kind = kinds.get(ln.product_id) if ln.product_id else None
            por_tipo["servicios" if kind == "servicio" else "equipos" if kind else "otros"].amount += part
    names = dict(db.execute(select(User.id, User.full_name).where(User.id.in_([k for k in vendedores if k]))).all()) if any(vendedores) else {}
    for k, ln in vendedores.items():
        if k is not None:
            ln.label = names.get(k) or f"Usuario {k}"
    return {
        "por_tipo": [ln.out() for ln in por_tipo.values()],
        "por_vendedor": [ln.out() for ln in sorted(vendedores.values(), key=lambda x: -x.amount)],
        "total": q2(total),
        "iva_cobrado": q2(iva),
        "facturas": n_docs,
    }


def _expense_credit(e: Expense, prorrata_pct) -> Decimal:
    """IVA acreditable del gasto en su divisa (la regla de services/reports; proporcional = la prorrata del periodo)."""
    from .reports import _expense_credit as credit

    c = credit(e)
    if c is None:
        c = d(e.tax_amount) * d(prorrata_pct or 0) / Decimal(100)
    return c


def expenses(db: Session, tid: int, a: date, b: date, conv: Converter, prorrata_pct=None) -> dict:
    cats = {c.id: c for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.tenant_id == tid))}
    groups: dict[str, dict[int | None, Line]] = {"costo_ventas": {}, "operativo": {}}
    iva_pagado = iva_acreditable = Decimal(0)
    n = 0
    for e in db.scalars(select(Expense).where(Expense.tenant_id == tid, Expense.date >= a, Expense.date <= b, Expense.status != "anulado")):
        n += 1
        cat = cats.get(e.category_id)
        g, auto = group_of(cat)
        credit = _expense_credit(e, prorrata_pct)
        amount = conv(d(e.total) - credit, e.currency, e.fx_rate, e.date)
        iva_pagado += conv(e.tax_amount, e.currency, e.fx_rate, e.date)
        iva_acreditable += conv(credit, e.currency, e.fx_rate, e.date)
        k = cat.id if cat is not None else None
        if k not in groups[g]:
            groups[g][k] = Line(
                str(k or "sin"),
                cat.name if cat is not None else NO_CATEGORY,
                extra={"category_id": k, "auto": auto, "commissions": is_commission_category(cat), "expenses": 0},
            )
        groups[g][k].amount += amount
        groups[g][k].extra["expenses"] += 1
    out = {}
    for g, rows in groups.items():
        lst = sorted(rows.values(), key=lambda x: -x.amount)
        out[g] = {"label": GROUP_LABEL[g], "por_categoria": [x.out() for x in lst], "total": q2(sum((x.amount for x in lst), Decimal(0)))}
    out["iva_pagado"] = q2(iva_pagado)
    out["iva_acreditable"] = q2(iva_acreditable)
    out["gastos"] = n
    return out


def _pct(part: Decimal, whole: Decimal) -> float | None:
    return round(float(part / whole * 100), 1) if whole else None


def unpaid_commission_expenses(db: Session, tid: int, a: date, b: date) -> int:
    """Comisiones de vendedor pagadas en el periodo que no tienen gasto (se pagaron antes de que existiera el registro
    automatico): el estado de resultados avisa y el admin las registra con un clic."""
    from ..models import Commission
    from .commission_expense import applies

    rows = db.scalars(
        select(Commission).where(
            Commission.tenant_id == tid, Commission.status == "pagada", Commission.expense_id.is_(None), Commission.paid_on >= a, Commission.paid_on <= b
        )
    ).all()
    return sum(1 for c in rows if applies(c))


def ally_commissions_paid(db: Session, tid: int, a: date, b: date) -> int:
    """Comisiones de aliado pagadas en el periodo: no van a "Comisiones" (su factura entra por Recepcion y cuenta en la
    categoria con que se acepto). El estado de resultados lo dice para que el indicador no se lea como el total."""
    from ..models import Commission
    from .commission_expense import applies

    rows = db.scalars(
        select(Commission).where(Commission.tenant_id == tid, Commission.status == "pagada", Commission.paid_on >= a, Commission.paid_on <= b)
    ).all()
    return sum(1 for c in rows if not applies(c))


def build(db: Session, tid: int, a: date, b: date, base: str = "CRC") -> dict:
    base = (base or "CRC").upper()
    conv = Converter(db, base)
    t = db.get(Tenant, tid)
    pct = (t.settings or {}).get("iva_prorrata_pct") if t else None
    inc = income(db, tid, a, b, conv)
    exp = expenses(db, tid, a, b, conv, pct)
    ventas = inc["total"]
    costo = exp["costo_ventas"]["total"]
    operativos = exp["operativo"]["total"]
    bruta = ventas - costo
    neta = bruta - operativos
    comisiones = q2(sum((d(x["amount"]) for g in ("costo_ventas", "operativo") for x in exp[g]["por_categoria"] if x["commissions"]), Decimal(0)))
    avisos = []
    sin_gasto = unpaid_commission_expenses(db, tid, a, b)
    if sin_gasto:
        avisos.append(f"{sin_gasto} comisión(es) pagada(s) en el periodo sin gasto registrado: regístrelas para que cuenten aquí")
    aliados = ally_commissions_paid(db, tid, a, b)
    if aliados:
        avisos.append(
            f"{aliados} pago(s) a aliados en el periodo no suman en «Comisiones»: su factura entra por Recepción y cuenta en la categoría"
            " con que se aceptó (elija «Comisiones» al aceptarla para verlas juntas)"
        )
    if any(x["auto"] for g in ("costo_ventas", "operativo") for x in exp[g]["por_categoria"]):
        avisos.append("Algunas categorías se clasificaron por su nombre: elija su grupo en Contabilidad → Categorías")
    return {
        "from": a,
        "to": b,
        "currency": base,
        "ingresos": inc,
        "costo_ventas": exp["costo_ventas"],
        "utilidad_bruta": q2(bruta),
        "margen_bruto_pct": _pct(bruta, ventas),
        "gastos_operacion": exp["operativo"],
        "comisiones": comisiones,
        "gastos_total": q2(costo + operativos),
        "utilidad_neta": q2(neta),
        "margen_neto_pct": _pct(neta, ventas),
        "iva": {
            "cobrado": inc["iva_cobrado"],
            "pagado": exp["iva_pagado"],
            "acreditable": exp["iva_acreditable"],
            "no_acreditable": q2(exp["iva_pagado"] - exp["iva_acreditable"]),
            "neto": q2(inc["iva_cobrado"] - exp["iva_acreditable"]),
        },
        "comisiones_sin_gasto": sin_gasto,
        "avisos": avisos,
    }


def rows_of(pl: dict) -> list[list]:
    """Filas (Concepto, Monto) para la vista generica de /reports/resultados (y su compatibilidad: "Gastos" es el total
    de gastos y "Ventas netas" el de ingresos)."""
    rows: list[list] = []
    for x in pl["ingresos"]["por_tipo"]:
        rows.append([f"Ingresos · {x['label']}", x["amount"]])
    rows.append(["Ventas netas", pl["ingresos"]["total"]])
    for x in pl["costo_ventas"]["por_categoria"]:
        rows.append([f"Costo de ventas · {x['label']}", x["amount"]])
    rows.append(["Costo de ventas", pl["costo_ventas"]["total"]])
    rows.append(["Utilidad bruta", pl["utilidad_bruta"]])
    for x in pl["gastos_operacion"]["por_categoria"]:
        rows.append([f"Gasto · {x['label']}", x["amount"]])
    rows.append(["Gastos de operación", pl["gastos_operacion"]["total"]])
    rows.append(["Gastos", pl["gastos_total"]])
    rows.append(["Utilidad neta (antes del impuesto sobre la renta)", pl["utilidad_neta"]])
    rows.append(["IVA cobrado en ventas", pl["iva"]["cobrado"]])
    rows.append(["IVA pagado en compras y gastos", pl["iva"]["pagado"]])
    rows.append(["   · de él, acreditable", pl["iva"]["acreditable"]])
    for x in pl["ingresos"]["por_vendedor"]:
        rows.append([f"Ventas de {x['label']}", x["amount"]])
    return rows


# ---------------------------------------------------------------- Excel
def to_xlsx(pl: dict, tenant_name: str) -> bytes:
    """Estado de resultados con formato: encabezado, secciones, subtotales en negrita, formato de moneda y una hoja de
    ventas por vendedor. Los nombres (categorias, vendedores) se escriben como texto literal (sin formulas)."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    from .reports import FORMULA_PREFIXES

    cur = pl["currency"]
    money = '"₡"#,##0.00;[Red]-"₡"#,##0.00' if cur == "CRC" else '"$"#,##0.00;[Red]-"$"#,##0.00'
    dark = PatternFill("solid", fgColor="15131A")
    soft = PatternFill("solid", fgColor="F3EEF0")
    thin = Side(style="thin", color="8A858F")

    def lit(cell):
        """Texto que empieza por = + - @ va como texto literal (nombres de categorias y vendedores: sin formulas)."""
        if isinstance(cell.value, str) and cell.value[:1] in FORMULA_PREFIXES:
            cell.data_type = "s"
            cell.quotePrefix = True

    wb = Workbook()
    ws = wb.active
    ws.title = "Estado de resultados"
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 12
    ws["A1"] = tenant_name
    lit(ws["A1"])
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Estado de resultados"
    ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = f"Del {pl['from'].strftime('%d/%m/%Y')} al {pl['to'].strftime('%d/%m/%Y')} · en {'colones (₡)' if cur == 'CRC' else 'dólares (US$)'}"
    ws["A3"].font = Font(color="8A858F")
    ws["A4"] = "Tipo de cambio: el de cada documento; si no lo guardó, el del BCCR de su fecha."
    ws["A4"].font = Font(color="8A858F", italic=True, size=9)
    r = 6

    def head(text: str):
        nonlocal r
        for col, val in (("A", text), ("B", "Monto"), ("C", "% ventas")):
            c = ws[f"{col}{r}"]
            c.value = val
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = dark
            c.alignment = Alignment(horizontal="left" if col == "A" else "right")
        r += 1

    def line(text: str, amount, bold: bool = False, indent: int = 1, total: bool = False):
        nonlocal r
        ws[f"A{r}"] = text
        lit(ws[f"A{r}"])
        ws[f"A{r}"].alignment = Alignment(indent=indent)
        ws[f"B{r}"] = float(d(amount))
        ws[f"B{r}"].number_format = money
        ventas = d(pl["ingresos"]["total"])
        if ventas:
            ws[f"C{r}"] = float(d(amount) / ventas)
            ws[f"C{r}"].number_format = "0.0%"
        if bold or total:
            for col in "ABC":
                ws[f"{col}{r}"].font = Font(bold=True)
        if total:
            for col in "ABC":
                ws[f"{col}{r}"].fill = soft
                ws[f"{col}{r}"].border = Border(top=thin)
        r += 1

    head("Ingresos (sin IVA)")
    for x in pl["ingresos"]["por_tipo"]:
        line(x["label"], x["amount"])
    line("Total de ingresos", pl["ingresos"]["total"], total=True, indent=0)
    r += 1
    head("Costo de ventas")
    for x in pl["costo_ventas"]["por_categoria"]:
        line(x["label"], x["amount"])
    line("Total costo de ventas", pl["costo_ventas"]["total"], total=True, indent=0)
    line("Utilidad bruta", pl["utilidad_bruta"], total=True, indent=0)
    r += 1
    head("Gastos de operación")
    for x in pl["gastos_operacion"]["por_categoria"]:
        line(x["label"] + (" (comisiones pagadas)" if x["commissions"] and x["label"].lower() == "comisiones" else ""), x["amount"])
    line("Total gastos de operación", pl["gastos_operacion"]["total"], total=True, indent=0)
    r += 1
    line("Utilidad neta (antes del impuesto sobre la renta)", pl["utilidad_neta"], total=True, indent=0)
    r += 1
    head("IVA del periodo (informativo)")
    line("IVA cobrado en ventas", pl["iva"]["cobrado"])
    line("IVA pagado en compras y gastos", pl["iva"]["pagado"])
    line("   de él, acreditable", pl["iva"]["acreditable"], indent=2)
    line("   de él, no acreditable (ya incluido en los gastos)", pl["iva"]["no_acreditable"], indent=2)
    line("IVA neto (cobrado − acreditable)", pl["iva"]["neto"], bold=True, indent=0)
    if pl["avisos"]:
        r += 1
        for a in pl["avisos"]:
            ws[f"A{r}"] = "⚠ " + a
            ws[f"A{r}"].font = Font(color="B00020", italic=True, size=9)
            r += 1
    ws.freeze_panes = "A6"

    v = wb.create_sheet("Ventas por vendedor")
    v.column_dimensions["A"].width = 48
    v.column_dimensions["B"].width = 12
    v.column_dimensions["C"].width = 20
    v.column_dimensions["D"].width = 12
    v.append(["Vendedor", "Facturas", "Venta sin IVA", "% del total"])
    for c in v[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = dark
    tot = d(pl["ingresos"]["total"])
    for x in pl["ingresos"]["por_vendedor"]:
        v.append([x["label"], x.get("invoices", 0), float(d(x["amount"])), float(d(x["amount"]) / tot) if tot else None])
        lit(v.cell(v.max_row, 1))
        v.cell(v.max_row, 3).number_format = money
        v.cell(v.max_row, 4).number_format = "0.0%"
    v.append(["Total", sum(x.get("invoices", 0) for x in pl["ingresos"]["por_vendedor"]), float(tot), 1 if tot else None])
    for c in v[v.max_row]:
        c.font = Font(bold=True)
        c.fill = soft
    v.cell(v.max_row, 3).number_format = money
    v.cell(v.max_row, 4).number_format = "0.0%"
    v.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
