"""Inventarios de proveedores: catalogo del proveedor separado del propio, lista de Eurocomp con categorias,
idempotencia, inactivar faltantes, vinculos por SKU/modelo, "Agregar a mi catalogo" con margen sobre venta,
search_offers para levantamientos y permisos (la vendedora no ve costos).

La lista es SINTETICA (mismo formato que la real de Eurocomp): la real es informacion comercial del proveedor."""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Product, SupplierItem
from app.services import supplier_catalog as sc
from app.services import supplier_lists as sl
from tests.test_campo import _login
from tests.test_session5 import _xlsx

LISTA = [
    ["EUROCOMP COSTA RICA"],
    ["VENTA Y DISTRIBUCION DE PARTES DE COMPUTADORA"],
    [None],
    ["Listado de Precios"],
    [None, None, None, None, None, "Total Piezas", 0],
    ["Fotografía", "Código", "Descripción", "Stock", "Precio", "Ordenar", "SubTotal"],
    ["ACCESORIOS Cables "],
    [None, 1205150, "CABLE DE RED UTP CAT6 HIKVISION DS-1LN6-UE-W(O-STD) 23AWG 305M 100% COBRE", 1, 122.31, 0, 0],
    ["SEGURIDAD Camaras "],
    [None, "SG2010", "CÁMARA IP HIKVISION DS-2CD1183G2-LIUF(2.8MM) DOMO 8MP SMART HYBRID\n\n- Resolución: 8 MP\n- Lente: 2.8 mm", 26, 75.89, 0, 0],
    [None, "SG2104", "CAMARA BULLET HIKVISION DS-2CD1047G3-LIU(2.8MM) 4 MP", 100, 70.91, 0, 0],
    [None, "SG2200", "CAMARA DOMO HIKVISION DS-2CD1143G2-LIU(2.8MM) 4 MP ACUSENSE EXTERIOR", 4, 650, 0, 0],
    ["REDES Y CONECTIVDAD Switches "],
    [None, "RC5079", "SWITCH HIKVISION DS-3E1520P-EI/M(O-STD) 16 PUERTOS GIGABIT POE", 10, 139.85, 0, 0],
    ["RESPALDO ELÉCTRICO UPS "],
    [None, "UP100", "UPS HIKVISION DS UPS2000 X 2000VA 1200W", 3, 0, 0, 0],
]


def _imp(client, rows=LISTA, commit=False, headers=None, **params):
    params = {"supplier": "Eurocomp", **params, "commit": commit}
    r = client.post("/supplier-catalog/import", params=params, files={"file": ("lista.xlsx", _xlsx(rows), "application/octet-stream")}, headers=headers or {})
    return r


def _items(client, **params):
    return client.get("/supplier-catalog/items", params={"limit": 200, **params}).json()


def _by_code(client, code, **params):
    return next(i for i in _items(client, estado="todos", **params)["items"] if i["code"] == code)


def _tax_ids(client):
    return [next(t["id"] for t in client.get("/taxes").json() if Decimal(str(t["rate"])) == 13)]


