"""O3-04: filtros propios de cada familia y relacionados por parecido real.

- Cada familia tiene sus filtros (4 grupos como maximo): camaras (resolucion, interior o exterior, conexion),
  parlantes (potencia, resistencia al agua), switches (puertos, PoE), UPS (VA). La misma regla en el API y en el
  navegador: los casos de portal/src/modules/store/__tests__/familias_casos.json (paridad).
- /c/parlantes no ofrece «Cable» ni «Cámara WiFi» como tipo; el tipo corregido a mano (web_type) gana; la llave de
  los kits (product_type) no cambia.
- Relacionados: mismo tipo, precio a +-30 %, los mas cercanos primero y lo comprable antes.
- La ficha se adapta: «¿Lo instalamos nosotros?» y «¿Qué necesito además?» solo en seguridad y redes; en audio y
  hogar, «Combiná con».
Datos ficticios, precios en colones <= 2 000."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as N

import pytest
from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import catalog_drafts as cd
from app.services import family_filters as ff
from app.services import store_showcase as sv
from tests.test_store_vitrina import _publicar

SLUG = "/public/store/crimson"
CASOS = json.loads((Path(__file__).resolve().parents[2] / "portal/src/modules/store/__tests__/familias_casos.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "tipo", "esperado"),
    [
        ("Cámara IP Hikvision bullet 4MP exterior PoE", "Cámara IP", {"resolucion": "4 MP", "ambiente": "Exterior", "conexion": "PoE"}),
        ("Cámara WiFi Ezviz C6N 1080p interior", "Cámara WiFi", {"resolucion": "2 MP", "ambiente": "Interior", "conexion": "WiFi"}),
        ("Cámara Hikvision turbo HD 2MP", "Cámara analógica", {"resolucion": "2 MP", "conexion": "Analógica"}),
        ("Switch PoE Hikvision 8 puertos", "Switch PoE", {"puertos": "8 puertos", "poe": "Con PoE"}),
        ("Switch TP-Link 5 puertos gigabit", "Switch", {"puertos": "5 puertos", "poe": "Sin PoE"}),
        ("UPS APC Back-UPS 600VA", "UPS", {"capacidad": "Hasta 750 VA"}),
        ("UPS CDP 1000VA", "UPS", {"capacidad": "751 a 1500 VA"}),
        ("Router TP-Link AX1800", "Router", {}),
    ],
)
def test_valores_por_familia(name, tipo, esperado):
    assert ff.attrs(N(name=name, description_store=""), tipo) == esperado
    assert len(ff.GROUPS.get(ff.family(tipo) or "", ())) <= ff.MAX_GROUPS


def _parlantes(db):
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    madre = Category(tenant_id=t.id, name=CASOS["madre"], show_on_web=True)
    db.add(madre)
    db.flush()
    cat = Category(tenant_id=t.id, name=CASOS["categoria"], parent_id=madre.id, show_on_web=True)
    db.add(cat)
    db.flush()
    ids = {}
    for x in CASOS["productos"]:
        p = Product(tenant_id=t.id, code=f"FAM-{x['id']}", name=x["name"], price=Decimal(x["price"]) / Decimal("1.13"), currency="CRC", category_id=cat.id,
                    show_on_web=True, images=[{"url": f"https://cdn.example.com/f{x['id']}.jpg", "main": True}])  # fmt: skip
        db.add(p)
        db.flush()
        ids[x["id"]] = p.id
    db.commit()
    return t, madre, cat, ids


def test_paridad_con_el_navegador(client, auth, db_session):
    _publicar(client)
    t, madre, cat, ids = _parlantes(db_session)
    back = {v: k for k, v in ids.items()}
    snap = client.get(f"{SLUG}/snapshot").json()
    by = {back[x["id"]]: x for x in snap["products"] if x["id"] in back}
    for x in CASOS["productos"]:  # el snapshot manda los mismos valores que usa el navegador
        assert by[x["id"]].get("fam") == x["fam"] and by[x["id"]].get("fa", {}) == x["fa"], x["name"]
    for c in CASOS["casos"]:
        r = client.get(f"{SLUG}/catalog", params={"category": "parlantes-de-prueba", "f": c["f"], "limit": 60}).json()
        assert sorted(back[i["id"]] for i in r["items"]) == c["ids"], c["f"]
        assert r["facets"]["groups"] == c["groups"], c["f"]
    # sin cable ni camaras como tipo en parlantes
    r = client.get(f"{SLUG}/catalog", params={"category": "parlantes-de-prueba"}).json()
    assert not any(x["name"].startswith(("Cámara", "Cable")) for x in r["facets"]["types"])


def test_tipo_a_mano_gana_y_los_kits_no_cambian(client, auth, db_session):
    _publicar(client)
    t, madre, cat, ids = _parlantes(db_session)
    p = db_session.get(Product, ids[6])
    antes = sv.product_type(p)
    p.web_type = "Barra de sonido"
    db_session.commit()
    r = client.get(f"{SLUG}/catalog", params={"category": "parlantes-de-prueba", "type": "Barra de sonido"}).json()
    assert [x["id"] for x in r["items"]] == [p.id] and r["items"][0]["type"] == "Barra de sonido"
    assert sv.product_type(p) == antes  # la llave de los kits no se toca


def test_relacionados_por_parecido_y_ficha_segun_familia(client, auth, db_session):
    _publicar(client)
    t, madre, cat, ids = _parlantes(db_session)
    # otro producto de la misma familia madre (audio) y otro tipo: «Combiná con»
    sub = Category(tenant_id=t.id, name="Micrófonos de prueba", parent_id=madre.id, show_on_web=True)
    db_session.add(sub)
    db_session.flush()
    db_session.add(Product(tenant_id=t.id, code="MIC-1", name="Micrófono Maono USB", price=Decimal("1000"), currency="CRC", category_id=sub.id, show_on_web=True,
                           images=[{"url": "https://cdn.example.com/m.jpg", "main": True}]))  # fmt: skip
    db_session.commit()
    main = next(w for w in client.get("/warehouses").json() if w["is_default"])
    for pid in ids.values():  # con existencia propia: se muestran (los agotados no salen en relacionados)
        assert client.post("/stock/movements", json={"product_id": pid, "warehouse_id": main["id"], "kind": "entrada", "quantity": 2}).status_code == 201
    path = client.get(f"{SLUG}/catalog", params={"ids": str(ids[1])}).json()["items"][0]["path"]
    ficha = client.get(f"{SLUG}/item/{path.split('/')[-1]}").json()
    # precio 1900: dentro del 30 % estan 2000 (5), 1800 (6) y 1500 (2), los mas cercanos primero; despues el resto
    rel = [{v: k for k, v in ids.items()}.get(x["id"]) for x in ficha["related"]]
    assert rel[:3] == [5, 6, 2] and set(rel) >= {3, 4}
    assert ficha["family"] == "hogar"
    assert [g["title"] for g in ficha["accessories"]] in ([], ["Combiná con"])


def test_regla_de_tipo_solo_para_lo_que_no_tiene(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(cd, "in_sync_window", lambda now=None: False)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    seg = Category(tenant_id=t.id, name="Seguridad", show_on_web=True)
    db_session.add(seg)
    db_session.flush()
    sin = Product(tenant_id=t.id, code="SG-9", name="Kit Hikvision de prueba", price=Decimal("1000"), currency="CRC", category_id=seg.id, show_on_web=True)
    con = Product(tenant_id=t.id, code="SG-8", name="Switch PoE 8 puertos", price=Decimal("1000"), currency="CRC", category_id=seg.id, show_on_web=True)
    db_session.add_all([sin, con])
    db_session.commit()
    sim = client.post("/store/content/generate", json={"field": "web_type", "product_ids": [sin.id, con.id], "simulate": True}).json()
    assert [(r["product_id"], r["after"]) for r in sim["preview"]] == [(sin.id, "Kit")]
