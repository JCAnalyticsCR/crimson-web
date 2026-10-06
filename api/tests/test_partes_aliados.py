"""Grupo B: partes de la oportunidad, aliados con solicitudes de costo y "aportado por".

Caso real (Andres): Nodo Latam contrata a Crimson para Yobel; Crimson contrata a Hauset la fibra; Nodo aporta el
Fortinet. Se prueba que el contratante sigue siendo a quien se cotiza, que todo deja bitacora, que la tarjeta del
inicio y la lista dicen lo mismo, que la vendedora no ve costos de aliados y que el portal del cliente no ve nada."""

from datetime import date, timedelta

import pytest

from app.models import AllyCostRequest, AllyParticipation, Opportunity

from .test_comercial import _login


def _partes(client):
    nodo = client.post("/customers", json={"name": "Nodo Latam", "id_type": "juridica"}).json()
    yobel = client.post("/customers", json={"name": "Yobel", "id_type": "juridica"}).json()
    assert client.post("/suppliers", json={"name": "Hauset"}).status_code == 201
    hauset = next(s for s in client.get("/suppliers").json() if s["name"] == "Hauset")
    return nodo, yobel, hauset


def test_oportunidad_yobel_partes_aliados_y_aportado(client, auth):
    nodo, yobel, hauset = _partes(client)
    body = {"title": "Yobel · red", "customer_id": nodo["id"], "end_customer_id": yobel["id"], "site": "CEDI Heredia", "amount": 0}
    o = client.post("/opportunities", json=body).json()
    assert o["customer"] == "Nodo Latam" and o["end_customer"] == "Yobel" and o["site"] == "CEDI Heredia"

    # cambiar el sitio deja la frase de partes en la bitacora
    r = client.put(f"/opportunities/{o['id']}", json={**body, "site": "CEDI Heredia, bodega 2"})
    assert "Partes: Nodo Latam → para Yobel · CEDI Heredia, bodega 2" in r.json()["notes"]

    # aliado Hauset subcontratista con contacto tecnico
    r = client.post(
        f"/opportunities/{o['id']}/allies",
        json={"supplier_id": hauset["id"], "role": "subcontratista", "scope": "Fibra entre bodegas", "contacts": {"tecnico": {"name": "Luis", "phone": "8888-0000"}}},
    )
    assert r.status_code == 201, r.text
    ally = r.json()[0]
    assert ally["name"] == "Hauset" and ally["company_kind"] == "supplier" and ally["contacts"]["tecnico"]["name"] == "Luis"
    assert client.post(f"/opportunities/{o['id']}/allies", json={"supplier_id": hauset["id"], "role": "subcontratista"}).status_code == 409
    assert client.post(f"/opportunities/{o['id']}/allies", json={"role": "referido"}).status_code == 422
    assert client.post(f"/opportunities/{o['id']}/allies", json={"supplier_id": hauset["id"], "role": "jefe"}).status_code == 422

    # solicitud de costo de fibra, vencida ayer
    ayer = (date.today() - timedelta(days=1)).isoformat()
    r = client.post(f"/opportunity-allies/{ally['id']}/requests", json={"what": "Costo de fibra", "responsible_id": auth["user"]["id"], "due_date": ayer})
    assert r.status_code == 201, r.text
    req = r.json()[0]["requests"][0]
    assert req["pending"] and req["overdue"] and req["status"] == "solicitado" and req["responsible"]

    det = client.get(f"/opportunities/{o['id']}").json()
    assert det["ally_pending"] == 1 and det["allies"][0]["requests"][0]["overdue"]
    assert "Aliado Hauset agregado como subcontratista" in det["notes"] and "Solicitud de costo a Hauset: Costo de fibra" in det["notes"]

    # la tarjeta del inicio y la lista de pendientes salen de la misma definicion
    pend = client.get("/opportunity-allies/pending").json()
    assert client.get("/dashboard").json()["acciones_pendientes"]["costos_aliados"] == len(pend) == 1
    assert pend[0]["ally"] == "Hauset" and pend[0]["opportunity_number"] == o["number"]

    # cotizacion desde la oportunidad: se cotiza al contratante; el Fortinet lo aporta Nodo
    qr = client.post(f"/opportunities/{o['id']}/quote", json={}).json()
    lines = [
        {"name": "FortiGate 60F", "quantity": 1, "unit_price": 0, "treatment": "aportado", "supplied_by": "Nodo Latam"},
        {"name": "Configuración", "quantity": 1, "unit_price": 450, "tax_rate": 13},
        {"name": "Cable", "quantity": 1, "unit_price": 10, "tax_rate": 13, "supplied_by": "Nodo Latam"},  # no aportado: se ignora
    ]
    q = client.put(f"/quotes/{qr['quote_id']}", json={"customer_id": nodo["id"], "currency": "USD", "lines": lines}).json()
    assert q["customer_id"] == nodo["id"]
    assert q["lines"][0]["supplied_by"] == "Nodo Latam" and q["lines"][2]["supplied_by"] is None
    assert [x["name"] for x in q["opportunity"]["parties"]] == ["Nodo Latam", "Yobel", "Hauset"]
    html = client.get(f"/quotes/{q['id']}/html").text
    assert "Aportado por Nodo Latam" in html

    # el costo llega y se aprueba (admin ve costos): sale de pendientes, el monto nunca va a la bitacora
    r = client.put(
        f"/opportunity-allies/requests/{req['id']}",
        json={"what": "Costo de fibra", "responsible_id": auth["user"]["id"], "due_date": ayer, "status": "recibido", "amount": "1234.50", "currency": "USD", "exclusions": "Sin obra civil"},
    )
    got = r.json()[0]["requests"][0]
    assert got["amount"] in ("1234.50", 1234.5) and got["exclusions"] == "Sin obra civil" and got["pending"] and not got["overdue"]
    r = client.put(f"/opportunity-allies/requests/{req['id']}", json={"what": "Costo de fibra", "status": "aprobado", "amount": "1234.50", "currency": "USD"})
    assert not r.json()[0]["requests"][0]["pending"]
    det = client.get(f"/opportunities/{o['id']}").json()
    assert det["ally_pending"] == 0 and "recibido → aprobado" in det["notes"] and "1234" not in det["notes"]
    assert client.get("/dashboard").json()["acciones_pendientes"]["costos_aliados"] == 0

    # proyecto desde la cotizacion: hereda cliente final, sitio y aliados
    pr = client.post(f"/quotes/{q['id']}/project", json={})
    assert pr.status_code == 201, pr.text
    pr = pr.json()
    assert pr["customer"] == "Nodo Latam" and pr["end_customer"] == "Yobel" and pr["site"] == "CEDI Heredia, bodega 2"
    assert [a["name"] for a in pr["allies"]] == ["Hauset"] and "requests" not in pr["allies"][0]

    # quitar el aliado deja linea en la bitacora
    client.delete(f"/opportunity-allies/{ally['id']}")
    assert "Aliado Hauset quitado (con 1 solicitud(es) de costo)" in client.get(f"/opportunities/{o['id']}").json()["notes"]