# ---------- parseo ----------
def test_modelos_y_categorias_del_formato_eurocomp():
    assert sc.extract_model("CÁMARA DOMO IP COLORVU HIKVISION DS-2CD1167G3-LIU(2.8MM)(LATAM ACUSENSE) 6MP") == "DS-2CD1167G3-LIU(2.8MM)"
    assert sc.extract_model("CONTROL DE ACCESO BARRERA VEHICULAR HIKVISION DS-TMG300-DR(3.4~6m telescopic)(O-STD)") == "DS-TMG300-DR"
    assert sc.extract_model("GRABADORA DVR HIKVISION IDS-7216HQHI-M1/S 16 CANALES") == "IDS-7216HQHI-M1/S"
    assert sc.extract_model("DASHCAM HIKVISION AE-DC2018-K2 1080P WIFI") == "AE-DC2018-K2"
    assert sc.extract_model("KIT DE CÁMARAS WIFI HIKVISION NKS4245WBTH 4MP 5MP") == "NKS4245WBTH"
    assert sc.extract_model("TARJETA DE PROXIMIDAD HIKVISION MIFARE Y EMID S50+TK4100") is None
    assert sc.model_key("DS-2CD1047G3-LIU") == "DS2CD1047G3LIU"
    assert sc.base_key("DS-2CD1183G2-LIUF(2.8MM)") == sc.model_key("DS-2CD1183G2-LIUF")
    assert sl.split_category("REDES Y CONECTIVDAD Amplificadores de Señal ") == ("Redes y conectividad", "Amplificadores de Señal")
    assert sl.split_category("SEGURIDAD Alarmas/Sirenas") == ("Seguridad", "Alarmas/Sirenas")

    p = sl.parse(_xlsx(LISTA), "lista.xlsx")
    assert p.format == "eurocomp" and p.header_row == 6 and p.categories == 4
    assert p.columns["code"] == "Código" and p.columns["name"] == "Descripción" and p.columns["cost"] == "Precio" and p.columns["stock"] == "Stock"
    rows = {r.code: r for r in p.rows}
    assert len(rows) == 6
    cable = rows["1205150"]  # el codigo numerico de Excel queda como texto
    assert (cable.category, cable.subcategory) == ("Accesorios", "Cables") and cable.cost == Decimal("122.31")
    cam = rows["SG2010"]
    assert cam.name.startswith("CÁMARA IP HIKVISION") and "- Lente: 2.8 mm" in cam.description and "\n" not in cam.name
    assert cam.model == "DS-2CD1183G2-LIUF(2.8MM)" and cam.brand == "Hikvision" and cam.stock == 26
    assert (cam.category, cam.subcategory) == ("Seguridad", "Camaras")
    assert (rows["RC5079"].category, rows["RC5079"].subcategory) == ("Redes y conectividad", "Switches")
    assert (rows["UP100"].category, rows["UP100"].subcategory) == ("Respaldo eléctrico", "UPS")


# ---------- importar: vista previa, idempotencia, inactivar ----------
def test_importar_vista_previa_y_confirmar_es_idempotente(client, auth):
    prev = _imp(client).json()
    s = prev["summary"]
    assert prev["format"] == "eurocomp" and prev["commit"] is False
    assert (s["nuevo"], s["dudoso"], s["error"], s["inactivar"]) == (5, 1, 0, 0)  # UP100 viene con precio 0
    dud = next(r for r in prev["result"] if r["action"] == "dudoso")
    assert dud["code"] == "UP100" and "precio en cero" in dud["warnings"]
    assert prev["supplier"]["new"] is True and prev["supplier"]["id"] is None
    assert _items(client)["total"] == 0  # la vista previa no guarda nada
    assert not [x for x in client.get("/suppliers").json() if x["name"] == "Eurocomp"]

    done = _imp(client, commit=True, list_date=(date.today() - timedelta(days=2)).isoformat()).json()
    assert done["summary"]["nuevo"] == 5 and done["supplier"]["id"]
    data = _items(client, supplier_id=done["supplier"]["id"])
    assert data["total"] == 6
    cam = next(i for i in data["items"] if i["code"] == "SG2010")
    assert cam["category"] == "Seguridad" and cam["subcategory"] == "Camaras" and cam["model"] == "DS-2CD1183G2-LIUF(2.8MM)"
    assert Decimal(str(cam["cost"])) == Decimal("75.89") and cam["currency"] == "USD" and cam["stock"] == 26
    assert cam["list_date"] == (date.today() - timedelta(days=2)).isoformat() and cam["source"] == "excel"
    assert Decimal(str(cam["cost_crc"])) == (Decimal("75.89") * Decimal(str(data["fx"]))).quantize(Decimal("0.01"))
    # los productos propios no se tocan: el catalogo del proveedor es aparte
    assert not client.get("/products", params={"q": "SG2010"}).json()["items"]

    # mismo archivo otra vez: no duplica ni cambia nada (el dudoso sigue marcado)
    again = _imp(client, commit=True, supplier_id=done["supplier"]["id"]).json()
    assert again["summary"]["sin_cambios"] == 5 and again["summary"]["dudoso"] == 1 and again["summary"]["nuevo"] == 0
    assert _items(client)["total"] == 6
    sup = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"] == "Eurocomp")
    assert sup["items"] == 6 and sup["active"] == 6 and sup["last_list"]["list_date"] == date.today().isoformat()
    assert {"category": "Seguridad", "subcategory": "Camaras", "count": 3} in sup["categories"]
    assert "SiReTT" in sup["api"]["label"]
    hist = client.get("/supplier-catalog/imports", params={"supplier_id": sup["id"]}).json()
    assert len(hist) == 2 and hist[0]["summary"]["sin_cambios"] == 5


