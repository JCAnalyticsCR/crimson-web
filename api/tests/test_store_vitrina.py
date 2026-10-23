"""Vitrina de la tienda propia (reemplazo de Fygaro): portada administrable, catalogo con filtros y busqueda
tolerante, ficha por direccion amigable, configurador, asesoria -> oportunidad, paginas propias y SEO por dominio."""

import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Category, EmailOutbox, Opportunity, Product, Tenant


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _publicar(client, **extra):
    r = client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892", "domain": "tienda.crimsoncr.com", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _catalogo(db):
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False  # el catalogo de la semilla no cuenta: solo el de esta prueba

    def cat(name):
        c = db.scalar(select(Category).where(Category.tenant_id == t.id, Category.name == name))
        if not c:
            c = Category(tenant_id=t.id, name=name, show_on_web=True)
            db.add(c)
            db.flush()
        return c

    vig, alm, red = cat("Videovigilancia"), cat("Almacenamiento"), cat("Redes")
    img = lambda n: [{"url": f"https://cdn.example.com/{n}.jpg", "main": True}]  # noqa: E731
    datos = [
        (
            "DS-2CD1043G2-LIU",
            "Cámara IP Hikvision Bullet 4MP 2.8mm IP67 PoE",
            60,
            "Hikvision",
            vig,
            "Visión nocturna IR hasta 30 m. Compresión H.265+.",
            img("bullet"),
        ),
        ("DS-2CD1143G2-LIU", "Cámara IP Hikvision Domo 4MP 2.8mm PoE", 55, "Hikvision", vig, "Domo para interiores, IR 30 m.", img("domo")),
        (
            "DS-7608NXI-K1/8P",
            "NVR Hikvision 8 canales IP AcuSense con PoE",
            180,
            "Hikvision",
            vig,
            "Grabador de 8 canales con switch PoE integrado.",
            img("nvr8"),
        ),
        ("DS-7104NI-Q1/4P", "Grabador IP NVR Hikvision 4CH PoE", 100, "Hikvision", vig, "", img("nvr4")),
        ("WD44PURZ", "Disco duro de 4TB Western Digital Purple", 120, "Western Digital", alm, "", img("wd4")),
        ("WD23PURZ", "Disco duro 2TB Western Digital Purple", 80, "Western Digital", alm, "", img("wd2")),
        ("DS-3E0109PE/M", "Switch PoE Hikvision 8 Puertos", 50, "Hikvision", red, "", img("sw8")),
        ("RJ45-100", "Conector RJ45 Cat6 (unidad)", 0.3, "Genérico", red, "", []),
        ("SIN-PRECIO", "Cámara PTZ Axis sin precio", 0, "Axis", vig, "", img("ptz")),
    ]
    out = {}
    for code, name, price, brand, cat, desc, images in datos:
        p = Product(
            tenant_id=t.id,
            code=code,
            name=name,
            price=price,
            currency="USD",
            brand=brand,
            category_id=cat.id,
            description_store=desc,
            images=images,
            show_on_web=True,
        )
        db.add(p)
        db.flush()
        out[code] = p
    oculto = Product(tenant_id=t.id, code="OCULTO", name="Cámara IP oculta 4MP", price=10, currency="USD", show_on_web=False)
    db.add(oculto)
    db.commit()
    return out


