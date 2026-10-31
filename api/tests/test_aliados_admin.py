"""Administracion -> Aliados y "Aliados y participacion" de la oportunidad (aliado_final §12.2, A1-A7)."""

from tests.aliados_helpers import aliado, escenario, ids, interno, usuario


def _servicio_mensualidad(client) -> int:
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


def test_alta_y_duplicados(client, auth, db_session):
    from app.models import Supplier

    r = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000001", "contacts": {"tecnico": {"name": "Contacto Técnico"}}})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["state"] == "activo" and a["contacts"]["tecnico"]["name"] == "Contacto Técnico"
    r = client.post("/allies", json={"name": "Otro nombre", "tax_id": "3-101-000001"})
    assert r.status_code == 409 and r.json()["existing"] == {"id": a["id"], "name": "Aliado Uno S.A."}
    r = client.post("/allies", json={"name": "  aliado   uno s.a. "})
    assert r.status_code == 409 and r.json()["existing"]["id"] == a["id"]
    sup = Supplier(tenant_id=auth["tenant"]["id"], name="Proveedor Aliado Dos", tax_id="3-101-000002", email="ventas@proveedor-dos.ejemplo.com")
    db_session.add(sup)
    db_session.commit()
    r = client.post("/allies", json={"supplier_id": sup.id})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["name"] == "Proveedor Aliado Dos" and b["tax_id"] == "3-101-000002" and b["supplier_id"] == sup.id and b["supplier"]["name"] == sup.name
    # la ficha y la lista
    assert client.get(f"/allies/{a['id']}").json()["participations"] == []
    assert {x["id"] for x in client.get("/allies").json()} >= {a["id"], b["id"]}
    # alta en un paso: invitar al primer usuario y la lista muestra el acceso al portal (pendiente, nunca entro)
    fila = next(x for x in client.get("/allies").json() if x["id"] == a["id"])
    assert fila["users_active"] == 0 and fila["invitations_pending"] == 0 and fila["last_login"] is None
    assert client.post(f"/allies/{a['id']}/invitations", json={"email": "comercial@aliado-uno.ejemplo.com", "role": "aliado"}).status_code == 201
    fila = next(x for x in client.get("/allies").json() if x["id"] == a["id"])
    assert fila["invitations_pending"] == 1 and fila["last_login"] is None


def test_permisos_de_administracion(client, auth, db_session):
    tid = auth["tenant"]["id"]
    a = aliado(client, dict(client.headers), "Aliado Uno S.A.", "3-101-000001")
    pid = _servicio_mensualidad(client)
    conta = interno(client, db_session, tid, "conta.aliados@ejemplo.com", "contabilidad")
    vend = interno(client, db_session, tid, "vend.aliados@ejemplo.com", "ventas")
    tec = interno(client, db_session, tid, "tec.aliados@ejemplo.com", "tecnico")
    assert client.get("/allies", headers=conta).status_code == 200
    assert client.put("/allies/prices", json=[], headers=conta).status_code == 403
    assert client.get(f"/allies/{a}/plan", headers=conta).status_code == 200
    plan = {"amount": 25000, "currency": "CRC", "mode": "fija", "service": "Soporte prioritario", "accepted": True, "next_date": "2026-11-01", "product_id": pid, "crear_cliente": True}
    assert client.put(f"/allies/{a}/plan", json=plan, headers=conta).status_code == 200
    assert client.post(f"/allies/{a}/invitations", json={"email": "x@aliado-uno.ejemplo.com", "role": "aliado"}, headers=conta).status_code == 403
    assert client.get("/allies", headers=vend).status_code == 403
    assert client.get("/allies/options", headers=vend).status_code == 200
    for path in ("/allies", "/allies/options"):
        assert client.get(path, headers=tec).status_code == 403, path


