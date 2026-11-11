"""O3-02: marcas bien escritas y codigo publico limpio, sin tocar el codigo ni el nombre fiscal.

- El filtro de marcas no repite marcas ni las muestra en minuscula; una direccion vieja con «Tp Link» sigue filtrando.
- El JSON-LD usa la grafia oficial, el codigo publico (nunca «EUR-») y el modelo limpio.
- Product.code no cambia en ningun producto.
- Un producto nuevo de la sincronizacion nace con la marca oficial.
- El guion de una sola vez simula, aplica solo lo aprobado con bitacora y deshace.
Datos ficticios, precios en colones <= 2 000."""

from types import SimpleNamespace as N

import pytest
from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import brand_cleanup
from app.services import catalog_text as ct
from app.services import supplier_sync as ss
from app.services.brands import brand_key, canonical_brand
from tests.test_catalogo_ampliado import eurocomp_ampliado  # noqa: F401, F811 (fixture)
from tests.test_store_vitrina import _publicar

SLUG = "/public/store/crimson"


@pytest.mark.parametrize(
    ("raw", "oficial"),
    [
        ("Tp Link", "TP-Link"), ("TP-LINK", "TP-Link"), ("tplink", "TP-Link"), ("JBL", "JBL"), ("Jbl", "JBL"), ("cdp", "CDP"),
        ("Lg", "LG"), ("D LINK", "D-Link"), ("ELGATO", "Elgato"), ("Xpg", "XPG ADATA"), ("THONET & VANDER", "Thonet & Vander"),
        ("Thonet and Vander", "Thonet & Vander"), ("HIKVISION", "Hikvision"), ("Ezviz", "EZVIZ"), ("WD", "Western Digital"),
        ("MARCA NUEVA", "Marca Nueva"), ("OtraMarca", "OtraMarca"), ("", None), (None, None),
    ],
)  # fmt: skip
def test_tabla_de_marcas(raw, oficial):
    assert canonical_brand(raw) == oficial
    if raw:
        assert canonical_brand(oficial) == oficial  # estable


@pytest.mark.parametrize(
    ("code", "model", "publico"),
    [
        ("EUR-SG2104", None, "SG2104"),
        ("EUR-JBLCHARGE6BLKAM", None, "JBLCHARGE6BLKAM"),
        ("DS-K4H258S(O-STD) (E)", None, "DS-K4H258S"),
        ("DS-2CD1167G3-LIU", "DS-2CD1167G3-LIU(2.8MM)(LATAM", "DS-2CD1167G3-LIU(2.8MM)"),
        ("EUR-C200", "TAPO", "C200"),
        ("EUR-C310", "TAPO C310", "C310"),
        ("WD43PURZ", "WD43PURZ", "WD43PURZ"),
    ],
)
def test_codigo_publico_limpio(code, model, publico):
    p = N(code=code, model=model, name="x", brand=None)
    assert ct.public_code(p) == publico
    assert not ct.public_code(p).upper().startswith("EUR-")


def _cat(db, t, name):
    c = Category(tenant_id=t.id, name=name, show_on_web=True)
    db.add(c)
    db.flush()
    return c