def test_portada_administrable_y_paginas(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    # sin eleccion de Andres, la portada arma destacados solos (con foto y precio) y trae los kits por defecto
    home = client.get("/public/store/crimson/showcase")
    assert home.status_code == 200, home.text
    h = home.json()
    assert h["featured"] and all(p["image"] and not p["price_on_request"] for p in h["featured"])
    assert {c["name"] for c in h["categories"]} >= {"Videovigilancia", "Almacenamiento", "Redes"}
    vig = next(c for c in h["categories"] if c["name"] == "Videovigilancia")
    assert vig["path"] == "/c/videovigilancia" and vig["count"] == 5
    assert len(h["kits"]) == 3 and h["kits"][0]["items"] > 0 and Decimal(str(h["kits"][0]["total"])) > 0
    assert len(h["trust"]) == 4

    # Mi Tienda -> Vitrina: banners, destacados y textos propios
    r = client.put(
        "/store/showcase",
        json={
            "headline": "Seguridad que se instala bien",
            "banners": [
                {"title": "Promo NVR", "image": "https://cdn.example.com/b.jpg", "link": "/c/videovigilancia", "position": 2},
                {"title": "Oculto", "active": False},
                {"title": "Primero", "position": 1},
            ],
            "featured": [prods["WD44PURZ"].id, 999999, prods["WD44PURZ"].id],
            "pages": {"envios": "Enviamos con Correos de CR."},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["featured"] == [prods["WD44PURZ"].id]  # sin duplicados ni ids ajenos
    assert client.put("/store/showcase", json={"banners": [{"title": "x", "link": "javascript:alert(1)"}]}).status_code == 422
    assert client.put("/store/showcase", json={"pages": {"otra": "x"}}).status_code == 422
    h = client.get("/public/store/crimson/showcase").json()
    assert [b["title"] for b in h["banners"]] == ["Primero", "Promo NVR"]
    assert [p["code"] for p in h["featured"]] == ["WD44PURZ"] and h["headline"] == "Seguridad que se instala bien"

    # paginas propias: envios editado, garantias por defecto, FAQ; terminos solo si existe el texto
    assert client.get("/public/store/crimson/info/envios").json()["body"] == "Enviamos con Correos de CR."
    assert "garantía" in client.get("/public/store/crimson/info/garantias").json()["body"].lower()
    assert client.get("/public/store/crimson/info/preguntas-frecuentes").json()["faq"]
    assert client.get("/public/store/crimson/info/terminos").status_code == 404
    client.put("/store", json={"legal": {"terms": "Términos de Crimson", "privacy": ""}})
    assert client.get("/public/store/crimson/info/terminos").json()["body"] == "Términos de Crimson"
    assert client.get("/public/store/crimson/info/nada").status_code == 404


def test_catalogo_busqueda_tolerante_filtros_y_orden(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    cat = lambda **q: client.get("/public/store/crimson/catalog", params=q)  # noqa: E731

    # modelos escritos de cualquier forma
    for q in ("ds-2cd", "DS2CD1043", "ds 2cd1043g2"):
        codes = [p["code"] for p in cat(q=q).json()["items"]]
        assert "DS-2CD1043G2-LIU" in codes, q
    assert cat(q="ds-2cd").json()["items"][0]["code"].startswith("DS-2CD")  # el codigo que empieza igual va primero
    # sin tildes, plural y sinonimos
    assert {p["code"] for p in cat(q="camaras hikvision").json()["items"]} == {"DS-2CD1043G2-LIU", "DS-2CD1143G2-LIU"}
    assert {p["code"] for p in cat(q="grabador").json()["items"]} == {"DS-7608NXI-K1/8P", "DS-7104NI-Q1/4P"}
    assert cat(q="OCULTO").json()["total"] == 0  # lo que no esta publicado no aparece
    vacio = cat(q="zzzz").json()
    assert vacio["total"] == 0 and vacio["items"] == []

    # categoria por slug + facetas
    r = cat(category="videovigilancia").json()
    assert r["total"] == 5 and r["category"]["name"] == "Videovigilancia"
    assert {b["name"] for b in r["facets"]["brands"]} == {"Hikvision", "Axis"}
    assert any(t["name"] == "Grabador NVR" for t in r["facets"]["types"])
    assert cat(category="no-existe").status_code == 404
    assert {p["code"] for p in cat(brand="Western Digital").json()["items"]} == {"WD44PURZ", "WD23PURZ"}
    assert {p["code"] for p in cat(type="Grabador NVR").json()["items"]} == {"DS-7608NXI-K1/8P", "DS-7104NI-Q1/4P"}

    # orden por precio: lo "a consultar" siempre al final
    asc = cat(sort="price_asc", limit=60).json()["items"]
    precios = [Decimal(str(p["display_price"])) for p in asc]
    assert precios[-1] == 0 and precios[:-1] == sorted(precios[:-1])
    assert asc[-1]["price_on_request"] is True
    # filtro de precio con IVA, en la moneda de la tienda
    mx = Decimal(str(asc[2]["display_price"]))
    assert all(Decimal(str(p["display_price"])) <= mx for p in cat(max_price=str(mx)).json()["items"])
    # paginacion
    p1, p2 = cat(limit=4, offset=0).json(), cat(limit=4, offset=4).json()
    assert p1["total"] == 9 and len(p1["items"]) == 4 and not {x["id"] for x in p1["items"]} & {x["id"] for x in p2["items"]}
    # comparador: ids con especificaciones
    cmp = cat(ids=f"{prods['DS-2CD1043G2-LIU'].id},{prods['DS-2CD1143G2-LIU'].id}").json()["items"]
    assert cmp[0]["specs"]["Resolución"] == "4 MP" and cmp[0]["specs"]["Formato"] == "Bullet" and cmp[1]["specs"]["Formato"] == "Domo"


def test_ficha_por_direccion_amigable(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    cam = prods["DS-2CD1043G2-LIU"]
    cam.compare_price = Decimal(80)
    cam.datasheet_url = "https://cdn.example.com/ficha.pdf"
    db_session.commit()
    path = f"/p/camara-ip-hikvision-bullet-4mp-2-8mm-ip67-poe-{cam.id}"
    r = client.get(f"/public/store/crimson/item/{path.split('/')[-1]}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["path"] == path and d["datasheet_url"].endswith(".pdf")
    assert Decimal(str(d["compare_display_price"])) > Decimal(str(d["display_price"]))  # precio anterior tachado
    assert Decimal(str(d["net_price"])) + Decimal(str(d["tax_amount"])) == Decimal(str(d["display_price"])).quantize(Decimal("0.01"))
    s = d["specs"]
    assert (
        s["Resolución"] == "4 MP"
        and s["Lente"] == "2.8 mm"
        and s["Visión nocturna"] == "IR hasta 30 m"
        and s["Protección"].startswith("IP67")
        and s["PoE"] == "Sí"
    )
    assert [c["name"] for c in d["breadcrumbs"]] == ["Tienda", "Videovigilancia"]
    assert any(g["title"] == "Grabador" for g in d["accessories"])  # ¿que necesito ademas?
    assert any(g["title"].startswith("Disco") for g in d["accessories"])
    assert d["related"] and all(x["id"] != cam.id for x in d["related"])
    # el slug viejo (otro nombre) igual resuelve por el id; uno ajeno o despublicado no
    assert client.get(f"/public/store/crimson/item/nombre-viejo-{cam.id}").json()["id"] == cam.id
    assert client.get("/public/store/crimson/item/nada").status_code == 404
    oculto = db_session.scalar(select(Product).where(Product.code == "OCULTO"))
    assert client.get(f"/public/store/crimson/item/x-{oculto.id}").status_code == 404
    # admin de vitrina por producto
    r = client.put(f"/store/showcase/products/{cam.id}", json={"specs": {"Garantía": "2 años"}, "compare_price": None})
    assert r.status_code == 200 and r.json()["specs"] == {"Garantía": "2 años"}
    d = client.get(f"/public/store/crimson/item/x-{cam.id}").json()
    assert d["specs"]["Garantía"] == "2 años" and d["compare_display_price"] is None
    assert client.put(f"/store/showcase/products/{cam.id}", json={"datasheet_url": "javascript:x"}).status_code == 422


def test_catalogo_y_ficha_con_existencias_reales(client, auth, db_session):
    """Regresion: con inventario integrado, la disponibilidad llega como fila de store_inventory (dict) y el
    catalogo/ficha/portada respondian 500 en cuanto un producto tenia existencias propias."""
    _publicar(client)
    prods = _catalogo(db_session)
    main = next(w for w in client.get("/warehouses").json() if w["is_default"])
    for code, qty in (("DS-2CD1043G2-LIU", 9), ("WD44PURZ", 1)):
        r = client.post("/stock/movements", json={"product_id": prods[code].id, "warehouse_id": main["id"], "kind": "entrada", "quantity": qty})
        assert r.status_code == 201, r.text

    r = client.get("/public/store/crimson/catalog", params={"limit": 60})
    assert r.status_code == 200, r.text
    av = {p["code"]: p["availability"] for p in r.json()["items"]}
    # mismo diccionario que el resto de la tienda (services/availability): en_bodega, con "ultimas" si quedan pocas
    assert av["DS-2CD1043G2-LIU"]["state"] == "en_bodega" and not av["DS-2CD1043G2-LIU"]["ultimas"]
    assert av["WD44PURZ"]["state"] == "en_bodega" and av["WD44PURZ"]["ultimas"]
    # el filtro de entrega inmediata usa la misma disponibilidad
    inm = {p["code"] for p in client.get("/public/store/crimson/catalog", params={"avail": "inmediata", "limit": 60}).json()["items"]}
    assert inm == {"DS-2CD1043G2-LIU", "WD44PURZ"}
    # con cantidad exacta publicada, la ficha limita la cantidad a lo que hay en bodega
    assert client.put("/store/inventory/settings", json={"quantity_display": "exacta"}).status_code == 200
    d = client.get(f"/public/store/crimson/item/x-{prods['DS-2CD1043G2-LIU'].id}")
    assert d.status_code == 200, d.text
    assert d.json()["availability"]["state"] == "en_bodega" and d.json()["availability"]["max_qty"] == 9
    assert client.get("/public/store/crimson/showcase").status_code == 200
    assert client.get("/public/store/crimson/catalog", params={"ids": str(prods["WD44PURZ"].id)}).status_code == 200


def test_arma_tu_sistema_con_productos_reales(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    r = client.post("/public/store/crimson/kit", json={"place": "negocio", "indoor": 3, "outdoor": 3, "days": 30, "remote": True})
    assert r.status_code == 200, r.text
    k = r.json()
    roles = {ln["role"]: ln for ln in k["lines"]}
    assert roles["Cámaras exteriores"]["product"]["code"] == "DS-2CD1043G2-LIU" and roles["Cámaras exteriores"]["quantity"] == 3
    assert roles["Cámaras interiores"]["product"]["code"] == "DS-2CD1143G2-LIU"
    # 6 camaras -> NVR de 8 canales (el de PoE integrado: sin switch aparte)
    assert roles["Grabador"]["product"]["code"] == "DS-7608NXI-K1/8P" and "Red" not in roles
    # 6 camaras x 30 dias x 21 GB x 1.1 = 4.2 TB -> no alcanza uno de 4 TB: dos discos
    assert roles["Almacenamiento"]["product"]["code"] == "WD44PURZ" and roles["Almacenamiento"]["quantity"] == 2
    assert roles["Instalación"]["product"]["code"] == "RJ45-100" and roles["Instalación"]["quantity"] == 12  # 2 por camara (reglas del levantamiento)
    assert any("técnico" in n for n in k["notes"]) and Decimal(str(k["total"])) > 0
    assert k["missing"]  # el cable no esta en este catalogo: se lista para cotizar, no se inventa
    assert client.post("/public/store/crimson/kit", json={"indoor": 0, "outdoor": 0}).status_code == 422
    assert prods


def test_kit_completo_con_bolsa_de_conectores_bobina_y_disco_de_eurocomp(client, auth, db_session):
    """Lo que vende Eurocomp: conectores en bolsa de 100, cable en bobina de 305 m y discos que llegan como "HD INTERNO"
    (el nombre limpio de la tienda los vuelve "Disco duro interno"). El kit sale con precio en vez de "lo cotizamos"."""
    from app.services.product_names import tidy

    _publicar(client)
    prods = _catalogo(db_session)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    prods["RJ45-100"].show_on_web = False  # solo la bolsa
    db_session.add_all(
        [
            Product(tenant_id=t.id, code="AC1070", name=tidy("CONECTOR HIKVISION DS-1M6AUA RJ45 CAT6A 100 UNIDADES SIN BLINDAJE"), price=12, currency="USD", show_on_web=True),
            Product(tenant_id=t.id, code="AC3211", name=tidy("CABLE UTP CAT6 NOVA FAN60305001C 305M PVC"), price=75, currency="USD", show_on_web=True),
            Product(tenant_id=t.id, code="AM2017", name=tidy("HD INTERNO 2TB SEAGATE ST2000VX017 3.5'' SATA III / OPTIMIZADO PARA VIDEOVIGILANCIA 303801487"), price=60, currency="USD", show_on_web=True),
        ]
    )
    for code in ("WD44PURZ", "WD23PURZ"):
        prods[code].show_on_web = False
    db_session.commit()
    k = client.post("/public/store/crimson/kit", json={"place": "casa", "indoor": 2, "outdoor": 2, "days": 15, "remote": True}).json()
    by_code = {ln["product"]["code"]: ln for ln in k["lines"]}
    assert by_code["AM2017"]["quantity"] == 1  # 1.4 TB -> el de 2 TB
    assert by_code["AC1070"]["quantity"] == 1 and "bolsa de 100" in by_code["AC1070"]["reason"]  # 8 conectores = 1 bolsa
    assert by_code["AC3211"]["quantity"] == 1  # 92 m -> 1 bobina
    assert [m["name"] for m in k["missing"]] == ["Caja de paso exterior"]  # lo unico que Eurocomp no vende


def test_nombres_legibles_desde_la_descripcion_del_proveedor():
    from app.services.product_names import tidy

    assert (
        tidy("CAMARA BULLET HIKVISION DS-2CD2043G2-LI2U(2.8MM)(O-STD) (E) 4MP ACUSENSE ALCANCE IR Y LUZ BLANCA HASTA 40 M 311326530")
        == "Cámara bullet Hikvision DS-2CD2043G2-LI2U(2.8MM) 4MP AcuSense alcance IR y luz blanca hasta 40 m"
    )
    assert tidy("HD INTERNO 2TB SEAGATE ST2000VX017 SATA III") == "Disco duro interno 2TB Seagate ST2000VX017 SATA III"
    assert tidy("SENSOR MAGNETICO PARA PUERTA HIKVISION DS-PD1-MC-RS 302400989 GRIS") == "Sensor magnético para puerta Hikvision DS-PD1-MC-RS gris"
    assert tidy("Cámara domo IP 4MP ColorVu") == "Cámara domo IP 4MP ColorVu"  # escrito a mano: no se toca
    assert tidy("") == ""


def test_asesoria_crea_oportunidad_y_avisa_a_ventas(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc())) or 0
    body = {
        "name": "María Rojas",
        "party": "empresa",
        "company": "Ferretería El Roble",
        "phone": "8888-1234",
        "email": "maria@elroble.cr",
        "solution": "cctv",
        "description": "Necesitamos 8 cámaras para la ferretería y la bodega.",
        "location": "Alajuela centro",
        "environment": "negocio",
        "timeline": "semanas",
        "install": True,
        "items": [{"product_id": prods["DS-2CD1043G2-LIU"].id, "quantity": 8}],
        "origin": "arma-tu-sistema",
    }
    r = client.post("/public/store/crimson/asesoria", json=body)
    assert r.status_code == 202, r.text
    assert r.json()["ok"] is True and "number" not in r.json()
    o = db_session.scalar(select(Opportunity).order_by(Opportunity.id.desc()))
    assert o.source == "Sitio web" and o.solution == "cctv" and o.status == "nuevo" and o.kind == "proyecto"
    assert o.contact["company"] == "Ferretería El Roble" and o.site == "Alajuela centro"
    assert "8 × Cámara IP Hikvision Bullet" in o.notes and "instalación" in o.notes.lower()
    assert Decimal(str(o.amount)) > 0 and o.next_action_date is not None
    assert "Entorno: Negocio · Plazo: Próximas semanas" in o.notes
    correos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes)).all()
    assert correos and any("Asesoría web" in c.subject for c in correos)
    # doble envio: misma persona y mismo texto -> nada nuevo, misma respuesta
    n = len(db_session.scalars(select(Opportunity)).all())
    assert client.post("/public/store/crimson/asesoria", json=body).json() == r.json()
    assert len(db_session.scalars(select(Opportunity)).all()) == n
    # honeypot: respuesta identica y nada creado
    assert client.post("/public/store/crimson/asesoria", json={**body, "description": "Otra cosa distinta aqui", "website": "http://spam"}).json() == r.json()
    assert len(db_session.scalars(select(Opportunity)).all()) == n
    # validacion: sin contacto, empresa sin nombre, campos extra
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    assert client.post("/public/store/crimson/asesoria", json={**body, "phone": "", "email": ""}).status_code == 422
    assert client.post("/public/store/crimson/asesoria", json={**body, "company": ""}).status_code == 422
    assert client.post("/public/store/crimson/asesoria", json={**body, "owner_id": 1}).status_code == 422
    assert client.post("/public/store/crimson/asesoria", json={**body, "timeline": "ayer"}).status_code == 422
    assert client.post("/public/store/crimson/asesoria", content=b"x" * 13000, headers={"Content-Type": "application/json"}).status_code == 413
    # limite por IP: 5 por hora
    reset_limiters()
    for _ in range(10):
        last = client.post("/public/store/crimson/asesoria", json={**body, "description": "Prueba de limite de solicitudes"})
    assert last.status_code == 429
    # tienda despublicada: no existe
    client.put("/store", json={"published": False})
    reset_limiters()
    assert client.post("/public/store/crimson/asesoria", json=body).status_code == 404


def test_seo_por_dominio_metas_datos_estructurados_sitemap_y_301(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    t.settings = {
        **t.settings,
        "fygaro": {
            "products": {"uuid-viejo": {"id": prods["WD44PURZ"].id}},
            "categories": {"12981": db_session.scalar(select(Category.id).where(Category.name == "Almacenamiento"))},
        },
    }
    db_session.commit()
    H = {"X-Store-Host": "tienda.crimsoncr.com"}
    cam = prods["DS-2CD1043G2-LIU"]

    head = client.get(f"/public/seo/head/p/x-{cam.id}", headers=H)
    assert head.status_code == 200 and head.headers["content-type"].startswith("text/html")
    html = head.text
    assert "<title>Cámara IP Hikvision Bullet 4MP" in html
    assert f'rel="canonical" href="https://tienda.crimsoncr.com/p/camara-ip-hikvision-bullet-4mp-2-8mm-ip67-poe-{cam.id}"' in html
    assert 'property="og:image" content="https://cdn.example.com/bullet.jpg"' in html and 'property="og:type" content="product"' in html
    ld = json.loads(re.search(r'<script data-sh type="application/ld\+json" id="sh-ld">(.*?)</script>', html, re.S).group(1))
    tipos = {n["@type"] for n in ld["@graph"]}
    assert {"Product", "BreadcrumbList", "Organization"} <= tipos
    prod = next(n for n in ld["@graph"] if n["@type"] == "Product")
    assert prod["offers"]["@type"] == "Offer" and Decimal(prod["offers"]["price"]) > 0 and prod["brand"]["name"] == "Hikvision" and prod["sku"] == cam.code

    # foto con direccion relativa (media del API) -> absoluta para Open Graph
    cam.images = [{"url": "/api/media/f/abc", "main": True}]
    db_session.commit()
    assert 'property="og:image" content="https://tienda.crimsoncr.com/api/media/f/abc"' in client.get(f"/public/seo/head/p/x-{cam.id}", headers=H).text

    # sin precio: Offer sin precio inventado
    ptz = client.get(f"/public/seo/head/p/x-{prods['SIN-PRECIO'].id}", headers=H).text
    ld2 = json.loads(re.search(r'id="sh-ld">(.*?)</script>', ptz, re.S).group(1))
    assert "price" not in next(n for n in ld2["@graph"] if n["@type"] == "Product")["offers"]
    # portada: Organization + WebSite con buscador; categoria; busqueda no indexable
    home = client.get("/public/seo/head", headers=H).text
    assert '"SearchAction"' in home and "Organization" in home
    assert "Videovigilancia ·" in client.get("/public/seo/head/c/videovigilancia", headers=H).text
    assert 'content="noindex,follow"' in client.get("/public/seo/head/buscar", headers=H).text
    faq = client.get("/public/seo/head/info/preguntas-frecuentes", headers=H).text
    assert '"FAQPage"' in faq
    # inyeccion: el texto del producto no rompe el <script>
    cam.name = "Cámara </script><script>alert(1)</script>"
    db_session.commit()
    bad = client.get(f"/public/seo/head/p/x-{cam.id}", headers=H).text
    assert "<script>alert(1)" not in bad
    # otro dominio (el panel): cuerpo vacio -> nginx usa el titulo por defecto
    assert client.get("/public/seo/head/", headers={"X-Store-Host": "portal.crimsoncr.com"}).text == ""

    sm = client.get("/public/seo/sitemap.xml", headers=H)
    assert sm.status_code == 200 and sm.headers["content-type"].startswith("application/xml")
    assert "https://tienda.crimsoncr.com/p/" in sm.text and "https://tienda.crimsoncr.com/c/videovigilancia" in sm.text
    assert "OCULTO".lower() not in sm.text.lower()
    assert client.get("/public/seo/sitemap.xml", headers={"X-Store-Host": "otro.com"}).status_code == 404
    assert "Sitemap: https://tienda.crimsoncr.com/sitemap.xml" in client.get("/public/seo/robots.txt", headers=H).text
    assert client.get("/public/seo/robots.txt", headers={"X-Store-Host": "portal.crimsoncr.com"}).text.strip().endswith("Disallow: /")
    assert client.get("/public/store/crimson/sitemap.xml").status_code == 200

    # existencia para nginx (404 real): ficha y categoria
    assert client.get(f"/public/seo/exists/p/x-{prods['WD44PURZ'].id}", headers=H).status_code == 204
    assert client.get("/public/seo/exists/p/no-existe-999999", headers=H).status_code == 403
    assert client.get("/public/seo/exists/c/videovigilancia", headers=H).status_code == 204
    assert client.get("/public/seo/exists/c/nada", headers=H).status_code == 403
    assert client.get("/public/seo/exists", headers={**H, "X-Original-URI": "/c/nada?x=1"}).status_code == 403
    assert client.get("/public/seo/exists", headers={**H, "X-Original-URI": f"/p/disco-{prods['WD44PURZ'].id}"}).status_code == 204
    assert client.get("/public/seo/exists/p/x-1", headers={"X-Store-Host": "portal.crimsoncr.com"}).status_code == 204
    # GA4: se guarda en Ajustes y se expone (el navegador lo carga solo con consentimiento)
    assert client.get("/public/store/crimson/site").json()["analytics"]["ga4_id"] is None
    assert client.put("/store/showcase", json={"ga4_id": "UA-123"}).status_code == 422
    client.put("/store/showcase", json={"ga4_id": "G-0PQ0N2M5TF"})
    assert client.get("/public/store/crimson/site").json()["analytics"]["ga4_id"] == "G-0PQ0N2M5TF"

    # 301 de las direcciones de Fygaro
    def go(uri):
        return client.get("/public/seo/legacy", headers={**H, "X-Original-URI": uri}, follow_redirects=False)

    r = go("/products/uuid-viejo/")
    assert r.status_code == 301 and r.headers["location"].startswith("https://tienda.crimsoncr.com/p/disco-duro-de-4tb")
    assert go("/products/category/12981/").headers["location"] == "https://tienda.crimsoncr.com/c/almacenamiento"
    assert go("/products/search/?keywords=nvr%208").headers["location"] == "https://tienda.crimsoncr.com/buscar?q=nvr%208"
    assert go("/terms").headers["location"].endswith("/info/terminos")
    assert go("/policies").headers["location"].endswith("/info/privacidad")
    assert go("/page/solicitud-de-asesoria-personalizada/").headers["location"].endswith("/asesoria")
    assert go("/products/").headers["location"].endswith("/catalogo")
    assert go("/products/no-existe/").headers["location"].endswith("/catalogo")
    # sin mapa de importacion: el mismo equipo por codigo (catalogo publico de la tienda vieja, fygaro_legacy.json)...
    r = go("/products/c2751caf-691d-494f-96e2-d34efdfe4de0/")  # Disco 4TB WD44PURZ en Fygaro
    assert r.status_code == 301 and r.headers["location"].startswith("https://tienda.crimsoncr.com/p/disco-duro-de-4tb")
    # ...y si ese equipo ya no se vende, una busqueda con su nombre (no el catalogo general)
    r = go("/products/c65ca62b-9889-41cd-b648-50c06739f501/")  # camara IFLUX que no esta en la tienda nueva
    assert r.headers["location"] == "https://tienda.crimsoncr.com/buscar?q=C%C3%A1mara%20Smart"
    # el modelo solo en el NOMBRE (asi vienen los productos del proveedor): palabra completa, nunca un pedazo
    from app.models import Product as _P

    sw = _P(tenant_id=prods["WD44PURZ"].tenant_id, code="RC5062", name="Switch Hikvision DS-3E1518P-SI 16 puertos PoE", price=100, show_on_web=True)
    parecido = _P(tenant_id=sw.tenant_id, code="249", name="Switch Hikvision DS-3E1518P-EI/M 16 puertos", price=100, show_on_web=True)
    db_session.add_all([parecido, sw])
    db_session.commit()
    r = go("/products/cb294175-80b7-40ec-856e-9565f0af1324/")  # Fygaro: SKU DS3E1518PSI
    assert r.headers["location"].endswith(f"-{sw.id}") and not r.headers["location"].endswith(f"-{parecido.id}")
    # el endpoint del portal (respaldo si nginx no lo atiende) resuelve igual
    assert client.get("/public/store/crimson/legacy/product/c2751caf-691d-494f-96e2-d34efdfe4de0").json() == {"product_id": prods["WD44PURZ"].id}
    assert client.get("/public/store/crimson/legacy/product/c65ca62b-9889-41cd-b648-50c06739f501").json() == {"search": "Cámara Smart"}
