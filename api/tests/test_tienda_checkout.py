"""Checkout de la tienda en linea: pasos, montos del servidor, idempotencia, pagos (SINPE/transferencia con comprobante
privado, tarjeta ONVO con webhook idempotente y modo simulado), estados con inventario y avisos, seguimiento publico
sin fuga de pedidos ajenos y gestion interna."""

import json
from decimal import Decimal

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SLUG = "/public/store/crimson"


@pytest.fixture
def inv_calls(monkeypatch):
    """Registra las llamadas al inventario de la tienda (el modulo real lo entrega otro frente)."""
    from app.services import store_inventory

    calls = []

    def hold(db, o, **kw):
        calls.append(("hold_for_order", o.number))
        db.commit()  # la real hace commit (o rollback + 409)
        return {"vence": None}

    for fn in ("release_for_order", "fulfill_for_order", "mark_paid"):
        monkeypatch.setattr(store_inventory, fn, lambda db, o, *a, _fn=fn, **k: calls.append((_fn, o.number)) or 0)
    monkeypatch.setattr(store_inventory, "hold_for_order", hold)
    return calls


def _shop(client, **checkout):
    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    cfg = {
        "methods": {
            "sinpe": {"active": True, "phone": "8888-7777", "holder": "Crimson SA"},
            "transferencia": {"active": True, "iban": "CR05015201001026284066", "bank": "BCR", "holder": "Crimson SA"},
            "tarjeta": {"active": checkout.get("tarjeta", False), "installments": checkout.get("cuotas", 0) > 0, "max_installments": checkout.get("cuotas", 0)},
            "efectivo": {"active": checkout.get("efectivo", True)},
        },
        "auto_invoice": checkout.get("auto_invoice", False),
        "free_shipping_from": checkout.get("free_shipping_from", 0),
        "pickup": {"address": "Oficinas Crimson, Escazú", "hours": "L-V 8-17"},
    }
    r = client.put("/store/checkout", json=cfg)
    assert r.status_code == 200, r.text
    return client.get(f"{SLUG}/products").json()


def _body(prod_id, **kw):
    b = {
        "lines": [{"product_id": prod_id, "quantity": 2}],
        "contact": {"name": "Ana Vega", "email": "Ana@Correo.com", "phone": "8888-0000", "id_type": "fisica", "id_number": "1-1111-0111"},
        "delivery": {"type": "envio", "province": "San José", "canton": "Escazú", "district": "San Rafael", "address": "200 m norte del parque"},
        "shipping_method": "Envío GAM",
        "payment_method": "sinpe",
        "accept_terms": True,
    }
    b.update(kw)
    return b


def test_config_and_quote_with_tax_breakdown(client, auth):
    prods = _shop(client)
    cfg = client.get(f"{SLUG}/checkout-config").json()
    codes = [m["code"] for m in cfg["methods"]]
    assert codes == ["sinpe", "transferencia", "efectivo"]  # tarjeta apagada
    assert "Escazú" in cfg["provinces"]["San José"] and "Guanacaste" in cfg["outside_gam"]["provinces"]
    assert {r["zone"] for r in cfg["shipping_rates"]} == {"retiro", "gam"}
    q = client.post(f"{SLUG}/quote", json=_body(prods[0]["id"])).json()
    assert Decimal(str(q["total"])) == Decimal(str(q["subtotal"])) - Decimal(str(q["discount_total"])) + Decimal(str(q["tax_total"])) + Decimal(
        str(q["shipping"])
    )
    assert q["taxes"] and sum(Decimal(str(t["amount"])) for t in q["taxes"]) == Decimal(str(q["tax_total"]))
    assert any(ln["shipping"] for ln in q["lines"])
    # zona: tarifa GAM a Guanacaste no
    far = _body(prods[0]["id"], delivery={"type": "envio", "province": "Guanacaste", "canton": "Liberia", "address": "Barrio Los Ángeles"})
    assert client.post(f"{SLUG}/quote", json=far).status_code == 422
    # canton que no es de esa provincia
    bad = _body(prods[0]["id"], delivery={"type": "envio", "province": "Heredia", "canton": "Escazú", "address": "casa azul esquinera"})
    assert client.post(f"{SLUG}/quote", json=bad).status_code == 422
    # retiro con tarifa de envio no
    assert client.post(f"{SLUG}/quote", json=_body(prods[0]["id"], delivery={"type": "retiro"})).status_code == 422
    # tarifa inexistente ya no se ignora en silencio
    assert client.post(f"{SLUG}/quote", json=_body(prods[0]["id"], shipping_method="Envío gratis")).status_code == 422


