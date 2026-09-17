from decimal import Decimal

from app.services.totals import LineIn, balance, compute_document, compute_line


def test_line_basic_iva13():
    lo = compute_line(LineIn(Decimal(2), Decimal("10000")))
    assert lo.subtotal == Decimal("20000.00000")
    assert lo.tax_amount == Decimal("2600.00000")
    assert lo.total == Decimal("22600.00000")


def test_line_discount_percent_and_amount():
    a = compute_line(LineIn(Decimal(1), Decimal(1000), "percent", Decimal(10)))
    b = compute_line(LineIn(Decimal(1), Decimal(1000), "amount", Decimal(100)))
    assert a.subtotal == b.subtotal == Decimal("900.00000")
    assert a.tax_amount == Decimal("117.00000")


def test_discount_never_exceeds_base():
    lo = compute_line(LineIn(Decimal(1), Decimal(500), "amount", Decimal(9999)))
    assert lo.subtotal == Decimal(0) and lo.total == Decimal(0)


def test_document_global_discount_prorated_by_rate():
    doc = compute_document(
        [LineIn(Decimal(1), Decimal(1000), tax_rate=Decimal(13)), LineIn(Decimal(1), Decimal(1000), tax_rate=Decimal(0))],
        "percent",
        10,
    )
    assert doc.subtotal == Decimal("2000.00000")
    assert doc.discount_total == Decimal("200.00000")
    # cada linea queda en 900 tras prorratear; solo la primera tributa 13%
    assert doc.tax_total == Decimal("117.00000")
    assert doc.total == Decimal("1917.00000")
    assert doc.taxable_by_rate == {"13": Decimal("900.00000"), "0": Decimal("900.00000")}


def test_rounding_half_up_5_decimals():
    lo = compute_line(LineIn(Decimal("3"), Decimal("33.33333"), tax_rate=Decimal(13)))
    assert lo.subtotal == Decimal("99.99999")
    assert lo.tax_amount == Decimal("13.00000")


def test_balance_rules():
    assert balance(1000, [("captura", 400)]) == Decimal("600.00000")
    assert balance(1000, [("captura", 1000), ("devolucion", 200)]) == Decimal("200.00000")
    assert balance(1000, [("autorizacion", 1000), ("void", 1000)]) == Decimal("1000.00000")


def test_empty_document():
    doc = compute_document([])
    assert doc.total == Decimal(0) and doc.lines == []
