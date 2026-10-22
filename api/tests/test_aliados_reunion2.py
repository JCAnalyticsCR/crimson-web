"""Reunion 2 (21/10/2026), pedidos de aliados: 14 (detalle de la oportunidad), 15 (bitacora compartida), 17
(ejecucion del proyecto), 20 (persona de un aliado como usuario interno) y 21 (inicio del tecnico aliado).

Repo publico: empresas, personas y correos ficticios."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tests.aliados_helpers import ADMIN_PW, PW, escenario, interno, keys_rec, login, participa

PROHIBIDAS = {"cost", "margin_pct", "economics", "unit_cost", "probability", "weighted", "quote_id", "price", "amount", "notes", "lost_reason"}


def _opp_abierta(client, admin_h, s, titulo="Cámaras parqueo este", ally=None, **extra):
    r = client.post("/opportunities", json={"title": titulo, "customer_id": s.c1, "amount": 900000, "next_action": "Visita técnica", "next_action_date": "2026-11-05", **extra}, headers=admin_h)
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    participa(client, admin_h, oid, ally or s.A, scope="Canalización\nInstalación de 8 cámaras", scope_lines=["Cámara domo 4 MP"])
    return oid


# ---------- 14 y 15: detalle de la oportunidad y bitacora compartida ----------
def test_detalle_oportunidad_y_bitacora_compartida(client, auth, db_session):
    admin_h = dict(client.headers)
    s = escenario(client, admin_h, db_session)
    oid = _opp_abierta(client, admin_h, s)
    assert client.post(f"/opportunities/{oid}/touch", json={"note": "Llamé al cliente: quiere arrancar en noviembre"}).status_code == 200
    assert client.post(f"/opportunities/{oid}/touch", json={"note": "Margen interno 30 %, no compartir"}).status_code == 200

    det = client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()
    assert det["next_action"] == "Visita técnica" and det["next_action_date"] == "2026-11-05"
    assert det["seguimientos"] == []  # por defecto nada se comparte
    assert det["participations"][0]["scope"].startswith("Canalización")
    assert not PROHIBIDAS & keys_rec(det)
    assert "Margen interno" not in str(det)

    st = client.get(f"/opportunities/{oid}/bitacora-aliado").json()
    assert st["can_share"] and st["companies"] == ["Aliado Uno S.A."] and st["shared"] == []
    lineas = [x for x in client.get(f"/opportunities/{oid}").json()["notes"].split("\n") if x.strip()]
    linea = next(x for x in lineas if "arrancar en noviembre" in x)
    r = client.put(f"/opportunities/{oid}/bitacora-aliado", json={"line": linea, "visible": True})
    assert r.status_code == 200 and r.json()["shared"] == [linea.strip()]

    seg = client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["seguimientos"]
    assert len(seg) == 1 and seg[0]["text"] == "Llamé al cliente: quiere arrancar en noviembre"
    assert seg[0]["author"].endswith("· Crimson") and seg[0]["when"]
    # la otra empresa aliada no participa en esta oportunidad: ni la ve
    assert client.get(f"/aliado/oportunidades/{oid}", headers=s.b_jefe).status_code == 404
    # una linea que no esta en la bitacora: 404; apagarla la quita del portal
    assert client.put(f"/opportunities/{oid}/bitacora-aliado", json={"line": "01/01/2026 10:00 · Nadie: inventada", "visible": True}).status_code == 404
    assert client.put(f"/opportunities/{oid}/bitacora-aliado", json={"line": linea, "visible": False}).json()["shared"] == []
    assert client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["seguimientos"] == []
    # sin aliado que la vea no se comparte nada
    r = client.post("/opportunities", json={"title": "Sin aliado", "customer_id": s.c1})
    o2 = r.json()["id"]
    client.post(f"/opportunities/{o2}/touch", json={"note": "Solo Crimson"})
    l2 = client.get(f"/opportunities/{o2}").json()["notes"].split("\n")[0]
    assert client.put(f"/opportunities/{o2}/bitacora-aliado", json={"line": l2, "visible": True}).status_code == 409
    # un vendedor no toca la bitacora de una oportunidad ajena
    vend = interno(client, db_session, s.tid, "vendedor.bitacora@ejemplo.com", "ventas")
    assert client.put(f"/opportunities/{oid}/bitacora-aliado", json={"line": linea, "visible": True}, headers=vend).status_code == 404


def test_monto_de_la_cotizacion_solo_si_se_habilita(client, auth, db_session):
    admin_h = dict(client.headers)
    s = escenario(client, admin_h, db_session)
    oid = _opp_abierta(client, admin_h, s)
    r = client.post(f"/opportunities/{oid}/quote", json={})
    assert r.status_code == 201, r.text
    numero = r.json()["number"]
    assert client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["cotizacion"] is None  # por defecto no
    assert client.get(f"/allies/{s.A}").json()["show_quote_total"] is False
    r = client.put(f"/allies/{s.A}", json={"show_quote_total": True})
    assert r.status_code == 200 and r.json()["show_quote_total"] is True
    cot = client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["cotizacion"]
    assert cot["number"] == numero and "total" in cot and "currency" in cot
    # solo para esa empresa: la empresa B (en otra oportunidad con cotizacion) sigue sin verlo
    ob = _opp_abierta(client, admin_h, s, "Red bodega oeste", ally=s.B)
    assert client.post(f"/opportunities/{ob}/quote", json={}).status_code == 201
    assert client.get(f"/aliado/oportunidades/{ob}", headers=s.b_jefe).json()["cotizacion"] is None


# ---------- 17: ejecucion del proyecto en comun ----------
def test_ejecucion_del_proyecto_en_comun(client, auth, db_session):
    from app.models import WorkOrder

    admin_h = dict(client.headers)
    s = escenario(client, admin_h, db_session)
    crimson = WorkOrder(
        tenant_id=s.tid, number="OT-CR-P1", title="Configuración del grabador", kind="instalacion", project_id=s.P1, customer_id=s.c1,
        technician_id=s.admin_id, scheduled_at=datetime.now(UTC) + timedelta(days=2),
    )  # fmt: skip
    otra = WorkOrder(tenant_id=s.tid, number="OT-B-P3", title="Trabajo de la otra empresa", kind="instalacion", project_id=s.P3, ally_company_id=s.B)
    db_session.add_all([crimson, otra])
    db_session.commit()

    p1 = client.get(f"/aliado/proyectos/{s.P1}", headers=s.a_jefe).json()
    ej = {o["number"]: o for o in p1["ejecucion"]["orders"]}
    assert {"OT-HOY", "OT-MANANA", "OT-OTRO", "OT-CR-P1"} <= set(ej)
    assert ej["OT-CR-P1"]["team"] == "crimson" and ej["OT-CR-P1"]["id"] is None  # la de Crimson no se abre
    assert ej["OT-HOY"]["team"] == "aliado" and ej["OT-HOY"]["id"] and ej["OT-HOY"]["technicians"] == ["Tomas Tecnico Uno"]
    admin_name = client.get("/auth/me").json()["user"]["full_name"]
    assert ej["OT-CR-P1"]["technicians"] == [admin_name.split()[0]]  # de Crimson, solo el nombre de pila
    assert not PROHIBIDAS & keys_rec(p1["ejecucion"])
    # proyecto compartido con otra empresa aliada: las OT de esa empresa no salen
    p3 = client.get(f"/aliado/proyectos/{s.P3}", headers=s.a_jefe).json()
    assert not {"OT-B", "OT-B-P3"} & {o["number"] for o in p3["ejecucion"]["orders"]}
    # tambien desde la oportunidad
    o1 = client.get(f"/aliado/oportunidades/{s.O1}", headers=s.a_jefe).json()
    assert o1["ejecucion"]["project"]["id"] == s.P1 and "OT-CR-P1" in {o["number"] for o in o1["ejecucion"]["orders"]}


# ---------- 21: inicio del tecnico aliado ----------
def test_inicio_del_tecnico_aliado(client, auth, db_session):
    from app.models import Survey

    admin_h = dict(client.headers)
    s = escenario(client, admin_h, db_session)
    oid = _opp_abierta(client, admin_h, s)
    db_session.add(Survey(tenant_id=s.tid, number="LEV-T1", kind="cctv", opportunity_id=oid, customer_id=s.c1, technician_id=s.uid_a_tec, ally_company_id=s.A, status="borrador"))
    db_session.commit()
    d = client.get("/aliado/inicio", headers=s.a_tec).json()
    t = d["tecnico"]
    assert "OT-MANANA" in {o["number"] for o in t["proximas"]} and all("id" not in o for o in t["proximas"])
    assert "OT-OTRO" not in {o["number"] for o in t["proximas"]}  # de otro tecnico
    opps = {o["title"]: o for o in t["oportunidades"]}
    assert opps["Cámaras parqueo este"]["mine"] is True
    assert "Ana Jefa Uno" in t["asignan"]
    assert "LEV-T1" in {x["number"] for x in d["hoy"]["levantamientos"]}
    assert not PROHIBIDAS & keys_rec(t)  # (las OT de hoy ya traen sus notas de campo: eso es de antes)
    # el detalle sigue cerrado al tecnico
    assert client.get("/aliado/oportunidades", headers=s.a_tec).status_code == 403
    assert client.get(f"/aliado/oportunidades/{oid}", headers=s.a_tec).status_code == 403
    # la otra empresa no ve las de esta
    tb = client.get("/aliado/inicio", headers=s.b_tec).json()["tecnico"]
    assert "Cámaras parqueo este" not in {o["title"] for o in tb["oportunidades"]}


# ---------- 20: persona de un aliado como usuario interno ----------
def test_aliado_no_entra_como_usuario_interno(client, auth, db_session):
    from app.models import TenantUser

    admin_h = dict(client.headers)
    s = escenario(client, admin_h, db_session)
    # una invitacion interna de antes de que existiera la empresa aliada
    vieja = client.post("/settings/invitations", json={"email": "jefe@instala-prueba.ejemplo.com", "role": "tecnico"})
    assert vieja.status_code == 201, vieja.text
    # empresa aliada de una persona fisica, con correo de dominio propio
    r = client.post("/allies", json={"name": "Rolando Prueba Mora", "email": "rolando@instala-prueba.ejemplo.com"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # ...ya no lo hace usuario interno al aceptarla
    keep = client.headers.pop("Authorization")
    r = client.post("/settings/invitations/accept", json={"token": vieja.json()["link"].rsplit("/", 1)[-1], "full_name": "Jefe Instala", "password": PW})
    client.headers["Authorization"] = keep
    assert r.status_code == 409 and "portal de aliados" in r.json()["detail"], r.text
    # invitar como interno su correo o uno de su dominio: no
    for correo in ("rolando@instala-prueba.ejemplo.com", "otro@instala-prueba.ejemplo.com"):
        r = client.post("/settings/invitations", json={"email": correo, "role": "tecnico"})
        assert r.status_code == 409 and "portal de aliados" in r.json()["detail"], r.text
    # quedo como interno de antes (dato real del pedido): la lista lo marca
    h = interno(client, db_session, s.tid, "rolando@instala-prueba.ejemplo.com", "tecnico")
    uid = client.get("/auth/me", headers=h).json()["user"]["id"]
    nombre = interno(client, db_session, s.tid, "rolando.mora@gmail.com", "tecnico")
    uid2 = client.get("/auth/me", headers=nombre).json()["user"]["id"]
    db_session.get(TenantUser, db_session.scalar(select(TenantUser.id).where(TenantUser.user_id == uid2))).user.full_name = "Rolando Prueba Mora"
    db_session.commit()
    rows = {u["id"]: u for u in client.get("/settings/users").json()["users"]}
    assert rows[uid]["ally_match"] == {"company_id": cid, "company": "Rolando Prueba Mora", "reason": "correo"}
    assert rows[uid2]["ally_match"]["reason"] == "nombre"
    assert rows[s.admin_id]["ally_match"] is None
    # cambiarle el rol interno o reactivarlo: no
    assert client.patch(f"/settings/users/{uid}", json={"role": "supervisor"}).status_code == 409
    assert client.patch(f"/settings/users/{uid}", json={"active": False}).status_code == 200  # desactivar si
    assert client.patch(f"/settings/users/{uid}", json={"active": True}).status_code == 409
    assert client.patch(f"/settings/users/{uid}", json={"active": False}).status_code == 200
    db_session.get(TenantUser, db_session.scalar(select(TenantUser.id).where(TenantUser.user_id == uid))).active = True
    db_session.commit()

    # pasarlo al portal de aliados: contrasena, no a un admin ni a si mismo, ni con oportunidades a su cargo
    base = {"user_id": uid, "role": "tecnico_aliado"}
    assert client.post(f"/allies/{cid}/users/from-internal", json=base).status_code == 403
    assert client.post(f"/allies/{cid}/users/from-internal", json={**base, "user_id": s.admin_id, "admin_password": ADMIN_PW}).status_code == 409
    vend = interno(client, db_session, s.tid, "vendedor.aliado@ejemplo.com", "ventas")
    vid = client.get("/auth/me", headers=vend).json()["user"]["id"]
    client.post("/opportunities", json={"title": "Suya", "customer_id": s.c1}, headers=vend)
    r = client.post(f"/allies/{cid}/users/from-internal", json={"user_id": vid, "role": "aliado", "admin_password": ADMIN_PW})
    assert r.status_code == 409 and "oportunidad" in r.json()["detail"]
    # un aliado no puede pasar gente (no tiene settings.configurar ni entra a /allies)
    assert client.post(f"/allies/{cid}/users/from-internal", json={**base, "admin_password": PW}, headers=s.a_jefe).status_code == 403

    r = client.post(f"/allies/{cid}/users/from-internal", json={**base, "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    assert {u["id"] for u in r.json()["users"]} == {uid} and r.json()["pendientes"] == {"ordenes": 0, "levantamientos": 0, "tickets": 0}
    m = db_session.scalar(select(TenantUser).where(TenantUser.user_id == uid))
    db_session.refresh(m)
    assert m.ally_company_id == cid and m.role_code == "tecnico_aliado"
    # ya no esta en Ajustes -> Usuarios; entra al portal de aliados y el panel interno le responde 403
    assert uid not in {u["id"] for u in client.get("/settings/users").json()["users"]}
    h = login(client, "rolando@instala-prueba.ejemplo.com")
    assert client.get("/aliado/inicio", headers=h).status_code == 200
    assert client.get("/opportunities", headers=h).status_code == 403
    # y no se vuelve a pasar
    assert client.post(f"/allies/{cid}/users/from-internal", json={**base, "admin_password": ADMIN_PW}).status_code == 404
