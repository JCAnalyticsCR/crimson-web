"""O1-17: quien vende y a nombre de quien se paga. Datos del comercio en Mi Tienda, en la respuesta publica, en el
checkout (con el titular del pago solo por nombre y el plazo real de la reserva) y en el correo del pedido. Datos de
prueba ficticios salvo los de Crimson que siembra la migracion (publicos en su sitio)."""

import importlib.util
from pathlib import Path

from tests.test_tienda_checkout import SLUG, _body, _shop

BIZ = {"legal_name": "Comercio de Prueba SRL", "legal_id": "3-102-000000", "address": "", "phone": "+506 2222-0000", "email": "ventas@example.com", "hours": "lunes a viernes de 8 a 5"}


def _migracion():
    path = next(Path(__file__).resolve().parents[1].glob("alembic/versions/tienda_comercio_*.py"))
    spec = importlib.util.spec_from_file_location("mig_comercio", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migracion_solo_llena_lo_vacio_y_es_idempotente():
    m = _migracion()
    nuevo = m.fill({"otra": 1})
    assert nuevo["otra"] == 1 and nuevo["store"]["business"]["legal_name"] == "Grupo Empresarial Crimson SRL"
    assert nuevo["store"]["business"]["legal_id"] == "3-102-914922" and nuevo["store"]["business"]["address"] == ""
    assert m.fill(nuevo) is None  # segunda vez: nada que cambiar
    # lo que el dueno ya escribio no se toca; solo se completa lo vacio
    propio = m.fill({"store": {"name": "X", "business": {"legal_name": "Otra SRL", "hours": "  "}}})
    assert propio["store"]["name"] == "X" and propio["store"]["business"]["legal_name"] == "Otra SRL"
    assert propio["store"]["business"]["hours"].startswith("lunes a viernes")


def test_datos_del_comercio_en_la_tienda_y_el_checkout(client, auth):
    _shop(client)
    r = client.put("/store", json={"business": BIZ})
    assert r.status_code == 200, r.text
    assert r.json()["business"]["legal_name"] == BIZ["legal_name"]
    home = client.get(SLUG).json()
    assert home["store"]["business"] == BIZ
    cfg = client.get(f"{SLUG}/checkout-config").json()
    assert cfg["business"]["legal_id"] == BIZ["legal_id"]
    # del titular solo el nombre: ni telefono de SINPE ni IBAN en la configuracion publica
    assert cfg["holders"] == {"sinpe": "Crimson SA", "transferencia": "Crimson SA"}
    plano = str(cfg)
    assert "8888-7777" not in plano and "CR05015201001026284066" not in plano
    # el plazo es el real de la reserva (services/availability)
    assert cfg["hold"] == {"enabled": True, "manual_hours": 48, "card_minutes": 30}
    # campos desconocidos no
    assert client.put("/store", json={"business": {"legal_name": "A", "otra": "x"}}).status_code == 422


def test_sin_datos_no_hay_etiquetas_vacias(client, auth):
    _shop(client)
    home = client.get(SLUG).json()
    assert home["store"]["business"] == {k: "" for k in BIZ}


def test_correo_del_pedido_lleva_los_datos_del_comercio(client, auth, db_session):
    from app.models import Order, Tenant
    from app.services import store_orders as so

    prods = _shop(client)
    client.put("/store", json={"business": BIZ})
    r = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], idempotency_key="comercio-0001"))
    assert r.status_code == 201, r.text
    o = db_session.query(Order).filter(Order.number == r.json()["number"]).one()
    t = db_session.get(Tenant, o.tenant_id)
    html = so._email_html(t, o, "recibido")
    assert "Comercio de Prueba SRL · Cédula jurídica 3-102-000000" in html
    assert "Horario: lunes a viernes de 8 a 5" in html
    # sin datos: nada de etiquetas sueltas
    t.settings = {**t.settings, "store": {**t.settings["store"], "business": {}}}
    db_session.commit()
    vacio = so._email_html(t, o, "recibido")
    assert "Cédula jurídica" not in vacio and "Horario:" not in vacio