def test_manipulated_amount_rejected(client, auth):
    prods = _shop(client)
    pid = prods[0]["id"]
    # el navegador no manda precios: un campo de precio en la linea se rechaza
    hack = _body(pid, lines=[{"product_id": pid, "quantity": 1, "unit_price": 1}])
    assert client.post(f"{SLUG}/checkout", json=hack).status_code == 422
    assert client.post(f"{SLUG}/checkout", json={**_body(pid), "total": 1}).status_code == 422
    # el total que vio el comprador no coincide con el del servidor -> no se crea el pedido
    r = client.post(f"{SLUG}/checkout", json=_body(pid, expected_total="1.00"))
    assert r.status_code == 409
    assert client.get("/store-orders").json()["items"] == []
    # cantidad absurda
    assert client.post(f"{SLUG}/checkout", json=_body(pid, lines=[{"product_id": pid, "quantity": 100000}])).status_code == 422


def test_sinpe_full_flow_idempotency_proof_and_tracking(client, auth, inv_calls, db_session):
    prods = _shop(client)
    pid = prods[0]["id"]
    q = client.post(f"{SLUG}/quote", json=_body(pid)).json()
    body = _body(pid, idempotency_key="ck-test-0001", expected_total=str(q["total"]))
    r = client.post(f"{SLUG}/checkout", json=body)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "nuevo" and o["payment_kind"] == "sinpe" and o["needs_proof"]
    assert "8888-7777" in o["instructions"] and o["number"] in o["instructions"]
    assert o["email"]["status"] == "simulado"  # sin RESEND_API_KEY: la UI no dice "te enviamos un correo"
    assert Decimal(str(o["total"])) == Decimal(str(q["total"]))
    # doble clic: misma clave -> mismo pedido, sin duplicar
    again = client.post(f"{SLUG}/checkout", json=body)
    assert again.status_code == 200 and again.json()["number"] == o["number"] and again.json()["duplicate"]
    lst = client.get("/store-orders").json()
    assert len(lst["items"]) == 1
    assert inv_calls == [("hold_for_order", o["number"])]
    # cliente creado y ligado (cedula normalizada, correo en minuscula)
    order = lst["items"][0]
    c = client.get(f"/customers/{order['customer_id']}").json()
    assert c["id_number"] == "111110111" and c["email"] == "ana@correo.com"
    # segundo pedido del mismo cliente (otra clave): se liga a la misma ficha
    r2 = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ck-test-0002", contact={**body["contact"], "id_number": "111110111"}))
    assert r2.status_code == 201
    assert client.get("/store-orders").json()["items"][0]["customer_id"] == order["customer_id"]

    # seguimiento con el enlace firmado
    tok = o["track_token"]
    pub = client.get(f"{SLUG}/orders/{tok}").json()
    assert pub["number"] == o["number"] and pub["status_label"] == "Pendiente de pago" and pub["needs_proof"]
    assert "id_number" not in json.dumps(pub["contact"]) and "Ana@" not in json.dumps(pub)  # correo enmascarado
    # buscar por numero + correo; correo ajeno -> misma respuesta que inexistente
    assert client.post(f"{SLUG}/orders/lookup", json={"number": o["number"].lower(), "email": "ana@correo.com"}).json()["token"]
    assert client.post(f"{SLUG}/orders/lookup", json={"number": o["number"], "email": "otra@correo.com"}).status_code == 404
    assert client.post(f"{SLUG}/orders/lookup", json={"number": "ORD-999999", "email": "ana@correo.com"}).status_code == 404
    # token alterado o de otro proposito -> 404
    assert client.get(f"{SLUG}/orders/{tok[:-3]}xyz").status_code == 404

    # comprobante: tipo y tamano validados, privado
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("x.txt", b"hola", "text/plain")}).status_code == 415
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("big.png", PNG + b"0" * (5 * 1024 * 1024), "image/png")}).status_code == 413
    up = client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("sinpe.png", PNG, "image/png")})
    assert up.status_code == 201 and up.json()["status"] == "en_revision"
    det = client.get(f"/store-orders/{order['id']}").json()
    mid = det["proofs"][0]["media_id"]
    from app.models import EmailOutbox, Media

    # la URL publica de multimedia NO sirve el comprobante, ni aparece en la galeria
    m = db_session.get(Media, mid)
    assert m.visibility == "private" and m.module == "sales" and client.get(f"/media/f/{m.key}").status_code in (401, 403, 404)
    assert all(x["id"] != mid for x in client.get("/media").json())
    proof = client.get(f"/store-orders/{order['id']}/proofs/{mid}")
    assert proof.status_code == 200 and proof.content == PNG and "no-store" in proof.headers["cache-control"]
    anon = client.get(f"/store-orders/{order['id']}/proofs/{mid}", headers={"Authorization": ""})
    assert anon.status_code == 401
    # el equipo recibe aviso de pedido nuevo y de comprobante subido
    asuntos = [e.subject for e in db_session.query(EmailOutbox).filter(EmailOutbox.entity == "order", EmailOutbox.entity_id == order["id"])]
    assert any(a.startswith("Pedido web") for a in asuntos) and any(a.startswith("Comprobante subido") for a in asuntos)
    assert any(a.startswith("Recibimos tu pedido") for a in asuntos)
    # un archivo publico identico NO reutiliza el comprobante privado
    pub_up = client.post("/media", files={"file": ("sinpe.png", PNG, "image/png")}).json()
    assert pub_up["id"] != mid
    # gestion: no se envia sin pago confirmado (SINPE)
    st = lambda s, **kw: client.post(f"/store-orders/{order['id']}/status", json={"status": s, **kw})  # noqa: E731
    assert st("enviado").status_code == 409
    paid = st("pagado", payment_ref="SINPE 123456")
    assert paid.status_code == 200 and paid.json()["paid_at"] and paid.json()["email_result"]["status"] == "simulado"
    assert ("mark_paid", o["number"]) in inv_calls  # desde "comprobante en revision" tambien fija la reserva
    assert st("pagado").status_code == 409  # ya esta
    assert st("preparando").status_code == 200
    sent = st("enviado", tracking_number="CR123456789", carrier="Correos de Costa Rica")
    assert sent.status_code == 200 and sent.json()["tracking_number"] == "CR123456789"
    assert ("fulfill_for_order", o["number"]) in inv_calls
    assert st("entregado").status_code == 200
    final = client.get(f"{SLUG}/orders/{tok}").json()
    assert final["status"] == "entregado" and final["tracking_number"] == "CR123456789"
    assert [h["status"] for h in final["history"]] == ["nuevo", "en_revision", "pagado", "preparando", "enviado", "entregado"]
    # reembolso: solo registro
    assert st("reembolsado", refund_amount="999999999").status_code == 422
    rf = st("reembolsado", refund_amount="1000", note="Devolución parcial")
    assert rf.status_code == 200 and rf.json()["refund"]["amount"] == "1000"
    # auditoria de cada transicion y hoja de despacho
    from app.models import AuditLog

    pasos = [
        a.diff.get("to") for a in db_session.query(AuditLog).filter(AuditLog.entity == "order", AuditLog.entity_id == order["id"], AuditLog.action == "status")
    ]
    assert pasos == ["en_revision", "pagado", "preparando", "enviado", "entregado", "reembolsado"]
    pr = client.get(f"/store-orders/{order['id']}/print")
    assert pr.status_code == 200 and "CR123456789" in pr.text and o["number"] in pr.text


