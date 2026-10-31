"""30/10 (Andres): al emitir salia "La linea 1 no tiene CABYS" aunque el producto (de Eurocomp) si lo tiene. El editor
manda el nombre y el precio de la linea, y antes el CABYS solo se copiaba del producto si la linea venia sin ellos."""

CABYS = "4528200000000"


def _producto(client) -> dict:
    r = client.post("/products", json={"name": "Cámara domo prueba CABYS", "code": "CAB-1", "price": 10000, "currency": "CRC", "cabys_code": CABYS})
    assert r.status_code == 201, r.text
    return r.json()


def test_la_linea_toma_el_cabys_del_producto_aunque_traiga_nombre_y_precio(client, auth):
    prod = _producto(client)
    linea = {"product_id": prod["id"], "name": "Cámara domo (nombre editado)", "unit_price": 12000, "quantity": 1}
    for path in ("/quotes", "/invoices?doc_type=TE"):
        r = client.post(path, json={"currency": "CRC", "lines": [linea]})
        assert r.status_code in (200, 201), r.text
        assert r.json()["lines"][0]["cabys_code"] == CABYS, path
    # una linea escrita a mano conserva el CABYS que le pongan
    r = client.post("/quotes", json={"currency": "CRC", "lines": [{"name": "Servicio", "unit_price": 1000, "quantity": 1, "cabys_code": "8121100000100"}]})
    assert r.json()["lines"][0]["cabys_code"] == "8121100000100"
