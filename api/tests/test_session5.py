"""Sesion 5: catalogo con costo, oportunidad -> levantamiento -> cotizacion -> proyecto -> orden de trabajo ->
entrega -> activos, mas compras, interruptor Web, disponibilidad en tienda y rentabilidad real."""

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from tests.test_session4 import _user_with_role

CATALOGO = [
    ["EUROCOMP COSTA RICA"],
    ["Listado de Precios"],
    ["Fotografía", "Código", "Descripción", "Stock", "Precio", "Ordenar", "SubTotal"],
    ["SEGURIDAD Camaras"],
    [None, "SG2104", "CAMARA BULLET HIKVISION DS-2CD1047G3-LIU(2.8MM) 4 MP\n\n- Resolución: 4 MP\n- Lente: 2.8 mm", 100, 70.91, 0, 0],
    [None, "SG2105", "CAMARA BULLET HIKVISION DS-2CD1067G3-LIU(2.8MM) 6 MP", 80, 73.03, 0, 0],
    ["SEGURIDAD Grabadoras"],
    [None, "NVR8", "NVR HIKVISION DS-7608NXI-K1 8 CANALES", 5, 120.00, 0, 0],
    [None, "MALO", "", 3, 10, 0, 0],
]


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_supplier_catalog_import_sets_cost_and_price(client, auth):
    data = _xlsx(CATALOGO)
    prev = client.post(
        "/import/catalogo", params={"supplier": "Eurocomp Costa Rica", "margin": 35}, files={"file": ("lista.xlsx", data, "application/octet-stream")}
    ).json()
    assert prev["summary"] == {"nuevo": 3, "actualizar": 0, "omitir": 0, "error": 1}
    assert not client.get("/products", params={"q": "SG2104"}).json()["items"]
    done = client.post(
        "/import/catalogo",
        params={"commit": True, "supplier": "Eurocomp Costa Rica", "margin": 35},
        files={"file": ("lista.xlsx", data, "application/octet-stream")},
    ).json()
    assert done["summary"]["nuevo"] == 3
    cam = client.get("/products", params={"q": "SG2104"}).json()["items"][0]
    full = client.get(f"/products/{cam['id']}").json()
    # 70.91 USD x 512.35 (tipo de cambio de la semilla) x 1.35 = 49 049 -> se redondea a la centena
    assert Decimal(str(full["cost"])) == Decimal("70.91") and full["cost_currency"] == "USD"
    assert Decimal(str(full["price"])) == Decimal("49100") and full["brand"] == "Hikvision"
    assert full["model"] == "DS-2CD1047G3-LIU(2.8MM)" and full["supplier_stock"] == 100
    cats = {c["name"] for c in client.get("/categories").json()}
    assert {"Seguridad", "Camaras", "Grabadoras"} <= cats
    # segunda corrida con otro margen: actualiza, no duplica
    again = client.post(
        "/import/catalogo",
        params={"commit": True, "supplier": "Eurocomp Costa Rica", "margin": 45},
        files={"file": ("lista.xlsx", data, "application/octet-stream")},
    ).json()
    assert again["summary"]["actualizar"] == 3
    assert Decimal(str(client.get(f"/products/{cam['id']}").json()["price"])) == Decimal("52700")


def test_web_toggle_and_store_availability(client, auth, db_session):
    client.put("/store", json={"kind": "tienda", "published": True})
    prod = client.get("/products", params={"q": "NVR-8CH"}).json()["items"][0]
    assert prod["show_on_web"] is True
    # el interruptor de la lista publica o quita sin abrir la ficha
    off = client.patch(f"/products/{prod['id']}/web", json={"show_on_web": False})
    assert off.status_code == 200 and off.json()["show_on_web"] is False
    assert all(x["id"] != prod["id"] for x in client.get("/public/store/crimson/products").json())
    client.patch(f"/products/{prod['id']}/web", json={"show_on_web": True})
    pub = next(x for x in client.get("/public/store/crimson/products").json() if x["id"] == prod["id"])
    assert pub["availability"]["state"] == "en_bodega" and "bodega" in pub["availability"]["label"]
    # producto sin existencias propias pero con stock del proveedor: bajo pedido
    nuevo = client.post(
        "/products", json={"name": "Cámara bajo pedido", "code": "BP-1", "price": 50000, "show_on_web": True, "tax_ids": prod["tax_ids"]}
    ).json()
    from app.models import Product

    db_session.get(Product, nuevo["id"]).supplier_stock = 12  # el stock del proveedor lo escribe la importación, no la ficha
    db_session.commit()
    bp = next(x for x in client.get("/public/store/crimson/products").json() if x["id"] == nuevo["id"])
    assert bp["availability"]["state"] == "bajo_pedido"
    av = client.get(f"/products/{prod['id']}/availability").json()
    assert Decimal(str(av["own_stock"])) > 0


