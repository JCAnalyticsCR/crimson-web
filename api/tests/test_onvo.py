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


def test_llave_ilegible_tras_rotar_se_avisa_y_la_nueva_queda_en_fiscal_keys(client, auth, fake_onvo, db_session, monkeypatch):
    """08/11: el JWT_SECRET se roto el 21/10 y las llaves de ONVO (cifrado heredado) quedaron ilegibles: la tarjeta se
    apago sin aviso. Ahora el estado lo dice, «Probar» lo explica, y la llave nueva se guarda con FISCAL_KEYS."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from app.models import PaymentGatewayConfig
    from app.services import onvo

    fake_onvo.connect(client)
    tid = auth["tenant"]["id"]
    g = db_session.query(PaymentGatewayConfig).filter_by(tenant_id=tid, provider="onvo").one()
    assert g.secret_encrypted.startswith("v1.") and g.webhook_secret_encrypted.startswith("v1.")  # llavero nuevo
    # simula lo de produccion: tokens heredados que ya no descifran (otro JWT_SECRET)
    viejo = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"tenant-secrets:JWT_SECRET-anterior-a-la-rotacion").digest()))
    g.secret_encrypted = viejo.encrypt(fake_onvo.key.encode()).decode()
    g.webhook_secret_encrypted = viejo.encrypt(fake_onvo.wh.encode()).decode()
    db_session.commit()
    st = client.get("/settings/gateways/onvo/status").json()
    assert not st["configured"] and st["unreadable"] == {"secret": True, "webhook": True}
    assert any("ya no se puede leer" in p for p in st["problems"])
    assert "ya no se puede leer" in client.post("/settings/gateways/onvo/test").json()["detail"]
    db_session.expire_all()
    assert onvo.secret_key(db_session, tid) is None  # la tarjeta no se ofrece (nada simulado)
    # se vuelve a cargar: queda en FISCAL_KEYS y se lee
    fake_onvo.connect(client)
    st = client.get("/settings/gateways/onvo/status").json()
    assert st["configured"] and st["unreadable"] == {"secret": False, "webhook": False}


# ---------------------------------------------------------------- formato firmado de ONVO (09/11)
WHSEC = "whsec_" + "s" * 32  # ficticio


def _signed(client, tid, typ, obj, *, evt="evt_1", mode="test", secret=WHSEC, ts=None, body=None):
    """Aviso con el formato firmado: X-Onvo-Signature t=..,v1=HMAC-SHA256(secreto, "t.cuerpo crudo")."""
    import hashlib
    import hmac
    import time

    raw = body if body is not None else json.dumps({"id": evt, "object": "event", "mode": mode, "type": typ, "data": {"object": obj}}).encode()
    t = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(secret.encode(), t.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return client.post(f"/webhooks/onvo/{tid}", content=raw, headers={"Content-Type": "application/json; charset=utf-8", "X-Onvo-Signature": f"t={t},v1={sig}"})


def test_formato_firmado_prueba_de_conexion_pago_duplicado_y_modo(client, auth, fake_onvo):
    """El endpoint que crea ONVO hoy es firmado (whsec_ + X-Onvo-Signature): antes daba 401 a todo, incluida la prueba
    del panel. Ahora: la prueba recibe 200 sin tocar pagos; el pago se aplica una vez (idempotente por evt_); firma
    mala, vencida o de otro secreto -> 401; evento de otro modo -> 200 sin aplicar."""
    import time

    tid = auth["tenant"]["id"]
    inv, token = _invoice(client)
    fake_onvo.connect(client, webhook_secret=WHSEC)
    st = client.get("/settings/gateways/onvo/status").json()
    assert st["webhook_format"] == "firmado" and st["last_webhook"] is None
    # «Probar conexion» del panel de ONVO
    r = _signed(client, tid, "webhook_endpoint.test", {"object": "webhook_endpoint", "id": "we_1"}, evt="evt_test_1")
    assert r.status_code == 200 and r.json()["test"] is True
    st = client.get("/settings/gateways/onvo/status").json()
    assert st["last_webhook"]["type"] == "webhook_endpoint.test"
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []
    # firma mala / vencida / otro secreto: 401 y nada se aplica
    client.post(f"/public/pay/{token}/onvo")
    fake_onvo.pay("cs_1")
    obj = {"object": "checkout_session", "id": "cs_1"}
    assert _signed(client, tid, "checkout_session.succeeded", obj, secret="whsec_" + "x" * 32).status_code == 401
    assert _signed(client, tid, "checkout_session.succeeded", obj, ts=time.time() - 400).status_code == 401
    raw = json.dumps({"id": "evt_2", "object": "event", "mode": "test", "type": "checkout_session.succeeded", "data": {"object": obj}}).encode()
    import hashlib
    import hmac

    t = str(int(time.time()))
    sig = hmac.new(WHSEC.encode(), t.encode() + b"." + raw, hashlib.sha256).hexdigest()
    tampered = raw.replace(b'"cs_1"', b'"cs_9"')  # el cuerpo cambiado invalida la firma
    assert client.post(f"/webhooks/onvo/{tid}", content=tampered, headers={"X-Onvo-Signature": f"t={t},v1={sig}"}).status_code == 401
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []
    # otro modo (live contra la llave de pruebas): 200 sin aplicar
    r = _signed(client, tid, "checkout_session.succeeded", obj, evt="evt_live", mode="live")
    assert r.status_code == 200 and r.json()["ignored"] == "modo distinto"
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []
    # el bueno se aplica (confirmado con la API de ONVO) y el mismo evt_ no duplica
    ok = _signed(client, tid, "checkout_session.succeeded", obj, evt="evt_2")
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert _signed(client, tid, "checkout_session.succeeded", obj, evt="evt_2").json()["duplicate"] is True
    i = client.get(f"/invoices/{inv['id']}").json()
    assert i["status"] == "pagada" and len(i["payments"]) == 1


def test_firma_con_dos_v1_durante_la_rotacion_del_secreto():
    import hashlib
    import hmac
    import time

    from app.providers.payments import OnvoAdapter

    body = b'{"id":"evt_9"}'
    t = str(int(time.time()))
    good = hmac.new(WHSEC.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert OnvoAdapter.verify_signature(WHSEC, f"t={t},v1={'0' * 64},v1={good}", body)
    assert not OnvoAdapter.verify_signature(WHSEC, f"t={t}", body)
    assert not OnvoAdapter.verify_signature(None, f"t={t},v1={good}", body)
    assert not OnvoAdapter.verify_signature(WHSEC, f"t=abc,v1={good}", body)
