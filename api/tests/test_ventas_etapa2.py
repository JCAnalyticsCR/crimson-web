"""Ventas etapa 2: jefe de ventas, tope de descuento por vendedor, cartera con dueno y traspaso de cartera.

Cubre:
- permisos del jefe (ve todo lo comercial, nunca el costo; asignar ventas segun Ajustes -> Ventas)
- topes del jefe: aprueba hasta su descuento y su monto; lo que pase lo aprueba el admin
- tope de descuento propio de un vendedor (Ajustes -> Usuarios), y que solo el admin lo cambia
- visibilidad de la cartera, bloqueo de duplicados de otro vendedor y tomar un cliente sin dueno
- cambiar el vendedor de la cuenta (bitacora + aviso), traspaso con vista previa, desactivar con cartera
- relleno de la migracion y la sugerencia de "ventas por asignar"

Repo publico: solo correos y nombres de prueba.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.deps import ROLE_PERMISSIONS
from app.models import AuditLog, Commission, Customer, EmailOutbox, Invoice, Opportunity, Quote, TenantUser, User
from tests.aliados_helpers import interno, uid


def _persona(client, db_session, tid: int, email: str, role: str, nombre: str) -> tuple[dict, int]:
    h = interno(client, db_session, tid, email, role)
    u = db_session.scalar(select(User).where(User.email == email))
    u.full_name = nombre
    db_session.commit()
    return h, u.id


def _producto(client, code: str, price: float) -> int:
    r = client.post("/products", json={"name": f"Equipo {code}", "code": code, "price": price, "currency": "CRC"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _cliente(client, h: dict, nombre: str, **extra) -> dict:
    r = client.post("/customers", json={"name": nombre, "id_type": "fisica", **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _cotizar(client, h: dict, cid: int, pid: int, qty: int = 1, pct: float = 0) -> dict:
    body = {"customer_id": cid, "lines": [{"product_id": pid, "name": "Equipo", "quantity": qty, "discount_type": "percent", "discount_value": pct, "tax_rate": 13}]}
    r = client.post("/quotes", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- Jefe de ventas: permisos ----------
def test_jefe_ve_todo_lo_comercial_sin_costos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    perms = ROLE_PERMISSIONS["jefe_ventas"]
    assert "costos" not in perms["catalog"] and "*" not in perms
    assert "aprobar" not in perms["sales"] and "descuento_libre" not in perms["sales"] and "anular" not in perms["sales"]
    assert "jefe_ventas" in client.get("/settings/users").json()["roles"]  # se elige en Usuarios

    vh, _ = _persona(client, db_session, tid, "vend.a@ejemplo.com", "ventas", "Vendedora A")
    jh, _ = _persona(client, db_session, tid, "jefe@ejemplo.com", "jefe_ventas", "Jefe Uno")
    r = client.post("/products", json={"name": "Camara costo", "code": "JC-1", "price": 1000, "currency": "CRC", "cost": 500, "cost_currency": "CRC"})
    pid = r.json()["id"]
    c = _cliente(client, vh, "Cliente de A", email="cliente.a@ejemplo.com")
    q = _cotizar(client, vh, c["id"], pid)
    # el jefe ve la cotizacion y el cliente de la vendedora; el producto sin costo
    assert any(x["id"] == q["id"] for x in client.get("/quotes", headers=jh).json()["items"])
    assert client.get(f"/quotes/{q['id']}", headers=jh).status_code == 200
    assert client.get(f"/customers/{c['id']}", headers=jh).status_code == 200
    pr = client.get(f"/products/{pid}", headers=jh).json()
    assert pr["cost"] is None and pr["margin_pct"] is None
    assert client.get("/opportunities/board", headers=jh).status_code == 200

    # asignar ventas por asignar: por defecto solo el admin; con el ajuste, tambien el jefe
    assert client.get("/sales/unassigned", headers=jh).status_code == 403
    ventas = client.get("/settings").json().get("ventas") or {}
    r = client.put("/settings", json={"ventas": {**ventas, "quienes_asignan_ventas": ["admin", "jefe_ventas"]}})
    assert r.status_code == 200, r.text
    assert client.get("/sales/unassigned", headers=jh).status_code == 200
    # el jefe no edita Ajustes ni los roles
    assert client.put("/settings", json={"ventas": {"quienes_asignan_ventas": ["admin"]}}, headers=jh).status_code == 403
    assert "jefe_ventas" not in [r["code"] for r in client.get("/settings/roles").json()["roles"] if r.get("editable")]


def test_ajustes_de_ventas_validan_y_dejan_bitacora(client, auth):
    assert client.put("/settings", json={"ventas": {"jefe_ventas_tope_descuento_pct": 150}}).status_code == 422
    assert client.put("/settings", json={"ventas": {"quienes_asignan_ventas": ["ventas"]}}).status_code == 422
    r = client.put("/settings", json={"ventas": {"jefe_ventas_tope_descuento_pct": 20, "jefe_ventas_tope_monto": 5_000_000, "quienes_asignan_ventas": ["jefe_ventas"]}})
    assert r.status_code == 200, r.text
    v = r.json()["ventas"]
    assert v["quienes_asignan_ventas"] == ["admin", "jefe_ventas"]  # el admin siempre
    assert v["jefe_ventas_tope_descuento_pct"] == 20


# ---------- Topes del jefe ----------
def test_jefe_aprueba_hasta_sus_topes_y_lo_demas_va_al_admin(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _persona(client, db_session, tid, "vend.b@ejemplo.com", "ventas", "Vendedor B")
    jh, jid = _persona(client, db_session, tid, "jefe.b@ejemplo.com", "jefe_ventas", "Jefa B")
    barato = _producto(client, "JB-1", 10_000)
    caro = _producto(client, "JB-2", 6_000_000)
    c = _cliente(client, vh, "Cliente B", email="cliente.b@ejemplo.com")

    # 12 %: pasa el 10 % general, cabe en el 15 % del jefe
    q1 = _cotizar(client, vh, c["id"], barato, pct=12)
    assert q1["status"] == "por_aprobar"
    assert client.get(f"/quotes/{q1['id']}", headers=jh).json()["team_approval"] == {"can": True, "reason": None}
    sec = next(s for s in client.get("/approvals", headers=jh).json()["sections"] if s["key"] == "cotizaciones_equipo")
    assert [i["id"] for i in sec["items"]] == [q1["id"]] and sec["items"][0]["can_approve"] is True
    # el admin tiene su propia seccion, no la del equipo
    assert "cotizaciones_equipo" not in [s["key"] for s in client.get("/approvals").json()["sections"]]
    r = client.post(f"/quotes/{q1['id']}/approve", headers=jh)
    assert r.status_code == 200 and r.json()["status"] == "creado", r.text
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "approve_quote", AuditLog.entity_id == q1["id"]))
    assert log.user_id == jid and log.diff.get("por") == "jefe_ventas"

    # 20 %: pasa el tope del jefe -> 403 y lo aprueba el admin
    q2 = _cotizar(client, vh, c["id"], barato, pct=20)
    assert q2["status"] == "por_aprobar"
    r = client.post(f"/quotes/{q2['id']}/approve", headers=jh)
    assert r.status_code == 403 and "administración" in r.json()["detail"]
    assert client.post(f"/quotes/{q2['id']}/reject", json={"reason": "No procede"}, headers=jh).status_code == 403
    assert client.post(f"/quotes/{q2['id']}/approve").status_code == 200

    # monto: 3 millones (sobre el tope general de 2) lo aprueba el jefe; 12 millones (sobre sus 10) no
    q3 = _cotizar(client, vh, c["id"], barato, qty=300)
    assert q3["status"] == "por_aprobar"
    assert client.post(f"/quotes/{q3['id']}/approve", headers=jh).status_code == 200
    q4 = _cotizar(client, vh, c["id"], caro, qty=2)
    assert q4["status"] == "por_aprobar"
    assert client.post(f"/quotes/{q4['id']}/approve", headers=jh).status_code == 403
    # una cotizacion sobre su tope de monto ni aparece en su bandeja
    sec = next(s for s in client.get("/approvals", headers=jh).json()["sections"] if s["key"] == "cotizaciones_equipo")
    assert q4["id"] not in [i["id"] for i in sec["items"]]

    # lo suyo: hasta su tope no se retiene; pasado el tope se retiene y no se lo aprueba el mismo
    jc = client.post("/customers", json={"name": "Cliente del jefe", "id_type": "fisica"}, headers=jh).json()
    assert _cotizar(client, jh, jc["id"], barato, pct=12)["status"] == "creado"
    propia = _cotizar(client, jh, jc["id"], barato, pct=18)
    assert propia["status"] == "por_aprobar"
    assert client.post(f"/quotes/{propia['id']}/approve", headers=jh).status_code == 403

    # el vendedor sigue sin aprobar nada
    assert client.post(f"/quotes/{q4['id']}/approve", headers=vh).status_code == 403
    # el inicio del jefe trae las aprobaciones del equipo y el embudo del equipo
    dash = client.get("/dashboard", headers=jh).json()
    assert dash["equipo"] is not None and "embudo" in dash["equipo"] and "por_vendedor" in dash["equipo"]
    assert client.get("/dashboard", headers=vh).json()["equipo"] is None
    assert vid


# ---------- Tope por vendedor ----------
def test_tope_de_descuento_propio_del_vendedor(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _persona(client, db_session, tid, "vend.tope@ejemplo.com", "ventas", "Vendedor Tope")
    pid = _producto(client, "TP-1", 10_000)
    c = _cliente(client, vh, "Cliente Tope", email="cliente.tope@ejemplo.com")
    assert _cotizar(client, vh, c["id"], pid, pct=15)["status"] == "por_aprobar"

    # solo el admin lo cambia
    assert client.patch(f"/settings/users/{vid}", json={"discount_limit_pct": 50}, headers=vh).status_code == 403
    assert client.patch(f"/settings/users/{vid}", json={"discount_limit_pct": 150}).status_code == 422
    assert client.patch(f"/settings/users/{vid}", json={"discount_limit_pct": 20}).status_code == 200
    fila = next(u for u in client.get("/settings/users").json()["users"] if u["id"] == vid)
    assert float(fila["discount_limit_pct"]) == 20
    assert client.get("/sales/discount-limit", headers=vh).json()["limit"] in (20, "20", "20.00")
    assert _cotizar(client, vh, c["id"], pid, pct=15)["status"] == "creado"
    assert _cotizar(client, vh, c["id"], pid, pct=25)["status"] == "por_aprobar"
    assert db_session.scalar(select(AuditLog).where(AuditLog.entity == "user", AuditLog.entity_id == vid, AuditLog.reason == "Tope de descuento del vendedor"))

    # vacio vuelve al tope general
    assert client.patch(f"/settings/users/{vid}", json={"discount_limit_pct": ""}).status_code == 200
    assert db_session.get(TenantUser, db_session.scalar(select(TenantUser.id).where(TenantUser.user_id == vid))).discount_limit_pct is None
    assert _cotizar(client, vh, c["id"], pid, pct=15)["status"] == "por_aprobar"


# ---------- Cartera con dueno ----------
def test_cada_vendedor_ve_su_cartera_y_no_duplica_la_ajena(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ah, aid = _persona(client, db_session, tid, "vend.ca@ejemplo.com", "ventas", "Ana Cartera")
    bh, _ = _persona(client, db_session, tid, "vend.cb@ejemplo.com", "ventas", "Beto Cartera")
    jh, _ = _persona(client, db_session, tid, "jefe.c@ejemplo.com", "jefe_ventas", "Jefe Cartera")
    kh, _ = _persona(client, db_session, tid, "conta.c@ejemplo.com", "contabilidad", "Conta Cartera")
    c = _cliente(client, ah, "Empresa de Ana", id_number="1-1111-1111", email="ana.cliente@ejemplo.com", phone="8888-7777")
    assert c["account_owner_id"] == aid and c["account_owner_name"] == "Ana Cartera"

    assert [x["id"] for x in client.get("/customers", headers=ah).json()["items"]] == [c["id"]]
    assert c["id"] not in [x["id"] for x in client.get("/customers", headers=bh).json()["items"]]
    assert client.get(f"/customers/{c['id']}", headers=bh).status_code == 404
    assert client.get(f"/customers/{c['id']}/overview", headers=bh).status_code == 404
    for h in (jh, kh, None):
        assert c["id"] in [x["id"] for x in client.get("/customers", headers=h).json()["items"]]
    # Beto no lo puede crear otra vez: ni por cedula, ni por correo, ni por telefono
    for extra in ({"id_number": "111111111"}, {"email": "ANA.CLIENTE@ejemplo.com"}, {"phone": "+506 8888 7777"}):
        r = client.post("/customers", json={"name": "Copia", "id_type": "fisica", **extra}, headers=bh)
        assert r.status_code == 409 and r.json()["detail"] == "Este cliente ya es atendido por Ana Cartera", (extra, r.text)
    # ni cotizarle
    r = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 13}]}, headers=bh)
    assert r.status_code == 422 and "Ana Cartera" in r.json()["detail"]

    # un cliente sin dueno: no sale en la lista ni por nombre, solo por cedula o correo exactos, y se toma
    sin = Customer(tenant_id=tid, name="Libre Sociedad", id_type="juridica", id_number="3-101-555555", email="libre@ejemplo.com")
    db_session.add(sin)
    db_session.commit()
    assert sin.id not in [x["id"] for x in client.get("/customers", params={"q": "Libre"}, headers=bh).json()["items"]]
    assert sin.id not in [x["id"] for x in client.get("/customers", params={"q": "5555"}, headers=bh).json()["items"]]
    hit = client.get("/customers", params={"q": "3101555555"}, headers=bh).json()["items"]
    assert hit and hit[0]["id"] == sin.id and hit[0]["claimable"] is True
    assert client.post(f"/customers/{sin.id}/claim", json={"query": "Libre"}, headers=bh).status_code == 404
    r = client.post(f"/customers/{sin.id}/claim", json={"query": "libre@ejemplo.com"}, headers=bh)
    assert r.status_code == 200 and r.json()["account_owner_name"] == "Beto Cartera"
    assert client.post(f"/customers/{sin.id}/claim", json={"query": "libre@ejemplo.com"}, headers=ah).status_code == 409


def test_admin_o_jefe_cambian_el_vendedor_de_la_cuenta(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ah, aid = _persona(client, db_session, tid, "vend.oa@ejemplo.com", "ventas", "Olga Dueña")
    bh, bid = _persona(client, db_session, tid, "vend.ob@ejemplo.com", "ventas", "Omar Nuevo")
    jh, _ = _persona(client, db_session, tid, "jefe.o@ejemplo.com", "jefe_ventas", "Jefe Dueños")
    c = _cliente(client, ah, "Cliente Olga", email="olga.cliente@ejemplo.com")
    assert client.put(f"/customers/{c['id']}/owner", json={"owner_id": bid}, headers=ah).status_code == 403
    r = client.put(f"/customers/{c['id']}/owner", json={"owner_id": bid, "reason": "Rotación de zona"}, headers=jh)
    assert r.status_code == 200 and r.json()["account_owner_id"] == bid
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "account_owner", AuditLog.entity_id == c["id"]).order_by(AuditLog.id.desc()))
    assert log.reason == "Rotación de zona" and log.after["account_owner_id"] == bid
    assert db_session.scalar(select(EmailOutbox).where(EmailOutbox.to == "vend.ob@ejemplo.com", EmailOutbox.entity == "customer"))
    assert client.get(f"/customers/{c['id']}", headers=bh).status_code == 200
    assert client.get(f"/customers/{c['id']}", headers=ah).status_code == 404
    # sin vendedor
    assert client.put(f"/customers/{c['id']}/owner", json={"owner_id": None}).json()["account_owner_id"] is None
    assert aid


def test_prospecto_que_se_cotiza_queda_del_vendedor(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _persona(client, db_session, tid, "vend.pr@ejemplo.com", "ventas", "Vendedor Prospecto")
    sin = Customer(tenant_id=tid, name="Prospecto Convertido", id_type="fisica")
    db_session.add(sin)
    db_session.commit()
    o = client.post("/opportunities", json={"title": "Cámaras bodega", "contact": {"name": "Don Prospecto"}}, headers=vh).json()
    r = client.post(f"/opportunities/{o['id']}/quote", json={"customer_id": sin.id}, headers=vh)
    assert r.status_code == 201, r.text
    db_session.refresh(sin)
    assert sin.account_owner_id == vid


# ---------- Traspaso ----------
def test_traspaso_de_cartera_con_vista_previa(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ah, aid = _persona(client, db_session, tid, "vend.ta@ejemplo.com", "ventas", "Tere Sale")
    _, bid = _persona(client, db_session, tid, "vend.tb@ejemplo.com", "ventas", "Toño Recibe")
    jh, _ = _persona(client, db_session, tid, "jefe.t@ejemplo.com", "jefe_ventas", "Jefe Traspaso")
    c = _cliente(client, ah, "Cliente Traspaso", email="traspaso@ejemplo.com")
    o = client.post("/opportunities", json={"title": "Alarma", "customer_id": c["id"]}, headers=ah).json()
    q = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "Visita", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]}, headers=ah).json()
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Visita", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]}, headers=ah).json()
    com = Commission(tenant_id=tid, user_id=aid, invoice_id=inv["id"], amount=50, earned_on=date.today(), status="pendiente", source_key="prueba-traspaso")
    db_session.add(com)
    db_session.commit()

    assert client.get(f"/cartera/sellers/{aid}", headers=ah).status_code == 403
    pv = client.get(f"/cartera/sellers/{aid}", headers=jh).json()
    assert pv["counts"] == {"clientes": 1, "oportunidades": 1, "cotizaciones": 1, "cotizaciones_con_comision": 0}
    assert bid in [x["id"] for x in pv["receivers"]] and aid not in [x["id"] for x in pv["receivers"]]
    assert client.post(f"/cartera/sellers/{aid}/transfer", json={"to_user_id": aid}, headers=jh).status_code == 422
    r = client.post(f"/cartera/sellers/{aid}/transfer", json={"to_user_id": bid}, headers=jh)
    assert r.status_code == 200 and r.json()["counts"]["clientes"] == 1, r.text

    db_session.expire_all()
    assert db_session.get(Customer, c["id"]).account_owner_id == bid
    assert db_session.get(Opportunity, o["id"]).owner_id == bid
    assert db_session.get(Quote, q["id"]).seller_id == bid
    # la factura y su comision se quedan con quien vendio
    assert db_session.get(Invoice, inv["id"]).seller_id == aid
    assert db_session.get(Commission, com.id).user_id == aid and db_session.get(Commission, com.id).status == "pendiente"
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "portfolio_transfer", AuditLog.entity_id == aid))
    assert log.diff["a"] == bid and log.diff["clientes"] == 1
    assert db_session.scalar(select(EmailOutbox).where(EmailOutbox.to == "vend.tb@ejemplo.com", EmailOutbox.subject.like("Recibió la cartera%")))


def test_desactivar_a_un_vendedor_con_cartera_ofrece_el_traspaso(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ah, aid = _persona(client, db_session, tid, "vend.des@ejemplo.com", "ventas", "Vendedor Sale")
    _cliente(client, ah, "Cliente que queda", email="queda@ejemplo.com")
    r = client.patch(f"/settings/users/{aid}", json={"active": False})
    assert r.status_code == 200 and r.json()["cartera"]["clientes"] == 1
    # sin cartera no ofrece nada
    _, sin = _persona(client, db_session, tid, "vend.vacio@ejemplo.com", "ventas", "Vendedor Vacio")
    assert "cartera" not in client.patch(f"/settings/users/{sin}", json={"active": False}).json()


# ---------- Relleno de la migracion y sugerencia ----------
def _migracion():
    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("ventas2_*.py"))
    spec = importlib.util.spec_from_file_location("mig_ventas2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_relleno_del_vendedor_de_la_cuenta(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _, aid = _persona(client, db_session, tid, "vend.r1@ejemplo.com", "ventas", "Relleno Uno")
    _, bid = _persona(client, db_session, tid, "vend.r2@ejemplo.com", "ventas", "Relleno Dos")
    con_fact = Customer(tenant_id=tid, name="Con factura")
    con_opp = Customer(tenant_id=tid, name="Con oportunidad")
    nada = Customer(tenant_id=tid, name="Sin historial")
    ya = Customer(tenant_id=tid, name="Ya tiene", account_owner_id=bid)
    db_session.add_all([con_fact, con_opp, nada, ya])
    db_session.flush()
    for n, (seller, day, st) in enumerate(((bid, 1, "enviada"), (aid, 5, "enviada"), (bid, 9, "anulada"))):
        db_session.add(Invoice(tenant_id=tid, number=f"REL-{n}", customer_id=con_fact.id, seller_id=seller, issue_date=date(2026, 10, day), status=st, currency="CRC"))
    db_session.add(Opportunity(tenant_id=tid, number="OPO-REL", title="Relleno", customer_id=con_opp.id, owner_id=bid))
    db_session.add(Opportunity(tenant_id=tid, number="OPO-REL2", title="Relleno factura", customer_id=con_fact.id, owner_id=bid))
    db_session.commit()

    _migracion().backfill(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Customer, con_fact.id).account_owner_id == aid  # la factura mas reciente no anulada manda
    assert db_session.get(Customer, con_opp.id).account_owner_id == bid
    assert db_session.get(Customer, nada.id).account_owner_id is None
    assert db_session.get(Customer, ya.id).account_owner_id == bid  # no se pisa


def test_ventas_por_asignar_sugiere_al_vendedor_de_la_cuenta(client, auth, db_session):
    from app.services.sale_assignment import suggest_seller

    tid = auth["tenant"]["id"]
    _, aid = _persona(client, db_session, tid, "vend.sug@ejemplo.com", "ventas", "Vendedor Sugerido")
    _, bid = _persona(client, db_session, tid, "vend.otro@ejemplo.com", "ventas", "Vendedor Historial")
    c = Customer(tenant_id=tid, name="Cliente sugerencia", account_owner_id=aid)
    db_session.add(c)
    db_session.flush()
    db_session.add(Invoice(tenant_id=tid, number="SUG-1", customer_id=c.id, seller_id=bid, issue_date=date.today(), status="enviada", currency="CRC"))
    db_session.commit()
    assert suggest_seller(db_session, tid, c.id) == aid
    assert uid(client, auth_headers(client)) != aid


def auth_headers(client) -> dict:
    return {"Authorization": client.headers["Authorization"]}