def test_opportunity_survey_quote_project_workorder_assets(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    from app.models import Product

    db_session.get(Product, cam["id"]).cost = Decimal("80")  # costo del proveedor, para que la rentabilidad sea real
    db_session.commit()

    # 1) oportunidad
    opp = client.post(
        "/opportunities",
        json={
            "title": "Condominio Los Robles · CCTV",
            "customer_id": cust["id"],
            "source": "Referido",
            "solution": "cctv",
            "amount": 900000,
            "probability": 40,
            "next_action": "Visita técnica",
            "next_action_date": str(date.today()),
        },
    ).json()
    assert opp["number"].startswith("OPO") and Decimal(str(opp["weighted"])) == Decimal("360000.00")
    board = client.get("/opportunities/board").json()
    assert board["columns"][0]["status"] == "nuevo" and Decimal(str(board["total"])) == Decimal("900000")

    # 2) el técnico levanta en sitio (sin ver precios)
    pw = _user_with_role(db_session, tid, "tecnico@ejemplo.com", "tecnico")
    client.headers.pop("Authorization", None)
    tk = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "tecnico@ejemplo.com", "password": pw}).json()["access_token"]}
    client.headers.update(admin_h)
    specs = client.get("/field/specs", headers=tk).json()
    assert specs["cctv"]["point_prefix"] == "CAM" and any(f["key"] == "distancia_m" for f in specs["cctv"]["fields"])
    catalogo_tecnico = client.get("/products", params={"q": "CAM-DOME"}, headers=tk).json()["items"][0]
    assert Decimal(str(catalogo_tecnico["price"])) == 0  # el técnico no ve plata

    lev = client.post(
        "/surveys",
        headers=tk,
        json={
            "kind": "cctv",
            "customer_id": cust["id"],
            "opportunity_id": opp["id"],
            "site": "Condominio Los Robles, Alajuela",
            "techs": 2,
            "days": 3,
            "points": [
                {"code": "CAM-01", "label": "Entrada", "data": {"tipo": "Bullet", "distancia_m": 72, "altura_m": 5}},
                {"code": "CAM-02", "label": "Parqueo", "data": {"tipo": "Domo", "distancia_m": 43}},
            ],
            "items": [{"product_id": cam["id"], "name": cam["name"], "quantity": 4, "unit": "Unid"}],
        },
    ).json()
    assert lev["number"].startswith("LEV") and lev["points_count"] == 2
    assert client.get("/opportunities/" + str(opp["id"])).json()["status"] == "levantamiento"
    sug = client.post(f"/surveys/{lev['id']}/suggest", headers=tk).json()
    assert any("holgura" in s["name"] for s in sug)
    assert client.get(f"/surveys/{lev['id']}/costing", headers=tk).status_code == 403  # el técnico no ve costos
    assert client.post(f"/surveys/{lev['id']}/send", headers=tk).json()["status"] == "enviado"

    # 3) administración costea y cotiza
    cost = client.get(f"/surveys/{lev['id']}/costing").json()
    assert cost["labor"]["cost"] == "150000.00" or Decimal(str(cost["labor"]["cost"])) == Decimal("150000")  # 2 técnicos x 3 días x 25 000
    assert Decimal(str(cost["cost_total"])) > 0 and Decimal(str(cost["price_suggested"])) > Decimal(str(cost["cost_total"]))
    q = client.post(f"/surveys/{lev['id']}/quote", json={"include_labor": True}).json()
    assert q["number"].startswith("COT")
    quote = client.get(f"/quotes/{q['quote_id']}").json()
    assert len(quote["lines"]) == 2 and "Instalación" in quote["lines"][1]["name"]
    assert client.get(f"/opportunities/{opp['id']}").json()["status"] == "cotizando"

    # 4) cotización aprobada -> proyecto (con su primera orden de trabajo)
    pr = client.post(f"/quotes/{q['quote_id']}/project", json={"site": "Condominio Los Robles"}).json()
    assert pr["number"].startswith("PRO") and pr["orders_total"] == 1
    assert client.get(f"/opportunities/{opp['id']}").json()["status"] == "ganada"
    req = client.get(f"/projects/{pr['id']}/requirements").json()
    assert req and Decimal(str(req[0]["planned"])) == 4

    # 5) el técnico ejecuta desde el celular
    orders = client.get("/work-orders", headers=tk).json()
    assert orders == []  # todavía no está asignada a él
    oid = client.get(f"/projects/{pr['id']}").json()["orders"][0]["id"]
    client.put(
        f"/work-orders/{oid}",
        json={
            "title": "Instalación CCTV",
            "project_id": pr["id"],
            "technician_id": _uid(db_session, "tecnico@ejemplo.com"),
            "scheduled_at": None,
            "materials": [{"product_id": cam["id"], "name": cam["name"], "quantity": 0, "planned": 4}],
        },
    )
    mine = client.get("/work-orders/meta/today", headers=tk).json()
    assert len(mine["next"]) == 1
    client.post(f"/work-orders/{oid}/arrive", headers=tk, json={})
    client.post(f"/work-orders/{oid}/start", headers=tk, json={})
    stock_antes = _stock(client, cam["id"])
    fin = client.post(
        f"/work-orders/{oid}/finish",
        headers=tk,
        json={
            "materials": [{"product_id": cam["id"], "name": cam["name"], "quantity": 4, "planned": 4}],
            "photos": ["https://x/foto1.jpg"],
            "notes": "Instalación completa",
            "customer_signature": "Ana del condominio",
        },
    ).json()
    assert fin["status"] == "finalizada" and fin["stock_applied"] is True
    assert _stock(client, cam["id"]) == stock_antes - 4
    assert client.get(f"/projects/{pr['id']}").json()["status"] == "terminado"

    # 6) activos del cliente y rentabilidad real
    act = client.post(
        "/assets",
        headers=tk,
        json={
            "customer_id": cust["id"],
            "project_id": pr["id"],
            "product_id": cam["id"],
            "name": "Cámara entrada",
            "serial": "SN-001",
            "location": "Entrada principal",
            "installed_at": str(date.today()),
            "warranty_until": str(date.today() + timedelta(days=30)),
        },
    ).json()
    assert act["warranty_days"] == 30
    assert client.get("/assets", params={"expiring": True}).json()[0]["serial"] == "SN-001"
    client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "entregado", "cost_labor": 150000, "cost_travel": 35000, "customer_id": cust["id"]})
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert Decimal(str(eco["cost_materials"])) > 0 and Decimal(str(eco["cost_real"])) > Decimal("185000")  # 4 cámaras + mano de obra + viáticos
    assert Decimal(str(eco["margin_real"])) < Decimal(str(eco["margin_planned"])) + 100
    rent = client.get("/reports/rentabilidad").json()
    assert rent["rows"][0][0] == pr["number"]  # el reporte vive en el catálogo: columnas + filas
    assert Decimal(str(rent["totals"]["Utilidad"])) == Decimal(str(eco["profit"]))
    informe = client.get(f"/projects/{pr['id']}/report")
    assert informe.status_code == 200 and "Informe técnico de entrega" in informe.text and "SN-001" in informe.text

    # 7) compras: lo que falta se pide al proveedor
    faltan = client.post(f"/projects/{pr['id']}/purchase-request")
    assert faltan.status_code in (201, 409)


