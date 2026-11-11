"""O4-07: portada con secciones ordenables, categorias administrables y colecciones por regla.

- Sin tocar nada, el orden de las secciones es el de hoy (las nuevas nacen ocultas); reordenar u ocultar cambia lo que
  la portada publica recibe al guardar.
- «Lo más pedido» solo con ventas reales (pedidos pagados en 90 dias).
- Categorias: orden, ocultar, imagen y texto llegan al arbol publico; sin ajustes, el de siempre.
- Colecciones: se actualizan solas con la existencia, responden 200 y una direccion que no existe da 404 real.
Datos ficticios, precios de 2 000 colones o menos."""

from sqlalchemy import select

from app.models import Product
from app.services import store_showcase as sv
from app.services import store_snapshot
from tests.test_tienda_inventario import _in, _prod, _publish, _whs

S = "/public/store/crimson"
HOY = [k for k, _, _ in sv.PORTADA_SECTIONS]


def _solo_estos(db_session):
    for p in db_session.scalars(select(Product)):
        p.show_on_web = False
    db_session.commit()


def test_sin_tocar_nada_la_portada_queda_igual_y_reordenar_la_cambia(client, auth):
    _publish(client)
    sc = client.get("/store/showcase").json()
    assert [s["key"] for s in sc["sections"]] == HOY
    pub = client.get(f"{S}/showcase").json()
    visibles = [k for k, _, vis in sv.PORTADA_SECTIONS if vis]
    assert [s["key"] for s in pub["sections"]] == visibles  # la nueva («coleccion») nace oculta
    nuevo = [
        {"key": "categorias", "visible": True, "title": ""},
        {"key": "hero", "visible": True, "title": ""},
        {"key": "marcas", "visible": False, "title": ""},
    ]
    assert client.put("/store/showcase", json={"sections": nuevo}).status_code == 200
    pub = [s["key"] for s in client.get(f"{S}/showcase").json()["sections"]]
    assert pub[:2] == ["categorias", "hero"] and "marcas" not in pub
    # lo que no se mando queda con su valor por defecto, al final y sin repetir
    assert len(client.get("/store/showcase").json()["sections"]) == len(HOY)


def test_lo_mas_pedido_solo_con_ventas_reales(client, auth):
    r = client.put("/store/showcase", json={"sections": [{"key": "seleccion", "visible": True, "title": "Lo más pedido"}]})
    assert r.status_code == 422 and "pedidos pagados" in r.json()["detail"]
    assert client.put("/store/showcase", json={"sections": [{"key": "seleccion", "visible": True, "title": "Selección de la casa"}]}).status_code == 200


def test_categorias_orden_ocultar_imagen_y_texto(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    a = client.post("/categories", json={"name": "Cámaras de prueba"}).json()
    b = client.post("/categories", json={"name": "Redes de prueba"}).json()
    c = client.post("/categories", json={"name": "Oculta de prueba"}).json()
    for i, cat in enumerate((a, a, b, c)):
        r = client.post("/products", json={"name": f"Equipo {i}", "code": f"CTG-{i}", "price": 1500, "show_on_web": True, "category_id": cat["id"]})
        assert r.status_code == 201, r.text
    antes = [n["id"] for n in client.get(f"{S}/showcase").json()["categories"]]
    assert antes[0] == a["id"]  # sin ajustes: por cantidad, como siempre
    cfg = {
        str(b["id"]): {"position": 0, "image": "/media/redes.png", "seo_text": "Redes de prueba."},
        str(a["id"]): {"position": 1},
        str(c["id"]): {"hidden": True},
    }
    assert client.put("/store/showcase", json={"categories": cfg}).status_code == 200
    arbol = client.get(f"{S}/showcase").json()["categories"]
    assert [n["id"] for n in arbol] == [b["id"], a["id"]]
    assert arbol[0]["image"] == "/media/redes.png" and arbol[0]["description"] == "Redes de prueba."
    # una categoria de otra empresa (o que no existe) no se guarda
    assert client.put("/store/showcase", json={"categories": {"999999": {"hidden": True}}}).status_code == 200
    assert "999999" not in client.get("/store/showcase").json()["categories"]


def test_coleccion_por_regla_se_actualiza_sola_y_404_real(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    main, _ = _whs(client)
    hay = _prod(client, "COL-1", "Cámara en bodega", price=1500)
    _prod(client, "COL-2", "Cámara sin existencia", price=1500)
    col = {"slug": "listo-para-entregar", "title": "Listo para entregar", "active": True, "rule": {"stock_only": True}}
    assert client.put("/store/showcase", json={"collections": [col]}).status_code == 200
    r = client.get(f"{S}/collections/listo-para-entregar")
    assert r.status_code == 200 and r.json()["n"] == 0
    _in(client, hay["id"], main["id"], 2)  # llega existencia: la coleccion lo muestra sin tocarla
    store_snapshot.rebuild(db_session, auth["tenant"]["id"])  # el rearmado de siempre (worker o siguiente visita)
    r = client.get(f"{S}/collections/listo-para-entregar")
    assert [p["id"] for p in r.json()["products"]] == [hay["id"]]
    assert client.get(f"{S}/collections/no-existe").status_code == 404
    col["active"] = False
    assert client.put("/store/showcase", json={"collections": [col]}).status_code == 200
    assert client.get(f"{S}/collections/listo-para-entregar").status_code == 404
    # precio maximo con IVA y direcciones unicas
    barata = {"slug": "baratas", "title": "Por menos de ₡1 000", "rule": {"max_price": 1000}}
    assert client.put("/store/showcase", json={"collections": [barata, {**barata}]}).status_code == 422
    assert client.put("/store/showcase", json={"collections": [barata]}).status_code == 200
    assert client.get(f"{S}/collections/baratas").json()["n"] == 0  # 1 500 + IVA no entra


def test_pagina_de_coleccion_seo_exists_y_sitemap(client, auth, db_session):
    H = {"X-Store-Host": "tienda.ejemplo.com"}
    assert client.put("/store", json={"kind": "tienda", "published": True, "domain": "tienda.ejemplo.com"}).status_code == 200
    col = {"slug": "listo-para-entregar", "title": "Listo para entregar", "text": "En bodega hoy.", "rule": {"stock_only": True}}
    assert client.put("/store/showcase", json={"collections": [col]}).status_code == 200
    # nginx (auth_request): existe -> 204; no existe -> 403 (404 real con la vitrina)
    assert client.get("/public/seo/exists/coleccion/listo-para-entregar", headers=H).status_code == 204
    assert client.get("/public/seo/exists/coleccion/no-existe", headers=H).status_code == 403
    head = client.get("/public/seo/head/coleccion/listo-para-entregar", headers=H).text
    assert "<title>Listo para entregar" in head and head.count("application/ld+json") == 1 and "CollectionPage" in head
    assert 'href="https://tienda.ejemplo.com/coleccion/listo-para-entregar"' in head
    assert "https://tienda.ejemplo.com/coleccion/listo-para-entregar" in client.get("/public/seo/sitemap.xml", headers=H).text
    r = client.get(f"{S}/collections/listo-para-entregar").json()
    assert r["seo"]["title"].startswith("Listo para entregar")
    # apagada: fuera del sitemap y 404
    col["active"] = False
    assert client.put("/store/showcase", json={"collections": [col]}).status_code == 200
    assert "/coleccion/" not in client.get("/public/seo/sitemap.xml", headers=H).text
    assert client.get("/public/seo/exists/coleccion/listo-para-entregar", headers=H).status_code == 403
