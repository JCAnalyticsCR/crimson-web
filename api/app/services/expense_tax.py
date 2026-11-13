"""Desglose del IVA de un gasto por tarifa (reunion con la contadora, 10/11/2026).

Una factura de proveedor (un supermercado, una farmacia) mezcla tarifas: 13 %, 4 %, 2 %, 1 %, exento, exonerado. El
gasto guarda UNA tarifa y los totales; el desglose dice cuanto de la base, del IVA y del credito es de cada tarifa:

    tax_breakdown(db, expense) -> list[TaxPart]      # firma estable: la usa la conexion con QuickBooks

Reglas
------
* Decimal siempre. Los montos llevan el signo del gasto (una nota de credito resta).
* La suma de las partes es el gasto: sum(base + tax) == total, sum(tax) == tax_amount y sum(acreditable) == el IVA
  acreditable del gasto (la misma regla que el D-104 en services/reports._expense_credit; proporcional = 0 con la marca
  prorrata). Si alguien cambio el gasto despues de guardar el desglose, las filas guardadas ya no suman y se vuelve a
  calcular (nunca se devuelve un desglose que no cuadra).
* Con XML: una parte por (tipo, tarifa) de las LineaDetalle. Base = SubTotal de la linea; impuesto = Monto menos la
  exoneracion. Los impuestos que no son IVA (selectivo, especificos) van aparte y nunca dan credito. Lo que el XML no
  explica linea a linea (otros cargos, redondeos) queda en «otros cargos». En una aceptacion parcial las partes se
  escalan a lo aceptado.
* Sin XML (gasto a mano): una sola parte con la tarifa del gasto.
* El credito se reparte entre las partes de IVA en proporcion a su impuesto; el ultimo centimo va a la ultima parte.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Expense, ExpenseTaxLine, ReceivedDocument
from ..providers.hacienda import xml44

Q5 = Decimal("0.00001")
ZERO = Decimal(0)
IVA_KINDS = ("gravado", "exonerado")
KIND_ORDER = {"gravado": 0, "exonerado": 1, "exento": 2, "otro_impuesto": 3, "otros_cargos": 4}
KIND_LABEL = {"gravado": "", "exonerado": "Exonerado", "exento": "Exento", "otro_impuesto": "Otro impuesto", "otros_cargos": "Otros cargos"}
EXEMPT_RATE_CODES = ("10",)  # CodigoTarifaIVA 10 = tarifa exenta (Nota 8.1, v4.4)
# Tarifas con cuenta propia opcional en QuickBooks. Si el rol de la tarifa no tiene cuenta, el asiento usa el general
# (iva_acreditable, o el gasto para el no acreditable): nada deja de exportarse por no mapearlos.
RATES = (Decimal(13), Decimal(8), Decimal(4), Decimal(2), Decimal(1), Decimal("0.5"))


def q5(x) -> Decimal:
    return (x if isinstance(x, Decimal) else Decimal(str(x or 0))).quantize(Q5, rounding=ROUND_HALF_UP)


def d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


@dataclass(frozen=True)
class TaxPart:
    """Una parte del gasto con su tarifa. rate en porcentaje (13, 4, 0.5...); montos en la divisa del gasto."""

    rate: Decimal
    base: Decimal
    tax: Decimal
    acreditable: Decimal
    kind: str = "gravado"  # gravado | exento | exonerado | otro_impuesto | otros_cargos
    rate_code: str | None = None
    exonerated: Decimal = ZERO
    prorrata: bool = False
    currency: str = "CRC"
    source: str = "manual"  # xml | manual

    @property
    def no_acreditable(self) -> Decimal:
        return self.tax - self.acreditable

    @property
    def total(self) -> Decimal:
        return self.base + self.tax

    @property
    def is_iva(self) -> bool:
        return self.kind in IVA_KINDS

    @property
    def label(self) -> str:
        r = rate_text(self.rate)
        if self.kind == "gravado":
            return f"{r} %"
        if self.kind == "exonerado":
            return f"Exonerado {r} %"
        if self.kind == "otro_impuesto":
            return f"Otro impuesto {r} %" if self.rate else "Otro impuesto"
        return KIND_LABEL.get(self.kind, self.kind)

    def out(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "rate": self.rate,
            "rate_code": self.rate_code,
            "base": self.base,
            "tax": self.tax,
            "exonerated": self.exonerated,
            "acreditable": self.acreditable,
            "no_acreditable": self.no_acreditable,
            "prorrata": self.prorrata,
            "currency": self.currency,
            "source": self.source,
        }


def rate_text(rate) -> str:
    s = format(d(rate).normalize(), "f")
    return s if "." not in s else s.rstrip("0").rstrip(".")


def norm_rate(rate) -> Decimal:
    """13.000 -> 13, 0.500 -> 0.5 (sin exponente: Decimal(10).normalize() seria 1E+1)."""
    return Decimal(rate_text(rate)) if rate else ZERO


def rate_key(rate) -> str:
    """13 -> "13", 0.5 -> "0_5" (para las claves de rol contable)."""
    return rate_text(rate).replace(".", "_")


def credit_role(rate) -> str:
    return f"iva_acreditable_{rate_key(rate)}"


def no_credit_role(rate) -> str:
    return f"iva_no_acreditable_{rate_key(rate)}"


# roles opcionales por tarifa (la conexion con QuickBooks puede listarlos en el mapeo)
RATE_ROLES: list[tuple[str, str]] = [(credit_role(r), f"IVA acreditable {rate_text(r)} % (opcional)") for r in RATES] + [
    (no_credit_role(r), f"IVA no acreditable {rate_text(r)} % (opcional; sin cuenta va al gasto)") for r in RATES
]


# ---------------------------------------------------------------- credito del gasto (misma regla que el D-104)
def expense_credit(e: Expense) -> Decimal | None:
    """IVA acreditable del gasto en su divisa. None = proporcional (la prorrata se aplica en el reporte de IVA).
    Filas viejas "credito" sin tax_credit_amount -> todo el IVA (services/reports._expense_credit)."""
    if e.iva_credit == "proporcional":
        return None
    if e.tax_credit_amount is not None:
        return d(e.tax_credit_amount)
    return d(e.tax_amount) if e.iva_credit in ("credito", "bienes_capital") else ZERO


# ---------------------------------------------------------------- lectura del XML
@dataclass
class _Raw:
    kind: str
    rate: Decimal
    rate_code: str | None
    base: Decimal = ZERO
    tax: Decimal = ZERO
    exonerated: Decimal = ZERO


def _rate_of(imp) -> tuple[Decimal, str | None]:
    code = xml44.text(imp, "CodigoTarifaIVA") or xml44.text(imp, "CodigoTarifa")
    tarifa = xml44.text(imp, "Tarifa")
    if tarifa is not None:
        rate = xml44.dec(tarifa)
    else:
        rate = xml44.RATE_OF_CODE.get(code or "", ZERO)
    return norm_rate(rate), code


def _line_base(ln) -> Decimal:
    sub = xml44.text(ln, "SubTotal")
    if sub is not None:
        return xml44.dec(sub)
    total = xml44.dec(xml44.text(ln, "MontoTotal"))
    desc = sum((xml44.dec(xml44.text(x, "MontoDescuento")) for x in xml44.kids(ln, "Descuento")), ZERO)
    return total - desc


def xml_raw_parts(root) -> list[_Raw]:
    """Partes sin ajustar (positivas, tal cual el XML) agrupadas por (tipo, tarifa)."""
    acc: OrderedDict[tuple, _Raw] = OrderedDict()

    def bucket(kind: str, rate: Decimal, code: str | None) -> _Raw:
        key = (kind, rate)
        if key not in acc:
            acc[key] = _Raw(kind, rate, code)
        elif acc[key].rate_code != code:
            acc[key].rate_code = None  # 04 y 06 son 4 %: la tarifa manda, el codigo solo si es uno
        return acc[key]

    for ln in xml44.descendants(root, "LineaDetalle"):
        base = _line_base(ln)
        imps = xml44.kids(ln, "Impuesto")
        ivas = [i for i in imps if (xml44.text(i, "Codigo") or "") in xml44.IVA_CODES]
        if not ivas:
            bucket("exento", ZERO, None).base += base
        for n, imp in enumerate(ivas):
            rate, code = _rate_of(imp)
            monto = xml44.dec(xml44.text(imp, "Monto"))
            exo = xml44.dec(xml44.text(imp, "Exoneracion", "MontoExoneracion"))
            net = max(monto - exo, ZERO)
            if exo > 0:
                kind = "exonerado"
            elif code in EXEMPT_RATE_CODES:
                kind = "exento"
            else:
                kind = "gravado"
            b = bucket(kind, rate, code)
            if n == 0:
                b.base += base  # la base de la linea una sola vez
            b.tax += net
            b.exonerated += min(exo, monto)
        for imp in imps:
            if imp in ivas:
                continue
            rate, code = _rate_of(imp)
            monto = xml44.dec(xml44.text(imp, "Monto"))
            exo = xml44.dec(xml44.text(imp, "Exoneracion", "MontoExoneracion"))
            b = bucket("otro_impuesto", rate, xml44.text(imp, "Codigo"))
            b.tax += max(monto - exo, ZERO)
            b.exonerated += min(exo, monto)
    return list(acc.values())


def _root_of(xml_text: str | None):
    if not xml_text:
        return None
    try:
        return xml44.parse_xml_bytes(xml_text.encode("utf-8"))
    except xml44.XmlRejected:
        return None


# ---------------------------------------------------------------- ajuste a los montos del gasto
def _largest(parts: list[_Raw], attr: str) -> _Raw | None:
    best = None
    for p in parts:
        if best is None or abs(getattr(p, attr)) > abs(getattr(best, attr)):
            best = p
    return best


def fit(
    raw: list[_Raw],
    *,
    total: Decimal,
    tax: Decimal,
    credit: Decimal | None,
    currency: str,
    source: str,
    sign: Decimal = Decimal(1),
    scale: bool = False,
) -> list[TaxPart]:
    """Lleva las partes del XML a los montos del gasto (signo, aceptacion parcial, otros cargos) y reparte el credito.
    Garantiza sum(base + tax) == total, sum(tax) == tax y sum(acreditable) == credit (0 si es prorrata)."""
    total, tax = q5(total), q5(tax)
    target_base = total - tax
    parts = [_Raw(p.kind, p.rate, p.rate_code, sign * p.base, sign * p.tax, sign * p.exonerated) for p in raw]
    if scale and parts:
        raw_tax = sum((p.tax for p in parts), ZERO)
        raw_base = sum((p.base for p in parts), ZERO)
        ft = tax / raw_tax if raw_tax else ZERO
        fb = target_base / raw_base if raw_base else ZERO
        for p in parts:
            p.tax, p.base, p.exonerated = p.tax * ft, p.base * fb, p.exonerated * fb
    for p in parts:
        p.base, p.tax, p.exonerated = q5(p.base), q5(p.tax), q5(p.exonerated)
    # lo que el XML no explica linea a linea
    rt = tax - sum((p.tax for p in parts), ZERO)
    if rt:
        big = _largest([p for p in parts if p.tax], "tax")
        if big is not None and abs(rt) <= Decimal("0.05"):
            big.tax += rt
        else:
            parts.append(_Raw("otro_impuesto", ZERO, None, ZERO, rt))
    rb = target_base - sum((p.base for p in parts), ZERO)
    if rb:
        cargos = next((p for p in parts if p.kind == "otros_cargos"), None)
        big = _largest(parts, "base")
        if cargos is not None:
            cargos.base += rb
        elif big is not None and abs(rb) <= Decimal("0.05"):
            big.base += rb
        else:
            parts.append(_Raw("otros_cargos", ZERO, None, rb))
    parts = [p for p in parts if p.base or p.tax or p.exonerated]
    if not parts:
        parts = [_Raw("exento", ZERO, None, target_base, tax)]
    parts.sort(key=lambda p: (KIND_ORDER.get(p.kind, 9), -p.rate))
    # credito: entre las partes de IVA, en proporcion a su impuesto
    shares = [ZERO] * len(parts)
    prorrata = credit is None
    if not prorrata:
        credit = q5(credit)
        idx = [i for i, p in enumerate(parts) if p.kind in IVA_KINDS and p.tax]
        if not idx:
            idx = [i for i, p in enumerate(parts) if p.tax] or [0]
        weight = sum((parts[i].tax for i in idx), ZERO)
        given = ZERO
        for n, i in enumerate(idx):
            if n == len(idx) - 1:
                shares[i] = credit - given
            else:
                shares[i] = q5(credit * parts[i].tax / weight) if weight else ZERO
                given += shares[i]
    return [
        TaxPart(
            rate=p.rate,
            base=p.base,
            tax=p.tax,
            acreditable=shares[i],
            kind=p.kind,
            rate_code=p.rate_code,
            exonerated=p.exonerated,
            prorrata=prorrata and p.kind in IVA_KINDS,
            currency=currency,
            source=source,
        )
        for i, p in enumerate(parts)
    ]


def manual_parts(e: Expense) -> list[TaxPart]:
    """Gasto sin XML: una sola parte con la tarifa del gasto (exento si no lleva IVA)."""
    tax, total = q5(e.tax_amount), q5(e.total)
    rate = norm_rate(e.tax_rate) if e.tax_rate is not None else ZERO
    credit = expense_credit(e)
    kind = "gravado" if (tax or rate) else "exento"
    return [
        TaxPart(
            rate=rate if kind == "gravado" else ZERO,
            base=total - tax,
            tax=tax,
            acreditable=q5(credit) if credit is not None else ZERO,
            kind=kind,
            prorrata=credit is None and kind == "gravado",
            currency=(e.currency or "CRC").upper(),
            source="manual",
        )
    ]


def _xml_of(db: Session, e: Expense) -> tuple[ReceivedDocument | None, object]:
    if not e.received_document_id:
        return None, None
    doc = db.get(ReceivedDocument, e.received_document_id)
    if doc is None or doc.tenant_id != e.tenant_id:
        return None, None
    return doc, _root_of(doc.xml_document)


def compute(db: Session, e: Expense) -> list[TaxPart]:
    """Desglose calculado (sin mirar lo guardado): del XML si el gasto tiene uno con lineas, si no una parte."""
    doc, root = _xml_of(db, e)
    raw = xml_raw_parts(root) if root is not None else []
    if not raw:
        return manual_parts(e)
    total = d(e.total)
    sign = Decimal(-1) if total < 0 or (doc is not None and doc.doc_type == "NC") else Decimal(1)
    doc_total = sum((p.base + p.tax for p in raw), ZERO) + (d(doc.other_charges) if doc is not None else ZERO)
    # aceptacion parcial (o un gasto por menos que el comprobante): las partes se escalan a lo aceptado
    scale = bool(doc is not None and (doc.action == "parcial" or abs(total) + Decimal(1) < doc_total))
    return fit(raw, total=total, tax=d(e.tax_amount), credit=expense_credit(e), currency=(e.currency or "CRC").upper(), source="xml", sign=sign, scale=scale)


def document_parts(doc: ReceivedDocument, credit: Decimal | None) -> list[TaxPart]:
    """Desglose de un comprobante respondido (para el D-104, tenga gasto o no), con el credito que le da la respuesta
    (None = proporcional). Montos con signo (NC resta) en la divisa del comprobante."""
    sign = Decimal(-1) if doc.doc_type == "NC" else Decimal(1)
    total, tax = d(doc.total), d(doc.tax_total)
    scale = False
    if doc.action == "parcial":
        if doc.accepted_total is not None:
            total, tax, scale = d(doc.accepted_total), d(doc.accepted_tax), True
        elif doc.applicable_amount is not None:
            total, tax, scale = d(doc.applicable_amount) + (credit or ZERO), credit or ZERO, True
    currency = (doc.currency or "CRC").upper()
    signed_credit = sign * credit if credit is not None else None
    root = _root_of(doc.xml_document)
    raw = xml_raw_parts(root) if root is not None else []
    if not raw:
        sub = total - tax
        rate = (abs(tax) / abs(sub) * 100).quantize(Decimal("0.001")) if sub else ZERO
        raw = [_Raw("gravado" if tax else "exento", norm_rate(rate) if tax else ZERO, None, sub, tax)]
    return fit(raw, total=sign * total, tax=sign * tax, credit=signed_credit, currency=currency, source="xml", sign=sign, scale=scale)


# ---------------------------------------------------------------- guardado
def _stored(db: Session, e: Expense) -> list[ExpenseTaxLine]:
    return list(db.scalars(select(ExpenseTaxLine).where(ExpenseTaxLine.expense_id == e.id, ExpenseTaxLine.tenant_id == e.tenant_id).order_by(ExpenseTaxLine.position, ExpenseTaxLine.id)))


def _part_of(r: ExpenseTaxLine) -> TaxPart:
    return TaxPart(
        rate=norm_rate(r.rate),
        base=q5(r.base),
        tax=q5(r.tax),
        acreditable=q5(r.credit),
        kind=r.kind,
        rate_code=r.rate_code,
        exonerated=q5(r.exonerated),
        prorrata=bool(r.prorrata),
        currency=r.currency,
        source=r.source,
    )


def stored_parts(db: Session, e: Expense) -> list[TaxPart]:
    """Lo guardado tal cual (sin revisar si todavia suma el gasto)."""
    return [_part_of(r) for r in _stored(db, e)] if e.id is not None else []


def consistent(e: Expense, parts: list[TaxPart]) -> bool:
    """Las partes suman exactamente el gasto (total, IVA y credito) y en su divisa."""
    if not parts:
        return False
    credit = expense_credit(e)
    cur = (e.currency or "CRC").upper()
    return (
        all(p.currency == cur for p in parts)
        and sum((p.total for p in parts), ZERO) == q5(e.total)
        and sum((p.tax for p in parts), ZERO) == q5(e.tax_amount)
        and sum((p.acreditable for p in parts), ZERO) == (q5(credit) if credit is not None else ZERO)
        and any(p.prorrata for p in parts) == (credit is None and any(p.is_iva for p in parts))
    )


def tax_breakdown(db: Session, expense: Expense) -> list[TaxPart]:
    """Partes del gasto por tarifa. Lo guardado si todavia suma el gasto; si no (o si no hay nada), lo calcula.
    No escribe: para guardar use sync()."""
    if expense.id is not None:
        parts = stored_parts(db, expense)
        if parts and consistent(expense, parts):
            return parts
    return compute(db, expense)


def sync(db: Session, expense: Expense) -> tuple[list[TaxPart], bool]:
    """Recalcula y guarda el desglose del gasto (sin commit). Idempotente: si lo guardado ya es lo calculado no toca
    nada. Devuelve (partes, cambio)."""
    if expense.id is None:
        db.flush()
    parts = compute(db, expense)
    old = stored_parts(db, expense)
    if old == parts:
        return parts, False
    db.execute(delete(ExpenseTaxLine).where(ExpenseTaxLine.expense_id == expense.id, ExpenseTaxLine.tenant_id == expense.tenant_id))
    for i, p in enumerate(parts):
        db.add(
            ExpenseTaxLine(
                tenant_id=expense.tenant_id,
                expense_id=expense.id,
                position=i,
                kind=p.kind,
                rate=p.rate,
                rate_code=p.rate_code,
                base=p.base,
                tax=p.tax,
                exonerated=p.exonerated,
                credit=p.acreditable,
                prorrata=p.prorrata,
                currency=p.currency,
                source=p.source,
            )
        )
    db.flush()
    return parts, True


def sync_quietly(db: Session, expense: Expense | None) -> None:
    """sync() que nunca tumba el flujo que la llama (recepcion): el desglose se puede recalcular con el relleno."""
    if expense is None:
        return
    try:
        with db.begin_nested():
            sync(db, expense)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("desglose de IVA del gasto %s", expense.id)


def summarize(parts: list[TaxPart]) -> dict:
    return {
        "base": sum((p.base for p in parts), ZERO),
        "tax": sum((p.tax for p in parts), ZERO),
        "acreditable": sum((p.acreditable for p in parts), ZERO),
        "no_acreditable": sum((p.no_acreditable for p in parts), ZERO),
        "total": sum((p.total for p in parts), ZERO),
    }


__all__ = ["RATE_ROLES", "TaxPart", "compute", "consistent", "credit_role", "document_parts", "expense_credit", "no_credit_role", "sync", "tax_breakdown"]