def test_lista_nueva_inactiva_lo_que_ya_no_viene_y_marca_saltos_de_costo(client, auth):
    _imp(client, commit=True)
    nueva = [r for r in LISTA if not (len(r) > 1 and r[1] == "RC5079")]
    nueva = [([None, "SG2104", r[2], 80, 95.0, 0, 0] if len(r) > 1 and r[1] == "SG2104" else r) for r in nueva]
    res = _imp(client, rows=nueva, commit=True).json()
    s = res["summary"]
    assert s["inactivar"] == 1 and res["inactivar"][0]["code"] == "RC5079"
    row = next(r for r in res["result"] if r["code"] == "SG2104")
    assert row["action"] == "dudoso" and row["base"] == "actualizado" and any("costo cambió +34 %" in w for w in row["warnings"])
    sw = _by_code(client, "RC5079")
    assert sw["active"] is False  # no se borra: queda inactivo
    assert _items(client)["total"] == 5 and _items(client, estado="inactivos")["total"] == 1
    cam = _by_code(client, "SG2104")
    assert Decimal(str(cam["cost"])) == Decimal("95") and Decimal(str(cam["prev_cost"])) == Decimal("70.91") and cam["stock"] == 80
    # si vuelve en la lista siguiente, se reactiva
    back = _imp(client, commit=True).json()
    assert back["summary"]["actualizado"] >= 1 and _by_code(client, "RC5079")["active"] is True


