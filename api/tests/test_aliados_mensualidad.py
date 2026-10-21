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


def _correo_ok(monkeypatch, salida: list | None = None):
    """El proveedor de correo acepta todo (y el PDF existe aunque el equipo no tenga WeasyPrint)."""
    from app.services import documents_send, mail

    def deliver(m, files=None):
        m.status, m.provider_id = "enviado", f"re_{m.id}"
        if salida is not None:
            salida.append((m, [n for n, _ in (files or [])]))

    monkeypatch.setattr(mail, "deliver", deliver)
    monkeypatch.setattr(documents_send, "render_pdf", lambda html: b"%PDF-1.7 prueba")


def test_factura_de_mensualidad_y_suscripcion(client, auth, db_session, monkeypatch):
    """Plan 1.4: la primera mensualidad queda por aprobar (borrador que el aliado no ve); "Aprobar y enviar" la manda
    con PDF y enlace, el aliado la ve "por pagar" y la paga en linea; la segunda sale sola."""
    from app.models import AllyCompany, Invoice, Recurrence

    salida: list = []
    _correo_ok(monkeypatch, salida)
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000001", "email": "pagos@aliado-uno.ejemplo.com"}).json()["id"]
    h = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    pid = _servicio(client)
    plan = _plan(client, a, pid).json()["plan"]
    assert plan["first_run_requires_approval"] is True and plan["auto_send"] is True
    comp = db_session.get(AllyCompany, a)
    r = client.post(f"/recurrences/{comp.recurrence_id}/run")
    assert r.status_code == 201, r.text
    assert r.json()["send_status"] == "por_aprobar" and r.json()["sent"] is False
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.external_order == "MENSUALIDAD-PLATAFORMA" and inv.customer_id == comp.customer_id
    # la primera espera la aprobacion: sigue "creado", sin enviar, y el aliado no la ve ni la descarga
    assert inv.status == "creado" and inv.sent_at is None
    assert not [m for m, _ in salida if m.to == "pagos@aliado-uno.ejemplo.com"]
    assert client.get("/aliado/suscripcion", headers=h).json()["facturas"] == []
    assert inv.id not in {f["id"] for f in client.get("/aliado/facturas", headers=h).json()}
    assert client.get(f"/aliado/facturas/{inv.id}/pdf", headers=h).status_code == 404
    assert client.post(f"/aliado/facturas/{inv.id}/pagar", headers=h).status_code == 404
    inv.due_date, inv.balance = date.today() - timedelta(days=3), inv.total
    db_session.commit()
    assert client.get("/aliado/suscripcion", headers=h).json()["estado"] == "al_dia"  # un borrador no le cuenta como atraso
    inv.due_date = date.today() + timedelta(days=15)
    db_session.commit()
    # Aprobaciones -> "Mensualidades por aprobar" con su accion
    sec = next(x for x in client.get("/approvals", params={"limit": 50}).json()["sections"] if x["key"] == "mensualidades_por_aprobar")
    item = next(i for i in sec["items"] if i["id"] == inv.id)
    assert item["action"]["path"] == f"/recurrences/{comp.recurrence_id}/approve-send" and item["action"]["label"] == "Aprobar y enviar"
    ok = client.post(item["action"]["path"]).json()
    assert ok["sent"] is True and ok["status"] == "enviada"
    m, files = [x for x in salida if x[0].to == "pagos@aliado-uno.ejemplo.com"][-1]
    assert "Aviso de cobro" in m.subject and "/pagar/" in m.html and files == [f"{inv.number}.pdf"]
    db_session.expire_all()
    inv = db_session.get(Invoice, inv.id)
    assert inv.status == "enviada" and inv.sent_at is not None
    assert db_session.get(Recurrence, comp.recurrence_id).first_run_requires_approval is False
    assert inv.id not in {i["id"] for i in next(x for x in client.get("/approvals").json()["sections"] if x["key"] == "mensualidades_por_aprobar")["items"]}
    # el aliado la ve por pagar, con su estado real, y la paga en linea
    sus = client.get("/aliado/suscripcion", headers=h).json()
    f = next(x for x in sus["facturas"] if x["id"] == inv.id)
    assert sus["estado"] == "al_dia" and sus["plan"]["frecuencia"] == "mensual" and f["estado"] == "por_pagar" and f["pagable"] is True
    pago = client.post(f"/aliado/facturas/{inv.id}/pagar", headers=h)
    assert pago.status_code == 200 and "/pagar/" in pago.json()["url"]
    assert client.get(f"/aliado/facturas/{inv.id}/pdf", headers=h).status_code == 200
    # facturas de otros clientes y anuladas: nunca
    otra = db_session.scalars(select(Invoice).where(Invoice.customer_id != comp.customer_id)).first()
    if otra is None:
        otro = client.get("/customers").json()["items"][0]
        q = client.post("/invoices", json={"customer_id": otro["id"], "lines": [{"name": "Algo", "quantity": 1, "unit_price": 1000}]})
        otra = db_session.get(Invoice, q.json()["id"])
    facturas = {x["id"] for x in client.get("/aliado/facturas", headers=h).json()}
    assert inv.id in facturas and otra.id not in facturas
    assert client.get(f"/aliado/facturas/{otra.id}/pdf", headers=h).status_code == 404
    assert client.post(f"/aliado/facturas/{otra.id}/pagar", headers=h).status_code == 404
    # vencida: saldo con fecha de vencimiento pasada
    inv.due_date, inv.balance = date.today() - timedelta(days=10), inv.total
    db_session.commit()
    sus = client.get("/aliado/suscripcion", headers=h).json()
    assert sus["estado"] == "vencida" and sus["vencido"]["dias"] == 10
    assert next(x for x in sus["facturas"] if x["id"] == inv.id)["estado"] == "vencida"
    assert client.get(f"/allies/{a}/plan").json()["estado"] == "vencida"
    # el segundo mes sale solo (sin pasar por Aprobaciones)
    r2 = client.post(f"/recurrences/{comp.recurrence_id}/run").json()
    assert r2["send_status"] == "enviada" and r2["sent"] is True
    db_session.expire_all()
    assert db_session.get(Invoice, r2["invoice_id"]).status == "enviada"
    out = client.get(f"/allies/{a}/plan").json()
    assert out["plan"]["last_send_status"] == "enviada" and out["plan"]["last_invoice_id"] == r2["invoice_id"]


