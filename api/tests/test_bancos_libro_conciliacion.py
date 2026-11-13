"""Bancos (reunion con la contadora, 10/11/2026): saldo inicial a la fecha de corte, libro con saldo corrido (cobros,
gastos pagados, pagos a proveedor, planilla por su neto, movimientos propios), importacion del estado de cuenta en
varios formatos con vista previa, conciliacion automatica y manual, resumen banco vs Crimson, aislamiento entre
empresas y topes del archivo. Datos ficticios."""

import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal

from tests.hacienda_utils import role_headers

CORTE = "2026-11-01"
D = Decimal


def _tid(auth) -> int:
    return auth["tenant"]["id"]


def _cuenta(client, name="BAC colones", currency="CRC", opening="1000000.00", kind="banco", headers=None) -> dict:
    h = headers or {}
    acc = client.post("/banking/accounts", headers=h, json={"name": name, "bank": "BAC", "currency": currency})
    assert acc.status_code == 201, acc.text
    r = client.put(f"/bank-books/accounts/{acc.json()['id']}/opening", headers=h, json={"opening_balance": opening, "opening_date": CORTE, "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def _factura(db, tid, number):
    from app.models import Invoice

    inv = Invoice(tenant_id=tid, number=number, issue_date=date(2026, 11, 1))
    db.add(inv)
    db.flush()
    return inv


def _cobro(db, tid, inv, amount, day, bank_id=None, ref=None, kind="captura", currency="CRC", method="transferencia"):
    from app.models import Payment

    p = Payment(
        tenant_id=tid, invoice_id=inv.id, method=method, kind=kind, currency=currency, amount=amount, bank_account_id=bank_id, external_ref=ref, paid_at=day
    )
    db.add(p)
    db.flush()
    return p


def _gasto(db, tid, total, day, bank_id=None, status="pagado", ref=None, purchase_type="contado", supplier_id=None, method="transferencia"):
    from app.models import Expense

    e = Expense(
        tenant_id=tid,
        description=f"Gasto {total}",
        date=day,
        currency="CRC",
        subtotal=total,
        tax_rate=0,
        tax_amount=0,
        total=total,
        bank_account_id=bank_id,
        status=status,
        reference=ref,
        purchase_type=purchase_type,
        supplier_id=supplier_id,
        payment_method=method,
    )
    db.add(e)
    db.flush()
    return e


def _escenario(db, tid, bank_id):
    """Movimientos de Crimson en la cuenta (y algunos que NO deben contar)."""
    from app.models import BankMovement, PayrollRun, Supplier, SupplierPayment, SupplierPaymentApplication

    inv = _factura(db, tid, "FEC-BANCO-1")
    _cobro(db, tid, inv, 50000, date(2026, 10, 30), bank_id)  # antes del corte: ya esta en el saldo inicial
    pago = _cobro(db, tid, inv, 113000, date(2026, 11, 3), bank_id, ref="88776655")
    _cobro(db, tid, inv, 13000, date(2026, 11, 4), bank_id, kind="devolucion")
    _cobro(db, tid, inv, 100, date(2026, 11, 4), bank_id, currency="USD")  # otra divisa: aviso, no suma
    sin_cuenta = _cobro(db, tid, inv, 25000, date(2026, 11, 6))  # sin cuenta: solo para casar a mano
    gasto = _gasto(db, tid, 45200, date(2026, 11, 5), bank_id, ref="FAC-77")
    _gasto(db, tid, 9999, date(2026, 11, 5), bank_id, status="registrado")  # no pagado
    sup = Supplier(tenant_id=tid, name="Proveedor Ficticio S.A.")
    db.add(sup)
    db.flush()
    credito = _gasto(db, tid, 30000, date(2026, 11, 2), bank_id, purchase_type="credito", supplier_id=sup.id)  # lo paga el pago a proveedor
    sp = SupplierPayment(tenant_id=tid, supplier_id=sup.id, date=date(2026, 11, 7), amount=30000, currency="CRC", method="transferencia", bank_account_id=bank_id, reference="TRF-5544")
    db.add(sp)
    db.flush()
    db.add(SupplierPaymentApplication(payment_id=sp.id, expense_id=credito.id, amount=30000))
    planilla = _gasto(db, tid, 600000, date(2026, 11, 8), bank_id, ref="PLANILLA-1")
    db.add(
        PayrollRun(
            tenant_id=tid, period_start=date(2026, 11, 1), period_end=date(2026, 11, 15), status="pagada", gross=550000, net=480000, ccss_employer=50000, expense_id=planilla.id
        )
    )
    db.add(BankMovement(tenant_id=tid, bank_account_id=bank_id, date=date(2026, 11, 9), amount=D("1250.50"), kind="interes", description="Intereses", status="registrado"))
    db.commit()
    return {"pago": pago, "gasto": gasto, "sp": sp, "sin_cuenta": sin_cuenta, "planilla": planilla, "inv": inv}


# ------------------------------------------------------------------ libro y saldo corrido


def test_saldo_inicial_y_libro_con_saldo_corrido(client, auth, db_session):
    acc = _cuenta(client)
    assert acc["opening_date"] == CORTE and D(acc["opening_balance"]) == D("1000000.00") and acc["kind"] == "banco"
    _escenario(db_session, _tid(auth), acc["id"])
    r = client.get(f"/bank-books/accounts/{acc['id']}/ledger")
    assert r.status_code == 200, r.text
    lg = r.json()
    by_type = [(e["date"], D(e["amount"])) for e in lg["entries"]]
    assert by_type == [
        ("2026-11-03", D("113000.00")),
        ("2026-11-04", D("-13000.00")),  # devolucion resta
        ("2026-11-05", D("-45200.00")),
        ("2026-11-07", D("-30000.00")),  # el pago a proveedor; el gasto de credito que cubre no sale dos veces
        ("2026-11-08", D("-480000.00")),  # planilla: el NETO que sale del banco, no el costo de 600 000
        ("2026-11-09", D("1250.50")),
    ]
    corridos = [D(e["balance"]) for e in lg["entries"]]
    assert corridos == [D("1113000.00"), D("1100000.00"), D("1054800.00"), D("1024800.00"), D("544800.00"), D("546050.50")]
    assert D(lg["closing_balance"]) == D("546050.50")
    assert lg["entries"][0]["counterparty"] is None and lg["entries"][0]["document"] == "FEC-BANCO-1"
    assert lg["entries"][4]["label"] == "Planilla (neto pagado)"
    assert any("otra divisa" in w for w in lg["warnings"])
    # rango: saldo al inicio del rango y solo los movimientos del rango
    r = client.get(f"/bank-books/accounts/{acc['id']}/ledger", params={"date_from": "2026-11-05", "date_to": "2026-11-07"})
    lg = r.json()
    assert D(lg["period_start_balance"]) == D("1100000.00") and len(lg["entries"]) == 2 and D(lg["closing_balance"]) == D("1024800.00")
    # tarjeta de la cuenta
    a = next(x for x in client.get("/bank-books/accounts").json() if x["id"] == acc["id"])
    assert D(a["book_balance"]) == D("546050.50") and a["bank_balance"] is None and a["unmatched_entries"] == 6


def test_fecha_real_del_pago_del_gasto_si_existe(client, auth, db_session):
    """Otro agente agrega Expense.paid_date: si el gasto la trae, el libro usa esa fecha (y sin la columna, Expense.date)."""
    acc = _cuenta(client)
    g = _gasto(db_session, _tid(auth), 10000, date(2026, 11, 3), acc["id"])
    db_session.commit()
    assert [e["date"] for e in client.get(f"/bank-books/accounts/{acc['id']}/ledger").json()["entries"]] == ["2026-11-03"]
    g.paid_date = date(2026, 11, 6)  # la instancia de la sesion de pruebas: simula la columna nueva
    assert [e["date"] for e in client.get(f"/bank-books/accounts/{acc['id']}/ledger").json()["entries"]] == ["2026-11-06"]


# ------------------------------------------------------------------ importacion: formatos


CSV_BCR = """Banco de Costa Rica;;;;;
Estado de cuenta corriente;;;;;
Fecha;Número de documento;Descripción;Débitos;Créditos;Saldo
30/10/2026;555;DEPOSITO VIEJO;;50.000,00;1.000.000,00
03/11/2026;88776655;SINPE MOVIL CLIENTE;;113.000,00;1.113.000,00
04/11/2026;1001;DEVOLUCION CLIENTE;13.000,00;;1.100.000,00
06/11/2026;FAC-77;PAGO PROVEEDOR;45.200,00;;1.054.800,00
06/11/2026;3321;TRANSFERENCIA RECIBIDA;;25.000,00;1.079.800,00
08/11/2026;777;PLANILLA NETO;480.000,00;;599.800,00
09/11/2026;;COMISION SINPE;2.500,00;;597.300,00
10/11/2026;TRF-5544;TRANSF PROVEEDOR;30.000,00;;567.300,00
;;Total;570.700,00;188.000,00;
"""

CSV_SIGNO = """Fecha,Concepto,Referencia,Monto,Saldo
13/11/2026,DEPOSITO EFECTIVO,9001,"150,000.00","717,300.00"
14/11/2026,CARGO TARJETA,9002,-12500.75,"704,799.25"
"""


def _up(client, aid, content: bytes, name: str, mapping=None, preview=True, headers=None):
    path = f"/bank-books/accounts/{aid}/statements" + ("/preview" if preview else "")
    data = {"mapping": json.dumps(mapping)} if mapping is not None else {}
    return client.post(path, headers=headers or {}, files={"file": (name, content, "text/csv")}, data=data)


def test_importar_debito_credito_separados_con_vista_previa(client, auth, db_session):
    from app.models import BankStatementLine

    acc = _cuenta(client)
    r = _up(client, acc["id"], CSV_BCR.encode("cp1252"), "bcr.csv")
    assert r.status_code == 200, r.text
    pv = r.json()
    m = pv["mapping"]
    cols = {c["index"]: c["header"] for c in pv["columns"]}
    assert cols[m["date"]] == "Fecha" and cols[m["debit"]] == "Débitos" and cols[m["credit"]] == "Créditos" and cols[m["balance"]] == "Saldo"
    assert cols[m["reference"]] == "Número de documento" and m["amount"] is None and m["date_order"] == "dmy"
    assert pv["total"] == 8 and pv["before_cutoff"] == 1 and pv["new"] == 7 and pv["errors"] == []
    assert D(pv["credits"]) == D("138000.00") and D(pv["debits"]) == D("-570700.00")
    assert D(pv["closing_balance"]) == D("567300.00")
    assert pv["continuity"] == {"date": "2026-11-03", "file": "1000000.00", "crimson": "1000000.00", "ok": True}
    assert pv["rows"][2]["amount"] == "-13000.00" and pv["rows"][1]["reference"] == "88776655"
    assert db_session.query(BankStatementLine).count() == 0  # la vista previa no guarda
    r = _up(client, acc["id"], CSV_BCR.encode("cp1252"), "bcr.csv", mapping=m, preview=False)
    assert r.status_code == 201, r.text
    assert r.json()["added"] == 7 and r.json()["before_cutoff"] == 1
    # otra vez el mismo archivo: nada nuevo
    pv2 = _up(client, acc["id"], CSV_BCR.encode("cp1252"), "bcr.csv").json()
    assert pv2["new"] == 0 and pv2["duplicates"] == 7
    assert _up(client, acc["id"], CSV_BCR.encode("cp1252"), "bcr.csv", preview=False).json()["added"] == 0
    a = next(x for x in client.get("/bank-books/accounts").json() if x["id"] == acc["id"])
    assert D(a["bank_balance"]) == D("567300.00") == D(a["bank_reported_balance"]) and a["last_statement_date"] == "2026-11-10"
    imps = client.get(f"/bank-books/accounts/{acc['id']}/statements").json()
    assert len(imps) == 2 and imps[-1]["added"] == 7 and D(imps[-1]["closing_balance"]) == D("567300.00")


def test_importar_monto_con_signo_y_mapeo_a_mano(client, auth, db_session):
    acc = _cuenta(client, opening="567300.00")
    pv = _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv").json()
    m = pv["mapping"]
    assert m["amount"] == 3 and m["debit"] is None and m["description"] == 1 and m["reference"] == 2
    assert [r["amount"] for r in pv["rows"]] == ["150000.00", "-12500.75"]
    assert [r["date"] for r in pv["rows"]] == ["2026-11-13", "2026-11-14"]  # 13/11: dd/mm sin dudas
    # la persona cambia el mapeo: sin referencia, la descripcion sale de la columna 2
    m2 = {**m, "reference": None, "description": 2}
    pv2 = _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv", mapping=m2).json()
    assert pv2["rows"][0]["description"] == "9001" and pv2["rows"][0]["reference"] is None
    # mapeos invalidos
    assert _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv", mapping={**m, "reference": 3}).status_code == 422  # columna repetida
    assert _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv", mapping={**m, "date": 99}).status_code == 422
    assert _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv", mapping={**m, "amount": None}).status_code == 422
    r = client.post(f"/bank-books/accounts/{acc['id']}/statements/preview", files={"file": ("x.csv", CSV_SIGNO.encode(), "text/csv")}, data={"mapping": "{no-json"})
    assert r.status_code == 422
    r = _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv", mapping=m, preview=False)
    assert r.status_code == 201 and r.json()["added"] == 2
    a = next(x for x in client.get("/bank-books/accounts").json() if x["id"] == acc["id"])
    assert D(a["bank_balance"]) == D("704799.25") == D(a["bank_reported_balance"])


def test_importar_xlsx_con_fechas_de_excel_y_columna_tipo(client, auth):
    from openpyxl import Workbook

    acc = _cuenta(client, currency="USD", name="BN dólares", opening="5000")
    wb = Workbook()
    ws = wb.active
    ws.append(["Banco Nacional"])
    ws.append(["Fecha de movimiento", "Comprobante", "Detalle", "Tipo", "Monto", "Saldo disponible"])
    ws.append([datetime(2026, 11, 2), "A1", "Depósito cliente", "C", 1500.5, 6500.5])
    ws.append([datetime(2026, 11, 3), "A2", "Pago servicio", "D", 200, 6300.5])
    ws.append(["02-nov-2026", "A3", "Abono", "Crédito", "10", "6310.50"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"/bank-books/accounts/{acc['id']}/statements/preview", files={"file": ("bn.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["mapping"]["type"] == 3 and pv["mapping"]["amount"] == 4
    assert [(x["date"], x["amount"]) for x in pv["rows"]] == [("2026-11-02", "1500.50"), ("2026-11-03", "-200.00"), ("2026-11-02", "10.00")]


def test_fechas_mm_dd_y_errores_por_fila(client, auth):
    acc = _cuenta(client)
    csv = "Fecha,Descripcion,Monto\n11/13/2026,Deposito,100\n11/14/2026,Retiro,-50\nmañana,Raro,20\n"
    pv = _up(client, acc["id"], csv.encode(), "us.csv").json()
    assert pv["mapping"]["date_order"] == "mdy" and [x["date"] for x in pv["rows"]] == ["2026-11-13", "2026-11-14"]
    assert pv["errors"] == [{"row": 4, "error": "Fecha que no se entiende: «mañana»"}]
    r = _up(client, acc["id"], csv.encode(), "us.csv", preview=False)
    assert r.status_code == 422 and "errores" in r.json()["detail"]  # con errores no se guarda nada


# ------------------------------------------------------------------ conciliacion


def test_conciliacion_automatica_manual_y_resumen(client, auth, db_session):
    from app.models import BankStatementLine, Payment

    tid = _tid(auth)
    acc = _cuenta(client)
    esc = _escenario(db_session, tid, acc["id"])
    # dos cobros iguales el mismo dia sin referencia: ambiguo, no se casa solo
    _cobro(db_session, tid, esc["inv"], 7000, date(2026, 11, 11), acc["id"])
    _cobro(db_session, tid, esc["inv"], 7000, date(2026, 11, 11), acc["id"])
    db_session.commit()
    csv = CSV_BCR.replace(";;Total", "11/11/2026;;DEPOSITO;;7.000,00;574.300,00\n15/11/2026;;SINPE TARDIO;;113.000,00;687.300,00\n;;Total")
    r = _up(client, acc["id"], csv.encode("cp1252"), "bcr.csv", preview=False)
    assert r.status_code == 201, r.text
    assert r.json()["matched"] == 5  # cobro (ref), devolucion, gasto (+1 dia), planilla, pago a proveedor (+3 dias)
    lines = {ln["description"]: ln for ln in client.get(f"/bank-books/accounts/{acc['id']}/lines").json()}
    assert lines["SINPE MOVIL CLIENTE"]["match"]["id"] == esc["pago"].id and lines["SINPE MOVIL CLIENTE"]["matched_by"] == "auto"
    assert lines["TRANSF PROVEEDOR"]["matched_type"] == "supplier_payment" and lines["PLANILLA NETO"]["match"]["id"] == esc["planilla"].id
    for d_ in ("DEPOSITO", "SINPE TARDIO", "COMISION SINPE", "TRANSFERENCIA RECIBIDA"):
        assert lines[d_]["status"] == "pendiente", d_

    rec = client.get(f"/bank-books/accounts/{acc['id']}/reconciliation").json()
    assert rec["as_of"] == "2026-11-15"
    assert D(rec["bank_balance"]) == D("687300.00") == D(rec["bank_reported_balance"])
    assert D(rec["book_balance"]) == D("560050.50")  # 546 050,50 + 2 x 7 000
    assert rec["pending_bank"]["count"] == 4 and D(rec["pending_bank"]["total"]) == D("142500.00")
    assert rec["pending_book"]["count"] == 3 and D(rec["pending_book"]["total"]) == D("15250.50")  # 2 cobros + intereses
    assert D(rec["unexplained"]) == D("0.00") and rec["reconciled"] is False

    # a mano: el deposito de 7 000 con uno de los dos cobros
    dep = lines["DEPOSITO"]
    cands = client.get(f"/bank-books/lines/{dep['id']}/candidates").json()
    assert [c["exact"] for c in cands[:2]] == [True, True]
    assert client.post(f"/bank-books/lines/{dep['id']}/match", json={"type": "payment", "id": cands[0]["id"]}).status_code == 200
    # el mismo cobro no se casa dos veces; ni un ingreso con una salida
    assert client.post(f"/bank-books/lines/{lines['SINPE TARDIO']['id']}/match", json={"type": "payment", "id": cands[0]["id"]}).status_code == 409
    assert client.post(f"/bank-books/lines/{lines['SINPE TARDIO']['id']}/match", json={"type": "expense", "id": esc["gasto"].id}).status_code == 422
    assert client.post(f"/bank-books/lines/{lines['COMISION SINPE']['id']}/match", json={"type": "payment", "id": cands[1]["id"]}).status_code == 422
    # el cobro sin cuenta aparece como sugerencia y al casarlo queda asignado a esta cuenta
    trf = lines["TRANSFERENCIA RECIBIDA"]
    cands = client.get(f"/bank-books/lines/{trf['id']}/candidates").json()
    assert cands[0]["id"] == esc["sin_cuenta"].id and cands[0]["unassigned"] and cands[0]["exact"]
    r = client.post(f"/bank-books/lines/{trf['id']}/match", json={"type": "payment", "id": esc["sin_cuenta"].id})
    assert r.status_code == 200 and r.json()["assigned_account"] is True
    db_session.expire_all()
    assert db_session.get(Payment, esc["sin_cuenta"].id).bank_account_id == acc["id"]
    # el cargo del banco que no esta en Crimson: se registra desde la linea y queda conciliado
    r = client.post(f"/bank-books/lines/{lines['COMISION SINPE']['id']}/movement", json={"kind": "cargo"})
    assert r.status_code == 201 and r.json()["status"] == "conciliado" and r.json()["match"]["amount"] == "-2500.00"
    # deshacer y volver a casar
    assert client.post(f"/bank-books/lines/{trf['id']}/unmatch").json()["status"] == "pendiente"
    assert client.post(f"/bank-books/lines/{trf['id']}/match", json={"type": "payment", "id": esc["sin_cuenta"].id}).status_code == 200
    # el SINPE tardio (fuera de +/-3 dias) casa a mano con el cobro de 113 000? ya esta casado: se ignora
    assert client.post(f"/bank-books/lines/{lines['SINPE TARDIO']['id']}/ignore").json()["status"] == "ignorado"

    rec = client.get(f"/bank-books/accounts/{acc['id']}/reconciliation").json()
    assert D(rec["bank_balance"]) == D("574300.00")  # lo ignorado no suma
    assert D(rec["book_balance"]) == D("582550.50")  # + 25 000 asignado, - 2 500 de cargo
    assert rec["pending_bank"]["count"] == 0
    assert [(e["type"], e["amount"]) for e in rec["pending_book"]["items"]] == [("movement", "1250.50"), ("payment", "7000.00")]
    assert D(rec["unexplained"]) == D("0.00")
    assert D(rec["difference"]) == D("-8250.50")  # exactamente lo pendiente de Crimson
    assert any("El banco reporta" in w for w in rec["warnings"])  # el saldo del banco incluia el SINPE ignorado
    assert db_session.query(BankStatementLine).filter_by(status="conciliado").count() == 8


def test_conciliacion_automatica_por_boton_y_ventana_de_tres_dias(client, auth, db_session):
    from app.models import BankStatementLine

    tid = _tid(auth)
    acc = _cuenta(client)
    inv = _factura(db_session, tid, "FEC-BANCO-2")
    _cobro(db_session, tid, inv, 5000, date(2026, 11, 2), acc["id"])
    _cobro(db_session, tid, inv, 6000, date(2026, 11, 2), acc["id"])
    db_session.commit()
    for i, (day, amt) in enumerate(((date(2026, 11, 5), 5000), (date(2026, 11, 6), 6000))):
        db_session.add(
            BankStatementLine(
                tenant_id=tid, bank_account_id=acc["id"], date=day, description=f"Dep {i}", amount=amt, status="pendiente", batch="t", fingerprint=f"fp{i}", imported_at=datetime(2026, 11, 7)
            )
        )
    db_session.commit()
    assert client.post(f"/bank-books/accounts/{acc['id']}/auto-match").json() == {"matched": 1}  # 3 dias si, 4 no


# ------------------------------------------------------------------ caja, transferencias, deshacer


def test_caja_recibe_efectivo_sin_cuenta_y_transferencias(client, auth, db_session):
    tid = _tid(auth)
    caja = _cuenta(client, name="Caja chica", opening="20000", kind="caja")
    bac = _cuenta(client, name="BAC", opening="0")
    assert caja["cash_default"] is True
    inv = _factura(db_session, tid, "FEC-CAJA")
    _cobro(db_session, tid, inv, 15000, date(2026, 11, 3), method="efectivo")
    _cobro(db_session, tid, inv, 8000, date(2026, 11, 3), method="sinpe")  # sin cuenta pero no es efectivo: no
    _gasto(db_session, tid, 3000, date(2026, 11, 4), method="efectivo")
    db_session.commit()
    r = client.post("/bank-books/transfers", json={"from_account_id": caja["id"], "to_account_id": bac["id"], "date": "2026-11-05", "amount": "30000"})
    assert r.status_code == 201, r.text
    assert D(client.get(f"/bank-books/accounts/{caja['id']}/ledger").json()["closing_balance"]) == D("2000.00")  # 20 000 + 15 000 - 3 000 - 30 000
    assert D(client.get(f"/bank-books/accounts/{bac['id']}/ledger").json()["closing_balance"]) == D("30000.00")
    usd = _cuenta(client, name="Dólares", currency="USD", opening="0")
    assert client.post("/bank-books/transfers", json={"from_account_id": bac["id"], "to_account_id": usd["id"], "date": "2026-11-06", "amount": "5100"}).status_code == 422
    assert client.post("/bank-books/transfers", json={"from_account_id": bac["id"], "to_account_id": usd["id"], "date": "2026-11-06", "amount": "5100", "amount_to": "10"}).status_code == 201
    assert D(client.get(f"/bank-books/accounts/{usd['id']}/ledger").json()["closing_balance"]) == D("10.00")
    # anular la transferencia anula las dos patas
    r = client.post(f"/bank-books/movements/{r.json()[0]['id']}/void")
    assert r.status_code == 200 and {m["status"] for m in r.json()} == {"anulado"} and len(r.json()) == 2
    assert D(client.get(f"/bank-books/accounts/{bac['id']}/ledger").json()["closing_balance"]) == D("-5100.00")
    # con dos cajas en colones nadie adivina: el efectivo sin cuenta no va a ninguna
    _cuenta(client, name="Caja 2", opening="0", kind="caja")
    assert D(client.get(f"/bank-books/accounts/{caja['id']}/ledger").json()["closing_balance"]) == D("20000.00")


def test_deshacer_importacion(client, auth, db_session):
    from app.models import BankStatementLine

    acc = _cuenta(client)
    res = _up(client, acc["id"], CSV_SIGNO.encode(), "bac.csv", preview=False).json()
    lines = client.get(f"/bank-books/accounts/{acc['id']}/lines").json()
    client.post(f"/bank-books/lines/{lines[0]['id']}/movement", json={"kind": "otro"})
    assert client.delete(f"/bank-books/statements/{res['import_id']}").status_code == 409
    client.post(f"/bank-books/lines/{lines[0]['id']}/unmatch")
    r = client.delete(f"/bank-books/statements/{res['import_id']}")
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert db_session.query(BankStatementLine).count() == 0


# ------------------------------------------------------------------ aislamiento y permisos


def _otra_empresa(client, db_session) -> dict:
    from app.core.security import hash_password
    from app.models import Tenant, TenantUser, User

    t2 = Tenant(name="Empresa B bancos", slug="empresa-b-bancos", settings={})
    db_session.add(t2)
    db_session.flush()
    u2 = User(email="admin.b.bancos@ejemplo.com", full_name="Admin B", password_hash=hash_password("Empresa-B-2026"))
    db_session.add(u2)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=t2.id, user_id=u2.id, role_code="admin"))
    db_session.commit()
    admin = client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": "admin.b.bancos@ejemplo.com", "password": "Empresa-B-2026"}).json()["access_token"]
    if admin:
        client.headers["Authorization"] = admin
    return {"Authorization": f"Bearer {tok}"}


def test_aislamiento_entre_empresas(client, auth, db_session):
    tid = _tid(auth)
    acc = _cuenta(client)
    esc = _escenario(db_session, tid, acc["id"])
    _up(client, acc["id"], CSV_BCR.encode("cp1252"), "bcr.csv", preview=False)
    line_a = client.get(f"/bank-books/accounts/{acc['id']}/lines").json()[0]
    b = _otra_empresa(client, db_session)
    assert client.get("/bank-books/accounts", headers=b).json() == []
    for path in (f"/bank-books/accounts/{acc['id']}/ledger", f"/bank-books/accounts/{acc['id']}/reconciliation", f"/bank-books/accounts/{acc['id']}/lines"):
        assert client.get(path, headers=b).status_code == 404, path
    assert client.get(f"/bank-books/lines/{line_a['id']}/candidates", headers=b).status_code == 404
    assert client.post(f"/bank-books/lines/{line_a['id']}/unmatch", headers=b).status_code == 404
    assert _up(client, acc["id"], CSV_SIGNO.encode(), "x.csv", headers=b).status_code == 404
    assert client.put(f"/bank-books/accounts/{acc['id']}/opening", headers=b, json={"opening_balance": "1", "opening_date": CORTE}).status_code == 404
    # B con su propia cuenta y su propio estado de cuenta: no ve ni puede casar lo de A
    acc_b = _cuenta(client, name="BAC B", headers=b)
    _up(client, acc_b["id"], CSV_BCR.encode("cp1252"), "bcr.csv", preview=False, headers=b)
    lines_b = client.get(f"/bank-books/accounts/{acc_b['id']}/lines", headers=b).json()
    assert all(ln["status"] == "pendiente" for ln in lines_b)  # los movimientos de A no le sirven a B
    sinpe_b = next(ln for ln in lines_b if ln["description"] == "SINPE MOVIL CLIENTE")
    assert client.get(f"/bank-books/lines/{sinpe_b['id']}/candidates", headers=b).json() == []
    assert client.post(f"/bank-books/lines/{sinpe_b['id']}/match", headers=b, json={"type": "payment", "id": esc["pago"].id}).status_code == 404
    assert client.post("/bank-books/transfers", headers=b, json={"from_account_id": acc_b["id"], "to_account_id": acc["id"], "date": CORTE, "amount": "1"}).status_code == 404


def test_permisos(client, auth, db_session):
    acc = _cuenta(client)
    ventas = role_headers(client, db_session, _tid(auth), "ventas.libro@ejemplo.com", "ventas")
    conta = role_headers(client, db_session, _tid(auth), "conta.libro@ejemplo.com", "contabilidad")
    assert client.get(f"/bank-books/accounts/{acc['id']}/ledger", headers=ventas).status_code == 403
    assert _up(client, acc["id"], CSV_SIGNO.encode(), "x.csv", headers=ventas).status_code == 403
    assert client.get(f"/bank-books/accounts/{acc['id']}/ledger", headers=conta).status_code == 200
    assert _up(client, acc["id"], CSV_SIGNO.encode(), "x.csv", preview=False, headers=conta).status_code == 201
    r = client.put(f"/bank-books/accounts/{acc['id']}/opening", headers=conta, json={"opening_balance": "12.345", "opening_date": CORTE})
    assert r.status_code == 422  # dos decimales


# ------------------------------------------------------------------ topes del archivo


def test_topes_del_archivo(client, auth):
    acc = _cuenta(client)
    aid = acc["id"]
    grande = b"Fecha,Monto\n" + b"01/11/2026,1\n" * (5 * 1024 * 1024 // 13 + 10)
    assert _up(client, aid, grande, "grande.csv").status_code == 413
    muchas = "Fecha,Monto\n" + "".join(f"02/11/2026,{i + 1}\n" for i in range(5001))
    r = _up(client, aid, muchas.encode(), "muchas.csv")
    assert r.status_code == 422 and "5000" in r.json()["detail"]
    assert _up(client, aid, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "viejo.xls").status_code == 422
    assert _up(client, aid, b"%PDF-1.4 ...", "estado.pdf").status_code == 422
    assert _up(client, aid, b"   ", "vacio.csv").status_code == 422
    assert _up(client, aid, b"hola,mundo\nsin,fechas\n", "raro.csv").status_code == 422  # sin columna de fecha
    # "bomba" XLSX: una parte que se descomprime enorme (relacion > 100) se rechaza antes de abrirla
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", "0" * (3 * 1024 * 1024))
    r = client.post(f"/bank-books/accounts/{aid}/statements/preview", files={"file": ("bomba.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 422 and "grande" in r.json()["detail"]