# ---------- vinculos con el catalogo propio ----------
def test_vinculos_por_sku_y_modelo_actualizan_costo_y_bajo_pedido(client, auth, db_session):
    tax = _tax_ids(client)
    # producto de la importacion anterior (legado): codigo del proveedor en supplier_sku
    p1 = client.post(
        "/products", json={"name": "Cámara bullet 4 MP", "code": "CAM-B4", "price": 60000, "supplier_sku": "SG2104", "show_on_web": True, "tax_ids": tax}
    ).json()
    # modelo en el nombre, sin campo modelo
    p2 = client.post("/products", json={"name": "Switch PoE 16p DS-3E1520P-EI/M(O-STD)", "code": "SW-16", "price": 120000, "tax_ids": tax}).json()
    # otra variante del modelo (sin lente): solo sugerencia
    p3 = client.post("/products", json={"name": "Domo 8MP", "code": "D8", "price": 70000, "model": "DS-2CD1183G2-LIUF", "tax_ids": tax}).json()
    # nombre parecido: solo sugerencia
    p4 = client.post("/products", json={"name": "Camara domo Hikvision 4 MP acusense exterior", "code": "CD4", "price": 450000, "tax_ids": tax}).json()

    prev = _imp(client).json()
    assert prev["summary"]["vinculos"] == 2 and prev["summary"]["sugerencias"] >= 2
    assert {(v["code"], v["method"]) for v in prev["vinculos"]} == {("SG2104", "sku"), ("RC5079", "modelo")}
    assert db_session.get(Product, p1["id"]).supplier_stock is None  # la vista previa no toca el producto
    res = _imp(client, commit=True).json()
    assert res["summary"]["productos"] == 2
    assert next(x for x in res["productos"] if x["product_id"] == p1["id"])["stock_after"] == 100

    db_session.expire_all()
    pr1 = db_session.get(Product, p1["id"])
    assert Decimal(str(pr1.cost)) == Decimal("70.91") and pr1.cost_currency == "USD" and pr1.supplier_stock == 100 and pr1.supplier_updated_at
    assert pr1.supplier_id == res["supplier"]["id"] and pr1.supplier_sku == "SG2104"
    assert db_session.get(Product, p2["id"]).supplier_stock == 10
    assert db_session.get(Product, p3["id"]).cost is None and db_session.get(Product, p4["id"]).cost is None  # sin confirmar no se vincula

    sug = {s["code"]: s for s in client.get("/supplier-catalog/suggestions").json()}
    assert sug["SG2010"]["suggestions"][0]["product_id"] == p3["id"] and sug["SG2010"]["suggestions"][0]["reason"] == "modelo_parecido"
    assert any(x["product_id"] == p4["id"] and x["reason"] == "nombre" for x in sug["SG2200"]["suggestions"])
    det = client.get(f"/supplier-catalog/items/{_by_code(client, 'SG2200')['id']}").json()
    assert det["suggestions"] and det["proposal"]["code"] == "DS-2CD1143G2-LIU(2.8MM)"

    # tienda: sin existencia propia y con stock del proveedor -> Bajo pedido (la logica de siempre)
    client.put("/store", json={"kind": "tienda", "published": True})
    pub = next(x for x in client.get("/public/store/crimson/products").json() if x["id"] == p1["id"])
    assert pub["availability"]["state"] == "bajo_pedido"

    # lista de productos: costo del proveedor con fecha y margen real sobre la venta; filtro con / sin proveedor
    con = {x["id"]: x for x in client.get("/products", params={"proveedor": "con", "limit": 100}).json()["items"]}
    sin = {x["id"] for x in client.get("/products", params={"proveedor": "sin", "limit": 100}).json()["items"]}
    assert {p1["id"], p2["id"]} <= set(con) and {p3["id"], p4["id"]} <= sin and p1["id"] not in sin
    row = con[p1["id"]]
    assert row["cost_source"] == "proveedor" and row["cost_supplier"] == "Eurocomp" and row["cost_date"] == date.today().isoformat()
    fx = Decimal(str(_items(client)["fx"]))
    cost_crc = (Decimal("70.91") * fx).quantize(Decimal("0.01"))
    assert Decimal(str(row["margin_real_pct"])) == ((Decimal(60000) - cost_crc) / Decimal(60000) * 100).quantize(Decimal("0.1"))
    assert row["margin_provisional"] is False and row["supplier_offers"] == 1
    p4row = next(x for x in client.get("/products", params={"q": "CD4"}).json()["items"])
    assert p4row["cost_source"] is None and p4row["margin_provisional"] is True  # sin costo: margen objetivo

    # vinculo manual confirmado: el producto toma costo y existencia del proveedor
    sg2200 = _by_code(client, "SG2200")
    r = client.post(f"/supplier-catalog/items/{sg2200['id']}/link", json={"product_id": p4["id"]})
    assert r.status_code == 200 and r.json()["product"]["id"] == p4["id"] and r.json()["link_method"] == "manual"
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, p4["id"]).cost)) == Decimal("650") and db_session.get(Product, p4["id"]).supplier_stock == 4
    offers = client.get("/supplier-catalog/offers", params={"product_id": p4["id"]}).json()["offers"]
    assert len(offers) == 1 and offers[0]["best"] is True and Decimal(str(offers[0]["cost"])) == Decimal("650")

    # quitar el vinculo: el producto deja de reportar existencia del proveedor (no queda "Bajo pedido" por error)
    sg2104 = _by_code(client, "SG2104")
    assert client.delete(f"/supplier-catalog/items/{sg2104['id']}/link").status_code == 200
    db_session.expire_all()
    assert db_session.get(Product, p1["id"]).supplier_stock is None
    pub = next(x for x in client.get("/public/store/crimson/products").json() if x["id"] == p1["id"])
    assert pub["availability"]["state"] != "bajo_pedido"

    # si el item vinculado deja de venir en la lista, el producto ya no tiene existencia del proveedor
    nueva = [r for r in LISTA if not (len(r) > 1 and r[1] == "RC5079")]
    _imp(client, rows=nueva, commit=True)
    db_session.expire_all()
    assert db_session.get(Product, p2["id"]).supplier_stock == 0


