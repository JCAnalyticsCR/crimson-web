"""Portal de aliados: la bateria de aislamiento (aliado_final §12.1, I1-I20).

La promesa: un usuario de una empresa aliada no entra a NADA interno (403), dentro de su portal solo ve lo que tiene
EN COMUN con Crimson (lo demas 404), nunca ve datos internos (costos, montos de la oportunidad, notas internas...) ni
lo de otra empresa aliada, y nada del navegador le elige la empresa."""

import re

import pytest
from fastapi import routing as fr
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.aliados_helpers import PW, aceptar, escenario, ids, interno, keys_rec, login, uid, usuario

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

# ---------- I1: toda ruta tiene dueno ----------
# Rutas sin get_principal / get_ally / get_client / get_session_principal: congeladas a mano, ruta por ruta.
PUBLICAS = {
    # cuenta: entrar, renovar, salir y recuperar la contrasena (antes de tener sesion)
    ("/auth/login", "POST"),
    ("/auth/logout", "POST"),
    ("/auth/refresh", "POST"),
    ("/auth/password-reset/check", "POST"),
    ("/auth/password-reset/confirm", "POST"),
    ("/auth/password-reset/request", "POST"),
    # invitaciones: la pagina publica /invitacion/:token
    ("/settings/invitations/accept", "POST"),
    ("/settings/invitations/check", "POST"),
    # salud, archivos con llave aleatoria (los privados piden firma) y webhooks firmados
    ("/health", "GET"),
    ("/media/f/{key}", "GET"),
    ("/webhooks/onvo/{tenant_id}", "POST"),
}
# Prefijos publicos por diseno: tienda, vitrina, SEO, eventos, pagos e incidentes (/public) y la API con llave (/v1)
PUBLIC_PREFIXES = ("/public/", "/v1/")


