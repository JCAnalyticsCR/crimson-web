"""ONVO Pay contra el contrato de su documentacion: llaves por empresa (validadas y nunca devueltas), enlace de pago de
factura -> Checkout -> webhook con X-Webhook-Secret confirmado con la API, consulta al volver (sin webhook), eventos
que no se pudieron confirmar quedan sin consumir, pagos tardios en pedidos cancelados y reembolso desde el pedido."""

import json
from decimal import Decimal

from app.providers.payments import OnvoError, cr_phone, key_mode, key_problem
from tests.test_tienda_checkout import SLUG, _body, _shop


def _invoice(client):
    cust = client.get("/customers").json()["items"][0]
    prod = client.get("/products", params={"q": "domo"}).json()["items"][0]
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": prod["id"], "quantity": 2}]}).json()
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    link = client.post(f"/invoices/{inv['id']}/payment-link").json()
    return inv, link["url"].rsplit("/", 1)[1]


def test_key_helpers():
    assert key_mode("onvo_live_secret_key_abcdefgh123") == "live" and key_mode("sk_test_123") is None
    assert "publicable" in key_problem("onvo_test_publishable_key_abcdefgh", "secret")
    assert "webhook" in key_problem("webhook_secret_abcdefghijkl", "secret")
    assert key_problem("onvo_test_secret_key_abcdefgh", "secret") is None
    assert cr_phone("8888-0000") == "+50688880000" and cr_phone("+506 8888 0000") == "+50688880000" and cr_phone("123") is None


def test_sync_throttle_per_session():
    from app.services import onvo

    onvo._last_sync.clear()
    assert not onvo._throttled("cs_x") and onvo._throttled("cs_x")  # segunda consulta seguida: no llama a ONVO
    assert not onvo._throttled("cs_y")  # otra sesion no se frena


def test_gateway_settings_validate_and_never_return_keys(client, auth, fake_onvo, monkeypatch):
    from app.core.config import settings

    put = lambda **b: client.put("/settings/gateways", json={"provider": "onvo", "active": True, **b})  # noqa: E731
    assert "publicable" in put(secret=fake_onvo.public).json()["detail"]
    assert "webhook" in put(secret=fake_onvo.wh).json()["detail"]
    assert put(secret=fake_onvo.key, webhook_secret=fake_onvo.key).status_code == 422  # llave de API en el campo del webhook
    assert put(secret=fake_onvo.key, client_id="onvo_live_publishable_key_" + "x" * 20).status_code == 422  # modos distintos
    assert put(active=True).status_code == 422  # activar sin llave
    fake_onvo.connect(client)
    g = client.get("/settings/gateways").json()
    dump = json.dumps(g, ensure_ascii=False)
    assert fake_onvo.key not in dump and fake_onvo.wh not in dump
    assert g[0]["mode"] == "test" and g[0]["webhook_secret_set"] and g[0]["secret_mask"].startswith("onvo_test_secret_key_••••")
    st = client.get("/settings/gateways/onvo/status").json()
    assert st["configured"] and st["mode"] == "test" and st["webhook_secret"] and st["source"] == "ajustes"
    assert st["webhook_url"].endswith(f"/webhooks/onvo/{auth['tenant']['id']}") and any("local" in p for p in st["problems"])
    assert client.post("/settings/gateways/onvo/test").json() == {"ok": True, "mode": "test", "detail": "Conectado a ONVO en modo pruebas"}

    def denied(self):
        raise OnvoError(401, "Unauthorized")

    monkeypatch.setattr("app.providers.payments.OnvoAdapter.ping", denied)
    t = client.post("/settings/gateways/onvo/test").json()
    assert not t["ok"] and "401" in t["detail"]
    # en produccion: no se guarda una llave de pruebas y la que ya estaba no cobra
    monkeypatch.setattr(settings, "env", "production")
    assert put(secret=fake_onvo.key).status_code == 422
    st = client.get("/settings/gateways/onvo/status").json()
    assert not st["configured"] and any("producción" in p for p in st["problems"])


def test_invoice_paylink_checkout_webhook_and_sync(client, auth, fake_onvo):
    tid = auth["tenant"]["id"]
    inv, token = _invoice(client)
    pay = f"/public/pay/{token}"
    assert client.get(pay).json()["methods"]["online"]["onvo"] is False
    assert client.post(f"{pay}/onvo").status_code == 503  # sin ONVO conectado
    fake_onvo.connect(client)
    assert client.get(pay).json()["methods"]["online"]["onvo"] is True
    r = client.post(f"{pay}/onvo")
    assert r.status_code == 200 and r.json() == {"checkout_url": "https://checkout.onvopay.com/pay/cs_1"}
    kw = fake_onvo.created[0]
    assert kw["amount_cents"] == int((Decimal(inv["balance"]) * 100).quantize(Decimal(1))) and kw["currency"] == inv["currency"]
    assert kw["metadata"]["invoice_id"] == inv["id"] and kw["redirect_url"].endswith(f"/pagar/{token}?onvo=volver")
    # volver sin pagar no cambia nada
    assert client.post(f"{pay}/onvo/sync").json()["invoice"]["status"] != "pagada"
    # secreto equivocado: 401 y no se aplica
    fake_onvo.pay("cs_1")
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}, secret="webhook_secret_otro_valor_123").status_code == 401
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []
    ok = fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).json()["duplicate"] is True
    # el evento de la intencion (con la metadata copiada) y la consulta al volver no duplican el pago
    fake_onvo.intents["pi_cs_1"]["metadata"] = {"invoice_id": str(inv["id"]), "tenant_id": str(tid)}
    assert fake_onvo.send(client, tid, "payment-intent.succeeded", {"id": "pi_cs_1"}).status_code == 200
    page = client.post(f"{pay}/onvo/sync").json()
    i = client.get(f"/invoices/{inv['id']}").json()
    assert i["status"] == "pagada" and len(i["payments"]) == 1
    assert i["payments"][0]["provider"] == "onvo" and i["payments"][0]["external_ref"] == "pi_cs_1"
    assert page["invoice"]["status"] == "pagada" and page["methods"]["online"]["onvo"] is False
    assert client.post(f"{pay}/onvo").status_code == 409  # ya no hay saldo