def test_usuarios_del_aliado_desde_admin_y_portal(client, auth, db_session):
    admin_h = dict(client.headers)
    a = aliado(client, admin_h, "Aliado Uno S.A.", "3-101-000001")
    b = aliado(client, admin_h, "Aliado Dos S.A.", "3-101-000002")
    jefe = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    usuario(client, admin_h, a, "tec@aliado-uno.ejemplo.com", "tecnico_aliado")
    roles = {u["email"]: u["role"] for u in client.get(f"/allies/{a}/users").json()["users"]}
    assert roles == {"jefe@aliado-uno.ejemplo.com": "aliado", "tec@aliado-uno.ejemplo.com": "tecnico_aliado"}
    r = client.post("/aliado/usuarios", json={"email": "nuevo@aliado-uno.ejemplo.com", "role": "aliado"}, headers=jefe)
    assert r.status_code == 202 and "link" not in r.json(), r.text  # el enlace le llega por correo, nunca al aliado
    inv = next(i for i in client.get("/aliado/usuarios", headers=jefe).json()["invitations"] if i["email"] == "nuevo@aliado-uno.ejemplo.com")
    assert inv["role"] == "tecnico_aliado"  # el aliado solo invita tecnicos
    assert client.post(f"/aliado/usuarios/invitaciones/{inv['id']}/revocar", headers=jefe).status_code == 200
    assert client.get("/aliado/usuarios", headers=jefe).json()["invitations"] == []
    ajena = client.post(f"/allies/{b}/invitations", json={"email": "pend@aliado-dos.ejemplo.com", "role": "tecnico_aliado"}).json()
    assert client.post(f"/aliado/usuarios/invitaciones/{ajena['id']}/revocar", headers=jefe).status_code == 404
    # desde administracion: cambiar rol (siempre de aliado) y nunca un rol interno
    tec = next(u for u in client.get(f"/allies/{a}/users").json()["users"] if u["role"] == "tecnico_aliado")
    assert client.patch(f"/allies/{a}/users/{tec['id']}", json={"role": "admin"}).status_code == 422
    assert client.patch(f"/allies/{b}/users/{tec['id']}", json={"active": False}).status_code == 404
    assert client.post(f"/allies/{a}/invitations", json={"email": "x@aliado-uno.ejemplo.com", "role": "ventas"}).status_code == 422


def test_auth_me_y_roles(client, auth, db_session):
    from app.core import deps
    from app.core.deps import ALLY_USER_ROLES, ASSIGNABLE_ROLES, ROLE_PERMISSIONS

    admin_h = dict(client.headers)
    a = aliado(client, admin_h, "Aliado Uno S.A.", "3-101-000001")
    h = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    me = client.get("/auth/me", headers=h).json()
    assert me["ally_company"]["id"] == a and me["ally_company"]["name"] == "Aliado Uno S.A." and me["ally_company"]["state"] == "activo"
    assert me["ally_company"]["features"]["ganancias"] is True
    assert client.get("/auth/me").json()["ally_company"] is None
    m = client.get("/auth/roles").json()
    assert {"aliado", "tecnico_aliado"} <= set(m["matrix"])
    assert not set(ALLY_USER_ROLES) & set(ASSIGNABLE_ROLES)
    assert "commissions" not in ROLE_PERMISSIONS["aliado"] and set(ROLE_PERMISSIONS["aliado"]) == {"aliado"}
    assert set(ROLE_PERMISSIONS["tecnico_aliado"]) == {"aliado"}
    assert not hasattr(deps, "ALLY_ROLES")


