"""Bandeja de Aprobaciones (reunion 13/10, 21:05-22:23): "no se quien es la oficina ni donde reciben eso".

Junta lo que ya espera un visto bueno (levantamientos, cotizaciones, costos de aliados, reservas, accesos, planillas
y comisiones). Cada seccion exige el permiso de la accion real, usa la misma definicion que su lista y nunca muestra
montos a quien no los ve. Los conteos son relativos (antes/despues) por si los datos semilla suman algo."""

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.models import AccessRequest, Commission, EmailOutbox, PayrollRun, Survey, Tenant
from tests.test_campo import _login, _survey
from tests.test_inventario_reservas import _entrada, _new_product, _wh

# plan maestro 1.6 suma "levantamientos_solicitados" (al principio del flujo) y "cotizaciones_aceptadas"
KEYS = [
    "levantamientos_solicitados",
    "levantamientos_por_revisar",
    "levantamientos_por_costear",
    "cotizaciones_por_aprobar",
    "cotizaciones_aceptadas",
    "costos_aliados",
    "reservas_material",
    "accesos_portal",
    "planillas_por_aprobar",
    "comisiones_por_aprobar",
]


def _full(client, h=None) -> dict:
    r = client.get("/approvals", params={"limit": 50}, headers=h or {})
    assert r.status_code == 200, r.text
    return r.json()


def _sec(client, key, h=None) -> dict:
    return next(s for s in _full(client, h)["sections"] if s["key"] == key)


def _ids(sec: dict) -> list[int]:
    return [i["id"] for i in sec["items"]]


def test_aprobaciones_permisos_por_rol(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tk = _login(client, db_session, tid, "tec-apr@ejemplo.com", "tecnico")
    vd = _login(client, db_session, tid, "ven-apr@ejemplo.com", "ventas")
    sup = _login(client, db_session, tid, "sup-apr@ejemplo.com", "supervisor")
    ct = _login(client, db_session, tid, "con-apr@ejemplo.com", "contabilidad")

    # el tecnico y el vendedor no entran a la bandeja
    for h in (tk, vd):
        assert client.get("/approvals", headers=h).status_code == 403
        assert client.get("/approvals/summary", headers=h).status_code == 403

    # admin: todas las secciones, en el orden del flujo, aunque esten en 0
    assert [s["key"] for s in _full(client)["sections"]] == KEYS
    assert [s["key"] for s in client.get("/approvals/summary").json()["sections"]] == KEYS

    # supervisor: solo lo que el ya puede aprobar (sin costos: el costo es del administrador)
    keys = [s["key"] for s in _full(client, sup)["sections"]]
    assert "levantamientos_por_revisar" in keys and "reservas_material" in keys
    for k in ("levantamientos_por_costear", "cotizaciones_por_aprobar", "costos_aliados", "accesos_portal", "planillas_por_aprobar", "comisiones_por_aprobar"):
        assert k not in keys, k

    # contador: planillas y comisiones
    keys = [s["key"] for s in _full(client, ct)["sections"]]
    assert "planillas_por_aprobar" in keys and "comisiones_por_aprobar" in keys
    assert "levantamientos_por_revisar" not in keys and "cotizaciones_por_aprobar" not in keys

    # si el admin le habilita costos al supervisor (Ajustes -> Quien ve costos), le aparece la seccion de aliados sola
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "cost_roles": ["supervisor"]}
    db_session.commit()
    keys = [s["key"] for s in client.get("/approvals/summary", headers=sup).json()["sections"]]
    assert "costos_aliados" in keys and "levantamientos_por_costear" not in keys  # costear exige ademas sales.crear

    matrix = client.get("/auth/roles").json()["matrix"]
    assert matrix["supervisor"]["approvals"] == ["ver"] and matrix["contabilidad"]["approvals"] == ["ver"]
    for rol in ("tecnico", "ventas", "caja", "inventario", "lectura", "soporte"):
        assert "approvals" not in matrix[rol], rol


