"""Fase 4-5: Mi Tienda (personalizacion, bloques, catalogo publico, carrito, checkout), ordenes -> tiquete,
cupones, recepcion de XML de compras, buscador global y API publica con credenciales + checkout JWT."""

import time
from decimal import Decimal

import jwt


def _publish_store(client, kind="tienda"):
    return client.put("/store", json={"kind": kind, "published": True, "whatsapp": "+50688889892", "tagline": "Seguridad y redes"}).json()


def test_store_publish_pages_and_public_catalog(client, auth):
    st = _publish_store(client, "catalogo")
    assert st["published"] and st["slug"] == "crimson" and st["primary"] == "#e2233a"
    pg = client.post(
        "/store/pages",
        json={
            "slug": "inicio",
            "title": "Inicio",
            "published": True,
            "blocks": [
                {
                    "type": "hero",
                    "title": "Tecnología que protege",
                    "text": "Videovigilancia, redes y accesos",
                    "cta": {"label": "Ver catálogo", "to": "/productos"},
                },
                {"type": "products", "title": "Destacados", "limit": 4},
            ],
        },
    )
    assert pg.status_code == 201 and len(pg.json()["blocks"]) == 2
    assert client.post("/store/pages", json={"slug": "inicio", "title": "Otra"}).status_code == 409
    assert client.post("/store/pages", json={"slug": "mala", "title": "X", "blocks": [{"type": "no-existe"}]}).status_code == 422

    home = client.get("/public/store/crimson")
    assert home.status_code == 200
    assert home.json()["store"]["name"] == "Crimson Consulting" and home.json()["nav"][0]["slug"] == "inicio"
    assert client.get("/public/store/crimson/pages/inicio").json()["blocks"][0]["type"] == "hero"
    prods = client.get("/public/store/crimson/products").json()
    assert prods and all(p["item_type"] == "producto" for p in prods)  # solo los marcados "mostrar en web"
    one = client.get(f"/public/store/crimson/products/{prods[0]['id']}").json()
    assert one["id"] == prods[0]["id"] and "related" in one
    # catálogo no permite checkout
    assert (
        client.post(
            "/public/store/crimson/checkout",
            json={"lines": [{"product_id": prods[0]["id"], "quantity": 1}], "contact": {"name": "X", "email": "x@crimsonapp.com"}},
        ).status_code
        == 409
    )


def test_store_checkout_coupon_and_order_to_ticket(client, auth):
    _publish_store(client, "tienda")
    client.post("/coupons", json={"code": "bienvenida", "kind": "percent", "value": 10})
    prods = client.get("/public/store/crimson/products").json()
    body = {
        "lines": [{"product_id": prods[0]["id"], "quantity": 2}],
        "contact": {"name": "Ana Vega", "email": "ana@crimsonapp.com", "phone": "+50688880000"},
        "shipping_method": "Envío GAM",
        "payment_method": "SINPE Móvil",
        "coupon_code": "BIENVENIDA",
    }
    q = client.post("/public/store/crimson/quote", json=body).json()
    assert Decimal(q["shipping"]) == Decimal(3500) and Decimal(q["discount_total"]) > 0
    assert Decimal(q["total"]) == Decimal(q["subtotal"]) - Decimal(q["discount_total"]) + Decimal(q["tax_total"]) + Decimal(q["shipping"])
    assert client.post("/public/store/crimson/quote", json={**body, "coupon_code": "NOEXISTE"}).status_code == 422

    order = client.post("/public/store/crimson/checkout", json=body)
    assert order.status_code == 201 and order.json()["number"].startswith("ORD-")
    assert order.json()["instructions"]  # instrucciones del método manual
    o = client.get("/orders").json()[0]
    assert o["status"] == "nuevo" and o["channel"] == "tienda" and o["contact"]["name"] == "Ana Vega"
    assert client.get("/coupons").json()[0]["uses"] == 1

    te = client.post(f"/orders/{o['id']}/invoice", params={"doc_type": "TE"})
    assert te.status_code == 201 and te.json()["number"].startswith("TE-")
    inv = client.get(f"/invoices/{te.json()['invoice_id']}").json()
    assert inv["doc_type"] == "TE" and any("Envio" in ln["name"] for ln in inv["lines"])
    assert Decimal(inv["total"]) == Decimal(q["total"])
    assert client.post(f"/orders/{o['id']}/invoice").status_code == 409  # ya tiene comprobante
    assert client.patch(f"/orders/{o['id']}", json={"status": "enviado"}).json()["status"] == "enviado"


