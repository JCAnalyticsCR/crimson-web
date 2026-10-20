"""Coherencia: un mismo producto dice lo mismo en TODAS las superficies (tienda: lista, ficha, vitrina, ficha de la
vitrina y /availability en vivo; panel: ficha, lista de productos, Inventario -> Disponibilidad; portal del aliado:
catalogo e inventario; punto de venta; recomendacion de equipos). Mismo estado y mismas cantidades, porque todas
usan la funcion unica (services/availability). Las bodegas de garantias nunca suman."""

from decimal import Decimal

from app.models import Product
from app.services import equipment_reco, supplier_catalog
from app.services import inventory as invsvc
from app.services import reservations as rsv
from tests.aliados_helpers import escenario
from tests.test_disponibilidad import supplier_item

D = Decimal


def _setup(client, db, s):
    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    client.patch(f"/products/{s.prod}/web", json={"show_on_web": True})
    main = invsvc.default_warehouse(db, s.tid)
    invsvc.move(db, s.tid, None, s.prod, main.id, "entrada", D(3))
    # 4 equipos de clientes en garantia del mismo modelo: no son existencia vendible en ningun lado
    rev = invsvc.special_warehouse(db, s.tid, "revision")
    invsvc.move(db, s.tid, None, s.prod, rev.id, "entrada", D(4))
    db.commit()
    r = rsv.create(db, s.tid, None, s.prod, D(1), main.id)  # proyecto aparta 1
    db.commit()
    rsv.confirm(db, r, None)
    supplier_item(db, s.prod, 5, hours_ago=3)


def _all_surfaces(client, db, s) -> dict[str, tuple]:
    pid = s.prod
    out = {}
    lista = next(x for x in client.get("/public/store/crimson/products").json() if x["id"] == pid)["availability"]
    out["tienda_lista"] = (lista["state"], lista["cantidad"])
    ficha = client.get(f"/public/store/crimson/products/{pid}").json()["availability"]
    out["tienda_ficha"] = (ficha["state"], ficha["cantidad"])
    vit = next(x for x in client.get("/public/store/crimson/catalog", params={"limit": 60}).json()["items"] if x["id"] == pid)["availability"]
    out["vitrina"] = (vit["state"], vit["cantidad"])
    vfi = client.get(f"/public/store/crimson/item/x-{pid}").json()["availability"]
    out["vitrina_ficha"] = (vfi["state"], vfi["cantidad"])
    viv = client.get("/public/store/crimson/availability", params={"ids": str(pid)}).json()[str(pid)]
    out["en_vivo"] = (viv["state"], viv["cantidad"])
    interno = client.get(f"/products/{pid}/availability").json()
    out["ficha_interna"] = (interno["state"], interno["own_free"])
    prod = client.get(f"/products/{pid}").json()["availability"]
    out["producto"] = (prod["state"], prod["own_free"])
    lst = next(x for x in client.get("/products", params={"q": "ALI-01"}).json()["items"] if x["id"] == pid)["availability"]
    out["productos_lista"] = (lst["state"], lst["own_free"])
    disp = next(x for x in client.get("/stock/availability").json()["items"] if x["id"] == pid)
    out["inventario_disponibilidad"] = (disp["state"], disp["own_free"])
    ali = client.get(f"/aliado/catalogo/{pid}", headers=s.a_jefe).json()["disponibilidad_detalle"]
    out["aliado_catalogo"] = (ali["state"], ali["en_bodega"])
    inv = next(x for x in client.get("/aliado/inventario?con_existencia=false", headers=s.a_jefe).json()["rows"] if x["product_id"] == pid)
    out["aliado_inventario"] = (inv["estado"], inv["disponible_total"])
    pos = next(x for x in client.get("/pos/catalog").json() if x["id"] == pid)
    out["pos"] = (pos["availability"]["state"], pos["stock"])
    p = db.get(Product, pid)
    off = equipment_reco.own_offer(db, p)
    out["recomendacion_a_mano"] = (off["availability_state"], off["stock_propio"])
    hit = next(o for o in supplier_catalog.search_offers(db, s.tid, terms=["Cámara demo aliado"]) if o.get("product_id") == pid and o["source"] == "propio")
    out["recomendacion_busqueda"] = (hit["availability_state"], hit["stock_propio"])
    return out


def test_mismo_estado_y_cantidades_en_todas_las_superficies(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    _setup(client, db_session, s)
    got = _all_surfaces(client, db_session, s)
    # 3 en bodega de venta - 1 reservado = 2 libres; los 4 en revision no cuentan
    for where, (state, qty) in got.items():
        assert state == "en_bodega", (where, state)
        assert D(str(qty)) == 2, (where, qty)
    # el proveedor tambien coincide donde se muestra (interno con nombre, aliado sin nombre)
    interno = client.get(f"/products/{s.prod}/availability").json()
    assert interno["supplier_qty"] == 5 and interno["supplier_name"] == "Eurocomp Costa Rica" and interno["max_qty"] == 7
    ali = client.get(f"/aliado/catalogo/{s.prod}", headers=s.a_jefe).json()["disponibilidad_detalle"]
    assert ali["bajo_pedido_cantidad"] == 5 and "Eurocomp" not in str(ali)
    pos = next(x for x in client.get("/pos/catalog").json() if x["id"] == s.prod)["availability"]
    assert pos["supplier_qty"] == 5 and pos["max_qty"] == 7
    off = equipment_reco.own_offer(db_session, db_session.get(Product, s.prod))
    assert off["stock_proveedor"] == 5 and off["supplier_fresh"]


def test_sin_propio_todas_dicen_bajo_pedido_y_con_dato_viejo_consultar(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    client.patch(f"/products/{s.prod}/web", json={"show_on_web": True})
    supplier_item(db_session, s.prod, 4, hours_ago=1)
    got = _all_surfaces(client, db_session, s)
    assert {v[0] for v in got.values()} == {"bajo_pedido"}, got
    assert {D(str(v[1] or 0)) for v in got.values()} == {D(0)}, got
    # el dato del proveedor envejece (mas de 48 h): en todos lados "consultar", y la tienda no lo vende
    supplier_item(db_session, s.prod, 4, hours_ago=50)
    got = _all_surfaces(client, db_session, s)
    assert {v[0] for v in got.values()} == {"consultar"}, got
    viv = client.get("/public/store/crimson/availability", params={"ids": str(s.prod)}).json()[str(s.prod)]
    assert viv["disponible"] is False and viv["accion"] == "whatsapp"
