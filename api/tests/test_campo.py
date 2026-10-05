"""Observaciones de Andres sobre campo y catalogo: decimales en medidas, mano de obra por tipo, sugerencias que
no borran, equipos vs materiales, costo editable en el costeo, a quien le llega el levantamiento, CABYS y bodega."""

from decimal import Decimal

import httpx

from tests.test_session4 import _user_with_role
from tests.test_session5 import _xlsx


def _login(client, db_session, tid, email, role):
    pw = _user_with_role(db_session, tid, email, role)
    admin = client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    client.headers["Authorization"] = admin
    return {"Authorization": f"Bearer {tok}"}


def _survey(client, headers=None, **extra):
    body = {
        "kind": "cctv",
        "site": "Bodega Alajuela",
        "points": [
            {"code": "CAM-01", "label": "Entrada", "data": {"tipo": "Bullet", "resolucion": "4 MP", "distancia_m": 40, "altura_m": 2.5, "alimentacion": "PoE"}},
            {
                "code": "CAM-02",
                "label": "Patio",
                "data": {"tipo": "Bullet", "resolucion": "4 MP", "distancia_m": "30,5", "altura_m": "5", "ambiente": "Exterior", "canalizacion": "EMT"},
                "notes": "Hay que romper cielo raso para pasar el cable",
            },
        ],
        **extra,
    }
    r = client.post("/surveys", json=body, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def test_mounting_height_keeps_decimals(client, auth):
    s = _survey(client)
    full = client.get(f"/surveys/{s['id']}").json()
    assert full["points"][0]["data"]["altura_m"] == 2.5  # no se trunca a 2
    assert full["points"][1]["data"]["distancia_m"] == 30.5  # coma decimal del celular
    assert full["points"][1]["data"]["altura_m"] == 5
    bad = client.post("/surveys", json={"kind": "cctv", "points": [{"code": "CAM-01", "data": {"altura_m": "dos"}}]})
    assert bad.status_code == 422 and "Altura" in bad.text


def test_labor_by_type_and_legacy_techs(client, auth, db_session):
    legacy = _survey(client, techs=3, days=2)
    assert legacy["labor"]["tecnico"]["people"] == 3 and Decimal(str(legacy["labor"]["tecnico"]["days"])) == 2
    assert legacy["labor"]["civil"]["people"] == 0

    admin_id = next(t["id"] for t in client.get("/field/technicians").json() if t["role"] == "admin")
    s = _survey(
        client,
        labor={"tecnico": {"people": 2, "days": 3}, "civil": {"people": 1, "days": 2}, "contratado": {"people": 1, "days": 1.5}},
        visit_tech_ids=[admin_id, 999999],
    )
    assert s["techs"] == 2 and Decimal(str(s["days"])) == 3  # espejo del personal tecnico
    assert s["visit_tech_ids"] == [admin_id] and len(s["visit_techs"]) == 1  # informativo; ids ajenos se descartan
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    # 25 000 por persona-dia en los tres tipos mientras no se configuren tarifas propias: (6 + 2 + 1.5) x 25 000
    assert Decimal(str(cost["labor"]["cost"])) == Decimal("237500")
    assert [t["key"] for t in cost["labor"]["types"]] == ["tecnico", "civil", "contratado"]
    assert client.post("/surveys", json={"kind": "cctv", "labor": {"magos": {"people": 1, "days": 1}}}).status_code == 422


def test_suggest_does_not_replace_and_apply_adds(client, auth):
    s = _survey(client, items=[{"name": "Conectores RJ45", "quantity": 1, "unit": "Unid"}, {"name": "Cinta aislante", "quantity": 2}])
    sug = client.post(f"/surveys/{s['id']}/suggest").json()
    by = {x["name"]: x for x in sug}
    assert all(x["reason"] for x in sug)
    assert by["Conectores RJ45"]["action"] == "sumar" and Decimal(str(by["Conectores RJ45"]["add_quantity"])) == 3
    assert by["Cámara Bullet 4 MP"]["kind"] == "equipo" and Decimal(str(by["Cámara Bullet 4 MP"]["quantity"])) == 2
    assert any("cielo raso" in x["name"].lower() for x in sug)  # sale de las observaciones del punto
    assert any("andamio" in x["name"].lower() for x in sug)  # CAM-02 va a 5 m
    # sugerir no toca el levantamiento
    assert len(client.get(f"/surveys/{s['id']}").json()["items"]) == 2

    r = client.post(
        f"/surveys/{s['id']}/suggest/apply",
        json=[{"name": "Conectores RJ45", "quantity": 3}, {"name": "Cámara Bullet 4 MP", "quantity": 2, "kind": "equipo"}],
    ).json()
    assert r["added"] == 1 and r["summed"] == 1
    items = {i["name"]: i for i in client.get(f"/surveys/{s['id']}").json()["items"]}
    assert set(items) == {"Conectores RJ45", "Cinta aislante", "Cámara Bullet 4 MP"}  # no borró nada
    assert Decimal(str(items["Conectores RJ45"]["quantity"])) == 4  # sumó, no duplicó
    assert items["Cámara Bullet 4 MP"]["kind"] == "equipo" and items["Cinta aislante"]["kind"] == "material"


def test_costing_groups_and_editable_cost_with_permissions(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prod = client.post("/products", json={"name": "Gabinete 12U", "code": "GAB-12U", "price": 0}).json()
    s = _survey(
        client,
        items=[
            {"product_id": prod["id"], "name": "Gabinete 12U", "quantity": 1, "kind": "equipo"},
            {"name": "Cable UTP Cat6", "quantity": 100, "unit": "m"},
        ],
    )
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    assert set(cost["missing_cost"]) == {"Gabinete 12U", "Cable UTP Cat6"}
    assert cost["lines"][0]["kind"] == "equipo" and "equipo" in cost["groups"] and "material" in cost["groups"]
    gab = next(x for x in cost["lines"] if x["product_id"] == prod["id"])
    cable = next(x for x in cost["lines"] if x["product_id"] is None)

    # el tecnico y la vendedora no ven ni escriben costos
    tk = _login(client, db_session, tid, "tec-costo@ejemplo.com", "tecnico")
    vk = _login(client, db_session, tid, "ventas-costo@ejemplo.com", "ventas")
    assert client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": gab["item_id"], "unit_cost": 1}], headers=tk).status_code == 403
    assert client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": gab["item_id"], "unit_cost": 1}], headers=vk).status_code == 403

    # texto libre no se puede guardar en el catalogo
    bad = client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": cable["item_id"], "unit_cost": 350, "save_to_catalog": True}])
    assert bad.status_code == 422

    new = client.post(
        f"/surveys/{s['id']}/costs",
        params={"margin": 35},
        json=[{"item_id": gab["item_id"], "unit_cost": 65000, "save_to_catalog": True}, {"item_id": cable["item_id"], "unit_cost": 350}],
    ).json()
    assert new["missing_cost"] == []
    gab2 = next(x for x in new["lines"] if x["product_id"] == prod["id"])
    assert gab2["cost_source"] == "levantamiento" or gab2["cost_source"] == "catalogo"
    assert Decimal(str(gab2["unit_price"])) == Decimal("100000")  # 65 000 / 0,65
    full = client.get(f"/products/{prod['id']}").json()
    assert Decimal(str(full["cost"])) == Decimal("65000") and full["cost_currency"] == "CRC"
    cab2 = next(x for x in new["lines"] if x["product_id"] is None)
    assert cab2["cost_source"] == "levantamiento" and Decimal(str(cab2["cost"])) == Decimal("35000")

    # el tecnico vuelve a guardar el levantamiento: el costo escrito no se pierde
    detail = client.get(f"/surveys/{s['id']}").json()
    client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "points": detail["points"], "items": detail["items"]})
    again = client.get(f"/surveys/{s['id']}/costing").json()
    assert next(x for x in again["lines"] if x["product_id"] is None)["cost_source"] == "levantamiento"