def test_agregar_a_mi_catalogo_con_margen_sobre_venta(client, auth, db_session):
    _imp(client, commit=True)
    item = _by_code(client, "SG2200")  # costo 650 USD
    r = client.post(f"/supplier-catalog/items/{item['id']}/catalog", json={"currency": "USD", "margin_pct": 35})
    assert r.status_code == 201, r.text
    out = r.json()
    # margen SOBRE LA VENTA: 650 / (1 - 0.35) = 1000 (no 650 x 1.35 = 877.50)
    assert Decimal(str(out["price"])) == Decimal("1000.00") and out["currency"] == "USD"
    full = client.get(f"/products/{out['product_id']}").json()
    assert full["code"] == "DS-2CD1143G2-LIU(2.8MM)" and full["model"] == "DS-2CD1143G2-LIU(2.8MM)"  # codigo propio = modelo universal
    assert full["supplier_sku"] == "SG2200" and full["cabys_code"] is None and full["show_on_web"] is False
    assert Decimal(str(full["cost"])) == Decimal("650") and full["cost_currency"] == "USD" and full["supplier_stock"] == 4
    assert full["tax_rate"] == 13 and full["brand"] == "Hikvision"
    assert full["cost_source"] == "proveedor" and Decimal(str(full["margin_real_pct"])) == Decimal("35.0")
    assert _by_code(client, "SG2200")["product"]["id"] == out["product_id"]
    assert client.post(f"/supplier-catalog/items/{item['id']}/catalog", json={}).status_code == 409  # ya vinculado

    # en colones con el margen de la empresa: costo x tipo de cambio / (1 - margen), redondeado a la centena
    from app.models import Tenant
    from app.services import pricing

    tenant = db_session.get(Tenant, auth["tenant"]["id"])
    m = pricing.default_margin(tenant)
    cam = _by_code(client, "SG2104")
    out2 = client.post(f"/supplier-catalog/items/{cam['id']}/catalog", json={}).json()
    fx = Decimal(str(_items(client)["fx"]))
    esperado = pricing.sale_price(db_session, Decimal("70.91"), "USD", "CRC", m, fx)
    assert Decimal(str(out2["price"])) == esperado and out2["currency"] == "CRC"
    assert Decimal(str(out2["margin_pct"])) == m
    # sin precio en la lista no se puede agregar
    ups = _by_code(client, "UP100")
    assert client.post(f"/supplier-catalog/items/{ups['id']}/catalog", json={}).status_code == 422


