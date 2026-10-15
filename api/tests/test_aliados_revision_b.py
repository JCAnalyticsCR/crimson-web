"""Portal de aliados: segunda revision independiente (levantamiento ligado a otra oportunidad, reasignar desde el
formulario del portal, avisos de trabajo sin tope, tecnicos desactivados que ya estaban, "Convertir en aliado" sin
coincidencia por nombre). Repo publico: solo nombres ficticios y correos de prueba."""

from sqlalchemy import func, select

from tests.aliados_helpers import escenario, participa


def _correos(db_session, to: str, numero: str | None = None) -> int:
    from app.models import EmailOutbox

    db_session.expire_all()
    q = select(func.count()).select_from(EmailOutbox).where(EmailOutbox.entity == "ally_work", EmailOutbox.to == to)
    if numero:
        q = q.where(EmailOutbox.subject.contains(numero))
    return db_session.scalar(q)


# ---------- ligar un levantamiento de un aliado a otra oportunidad ----------
def test_ligar_levantamiento_del_aliado_a_otra_oportunidad(client, auth, db_session):
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    # O4: del mismo cliente, abierta y sin la empresa A
    r = client.post(f"/surveys/{s.S_A}/opportunity", json={"opportunity_id": s.O4})
    assert r.status_code == 422 and "no participa" in r.json()["detail"], r.text
    db_session.expire_all()
    assert db_session.get(Survey, s.S_A).opportunity_id == s.O1
    lev = client.get(f"/aliado/levantamientos/{s.S_A}", headers=s.a_jefe).json()
    assert lev["opportunity_id"] == s.O1 and (lev["opportunity"] or {}).get("id") != s.O4
    # desligarlo tampoco: primero se le pasa a Crimson
    r = client.post(f"/surveys/{s.S_A}/opportunity", json={"opportunity_id": None})
    assert r.status_code == 422, r.text
    db_session.expire_all()
    assert db_session.get(Survey, s.S_A).opportunity_id == s.O1
    # a una oportunidad abierta donde A SI participa: se liga y el aliado la sigue viendo
    o7 = client.post("/opportunities", json={"title": "Ampliacion bodega norte", "customer_id": s.c1}).json()["id"]
    participa(client, dict(client.headers), o7, s.A)
    r = client.post(f"/surveys/{s.S_A}/opportunity", json={"opportunity_id": o7})
    assert r.status_code == 200, r.text
    assert client.get(f"/aliado/levantamientos/{s.S_A}", headers=s.a_jefe).json()["opportunity_id"] == o7
    # el de Crimson se liga y desliga como siempre
    assert client.post(f"/surveys/{s.S_cr}/opportunity", json={"opportunity_id": s.O4}).status_code == 200
    assert client.post(f"/surveys/{s.S_cr}/opportunity", json={"opportunity_id": None}).status_code == 200
    # devuelto a Crimson (tecnico interno), ya se puede ligar a O4 y el aliado deja de verlo
    assert client.post(f"/surveys/{s.S_A}/assign", json={"technician_id": s.admin_id}).status_code == 200
    assert client.post(f"/surveys/{s.S_A}/opportunity", json={"opportunity_id": s.O4}).status_code == 200
    assert client.get(f"/aliado/levantamientos/{s.S_A}", headers=s.a_jefe).status_code == 404


# ---------- reasignar desde el formulario del portal (PUT) ----------
def test_reasignar_desde_el_formulario_audita_y_avisa(client, auth, db_session):
    from app.models import AuditLog

    s = escenario(client, dict(client.headers), db_session)
    tec2 = "tec2@aliado-uno.ejemplo.com"
    r = client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "technician_id": s.uid_a_tec2}, headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["technician_id"] == s.uid_a_tec2, r.text
    assert _correos(db_session, tec2, "LEV-A1") == 1
    asignar = db_session.scalars(select(AuditLog).where(AuditLog.action == "assign", AuditLog.entity == "survey", AuditLog.entity_id == s.S_A)).all()
    assert [a.diff for a in asignar] == [{"de": s.uid_a_tec, "a": s.uid_a_tec2}]
    assert asignar[0].user_id == s.uid_a_jefe
    # la bitacora del guardado tambien muestra el cambio de tecnico (la foto "antes" es de antes de reasignar)
    upd = db_session.scalars(select(AuditLog).where(AuditLog.action == "update", AuditLog.entity == "survey", AuditLog.entity_id == s.S_A)).all()
    assert any((u.after or {}).get("technician_id") == s.uid_a_tec2 and (u.before or {}).get("technician_id") == s.uid_a_tec for u in upd)
    # guardar otra vez sin cambiar el tecnico: ni otra auditoria de asignacion ni otro correo
    assert client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "technician_id": s.uid_a_tec2}, headers=s.a_jefe).status_code == 200
    assert _correos(db_session, tec2, "LEV-A1") == 1
    db_session.expire_all()
    assert len(db_session.scalars(select(AuditLog).where(AuditLog.action == "assign", AuditLog.entity == "survey", AuditLog.entity_id == s.S_A)).all()) == 1


