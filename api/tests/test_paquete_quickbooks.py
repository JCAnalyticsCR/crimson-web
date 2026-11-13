"""Paquete mensual para la contadora (QuickBooks Online por archivo, services/qb_package.py).

Cubre:
* Plan de cuentas: el CSV real de QBO en español («Nombre de la cuenta, Tipo de cuenta, Tipo de detalles, Moneda,
  Saldo de QuickBooks, Saldo bancario»), XLSX con filas de título, inglés y plurales.
* Flujo con verificación humana: un mes sin revisar no genera paquete; con errores no se marca revisado; generar
  cierra el mes y deja registro (quién, cuándo, qué salió).
* Contenido del ZIP: clientes, proveedores, facturas de venta y de proveedor en formato de importación, Excel de
  control, LEEME y respaldos XML.
* IVA por tarifa en el Excel y en las facturas de proveedor (con services/expense_tax y sin él).
* Paquete siguiente: no repite lo enviado, lo que cambió sale en «Ajustes» y lo nuevo de un mes con paquete se incluye.
* Aislamiento entre empresas.
"""

from __future__ import annotations

import csv
import io
import sys
import types
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

import openpyxl

from app.models import (
    AccountingExport,
    AccountingPeriod,
    AccountingRoleMapping,
    Customer,
    EInvoiceDocument,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    Payment,
    QboAccount,
    Supplier,
    Tenant,
    TenantUser,
    User,
)
from app.services import qb_package as qp
from tests.hacienda_utils import role_headers

MES = "2026-10"
DIA = date(2026, 10, 15)


def _tid(db) -> int:
    return db.query(Tenant).order_by(Tenant.id).first().id


def _customer(db, tid, name="Condominio Las Brisas", id_number="3-109-123456") -> Customer:
    c = Customer(tenant_id=tid, name=name, id_number=id_number, id_type="juridica", email="conta@brisas.cr", phone="2222-0000")
    db.add(c)
    db.flush()
    return c


def _supplier(db, tid, name="Distribuidora Eléctrica S.A.") -> Supplier:
    s = Supplier(tenant_id=tid, name=name, tax_id="3-101-654321", email="ventas@dist.cr")
    db.add(s)
    db.flush()
    return s


def _invoice(db, tid, cust_id, number="FE-000101", day=DIA, lines=((2, 50000, 13, "08"), (1, 10000, 4, "04"))) -> Invoice:
    sub = tax = Decimal(0)
    inv = Invoice(tenant_id=tid, number=number, customer_id=cust_id, currency="CRC", fx_sell=1, fx_buy=1, issue_date=day,
                  status="enviada", einvoice_status="aceptada", doc_type="FE", consecutive="00100001010000000101",
                  clave="5" * 50, sale_condition="01", payment_method="01")
    db.add(inv)
    db.flush()
    for i, (qty, price, rate, code) in enumerate(lines):
        st = Decimal(qty) * Decimal(price)
        tx = st * Decimal(rate) / 100
        sub += st
        tax += tx
        db.add(InvoiceLine(invoice_id=inv.id, position=i, name=f"Línea {i}", quantity=qty, unit_price=price, tax_rate=rate,
                           tax_rate_code=code, subtotal=st, tax_amount=tx, total=st + tx))
    inv.subtotal, inv.tax_total, inv.total, inv.balance = sub, tax, sub + tax, sub + tax
    db.flush()
    db.refresh(inv)
    return inv


def _category_mapped(db, tid, name="Materiales", account="Gastos:Materiales de instalación") -> ExpenseCategory:
    cat = ExpenseCategory(tenant_id=tid, name=name, active=True)
    db.add(cat)
    acc = QboAccount(tenant_id=tid, full_name=account, active=True)
    db.add(acc)
    db.flush()
    db.add(AccountingRoleMapping(tenant_id=tid, role_key=f"gasto_cat_{cat.id}", qbo_account_id=acc.id))
    db.flush()
    return cat