def test_aprobaciones_levantamiento_revisar_costear_y_cotizar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tk = _login(client, db_session, tid, "tec-apr2@ejemplo.com", "tecnico")
    sup = _login(client, db_session, tid, "sup-apr2@ejemplo.com", "supervisor")
    cust = client.get("/customers").json()["items"][0]
    revisar0 = _sec(client, "levantamientos_por_revisar")["count"]
    costear0 = _sec(client, "levantamientos_por_costear")["count"]

    s = _survey(client, headers=tk, customer_id=cust["id"], items=[{"name": "Cámara bullet", "quantity": 2}])
    sec = _sec(client, "levantamientos_por_revisar")
    assert sec["count"] == revisar0 and s["id"] not in _ids(sec)  # un borrador no espera a nadie

    assert client.post(f"/surveys/{s['id']}/send", headers=tk).status_code == 200
    sec = _sec(client, "levantamientos_por_revisar")
    assert sec["count"] == revisar0 + 1
    it = next(i for i in sec["items"] if i["id"] == s["id"])
    assert it["ref"] == s["number"] and it["to"] == f"/levantamientos?id={s['id']}" and it["amount"] is None
    assert it["title"] == cust["name"] and it["who"] and it["since"] and it["badge"] is None
    assert sec["reviewers"]  # "¿quien es la oficina?": a quien le llega el aviso
    # la lista filtrada dice lo mismo que la bandeja
    assert s["id"] in [x["id"] for x in client.get("/surveys", params={"bandeja": "por_revisar"}, headers=sup).json()]
    assert client.get("/surveys", params={"bandeja": "otra"}).status_code == 422

    # el aviso lleva directo al levantamiento y a Aprobaciones; ya nadie habla de "la oficina"
    correos = db_session.scalars(
        select(EmailOutbox).where(EmailOutbox.entity == "survey", EmailOutbox.entity_id == s["id"]).order_by(EmailOutbox.id.desc())
    ).all()
    assert correos and f"/levantamientos?id={s['id']}" in correos[0].html and "Aprobaciones" in correos[0].html
    assert all("oficina" not in m.html.lower() for m in correos)

    # el supervisor aprueba: sale de "por revisar" y entra a "por costear" (que solo ve quien costea)
    assert client.post(f"/surveys/{s['id']}/review", json={"action": "aprobar"}, headers=sup).json()["review_status"] == "aprobado"
    assert s["id"] not in _ids(_sec(client, "levantamientos_por_revisar"))
    sec = _sec(client, "levantamientos_por_costear")
    assert sec["count"] == costear0 + 1
    it = next(i for i in sec["items"] if i["id"] == s["id"])
    assert it["to"].endswith("&costear=1") and it["who"] == "Supervisor" and it["amount"] is None
    assert [x["id"] for x in client.get("/surveys", params={"bandeja": "por_costear"}).json()] == _ids(sec)

    # en la papelera no cuenta
    otro = _survey(client, headers=tk, customer_id=cust["id"], items=[{"name": "Cámara domo", "quantity": 1}])
    client.post(f"/surveys/{otro['id']}/send", headers=tk)
    assert _sec(client, "levantamientos_por_revisar")["count"] == revisar0 + 1
    db_session.get(Survey, otro["id"]).trashed_at = datetime.now(UTC)
    db_session.commit()
    assert _sec(client, "levantamientos_por_revisar")["count"] == revisar0
    assert otro["id"] not in [x["id"] for x in client.get("/surveys", params={"bandeja": "por_revisar"}).json()]

    # el resumen (numero del menu) y la bandeja cuentan lo mismo
    full = _full(client)
    summ = client.get("/approvals/summary").json()
    assert summ["total"] == full["total"] == sum(x["count"] for x in full["sections"])
    assert {x["key"]: x["count"] for x in summ["sections"]} == {x["key"]: x["count"] for x in full["sections"]}

    # el admin cotiza: sale de "por costear"
    assert client.post(f"/surveys/{s['id']}/quote", json={}).status_code == 201
    sec = _sec(client, "levantamientos_por_costear")
    assert sec["count"] == costear0 and s["id"] not in _ids(sec)

    # devuelto con observaciones: vuelve al tecnico y sale de la bandeja
    dev = _survey(client, headers=tk, customer_id=cust["id"], items=[{"name": "Cámara bullet", "quantity": 1}])
    client.post(f"/surveys/{dev['id']}/send", headers=tk)
    assert dev["id"] in _ids(_sec(client, "levantamientos_por_revisar"))
    r = client.post(f"/surveys/{dev['id']}/review", json={"action": "devolver", "note": "Faltan fotos del rack"}, headers=sup)
    assert r.json()["review_status"] == "devuelto"
    assert dev["id"] not in _ids(_sec(client, "levantamientos_por_revisar"))
    assert dev["id"] not in _ids(_sec(client, "levantamientos_por_costear"))
    devuelto = db_session.scalars(
        select(EmailOutbox).where(EmailOutbox.entity == "survey", EmailOutbox.entity_id == dev["id"]).order_by(EmailOutbox.id.desc())
    ).first()
    assert "devuelto" in devuelto.subject and f"/levantamientos?id={dev['id']}" in devuelto.html and "oficina" not in devuelto.html.lower()
    # al reenviar tras resolver, la bandeja lo marca
    for o in client.get(f"/surveys/{dev['id']}", headers=tk).json()["observations"]:
        client.post(f"/surveys/{dev['id']}/observations/{o['id']}", json={"resolution": "Listo"}, headers=tk)
    assert client.post(f"/surveys/{dev['id']}/send", headers=tk).status_code == 200
    it = next(i for i in _sec(client, "levantamientos_por_revisar")["items"] if i["id"] == dev["id"])
    assert it["badge"] == "Reenviado tras observaciones"


