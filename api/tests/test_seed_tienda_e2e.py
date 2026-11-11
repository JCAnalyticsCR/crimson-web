"""Semilla de la tienda local de pruebas (scripts/seed_tienda_e2e.py, plan de la tienda O0-03): deja los seis productos
en su estado, se puede correr dos veces, alcanza para una compra SINPE completa por la API publica y no corre fuera
de una base local. La compra en un navegador de verdad esta en portal/e2e/compra.spec.ts."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Order, Product, Tenant, User
from scripts import seed_tienda_e2e as semilla

PUB = "/public/store/crimson"
D = Decimal


def _sembrar(db):
    # la base de las pruebas trae el catalogo demo de conftest (productos propios en la web): por eso forzar
    return semilla.sembrar(db, forzar=True)


def test_seis_productos_cada_uno_en_su_estado(client, db_session):
    r = _sembrar(db_session)
    estados = {c: p["estado"] for c, p in r["productos"].items()}
    assert estados == {
        "E2E-BOD-1": "en_bodega",
        "E2E-BOD-2": "en_bodega",
        "E2E-PED-1": "bajo_pedido",
        "E2E-PED-2": "bajo_pedido",
        "E2E-CON-1": "consultar",
        "E2E-AGO-1": "agotado",
    }
    assert all(p["precio_con_iva"] <= 2000 for p in r["productos"].values())  # datos de prueba: 2 000 colones o menos
    # la tienda responde por su dominio local y el catalogo publico los lista (el agotado tambien: no esta oculto)
    assert client.get("/public/store-domain", params={"host": "tienda.localhost"}).json() == {"slug": "crimson"}
    lista = {p["code"]: p for p in client.get(f"{PUB}/products", params={"q": "E2E"}).json()}
    assert set(lista) == set(estados)
    assert lista["E2E-BOD-1"]["availability"]["label"] == "En bodega · 40 disponibles" and D(str(lista["E2E-BOD-1"]["display_price"])) == D("1695.00")
    assert lista["E2E-PED-1"]["availability"]["disponible"] and lista["E2E-PED-1"]["availability"]["max_qty"] == 25
    assert not lista["E2E-CON-1"]["availability"]["disponible"] and not lista["E2E-AGO-1"]["availability"]["disponible"]
    # pagos: SINPE y tarjeta en modo de prueba (sin llave de ONVO, fuera de produccion); tarifas con IVA
    cfg = client.get(f"{PUB}/checkout-config").json()
    assert [(m["code"], m["simulated"]) for m in cfg["methods"]] == [("sinpe", False), ("tarjeta", True)]
    assert {(x["name"], x["zone"], D(str(x["display_amount"]))) for x in cfg["shipping_rates"]} == {
        ("Correos de Costa Rica", None, D("3955.00")),
        ("Fast Crimson", "gam", D("5311.00")),
    }
    assert D(str(cfg["free_shipping_from"])) == D("50000.00") and cfg["terms_required"] and not cfg["verify_email"] and not cfg["auto_invoice"]
    assert client.get(f"{PUB}/site").json() == {"analytics": {"ga4_id": None}}  # sin medicion
    # la semilla no crea usuarios ni contrasenas (los que hay son de conftest)
    assert db_session.scalars(select(User.email)).all() == ["admin@crimsonapp.com"]


def test_es_idempotente_y_repone_lo_vendido(client, db_session):
    a = _sembrar(db_session)
    pid = a["productos"]["E2E-BOD-2"]["id"]
    body = {
        "lines": [{"product_id": pid, "quantity": 2}],
        "contact": {"name": "Ana Prueba Ficticia", "email": "ana.prueba@example.com", "phone": "8888 0000"},
        "delivery": {"type": "envio", "province": "Heredia", "canton": "Barva", "district": "San Pedro", "address": "Del parque 200 m norte"},
        "shipping_method": "Fast Crimson",
        "payment_method": "sinpe",
        "accept_terms": True,
    }
    assert client.post(f"{PUB}/checkout", json=body).status_code == 201
    assert client.get(f"{PUB}/availability", params={"ids": pid}).json()[str(pid)]["cantidad"] == 1  # 3 - 2 apartados
    b = _sembrar(db_session)
    assert {c: p["id"] for c, p in a["productos"].items()} == {c: p["id"] for c, p in b["productos"].items()}  # los mismos, no duplicados
    assert len(db_session.scalars(select(Product).where(Product.code.startswith("E2E-"))).all()) == 6
    assert client.get(f"{PUB}/availability", params={"ids": pid}).json()[str(pid)]["cantidad"] == 3  # repuesto: lo libre vuelve a 3
    # no toca los precios de los productos que no son suyos
    demo = db_session.scalar(select(Product).where(Product.code == "CAM-DOME-4MP"))
    assert D(str(demo.price)) == D("68500")


def test_compra_sinpe_completa_por_la_api(client, db_session):
    r = _sembrar(db_session)
    pid = r["productos"]["E2E-BOD-1"]["id"]
    body = {
        "lines": [{"product_id": pid, "quantity": 1}],
        "contact": {"name": "Ana Prueba Ficticia", "email": "ana.prueba@example.com", "phone": "8888 0000"},
        "delivery": {"type": "envio", "province": "Heredia", "canton": "Barva", "district": "San Pedro", "address": "Del parque 200 m norte"},
        "shipping_method": "Fast Crimson",
        "payment_method": "sinpe",
        "accept_terms": True,
    }
    q = client.post(f"{PUB}/quote", json=body).json()
    assert D(str(q["total"])) == D("7006.00")  # 1 695 del producto + 5 311 del envio, con IVA
    o = client.post(f"{PUB}/checkout", json={**body, "idempotency_key": "e2e-semilla-0001", "expected_total": str(q["total"])})
    assert o.status_code == 201, o.text
    o = o.json()
    assert o["status"] == "nuevo" and o["payment_kind"] == "sinpe" and o["needs_proof"]
    assert "0000-0000" in o["instructions"] and o["number"] in o["instructions"]
    assert o["email"]["status"] == "simulado"  # sin RESEND_API_KEY no sale ningun correo
    pub = client.get(f"{PUB}/orders/{o['track_token']}").json()
    assert pub["number"] == o["number"] and pub["status_label"] == "Pendiente de pago" and pub["needs_proof"]
    # el pago con tarjeta de prueba: se crea el pedido y «Simular pago aprobado» lo deja pagado, sin ONVO
    t = client.post(f"{PUB}/checkout", json={**body, "payment_method": "tarjeta", "idempotency_key": "e2e-semilla-0002"}).json()
    assert t["pay"] and t["pay"].get("simulated") and not t["pay"].get("checkout_url")
    paid = client.post(f"{PUB}/orders/{t['track_token']}/simulate-payment")
    assert paid.status_code == 200 and paid.json()["status"] == "pagado"
    assert len(db_session.scalars(select(Order).where(Order.channel == "tienda")).all()) == 2


def test_candados_de_entorno_y_de_catalogo(db_session):
    ok = "sqlite:///./x.db"
    semilla.revisar_entorno(ok, "local", {})
    semilla.revisar_entorno("postgresql+psycopg://u:p@localhost:5432/crimson", "local", {})
    semilla.revisar_entorno("postgresql+psycopg://u:p@postgres:5432/crimson", "local", {})
    for url, env, entorno in (
        (ok, "production", {}),
        (ok, "staging", {}),
        (ok, "local", {"RAILWAY_ENVIRONMENT_ID": "abc"}),
        ("postgresql+psycopg://u:p@db.ejemplo.com:5432/crimson", "local", {}),
        ("postgresql+psycopg://u:p@postgres.railway.internal:5432/railway", "local", {}),
    ):
        with pytest.raises(semilla.SemillaRechazada):
            semilla.revisar_entorno(url, env, entorno)
    # una base con catalogo propio en la web (aqui, el demo de conftest) no se pisa sin pedirlo, y no queda nada a medias
    with pytest.raises(semilla.SemillaRechazada):
        semilla.sembrar(db_session)
    db_session.rollback()
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    assert not ((t.settings or {}).get("store") or {}).get("published")
    assert db_session.scalars(select(Product).where(Product.code.startswith("E2E-"))).all() == []


def test_base_de_pruebas_por_nombre():
    assert semilla.base_de_pruebas("sqlite:///./x.db")
    assert semilla.base_de_pruebas("postgresql+psycopg://u:p@localhost:5432/crimson_e2e")
    assert semilla.base_de_pruebas("postgresql+psycopg://u:p@localhost:5432/crimson_test")
    assert not semilla.base_de_pruebas("postgresql+psycopg://u:p@localhost:15432/railway")
    assert not semilla.base_de_pruebas("postgresql+psycopg://u:p@postgres:5432/crimson")


def test_postgres_con_tablas_y_sin_marca_se_rechaza_antes_de_migrar():
    import sqlalchemy as sa
    from sqlalchemy.engine import make_url

    real = sa.create_engine("sqlite://")
    with real.begin() as c:
        c.execute(sa.text("CREATE TABLE product (id INTEGER PRIMARY KEY)"))

    class Tunel:  # una "Postgres" en localhost (un tunel a la base real) que ya tiene tablas
        url = make_url("postgresql+psycopg://u:p@localhost:15432/railway")

        def connect(self):
            return real.connect()

    with pytest.raises(semilla.SemillaRechazada):
        semilla.revisar_base_antes_de_migrar(Tunel())
    Tunel.url = make_url("postgresql+psycopg://u:p@localhost:5432/crimson_e2e")
    semilla.revisar_base_antes_de_migrar(Tunel())  # con marca de pruebas: pasa
    semilla.revisar_base_antes_de_migrar(real)  # SQLite: pasa


def test_tienda_e2e_local_rechaza_railway_antes_de_limpiar_y_sin_migrar(monkeypatch, tmp_path):
    import os
    import sys

    from alembic import command
    from scripts import tienda_e2e_local

    llamadas = []
    monkeypatch.setattr(command, "upgrade", lambda *a, **k: llamadas.append(a))
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("E2E_DATABASE_URL", "postgresql://u:p@localhost:15432/railway")
    for salida in ("stdout", "stderr"):  # la captura de pytest no siempre trae reconfigure()
        monkeypatch.setattr(sys, salida, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    antes = dict(os.environ)
    assert tienda_e2e_local.main(["--solo-preparar", "--datos", str(tmp_path / "datos")]) == 2
    assert llamadas == []
    assert dict(os.environ) == antes  # no alcanzo a limpiar ni a cambiar el entorno


def test_forzar_solo_en_una_base_de_pruebas(db_session, monkeypatch):
    monkeypatch.setattr(semilla, "base_de_pruebas", lambda url: False)
    with pytest.raises(semilla.SemillaRechazada):
        semilla.sembrar(db_session, forzar=True)
