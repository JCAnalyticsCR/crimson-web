"""Auditoria de margenes (oct 2026): venta sin IVA en proyectos, costo planificado con mano de obra del
levantamiento, comision sin IVA, P = 0 bloqueado y C desconocido marcado como provisional."""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import Commission, Product, Project
from tests.test_session6 import _login, _uid

D = Decimal


def _cust(client):
    return client.get("/customers").json()["items"][0]


def _producto_con_costo(client, db_session, costo=30000, code="CAM-MRG"):
    prod = client.post("/products", json={"name": f"Cámara {code}", "code": code, "price": 0}).json()
    obj = db_session.get(Product, prod["id"])
    obj.cost, obj.cost_currency = D(costo), "CRC"
    db_session.commit()
    return prod


def _quote(client, lines, **extra):
    r = client.post("/quotes", json={"customer_id": _cust(client)["id"], "lines": lines, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 1. precio del proyecto = venta sin IVA ----------
def test_precio_del_proyecto_es_venta_sin_iva(client, auth, db_session):
    prod = _producto_con_costo(client, db_session)
    q = _quote(
        client,
        [
            {"product_id": prod["id"], "name": prod["name"], "quantity": 2, "unit_price": 50000, "tax_rate": 13},
            {"name": "Instalación", "quantity": 1, "unit_price": 20000, "tax_rate": 13},
            {"name": "Firewall del cliente", "quantity": 1, "unit_price": 500000, "tax_rate": 13, "treatment": "aportado"},
        ],
        discount_type="percent",
        discount_value=10,
    )
    # subtotal 120 000 - 10 % = 108 000 sin IVA; con IVA 122 040
    assert D(str(q["total"])) == D("122040")
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["price"])) == D("108000")
    assert D(str(eco["cost_planned"])) == D("60000")  # sin levantamiento: solo productos, como antes
    assert D(str(eco["margin_planned"])) == D("44.4")  # (108 000 - 60 000) / 108 000

    cust = _cust(client)
    client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "terminado", "cost_labor": 8000, "customer_id": cust["id"]})
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["profit"])) == D("100000")  # 108 000 - 8 000, no 122 040 - 8 000
    rent = client.get("/reports/rentabilidad").json()
    assert D(str(rent["totals"]["Venta sin IVA"])) == D("108000") and D(str(rent["totals"]["Utilidad"])) == D("100000")
    tipos = client.get("/reports/rentabilidad_tipo").json()
    assert D(str(tipos["totals"]["Venta sin IVA"])) == D("108000")
    assert D(str(client.get("/dashboard").json()["gerencia"]["profit_month"])) == D("100000")


# ---------- 2. costo planificado con mano de obra, transporte y viaticos ----------
def test_costo_planificado_incluye_mano_de_obra_del_levantamiento(client, auth, db_session):
    prod = _producto_con_costo(client, db_session, code="CAM-LEV")
    s = client.post(
        "/surveys",
        json={
            "kind": "cctv",
            "customer_id": _cust(client)["id"],
            "labor": {"tecnico": {"people": 2, "days": 3}},
            "items": [{"product_id": prod["id"], "name": prod["name"], "quantity": 2, "unit": "Unid", "kind": "equipo"}],
        },
    ).json()
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    mano = D(str(cost["labor"]["cost"])) + D(str(cost["labor"]["travel"]))
    assert mano == D("150000") + D("35000")  # 2 x 3 x 25 000 + transporte por defecto
    q = client.post(f"/surveys/{s['id']}/quote", json={"skip_review": True}).json()
    pr = client.post(f"/quotes/{q['quote_id']}/project", json={}).json()
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["cost_planned"])) == D("60000") + mano == D(str(cost["cost_total"]))


