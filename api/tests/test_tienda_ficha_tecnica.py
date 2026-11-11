"""O3-05: ficha tecnica derivada de lo que ya esta escrito, por lote aprobado y medida por exactitud.

- Las viñetas «- Clave: valor» pasan a especificaciones sin «No especificado / No indicado / No aplica» (y las pegadas
  en una sola linea se separan). Por familia, lo que el nombre dice con numero y unidad.
- «1080p» no genera puertos; el zoom no sale de cualquier «N x»; «audio bidireccional» no es «Micrófono integrado».
- Todo sale como borrador a web_specs (O3-11); la ficha muestra primero lo escrito a mano, luego lo aprobado, y deja de
  pintar lo deducido al vuelo cuando su categoria tiene un lote aplicado.
- El comparador de dos parlantes muestra 3 filas o mas con datos.
La exactitud (muestra de 50 con error menor al 2 %) es la compuerta de cada lote (test_tienda_lotes_contenido).
Datos ficticios, precios en colones <= 2 000."""

from decimal import Decimal
from types import SimpleNamespace as N

from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import catalog_drafts as cd
from app.services import store_showcase as sv
from app.services.catalog_fichas import family_specs, specs_from_text
from tests.test_store_vitrina import _publicar

SLUG = "/public/store/crimson"
VIÑETAS = """- Potencia: 45 W RMS
- Conectividad: Bluetooth 5.4
- Micrófono: No indicado
- Resistencia: IP67 - Batería: Hasta 20 horas
- Carga y Energía (si aplica): USB-C
- Garantía: No especificado
- Color: No aplica"""


def test_viñetas_a_especificaciones_sin_rellenos():
    s = specs_from_text(VIÑETAS)
    assert s == {"Potencia": "45 W RMS", "Conectividad": "Bluetooth 5.4", "Resistencia": "IP67", "Batería": "Hasta 20 horas", "Carga y Energía": "USB-C"}
    assert not any("No " in v for v in s.values())
    assert specs_from_text("PARLANTE JBL CHARGE 6 311326530") == {}


def test_expresiones_fragiles_corregidas():
    assert sv.ports(N(name="Kit de cámaras 1080p con DVR")) is None  # antes: 80 puertos
    sw = N(name="Switch PoE 8 puertos para cámaras 1080p", brand=None, model=None, description_store="", description_invoice=None, specs={})
    assert sv.derive_specs(sw, "Switch PoE")["Puertos"] == "8"
    ptz = N(name="Cámara PTZ 4MP 25x IR 100m", brand=None, model=None, description_store="", description_invoice=None, specs={})
    assert sv.derive_specs(ptz, "Cámara PTZ")["Zoom óptico"] == "25×"
    caja = N(name="Cámara PTZ 2MP soporte 2 x 4", brand=None, model=None, description_store="", description_invoice=None, specs={})
    assert "Zoom óptico" not in sv.derive_specs(caja, "Cámara PTZ")
    wifi = N(name="Cámara WiFi 1080p audio bidireccional", brand=None, model=None, description_store="", description_invoice=None, specs={})
    assert sv.derive_specs(wifi, "Cámara WiFi")["Audio"] == "Bidireccional"  # antes: «Micrófono integrado»


def test_datos_por_familia_desde_el_nombre():
    assert family_specs("Parlantes", "Parlante JBL Charge 6 45W rms bluetooth 5.4 IP67 hasta 20 horas") == {
        "Potencia": "45 W", "Bluetooth": "Bluetooth 5.4", "Protección": "IP67", "Batería": "Hasta 20 h"}  # fmt: skip
    assert family_specs("Router", "Router TP-Link Archer AX23 AX1800 WiFi 6 4 puertos") == {"Norma WiFi": "WiFi 6 (AX1800)", "Puertos": "4"}
    assert family_specs("Router", "Router Archer C6 AC1200 1200 Mbps") == {"Norma WiFi": "WiFi 5 (AC1200)", "Velocidad": "1200 Mbps"}
    assert family_specs("UPS", "UPS APC 600VA 330W 6 tomas") == {"Capacidad": "600 VA", "Potencia": "330 W", "Tomas": "6"}
    assert family_specs("Parlantes", "Parlante Go 4 bluetooth 4.2W") == {"Potencia": "4.2 W", "Bluetooth": "Sí"}
    assert family_specs("Power banks", "Power bank Xiaomi 10000mAh 22.5W") == {"Capacidad": "10000 mAh", "Potencia": "22.5 W"}
    assert family_specs("Cámara IP", "Cámara 4MP") == {}


