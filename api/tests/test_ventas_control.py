"""Pruebas para las 11 auditorías del Rol Vendedor (ventas-control, oct 2026).

Cubre:
  1. Descuentos: precio de catálogo (cross-currency), tratamientos _NO_SUMA, approval_reasons en
     factura directa y POS.
  2. Embudo: "ganada" requiere cotización o crm_pipeline.ver_todo; "perdida" requiere motivo de la
     lista cerrada + campo lost_competitor.
  3. Inventario: nota obligatoria para salidas/ajustes del vendedor.
  4. Envíos: vendedor solo puede enviar al correo registrado del cliente.
  6. Prospecto → Cliente: meta devuelve lost_reasons; /opportunities/{id} expone lost_competitor.
"""

from decimal import Decimal

import pytest
from tests.conftest import ADMIN
from tests.test_session4 import _user_with_role

D = Decimal


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _cust(client):
    return client.get("/customers").json()["items"][0]


def _vendor_headers(client, db_session, email="v-ctrl@ejemplo.com"):
    """Crea un usuario con rol 'ventas' y devuelve los headers de autenticación."""
    admin_h = dict(client.headers)
    tid = client.get("/auth/me").json()["tenant"]["id"]
    pw = _user_with_role(db_session, tid, email, "ventas")
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    client.headers.update(admin_h)
    return {"Authorization": f"Bearer {tok}"}


def _prod_usd(client, db_session, price_usd=100):
    """Crea un producto en USD para probar la comparación cross-currency."""
    from app.models import Product

    p = client.post("/products", json={"name": "Cámara USD", "code": "CAM-USD-VC", "price": price_usd}).json()
    obj = db_session.get(Product, p["id"])
    obj.currency = "USD"
    db_session.commit()
    return p


def _prod_crc(client, price=50000):
    return client.post("/products", json={"name": "Cámara CRC", "code": "CAM-CRC-VC", "price": price}).json()


def _warehouse(client):
    """Devuelve el ID de la bodega principal."""
    ws = client.get("/warehouses").json()
    return ws[0]["id"]


def _opp(client, **extra):
    c = _cust(client)
    return client.post(
        "/opportunities",
        json={
            "title": "Opp de prueba",
            "kind": "venta",
            "customer_id": c["id"],
            "amount": 100000,
            "probability": 50,
            **extra,
        },
    ).json()


# ─────────────────────────────────────────────────────────────────────────────
# 1. DESCUENTOS
# ─────────────────────────────────────────────────────────────────────────────


def test_descuento_cross_currency_activa_por_aprobar(client, auth, db_session):
    """Un producto en USD con precio de catálogo equivalente a 530 000 CRC:
    venderlo a 400 000 CRC supera el límite del 10 % del vendedor → por_aprobar."""
    from datetime import date
    from app.models import ExchangeRate

    # Asegurar tipo de cambio
    today = date.today()
    fx = db_session.query(ExchangeRate).filter_by(date=today, currency="USD").first()
    if fx is None:
        fx = ExchangeRate(date=today, currency="USD", sell=530, buy=520, source="manual")
        db_session.add(fx)
        db_session.commit()
    else:
        fx.sell, fx.buy = 530, 520
        db_session.commit()

    prod = _prod_usd(client, db_session, price_usd=1000)  # 1 000 USD × 530 = 530 000 CRC
    v = _vendor_headers(client, db_session, "v-cross@ejemplo.com")
    c = _cust(client)
    # vender a 400 000 CRC = 24.5 % de descuento → por_aprobar (límite 10 %)
    q = client.post(
        "/quotes",
        json={"customer_id": c["id"], "lines": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 400000, "tax_rate": 13}]},
        headers=v,
    ).json()
    assert q["status"] == "por_aprobar", f"Esperaba por_aprobar, recibí: {q['status']}"