# ---------- 3. comision sobre la venta sin IVA ----------
def test_comision_venta_se_calcula_sin_iva_y_no_toca_las_viejas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    v = _login(client, db_session, tid, "comi-iva@ejemplo.com", "ventas", admin_h)
    uid = _uid(db_session, "comi-iva@ejemplo.com")
    client.post("/commissions/rules", json={"user_id": uid, "name": "Vendedor", "base": "venta", "percent": 5})
    inv = client.post(
        "/invoices", headers=v, json={"customer_id": _cust(client)["id"], "lines": [{"name": "CCTV", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}]}
    ).json()
    assert D(str(inv["total"])) == D("1130000")

    # una comision devengada antes del cambio (sobre el monto con IVA) queda como estaba
    vieja = Commission(tenant_id=tid, user_id=uid, invoice_id=inv["id"], base="venta", base_amount=D("113000"), percent=D(5), amount=D("5650"), currency="CRC")
    vieja.earned_on = date.today()
    db_session.add(vieja)
    db_session.commit()

    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 565000})
    rows = client.get("/commissions").json()["rows"]
    nueva = next(r for r in rows if r["id"] != vieja.id)
    assert D(str(nueva["base_amount"])) == D("500000.00")  # la mitad cobrada, sin su IVA
    assert D(str(nueva["amount"])) == D("25000.00")
    antigua = next(r for r in rows if r["id"] == vieja.id)
    assert D(str(antigua["base_amount"])) == D("113000") and D(str(antigua["amount"])) == D("5650")


# ---------- 4. P = 0 ----------
def test_propuesta_sin_precio_de_venta_no_sale(client, auth):
    q = _quote(
        client,
        [
            {"name": "Equipo del cliente", "quantity": 1, "unit_price": 500000, "tax_rate": 13, "treatment": "aportado"},
            {"name": "Revisión", "quantity": 1, "unit_price": 0, "tax_rate": 13, "treatment": "cortesia"},
            {"name": "Obra civil", "quantity": 1, "unit_price": 0, "tax_rate": 13, "treatment": "excluido"},
        ],
    )
    assert D(str(q["total"])) == 0
    for r in (
        client.post(f"/quotes/{q['id']}/send"),
        client.post(f"/quotes/{q['id']}/convert"),
        client.post(f"/quotes/{q['id']}/email", json={"to": "x@example.com"}),
        client.post(f"/quotes/{q['id']}/project", json={}),
    ):
        assert r.status_code == 409 and "La propuesta no tiene precio de venta" in r.json()["detail"], r.text


# ---------- 5. C desconocido no es 0 ----------
def test_costeo_con_costos_faltantes_es_provisional(client, auth):
    s = client.post(
        "/surveys",
        json={"kind": "cctv", "customer_id": _cust(client)["id"], "items": [{"name": "Sensor sin costo", "quantity": 2}, {"name": "Cable", "quantity": 10}]},
    ).json()
    c = client.get(f"/surveys/{s['id']}/costing").json()
    assert c["cost_complete"] is False and c["margin_provisional"] is True
    assert len(c["missing_cost"]) == 2 and "margin_pct" in c and "cost_total" in c  # el contrato anterior sigue igual
    items = {x["name"]: x["item_id"] for x in c["lines"]}
    c2 = client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": i, "unit_cost": 1000} for i in items.values()]).json()
    assert c2["cost_complete"] is True and c2["margin_provisional"] is False and c2["missing_cost"] == []


# ---------- migracion de datos ----------
def _migracion():
    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("margenes_*.py"))
    spec = importlib.util.spec_from_file_location("margenes_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migracion_recalcula_precio_desde_la_cotizacion(client, auth, db_session):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 100000, "tax_rate": 13}])
    con_cot = client.post(f"/quotes/{q['id']}/project", json={}).json()
    sin_cot = client.post("/projects", json={"name": "Suelto", "customer_id": _cust(client)["id"]}).json()
    # como quedaron antes del cambio: precio con IVA
    db_session.get(Project, con_cot["id"]).price = D("113000")
    db_session.get(Project, sin_cot["id"]).price = D("777")
    db_session.commit()

    mod = _migracion()
    conn = db_session.connection()
    mod.op = Operations(MigrationContext.configure(conn))
    mod.upgrade()
    db_session.expire_all()
    assert D(str(db_session.get(Project, con_cot["id"]).price)) == D("100000")
    assert D(str(db_session.get(Project, sin_cot["id"]).price)) == D("777")  # sin cotizacion: queda igual
    mod.downgrade()
    db_session.expire_all()
    assert D(str(db_session.get(Project, con_cot["id"]).price)) == D("113000")