def test_plan_usa_el_servicio_por_defecto(client, auth, db_session):
    """Semilla (plan 1.4): "Mensualidad de la plataforma" con CABYS queda por defecto en Ajustes -> Aliados."""
    from app.models import AllyCompany, Product, Recurrence

    cfg = client.get("/allies/config").json()
    prod = db_session.get(Product, cfg["plan_product_id"])
    assert prod.code == "SRV-PLATAFORMA" and prod.item_type == "servicio" and len(prod.cabys_code) == 13 and not prod.show_on_web
    a = client.post("/allies", json={"name": "Aliado Dos S.A.", "tax_id": "3-101-000002"}).json()["id"]
    body = {"amount": 40000, "currency": "CRC", "mode": "fija", "next_date": date.today().isoformat(), "crear_cliente": True}
    assert client.put(f"/allies/{a}/plan", json=body).status_code == 200
    rec = db_session.get(Recurrence, db_session.get(AllyCompany, a).recurrence_id)
    assert rec.template["lines"][0]["product_id"] == prod.id and rec.first_run_requires_approval is True
    # actualizar el plan no vuelve a pedir aprobacion si ya se aprobo (otra fecha); otro monto si
    rec.first_run_requires_approval = False
    db_session.commit()
    otra_fecha = (date.today() + timedelta(days=5)).isoformat()
    assert client.put(f"/allies/{a}/plan", json={**body, "next_date": otra_fecha}).json()["plan"]["first_run_requires_approval"] is False
    assert client.put(f"/allies/{a}/plan", json={**body, "amount": 45000}).json()["plan"]["first_run_requires_approval"] is True