def test_sync_applies_without_webhook_and_forged_events_do_nothing(client, auth, fake_onvo):
    tid = auth["tenant"]["id"]
    fake_onvo.connect(client)
    inv, token = _invoice(client)
    client.post(f"/public/pay/{token}/onvo")
    # "pagado" para una sesion que ONVO dice que no esta pagada (falso, o ONVO aun no se actualiza): 503 y sin consumir
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1", "metadata": {"invoice_id": str(inv["id"])}}).status_code == 503
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []
    # ONVO caido al confirmar: 503 y el evento NO se consume (el reintento si se aplica)
    fake_onvo.pay("cs_1")
    fake_onvo.fail_get = 1
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 503
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []
    # sin webhook: el comprador vuelve y la consulta a ONVO aplica el pago
    assert client.post(f"/public/pay/{token}/onvo/sync").json()["invoice"]["status"] == "pagada"
    # el reintento del webhook llega despues: 200 y sin pago doble
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).json() == {"ok": True}
    assert len(client.get(f"/invoices/{inv['id']}").json()["payments"]) == 1


def test_store_card_sync_failed_attempt_and_refund(client, auth, fake_onvo):
    tid = auth["tenant"]["id"]
    fake_onvo.connect(client)
    prods = _shop(client, tarjeta=True)
    o = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta")).json()
    oid = client.get("/store-orders").json()["items"][0]["id"]
    assert fake_onvo.created[0]["customer"]["phone"] == "8888-0000" and fake_onvo.created[0]["metadata"]["order_id"] == oid
    sync = f"{SLUG}/orders/{o['track_token']}/card/sync"
    assert client.post(sync).json()["status"] == "nuevo"
    # un intento rechazado deja nota; la misma intencion reintentada y aprobada si paga
    meta = {"order_id": str(oid), "tenant_id": str(tid)}
    failed = {"id": "pi_cs_1", "metadata": meta, "error": {"code": "declined", "message": "Transaction declined"}}
    assert fake_onvo.send(client, tid, "payment-intent.failed", failed).status_code == 200
    assert "rechazó" in client.get(f"/store-orders/{oid}").json()["history"][-1]["note"]
    fake_onvo.pay("cs_1")
    assert client.post(sync).json()["status"] == "pagado"  # confirmado al volver, sin webhook
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 200
    det = client.get(f"/store-orders/{oid}").json()
    assert det["payment_ref"] == "pi_cs_1" and det["onvo_refundable"]
    assert [h["status"] for h in det["history"] if h.get("kind") != "nota"].count("pagado") == 1
    # reembolso parcial por ONVO desde el pedido
    r = client.post(f"/store-orders/{oid}/status", json={"status": "reembolsado", "refund_via_onvo": True, "refund_amount": "1000", "note": "Cliente devolvio"})
    assert r.status_code == 200, r.text
    assert fake_onvo.refunds == [{"id": "rf_1", "paymentIntentId": "pi_cs_1", "amount": 100000, "status": "succeeded"}]
    det = r.json()
    assert det["status"] == "reembolsado" and "Reembolso ONVO rf_1" in det["history"][-1]["note"] and not det["onvo_refundable"]
    assert sum(1 for h in det["history"] if (h.get("note") or "").startswith("Reembolso ONVO rf_1")) == 1  # anotado antes del cambio de estado


def test_late_payment_on_cancelled_order_is_flagged_once(client, auth, fake_onvo):
    tid = auth["tenant"]["id"]
    fake_onvo.connect(client)
    prods = _shop(client, tarjeta=True)
    client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta"))
    oid = client.get("/store-orders").json()["items"][0]["id"]
    assert client.post(f"/store-orders/{oid}/status", json={"status": "cancelado", "note": "Sin stock"}).status_code == 200
    fake_onvo.pay("cs_1", intent_meta={"order_id": str(oid), "tenant_id": str(tid)})
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 200
    assert fake_onvo.send(client, tid, "payment-intent.succeeded", {"id": "pi_cs_1"}).status_code == 200
    det = client.get(f"/store-orders/{oid}").json()
    notes = [h["note"] for h in det["history"] if "devolver el dinero" in (h.get("note") or "")]
    assert det["status"] == "cancelado" and len(notes) == 1 and "pi_cs_1" in notes[0]