def test_tracking_does_not_leak_other_orders(client, auth):
    prods = _shop(client)
    a = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], idempotency_key="ck-leak-000A")).json()
    b = client.post(
        f"{SLUG}/checkout",
        json=_body(prods[0]["id"], idempotency_key="ck-leak-000B", contact={"name": "Luis Mora", "email": "luis@correo.com", "phone": "88887777"}),
    ).json()
    pa = client.get(f"{SLUG}/orders/{a['track_token']}").json()
    assert pa["number"] == a["number"] and "luis" not in json.dumps(pa).lower()
    # con el numero de B y el correo de A no se obtiene nada
    assert client.post(f"{SLUG}/orders/lookup", json={"number": b["number"], "email": "ana@correo.com"}).status_code == 404
    # un token de enlace de pago (otro proposito) no abre pedidos
    from app.core.security import sign_purpose_token

    fake = sign_purpose_token("paylink", {"o": 2, "t": 1}, 1)
    assert client.get(f"{SLUG}/orders/{fake}").status_code == 404
    # comprobante subido a B no se puede pedir colgado de A
    client.post(f"{SLUG}/orders/{b['track_token']}/proof", files={"file": ("b.png", PNG + b"b", "image/png")})
    items = client.get("/store-orders").json()["items"]
    ida = next(x["id"] for x in items if x["number"] == a["number"])
    idb = next(x for x in items if x["number"] == b["number"])
    assert client.get(f"/store-orders/{ida}/proofs/{idb['proofs'][0]['media_id']}").status_code == 404
    # rate limit de la busqueda
    codes = [client.post(f"{SLUG}/orders/lookup", json={"number": "ORD-X", "email": "x@y.com"}).status_code for _ in range(12)]
    assert 429 in codes