def test_send_shows_who_was_notified(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tk = _login(client, db_session, tid, "tec-envio@ejemplo.com", "tecnico")
    _user_with_role(db_session, tid, "super@ejemplo.com", "supervisor")
    s = _survey(client, headers=tk)
    sent = client.post(f"/surveys/{s['id']}/send", headers=tk).json()
    assert sent["status"] == "enviado" and sent["sent_by"] == "Tecnico"
    names = {n["name"] for n in sent["notified"]}
    assert "Supervisor" in names and len(names) >= 2  # admin + supervisor
    assert sent["pending_review"]["roles"] == ["admin", "supervisor"]
    row = next(x for x in client.get("/surveys", headers=tk).json() if x["id"] == s["id"])
    assert row["sent_by"] == "Tecnico" and row["sent_at"] and "Supervisor" in row["pending_review"]["people"]


def test_cabys_proxy_with_mock(client, auth, monkeypatch):
    from app.services import cabys as svc

    svc.clear_cache()
    calls = []

    def fake(params):
        calls.append(params)
        if "codigo" in params:
            return [{"codigo": "4527100000100", "descripcion": "Cámaras de video", "impuesto": 13}]
        return {"total": 1, "cantidad": 1, "cabys": [{"codigo": "4527100000100", "descripcion": "Cámaras de video", "impuesto": 13, "categorias": ["x"]}]}

    monkeypatch.setattr(svc, "_fetch", fake)
    r = client.get("/cabys", params={"q": "camara"})
    assert r.status_code == 200 and r.json()[0] == {"code": "4527100000100", "description": "Cámaras de video", "tax_rate": 13.0, "categories": ["x"]}
    client.get("/cabys", params={"q": "  CAMARA "})
    assert len(calls) == 1  # segunda consulta sale de la cache
    assert client.get("/cabys", params={"codigo": "4527100000100"}).json()[0]["code"] == "4527100000100"
    assert client.get("/cabys", params={"q": "ab"}).status_code == 422

    def down(params):
        raise httpx.ConnectTimeout("sin red")

    monkeypatch.setattr(svc, "_fetch", down)
    r = client.get("/cabys", params={"q": "switch"})
    assert r.status_code == 502 and "Hacienda" in r.json()["detail"]


def test_import_sets_product_warehouse_and_filter(client, auth):
    data = _xlsx(
        [
            ["Codigo", "Nombre", "Precio", "IVA", "Bodega"],
            ["BOD-1", "Cable Cat6", 1000, 13, "Bodega Heredia"],
            ["BOD-2", "Conector", 100, 13, "bodega heredia"],
            ["BOD-3", "Placa", 500, 13, None],
        ]
    )
    prev = client.post("/import/products", files={"file": ("p.xlsx", data, "application/octet-stream")}).json()
    assert "(nueva)" in prev["result"][0]["detail"] and prev["columns"]["warehouse"] == "bodega"
    done = client.post("/import/products", params={"commit": True}, files={"file": ("p.xlsx", data, "application/octet-stream")}).json()
    assert done["summary"]["nuevo"] == 3
    whs = [w for w in client.get("/products/meta/filters").json()["warehouses"] if w["name"].lower() == "bodega heredia"]
    assert len(whs) == 1  # una sola bodega aunque cambien las mayusculas
    codes = {p["code"] for p in client.get("/products", params={"warehouse_id": whs[0]["id"]}).json()["items"]}
    assert codes == {"BOD-1", "BOD-2"}
    one = client.get("/products", params={"q": "BOD-1"}).json()["items"][0]
    assert one["warehouse_id"] == whs[0]["id"]

    # lista de proveedor: columna Bodega
    cat = _xlsx([["Codigo", "Descripcion", "Stock", "Precio", "Bodega"], [None, None, None, None, None], ["SUP-1", "SWITCH POE 8P", 4, 50, "Bodega Heredia"]])
    client.post("/import/catalogo", params={"commit": True, "supplier": "Prov"}, files={"file": ("c.xlsx", cat, "application/octet-stream")})
    sup = client.get("/products", params={"q": "SUP-1"}).json()["items"][0]
    assert sup["warehouse_id"] == whs[0]["id"]


def test_sugerencia_de_cable_suma_al_cable_ya_cargado(client, auth):
    """Con 'Cable UTP Cat6, 50 m' cargado y 85 m calculados, la sugerencia proponia 85 m de cable NUEVO
    (quedaban 135 m) y marcaba el UTP como 'ya alcanza'. Tiene que proponer sumar 35 m al UTP."""
    s = client.post(
        "/surveys",
        json={
            "kind": "cctv",
            "points": [
                {"code": "CAM-01", "data": {"distancia_m": "60", "tipo": "bullet"}},
                {"code": "CAM-02", "data": {"distancia_m": "14", "tipo": "domo"}},
            ],
            "items": [{"name": "Cable UTP Cat6", "quantity": 50, "unit": "m"}],
        },
    ).json()
    sug = client.post(f"/surveys/{s['id']}/suggest").json()
    sug = sug if isinstance(sug, list) else sug.get("suggestions", [])
    cables = [x for x in sug if "cable" in x["name"].lower()]
    assert len(cables) == 1, [x["name"] for x in cables]  # sin la linea habitual duplicada
    c = cables[0]
    assert c["action"] == "sumar" and Decimal(str(c["add_quantity"])) == Decimal(35)
    assert c["matched_name"] == "Cable UTP Cat6"
    r = client.post(
        f"/surveys/{s['id']}/suggest/apply",
        json=[{"name": c["name"], "quantity": "35", "unit": "m", "item_id": c["item_id"]}],
    ).json()
    assert r["summed"] == 1 and r["added"] == 0
    lineas = [i for i in r["survey"]["items"] if "cable" in i["name"].lower()]
    assert len(lineas) == 1 and Decimal(str(lineas[0]["quantity"])) == Decimal(85)


def test_ligar_levantamiento_hecho_antes_de_la_oportunidad(client, auth):
    """Andres: 'hice el levantamiento en el sitio antes de registrar la oportunidad; quiero asignarlo despues'.
    Caso real: se visita, se cotiza, y recien ahi se crea la oportunidad. Al ligar, la oportunidad hereda
    cliente, cotizacion, monto y etapa, y queda anotado en su bitacora."""
    cli = client.get("/customers", params={"limit": 1}).json()
    cli = (cli if isinstance(cli, list) else cli.get("items", cli.get("rows", [])))[0]
    s = client.post(
        "/surveys",
        json={"kind": "cctv", "customer_id": cli["id"], "points": [{"code": "CAM-01", "data": {"distancia_m": "20", "tipo": "bullet"}}],
              "items": [{"name": "Gabinete 15U", "quantity": 1}]},
    ).json()
    gab = next(i for i in s["items"] if i["name"] == "Gabinete 15U")
    client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": gab["id"], "unit_cost": "50000"}])
    q = client.post(f"/surveys/{s['id']}/quote", json={"margin": 35}).json()

    o = client.post("/opportunities", json={"title": "Condominio creado despues", "amount": "1000", "probability": 40}).json()
    assert o["status"] == "nuevo"
    r = client.post(f"/surveys/{s['id']}/opportunity", json={"opportunity_id": o["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["opportunity_id"] == o["id"]
    o2 = client.get(f"/opportunities/{o['id']}").json()
    assert o2["customer_id"] == cli["id"]  # hereda el cliente del levantamiento
    assert o2["quote_id"] == q["quote_id"] and Decimal(str(o2["amount"])) == Decimal(str(q["total"]))
    assert o2["status"] == "cotizando"
    assert f"se ligo el levantamiento {s['number']}" in (o2.get("notes") or "")

    # no se liga a una oportunidad de otro cliente
    otro = client.post("/customers", json={"name": "Otro Cliente SA", "id_type": "02", "id_number": "3101999888"}).json()
    o3 = client.post("/opportunities", json={"title": "Ajena", "customer_id": otro["id"]}).json()
    assert client.post(f"/surveys/{s['id']}/opportunity", json={"opportunity_id": o3["id"]}).status_code == 409

    # desligar
    r = client.post(f"/surveys/{s['id']}/opportunity", json={"opportunity_id": None})
    assert r.status_code == 200 and r.json()["opportunity_id"] is None


def test_tipos_de_camara_lente_dual_180_y_360(client, auth):
    """Andres pidio estos dos tipos en el levantamiento."""
    specs = client.get("/field/specs").json()
    tipos = next(f for f in specs["cctv"]["fields"] if f["key"] == "tipo")["options"]
    assert "Lente dual 180°" in tipos and "360°" in tipos
    s = client.post("/surveys", json={"kind": "cctv", "points": [{"code": "CAM-01", "data": {"tipo": "360°"}}, {"code": "CAM-02", "data": {"tipo": "Lente dual 180°"}}]})
    assert s.status_code == 201, s.text
    assert [p["data"]["tipo"] for p in s.json()["points"]] == ["360°", "Lente dual 180°"]


def test_precio_de_mano_de_obra_editable(client, auth):
    """Andres: 'que se pueda editar el precio de mano de obra'. Dos niveles: la tarifa general en Ajustes y el
    precio de un levantamiento puntual en su costeo, sin tocar la general."""
    assert client.put("/settings", json={"labor_day_cost": 30000, "labor_day_cost_civil": 20000, "per_diem_cost": 5000, "travel_cost": 10000}).status_code == 200
    body = {"kind": "cctv", "labor": {"tecnico": {"people": 2, "days": "1"}, "civil": {"people": 1, "days": "2"}}, "points": [{"code": "CAM-01", "data": {}}]}
    s = client.post("/surveys", json=body).json()
    c = client.get(f"/surveys/{s['id']}/costing").json()
    tipos = {t["key"]: t for t in c["labor"]["types"]}
    assert Decimal(str(tipos["tecnico"]["day_cost"])) == 30000 and Decimal(str(tipos["civil"]["day_cost"])) == 20000
    assert Decimal(str(c["labor"]["cost"])) == 30000 * 2 + 20000 * 2
    assert Decimal(str(c["labor"]["viaticos"])) == 5000 * 4 and Decimal(str(c["labor"]["transport"])) == 10000

    # solo para este levantamiento
    c2 = client.put(f"/surveys/{s['id']}/labor-rates", json={"tecnico": 45000, "transport": 0}).json()
    t2 = {t["key"]: t for t in c2["labor"]["types"]}
    assert Decimal(str(t2["tecnico"]["day_cost"])) == 45000 and t2["tecnico"]["custom"] is True
    assert Decimal(str(t2["tecnico"]["day_cost_default"])) == 30000 and t2["civil"]["custom"] is False
    assert Decimal(str(c2["labor"]["cost"])) == 45000 * 2 + 20000 * 2 and Decimal(str(c2["labor"]["transport"])) == 0

    # el tecnico vuelve a guardar el levantamiento: la tarifa ajustada sobrevive
    client.put(f"/surveys/{s['id']}", json=body)
    c3 = client.get(f"/surveys/{s['id']}/costing").json()
    assert Decimal(str({t["key"]: t for t in c3["labor"]["types"]}["tecnico"]["day_cost"])) == 45000

    # la tarifa general no cambio para otros levantamientos
    otro = client.post("/surveys", json=body).json()
    assert Decimal(str({t["key"]: t for t in client.get(f"/surveys/{otro['id']}/costing").json()["labor"]["types"]}["tecnico"]["day_cost"])) == 30000

    # volver a Ajustes
    c4 = client.put(f"/surveys/{s['id']}/labor-rates", json={}).json()
    assert Decimal(str({t["key"]: t for t in c4["labor"]["types"]}["tecnico"]["day_cost"])) == 30000