def test_vendedora_no_ve_costos_ni_oportunidades_ajenas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    nodo, _yobel, hauset = _partes(client)
    ajena = client.post("/opportunities", json={"title": "Ajena", "customer_id": nodo["id"]}).json()
    a_ajeno = client.post(f"/opportunities/{ajena['id']}/allies", json={"supplier_id": hauset["id"]}).json()[0]
    client.post(f"/opportunity-allies/{a_ajeno['id']}/requests", json={"what": "Fibra ajena"})

    vh = _login(client, admin_h, db_session, tid, "vende@crimson.cr", "ventas")
    # lo ajeno no existe para ella
    assert client.get(f"/opportunities/{ajena['id']}/allies", headers=vh).status_code == 404
    assert client.post(f"/opportunity-allies/{a_ajeno['id']}/requests", json={"what": "Otra cosa"}, headers=vh).status_code == 404
    assert client.get("/opportunity-allies/pending", headers=vh).json() == []
    assert client.get("/dashboard", headers=vh).json()["acciones_pendientes"]["costos_aliados"] == 0

    mia = client.post("/opportunities", json={"title": "Mía", "customer_id": nodo["id"]}, headers=vh).json()
    a = client.post(f"/opportunities/{mia['id']}/allies", json={"customer_id": nodo["id"], "role": "contratante"}, headers=vh).json()[0]
    r = client.post(f"/opportunity-allies/{a['id']}/requests", json={"what": "Licencias", "amount": "10"}, headers=vh)
    assert r.status_code == 403  # el monto es de quien ve costos
    r = client.post(f"/opportunity-allies/{a['id']}/requests", json={"what": "Licencias"}, headers=vh)
    req = r.json()[0]["requests"][0]
    assert req["costs_visible"] is False and "amount" not in req and "attachments" not in req
    assert client.get("/dashboard", headers=vh).json()["acciones_pendientes"]["costos_aliados"] == len(client.get("/opportunity-allies/pending", headers=vh).json()) == 1
    # puede marcarla recibida pero no aprobarla
    assert client.put(f"/opportunity-allies/requests/{req['id']}", json={"what": "Licencias", "status": "recibido"}, headers=vh).status_code == 200
    assert client.put(f"/opportunity-allies/requests/{req['id']}", json={"what": "Licencias", "status": "aprobado"}, headers=vh).status_code == 403

    # el admin si ve el monto que registra
    r = client.put(f"/opportunity-allies/requests/{req['id']}", json={"what": "Licencias", "status": "recibido", "amount": "99", "currency": "USD"}, headers=admin_h)
    assert r.json()[0]["requests"][0]["amount"] in ("99.00", 99, "99")
    # y la vendedora sigue sin verlo
    det = client.get(f"/opportunities/{mia['id']}", headers=vh).json()
    assert "amount" not in det["allies"][0]["requests"][0] and det["sees_costs"] is False