def _parlantes(db):
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    cat = Category(tenant_id=t.id, name="Parlantes portátiles", show_on_web=True)
    otra = Category(tenant_id=t.id, name="Cámaras de prueba", show_on_web=True)
    db.add_all([cat, otra])
    db.flush()
    a = Product(tenant_id=t.id, code="PL-1", name="Parlante JBL Charge 6 45W bluetooth", brand="JBL", price=Decimal("1900"), currency="CRC",
                category_id=cat.id, show_on_web=True, description_store=VIÑETAS, images=[{"url": "https://cdn.example.com/a.jpg", "main": True}])  # fmt: skip
    b = Product(tenant_id=t.id, code="PL-2", name="Parlante JBL Go 4 bluetooth 4.2W IP67", brand="JBL", price=Decimal("1500"), currency="CRC",
                category_id=cat.id, show_on_web=True, description_store="- Potencia: 4.2 W\n- Batería: 7 horas\n- Micrófono: No especificado",
                images=[{"url": "https://cdn.example.com/b.jpg", "main": True}])  # fmt: skip
    c = Product(tenant_id=t.id, code="CM-1", name="Cámara WiFi 1080p audio bidireccional", brand="Ezviz", price=Decimal("1200"), currency="CRC",
                category_id=otra.id, show_on_web=True, images=[{"url": "https://cdn.example.com/c.jpg", "main": True}])  # fmt: skip
    db.add_all([a, b, c])
    db.commit()
    return t, cat, otra, a, b, c


def _aplicar(client, bid):
    client.post(f"/store/content/batches/{bid}/approve", json={})
    rows = [x["id"] for x in client.get(f"/store/content/batches/{bid}").json()["items"]]
    assert client.post(f"/store/content/batches/{bid}/sample", json={"checked": rows, "wrong": []}).json()["ok"]
    r = client.post(f"/store/content/batches/{bid}/apply")
    assert r.status_code == 200, r.text


def test_lote_de_fichas_la_ficha_y_el_comparador(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(cd, "in_sync_window", lambda now=None: False)
    _publicar(client)
    t, cat, otra, a, b, c = _parlantes(db_session)
    a.specs = {"Garantía": "1 año"}  # escrito a mano: va primero y nunca se pisa
    db_session.commit()
    # antes del lote: lo deducido al vuelo
    pre = client.get(f"{SLUG}/catalog", params={"ids": f"{c.id}"}).json()["items"][0]["specs"]
    assert pre["Audio"] == "Bidireccional"
    sim = client.post("/store/content/generate", json={"field": "web_specs", "category_id": cat.id, "simulate": True}).json()
    assert sim["counts"]["propuestos"] == 2
    after = {r["product_id"]: r["after"] for r in sim["preview"]}
    assert after[a.id]["Potencia"] == "45 W RMS" and "Micrófono" not in after[a.id] and after[a.id]["Bluetooth"] == "Sí"
    assert after[b.id] == {"Potencia": "4.2 W", "Batería": "7 horas", "Bluetooth": "Sí", "Protección": "IP67"}
    assert all("No " not in v for x in after.values() for v in x.values())
    bid = client.post("/store/content/generate", json={"field": "web_specs", "category_id": cat.id, "simulate": False}).json()["batch_id"]
    db_session.expire_all()
    assert db_session.get(Product, a.id).web_specs is None  # borrador: la tienda no cambia todavia
    _aplicar(client, bid)
    ficha = client.get(f"{SLUG}/catalog", params={"ids": f"{a.id},{b.id}"}).json()["items"]
    sa, sb = ficha[0]["specs"], ficha[1]["specs"]
    assert list(sa)[:4] == ["Tipo", "Marca", "Garantía", "Potencia"]  # tipo y marca, lo escrito a mano y lo aprobado
    assert sa["Marca"] == "JBL" and sa["Potencia"] == "45 W RMS"
    # comparador de dos parlantes: 3 filas o mas con datos de los dos
    comunes = [k for k in sa if k in sb and k not in ("Tipo", "Marca")]
    assert len([k for k in sa if k in sb]) >= 3 and "Potencia" in comunes
    # la categoria con lote aplicado ya no pinta lo deducido al vuelo para un producto sin web_specs
    d = Product(tenant_id=t.id, code="PL-3", name="Parlante Argom 20W bluetooth", brand="Argom", price=Decimal("1000"), currency="CRC",
                category_id=cat.id, show_on_web=True, images=[{"url": "https://cdn.example.com/d.jpg", "main": True}])  # fmt: skip
    db_session.add(d)
    db_session.commit()
    sd = client.get(f"{SLUG}/catalog", params={"ids": str(d.id)}).json()["items"][0]["specs"]
    assert set(sd) <= {"Tipo", "Marca", "Modelo"}
    # otra categoria sin lote: sigue igual que antes
    assert client.get(f"{SLUG}/catalog", params={"ids": f"{c.id}"}).json()["items"][0]["specs"] == pre