def test_cancel_requires_reason_and_releases(client, auth, inv_calls):
    prods = _shop(client)
    o = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="transferencia")).json()
    assert "CR05015201001026284066" in o["instructions"]
    oid = client.get("/store-orders").json()["items"][0]["id"]
    assert client.post(f"/store-orders/{oid}/status", json={"status": "cancelado"}).status_code == 422
    c = client.post(f"/store-orders/{oid}/status", json={"status": "cancelado", "note": "Cliente desistió"})
    assert c.status_code == 200 and c.json()["cancel_reason"] == "Cliente desistió"
    assert ("release_for_order", o["number"]) in inv_calls
    # nunca se pago: no hay reembolso
    assert client.post(f"/store-orders/{oid}/status", json={"status": "reembolsado"}).status_code == 409
    pub = client.get(f"{SLUG}/orders/{o['track_token']}").json()
    assert pub["status"] == "cancelado" and pub["cancel_reason"] == "Cliente desistió" and pub["instructions"] is None


def test_cash_on_delivery_advances_without_payment(client, auth, inv_calls):
    prods = _shop(client)
    cash = client.post(
        f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="efectivo", delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    ).json()
    assert cash["status"] == "nuevo" and cash["expires_at"] is None and cash["instructions"]
    by = {x["number"]: x for x in client.get("/store-orders").json()["items"]}
    # efectivo contra entrega avanza sin pago y se marca pagado al entregar
    cid = by[cash["number"]]["id"]
    assert client.post(f"/store-orders/{cid}/status", json={"status": "preparando"}).status_code == 200
    assert ("mark_paid", cash["number"]) in inv_calls  # contra entrega: la reserva deja de vencer al prepararlo
    assert client.post(f"/store-orders/{cid}/status", json={"status": "listo"}).status_code == 200
    done = client.post(f"/store-orders/{cid}/status", json={"status": "entregado"}).json()
    assert done["paid_at"] and done["status"] == "entregado"


