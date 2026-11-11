"""O2-01b: contenido real de la vitrina en el HTML (/public/seo/body, nginx lo incrusta por SSI). Apagado por defecto y
por tipo de ruta (settings.store.ssr_body). Mismo dato que la vitrina: el precio y la disponibilidad del HTML son los de
React (prueba de paridad); nada del catalogo puede abrir etiquetas; si falla, vacio y la pagina sigue como hoy."""

import re
from html import unescape

import pytest
from sqlalchemy import select

from app.models import Product, Tenant
from app.services.store_ssr import money
from tests.test_store_vitrina import _catalogo, _publicar
from tests.test_tienda_paginacion import _muchos

H = {"X-Store-Host": "tienda.crimsoncr.com"}
TODO = {"ficha": True, "categoria": True, "portada": True}


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _body(client, ruta: str) -> str:
    r = client.get(f"/public/seo/body{ruta}", headers=H)
    assert r.status_code == 200
    return r.text


def _items(html: str) -> dict[int, tuple[str, str]]:
    """{id: (precio, disponibilidad)} de cada producto listado en el cuerpo."""
    out = {}
    for m in re.finditer(r'<li data-sh-id="(\d+)">(.*?)</li>', html, re.S):
        precio = re.search(r'class="sh-ssr__precio">(.*?)</span>', m.group(2)).group(1)
        disp = re.search(r'class="sh-ssr__disp">(.*?)</span>', m.group(2))
        out[int(m.group(1))] = (unescape(precio), unescape(disp.group(1)) if disp else "")
    return out


def test_precio_como_lo_escribe_la_vitrina():
    # los mismos casos que Intl.NumberFormat("es-CR", narrowSymbol) en el navegador
    nb = " "
    assert money(999) == "₡999"
    assert money(1234) == f"₡1{nb}234"
    assert money(1234.5) == f"₡1{nb}235"
    assert money(2.5) == "₡3"
    assert money(1234567.891) == f"₡1{nb}234{nb}568"
    assert money(1234.5, "USD") == f"$1{nb}234,50"
    assert money(999, "USD") == "$999,00"


