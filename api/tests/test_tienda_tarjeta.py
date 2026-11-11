"""O2-06: la tarjeta de producto trae sus datos clave (sv.key_specs) en el snapshot, que es lo que pinta el catalogo,
y en las tarjetas completas de /showcase. Es un campo aditivo («ks»): un navegador viejo lo ignora. Datos ficticios."""

from tests.test_tienda_portada import S, _existencia, _limites, _tienda  # noqa: F401
from tests.test_store_vitrina import _publicar


def test_el_snapshot_trae_el_dato_clave_de_cada_camara(client, auth, db_session):
    _publicar(client)
    prods = _tienda(db_session)
    snap = client.get(f"{S}/snapshot").json()
    por_codigo = {p["code"]: p for p in snap["products"]}
    assert por_codigo["CAM-A"]["ks"][0] == "4 MP"
    assert por_codigo["CAM-C"]["ks"][0] == "2 MP"
    assert all(isinstance(p.get("ks"), list) for p in snap["products"])
    assert prods  # la barra de sonido no trae datos de camara
    assert not any("MP" in x for x in por_codigo["BARRA"]["ks"])


def test_las_tarjetas_de_la_portada_traen_sus_datos_clave(client, auth, db_session):
    _publicar(client)
    prods = _tienda(db_session)
    _existencia(client, prods["CAM-B"].id)
    h = client.get(f"{S}/showcase").json()
    assert [p["code"] for p in h["featured"]] == ["CAM-B"]
    assert h["featured"][0]["ks"][0] == "4 MP"
