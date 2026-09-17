"""Eje 1 del plan: cliente -> producto -> cotizacion -> factura -> pago -> enlace -> dashboard."""

from decimal import Decimal

from tests.conftest import ADMIN


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_login_bad_password_and_lockout(client):
    for _ in range(6):
        r = client.post("/auth/login", json={"email": ADMIN[0], "password": "mala-mala-1"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 423  # bloqueada por intentos


def test_protected_requires_token(client):
    assert client.get("/customers").status_code == 401


def test_refresh_rotation(client, auth):
    r1 = client.post("/auth/refresh")
    assert r1.status_code == 200 and r1.json()["access_token"]
    # el cookie viejo ya fue reemplazado por el nuevo en el jar del cliente; un segundo refresh sigue funcionando
    r2 = client.post("/auth/refresh")
    assert r2.status_code == 200


def test_sales_flow(client, auth):
    assert auth["tenant"]["slug"] == "crimson" and auth["role"] == "admin"
    customers = client.get("/customers").json()["items"]
    products = client.get("/products", params={"q": "PTZ"}).json()["items"]
    assert customers and products
    cust, ptz = customers[0], products[0]

    body = {
        "customer_id": cust["id"],
        "currency": "CRC",
        "discount_type": "percent",
        "discount_value": 5,
        "external_notes": "Incluye instalacion.",
        "internal_notes": "Margen ok",
        "activity_code": "6202.0",
        "lines": [
            {"product_id": ptz["id"], "quantity": 2},
            {"name": "Instalacion", "quantity": 2, "unit_price": 25000, "tax_rate": 13},
        ],
    }
    prev = client.post("/documents/preview", json=body).json()
    q = client.post("/quotes", json=body)
    assert q.status_code == 201, q.text
    q = q.json()
    assert q["number"] == "COT-000001" and q["status"] == "creado" and q["customer_name"] == cust["name"]
    assert q["lines"][0]["name"] == ptz["name"] and Decimal(q["lines"][0]["unit_price"]) == Decimal(str(ptz["price"]))
    assert Decimal(q["total"]) == Decimal(prev["total"])
    gross = Decimal(2) * Decimal(str(ptz["price"])) + Decimal(50000)
    assert Decimal(q["subtotal"]) == gross
    assert Decimal(q["total"]) == (gross * Decimal("0.95") * Decimal("1.13")).quantize(Decimal("0.00001"))

    # duplicar y anular
    dup = client.post(f"/quotes/{q['id']}/duplicate").json()
    assert dup["number"] == "COT-000002"
    assert client.post(f"/quotes/{dup['id']}/void").json()["status"] == "anulada"

    # convertir a factura conserva lineas y totales
    inv = client.post(f"/quotes/{q['id']}/convert")
    assert inv.status_code == 201, inv.text
    inv = inv.json()
    assert inv["number"] == "FEC-000001" and inv["consecutive"] == "00100001010000000001"
    assert Decimal(inv["total"]) == Decimal(q["total"]) and Decimal(inv["balance"]) == Decimal(q["total"])
    assert client.get(f"/quotes/{q['id']}").json()["status"] == "convertida"
    assert client.post(f"/quotes/{q['id']}/convert").status_code == 409

    # pago parcial -> parcial; pago total -> pagada
    half = (Decimal(inv["total"]) / 2).quantize(Decimal("0.01"))
    r = client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "kind": "captura", "amount": str(half), "external_ref": "SINPE-123"})
    assert r.status_code == 201 and r.json()["status"] == "parcial"
    r = client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "kind": "captura", "amount": str(Decimal(inv["total"]) - half)})
    assert r.json()["status"] == "pagada" and Decimal(r.json()["balance"]) == 0
    assert client.put(f"/invoices/{inv['id']}", json=body).status_code == 409  # con pagos no se edita

    # enlace de pago publico
    link = client.post(f"/invoices/{inv['id']}/payment-link").json()
    assert link["url"].startswith("http") and link["whatsapp_url"].startswith("https://wa.me/?text=")
    token = link["url"].rsplit("/", 1)[1]
    pub = client.get(f"/public/pay/{token}")
    assert pub.status_code == 200 and pub.json()["invoice"]["number"] == "FEC-000001"

    # dashboard
    dash = client.get("/dashboard").json()
    assert Decimal(dash["pagos"]["hoy"]) == Decimal(inv["total"])
    assert dash["facturas_recientes"][0]["number"] == "FEC-000001"
    assert dash["acciones_pendientes"]["facturas_por_cobrar"] == 0


def test_role_permissions_enforced(client, auth, db_session):
    from app.core.security import hash_password
    from app.models import TenantUser, User

    u = User(email="caja@crimsonapp.com", full_name="Caja", password_hash=hash_password("Caja-2026-seguro"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=auth["tenant"]["id"], user_id=u.id, role_code="caja"))
    db_session.commit()
    r = client.post("/auth/login", json={"email": "caja@crimsonapp.com", "password": "Caja-2026-seguro"})
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/invoices", headers=h).status_code == 200
    assert client.post("/customers", json={"name": "X"}, headers=h).status_code == 403