def _expense(db, tid, sup_id, cat_id, subtotal=100000, rate=13, credit=None, day=DIA, reference="FP-555", **kw) -> Expense:
    tax = Decimal(subtotal) * Decimal(rate) / 100
    e = Expense(tenant_id=tid, supplier_id=sup_id, category_id=cat_id, description="Cable UTP y conectores", date=day, currency="CRC",
                subtotal=subtotal, tax_rate=rate, tax_amount=tax, total=Decimal(subtotal) + tax, iva_credit="credito",
                tax_credit_amount=tax if credit is None else credit, reference=reference, status="registrado", purchase_type="credito", **kw)
    db.add(e)
    db.flush()
    return e


def _seed_month(db, tid):
    c = _customer(db, tid)
    s = _supplier(db, tid)
    cat = _category_mapped(db, tid)
    inv = _invoice(db, tid, c.id)
    e = _expense(db, tid, s.id, cat.id)
    db.add(Payment(tenant_id=tid, invoice_id=inv.id, method="sinpe", kind="captura", currency="CRC", amount=inv.total,
                   paid_at=date(2026, 10, 20), status="confirmado", external_ref="SINPE-778899"))
    db.commit()
    return c, s, cat, inv, e


def _zip(r) -> zipfile.ZipFile:
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _csv_rows(zf: zipfile.ZipFile, name: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8"))))


def _sheet(zf: zipfile.ZipFile, name: str, month: str = MES) -> list[tuple]:
    wb = openpyxl.load_workbook(io.BytesIO(zf.read(f"control_{month}.xlsx")), data_only=True)
    return list(wb[name].iter_rows(values_only=True))


def _review_and_generate(client, month=MES, **body):
    r = client.post(f"/qb/paquete/{month}/revisado")
    assert r.status_code == 200, r.text
    return client.post(f"/qb/paquete/{month}/generar", json=body)


# ---------------------------------------------------------------------------
# Plan de cuentas
# ---------------------------------------------------------------------------

BLANCA_CSV = (
    "Nombre de la cuenta,Tipo de cuenta,Tipo de detalles,Moneda,Saldo de QuickBooks,Saldo bancario\n"
    "Caja chica,Efectivo y equivalentes de efectivo,Caja chica,CRC,0.00,\n"
    "Banco BAC dólares,Bancos,Cuenta corriente,USD,0.00,0.00\n"
    "Gastos:Materiales de instalación,Gastos,Suministros y materiales,CRC,0.00,\n"
    "TOTAL,,,,0.00,\n"
)


def test_plan_de_cuentas_csv_de_blanca(client, auth, db_session):
    r = client.post("/qb/accounts/import", files={"file": ("Lista de cuentas.csv", BLANCA_CSV.encode("utf-8"), "text/csv")})
    assert r.status_code == 201, r.text
    assert r.json()["inserted"] == 3
    accs = {a["full_name"]: a for a in client.get("/qb/accounts").json()}
    assert set(accs) == {"Caja chica", "Banco BAC dólares", "Gastos:Materiales de instalación"}
    assert accs["Banco BAC dólares"]["currency"] == "USD"
    assert accs["Banco BAC dólares"]["account_type"] == "Bancos"
    acc = db_session.query(QboAccount).filter_by(full_name="Caja chica").one()
    assert acc.detail_type == "Caja chica"


def test_plan_de_cuentas_variantes():
    # punto y coma, Latin-1, sin tildes y en plural
    latin = "Nombres de las cuentas;Tipos de cuenta;Tipo de detalle;Monedas\nCaja general;Efectivo;Caja;Colón costarricense\n".encode("latin-1")
    rows = qp_parse(latin, "cuentas.csv")
    assert rows == [{"full_name": "Caja general", "account_type": "Efectivo", "detail_type": "Caja", "currency": "CRC"}]
    # inglés
    en = b"Account,Type,Detail Type,Currency,Balance\nPetty cash,Bank,Cash on hand,USD,0\n"
    assert qp_parse(en, "a.csv")[0]["currency"] == "USD"
    # XLSX con título, empresa y línea en blanco antes de la cabecera (como lo exporta QBO)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Lista de cuentas"])
    ws.append(["Grupo Empresarial Crimson"])
    ws.append([])
    ws.append(["Nombre de la cuenta", "Tipo de cuenta", "Tipo de detalles", "Moneda", "Saldo de QuickBooks", "Saldo bancario"])
    ws.append(["Banco Nacional", "Bancos", "Cuenta corriente", "CRC", 0, None])
    ws.append(["jueves, 12 de noviembre de 2026 10:00 a. m. GMT-06:00"])
    buf = io.BytesIO()
    wb.save(buf)
    rows = qp_parse(buf.getvalue(), "Lista de cuentas.xlsx")
    assert [r["full_name"] for r in rows] == ["Banco Nacional"]


def qp_parse(content: bytes, name: str) -> list[dict]:
    from app.routers.quickbooks import _parse_accounts

    return _parse_accounts(content, name)


# ---------------------------------------------------------------------------
# Flujo: revisado → paquete
# ---------------------------------------------------------------------------

def test_mes_no_revisado_bloquea_el_paquete(client, auth, db_session):
    _seed_month(db_session, _tid(db_session))
    r = client.post(f"/qb/paquete/{MES}/generar", json={})
    assert r.status_code == 409
    assert "revisado" in r.json()["detail"]
    assert db_session.query(AccountingExport).filter_by(kind="paquete").count() == 0


def test_errores_impiden_marcar_revisado(client, auth, db_session):
    tid = _tid(db_session)
    s = _supplier(db_session, tid)
    cat = ExpenseCategory(tenant_id=tid, name="Viáticos", active=True)  # sin cuenta de QBO
    db_session.add(cat)
    db_session.flush()
    _expense(db_session, tid, s.id, cat.id)
    db_session.commit()
    rev = client.get(f"/qb/paquete/{MES}").json()
    assert rev["can_mark_reviewed"] is False
    assert any(i["code"] == "category_unmapped" and "Viáticos" in i["message"] for i in rev["issues"])
    r = client.post(f"/qb/paquete/{MES}/revisado")
    assert r.status_code == 409 and "Viáticos" in r.json()["detail"]


def test_paquete_completo_y_registro(client, auth, db_session):
    tid = _tid(db_session)
    c, s, cat, inv, e = _seed_month(db_session, tid)
    db_session.add(EInvoiceDocument(tenant_id=tid, invoice_id=inv.id, doc_type="FE", consecutive=inv.consecutive, clave=inv.clave,
                                    provider="hacienda", status="aceptada", xml_document="<FacturaElectronica/>",
                                    xml_response="<MensajeHacienda/>"))
    db_session.commit()

    rev = client.get(f"/qb/paquete/{MES}").json()
    assert rev["counts"]["invoices"] == 1 and rev["counts"]["expenses"] == 1 and rev["counts"]["payments"] == 1
    assert rev["can_mark_reviewed"] is True and rev["can_generate"] is False

    # borrador del Excel antes de revisar
    r = client.get(f"/qb/paquete/{MES}/control")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert "BORRADOR" in str(wb["Resumen"]["B4"].value)

    zf = _zip(_review_and_generate(client))
    names = set(zf.namelist())
    assert {"1_clientes.csv", "2_proveedores.csv", "3_facturas_venta.csv", "4_facturas_proveedor.csv", f"control_{MES}.xlsx", "LEEME.txt"} <= names
    assert any(n.startswith("respaldos/ventas/") and n.endswith(".xml") for n in names)
    assert any(n.endswith("_respuesta_hacienda.xml") for n in names)

    clientes = _csv_rows(zf, "1_clientes.csv")
    assert clientes[0]["Name"] == "Condominio Las Brisas" and clientes[0]["Tax ID"] == "3-109-123456"
    assert _csv_rows(zf, "2_proveedores.csv")[0]["Name"] == "Distribuidora Eléctrica S.A."

    fv = _csv_rows(zf, "3_facturas_venta.csv")
    assert len(fv) == 2  # una fila por línea
    assert {r["Invoice no"] for r in fv} == {"FE-000101"}
    assert fv[0]["Invoice Date"] == "15/10/2026"  # dd/mm/aaaa por defecto
    assert (fv[0]["Item Amount"], fv[0]["Item Tax Code"], fv[0]["Item Tax Amount"]) == ("100000.00", "IVA 13%", "13000.00")
    assert (fv[1]["Item Tax Code"], fv[1]["Item Tax Amount"]) == ("IVA 4%", "400.00")
    assert fv[0]["Item Quantity"] == "2" and fv[0]["Item Rate"] == "50000.00"

    fp = _csv_rows(zf, "4_facturas_proveedor.csv")
    assert fp == [{**fp[0], "Bill no": "FP-555", "Supplier": "Distribuidora Eléctrica S.A.", "Account": "Gastos:Materiales de instalación",
                   "Line Amount": "100000.00", "Line Tax Code": "IVA 13%", "Line Tax Amount": "13000.00"}]

    cobros = _sheet(zf, "Cobros")
    assert cobros[1][0] == "20/10/2026" and cobros[1][4] == "SINPE Móvil" and cobros[1][5] == "SINPE-778899"
    d104 = {row[0] + "|" + str(row[1] or ""): row for row in _sheet(zf, "D-104 (referencial)")[1:] if row[0]}
    assert d104["Ventas|IVA 13%"][3] == 13000 and d104["Ventas|IVA 4%"][3] == 400
    assert d104["IVA débito (ventas - NC)|"][3] == 13400
    assert d104["IVA crédito (acreditable)|"][4] == 13000

    # registro: el mes queda cerrado, con quién y cuándo
    period = db_session.query(AccountingPeriod).filter_by(tenant_id=tid, month=MES).one()
    assert period.status == "exportado" and period.reviewed_by is not None and period.exported_by is not None
    ex = db_session.query(AccountingExport).filter_by(tenant_id=tid, kind="paquete").one()
    assert ex.entry_count == 3 and str(inv.id) in ex.manifest["invoice"]
    hist = client.get("/qb/exports").json()
    assert hist[0]["kind"] == "paquete" and hist[0]["exported_by"]

    # volver a generar un mes cerrado: admin con motivo, y arma el mismo paquete
    assert client.post(f"/qb/paquete/{MES}/generar", json={}).status_code == 422
    zf2 = _zip(client.post(f"/qb/paquete/{MES}/generar", json={"reexport_reason": "Se perdió el archivo"}))
    assert len(_csv_rows(zf2, "3_facturas_venta.csv")) == 2


def test_contabilidad_sin_admin_no_regenera_mes_cerrado(client, auth, db_session):
    tid = _tid(db_session)
    _seed_month(db_session, tid)
    _zip(_review_and_generate(client))
    h = role_headers(client, db_session, tid, "conta.paquete@example.com", "contabilidad")
    r = client.post(f"/qb/paquete/{MES}/generar", json={"reexport_reason": "otra vez"}, headers=h)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# IVA por tarifa
# ---------------------------------------------------------------------------

def test_iva_por_tarifa_con_expense_tax(client, auth, db_session, monkeypatch):
    """Con services/expense_tax: una línea por tarifa; la parte no acreditable va sumada al gasto."""
    tid = _tid(db_session)
    s = _supplier(db_session, tid)
    cat = _category_mapped(db_session, tid)
    e = _expense(db_session, tid, s.id, cat.id, subtotal=150000, credit=Decimal("13400"))
    e.tax_amount, e.total = Decimal("15400"), Decimal("165400")
    db_session.commit()

    fake = types.ModuleType("app.services.expense_tax")

    def tax_breakdown(db, expense):
        return [
            {"rate": Decimal("13"), "base": Decimal("100000"), "tax": Decimal("13000"), "acreditable": True},
            {"rate": Decimal("4"), "base": Decimal("50000"), "tax": Decimal("2000"), "acreditable": Decimal("400")},
            {"rate": Decimal("0"), "base": Decimal("0"), "tax": Decimal("0"), "acreditable": False, "rate_code": "10"},
        ]

    fake.tax_breakdown = tax_breakdown
    monkeypatch.setitem(sys.modules, "app.services.expense_tax", fake)

    zf = _zip(_review_and_generate(client))
    gastos = _sheet(zf, "Gastos")
    head = gastos[0]
    rows = [dict(zip(head, r, strict=False)) for r in gastos[1:]]
    by_rate = {r["Tarifa"]: r for r in rows}
    assert set(by_rate) == {"IVA 13%", "IVA 4%", "Exento"}
    assert by_rate["IVA 13%"]["IVA acreditable"] == 13000 and by_rate["IVA 13%"]["IVA no acreditable"] == 0
    assert by_rate["IVA 4%"]["IVA"] == 2000 and by_rate["IVA 4%"]["IVA acreditable"] == 400 and by_rate["IVA 4%"]["IVA no acreditable"] == 1600

    fp = _csv_rows(zf, "4_facturas_proveedor.csv")
    lines = [(r["Line Amount"], r["Line Tax Code"], r["Line Tax Amount"]) for r in fp]
    # 13 %: todo acreditable; 4 %: 20 % acreditable (base 10 000 con IVA 400) y el resto (40 000 + 1 600) sin IVA
    assert ("100000.00", "IVA 13%", "13000.00") in lines
    assert ("10000.00", "IVA 4%", "400.00") in lines
    assert ("41600.00", qp.NON_CREDITABLE_CODE, "0.00") in lines
    total = sum(Decimal(a) + Decimal(t) for a, _c, t in lines)
    assert total == Decimal("165400.00")  # cuadra con el total del gasto

    d104 = {row[0] + "|" + str(row[1] or ""): row for row in _sheet(zf, "D-104 (referencial)")[1:] if row[0]}
    assert d104["Compras y gastos|IVA 4%"][4] == 400 and d104["Compras y gastos|IVA 4%"][5] == 1600


def test_iva_sin_expense_tax_usa_el_gasto(db_session, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.expense_tax", None)  # el módulo del otro agente no está
    tid = _tid(db_session)
    s = _supplier(db_session, tid)
    cat = _category_mapped(db_session, tid)
    e = _expense(db_session, tid, s.id, cat.id, subtotal=20000, rate=2, credit=Decimal(0))
    parts = qp.expense_parts(db_session, e)
    assert [(p.key, p.base, p.tax, p.creditable) for p in parts] == [("2", Decimal(20000), Decimal(400), Decimal(0))]
    rows = qp.bill_rows(db_session, e, "%d/%m/%Y", {f"gasto_cat_{cat.id}": "Gastos:Materiales de instalación"})
    assert [(r["Line Amount"], r["Line Tax Code"]) for r in rows] == [("20400.00", qp.NON_CREDITABLE_CODE)]


# ---------------------------------------------------------------------------
# Paquete siguiente: ajustes y nada repetido
# ---------------------------------------------------------------------------

def test_paquete_siguiente_ajustes_y_documentos_tardios(client, auth, db_session):
    tid = _tid(db_session)
    c, s, cat, inv, e = _seed_month(db_session, tid)
    _zip(_review_and_generate(client))

    # después del paquete de octubre: cambia la factura, se anula el gasto y entra una factura con fecha de octubre
    ln = inv.lines[0]
    ln.subtotal, ln.tax_amount = Decimal(90000), Decimal(11700)
    inv.total = Decimal(102100)
    e.status = "anulado"
    tardia = _invoice(db_session, tid, c.id, number="FE-000102", day=date(2026, 10, 30), lines=((1, 5000, 13, "08"),))
    nov = _invoice(db_session, tid, c.id, number="FE-000150", day=date(2026, 11, 3), lines=((1, 7000, 13, "08"),))
    db_session.commit()

    rev = client.get("/qb/paquete/2026-11").json()
    assert rev["counts"]["invoices"] == 2 and rev["counts"]["late"] == 1 and rev["counts"]["changes"] == 2
    assert rev["counts"]["customers_new"] == 0  # el cliente ya salió en octubre

    zf = _zip(_review_and_generate(client, "2026-11"))
    fv = _csv_rows(zf, "3_facturas_venta.csv")
    assert {r["Invoice no"] for r in fv} == {tardia.number, nov.number}  # FE-000101 no se repite
    assert "1_clientes.csv" not in zf.namelist() and "4_facturas_proveedor.csv" not in zf.namelist()
    ajustes = _sheet(zf, "Ajustes", "2026-11")
    motivos = {r[0]: r[2] for r in ajustes[1:]}
    assert motivos["Factura FE-000101"] == "Cambió después del paquete"
    assert any(k.startswith(f"Gasto #{e.id}") and v.startswith("Anulado") for k, v in motivos.items())
    assert any("De un mes anterior" in str(r[-1]) for r in _sheet(zf, "Ventas", "2026-11")[1:])

    # diciembre: los ajustes ya informados no se repiten
    rev = client.get("/qb/paquete/2026-12").json()
    assert rev["counts"]["changes"] == 0 and rev["counts"]["invoices"] == 0


def test_cambio_de_contacto_sale_para_actualizar(client, auth, db_session):
    tid = _tid(db_session)
    c, *_ = _seed_month(db_session, tid)
    _zip(_review_and_generate(client))
    c.email = "nuevo@brisas.cr"
    _invoice(db_session, tid, c.id, number="FE-000200", day=date(2026, 11, 5), lines=((1, 1000, 13, "08"),))
    db_session.commit()
    zf = _zip(_review_and_generate(client, "2026-11"))
    assert "1_clientes.csv" not in zf.namelist()
    assert _csv_rows(zf, "1_clientes_actualizar.csv")[0]["Email"] == "nuevo@brisas.cr"


# ---------------------------------------------------------------------------
# Aislamiento y límites
# ---------------------------------------------------------------------------

def test_aislamiento_entre_empresas(client, auth, db_session):
    from app.core.security import hash_password

    tid = _tid(db_session)
    _seed_month(db_session, tid)
    t2 = Tenant(slug="empresa2", name="Empresa 2", plan="pro", default_currency="CRC", tax_id="3-102-999999", settings={})
    db_session.add(t2)
    db_session.flush()
    c2 = _customer(db_session, t2.id, name="Cliente de la otra empresa", id_number="1-1111-1111")
    _invoice(db_session, t2.id, c2.id, number="OTRA-0001")
    pw = "Empresa2-seguro-2026"
    u2 = User(email="admin2@empresa2.com", full_name="Admin 2", password_hash=hash_password(pw))
    db_session.add(u2)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=t2.id, user_id=u2.id, role_code="admin"))
    db_session.commit()

    zf = _zip(_review_and_generate(client))
    fv = _csv_rows(zf, "3_facturas_venta.csv")
    assert {r["Invoice no"] for r in fv} == {"FE-000101"}
    assert all(r["Name"] != "Cliente de la otra empresa" for r in _csv_rows(zf, "1_clientes.csv"))

    tok = client.post("/auth/login", json={"email": "admin2@empresa2.com", "password": pw}).json()["access_token"]
    h2 = {"Authorization": f"Bearer {tok}"}
    rev2 = client.get(f"/qb/paquete/{MES}", headers=h2).json()
    assert rev2["counts"]["invoices"] == 1 and rev2["status"] == "abierto"  # el cierre de la empresa 1 no la toca
    assert all(e["kind"] != "paquete" for e in client.get("/qb/exports", headers=h2).json())


