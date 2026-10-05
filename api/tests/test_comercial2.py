"""Revision de Andres (comercial 2): tipo de oportunidad, responsable visible y reasignable, cotizacion directa desde
la oportunidad, acciones pendientes del inicio que llevan a la lista filtrada, y levantamientos con puntos mixtos."""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Invoice, Opportunity, Quote, Survey, Tenant

from .test_comercial import _login


def _cust(client):
    return client.get("/customers").json()["items"][0]


# ---------- 1. tipo de oportunidad ----------
def test_kind_default_and_venta_skips_project(client, auth, db_session):
    legacy = client.post("/opportunities", json={"title": "Sin tipo"}).json()
    assert legacy["kind"] == "proyecto"  # lo de antes sigue igual
    assert client.post("/opportunities", json={"title": "Malo", "kind": "otra"}).status_code == 422

    cust = _cust(client)
    venta = client.post("/opportunities", json={"title": "Venta de 4 cámaras", "kind": "venta", "customer_id": cust["id"]}).json()
    assert venta["kind"] == "venta"
    assert [o["id"] for o in client.get("/opportunities?kind=venta").json()] == [venta["id"]]
    q = client.post(f"/opportunities/{venta['id']}/quote", json={}).json()
    qq = client.get(f"/quotes/{q['quote_id']}").json()
    assert qq["opportunity"]["kind"] == "venta"  # el editor no ofrece "convertir a proyecto"
    # proyecto desde una cotizacion de venta: solo si se confirma a proposito
    r = client.post(f"/quotes/{q['quote_id']}/project", json={})
    assert r.status_code == 409 and "solo venta" in r.text
    # la factura cierra la venta: oportunidad ganada
    client.put(f"/quotes/{q['quote_id']}", json={"customer_id": cust["id"], "lines": [{"name": "Cámara", "quantity": 4, "unit_price": 10000, "tax_rate": 13}]})
    assert client.post(f"/quotes/{q['quote_id']}/convert").status_code == 201
    assert client.get(f"/opportunities/{venta['id']}").json()["status"] == "ganada"

    # en "proyecto" el proyecto se crea como siempre y la oportunidad no se gana al facturar
    pro = client.post("/opportunities", json={"title": "Instalación", "customer_id": cust["id"]}).json()
    q2 = client.post(f"/opportunities/{pro['id']}/quote", json={}).json()
    assert client.post(f"/quotes/{q2['quote_id']}/project", json={}).status_code == 201
    assert client.get(f"/opportunities/{pro['id']}").json()["status"] == "ganada"


def test_venta_project_with_force(client, auth):
    cust = _cust(client)
    venta = client.post("/opportunities", json={"title": "Venta", "kind": "venta", "customer_id": cust["id"]}).json()
    q = client.post(f"/opportunities/{venta['id']}/quote", json={}).json()
    assert client.post(f"/quotes/{q['quote_id']}/project", json={"force": True}).status_code == 201