# ---------- avisos de trabajo: sin repetir y con tope ----------
def test_alternar_tecnico_no_inunda_de_correos(client, auth, db_session, monkeypatch):
    from app.core import ratelimit
    from app.models import EmailOutbox

    s = escenario(client, dict(client.headers), db_session)
    tec, tec2 = "tec@aliado-uno.ejemplo.com", "tec2@aliado-uno.ejemplo.com"
    url = f"/aliado/levantamientos/{s.S_A}/asignar"
    for uid in (s.uid_a_tec2, s.uid_a_tec, s.uid_a_tec2, s.uid_a_tec):
        assert client.patch(url, json={"technician_id": uid}, headers=s.a_jefe).status_code == 200
    assert _correos(db_session, tec2, "LEV-A1") == 1 and _correos(db_session, tec, "LEV-A1") == 1
    # el aviso guarda el id del trabajo
    m = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "ally_work", EmailOutbox.to == tec2)).first()
    assert m.entity_id == s.S_A
    # OT: quitar y volver a poner al ayudante tampoco repite el correo
    ot = f"/aliado/ordenes/{s.W_hoy}/asignar"
    for ayudantes in ([s.uid_a_tec2], [], [s.uid_a_tec2], []):
        assert client.patch(ot, json={"technician_id": s.uid_a_tec, "helpers": ayudantes}, headers=s.a_jefe).status_code == 200
    assert _correos(db_session, tec2, "OT-HOY") == 1
    # y cada persona tiene tope de reasignaciones por hora
    monkeypatch.setattr(ratelimit.ally_assign_limiter, "limit", 1)
    ratelimit.ally_assign_limiter.reset()
    assert client.patch(url, json={"technician_id": s.uid_a_tec2}, headers=s.a_jefe).status_code == 200
    assert client.patch(url, json={"technician_id": s.uid_a_tec}, headers=s.a_jefe).status_code == 429
    assert client.patch(ot, json={"technician_id": s.uid_a_tec, "helpers": []}, headers=s.a_jefe).status_code == 429
    r = client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "technician_id": s.uid_a_tec}, headers=s.a_jefe)
    assert r.status_code == 429, r.text
    # guardar sin reasignar no cuenta
    assert client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv"}, headers=s.a_jefe).status_code == 200


# ---------- un tecnico aliado desactivado que YA estaba no traba la edicion ----------
def test_ayudante_desactivado_que_ya_estaba_no_traba(client, auth, db_session):
    from app.models import WorkOrder

    s = escenario(client, dict(client.headers), db_session)
    assert client.put(f"/work-orders/{s.W_hoy}", json={"helpers": [s.uid_a_tec2]}).status_code == 200
    assert client.patch(f"/allies/{s.A}/users/{s.uid_a_tec2}", json={"active": False}).status_code == 200
    # cambiar el tecnico principal por otro de A, conservando al ayudante desactivado: pasa
    r = client.patch(f"/work-orders/{s.W_hoy}/assign", json={"technician_id": s.uid_a_jefe, "helpers": [s.uid_a_tec2]})
    assert r.status_code == 200 and r.json()["ally_company_id"] == s.A, r.text
    r = client.put(f"/work-orders/{s.W_hoy}", json={"technician_id": s.uid_a_tec, "helpers": [s.uid_a_tec2]})
    assert r.status_code == 200 and r.json()["ally_company_id"] == s.A, r.text
    db_session.expire_all()
    o = db_session.get(WorkOrder, s.W_hoy)
    assert o.technician_id == s.uid_a_tec and o.helpers == [s.uid_a_tec2]
    # meterlo de nuevo en otra orden si se frena
    assert client.patch(f"/work-orders/{s.W_manana}/assign", json={"technician_id": s.uid_a_tec, "helpers": [s.uid_a_tec2]}).status_code == 422


