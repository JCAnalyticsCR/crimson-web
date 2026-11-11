"""O4-08: Mi Tienda -> SEO. El titulo y la imagen para compartir de la portada (antes fijos en codigo) se editan en el
panel y salen en el <head> que ve Google; vacios, los de siempre. Nada de medicion de terceros: un ga4_id viejo se
descarta. Datos ficticios."""

H = {"X-Store-Host": "tienda.ejemplo.com"}


def _tienda(client):
    r = client.put("/store", json={"kind": "tienda", "published": True, "domain": "tienda.ejemplo.com"})
    assert r.status_code == 200, r.text


def test_titulo_descripcion_e_imagen_de_la_portada(client, auth):
    _tienda(client)
    antes = client.get("/public/seo/head", headers=H).text
    assert "Tienda de seguridad electrónica y redes en Costa Rica" in antes  # el de siempre
    r = client.put(
        "/store/showcase",
        json={"seo_title": "Cámaras y redes de prueba", "seo_description": "Descripción de prueba para Google.", "share_image": "/media/compartir.png"},
    )
    assert r.status_code == 200, r.text
    sc = client.get("/store/showcase").json()
    assert sc["seo_title"] == "Cámaras y redes de prueba" and sc["share_image"] == "/media/compartir.png"
    head = client.get("/public/seo/head", headers=H).text
    assert "<title>Cámaras y redes de prueba</title>" in head
    assert "Descripción de prueba para Google." in head
    assert 'content="https://tienda.ejemplo.com/media/compartir.png"' in head
    # vacio vuelve al de siempre
    assert client.put("/store/showcase", json={"seo_title": ""}).status_code == 200
    assert "Tienda de seguridad electrónica y redes en Costa Rica" in client.get("/public/seo/head", headers=H).text


def test_sin_medicion_de_terceros(client, auth):
    _tienda(client)
    assert client.put("/store/showcase", json={"ga4_id": "G-PRUEBA123"}).status_code == 200
    assert "ga4" not in str(client.get("/store/showcase").json()).lower()
    assert "G-PRUEBA123" not in client.get("/public/seo/head", headers=H).text
    assert client.put("/store/showcase", json={"share_image": "javascript:alert(1)"}).status_code == 422