def test_no_suma_cuenta_como_100_pct_descuento(client, auth, db_session):
    """Una línea con tratamiento 'cortesia' suma al contador de descuento incluso con líneas normales."""
    prod = _prod_crc(client)
    v = _vendor_headers(client, db_session, "v-nosum@ejemplo.com")
    c = _cust(client)
    # línea normal en precio catalogo + línea cortesía: la cortesía debe activar approval_reasons
    q = client.post(
        "/quotes",
        json={
            "customer_id": c["id"],
            "lines": [
                {"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 50000, "tax_rate": 13},
                {"name": "Instalación gratis", "quantity": 1, "unit_price": 20000, "tax_rate": 0, "treatment": "cortesia"},
            ],
        },
        headers=v,
    ).json()
    # la cortesía activa approval_reasons (free-line sin producto del catálogo), queda por_aprobar
    assert q["status"] == "por_aprobar"


def test_factura_directa_con_descuento_excesivo_rechazada(client, auth, db_session):
    """Factura directa con descuento mayor al límite → 422 (no puede quedar por_aprobar)."""
    prod = _prod_crc(client)
    v = _vendor_headers(client, db_session, "v-inv@ejemplo.com")
    c = _cust(client)
    r = client.post(
        "/invoices",
        json={"customer_id": c["id"], "lines": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 40000, "discount_value": 15, "tax_rate": 13}]},
        headers=v,
    )
    assert r.status_code == 422, r.text
    assert "aprobación" in r.json()["detail"].lower() or "límite" in r.json()["detail"].lower()


def test_pos_con_descuento_excesivo_rechazado(client, auth, db_session):
    """POS con descuento mayor al límite → 422."""
    prod = _prod_crc(client)
    v = _vendor_headers(client, db_session, "v-pos@ejemplo.com")
    r = client.post(
        "/pos/sale",
        json={
            "lines": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 50000, "discount_value": 30, "tax_rate": 13}],
            "payments": [{"method": "efectivo", "amount": 999999}],
        },
        headers=v,
    )
    assert r.status_code == 422, r.text


# ─────────────────────────────────────────────────────────────────────────────
# 2. EMBUDO
# ─────────────────────────────────────────────────────────────────────────────


def test_ganada_sin_cotizacion_bloqueada_para_vendedor(client, auth, db_session):
    """El vendedor no puede marcar 'ganada' sin cotización/proyecto vinculado."""
    v = _vendor_headers(client, db_session, "v-ganada@ejemplo.com")
    # El vendedor crea su propia oportunidad (owner = él mismo)
    c = _cust(client)
    o = client.post("/opportunities", json={"title": "Opp ganada test", "kind": "venta", "customer_id": c["id"], "amount": 100000, "probability": 50}, headers=v).json()
    r = client.post(
        f"/opportunities/{o['id']}/touch",
        json={"note": "Cerramos el trato", "status": "ganada"},
        headers=v,
    )
    assert r.status_code in (403, 422), f"Debió rechazarse: {r.status_code} {r.text}"


def test_ganada_con_cotizacion_permitida(client, auth, db_session):
    """Con cotización vinculada, el vendedor puede marcar 'ganada'."""
    from app.models import Opportunity

    v = _vendor_headers(client, db_session, "v-ganada2@ejemplo.com")
    c = _cust(client)
    prod = _prod_crc(client)
    # Vendedor crea cotización y oportunidad
    q = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 50000, "tax_rate": 13}]}, headers=v).json()
    o = client.post("/opportunities", json={"title": "Opp ganada2 test", "kind": "venta", "customer_id": c["id"], "amount": 50000, "probability": 90}, headers=v).json()
    # Vincular la cotización directamente en DB (la API no expone quote_id en el PUT; se linkea al crear la cotización desde la oportunidad)
    oobj = db_session.get(Opportunity, o["id"])
    oobj.quote_id = q["id"]
    db_session.commit()
    r = client.post(f"/opportunities/{o['id']}/touch", json={"note": "Cerramos el trato", "status": "ganada"}, headers=v)
    assert r.status_code == 200, r.text