def test_filtro_sin_duplicados_json_ld_oficial_y_codigo_intacto(client, auth, db_session):
    _publicar(client)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db_session.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    red = _cat(db_session, t, "Redes de prueba")
    datos = [
        ("EUR-RT01", "Router TP-Link Archer AX23 WiFi 6", "Tp Link", "ARCHER AX23"),
        ("EUR-RT02", "Router TP-Link Archer C6 AC1200", "TP-LINK", "ARCHER C6"),
        ("EUR-SW01", "Switch TP-Link TL-SG1005D 5 puertos", "TP-Link", "TL-SG1005D"),
        ("EUR-SW02", "Switch D-Link DES-1016A 16 puertos", "d-link", None),
    ]
    prods = []
    for code, name, brand, model in datos:
        p = Product(tenant_id=t.id, code=code, name=name, brand=brand, model=model, price=1500, currency="CRC", category_id=red.id, show_on_web=True,
                    images=[{"url": f"https://cdn.example.com/{code}.jpg", "main": True}])  # fmt: skip
        db_session.add(p)
        prods.append(p)
    db_session.commit()
    codes = {p.id: p.code for p in prods}

    cat = client.get(f"{SLUG}/catalog", params={"category": "redes-de-prueba"}).json()
    marcas = [b["name"] for b in cat["facets"]["brands"]]
    assert sorted(marcas) == ["D-Link", "TP-Link"]
    assert next(b for b in cat["facets"]["brands"] if b["name"] == "TP-Link")["count"] == 3
    # una direccion vieja con la grafia del proveedor sigue filtrando
    viejo = client.get(f"{SLUG}/catalog", params={"category": "redes-de-prueba", "brand": "Tp Link"}).json()
    assert viejo["total"] == 3
    snap = client.get(f"{SLUG}/snapshot").json()
    assert sorted(b["name"] for b in snap["brands"]) == ["D-Link", "TP-Link"]
    assert all(not (x.get("public_code") or x["code"]).startswith("EUR-") or x.get("public_code") for x in snap["products"])
    assert all(not x.get("public_code", "").startswith("EUR-") for x in snap["products"])
    item = next(x for x in cat["items"] if x["code"] == "EUR-RT01")
    assert item["brand"] == "TP-Link" and item["public_code"] == "ARCHER AX23"
    sw = next(x for x in cat["items"] if x["code"] == "EUR-SW02")
    assert sw["public_code"] == "SW02" and sw["brand"] == "D-Link"
    ficha = client.get(f"{SLUG}/item/{item['path'].split('/')[-1]}").json()
    ld = next(x for x in ficha["seo"]["ld"]["@graph"] if x.get("@type") == "Product")
    assert ld["brand"]["name"] == "TP-Link" and ld["sku"] == "ARCHER AX23" and ld["mpn"] == "ARCHER AX23"
    assert ficha["specs"]["Marca"] == "TP-Link"
    db_session.expire_all()
    assert {p.id: db_session.get(Product, p.id).code for p in prods} == codes  # el codigo no se toco
    assert db_session.get(Product, prods[0].id).brand == "Tp Link"  # la tienda la muestra bien sin reescribirla


def test_guion_simula_aplica_lo_aprobado_y_deshace(client, auth, db_session):
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    a = Product(tenant_id=t.id, code="M-1", name="Parlante uno", brand="Tp Link", price=1000, currency="CRC")
    b = Product(tenant_id=t.id, code="M-2", name="Parlante dos", brand="JBL", price=1000, currency="CRC")
    c = Product(tenant_id=t.id, code="M-3", name="Parlante tres", brand="jbl", price=1000, currency="CRC")
    db_session.add_all([a, b, c])
    db_session.commit()
    antes = {p.id: (p.code, p.name, p.price) for p in (a, b, c)}
    rep = brand_cleanup.simulate(db_session, t.id)
    mine = {x["product_id"]: x for x in rep["changes"] if x["product_id"] in antes}
    assert mine == {a.id: {"product_id": a.id, "before": "Tp Link", "after": "TP-Link"}, c.id: {"product_id": c.id, "before": "jbl", "after": "JBL"}}
    db_session.expire_all()
    assert db_session.get(Product, a.id).brand == "Tp Link"  # simular no escribe
    c_ = db_session.get(Product, c.id)
    c_.brand = "JBL Pro"  # alguien la cambio despues del informe: no se pisa
    db_session.commit()
    rep["changes"] = [x for x in rep["changes"] if x["product_id"] in antes]
    log = brand_cleanup.apply(db_session, t.id, rep)
    db_session.commit()
    assert [x["product_id"] for x in log["log"]] == [a.id] and log["skipped"] == [c.id]
    assert db_session.get(Product, a.id).brand == "TP-Link"
    assert {p: (db_session.get(Product, p).code, db_session.get(Product, p).name, db_session.get(Product, p).price) for p in antes} == antes
    out = brand_cleanup.undo(db_session, t.id, log)
    db_session.commit()
    assert out == {"undone": 1, "changed_since": []}
    assert db_session.get(Product, a.id).brand == "Tp Link"


def test_alta_nueva_nace_con_la_marca_oficial(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    eurocomp_ampliado["items"].append(
        {**eurocomp_ampliado["items"][0], "codigo": "RD0001", "descripcion": "ROUTER TP LINK ARCHER C6 AC1200", "marca": "TP LINK", "modelo": "ARCHER C6"}  # fmt: skip
    )
    eurocomp_ampliado["items"].append({**eurocomp_ampliado["items"][0], "codigo": "SG7777", "descripcion": "CAMARA WIFI EZVIZ C6N", "marca": "EZVIZ", "modelo": "C6N"})
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)
    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": False})
    db_session.commit()
    ss.auto_catalog_run(db_session, db_session.get(ss.Tenant, tid), ss.find_supplier(db_session, tid), commit_batches=True)
    rt = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "RD0001"))
    ez = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG7777"))
    assert rt.brand == "TP-Link" and ez.brand == "EZVIZ"
    assert brand_key("Tp Link") == brand_key(rt.brand)
