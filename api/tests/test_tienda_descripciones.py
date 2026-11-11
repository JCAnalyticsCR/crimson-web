"""O3-06: descripciones utiles; fuera la linea del proveedor en mayusculas.

- La ficha y la meta descripcion nunca imprimen la descripcion de factura (mayusculas con codigo interno al final);
  una descripcion de tienda igual al nombre o gritada tampoco se muestra.
- Metas de 155 caracteres o menos, sin «No indicado» y sin codigos internos.
- Sin texto propio: 3 a 5 viñetas desde la ficha aprobada y una frase de plantilla con esos mismos datos, como
  borrador (O3-11). Solo reglas y plantillas: ninguna IA ni servicio externo.
Datos ficticios, precios en colones <= 2 000."""

import re
from decimal import Decimal
from types import SimpleNamespace as N

from sqlalchemy import select

from app.models import CatalogDraft, Category, Product, Tenant
from app.services import catalog_drafts as cd
from app.services.catalog_fichas import describe, store_description, useless_description
from tests.test_store_vitrina import _publicar

SLUG = "/public/store/crimson"
H = {"X-Store-Host": "tienda.crimsoncr.com"}
GRITO = "CAMARA IP HIKVISION DS-2CD1043G2-LIU BULLET 4MP 2.8MM 311326530"


def _p(**kw):
    base = {"name": "Cámara IP Hikvision bullet 4MP", "description_store": None, "description_invoice": GRITO, "web_description": None, "web_name": None}
    return N(**{**base, **kw})


def test_que_descripcion_se_muestra():
    assert store_description(_p()) is None  # nunca la de factura
    assert store_description(_p(description_store=GRITO)) is None  # la linea del proveedor en mayusculas
    assert store_description(_p(description_store="Cámara IP Hikvision bullet 4MP.")) is None  # igual al nombre
    assert useless_description("", "x")
    lista = "- Resolución: 4 MP\n- Micrófono: No indicado\n- Lente: 2.8 mm"
    assert store_description(_p(description_store=lista)) == "- Resolución: 4 MP\n- Lente: 2.8 mm"
    assert store_description(_p(description_store=lista, web_description="Texto propio.")) == "Texto propio."


def test_plantilla_sin_adjetivos_con_los_mismos_datos():
    p = _p(name="PARLANTE JBL CHARGE 6 45W 311326530", web_name="Parlante JBL Charge 6 · 45 W · Bluetooth")
    txt = describe(p, "Parlantes", {"Potencia": "45 W", "Bluetooth": "Sí", "Protección": "IP67", "Batería": "Hasta 20 h"})
    first, *bullets = txt.split("\n")
    assert first == "Parlante JBL Charge 6 · 45 W · Bluetooth, de 45 W de potencia, con Bluetooth y protección IP67."
    assert bullets == ["- Potencia: 45 W", "- Bluetooth: Sí", "- Protección: IP67", "- Batería: Hasta 20 h"]
    assert not re.search(r"\d{9}", txt)
    for adj in ("increíble", "mejor", "excelente", "potente", "premium", "ideal"):
        assert adj not in txt.lower()
    # con menos de 3 datos, una sola frase (lo honesto)
    assert "\n" not in describe(p, "Parlantes", {"Potencia": "45 W"})