def test_perdida_sin_motivo_rechazada(client, auth, db_session):
    """'perdida' sin lost_reason → 422."""
    v = _vendor_headers(client, db_session, "v-perdida@ejemplo.com")
    c = _cust(client)
    o = client.post("/opportunities", json={"title": "Opp perdida test", "kind": "venta", "customer_id": c["id"], "amount": 100000, "probability": 50}, headers=v).json()
    r = client.post(
        f"/opportunities/{o['id']}/touch",
        json={"note": "Nos fue mal", "status": "perdida"},
        headers=v,
    )
    assert r.status_code == 422, f"Debió rechazar sin motivo: {r.status_code}"


def test_perdida_con_motivo_invalido_rechazada(client, auth, db_session):
    """'perdida' con motivo fuera de la lista cerrada → 422."""
    v = _vendor_headers(client, db_session, "v-perdida2@ejemplo.com")
    c = _cust(client)
    o = client.post("/opportunities", json={"title": "Opp perdida2 test", "kind": "venta", "customer_id": c["id"], "amount": 100000, "probability": 50}, headers=v).json()
    r = client.post(
        f"/opportunities/{o['id']}/touch",
        json={"note": "Nos fue mal", "status": "perdida", "lost_reason": "No me gustó"},
        headers=v,
    )
    assert r.status_code == 422, r.text


def test_perdida_con_motivo_valido_y_competidor(client, auth, db_session):
    """'perdida' con motivo válido + competidor → 200 y los datos quedan guardados."""
    v = _vendor_headers(client, db_session, "v-perdida3@ejemplo.com")
    c = _cust(client)
    o = client.post("/opportunities", json={"title": "Opp perdida3 test", "kind": "venta", "customer_id": c["id"], "amount": 100000, "probability": 50}, headers=v).json()
    r = client.post(
        f"/opportunities/{o['id']}/touch",
        json={"note": "Eligieron otra opción", "status": "perdida", "lost_reason": "Eligió a la competencia", "lost_competitor": "Securitas"},
        headers=v,
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/opportunities/{o['id']}").json()
    assert detail["lost_reason"] == "Eligió a la competencia"
    assert detail["lost_competitor"] == "Securitas"


def test_meta_incluye_lost_reasons(client, auth):
    """El endpoint de meta devuelve la lista de motivos de pérdida."""
    meta = client.get("/opportunities/meta/config").json()
    assert "lost_reasons" in meta
    assert "Precio" in meta["lost_reasons"]
    assert "Eligió a la competencia" in meta["lost_reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. INVENTARIO
# ─────────────────────────────────────────────────────────────────────────────


def test_salida_sin_nota_bloqueada_para_vendedor(client, auth, db_session):
    """El vendedor no puede hacer salida de inventario sin nota."""
    from app.models import Product

    prod = client.post("/products", json={"name": "Prd Salida", "code": "PRD-SAL-VC", "price": 1000}).json()
    obj = db_session.get(Product, prod["id"])
    obj.quantity = 10
    db_session.commit()

    v = _vendor_headers(client, db_session, "v-inv-nota@ejemplo.com")
    wid = _warehouse(client)
    r = client.post(
        "/stock/movements",
        json={"product_id": prod["id"], "warehouse_id": wid, "kind": "salida", "quantity": 1},
        headers=v,
    )
    assert r.status_code == 422, f"Debió requerir nota: {r.status_code}"
    assert "nota" in r.json()["detail"].lower() or "motivo" in r.json()["detail"].lower()


def test_ajuste_sin_nota_bloqueado_para_vendedor(client, auth, db_session):
    """El vendedor no puede hacer ajuste de inventario sin nota."""
    from app.models import Product

    prod = client.post("/products", json={"name": "Prd Ajuste", "code": "PRD-AJU-VC", "price": 1000}).json()
    obj = db_session.get(Product, prod["id"])
    obj.quantity = 10
    db_session.commit()

    v = _vendor_headers(client, db_session, "v-inv-aj@ejemplo.com")
    wid = _warehouse(client)
    r = client.post(
        "/stock/movements",
        json={"product_id": prod["id"], "warehouse_id": wid, "kind": "ajuste", "quantity": 8},
        headers=v,
    )
    assert r.status_code == 422, r.text


def test_salida_con_nota_permitida_para_vendedor(client, auth, db_session):
    """Con nota, el vendedor puede hacer salida."""
    from app.models import Product

    prod = client.post("/products", json={"name": "Prd Sal OK", "code": "PRD-SOK-VC", "price": 1000}).json()
    obj = db_session.get(Product, prod["id"])
    obj.quantity = 10
    db_session.commit()

    v = _vendor_headers(client, db_session, "v-inv-ok@ejemplo.com")
    wid = _warehouse(client)
    r = client.post(
        "/stock/movements",
        json={"product_id": prod["id"], "warehouse_id": wid, "kind": "salida", "quantity": 1, "note": "Demostración al cliente"},
        headers=v,
    )
    assert r.status_code == 201, r.text


# ─────────────────────────────────────────────────────────────────────────────
# 4. ENVÍOS
# ─────────────────────────────────────────────────────────────────────────────


def test_vendedor_solo_puede_enviar_al_correo_del_cliente(client, auth, db_session):
    """El vendedor no puede enviar a un correo diferente al registrado del cliente."""
    from app.models import Customer

    prod = _prod_crc(client)
    v = _vendor_headers(client, db_session, "v-envio@ejemplo.com")
    c = _cust(client)
    # asegurar que el cliente tiene correo registrado
    cobj = db_session.get(Customer, c["id"])
    if not cobj.email:
        cobj.email = "cliente.real@ejemplo.com"
        db_session.commit()
    q = client.post(
        "/quotes",
        json={"customer_id": c["id"], "lines": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 50000, "tax_rate": 13}]},
        headers=v,
    ).json()
    # enviar a un correo diferente al registrado del cliente → 403
    r = client.post(
        f"/quotes/{q['id']}/email",
        json={"to": "intruso@hacker.com"},
        headers=v,
    )
    assert r.status_code == 403, f"Debió rechazar envío a otro correo: {r.status_code} {r.text}"


