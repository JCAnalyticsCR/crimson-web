"""Kits editables (pedido de Andres): en Mi Tienda -> Vitrina cada kit puede fijar un producto por pieza y sumar
adicionales; en "Arma tu sistema" cada pieza trae mas informacion y alternativas para cambiarla (la eleccion viaja como
choices y el servidor la valida); y las sugerencias automaticas varian de un dia a otro entre equivalentes.

Datos ficticios, precios en colones <= 2 000."""

import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.models import ExchangeRate, Product, Tenant
from app.services import store_showcase as sv
from tests.test_store_vitrina import _publicar

KIT = "/public/store/crimson/kit"
PORTADA = "/public/store/crimson/showcase"
CASA = {"place": "casa", "indoor": 2, "outdoor": 2, "days": 15, "remote": True}  # 1.4 TB
PIEZAS = set(sv.KIT_ROLES)


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _img(code: str) -> list[dict]:
    return [{"url": f"https://cdn.example.com/kit/{code.lower().replace('/', '-')}.jpg", "main": True}]


def _catalogo(db) -> dict[str, Product]:
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False  # solo cuenta el catalogo de esta prueba
    datos = [
        # (codigo, nombre, precio)
        ("CAM-ALFA", "Cámara IP Bullet 4MP PoE Alfa 2.8mm IR 30m", 1000),
        ("CAM-BETA", "Cámara IP Bullet 4MP PoE Beta 2.8mm", 1050),
        ("CAM-GAMA", "Cámara IP Bullet 4MP PoE Gama 4mm", 1100),
        ("CAM-CARA", "Cámara IP Bullet 4MP PoE Cara 2.8mm", 1500),  # fuera del 18 %: nunca rota
        ("CAM-8MP", "Cámara IP Bullet 8MP PoE Delta 2.8mm", 1900),  # otro nivel de ajuste: nunca rota
        ("DOMO-UNO", "Cámara IP Domo 4MP PoE Uno", 900),
        ("DOMO-DOS", "Cámara IP Domo 2MP PoE Dos", 700),
        ("CAM-WIFI", "Cámara WiFi Domo 4MP interior", 600),
        ("NVR-4", "NVR 4 canales Hikvision", 1200),
        ("NVR-8/8P", "NVR 8 canales con PoE", 1800),
        ("NVR-16", "NVR 16 canales", 2000),
        ("HDD-1", "Disco duro 1TB Purple", 500),
        ("HDD-2", "Disco duro 2TB Purple", 800),
        ("HDD-4", "Disco duro 4TB Purple", 1400),
        ("SW-8", "Switch PoE 8 puertos", 900),
        ("UPS-600", "UPS 600VA respaldo", 1900),
    ]
    out = {}
    for code, name, price in datos:
        p = Product(tenant_id=t.id, code=code, name=name, price=price, currency="CRC", images=_img(code), show_on_web=True)
        db.add(p)
        db.flush()
        out[code] = p
    db.commit()
    return out


def _mas(db, c: dict[str, Product], code: str, name: str, price, currency: str = "CRC", **kw) -> Product:
    """un producto mas en el catalogo de la prueba"""
    p = Product(tenant_id=c["CAM-ALFA"].tenant_id, code=code, name=name, price=price, currency=currency, images=_img(code), show_on_web=True, **kw)
    db.add(p)
    db.commit()
    c[code] = p
    return p


def _kits(client) -> list[dict]:
    return client.get("/store/showcase").json()["kits"]


def _put_kits(client, kits):
    r = client.put("/store/showcase", json={"kits": kits})
    assert r.status_code == 200, r.text
    return r.json()


def _roles(k: dict) -> dict:
    return {ln["key"]: ln for ln in k["lines"]}


def _d(v) -> Decimal:
    return Decimal(str(v))


def _reloj(monkeypatch, cuando: datetime) -> None:
    from app.core import timeutil

    monkeypatch.setattr(timeutil, "_utcnow", lambda: cuando)


def _linea(k: dict, key: str) -> dict:
    """la linea de una pieza en lo que devuelve sv.build_kit (productos, no tarjetas)"""
    return next(ln for ln in k["lines"] if ln["key"] == key)