def test_apagado_por_defecto_y_por_tipo_de_ruta(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    cam = prods["DS-2CD1043G2-LIU"]
    rutas = {"portada": "/", "categoria": "/c/videovigilancia", "ficha": f"/p/x-{cam.id}"}
    for ruta in rutas.values():
        assert _body(client, ruta) == ""
    # apagado: nginx guarda el vacio 5 minutos (el HTML espera esta subpeticion)
    assert client.get("/public/seo/body/", headers=H).headers["cache-control"] == "public, max-age=300"
    assert client.get("/store").json()["ssr_body"] == {"ficha": False, "categoria": False, "portada": False}
    # se enciende uno a la vez
    assert client.put("/store", json={"ssr_body": {"ficha": True}}).status_code == 200
    assert "<h1>" in _body(client, rutas["ficha"])
    assert _body(client, rutas["categoria"]) == "" and _body(client, rutas["portada"]) == ""
    # nada en rutas sin cuerpo ni en otro dominio
    client.put("/store", json={"ssr_body": TODO})
    assert _body(client, "/buscar") == "" and _body(client, "/asesoria") == ""
    assert client.get("/public/seo/body/", headers={"X-Store-Host": "panel.crimsoncr.com"}).text == ""
    # una llave que no existe no se guarda
    assert client.put("/store", json={"ssr_body": {"todo": True}}).status_code == 422


def test_cuerpo_de_ficha_categoria_y_portada_con_paridad(client, auth, db_session):
    _publicar(client, ssr_body=TODO)
    prods = _catalogo(db_session)
    cam = prods["DS-2CD1043G2-LIU"]

    # ficha: H1, migas, precio con IVA, disponibilidad real, descripcion, categoria, relacionados, WhatsApp y telefono
    it = client.get(f"/public/store/crimson/item/x-{cam.id}").json()
    html = _body(client, f"/p/x-{cam.id}")
    assert f"<h1>{it['name']}</h1>" in html
    assert '<nav aria-label="Migas de pan">' in html and '<a href="/">Tienda</a>' in html
    precio = unescape(re.search(r'<strong class="sh-ssr__precio">(.*?)</strong>', html).group(1))
    assert precio == money(it["display_price"], it["display_currency"])  # paridad con lo que pinta la ficha
    assert "IVA incluido" in html
    assert unescape(re.search(r'class="sh-ssr__disp">(.*?)</span>', html).group(1)) == it["availability"]["label"]
    assert "Visión nocturna IR hasta 30 m" in unescape(html)
    assert f'<a href="{it["category_path"]}">Ver más en' in html
    assert 'href="https://wa.me/50688889892?text=' in html and 'href="tel:+50688889892"' in html
    assert "<script" not in html.lower() and "onclick" not in html.lower()

    # categoria: H1 y la lista con el mismo precio y la misma disponibilidad del snapshot (lo que usa la vitrina)
    snap = client.get("/public/store/crimson/snapshot").json()
    por_id = {p["id"]: p for p in snap["products"]}
    html = _body(client, "/c/videovigilancia")
    assert "<h1>Videovigilancia</h1>" in html
    items = _items(html)
    assert cam.id in items and len(items) >= 3
    for pid, (precio, disp) in items.items():
        p = por_id[pid]
        assert precio == (money(p["display_price"], snap["currency"]) if p["display_price"] > 0 else "Precio a consultar")
        assert disp == (p["availability"] or {}).get("label", "")
    # catalogo completo
    assert "<h1>Catálogo completo</h1>" in _body(client, "/catalogo")

    # portada: H1 (el titular de la vitrina), categorias y destacados con el mismo precio
    sc = client.get("/public/store/crimson/showcase").json()
    html = _body(client, "/")
    assert re.search(r"<h1>[^<]+</h1>", html)
    assert '<a href="/c/videovigilancia">Videovigilancia</a>' in html
    for f in sc["featured"]:
        assert _items(html)[f["id"]][0] == (money(f["display_price"], f["display_currency"]) if float(f["display_price"]) > 0 else "Precio a consultar")


def test_paginas_de_una_categoria_en_el_cuerpo(client, auth, db_session):
    _publicar(client, ssr_body=TODO)
    _muchos(db_session, 30)
    p1 = _body(client, "/c/seguridad")
    p2 = _body(client, "/c/seguridad?pagina=2")
    assert len(_items(p1)) == 24 and len(_items(p2)) == 6
    assert not set(_items(p1)) & set(_items(p2))
    assert '<a rel="next" href="/c/seguridad?pagina=2">' in p1 and 'rel="prev"' not in p1
    assert '<a rel="prev" href="/c/seguridad">' in p2 and 'rel="next"' not in p2
    assert "Página 2 de 2" in p2


def test_nada_del_catalogo_abre_etiquetas(client, auth, db_session):
    _publicar(client, ssr_body=TODO, name='Tienda <img src=x onerror="alert(1)">')
    prods = _catalogo(db_session)
    p = prods["WD44PURZ"]
    p.name = "Disco </li><script>alert(1)</script>"
    p.description_store = "<b>negrita</b> & más"
    db_session.commit()
    html = _body(client, f"/p/x-{p.id}")
    assert "<script" not in html.lower() and "<img" not in html.lower() and "<b>negrita" not in html
    assert "&lt;script&gt;" in html and "&lt;b&gt;negrita&lt;/b&gt; &amp; más" in html


def test_cuerpo_que_falla_no_tumba_el_html(client, auth, db_session, monkeypatch):
    _publicar(client, ssr_body=TODO)
    from app.routers import store_seo

    def roto(*a, **k):
        raise RuntimeError("caido")

    monkeypatch.setattr(store_seo, "body_html", roto)
    r = client.get("/public/seo/body/", headers=H)
    assert r.status_code == 200 and r.text == "" and r.headers["cache-control"] == "no-store"


def test_ficha_oculta_o_inexistente_sin_cuerpo(client, auth, db_session):
    _publicar(client, ssr_body=TODO)
    prods = _catalogo(db_session)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    oculto = db_session.scalar(select(Product).where(Product.tenant_id == t.id, Product.code == "OCULTO"))
    assert _body(client, f"/p/x-{oculto.id}") == ""
    assert _body(client, "/p/x-999999") == ""
    assert _body(client, "/c/no-existe") == ""
    assert prods