def test_aprobaciones_cotizacion_reserva_acceso_planilla_comision(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vd = _login(client, db_session, tid, "ven-apr3@ejemplo.com", "ventas")
    ct = _login(client, db_session, tid, "con-apr3@ejemplo.com", "contabilidad")
    antes = {s["key"]: s["count"] for s in _full(client)["sections"]}
    cust = client.get("/customers").json()["items"][0]

    # cotizacion sobre el tope: espera aprobacion del admin (que ve precios: lleva el monto)
    q = client.post(
        "/quotes", headers=vd, json={"customer_id": cust["id"], "lines": [{"name": "Proyecto grande", "quantity": 1, "unit_price": 2500000, "tax_rate": 13}]}
    ).json()
    assert q["status"] == "por_aprobar"
    sec = _sec(client, "cotizaciones_por_aprobar")
    assert sec["count"] == antes["cotizaciones_por_aprobar"] + 1
    it = next(i for i in sec["items"] if i["id"] == q["id"])
    assert it["to"] == f"/cotizaciones/{q['id']}" and it["amount"] is not None and it["currency"] and it["subtitle"]
    assert client.post(f"/quotes/{q['id']}/approve").status_code == 200
    assert q["id"] not in _ids(_sec(client, "cotizaciones_por_aprobar"))

    # reserva interna sin proyecto: espera confirmacion de bodega o del supervisor
    prod = _new_product(client, code="APR-RES", name="Cámara aprobaciones")
    w = _wh(client)
    _entrada(client, prod["id"], w["id"], 5)
    r = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 1})
    assert r.status_code == 201 and r.json()["status"] == "pendiente"
    sec = _sec(client, "reservas_material")
    assert sec["count"] == antes["reservas_material"] + 1
    it = next(i for i in sec["items"] if i["id"] == r.json()["id"])
    assert it["to"] == "/inventario?tab=reservas" and it["title"] == "Cámara aprobaciones" and it["amount"] is None

    # acceso al portal, planilla y comision (insertados directo) + lo mismo en otra empresa, que no debe contar
    otra = Tenant(slug="otra-aprobaciones", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    uid = auth["user"]["id"]
    for t in (tid, otra.id):
        db_session.add(AccessRequest(tenant_id=t, name="Persona Prueba", email="acceso@ejemplo.com", company="Empresa Ejemplo"))
        db_session.add(PayrollRun(tenant_id=t, period_start=date(2026, 10, 1), period_end=date(2026, 10, 15)))
        db_session.add(Commission(tenant_id=t, user_id=uid, amount=1000, earned_on=date.today()))
    db_session.commit()
    full = _full(client)
    now = {s["key"]: s for s in full["sections"]}
    for k in ("accesos_portal", "planillas_por_aprobar", "comisiones_por_aprobar"):
        assert now[k]["count"] == antes[k] + 1, k
    acc = now["accesos_portal"]["items"][-1]
    assert acc["title"] == "Empresa Ejemplo" and acc["subtitle"] == "Persona Prueba · acceso@ejemplo.com" and acc["to"] == "/accesos-clientes"
    assert all(i["amount"] is None for i in now["planillas_por_aprobar"]["items"])  # planillas: sin montos
    pl = now["planillas_por_aprobar"]["items"][-1]
    assert pl["title"].startswith("Planilla mensual 01/10") and pl["to"] == "/planillas?tab=planillas"
    com = now["comisiones_por_aprobar"]["items"][-1]
    assert com["to"] == "/comisiones?tab=bandeja" and com["amount"] is not None  # contrato economia 3: la bandeja agrupada
    # nada de montos en levantamientos, reservas ni accesos
    for k in ("levantamientos_por_revisar", "levantamientos_por_costear", "reservas_material", "accesos_portal"):
        assert all(i["amount"] is None for i in now[k]["items"]), k

    # el contador ve planillas y comisiones con los mismos numeros
    cont = {s["key"]: s["count"] for s in _full(client, ct)["sections"]}
    assert cont["planillas_por_aprobar"] == now["planillas_por_aprobar"]["count"]
    assert cont["comisiones_por_aprobar"] == now["comisiones_por_aprobar"]["count"]


def test_aprobaciones_comision_sobre_margen_sin_porcentaje_para_quien_no_ve_costos(client, auth, db_session):
    """Monto / % = utilidad de la venta: el % de una comision sobre margen es informacion de costos."""
    tid = auth["tenant"]["id"]
    ct = _login(client, db_session, tid, "con-apr4@ejemplo.com", "contabilidad")
    uid = auth["user"]["id"]
    margen = Commission(tenant_id=tid, user_id=uid, base="margen", percent=10, base_amount=10000, amount=1000, earned_on=date.today())
    venta = Commission(tenant_id=tid, user_id=uid, base="venta", percent=5, base_amount=20000, amount=1000, earned_on=date.today())
    db_session.add_all([margen, venta])
    db_session.commit()

    def item(h, cid):
        return next(i for i in _sec(client, "comisiones_por_aprobar", h)["items"] if i["id"] == cid)

    # el admin ve costos: el % completo
    assert item(None, margen.id)["subtitle"] == "Sobre margen · 10 %"
    # contabilidad (sin catalog.costos): el monto a aprobar si, el % sobre margen no; sobre venta no revela costos
    m = item(ct, margen.id)
    assert m["subtitle"] == "Sobre margen" and m["amount"] is not None
    assert item(ct, venta.id)["subtitle"] == "Sobre venta · 5 %"

    # si el admin le habilita costos a contabilidad (Ajustes -> Quien ve costos), vuelve el %
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "cost_roles": ["contabilidad"]}
    db_session.commit()
    assert item(ct, margen.id)["subtitle"] == "Sobre margen · 10 %"


