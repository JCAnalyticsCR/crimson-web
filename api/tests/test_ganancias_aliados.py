"""Economia: lo que recibe un aliado (pago acordado = su precio aprobado, comision por regla, bonos) y "Mis numeros".

El ejemplo de Andres (13/10, 26:36-27:23): el aliado cotiza mano de obra en 100 000, Crimson le vende al cliente en
200 000 y el aliado ve que recibe sus 100 000 (mas su comision y bonos), nunca el precio de venta de Crimson. Lo
del aliado sale por su portal (/aliado/ganancias*, aliado_final R7); /commissions* es interno. Nombres ficticios."""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Commission
from tests.aliados_helpers import interno, keys_rec, usuario

D = Decimal
HOY = date.today()


def _cust(client):
    return client.get("/customers").json()["items"][0]


def _negocio(client, ally_id: int, venta=200000, precio=100000, titulo="Cámaras bodega norte", estado="aprobado", proyecto=True):
    """Oportunidad con el aliado, su precio (en colones) y la cotizacion de Crimson -> proyecto (o solo la
    cotizacion, si proyecto=False: una venta sin proyecto)."""
    o = client.post("/opportunities", json={"title": titulo, "customer_id": _cust(client)["id"], "amount": venta}).json()
    r = client.post(f"/opportunities/{o['id']}/allies", json={"ally_company_id": ally_id, "role": "subcontratista"})
    assert r.status_code == 201, r.text
    aid = next(a["id"] for a in r.json() if a.get("ally_company_id") == ally_id)
    lst = client.post(
        f"/opportunity-allies/{aid}/requests", json={"what": "Mano de obra de fibra", "status": estado, "amount": precio, "currency": "CRC"}
    ).json()
    rid = next(x for x in lst if x["id"] == aid)["requests"][0]["id"]
    q = client.post(f"/opportunities/{o['id']}/quote", json={}).json()
    r = client.put(
        f"/quotes/{q['quote_id']}",
        json={"customer_id": _cust(client)["id"], "lines": [{"name": "Instalación", "quantity": 1, "unit_price": venta, "tax_rate": 13}]},
    )
    assert r.status_code == 200, r.text
    if not proyecto:
        return {"opp": o["id"], "aid": aid, "rid": rid, "qid": q["quote_id"], "pr": None}
    pr = client.post(f"/quotes/{q['quote_id']}/project", json={})
    assert pr.status_code == 201, pr.text
    return {"opp": o["id"], "aid": aid, "rid": rid, "qid": q["quote_id"], "pr": pr.json()}


def _acuerdo(client, aid, committed: date):
    r = client.put(
        f"/commissions/deals/{aid}",
        json={
            "commission": {"base": "venta", "percent": 10, "trigger": "proyecto_completado"},
            "bonus_complete": {"amount": 25000},
            "bonus_early": {"amount": 15000, "committed_date": str(committed), "early_days": 1},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _completar(client, pr):
    r = client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "terminado"})
    assert r.status_code == 200, r.text


def _mov(client, **params):
    return client.get("/commissions", params={"beneficiary": "aliado", **params}).json()["rows"]


