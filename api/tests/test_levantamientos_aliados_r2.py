"""Reunion 21/10 (lista 2), frente levantamientos: items 16, 18 y 19 del portal de aliados.

16: el aliado propone oportunidades (pendientes de aprobacion de Crimson) y levanta en las en comun con su tecnico.
18: un levantamiento de Crimson en una oportunidad en comun hecho por gente del aliado le aparece en su portal.
19: el aliado sugiere materiales y saca el informe preliminar sin precios, costos ni proveedores.
Repo publico: solo nombres ficticios y correos de prueba."""

from sqlalchemy import select

from tests.aliados_helpers import escenario, ids, interno, keys_rec, participa


def _opp_abierta(client, s, titulo="Oportunidad abierta en común"):
    o = client.post("/opportunities", json={"title": titulo, "customer_id": s.c1, "status": "contactado"}).json()
    participa(client, s.admin_h, o["id"], s.A)
    return o["id"]


# ---------- 18 ----------
def test_levantamiento_de_crimson_hecho_por_el_tecnico_del_aliado_le_aparece(client, auth, db_session):
    """Reproduce LEV-2026-0011: hecho desde el panel de Crimson (sin empresa asignada) con el tecnico del aliado en la
    visita, en una oportunidad visible para su empresa. Antes: "Todavía no hay levantamientos de su empresa"."""
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    o = _opp_abierta(client, s)
    r = client.post(
        "/surveys", json={"kind": "cctv", "customer_id": s.c1, "opportunity_id": o, "site": "Condominio de prueba", "visit_tech_ids": [s.uid_a_tec]}
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert db_session.get(Survey, sid).ally_company_id is None  # sigue siendo de Crimson

    # el aliado lo ve en su lista, en la oportunidad y en el detalle (solo lectura: el tecnico a cargo es de Crimson)
    assert sid in ids(client.get("/aliado/levantamientos", headers=s.a_jefe).json())
    det_o = client.get(f"/aliado/oportunidades/{o}", headers=s.a_jefe).json()
    assert sid in {x["id"] for x in det_o["surveys"]}
    det = client.get(f"/aliado/levantamientos/{sid}", headers=s.a_jefe)
    assert det.status_code == 200 and det.json()["can_edit"] is False
    assert client.put(f"/aliado/levantamientos/{sid}", json={"kind": "cctv"}, headers=s.a_jefe).status_code == 409
    assert client.post(f"/aliado/levantamientos/{sid}/enviar", headers=s.a_jefe).status_code == 409
    # otra empresa no lo ve
    assert sid not in ids(client.get("/aliado/levantamientos", headers=s.b_jefe).json())
    assert client.get(f"/aliado/levantamientos/{sid}", headers=s.b_jefe).status_code == 404

    # el tecnico a cargo es del aliado: es suyo y lo puede editar
    sv = Survey(tenant_id=s.tid, number="LEV-T18", kind="cctv", opportunity_id=o, customer_id=s.c1, technician_id=s.uid_a_tec, status="borrador")
    db_session.add(sv)
    db_session.commit()
    det = client.get(f"/aliado/levantamientos/{sv.id}", headers=s.a_jefe).json()
    assert det["can_edit"] is True
    assert client.put(f"/aliado/levantamientos/{sv.id}", json={"kind": "cctv", "site": "Portón 2"}, headers=s.a_tec).status_code == 200

    # fuera de lo compartido no: oportunidad sin el aliado y participacion oculta
    fuera = [
        Survey(tenant_id=s.tid, number="LEV-T18-O4", kind="cctv", opportunity_id=s.O4, technician_id=s.uid_a_tec, status="borrador"),
        Survey(tenant_id=s.tid, number="LEV-T18-O5", kind="cctv", opportunity_id=s.O5, technician_id=s.uid_a_tec, status="borrador"),
        Survey(tenant_id=s.tid, number="LEV-T18-SIN", kind="cctv", technician_id=s.uid_a_tec, status="borrador"),
    ]
    db_session.add_all(fuera)
    db_session.commit()
    vistos = ids(client.get("/aliado/levantamientos", headers=s.a_jefe).json())
    assert not {x.id for x in fuera} & vistos
    # lo de siempre sigue igual: el de Crimson sin gente del aliado no sale
    assert s.S_cr not in vistos and s.S_A in vistos


# ---------- 16 ----------
def _proponer(client, s, titulo="Condominio nuevo del aliado", **extra):
    body = {"title": titulo, "customer_name": "Cliente Final Demo", "site": "San José", "solution": "cctv", "contact": {"phone": "8888-0000"}, **extra}
    return client.post("/aliado/oportunidades", json=body, headers=s.a_jefe)


def test_aliado_propone_oportunidad_y_crimson_la_aprueba(client, auth, db_session):
    from app.models import AllyParticipation, EmailOutbox, Opportunity

    s = escenario(client, dict(client.headers), db_session)
    assert _proponer(client, s, customer_id=s.c1).status_code == 422  # nunca un cliente de Crimson desde el portal
    assert client.post("/aliado/oportunidades", json={"title": "Del técnico", "customer_name": "X Demo"}, headers=s.a_tec).status_code == 403
    r = _proponer(client, s)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["approval"] == "pendiente" and o["approval_label"] == "Pendiente de aprobación de Crimson" and o["can_survey"] is False
    assert "amount" not in keys_rec(o)
    op = db_session.get(Opportunity, o["id"])
    assert op.owner_id is None and op.ally_proposed_by == s.A and op.customer_id is None and op.source == "aliado"
    part = db_session.scalar(select(AllyParticipation).where(AllyParticipation.opportunity_id == op.id))
    assert part.ally_company_id == s.A and part.visible_to_ally is True
    aviso = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "opportunity", EmailOutbox.entity_id == op.id)).all()
    assert aviso and "pendiente de su aprobación" in aviso[0].subject

    # la ve en su lista; la otra empresa no
    assert o["id"] in ids(client.get("/aliado/oportunidades", headers=s.a_jefe).json())
    assert o["id"] not in ids(client.get("/aliado/oportunidades", headers=s.b_jefe).json())
    # pendiente: no se levanta
    r = client.post("/aliado/levantamientos", json={"opportunity_id": o["id"], "kind": "cctv"}, headers=s.a_jefe)
    assert r.status_code == 409 and "aprobación" in r.json()["detail"]

    # Crimson: la ve como propuesta; un vendedor no la decide
    pr = client.get(f"/opportunities/{o['id']}/ally-proposal").json()["proposal"]
    assert pr["status"] == "pendiente" and pr["company"] == "Aliado Uno S.A."
    ven = interno(client, db_session, s.tid, "ventas.r2@ejemplo.com", "ventas")
    assert client.post(f"/opportunities/{o['id']}/ally-proposal/approve", json={}, headers=ven).status_code in (403, 404)
    r = client.post(f"/opportunities/{o['id']}/ally-proposal/approve", json={"owner_id": s.admin_id})
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["status"] == "aprobada"
    assert client.post(f"/opportunities/{o['id']}/ally-proposal/approve", json={}).status_code == 409
    db_session.expire_all()
    assert db_session.get(Opportunity, o["id"]).owner_id == s.admin_id
    al_aliado = db_session.scalars(
        select(EmailOutbox).where(EmailOutbox.entity == "opportunity", EmailOutbox.entity_id == op.id, EmailOutbox.to == "jefe@aliado-uno.ejemplo.com")
    ).all()
    assert al_aliado and "aprobó" in al_aliado[0].subject

    # aprobada: el aliado levanta y se lo asigna a su tecnico (o a si mismo)
    det = client.get(f"/aliado/oportunidades/{o['id']}", headers=s.a_jefe).json()
    assert det["approval"] == "aprobada" and det["can_survey"] is True
    r = client.post("/aliado/levantamientos", json={"opportunity_id": o["id"], "kind": "cctv", "technician_id": s.uid_a_tec}, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    assert r.json()["technician_id"] == s.uid_a_tec
    assert r.json()["id"] in ids(client.get("/aliado/levantamientos", headers=s.a_tec).json())


def test_crimson_rechaza_la_propuesta_y_ajuste_sin_aprobacion(client, auth, db_session):
    from app.models import Opportunity, Tenant

    s = escenario(client, dict(client.headers), db_session)
    o = _proponer(client, s, titulo="Propuesta a rechazar").json()
    assert client.post(f"/opportunities/{o['id']}/ally-proposal/reject", json={"reason": "x"}).status_code == 422
    r = client.post(f"/opportunities/{o['id']}/ally-proposal/reject", json={"reason": "Ya la lleva otro canal"})
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "rechazada"
    db_session.expire_all()
    op = db_session.get(Opportunity, o["id"])
    assert op.status == "perdida" and "Ya la lleva otro canal" in (op.lost_reason or "")
    cerradas = client.get("/aliado/oportunidades?estado=cerradas", headers=s.a_jefe).json()
    fila = next(x for x in cerradas if x["id"] == o["id"])
    assert fila["approval_label"] == "No aprobada por Crimson" and "Ya la lleva otro canal" in fila["approval_reason"]
    # una de Crimson no es propuesta
    assert client.get(f"/opportunities/{s.O1}/ally-proposal").json() == {"proposal": None}
    assert client.post(f"/opportunities/{s.O1}/ally-proposal/approve", json={}).status_code == 409

    # Ajustes puede apagar la aprobacion (pregunta abierta 8): nace aprobada y se puede levantar
    t = db_session.get(Tenant, s.tid)
    t.settings = {**(t.settings or {}), "ally_opportunity_approval": False}
    db_session.commit()
    o2 = _proponer(client, s, titulo="Sin aprobación previa").json()
    assert o2["approval"] == "aprobada" and o2["can_survey"] is True


# ---------- 19 ----------
def test_aliado_sugiere_materiales_e_informe_sin_precios(client, auth, db_session):
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    o = _opp_abierta(client, s)
    body = {
        "opportunity_id": o,
        "kind": "cctv",
        "points": [{"code": "CAM-01", "label": "Entrada", "data": {}}, {"code": "CAM-02", "label": "Patio", "data": {}}],
        "items": [{"name": "Cámara domo", "quantity": 2, "product_id": s.prod}],
    }
    r = client.post("/aliado/levantamientos", json=body, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    item_id = r.json()["items"][0]["id"]
    # Crimson costea esa linea: nada de eso puede salir al aliado
    assert client.post(f"/surveys/{sid}/costs", json=[{"item_id": item_id, "unit_cost": 98765}]).status_code == 200

    r = client.post(f"/aliado/levantamientos/{sid}/sugerir", headers=s.a_tec)
    assert r.status_code == 404  # el tecnico del aliado no ve este levantamiento si no es suyo
    r = client.post(f"/aliado/levantamientos/{sid}/sugerir", headers=s.a_jefe)
    assert r.status_code == 200, r.text
    sug = r.json()
    assert sug, "la sugerencia no puede venir vacía"
    malas = {"candidates", "recommend", "cost", "cost_crc", "sale_price", "price", "supplier", "unit_cost"}
    assert not malas & keys_rec(sug)
    elegido = next(x for x in sug if x["action"] == "nuevo")
    linea = {"name": elegido["name"], "quantity": 1, "unit": elegido["unit"], "kind": elegido["kind"]}
    r = client.post(f"/aliado/levantamientos/{sid}/sugerir/agregar", json=[linea, linea], headers=s.a_jefe)  # doble envio en la lista
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    assert "98765" not in r.text and "unit_cost" not in r.text
    db_session.expire_all()
    assert sum(1 for it in db_session.get(Survey, sid).items if it.name == elegido["name"]) == 1

    r = client.get(f"/aliado/levantamientos/{sid}/informe.pdf", headers=s.a_jefe)
    assert r.status_code == 200, r.text
    if r.headers.get("X-PDF-Fallback") == "html":
        assert "98765" not in r.text and "₡" not in r.text and "Informe preliminar" in r.text
    assert client.get(f"/aliado/levantamientos/{sid}/informe.pdf", headers=s.b_jefe).status_code == 404

    # uno de Crimson que solo ve (la gente del aliado visito): sin sugerir ni agregar
    r = client.post("/surveys", json={"kind": "cctv", "customer_id": s.c1, "opportunity_id": o, "visit_tech_ids": [s.uid_a_tec]})
    otro = r.json()["id"]
    assert client.post(f"/aliado/levantamientos/{otro}/sugerir", headers=s.a_jefe).status_code == 409
    assert client.post(f"/aliado/levantamientos/{otro}/sugerir/agregar", json=[linea], headers=s.a_jefe).status_code == 409
    assert client.get(f"/aliado/levantamientos/{otro}/informe.pdf", headers=s.a_jefe).status_code == 200


def test_sugerencias_internas_no_duplican_con_doble_envio(client, auth, db_session):
    """25: un doble clic en «Agregar al levantamiento» que manda dos veces la misma linea no la suma dos veces."""
    r = client.post("/surveys", json={"kind": "cctv", "points": [{"code": "CAM-01", "data": {}}]})
    sid = r.json()["id"]
    linea = {"name": "Conector RJ45", "quantity": 2, "unit": "Unid", "kind": "material"}
    r = client.post(f"/surveys/{sid}/suggest/apply", json=[linea, linea])
    assert r.status_code == 200 and r.json()["added"] == 1
    its = [x for x in r.json()["survey"]["items"] if x["name"] == "Conector RJ45"]
    assert len(its) == 1 and float(its[0]["quantity"]) == 2


# ---------- hallazgos de revision ----------
def test_18_tecnico_con_rol_interno_de_la_empresa_aliada_y_lo_de_crimson_no_es_pendiente(client, auth, db_session):
    """LEV-2026-0011: el tecnico tenia rol interno ("Técnico instalador", item 20). Con su membresia en la empresa
    aliada el levantamiento le sale; sin empresa (dato sin corregir) no hay nada que lo ligue. Lo de Crimson que su
    gente solo visito no cuenta como "por hacer" ni sale en Mi dia, y del tecnico de Crimson solo va el nombre de pila."""
    from app.models import Survey, TenantUser, User

    s = escenario(client, dict(client.headers), db_session)
    o = _opp_abierta(client, s)
    interno(client, db_session, s.tid, "instalador.r2@ejemplo.com", "tecnico")
    inst = db_session.scalar(select(User).where(User.email == "instalador.r2@ejemplo.com"))
    sv = Survey(tenant_id=s.tid, number="LEV-T18-INST", kind="cctv", opportunity_id=o, customer_id=s.c1, technician_id=inst.id, status="borrador")
    db_session.add(sv)
    db_session.commit()
    assert sv.id not in ids(client.get("/aliado/levantamientos", headers=s.a_jefe).json())  # sin empresa: no hay como ligarlo
    m = db_session.scalar(select(TenantUser).where(TenantUser.tenant_id == s.tid, TenantUser.user_id == inst.id))
    m.ally_company_id = s.A  # corregir el dato (item 20): su membresia pasa a la empresa aliada
    db_session.commit()
    assert sv.id in ids(client.get("/aliado/levantamientos", headers=s.a_jefe).json())

    antes = client.get("/aliado/inicio", headers=s.a_jefe).json()["counters"]
    r = client.post("/surveys", json={"kind": "cctv", "customer_id": s.c1, "opportunity_id": o, "visit_tech_ids": [s.uid_a_tec, s.admin_id]})
    sid = r.json()["id"]
    assert db_session.get(Survey, sid).status == "borrador"
    despues = client.get("/aliado/inicio", headers=s.a_jefe).json()["counters"]
    assert despues["levantamientos_por_hacer"] == antes["levantamientos_por_hacer"]
    assert sid not in ids(client.get("/aliado/mi-dia", headers=s.a_jefe).json()["levantamientos"])

    admin = db_session.get(User, s.admin_id)
    det = client.get(f"/aliado/levantamientos/{sid}", headers=s.a_jefe)
    assert det.status_code == 200
    assert det.json()["technician_id"] is None and det.json()["technician"] == admin.full_name.split()[0]
    if " " in admin.full_name:
        assert admin.full_name not in det.text
    rep = client.get(f"/aliado/levantamientos/{sid}/informe.pdf", headers=s.a_jefe)
    assert rep.status_code == 200
    if rep.headers.get("X-PDF-Fallback") == "html":
        assert "Tomas Tecnico Uno" in rep.text  # su equipo, con nombre completo
        if " " in admin.full_name:
            assert admin.full_name not in rep.text


def test_16_motivo_del_rechazo_aprobar_despues_y_ajuste_desde_aliados(client, auth, db_session):
    from app.models import Opportunity, Tenant

    s = escenario(client, dict(client.headers), db_session)
    o = _proponer(client, s, titulo="Propuesta que se reconsidera").json()
    fila = next(x for x in client.get("/opportunities").json() if x["id"] == o["id"])
    assert fila["ally_approval"] == "pendiente" and fila["ally_proposed_by"] == s.A
    assert client.post(f"/opportunities/{o['id']}/ally-proposal/reject", json={"reason": "Falta el sitio exacto"}).status_code == 200
    det = client.get(f"/aliado/oportunidades/{o['id']}", headers=s.a_jefe).json()
    assert "Falta el sitio exacto" in (det["approval_reason"] or "") and det["can_survey"] is False
    assert client.post(f"/opportunities/{o['id']}/ally-proposal/reject", json={"reason": "Otra vez"}).status_code == 409
    # Crimson cambia de opinion: se aprueba y se reabre
    r = client.post(f"/opportunities/{o['id']}/ally-proposal/approve", json={})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    op = db_session.get(Opportunity, o["id"])
    assert op.ally_approval == "aprobada" and op.status == "nuevo" and op.lost_reason is None and op.closed_at is None
    det = client.get(f"/aliado/oportunidades/{o['id']}", headers=s.a_jefe).json()
    assert det["can_survey"] is True and det["approval_reason"] is None

    # Ajustes de aliados: el interruptor de la aprobacion
    assert client.get("/allies/config").json()["opportunity_approval"] is True
    r = client.put("/allies/config", json={"opportunity_approval": False})
    assert r.status_code == 200 and r.json()["opportunity_approval"] is False
    db_session.expire_all()
    assert db_session.get(Tenant, s.tid).settings["ally_opportunity_approval"] is False
    assert _proponer(client, s, titulo="Ya sin aprobación").json()["approval"] == "aprobada"


def test_16_tecnico_elegido_al_crear_recibe_el_aviso(client, auth, db_session):
    from app.models import EmailOutbox

    s = escenario(client, dict(client.headers), db_session)
    o = _opp_abierta(client, s)
    r = client.post("/aliado/levantamientos", json={"opportunity_id": o, "kind": "cctv", "technician_id": s.uid_a_tec}, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    avisos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "ally_work", EmailOutbox.entity_id == r.json()["id"])).all()
    assert [a.to for a in avisos] == ["tec@aliado-uno.ejemplo.com"]
    # para si mismo: sin aviso
    r = client.post("/aliado/levantamientos", json={"opportunity_id": o, "kind": "cctv"}, headers=s.a_jefe)
    assert not db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "ally_work", EmailOutbox.entity_id == r.json()["id"])).all()


def test_19_agregar_sugerencias_a_uno_aprobado_lo_devuelve_a_revision(client, auth, db_session):
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    o = _opp_abierta(client, s)
    r = client.post("/aliado/levantamientos", json={"opportunity_id": o, "kind": "cctv", "points": [{"code": "CAM-01", "data": {}}]}, headers=s.a_jefe)
    sid = r.json()["id"]
    sv = db_session.get(Survey, sid)
    sv.status, sv.review_status = "enviado", "aprobado"
    db_session.commit()
    linea = {"name": "Conector RJ45 de prueba", "quantity": 2, "unit": "Unid", "kind": "material"}
    r = client.post(f"/aliado/levantamientos/{sid}/sugerir/agregar", json=[linea], headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert r.json()["survey"]["review_status"] == "pendiente"
    db_session.expire_all()
    assert db_session.get(Survey, sid).review_status == "pendiente"
