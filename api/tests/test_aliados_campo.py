"""Campo, soporte y precios del aliado (aliado_final §12.4, C1-C7). C8 (el refactor de fieldwork no cambia nada
interno) lo cubre la suite completa: test_campo*, test_grupo_b, test_partes_aliados, test_portal_cliente e inventario."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from tests.aliados_helpers import escenario, ids, interno, login, participa, proyecto, uid


def _opp_abierta(client, s, titulo="Oportunidad abierta en común"):
    o = client.post("/opportunities", json={"title": titulo, "customer_id": s.c1, "status": "contactado"}).json()
    participa(client, s.admin_h, o["id"], s.A)
    return o["id"]


def test_aliado_hace_levantamiento_en_oportunidad_comun(client, auth, db_session):
    from app.models import Opportunity, Survey

    s = escenario(client, dict(client.headers), db_session)
    o7 = _opp_abierta(client, s)
    body = {"opportunity_id": o7, "kind": "cctv", "site": "Bodega 3", "items": [{"name": "Cable UTP", "quantity": 20, "unit": "m"}]}
    assert client.post("/aliado/levantamientos", json={**body, "customer_id": s.c2}, headers=s.a_jefe).status_code == 422
    r = client.post("/aliado/levantamientos", json=body, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    sv = db_session.get(Survey, sid)
    assert sv.ally_company_id == s.A and sv.customer_id == s.c1 and sv.technician_id == s.uid_a_jefe
    assert db_session.get(Opportunity, o7).status == "levantamiento"
    assert client.post("/aliado/levantamientos", json={**body, "opportunity_id": s.O2}, headers=s.a_jefe).status_code == 404
    assert client.post("/aliado/levantamientos", json={"kind": "cctv"}, headers=s.a_jefe).status_code == 422
    assert client.post("/aliado/levantamientos", json={**body, "technician_id": s.uid_b_tec}, headers=s.a_jefe).status_code == 422
    r = client.post(f"/aliado/levantamientos/{sid}/enviar", headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["review_status"] == "pendiente" and r.json()["notified"] == [{"name": "Crimson"}], r.text
    assert client.put(f"/aliado/levantamientos/{sid}", json={"kind": "cctv"}, headers=s.a_jefe).status_code == 409  # en revision
    sup = interno(client, db_session, s.tid, "supervisor.campo@ejemplo.com", "supervisor")
    vista = client.get(f"/surveys/{sid}", headers=sup).json()
    assert vista["ally_company"] == "Aliado Uno S.A." and vista["ally_company_id"] == s.A
    r = client.post(f"/surveys/{sid}/review", json={"action": "devolver", "observations": [{"text": "Falta la altura de las cámaras"}]}, headers=sup)
    assert r.status_code == 200, r.text
    det = client.get(f"/aliado/levantamientos/{sid}", headers=s.a_jefe).json()
    obs = det["observations"][0]
    assert obs["created_by"] == "Crimson" and "Supervisor" not in str(det) and det["review_status"] == "devuelto"
    r = client.post(f"/aliado/levantamientos/{sid}/observaciones/{obs['id']}", json={"resolution": "Agregada"}, headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["observations"][0]["resolved"] is True
    r = client.post(f"/aliado/levantamientos/{sid}/enviar", headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["review_status"] == "pendiente"


def test_guardar_no_borra_lo_interno(client, auth, db_session):
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    sv = db_session.get(Survey, s.S_A)
    r = client.put(
        f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "items": [{"name": "Cámara domo", "quantity": 2, "product_id": s.prod}]}, headers=s.a_tec
    )
    assert r.status_code == 200, r.text
    item_id = r.json()["items"][0]["id"]
    assert client.put(f"/surveys/{s.S_A}/labor-rates", json={"tecnico": 40000}).status_code == 200
    assert client.post(f"/surveys/{s.S_A}/costs", json=[{"item_id": item_id, "unit_cost": 12345}]).status_code == 200
    r = client.put(
        f"/aliado/levantamientos/{s.S_A}",
        json={"kind": "cctv", "site": "Nuevo sitio", "items": [{"id": item_id, "name": "Cámara domo", "quantity": 3, "product_id": s.prod}]},
        headers=s.a_tec,
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    sv = db_session.get(Survey, s.S_A)
    assert sv.site == "Nuevo sitio" and (sv.labor or {}).get("tarifas", {}).get("tecnico") == "40000"
    assert Decimal(str(sv.items[0].unit_cost)) == Decimal("12345") and Decimal(str(sv.items[0].quantity)) == 3
    assert "unit_cost" not in r.text and "tarifas" not in r.text


def test_asignacion_interna_valida_empresa_y_proyecto(client, auth, db_session):
    from app.models import WorkOrder

    s = escenario(client, dict(client.headers), db_session)
    base = {"title": "Instalación de cámaras", "scheduled_at": datetime.now(UTC).isoformat()}
    r = client.post("/work-orders", json={**base, "project_id": s.P2, "technician_id": s.uid_a_tec})
    assert r.status_code == 422 and "no participa" in r.json()["detail"]
    r = client.post("/work-orders", json={**base, "project_id": s.P1, "technician_id": s.uid_a_tec})
    assert r.status_code == 201, r.text
    ot = r.json()
    assert ot["ally_company_id"] == s.A and ot["ally_company"] == "Aliado Uno S.A."
    r = client.post("/work-orders", json={**base, "project_id": s.P3, "technician_id": s.uid_a_tec, "helpers": [s.uid_b_tec]})
    assert r.status_code == 422 and "dos empresas" in r.json()["detail"]
    assert client.put(f"/work-orders/{ot['id']}", json={"site": "Otro sitio"}).status_code == 200
    assert db_session.get(WorkOrder, ot["id"]).ally_company_id == s.A  # PUT sin el campo no la borra
    con = client.get(f"/field/technicians?project_id={s.P1}").json()
    a_tec = next(x for x in con if x["id"] == s.uid_a_tec)
    assert a_tec["company"] == "Aliado Uno S.A." and a_tec["ally_company_id"] == s.A
    assert s.uid_b_tec not in ids(con)
    assert s.uid_a_tec not in ids(client.get("/field/technicians").json())
    # el aliado ve la orden nueva en su portal y su tecnico en "hoy"
    assert ot["id"] in ids(client.get("/aliado/ordenes", headers=s.a_jefe).json())
    assert ot["id"] in ids(client.get("/aliado/mi-dia", headers=s.a_tec).json()["ordenes"])


def test_tecnico_aliado_finaliza_ot(client, auth, db_session):
    from app.models import Project, Warehouse
    from app.services import inventory as invsvc

    s = escenario(client, dict(client.headers), db_session)
    o = _opp_abierta(client, s, "Proyecto chico del aliado")
    pr = proyecto(db_session, s.tid, o, "Proyecto chico", s.c1)
    wh = db_session.scalars(select(Warehouse).where(Warehouse.tenant_id == s.tid, Warehouse.is_default.is_(True))).first()
    invsvc.move(db_session, s.tid, s.admin_id, s.prod, wh.id, "entrada", Decimal(5), unit_cost=100000)
    db_session.commit()
    mats = [{"product_id": s.prod, "name": "Cámara demo aliado", "quantity": 0, "planned": 2}]
    r = client.post(
        "/work-orders",
        json={"title": "Instalar", "project_id": pr.id, "technician_id": s.uid_a_tec, "scheduled_at": datetime.now(UTC).isoformat(), "materials": mats},
    )
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert client.post(f"/aliado/ordenes/{oid}/cancel", json={}, headers=s.a_tec).status_code == 404
    assert client.post(f"/aliado/ordenes/{oid}/start", json={}, headers=s.a_tec).status_code == 200
    r = client.post(
        f"/aliado/ordenes/{oid}/finish",
        json={"materials": [{"product_id": s.prod, "name": "Cámara demo aliado", "quantity": 2, "planned": 2}], "customer_signature": "Recibe Conforme"},
        headers=s.a_tec,
    )
    assert r.status_code == 200 and r.json()["status"] == "finalizada", r.text
    assert invsvc.on_hand(db_session, s.tid, s.prod, wh.id) == 3
    assert client.post(f"/aliado/ordenes/{oid}/finish", json={}, headers=s.a_tec).status_code == 409
    assert invsvc.on_hand(db_session, s.tid, s.prod, wh.id) == 3  # una sola vez
    db_session.expire_all()
    assert db_session.get(Project, pr.id).status == "terminado"
    # materiales de otra empresa o inexistentes: 404
    r = client.post("/work-orders", json={"title": "Otra", "project_id": pr.id, "technician_id": s.uid_a_tec, "scheduled_at": datetime.now(UTC).isoformat()})
    assert (
        client.post(f"/aliado/ordenes/{r.json()['id']}/progress", json={"materials": [{"product_id": 999999, "name": "x"}]}, headers=s.a_tec).status_code == 404
    )


def test_ticket_del_aliado(client, auth, db_session):
    from app.core.security import hash_password
    from app.models import SupportTicket, TenantUser, User

    s = escenario(client, dict(client.headers), db_session)
    body = {"project_id": s.P1, "subject": "Cámara sin imagen", "kind": "soporte", "priority": "alta", "description": "No da video desde ayer"}
    r = client.post("/aliado/tickets", json=body, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    t = db_session.get(SupportTicket, tid)
    assert t.ally_company_id == s.A and t.customer_id == s.c1 and t.assigned_to is None and t.sla
    assert client.post("/aliado/tickets", json={**body, "project_id": s.P2}, headers=s.a_jefe).status_code == 404
    assert client.post("/aliado/tickets", json={**body, "asset_id": s.asset_otro_cliente}, headers=s.a_jefe).status_code == 404
    assert client.post("/aliado/tickets", json=body, headers=s.a_tec).status_code == 403
    r = client.post(f"/aliado/tickets/{tid}/notas", json={"body": "Revisamos el cableado"}, headers=s.a_jefe)
    assert r.status_code == 201 and r.json()["notes"][-1]["internal"] is True
    u = User(email="cliente.uno@ejemplo.com", full_name="Cliente Uno", password_hash=hash_password("Aliado-prueba-2026"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=s.tid, user_id=u.id, role_code="cliente_admin", customer_id=s.c1))
    db_session.commit()
    cli = login(client, u.email)
    vista = client.get(f"/cliente/tickets/{tid}", headers=cli)
    assert vista.status_code == 200 and "Revisamos el cableado" not in vista.text
    r = client.patch(f"/aliado/tickets/{tid}", json={"status": "resuelto", "solution": "Se cambió el conector"}, headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["resolved_at"] and r.json()["status"] == "resuelto", r.text


def test_asignar_ticket_y_contrato_internos(client, auth, db_session):
    from app.models import SupportTicket, WorkOrder

    s = escenario(client, dict(client.headers), db_session)
    body = {"subject": "Falla TK-NULL", "customer_id": s.c1, "project_id": s.P1, "assigned_to": s.uid_a_tec, "priority": "media", "kind": "soporte"}
    r = client.put(f"/tickets/{s.T_null}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["ally_company_id"] == s.A and r.json()["ally_company"] == "Aliado Uno S.A."
    r = client.post(f"/tickets/{s.T_null}/work-order", json={"technician_id": s.uid_a_tec})
    assert r.status_code == 201, r.text
    assert db_session.get(WorkOrder, r.json()["work_order_id"]).ally_company_id == s.A
    r = client.post(f"/contracts/{s.K_A}/ticket")
    assert r.status_code == 201, r.text
    assert db_session.get(SupportTicket, r.json()["id"]).ally_company_id == s.A
    agentes = client.get(f"/tickets/meta/agents?ally_company_id={s.A}").json()
    assert {x["id"] for x in agentes if x["company"] == "Aliado Uno S.A."} >= {s.uid_a_tec, s.uid_a_jefe}
    assert s.uid_a_tec not in {x["id"] for x in client.get("/tickets/meta/agents").json()}
    # nota interna compartida con el aliado
    client.post(f"/tickets/{s.T_null}/notes", json={"body": "Compartida con el aliado", "internal": True, "share_ally": True})
    client.post(f"/tickets/{s.T_null}/notes", json={"body": "Solo para Crimson", "internal": True})
    txt = client.get(f"/aliado/tickets/{s.T_null}", headers=s.a_jefe).text
    assert "Compartida con el aliado" in txt and "Solo para Crimson" not in txt
    # el filtro a:<id> de la bandeja trae los asignados explicitamente a la empresa
    assert s.T_null in {t["id"] for t in client.get(f"/tickets?ally=a:{s.A}").json()}
    # contrato con empresa aliada: si tiene proyecto, la empresa tiene que participar
    k = {"customer_id": s.c1, "name": "Mantenimiento del aliado", "project_id": s.P2, "ally_company_id": s.A}
    assert client.post("/contracts", json=k).status_code == 422
    r = client.post("/contracts", json={**k, "project_id": s.P1})
    assert r.status_code == 201 and r.json()["ally_company_id"] == s.A


def test_aliado_responde_y_envia_precios(client, auth, db_session):
    from app.models import AllyCostRequest, EmailOutbox

    s = escenario(client, dict(client.headers), db_session)
    vend = interno(client, db_session, s.tid, "vendedora.precios@ejemplo.com", "ventas")
    o = client.post("/opportunities", json={"title": "Oportunidad de la vendedora", "customer_id": s.c1}, headers=vend).json()
    aid = participa(client, s.admin_h, o["id"], s.A)
    r = client.post(f"/opportunity-allies/{aid}/requests", json={"what": "Precio de instalación de 8 cámaras", "due_date": "2026-12-01"})
    assert r.status_code == 201, r.text
    rid = next(x["id"] for a in r.json() for x in a["requests"])
    mail = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "ally_cost_request", EmailOutbox.entity_id == rid)).all()
    assert [m.to for m in mail] == ["jefe@aliado-uno.ejemplo.com"] and "₡" not in mail[0].html
    lines = [{"concept": "Cámara instalada", "quantity": 8, "unit": "u", "unit_price": 1500.5}, {"concept": "Configuración", "quantity": 1, "unit_price": 3000}]
    r = client.put(f"/aliado/solicitudes/{rid}", json={"lines": lines, "currency": "CRC"}, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "respondido" and Decimal(str(r.json()["amount"])) == Decimal("15004.00")
    assert client.put(f"/aliado/solicitudes/{rid}", json={"amount": 1, "lines": lines, "currency": "CRC"}, headers=s.a_jefe).status_code == 422
    req = next(x for a in client.get(f"/opportunities/{o['id']}/allies", headers=vend).json() for x in a["requests"])
    assert "amount" not in req and "lines" not in req and req["responded_at"] and req["source"] == "crimson"
    notas = client.get(f"/opportunities/{o['id']}", headers=vend).json()["notes"]
    assert "Aliado Uno S.A." in notas and "15004" not in notas and "15,004" not in notas
    adm = next(x for a in client.get(f"/opportunities/{o['id']}/allies").json() for x in a["requests"])
    assert len(adm["lines"]) == 2  # quien ve costos ve el desglose
    r = client.put(f"/opportunity-allies/requests/{rid}", json={"what": adm["what"], "status": "aprobado", "amount": "15004", "currency": "CRC"})
    assert r.status_code == 200, r.text
    assert client.put(f"/aliado/solicitudes/{rid}", json={"amount": 9000, "currency": "CRC"}, headers=s.a_jefe).status_code == 409
    r = client.post(f"/aliado/proyectos/{s.P1}/precios", json={"what": "Ampliación de 2 cámaras", "amount": 5000, "currency": "CRC"}, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    nueva = db_session.get(AllyCostRequest, r.json()["id"])
    from app.models import Opportunity

    assert nueva.source == "aliado" and nueva.status == "respondido" and nueva.responsible_id == db_session.get(Opportunity, s.O1).owner_id
    r = client.post(f"/aliado/proyectos/{s.P3}/precios", json={"what": "Precio sin papel", "amount": 100, "currency": "CRC"}, headers=s.a_jefe)
    assert r.status_code == 422 and "papel" in r.json()["detail"]
    r = client.post(
        f"/aliado/proyectos/{s.P3}/precios",
        json={"what": "Precio con papel", "amount": 100, "currency": "CRC", "participation_id": s.part_O3_A2},
        headers=s.a_jefe,
    )
    assert r.status_code == 201
    assert uid(client, s.a_jefe) == s.uid_a_jefe


def test_asignar_desde_la_bandeja_y_levantamiento_a_tecnico_aliado(client, auth, db_session):
    from app.models import SupportTicket, Survey

    s = escenario(client, dict(client.headers), db_session)
    # "Tomar"/responsable de la bandeja: un tecnico aliado deja el ticket asignado a su empresa
    r = client.post(f"/tickets/{s.T_A_libre}/assign", json={"assigned_to": s.uid_a_tec2})
    assert r.status_code == 200, r.text
    assert db_session.get(SupportTicket, s.T_A_libre).ally_company_id == s.A
    assert client.post(f"/tickets/{s.T_A_libre}/assign", json={"assigned_to": s.uid_b_tec}).status_code == 422
    # levantamiento de Crimson en O1 -> tecnico de A (participa en O1); el de B no
    tecs = client.get(f"/field/technicians?opportunity_id={s.O1}").json()
    assert s.uid_a_tec in ids(tecs) and s.uid_b_tec not in ids(tecs)
    assert client.post(f"/surveys/{s.S_cr}/assign", json={"technician_id": s.uid_b_tec}).status_code == 422
    r = client.post(f"/surveys/{s.S_cr}/assign", json={"technician_id": s.uid_a_tec})
    assert r.status_code == 200 and r.json()["ally_company"] == "Aliado Uno S.A.", r.text
    db_session.expire_all()
    assert db_session.get(Survey, s.S_cr).ally_company_id == s.A
    assert s.S_cr in ids(client.get("/aliado/levantamientos", headers=s.a_tec).json())