def test_mensualidad_por_defecto_configurable(client, auth, db_session):
    """El precio lo fija Andres (sesion 0.11): por defecto fija por empresa y sin monto; queda en Ajustes -> Aliados y
    con eso arranca el formulario de cada plan nuevo."""
    from tests.test_session4 import _user_with_role

    cfg = client.get("/allies/config").json()
    assert cfg["plan_default"] == {"amount": None, "currency": "CRC", "mode": "fija"}
    assert cfg["plan_product"]["code"] == "SRV-PLATAFORMA" and len(cfg["plan_product"]["cabys_code"]) == 13
    out = client.put("/allies/config", json={"plan_default_amount": 35000, "plan_default_currency": "USD", "plan_default_mode": "por_usuario"})
    assert out.status_code == 200, out.text
    pd = out.json()["plan_default"]
    assert Decimal(str(pd["amount"])) == 35000 and pd["currency"] == "USD" and pd["mode"] == "por_usuario"
    a = client.post("/allies", json={"name": "Aliado Tres S.A.", "tax_id": "3-101-000003"}).json()["id"]
    plan = client.get(f"/allies/{a}/plan").json()
    assert plan["plan"] is None and plan["defaults"]["mode"] == "por_usuario" and Decimal(str(plan["defaults"]["amount"])) == 35000
    assert client.put("/allies/config", json={"plan_default_amount": None}).json()["plan_default"]["amount"] is None
    # cambiarla exige el permiso de la mensualidad
    pw = _user_with_role(db_session, auth["tenant"]["id"], "ventas.mensualidad@ejemplo.com", "ventas")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "ventas.mensualidad@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    r = client.put("/allies/config", json={"plan_default_amount": 1}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_ganancias_con_mensualidad(client, auth, db_session):
    from app.models import AllyCompany, Invoice

    s = escenario(client, dict(client.headers), db_session)
    pid = _servicio(client)
    assert _plan(client, s.A, pid).status_code == 200
    comp = db_session.get(AllyCompany, s.A)
    r = client.post(f"/recurrences/{comp.recurrence_id}/run")
    assert r.status_code == 201
    mes = date.today().strftime("%Y-%m")
    # el borrador "creado" que deja la recurrencia no se ve en suscripcion ni en facturas: tampoco se descuenta del neto
    g = client.get("/aliado/ganancias", headers=s.a_jefe).json()
    assert Decimal(str(g["totales"]["mensualidad"])) == 0
    assert all(Decimal(str(m["mensualidad"])) == 0 for m in g["por_mes"])
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    inv.status = "enviada"
    db_session.commit()
    g = client.get("/aliado/ganancias", headers=s.a_jefe).json()
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


def test_plan_revision_de_la_primera_y_cambio_de_monto(client, auth, db_session):
    """Plan 1.4: dejar que la mensualidad salga sola sin revisar es de quien envia facturas (contabilidad tiene
    aliados.mensualidad pero no sales.enviar), y cambiar el monto vuelve a pedir "Aprobar y enviar". Queda en la bitacora."""
    from app.models import AllyCompany, AuditLog, Recurrence
    from tests.test_session4 import _user_with_role

    a = client.post("/allies", json={"name": "Aliado Dos S.A.", "tax_id": "3-101-000002", "email": "pagos@aliado-dos.ejemplo.com"}).json()["id"]
    pid = _servicio(client)
    assert _plan(client, a, pid).json()["plan"]["first_run_requires_approval"] is True
    pw = _user_with_role(db_session, auth["tenant"]["id"], "conta.mensualidad@ejemplo.com", "contabilidad")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "conta.mensualidad@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    body = {"amount": 50000, "currency": "CRC", "mode": "fija", "next_date": date.today().isoformat(), "product_id": pid}
    assert client.put(f"/allies/{a}/plan", json={**body, "first_run_requires_approval": False}, headers=h).status_code == 403
    assert client.put(f"/allies/{a}/plan", json={**body, "first_run_requires_approval": True}, headers=h).status_code == 200
    # el admin la deja salir sola; despues sube el monto: la proxima vuelve a esperar la revision
    assert _plan(client, a, pid, first_run_requires_approval=False).json()["plan"]["first_run_requires_approval"] is False
    assert _plan(client, a, pid, amount=65000, first_run_requires_approval=False).json()["plan"]["first_run_requires_approval"] is True
    rec = db_session.get(Recurrence, db_session.get(AllyCompany, a).recurrence_id)
    assert rec.first_run_requires_approval is True
    logs = db_session.scalars(
        select(AuditLog).where(AuditLog.entity == "ally_company", AuditLog.entity_id == a, AuditLog.action == "plan").order_by(AuditLog.id)
    ).all()
    assert logs[-1].diff["envio"] == {
        "antes": {"auto_send": True, "first_run_requires_approval": False},
        "despues": {"auto_send": True, "first_run_requires_approval": True},
    }