# ---------------------------------------------------------------- Mi Tienda: productos fijos y adicionales
def test_pins_y_adicionales_en_tarjeta_vista_previa_y_kit(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    otra = Tenant(slug="otra-kits", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    ajeno = Product(tenant_id=otra.id, code="AJENO", name="NVR 8 canales ajeno", price=100, currency="CRC", show_on_web=True)
    db_session.add(ajeno)
    # el grabador que se va a fijar tiene precio web distinto del de lista: la tarjeta, la vista previa y la pagina
    # cobran el precio web (el del carrito), no el de lista
    c["NVR-8/8P"].web_price = 1900
    db_session.commit()

    etag = client.get(PORTADA).headers["etag"]
    kits = _kits(client)
    casa = next(k for k in kits if k["id"] == "casa-4")
    casa["pins"] = {"nvr": c["NVR-8/8P"].id, "disk": c["HDD-4"].id, "cam_in": c["HDD-1"].id, "cam_out": ajeno.id}
    casa["extras"] = [{"product_id": c["UPS-600"].id, "quantity": 2}, {"product_id": ajeno.id, "quantity": 1}]
    out = _put_kits(client, kits)
    guardado = next(k for k in out["kits"] if k["id"] == "casa-4")
    # lo que nunca sirve (un disco como camara) o es de otra empresa se descarta con aviso; lo demas queda fijo
    assert guardado["pins"] == {"nvr": c["NVR-8/8P"].id, "disk": c["HDD-4"].id}
    assert guardado["extras"] == [{"product_id": c["UPS-600"].id, "quantity": 2}]
    # el motivo se lee como esta escrito (solo baja la inicial): «cámara IP», no «cámara ip»
    assert any("Cámaras interiores" in w and "(no es una cámara IP)" in w for w in out["warnings"])
    assert any("adicional" in w for w in out["warnings"]) and len(out["warnings"]) == 3
    # guardar la vitrina sube la version: quien tenia la portada en cache recibe la nueva, no un 304 con el kit viejo
    r = client.get(PORTADA, headers={"If-None-Match": etag})
    assert r.status_code == 200 and r.headers["etag"] != etag

    pv = next(x for x in out["kits_preview"] if x["id"] == "casa-4")
    assert {p["key"]: (p["ok"], p["used"]) for p in pv["pins"]} == {"nvr": (True, True), "disk": (True, True)}
    lines = {ln["key"]: ln for ln in pv["lines"]}
    assert lines["nvr"]["product_id"] == c["NVR-8/8P"].id and lines["nvr"]["source"] == "pin"
    assert "switch" not in lines  # el NVR fijado trae PoE: no hace falta switch
    assert lines["disk"]["product_id"] == c["HDD-4"].id and lines["disk"]["quantity"] == 1
    assert lines[f"extra-{c['UPS-600'].id}"]["quantity"] == 2
    roles = {r["key"]: r for r in pv["roles"]}
    assert roles["cam_out"]["source"] == "auto" and len(roles["cam_out"]["options"]) >= 3  # candidatos para fijar

    # la tarjeta de la portada y "Arma tu sistema" abierto desde ella: mismos productos y mismo total
    card = next(k for k in client.get(PORTADA).json()["kits"] if k["id"] == "casa-4")
    r = client.post(KIT, json={**CASA, "kit": "casa-4"})
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["kit"] == {"id": "casa-4", "title": casa["title"]}
    assert _d(card["total"]) == _d(k["total"]) == _d(pv["total"])
    roles = _roles(k)
    assert roles["nvr"]["product"]["id"] == c["NVR-8/8P"].id and roles["nvr"]["source"] == "pin" and roles["nvr"]["pinned"] is True
    assert _d(roles["nvr"]["unit_price"]) == Decimal("2147.00")  # 1 900 de precio web + IVA (de lista serian 2 034)
    assert _d(k["total"]) == sum(_d(ln["subtotal"]) for ln in k["lines"])
    assert roles[f"extra-{c['UPS-600'].id}"]["role"] == "Adicional" and roles[f"extra-{c['UPS-600'].id}"]["quantity"] == 2
    assert card["items"] == sum(ln["quantity"] for ln in k["lines"])
    assert "pin_status" not in k  # lo interno de Mi Tienda no sale a la tienda

    # se despublica el NVR fijado: vuelve a la sugerencia automatica y la vista previa dice por que
    c["NVR-8/8P"].show_on_web = False
    db_session.commit()
    pv = next(x for x in client.get("/store/showcase").json()["kits_preview"] if x["id"] == "casa-4")
    pin = next(p for p in pv["pins"] if p["key"] == "nvr")
    assert pin["ok"] is False and pin["reason"] == "No está publicado en la web."
    nvr = next(ln for ln in pv["lines"] if ln["key"] == "nvr")
    assert nvr["source"] == "auto" and nvr["product_id"] == c["NVR-4"].id
    assert any(ln["key"] == "switch" for ln in pv["lines"])  # el automatico no trae PoE: vuelve el switch
    k2 = client.post(KIT, json={**CASA, "kit": "casa-4"}).json()
    assert _roles(k2)["nvr"]["product"]["id"] == c["NVR-4"].id

    # un NVR de 4 canales fijado en un kit de 8 camaras no sirve: automatico con el motivo
    kits = _kits(client)
    neg = next(k for k in kits if k["id"] == "negocio-8")
    neg["pins"] = {"nvr": c["NVR-4"].id}
    out = _put_kits(client, kits)
    pin = next(p for p in next(x for x in out["kits_preview"] if x["id"] == "negocio-8")["pins"] if p["key"] == "nvr")
    assert pin["ok"] is False and "4 canales" in pin["reason"] and "8 cámaras" in pin["reason"]
    # una pieza desconocida es un error de quien llama (422), no se guarda a ciegas
    assert client.put("/store/showcase", json={"kits": [{**neg, "pins": {"bateria": 1}}]}).status_code == 422


def test_el_id_del_kit_decide_entre_dos_kits_con_las_mismas_respuestas(client, auth, db_session):
    """La tarjeta manda el id de SU kit: con dos kits de las mismas respuestas, cada pagina arma el suyo (sus productos
    fijos) y cuadra con su tarjeta."""
    _publicar(client)
    c = _catalogo(db_session)
    kits = _kits(client)
    casa = next(k for k in kits if k["id"] == "casa-4")
    gemelo = {**casa, "id": "casa-4b", "title": "Casa con grabador PoE", "pins": {"nvr": c["NVR-8/8P"].id}}
    _put_kits(client, [*kits, gemelo])
    cards = {k["id"]: k for k in client.get(PORTADA).json()["kits"]}
    a = client.post(KIT, json={**CASA, "kit": "casa-4"}).json()
    b = client.post(KIT, json={**CASA, "kit": "casa-4b"}).json()
    assert a["kit"]["id"] == "casa-4" and _roles(a)["nvr"]["product"]["id"] == c["NVR-4"].id and "switch" in _roles(a)
    assert b["kit"]["id"] == "casa-4b" and _roles(b)["nvr"]["product"]["id"] == c["NVR-8/8P"].id and "switch" not in _roles(b)
    assert _roles(b)["nvr"]["source"] == "pin"
    assert _d(a["total"]) == _d(cards["casa-4"]["total"]) and _d(b["total"]) == _d(cards["casa-4b"]["total"])
    assert _d(a["total"]) != _d(b["total"])


def test_kits_con_el_mismo_id_reciben_uno_propio_al_guardar(client, auth, db_session):
    """«+ Kit» generaba ids repetidos (kit-4 dos veces): la vista previa de Mi Tienda empareja por kit, asi que cada uno
    necesita el suyo. El API lo corrige al guardar y devuelve la vista previa en el mismo orden que los kits."""
    _publicar(client)
    c = _catalogo(db_session)
    kits = _kits(client)
    uno = {**kits[0], "id": "kit-4", "title": "Uno"}
    dos = {**kits[2], "id": "kit-4", "title": "Dos", "pins": {"nvr": c["NVR-4"].id}}  # bodega de 16 camaras
    out = _put_kits(client, [uno, dos])
    assert [k["id"] for k in out["kits"]] == ["kit-4", "kit-4-2"]
    assert [x["id"] for x in out["kits_preview"]] == ["kit-4", "kit-4-2"]
    # cada vista previa es la de SU kit: el motivo del grabador fijado sale en el segundo, no se pierde
    assert out["kits_preview"][0]["pins"] == []
    pin = out["kits_preview"][1]["pins"][0]
    assert pin["key"] == "nvr" and pin["ok"] is False and "4 canales" in pin["reason"] and "16 cámaras" in pin["reason"]


# ---------------------------------------------------------------- "Arma tu sistema": mas informacion y "Cambiar"
def test_alternativas_y_cambios_validados(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    k = client.post(KIT, json=CASA).json()
    roles = _roles(k)
    cam = roles["cam_out"]
    # mas informacion por linea: datos clave, precio unitario y alternativas rotuladas
    assert "4 MP" in cam["specs"] and "Bullet" in cam["specs"]
    assert _d(cam["unit_price"]) * cam["quantity"] == _d(cam["subtotal"])
    assert 2 <= len(cam["alternatives"]) <= 4
    assert all(a["label"] in ("Económica", "Recomendada", "Superior", "Similar") and a["product"]["image"] for a in cam["alternatives"])
    assert any(a["label"] == "Superior" and a["product"]["id"] == c["CAM-8MP"].id for a in cam["alternatives"])
    assert "4 canales" in roles["nvr"]["specs"] and "Sin PoE" in roles["nvr"]["specs"]
    assert roles["disk"]["specs"][0] == "2 TB" and roles["switch"]["specs"][:2] == ["8 puertos", "PoE"]
    s = k["summary"]
    assert (s["indoor"], s["outdoor"], s["days"], s["poe"], s["switch_ports"]) == (2, 2, 15, "switch", 8)
    assert _d(s["installed_tb"]) == 2 and _d(s["need_tb"]) == Decimal("1.4")

    # el cliente elige la superior: cambia la linea y el total, por la diferencia anunciada
    sup = next(a for a in cam["alternatives"] if a["product"]["id"] == c["CAM-8MP"].id)
    k2 = client.post(KIT, json={**CASA, "choices": {"cam_out": c["CAM-8MP"].id}}).json()
    assert _roles(k2)["cam_out"]["product"]["id"] == c["CAM-8MP"].id and _roles(k2)["cam_out"]["source"] == "choice"
    assert _d(k2["total"]) == _d(k["total"]) + _d(sup["diff"])
    assert k2["choices"] == {"cam_out": c["CAM-8MP"].id}

    # un NVR con PoE elegido quita el switch
    k3 = client.post(KIT, json={**CASA, "choices": {"nvr": c["NVR-8/8P"].id}}).json()
    assert "switch" not in _roles(k3) and k3["summary"]["poe"] == "grabador"
    assert "PoE integrado (8 puertos)" in _roles(k3)["nvr"]["specs"]

    # elecciones que no calzan se ignoran (sin error: vienen de un enlace)
    otra = Tenant(slug="otra-kits-2", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    ajeno = Product(tenant_id=otra.id, code="AJ-CAM", name="Cámara IP Bullet 4MP PoE ajena", price=100, currency="CRC", show_on_web=True)
    oculta = Product(tenant_id=c["CAM-ALFA"].tenant_id, code="CAM-OC", name="Cámara IP Bullet 4MP PoE oculta", price=950, currency="CRC", show_on_web=False)
    db_session.add_all([ajeno, oculta])
    db_session.commit()
    malas = {
        "nvr": c["HDD-4"].id,  # pieza equivocada
        "cam_in": c["CAM-WIFI"].id,  # incompatible (WiFi)
        "cam_out": ajeno.id,  # de otra empresa
        "disk": 999999,  # no existe
        "switch": oculta.id,  # sin publicar
        "bateria": c["UPS-600"].id,  # pieza desconocida
    }
    r = client.post(KIT, json={**CASA, "choices": malas})
    assert r.status_code == 200, r.text
    k4 = r.json()
    assert k4["choices"] == {}
    assert {key: ln["product"]["id"] for key, ln in _roles(k4).items()} == {key: ln["product"]["id"] for key, ln in roles.items()}
    assert k4["total"] == k["total"]
    oc = client.post(KIT, json={**CASA, "choices": {"cam_out": oculta.id}}).json()
    assert oc["choices"] == {} and _roles(oc)["cam_out"]["product"]["id"] != oculta.id


def test_la_diferencia_del_grabador_cuenta_el_switch_que_entra_o_sale(client, auth, db_session):
    """El grabador decide si el kit lleva switch: la diferencia y el rotulo de cada alternativa son los del KIT, en los
    dos sentidos (total despues de elegir = total + diferencia anunciada)."""
    _publicar(client)
    c = _catalogo(db_session)

    def elegir(k: dict, alt: dict) -> dict:
        k2 = client.post(KIT, json={**CASA, "choices": {"nvr": alt["product"]["id"]}}).json()
        assert _roles(k2)["nvr"]["product"]["id"] == alt["product"]["id"]
        assert _d(k2["total"]) == _d(k["total"]) + _d(alt["diff"]), (alt["product"]["code"], alt["label"], alt["diff"])
        return k2

    # 1) hoy el grabador no trae PoE y va con switch: uno con PoE integrado SACA el switch
    k = client.post(KIT, json=CASA).json()
    r = _roles(k)
    assert r["nvr"]["product"]["id"] == c["NVR-4"].id and "switch" in r
    poe = next(a for a in r["nvr"]["alternatives"] if a["product"]["id"] == c["NVR-8/8P"].id)
    # la linea sola sube (2 034 contra 1 356), pero el kit BAJA porque el switch (1 017) ya no hace falta
    assert _d(poe["subtotal"]) > _d(r["nvr"]["subtotal"])
    assert _d(poe["diff"]) == _d(poe["subtotal"]) - _d(r["nvr"]["subtotal"]) - _d(r["switch"]["subtotal"]) == Decimal("-339.00")
    assert "Switch PoE 8 puertos" in poe["note"] and "ya no hace falta" in poe["note"]
    assert "switch" not in _roles(elegir(k, poe))
    for a in r["nvr"]["alternatives"]:
        elegir(k, a)

    # 2) al reves: el grabador de hoy trae PoE (sin switch) y la alternativa sin PoE METE el switch
    _mas(db_session, c, "NVR-4/4P", "NVR 4 canales con PoE", 1500)
    k = client.post(KIT, json=CASA).json()
    r = _roles(k)
    assert r["nvr"]["product"]["id"] == c["NVR-4/4P"].id and "switch" not in r and k["summary"]["poe"] == "grabador"
    sin = next(a for a in r["nvr"]["alternatives"] if a["product"]["id"] == c["NVR-4"].id)
    # la linea sola es mas barata (1 356 contra 1 695), pero el kit SUBE: no puede decir «Económica»
    assert _d(sin["subtotal"]) < _d(r["nvr"]["subtotal"])
    assert _d(sin["diff"]) == Decimal("678.00") and sin["label"] != "Económica"
    assert "Switch PoE 8 puertos" in sin["note"] and "suma" in sin["note"]
    assert "switch" in _roles(elegir(k, sin))
    for a in r["nvr"]["alternatives"]:
        elegir(k, a)
        assert a["label"] != "Económica" or _d(a["diff"]) < 0


def test_la_sugerencia_y_los_rotulos_usan_el_precio_que_ve_el_cliente(client, auth, db_session, monkeypatch):
    """Lo que se compara para sugerir (orden, equivalentes hasta 18 %) y para rotular («Económica») es el precio que ve
    el cliente: el precio web, con IVA y en la moneda de la tienda. No el numero crudo de la lista."""
    _publicar(client)
    c = _catalogo(db_session)
    # ALFA es la mas barata de LISTA (1 000) pero su precio web es 1 900: al cliente le cuesta mas que BETA y GAMA
    c["CAM-ALFA"].web_price = 1900
    # y una en dolares: "3" es el numero mas chico, pero son ~1 737 colones con IVA
    usd = _mas(db_session, c, "CAM-USD", "Cámara IP Bullet 4MP PoE Dolar 2.8mm", 3, currency="USD")
    mas_barata = Decimal("1186.50")  # BETA: 1 050 + IVA
    sugeridas = set()
    for dia in range(1, 15):
        _reloj(monkeypatch, datetime(2026, 11, dia, 15, 0, tzinfo=UTC))
        k = client.post(KIT, json=CASA).json()
        cam = _roles(k)["cam_out"]
        sugeridas.add(cam["product"]["id"])
        # la sugerida esta dentro del 18 % de la mas barata QUE VE EL CLIENTE
        assert _d(cam["unit_price"]) <= mas_barata * sv.VARIETY_BAND, (dia, cam["product"]["code"])
        for ln in k["lines"]:
            for a in ln["alternatives"]:
                # el rotulo nunca contradice el monto: «Económica» siempre baja el total
                assert a["label"] != "Económica" or _d(a["diff"]) < 0, (dia, ln["key"], a["product"]["code"], a["diff"])
        for a in cam["alternatives"]:
            if a["product"]["id"] in (usd.id, c["CAM-ALFA"].id):
                assert _d(a["diff"]) > 0 and a["label"] != "Económica"
    assert sugeridas == {c["CAM-BETA"].id, c["CAM-GAMA"].id}  # rota, pero solo entre las que cuestan parecido
    # el precio en dolares se compara convertido: 3 USD x 512,35 + IVA
    k = client.post(KIT, json={**CASA, "choices": {"cam_out": usd.id}}).json()
    assert _d(_roles(k)["cam_out"]["unit_price"]) == Decimal("1736.87")


def test_un_producto_a_consultar_no_se_sugiere_ni_se_ofrece(client, auth, db_session, monkeypatch):
    """Retenido por margen o sin tipo de cambio = "precio a consultar": no entra a la rotacion (bajaba el total de la
    tarjeta unos dias si y otros no), no se ofrece como alternativa y no se puede elegir ni fijar."""
    _publicar(client)
    c = _catalogo(db_session)
    c["CAM-BETA"].margin_hold = True  # el guardian de margen lo saco de venta
    db_session.execute(delete(ExchangeRate))  # sin tipo de cambio, lo que esta en dolares queda "a consultar"
    usd = _mas(db_session, c, "CAM-USD", "Cámara IP Bullet 4MP PoE Dolar 2.8mm", 2, currency="USD")
    fuera = {c["CAM-BETA"].id, usd.id}
    totales = set()
    for dia in range(1, 15):
        _reloj(monkeypatch, datetime(2026, 11, dia, 15, 0, tzinfo=UTC))
        card = next(k for k in client.get(PORTADA).json()["kits"] if k["id"] == "casa-4")
        k = client.post(KIT, json={**CASA, "kit": "casa-4"}).json()
        assert _roles(k)["cam_out"]["product"]["id"] not in fuera, dia
        assert not any(a["product"]["id"] in fuera for ln in k["lines"] for a in ln["alternatives"])
        assert not any(ln["product"]["price_on_request"] for ln in k["lines"])
        # la tarjeta siempre lleva las 7 piezas (2 + 2 camaras, grabador, disco y switch) y cuadra con la pagina
        assert card["items"] == 7 and _d(card["total"]) == _d(k["total"])
        totales.add(_d(card["total"]))
    assert max(totales) - min(totales) <= Decimal("226.00")  # solo se mueve entre equivalentes (ALFA y GAMA)
    # desde un enlace tampoco vale...
    for pid in fuera:
        k = client.post(KIT, json={**CASA, "choices": {"cam_out": pid}}).json()
        assert k["choices"] == {} and _roles(k)["cam_out"]["product"]["id"] not in fuera
    # ...ni fijado en el kit: vuelve a la sugerencia automatica y Mi Tienda dice por que
    kits = _kits(client)
    next(k for k in kits if k["id"] == "casa-4")["pins"] = {"cam_out": c["CAM-BETA"].id}
    next(k for k in kits if k["id"] == "negocio-8")["pins"] = {"cam_out": usd.id}
    next(k for k in kits if k["id"] == "negocio-8")["extras"] = [{"product_id": c["CAM-BETA"].id, "quantity": 1}]
    out = _put_kits(client, kits)
    casa = next(x for x in out["kits_preview"] if x["id"] == "casa-4")
    assert casa["pins"][0]["ok"] is False and "margen" in casa["pins"][0]["reason"]
    assert next(ln for ln in casa["lines"] if ln["key"] == "cam_out")["source"] == "auto"
    neg = next(x for x in out["kits_preview"] if x["id"] == "negocio-8")
    assert neg["pins"][0]["ok"] is False and "tipo de cambio" in neg["pins"][0]["reason"]
    assert neg["extras"][0]["ok"] is False and "margen" in neg["extras"][0]["reason"]
    assert all(ln["product_id"] not in fuera for ln in neg["lines"])


def test_la_recomendada_se_puede_elegir_y_no_cuenta_como_cambio_del_cliente(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    # (a) Pieza fijada con un producto que hoy no se puede comprar. La «Recomendada» sale de lo que SI se puede comprar
    # (un disco que no es de videovigilancia): entre TODO el catalogo no seria candidato, pero el servidor la ofrece,
    # asi que elegirla tiene que funcionar (antes el toque no hacia nada: seguia el fijado).
    blue = _mas(db_session, c, "HDD-2B", "Disco duro 2TB Blue", 600)
    prods = list(c.values())
    comprable = {p.id for p in prods if "Purple" not in p.name}
    k = sv.build_kit(prods, CASA, comprable, pins={"disk": c["HDD-4"].id}, alternatives=True)
    disk = _linea(k, "disk")
    assert disk["product"].id == c["HDD-4"].id and disk["source"] == "pin" and disk["pinned"] is True
    rec = next(a for a in disk["alternatives"] if a["auto"])
    assert rec["product"].id == blue.id and rec["label"] == "Recomendada"
    k = sv.build_kit(prods, CASA, comprable, pins={"disk": c["HDD-4"].id}, choices={"disk": blue.id}, alternatives=True)
    disk = _linea(k, "disk")
    assert disk["product"].id == blue.id and disk["source"] == "choice" and k["choices"] == {"disk": blue.id}
    assert disk["pinned"] is True and disk["suggested"] is False  # el kit fija otro: aqui si es un cambio del cliente
    assert k["pin_status"]["disk"]["ok"] is True and k["pin_status"]["disk"]["used"] is False

    # (b) Sin producto fijado, la «Recomendada» es la sugerencia de hoy: la tienda la pide SIN eleccion (vuelve a
    # automatico). Y si un enlace la trae como eleccion, se respeta pero la linea avisa que no es un cambio.
    blue.show_on_web = False
    db_session.commit()
    k = client.post(KIT, json={**CASA, "choices": {"disk": c["HDD-4"].id}}).json()
    disk = _roles(k)["disk"]
    assert (disk["source"], disk["suggested"], disk["pinned"]) == ("choice", False, False)
    rec = next(a for a in disk["alternatives"] if a["auto"])
    assert rec["product"]["id"] == c["HDD-2"].id and rec["label"] == "Recomendada"
    k = client.post(KIT, json={**CASA, "choices": {"disk": c["HDD-2"].id}}).json()
    disk = _roles(k)["disk"]
    assert (disk["source"], disk["suggested"]) == ("choice", True) and k["choices"] == {"disk": c["HDD-2"].id}
    assert _roles(client.post(KIT, json=CASA).json())["disk"]["source"] == "auto"


def test_numeros_no_finitos_en_el_cuerpo_nunca_dan_500(client, auth, db_session):
    """json.loads acepta Infinity, NaN y 1e999. En lo que viene de un enlace (choices, kit) se ignoran; en una respuesta
    del configurador es un 422. Nunca un error interno (el cliente de prueba relanza cualquier excepcion del servidor)."""
    _publicar(client)
    _catalogo(db_session)
    base = client.post(KIT, json=CASA).json()

    def crudo(extra: str, cuerpo: str = '"place":"casa","indoor":2,"outdoor":2,"days":15,"remote":true,'):
        return client.post(KIT, content=("{" + cuerpo + extra + "}").encode(), headers={"Content-Type": "application/json"})

    for lit in ("1e999", "-1e999", "1e400", "Infinity", "-Infinity", "NaN"):
        r = crudo(f'"choices":{{"nvr":{lit},"disk":{lit},"cam_out":{lit}}}')
        assert r.status_code == 200, (lit, r.text)
        assert r.json()["choices"] == {} and r.json()["total"] == base["total"]
        assert crudo(f'"choices":{lit}').status_code == 200, lit
        assert crudo(f'"kit":{lit}').status_code == 200, lit
        assert crudo(f'"outdoor":{lit}', cuerpo='"place":"casa","indoor":2,').status_code == 422, lit
    assert crudo('"choices":{"nvr":true,"disk":123456789012345678901234567890,"cam_out":[1],"cam_in":"x"}').json()["choices"] == {}

    # Mi Tienda: un producto fijado no finito se descarta sin tumbar el guardado
    kits = _kits(client)
    next(k for k in kits if k["id"] == "casa-4")["pins"] = {"nvr": "__INF__"}
    r = client.put("/store/showcase", content=json.dumps({"kits": kits}).replace('"__INF__"', "1e999").encode(), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert next(k for k in r.json()["kits"] if k["id"] == "casa-4")["pins"] == {}
    # y un adicional no finito es un 422 normal
    next(k for k in kits if k["id"] == "casa-4").update(pins={}, extras=[{"product_id": "__INF__", "quantity": 1}])
    r = client.put(
        "/store/showcase", content=json.dumps({"kits": kits}).replace('"__INF__"', "Infinity").encode(), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422, r.text
    assert sv.clean_pins({"nvr": float("inf"), "disk": float("nan"), "switch": True, "cam_in": 7}) == {"cam_in": 7}
    assert sv.clean_extras([{"product_id": float("inf")}, {"product_id": 5, "quantity": float("inf")}, {"product_id": 6}]) == [{"product_id": 6, "quantity": 1}]


def test_cantidad_de_discos_se_recalcula_al_cambiar_capacidad(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    neg = {"place": "negocio", "indoor": 4, "outdoor": 4, "days": 30, "remote": True}  # 8 x 30 x 21 GB x 1.1 = 5.5 TB
    k = client.post(KIT, json=neg).json()
    disk = _roles(k)["disk"]
    assert disk["product"]["id"] == c["HDD-4"].id and disk["quantity"] == 2
    k2 = client.post(KIT, json={**neg, "choices": {"disk": c["HDD-2"].id}}).json()
    d2 = _roles(k2)["disk"]
    assert d2["product"]["id"] == c["HDD-2"].id and d2["quantity"] == 3 and _d(d2["subtotal"]) == 3 * _d(d2["unit_price"])
    assert _d(k2["summary"]["installed_tb"]) == 6
    alt = next(a for a in disk["alternatives"] if a["product"]["id"] == c["HDD-2"].id)
    assert alt["quantity"] == 3 and _d(k2["total"]) == _d(k["total"]) + _d(alt["diff"])
    # 1 TB pediria 6 discos: no es alternativa ni se acepta como eleccion
    assert all(a["product"]["id"] != c["HDD-1"].id for a in disk["alternatives"])
    assert client.post(KIT, json={**neg, "choices": {"disk": c["HDD-1"].id}}).json()["choices"] == {}
    # la eleccion sobrevive al cambiar respuestas mientras siga calzando (casa: 1.4 TB -> 1 disco de 2 TB)
    k3 = client.post(KIT, json={**CASA, "choices": {"disk": c["HDD-2"].id}}).json()
    assert _roles(k3)["disk"]["quantity"] == 1 and k3["choices"] == {"disk": c["HDD-2"].id}


def test_nvr_con_menos_puertos_poe_que_camaras_conserva_el_switch(client, auth, db_session):
    """Un grabador de 32 canales con 16 puertos PoE ("/16P") no alimenta 20 camaras: el kit lleva switch y no dice
    «PoE integrado en el grabador». Con 16 camaras si alcanza."""
    _publicar(client)
    c = _catalogo(db_session)
    nvr = _mas(db_session, c, "NVR-7732/16P", "NVR 32 canales con 16 puertos PoE", 2000)
    _mas(db_session, c, "NVR-32", "NVR 32 canales", 1900)
    _mas(db_session, c, "SW-24", "Switch PoE 24 puertos", 1500)
    veinte = {"place": "bodega", "indoor": 4, "outdoor": 16, "days": 15, "remote": True}
    for body in (veinte, {**veinte, "choices": {"nvr": nvr.id}}):
        k = client.post(KIT, json=body).json()
        r = _roles(k)
        assert k["cameras"] == 20 and r["switch"]["product"]["id"] == c["SW-24"].id, body
        assert (k["summary"]["poe"], k["summary"]["switch_ports"]) == ("switch", 24)
        assert not any(m["key"] == "switch" for m in k["missing"])
    r = _roles(k)  # con el /16P elegido
    assert r["nvr"]["product"]["id"] == nvr.id and "PoE integrado (16 puertos)" in r["nvr"]["specs"]
    assert "no alcanzan para 20 cámaras" in r["nvr"]["reason"] and "no hace falta switch" not in r["nvr"]["reason"]
    # 16 camaras: los 16 puertos alcanzan, sin switch
    k = client.post(KIT, json={**veinte, "outdoor": 12, "choices": {"nvr": nvr.id}}).json()
    assert k["cameras"] == 16 and "switch" not in _roles(k) and k["summary"]["poe"] == "grabador"
    assert "no hace falta switch" in _roles(k)["nvr"]["reason"]
    assert sv.nvr_poe_ports(nvr) == 16 and sv.nvr_poe_covers(nvr, 16) and not sv.nvr_poe_covers(nvr, 17)
    assert sv.nvr_poe_ports(c["NVR-4"]) is None and not sv.nvr_poe_covers(c["NVR-4"], 1)


# ---------------------------------------------------------------- variedad diaria
def test_rotacion_diaria_entre_equivalentes_y_pins_fijos(db_session):
    c = _catalogo(db_session)
    # el borde de la banda: 1 180 es hasta 18 % mas que la mas barata (1 000); 1 190 ya no
    dentro = _mas(db_session, c, "CAM-1180", "Cámara IP Bullet 4MP PoE Borde 2.8mm", 1180)
    fuera = _mas(db_session, c, "CAM-1190", "Cámara IP Bullet 4MP PoE Limite 2.8mm", 1190)
    prods = [p for p in c.values()]
    eq = {c["CAM-ALFA"].id, c["CAM-BETA"].id, c["CAM-GAMA"].id, dentro.id}

    def cam(day, **kw):
        k = sv.build_kit(prods, CASA, day=day, **kw)
        return next(ln for ln in k["lines"] if ln["key"] == "cam_out")["product"].id

    hoy = date(2026, 11, 10)
    # mismo dia y mismas respuestas: siempre el mismo
    assert len({cam(hoy) for _ in range(5)}) == 1
    picks = {cam(date.fromordinal(hoy.toordinal() + i)) for i in range(60)}
    assert picks == eq and fuera.id not in picks  # varia entre TODOS los equivalentes (mismo ajuste, hasta 18 % mas) y solo entre ellos
    # los fijados en Mi Tienda nunca rotan
    assert {cam(date.fromordinal(hoy.toordinal() + i), pins={"cam_out": c["CAM-CARA"].id}) for i in range(20)} == {c["CAM-CARA"].id}
    # lo que se puede comprar ya manda sobre la rotacion: con un solo equivalente vendible, siempre ese
    assert {cam(date.fromordinal(hoy.toordinal() + i), prefer={c["CAM-GAMA"].id}) for i in range(20)} == {c["CAM-GAMA"].id}


def test_misma_sugerencia_en_tarjeta_y_pagina_el_mismo_dia(client, auth, db_session, monkeypatch):
    _publicar(client)
    c = _catalogo(db_session)
    vistos, etags, ayer = set(), set(), None
    for dia in range(1, 29):
        mediodia = datetime(2026, 11, dia, 15, 0, tzinfo=UTC)  # 09:00 en Costa Rica
        _reloj(monkeypatch, mediodia)
        r = client.get(PORTADA)
        card = next(k for k in r.json()["kits"] if k["id"] == "casa-4")
        k = client.post(KIT, json={**CASA, "kit": "casa-4"}).json()
        assert _d(card["total"]) == _d(k["total"])
        cam = _roles(k)["cam_out"]["product"]["id"]
        vistos.add(cam)
        etag = r.headers["etag"]
        etags.add(etag)
        if dia % 9 == 1:
            # el dia de la rotacion es el de Costa Rica: a las 21:00 de alla (03:00 UTC del dia siguiente) sigue siendo
            # el mismo kit y la misma portada (304)...
            _reloj(monkeypatch, mediodia + timedelta(hours=12))
            assert client.get(PORTADA, headers={"If-None-Match": etag}).status_code == 304
            assert _roles(client.post(KIT, json={**CASA, "kit": "casa-4"}).json())["cam_out"]["product"]["id"] == cam
            # ...y la portada de ayer ya no vale hoy (el dia va en la etiqueta: no se sirve el kit de ayer)
            if ayer:
                assert client.get(PORTADA, headers={"If-None-Match": ayer}).status_code == 200
        ayer = etag
    assert len(vistos) > 1 and vistos <= {c["CAM-ALFA"].id, c["CAM-BETA"].id, c["CAM-GAMA"].id}  # varia de verdad por los endpoints
    assert len(etags) == 28


def test_el_enlace_con_todas_las_piezas_reproduce_el_kit_otro_dia(client, auth, db_session, monkeypatch):
    """«Mi kit» (WhatsApp, asesoria) lleva TODAS las piezas del kit en pantalla, no solo las que el cliente cambio: las
    automaticas rotan cada dia, y con el enlace el vendedor ve mañana los mismos productos y el mismo total."""
    _publicar(client)
    c = _catalogo(db_session)
    _reloj(monkeypatch, datetime(2026, 11, 10, 15, 0, tzinfo=UTC))
    hoy = client.post(KIT, json={**CASA, "kit": "casa-4"}).json()
    piezas = {ln["key"]: ln["product"]["id"] for ln in hoy["lines"] if ln["key"] in PIEZAS}
    assert set(piezas) == {"cam_out", "cam_in", "nvr", "disk", "switch"}
    assert all(ln["source"] == "auto" for ln in hoy["lines"]) and hoy["choices"] == {}
    sin_enlace = set()
    for mas in range(1, 15):
        _reloj(monkeypatch, datetime(2026, 11, 10, 15, 0, tzinfo=UTC) + timedelta(days=mas))
        auto = _roles(client.post(KIT, json={**CASA, "kit": "casa-4"}).json())["cam_out"]["product"]["id"]
        sin_enlace.add(auto)
        k = client.post(KIT, json={**CASA, "kit": "casa-4", "choices": piezas}).json()
        assert {ln["key"]: ln["product"]["id"] for ln in k["lines"]} == {ln["key"]: ln["product"]["id"] for ln in hoy["lines"]}, mas
        assert [ln["quantity"] for ln in k["lines"]] == [ln["quantity"] for ln in hoy["lines"]]
        assert _d(k["total"]) == _d(hoy["total"]) and k["choices"] == piezas
        # la pieza viene como eleccion; solo cuenta como "cambio del cliente" si no es la sugerencia de ese dia
        cam = _roles(k)["cam_out"]
        assert cam["source"] == "choice" and cam["suggested"] is (cam["product"]["id"] == auto)
    assert len(sin_enlace) > 1  # sin las piezas en el enlace, la camara cambiaba de un dia a otro

    # Una pieza que salio de "lo que se puede comprar" tambien viaja y se respeta: aqui los discos de videovigilancia
    # no se pueden comprar y la sugerencia rota entre dos discos comunes, que entre TODO el catalogo no serian candidatos.
    a = _mas(db_session, c, "HDD-2B", "Disco duro 2TB Blue", 600)
    b = _mas(db_session, c, "HDD-2R", "Disco duro 2TB Red", 620)
    prods = list(c.values())
    comprable = {p.id for p in prods if "Purple" not in p.name}
    d0 = date(2026, 11, 10)
    k0 = sv.build_kit(prods, CASA, comprable, day=d0)
    todas = {ln["key"]: ln["product"].id for ln in k0["lines"]}
    assert todas["disk"] in (a.id, b.id)
    rotados = set()
    for mas in range(1, 21):
        dia = date.fromordinal(d0.toordinal() + mas)
        rotados.add(_linea(sv.build_kit(prods, CASA, comprable, day=dia), "disk")["product"].id)
        k = sv.build_kit(prods, CASA, comprable, day=dia, choices=todas)
        assert {ln["key"]: ln["product"].id for ln in k["lines"]} == todas, mas
    assert rotados == {a.id, b.id}


def test_una_sola_pasada_por_el_catalogo_por_pieza(client, auth, db_session, monkeypatch):
    """Armar un kit recorre el catalogo UNA vez por pieza: de esa pasada salen los candidatos de "lo que se puede
    comprar" y los de todo el catalogo (antes eran dos pasadas y la portada y /kit costaban mas del doble)."""
    _publicar(client)
    c = _catalogo(db_session)
    prods = list(c.values())
    real, pasadas = sv._role_scan, []

    def espia(key, pool, ctx):
        pasadas.append((key, len(pool)))
        return real(key, pool, ctx)

    monkeypatch.setattr(sv, "_role_scan", espia)
    comprable = {p.id for p in prods if "Purple" not in p.name}
    k = sv.build_kit(prods, CASA, comprable, pins={"disk": c["HDD-4"].id}, choices={"cam_out": c["CAM-CARA"].id}, alternatives=True, options=8)
    assert _linea(k, "cam_out")["source"] == "choice" and _linea(k, "disk")["source"] == "pin" and _linea(k, "nvr")["alternatives"]
    veces = Counter(key for key, _ in pasadas)
    assert set(veces) >= {"cam_out", "cam_in", "nvr", "disk", "switch"} and set(veces.values()) == {1}, veces
    assert {n for _, n in pasadas} == {len(prods)}  # siempre sobre el catalogo completo: lo comprable se saca de ahi
    # por los endpoints: /kit una pasada por pieza; la portada, una por pieza y por kit (3 kits activos)
    pasadas.clear()
    assert client.post(KIT, json={**CASA, "choices": {"nvr": c["NVR-8/8P"].id}}).status_code == 200
    assert set(Counter(key for key, _ in pasadas).values()) == {1}
    pasadas.clear()
    assert len(client.get(PORTADA).json()["kits"]) == 3
    assert max(Counter(key for key, _ in pasadas).values()) == 3


# ---------------------------------------------------------------- guardar la vitrina
def test_guardar_la_vitrina_no_espera_a_un_broker_que_no_existe(client, auth, monkeypatch):
    """La API no tiene la cola del worker: guardar la vitrina no intenta encolar (antes cada guardado esperaba ~4 s el
    intento, con la fila de la empresa modificada y sin confirmar). El snapshot se rearma en la siguiente visita."""
    from celery import Celery, _state

    from app.services import store_snapshot as snap

    intentos = []
    monkeypatch.setattr(Celery, "send_task", lambda self, *a, **kw: intentos.append((a, kw)))
    # como el proceso de la API: la app de Celery por defecto, sin broker (otra prueba del mismo proceso pudo haber
    # importado la app del worker, que si lo tiene)
    monkeypatch.setattr(_state, "default_app", Celery("api-sin-cola", set_as_current=False))
    monkeypatch.setattr(_state._tls, "current_app", None, raising=False)
    r = client.put("/store/showcase", json={"headline": "Sin cola"})
    assert r.status_code == 200 and r.json()["headline"] == "Sin cola"
    snap.enqueue_rebuild(1)
    assert intentos == []
    # como el worker, que si tiene la cola: encola una vez, con el debounce, y sin reintentos que retengan al que llama
    monkeypatch.setattr(_state, "default_app", Celery("worker-de-prueba", broker="memory://", set_as_current=False))
    snap.enqueue_rebuild(7, countdown=5)
    assert intentos == [(("store.rebuild_snapshot",), {"args": [7], "countdown": 5, "retry": False})]


def test_dos_guardados_seguidos_de_la_vitrina_no_se_pierden(tmp_path, monkeypatch):
    """Guardar la vitrina sube la version y la siguiente visita rearma el snapshot (medio segundo o mas con el catalogo
    real). Ese rearmado escribia TODOS los ajustes de la empresa desde la copia que leyo al empezar: un segundo guardado
    confirmado mientras tanto desaparecia sin aviso. Como en produccion: una sesion de base de datos por peticion."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.orm import sessionmaker

    from app.core import ratelimit as rl
    from app.core.db import Base, get_db
    from app.main import create_app
    from app.seeds import seed
    from app.services import store_snapshot as snap
    from tests.conftest import ADMIN

    engine = create_engine(f"sqlite:///{tmp_path / 'vitrina.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        seed(s, *ADMIN)
    for lim in vars(rl).values():
        if isinstance(lim, rl.SlidingWindow):
            lim.reset()
    otra_pestana: list[str] = []  # descripciones que "otra pestaña" guarda justo antes de que un PUT relea la empresa

    class SesionDePeticion(OrmSession):
        def get(self, entity, ident, **kw):
            # el instante entre que la peticion cargo la empresa (permisos) y la relee para guardar
            if entity is Tenant and kw.get("with_for_update") and otra_pestana:
                with Session() as otra:
                    t = otra.get(Tenant, ident)
                    st = dict(t.settings)
                    st["store"] = {**st["store"], "showcase": {**st["store"]["showcase"], "seo_description": otra_pestana.pop()}}
                    t.settings = st
                    otra.commit()
            return super().get(entity, ident, **kw)

    SesionPeticion = sessionmaker(bind=engine, expire_on_commit=False, class_=SesionDePeticion)

    def una_sesion_por_peticion():
        s = SesionPeticion()
        try:
            yield s
        finally:
            s.close()

    app = create_app()
    app.dependency_overrides[get_db] = una_sesion_por_peticion
    client = TestClient(app)
    try:
        r = client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 200, r.text
        client.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
        _publicar(client)
        assert client.put("/store/showcase", json={"headline": "Guardado UNO"}).status_code == 200

        armar, segundo = snap.build, {}

        def armar_lento(db, t, ctx):
            gz = armar(db, t, ctx)
            if not segundo:  # mientras el snapshot se arma, Andres guarda otra vez (otra peticion, otra sesion)
                r2 = client.put("/store/showcase", json={"headline": "Guardado DOS", "subline": "Bajada DOS"})
                segundo.update(status=r2.status_code, headline=r2.json().get("headline"))
            return gz

        monkeypatch.setattr(snap, "build", armar_lento)
        r = client.get("/public/store/crimson/snapshot")  # visita sin cache: rearma dentro de la peticion
        assert r.status_code == 200
        assert segundo == {"status": 200, "headline": "Guardado DOS"}

        guardado = client.get("/store/showcase").json()
        assert (guardado["headline"], guardado["subline"]) == ("Guardado DOS", "Bajada DOS")  # el segundo guardado sigue ahi
        assert client.get(PORTADA).json()["headline"] == "Guardado DOS"
        with Session() as s:
            st = s.scalar(select(Tenant).where(Tenant.slug == "crimson")).settings
        # la marca del snapshot es la de la version con la que se armo (la del primer guardado): sigue "viejo", y la
        # siguiente visita lo rearma con lo nuevo
        assert st["store"]["showcase"]["headline"] == "Guardado DOS"
        assert st["snapshot_cv"] and st["catalog_v"] > st["snapshot_cv"]
        # y un tercer guardado, ya sin rearmado en medio, tampoco pisa nada
        assert client.put("/store/showcase", json={"subline": "Bajada TRES"}).status_code == 200
        guardado = client.get("/store/showcase").json()
        assert (guardado["headline"], guardado["subline"]) == ("Guardado DOS", "Bajada TRES")
        # El propio guardado tampoco escribe desde una copia vieja: relee la empresa (con candado) antes de armar los
        # ajustes. Lo que otra pestaña confirmo entre que esta peticion cargo la empresa y guardo, sigue ahi.
        otra_pestana.append("Guardado por otra pestaña")
        assert client.put("/store/showcase", json={"subline": "Bajada CUATRO"}).status_code == 200
        assert otra_pestana == []  # el guardado paso por la relectura con candado
        guardado = client.get("/store/showcase").json()
        assert (guardado["subline"], guardado["seo_description"]) == ("Bajada CUATRO", "Guardado por otra pestaña")
    finally:
        client.close()
        engine.dispose()