def test_card_simulated_flow_and_not_in_production(client, auth, monkeypatch):
    from app.core.config import settings

    prods = _shop(client, tarjeta=True)
    m = {x["code"]: x for x in client.get(f"{SLUG}/checkout-config").json()["methods"]}
    assert m["tarjeta"]["simulated"]
    o = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta")).json()
    assert o["pay"]["simulated"] and o["pay"]["checkout_url"] is None
    pub = client.get(f"{SLUG}/orders/{o['track_token']}").json()
    assert pub["card"] == {"pending": True, "simulated": True}
    # en produccion no existe el pago de prueba ni se ofrece la tarjeta sin credenciales
    monkeypatch.setattr(settings, "env", "production")
    assert client.post(f"{SLUG}/orders/{o['track_token']}/simulate-payment").status_code == 404
    assert "tarjeta" not in [x["code"] for x in client.get(f"{SLUG}/checkout-config").json()["methods"]]
    monkeypatch.setattr(settings, "env", "local")
    ok = client.post(f"{SLUG}/orders/{o['track_token']}/simulate-payment")
    assert ok.status_code == 200 and ok.json()["status"] == "pagado"
    assert client.post(f"{SLUG}/orders/{o['track_token']}/simulate-payment").status_code == 409


def test_onvo_webhook_marks_order_paid_idempotently(client, auth, fake_onvo):
    tid = auth["tenant"]["id"]
    fake_onvo.connect(client)
    prods = _shop(client, tarjeta=True)
    o = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta")).json()
    assert o["pay"] == {"checkout_url": "https://checkout.onvopay.com/pay/cs_1", "simulated": False}
    oid = client.get("/store-orders").json()["items"][0]["id"]
    # con credenciales reales no hay pago simulado
    assert client.post(f"{SLUG}/orders/{o['track_token']}/simulate-payment").status_code == 404
    cents = int((Decimal(str(o["total"])) * 100).quantize(Decimal(1)))
    assert fake_onvo.created[0]["amount_cents"] == cents and fake_onvo.created[0]["customer"]["email"] == "ana@correo.com"

    fake_onvo.pay("cs_1", amount=cents - 500)  # ONVO cobro menos que el total
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 200
    det = client.get(f"/store-orders/{oid}").json()
    assert det["status"] == "nuevo" and "no cubre el total" in det["history"][-1]["note"]

    o2 = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta", idempotency_key="ck-onvo-2")).json()
    oid2 = client.get("/store-orders").json()["items"][0]["id"]
    fake_onvo.pay("cs_2")
    ev = {"id": "cs_2", "metadata": {"order_id": str(oid2), "tenant_id": str(tid)}}
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", ev).json() == {"ok": True}
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", ev).json() == {"ok": True, "duplicate": True}  # reintento
    # el otro evento del mismo pago (si ONVO copia la metadata a la intencion) no paga dos veces
    fake_onvo.intents["pi_cs_2"]["metadata"] = {"order_id": str(oid2), "tenant_id": str(tid)}
    assert fake_onvo.send(client, tid, "payment-intent.succeeded", {"id": "pi_cs_2"}).status_code == 200
    det = client.get(f"/store-orders/{oid2}").json()
    assert det["status"] == "pagado" and det["payment_ref"] == "pi_cs_2" and o2["number"] == det["number"]
    assert [h["status"] for h in det["history"] if h.get("kind") != "nota"].count("pagado") == 1
    # pedido de otra empresa en metadata: no se toca
    fake_onvo.sessions["cs_1"]["metadata"]["tenant_id"] = str(tid + 99)
    fake_onvo.pay("cs_1")
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1", "x": 1} | {"id": "cs_1"}).status_code == 200
    assert client.get(f"/store-orders/{oid}").json()["status"] == "nuevo"

