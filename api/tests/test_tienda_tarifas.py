"""O1-11a: tarifas de envio con esquema (RateIn), id estable y checkout que casa por id con respaldo por nombre.

El fixture TARIFAS_GUARDADAS imita lo que la tienda tiene guardado hoy (la forma que deja Mi Tienda: nombre, monto,
activo, por kilo, recargo y zona) y la respuesta publica de checkout-config. Es FICTICIO: no se consulto produccion;
el dueno debe confirmar que el JSON real tiene esta forma (ver el informe de la tarea)."""

from tests.test_tienda_checkout import SLUG, _body, _shop

# lo que Mi Tienda guarda (montos en colones sin IVA, numericos)
TARIFAS_GUARDADAS = [
    {"name": "Retiro en tienda", "amount": 0, "active": True},
    {"name": "Envío GAM", "amount": 4000, "active": True, "zone": "gam"},
    {"name": "Fuera de GAM (Correos)", "amount": 2000, "per_kg": 1000, "overhead_pct": 10, "active": True, "zone": "fuera_gam"},
    {"name": "Envío viejo", "amount": 3500, "active": False, "zone": ""},
]
# respuesta publica de checkout-config de esas tarifas (solo las activas; display_amount con IVA)
CHECKOUT_CONFIG_PUBLICO = {
    "shipping_rates": [
        {"name": "Retiro en tienda", "zone": "retiro", "per_kg": False, "display_amount": "0.00"},
        {"name": "Envío GAM", "zone": "gam", "per_kg": False, "display_amount": "4520.00"},
        {"name": "Fuera de GAM (Correos)", "zone": "fuera_gam", "per_kg": True, "display_amount": "2260.00"},
    ]
}


def test_lo_guardado_hoy_valida_y_recibe_id(client, auth):
    r = client.put("/store", json={"shipping_rates": TARIFAS_GUARDADAS})
    assert r.status_code == 200, r.text
    rates = r.json()["shipping_rates"]
    assert [x["name"] for x in rates] == [x["name"] for x in TARIFAS_GUARDADAS]
    assert all(x["id"] for x in rates) and len({x["id"] for x in rates}) == len(rates)
    assert rates[1]["amount"] == 4000 and rates[2]["per_kg"] == 1000 and rates[2]["overhead_pct"] == 10
    assert "zone" not in rates[3]  # zona vacia = deducida del nombre
    # el id se conserva al volver a guardar sin id (pantallas viejas) y con id
    again = client.put("/store", json={"shipping_rates": [{k: v for k, v in x.items() if k != "id"} for x in rates]}).json()["shipping_rates"]
    assert [x["id"] for x in again] == [x["id"] for x in rates]
    # la respuesta publica trae el id y la misma forma que el fixture
    _shop(client)
    cfg = client.get(f"{SLUG}/checkout-config").json()
    assert [(x["name"], x["zone"], x["per_kg"]) for x in cfg["shipping_rates"]] == [
        (x["name"], x["zone"], x["per_kg"]) for x in CHECKOUT_CONFIG_PUBLICO["shipping_rates"]
    ]
    assert all(x["id"] for x in cfg["shipping_rates"])


def test_esquema_rechaza_lo_malo(client, auth):
    def put(rates):
        return client.put("/store", json={"shipping_rates": rates}).status_code

    assert put([{"name": "Envío GAM", "amount": 4000}, {"name": "envío gam", "amount": 1}]) == 422  # duplicado
    assert put([{"name": "X", "amount": 1}]) == 422  # nombre corto
    assert put([{"name": "Envío GAM", "amount": -1}]) == 422
    assert put([{"name": "Envío GAM", "amount": "4.700"}]) == 422  # texto: nunca se convierte en 4,7
    assert put([{"name": "Envío GAM", "amount": 10, "overhead_pct": 150}]) == 422
    assert put([{"name": "Envío GAM", "amount": 10, "per_kg": -5}]) == 422
    assert put([{"name": "Envío GAM", "amount": 10, "zone": "luna"}]) == 422
    assert put([{"name": "Envío GAM", "amount": 4700.5}]) == 200


def test_carrito_abierto_con_nombre_viejo_sigue_cotizando(client, auth):
    prods = _shop(client)
    rates = client.get("/store").json()["shipping_rates"]
    gam = next(x for x in rates if x["name"] == "Envío GAM")
    assert gam.get("id")
    q0 = client.post(f"{SLUG}/quote", json=_body(prods[0]["id"], shipping_rate_id=gam["id"]))
    assert q0.status_code == 200, q0.text
    # el dueno renombra la tarifa mientras el cliente tiene el checkout abierto
    renamed = [{**x, "name": "Envío dentro de la GAM"} if x["id"] == gam["id"] else x for x in rates]
    assert client.put("/store", json={"shipping_rates": renamed}).status_code == 200
    q1 = client.post(f"{SLUG}/quote", json=_body(prods[0]["id"], shipping_method="Envío GAM", shipping_rate_id=gam["id"]))
    assert q1.status_code == 200, q1.text
    assert q1.json()["total"] == q0.json()["total"]
    # sin id, el nombre viejo ya no existe: se avisa en vez de cobrar otra cosa
    assert client.post(f"{SLUG}/quote", json=_body(prods[0]["id"], shipping_method="Envío GAM")).status_code == 422
    # por nombre (clientes viejos sin id) sigue sirviendo
    assert client.post(f"{SLUG}/quote", json=_body(prods[0]["id"], shipping_method="Envío dentro de la GAM")).status_code == 200