def test_vendedor_puede_enviar_al_correo_registrado(client, auth, db_session):
    """El vendedor puede enviar al correo registrado del cliente."""
    from app.models import Customer

    prod = _prod_crc(client)
    v = _vendor_headers(client, db_session, "v-envio2@ejemplo.com")
    c = _cust(client)
    # asegurar que el cliente tiene correo
    cobj = db_session.get(Customer, c["id"])
    if not cobj.email:
        cobj.email = "cliente@ejemplo.com"
        db_session.commit()

    q = client.post(
        "/quotes",
        json={"customer_id": c["id"], "lines": [{"product_id": prod["id"], "name": prod["name"], "quantity": 1, "unit_price": 50000, "tax_rate": 13}]},
        headers=v,
    ).json()
    email_cliente = cobj.email
    r = client.post(
        f"/quotes/{q['id']}/email",
        json={"to": email_cliente},
        headers=v,
    )
    # 200 o 422 (si falla por otro motivo de negocio, no debe ser 403)
    assert r.status_code != 403, f"No debió rechazar el correo del cliente: {r.status_code} {r.text}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. PROSPECTO → CLIENTE: campo contact expuesto en la API
# ─────────────────────────────────────────────────────────────────────────────


def test_oportunidad_expone_campo_contact(client, auth, db_session):
    """Una oportunidad creada con contact dict lo devuelve en GET /opportunities/{id}."""
    from app.models import Opportunity

    o = _opp(client)
    oobj = db_session.get(Opportunity, o["id"])
    oobj.contact = {"name": "Juan Prospecto", "phone": "88887777", "email": "juan@prospecto.com"}
    db_session.commit()

    detail = client.get(f"/opportunities/{o['id']}").json()
    assert detail.get("contact", {}).get("phone") == "88887777"
    assert detail.get("contact", {}).get("email") == "juan@prospecto.com"


def test_oportunidad_fuente_sitio_web_normalizada(client, auth, db_session):
    """Crear oportunidad con source='Sitio web' la almacena y devuelve correctamente."""
    c = _cust(client)
    o = client.post(
        "/opportunities",
        json={"title": "Web test", "kind": "venta", "customer_id": c["id"], "amount": 50000, "probability": 20, "source": "Sitio web"},
    ).json()
    assert o.get("source") == "Sitio web"
