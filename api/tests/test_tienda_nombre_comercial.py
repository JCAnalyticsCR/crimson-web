"""O3-01: columnas de contenido y nombre comercial visible en toda la tienda.

Un producto con web_name lo muestra en tarjeta, ficha, migas, titulo, JSON-LD, correo y «Mi pedido»; la factura imprime
el nombre fiscal (Product.name). La direccion de la ficha no cambia, el buscador encuentra por los dos nombres, las
consultas de /catalog, /showcase y el snapshot no suben y la sincronizacion con el proveedor no toca las columnas web_.
Datos ficticios, precios en colones <= 2 000."""

import re
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import select

from app.models import Order, Product
from app.services import store_orders as so
from app.services import supplier_sync as ss
from tests.test_catalogo_ampliado import eurocomp_ampliado  # noqa: F401, F811 (fixture)
from tests.test_store_vitrina import _catalogo, _publicar
from tests.test_tienda_checkout import SLUG, _body, _shop

H = {"X-Store-Host": "tienda.crimsoncr.com"}
WEB = "Cámara IP Hikvision bullet · 4 MP · PoE"


def _ld_product(ld: dict) -> dict:
    return next(x for x in ld["@graph"] if x.get("@type") == "Product")


def test_nombre_comercial_en_tarjeta_ficha_titulo_y_json_ld(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    p = prods["DS-2CD1043G2-LIU"]
    fiscal, path_antes = p.name, client.get(f"{SLUG}/catalog", params={"ids": str(p.id)}).json()["items"][0]["path"]
    p.web_name = WEB
    db_session.commit()

    card = client.get(f"{SLUG}/catalog", params={"q": "bullet"}).json()["items"][0]
    assert card["display_name"] == WEB and card["name"] == fiscal  # name sigue siendo el fiscal
    ficha = client.get(f"{SLUG}/item/{card['path'].split('/')[-1]}").json()
    assert ficha["display_name"] == WEB and ficha["path"] == path_antes  # la direccion no se mueve
    seo = ficha["seo"]
    tienda = seo["title"].split(" | Precio en Costa Rica · ")[-1]
    assert seo["title"] == f"{WEB} | Precio en Costa Rica · {tienda}"
    assert seo["description"].startswith("Cámara IP Hikvision") and "con IVA." in seo["description"]
    assert seo["description"].endswith("Envío a todo Costa Rica y factura electrónica.")
    prod_ld = _ld_product(seo["ld"])
    assert prod_ld["name"] == WEB
    crumbs = next(x for x in seo["ld"]["@graph"] if x.get("@type") == "BreadcrumbList")["itemListElement"]
    assert crumbs[-1]["name"] == WEB
    # el SSI del dominio de la tienda dice lo mismo (un solo JSON-LD: el del servidor)
    head = client.get(f"/public/seo/head{ficha['path']}", headers=H).text
    assert f"{WEB} | Precio en Costa Rica · {tienda}" in head
    # el snapshot lleva el nombre comercial solo del que lo tiene
    snap = client.get(f"{SLUG}/snapshot").json()
    by_id = {x["id"]: x for x in snap["products"]}
    assert by_id[p.id]["display_name"] == WEB and by_id[p.id]["name"] == fiscal
    assert "display_name" not in by_id[prods["DS-2CD1143G2-LIU"].id]
    # sin nombre comercial, la ficha sigue con el titulo de siempre (no cambian todas a la vez)
    otra = client.get(f"{SLUG}/item/{by_id[prods['DS-2CD1143G2-LIU'].id]['path'].split('/')[-1]}").json()
    assert otra["seo"]["title"] == f"{prods['DS-2CD1143G2-LIU'].name} · {tienda}"


def test_buscar_por_el_nombre_viejo_y_por_el_nuevo(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    p = prods["WD44PURZ"]
    p.web_name = "Disco duro Western Digital Purple · 4 TB · videovigilancia"
    db_session.commit()
    viejo = client.get(f"{SLUG}/catalog", params={"q": "disco duro de 4tb"}).json()["items"]
    nuevo = client.get(f"{SLUG}/catalog", params={"q": "videovigilancia purple"}).json()["items"]
    assert p.id in [x["id"] for x in viejo] and p.id in [x["id"] for x in nuevo]
    snap = client.get(f"{SLUG}/snapshot").json()
    sf = next(x["sf"] for x in snap["products"] if x["id"] == p.id)
    assert "videovigilancia" in sf and "4tb" in sf


def test_seo_a_mano_manda_y_ficha_oculta_mas_de_60_dias_no_se_indexa(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    p = prods["DS-3E0109PE/M"]
    p.web_name, p.seo_title, p.seo_description = "Switch PoE Hikvision · 8 puertos", "Switch PoE de 8 puertos", "Switch para cámaras."
    p.hidden_since = datetime.now(UTC) - timedelta(days=61)
    db_session.commit()
    path = client.get(f"{SLUG}/catalog", params={"ids": str(p.id)}).json()["items"][0]["path"]
    seo = client.get(f"{SLUG}/item/{path.split('/')[-1]}").json()["seo"]
    assert seo["title"] == "Switch PoE de 8 puertos" and seo["description"] == "Switch para cámaras."
    assert seo["robots"] == "noindex,follow"
    p.hidden_since = datetime.now(UTC) - timedelta(days=10)
    db_session.commit()
    assert client.get(f"{SLUG}/item/{path.split('/')[-1]}").json()["seo"]["robots"] == "index,follow"


def test_pedido_guarda_dos_nombres_correo_y_mi_pedido_el_visible_factura_el_fiscal(client, auth, db_session):
    prods = _shop(client)
    pid = prods[0]["id"]
    p = db_session.get(Product, pid)
    fiscal = p.name
    p.web_name = "Nombre comercial de prueba · 4 MP"
    db_session.commit()
    q = client.post(f"{SLUG}/quote", json=_body(pid)).json()
    assert q["lines"][0]["name"] == "Nombre comercial de prueba · 4 MP"  # el carrito ve el visible
    o = client.post(f"{SLUG}/checkout", json=_body(pid)).json()
    assert o["lines"][0]["name"] == "Nombre comercial de prueba · 4 MP"
    mine = client.get(f"{SLUG}/orders/{o['track_token']}").json()
    assert mine["lines"][0]["name"] == "Nombre comercial de prueba · 4 MP"  # «Mi pedido»
    order = db_session.scalar(select(Order).order_by(Order.id.desc()))
    ln = next(x for x in order.lines if x.product_id == pid)
    assert ln.name == fiscal and ln.display_name == "Nombre comercial de prueba · 4 MP"
    assert "Nombre comercial de prueba · 4 MP" in so._summary_html(order)  # correo
    panel = client.get(f"/store-orders/{order.id}").json()
    pl = next(x for x in panel["lines"] if x["product_id"] == pid)
    assert pl["name"] == fiscal and pl["display_name"] == "Nombre comercial de prueba · 4 MP"
    client.post(f"/store-orders/{order.id}/status", json={"status": "pagado"})
    inv = client.post(f"/orders/{order.id}/invoice", params={"doc_type": "FE"})
    assert inv.status_code == 201, inv.text
    lines = client.get(f"/invoices/{inv.json()['invoice_id']}").json()["lines"]
    assert any(x["name"] == fiscal for x in lines) and not any("comercial" in (x["name"] or "") for x in lines)


def _contar(db_session, fn) -> int:
    engine = db_session.get_bind()
    n = []
    cb = lambda *a: n.append(1)  # noqa: E731
    sa.event.listen(engine, "before_cursor_execute", cb)
    try:
        fn()
    finally:
        sa.event.remove(engine, "before_cursor_execute", cb)
    return len(n)


def test_las_consultas_no_suben_con_nombre_comercial(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    from app.models import Tenant
    from app.services import store_snapshot

    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))

    def medir():
        return (
            _contar(db_session, lambda: client.get(f"{SLUG}/catalog")),
            _contar(db_session, lambda: client.get(f"{SLUG}/showcase")),
            _contar(db_session, lambda: store_snapshot.build(db_session, t, __import__("app.routers.store_showcase", fromlist=["Ctx"]).Ctx(db_session, t))),
        )

    medir()  # calienta (snapshot y cachés)
    antes = medir()
    for i, p in enumerate(prods.values()):
        p.web_name, p.web_type = f"Nombre comercial {i}", ("Cámara IP" if i % 2 else None)
    db_session.commit()
    medir()
    assert medir() == antes


def test_editar_a_mano_marca_web_manual(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    pid = prods["WD23PURZ"].id
    r = client.put(f"/store/showcase/products/{pid}", json={"web_name": "Disco duro WD Purple · 2 TB", "warranty_months": 24, "gtin": "0718037888888"})
    assert r.status_code == 200, r.text
    assert r.json()["web_name"] == "Disco duro WD Purple · 2 TB" and r.json()["web_manual"] is True
    assert client.put(f"/store/showcase/products/{pid}", json={"gtin": "12AB"}).status_code == 422
    p = db_session.get(Product, pid)
    db_session.refresh(p)
    assert p.name == "Disco duro 2TB Western Digital Purple" and p.code == "WD23PURZ"  # lo fiscal no cambia


def test_la_sincronizacion_no_pisa_las_columnas_web(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)
    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": False})
    db_session.commit()
    ss.auto_catalog_run(db_session, db_session.get(ss.Tenant, tid), ss.find_supplier(db_session, tid), commit_batches=True)
    cam = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG2104"))
    assert cam.web_name is None and cam.web_specs is None and cam.web_manual is False  # nace sin contenido propio
    vals = {
        "web_name": "Cámara IP Hikvision · 4 MP",
        "web_subtitle": "Para exterior",
        "web_type": "Cámara IP",
        "web_specs": {"Resolución": "4 MP"},
        "web_description": "Texto propio.",
        "web_manual": True,
        "warranty_months": 12,
        "seo_title": "Título",
        "seo_description": "Descripción",
        "gtin": "12345678",
    }
    for k, v in vals.items():
        setattr(cam, k, v)
    db_session.commit()
    eurocomp_ampliado["items"][0] = {**eurocomp_ampliado["items"][0], "precio": "52.00", "stock": "9.000", "descripcion": "CAMARA IP HIKVISION DS-2CD1047G3 NUEVA"}
    ss.sync_all(db_session)
    ss.refresh_linked(db_session)
    db_session.expire_all()
    cam = db_session.get(Product, cam.id)
    assert {k: getattr(cam, k) for k in vals} == vals
    import inspect

    from app.services import supplier_catalog as sc

    for mod in (ss, sc):  # ningun camino de la sincronizacion escribe columnas web_ (solo product_from_item las deja vacias)
        src = inspect.getsource(mod)
        assert not re.search(r"\.web_(name|subtitle|type|specs|description|manual)\s*=", src), mod.__name__
