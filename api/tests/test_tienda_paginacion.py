"""O2-01c: paginacion enlazable del catalogo y de las categorias. /c/<x>?pagina=2 tiene su propia canonica, su titulo
(«… · Página 2») y sus enlaces prev/next; cualquier otro parametro no cambia la canonica (nginx solo deja pasar
pagina) y una pagina que no existe no se indexa."""

import re

import pytest
from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.routers.store_seo import pagina_de
from tests.test_store_vitrina import _publicar

H = {"X-Store-Host": "tienda.crimsoncr.com"}
BASE = "https://tienda.crimsoncr.com"


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _muchos(db, n: int = 30) -> Category:
    """n camaras ficticias publicadas en «Seguridad» (mas de una pagina de 24)."""
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    cat = Category(tenant_id=t.id, name="Seguridad", show_on_web=True)
    db.add(cat)
    db.flush()
    for i in range(n):
        db.add(
            Product(tenant_id=t.id, code=f"PAG-{i:03d}", name=f"Cámara de prueba {i:03d}", price=10 + i, currency="USD", category_id=cat.id, show_on_web=True)
        )
    db.commit()
    return cat


def _snapshot(client):
    assert client.get("/public/store/crimson/snapshot").status_code == 200  # arma el snapshot (los conteos salen de ahi)


def test_pagina_de_acepta_solo_enteros_en_rango():
    assert [pagina_de(x) for x in (None, "", "1", "2", "007", "0", "-3", "2a", "1e3", "999")] == [1, 1, 1, 2, 7, 1, 1, 1, 1, 200]


def test_pagina_2_de_una_categoria_tiene_canonica_titulo_y_vecinas(client, auth, db_session):
    _publicar(client)
    _muchos(db_session)
    _snapshot(client)
    p1 = client.get("/public/seo/head/c/seguridad", headers=H).text
    assert f'<link data-sh rel="canonical" href="{BASE}/c/seguridad">' in p1
    assert f'<link data-sh rel="next" href="{BASE}/c/seguridad?pagina=2">' in p1 and 'rel="prev"' not in p1
    assert "Página" not in p1.split("</title>")[0]

    p2 = client.get("/public/seo/head/c/seguridad?pagina=2", headers=H).text
    assert re.search(r"<title>Seguridad · [^<]+ · Página 2</title>", p2)
    assert f'<link data-sh rel="canonical" href="{BASE}/c/seguridad?pagina=2">' in p2
    assert f'<link data-sh rel="prev" href="{BASE}/c/seguridad">' in p2
    assert 'rel="next"' not in p2  # 30 productos: 2 paginas de 24
    assert f'property="og:url" content="{BASE}/c/seguridad?pagina=2"' in p2
    assert 'content="index,follow"' in p2
    assert f'"url": "{BASE}/c/seguridad?pagina=2"' in p2.replace("\\u0026", "&")  # CollectionPage del JSON-LD

    # mas alla de la ultima pagina: no se indexa y la canonica es la primera
    p9 = client.get("/public/seo/head/c/seguridad?pagina=9", headers=H).text
    assert 'content="noindex,follow"' in p9 and f'rel="canonical" href="{BASE}/c/seguridad">' in p9
    # basura en pagina = primera pagina (nunca un error en el <head>)
    assert client.get("/public/seo/head/c/seguridad?pagina=abc", headers=H).text == p1


def test_otros_parametros_no_cambian_la_canonica(client, auth, db_session):
    """nginx ($seo_path) solo deja pasar pagina; si igual llegara otro parametro, el API lo ignora."""
    _publicar(client)
    _muchos(db_session)
    _snapshot(client)
    a = client.get("/public/seo/head/c/seguridad", headers=H).text
    b = client.get("/public/seo/head/c/seguridad?orden=price_asc&marca=X&utm_source=y", headers=H).text
    assert a == b


def test_el_catalogo_completo_tambien_pagina(client, auth, db_session):
    _publicar(client)
    _muchos(db_session, 50)
    _snapshot(client)
    p3 = client.get("/public/seo/head/catalogo?pagina=3", headers=H).text
    assert f'rel="canonical" href="{BASE}/catalogo?pagina=3">' in p3 and "Página 3</title>" in p3
    assert f'rel="prev" href="{BASE}/catalogo?pagina=2">' in p3 and 'rel="next"' not in p3
    p2 = client.get("/public/seo/head/catalogo?pagina=2", headers=H).text
    assert f'rel="next" href="{BASE}/catalogo?pagina=3">' in p2
    # la pagina no toca otras rutas
    assert 'rel="next"' not in client.get("/public/seo/head/asesoria?pagina=2", headers=H).text
