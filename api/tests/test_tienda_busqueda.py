"""O1-14: buscador tolerante con UNA regla (services/store_search) en el servidor y en el navegador. Los casos viven
en portal/src/modules/store/__tests__/busqueda_casos.json y los comprueban las dos pruebas (esta y busqueda.test.ts):
si una regla cambia de un lado y no del otro, una de las dos falla."""

import gzip
import json
from pathlib import Path

from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import store_search as ss

CASOS = Path(__file__).resolve().parents[2] / "portal" / "src" / "modules" / "store" / "__tests__" / "busqueda_casos.json"


def test_paridad_con_el_navegador_en_30_consultas():
    data = json.loads(CASOS.read_text(encoding="utf-8"))
    assert data["syn"] == ss.SYNONYMS  # la tabla del archivo es la que manda el snapshot
    assert len(data["cases"]) >= 30
    for p in data["products"]:
        assert p["sf"] == ss.search_field(p["name"], p["brand"], p["model"], p["code"], p["sku"], p["category"], p["type"])
    for c in data["cases"]:
        sc = {p["id"]: ss.score(c["q"], name=p["name"], brand=p["brand"], model=p["model"], code=p["code"], category=p["category"], sf=p["sf"]) for p in data["products"]}
        got = [i for i, s in sorted(((i, s) for i, s in sc.items() if s), key=lambda x: (-x[1], x[0]))]
        assert got == c["ids"], c["q"]


def test_plural_guiones_codigos_y_sinonimos():
    sf = ss.search_field("Cámara IP bullet 4 MP", "Hikvision", "DS-2CD1047G3-L", "AC1104", "EC-77", "Cámaras IP", "Cámara IP")
    kw = {"name": "Cámara IP bullet 4 MP", "brand": "Hikvision", "model": "DS-2CD1047G3-L", "code": "AC1104", "category": "Cámaras IP", "sf": sf}
    for q in ("camara", "camaras", "CÁMARAS", "ds2cd1047g3", "DS-2CD1047G3", "ac1104", "ec77"):
        assert ss.score(q, **kw) > 0, q
    assert ss.score("ds2cd1047g3", **kw) > ss.score("camara", **kw)  # el modelo pesa mas que el nombre
    assert ss.score("parlante", **kw) == 0
    parl = {"name": "Parlante Bluetooth", "brand": "Sonar", "model": "SG5071", "code": "X1", "category": "Audio"}
    parl["sf"] = ss.search_field(*parl.values(), None, "Parlante")
    assert ss.score("bocina", **parl) > 0 and ss.score("altavoz", **parl) > 0 and ss.score("sg5071", **parl) > 100


def test_catalogo_del_servidor_y_snapshot_con_la_regla_nueva(client, auth, db_session):
    client.put("/store", json={"kind": "tienda", "published": True})
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db_session.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    cat = Category(tenant_id=t.id, name="Audio de prueba", show_on_web=True)
    db_session.add(cat)
    db_session.flush()
    db_session.add_all(
        [
            Product(tenant_id=t.id, code="PR-BOC", name="Parlante portátil de prueba", model="SG5071", brand="Sonar", price=1000, category_id=cat.id, show_on_web=True),
            Product(tenant_id=t.id, code="PR-TPL", name="Switch 8 puertos de prueba", brand="TP-Link", model="TL-SG1008P", price=1500, show_on_web=True),
        ]
    )
    db_session.commit()

    def nombres(q):
        return sorted(x["name"] for x in client.get("/public/store/crimson/catalog", params={"q": q}).json()["items"])

    assert nombres("bocina") == ["Parlante portátil de prueba"]
    assert nombres("sg5071") == nombres("SG-5071") == ["Parlante portátil de prueba"]
    assert nombres("tp-link") == nombres("tp link") == nombres("tplink") == ["Switch 8 puertos de prueba"]
    snap = client.get("/public/store/crimson/snapshot").json()
    assert snap["syn"] == ss.SYNONYMS
    sf = next(p["sf"] for p in snap["products"] if p["code"] == "PR-BOC")
    assert sf.startswith("parlante portatil de prueba sonar sg5071 pr boc audio de prueba")  # nombre, marca, modelo, codigo, categoria


def test_tamano_del_snapshot_por_cada_1000_productos(client, auth, db_session):
    """Tope: 120 KB comprimidos por cada 1 000 productos (con el sf nuevo y la tabla de sinonimos)."""
    from app.services import store_snapshot

    client.put("/store", json={"kind": "tienda", "published": True})
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db_session.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    cats = [Category(tenant_id=t.id, name=n, show_on_web=True) for n in ("Cámaras IP", "Grabadores", "Redes y conectividad", "Audio", "Respaldo eléctrico")]
    db_session.add_all(cats)
    db_session.flush()
    marcas = ("Hikvision", "TP-Link", "Ezviz", "Dahua", "Nodo", "Vigia", "Sonar")
    tipos = ("Cámara IP bullet", "Grabador NVR", "Switch PoE", "Punto de acceso WiFi 6", "Parlante Bluetooth", "UPS interactiva")
    for i in range(1000):
        m = marcas[i % len(marcas)]
        db_session.add(
            Product(
                tenant_id=t.id,
                code=f"PR{i:05d}",
                name=f"{tipos[i % len(tipos)]} {m} modelo {i % 97} de {4 + i % 4} MP con {8 + i % 9} puertos de prueba {i}",
                brand=m,
                model=f"{m[:2].upper()}-{1000 + i}X-{i % 13}",
                supplier_sku=f"EC-{50000 + i}",
                price=100 + i,
                category_id=cats[i % len(cats)].id,
                images=[{"url": f"https://cdn.example.com/productos/2026/{i}.jpg", "main": True}],
                show_on_web=True,
            )
        )
    db_session.commit()
    store_snapshot.rebuild(db_session, t.id)
    gz, _ = store_snapshot.get_or_rebuild(db_session, t.id)
    n = json.loads(gzip.decompress(gz))["n"]
    assert n == 1000
    print(f"[SNAPSHOT] {n} productos: {len(gz)} bytes gz")
    assert len(gz) <= 120 * 1024 * n / 1000, f"{len(gz)} bytes para {n} productos"
