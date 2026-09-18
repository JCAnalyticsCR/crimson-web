"""Fase 1.2-3: impresion, envio, inventario, factura electronica sandbox + NC, reportes/Excel, ajustes, recurrencias, webhook ONVO."""

import json
from decimal import Decimal

from app.core.crypto import encrypt
from app.providers.payments import OnvoAdapter


def _quote_to_invoice(client):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    body = {"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 2}]}
    q = client.post("/quotes", json=body).json()
    return client.post(f"/quotes/{q['id']}/convert").json(), prods[0]


def test_html_pdf_and_email(client, auth):
    inv, _ = _quote_to_invoice(client)
    html = client.get(f"/invoices/{inv['id']}/html")
    assert html.status_code == 200 and inv["number"] in html.text and "Crimson Consulting" in html.text
    pdf = client.get(f"/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200  # PDF real o HTML de respaldo segun WeasyPrint
    r = client.post(f"/invoices/{inv['id']}/email", json={"message": "Gracias por su compra"})
    assert r.status_code == 200 and r.json()["status"] in ("simulado", "enviado")
    assert client.get(f"/invoices/{inv['id']}").json()["status"] == "enviada"
    out = client.get("/settings/outbox").json()
    assert out and out[0]["entity"] == "invoice"


def test_inventory_deducts_on_sale_and_restocks_on_void(client, auth):
    inv, prod = _quote_to_invoice(client)
    lv = [x for x in client.get("/stock").json()["levels"] if x["product_id"] == prod["id"]]
    assert lv and Decimal(str(lv[0]["quantity"])) == Decimal(4)  # 6 iniciales - 2 vendidas
    client.post(f"/invoices/{inv['id']}/void")
    lv = [x for x in client.get("/stock").json()["levels"] if x["product_id"] == prod["id"]]
    assert Decimal(str(lv[0]["quantity"])) == Decimal(6)
    # transferencia entre bodegas y stock bajo
    whs = client.get("/warehouses").json()
    r = client.post("/stock/transfer", json={"product_id": prod["id"], "from_id": whs[0]["id"], "to_id": whs[1]["id"], "quantity": 5})
    assert r.status_code == 201
    low = client.get("/stock").json()["low"]
    assert any(x["product_id"] == prod["id"] for x in low) is False  # 1 + 5 = 6 total > min 2
    rep = client.get("/reports/inventario").json()
    assert rep["rows"] and rep["columns"][0] == "Código"


def test_einvoice_sandbox_and_credit_note(client, auth):
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    inv, _ = _quote_to_invoice(client)
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "aceptada" and len(r.json()["clave"]) == 50
    assert client.get(f"/invoices/{inv['id']}").json()["einvoice_status"] == "aceptada"
    assert client.post(f"/invoices/{inv['id']}/emit").status_code == 409
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 409  # aceptada: solo NC
    docs = client.get(f"/invoices/{inv['id']}/xml").json()
    assert len(docs) == 1 and docs[0]["has_document"] and docs[0]["has_response"]
    xml = client.get(f"/invoices/{inv['id']}/xml/{docs[0]['id']}/document")
    assert xml.status_code == 200 and "<Clave>" in xml.text and "FacturaElectronica" in xml.text
    nc = client.post(f"/invoices/{inv['id']}/credit-note", json={"reason": "Error en cantidad"})
    assert nc.status_code == 200 and nc.json()["status"] == "aceptada"
    assert client.get(f"/invoices/{inv['id']}").json()["status"] == "anulada"
    assert len(client.get(f"/invoices/{inv['id']}/xml").json()) == 2


def test_reports_json_and_xlsx(client, auth):
    inv, _ = _quote_to_invoice(client)
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "efectivo", "kind": "captura", "amount": inv["total"]})
    cat = client.post("/expense-categories", json={"name": "Combustible test"}).json()
    e = client.post("/expenses", json={"category_id": cat["id"], "description": "Gasolina", "date": inv["issue_date"], "subtotal": 10000, "tax_rate": 13})
    assert e.status_code == 201 and Decimal(e.json()["total"]) == Decimal("11300")
    for key in ("facturacion", "pendientes", "transacciones", "cierre", "impuesto", "iva", "gastos", "resultados", "productos"):
        r = client.get(f"/reports/{key}")
        assert r.status_code == 200 and r.json()["columns"], key
    iva = client.get("/reports/iva").json()
    assert Decimal(str(iva["totals"]["a_pagar"])) == Decimal(inv["tax_total"]) - Decimal("1300")
    x = client.get("/reports/facturacion", params={"format": "xlsx"})
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxmlformats") and len(x.content) > 3000


