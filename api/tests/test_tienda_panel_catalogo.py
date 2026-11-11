"""O4-04: Mi Tienda -> Catalogo unificado.

- GET /store/catalog pagina en el servidor (48 por pagina): todos los publicados son alcanzables, con filtros de
  calidad y su numero.
- PATCH /store/catalog/{id}: un solo lugar para todo lo web; permisos por campo (publicar = catalog.publicar);
  marca el catalogo; nunca toca el nombre fiscal ni el codigo; no acepta precio de comparacion a mano.
- Los endpoints viejos (Inventario web, Vitrina -> Por producto, Productos -> publicar) devuelven lo mismo que antes.
Datos ficticios, precios de 2 000 colones o menos."""

from sqlalchemy import select

from app.models import Product, Tenant
from tests.test_session4 import _user_with_role
from tests.test_tienda_inventario import _in, _prod, _publish, _whs


def _solo_estos(db_session):
    for p in db_session.scalars(select(Product)):
        p.show_on_web = False
    db_session.commit()


def _login(client, db_session, auth, email, role):
    pw = _user_with_role(db_session, auth["tenant"]["id"], email, role)
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    return {"Authorization": f"Bearer {tok}"}


def test_paginado_en_el_servidor_y_todos_alcanzables(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    for i in range(53):
        _prod(client, f"CAT-{i:03d}", f"Equipo de catálogo {i:03d}", price=1000 + i)
    r1 = client.get("/store/catalog").json()
    assert r1["total"] == 53 and r1["pages"] == 2 and r1["per_page"] == 48 and len(r1["items"]) == 48
    r2 = client.get("/store/catalog", params={"pagina": 2}).json()
    vistos = {x["id"] for x in r1["items"]} | {x["id"] for x in r2["items"]}
    assert len(vistos) == 53
    # busqueda y filtros de calidad con su numero
    assert client.get("/store/catalog", params={"q": "catálogo 007"}).json()["total"] == 1
    faltas = {f["key"]: f["n"] for f in r1["faltas"]}
    assert faltas["sin_descripcion"] == 53 and faltas["sin_foto"] == 53
    assert client.get("/store/catalog", params={"falta": "sin_foto"}).json()["total"] == 53
    # publicado: los no publicados no salen por defecto
    assert client.get("/store/catalog", params={"publicado": "no", "q": "catálogo"}).json()["total"] == 0


def test_filtros_por_estado_y_nombre_largo(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    main, _ = _whs(client)
    a = _prod(client, "CAT-B1", "Cámara en bodega", price=1500)
    _in(client, a["id"], main["id"], 2)
    _prod(client, "CAT-L1", "Cámara de prueba con un nombre comercial larguísimo que Google cortaría sin duda", price=1500)
    r = client.get("/store/catalog").json()
    estados = {e["key"]: e["n"] for e in r["estados"]}
    assert estados["en_bodega"] == 1
    assert [x["id"] for x in client.get("/store/catalog", params={"estado": "en_bodega"}).json()["items"]] == [a["id"]]
    largo = client.get("/store/catalog", params={"falta": "nombre_largo"}).json()
    assert largo["total"] == 1 and "nombre_largo" in largo["items"][0]["faltas"]


def test_un_solo_patch_para_lo_web_y_marca_el_catalogo(client, auth, db_session):
    _publish(client)
    pr = _prod(client, "CAT-P1", "Equipo de prueba", price=1800, web=False)
    tid = auth["tenant"]["id"]
    v0 = (db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0
    body = {
        "show_on_web": True,
        "web_featured": True,
        "description_store": "Descripción corta de prueba.",
        "images": [{"url": "/media/uno.png"}, {"url": "/media/dos.png"}],
        "specs": {"Garantía": "12 meses"},
        "datasheet_url": "https://ejemplo.com/ficha.pdf",
        "brand": "MarcaPrueba",
    }
    r = client.patch(f"/store/catalog/{pr['id']}", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["show_on_web"] and d["web_featured"] and d["description_store"] == "Descripción corta de prueba."
    assert [i["url"] for i in d["images"]] == ["/media/uno.png", "/media/dos.png"] and d["specs"] == {"Garantía": "12 meses"}
    assert d["name"] == "Equipo de prueba" and d["code"] == "CAT-P1"  # nunca cambia el nombre fiscal ni el codigo
    assert d["seo"] is None or d["seo"]["title"]
    db_session.expire_all()
    assert ((db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0) > v0
    # ni nombre, ni codigo, ni precio de comparacion escrito a mano
    for malo in ({"name": "Otro"}, {"code": "X"}, {"compare_price": "2000"}):
        assert client.patch(f"/store/catalog/{pr['id']}", json=malo).status_code == 422, malo


def test_permisos_por_campo(client, auth, db_session):
    _publish(client)
    pr = _prod(client, "CAT-PERM", "Equipo de permisos", price=1500, web=False)
    h = _login(client, db_session, auth, "inventario.cat@ejemplo.com", "inventario")
    url = f"/store/catalog/{pr['id']}"
    assert client.get("/store/catalog", headers=h).status_code == 200
    assert client.patch(url, json={"show_on_web": True}, headers=h).status_code == 403  # publicar: solo el administrador
    r = client.patch(url, json={"show_on_web": False, "description_store": "Texto del inventario"}, headers=h)  # reenviar como esta: si
    assert r.status_code == 200, r.text
    assert r.json()["description_store"] == "Texto del inventario"
    hv = _login(client, db_session, auth, "vendedor.cat@ejemplo.com", "ventas")
    assert client.get("/store/catalog", headers=hv).status_code == 403
    assert client.patch(url, json={"description_store": "x"}, headers=hv).status_code == 403


def test_los_endpoints_viejos_devuelven_lo_mismo(client, auth, db_session):
    _publish(client)
    pr = _prod(client, "CAT-OLD", "Equipo viejo", price=1500, web=False)
    r = client.patch(f"/store/inventory/products/{pr['id']}", json={"show_on_web": True, "web_featured": True, "web_price": "1200"})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"id", "show_on_web", "web_featured", "web_price", "price", "estado"}
    assert r.json()["show_on_web"] is True and float(r.json()["web_price"]) == 1200
    r = client.patch(f"/store/inventory/products/{pr['id']}", json={"web_price": None})
    assert r.status_code == 200 and r.json()["web_price"] is None  # null vuelve al precio de siempre
    r = client.put(f"/store/showcase/products/{pr['id']}", json={"datasheet_url": "https://ejemplo.com/f.pdf", "specs": {"Canales": "4"}})
    assert r.status_code == 200, r.text
    assert r.json()["datasheet_url"] == "https://ejemplo.com/f.pdf" and r.json()["specs"] == {"Canales": "4"}
    r = client.patch(f"/products/{pr['id']}/web", json={"show_on_web": False})
    assert r.status_code == 200 and r.json()["show_on_web"] is False