# ---------- servicio para levantamientos ----------
def test_search_offers_y_best_offer(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tax = _tax_ids(client)
    p1 = client.post("/products", json={"name": "Cámara bullet 4 MP", "code": "CAM-B4", "price": 60000, "supplier_sku": "SG2104", "tax_ids": tax}).json()
    _imp(client, commit=True)

    rows = sc.search_offers(db_session, tid, terms=["camara", "domo"], attrs={"resolucion": "4 MP", "ambiente": "Exterior"})
    assert rows, "debe encontrar camaras"
    top = rows[0]
    assert top["source"] == "proveedor" and top["code"] == "SG2200" and top["supplier"] == "Eurocomp"
    for k in (
        "source",
        "product_id",
        "supplier_item_id",
        "supplier",
        "code",
        "name",
        "model",
        "cost",
        "currency",
        "cost_crc",
        "sale_price",
        "stock_propio",
        "stock_proveedor",
        "updated_at",
    ):
        assert k in top
    fx = sc.usd_rate(db_session)
    assert top["cost"] == Decimal("650") and top["cost_crc"] == (Decimal("650") * fx).quantize(Decimal("0.01")) and top["stock_proveedor"] == 4
    codes = [r["code"] for r in rows]
    assert "SG2010" in codes  # domo 8 MP: menos puntaje que el de 4 MP
    assert codes.index("SG2200") < codes.index("SG2010")
    # el item vinculado no se repite: sale como producto propio con el costo y la existencia del proveedor
    assert "SG2104" not in codes
    own = sc.search_offers(db_session, tid, terms=["camara", "bullet"])
    mine = next(r for r in own if r["product_id"] == p1["id"])
    assert mine["source"] == "propio" and mine["code"] == "CAM-B4" and mine["sale_price"] == Decimal("60000")
    assert mine["stock_proveedor"] == 100 and mine["cost"] == Decimal("70.91") and mine["supplier"] == "Eurocomp" and mine["stock_propio"] == 0

    # categoria del proveedor como filtro; sin criterios no devuelve nada
    sw = sc.search_offers(db_session, tid, terms=["hikvision"], category="switches")
    assert [r["code"] for r in sw] == ["RC5079"]
    assert sc.search_offers(db_session, tid) == []
    assert len(sc.search_offers(db_session, tid, terms=["hikvision"], limit=2)) == 2

    best = sc.best_offer_for_product(db_session, db_session.get(Product, p1["id"]))
    item = db_session.query(SupplierItem).filter_by(code="SG2104").one()
    assert best["supplier_item_id"] == item.id and best["cost"] == Decimal("70.91") and best["stock_proveedor"] == 100
    assert sc.best_offer_for_product(db_session, db_session.get(Product, client.get("/products").json()["items"][-1]["id"])) is None
    # endpoint del portal con el mismo servicio
    api = client.get("/supplier-catalog/search", params={"q": "switch poe"}).json()
    rc = next(r for r in api if r["code"] == "RC5079")
    assert rc["source"] == "proveedor" and "cost" in rc
    assert api[0]["source"] == "propio"  # el switch de la semilla esta en bodega: va primero


# ---------- permisos ----------
def test_vendedora_ve_existencias_del_proveedor_pero_nunca_el_costo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tax = _tax_ids(client)
    p1 = client.post("/products", json={"name": "Cámara bullet 4 MP", "code": "CAM-B4", "price": 60000, "supplier_sku": "SG2104", "tax_ids": tax}).json()
    _imp(client, commit=True)
    vend = _login(client, db_session, tid, "vendedora.prov@ejemplo.com", "ventas")

    data = client.get("/supplier-catalog/items", headers=vend).json()
    assert data["total"] == 6 and data["fx"] is None
    it = next(i for i in data["items"] if i["code"] == "SG2104")
    assert it["stock"] == 100 and "cost" not in it and "cost_crc" not in it and "prev_cost" not in it
    det = client.get(f"/supplier-catalog/items/{it['id']}", headers=vend).json()
    assert "cost" not in det and "proposal" not in det and det["raw"] is None
    assert _imp(client, commit=True, headers=vend).status_code == 403
    assert client.post(f"/supplier-catalog/items/{it['id']}/catalog", json={}, headers=vend).status_code == 403
    assert client.post(f"/supplier-catalog/items/{it['id']}/link", json={"product_id": p1["id"]}, headers=vend).status_code == 403
    offers = client.get("/supplier-catalog/offers", params={"product_id": p1["id"]}, headers=vend).json()["offers"]
    assert offers and "cost" not in offers[0] and "cost_crc" not in offers[0] and offers[0]["stock_proveedor"] == 100
    srch = client.get("/supplier-catalog/search", params={"q": "camara"}, headers=vend).json()
    assert srch and all("cost" not in r and "cost_crc" not in r for r in srch)
    prods = client.get("/products", params={"proveedor": "con"}, headers=vend).json()["items"]
    row = next(x for x in prods if x["id"] == p1["id"])
    assert row["cost"] is None and row["cost_source"] is None and row["margin_real_pct"] is None
    full = client.get(f"/products/{p1['id']}", headers=vend).json()
    assert full["cost"] is None and full["cost_crc"] is None


# ---------- otros proveedores: columnas por sinonimo o mapeo ----------
def test_lista_generica_con_mapeo_de_columnas(client, auth):
    csv = "SKU;Producto;Marca;Existencia;Costo;Familia\nTP-01;Switch TP-Link 8 puertos;TP-Link;12;25,50;Redes\nTP-02;Router TP-Link AX1800;TP-Link;0;48,00;Redes\n"
    r = client.post(
        "/supplier-catalog/import", params={"supplier": "Intcomex", "commit": True}, files={"file": ("lista.csv", csv.encode("utf-8"), "text/csv")}
    ).json()
    assert r["format"] == "generico" and r["summary"]["nuevo"] == 2 and r["columns"]["stock"] == "Existencia"
    sw = _by_code(client, "TP-01")
    assert sw["brand"] == "TP-Link" and sw["category"] == "Redes" and Decimal(str(sw["cost"])) == Decimal("25.50") and sw["stock"] == 12

    raro = "Ref Prov,Articulo,Precio Neto\nX-1,Cable HDMI 2 m,3.10\n"
    bad = client.post("/supplier-catalog/import", params={"supplier": "Otro"}, files={"file": ("l.csv", raro.encode(), "text/csv")})
    assert bad.status_code == 422 and "columnas" in bad.json()["detail"]
    import json

    ok = client.post(
        "/supplier-catalog/import",
        params={"supplier": "Otro", "commit": True, "mapping": json.dumps({"code": "Ref Prov", "name": "Articulo", "cost": "Precio Neto"})},
        files={"file": ("l.csv", raro.encode(), "text/csv")},
    ).json()
    assert ok["summary"]["nuevo"] == 1 and Decimal(str(_by_code(client, "X-1")["cost"])) == Decimal("3.10")
    # cada proveedor tiene su catalogo: el mismo codigo puede existir en dos proveedores
    assert client.get("/supplier-catalog/items", params={"q": "hdmi"}).json()["total"] == 1