def test_portal_del_cliente_no_ve_aliados(client, auth, db_session):
    from .test_portal_cliente import _crear_cliente_portal

    admin_h = dict(client.headers)
    nodo, yobel, hauset = _partes(client)
    o = client.post("/opportunities", json={"title": "Yobel", "customer_id": nodo["id"], "end_customer_id": yobel["id"]}).json()
    a = client.post(f"/opportunities/{o['id']}/allies", json={"supplier_id": hauset["id"]}).json()[0]
    client.post(f"/opportunity-allies/{a['id']}/requests", json={"what": "Fibra"})

    ch = _crear_cliente_portal(client, admin_h, nodo["id"], "ti@nodo.lat", "cliente_admin", "Ana Nodo")
    for path in ("/opportunity-allies/pending", f"/opportunities/{o['id']}/allies", f"/opportunities/{o['id']}", "/opportunity-allies/companies"):
        assert client.get(path, headers=ch).status_code == 403, path
    assert client.post(f"/opportunity-allies/{a['id']}/requests", json={"what": "Otra cosa"}, headers=ch).status_code == 403
    for path in ("/cliente/inicio", "/cliente/documentos", "/cliente/equipos"):
        r = client.get(path, headers=ch)
        assert r.status_code == 200, path
        assert "Hauset" not in r.text and "Fibra" not in r.text


def test_ejemplo_yobel_y_purga(client, auth, db_session, monkeypatch):
    from app.core.config import settings
    from app.seeds_partes import seed_ejemplo_yobel
    from app.services import archive

    tid, uid = auth["tenant"]["id"], auth["user"]["id"]
    o = seed_ejemplo_yobel(db_session, tid, uid)
    db_session.commit()
    assert seed_ejemplo_yobel(db_session, tid, uid).id == o.id  # idempotente
    det = client.get(f"/opportunities/{o.id}").json()
    assert det["customer"] == "Nodo Latam" and det["end_customer"] == "Yobel" and det["ally_pending"] == 1
    assert det["allies"][0]["name"] == "Hauset" and det["allies"][0]["role"] == "subcontratista"
    q = client.get(f"/quotes/{o.quote_id}").json()
    assert q["lines"][0]["treatment"] == "aportado" and q["lines"][0]["supplied_by"] == "Nodo Latam"

    # la papelera purga la oportunidad con sus aliados y solicitudes
    archive.trash(db_session, "opportunity", db_session.get(Opportunity, o.id), uid)
    archive.purge(db_session, "opportunity", db_session.get(Opportunity, o.id), uid)
    db_session.commit()
    assert db_session.query(AllyParticipation).count() == 0 and db_session.query(AllyCostRequest).count() == 0

    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(RuntimeError):
        seed_ejemplo_yobel(db_session, tid, uid)
