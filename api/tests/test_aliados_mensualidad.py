"""Mensualidad del aliado y lo que recibe (aliado_final §12.5, M1-M5). El dinero que recibe el aliado es del frente
economia (services/commissions); aqui se prueba el contrato que expone el portal y la mensualidad, que si es de este
frente."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from tests.aliados_helpers import aceptar, escenario, usuario


def _servicio(client) -> int:
    r = client.post(
        "/products",
        json={
            "name": "Mensualidad de la plataforma",
            "code": "SRV-PLAT",
            "item_type": "servicio",
            "price": 0,
            "currency": "CRC",
            "cabys_code": "8314100000000",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _plan(client, ally_id, pid, **kw):
    body = {"amount": 50000, "currency": "CRC", "mode": "fija", "next_date": date.today().isoformat(), "product_id": pid, "crear_cliente": True, **kw}
    return client.put(f"/allies/{ally_id}/plan", json=body)


def test_plan_crea_cliente_y_recurrencia(client, auth, db_session):
    from app.models import AllyCompany, Customer, Recurrence

    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000001", "email": "pagos@aliado-uno.ejemplo.com"}).json()["id"]
    pid = _servicio(client)
    sin_cabys = client.post("/products", json={"name": "Servicio sin cabys", "code": "SRV-X", "item_type": "servicio", "price": 0, "currency": "CRC"}).json()[
        "id"
    ]
    assert _plan(client, a, sin_cabys).status_code == 422
    assert (
        client.put(f"/allies/{a}/plan", json={"amount": 50000, "currency": "CRC", "mode": "fija", "next_date": "2026-11-01", "product_id": pid}).status_code
        == 422
    )
    r = _plan(client, a, pid)
    assert r.status_code == 200, r.text
    out = r.json()
    comp = db_session.get(AllyCompany, a)
    cust = db_session.get(Customer, comp.customer_id)
    rec = db_session.get(Recurrence, comp.recurrence_id)
    assert cust.name == "Aliado Uno S.A." and cust.id_number == "3-101-000001" and cust.id_type == "juridica"
    assert rec.kind == "factura" and rec.customer_id == cust.id and rec.auto_charge is False and rec.frequency == "mensual"
    assert Decimal(rec.template["lines"][0]["quantity"]) == 1 and rec.template["external_order"] == "MENSUALIDAD-PLATAFORMA"
    assert out["plan"]["mode"] == "fija" and out["customer"]["id"] == cust.id
    # actualizar no crea otra recurrencia
    assert _plan(client, a, pid, amount=60000).status_code == 200
    assert db_session.scalars(select(Recurrence).where(Recurrence.customer_id == cust.id)).all() == [rec]


def test_por_usuario_recalcula(client, auth, db_session):
    from app.models import AllyCompany, Recurrence

    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A."}).json()["id"]
    pid = _servicio(client)
    usuario(client, admin_h, a, "uno@aliado-uno.ejemplo.com", "aliado")
    usuario(client, admin_h, a, "dos@aliado-uno.ejemplo.com", "tecnico_aliado")

    def cantidad():
        db_session.expire_all()
        rec = db_session.get(Recurrence, db_session.get(AllyCompany, a).recurrence_id)
        return Decimal(rec.template["lines"][0]["quantity"])

    assert _plan(client, a, pid, mode="por_usuario").status_code == 200
    assert cantidad() == 2
    dos = next(u for u in client.get(f"/allies/{a}/users").json()["users"] if u["email"] == "dos@aliado-uno.ejemplo.com")
    assert client.patch(f"/allies/{a}/users/{dos['id']}", json={"active": False}).status_code == 200
    assert cantidad() == 1
    inv = client.post(f"/allies/{a}/invitations", json={"email": "tres@aliado-uno.ejemplo.com", "role": "tecnico_aliado"}).json()
    aceptar(client, inv["link"], "Tres Persona")
    assert cantidad() == 2


def test_factura_de_mensualidad_y_suscripcion(client, auth, db_session):
    from app.models import AllyCompany, Invoice

    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000001"}).json()["id"]
    h = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    pid = _servicio(client)
    assert _plan(client, a, pid).status_code == 200
    comp = db_session.get(AllyCompany, a)
    r = client.post(f"/recurrences/{comp.recurrence_id}/run")
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.external_order == "MENSUALIDAD-PLATAFORMA" and inv.customer_id == comp.customer_id
    # recien emitida por la recurrencia queda "creado" (sin enviar): el aliado todavia no la ve ni la descarga
    assert inv.status == "creado"
    assert client.get("/aliado/suscripcion", headers=h).json()["facturas"] == []
    assert inv.id not in {f["id"] for f in client.get("/aliado/facturas", headers=h).json()}
    assert client.get(f"/aliado/facturas/{inv.id}/pdf", headers=h).status_code == 404
    inv.due_date, inv.balance = date.today() - timedelta(days=3), inv.total
    db_session.commit()
    assert client.get("/aliado/suscripcion", headers=h).json()["estado"] == "al_dia"  # un borrador no le cuenta como atraso
    inv.status = "enviada"
    inv.due_date, inv.balance = date.today() + timedelta(days=15), inv.total
    db_session.commit()
    sus = client.get("/aliado/suscripcion", headers=h).json()
    assert [f["id"] for f in sus["facturas"]] == [inv.id] and sus["estado"] == "al_dia" and sus["plan"]["frecuencia"] == "mensual"
    # facturas de otros clientes y anuladas: nunca
    otra = db_session.scalars(select(Invoice).where(Invoice.customer_id != comp.customer_id)).first()
    if otra is None:
        otro = client.get("/customers").json()["items"][0]
        q = client.post("/invoices", json={"customer_id": otro["id"], "lines": [{"name": "Algo", "quantity": 1, "unit_price": 1000}]})
        otra = db_session.get(Invoice, q.json()["id"])
    facturas = {f["id"] for f in client.get("/aliado/facturas", headers=h).json()}
    assert inv.id in facturas and otra.id not in facturas
    assert client.get(f"/aliado/facturas/{otra.id}/pdf", headers=h).status_code == 404
    assert client.get(f"/aliado/facturas/{inv.id}/pdf", headers=h).status_code == 200
    # vencida: saldo con fecha de vencimiento pasada
    inv.due_date, inv.balance = date.today() - timedelta(days=10), inv.total
    db_session.commit()
    sus = client.get("/aliado/suscripcion", headers=h).json()
    assert sus["estado"] == "vencida" and sus["vencido"]["dias"] == 10
    assert client.get(f"/allies/{a}/plan").json()["estado"] == "vencida"


def test_ganancias_con_mensualidad(client, auth, db_session):
    from app.models import AllyCompany

    s = escenario(client, dict(client.headers), db_session)
    pid = _servicio(client)
    assert _plan(client, s.A, pid).status_code == 200
    comp = db_session.get(AllyCompany, s.A)
    assert client.post(f"/recurrences/{comp.recurrence_id}/run").status_code == 201
    g = client.get("/aliado/ganancias", headers=s.a_jefe).json()
    mes = date.today().strftime("%Y-%m")
    fila = next(m for m in g["por_mes"] if m["mes"] == mes)
    assert Decimal(str(fila["mensualidad"])) == 50000 and Decimal(str(fila["neto"])) == Decimal(str(fila["devengado"])) - 50000
    assert Decimal(str(g["totales"]["mensualidad"])) == 50000 and "Neto" in g["nota"]
    gb = client.get("/aliado/ganancias", headers=s.b_jefe).json()
    assert Decimal(str(gb["totales"]["mensualidad"])) == 0
    assert client.get(f"/aliado/ganancias/proyectos/{s.P2}", headers=s.a_jefe).status_code == 404
    assert client.get(f"/aliado/ganancias/proyectos/{s.P1}", headers=s.a_jefe).status_code == 200


def test_ganancias_sin_economia(client, auth, db_session, monkeypatch):
    import importlib.util

    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A."}).json()["id"]
    h = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "app.services.commissions" else real(name, *a, **k))
    assert client.get("/aliado/ganancias", headers=h).status_code == 503
    assert client.get("/auth/me", headers=h).json()["ally_company"]["features"]["ganancias"] is False
    assert client.get("/aliado/inicio", headers=h).json()["dinero"] is None
