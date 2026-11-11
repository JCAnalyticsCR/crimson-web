"""O1-09: envio visible antes del checkout. shipping_public sale de la configuracion real (umbral, tarifas activas con
monto final con IVA, zona y plazo), el retiro solo con direccion y la factura solo si la tienda la emite de verdad."""

from decimal import Decimal

S = "/public/store/crimson"


def _checkout(client, **kw):
    cfg = {"methods": {"sinpe": {"active": True, "phone": "8888-7777", "holder": "Comercio de Prueba"}}, **kw}
    r = client.put("/store/checkout", json=cfg)
    assert r.status_code == 200, r.text


def test_envio_publico_sale_de_la_configuracion(client, auth):
    r = client.put(
        "/store",
        json={
            "kind": "tienda",
            "published": True,
            "shipping_rates": [
                {"id": "retiro", "name": "Retiro en tienda", "amount": 0, "active": True, "zone": "retiro"},
                {"id": "gam", "name": "Mensajería GAM", "amount": 1000, "active": True, "zone": "gam"},
                {"id": "correos", "name": "Correos de Costa Rica", "amount": 800, "active": True},
                {"id": "vieja", "name": "Tarifa apagada", "amount": 10, "active": False},
            ],
        },
    )
    assert r.status_code == 200, r.text
    _checkout(client, free_shipping_from=2000)
    sp = client.get(S).json()["shipping_public"]
    assert Decimal(str(sp["free_from"])) == Decimal("2000")
    # montos finales con IVA, de menor a mayor; sin la de retiro ni la apagada; zona en palabras del cliente
    assert [(x["name"], Decimal(str(x["amount"])), x["area"]) for x in sp["rates"]] == [
        ("Correos de Costa Rica", Decimal("904.00"), "a todo el país"),
        ("Mensajería GAM", Decimal("1130.00"), "en la GAM"),
    ]
    # retiro activo pero sin direccion: no se promete
    assert sp["pickup"] is None and sp["invoice"] is False
    # cambiar el umbral o la direccion en el panel cambia lo publico
    _checkout(client, free_shipping_from=3000, pickup={"address": "Oficina de prueba, San José", "hours": "L-V 9-17"})
    sp = client.get(S).json()["shipping_public"]
    assert Decimal(str(sp["free_from"])) == Decimal("3000") and sp["pickup"]["address"] == "Oficina de prueba, San José"
    # sin envio gratis configurado: null (la franja no aparece)
    _checkout(client, free_shipping_from=0)
    assert client.get(S).json()["shipping_public"]["free_from"] is None
