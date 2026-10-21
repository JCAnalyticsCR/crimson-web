"""Economia (13/10): motor de comisiones v2. Reglas por persona y evento, tope por factura, devoluciones, anulacion,
aceptacion de cotizacion, proyecto completado, base utilidad que espera costos, bandeja, segregacion, visibilidad y
tiempo real del vendedor. Repo publico: solo nombres y correos de prueba."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Commission, CommissionRule, ExchangeRate, Opportunity, Product, Project
from app.providers.einvoice import cr_now
from app.services.commissions import Deal, best_rule
from tests.aliados_helpers import interno, uid

D = Decimal
# la fecha de Costa Rica, la que usa la app para cortar (backfill, meses): con el reloj en UTC date.today() ya es la de
# manana de 18:00 a 24:00 CR, y el ultimo dia del mes HOY.replace(day=1) caeria en el mes siguiente ("fecha futura")
HOY = cr_now().date()


def _cust(client):
    return client.get("/customers").json()["items"][0]


def _inv(client, h=None, unit=1000000, lines=None, **extra):
    body = {"customer_id": _cust(client)["id"], "lines": lines or [{"name": "Instalación CCTV", "quantity": 1, "unit_price": unit, "tax_rate": 13}], **extra}
    r = client.post("/invoices", json=body, headers=h or {})
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, iid, amount, kind="captura", h=None):
    r = client.post(f"/invoices/{iid}/payments", json={"method": "transferencia", "amount": amount, "kind": kind}, headers=h or {})
    assert r.status_code == 201, r.text
    return r.json()


def _rows(client, h=None, **params):
    return client.get("/commissions", params=params, headers=h or {}).json()["rows"]


def _rule(client, **body):
    r = client.post("/commissions/rules", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _vendedora(client, db_session, auth, email="vendedora@ejemplo.com", role="ventas"):
    h = interno(client, db_session, auth["tenant"]["id"], email, role)
    return h, uid(client, h)


def _quote(client, lines, **extra):
    r = client.post("/quotes", json={"customer_id": _cust(client)["id"], "lines": lines, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 1. regla mas especifica ----------
def _r(rid, **kw):
    base = {"tenant_id": 1, "active": True, "beneficiary": "vendedor", "kind": "comision", "base": "venta", "percent": D(5), "trigger": "cobro"}
    base.update(kw)
    return CommissionRule(id=rid, **base)


def test_best_rule_especificidad():
    deal = Deal(1, None, None, None, None, "venta", "cctv", 7)

    def pick(rules, **kw):
        args = {"beneficiary": "vendedor", "kind": "comision", "user_id": 7, "user_role": "ventas", "deal": deal, "on": HOY, "general_roles": ["ventas"]}
        args.update(kw)
        r = best_rule(rules, **args)
        return r.id if r else None

    general, general_cctv, persona, persona_cctv = _r(1), _r(2, solution="cctv"), _r(3, user_id=7), _r(4, user_id=7, solution="cctv")
    assert pick([general, general_cctv, persona, persona_cctv]) == 4
    assert pick([general, general_cctv, persona]) == 3
    assert pick([general, general_cctv]) == 2
    assert pick([general]) == 1
    # deal_kind filtra; empate gana la mas reciente; vencida no aplica; otro vendedor no
    assert pick([general, _r(5, user_id=7, deal_kind="proyecto")]) == 1
    assert pick([_r(6, user_id=7), _r(8, user_id=7)]) == 8
    assert pick([general, _r(9, user_id=7, solution="cctv", valid_to=HOY - timedelta(days=1))]) == 1
    assert pick([_r(10, user_id=8)]) is None
    # la general no alcanza roles fuera de la configuracion (el admin no se comisiona solo)
    assert pick([general], user_role="admin") is None
    assert pick([general], user_role="admin", general_roles=["ventas", "admin"]) == 1


# ---------- 2. regla general y roles ----------
def test_regla_general_solo_alcanza_roles_configurados(client, auth, db_session):
    vh, _vid = _vendedora(client, db_session, auth)
    sh, sid = _vendedora(client, db_session, auth, "supervisora@ejemplo.com", "supervisor")
    _rule(client, name="Todos", percent=5)
    admin_inv = _inv(client)
    _pay(client, admin_inv["id"], 1130000)
    assert _rows(client) == []  # el admin no comisiona con la regla general
    v_inv = _inv(client, vh)
    _pay(client, v_inv["id"], 565000)
    assert len(_rows(client)) == 1
    s_inv = _inv(client, seller_id=sid)
    _pay(client, s_inv["id"], 565000)
    assert len(_rows(client)) == 1  # supervisora: fuera de los roles de la regla general
    r = client.put("/commissions/config", json={"general_rule_roles": ["ventas", "supervisor"]})
    assert r.status_code == 200 and r.json()["general_rule_roles"] == ["ventas", "supervisor"]
    s_inv2 = _inv(client, seller_id=sid)
    _pay(client, s_inv2["id"], 565000)
    filas = _rows(client)
    assert len(filas) == 2 and any(f["user_id"] == sid for f in filas)
    assert client.put("/commissions/config", json={"general_rule_roles": ["cliente_admin"]}).status_code == 422


# ---------- 3. atribucion ----------
def test_atribucion_al_dueno_de_la_oportunidad(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    o = client.post("/opportunities", json={"title": "Cámaras bodega", "customer_id": _cust(client)["id"], "owner_id": vid, "amount": 1000000}).json()
    q = client.post(f"/opportunities/{o['id']}/quote", json={}).json()
    qq = client.get(f"/quotes/{q['quote_id']}").json()
    r = client.put(
        f"/quotes/{q['quote_id']}",
        json={"customer_id": qq["customer_id"], "lines": [{"name": "Kit CCTV", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}]},
    )
    assert r.status_code == 200, r.text
    inv = client.post(f"/quotes/{q['quote_id']}/convert").json()
    _pay(client, inv["id"], 1130000)
    filas = _rows(client)
    assert len(filas) == 1 and filas[0]["user_id"] == vid and D(str(filas[0]["amount"])) == D("50000.00")


# ---------- 4. sobrepago (falla B) ----------
def test_sobrepago_no_comisiona_de_mas(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 1130000)
    _pay(client, inv["id"], 1130000)  # pagada dos veces por error
    assert sum(D(str(f["base_amount"])) for f in _rows(client)) == D("1000000.00")


# ---------- 5. devolucion (falla A) ----------
def test_devolucion_revierte_y_nunca_suma(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 1130000)
    _pay(client, inv["id"], 565000, kind="devolucion")
    filas = _rows(client)
    ajustes = [f for f in filas if f["kind"] == "ajuste"]
    assert len(ajustes) == 1 and D(str(ajustes[0]["amount"])) == D("-25000.00") and ajustes[0]["status"] == "pendiente"
    assert ajustes[0]["reverses_id"] and "devolución" in ajustes[0]["description"]
    assert sum(1 for f in filas if D(str(f["amount"])) > 0) == 1
    inv2 = _inv(client, vh)
    _pay(client, inv2["id"], 1130000, kind="autorizacion")
    assert not [f for f in _rows(client) if f["invoice_id"] == inv2["id"]]


# ---------- 6. factura anulada (falla E) ----------
def test_factura_anulada(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 200
    f = _rows(client)[0]
    assert f["status"] == "anulada" and "anulada" in f["void_reason"]
    inv2 = _inv(client, vh)
    _pay(client, inv2["id"], 565000)
    pagada = next(x for x in _rows(client) if x["invoice_id"] == inv2["id"])
    assert client.patch("/commissions", json={"ids": [pagada["id"]], "status": "pagada", "paid_ref": "Planilla oct-2026"}).status_code == 200
    client.post(f"/invoices/{inv2['id']}/void")
    rev = [x for x in _rows(client) if x["invoice_id"] == inv2["id"] and x["kind"] == "ajuste"]
    assert len(rev) == 1 and rev[0]["reverses_id"] == pagada["id"] and rev[0]["status"] == "pendiente" and D(str(rev[0]["amount"])) == D("-25000.00")
    # "al aceptar la cotizacion": nace sin factura y la cotizacion convertida ya no se anula; anular su factura la anula
    _rule(client, user_id=vid, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, [{"name": "Kit", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], seller_id=vid)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    inv3 = client.post(f"/quotes/{q['id']}/convert").json()
    de_q = [x for x in _rows(client) if x["quote_id"] == q["id"]]
    assert len(de_q) == 1 and de_q[0]["invoice_id"] is None and de_q[0]["status"] == "pendiente"
    assert client.post(f"/quotes/{q['id']}/void").status_code == 409
    assert client.post(f"/invoices/{inv3['id']}/void").status_code == 200
    de_q = [x for x in _rows(client) if x["quote_id"] == q["id"]]
    assert len(de_q) == 1 and de_q[0]["status"] == "anulada" and inv3["number"] in de_q[0]["void_reason"]


# ---------- 7. mismo pago, vendedor y aliado (falla C) ----------
def test_mismo_pago_vendedor_y_aliado(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000101"}).json()["id"]
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    _rule(client, beneficiary="aliado", name="Todos los aliados", percent=10)
    o = client.post("/opportunities", json={"title": "Red oficina", "customer_id": _cust(client)["id"], "owner_id": vid, "amount": 1000000}).json()
    assert client.post(f"/opportunities/{o['id']}/allies", json={"ally_company_id": ally, "role": "subcontratista"}).status_code == 201
    q = client.post(f"/opportunities/{o['id']}/quote", json={}).json()
    client.put(
        f"/quotes/{q['quote_id']}", json={"customer_id": _cust(client)["id"], "lines": [{"name": "Red", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}]}
    )
    inv = client.post(f"/quotes/{q['quote_id']}/convert").json()
    _pay(client, inv["id"], 565000)
    filas = _rows(client)
    assert {f["beneficiary"] for f in filas} == {"vendedor", "aliado"}
    pagos = {db_session.get(Commission, f["id"]).payment_id for f in filas}
    assert len(pagos) == 1 and None not in pagos
    aliado = next(f for f in filas if f["beneficiary"] == "aliado")
    assert aliado["ally_company_id"] == ally and D(str(aliado["amount"])) == D("50000.00")


# ---------- 8. al aceptar la cotizacion ----------
def _acepta(client, qid, status="aceptada"):
    body = {"status": status} if status == "pendiente" else {"status": status, "contact_name": "Persona Cliente", "channel": "correo"}
    r = client.post(f"/quotes/{qid}/acceptance", json=body)
    assert r.status_code == 200, r.text


def test_devengo_al_aceptar(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, [{"name": "Kit", "quantity": 1, "unit_price": 200000, "tax_rate": 13}], seller_id=vid)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    filas = _rows(client)
    assert len(filas) == 1 and D(str(filas[0]["amount"])) == D("10000.00")
    assert db_session.get(Commission, filas[0]["id"]).source_key.endswith(":1")
    _acepta(client, q["id"], "pendiente")
    assert _rows(client)[0]["status"] == "anulada"
    _acepta(client, q["id"])
    vivas = [f for f in _rows(client) if f["status"] != "anulada"]
    assert len(vivas) == 1 and db_session.get(Commission, vivas[0]["id"]).source_key.endswith(":2")
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    _pay(client, inv["id"], 226000)
    assert len([f for f in _rows(client) if f["status"] != "anulada"]) == 1  # convertir y cobrar no duplican
    # sin aceptacion registrada: convertir devenga una vez (clave :0) y el proyecto no duplica
    q2 = _quote(client, [{"name": "Kit 2", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], seller_id=vid)
    client.post(f"/quotes/{q2['id']}/convert")
    assert client.post(f"/quotes/{q2['id']}/project", json={}).status_code == 201
    de_q2 = [f for f in _rows(client) if f["quote_id"] == q2["id"]]
    assert len(de_q2) == 1 and db_session.get(Commission, de_q2[0]["id"]).source_key.endswith(":0")


# ---------- 9. al completar el proyecto ----------
def test_devengo_al_completar_y_cancelar(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Al completar", percent=10, trigger="proyecto_completado")
    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 500000, "tax_rate": 13}], seller_id=vid)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    body = {"name": pr["name"], "status": "terminado"}
    assert client.put(f"/projects/{pr['id']}", json=body).status_code == 200
    filas = _rows(client)
    assert len(filas) == 1 and D(str(filas[0]["amount"])) == D("50000.00") and filas[0]["project_id"] == pr["id"]
    completado = db_session.get(Project, pr["id"]).completed_at
    assert completado is not None
    client.put(f"/projects/{pr['id']}", json={**body, "status": "entregado"})
    assert len(_rows(client)) == 1 and db_session.get(Project, pr["id"]).completed_at == completado
    client.put(f"/projects/{pr['id']}", json={**body, "status": "cancelado"})
    assert _rows(client)[0]["status"] == "anulada"


# ---------- 10. utilidad sin costo (falla D) ----------
def test_utilidad_sin_costo_queda_en_espera(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Utilidad", base="margen", percent=10)
    prod = client.post("/products", json={"name": "Cámara domo prueba", "code": "CAM-UT", "price": 0}).json()
    lines = [
        {"product_id": prod["id"], "name": "Cámara domo prueba", "quantity": 1, "unit_price": 100000, "tax_rate": 13},
        {
            "product_id": prod["id"],
            "name": "Cámara del cliente",
            "quantity": 5,
            "unit_price": 0,
            "tax_rate": 13,
            "treatment": "aportado",
            "supplied_by": "Cliente",
        },
    ]
    inv = _inv(client, lines=lines, seller_id=vid)
    _pay(client, inv["id"], inv["total"])
    f = _rows(client)[0]
    assert f["status"] == "en_espera" and f["hold_reason"].startswith("Falta el costo de") and D(str(f["amount"])) == 0
    obj = db_session.get(Product, prod["id"])
    obj.cost, obj.cost_currency = D("60000"), "CRC"
    db_session.commit()
    r = client.post(f"/commissions/entries/{f['id']}/recalc")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pendiente" and D(str(r.json()["amount"])) == D("4000.00")  # 10 % de (100 000 - 60 000): lo aportado no es costo
    assert client.post(f"/commissions/entries/{f['id']}/recalc").status_code == 200
    client.patch("/commissions", json={"ids": [f["id"]], "status": "aprobada"})
    assert client.post(f"/commissions/entries/{f['id']}/recalc").status_code == 409


# ---------- 11. utilidad de proyecto espera el cierre ----------
def test_utilidad_de_proyecto_espera_cierre(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Utilidad", base="margen", percent=10)
    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}], seller_id=vid)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    _pay(client, inv["id"], 1130000)
    f = _rows(client)[0]
    assert f["status"] == "en_espera" and f["project_id"] == pr["id"]
    client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "terminado", "cost_labor": 400000})
    f = _rows(client)[0]
    assert f["status"] == "pendiente" and D(str(f["amount"])) == D("60000.00")  # 10 % de (1 000 000 - 400 000)


# ---------- 12. transiciones y segregacion (falla G) ----------
def test_transiciones_y_segregacion(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)
    f = _rows(client)[0]
    assert client.patch("/commissions", json={"ids": [f["id"]], "status": "pagada"}).status_code == 200
    r = client.patch("/commissions", json={"ids": [f["id"]], "status": "pendiente"})
    assert r.status_code == 409 and "pagada" in r.json()["detail"]
    assert client.patch("/commissions", json={"ids": [f["id"]], "status": "anulada"}).status_code == 422  # sin motivo
    ch = interno(client, db_session, tid, "contadora@ejemplo.com", "contabilidad")
    cid = uid(client, ch)
    propia = Commission(tenant_id=tid, user_id=cid, amount=1000, amount_crc=1000, earned_on=HOY, status="pendiente")
    db_session.add(propia)
    db_session.commit()
    r = client.patch("/commissions", json={"ids": [propia.id], "status": "aprobada"}, headers=ch)
    assert r.status_code == 409 and "propia" in r.json()["detail"]
    r = client.patch("/commissions", json={"ids": [propia.id, 99999], "status": "aprobada"})
    assert r.status_code == 200 and r.json()["actualizadas"] == 1 and r.json()["omitidas"][0]["id"] == 99999
    assert client.patch("/commissions", json={"ids": [propia.id], "status": "anulada", "reason": "Error de carga"}).status_code == 200
    assert client.patch("/commissions", json={"ids": [propia.id], "status": "pagada"}).status_code == 409


# ---------- 13. visibilidad del vendedor (falla F) ----------
def test_visibilidad_vendedor(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _vendedora(client, db_session, auth)
    oh, oid = _vendedora(client, db_session, auth, "otra@ejemplo.com")
    _rule(client, user_id=vid, name="Sobre utilidad", base="margen", percent=10)
    _rule(client, user_id=oid, name="Otra", percent=7)
    _rule(client, beneficiary="aliado", name="Aliados", percent=3)
    db_session.add(Commission(tenant_id=tid, user_id=vid, base="margen", base_amount=500, percent=10, amount=50, earned_on=HOY))
    db_session.commit()
    f = _rows(client, vh)[0]
    assert f["base_amount"] is None and f["percent"] is None
    reglas = client.get("/commissions/rules", headers=vh).json()
    assert {r["name"] for r in reglas} == {"Sobre utilidad"} and reglas[0]["percent"] is None
    assert client.get("/commissions/numbers", headers=vh).status_code == 403
    assert client.get("/commissions/earnings", headers=vh).status_code == 403
    assert client.get("/commissions/inbox", headers=vh).status_code == 403
    assert client.get("/commissions/summary", params={"user_id": oid}, headers=vh).status_code == 403


# ---------- 14. totales en colones (falla H) ----------
def test_totales_en_colones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    db_session.scalar(select(ExchangeRate).where(ExchangeRate.currency == "USD").order_by(ExchangeRate.date.desc())).sell = D("500")
    uid_admin = auth["user"]["id"]
    db_session.add(Commission(tenant_id=tid, user_id=uid_admin, amount=25000, currency="CRC", amount_crc=25000, fx=1, earned_on=HOY))
    db_session.add(Commission(tenant_id=tid, user_id=uid_admin, amount=50, currency="USD", earned_on=HOY))  # vieja: sin amount_crc
    db_session.commit()
    r = client.get("/commissions").json()
    assert D(str(r["totals"]["pendiente"])) == D("50000.00")  # 25 000 + 50 x 500
    assert D(str(r["by_currency"]["USD"]["pendiente"])) == D("50.00") and D(str(r["by_currency"]["CRC"]["pendiente"])) == D("25000.00")


# ---------- 15. tiempo real ----------
def test_resumen_tiempo_real(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)
    client.post("/opportunities", json={"title": "Embudo", "customer_id": _cust(client)["id"], "amount": 1000000, "probability": 50}, headers=vh)
    s = client.get("/commissions/summary", headers=vh).json()
    assert D(str(s["ganado"]["total"])) == D("25000.00") and D(str(s["ganado"]["pendiente"])) == D("25000.00")
    assert D(str(s["por_cobrar"]["total"])) == D("25000.00") and s["por_cobrar"]["facturas"] == 1 and s["por_cobrar"]["estimado"] is False
    assert D(str(s["embudo"]["total"])) == D("25000.00") and s["embudo"]["estimado"] is True
    assert s["reglas"] and "5 %" in s["reglas"][0]["summary"] and len(s["historial"]) == 12
    dash = client.get("/dashboard", headers=vh).json()
    assert D(str(dash["comisiones"]["ganado_mes"])) == D("25000.00") and D(str(dash["comisiones"]["por_cobrar"])) == D("25000.00")
    assert client.get("/dashboard").json()["comisiones"] is None  # el admin ve la empresa, no "mis comisiones"


# ---------- 16. validaciones ----------
def test_validaciones_de_regla(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _vendedora(client, db_session, auth)
    assert client.post("/commissions/rules", json={"beneficiary": "aliado", "kind": "bono_anticipo", "fixed_amount": 1000, "name": "x"}).status_code == 422
    assert client.post("/commissions/rules", json={"name": "Cero", "percent": 0}).status_code == 422
    assert client.post("/commissions/rules", json={"name": "Ambos", "percent": 5, "fixed_amount": 1000}).status_code == 422
    assert (
        client.post("/commissions/rules", json={"name": "Fechas", "percent": 5, "valid_from": str(HOY), "valid_to": str(HOY - timedelta(days=1))}).status_code
        == 422
    )
    cli = client.post("/customers", json={"name": "Cliente Demo"}).json()
    from app.core.security import hash_password
    from app.models import TenantUser, User

    u = User(email="portal@ejemplo.com", full_name="Persona Portal", password_hash=hash_password("Rol-prueba-2026"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code="cliente_usuario", customer_id=cli["id"]))
    db_session.commit()
    assert client.post("/commissions/rules", json={"user_id": u.id, "name": "Cliente", "percent": 5}).status_code == 422
    assert client.post("/commissions/rules", json={"user_id": 99999, "name": "Nadie", "percent": 5}).status_code == 422
    rid = _rule(client, user_id=vid, name="Vendedora", percent=5)
    fixed = client.post("/commissions/rules", json={"name": "Fijo", "fixed_amount": 5000})
    assert fixed.status_code == 201
    assert client.get("/commissions/rules").json()[-1]["fixed_currency"] == "CRC"
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)
    assert client.delete(f"/commissions/rules/{rid}").status_code == 409
    assert client.delete(f"/commissions/rules/{fixed.json()['id']}").status_code == 200
    ben = client.get("/commissions/beneficiaries").json()
    assert any(x["id"] == vid and x["role"] == "ventas" and x["general"] for x in ben["users"]) and all(x["id"] != u.id for x in ben["users"])
    assert client.get("/commissions/beneficiaries", headers=vh).status_code == 403


# ---------- 17. backfill ----------
def test_backfill_idempotente(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)  # sin reglas: no comisiona
    assert _rows(client) == []
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    desde = str(HOY.replace(day=1))
    r = client.post("/commissions/backfill", json={"from": desde, "dry_run": True}).json()
    assert r["creados"] == 1 and r["dry_run"] is True and D(str(r["por_beneficiario"][0]["total_crc"])) == D("25000.00")
    assert _rows(client) == []
    assert client.post("/commissions/backfill", json={"from": desde, "dry_run": False}).json()["creados"] == 1
    assert len(_rows(client)) == 1
    assert client.post("/commissions/backfill", json={"from": desde, "dry_run": False}).json()["creados"] == 0
    assert client.post("/commissions/backfill", json={"from": str(HOY + timedelta(days=1))}).status_code == 422
    # crear el proyecto desde la cotizacion no cambia su estado pero es aceptacion implicita: "al aceptar" lo alcanza
    q = _quote(client, [{"name": "Kit", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], seller_id=vid)
    assert client.post(f"/quotes/{q['id']}/project", json={}).status_code == 201
    _rule(client, user_id=vid, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    assert client.post("/commissions/backfill", json={"from": desde, "dry_run": True}).json()["creados"] == 1
    assert client.post("/commissions/backfill", json={"from": desde, "dry_run": False}).json()["creados"] == 1
    assert client.post("/commissions/backfill", json={"from": desde, "dry_run": False}).json()["creados"] == 0
    de_q = [f for f in _rows(client) if f["quote_id"] == q["id"]]
    assert len(de_q) == 1 and D(str(de_q[0]["amount"])) == D("5000.00")


# ---------- 18. bandeja ----------
def test_bandeja_y_contador(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 1130000)
    _pay(client, inv["id"], 565000, kind="devolucion")
    tid = auth["tenant"]["id"]
    db_session.add(Commission(tenant_id=tid, user_id=vid, amount=0, earned_on=HOY, status="en_espera", base="margen", hold_reason="Falta el costo de: Algo"))
    db_session.commit()
    b = client.get("/commissions/inbox").json()
    g = next(x for x in b["por_aprobar"] if x["key"] == f"u:{vid}")
    assert g["count"] == 2 and D(str(g["total_crc"])) == D("25000.00") and g["has_adjustments"] is True
    assert b["totals"]["en_espera"] == 1 and len(b["en_espera"]) == 1
    assert client.get("/dashboard").json()["acciones_pendientes"]["comisiones_por_aprobar"] == 3
    adj = client.post("/commissions/adjustments", json={"beneficiary": "vendedor", "user_id": vid, "amount": "-1000", "description": "Descuento"})
    assert adj.status_code == 201 and adj.json()["kind"] == "ajuste" and adj.json()["trigger"] == "manual"
    rep = client.get("/reports/comisiones").json()
    assert rep["key"] == "comisiones" and len(rep["rows"]) >= 3


# ---------- 19. rentabilidad en USD y con aliados (fallas J y K) ----------
def test_economics_usd_y_aliados(client, auth, db_session):
    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 1000, "tax_rate": 13}], currency="USD", fx_sell=500)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["price"])) == D("1000") and D(str(eco["price_crc"])) == D("500000.00")
    client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "en_curso", "cost_labor": 100000})
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["profit"])) == D("400000.00") and round(eco["margin_real"]) == 80  # colones contra colones
    # precio de aliado aprobado: costo directo
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000102"}).json()["id"]
    o = client.post("/opportunities", json={"title": "Con aliado", "customer_id": _cust(client)["id"], "amount": 500000}).json()
    db_session.get(Opportunity, o["id"]).project_id = pr["id"]
    db_session.get(Project, pr["id"]).opportunity_id = o["id"]
    db_session.commit()
    aid = client.post(f"/opportunities/{o['id']}/allies", json={"ally_company_id": ally, "role": "subcontratista"}).json()[0]["id"]
    from app.models import AllyParticipation

    db_session.get(AllyParticipation, aid).project_id = pr["id"]
    db_session.commit()
    client.post(f"/opportunity-allies/{aid}/requests", json={"what": "Mano de obra", "status": "aprobado", "amount": 100000, "currency": "CRC"})
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["cost_allies"])) == D("100000.00") and D(str(eco["cost_real"])) == D("200000.00")
    # comision devengada del proyecto: utilidad neta
    tid = auth["tenant"]["id"]
    db_session.add(Commission(tenant_id=tid, user_id=auth["user"]["id"], project_id=pr["id"], amount=20000, amount_crc=20000, earned_on=HOY))
    db_session.commit()
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["commissions"])) == D("20000.00") and D(str(eco["profit_net"])) == D(str(eco["profit"])) - 20000
    deal = client.get(f"/projects/{pr['id']}").json()["allies"][0]["deal"]
    assert D(str(deal["pago_acordado_crc"])) == D("100000.00")


# ---------- 20. revision: la NC tambien anula (falla E por el camino de Hacienda) ----------
def test_factura_anulada_por_nota_de_credito(client, auth, db_session):
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)
    assert client.post(f"/invoices/{inv['id']}/emit").json()["status"] == "aceptada"
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 409  # aceptada: solo NC
    assert client.post(f"/invoices/{inv['id']}/credit-note", json={"reason": "Error en cantidad"}).status_code == 200
    f = next(x for x in _rows(client) if x["invoice_id"] == inv["id"])
    assert f["status"] == "anulada" and "anulada" in f["void_reason"]
    inv2 = _inv(client, vh)
    _pay(client, inv2["id"], 565000)
    pagada = next(x for x in _rows(client) if x["invoice_id"] == inv2["id"])
    assert client.patch("/commissions", json={"ids": [pagada["id"]], "status": "pagada", "paid_ref": "Planilla oct-2026"}).status_code == 200
    client.post(f"/invoices/{inv2['id']}/emit")
    assert client.post(f"/invoices/{inv2['id']}/credit-note", json={"reason": "Devolución total"}).status_code == 200
    rev = [x for x in _rows(client) if x["invoice_id"] == inv2["id"] and x["kind"] == "ajuste"]
    assert len(rev) == 1 and rev[0]["reverses_id"] == pagada["id"] and D(str(rev[0]["amount"])) == D("-25000.00")


# ---------- 21. revision: el concepto sobre utilidad no lleva el % (falla F por la descripcion) ----------
def test_concepto_sobre_utilidad_sin_porcentaje(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Sobre utilidad", base="margen", percent=10)
    prod = client.post("/products", json={"name": "Switch prueba", "code": "SW-UT", "price": 0}).json()
    obj = db_session.get(Product, prod["id"])
    obj.cost, obj.cost_currency = D("60000"), "CRC"
    db_session.commit()
    inv = _inv(client, lines=[{"product_id": prod["id"], "name": "Switch prueba", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], seller_id=vid)
    _pay(client, inv["id"], inv["total"])
    mia = _rows(client, vh)[0]
    assert mia["status"] == "pendiente" and D(str(mia["amount"])) == D("4000.00")
    assert mia["percent"] is None and mia["base_amount"] is None and "%" not in mia["description"]
    assert "%" not in str(client.get("/reports/comisiones").json()["rows"])
    admin = _rows(client)[0]
    assert D(str(admin["percent"])) == D("10.00")  # quien ve costos sigue viendo el % en su columna


# ---------- 22. revision: aceptar dos veces, o aceptar, deshacer y convertir ----------
def test_aceptacion_repetida_y_conversion_tras_deshacer(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, [{"name": "Kit", "quantity": 1, "unit_price": 200000, "tax_rate": 13}], seller_id=vid)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    _acepta(client, q["id"])  # doble envio o aceptacion de otra version: no duplica
    assert len([f for f in _rows(client) if f["status"] != "anulada"]) == 1
    q3 = _quote(client, [{"name": "Kit 3", "quantity": 1, "unit_price": 200000, "tax_rate": 13}], seller_id=vid)
    client.post(f"/quotes/{q3['id']}/send")
    _acepta(client, q3["id"])
    _acepta(client, q3["id"], "pendiente")
    assert client.post(f"/quotes/{q3['id']}/convert").status_code in (200, 201)
    vivas = [f for f in _rows(client) if f["quote_id"] == q3["id"] and f["status"] != "anulada"]
    assert len(vivas) == 1 and D(str(vivas[0]["amount"])) == D("10000.00")  # convertir es aceptacion implicita


# ---------- 23. revision: la regla de alguien desactivado se puede apagar ----------
def test_regla_de_vendedor_desactivado(client, auth, db_session):
    from app.models import TenantUser

    vh, vid = _vendedora(client, db_session, auth)
    rid = _rule(client, user_id=vid, name="Vendedora", percent=5)
    inv = _inv(client, vh)
    _pay(client, inv["id"], 565000)
    m = db_session.scalar(select(TenantUser).where(TenantUser.tenant_id == auth["tenant"]["id"], TenantUser.user_id == vid))
    m.active = False
    db_session.commit()
    r = client.put(f"/commissions/rules/{rid}", json={"user_id": vid, "name": "Vendedora", "percent": 5, "active": False})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(CommissionRule, rid).active is False
    # elegir a alguien inactivo para una regla nueva sigue sin poderse
    assert client.post("/commissions/rules", json={"user_id": vid, "name": "Otra", "percent": 5}).status_code == 422


# ---------- 24. revision: cerrar el proyecto facturandolo recalcula lo que espera la utilidad ----------
def test_cierre_por_factura_recalcula_utilidad(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Utilidad", base="margen", percent=10)
    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}], seller_id=vid)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    _pay(client, inv["id"], 1130000)
    client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "en_curso", "cost_labor": 400000})
    assert _rows(client)[0]["status"] == "en_espera"  # abierto: espera el cierre
    r = client.post(f"/projects/{pr['id']}/invoice")
    assert r.status_code == 201, r.text
    f = _rows(client)[0]
    assert f["status"] == "pendiente" and D(str(f["amount"])) == D("60000.00")


# ---------- 25. revision: lectura no ve el precio del aliado; Aprobaciones con el libro v2 ----------
def test_lectura_sin_precio_del_aliado_y_aprobaciones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000111"}).json()["id"]
    o = client.post("/opportunities", json={"title": "Fibra norte", "customer_id": _cust(client)["id"], "amount": 200000}).json()
    aid = client.post(f"/opportunities/{o['id']}/allies", json={"ally_company_id": ally, "role": "subcontratista"}).json()[0]["id"]
    body = {"what": "Mano de obra de fibra", "status": "aprobado", "amount": 100000, "currency": "CRC"}
    assert client.post(f"/opportunity-allies/{aid}/requests", json=body).status_code == 201
    q = client.post(f"/opportunities/{o['id']}/quote", json={}).json()
    lines = [{"name": "Fibra", "quantity": 1, "unit_price": 200000, "tax_rate": 13}]
    assert client.put(f"/quotes/{q['quote_id']}", json={"customer_id": _cust(client)["id"], "lines": lines}).status_code == 200
    pr = client.post(f"/quotes/{q['quote_id']}/project", json={}).json()
    assert client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "terminado"}).status_code == 200
    lh = interno(client, db_session, tid, "socio@ejemplo.com", "lectura")
    ch = interno(client, db_session, tid, "contadora@ejemplo.com", "contabilidad")
    pago_l = next(f for f in _rows(client, lh) if f["kind"] == "pago_acordado")
    assert pago_l["amount"] is None and pago_l["base_amount"] is None and pago_l["amount_crc"] is None
    assert D(str(client.get("/commissions", headers=lh).json()["totals"]["pendiente"])) == 0
    pago_c = next(f for f in _rows(client, ch) if f["kind"] == "pago_acordado")
    assert D(str(pago_c["amount"])) == D("100000.00") and pago_c["base_amount"] is None  # contabilidad lo paga
    for path in ("/commissions/earnings", "/commissions/allies"):
        assert client.get(path, params={"ally_company_id": ally}, headers=lh).status_code == 403, path
        assert client.get(path, params={"ally_company_id": ally}, headers=ch).status_code == 403, path
    assert not [r for r in client.get("/reports/comisiones", headers=lh).json()["rows"] if r[3] == "Pago acordado"]
    assert [r for r in client.get("/reports/comisiones", headers=ch).json()["rows"] if r[3] == "Pago acordado"]
    # Aprobaciones: el aliado por su nombre, el tipo del movimiento y la misma cuenta que Inicio
    espera = Commission(
        tenant_id=tid, user_id=auth["user"]["id"], amount=0, earned_on=HOY, status="en_espera", base="margen", hold_reason="Falta el costo de: Algo"
    )
    db_session.add(espera)
    db_session.commit()
    sec = next(s for s in client.get("/approvals").json()["sections"] if s["key"] == "comisiones_por_aprobar")
    item = next(i for i in sec["items"] if i["title"] == "Aliado Uno S.A.")
    assert item["subtitle"].startswith("Pago acordado") and item["to"] == "/comisiones?tab=bandeja"
    assert sec["count"] == client.get("/dashboard").json()["acciones_pendientes"]["comisiones_por_aprobar"]


# ---------- 26. revision: listado paginado en SQL ----------
def test_movimientos_paginados(client, auth, db_session):
    tid = auth["tenant"]["id"]
    for i in range(5):
        db_session.add(
            Commission(tenant_id=tid, user_id=auth["user"]["id"], amount=1000, amount_crc=1000, fx=1, currency="CRC", earned_on=HOY - timedelta(days=i))
        )
    db_session.commit()
    r = client.get("/commissions", params={"limit": 2, "offset": 1}).json()
    assert len(r["rows"]) == 2 and r["total_rows"] == 5 and D(str(r["totals"]["pendiente"])) == D("5000.00")
    assert r["rows"][0]["earned_on"] == str(HOY - timedelta(days=1))


def _neto(filas) -> Decimal:
    return sum((D(str(f["amount"])) for f in filas if f["status"] != "anulada"), D(0))


# ---------- 27. revision 3: reversar dos veces no le vuelve a deber lo pagado ----------
def test_doble_reverso_de_la_cotizacion(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, [{"name": "Kit", "quantity": 1, "unit_price": 200000, "tax_rate": 13}], seller_id=vid)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    c1 = _rows(client)[0]
    assert client.patch("/commissions", json={"ids": [c1["id"]], "status": "pagada", "paid_ref": "Planilla oct-2026"}).status_code == 200
    _acepta(client, q["id"], "pendiente")
    assert _neto(_rows(client)) == 0  # pagada + su reverso
    _acepta(client, q["id"])
    assert _neto(_rows(client)) == D("10000.00")
    _acepta(client, q["id"], "pendiente")
    filas = _rows(client)
    assert _neto(filas) == 0
    rev = next(f for f in filas if f["reverses_id"] == c1["id"])
    assert rev["status"] == "pendiente"  # el reverso de la pagada sigue vivo
    # pagada y revertida: convertir (aceptacion implicita) devenga otra vez
    assert client.post(f"/quotes/{q['id']}/convert").status_code in (200, 201)
    assert _neto(_rows(client)) == D("10000.00")


# ---------- 28. revision 3: la devolucion tambien baja lo que espera la utilidad ----------
def test_devolucion_de_lo_que_espera_costos(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Utilidad", base="margen", percent=10)
    prod = client.post("/products", json={"name": "Cámara domo prueba", "code": "CAM-DV", "price": 0}).json()
    inv = _inv(client, lines=[{"product_id": prod["id"], "name": "Cámara domo prueba", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], seller_id=vid)
    _pay(client, inv["id"], inv["total"])
    assert _rows(client)[0]["status"] == "en_espera"
    _pay(client, inv["id"], inv["total"], kind="devolucion")
    f = _rows(client)[0]
    assert f["status"] == "anulada" and "devolvió" in f["void_reason"]
    obj = db_session.get(Product, prod["id"])
    obj.cost, obj.cost_currency = D("60000"), "CRC"
    db_session.commit()
    assert client.post("/commissions/recalc-waiting").status_code == 200
    assert _neto(_rows(client)) == 0  # nunca comision sobre plata devuelta
    _pay(client, inv["id"], inv["total"])  # vuelve a pagar: el tope de la factura no cuenta lo devuelto
    assert _neto(_rows(client)) == D("4000.00")


def test_devolucion_parcial_con_utilidad_del_proyecto_en_espera(client, auth, db_session):
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Utilidad", base="margen", percent=10)
    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}], seller_id=vid)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    _pay(client, inv["id"], 1130000)
    _pay(client, inv["id"], 565000, kind="devolucion")
    client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "terminado", "cost_labor": 400000})
    vivas = [f for f in _rows(client) if f["status"] != "anulada"]
    assert len(vivas) == 1 and vivas[0]["status"] == "pendiente" and D(str(vivas[0]["amount"])) == D("30000.00")  # 10 % de la mitad de 600 000


# ---------- 29. revision 3: cancelar un proyecto completado exige aprobar comisiones ----------
def test_cancelar_proyecto_completado(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _vendedora(client, db_session, auth)
    _rule(client, user_id=vid, name="Al completar", percent=10, trigger="proyecto_completado")
    q = _quote(client, [{"name": "Instalación", "quantity": 1, "unit_price": 500000, "tax_rate": 13}], seller_id=vid)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    body = {"name": pr["name"], "status": "terminado"}
    assert client.put(f"/projects/{pr['id']}", json=body).status_code == 200
    c = _rows(client)[0]
    assert client.patch("/commissions", json={"ids": [c["id"]], "status": "pagada", "paid_ref": "Planilla oct-2026"}).status_code == 200
    sh = interno(client, db_session, tid, "supervisora@ejemplo.com", "supervisor")
    r = client.put(f"/projects/{pr['id']}", json={**body, "status": "cancelado"}, headers=sh)
    assert r.status_code == 409 and "aprueba comisiones" in r.json()["detail"]
    assert _neto(_rows(client)) == D("50000.00")
    assert client.put(f"/projects/{pr['id']}", json={**body, "status": "cancelado"}).status_code == 200
    assert _neto(_rows(client)) == 0
    db_session.expire_all()
    assert db_session.get(Project, pr["id"]).completed_at is None
    # vuelve a cerrarse: se completa otra vez y devenga con clave nueva (lo pagado y revertido queda de historial)
    assert client.put(f"/projects/{pr['id']}", json=body).status_code == 200
    filas = _rows(client)
    assert _neto(filas) == D("50000.00")
    nueva = next(f for f in filas if f["kind"] == "comision" and f["status"] == "pendiente")
    assert db_session.get(Commission, nueva["id"]).source_key.endswith(":re1")
    # sin nada devengado vivo, el supervisor si lo cancela
    q2 = _quote(client, [{"name": "Otra", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], seller_id=vid)
    pr2 = client.post(f"/quotes/{q2['id']}/project", json={}).json()
    assert client.put(f"/projects/{pr2['id']}", json={"name": pr2["name"], "status": "cancelado"}, headers=sh).status_code == 200