def test_honeypot_terms_dashboard_and_invoice_with_payment(client, auth):
    prods = _shop(client)
    pid = prods[0]["id"]
    assert client.post(f"{SLUG}/checkout", json=_body(pid, website="http://spam")).json() == {"ok": True, "number": None}
    assert client.get("/store-orders").json()["items"] == []
    client.put("/store", json={"legal": {"terms": "Términos de venta", "privacy": ""}})
    assert client.post(f"{SLUG}/checkout", json=_body(pid, accept_terms=False)).status_code == 422
    assert client.get(f"{SLUG}/checkout-config").json()["terms_required"]
    # juridica sin razon social
    jur = {"name": "Ana", "email": "a@b.com", "phone": "88880000", "id_type": "juridica", "id_number": "3-101-123456"}
    assert client.post(f"{SLUG}/checkout", json=_body(pid, contact=jur)).status_code == 422
    assert client.post(f"{SLUG}/checkout", json=_body(pid, contact={**jur, "id_number": "123"})).status_code == 422
    o = client.post(f"{SLUG}/checkout", json=_body(pid, contact={**jur, "company": "Ana SA"})).json()
    oid = client.get("/store-orders").json()["items"][0]["id"]
    tok = o["track_token"]
    client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("c.pdf", b"%PDF-1.4 comprobante", "application/pdf")})
    dash = client.get("/dashboard").json()["acciones_pendientes"]
    assert dash["pedidos_web"] == 1
    assert len(client.get("/store-orders", params={"pendiente": "por_atender"}).json()["items"]) == 1
    # rechazar comprobante -> vuelve a pendiente con aviso
    back = client.post(f"/store-orders/{oid}/status", json={"status": "nuevo", "note": "El monto no coincide"})
    assert back.status_code == 200 and back.json()["status"] == "nuevo"
    client.post(f"/store-orders/{oid}/status", json={"status": "pagado"})
    # factura electronica a nombre de la empresa con el cobro aplicado
    inv = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True})
    assert inv.status_code == 201, inv.text
    i = client.get(f"/invoices/{inv.json()['invoice_id']}").json()
    assert Decimal(str(i["balance"])) == 0 and Decimal(str(i["total"])) == Decimal(str(o["total"]))
    assert client.get(f"/store-orders/{oid}").json()["status"] == "pagado"  # facturar no mueve el estado


def test_auto_invoice_on_payment_sandbox_and_no_double_stock(client, auth, db_session, monkeypatch):
    from app.models import EmailOutbox, Invoice
    from app.services import inventory as invsvc
    from app.services import store_inventory

    client.put("/settings", json={"einvoice_provider": "sandbox"})
    prods = _shop(client, auto_invoice=True)
    o = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"])).json()
    oid = client.get("/store-orders").json()["items"][0]["id"]
    # el inventario de la tienda ya reservo: facturar no descuenta otra vez
    deducted, legacy = [], []
    monkeypatch.setattr(store_inventory, "deduct_for_order_invoice", lambda db, o, inv, uid=None: deducted.append((o.number, inv.number)))
    monkeypatch.setattr(invsvc, "deduct_for_invoice", lambda *a, **k: legacy.append(a))
    paid = client.post(f"/store-orders/{oid}/status", json={"status": "pagado", "payment_ref": "SINPE 999"}).json()
    # facturar el pedido pasa por la capa de la tienda (descuenta solo lo que falte), nunca por el descuento viejo
    assert paid["invoice_id"] and len(deducted) == 1 and deducted[0][0] == o["number"] and legacy == []
    inv = db_session.get(Invoice, paid["invoice_id"])
    # venta web sin vendedor asignado: no comisiona; la "tecleo" quien confirmo el pago (la ve en sus Facturas, 21/10 item 24)
    me = client.get("/auth/me").json()["user"]["id"]
    assert inv.doc_type == "FE" and inv.einvoice_status == "aceptada" and inv.seller_id is None and inv.created_by == me
    assert inv.balance == 0 and abs(float(inv.total) - float(o["total"])) < 0.01
    notas = [h["note"] for h in paid["history"] if h.get("kind") == "nota"]
    assert any("SANDBOX" in n and inv.number in n for n in notas)
    # sin Hacienda en produccion el comprobante no tiene validez fiscal: nunca se le manda al cliente como factura
    assert any("no se envió al cliente" in n for n in notas)
    assert db_session.query(EmailOutbox).filter(EmailOutbox.entity == "invoice", EmailOutbox.entity_id == inv.id).count() == 0
    # sin proveedor fiscal: el comprobante se crea pero se dice que queda sin emitir
    client.put("/settings", json={"einvoice_provider": "none"})
    o2 = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], contact={"name": "Luis", "email": "l@correo.com", "phone": "88880001"})).json()
    oid2 = next(x["id"] for x in client.get("/store-orders").json()["items"] if x["number"] == o2["number"])
    p2 = client.post(f"/store-orders/{oid2}/status", json={"status": "pagado"}).json()
    assert p2["invoice_id"] and any("sin emitir" in h["note"] for h in p2["history"] if h.get("kind") == "nota")
    assert db_session.get(Invoice, p2["invoice_id"]).doc_type == "TE"  # sin cedula: tiquete