def test_reception_of_supplier_xml(client, auth):
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    xml = (
        '<?xml version="1.0"?><FacturaElectronica><Clave>50601012600310200000000100001010000000123456789012</Clave>'
        "<NumeroConsecutivo>00100001010000000123</NumeroConsecutivo><FechaEmision>2026-09-10T10:00:00</FechaEmision>"
        "<Emisor><Nombre>Distribuidora Tech SA</Nombre><Identificacion><Numero>3-101-999888</Numero></Identificacion></Emisor>"
        "<ResumenFactura><CodigoMoneda>CRC</CodigoMoneda><TotalVenta>100000</TotalVenta><TotalImpuesto>13000</TotalImpuesto>"
        "<TotalComprobante>113000</TotalComprobante></ResumenFactura></FacturaElectronica>"
    )
    r = client.post("/reception/upload", files={"file": ("factura.xml", xml, "application/xml")})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["issuer_name"] == "Distribuidora Tech SA" and Decimal(d["total"]) == Decimal(113000) and d["action"] is None
    assert client.post("/reception/upload", files={"file": ("dup.xml", xml, "application/xml")}).status_code == 409
    assert client.post("/reception/upload", files={"file": ("mal.xml", "<xml/>", "application/xml")}).status_code == 422

    resp = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    assert resp.status_code == 200 and resp.json()["hacienda_status"] == "aprobado" and resp.json()["expense_id"]
    assert client.post(f"/reception/{d['id']}/respond", json={"action": "rechazada"}).status_code == 409
    x = client.get(f"/reception/{d['id']}/xml/response")
    assert x.status_code == 200 and "MensajeReceptor" in x.text
    gastos = client.get("/expenses").json()
    assert any(g["reference"] == d["clave"] and g["iva_credit"] == "credito" for g in gastos)
    iva = client.get("/reports/iva").json()
    assert Decimal(str(iva["totals"]["a_pagar"])) == Decimal(-13000)  # solo crédito, sin ventas


def test_global_search(client, auth):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "PTZ"}).json()["items"]
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]}).json()
    res = client.get("/search", params={"q": q["number"]}).json()
    assert any(r["kind"] == "cotización" and r["title"] == q["number"] for r in res)
    res = client.get("/search", params={"q": "PTZ"}).json()
    assert any(r["kind"] == "producto" for r in res)
    res = client.get("/search", params={"q": cust["name"][:8]}).json()
    assert any(r["kind"] == "cliente" for r in res)


def test_public_api_credentials_and_checkout_jwt(client, auth):
    cred = client.post("/settings/api-credentials", json={"name": "Integración web"})
    assert cred.status_code == 201
    kid, secret = cred.json()["kid"], cred.json()["secret"]
    assert kid.startswith("pk_") and secret.startswith("sk_")
    assert client.get("/settings/api-credentials").json()[0]["kid"] == kid

    h = {"X-Api-Key": kid, "X-Api-Secret": secret}
    assert client.get("/v1/customers").status_code == 401
    assert client.get("/v1/customers", headers={"X-Api-Key": kid, "X-Api-Secret": "sk_malo"}).status_code == 401
    assert client.get("/v1/customers", headers=h).status_code == 200
    assert client.get("/v1/products", headers=h).json()
    assert isinstance(client.get("/v1/inventory", headers=h).json(), list)

    c = client.post(
        "/v1/customers", headers=h, json={"name": "Cliente API", "id_type": "juridica", "id_number": "3-101-555444", "email": "api@crimsonapp.com"}
    ).json()
    i = client.post(
        "/v1/invoices", headers=h, json={"customer_id": c["id"], "lines": [{"name": "Servicio API", "quantity": 1, "unit_price": 50000, "tax_rate": 13}]}
    )
    assert i.status_code == 201 and Decimal(i.json()["total"]) == Decimal("56500.00000")
    link = client.post(f"/v1/payment-links/{i.json()['id']}", headers=h)
    assert link.status_code == 200 and link.json()["url"].startswith("http")

    now = int(time.time())
    token = jwt.encode(
        {"amount": 25000, "currency": "CRC", "custom_reference": "PED-9001", "description": "Pedido web", "iat": now, "nbf": now - 5, "exp": now + 600},
        secret,
        algorithm="HS256",
        headers={"kid": kid},
    )
    r = client.post("/v1/checkout", json={"token": token})
    assert r.status_code == 201 or r.status_code == 200
    assert r.json()["checkout_url"].startswith("http") and r.json()["number"].startswith("TE-")
    bad = jwt.encode({"amount": 1, "currency": "CRC", "custom_reference": "X", "exp": now + 60}, "otro-secreto", algorithm="HS256", headers={"kid": kid})
    assert client.post("/v1/checkout", json={"token": bad}).status_code == 401
    client.delete(f"/settings/api-credentials/{cred.json()['id']}")
    assert client.get("/v1/customers", headers=h).status_code == 401
