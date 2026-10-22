"""Pruebas de integración: SupplierPayment en motor de asientos + formato de fecha configurable
+ columna Account con jerarquía QBO.

Cubre:
* Asiento simple de SupplierPayment CRC (DR cxp / CR banco).
* Asiento con aplicación a Expense de crédito.
* Anulación: asiento reverso.
* Diferencial cambiario USD con distinto TC entre gasto y pago.
* regenerate_all usa SupplierPayment (no el viejo path de gasto-crédito).
* GET /qb/config devuelve formato de fecha predeterminado.
* PUT /qb/config persiste el formato.
* Formato inválido → 422.
* CSV exportado usa el formato configurado (aaaa-mm-dd).
* Columna Account usa full_name con jerarquía "Padre:Hijo".
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models import (
    AccountingPeriod,
    AccountingRoleMapping,
    BankAccount,
    Customer,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    JournalEntry,
    QboAccount,
    Supplier,
    SupplierPayment,
    SupplierPaymentApplication,
    Tenant,
    TenantUser,
    User,
)
from app.services import journal as js
from tests.hacienda_utils import role_headers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOY = date.today()
MES = HOY.strftime("%Y-%m")
D = lambda x: Decimal(str(x))  # noqa: E731


def _tid(db_session) -> int:
    return db_session.query(Tenant).first().id


def _customer(db_session, tid: int) -> Customer:
    c = Customer(tenant_id=tid, name="Cliente Integ", id_number="3-0555-0001")
    db_session.add(c)
    db_session.flush()
    return c


def _bank(db_session, tid: int, currency: str = "CRC") -> BankAccount:
    b = BankAccount(tenant_id=tid, name="Banco Integ", currency=currency, active=True)
    db_session.add(b)
    db_session.flush()
    return b


def _cat(db_session, tid: int) -> ExpenseCategory:
    cat = ExpenseCategory(tenant_id=tid, name="Compras Integ", active=True)
    db_session.add(cat)
    db_session.flush()
    return cat


def _supplier(db_session, tid: int) -> Supplier:
    s = Supplier(tenant_id=tid, name="Proveedor Integ", tax_id="3-101-888001")
    db_session.add(s)
    db_session.flush()
    return s


def _expense_credito(db_session, tid: int, cat_id: int, amount: float = 11300,
                     currency: str = "CRC", fx_rate: float = 1) -> Expense:
    sub = amount / 1.13
    tax = amount - sub
    exp = Expense(
        tenant_id=tid, category_id=cat_id, description="Gasto crédito integ",
        date=HOY, currency=currency, subtotal=sub, tax_rate=13, tax_amount=tax,
        total=amount, iva_credit="credito", tax_credit_amount=tax,
        purchase_type="credito", bank_account_id=None, status="registrado", fx_rate=fx_rate,
    )
    db_session.add(exp)
    db_session.flush()
    return exp


def _sp(db_session, tid: int, sup_id: int, amount: float = 50000,
        currency: str = "CRC", fx_rate: float = 1, method: str = "transferencia",
        bank_id: int | None = None, status: str = "registrado",
        expense_ids: list[int] | None = None) -> SupplierPayment:
    sp = SupplierPayment(
        tenant_id=tid, supplier_id=sup_id, date=HOY,
        amount=amount, currency=currency, fx_rate=fx_rate,
        method=method, bank_account_id=bank_id, status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(sp)
    db_session.flush()
    for eid in (expense_ids or []):
        db_session.add(SupplierPaymentApplication(
            payment_id=sp.id, expense_id=eid, amount=amount,
        ))
    db_session.commit()
    return db_session.get(SupplierPayment, sp.id)


def _balanced(lines) -> bool:
    debe = sum(D(ln.amount) for ln in lines if ln.side == "debe")
    haber = sum(D(ln.amount) for ln in lines if ln.side == "haber")
    return abs(debe - haber) <= D("0.02")


def _setup_mapping(db_session, tid: int) -> QboAccount:
    acc = QboAccount(
        tenant_id=tid, full_name="Cuenta genérica integ",
        active=True, imported_at=datetime.now(timezone.utc),
    )
    db_session.add(acc)
    db_session.flush()
    from app.models import ACCOUNTING_ROLES, bank_role, expense_cat_role
    roles_to_map = [r for r, _ in ACCOUNTING_ROLES]
    # dinámicos de prueba
    banks = db_session.query(BankAccount).filter_by(tenant_id=tid).all()
    cats = db_session.query(ExpenseCategory).filter_by(tenant_id=tid).all()
    roles_to_map += [bank_role(b.id) for b in banks]
    roles_to_map += [expense_cat_role(c.id) for c in cats]
    for rk in roles_to_map:
        existing = db_session.query(AccountingRoleMapping).filter_by(
            tenant_id=tid, role_key=rk
        ).first()
        if existing:
            existing.qbo_account_id = acc.id
        else:
            db_session.add(AccountingRoleMapping(tenant_id=tid, role_key=rk, qbo_account_id=acc.id))
    db_session.commit()
    return acc


def _invoice(db_session, tid: int, cust_id: int, total: float = 113000) -> Invoice:
    sub = total / 1.13
    tax = total - sub
    inv = Invoice(
        tenant_id=tid, number=f"FEC-INTEG-{total:.0f}", customer_id=cust_id,
        currency="CRC", fx_sell=1, fx_buy=1, issue_date=HOY,
        subtotal=sub, discount_total=0, tax_total=tax, total=total,
        status="creado", einvoice_status="sin_emitir",
        sale_condition="01", payment_method="01", balance=total,
    )
    db_session.add(inv)
    db_session.flush()
    db_session.add(InvoiceLine(
        invoice_id=inv.id, name="Servicio integ", position=0,
        quantity=1, unit_price=sub, subtotal=sub, tax_rate=13,
        tax_code="01", tax_rate_code="08", tax_amount=tax, total=total,
    ))
    db_session.commit()
    return db_session.get(Invoice, inv.id)


# ---------------------------------------------------------------------------
# 17. SupplierPayment en el motor de asientos
# ---------------------------------------------------------------------------

def test_asiento_pago_proveedor_simple(db_session):
    """DR cxp_crc / CR banco sin aplicaciones."""
    tid = _tid(db_session)
    bank = _bank(db_session, tid)
    sup = _supplier(db_session, tid)
    sp = _sp(db_session, tid, sup.id, amount=50000, bank_id=bank.id)

    entry = js.build_supplier_payment_entry(db_session, sp)
    assert entry is not None, "entry es None"
    assert entry.source_type == "supplier_payment"
    assert entry.source_id == sp.id
    assert _balanced(entry.lines)
    roles = {ln.role_key: ln.side for ln in entry.lines if ln.role_key != "redondeo"}
    assert roles.get("cxp_proveedores_crc") == "debe"
    assert any(k.startswith("banco_") and v == "haber" for k, v in roles.items())


def test_asiento_pago_proveedor_con_aplicacion(db_session):
    """DR cxp con monto del gasto aplicado."""
    tid = _tid(db_session)
    bank = _bank(db_session, tid)
    cat = _cat(db_session, tid)
    sup = _supplier(db_session, tid)
    exp = _expense_credito(db_session, tid, cat.id, amount=11300)
    sp = _sp(db_session, tid, sup.id, amount=11300, bank_id=bank.id, expense_ids=[exp.id])

    entry = js.build_supplier_payment_entry(db_session, sp)
    assert entry is not None
    assert _balanced(entry.lines)
    roles = {ln.role_key: ln.side for ln in entry.lines if ln.role_key != "redondeo"}
    assert roles.get("cxp_proveedores_crc") == "debe"


def test_asiento_pago_proveedor_anulado(db_session):
    """Pago anulado → asiento reverso (cxp en haber, banco en debe)."""
    tid = _tid(db_session)
    bank = _bank(db_session, tid)
    sup = _supplier(db_session, tid)
    sp = _sp(db_session, tid, sup.id, amount=30000, bank_id=bank.id, status="anulado")
    sp.voided_at = datetime.now(timezone.utc)
    db_session.commit()

    entry = js.build_supplier_payment_entry(db_session, sp)
    assert entry is not None
    assert _balanced(entry.lines)
    roles = {ln.role_key: ln.side for ln in entry.lines if ln.role_key != "redondeo"}
    assert roles.get("cxp_proveedores_crc") == "haber", "reverso: cxp debe ser haber"
    assert any(k.startswith("banco_") and v == "debe" for k, v in roles.items()), "reverso: banco debe ser debe"


def test_asiento_pago_proveedor_usd_diferencial(db_session):
    """TC distinto entre gasto y pago → línea diferencial_cambiario."""
    tid = _tid(db_session)
    bank = _bank(db_session, tid, currency="USD")
    cat = _cat(db_session, tid)
    sup = _supplier(db_session, tid)
    exp = Expense(
        tenant_id=tid, category_id=cat.id, description="Gasto USD diferencial",
        date=HOY, currency="USD", subtotal=D("100"), tax_rate=13, tax_amount=D("13"),
        total=D("113"), iva_credit="credito", tax_credit_amount=D("13"),
        purchase_type="credito", bank_account_id=bank.id, status="registrado", fx_rate=500,
    )
    db_session.add(exp)
    db_session.flush()
    # Pago a TC 510: 10 CRC por dólar de diferencial
    sp = _sp(db_session, tid, sup.id, amount=113, currency="USD", fx_rate=510,
             bank_id=bank.id, expense_ids=[exp.id])

    entry = js.build_supplier_payment_entry(db_session, sp)
    assert entry is not None
    assert _balanced(entry.lines)
    role_keys = [ln.role_key for ln in entry.lines]
    assert "diferencial_cambiario" in role_keys, f"Sin diferencial. Roles: {role_keys}"


def test_regenerate_all_usa_supplier_payment_no_gasto(db_session):
    """regenerate_all genera asiento desde SupplierPayment, no desde gasto-crédito."""
    tid = _tid(db_session)
    bank = _bank(db_session, tid)
    cat = _cat(db_session, tid)
    sup = _supplier(db_session, tid)
    exp = _expense_credito(db_session, tid, cat.id)
    sp = _sp(db_session, tid, sup.id, amount=5000, bank_id=bank.id, expense_ids=[exp.id])

    js.regenerate_all(db_session, tid, MES)
    entries = db_session.query(JournalEntry).filter_by(
        tenant_id=tid, source_type="supplier_payment"
    ).all()
    # Exactamente 1 asiento de supplier_payment (del SupplierPayment real, no del gasto)
    assert len(entries) == 1
    assert entries[0].source_id == sp.id


# ---------------------------------------------------------------------------
# 18. Formato de fecha configurable
# ---------------------------------------------------------------------------

def test_config_fecha_default(client, auth):
    r = client.get("/qb/config", )
    assert r.status_code == 200
    j = r.json()
    assert j["date_format"] == "dd/mm/aaaa"
    assert set(["dd/mm/aaaa", "mm/dd/aaaa", "aaaa-mm-dd"]).issubset(set(j["date_format_options"]))


def test_config_fecha_guardar_y_leer(client, auth, db_session):
    r = client.put("/qb/config", json={"date_format": "mm/dd/aaaa"}, )
    assert r.status_code == 200
    r2 = client.get("/qb/config", )
    assert r2.json()["date_format"] == "mm/dd/aaaa"
    # Restaurar
    client.put("/qb/config", json={"date_format": "dd/mm/aaaa"}, )


def test_config_fecha_invalida_rechazada(client, auth):
    r = client.put("/qb/config", json={"date_format": "dd-mm-yyyy"}, )
    assert r.status_code == 422


def test_exportacion_usa_formato_iso(client, auth, db_session):
    """CSV exportado usa aaaa-mm-dd si así está configurado."""
    tid = _tid(db_session)
    cust = _customer(db_session, tid)
    _invoice(db_session, tid, cust.id, total=113000)
    _setup_mapping(db_session, tid)

    client.put("/qb/config", json={"date_format": "aaaa-mm-dd"}, )

    r = client.post(f"/qb/{MES}/export", json={}, )
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        csv_names = [n for n in z.namelist() if n.endswith(".csv") and "asientos" in n]
        assert csv_names, f"No hay CSV de asientos en el ZIP: {z.namelist()}"
        content = z.read(csv_names[0]).decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    assert rows, "CSV de asientos vacío"
    date_val = rows[0]["Journal Date"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_val), f"Formato ISO inesperado: {date_val!r}"

    # Restaurar
    client.put("/qb/config", json={"date_format": "dd/mm/aaaa"}, )


# ---------------------------------------------------------------------------
# 19. Columna Account usa full_name con jerarquía "Padre:Hijo"
# ---------------------------------------------------------------------------

def test_account_columna_jerarquia(client, auth, db_session):
    """La columna Account del CSV usa el full_name tal como está importado (Padre:Hijo)."""
    tid = _tid(db_session)
    cust = _customer(db_session, tid)
    _invoice(db_session, tid, cust.id, total=113000)
    _setup_mapping(db_session, tid)

    # Reemplazar la cuenta genérica del rol ventas_servicios por una con jerarquía
    acc_hier = QboAccount(
        tenant_id=tid, full_name="Ingresos:Ventas de servicios:Prueba",
        active=True, imported_at=datetime.now(timezone.utc),
    )
    db_session.add(acc_hier)
    db_session.flush()
    mapping_row = db_session.query(AccountingRoleMapping).filter_by(
        tenant_id=tid, role_key="ventas_servicios"
    ).first()
    if mapping_row:
        mapping_row.qbo_account_id = acc_hier.id
    else:
        db_session.add(AccountingRoleMapping(
            tenant_id=tid, role_key="ventas_servicios", qbo_account_id=acc_hier.id
        ))
    db_session.commit()
    # Expirar todo para que el endpoint vea los datos frescos (expire_on_commit=False no recarga relaciones)
    db_session.expire_all()

    r = client.post(f"/qb/{MES}/export", json={}, )
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        csv_names = [n for n in z.namelist() if n.endswith(".csv") and "asientos" in n]
        content = z.read(csv_names[0]).decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(content))
    accounts = [row["Account"] for row in reader]
    assert "Ingresos:Ventas de servicios:Prueba" in accounts, \
        f"No encontré la cuenta jerárquica. Cuentas en CSV: {accounts}"
