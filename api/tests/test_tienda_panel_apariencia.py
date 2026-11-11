"""O4-06: la Apariencia que si se aplica. El tema (tema, acento, radio y fondo) se guarda en settings.store.theme, se
valida y sale en la respuesta publica de la tienda para que la vitrina lo aplique como variables de .shop
(portal/src/modules/store/theme.ts). Sin tema, la respuesta publica dice None y la tienda queda como hoy. El tema
entra al historial y se puede restaurar. Datos ficticios."""

S = "/public/store/crimson"


def test_el_tema_se_guarda_y_sale_en_la_tienda(client, auth):
    assert client.put("/store", json={"published": True, "kind": "tienda"}).status_code == 200
    assert client.get(S).json()["store"]["theme"] is None  # sin elegir: como hoy
    tema = {"preset": "clasico", "accent": "#1d4ed8", "radius": 10, "background": "blanco"}
    r = client.put("/store", json={"theme": tema, "primary": "#1d4ed8"})
    assert r.status_code == 200, r.text
    assert r.json()["theme"] == tema
    assert client.get(S).json()["store"]["theme"] == tema


def test_tema_invalido_se_rechaza(client, auth):
    for malo in ({"accent": "azul"}, {"radius": 40}, {"background": "negro"}, {"preset": "otro"}, {"accent": "#000000", "fuente": "x"}):
        assert client.put("/store", json={"theme": malo}).status_code == 422, malo


def test_el_tema_se_restaura_desde_el_historial(client, auth):
    assert client.put("/store", json={"theme": {"accent": "#e2233a"}}).status_code == 200
    assert client.put("/store", json={"theme": {"accent": "#1d4ed8"}}).status_code == 200
    fila = next(x for x in client.get("/store/history").json() if "Apariencia" in x["restaurables"])
    assert client.post(f"/store/history/{fila['id']}/restore").status_code == 200
    assert client.get("/store").json()["theme"]["accent"] == "#e2233a"