def test_free_shipping_usd_coherence_and_stock(client, auth, monkeypatch):
    from app.services import store_inventory

    prods = _shop(client, free_shipping_from=1)  # 1 colon: cualquier pedido califica
    pid = prods[0]["id"]
    q = client.post(f"{SLUG}/quote", json=_body(pid)).json()
    assert Decimal(str(q["shipping"])) == 0 and q["free_shipping"]
    assert not any(ln["shipping"] for ln in q["lines"])
    # retiro nunca cobra ni cuenta como envio gratis
    r = client.post(f"{SLUG}/quote", json=_body(pid, delivery={"type": "retiro"}, shipping_method="Retiro en tienda")).json()
    assert not r["free_shipping"]
    # tienda en dolares sin envio gratis: total = subtotal - descuento + IVA + envio, todo en USD
    _shop(client, free_shipping_from=0)
    client.put("/store", json={"currency": "USD"})
    u = client.post(f"{SLUG}/quote", json=_body(pid)).json()
    assert u["currency"] == "USD" and Decimal(str(u["shipping"])) > 0
    tot = Decimal(str(u["subtotal"])) - Decimal(str(u["discount_total"])) + Decimal(str(u["tax_total"])) + Decimal(str(u["shipping"]))
    assert abs(tot - Decimal(str(u["total"]))) <= Decimal("0.01")
    assert sum(Decimal(str(x["amount"])) for x in u["taxes"]) == Decimal(str(u["tax_total"]))
    ou = client.post(f"{SLUG}/checkout", json=_body(pid, expected_total=str(u["total"]))).json()
    assert ou["currency"] == "USD" and "$" in ou["instructions"]
    # si la reserva atomica ya no alcanza (409 de la capa de inventario), no queda pedido a medias
    from fastapi import HTTPException

    def no_stock(db, o, **kw):
        db.rollback()
        raise HTTPException(409, "Ya no hay suficiente: quedan 0")

    monkeypatch.setattr(store_inventory, "availability", lambda db, t, ids, public=True: {})
    monkeypatch.setattr(store_inventory, "hold_for_order", no_stock)
    antes = len(client.get("/store-orders").json()["items"])
    r = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ck-sin-stock-01"))
    assert r.status_code == 409 and len(client.get("/store-orders").json()["items"]) == antes


def test_card_installments_preference_sent_to_onvo(client, auth, fake_onvo):
    fake_onvo.connect(client)
    prods = _shop(client, tarjeta=True, cuotas=6)
    o = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta")).json()
    seen = fake_onvo.created[0]
    assert seen["metadata"]["installments"] == 6 and seen["metadata"]["order_number"] == o["number"]
    assert seen["amount_cents"] == int((Decimal(str(o["total"])) * 100).quantize(Decimal(1))) and "pedido=" in seen["redirect_url"]