def _catalogo(db):
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    cat = Category(tenant_id=t.id, name="Cámaras de prueba", show_on_web=True)
    db.add(cat)
    db.flush()
    img = [{"url": "https://cdn.example.com/x.jpg", "main": True}]
    sin = Product(tenant_id=t.id, code="DS-1", name="Cámara IP Hikvision bullet 4MP 2.8mm", description_invoice=GRITO, brand="Hikvision", price=Decimal("1900"),
                  currency="CRC", category_id=cat.id, show_on_web=True, images=img, web_specs={"Resolución": "4 MP", "Lente": "2.8 mm", "Protección": "IP67"})  # fmt: skip
    grito = Product(tenant_id=t.id, code="DS-2", name="Cámara IP Hikvision domo 2MP", description_store=GRITO, description_invoice=GRITO, brand="Hikvision",
                    price=Decimal("1500"), currency="CRC", category_id=cat.id, show_on_web=True, images=img)  # fmt: skip
    lista = Product(tenant_id=t.id, code="DS-3", name="Cámara IP Hikvision turret 4MP", brand="Hikvision", price=Decimal("1800"), currency="CRC",
                    description_store="- Resolución: 4 MP\n- Micrófono: No indicado\n- Visión nocturna: IR 30 m\n- Compresión: H.265+ y además un texto largo " + "x" * 200,
                    category_id=cat.id, show_on_web=True, images=img)  # fmt: skip
    propio = Product(tenant_id=t.id, code="DS-4", name="Cámara IP Hikvision bullet 6MP", brand="Hikvision", price=Decimal("2000"), currency="CRC",
                     description_store="Cámara para exterior con visión nocturna de 30 m.", category_id=cat.id, show_on_web=True, images=img)  # fmt: skip
    db.add_all([sin, grito, lista, propio])
    db.commit()
    return t, cat, sin, grito, lista, propio


def test_ficha_y_metas_sin_la_linea_del_proveedor(client, auth, db_session):
    _publicar(client)
    t, cat, sin, grito, lista, propio = _catalogo(db_session)
    items = client.get(f"{SLUG}/catalog", params={"category": "camaras-de-prueba"}).json()["items"]
    for it in items:
        assert it["description"] is None or (GRITO not in it["description"] and "311326530" not in it["description"])
    for p in (sin, grito, lista, propio):
        path = next(x["path"] for x in items if x["id"] == p.id)
        ficha = client.get(f"{SLUG}/item/{path.split('/')[-1]}").json()
        assert ficha["description"] is None or ficha["description"] != p.name
        assert ficha["description"] is None or not ficha["description"].isupper()
        meta = ficha["seo"]["description"]
        assert len(meta) <= 155, meta
        assert "No indicado" not in meta and not re.search(r"\b\d{9}\b", meta) and GRITO not in meta, meta
        ld = next(x for x in ficha["seo"]["ld"]["@graph"] if x.get("@type") == "Product")
        assert "311326530" not in ld["description"]
        head = client.get(f"/public/seo/head{path}", headers=H).text
        assert "311326530" not in head
    f = client.get(f"{SLUG}/item/{next(x['path'] for x in items if x['id'] == lista.id).split('/')[-1]}").json()
    assert f["description"].startswith("- Resolución: 4 MP\n- Visión nocturna") and "No indicado" not in f["description"]
    assert f["seo"]["description"].startswith("Resolución: 4 MP; Visión nocturna: IR 30 m")


def test_borrador_de_descripcion_solo_para_lo_que_no_tiene_texto(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(cd, "in_sync_window", lambda now=None: False)
    _publicar(client)
    t, cat, sin, grito, lista, propio = _catalogo(db_session)
    sim = client.post("/store/content/generate", json={"field": "web_description", "category_id": cat.id, "simulate": True}).json()
    by = {r["product_id"]: r for r in sim["preview"]}
    assert set(by) == {sin.id}  # grito: sin ficha aprobada (no se inventa); lista y propio: ya tienen texto
    first, *bullets = by[sin.id]["after"].split("\n")
    assert first == "Cámara IP Hikvision bullet 4MP 2.8mm, con resolución de 4 MP y lente de 2.8 mm."
    assert bullets == ["- Resolución: 4 MP", "- Lente: 2.8 mm", "- Protección: IP67"]
    bid = client.post("/store/content/generate", json={"field": "web_description", "category_id": cat.id, "simulate": False}).json()["batch_id"]
    d = db_session.scalar(select(CatalogDraft).where(CatalogDraft.batch_id == bid))
    assert d.status == "propuesto" and d.product_id == sin.id
    db_session.expire_all()
    assert db_session.get(Product, sin.id).web_description is None  # nada se publica solo