def test_ticket_con_tecnico_desactivado_cambia_de_proyecto(client, auth, db_session):
    from app.models import SupportTicket

    s = escenario(client, dict(client.headers), db_session)
    assert client.patch(f"/allies/{s.A}/users/{s.uid_a_tec}", json={"active": False}).status_code == 200
    body = {"subject": "Falla TK-A", "customer_id": s.c1, "priority": "media", "assigned_to": s.uid_a_tec}
    r = client.put(f"/tickets/{s.T_A}", json={**body, "project_id": s.P3})  # A tambien participa en P3
    assert r.status_code == 200, r.text
    db_session.expire_all()
    t = db_session.get(SupportTicket, s.T_A)
    assert t.project_id == s.P3 and t.ally_company_id == s.A and t.assigned_to == s.uid_a_tec
    # a un proyecto donde A no participa sigue siendo 422 (la empresa y la participacion se validan siempre)
    r = client.put(f"/tickets/{s.T_A}", json={**body, "project_id": s.P_cr})
    assert r.status_code == 422 and "no participa" in r.json()["detail"], r.text


# ---------- "Convertir en aliado" sin elegir empresa ----------
def _part(client, oid: int, role: str = "subcontratista", **company) -> int:
    r = client.post(f"/opportunities/{oid}/allies", json={"role": role, **company})
    assert r.status_code == 201, r.text
    key, val = next(iter(company.items()))
    return next(a["id"] for a in r.json() if a.get(key) == val and a["role"] == role)


def test_convertir_no_liga_por_nombre_ni_cambia_la_facturacion(client, auth, db_session):
    from app.models import AllyCompany, AllyParticipation

    s = escenario(client, dict(client.headers), db_session)
    # alguien da de alta una empresa aliada con el MISMO nombre que el cliente c1 (otra cedula: ninguna)
    r = client.post("/opportunity-allies/companies", json={"name": "Cliente Demo Uno"})
    assert r.status_code == 201, r.text
    falsa = r.json()["id"]
    pid = _part(client, s.O4, customer_id=s.c1)
    assert client.post(f"/opportunity-allies/{pid}/requests", json={"what": "Precio de cableado", "amount": 1000, "currency": "CRC"}).status_code == 201
    r = client.post(f"/opportunity-allies/{pid}/to-ally", json={})
    assert r.status_code == 409 and r.json()["existing"]["id"] == falsa, r.text
    db_session.expire_all()
    assert db_session.get(AllyCompany, falsa).customer_id is None
    assert db_session.get(AllyParticipation, pid).customer_id == s.c1 and db_session.get(AllyParticipation, pid).ally_company_id is None
    # elegirla a mano con solicitudes de otra empresa: tampoco
    assert client.post(f"/opportunity-allies/{pid}/to-ally", json={"ally_company_id": falsa}).status_code == 409
    # sin solicitudes y elegida a mano: se convierte, pero la empresa aliada NO toma el cliente de facturacion
    pid2 = _part(client, s.O4, "suministro", customer_id=s.c1)
    r = client.post(f"/opportunity-allies/{pid2}/to-ally", json={"ally_company_id": falsa})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(AllyCompany, falsa).customer_id is None

    # misma cedula (no vacia): es la misma empresa y se liga, pero el cliente de facturacion lo liga Administracion
    r = client.post("/opportunity-allies/companies", json={"name": "Razon Social Dos", "tax_id": "3-101-222222"})
    assert r.status_code == 201, r.text
    misma = r.json()["id"]
    pid3 = _part(client, s.O4, customer_id=s.c2)
    r = client.post(f"/opportunity-allies/{pid3}/to-ally", json={})
    assert r.status_code == 200 and next(a for a in r.json() if a["id"] == pid3)["ally_company_id"] == misma, r.text
    db_session.expire_all()
    assert db_session.get(AllyCompany, misma).customer_id is None

    # sin ninguna empresa aliada que calce: se crea con los datos del cliente y ligada a el (es esa misma empresa)
    c3 = client.post("/customers", json={"name": "Cliente Demo Tres", "id_type": "juridica", "id_number": "3-101-333333"}).json()["id"]
    o8 = client.post("/opportunities", json={"title": "Cableado oficina tres", "customer_id": c3}).json()["id"]
    pid4 = _part(client, o8, customer_id=c3)
    r = client.post(f"/opportunity-allies/{pid4}/to-ally", json={})
    assert r.status_code == 200, r.text
    nueva = next(a for a in r.json() if a["id"] == pid4)["ally_company_id"]
    db_session.expire_all()
    comp = db_session.get(AllyCompany, nueva)
    assert comp.customer_id == c3 and comp.tax_id == "3-101-333333" and comp.name == "Cliente Demo Tres"
