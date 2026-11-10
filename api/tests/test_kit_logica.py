"""«Arma tu sistema» con mas logica (pedido 10/11): calidad de imagen, color de noche, audio y deteccion de personas,
grabacion por movimiento, distancia, espacio para crecer, UPS, discos que caben en el grabador y grabador de la misma
marca que las camaras. Cada decision se explica al cliente en `why`.

Datos ficticios, precios en colones <= 2 000."""

from decimal import Decimal

import pytest

from app.services import store_showcase as sv
from tests.test_kits_editables import CASA, KIT, _catalogo, _d, _mas, _roles
from tests.test_store_vitrina import _publicar


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _kit(client, **kw) -> dict:
    r = client.post(KIT, json={**CASA, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _textos(k: dict, tone: str | None = None) -> list[str]:
    return [w["text"] for w in k["why"] if tone is None or w["tone"] == tone]


def test_un_enlace_sin_los_detalles_arma_el_kit_de_siempre(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    k = _kit(client)
    assert k["answers"] == {
        **CASA,
        "quality": "4mp",
        "night": "ir",
        "audio": False,
        "smart": False,
        "record": "continuo",
        "distance": "media",
        "growth": False,
        "ups": False,
    }
    assert _d(k["storage_tb"]) == Decimal("1.4")  # 4 x 15 x 21 GB x 1.1
    assert "ups" not in _roles(k)
    assert k["summary"]["ups"] is None
    # respuestas fuera de lo permitido: 422, nunca otro kit
    assert client.post(KIT, json={**CASA, "quality": "12mp"}).status_code == 422
    assert client.post(KIT, json={**CASA, "night": "verde"}).status_code == 422


def test_color_de_noche_audio_y_deteccion_eligen_camaras_que_lo_traen(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    _mas(db_session, c, "CAM-COLOR", "Cámara IP Bullet 4MP PoE ColorVu Zeta 2.8mm", 1400)
    _mas(db_session, c, "CAM-ACU", "Cámara IP Bullet 4MP PoE AcuSense audio Eta 2.8mm", 1600)
    base = _roles(_kit(client))["cam_out"]["product"]["code"]
    assert base in ("CAM-ALFA", "CAM-BETA", "CAM-GAMA")  # sin pedir nada: la mas barata que calza

    k = _kit(client, night="color")
    assert _roles(k)["cam_out"]["product"]["code"] == "CAM-COLOR"
    assert any("afuera con imagen a color de noche" in t for t in _textos(k, "ok"))
    # adentro no hay domos a color: se dice, y va el mas parecido
    assert any("adentro con imagen a color de noche" in t for t in _textos(k, "warn"))
    assert _roles(k)["cam_in"]["product"]["code"] in ("DOMO-UNO", "DOMO-DOS")

    k = _kit(client, audio=True, smart=True)
    assert _roles(k)["cam_out"]["product"]["code"] == "CAM-ACU"
    assert "Detecta personas y vehículos" in _roles(k)["cam_out"]["specs"]
    # las alternativas siguen ahi (el cliente puede volver a una mas barata) y el total cuadra al elegirla
    alt = next(a for a in _roles(k)["cam_out"]["alternatives"] if a["product"]["code"] == "CAM-COLOR")
    k2 = _kit(client, audio=True, smart=True, choices={"cam_out": c["CAM-COLOR"].id})
    assert _d(k2["total"]) == _d(k["total"]) + _d(alt["diff"])


def test_la_calidad_cambia_camara_y_disco(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    k8 = _kit(client, quality="8mp")
    assert _roles(k8)["cam_out"]["product"]["code"] == "CAM-8MP"
    assert _d(k8["storage_tb"]) == Decimal("2.8")  # 4 x 15 x 42 GB x 1.1 = 2.77
    assert k8["summary"]["quality"] == "8 MP (4K)"
    # adentro no hay domos de 8 MP: se avisa con la resolucion que va
    assert any("adentro de 8 MP (4K) no están disponibles" in t for t in _textos(k8, "warn"))
    k2 = _kit(client, quality="2mp")
    assert _roles(k2)["cam_in"]["product"]["code"] == "DOMO-DOS"
    # afuera no hay bullets de 2 MP: van de 4 MP y el disco se calcula para 4 MP (no se queda corto)
    assert _roles(k2)["cam_out"]["product"]["code"] in ("CAM-ALFA", "CAM-BETA", "CAM-GAMA")
    assert _d(k2["storage_tb"]) == Decimal("1.4")
    k2i = _kit(client, quality="2mp", outdoor=0, indoor=4)
    assert _d(k2i["storage_tb"]) == Decimal("0.7")  # solo domos de 2 MP: 4 x 15 x 11 GB x 1.1 = 0.73


def test_grabar_solo_con_movimiento_pide_menos_disco(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    cont = _kit(client, days=60)
    mov = _kit(client, days=60, record="movimiento")
    assert _d(cont["storage_tb"]) == Decimal("5.5")  # 4 x 60 x 21 x 1.1
    assert _d(mov["storage_tb"]) == Decimal("2.2")  # el 40 %
    assert _roles(mov)["disk"]["quantity"] * 4 >= 2.2
    assert any("solo con movimiento" in n for n in mov["notes"])


def test_espacio_para_crecer_sube_el_grabador_y_lo_dice(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    k = _kit(client)
    assert _roles(k)["nvr"]["product"]["code"] == "NVR-4"
    assert k["summary"]["free_channels"] == 0
    k = _kit(client, growth=True)  # 4 camaras + la mitad (min 2) = 6 -> 8 canales
    assert _roles(k)["nvr"]["product"]["code"] == "NVR-8/8P"
    assert k["summary"]["channels"] == 8 and k["summary"]["free_channels"] == 4
    assert any("espacio para 4 más" in t for t in _textos(k, "ok"))
    assert sv.channel_size(4) == 8 and sv.channel_size(8) == 16 and sv.channel_size(16) == 32 and sv.channel_size(1) == 4


def test_los_discos_caben_en_el_grabador(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    # el grabador de 4 canales admite un solo disco: 60 dias (5.5 TB) no caben en 1 x 4 TB
    c["NVR-4"].name = "NVR 4 canales Hikvision 1 SATA hasta 8 TB"
    _mas(db_session, c, "NVR-4-2", "NVR 4 canales Hikvision 2 SATA hasta 16 TB", 1500)
    db_session.commit()
    k = _kit(client, days=60)
    assert _roles(k)["nvr"]["product"]["code"] == "NVR-4-2"  # al de 1 bahia no le cabe: el de 2
    disk = _roles(k)["disk"]
    assert disk["quantity"] <= 2
    assert "2 discos" in _roles(k)["nvr"]["specs"]
    # el cliente elige igual el de 1 bahia: el kit lo advierte
    k1 = _kit(client, days=60, choices={"nvr": c["NVR-4"].id})
    assert any("al grabador le caben 1" in t or "admite hasta 8 TB" in t for t in _textos(k1, "warn"))
    assert sv.nvr_bays(c["NVR-4"]) == 1 and sv.nvr_max_tb(c["NVR-4"]) == 8


def test_grabador_de_la_misma_marca_que_las_camaras(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    for code in ("CAM-ALFA", "CAM-BETA", "CAM-GAMA", "DOMO-UNO", "DOMO-DOS"):
        c[code].name = c[code].name.replace("Cámara IP", "Cámara IP TP-Link VIGI")
    _mas(db_session, c, "VIGI-NVR8", "Grabadora NVR TP-Link VIGI 8 canales", 1700)
    db_session.commit()
    k = _kit(client)
    # NVR-4 (Hikvision) es mas barato y de menos canales, pero las camaras son VIGI: va el VIGI
    assert _roles(k)["nvr"]["product"]["code"] == "VIGI-NVR8"
    assert any("misma marca que las cámaras (TP-Link)" in t for t in _textos(k, "ok"))
    k = _kit(client, choices={"nvr": c["NVR-4"].id})
    assert any("otra marca que las cámaras" in t for t in _textos(k, "warn"))


def test_ups_dimensionada_por_el_consumo(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    _mas(db_session, c, "BAT-7", "Batería UPS 12V 7AH", 300)
    _mas(db_session, c, "UPS-GRANDE", "UPS 1500VA 900W respaldo", 1950)
    k = _kit(client, ups=True)
    ups = _roles(k)["ups"]
    # 4 camaras x 7 W + grabador 15 W + switch 10 W = 53 W (+30 % = 69 W): alcanza la de 600 VA (~360 W); nunca la bateria
    assert ups["product"]["code"] == "UPS-600"
    assert k["summary"]["ups"]["load_w"] == 53
    assert "600 VA" in ups["specs"]
    assert any(t.startswith("UPS de 600 VA para ~53 W") for t in _textos(k, "ok"))
    assert "BAT-7" not in [a["product"]["code"] for a in ups.get("alternatives", [])]
    # sin UPS en el catalogo: queda por cotizar y se dice
    c["UPS-600"].show_on_web = False
    c["UPS-GRANDE"].show_on_web = False
    db_session.commit()
    k = _kit(client, ups=True)
    assert any(m["key"] == "ups" for m in k["missing"])
    assert any("No hay una UPS disponible" in t for t in _textos(k, "warn"))


def test_distancia_larga_pide_mas_cable_y_lo_explica(client, auth, db_session):
    _publicar(client)
    c = _catalogo(db_session)
    _mas(db_session, c, "UTP-305", "Bobina cable UTP Cat6 305 m", 1900)
    corta = _kit(client, distance="corta")
    larga = _kit(client, distance="larga")
    assert corta["summary"]["meters"] == 15 and larga["summary"]["meters"] == 70
    assert _roles(larga)["cable"]["quantity"] >= _roles(corta)["cable"]["quantity"]
    assert any("100 m que admite el PoE" in t for t in _textos(larga, "ok"))
    assert not any("PoE" in t for t in _textos(corta))


def test_el_enlace_con_los_detalles_reproduce_el_mismo_kit(client, auth, db_session):
    _publicar(client)
    _catalogo(db_session)
    pedido = {"quality": "8mp", "night": "color", "record": "movimiento", "growth": True, "ups": True, "distance": "larga"}
    a = _kit(client, **pedido)
    b = client.post(KIT, json=a["answers"]).json()  # lo que el portal pone en la direccion son las respuestas devueltas
    assert [ln["product"]["id"] for ln in a["lines"]] == [ln["product"]["id"] for ln in b["lines"]]
    assert a["total"] == b["total"]