def test_archivos_parten_en_100_facturas_y_1000_filas():
    docs = [[{"n": i}] for i in range(150)]
    files = qp._chunks(docs)
    assert [len(f) for f in files] == [100, 50]
    big = [[{"n": i}] * 400 for i in range(3)]  # 3 facturas de 400 filas: nunca se parte una factura
    assert [len(f) for f in qp._chunks(big)] == [800, 400]


def test_vendedor_no_ve_el_paquete(client, auth, db_session):
    h = role_headers(client, db_session, _tid(db_session), "vende.paquete@example.com", "ventas")
    assert client.get(f"/qb/paquete/{MES}", headers=h).status_code == 403
    assert client.post(f"/qb/paquete/{MES}/revisado", headers=h).status_code == 403


def test_quitar_revisado(client, auth, db_session):
    _seed_month(db_session, _tid(db_session))
    assert client.post(f"/qb/paquete/{MES}/revisado").status_code == 200
    assert client.get(f"/qb/paquete/{MES}").json()["can_generate"] is True
    assert client.post(f"/qb/paquete/{MES}/quitar-revisado").json()["status"] == "abierto"
    assert client.post(f"/qb/paquete/{MES}/generar", json={}).status_code == 409


def test_respaldo_pdf_de_compra(client, auth, db_session):
    """El PDF y el XML del comprobante recibido viajan en respaldos/compras."""
    import hashlib
    import secrets

    from app.models import Media, ReceivedDocument

    tid = _tid(db_session)
    _c, _s, _cat, _inv, e = _seed_month(db_session, tid)
    pdf = b"%PDF-1.4 prueba"
    m = Media(tenant_id=tid, key=secrets.token_hex(8), filename="factura.pdf", content_type="application/pdf", size=len(pdf),
              sha256=hashlib.sha256(pdf).hexdigest(), data=pdf, visibility="private", module="accounting")
    db_session.add(m)
    db_session.flush()
    db_session.add(ReceivedDocument(tenant_id=tid, clave="6" * 50, consecutive="00100001010000000999", expense_id=e.id,
                                    xml_document="<FacturaElectronica/>", pdf_media_id=m.id,
                                    created_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
    db_session.commit()
    zf = _zip(_review_and_generate(client))
    compras = [n for n in zf.namelist() if n.startswith("respaldos/compras/")]
    assert any(n.endswith(".pdf") for n in compras) and any(n.endswith(".xml") for n in compras)
    assert zf.read(next(n for n in compras if n.endswith(".pdf"))) == pdf


def _xlsx_dimension_a1(rows) -> bytes:
    """Excel como los que generan algunos sistemas de informes (QuickBooks): la hoja declara medir solo «A1»."""
    import re

    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            b = zin.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet"):
                b = re.sub(rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1"/>', b)
            zout.writestr(item, b)
    return out.getvalue()


def test_plan_de_cuentas_excel_con_dimension_mal_declarada(client, auth, db_session):
    """El reporte de Andrés (13/11): el Excel exportado decía medir solo A1 y el importador leía una celda."""
    data = _xlsx_dimension_a1(
        [("", "Grupo Empresarial Crimson"), ("", "Lista de cuentas"), ("", "")]
        + [("", "Cuenta", "Tipo", "Tipo de detalle", "Descripción", "Saldo total")]
        + [("", "Bac Colones", "Efectivo y equivalentes de efectivo", "Banco", "", 0),
           ("", "Cuenta por Pagar CRC", "Cuentas por pagar (C/P)", "Cuentas por pagar (C/P)", "", 0)]
    )
    r = client.post("/qb/accounts/import", files={"file": ("Lista de cuentas.xlsx", data, "application/octet-stream")})
    assert r.status_code == 201, r.text
    assert r.json()["inserted"] == 2


def test_plan_de_cuentas_error_dice_que_columnas_trae(client, auth, db_session):
    r = client.post("/qb/accounts/import", files={"file": ("otro.csv", b"Fecha,Monto,Detalle\n01/11/2026,100,x\n", "text/csv")})
    assert r.status_code == 422 and "«Fecha», «Monto», «Detalle»" in r.json()["detail"]
    r = client.post("/qb/accounts/import", files={"file": ("viejo.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "application/vnd.ms-excel")})
    assert r.status_code == 422 and ".xls" in r.json()["detail"]