def test_ejemplo_de_andres(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000201"}).json()["id"]
    n = _negocio(client, ally)
    deal = _acuerdo(client, n["aid"], HOY + timedelta(days=1))
    p = deal["projection"]
    assert D(str(p["pago_acordado"])) == 100000 and D(str(p["comision"])) == 20000 and D(str(p["bonos"])) == 25000 and D(str(p["bono_anticipo"])) == 15000
    assert deal["commission"]["trigger"] == "proyecto_completado" and deal["bonus_early"]["early_days"] == 1
    _completar(client, n["pr"])
    filas = _mov(client, project_id=n["pr"]["id"])
    montos = sorted((f["kind"], D(str(f["amount"]))) for f in filas)
    assert montos == [("bono", D("15000.00")), ("bono", D("25000.00")), ("comision", D("20000.00")), ("pago_acordado", D("100000.00"))]
    assert all(f["status"] == "pendiente" and f["ally_company_id"] == ally for f in filas)
    # con la fecha comprometida hoy y un dia de anticipacion: sin bono por terminar antes
    n2 = _negocio(client, ally, titulo="Red oficina sur")
    _acuerdo(client, n2["aid"], HOY)
    _completar(client, n2["pr"])
    kinds = sorted(f["description"].split(" · ")[0] for f in _mov(client, project_id=n2["pr"]["id"]))
    assert len(kinds) == 3 and not any("antes" in k for k in kinds)
    # el acuerdo propio manda sobre una regla general de aliados; null vuelve a la general
    client.post("/commissions/rules", json={"beneficiary": "aliado", "name": "Todos los aliados", "percent": 5})
    r = client.put(f"/commissions/deals/{n2['aid']}", json={"commission": None, "bonus_complete": None, "bonus_early": None}).json()
    assert r["commission"] is None and r["general_rule"] and "5 %" in r["general_rule"]["summary"]


def test_pago_acordado_al_cobrar(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000202"}).json()["id"]
    assert client.put("/commissions/config", json={"ally_payout_trigger": "cobro"}).status_code == 200
    n = _negocio(client, ally)
    inv = client.post(f"/quotes/{n['qid']}/convert").json()
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 113000})  # 50 %
    pagos = [f for f in _mov(client) if f["kind"] == "pago_acordado"]
    assert len(pagos) == 1 and D(str(pagos[0]["amount"])) == D("50000.00") and pagos[0]["trigger"] == "cobro"
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 135600})  # 60 %: tope
    pagos = [f for f in _mov(client) if f["kind"] == "pago_acordado"]
    assert sum(D(str(f["amount"])) for f in pagos) == D("100000.00")
    _completar(client, n["pr"])  # al completar ya no devenga otra vez el pago acordado
    assert sum(D(str(f["amount"])) for f in _mov(client) if f["kind"] == "pago_acordado") == D("100000.00")


def test_alcance_del_aliado(client, auth, db_session):
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000203"}).json()["id"]
    b = client.post("/allies", json={"name": "Aliado Dos S.A.", "tax_id": "3-101-000204"}).json()["id"]
    ha = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    hb = usuario(client, admin_h, b, "jefe@aliado-dos.ejemplo.com", "aliado")
    na = _negocio(client, a)
    nb = _negocio(client, b, titulo="Red oficina sur")
    _acuerdo(client, na["aid"], HOY + timedelta(days=3))
    _completar(client, na["pr"])
    _completar(client, nb["pr"])
    inv = client.post(f"/quotes/{na['qid']}/convert").json()
    ga = client.get("/aliado/ganancias", headers=ha).json()
    assert D(str(ga["totales"]["por_recibir"])) == D("160000.00")  # 100 000 + 20 000 + 25 000 + 15 000
    assert [x["project_id"] for x in ga["proyectos"]] == [na["pr"]["id"]]
    assert D(str(ga["proyectos"][0]["pago_acordado"])) == 100000 and ga["proyectos"][0]["lo_que_pusiste"][0]["what"] == "Mano de obra de fibra"
    gb = client.get("/aliado/ganancias", headers=hb).json()
    assert [x["project_id"] for x in gb["proyectos"]] == [nb["pr"]["id"]] and D(str(gb["totales"]["por_recibir"])) == D("100000.00")
    assert client.get(f"/aliado/ganancias/proyectos/{nb['pr']['id']}", headers=ha).status_code == 404
    det = client.get(f"/aliado/ganancias/proyectos/{na['pr']['id']}", headers=ha)
    assert det.status_code == 200 and len(det.json()["movimientos"]) == 4
    qnum = client.get(f"/quotes/{na['qid']}").json()["number"]
    proy = client.get(f"/aliado/proyectos/{na['pr']['id']}", headers=ha).json()
    for body in (ga, det.json(), proy["lo_que_recibo"]):
        keys = keys_rec(body)
        assert not {"price", "price_crc", "cost_real", "cost_allies", "base_amount", "percent", "invoice_id", "quote_id"} & keys
        assert inv["number"] not in str(body), [k for k in str(body).split(",") if inv["number"] in k]
    # el concepto no lleva el % (con el monto despejaria la venta al cliente) ni el numero de la cotizacion
    for m in det.json()["movimientos"] + proy["lo_que_recibo"]["movimientos"]:
        assert "%" not in m["description"] and qnum not in m["description"] and na["pr"]["number"] in m["description"], m["description"]
    com = next(m for m in det.json()["movimientos"] if m["kind"] == "comision")
    assert com["description"] == f"Comisión al completar el proyecto · {na['pr']['number']}"
    # /commissions* es interno: el aliado no entra; el admin lo consulta por empresa
    assert client.get("/commissions", headers=ha).status_code == 403
    assert client.get("/commissions/earnings", params={"ally_company_id": a}, headers=ha).status_code == 403
    adm = client.get("/commissions/earnings", params={"ally_company_id": a}).json()
    assert D(str(adm["totales"]["por_recibir"])) == D("160000.00")
    assert client.get(f"/commissions/earnings/projects/{nb['pr']['id']}", params={"ally_company_id": a}).status_code == 404
    lista = {x["ally_company_id"]: x for x in client.get("/commissions/allies").json()}
    assert D(str(lista[a]["por_recibir"])) == D("160000.00") and lista[b]["proyectos"] == 1
    assert D(str(proy["lo_que_recibo"]["totales"]["por_recibir"])) == D("160000.00")


