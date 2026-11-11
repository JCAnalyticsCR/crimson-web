"""O1-08: el tipo que se muestra (tarjeta, ficha, filtro ?tipo= y buscador) va separado del tipo funcional con que se
arman kits, accesorios y destacados. Una barra de sonido no dice «Cámara WiFi»; los kits arman las mismas piezas.

Tabla dorada: nombres con la forma de los del catalogo (marcas y modelos de fabricante, sin datos de clientes) y la
categoria donde los deja la lista del proveedor. Columna «funcional»: lo que devuelve product_type HOY y no debe
cambiar (lo usan los kits). Columna «mostrado»: el rotulo nuevo. Las filas donde difieren son la tabla de diferencias
aprobada; agregar un caso nuevo es agregar una fila. Datos ficticios, precios en colones <= 2 000."""

from decimal import Decimal
from types import SimpleNamespace as N

import pytest
from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import product_typing as pt
from app.services import store_showcase as sv
from tests.test_store_vitrina import _publicar

SEG, RED, ENE = "Seguridad", "Redes y conectividad", "Respaldo eléctrico"
AUD, SH, ELE, ALM = "Audio y sonido", "Smart home", "Electrodomésticos", "Almacenamiento"

# (nombre, subcategoria, familia, funcional (no cambia), mostrado)
DORADA = [
    # fuera de seguridad, redes y energia: la subcategoria
    ("Amazon Echo Dot 5ta generación parlante inteligente Alexa WiFi", "Asistentes de voz", SH, "Cámara WiFi", "Asistentes de voz"),
    ("Amazon Fire TV Stick 4K control remoto por voz Alexa WiFi", "Streaming", SH, "Cámara WiFi", "Streaming"),
    ("Barra de sonido Thonet & Vander Kraftig 2.1 bluetooth inalámbrica", "Barras de sonido", AUD, "Cámara WiFi", "Barras de sonido"),
    ("Micrófono inalámbrico JBL Wireless Microphone doble", "Micrófonos", AUD, "Cámara WiFi", "Micrófonos"),
    ("Afeitadora Oraimo SmartShaver inalámbrica recargable", "Cuidado personal", ELE, "Cámara WiFi", "Cuidado personal"),
    ("Enchufe inteligente TP-Link Tapo KP303 regleta WiFi 3 tomas", "Enchufes inteligentes", SH, "Cámara WiFi", "Enchufes inteligentes"),
    ("Cámara WiFi Ezviz C6N 1080p interior", "Cámaras inteligentes", SH, "Cámara WiFi", "Cámaras inteligentes"),
    # dentro de las familias: «Cámara …» solo con categoria de camaras o palabra de camara en el nombre
    ("Repetidor de señal WiFi TP-Link RE205 AC750 doble banda", "Repetidores", RED, "Cámara WiFi", "Repetidores"),
    ("Barrera vehicular Hikvision DS-TMG4B2-L brazo 3 m control remoto inalámbrico", "Barreras vehiculares", SEG, "Cámara WiFi", "Barreras vehiculares"),
    ("Controlador de acceso Hikvision DS-K2622X 2 puertas TCP/IP WiFi", "Control de acceso", SEG, "Cámara WiFi", "Control de acceso"),
    ("Teclado inalámbrico para alarma Hikvision AX PRO", "Alarmas", SEG, "Alarma", "Alarma"),
    ("Bracket de pared Hikvision DS-1272ZJ-110", "Accesorios CCTV", SEG, "Gabinete", "Accesorios CCTV"),  # «rack» en «bracket»
    ("Cableado estructurado patch panel 24 puertos cat6", "Cableado", RED, "Cable", "Cableado"),  # «cable» en «cableado»
    # las camaras siguen siendo camaras (por palabra o por categoria)
    ("Cámara IP Hikvision DS-2CD1043G2-LIU bullet 4MP 2.8mm", "Cámaras", SEG, "Cámara IP", "Cámara IP"),
    ("Hikvision DS-2CD2143G2-I domo 4MP AcuSense", "Cámaras", SEG, "Cámara IP", "Cámara IP"),
    ("Hikvision DS-2DE4425IW-DE PTZ 4MP 25x", "Cámaras", SEG, "Cámara PTZ", "Cámara PTZ"),
    ("Hikvision DS-2CE16D0T-IT3F turbo HD bullet 2MP", "Cámaras", SEG, "Cámara analógica", "Cámara analógica"),
    ("Ezviz H6c Pro 2K WiFi pan tilt", "Cámaras WiFi", SEG, "Cámara WiFi", "Cámara WiFi"),
    ("Cámara de seguridad Tapo C200 1080P WiFi audio bidireccional", "Cámaras WiFi", SEG, "Cámara WiFi", "Cámara WiFi"),
    # lo demas de seguridad, redes y energia queda igual
    ("NVR Hikvision 8 canales DS-7608NXI-K1/8P PoE", "Grabadores", SEG, "Grabador NVR", "Grabador NVR"),
    ("Disco duro Western Digital Purple 4TB", "Discos duros", ALM, "Disco duro", "Disco duro"),
    ("Switch PoE Hikvision 8 puertos DS-3E0109P-E/M", "Switches", RED, "Switch PoE", "Switch PoE"),
    ("UPS APC Back-UPS 600VA", "UPS", ENE, "UPS", "UPS"),
    ("Rack de pared 6U con puerta de vidrio", "Gabinetes", RED, "Gabinete", "Gabinete"),
    ("Cable UTP cat6 305 m exterior", "Cables", RED, "Cable", "Cable"),
]
SIN_CAMARA = ("Echo Dot", "Fire TV", "Thonet", "JBL", "Oraimo", "RE205", "KP303", "DS-TMG", "DS-K2622X")