def _uid(db, email):
    from sqlalchemy import select

    from app.models import User

    return db.scalar(select(User).where(User.email == email)).id


def _stock(client, product_id):
    rows = client.get("/stock").json()["levels"]
    return sum(Decimal(str(r["quantity"])) for r in rows if r["product_id"] == product_id)


def test_technician_cannot_see_money_or_others(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    pw = _user_with_role(db_session, tid, "tec2@ejemplo.com", "tecnico")
    client.headers.pop("Authorization", None)
    tk = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "tec2@ejemplo.com", "password": pw}).json()["access_token"]}
    client.headers.update(admin_h)
    me = client.get("/auth/me", headers=tk).json()
    assert "precios" not in me["permissions"]["catalog"] and "sales" not in me["permissions"]
    assert client.get("/invoices", headers=tk).status_code == 403
    assert client.get("/reports/facturacion", headers=tk).status_code == 403
    assert client.get("/dashboard", headers=tk).json()["pagos"] is None
    otro = client.post("/surveys", json={"kind": "redes", "site": "Otra empresa"}).json()  # levantamiento del admin
    assert client.get(f"/surveys/{otro['id']}", headers=tk).status_code == 404
    assert client.get("/surveys", headers=tk).json() == []


def test_ceo_dashboard_row(client, auth):
    cust = client.get("/customers").json()["items"][0]
    client.post("/opportunities", json={"title": "Pipeline", "customer_id": cust["id"], "amount": 2000000, "probability": 50})
    client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 100000, "tax_rate": 13}]})
    g = client.get("/dashboard").json()["gerencia"]
    assert Decimal(str(g["pipeline"])) == Decimal("2000000") and Decimal(str(g["pipeline_weighted"])) == Decimal("1000000.00")
    assert Decimal(str(g["receivable"])) >= Decimal("113000") and g["opportunities"] >= 1
    assert set(g) >= {"projects_active", "jobs_week", "quotes_sent", "warranties_soon", "margin_month"}
