"""O1-04: portada honesta y sin relleno. Destacados con su origen (manual o automatico; los automaticos solo
comprables y con precio), banners de relleno que se guardan inactivos con aviso y no se pintan, marcas solo de
seguridad, redes y respaldo, kits que dicen si se pagan en linea, sellos con dato verificable y «¿Cómo pago?»
desde los metodos activos. Datos ficticios, precios en colones <= 2 000."""

from types import SimpleNamespace as N

import pytest
from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import store_showcase as sv
from tests.test_store_vitrina import _publicar

S = "/public/store/crimson"


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _tienda(db):
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False

    def cat(name, parent=None):
        c = Category(tenant_id=t.id, name=name, show_on_web=True, parent_id=parent.id if parent else None)
        db.add(c)
        db.flush()
        return c

    seg, aud = cat("Seguridad"), cat("Audio y sonido")
    cams, barras = cat("Cámaras", seg), cat("Barras de sonido", aud)
    img = lambda n: [{"url": f"https://cdn.example.com/{n}.jpg", "main": True}]  # noqa: E731
    datos = [
        ("CAM-A", "Cámara IP bullet 4MP PoE Alfa", 1500, "Vigil", cams),
        ("CAM-B", "Cámara IP domo 4MP PoE Beta", 1400, "Vigil", cams),
        ("CAM-C", "Cámara IP bullet 2MP PoE sin precio", 0, "Vigil", cams),
        ("BARRA", "Barra de sonido Resonar 2.1", 1800, "Resonar", barras),
    ]
    out = {}
    for code, name, price, brand, c in datos:
        p = Product(tenant_id=t.id, code=code, name=name, price=price, currency="CRC", brand=brand, category_id=c.id, images=img(code), show_on_web=True)
        db.add(p)
        db.flush()
        out[code] = p
    db.commit()
    return out


def _existencia(client, pid, qty=3):
    wh = next(w for w in client.get("/warehouses").json() if w["is_default"])
    assert client.post("/stock/movements", json={"product_id": pid, "warehouse_id": wh["id"], "kind": "entrada", "quantity": qty}).status_code == 201


def test_destacados_con_su_origen_y_los_automaticos_solo_comprables(client, auth, db_session):
    _publicar(client)
    prods = _tienda(db_session)
    _existencia(client, prods["CAM-B"].id)
    _existencia(client, prods["CAM-C"].id)  # comprable pero sin precio: tampoco
    h = client.get(f"{S}/showcase").json()
    assert h["featured_source"] == "auto"
    assert [p["code"] for p in h["featured"]] == ["CAM-B"]  # CAM-A no se puede comprar; CAM-C no tiene precio
    assert client.put("/store/showcase", json={"featured": [prods["CAM-A"].id]}).status_code == 200
    h = client.get(f"{S}/showcase").json()
    assert h["featured_source"] == "manual" and [p["code"] for p in h["featured"]] == ["CAM-A"]  # lo elegido manda


def test_banner_de_relleno_se_guarda_inactivo_con_aviso_y_no_se_pinta(client, auth, db_session):
    _publicar(client)
    _tienda(db_session)
    r = client.put(
        "/store/showcase",
        json={
            "banners": [
                {"title": "Nuevo banner", "image": "https://cdn.example.com/b.jpg", "link": "/catalogo"},
                {"title": "Sin imagen", "link": "/catalogo"},
                {"title": "Promo", "image": "https://cdn.example.com/p.jpg", "link": "https://tienda.crimsoncr.com/c/camaras?x=1"},
                {"title": "", "image": "https://cdn.example.com/v.jpg", "active": False},
            ]
        },
    )
    assert r.status_code == 200, r.text  # se sanea, no se rechaza: el editor manda la lista completa
    d = r.json()
    assert d["warnings"] == [sv.BANNER_OFF_WARNING]
    assert [b["active"] for b in d["banners"]] == [False, False, True, False]  # se guardan todos, los de relleno apagados
    assert d["banners"][2]["link"] == "/c/camaras?x=1"  # el propio dominio es ruta interna
    h = client.get(f"{S}/showcase").json()
    assert [b["title"] for b in h["banners"]] == ["Promo"]
    snap = client.get(f"{S}/snapshot").json()
    assert [b["title"] for b in snap["showcase"]["banners"]] == ["Promo"]
    # un banner guardado antes de O1-04 (activo y sin imagen) tampoco se pinta
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    st = dict(t.settings)
    st["store"] = {**st["store"], "showcase": {**st["store"]["showcase"], "banners": [{"title": "Nuevo banner", "active": True}]}}
    t.settings = st
    sv.bump_catalog_version(db_session, t)
    db_session.commit()
    assert client.get(f"{S}/showcase").json()["banners"] == []