@pytest.mark.parametrize(("nombre", "sub", "fam", "funcional", "mostrado"), DORADA, ids=[r[0][:40] for r in DORADA])
def test_tabla_dorada(nombre, sub, fam, funcional, mostrado):
    p = N(name=nombre)
    assert sv.product_type(p) == funcional  # la llave de los kits no cambia
    assert pt.display_type(p, sub, fam) == mostrado


@pytest.mark.parametrize("nombre", [r[0] for r in DORADA if any(w in r[0] for w in SIN_CAMARA)])
def test_ninguno_dice_camara_ni_trae_datos_de_camara(nombre):
    """con su categoria y tambien sin ninguna (solo por el nombre)"""
    row = next(r for r in DORADA if r[0] == nombre)
    p = N(
        name=nombre, brand=None, model=None, description_store="Micrófono integrado. Visión nocturna IR 30 m. Lente 2.8 mm.", description_invoice=None, specs={}
    )
    for shown in (pt.display_type(p, row[1], row[2]), pt.display_type(p)):
        assert not shown.startswith("Cámara"), shown
        s = sv.derive_specs(p, shown)
        assert not {"Lente", "Visión nocturna", "Audio", "Resolución", "Formato"} & set(s), s


def test_derive_specs_ignora_los_rellenos_del_proveedor():
    cam = N(
        name="Cámara IP bullet 4MP 2.8mm",
        brand="Hikvision",
        model=None,
        description_store="- Micrófono: No indicado\n- Visión nocturna: No aplica\n- Protección IP67",
        description_invoice=None,
        specs={"Garantía": "No especificado", "Color": "Blanco"},
    )
    s = sv.derive_specs(cam, "Cámara IP")
    assert s["Tipo"] == "Cámara IP" and s["Lente"] == "2.8 mm" and s["Protección"] == "IP67"
    assert "Audio" not in s and "Visión nocturna" not in s and "Garantía" not in s and s["Color"] == "Blanco"
    # sin el tipo mostrado (armador de kits, equipment_reco) todo queda como antes
    viejo = sv.derive_specs(cam)
    assert viejo["Audio"] == "Micrófono integrado" and viejo["Garantía"] == "No especificado"


def test_cadena_de_categorias():
    cats = [N(id=1, name="Audio y sonido", parent_id=None), N(id=2, name="Barras de sonido", parent_id=1), N(id=3, name="Bucle", parent_id=3)]
    assert pt.category_chain(cats) == {1: ("Audio y sonido", "Audio y sonido"), 2: ("Barras de sonido", "Audio y sonido"), 3: ("Bucle", "Bucle")}


