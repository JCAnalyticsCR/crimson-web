"""Bancos: contabilidad administra cuentas sin Ajustes; saldo segun el extracto (apertura + lineas); chequeras con su
rango y siguiente numero; tarjetas de debito con SOLO los ultimos 4 digitos."""

from datetime import UTC, date, datetime
from decimal import Decimal

from tests.hacienda_utils import role_headers


def test_cuentas_saldo_chequera_y_tarjeta(client, auth, db_session):
    from app.models import BankStatementLine

    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.banco@ejemplo.com", "contabilidad")
    assert client.get("/settings/bank-accounts", headers=conta).status_code == 403  # lo viejo sigue siendo del admin
    r = client.post("/banking/accounts", headers=conta, json={"name": "BCR dólares", "bank": "BCR", "currency": "USD", "opening_balance": 1000, "opening_date": "2026-01-01"})
    assert r.status_code == 201, r.text
    acc = r.json()
    r = client.put(f"/banking/accounts/{acc['id']}", headers=conta, json={"name": "BCR USD", "bank": "BCR", "currency": "USD", "opening_balance": 1500, "opening_date": "2026-01-01"})
    assert r.status_code == 200 and r.json()["name"] == "BCR USD"
    for dt, amt in (("2025-12-31", 999), ("2026-02-01", 250), ("2026-03-01", -100)):
        db_session.add(BankStatementLine(tenant_id=auth["tenant"]["id"], bank_account_id=acc["id"], date=date.fromisoformat(dt), description="Mov", amount=amt, status="pendiente", batch="b1", fingerprint=f"fp-{dt}", imported_at=datetime.now(UTC)))
    db_session.commit()
    a = next(x for x in client.get("/banking/accounts", headers=conta).json() if x["id"] == acc["id"])
    assert Decimal(str(a["balance"])) == Decimal("1650.00") and a["balance_date"] == "2026-03-01"  # lo anterior a la apertura no cuenta
    # tarjeta: solo los ultimos 4
    assert client.post(f"/banking/accounts/{acc['id']}/instruments", headers=conta, json={"kind": "tarjeta_debito", "name": "Débito", "last4": "4111111111111111"}).status_code == 422
    t = client.post(f"/banking/accounts/{acc['id']}/instruments", headers=conta, json={"kind": "tarjeta_debito", "name": "Débito", "last4": "1234", "holder": "Gerencia"})
    assert t.status_code == 201 and t.json()["last4"] == "1234"
    # chequera: rango y el siguiente avanza con un gasto pagado con cheque
    assert client.post(f"/banking/accounts/{acc['id']}/instruments", headers=conta, json={"kind": "chequera", "name": "Chequera", "check_from": 200, "check_to": 100}).status_code == 422
    ch = client.post(f"/banking/accounts/{acc['id']}/instruments", headers=conta, json={"kind": "chequera", "name": "Chequera 1", "check_from": 100, "check_to": 150}).json()
    assert ch["next_check"] == 100
    e = client.post(
        "/expenses",
        headers=conta,
        json={"description": "Pago proveedor", "date": date.today().isoformat(), "subtotal": 1000, "tax_rate": 0, "bank_account_id": acc["id"], "bank_instrument_id": ch["id"], "check_number": "105", "payment_method": "cheque"},
    )
    assert e.status_code == 201, e.text
    ins = client.get(f"/banking/accounts/{acc['id']}/instruments", headers=conta).json()
    assert next(i for i in ins if i["id"] == ch["id"])["next_check"] == 106
    assert len(next(x for x in client.get("/banking/accounts", headers=conta).json() if x["id"] == acc["id"])["instruments"]) == 2
    ventas = role_headers(client, db_session, auth["tenant"]["id"], "ventas.banco@ejemplo.com", "ventas")
    assert client.post("/banking/accounts", headers=ventas, json={"name": "X"}).status_code == 403