def test_marcas_solo_de_seguridad_redes_y_respaldo(client, auth, db_session):
    _publicar(client)
    _tienda(db_session)
    marcas = {b["name"] for b in client.get(f"{S}/showcase").json()["brands"]}
    assert marcas == {"Vigil"}  # Resonar solo vende audio


def test_kit_dice_si_se_paga_en_linea(client, auth, db_session):
    _publicar(client)
    _tienda(db_session)
    kits = client.get(f"{S}/showcase").json()["kits"]
    assert kits and all(k["payable"] is False for k in kits)  # piezas sin existencia: precio de referencia


def test_sellos_con_dato_verificable_y_como_pago_desde_los_metodos(client, auth, db_session):
    _publicar(client)
    _tienda(db_session)
    titulos = [s["title"] for s in client.get(f"{S}/showcase").json()["trust"]]
    assert not any(t.startswith("Soporte") for t in titulos)  # sin horario no se publica
    _publicar(client, business={"hours": "Lunes a viernes, 8 a. m. a 5 p. m."})
    soporte = next(s for s in client.get(f"{S}/showcase").json()["trust"] if s["title"].startswith("Soporte"))
    assert soporte["text"].endswith(". Horario: Lunes a viernes, 8 a. m. a 5 p. m.")

    metodos = {
        "sinpe": {"active": True, "phone": "8888-7777", "holder": "Comercio de Prueba"},
        "transferencia": {"active": True, "iban": "CR05015201001026284066", "bank": "BCR", "holder": "Comercio de Prueba"},
        "tarjeta": {"active": True, "installments": False, "max_installments": 0},
        "efectivo": {"active": False},
    }
    assert client.put("/store/checkout", json={"methods": metodos}).status_code == 200
    faq = client.get(f"{S}/info/preguntas-frecuentes").json()["faq"]
    pago = next(f for f in faq if f["q"] == "¿Cómo pago?")
    assert pago["a"] == "Con tarjeta de crédito o débito, SINPE Móvil o transferencia."
    assert client.put("/store/checkout", json={"methods": {**metodos, "tarjeta": {"active": False}, "transferencia": {"active": False}}}).status_code == 200
    pago = next(f for f in client.get(f"{S}/info/preguntas-frecuentes").json()["faq"] if f["q"] == "¿Cómo pago?")
    assert pago["a"] == "Con SINPE Móvil."


def test_reglas_puras():
    assert sv.internal_link("https://www.tienda.example.com/p/x-1", "tienda.example.com") == "/p/x-1"
    assert sv.internal_link("https://tienda.example.com", "https://tienda.example.com/") == "/"
    assert sv.internal_link("https://otro.example.com/p", "tienda.example.com") == "https://otro.example.com/p"
    assert sv.banner_incomplete({"title": " nuevo BANNER ", "image": "x"}) and not sv.banner_incomplete({"title": "Promo", "image": "x"})
    assert sv.pay_answer([{"code": "efectivo", "pickup_only": True}]) == "Con efectivo al retirar."
    assert sv.public_faq([{"q": "¿Cómo pago?", "a": "x"}, {"q": "Otra", "a": "y"}], []) == [{"q": "Otra", "a": "y"}]
    soporte = N(title="Soporte técnico", text="WhatsApp de lunes a viernes")
    assert sv.public_trust([vars(soporte)], None) == [vars(soporte)]  # ya trae su horario


def test_producto_de_portada_cae_al_siguiente_comprable_o_a_ninguno(client, auth, db_session):
    """O2-08: el panel guarda el elegido y hasta 5 de respaldo; la portada muestra el primero comprable."""
    _publicar(client)
    prods = _tienda(db_session)
    _existencia(client, prods["CAM-B"].id)
    orden = [prods["CAM-A"].id, prods["CAM-B"].id]
    r = client.put("/store/showcase", json={"hero": orden})
    assert r.status_code == 200 and r.json()["hero"] == orden
    assert [x["code"] for x in r.json()["hero_products"]] == ["CAM-A", "CAM-B"]
    h = client.get(f"{S}/showcase").json()
    assert h["hero"]["code"] == "CAM-B"  # CAM-A no se puede comprar: manda el siguiente
    _existencia(client, prods["CAM-A"].id)
    assert client.get(f"{S}/showcase").json()["hero"]["code"] == "CAM-A"  # el elegido vuelve apenas es comprable
    assert client.put("/store/showcase", json={"hero": [prods["CAM-C"].id]}).status_code == 200  # sin precio
    assert client.get(f"{S}/showcase").json()["hero"] is None
    assert client.put("/store/showcase", json={"hero": [999999]}).json()["hero"] == []  # ajeno o inexistente
