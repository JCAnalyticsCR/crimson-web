"""O2-01a: datos de arranque de la vitrina dentro del HTML (/public/seo/boot, nginx lo incrusta por SSI). La tienda pinta
cabecera y portada sin esperar /store-domain ni /store. El JSON va dentro de un <script>: ningun texto puede cerrarlo."""

import json
import re

import pytest
from sqlalchemy import select

from app.models import Tenant
from tests.test_store_vitrina import _catalogo, _publicar

H = {"X-Store-Host": "tienda.crimsoncr.com"}


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _boot(html: str) -> dict:
    m = re.fullmatch(r'<script type="application/json" id="sh-boot">(.*)</script>', html, re.S)
    assert m, html[:200]
    return json.loads(m.group(1))


def test_arranque_de_la_portada_trae_slug_tienda_y_portada_sin_textos_legales(client, auth, db_session):
    _publicar(client, legal={"terms": "Clausula ficticia de prueba. " * 900, "privacy": ""})
    _catalogo(db_session)
    r = client.get("/public/seo/boot/", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "max-age=60" in r.headers["cache-control"]
    b = _boot(r.text)
    assert b["slug"] == "crimson" and b["path"] == "/"
    # los mismos datos base de /public/store/<slug>, sin los textos legales (solo si existen)
    home = client.get("/public/store/crimson").json()
    assert b["store"]["categories"] == home["categories"] and b["store"]["store"]["name"] == home["store"]["name"]
    assert b["store"]["store"]["legal"] == {"privacy": "", "terms": ""}
    assert b["store"]["store"]["legal_on"] == {"privacy": False, "terms": True}
    assert "Clausula ficticia" not in r.text and len(r.content) < 20_000
    # la portada: el mismo titular y los mismos destacados que /showcase
    sc = client.get("/public/store/crimson/showcase").json()
    assert b["showcase"]["headline"] == sc["headline"]
    assert [p["id"] for p in b["showcase"]["featured"]] == [p["id"] for p in sc["featured"]]
    # otra ruta: datos base sin portada
    b2 = _boot(client.get("/public/seo/boot/c/videovigilancia", headers=H).text)
    assert b2["path"] == "/c/videovigilancia" and "showcase" not in b2 and b2["slug"] == "crimson"


def test_arranque_escapa_un_cierre_de_script_y_los_separadores_de_linea(client, auth, db_session):
    nombre = "Tienda </script><script>alert(1)</script>   & <!-- fin"
    _publicar(client, name=nombre, tagline="Línea nueva </SCRIPT>")
    _catalogo(db_session)
    html = client.get("/public/seo/boot", headers=H).text
    cuerpo = html.removeprefix('<script type="application/json" id="sh-boot">').removesuffix("</script>")
    # nada dentro del JSON puede cerrar el <script>, abrir un comentario HTML ni cortar la linea en JavaScript
    assert "<" not in cuerpo and ">" not in cuerpo and "&" not in cuerpo
    assert " " not in cuerpo and " " not in cuerpo
    assert html.lower().count("</script>") == 1
    b = _boot(html)
    assert b["store"]["store"]["name"] == nombre  # y el dato llega intacto al navegador
    assert b["store"]["store"]["tagline"] == "Línea nueva </SCRIPT>"


def test_arranque_vacio_fuera_de_la_tienda_o_despublicada(client, auth, db_session):
    _publicar(client)
    assert client.get("/public/seo/boot/", headers={"X-Store-Host": "panel.crimsoncr.com"}).text == ""
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    t.settings = {**t.settings, "store": {**t.settings["store"], "published": False}}
    db_session.commit()
    r = client.get("/public/seo/boot/", headers=H)
    assert r.text == "" and r.headers["cache-control"] == "no-store"


def test_arranque_que_falla_no_tumba_el_html(client, auth, db_session, monkeypatch):
    _publicar(client)
    from app.routers import store_seo

    def roto(*a, **k):
        raise RuntimeError("caido")

    monkeypatch.setattr(store_seo, "boot_data", roto)
    r = client.get("/public/seo/boot/", headers=H)
    assert r.status_code == 200 and r.text == ""