# ---------------------------------------------------------------- la tienda de punta a punta


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
    cams, nvrs, barras = cat("Cámaras", seg), cat("Grabadores", seg), cat("Barras de sonido", aud)
    alm = cat("Almacenamiento")
    img = lambda n: [{"url": f"https://cdn.example.com/{n}.jpg", "main": True}]  # noqa: E731
    datos = [
        ("BARRA", "Barra de sonido Resonar 2.1 con subwoofer inalámbrico", 1500, "Resonar", barras, "Micrófono: No indicado"),
        ("BARRA-2", "Barra de sonido Resonar mini inalámbrica", 900, "Resonar", barras, ""),
        ("CAM-WIFI", "Cámara WiFi domo 2MP interior microSD", 1200, "Vigil", cams, "Audio de dos vías"),
        ("CAM-IP", "Cámara IP bullet 4MP 2.8mm PoE", 1400, "Vigil", cams, ""),
        ("NVR-AGOTADO", "NVR 4 canales PoE agotado", 1800, "Vigil", nvrs, ""),
        ("NVR-OK", "NVR 4 canales PoE", 1900, "Vigil", nvrs, ""),
        ("HDD", "Disco duro Purple 1TB", 800, "Vigil", alm, ""),
        ("SD", "Memoria microSD 64GB para cámaras", 300, "Vigil", alm, ""),
    ]
    out = {}
    for code, name, price, brand, c, desc in datos:
        p = Product(
            tenant_id=t.id,
            code=code,
            name=name,
            price=price,
            currency="CRC",
            brand=brand,
            category_id=c.id,
            description_store=desc,
            images=img(code),
            show_on_web=True,
        )
        db.add(p)
        db.flush()
        out[code] = p
    db.commit()
    return out


def _existencia(client, pid, qty):
    wh = next(w for w in client.get("/warehouses").json() if w["is_default"])
    r = client.post("/stock/movements", json={"product_id": pid, "warehouse_id": wh["id"], "kind": "entrada", "quantity": qty})
    assert r.status_code == 201, r.text


def test_tarjeta_ficha_filtro_y_snapshot_usan_el_tipo_mostrado(client, auth, db_session):
    _publicar(client)
    prods = _tienda(db_session)
    for code in ("BARRA", "BARRA-2", "CAM-WIFI", "CAM-IP", "NVR-OK", "HDD", "SD"):
        _existencia(client, prods[code].id, 3)
    S = "/public/store/crimson"

    cat = client.get(f"{S}/catalog", params={"category": "barras-de-sonido"}).json()
    assert {i["type"] for i in cat["items"]} == {"Barras de sonido"}
    assert all(not t["name"].startswith("Cámara") for t in cat["facets"]["types"])
    camaras = client.get(f"{S}/catalog", params={"type": "Cámara WiFi"}).json()["items"]
    assert [i["code"] for i in camaras] == ["CAM-WIFI"]  # la barra ya no sale filtrando por camaras WiFi
    assert "BARRA" not in {i["code"] for i in client.get(f"{S}/catalog", params={"q": "camara"}).json()["items"]}

    barra = client.get(f"{S}/item/x-{prods['BARRA'].id}").json()
    assert barra["type"] == "Barras de sonido" and barra["specs"]["Tipo"] == "Barras de sonido"
    assert "Audio" not in barra["specs"]  # «Micrófono: No indicado» no es un microfono
    assert barra["accessories"] == []  # las reglas de accesorios solo aplican a camaras y grabadores
    assert [r["code"] for r in barra["related"]] == ["BARRA-2"]

    snap = client.get(f"{S}/snapshot").json()  # el cliente de pruebas ya lo descomprime
    tipos = {p["code"]: p["type"] for p in snap["products"]}
    assert tipos["BARRA"] == "Barras de sonido" and tipos["CAM-WIFI"] == "Cámara WiFi"
    assert all(not t["name"].startswith("Cámara") or t["count"] == 1 for t in snap["types"])


def test_que_necesito_ademas_sin_agotados_y_primero_lo_comprable(client, auth, db_session):
    _publicar(client)
    prods = _tienda(db_session)
    for code in ("CAM-IP", "NVR-OK", "HDD"):
        _existencia(client, prods[code].id, 2)
    S = "/public/store/crimson"
    ficha = client.get(f"{S}/item/x-{prods['CAM-IP'].id}").json()
    grupos = {g["title"]: [i["code"] for i in g["items"]] for g in ficha["accessories"]}
    assert grupos["Grabador"] == ["NVR-OK"]  # el agotado no se sugiere
    assert grupos["Disco para el grabador"] == ["HDD"]
    assert "Switch PoE" not in grupos  # sin un sugerido comprable el bloque no sale
    assert all(r["availability"]["state"] != "agotado" for r in ficha["related"])
    # la camara WiFi sugiere la memoria solo si se puede comprar; sin existencia, el bloque no sale
    wifi = client.get(f"{S}/item/x-{prods['CAM-WIFI'].id}").json()
    assert wifi["accessories"] == []
    _existencia(client, prods["SD"].id, 5)
    wifi = client.get(f"{S}/item/x-{prods['CAM-WIFI'].id}").json()
    assert [[i["code"] for i in g["items"]] for g in wifi["accessories"]] == [["SD"]]
    assert Decimal(str(wifi["accessories"][0]["items"][0]["display_price"])) > 0