def test_aprobaciones_costo_de_aliado_solo_cuando_llega(client, auth):
    cliente = client.post("/customers", json={"name": "Cliente Ejemplo SA", "id_type": "juridica"}).json()
    assert client.post("/suppliers", json={"name": "Aliado Ejemplo"}).status_code == 201
    aliado = next(s for s in client.get("/suppliers").json() if s["name"] == "Aliado Ejemplo")
    o = client.post("/opportunities", json={"title": "Red de prueba", "customer_id": cliente["id"], "amount": 0}).json()
    r = client.post(f"/opportunities/{o['id']}/allies", json={"supplier_id": aliado["id"], "role": "subcontratista"})
    assert r.status_code == 201, r.text
    antes = _sec(client, "costos_aliados")["count"]

    r = client.post(f"/opportunity-allies/{r.json()[0]['id']}/requests", json={"what": "Costo de fibra", "responsible_id": auth["user"]["id"]})
    req = r.json()[0]["requests"][0]
    # "solicitado" espera al aliado, no a nosotros
    sec = _sec(client, "costos_aliados")
    assert sec["count"] == antes and req["id"] not in _ids(sec)

    body = {"what": "Costo de fibra", "responsible_id": auth["user"]["id"], "status": "recibido", "amount": "1234.50", "currency": "USD"}
    assert client.put(f"/opportunity-allies/requests/{req['id']}", json=body).status_code == 200
    sec = _sec(client, "costos_aliados")
    assert sec["count"] == antes + 1
    it = next(i for i in sec["items"] if i["id"] == req["id"])
    assert it["to"] == f"/oportunidades?id={o['id']}" and it["ref"] == o["number"] and it["title"] == "Aliado Ejemplo"
    assert it["amount"] is not None and it["currency"] == "USD"  # el admin ve costos

    assert client.put(f"/opportunity-allies/requests/{req['id']}", json={**body, "status": "aprobado"}).status_code == 200
    sec = _sec(client, "costos_aliados")
    assert sec["count"] == antes and req["id"] not in _ids(sec)