def test_precio_desaprobado_revierte(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000205"}).json()["id"]
    n = _negocio(client, ally)
    _completar(client, n["pr"])
    pago = next(f for f in _mov(client) if f["kind"] == "pago_acordado")
    body = {"what": "Mano de obra de fibra", "amount": 100000, "currency": "CRC"}
    assert client.put(f"/opportunity-allies/requests/{n['rid']}", json={**body, "status": "rechazado"}).status_code == 200
    assert next(f for f in _mov(client) if f["id"] == pago["id"])["status"] == "anulada"
    # se vuelve a aprobar con otro monto (proyecto ya completado): devenga de nuevo; pagado y desaprobado -> ajuste
    client.put(f"/opportunity-allies/requests/{n['rid']}", json={**body, "status": "aprobado", "amount": 90000})
    nuevo = next(f for f in _mov(client) if f["kind"] == "pago_acordado" and f["status"] == "pendiente")
    assert D(str(nuevo["amount"])) == D("90000.00")
    client.put(f"/opportunity-allies/requests/{n['rid']}", json={**body, "status": "aprobado", "amount": 95000})
    assert D(str(next(f for f in _mov(client) if f["id"] == nuevo["id"])["amount"])) == D("95000.00")  # pendiente: se actualiza
    assert client.patch("/commissions", json={"ids": [nuevo["id"]], "status": "pagada", "paid_ref": "Transferencia 1234"}).status_code == 200
    client.put(f"/opportunity-allies/requests/{n['rid']}", json={**body, "status": "aprobado", "amount": 100000})
    dif = [f for f in _mov(client) if f["kind"] == "ajuste"]
    assert len(dif) == 1 and D(str(dif[0]["amount"])) == D("5000.00")  # pagado: la diferencia va en un ajuste
    client.put(f"/opportunity-allies/requests/{n['rid']}", json={**body, "status": "rechazado"})
    rev = [f for f in _mov(client) if f["kind"] == "ajuste" and f["reverses_id"] == nuevo["id"] and D(str(f["amount"])) < 0]
    assert len(rev) == 1 and D(str(rev[0]["amount"])) == D("-95000.00") and rev[0]["status"] == "pendiente"


def test_quitar_aliado_con_movimientos(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000206"}).json()["id"]
    n = _negocio(client, ally)
    _acuerdo(client, n["aid"], HOY + timedelta(days=5))
    _completar(client, n["pr"])
    r = client.delete(f"/opportunity-allies/{n['aid']}")
    assert r.status_code == 409 and "Comisiones" in r.json()["detail"]
    assert client.delete(f"/opportunity-allies/requests/{n['rid']}").status_code == 409
    ids = [f["id"] for f in _mov(client)]
    assert client.patch("/commissions", json={"ids": ids, "status": "anulada", "reason": "Prueba de borrado"}).status_code == 200
    assert client.delete(f"/opportunity-allies/{n['aid']}").status_code == 200  # anulados: se puede; las reglas del acuerdo se van
    assert not [r for r in client.get("/commissions/rules").json() if r["participation_id"] == n["aid"]]


def test_mis_numeros_por_aliado(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000207"}).json()["id"]
    n = _negocio(client, ally)
    _acuerdo(client, n["aid"], HOY + timedelta(days=5))
    _completar(client, n["pr"])
    r = client.get("/commissions/numbers")
    assert r.status_code == 200, r.text
    num = r.json()
    fila = next(x for x in num["por_aliado"] if x["ally_company_id"] == ally)
    assert D(str(fila["pagado_al_aliado"])) == D("100000.00") and D(str(fila["comisiones_bonos"])) == D("60000.00")
    assert D(str(fila["venta_proyectos"])) == D("200000.00") and D(str(fila["utilidad_crimson"])) == D("40000.00")  # 200 000 - 100 000 - 60 000
    mes = next(m for m in num["meses"] if m["mes"] == HOY.strftime("%Y-%m"))
    assert mes["proyectos_cerrados"] >= 1 and D(str(mes["pagos_aliados"])) == D("100000.00")
    assert D(str(mes["utilidad_despues_comisiones"])) == D(str(mes["utilidad_bruta"])) - D(str(mes["comisiones_vendedores"])) - D(
        str(mes["comisiones_aliados"])
    )
    vh = interno(client, db_session, auth["tenant"]["id"], "vendedora@ejemplo.com", "ventas")
    assert client.get("/commissions/numbers", headers=vh).status_code == 403
    ch = interno(client, db_session, auth["tenant"]["id"], "contadora@ejemplo.com", "contabilidad")
    assert client.get("/commissions/numbers", headers=ch).status_code == 403  # contabilidad no ve costos por defecto
    assert client.get(f"/commissions/deals/{n['aid']}", headers=ch).status_code == 403


def test_tecnico_aliado_sin_comisiones(client, auth, db_session):
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000208"}).json()["id"]
    h = usuario(client, admin_h, a, "tec@aliado-uno.ejemplo.com", "tecnico_aliado")
    for path in ("/commissions", "/commissions/rules", "/commissions/summary", "/commissions/earnings", "/commissions/inbox", "/commissions/numbers"):
        assert client.get(path, headers=h).status_code == 403, path
    assert client.get("/aliado/ganancias", headers=h).status_code == 403
    assert db_session.query(Commission).count() == 0


# ---------- revision (economia) ----------
def test_participacion_oculta_no_sale_en_ganancias(client, auth, db_session):
    from app.models import AllyParticipation

    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000209"}).json()["id"]
    ha = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    n = _negocio(client, a)
    _acuerdo(client, n["aid"], HOY + timedelta(days=3))
    assert [x["project_id"] for x in client.get("/aliado/ganancias", headers=ha).json()["proyectos"]] == [n["pr"]["id"]]
    db_session.get(AllyParticipation, n["aid"]).visible_to_ally = False  # Crimson deja de compartirle el negocio
    db_session.commit()
    g = client.get("/aliado/ganancias", headers=ha).json()
    assert g["proyectos"] == [] and n["pr"]["name"] not in str(g) and "Mano de obra de fibra" not in str(g)
    assert client.get(f"/aliado/ganancias/proyectos/{n['pr']['id']}", headers=ha).status_code == 404
    assert client.get(f"/aliado/proyectos/{n['pr']['id']}", headers=ha).status_code == 404
    assert len(g["por_mes"]) == 24  # dos anos con el mes en curso (spec 9.8)


def test_precio_aprobado_al_crearlo_con_proyecto_completado(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000210"}).json()["id"]
    n = _negocio(client, ally, estado="recibido")
    _completar(client, n["pr"])
    assert not [f for f in _mov(client) if f["kind"] == "pago_acordado"]  # en revision: no se devenga
    r = client.post(f"/opportunity-allies/{n['aid']}/requests", json={"what": "Fusiones extra", "status": "aprobado", "amount": 30000, "currency": "CRC"})
    assert r.status_code == 201, r.text
    pagos = [f for f in _mov(client) if f["kind"] == "pago_acordado"]
    assert len(pagos) == 1 and D(str(pagos[0]["amount"])) == D("30000.00") and pagos[0]["status"] == "pendiente"
    assert db_session.get(Commission, pagos[0]["id"]).source_key.startswith("pago:")


def test_ajuste_del_pago_acordado_no_resta_dos_veces(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000211"}).json()["id"]
    n = _negocio(client, ally)
    _completar(client, n["pr"])
    pago = next(f for f in _mov(client) if f["kind"] == "pago_acordado")
    assert client.patch("/commissions", json={"ids": [pago["id"]], "status": "pagada", "paid_ref": "Transferencia 1"}).status_code == 200
    body = {"what": "Mano de obra de fibra", "status": "aprobado", "amount": 120000, "currency": "CRC"}
    assert client.put(f"/opportunity-allies/requests/{n['rid']}", json=body).status_code == 200
    assert [D(str(f["amount"])) for f in _mov(client) if f["kind"] == "ajuste"] == [D("20000.00")]
    eco = client.get(f"/projects/{n['pr']['id']}").json()["economics"]
    assert D(str(eco["cost_allies"])) == D("120000.00") and D(str(eco["profit"])) == D("80000.00")
    assert D(str(eco["commissions"])) == 0 and D(str(eco["profit_net"])) == D("80000.00")  # el ajuste ya esta en el costo
    mes = next(m for m in client.get("/commissions/numbers").json()["meses"] if m["mes"] == HOY.strftime("%Y-%m"))
    assert D(str(mes["comisiones_aliados"])) == 0 and D(str(mes["pagos_aliados"])) == D("120000.00")


def test_cambio_de_precio_al_cobrar_es_proporcional(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000212"}).json()["id"]
    assert client.put("/commissions/config", json={"ally_payout_trigger": "cobro"}).status_code == 200
    n = _negocio(client, ally)
    inv = client.post(f"/quotes/{n['qid']}/convert").json()
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 113000})  # 50 %
    body = {"what": "Mano de obra de fibra", "status": "aprobado", "amount": 120000, "currency": "CRC"}
    assert client.put(f"/opportunity-allies/requests/{n['rid']}", json=body).status_code == 200
    assert [D(str(f["amount"])) for f in _mov(client) if f["kind"] == "ajuste"] == [D("10000.00")]  # 50 % de 120 000 - 50 000
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 113000})  # el otro 50 %
    assert sum(D(str(f["amount"])) for f in _mov(client) if f["kind"] in ("pago_acordado", "ajuste")) == D("120000.00")


def test_devolucion_con_comision_y_pago_acordado_al_cobrar(client, auth, db_session):
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000213"}).json()["id"]
    assert client.put("/commissions/config", json={"ally_payout_trigger": "cobro"}).status_code == 200
    client.post("/commissions/rules", json={"beneficiary": "aliado", "name": "Todos los aliados", "percent": 10})
    n = _negocio(client, ally)
    inv = client.post(f"/quotes/{n['qid']}/convert").json()
    pay = "/invoices/{}/payments".format(inv["id"])
    client.post(pay, json={"method": "transferencia", "amount": 113000})  # 50 %
    client.post(pay, json={"method": "transferencia", "amount": 56500, "kind": "devolucion"})  # devuelve 25 %
    client.post(pay, json={"method": "transferencia", "amount": 226000})  # paga de mas: 100 %
    filas = _mov(client)
    com = sum(D(str(f["amount"])) for f in filas if not db_session.get(Commission, f["id"]).cost_request_id)
    pago = sum(D(str(f["amount"])) for f in filas if db_session.get(Commission, f["id"]).cost_request_id)
    assert com == D("20000.00") and pago == D("100000.00")  # 10 % de la venta y el precio: nunca de mas (falla B)


def test_precio_aprobado_despues_de_cobrar(client, auth, db_session):
    """E5: el precio se aprueba con el negocio ya cobrado y el pago acordado se devenga al cobrar (venta sin proyecto,
    o ally_payout_trigger = cobro): devenga la parte ya cobrada; no queda "proyectado" para siempre."""
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000214"}).json()["id"]
    body = {"what": "Mano de obra de fibra", "status": "aprobado", "amount": 100000, "currency": "CRC"}
    # venta sin proyecto, cobrada completa, precio en revision
    n = _negocio(client, ally, estado="recibido", proyecto=False)
    inv = client.post(f"/quotes/{n['qid']}/convert").json()
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 226000})
    assert not [f for f in _mov(client) if f["kind"] == "pago_acordado"]  # en revision: no se devenga
    assert client.put(f"/opportunity-allies/requests/{n['rid']}", json=body).status_code == 200
    pagos = [f for f in _mov(client) if f["kind"] == "pago_acordado"]
    assert len(pagos) == 1 and D(str(pagos[0]["amount"])) == D("100000.00") and pagos[0]["trigger"] == "cobro"
    assert pagos[0]["invoice_id"] == inv["id"] and pagos[0]["status"] == "pendiente"
    # repetir la aprobacion (otro guardado igual) no duplica
    assert client.put(f"/opportunity-allies/requests/{n['rid']}", json=body).status_code == 200
    assert len([f for f in _mov(client) if f["kind"] == "pago_acordado"]) == 1
    # con proyecto y pago acordado al cobrar: aprobado con el 50 % cobrado devenga 50 %; el otro cobro completa
    assert client.put("/commissions/config", json={"ally_payout_trigger": "cobro"}).status_code == 200
    n2 = _negocio(client, ally, estado="recibido", titulo="Red oficina sur")
    inv2 = client.post(f"/quotes/{n2['qid']}/convert").json()
    client.post(f"/invoices/{inv2['id']}/payments", json={"method": "transferencia", "amount": 113000})  # 50 %
    assert client.put(f"/opportunity-allies/requests/{n2['rid']}", json=body).status_code == 200
    de2 = [f for f in _mov(client, project_id=n2["pr"]["id"]) if f["kind"] == "pago_acordado"]
    assert [D(str(f["amount"])) for f in de2] == [D("50000.00")]
    g = client.get("/commissions/earnings", params={"ally_company_id": ally}).json()
    fila = next(x for x in g["proyectos"] if x["project_id"] == n2["pr"]["id"])
    assert D(str(fila["proyectado"])) == D("50000.00")
    client.post(f"/invoices/{inv2['id']}/payments", json={"method": "transferencia", "amount": 113000})
    de2 = [f for f in _mov(client, project_id=n2["pr"]["id"]) if f["kind"] == "pago_acordado"]
    assert sum(D(str(f["amount"])) for f in de2) == D("100000.00")
    g = client.get("/commissions/earnings", params={"ally_company_id": ally}).json()
    assert D(str(next(x for x in g["proyectos"] if x["project_id"] == n2["pr"]["id"])["proyectado"])) == 0


def test_proyecto_cancelado_no_proyecta(client, auth, db_session):
    admin_h = dict(client.headers)
    ally = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000215"}).json()["id"]
    ha = usuario(client, admin_h, ally, "jefe@aliado-uno.ejemplo.com", "aliado")
    n = _negocio(client, ally)
    g = client.get("/aliado/ganancias", headers=ha).json()
    assert D(str(g["totales"]["proyectado"])) == D("100000.00")
    r = client.put(f"/projects/{n['pr']['id']}", json={"name": n["pr"]["name"], "status": "cancelado"})
    assert r.status_code == 200, r.text
    for h in (ha, admin_h):
        path = "/aliado/ganancias" if h is ha else "/commissions/earnings"
        g = client.get(path, headers=h, params={} if h is ha else {"ally_company_id": ally}).json()
        assert D(str(g["totales"]["proyectado"])) == 0 and all(D(str(x["proyectado"])) == 0 for x in g["proyectos"])
    det = client.get(f"/aliado/ganancias/proyectos/{n['pr']['id']}", headers=ha).json()
    assert D(str(det["totales"]["proyectado"])) == 0 and D(str(det["proyectado"]["pago_acordado"])) == 0
