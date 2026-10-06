"""Auditoria 5 oct 2026: tratamiento de lineas en 0, margen vs recargo y unidades de servicio."""

from decimal import Decimal

from app.services import pricing
from app.services.survey_specs import classify_kind, normalize_item
from app.services.units import hacienda_unit


def _cust(client):
    return client.get("/customers").json()["items"][0]


def _quote(client, lines):
    r = client.post("/quotes", json={"customer_id": _cust(client)["id"], "lines": lines})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 6. tratamiento de lineas ----------
def test_aportado_cortesia_excluido_no_suman(client, auth):
    q = _quote(
        client,
        [
            {"name": "Instalacion", "quantity": 1, "unit_price": 100000, "tax_rate": 13},
            {"name": "Fortinet del cliente", "quantity": 1, "unit_price": 500000, "tax_rate": 13, "treatment": "aportado"},
            {"name": "Configuracion extra", "quantity": 1, "unit_price": 0, "tax_rate": 13, "treatment": "cortesia"},
            {"name": "Obra civil", "quantity": 1, "unit_price": 0, "tax_rate": 13, "treatment": "excluido"},
        ],
    )
    assert Decimal(str(q["total"])) == Decimal("113000")
    assert [ln["treatment"] for ln in q["lines"]] == ["normal", "aportado", "cortesia", "excluido"]
    html = client.get(f"/quotes/{q['id']}/pdf").text
    assert "Equipo aportado por el cliente/aliado" in html and "Cortesía" in html
    assert "Exclusiones" in html and "Obra civil" in html
    assert client.post(f"/quotes/{q['id']}/send").status_code == 200


def test_pendiente_bloquea_enviar_y_convertir_pero_guarda(client, auth):
    lines = [{"name": "Sensor Paradox", "quantity": 4, "unit_price": 0, "tax_rate": 13, "treatment": "pendiente"}]
    q = _quote(client, lines)  # guardar si
    r = client.post(f"/quotes/{q['id']}/send")
    assert r.status_code == 409 and "pendientes" in r.json()["detail"]
    assert client.post(f"/quotes/{q['id']}/convert").status_code == 409
    assert client.post(f"/quotes/{q['id']}/email", json={"to": "x@example.com"}).status_code == 409
    # con precio ya sale
    lines[0].update(unit_price=15000, treatment="normal")
    assert client.put(f"/quotes/{q['id']}", json={"customer_id": _cust(client)["id"], "lines": lines}).status_code == 200
    assert client.post(f"/quotes/{q['id']}/convert").status_code == 201


def test_linea_en_cero_sin_tratamiento_pide_elegirlo(client, auth):
    q = _quote(client, [{"name": "Fortinet", "quantity": 1, "unit_price": 0, "tax_rate": 13}])
    r = client.post(f"/quotes/{q['id']}/send")
    assert r.status_code == 409 and "Elegí" in r.json()["detail"]


def test_factura_no_acepta_pendiente(client, auth):
    r = client.post("/invoices", json={"customer_id": _cust(client)["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 0, "treatment": "pendiente"}]})
    assert r.status_code == 422


def test_levantamiento_sin_costo_cotiza_pendiente(client, auth):
    s = client.post(
        "/surveys",
        json={"kind": "cctv", "customer_id": _cust(client)["id"], "items": [{"name": "Sensor sin costo", "quantity": 2}]},
    ).json()
    q = client.post(f"/surveys/{s['id']}/quote", json={"margin": 35}).json()
    quote = client.get(f"/quotes/{q['quote_id']}").json()
    sensor = next(ln for ln in quote["lines"] if ln["name"] == "Sensor sin costo")
    assert sensor["treatment"] == "pendiente"  # no un 0 callado
    assert client.post(f"/quotes/{q['quote_id']}/send").status_code == 409


# ---------- 7. margen vs recargo ----------
def test_margen_vs_recargo():
    assert pricing.price_from_margin(100000, 30) == Decimal("142857.14")
    assert pricing.price_from_markup(100000, 30) == Decimal("130000.00")
    assert pricing.margin_from_markup(30) == Decimal("23.08")
    assert pricing.markup_from_margin(30) == Decimal("42.86")


def test_politica_vigente_no_cambia(client, auth):
    """El costeo sigue con margen sobre venta (35 % -> /0.65, redondeo a la centena) y ahora lo dice."""
    assert pricing.sale_price(None, Decimal(65000), "CRC", "CRC", Decimal(35)) == Decimal(100000)
    s = client.post("/surveys", json={"kind": "cctv", "customer_id": _cust(client)["id"], "items": [{"name": "Gabinete 15U", "quantity": 1}]}).json()
    c = client.get(f"/surveys/{s['id']}/costing").json()
    pol = c["pricing_policy"]
    assert pol["mode"] == "margen_sobre_venta" and "÷ (1 − margen)" in pol["formula"]
    assert Decimal(str(pol["target_markup_pct"])) == Decimal("53.85")  # 35 % de margen = 53.85 % de recargo


# ---------- 8. unidades de servicio ----------
def test_unidades_hacienda():
    assert hacienda_unit("dia") == "d" and hacienda_unit("jornada") == "d"
    assert hacienda_unit("hora") == "h" and hacienda_unit("servicio") == "Os" and hacienda_unit("mes") == "Os"
    assert hacienda_unit("Sp") == "Sp" and hacienda_unit("m") == "m" and hacienda_unit(None) == "Unid"


def test_elevador_es_servicio_no_material_por_metro(client, auth):
    assert classify_kind("Alquiler de elevador") == "servicio"
    assert classify_kind("Grúa para poste") == "servicio"
    assert classify_kind("Cable UTP Cat6") == "material"
    assert normalize_item("Elevador tijera", "material", "m") == ("servicio", "servicio")
    s = client.post(
        "/surveys",
        json={"kind": "cctv", "items": [{"name": "Elevador tijera", "quantity": 2, "unit": "m", "kind": "material"}, {"name": "Andamio", "quantity": 3, "unit": "dia"}]},
    ).json()
    el = next(i for i in s["items"] if i["name"] == "Elevador tijera")
    an = next(i for i in s["items"] if i["name"] == "Andamio")
    assert (el["kind"], el["unit"]) == ("servicio", "servicio")
    assert (an["kind"], an["unit"]) == ("servicio", "dia")
