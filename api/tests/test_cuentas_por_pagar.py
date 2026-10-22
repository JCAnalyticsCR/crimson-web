"""Cuentas por pagar: pagos a proveedor, aplicaciones, saldo, antigüedad y conciliación bancaria.

Corre sólo estos tests (más los de gastos/conciliación relacionados):
    cd api && uv run pytest -q -p no:cacheprovider tests/test_cuentas_por_pagar.py tests/test_bancos_instrumentos.py tests/test_ops.py -k "expense or bank or concili or cxp or pagar"
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.hacienda_utils import role_headers


# ---------- helpers ----------

def _mk_supplier(client) -> int:
    r = client.post("/suppliers", json={"name": "Proveedor CxP", "tax_id": "3101999999"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_expense(client, *, supplier_id: int, total: float = 10_000, currency: str = "CRC", purchase_type: str = "credito", due_date: str | None = None) -> dict:
    hoy = date.today().isoformat()
    dd = due_date or (date.today() + timedelta(days=30)).isoformat()
    body: dict = {
        "description": f"Factura {supplier_id}",
        "date": hoy,
        "subtotal": total / 1.13,
        "tax_rate": 13,
        "currency": currency,
        "purchase_type": purchase_type,
        "due_date": dd if purchase_type == "credito" else None,
        "supplier_id": supplier_id,
    }
    r = client.post("/expenses", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _mk_payment(client, *, supplier_id: int, amount: float, applications: list | None = None, **kw) -> dict:
    body = {
        "supplier_id": supplier_id,
        "date": date.today().isoformat(),
        "amount": amount,
        "currency": "CRC",
        "method": "transferencia",
        **kw,
    }
    if applications is not None:
        body["applications"] = applications
    r = client.post("/cxp/payments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 1. Pago parcial y saldo ----------

def test_pago_parcial_actualiza_saldo(client, auth):
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=10_000)
    eid = exp["id"]
    assert exp["status"] == "registrado"

    # pago parcial (la mitad)
    pay = _mk_payment(client, supplier_id=sid, amount=5_000, applications=[{"expense_id": eid, "amount": 5_000}])
    assert pay["status"] == "registrado"
    assert pay["applications"][0]["expense_id"] == eid

    # el gasto sigue abierto
    bal = client.get(f"/cxp/expenses/{eid}/balance").json()
    assert bal["balance"] == pytest.approx(5_000, rel=1e-3)
    assert bal["applied"] == pytest.approx(5_000, rel=1e-3)

    # saldo en expenses sigue "registrado" (no pagado)
    exp2 = [e for e in client.get("/expenses").json() if e["id"] == eid]
    assert exp2 and exp2[0]["status"] == "registrado"

    # pago del resto
    _mk_payment(client, supplier_id=sid, amount=5_000, applications=[{"expense_id": eid, "amount": 5_000}])
    exp3 = [e for e in client.get("/expenses").json() if e["id"] == eid]
    assert exp3 and exp3[0]["status"] == "pagado"
    bal2 = client.get(f"/cxp/expenses/{eid}/balance").json()
    assert bal2["balance"] == pytest.approx(0, abs=0.01)


# ---------- 2. Sobrepago rechazado ----------

def test_sobrepago_rechazado(client, auth):
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=1_000)
    eid = exp["id"]

    r = client.post("/cxp/payments", json={
        "supplier_id": sid,
        "date": date.today().isoformat(),
        "amount": 2_000,
        "currency": "CRC",
        "method": "transferencia",
        "applications": [{"expense_id": eid, "amount": 2_000}],
    })
    assert r.status_code == 422


# ---------- 3. Anulación devuelve saldo y estado ----------

def test_anulacion_devuelve_saldo(client, auth):
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=8_000)
    eid = exp["id"]

    # pago completo
    pay = _mk_payment(client, supplier_id=sid, amount=8_000, applications=[{"expense_id": eid, "amount": 8_000}])
    pid = pay["id"]
    exps = [e for e in client.get("/expenses").json() if e["id"] == eid]
    assert exps[0]["status"] == "pagado"

    # anular
    r = client.post(f"/cxp/payments/{pid}/anular", json={"reason": "Error de imputación"})
    assert r.status_code == 200
    assert r.json()["status"] == "anulado"

    # saldo vuelve
    bal = client.get(f"/cxp/expenses/{eid}/balance").json()
    assert bal["balance"] == pytest.approx(8_000, rel=1e-2)

    exps2 = [e for e in client.get("/expenses").json() if e["id"] == eid]
    assert exps2[0]["status"] == "registrado"

    # no se puede anular dos veces
    r2 = client.post(f"/cxp/payments/{pid}/anular", json={"reason": "De nuevo"})
    assert r2.status_code == 409


# ---------- 4. Pago en USD con FX ----------

def test_pago_usd_con_fx(client, auth, db_session):
    from app.models import ExchangeRate

    hoy = date.today()
    if not db_session.query(ExchangeRate).filter(ExchangeRate.currency == "USD", ExchangeRate.date <= hoy).first():
        db_session.add(ExchangeRate(currency="USD", date=hoy, sell=520.0, buy=515.0, source="manual"))
        db_session.commit()

    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=200, currency="USD")
    eid = exp["id"]

    pay = _mk_payment(client, supplier_id=sid, amount=100, currency="USD", applications=[{"expense_id": eid, "amount": 100}])
    assert pay["currency"] == "USD"
    assert float(pay["fx_rate"]) > 1  # no queda en 1 para USD

    bal = client.get(f"/cxp/expenses/{eid}/balance").json()
    assert bal["balance"] == pytest.approx(100, rel=1e-2)


# ---------- 5. Gasto de contado no es aplicable ----------

def test_gasto_contado_no_aplicable(client, auth):
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=5_000, purchase_type="contado")
    eid = exp["id"]

    r = client.post("/cxp/payments", json={
        "supplier_id": sid,
        "date": date.today().isoformat(),
        "amount": 5_000,
        "currency": "CRC",
        "method": "efectivo",
        "applications": [{"expense_id": eid, "amount": 5_000}],
    })
    assert r.status_code == 422


# ---------- 6. Antigüedad ----------

def test_antigüedad_clasifica_correctamente(client, auth):
    from app.models import Expense

    sid = _mk_supplier(client)
    hoy = date.today()
    tid = auth["tenant"]["id"]

    # corriente (vence en 10 días)
    _mk_expense(client, supplier_id=sid, total=1_000, due_date=(hoy + timedelta(days=10)).isoformat())
    # 1-30 días vencida
    _mk_expense(client, supplier_id=sid, total=2_000, due_date=(hoy - timedelta(days=15)).isoformat())
    # >90 días vencida
    _mk_expense(client, supplier_id=sid, total=3_000, due_date=(hoy - timedelta(days=100)).isoformat())

    aging = client.get("/cxp/aging").json()
    assert aging["rows"]
    row = next((r for r in aging["rows"] if r["supplier_id"] == sid), None)
    assert row is not None
    assert float(row["corriente"]) == pytest.approx(1_000, rel=1e-2)
    assert float(row["d1_30"]) == pytest.approx(2_000, rel=1e-2)
    assert float(row["d90_mas"]) == pytest.approx(3_000, rel=1e-2)
    assert float(row["total"]) == pytest.approx(6_000, rel=1e-2)


# ---------- 7. Conciliación bancaria: débito casa contra SupplierPayment ----------

def test_conciliacion_con_supplier_payment(client, auth, db_session):
    from app.models import BankAccount, BankStatementLine, ExchangeRate

    hoy = date.today()
    if not db_session.query(ExchangeRate).filter(ExchangeRate.currency == "USD", ExchangeRate.date <= hoy).first():
        db_session.add(ExchangeRate(currency="USD", date=hoy, sell=520.0, buy=515.0, source="manual"))
        db_session.commit()

    acc = client.post("/banking/accounts", json={"name": "CRC concilia", "bank": "BCR", "currency": "CRC"}).json()
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=15_000)
    eid = exp["id"]

    # crear el pago a proveedor
    pay = _mk_payment(client, supplier_id=sid, amount=15_000, applications=[{"expense_id": eid, "amount": 15_000}])
    pid = pay["id"]

    # importar una línea de extracto de -15000
    tid = auth["tenant"]["id"]
    ln = BankStatementLine(
        tenant_id=tid,
        bank_account_id=acc["id"],
        date=hoy,
        description="TRANSFER PROVEEDOR CXP",
        amount=-15_000,
        status="pendiente",
        batch="b-cxp-1",
        fingerprint="fp-cxp-1",
        imported_at=datetime.now(UTC),
    )
    db_session.add(ln)
    db_session.commit()

    # casar manualmente contra el supplier_payment
    r = client.post(f"/banking/lines/{ln.id}/match", json={"type": "supplier_payment", "id": pid})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "conciliado"
    assert r.json()["match"]["type"] == "supplier_payment"

    # des-casar
    r2 = client.post(f"/banking/lines/{ln.id}/unmatch")
    assert r2.status_code == 200 and r2.json()["status"] == "pendiente"


# ---------- 8. Registrar pago desde débito del extracto ----------

def test_pay_supplier_from_debit_line(client, auth, db_session):
    from app.models import BankAccount, BankStatementLine, Expense

    hoy = date.today()
    acc = client.post("/banking/accounts", json={"name": "CRC pago directo", "bank": "BCR", "currency": "CRC"}).json()
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=20_000)
    eid = exp["id"]

    tid = auth["tenant"]["id"]
    ln = BankStatementLine(
        tenant_id=tid,
        bank_account_id=acc["id"],
        date=hoy,
        description="PAGO PROV CXP TEST",
        amount=-20_000,
        status="pendiente",
        batch="b-cxp-2",
        fingerprint="fp-cxp-2",
        imported_at=datetime.now(UTC),
    )
    db_session.add(ln)
    db_session.commit()

    r = client.post(f"/banking/lines/{ln.id}/pay-supplier", json={
        "supplier_id": sid,
        "method": "transferencia",
        "applications": [{"expense_id": eid, "amount": 20_000}],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "conciliado"
    assert body["match"]["type"] == "supplier_payment"

    # el gasto queda pagado
    exps = [e for e in client.get("/expenses").json() if e["id"] == eid]
    assert exps and exps[0]["status"] == "pagado"


# ---------- 9. Permisos ----------

def test_permisos_cxp(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ventas = role_headers(client, db_session, tid, "ventas.cxp@ejemplo.com", "ventas")

    # ventas no puede listar ni crear
    assert client.get("/cxp/payments", headers=ventas).status_code == 403
    assert client.post("/cxp/payments", headers=ventas, json={}).status_code == 403

    # contabilidad sí puede
    conta = role_headers(client, db_session, tid, "conta.cxp@ejemplo.com", "contabilidad")
    r = client.get("/cxp/payments", headers=conta)
    assert r.status_code == 200


# ---------- 10. Aislamiento multi-tenant ----------

def test_aislamiento_tenant(client, auth, db_session):
    from app.core.security import hash_password
    from app.models import Tenant, TenantUser, User

    # empresa B
    t2 = Tenant(name="Empresa B", slug="empresa-b-cxp", settings={})
    db_session.add(t2)
    db_session.flush()
    pw2 = "Empresa-B-2026"
    u2 = User(email="admin.b.cxp@ejemplo.com", full_name="Admin B", password_hash=hash_password(pw2))
    db_session.add(u2)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=t2.id, user_id=u2.id, role_code="admin"))
    db_session.commit()

    admin_b_headers = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "admin.b.cxp@ejemplo.com", "password": pw2}).json()["access_token"]}

    # crear proveedor y gasto en empresa A (admin principal)
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=5_000)
    eid = exp["id"]

    # empresa B no puede pagar el gasto de empresa A
    r = client.post("/cxp/payments", headers=admin_b_headers, json={
        "supplier_id": sid,
        "date": date.today().isoformat(),
        "amount": 5_000,
        "currency": "CRC",
        "method": "efectivo",
        "applications": [{"expense_id": eid, "amount": 5_000}],
    })
    # o no encuentra el proveedor (404) o no encuentra el gasto (404)
    assert r.status_code in (404, 422), r.text

    # empresa B no puede ver el saldo de empresa A
    r2 = client.get(f"/cxp/expenses/{eid}/balance", headers=admin_b_headers)
    assert r2.status_code == 404


# ---------- 11. Estado de cuenta del proveedor ----------

def test_estado_cuenta_proveedor(client, auth):
    sid = _mk_supplier(client)
    exp1 = _mk_expense(client, supplier_id=sid, total=3_000)
    exp2 = _mk_expense(client, supplier_id=sid, total=5_000)

    # pago parcial en exp1
    _mk_payment(client, supplier_id=sid, amount=1_500, applications=[{"expense_id": exp1["id"], "amount": 1_500}])

    stmt = client.get(f"/cxp/suppliers/{sid}/statement").json()
    assert stmt["supplier"]["id"] == sid
    exp_rows = {e["expense_id"]: e for e in stmt["expenses"]}
    assert abs(exp_rows[exp1["id"]]["balance"] - 1_500) < 1
    assert abs(exp_rows[exp2["id"]]["balance"] - 5_000) < 1
    assert abs(stmt["total_balance"] - 6_500) < 1
    assert len(stmt["payments"]) == 1


# ---------- 12. Resumen ejecutivo ----------

def test_summary_cxp(client, auth):
    sid = _mk_supplier(client)
    hoy = date.today()
    _mk_expense(client, supplier_id=sid, total=12_000, due_date=(hoy + timedelta(days=3)).isoformat())
    _mk_expense(client, supplier_id=sid, total=8_000, due_date=(hoy - timedelta(days=5)).isoformat())

    s = client.get("/cxp/summary").json()
    assert s["count"] >= 2
    assert s["total_crc"] > 0
    assert s["vencido_crc"] > 0
    assert s["semana_crc"] > 0  # el que vence en 3 días


# ---------- 13. Candidatos de conciliación incluyen supplier_payment ----------

def test_candidatos_incluyen_supplier_payment(client, auth, db_session):
    from app.models import BankAccount, BankStatementLine, BankStatementLine as BSL

    hoy = date.today()
    acc = client.post("/banking/accounts", json={"name": "CRC candidatos", "bank": "BCR", "currency": "CRC"}).json()
    sid = _mk_supplier(client)
    exp = _mk_expense(client, supplier_id=sid, total=7_500)
    pay = _mk_payment(client, supplier_id=sid, amount=7_500, applications=[{"expense_id": exp["id"], "amount": 7_500}])

    tid = auth["tenant"]["id"]
    ln = BankStatementLine(
        tenant_id=tid,
        bank_account_id=acc["id"],
        date=hoy,
        description="TRANSFER PROV",
        amount=-7_500,
        status="pendiente",
        batch="b-cand-1",
        fingerprint="fp-cand-1",
        imported_at=datetime.now(UTC),
    )
    db_session.add(ln)
    db_session.commit()

    cands = client.get(f"/banking/lines/{ln.id}/candidates").json()
    types = [c["type"] for c in cands]
    assert "supplier_payment" in types
    sp_cand = next(c for c in cands if c["type"] == "supplier_payment")
    assert sp_cand["id"] == pay["id"]