def _flat(routes):
    """(path, metodos, dependant) de todas las rutas (FastAPI nuevo agrupa las incluidas en _IncludedRouter)."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r.path, r.methods, r.dependant
        elif isinstance(r, getattr(fr, "_IncludedRouter", ())):
            yield from _flat(r.effective_candidates())
        elif isinstance(r, getattr(fr, "_EffectiveRouteContext", ())) and r.dependant is not None:
            yield r.path, r.methods, r.dependant


def _calls(dep, acc=None):
    acc = set() if acc is None else acc
    for d in dep.dependencies:
        acc.add(d.call)
        _calls(d, acc)
    return acc


def _routes():
    from app.main import create_app

    return list(_flat(create_app().routes))


def test_toda_ruta_tiene_dueno():
    from app.core.ally import get_ally
    from app.core.deps import get_principal, get_session_principal
    from app.routers.client_portal import get_client

    duenos = {get_principal, get_ally, get_client, get_session_principal}
    sin_dueno, solo_sesion, aliado_sin_puerta = [], [], []
    for path, methods, dep in _routes():
        c = _calls(dep)
        if path.startswith("/aliado") and get_ally not in c:
            aliado_sin_puerta.append(path)
        tiene = duenos & c
        if not tiene:
            for m in methods:
                if (path, m) not in PUBLICAS and not path.startswith(PUBLIC_PREFIXES):
                    sin_dueno.append((path, m))
        elif tiene == {get_session_principal} and not path.startswith("/auth/"):
            solo_sesion.append(path)
    assert aliado_sin_puerta == []
    assert solo_sesion == [], "solo /auth/ usa la sesion sin get_principal/get_ally/get_client"
    assert sin_dueno == [], "ruta nueva sin dueno: revisala y agregala a PUBLICAS si de verdad es publica"


# ---------- I2: barrido de internos ----------
EXPLICITAS = [
    "/media",
    "/media/sign",
    "/fx",
    "/tenant",
    "/auth/roles",
    "/settings/users",
    "/products",
    "/stock",
    "/opportunities",
    "/opportunity-allies/companies",
    "/allies",
    "/archive",
    "/commissions",
    "/commissions/earnings",
    "/search",
]


@pytest.mark.parametrize("rol", ["aliado", "tecnico_aliado"])
def test_barrido_internos_403(client, auth, db_session, rol):
    from app.core.deps import get_principal

    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Barrido S.A."}).json()["id"]
    h = usuario(client, admin_h, a, f"barrido.{rol}@aliado-uno.ejemplo.com", rol)
    client.headers.pop("Authorization")
    malos, barridas = [], 0
    for path, methods, dep in _routes():
        if get_principal not in _calls(dep):
            continue
        barridas += 1
        url = re.sub(r"\{[^}]+\}", "1", path)
        m = sorted(methods)[0]
        r = client.request(m, url, headers=h, json=None if m in ("GET", "DELETE") else {})
        if r.status_code != 403:
            malos.append((m, path, r.status_code))
    for url in EXPLICITAS:  # las que existen: 403; las que todavia no existen (economia) no pueden dar 200
        r = client.get(url, headers=h)
        if r.status_code not in (403, 404, 405):
            malos.append(("GET", url, r.status_code))
    client.headers.update(admin_h)
    assert barridas > 300 and malos == []


def test_portal_aliado_cerrado_a_otros(client, auth, db_session):
    from app.models import Customer, TenantUser, User

    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Cerrado S.A."}).json()["id"]
    al = usuario(client, admin_h, a, "jefe.cerrado@aliado-uno.ejemplo.com", "aliado")
    vend = interno(client, db_session, tid, "vendedora.cerrado@ejemplo.com", "ventas")
    from app.core.security import hash_password

    c = db_session.scalars(select(Customer).where(Customer.tenant_id == tid)).first()
    u = User(email="cliente.cerrado@ejemplo.com", full_name="Cliente Prueba", password_hash=hash_password(PW))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code="cliente_admin", customer_id=c.id))
    db_session.commit()
    cli = login(client, u.email)
    for h in (admin_h, vend, cli):
        for path in ("/aliado/inicio", "/aliado/oportunidades"):
            assert client.get(path, headers=h).status_code == 403, path
    assert client.get("/cliente/inicio", headers=al).status_code == 403
    assert client.get("/aliado/inicio", headers=al).status_code == 200


def test_membresia_inconsistente_bloqueada(client, auth, db_session):
    from app.core.security import hash_password
    from app.models import Customer, TenantUser, User

    tid = auth["tenant"]["id"]
    a = client.post("/allies", json={"name": "Aliado Raro S.A."}).json()["id"]
    raro = User(email="admin.raro@aliado-uno.ejemplo.com", full_name="Raro", password_hash=hash_password(PW))
    sin = User(email="sin.empresa@aliado-uno.ejemplo.com", full_name="Sin Empresa", password_hash=hash_password(PW))
    db_session.add_all([raro, sin])
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=raro.id, role_code="admin", ally_company_id=a))
    db_session.add(TenantUser(tenant_id=tid, user_id=sin.id, role_code="aliado"))
    db_session.commit()
    h1, h2 = login(client, raro.email), login(client, sin.email)
    assert client.get("/auth/me", headers=h1).status_code == 403
    assert client.get("/products", headers=h1).status_code == 403
    assert client.get("/auth/me", headers=h2).status_code == 403
    assert client.get("/aliado/inicio", headers=h2).status_code == 403
    # una membresia nunca es de un cliente y de un aliado a la vez (CHECK)
    c = db_session.scalars(select(Customer).where(Customer.tenant_id == tid)).first()
    doble = User(email="doble@aliado-uno.ejemplo.com", full_name="Doble", password_hash="x")
    db_session.add(doble)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=doble.id, role_code="aliado", customer_id=c.id, ally_company_id=a))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invitaciones_no_convierten_cuentas(client, auth, db_session):
    from app.models import Customer, TenantUser

    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A.", "tax_id": "3-101-000001"}).json()["id"]
    b = client.post("/allies", json={"name": "Aliado Dos S.A.", "tax_id": "3-101-000002"}).json()["id"]
    interno(client, db_session, tid, "interna@ejemplo.com", "ventas")
    c = db_session.scalars(select(Customer).where(Customer.tenant_id == tid)).first()
    r = client.post(f"/customers/{c.id}/portal/invitations", json={"email": "cliente@ejemplo.com", "role": "cliente_usuario"})
    assert r.status_code == 201, r.text
    aceptar(client, r.json()["link"], "Cliente Persona")
    usuario(client, admin_h, b, "persona@aliado-dos.ejemplo.com", "aliado")
    for correo in ("interna@ejemplo.com", "cliente@ejemplo.com", "persona@aliado-dos.ejemplo.com", "admin@crimsonapp.com"):
        r = client.post(f"/allies/{a}/invitations", json={"email": correo, "role": "aliado"})
        assert r.status_code == 409, (correo, r.text)
    h = usuario(client, admin_h, a, "nueva@aliado-uno.ejemplo.com", "aliado", "Nueva Persona")
    m = db_session.scalars(select(TenantUser).join(TenantUser.user).where(TenantUser.tenant_id == tid, TenantUser.ally_company_id == a)).one()
    assert m.role_code == "aliado" and m.customer_id is None
    me = client.get("/auth/me", headers=h).json()
    assert me["role"] == "aliado" and me["ally_company"]["name"] == "Aliado Uno S.A." and me["ally_company"]["state"] == "activo"
    lista = client.get("/settings/users").json()
    assert m.user_id not in {u["id"] for u in lista["users"]}
    assert all(i["email"] != "nueva@aliado-uno.ejemplo.com" for i in lista["invitations"])
    assert client.patch(f"/settings/users/{m.user_id}", json={"role": "ventas"}).status_code == 404
    assert client.post("/settings/invitations", json={"email": "otra@ejemplo.com", "role": "aliado"}).status_code == 422


def test_suspension_e_inactivacion(client, auth, db_session):
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Uno S.A."}).json()["id"]
    h = usuario(client, admin_h, a, "jefe@aliado-uno.ejemplo.com", "aliado")
    pendiente = client.post(f"/allies/{a}/invitations", json={"email": "pendiente@aliado-uno.ejemplo.com", "role": "tecnico_aliado"}).json()
    assert client.post(f"/allies/{a}/suspend", json={"reason": "Mensualidad atrasada"}).status_code == 200
    r = client.get("/aliado/oportunidades", headers=h)
    assert r.status_code == 403 and "Mensualidad atrasada" in r.json()["detail"]
    r = client.get("/aliado/inicio", headers=h)
    assert r.status_code == 200 and r.json()["company"]["state"] == "suspendido" and "counters" not in r.json()
    assert client.get("/aliado/suscripcion", headers=h).status_code == 200
    assert client.get("/aliado/facturas", headers=h).status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 200
    assert client.post(f"/allies/{a}/reactivate").status_code == 200
    assert client.get("/aliado/oportunidades", headers=h).status_code == 200
    assert client.post(f"/allies/{a}/deactivate").status_code == 200
    for path in ("/aliado/inicio", "/aliado/oportunidades", "/aliado/suscripcion"):
        assert client.get(path, headers=h).status_code == 403
    keep = client.headers.pop("Authorization")
    r = client.post("/settings/invitations/accept", json={"token": pendiente["link"].rsplit("/", 1)[-1], "full_name": "Pendiente Persona", "password": PW})
    client.headers["Authorization"] = keep
    assert r.status_code == 409


def test_tope_de_usuarios(client, auth, db_session):
    admin_h = dict(client.headers)
    a = client.post("/allies", json={"name": "Aliado Tope S.A.", "max_users": 2}).json()["id"]
    usuario(client, admin_h, a, "uno@aliado-uno.ejemplo.com", "aliado")
    usuario(client, admin_h, a, "dos@aliado-uno.ejemplo.com", "tecnico_aliado")
    r = client.post(f"/allies/{a}/invitations", json={"email": "tres@aliado-uno.ejemplo.com", "role": "tecnico_aliado"})
    assert r.status_code == 409 and "2 usuarios" in r.json()["detail"]
    dos = next(u for u in client.get(f"/allies/{a}/users").json()["users"] if u["email"] == "dos@aliado-uno.ejemplo.com")
    assert client.patch(f"/allies/{a}/users/{dos['id']}", json={"active": False}).status_code == 200
    assert client.post(f"/allies/{a}/invitations", json={"email": "tres@aliado-uno.ejemplo.com", "role": "tecnico_aliado"}).status_code == 201


def test_vista_previa_admin(client, auth, db_session):
    from app.models import AuditLog

    admin_h = dict(client.headers)
    s = escenario(client, admin_h, db_session)
    r = client.post(f"/allies/{s.A}/preview", json={})
    assert r.status_code == 200 and r.json()["company"]["name"] == "Aliado Uno S.A."
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "preview_ally", AuditLog.entity_id == s.A)).first() is not None
    pv = {**admin_h, "X-View-As-Ally": str(s.A)}
    reales = ids(client.get("/aliado/oportunidades?estado=todas", headers=s.a_jefe).json())
    assert ids(client.get("/aliado/oportunidades?estado=todas", headers=pv).json()) == reales == {s.O1, s.O3}
    assert client.post("/aliado/levantamientos", json={"project_id": s.P1}, headers=pv).status_code == 403
    pvt = {**pv, "X-View-As-User": str(s.uid_a_tec)}
    vista, real = client.get("/aliado/mi-dia", headers=pvt).json(), client.get("/aliado/mi-dia", headers=s.a_tec).json()
    for k in ("ordenes", "levantamientos", "tickets"):
        assert ids(vista[k]) == ids(real[k]), k
    assert all(not o["can_act"] for o in vista["ordenes"])  # vista previa: solo lectura
    assert client.get("/aliado/mi-dia", headers={**pv, "X-View-As-User": str(s.uid_b_tec)}).status_code == 422
    vend = interno(client, db_session, s.tid, "vendedora.preview@ejemplo.com", "ventas")
    assert client.get("/aliado/oportunidades", headers={**vend, "X-View-As-Ally": str(s.A)}).status_code == 403
    # solo el admin activa la vista previa
    assert client.post(f"/allies/{s.A}/preview", json={}, headers=vend).status_code == 403


def test_oportunidades_solo_en_comun(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    client.post(f"/opportunity-allies/{s.part_O3_B}/requests", json={"what": "Precio de lectoras para B"})
    assert ids(client.get("/aliado/oportunidades?estado=todas", headers=s.a_jefe).json()) == {s.O1, s.O3}
    assert ids(client.get("/aliado/oportunidades?estado=todas", headers=s.b_jefe).json()) == {s.O2, s.O3}
    for o in (s.O2, s.O4, s.O5, s.O6):
        assert client.get(f"/aliado/oportunidades/{o}", headers=s.a_jefe).status_code == 404, o
    r = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe)
    assert r.status_code == 200 and "Aliado Dos" not in r.text and "lectoras para B" not in r.text
    det = r.json()
    assert not {"amount", "currency", "probability", "weighted", "notes", "next_action", "lost_reason", "quote_id", "owner_id"} & set(det)
    assert not {"probability", "weighted", "notes", "next_action", "next_action_date", "lost_reason", "quote_id", "owner_id"} & keys_rec(det)
    assert {x["role"] for x in det["participations"]} == {"subcontratista", "suministro"}


def test_quitar_u_ocultar_revoca_al_instante(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert client.get(f"/aliado/oportunidades/{s.O1}", headers=s.a_jefe).status_code == 200
    r = client.put(f"/opportunity-allies/{s.part_O1}", json={"ally_company_id": s.A, "role": "subcontratista", "visible_to_ally": False})
    assert r.status_code == 200, r.text
    assert client.get(f"/aliado/oportunidades/{s.O1}", headers=s.a_jefe).status_code == 404
    assert client.get(f"/aliado/proyectos/{s.P1}", headers=s.a_jefe).status_code == 404
    assert s.O1 not in ids(client.get("/aliado/oportunidades?estado=todas", headers=s.a_jefe).json())
    assert s.P1 not in ids(client.get("/aliado/proyectos?estado=todos", headers=s.a_jefe).json())
    for aid in (s.part_O3_A, s.part_O3_A2):
        assert client.delete(f"/opportunity-allies/{aid}").status_code == 200
    assert client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe).status_code == 404
    assert client.get(f"/aliado/proyectos/{s.P3}", headers=s.a_jefe).status_code == 404
    assert client.get(f"/aliado/oportunidades/{s.O3}", headers=s.b_jefe).status_code == 200


def test_proyectos_sin_plata(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert ids(client.get("/aliado/proyectos?estado=todos", headers=s.a_jefe).json()) == {s.P1, s.P3}
    for pid in (s.P1, s.P3):
        det = client.get(f"/aliado/proyectos/{pid}", headers=s.a_jefe).json()
        assert not {"price", "economics", "cost_planned", "cost_labor", "quote_id", "invoice_id", "notes", "photos", "supervisor_id"} & set(det)
    p3 = client.get(f"/aliado/proyectos/{s.P3}", headers=s.a_jefe).json()
    assert s.W_B not in {o["id"] for o in p3["orders"]}
    assert client.get(f"/aliado/proyectos/{s.P2}", headers=s.a_jefe).status_code == 404


PROHIBIDAS = {
    "cost", "cost_currency", "margin_pct", "economics", "unit_cost", "supplier_id", "supplier_sku", "supplier_stock", "supplier",
    "offer_source", "billable", "invoice_id", "quote_id", "ip", "mac", "firmware", "purchase_ref", "probability", "weighted",
    "warehouse_id", "stock_applied", "base_amount", "percent",
}  # fmt: skip


def _gets(s):
    return [
        "/aliado/inicio",
        "/aliado/mi-dia",
        "/aliado/oportunidades?estado=todas",
        f"/aliado/oportunidades/{s.O1}",
        f"/aliado/oportunidades/{s.O3}",
        "/aliado/solicitudes",
        "/aliado/proyectos?estado=todos",
        f"/aliado/proyectos/{s.P1}",
        f"/aliado/proyectos/{s.P3}",
        "/aliado/clientes",
        "/aliado/tecnicos",
        "/aliado/levantamientos",
        f"/aliado/levantamientos/{s.S_A}",
        f"/aliado/levantamientos/{s.S_A}/sistema",
        "/aliado/ordenes?estado=todas",
        f"/aliado/ordenes/{s.W_hoy}",
        "/aliado/tickets?estado=todos",
        f"/aliado/tickets/{s.T_A}",
        "/aliado/mantenimientos",
        "/aliado/activos",
        "/aliado/catalogo",
        "/aliado/catalogo/meta",
        f"/aliado/catalogo/{s.prod}",
        "/aliado/productos/buscar",
        "/aliado/inventario?con_existencia=false",
        "/aliado/ganancias",
        f"/aliado/ganancias/proyectos/{s.P1}",
        "/aliado/suscripcion",
        "/aliado/facturas",
        "/aliado/usuarios",
    ]


def test_salidas_sin_datos_internos(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    client.post(f"/opportunity-allies/{s.part_O1}/requests", json={"what": "Precio de instalación"})
    for url in _gets(s):
        r = client.get(url, headers=s.a_jefe)
        assert r.status_code == 200, (url, r.text)
        malas = PROHIBIDAS & keys_rec(r.json())
        assert not malas, (url, malas)


def test_tecnico_aliado_solo_lo_de_hoy(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    dia = client.get("/aliado/mi-dia", headers=s.a_tec).json()
    vistos = ids(dia["ordenes"])
    assert s.W_hoy in vistos and not {s.W_manana, s.W_otro, s.W_ayer_fin, s.W_B, s.W_null} & vistos
    for w in (s.W_manana, s.W_otro, s.W_ayer_fin):
        assert client.get(f"/aliado/ordenes/{w}", headers=s.a_tec).status_code == 404, w
    r = client.post(f"/aliado/ordenes/{s.W_hoy}/finish", json={}, headers=s.a_tec)
    assert r.status_code == 200 and r.json()["status"] == "finalizada", r.text
    assert client.get(f"/aliado/ordenes/{s.W_hoy}", headers=s.a_tec).status_code == 200  # finalizada hoy: sigue
    for path in ("/aliado/catalogo", "/aliado/inventario", "/aliado/oportunidades", "/aliado/proyectos", "/aliado/ganancias", "/aliado/activos", "/aliado/mantenimientos", "/aliado/usuarios", "/aliado/suscripcion"):
        assert client.get(path, headers=s.a_tec).status_code == 403, path
    assert client.post("/aliado/levantamientos", json={"project_id": s.P1}, headers=s.a_tec).status_code == 403
    prods = client.get("/aliado/productos/buscar?q=demo", headers=s.a_tec).json()
    assert prods and not any("price" in k or "precio" in k for k in keys_rec(prods))


def test_levantamientos_por_empresa(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    vistos = ids(client.get("/aliado/levantamientos", headers=s.a_jefe).json())
    assert s.S_A in vistos and not {s.S_B, s.S_cr, s.S_null} & vistos
    for sid in (s.S_B, s.S_cr, s.S_null):
        assert client.get(f"/aliado/levantamientos/{sid}", headers=s.a_jefe).status_code == 404, sid
    assert s.S_A in ids(client.get("/aliado/levantamientos", headers=s.a_tec).json())
    assert s.S_A not in ids(client.get("/aliado/levantamientos", headers=s.a_tec2).json())
    assert client.get(f"/aliado/levantamientos/{s.S_A}", headers=s.a_tec2).status_code == 404


def test_tickets_y_notas(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    vistos = ids(client.get("/aliado/tickets?estado=todos", headers=s.a_jefe).json())
    assert {s.T_A, s.T_A_libre} <= vistos and not {s.T_B, s.T_null} & vistos
    for t in (s.T_B, s.T_null):
        assert client.get(f"/aliado/tickets/{t}", headers=s.a_jefe).status_code == 404
    det = client.get(f"/aliado/tickets/{s.T_A}", headers=s.a_jefe).json()
    cuerpos = {n["body"] for n in det["notes"]}
    assert cuerpos == {"Nota interna compartida con el aliado", "Respuesta publica al cliente", "Nota del aliado"}
    assert not {"amount", "billable", "invoice_id", "contract_id", "tags", "channel"} & set(det)
    tec = ids(client.get("/aliado/tickets?estado=todos", headers=s.a_tec).json())
    assert s.T_A in tec and s.T_A_libre not in tec


def test_mantenimientos_y_activos(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    ks = client.get("/aliado/mantenimientos", headers=s.a_jefe).json()
    assert ids(ks) == {s.K_A, s.K_P1}
    assert not {"amount", "currency", "notes"} & keys_rec(ks)
    act = ids(client.get("/aliado/activos", headers=s.a_jefe).json())
    assert act == {s.asset_p1}
    assert client.get(f"/aliado/activos?customer_id={s.c2}", headers=s.a_jefe).json() == []
    assert client.put("/allies/config", json={"assets_scope": "clientes"}).status_code == 200
    act = ids(client.get("/aliado/activos", headers=s.a_jefe).json())
    assert act == {s.asset_p1, s.asset_otro_proy} and s.asset_otro_cliente not in act


def test_el_navegador_no_elige_empresa(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    for extra in ({"ally_company_id": s.B}, {"customer_id": s.c2}, {"tenant_id": 99}):
        r = client.post("/aliado/levantamientos", json={"project_id": s.P1, **extra}, headers=s.a_jefe)
        assert r.status_code == 422, extra
    rq = client.post(f"/opportunity-allies/{s.part_O1}/requests", json={"what": "Precio de canalización"}).json()
    rid = next(r["id"] for a in rq for r in a["requests"])
    r = client.put(f"/aliado/solicitudes/{rid}", json={"amount": 1000, "currency": "CRC", "status": "aprobado"}, headers=s.a_jefe)
    assert r.status_code == 422
    assert client.patch(f"/aliado/tickets/{s.T_A}", json={"status": "cerrado"}, headers=s.a_jefe).status_code == 403


def test_media_ajena_rechazada(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    crimson = client.post("/media?visibility=private&module=field", files={"file": ("crimson.png", PNG + b"crimson", "image/png")}).json()
    r = client.post("/aliado/levantamientos", json={"project_id": s.P1, "photos": [crimson["url"]]}, headers=s.a_jefe)
    assert r.status_code == 422 and "fotograf" in r.json()["detail"]
    mia = client.post("/aliado/media", files={"file": ("mia.png", PNG + b"aliado", "image/png")}, headers=s.a_jefe)
    assert mia.status_code == 201, mia.text
    r = client.post("/aliado/levantamientos", json={"project_id": s.P1, "photos": [mia.json()["url"]]}, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    # los mismos bytes que una foto de Crimson: registro propio del aliado (no reutiliza el de Crimson)
    copia = client.post("/aliado/media", files={"file": ("copia.png", PNG + b"crimson", "image/png")}, headers=s.a_jefe).json()
    assert copia["id"] != crimson["id"]
    firmadas = client.post("/aliado/media/firmar", json={"urls": [crimson["url"], copia["url"]]}, headers=s.a_jefe).json()["urls"]
    assert crimson["url"] not in firmadas and copia["url"] in firmadas
    r = client.post(f"/aliado/proyectos/{s.P1}/precios", json={"what": "Mano de obra", "amount": 1000, "currency": "CRC", "attachment_ids": [crimson["id"]]}, headers=s.a_jefe)
    assert r.status_code == 404


def test_reasignacion_solo_dentro_del_equipo(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    r = client.patch(f"/aliado/ordenes/{s.W_hoy}/asignar", json={"technician_id": s.uid_a_tec2}, headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["technician_id"] == s.uid_a_tec2, r.text
    tec_interno = uid(client, interno(client, db_session, s.tid, "tecnico.interno@ejemplo.com", "tecnico"))
    for otro in (tec_interno, s.uid_b_tec):
        assert client.patch(f"/aliado/ordenes/{s.W_hoy}/asignar", json={"technician_id": otro}, headers=s.a_jefe).status_code == 422
    assert client.patch(f"/aliado/levantamientos/{s.S_A}/asignar", json={"technician_id": s.uid_b_tec}, headers=s.a_jefe).status_code == 422
    assert client.patch(f"/aliado/usuarios/{s.uid_b_tec}", json={"active": False}, headers=s.a_jefe).status_code == 404
    assert client.patch(f"/aliado/usuarios/{s.uid_a_jefe}", json={"active": False}, headers=s.a_jefe).status_code == 409


def test_dos_aliados_mismo_proyecto(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    client.post(f"/opportunity-allies/{s.part_O3_A}/requests", json={"what": "Precio para Uno"})
    client.post(f"/opportunity-allies/{s.part_O3_B}/requests", json={"what": "Precio para Dos"})
    for h, otro, mio, ajena in ((s.a_jefe, "Aliado Dos", "Precio para Uno", "Precio para Dos"), (s.b_jefe, "Aliado Uno", "Precio para Dos", "Precio para Uno")):
        for url in (f"/aliado/proyectos/{s.P3}", f"/aliado/oportunidades/{s.O3}", "/aliado/solicitudes", "/aliado/ordenes?estado=todas"):
            r = client.get(url, headers=h)
            assert r.status_code == 200 and otro not in r.text and ajena not in r.text, url
        assert mio in client.get("/aliado/solicitudes", headers=h).text
    a_orders = ids(client.get(f"/aliado/proyectos/{s.P3}", headers=s.a_jefe).json()["orders"])
    b_orders = ids(client.get(f"/aliado/proyectos/{s.P3}", headers=s.b_jefe).json()["orders"])
    assert s.W_B in b_orders and s.W_B not in a_orders
