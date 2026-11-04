"""04/11: el CABYS de cada linea sale en la factura y el tiquete. Antes el editor no mandaba el CABYS: una linea escrita a
mano quedaba siempre sin el (Hacienda la rechaza al emitir) y la linea de envio de la tienda tampoco lo traia."""

from decimal import Decimal

from sqlalchemy import select

from app.models import InvoiceLine, Product
from tests.test_reunion_21_10_cotiz import _cust
from tests.test_tienda_checkout import SLUG, _body, _shop


def test_linea_libre_guarda_su_cabys_y_sale_en_el_pdf(client, auth):
    c = _cust(client)
    for doc_type in ("FE", "TE"):
        inv = client.post(
            "/invoices",
            params={"doc_type": doc_type},
            json={"customer_id": c["id"], "lines": [{"name": "Instalación", "cabys_code": "8713-2000-00000", "unit": "Sp", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]},
        )
        assert inv.status_code == 201, inv.text
        i = client.get(f"/invoices/{inv.json()['id']}").json()
        assert i["lines"][0]["cabys_code"] == "8713200000000"  # guiones fuera
        assert "CABYS 8713200000000" in client.get(f"/invoices/{i['id']}/html").text
    # 12 digitos: se rechaza con el motivo, no se guarda una linea que Hacienda va a rechazar despues
    r = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "X", "cabys_code": "871320000000", "quantity": 1, "unit_price": 1}]})
    assert r.status_code == 422 and "13 dígitos" in r.text


def test_linea_de_producto_toma_el_cabys_del_producto(client, auth, db_session):
    tid = auth["tenant"]["id"]
    pr = Product(tenant_id=tid, code="CAM-1", name="Cámara", price=Decimal("10000"), cabys_code="4526100000200")
    db_session.add(pr)
    db_session.commit()
    c = _cust(client)
    # el editor manda nombre y precio sin CABYS: igual sale el del producto
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"product_id": pr.id, "name": "Cámara", "quantity": 1, "unit_price": 10000}]}).json()
    assert "CABYS 4526100000200" in client.get(f"/invoices/{inv['id']}/html").text


def test_envio_de_la_tienda_lleva_cabys_de_servicio(client, auth, db_session):
    prods = _shop(client)
    client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"]))
    oid = client.get("/store-orders").json()["items"][0]["id"]
    client.post(f"/store-orders/{oid}/status", json={"status": "pagado"})
    r = client.post(f"/orders/{oid}/invoice", params={"doc_type": "TE", "register_payment": True})
    assert r.status_code == 201, r.text
    lines = db_session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == r.json()["invoice_id"])).all()
    envio = next(ln for ln in lines if ln.name.startswith("Envio - "))
    assert envio.cabys_code == "6803000000000" and envio.unit == "Os"  # servicio, no mercancia
    # Mi Tienda lo puede cambiar (lo confirma el contador)
    cfg = client.get("/store/checkout").json()["checkout"]
    assert cfg["shipping_cabys"] == "6803000000000"


def test_descuento_general_sale_una_sola_vez_abajo(client, auth):
    """04/11 (COT-000011): el descuento general se repartia en el Subtotal de cada linea (73 100 salia 60 452,94) y
    abajo se volvia a restar: parecia doble. La linea muestra cantidad x precio menos SU descuento; el general, solo
    en los totales. Los montos guardados (impuesto, XML) no cambian."""
    c = _cust(client)
    q = client.post(
        "/quotes",
        json={
            "customer_id": c["id"],
            "discount_type": "amount",
            "discount_value": 20000,
            "lines": [
                {"name": "Disco duro 1 TB", "quantity": 1, "unit_price": 73100, "tax_rate": 13},
                {"name": "Reemplazo de disco", "quantity": 1, "unit_price": 42500, "tax_rate": 13, "discount_type": "percent", "discount_value": 10},
            ],
        },
    ).json()
    html = client.get(f"/quotes/{q['id']}/html").text
    assert html.count("₡73,100.00") == 2  # precio y subtotal de la linea, sin el descuento general
    assert "₡38,250.00" in html  # 42 500 menos el 10 % de ESA linea
    assert "60,452" not in html and "−₡20,000.00" in html  # el general, una vez, en los totales
    assert Decimal(str(q["total"])) == Decimal(str(client.get(f"/quotes/{q['id']}").json()["total"]))  # el total no cambia
