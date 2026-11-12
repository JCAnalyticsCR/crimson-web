"""Cuentas por pagar (reunion con la contadora 10/11): «Marcar pagada» con fecha, medio, cuenta y referencia; desmarcar
con motivo; varias a la vez; aislamiento entre empresas; antiguedad y totales por moneda; corte con Fygaro (1/11/2026);
asiento balanceado por el camino de SupplierPayment; gasto recibido por correo con la condicion del XML como pista.

Todos los datos son ficticios.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.hacienda_utils import clave, fe_xml, role_headers

HOY = date.today()
CORTE = date(2026, 11, 1)
DESDE = max(CORTE + timedelta(days=1), HOY - timedelta(days=5))  # fecha de factura despues del corte y no futura


# ---------------------------------------------------------------- ayudas

def _supplier(client, name="Proveedor Pagos SA", tax_id="3101777001", headers=None) -> int:
    r = client.post("/suppliers", json={"name": name, "tax_id": tax_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bank(client, name="Cuenta corriente BCR", currency="CRC", headers=None) -> int:
    r = client.post("/banking/accounts", json={"name": name, "bank": "BCR", "currency": currency}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _expense(client, *, supplier_id=None, total=11_300.0, fecha: date = DESDE, currency="CRC", purchase_type="contado", due: date | None = None, headers=None) -> dict:
    body = {
        "description": f"Factura prueba {total} {fecha}",
        "date": fecha.isoformat(),
        "subtotal": round(total / 1.13, 5),
        "tax_rate": 13,
        "currency": currency,
        "purchase_type": purchase_type,
        "due_date": due.isoformat() if due else None,
        "supplier_id": supplier_id,
    }
    if currency != "CRC":
        body["fx_rate"] = 510
    r = client.post("/expenses", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _mark(client, ids, *, bank_id=None, method="transferencia", paid=None, ref="TRF-0001", headers=None, **kw):
    body = {"expense_ids": ids, "paid_date": (paid or HOY).isoformat(), "method": method, "bank_account_id": bank_id, "reference": ref, **kw}
    return client.post("/cxp/mark-paid", json=body, headers=headers)


def _exp(client, eid, headers=None) -> dict:
    return next(e for e in client.get("/expenses", params={"limit": 500}, headers=headers).json() if e["id"] == eid)


def _pending_ids(client, headers=None, **params) -> set[int]:
    return {r["expense_id"] for r in client.get("/cxp/pending", params=params, headers=headers).json()["rows"]}


def _usd(db_session):
    from app.models import ExchangeRate

    if not db_session.query(ExchangeRate).filter(ExchangeRate.currency == "USD", ExchangeRate.date <= date(2026, 10, 30)).first():
        db_session.add(ExchangeRate(currency="USD", date=date(2026, 10, 30), sell=510.0, buy=505.0, source="manual"))
        db_session.commit()


def _otra_empresa(client, db_session):
    from app.core.security import hash_password
    from app.models import Tenant, TenantUser, User

    t2 = Tenant(name="Empresa B Pagos", slug="empresa-b-pagos", settings={})
    db_session.add(t2)
    db_session.flush()
    u2 = User(email="admin.b.pagos@ejemplo.com", full_name="Admin B", password_hash=hash_password("Empresa-B-2026"))
    db_session.add(u2)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=t2.id, user_id=u2.id, role_code="admin"))
    db_session.commit()
    admin = client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": "admin.b.pagos@ejemplo.com", "password": "Empresa-B-2026"}).json()["access_token"]
    if admin:
        client.headers["Authorization"] = admin
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------- marcar y desmarcar

def test_marcar_y_desmarcar_con_motivo(client, auth, db_session):
    from app.models import AuditLog, SupplierPayment

    sid = _supplier(client)
    bid = _bank(client)
    e = _expense(client, supplier_id=sid)
    assert e["pay_state"] == "pendiente" and e["paid_date"] is None
    assert e["id"] in _pending_ids(client)

    r = _mark(client, [e["id"]], bank_id=bid, ref="SINPE-778899", method="sinpe", note="pago de prueba")
    assert r.status_code == 200, r.text
    g = r.json()["paid"][0]
    assert g["status"] == "pagado" and g["pay_state"] == "pagado"
    assert g["paid_date"] == HOY.isoformat() and g["payment_reference"] == "SINPE-778899"
    assert g["payment_method"] == "sinpe" and g["bank_account_id"] == bid
    assert e["id"] not in _pending_ids(client)
    assert e["id"] in {x["id"] for x in client.get("/expenses", params={"pay_state": "pagado"}).json()}
    pay = db_session.query(SupplierPayment).filter_by(reference="SINPE-778899").one()
    assert pay.status == "registrado" and Decimal(str(pay.amount)) == Decimal("11300") and pay.bank_account_id == bid
    # no se marca dos veces
    assert _mark(client, [e["id"]], bank_id=bid).status_code == 409

    # desmarcar: pide motivo y queda en la bitacora
    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": ""}).status_code == 422
    r = client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "Se marcó la factura equivocada"})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["status"] == "registrado" and g["pay_state"] == "pendiente" and g["paid_date"] is None and g["payment_reference"] is None
    db_session.expire_all()
    pay = db_session.get(SupplierPayment, pay.id)
    assert pay.status == "anulado" and pay.void_reason == "Se marcó la factura equivocada"
    log = db_session.query(AuditLog).filter(AuditLog.entity == "expense", AuditLog.entity_id == e["id"]).all()
    assert any("Se marcó la factura equivocada" in (a.reason or "") for a in log)
    assert any("Marcada pagada" in (a.reason or "") for a in log)
    assert e["id"] in _pending_ids(client)
    # no se desmarca lo que no esta pagado
    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "otra vez"}).status_code == 409
    # se puede volver a marcar con los datos correctos
    assert _mark(client, [e["id"]], bank_id=bid, ref="TRF-CORRECTA").status_code == 200


def test_formulario_no_cambia_el_estado_de_un_gasto_con_pago(client, auth):
    sid = _supplier(client)
    bid = _bank(client)
    e = _expense(client, supplier_id=sid)
    assert _mark(client, [e["id"]], bank_id=bid).status_code == 200
    g = _exp(client, e["id"])
    body = {k: g[k] for k in ("description", "date", "currency", "subtotal", "tax_rate", "iva_credit", "purchase_type", "supplier_id")}
    r = client.put(f"/expenses/{e['id']}", json={**body, "status": "registrado"})
    assert r.status_code == 409 and "Desmarcar" in r.json()["detail"]
    r = client.put(f"/expenses/{e['id']}", json={**body, "status": "anulado"})
    assert r.status_code == 409
    # editar otra cosa (las notas) sin tocar el estado si se puede
    assert client.put(f"/expenses/{e['id']}", json={**body, "status": "pagado", "notes": "nota"}).status_code == 200


def test_gasto_pagado_por_otro_camino_tiene_fecha_de_pago(client, auth):
    """Planilla, comisiones, extracto o un gasto creado ya "pagado": paid_date nunca queda vacio (toma la de la factura)."""
    r = client.post("/expenses", json={"description": "Caja chica", "date": DESDE.isoformat(), "subtotal": 1000, "tax_rate": 0, "status": "pagado"})
    assert r.status_code == 201
    assert r.json()["paid_date"] == DESDE.isoformat() and r.json()["pay_state"] == "pagado"


# ---------------------------------------------------------------- varias a la vez

def test_varias_a_la_vez_y_todo_o_nada(client, auth, db_session):
    from app.models import SupplierPayment

    sid = _supplier(client)
    otro = _supplier(client, name="Otro Proveedor SA", tax_id="3101777002")
    bid = _bank(client)
    a = _expense(client, supplier_id=sid, total=1130)
    b = _expense(client, supplier_id=sid, total=2260)
    c = _expense(client, supplier_id=otro, total=3390)
    sin = _expense(client, supplier_id=None, total=565)  # sin proveedor tambien se paga

    r = _mark(client, [a["id"], b["id"], c["id"], sin["id"]], bank_id=bid, ref="TRF-LOTE-1")
    assert r.status_code == 200, r.text
    assert {g["id"] for g in r.json()["paid"]} == {a["id"], b["id"], c["id"], sin["id"]}
    assert all(g["status"] == "pagado" and g["payment_reference"] == "TRF-LOTE-1" for g in r.json()["paid"])
    pays = db_session.query(SupplierPayment).filter_by(reference="TRF-LOTE-1").all()
    assert len(pays) == 4 and {p.supplier_id for p in pays} == {sid, otro, None}

    # todo o nada: si una ya esta pagada, ninguna de las otras se marca
    d = _expense(client, supplier_id=sid, total=1000)
    r = _mark(client, [d["id"], a["id"]], bank_id=bid, ref="TRF-LOTE-2")
    assert r.status_code == 409
    assert _exp(client, d["id"])["status"] == "registrado"
    assert db_session.query(SupplierPayment).filter_by(reference="TRF-LOTE-2").count() == 0


# ---------------------------------------------------------------- validaciones

def test_validaciones_del_pago(client, auth, db_session):
    from app.models import BankInstrument

    _usd(db_session)
    sid = _supplier(client)
    crc = _bank(client)
    usd = _bank(client, name="Cuenta dólares", currency="USD")
    e = _expense(client, supplier_id=sid)
    eu = _expense(client, supplier_id=sid, total=113, currency="USD")

    assert _mark(client, [e["id"]], bank_id=crc, paid=HOY + timedelta(days=1)).status_code == 422  # futura
    assert _mark(client, [e["id"]], bank_id=crc, ref="").status_code == 422  # transferencia sin referencia
    assert _mark(client, [e["id"]], bank_id=None).status_code == 422  # sin cuenta
    assert _mark(client, [e["id"]], bank_id=crc, method="bitcoin").status_code == 422
    assert _mark(client, [e["id"]], bank_id=999999).status_code == 404
    assert _mark(client, [999999], bank_id=crc).status_code == 404
    # moneda: factura en dolares no sale de la cuenta en colones
    r = _mark(client, [eu["id"]], bank_id=crc)
    assert r.status_code == 422 and "USD" in r.json()["detail"]
    r = _mark(client, [eu["id"]], bank_id=usd, ref="TRF-USD-1")
    assert r.status_code == 200, r.text
    # tarjeta de otra cuenta
    tarjeta = BankInstrument(tenant_id=auth["tenant"]["id"], bank_account_id=usd, kind="tarjeta_debito", name="Débito", last4="4321")
    db_session.add(tarjeta)
    db_session.commit()
    assert _mark(client, [e["id"]], bank_id=crc, method="tarjeta", bank_instrument_id=tarjeta.id).status_code == 422
    # fecha de pago anterior a la factura: aviso que hay que confirmar
    previo = DESDE - timedelta(days=1)
    r = _mark(client, [e["id"]], bank_id=crc, paid=previo)
    assert r.status_code == 409 and "anterior a la factura" in r.json()["detail"]
    r = _mark(client, [e["id"]], bank_id=crc, paid=previo, before_invoice_ok=True)
    assert r.status_code == 200 and r.json()["warnings"] and r.json()["paid"][0]["paid_date"] == previo.isoformat()
    # efectivo no pide cuenta; cheque pide numero
    f = _expense(client, supplier_id=sid, total=500)
    assert _mark(client, [f["id"]], bank_id=crc, method="cheque", ref=None).status_code == 422
    assert _mark(client, [f["id"]], bank_id=None, method="efectivo", ref=None).status_code == 200
    # nota de credito (monto negativo) no se paga
    from app.models import Expense

    nc = Expense(tenant_id=auth["tenant"]["id"], description="NC recibida", date=DESDE, currency="CRC", subtotal=-100, tax_rate=13, tax_amount=-13, total=-113, status="registrado")
    db_session.add(nc)
    db_session.commit()
    assert _mark(client, [nc.id], bank_id=crc).status_code == 422
    assert nc.id not in _pending_ids(client)


def test_permisos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    bid = _bank(client)
    e = _expense(client)
    ventas = role_headers(client, db_session, tid, "ventas.pagos@ejemplo.com", "ventas")
    assert client.get("/cxp/pending", headers=ventas).status_code == 403
    assert _mark(client, [e["id"]], bank_id=bid, headers=ventas).status_code == 403
    conta = role_headers(client, db_session, tid, "conta.pagos@ejemplo.com", "contabilidad")
    assert _mark(client, [e["id"]], bank_id=bid, headers=conta).status_code == 200
    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "Prueba de permisos"}, headers=ventas).status_code == 403
    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "Prueba de permisos"}, headers=conta).status_code == 200


# ---------------------------------------------------------------- aislamiento

def test_aislamiento_entre_empresas(client, auth, db_session):
    b = _otra_empresa(client, db_session)
    sid = _supplier(client)
    bid_a = _bank(client)
    e = _expense(client, supplier_id=sid)
    bid_b = _bank(client, name="Cuenta B", headers=b)
    eb = _expense(client, headers=b)

    # B no ve ni marca ni desmarca lo de A; tampoco usa la cuenta de A
    assert e["id"] not in _pending_ids(client, headers=b)
    assert eb["id"] in _pending_ids(client, headers=b) and eb["id"] not in _pending_ids(client)
    assert _mark(client, [e["id"]], bank_id=bid_b, headers=b).status_code == 404
    assert _mark(client, [eb["id"]], bank_id=bid_a, headers=b).status_code == 404
    assert _mark(client, [e["id"]], bank_id=bid_a).status_code == 200
    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "intento ajeno"}, headers=b).status_code == 404
    # A no puede mezclar un gasto de B en su lote
    assert _mark(client, [eb["id"]], bank_id=bid_a).status_code == 404
    assert _exp(client, e["id"])["status"] == "pagado"
    assert _exp(client, eb["id"], headers=b)["status"] == "registrado"


# ---------------------------------------------------------------- antiguedad y totales

def test_antiguedad_y_totales_por_moneda(client, auth, db_session):
    _usd(db_session)
    sid = _supplier(client)
    base = date(2026, 11, 2)
    corte = date(2027, 2, 15)
    casos = [
        (date(2027, 2, 20), 1000, "corriente"),
        (date(2027, 2, 1), 2000, "d1_30"),  # 14 dias
        (date(2026, 12, 20), 3000, "d31_60"),  # 57
        (date(2026, 11, 25), 4000, "d61_90"),  # 82
        (date(2026, 11, 5), 5000, "d90_mas"),  # 102
    ]
    ids = {}
    for due, total, bucket in casos:
        ids[_expense(client, supplier_id=sid, total=total, fecha=base, purchase_type="credito", due=due)["id"]] = bucket
    usd = _expense(client, supplier_id=sid, total=226, fecha=base, currency="USD", purchase_type="credito", due=date(2027, 1, 1))  # 45 dias
    contado = _expense(client, supplier_id=sid, total=700, fecha=base)  # contado, sin vencimiento: vence con la factura (105 dias)

    res = client.get("/cxp/pending", params={"as_of": corte.isoformat()}).json()
    assert res["cutoff"] == "2026-11-01"
    rows = {r["expense_id"]: r for r in res["rows"]}
    for eid, bucket in ids.items():
        assert rows[eid]["bucket"] == bucket, (eid, rows[eid])
    assert rows[usd["id"]]["bucket"] == "d31_60" and rows[usd["id"]]["currency"] == "USD"
    assert rows[contado["id"]]["has_due_date"] is False and rows[contado["id"]]["bucket"] == "d90_mas"
    crc = res["totals"]["CRC"]
    assert crc["corriente"] == pytest.approx(1000, abs=0.05)
    assert crc["d1_30"] == pytest.approx(2000, abs=0.05) and crc["d31_60"] == pytest.approx(3000, abs=0.05)
    assert crc["d61_90"] == pytest.approx(4000, abs=0.05) and crc["d90_mas"] == pytest.approx(5700, abs=0.05)
    assert crc["vencido"] == pytest.approx(14_700, abs=0.1)
    assert crc["total"] == pytest.approx(15_700, abs=0.1) and crc["count"] == 6
    assert res["totals"]["USD"]["total"] == pytest.approx(226, abs=0.01) and res["totals"]["USD"]["d31_60"] == pytest.approx(226, abs=0.01)
    # el resumen de antiguedad por proveedor tambien separa las monedas
    aging = client.get("/cxp/aging").json()
    assert set(aging["by_currency"]) == {"CRC", "USD"}
    # filtro por moneda
    assert _pending_ids(client, currency="USD", as_of=corte.isoformat()) == {usd["id"]}


# ---------------------------------------------------------------- corte con Fygaro

def test_lo_anterior_al_corte_no_es_pendiente(client, auth):
    sid = _supplier(client)
    bid = _bank(client)
    viejo = _expense(client, supplier_id=sid, fecha=date(2026, 10, 20), purchase_type="credito", due=date(2026, 11, 20))
    nuevo = _expense(client, supplier_id=sid, fecha=CORTE)
    assert viejo["pay_state"] == "anterior" and nuevo["pay_state"] == "pendiente"
    pend = _pending_ids(client)
    assert nuevo["id"] in pend and viejo["id"] not in pend
    assert viejo["id"] not in {r["expense_id"] for r in client.get("/cxp/upcoming").json()}
    assert all(r["supplier_id"] != sid or float(r["total"]) == pytest.approx(11_300, abs=0.1) for r in client.get("/cxp/aging").json()["rows"])
    lista = {x["id"] for x in client.get("/expenses", params={"pay_state": "pendiente", "limit": 500}).json()}
    assert nuevo["id"] in lista and viejo["id"] not in lista
    assert viejo["id"] in {x["id"] for x in client.get("/expenses", params={"pay_state": "anterior", "limit": 500}).json()}
    # igual se puede marcar pagado a mano
    assert _mark(client, [viejo["id"]], bank_id=bid, paid=date(2026, 10, 25)).status_code == 200


# ---------------------------------------------------------------- asiento

def test_asiento_balanceado_y_desmarcar_sin_exportar(client, auth, db_session):
    """Marcar pagada = gasto DR gasto+IVA / CR CxP (aunque sea de contado con cuenta) + pago DR CxP / CR banco, los dos
    balanceados. Desmarcar antes de exportar deja el pago sin asiento (no un reverso suelto)."""
    from app.models import JournalEntry, SupplierPayment
    from app.services import journal as js

    tid = auth["tenant"]["id"]
    sid = _supplier(client)
    bid = _bank(client)
    e = _expense(client, supplier_id=sid, total=11_300, fecha=HOY)
    # cuenta puesta de antemano en el formulario (contado): igual el gasto va contra CxP cuando se paga por CxP
    g = _exp(client, e["id"])
    body = {k: g[k] for k in ("description", "date", "currency", "subtotal", "tax_rate", "iva_credit", "purchase_type", "supplier_id")}
    assert client.put(f"/expenses/{e['id']}", json={**body, "bank_account_id": bid}).status_code == 200
    assert _mark(client, [e["id"]], bank_id=bid, paid=HOY).status_code == 200

    mes = HOY.strftime("%Y-%m")
    res = js.regenerate_all(db_session, tid, mes)
    assert not res["errors"], res
    ent = {(j.source_type, j.source_id): j for j in db_session.query(JournalEntry).filter_by(tenant_id=tid).all()}
    pay = db_session.query(SupplierPayment).filter_by(tenant_id=tid, status="registrado").one()

    def sides(j):
        debe = sum(Decimal(str(ln.amount)) for ln in j.lines if ln.side == "debe")
        haber = sum(Decimal(str(ln.amount)) for ln in j.lines if ln.side == "haber")
        return debe, haber, {(ln.role_key, ln.side) for ln in j.lines}

    debe, haber, roles = sides(ent[("expense", e["id"])])
    assert debe == haber == Decimal("11300.00")
    assert ("cxp_proveedores_crc", "haber") in roles and (f"banco_{bid}", "haber") not in roles
    debe, haber, roles = sides(ent[("supplier_payment", pay.id)])
    assert debe == haber == Decimal("11300.00")
    assert ("cxp_proveedores_crc", "debe") in roles and (f"banco_{bid}", "haber") in roles

    # desmarcar en el mismo mes, sin exportar: el asiento del pago desaparece; el del gasto queda en CxP
    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "Pago duplicado"}).status_code == 200
    db_session.expire_all()
    js.regenerate_all(db_session, tid, mes)
    tipos = {(j.source_type, j.source_id) for j in db_session.query(JournalEntry).filter_by(tenant_id=tid).all()}
    assert ("supplier_payment", pay.id) not in tipos and ("expense", e["id"]) in tipos
    exp_entry = db_session.query(JournalEntry).filter_by(tenant_id=tid, source_type="expense", source_id=e["id"]).one()
    assert ("cxp_proveedores_crc", "haber") in sides(exp_entry)[2]


def test_desmarcar_ya_exportado_genera_reverso(client, auth, db_session):
    from datetime import UTC, datetime

    from app.models import AccountingExport, AccountingPeriod, JournalEntry, SupplierPayment
    from app.services import journal as js

    tid = auth["tenant"]["id"]
    bid = _bank(client)
    e = _expense(client, total=5650, fecha=HOY)
    assert _mark(client, [e["id"]], bank_id=bid, paid=HOY).status_code == 200
    mes = HOY.strftime("%Y-%m")
    js.regenerate_all(db_session, tid, mes)
    pay = db_session.query(SupplierPayment).filter_by(tenant_id=tid).one()
    per = AccountingPeriod(tenant_id=tid, month=mes, status="abierto")
    db_session.add(per)
    db_session.flush()
    exp_row = AccountingExport(tenant_id=tid, month=mes, period_id=per.id, exported_by=1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    db_session.add(exp_row)
    db_session.flush()
    je = db_session.query(JournalEntry).filter_by(tenant_id=tid, source_type="supplier_payment", source_id=pay.id).one()
    je.export_id = exp_row.id
    db_session.commit()

    assert client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "Banco devolvió la transferencia"}).status_code == 200
    db_session.expire_all()
    js.regenerate_all(db_session, tid, mes)
    entries = db_session.query(JournalEntry).filter_by(tenant_id=tid, source_type="supplier_payment", source_id=pay.id).all()
    kinds = {j.kind: j for j in entries}
    assert set(kinds) == {"normal", "ajuste"}
    roles = {(ln.role_key, ln.side) for ln in kinds["ajuste"].lines}
    assert ("cxp_proveedores_crc", "haber") in roles and (f"banco_{bid}", "debe") in roles  # reverso exacto
    debe = sum(Decimal(str(ln.amount)) for ln in kinds["ajuste"].lines if ln.side == "debe")
    haber = sum(Decimal(str(ln.amount)) for ln in kinds["ajuste"].lines if ln.side == "haber")
    assert debe == haber


def test_no_se_desmarca_lo_conciliado(client, auth, db_session):
    from datetime import UTC, datetime

    from app.models import BankStatementLine, SupplierPayment

    tid = auth["tenant"]["id"]
    bid = _bank(client)
    e = _expense(client, total=2260)
    assert _mark(client, [e["id"]], bank_id=bid).status_code == 200
    pay = db_session.query(SupplierPayment).filter_by(tenant_id=tid).one()
    db_session.add(BankStatementLine(tenant_id=tid, bank_account_id=bid, date=HOY, description="TRF PROVEEDOR", amount=-2260, status="conciliado",
                                     matched_type="supplier_payment", matched_id=pay.id, matched_by="manual", batch="b-pagos", fingerprint="fp-pagos-1", imported_at=datetime.now(UTC)))
    db_session.commit()
    r = client.post(f"/cxp/expenses/{e['id']}/unmark", json={"reason": "corrección"})
    assert r.status_code == 409 and "conciliación" in r.json()["detail"]


# ---------------------------------------------------------------- gasto recibido por correo

def _xml(n: int, condicion: str, plazo: int | None = None) -> str:
    ddmmaa = HOY.strftime("%d%m%y")
    cl = clave(fecha=ddmmaa, emisor="003101000001", consecutivo=f"0010000101{n:010d}")
    x = fe_xml(cl, consecutivo=f"0010000101{n:010d}", fecha=f"{HOY.isoformat()}T10:00:00-06:00")
    extra = f"<PlazoCredito>{plazo}</PlazoCredito>" if plazo is not None else ""
    return x.replace("<CondicionVenta>01</CondicionVenta>", f"<CondicionVenta>{condicion}</CondicionVenta>{extra}")


def _recibir(client, xml: str) -> dict:
    r = client.post("/reception/upload", files={"file": ("factura.xml", xml.encode(), "application/xml")})
    assert r.status_code == 201, r.text
    r = client.post(f"/reception/{r.json()['id']}/respond", json={"action": "aceptada"})
    assert r.status_code in (200, 201), r.text
    return _exp(client, r.json()["expense_id"])


def test_gasto_por_correo_queda_pendiente_con_la_pista_del_xml(client, auth):
    contado = _recibir(client, _xml(901, "01"))
    assert contado["status"] == "registrado" and contado["xml_condition"] == "contado" and contado["pay_hint"] == "Contado según XML"
    assert contado["pay_state"] == ("pendiente" if HOY >= CORTE else "anterior")
    credito = _recibir(client, _xml(902, "02", 30))
    assert credito["status"] == "registrado" and credito["purchase_type"] == "credito" and credito["xml_condition"] == "credito"
    assert credito["due_date"] == (HOY + timedelta(days=30)).isoformat()
    if HOY >= CORTE:
        rows = {r["expense_id"]: r for r in client.get("/cxp/pending").json()["rows"]}
        assert rows[contado["id"]]["hint"] == "Contado según XML" and rows[credito["id"]]["has_due_date"] is True


# ---------------------------------------------------------------- cobros de facturas de venta

def test_cobro_valida_cuenta_y_fecha(client, auth, db_session):
    b = _otra_empresa(client, db_session)
    ajena = _bank(client, name="Cuenta de B", headers=b)
    propia = _bank(client)
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]}).json()
    base = {"method": "sinpe", "kind": "captura", "amount": "10", "external_ref": "SINPE-55"}
    assert client.post(f"/invoices/{inv['id']}/payments", json={**base, "bank_account_id": ajena}).status_code == 404
    assert client.post(f"/invoices/{inv['id']}/payments", json={**base, "paid_at": (HOY + timedelta(days=2)).isoformat()}).status_code == 422
    r = client.post(f"/invoices/{inv['id']}/payments", json={**base, "bank_account_id": propia, "paid_at": HOY.isoformat()})
    assert r.status_code == 201, r.text
