"""04/11: la vendedora crea una factura de punta a punta. Lo que el recorrido encontro y quedo corregido:
- en simulado una linea sin CABYS salia "aceptada" (con ceros): ahora falla igual que con Hacienda real, con el motivo;
- el mensaje del tope habla del precio rebajado, no solo del descuento;
- el cliente sin dueno que la vendedora escribe con su cedula exacta se toma (claim) y se usa en la factura.
Y lo que ya estaba bien y no debe romperse: no ve costos, el CABYS sale en el PDF, no anula, no edita una emitida.
Precios ficticios de 2 000 colones o menos."""

from decimal import Decimal

from app.models import Customer, Product

from .test_rol_vendedor import COSTO, _entrar


def _keys(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, acc)
    return acc


def _sin_costos(j) -> bool:
    return not [k for k in _keys(j) if any(c in k.lower() for c in COSTO) and k not in ("unit_price", "price")]


def test_factura_de_la_vendedora_de_punta_a_punta(client, auth, db_session):
    tid = auth["tenant"]["id"]
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    pr = Product(tenant_id=tid, code="CAM-9", name="Cámara IP 4 MP", price=Decimal("1800"), cost=Decimal("1.5"), cost_currency="USD", cabys_code="4526100000200", unit="Unid")
    libre_cliente = Customer(tenant_id=tid, name="Pacific Sun S.A.", id_type="juridica", id_number="3-101-777777")
    db_session.add_all([pr, libre_cliente])
    db_session.commit()
    v = _entrar(client, db_session, tid, "vende.recorrido@ejemplo.com", "ventas")

    # el cliente sin dueno no esta en su lista; con la cedula exacta lo toma (lo que hace el alta rapida del editor)
    assert libre_cliente.id not in {c["id"] for c in client.get("/customers", headers=v).json()["items"]}
    dup = client.post("/customers", json={"name": "Pacific Sun", "id_type": "juridica", "id_number": "3-101-777777"}, headers=v)
    assert dup.status_code == 409 and dup.json()["existing"]["id"] == libre_cliente.id
    toma = client.post(f"/customers/{libre_cliente.id}/claim", json={"query": "3-101-777777"}, headers=v)
    assert toma.status_code == 200 and toma.json()["account_owner_name"]
    cid = libre_cliente.id

    linea_p = {"product_id": pr.id, "name": pr.name, "quantity": 1, "unit_price": 1800}
    linea_l = {"name": "Visita técnica", "cabys_code": "8713200000000", "unit": "Sp", "quantity": 1, "unit_price": 1000}
    fe = client.post("/invoices", params={"doc_type": "FE"}, json={"customer_id": cid, "lines": [linea_p, linea_l]}, headers=v)
    assert fe.status_code == 201, fe.text
    fe = fe.json()
    assert _sin_costos(fe) and fe["seller_name"]
    html = client.get(f"/invoices/{fe['id']}/html", headers=v).text
    assert "CABYS 4526100000200" in html and "CABYS 8713200000000" in html
    emit = client.post(f"/invoices/{fe['id']}/emit", headers=v)
    assert emit.status_code == 200 and emit.json()["status"] == "aceptada"
    assert client.put(f"/invoices/{fe['id']}", json={"customer_id": cid, "lines": [linea_l]}, headers=v).status_code == 409
    assert client.post(f"/invoices/{fe['id']}/void", headers=v).status_code == 403
    assert client.post(f"/invoices/{fe['id']}/payments", json={"amount": str(fe["total"]), "method": "sinpe"}, headers=v).status_code == 201

    # linea libre sin CABYS: se guarda (el editor avisa), pero NO se emite ni en simulado, con el motivo
    sin = client.post("/invoices", params={"doc_type": "TE"}, json={"lines": [{"name": "Algo sin código", "quantity": 1, "unit_price": 1000}]}, headers=v)
    assert sin.status_code == 201
    r = client.post(f"/invoices/{sin.json()['id']}/emit", headers=v)
    assert r.status_code == 422 and "no tiene CABYS" in r.json()["detail"] and "Algo sin código" in r.json()["detail"]
    assert client.get(f"/invoices/{sin.json()['id']}", headers=v).json()["einvoice_status"] != "aceptada"

    # bajar el precio cuenta para el tope, y el mensaje lo dice
    r = client.post("/invoices", json={"customer_id": cid, "lines": [{**linea_p, "unit_price": 100}]}, headers=v)
    assert r.status_code == 422 and "por debajo del precio de lista" in r.json()["detail"]