# ---------- 2. responsable ----------
def test_owner_visible_filter_and_reassign(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    vend = _login(client, admin_h, db_session, tid, "vend.r@ejemplo.com", "ventas")
    vend_id = client.get("/auth/me", headers=vend).json()["user"]["id"]
    admin_id = client.get("/auth/me").json()["user"]["id"]

    mia = client.post("/opportunities", json={"title": "De gerencia"}).json()
    suya = client.post("/opportunities", json={"title": "Del vendedor"}, headers=vend).json()
    assert mia["owner"] and mia["owner_initials"] and suya["owner_id"] == vend_id

    # filtro por responsable en lista y embudo
    assert [o["id"] for o in client.get(f"/opportunities?owner_id={vend_id}").json()] == [suya["id"]]
    board = client.get(f"/opportunities/board?owner_id={vend_id}").json()
    assert sum(c["count"] for c in board["columns"]) == 1
    # el vendedor sin ver_todo no ve lo ajeno aunque pida otro responsable
    assert all(o["owner_id"] == vend_id for o in client.get(f"/opportunities?owner_id={admin_id}", headers=vend).json())
    assert all(o["owner_id"] == vend_id for c in client.get("/opportunities/board", headers=vend).json()["columns"] for o in c["items"])

    # reasignar: gerencia si, vendedor no; queda en bitacora y auditoria
    assert client.post(f"/opportunities/{suya['id']}/assign", json={"owner_id": admin_id}, headers=vend).status_code == 403
    r = client.post(f"/opportunities/{mia['id']}/assign", json={"owner_id": vend_id})
    assert r.status_code == 200 and r.json()["owner_id"] == vend_id and "Reasignada" in r.json()["notes"]
    assert client.post(f"/opportunities/{mia['id']}/assign", json={"owner_id": 999999}).status_code == 422
    # editar sin responsable no lo borra
    body = {"title": "De gerencia (editada)", "owner_id": None}
    assert client.put(f"/opportunities/{mia['id']}", json=body).json()["owner_id"] == vend_id
    # el vendedor no se asigna oportunidades a otro al crear
    otra = client.post("/opportunities", json={"title": "Otra", "owner_id": admin_id}, headers=vend).json()
    assert otra["owner_id"] == vend_id


# ---------- 3. cotizacion desde la oportunidad ----------
def test_quote_from_opportunity(client, auth, db_session):
    cust = _cust(client)
    o = client.post("/opportunities", json={"title": "Sin cliente", "amount": 50000, "status": "contactado"}).json()
    assert client.post(f"/opportunities/{o['id']}/quote", json={}).status_code == 422  # pide el cliente primero
    r = client.post(f"/opportunities/{o['id']}/quote", json={"customer_id": cust["id"]})
    assert r.status_code == 201, r.text
    got = client.get(f"/opportunities/{o['id']}").json()
    assert got["quote_id"] == r.json()["quote_id"] and got["status"] == "cotizando" and got["customer_id"] == cust["id"]
    assert Decimal(str(got["amount"])) == 50000  # el estimado queda hasta que la cotizacion tenga lineas
    assert "creada desde la oportunidad" in got["notes"]
    assert client.post(f"/opportunities/{o['id']}/quote", json={}).status_code == 409  # una sola cotizacion viva
    # el monto sigue a la cotizacion al guardarla
    q = client.put(
        f"/quotes/{r.json()['quote_id']}", json={"customer_id": cust["id"], "lines": [{"name": "Kit", "quantity": 1, "unit_price": 100000, "tax_rate": 13}]}
    ).json()
    assert Decimal(str(client.get(f"/opportunities/{o['id']}").json()["amount"])) == Decimal(str(q["total"])) == 113000

    # nunca retrocede: una oportunidad en negociacion sigue en negociacion
    n = client.post("/opportunities", json={"title": "Negociando", "customer_id": cust["id"], "status": "negociacion"}).json()
    client.post(f"/opportunities/{n['id']}/quote", json={})
    assert client.get(f"/opportunities/{n['id']}").json()["status"] == "negociacion"


# ---------- 4. acciones pendientes -> lista filtrada ----------
def test_dashboard_pending_matches_filtered_lists(client, auth, db_session):
    cust = _cust(client)
    tid = auth["tenant"]["id"]
    line = [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]
    past = str(date.today() - timedelta(days=10))
    ids = [client.post("/invoices", json={"customer_id": cust["id"], "lines": line, "due_date": past}).json()["id"] for _ in range(2)]
    client.post("/invoices", json={"customer_id": cust["id"], "lines": line})
    inv = db_session.get(Invoice, ids[0])
    inv.einvoice_status = "rechazada"
    db_session.commit()
    client.post(f"/invoices/{ids[1]}/payment-link")
    client.post(f"/invoices/{ids[1]}/payment-link")  # mismo enlace o uno nuevo: la factura cuenta una vez
    qs = [client.post("/quotes", json={"customer_id": cust["id"], "lines": line}).json()["id"] for _ in range(3)]
    db_session.get(Quote, qs[0]).status = "por_aprobar"
    db_session.commit()

    def check(headers=None):
        pend = client.get("/dashboard", headers=headers or {}).json()["acciones_pendientes"]
        for key, path in [
            ("facturas_vencidas", "invoices"),
            ("facturas_por_cobrar", "invoices"),
            ("documentos_rechazados", "invoices"),
            ("enlaces_abiertos", "invoices"),
            ("cotizaciones_sin_respuesta", "quotes"),
            ("cotizaciones_por_aprobar", "quotes"),
        ]:
            rows = client.get(f"/{path}?pendiente={key}&limit=100", headers=headers or {}).json()["items"]
            assert len(rows) == pend[key], (key, len(rows), pend[key])
        return pend

    pend = check()
    assert pend["facturas_vencidas"] >= 2 and pend["documentos_rechazados"] >= 1 and pend["enlaces_abiertos"] >= 1
    assert pend["cotizaciones_por_aprobar"] >= 1 and pend["cotizaciones_sin_respuesta"] >= 2
    # el vendedor: tarjetas y listas cuentan solo lo suyo, y siguen coincidiendo
    vend = _login(client, dict(client.headers), db_session, tid, "vend.d@ejemplo.com", "ventas")
    client.post("/invoices", json={"customer_id": cust["id"], "lines": line, "due_date": past}, headers=vend)
    assert check(vend)["facturas_vencidas"] == 1
    assert client.get("/invoices?pendiente=no_existe").status_code == 422
    assert client.get("/quotes?pendiente=facturas_vencidas").status_code == 422
    stock = client.get("/stock").json()
    assert client.get("/dashboard").json()["acciones_pendientes"]["stock_bajo"] == len(stock["low"])


# ---------- 5. levantamientos con puntos de otros tipos ----------
def _mixed(client, cust_id=None):
    body = {
        "kind": "cctv",
        "customer_id": cust_id,
        "points": [
            {"code": "CAM-01", "data": {"tipo": "Domo", "resolucion": "4 MP", "distancia_m": 20, "alimentacion": "PoE"}},
            {
                "code": "ACC-01",
                "kind": "acceso",
                "label": "Puerta principal",
                "data": {"cerradura": "Magnética 280 kg", "lectura": ["Tarjeta", "PIN"], "salida": "Botón", "distancia_m": "12,5"},
            },
            {"code": "DAT-01", "kind": "cableado", "data": {"metros": 30, "categoria": "Cat6"}},
            {"code": "UPS-01", "kind": "ups", "data": {"carga_w": 400}},
        ],
    }
    r = client.post("/surveys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_mixed_points_suggest_by_point_kind(client, auth):
    s = _mixed(client)
    full = client.get(f"/surveys/{s['id']}").json()
    assert [p["kind"] for p in full["points"]] == ["cctv", "acceso", "cableado", "ups"]
    assert full["points"][1]["data"]["distancia_m"] == 12.5  # campos numericos segun el tipo del punto
    bad = client.post("/surveys", json={"kind": "cctv", "points": [{"code": "UPS-01", "kind": "ups", "data": {"carga_w": "mucha"}}]})
    assert bad.status_code == 422 and "Carga" in bad.text

    by = {x["name"]: x for x in client.post(f"/surveys/{s['id']}/suggest").json()}
    # camara: reglas de CCTV solo para la camara
    assert Decimal(str(by["Cámara Domo 4 MP"]["quantity"])) == 1 and Decimal(str(by["Conectores RJ45"]["quantity"])) == 2
    # puerta: reglas de acceso
    assert by["Lector de tarjeta"]["kind"] == "equipo" and "Teclado PIN" in by and "Botón de salida" in by
    assert "Controladora de acceso 2 puertas" in by and "Fuente 12 VDC con respaldo" in by
    assert Decimal(str(by["Cable 4x22 (metros con 15 % de holgura)"]["quantity"])) == 14  # 12.5 + 15 %
    # punto de red: reglas de cableado (jack, placa, patch cords) y su cable entra al UTP
    assert Decimal(str(by["Jacks"]["quantity"])) == 1 and Decimal(str(by["Patch cords"]["quantity"])) == 2
    assert Decimal(str(by["Cable (metros con 15 % de holgura)"]["quantity"])) == 58  # (20 + 30) * 1.15; la puerta no suma UTP
    # UPS: capacidad desde la carga
    assert "UPS 1000 VA" in by
    # habituales de los otros tipos, para revisar
    assert by["Cerradura"]["action"] == "revisar" or any("cerradura" in n.lower() for n in by)


def test_suggest_without_points_offers_usual_materials(client, auth):
    s = client.post("/surveys", json={"kind": "acceso", "site": "Oficina"}).json()
    sug = client.post(f"/surveys/{s['id']}/suggest").json()
    assert sug and all(x["action"] == "revisar" for x in sug)
    assert "Cerradura" in {x["name"] for x in sug} and all("Todavía no hay puntos" in x["reason"] for x in sug)


def test_mixed_survey_report_costing_and_quote(client, auth, db_session):
    cust = _cust(client)
    s = _mixed(client, cust["id"])
    sug = client.post(f"/surveys/{s['id']}/suggest").json()
    picks = [{"name": x["name"], "quantity": x["add_quantity"], "unit": x["unit"], "kind": x["kind"]} for x in sug if x["action"] == "nuevo"]
    assert client.post(f"/surveys/{s['id']}/suggest/apply", json=picks).status_code == 200
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    assert len(cost["lines"]) == len(picks)
    from app.services.survey_report import render_survey_html

    page = render_survey_html(db_session, db_session.get(Survey, s["id"]), db_session.get(Tenant, auth["tenant"]["id"]))
    assert "Puerta · Puerta principal" in page and "Cerradura" in page and "Carga a respaldar" in page
    r = client.post(f"/surveys/{s['id']}/quote", json={})
    assert r.status_code == 201, r.text


def test_survey_quote_never_moves_opportunity_back(client, auth, db_session):
    cust = _cust(client)
    o = client.post("/opportunities", json={"title": "Ya enviada", "customer_id": cust["id"]}).json()
    s = client.post(
        "/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": o["id"], "items": [{"name": "Cámara", "quantity": 1}]}
    ).json()
    db_session.get(Opportunity, o["id"]).status = "negociacion"
    db_session.commit()
    assert client.post(f"/surveys/{s['id']}/quote", json={}).status_code == 201
    assert client.get(f"/opportunities/{o['id']}").json()["status"] == "negociacion"
