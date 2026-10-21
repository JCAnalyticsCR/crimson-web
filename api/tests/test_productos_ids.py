"""/products?ids=: las fotos de las lineas de una cotizacion en un solo viaje (solo productos de la empresa)."""


def test_productos_por_ids(client, auth):
    a = client.post("/products", json={"name": "Camara A", "code": "CAM-A", "price": 1000}).json()
    b = client.post("/products", json={"name": "Camara B", "code": "CAM-B", "price": 2000}).json()
    r = client.get(f"/products?ids={a['id']},{b['id']},999999,x&limit=100").json()
    assert {x["id"] for x in r["items"]} == {a["id"], b["id"]}
    assert client.get("/products?ids=,&limit=100").json()["items"] == []