def test_settings_users_invitations(client, auth):
    s = client.put("/settings", json={"quote_valid_days": 20, "bcc": ["conta@crimsonapp.com"], "invoice_footer": "Gracias"}).json()
    assert s["quote_valid_days"] == 20 and s["manual_payment_methods"]
    inv = client.post("/settings/invitations", json={"email": "ventas@crimsonapp.com", "role": "ventas"})
    assert inv.status_code == 201
    token = inv.json()["link"].rsplit("/", 1)[1]
    assert client.post("/settings/invitations/accept", json={"token": token, "full_name": "Vendedor", "password": "corta"}).status_code == 422
    assert client.post("/settings/invitations/accept", json={"token": token, "full_name": "Vendedor", "password": "Ventas-2026-ok"}).status_code == 200
    users = client.get("/settings/users").json()["users"]
    assert any(u["email"] == "ventas@crimsonapp.com" and u["role"] == "ventas" for u in users)
    r = client.post("/auth/login", json={"email": "ventas@crimsonapp.com", "password": "Ventas-2026-ok"})
    assert r.status_code == 200 and r.json()["role"] == "ventas"
    assert client.put("/settings/gateways", json={"provider": "onvo", "client_id": "pk_test", "secret": "sk_test_123", "active": True}).status_code == 200
    g = client.get("/settings/gateways").json()
    assert g[0]["provider"] == "onvo" and "sk_test" not in json.dumps(g)


def test_recurrence_generates_invoice(client, auth):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "mantenimiento"}).json()["items"]
    r = client.post(
        "/recurrences",
        json={
            "name": "Mantenimiento mensual",
            "customer_id": cust["id"],
            "next_date": "2026-09-01",
            "frequency": "mensual",
            "template": {"lines": [{"product_id": prods[0]["id"], "quantity": 1}]},
        },
    )
    assert r.status_code == 201
    run = client.post(f"/recurrences/{r.json()['id']}/run")
    assert run.status_code == 201 and run.json()["number"].startswith("FEC-") and run.json()["next_date"] == "2026-10-01"
    assert client.get(f"/invoices/{run.json()['invoice_id']}").json()["customer_name"] == cust["name"]


def test_onvo_webhook_signature_idempotency_and_payment(client, auth, db_session):
    from app.models import PaymentGatewayConfig

    inv, _ = _quote_to_invoice(client)
    tid = auth["tenant"]["id"]
    db_session.add(PaymentGatewayConfig(tenant_id=tid, provider="onvo", client_id="pk", secret_encrypted=encrypt("whsec_test"), active=True))
    db_session.commit()
    cents = int(Decimal(inv["total"]) * 100)
    ev = {"id": "evt_1", "type": "payment_intent.succeeded", "data": {"id": "pi_1", "amount": cents, "metadata": {"invoice_id": inv["id"]}}}
    body = json.dumps(ev).encode()
    bad = client.post(f"/webhooks/onvo/{tid}", content=body, headers={"Content-Type": "application/json", "Onvo-Signature": "t=1,v1=deadbeef"})
    assert bad.status_code == 401
    sig = OnvoAdapter.sign("whsec_test", body)
    ok = client.post(f"/webhooks/onvo/{tid}", content=body, headers={"Content-Type": "application/json", "Onvo-Signature": sig})
    assert ok.status_code == 200
    dup = client.post(f"/webhooks/onvo/{tid}", content=body, headers={"Content-Type": "application/json", "Onvo-Signature": sig})
    assert dup.json()["duplicate"] is True
    i = client.get(f"/invoices/{inv['id']}").json()
    assert i["status"] == "pagada" and len(i["payments"]) == 1 and i["payments"][0]["provider"] == "onvo"