def test_buscador_solo_aliados_y_alta_rapida(client, auth, db_session):
    tid = auth["tenant"]["id"]
    a = aliado(
        client,
        dict(client.headers),
        "Aliado Uno S.A.",
        "3-101-000001",
        contacts={"comercial": {"name": "Contacto Comercial", "email": "ventas@aliado-uno.ejemplo.com"}},
    )
    from app.models import Supplier

    db_session.add(Supplier(tenant_id=tid, name="Proveedor Demo Buscador"))
    db_session.commit()
    vend = interno(client, db_session, tid, "vend.buscador@ejemplo.com", "ventas")
    hits = client.get("/opportunity-allies/companies?q=", headers=vend).json()
    assert hits and {x["kind"] for x in hits} == {"ally"} and hits[0]["tax_id"] == "3-101-000001"
    # cartera con dueno (ventas etapa 2): la vendedora solo ve sus clientes; crea uno para su oportunidad
    assert client.post("/customers", json={"name": "Cliente de la vendedora", "id_type": "fisica"}, headers=vend).status_code == 201
    todos = client.get("/opportunity-allies/companies?q=&todos=true", headers=vend).json()
    assert {x["kind"] for x in todos} == {"ally", "customer", "supplier"}
    r = client.post(
        "/opportunity-allies/companies",
        json={"name": "Aliado Nuevo S.A.", "tax_id": "3-101-000009", "contacts": {"tecnico": {"name": "Técnico Nuevo"}}},
        headers=vend,
    )
    assert r.status_code == 201, r.text
    assert set(r.json()) >= {"kind", "id", "name", "hint", "tax_id"} and r.json()["kind"] == "ally"
    r2 = client.post("/opportunity-allies/companies", json={"name": "aliado nuevo s.a."}, headers=vend)
    assert r2.status_code == 409 and r2.json()["existing"]["id"] == r.json()["id"]
    cust = client.get("/customers", headers=vend).json()["items"][0]
    o = client.post("/opportunities", json={"title": "Oportunidad de la vendedora", "customer_id": cust["id"]}, headers=vend).json()
    r = client.post(f"/opportunities/{o['id']}/allies", json={"ally_company_id": a, "role": "subcontratista"}, headers=vend)
    assert r.status_code == 201, r.text
    p = r.json()[0]
    assert p["company_kind"] == "ally" and p["ally_company_id"] == a and p["visible_to_ally"] is True
    assert p["contacts"]["comercial"]["name"] == "Contacto Comercial"  # precargados desde la empresa aliada
    sup = client.get("/opportunity-allies/companies?q=&todos=true", headers=vend).json()
    proveedor = next(x for x in sup if x["kind"] == "supplier")
    r = client.post(f"/opportunities/{o['id']}/allies", json={"supplier_id": proveedor["id"], "role": "suministro"}, headers=vend)
    assert r.status_code == 201 and any(x["company_kind"] == "supplier" for x in r.json())


def test_convertir_participacion_vieja(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert client.get(f"/aliado/oportunidades/{s.O6}", headers=s.a_jefe).status_code == 404
    r = client.post(f"/opportunity-allies/{s.part_O6}/to-ally", json={})
    assert r.status_code == 200, r.text
    p = next(x for x in r.json() if x["id"] == s.part_O6)
    assert p["ally_company_id"] == s.A and p["company_kind"] == "ally" and p["supplier_id"] is None
    assert client.get(f"/aliado/oportunidades/{s.O6}", headers=s.a_jefe).status_code == 200
    assert s.O6 in ids(client.get("/aliado/oportunidades?estado=todas", headers=s.a_jefe).json())
    assert client.post(f"/opportunity-allies/{s.part_O6}/to-ally", json={}).status_code == 409


def test_responsable_siempre_interno(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    r = client.post(f"/opportunity-allies/{s.part_O1}/requests", json={"what": "Precio de cableado", "responsible_id": s.uid_a_jefe})
    assert r.status_code == 422 and "equipo de Crimson" in r.json()["detail"]
    r = client.post("/opportunities", json={"title": "Con responsable aliado", "customer_id": s.c1, "owner_id": s.uid_a_jefe})
    assert r.status_code == 422
    o = client.post("/opportunities", json={"title": "Reasignar a un aliado", "customer_id": s.c1}).json()
    assert client.post(f"/opportunities/{o['id']}/assign", json={"owner_id": s.uid_a_tec}).status_code == 422
