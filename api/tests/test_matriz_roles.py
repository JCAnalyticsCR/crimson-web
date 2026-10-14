"""Matriz de roles definitiva (reunion con Andres, 13/10).

- Eventos sale del menu y de los permisos de todos los roles menos el admin (02:26: "nosotros no hacemos eventos").
- El costo lo ve solo el administrador (08:07) salvo los roles que el admin habilite en Ajustes -> "Quien ve costos".
- Al quitarle costos a bodega y al supervisor, editar ya no borra el costo del producto ni del proyecto.
- El tecnico no ve precios en ninguna salida (22:07-22:23): buscador, contratos, tickets y ficha del cliente.
- El vendedor queda recortado a lo suyo; aliado y tecnico de aliado existen pero no se asignan todavia.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.deps import ALLY_ROLES, ASSIGNABLE_ROLES, ROLE_PERMISSIONS

PW = "Rol-prueba-2026"


def _entrar(client, db_session, tid, email, role):
    """Crea un usuario con ese rol directo en la base y devuelve su encabezado de sesion (el admin sigue logueado)."""
    from app.core.security import hash_password
    from app.models import TenantUser, User

    u = User(email=email, full_name=role.capitalize(), password_hash=hash_password(PW))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code=role))
    db_session.commit()
    admin = client.headers.pop("Authorization", None)
    r = client.post("/auth/login", json={"email": email, "password": PW})
    client.headers["Authorization"] = admin
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(client, h) -> int:
    return client.get("/auth/me", headers=h).json()["user"]["id"]


def _producto(client) -> dict:
    """Producto generico (repo publico): precio 100, costo 80 en colones."""
    r = client.post("/products", json={"name": "Camara matriz", "code": "MAT-01", "price": 100, "currency": "CRC", "cost": 80, "cost_currency": "CRC"})
    assert r.status_code == 201, r.text
    return r.json()


def _cliente(client) -> dict:
    return client.get("/customers").json()["items"][0]


def test_eventos_fuera_de_todos_los_roles(client, auth, db_session):
    for r, perms in ROLE_PERMISSIONS.items():
        if r != "admin":
            assert "events" not in perms, r
    tid = auth["tenant"]["id"]
    for role in ("ventas", "caja", "lectura"):
        h = _entrar(client, db_session, tid, f"ev-{role}@ejemplo.com", role)
        assert client.get("/events", headers=h).status_code == 403
    # el codigo sigue vivo: el admin conserva la API (sin pantalla) y los datos no se tocan
    assert client.get("/events").status_code == 200


def test_costo_solo_admin_por_defecto(client, auth, db_session):
    for r, perms in ROLE_PERMISSIONS.items():
        if r != "admin":
            assert "costos" not in perms.get("catalog", []), r
    tid = auth["tenant"]["id"]
    prod = _producto(client)
    for role in ("supervisor", "contabilidad", "inventario", "lectura"):
        h = _entrar(client, db_session, tid, f"costo-{role}@ejemplo.com", role)
        pr = client.get(f"/products/{prod['id']}", headers=h).json()
        assert pr["cost"] is None and pr["margin_pct"] is None, role
        if role == "contabilidad":
            keys = {x["key"] for x in client.get("/reports", headers=h).json()}
            assert "rentabilidad" not in keys and "rentabilidad_tipo" not in keys
            assert client.get("/reports/rentabilidad", headers=h).status_code == 403
            g = client.get("/dashboard", headers=h).json()["gerencia"]
            assert g["profit_month"] is None and g["margin_month"] is None and g["projects_without_costs"] is None
            assert "pipeline" in g and g["pipeline"] is not None  # el resto de la fila de gerencia sigue
    assert client.get("/dashboard").json()["gerencia"]["margin_month"] is not None


def test_ajuste_quien_ve_costos(client, auth, db_session):
    from app.models import AuditLog

    tid = auth["tenant"]["id"]
    prod = _producto(client)
    k = _entrar(client, db_session, tid, "conta-costos@ejemplo.com", "contabilidad")
    # ventas, tecnicos, aliados (ni nadie fuera de la lista) nunca ven costos, ni por ajuste
    for malo in (["ventas"], ["tecnico"], ["aliado"], ["caja"], ["contabilidad", "tecnico_aliado"]):
        assert client.put("/settings", json={"cost_roles": malo}).status_code == 422, malo
    assert client.get(f"/products/{prod['id']}", headers=k).json()["cost"] is None
    # solo el admin cambia el ajuste
    assert client.put("/settings", json={"cost_roles": ["contabilidad"]}, headers=k).status_code == 403

    r = client.put("/settings", json={"cost_roles": ["contabilidad", "contabilidad"]})
    assert r.status_code == 200 and r.json()["cost_roles"] == ["contabilidad"]
    # efecto en la siguiente peticion, sin volver a iniciar sesion
    assert Decimal(str(client.get(f"/products/{prod['id']}", headers=k).json()["cost"])) == Decimal("80")
    assert "costos" in client.get("/auth/me", headers=k).json()["permissions"]["catalog"]
    m = client.get("/auth/roles").json()["matrix"]
    assert "costos" in m["contabilidad"]["catalog"] and "costos" not in m["supervisor"]["catalog"]
    assert "costos" not in ROLE_PERMISSIONS["contabilidad"]["catalog"]  # el mapa compartido no se muta
    u = client.get("/settings/users").json()
    assert u["cost_roles"] == ["contabilidad"] and set(u["cost_role_choices"]) == {"supervisor", "contabilidad", "inventario", "lectura"}
    log = db_session.scalars(select(AuditLog).where(AuditLog.action == "permissions", AuditLog.entity == "tenant")).all()
    assert len(log) == 1 and log[0].after == {"cost_roles": ["contabilidad"]} and log[0].before == {"cost_roles": []}
    # guardar lo mismo no ensucia la bitacora
    client.put("/settings", json={"cost_roles": ["contabilidad"]})
    assert len(db_session.scalars(select(AuditLog).where(AuditLog.action == "permissions", AuditLog.entity == "tenant")).all()) == 1

    assert client.put("/settings", json={"cost_roles": []}).status_code == 200
    assert client.get(f"/products/{prod['id']}", headers=k).json()["cost"] is None
    assert "costos" not in client.get("/auth/me", headers=k).json()["permissions"]["catalog"]


def test_ajuste_viejo_con_rol_no_elegible_se_ignora(client, auth, db_session):
    """Un ajuste escrito a mano con un rol que nunca ve costos no le abre nada."""
    from app.models import Tenant

    tid = auth["tenant"]["id"]
    prod = _producto(client)
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "cost_roles": ["ventas", "inventario"]}
    db_session.commit()
    v = _entrar(client, db_session, tid, "vend-viejo@ejemplo.com", "ventas")
    b = _entrar(client, db_session, tid, "bod-viejo@ejemplo.com", "inventario")
    assert client.get(f"/products/{prod['id']}", headers=v).json()["cost"] is None
    assert Decimal(str(client.get(f"/products/{prod['id']}", headers=b).json()["cost"])) == Decimal("80")
    assert client.get("/settings/users").json()["cost_roles"] == ["inventario"]


def test_editar_sin_ver_costos_no_borra_costos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prod = _producto(client)
    b = _entrar(client, db_session, tid, "bodega-edit@ejemplo.com", "inventario")
    r = client.put(f"/products/{prod['id']}", json={"name": "Camara matriz v2", "code": "MAT-01", "cost": None, "margin_pct": None}, headers=b)
    assert r.status_code == 200, r.text
    admin_view = client.get(f"/products/{prod['id']}").json()
    assert Decimal(str(admin_view["cost"])) == Decimal("80") and admin_view["name"] == "Camara matriz v2"
    # un producto creado por bodega nace sin costo aunque lo mande (lo carga el admin)
    nuevo = client.post("/products", json={"name": "Otro", "code": "MAT-02", "price": 50, "cost": 1}, headers=b)
    assert nuevo.status_code == 201 and client.get(f"/products/{nuevo.json()['id']}").json()["cost"] is None

    s = _entrar(client, db_session, tid, "sup-edit@ejemplo.com", "supervisor")
    sup_id = _uid(client, s)
    cust = _cliente(client)
    pr = client.post(
        "/projects",
        json={"name": "Proyecto matriz", "customer_id": cust["id"], "cost_labor": 400, "supervisor_id": sup_id, "photos": ["https://ejemplo.com/a.png"]},
    ).json()
    r = client.put(f"/projects/{pr['id']}", json={"name": "Proyecto matriz", "customer_id": cust["id"], "status": "en_curso", "cost_labor": 0}, headers=s)
    assert r.status_code == 200, r.text
    assert "economics" not in r.json()  # el supervisor no ve la economia
    out = client.get(f"/projects/{pr['id']}").json()
    assert Decimal(str(out["economics"]["cost_labor"])) == Decimal("400")
    assert out["status"] == "en_curso" and out["supervisor_id"] == sup_id and out["photos"] == ["https://ejemplo.com/a.png"]
    # el supervisor crea un proyecto: los costos que mande no se guardan
    otro = client.post("/projects", json={"name": "Proyecto sup", "customer_id": cust["id"], "cost_labor": 999}, headers=s).json()
    assert Decimal(str(client.get(f"/projects/{otro['id']}").json()["economics"]["cost_labor"])) == Decimal("0")
    # el admin si edita la economia
    client.put(f"/projects/{pr['id']}", json={"name": "Proyecto matriz", "cost_labor": 450})
    assert Decimal(str(client.get(f"/projects/{pr['id']}").json()["economics"]["cost_labor"])) == Decimal("450")


def test_compras_sin_costo_para_quien_no_ve_costos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prod = _producto(client)
    req = client.post("/purchase-requests", json={"lines": [{"product_id": prod["id"], "name": "Camara matriz", "quantity": 2}]})
    assert req.status_code == 201 and Decimal(str(req.json()["total_cost"])) == Decimal("160")
    s = _entrar(client, db_session, tid, "sup-compras@ejemplo.com", "supervisor")
    row = next(x for x in client.get("/purchase-requests", headers=s).json() if x["id"] == req.json()["id"])
    assert row["total_cost"] is None and row["lines"] and all(ln["unit_cost"] is None for ln in row["lines"])
    # lo que crea el supervisor tampoco le devuelve el costo
    mio = client.post("/purchase-requests", json={"lines": [{"product_id": prod["id"], "name": "Camara matriz", "quantity": 1}]}, headers=s).json()
    assert mio["total_cost"] is None
    k = _entrar(client, db_session, tid, "conta-compras@ejemplo.com", "contabilidad")
    assert all(x["total_cost"] is None for x in client.get("/purchase-requests", headers=k).json())


def test_tecnico_no_ve_precios_en_ninguna_salida(client, auth, db_session):
    from app.models import Order

    tid = auth["tenant"]["id"]
    prod = _producto(client)
    cust = _cliente(client)
    t = _entrar(client, db_session, tid, "tec-precios@ejemplo.com", "tecnico")
    tec_id = _uid(client, t)

    # buscador global: el producto sale, sin precio
    prods = [x for x in client.get("/search", params={"q": "MAT-01"}, headers=t).json() if x["kind"] == "producto"]
    assert prods and all("100" not in f"{x['title']} {x['sub']}" for x in prods)
    assert any("100" in x["sub"] for x in client.get("/search", params={"q": "MAT-01"}).json() if x["kind"] == "producto")

    # ficha de vitrina del producto (catalog.ver): sin precio ni precio anterior, aunque recorra los ids
    assert client.put(f"/store/showcase/products/{prod['id']}", json={"compare_price": 120}).status_code == 200
    vit = client.get(f"/store/showcase/products/{prod['id']}", headers=t)
    assert vit.status_code == 200 and vit.json()["price"] is None and vit.json()["compare_price"] is None
    adm_vit = client.get(f"/store/showcase/products/{prod['id']}").json()
    assert Decimal(str(adm_vit["price"])) == Decimal("100") and Decimal(str(adm_vit["compare_price"])) == Decimal("120")

    # contratos: los ve sin monto y no los crea, edita ni programa
    body = {"customer_id": cust["id"], "name": "Contrato matriz", "amount": 50000}
    ctr = client.post("/contracts", json=body).json()
    fila = next(x for x in client.get("/contracts", headers=t).json() if x["id"] == ctr["id"])
    assert fila["amount"] is None and fila["currency"] is None
    assert client.put(f"/contracts/{ctr['id']}", json=body, headers=t).status_code == 403
    assert client.post("/contracts", json=body, headers=t).status_code == 403
    assert client.post(f"/contracts/{ctr['id']}/ticket", headers=t).status_code == 403
    assert Decimal(str(next(x for x in client.get("/contracts").json() if x["id"] == ctr["id"])["amount"])) == Decimal("50000")

    # ticket cobrable asignado al tecnico: no ve el monto y al resolverlo no lo borra
    tk = client.post("/tickets", json={"subject": "Camara sin imagen", "customer_id": cust["id"], "assigned_to": tec_id}).json()
    assert client.patch(f"/tickets/{tk['id']}", json={"status": "asignado", "billable": True, "amount": 25000}).status_code == 200
    assert client.get(f"/tickets/{tk['id']}", headers=t).json()["amount"] is None
    assert all(x["amount"] is None for x in client.get("/tickets", headers=t).json())
    r = client.patch(f"/tickets/{tk['id']}", json={"status": "resuelto", "solution": "Se cambio la fuente", "amount": 0}, headers=t)
    assert r.status_code == 200 and r.json()["amount"] is None
    admin_tk = client.get(f"/tickets/{tk['id']}").json()
    assert Decimal(str(admin_tk["amount"])) == Decimal("25000") and admin_tk["status"] == "resuelto"

    # ficha 360 del cliente: sin pedidos, facturas ni cotizaciones, y los tickets sin monto
    db_session.add(Order(tenant_id=tid, number="WEB-MAT-1", customer_id=cust["id"], total=500, currency="CRC"))
    db_session.commit()
    client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]})
    ov = client.get(f"/customers/{cust['id']}/overview", headers=t).json()
    assert not {e["kind"] for e in ov["timeline"]} & {"orden", "factura", "cotizacion"}
    assert ov["tickets"] and all(x["amount"] is None for x in ov["tickets"])
    adm = client.get(f"/customers/{cust['id']}/overview").json()
    assert {"orden", "factura"} <= {e["kind"] for e in adm["timeline"]}

    # cupones (codigo y monto de descuento vigentes): son de quien administra la tienda, ni en su lista ni en el Archivo
    cp = client.post("/coupons", json={"code": "MATRIZ10", "value": 10}).json()
    assert client.get("/coupons", headers=t).status_code == 403
    assert client.post(f"/archive/coupon/{cp['id']}/archive").status_code == 200
    assert client.get("/archive", params={"kind": "coupon"}, headers=t).status_code == 403
    assert all(r["kind"] != "coupon" for r in client.get("/archive", headers=t).json()["rows"])
    assert any(r["kind"] == "coupon" and r["id"] == cp["id"] for r in client.get("/archive").json()["rows"])


def test_vendedor_matriz(client, auth, db_session):
    from app.models import Commission

    ventas = ROLE_PERMISSIONS["ventas"]
    # field.resumen: los levantamientos de sus oportunidades en solo lectura (frente vendedor); sin crear ni editar
    assert not {"events", "projects", "support_desk"} & set(ventas) and ventas["field"] == ["ver", "resumen"]
    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    v = _entrar(client, db_session, tid, "vend-matriz@ejemplo.com", "ventas")
    v_id = _uid(client, v)
    otro = _entrar(client, db_session, tid, "vend-otro@ejemplo.com", "ventas")
    otro_id = _uid(client, otro)

    assert client.get("/projects", headers=v).status_code == 403
    assert client.get("/tickets", headers=v).status_code == 403
    assert client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"]}, headers=v).status_code == 403

    # levantamientos de SUS oportunidades: resumen de solo lectura; los de oportunidades ajenas no existen para el
    mia = client.post("/opportunities", json={"title": "Camaras bodega", "customer_id": cust["id"], "amount": 500000}, headers=v).json()
    ajena = client.post("/opportunities", json={"title": "Camaras oficina", "customer_id": cust["id"], "amount": 300000}).json()
    s_mio = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": mia["id"]}).json()
    s_ajeno = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": ajena["id"]}).json()
    ids = {x["id"] for x in client.get("/surveys", headers=v).json()}
    assert s_mio["id"] in ids and s_ajeno["id"] not in ids
    assert client.get(f"/surveys/{s_mio['id']}", headers=v).status_code == 200
    assert client.get(f"/surveys/{s_ajeno['id']}", headers=v).status_code == 404
    assert client.put(f"/surveys/{s_mio['id']}", json={"kind": "cctv"}, headers=v).status_code == 403  # no lo edita
    # el tecnico sigue viendo solo los suyos
    t = _entrar(client, db_session, tid, "tec-matriz@ejemplo.com", "tecnico")
    assert {x["id"] for x in client.get("/surveys", headers=t).json()} == set()

    # ficha del cliente: solo sus oportunidades
    opps = {x["id"] for x in client.get(f"/customers/{cust['id']}/overview", headers=v).json()["opportunities"]}
    assert mia["id"] in opps and ajena["id"] not in opps

    # reglas de comision: la suya y la general, nunca la de otro vendedor
    client.post("/commissions/rules", json={"user_id": otro_id, "name": "Otro", "base": "venta", "percent": 7})
    client.post("/commissions/rules", json={"user_id": v_id, "name": "Mia", "base": "venta", "percent": 5})
    client.post("/commissions/rules", json={"name": "General", "base": "venta", "percent": 3})
    reglas = client.get("/commissions/rules", headers=v).json()
    assert {r["name"] for r in reglas} == {"Mia", "General"} and all(r["user_id"] != otro_id for r in reglas)
    assert len(client.get("/commissions/rules").json()) == 3
    # comision sobre margen: la base es la utilidad, no la ve
    db_session.add(Commission(tenant_id=tid, user_id=v_id, base="margen", base_amount=500, percent=10, amount=50, earned_on=date.today()))
    db_session.commit()
    fila = client.get("/commissions", headers=v).json()["rows"][0]
    assert fila["base_amount"] is None and Decimal(str(fila["amount"])) == Decimal("50")
    assert client.get("/commissions").json()["rows"][0]["base_amount"] is not None

    # cotizacion bajo el margen minimo: va a aprobacion sin decirle el margen exacto (deja deducir el costo)
    prod = _producto(client)
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": prod["id"], "quantity": 1}]}, headers=v).json()
    assert q["status"] == "por_aprobar" and "margen" in q["approval_reason"] and "20.0" not in q["approval_reason"]
    q_admin = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": prod["id"], "quantity": 1}]}).json()
    assert q_admin["status"] == "creado"  # quien aprueba no se pide permiso


def test_archivo_del_vendedor_solo_sus_oportunidades(client, auth, db_session):
    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    v = _entrar(client, db_session, tid, "vend-arch2@ejemplo.com", "ventas")
    mia = client.post("/opportunities", json={"title": "Mia", "customer_id": cust["id"], "amount": 1000}, headers=v).json()
    ajena = client.post("/opportunities", json={"title": "Ajena", "customer_id": cust["id"], "amount": 1000}).json()
    for o in (mia, ajena):
        assert client.post(f"/archive/opportunity/{o['id']}/archive").status_code == 200
    ids = {r["id"] for r in client.get("/archive", params={"kind": "opportunity"}, headers=v).json()["rows"]}
    assert mia["id"] in ids and ajena["id"] not in ids
    assert client.post(f"/archive/opportunity/{ajena['id']}/restore", headers=v).status_code == 404
    assert client.post(f"/archive/opportunity/{mia['id']}/restore", headers=v).status_code == 200


def test_aliados_definidos_pero_no_asignables(client, auth, db_session):
    for r in ALLY_ROLES:
        assert r in ROLE_PERMISSIONS and r not in ASSIGNABLE_ROLES
        perms = ROLE_PERMISSIONS[r]
        assert not {"sales", "payments", "accounting", "payroll", "reports", "purchases", "settings", "events", "vehicles"} & set(perms), r
        assert not {"precios", "costos"} & set(perms["catalog"]), r
        assert all("ver_todo" not in acts for acts in perms.values()), r
    assert not {"assets", "crm", "crm_pipeline"} & set(ROLE_PERMISSIONS["tecnico_aliado"])
    assert not set(ALLY_ROLES) & set(client.get("/settings/users").json()["roles"])
    assert client.post("/settings/invitations", json={"email": "aliado@ejemplo.com", "role": "aliado"}).status_code == 422
    assert client.post("/settings/invitations", json={"email": "tec.aliado@ejemplo.com", "role": "tecnico_aliado"}).status_code == 422
    # aparecen en "Ver como" (la matriz que usa la vista previa del admin)
    m = client.get("/auth/roles").json()
    assert set(ALLY_ROLES) <= set(m["matrix"]) and m["roles"][:4] == ["admin", "ventas", "contabilidad", "tecnico"]
    # tampoco se le asigna a un usuario que ya existe
    v = _entrar(client, db_session, auth["tenant"]["id"], "vend-aliado@ejemplo.com", "ventas")
    assert client.patch(f"/settings/users/{_uid(client, v)}", json={"role": "aliado"}).status_code == 422


def test_archivo_cerrado_a_aliados(client, auth, db_session):
    tid = auth["tenant"]["id"]
    from app.core.security import hash_password
    from app.models import TenantUser, User

    for role in ALLY_ROLES:
        u = User(email=f"{role}@ejemplo.com", full_name="Aliado", password_hash=hash_password(PW))
        db_session.add(u)
        db_session.flush()
        db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code=role))
        db_session.commit()
        admin = client.headers.pop("Authorization", None)
        r = client.post("/auth/login", json={"email": u.email, "password": PW})
        client.headers["Authorization"] = admin
        if r.status_code != 200:  # el frente de alcance aliado puede exigir empresa aliada desde el login
            assert r.status_code in (401, 403)
            continue
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert client.get("/archive", headers=h).status_code == 403
        assert client.get("/archive", params={"kind": "survey"}, headers=h).status_code == 403
        assert client.post("/archive/survey/1/restore", headers=h).status_code == 403


def test_avisos_de_pedidos_web_solo_roles_del_equipo(client, auth):
    cfg = {"methods": {"efectivo": {"active": True}}}
    for malo in (["aliado"], ["cliente_admin"], ["soporte"]):
        assert client.put("/store/checkout", json={**cfg, "notify_roles": malo}).status_code == 422, malo
    assert client.put("/store/checkout", json={**cfg, "notify_roles": ["admin", "ventas"]}).status_code == 200


def test_menu_y_api_coinciden_en_dinero():
    """Un rol sin precio de venta no abre cotizaciones, facturas ni cobros (soporte es lectura de diagnostico)."""
    for r, perms in ROLE_PERMISSIONS.items():
        if r in ("admin", "soporte"):
            continue
        if "precios" not in perms.get("catalog", []):
            assert "sales" not in perms and "payments" not in perms, r


# ---------- Hallazgos de la revision independiente ----------
def test_levantamiento_sin_tecnico_respeta_el_alcance_del_vendedor(client, auth, db_session):
    """La ficha tiene el mismo alcance que la lista: uno sin tecnico de una oportunidad ajena no se abre por id."""
    from app.models import Survey

    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    v = _entrar(client, db_session, tid, "vend-sintec@ejemplo.com", "ventas")
    mia = client.post("/opportunities", json={"title": "Mia", "customer_id": cust["id"], "amount": 1000}, headers=v).json()
    ajena = client.post("/opportunities", json={"title": "Ajena", "customer_id": cust["id"], "amount": 1000}).json()
    s_mio = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": mia["id"]}).json()
    s_ajeno = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": ajena["id"]}).json()
    s_suelto = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"]}).json()
    for x in (s_mio, s_ajeno, s_suelto):  # datos viejos o importados: sin tecnico
        db_session.get(Survey, x["id"]).technician_id = None
    db_session.commit()
    ids = {x["id"] for x in client.get("/surveys", headers=v).json()}
    assert s_mio["id"] in ids and not {s_ajeno["id"], s_suelto["id"]} & ids
    assert client.get(f"/surveys/{s_mio['id']}", headers=v).status_code == 200
    assert client.get(f"/surveys/{s_ajeno['id']}", headers=v).status_code == 404
    assert client.get(f"/surveys/{s_suelto['id']}", headers=v).status_code == 404
    # el tecnico conserva lo de antes: un levantamiento sin tecnico se le abre
    t = _entrar(client, db_session, tid, "tec-sintec@ejemplo.com", "tecnico")
    assert client.get(f"/surveys/{s_suelto['id']}", headers=t).status_code == 200


def test_archivo_del_vendedor_sin_levantamientos_que_no_restaura(client, auth, db_session):
    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    v = _entrar(client, db_session, tid, "vend-arch-lev@ejemplo.com", "ventas")
    mia = client.post("/opportunities", json={"title": "Mia", "customer_id": cust["id"], "amount": 1000}, headers=v).json()
    s = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": mia["id"]}).json()
    assert client.post(f"/archive/survey/{s['id']}/archive").status_code == 200
    r = client.get("/archive", params={"kind": "survey"}, headers=v)
    assert r.status_code == 200 and s["id"] not in {x["id"] for x in r.json()["rows"]}
    assert client.post(f"/archive/survey/{s['id']}/restore", headers=v).status_code == 403
    assert s["id"] in {x["id"] for x in client.get("/archive", params={"kind": "survey"}).json()["rows"]}


def test_notas_internas_sin_costo_ni_margen(client, auth, db_session):
    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    s = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "items": [{"name": "Gabinete 15U", "quantity": 1}]}).json()
    client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": s["items"][0]["id"], "unit_cost": "50000"}])
    q = client.post(f"/surveys/{s['id']}/quote", json={"margin": 35, "skip_review": True})
    assert q.status_code == 201, q.text
    notas = client.get(f"/quotes/{q.json()['quote_id']}").json()["internal_notes"]
    assert s["number"] in notas and "osto" not in notas and "margen" not in notas and "50" not in notas
    # documentos viejos (antes de la matriz) traen las cifras: a quien no ve costos no le salen
    vieja = "Levantamiento LEV-0099. Costo estimado ₡50,000 · margen 35 %."
    linea = [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]
    qv = client.post("/quotes", json={"customer_id": cust["id"], "internal_notes": vieja, "lines": linea}).json()
    iv = client.post("/invoices", json={"customer_id": cust["id"], "internal_notes": vieja, "lines": linea}).json()
    k = _entrar(client, db_session, tid, "conta-notas@ejemplo.com", "contabilidad")
    assert client.get(f"/quotes/{qv['id']}", headers=k).json()["internal_notes"] == "Levantamiento LEV-0099."
    assert client.get(f"/invoices/{iv['id']}", headers=k).json()["internal_notes"] == "Levantamiento LEV-0099."
    assert client.get(f"/quotes/{qv['id']}").json()["internal_notes"] == vieja  # el admin las sigue viendo


def test_tecnico_sin_precios_en_inventario_web_ni_variantes(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prod = _producto(client)
    r = client.put(f"/products/{prod['id']}/variants", json=[{"name": "Blanca", "code": "MAT-01-B", "price": 120}, {"name": "Negra", "code": "MAT-01-N"}])
    assert r.status_code == 200, r.text
    t = _entrar(client, db_session, tid, "tec-web@ejemplo.com", "tecnico")
    inv = client.get("/store/inventory", params={"todos": True}, headers=t)
    assert inv.status_code == 200
    fila = next(x for x in inv.json()["items"] if x["id"] == prod["id"])
    assert fila["price"] is None and fila["web_price"] is None
    adm = next(x for x in client.get("/store/inventory", params={"todos": True}).json()["items"] if x["id"] == prod["id"])
    assert Decimal(str(adm["price"])) == Decimal("100")
    vs = client.get(f"/products/{prod['id']}/variants", headers=t).json()
    assert len(vs) == 2 and all(x["price"] is None and x["effective_price"] is None for x in vs)
    assert {Decimal(str(x["effective_price"])) for x in client.get(f"/products/{prod['id']}/variants").json()} == {Decimal("120"), Decimal("100")}


def test_bodega_no_importa_listas_de_proveedor(client, auth, db_session):
    b = _entrar(client, db_session, auth["tenant"]["id"], "bodega-import@ejemplo.com", "inventario")
    archivo = ("lista.csv", b"Codigo,Descripcion,Precio\nX-1,Camara de prueba,10\n", "text/csv")
    for commit in (False, True):  # ni la vista previa: deja deducir el margen por defecto
        r = client.post("/import/catalogo", params={"commit": commit, "supplier": "Prov"}, files={"file": archivo}, headers=b)
        assert r.status_code == 403 and "costos" in r.json()["detail"]
    assert client.get("/products", params={"q": "X-1"}).json()["items"] == []


def test_solicitud_de_compra_no_toma_datos_de_otra_empresa(client, auth, db_session):
    from app.models import Product, Project, Supplier, Tenant

    otra = Tenant(slug="otra-compras", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    prod = Product(tenant_id=otra.id, name="Equipo ajeno", code="AJ-1", price=10, cost=7)
    sup = Supplier(tenant_id=otra.id, name="Proveedor ajeno")
    proj = Project(tenant_id=otra.id, number="PRO-AJ", name="Proyecto ajeno")
    db_session.add_all([prod, sup, proj])
    db_session.commit()
    linea = [{"name": "Material", "quantity": 1}]
    assert client.post("/purchase-requests", json={"lines": [{"product_id": prod.id, "name": "Equipo ajeno", "quantity": 1}]}).status_code == 404
    assert client.post("/purchase-requests", json={"supplier_id": sup.id, "lines": linea}).status_code == 404
    assert client.post("/purchase-requests", json={"project_id": proj.id, "lines": linea}).status_code == 404
    assert client.post("/purchase-requests", json={"lines": linea}).status_code == 201


def test_ficha_del_cliente_con_el_alcance_de_proyectos(client, auth, db_session):
    from app.models import Project

    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    t = _entrar(client, db_session, tid, "tec-proy@ejemplo.com", "tecnico")
    ajeno = client.post("/projects", json={"name": "Proyecto ajeno", "customer_id": cust["id"]}).json()
    suyo = client.post("/projects", json={"name": "Proyecto suyo", "customer_id": cust["id"]}).json()
    db_session.get(Project, suyo["id"]).supervisor_id = _uid(client, t)
    db_session.commit()
    ids = {x["id"] for x in client.get(f"/customers/{cust['id']}/overview", headers=t).json()["projects"]}
    assert suyo["id"] in ids and ajeno["id"] not in ids
    assert ids == {x["id"] for x in client.get("/projects", headers=t).json()}
    assert {ajeno["id"], suyo["id"]} <= {x["id"] for x in client.get(f"/customers/{cust['id']}/overview").json()["projects"]}


def test_monto_de_ticket_resuelto_lo_pone_admin_o_contador(client, auth, db_session):
    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    t = _entrar(client, db_session, tid, "tec-monto@ejemplo.com", "tecnico")
    tk = client.post("/tickets", json={"subject": "Camara sin imagen", "customer_id": cust["id"], "assigned_to": _uid(client, t)}).json()
    r = client.patch(f"/tickets/{tk['id']}", json={"status": "resuelto", "solution": "Se cambio la fuente", "billable": True, "amount": 0}, headers=t)
    assert r.status_code == 200 and r.json()["amount"] is None
    # ni el tecnico ni solo lectura escriben el monto
    lec = _entrar(client, db_session, tid, "lectura-monto@ejemplo.com", "lectura")
    for h in (t, lec):
        assert client.patch(f"/tickets/{tk['id']}/amount", json={"amount": 15000}, headers=h).status_code == 403
    k = _entrar(client, db_session, tid, "conta-monto@ejemplo.com", "contabilidad")
    assert client.get("/tickets/meta/config", headers=k).json()["can_charge"] is True
    assert client.get("/tickets/meta/config", headers=t).json()["can_charge"] is False
    r = client.patch(f"/tickets/{tk['id']}/amount", json={"amount": 15000}, headers=k)
    assert r.status_code == 200 and Decimal(str(r.json()["amount"])) == Decimal("15000") and r.json()["status"] == "resuelto"
    ov = client.get(f"/customers/{cust['id']}/overview").json()
    assert Decimal(str(next(x for x in ov["tickets"] if x["id"] == tk["id"])["amount"])) == Decimal("15000")
    # un ticket que no se cobra no lleva monto
    gratis = client.post("/tickets", json={"subject": "Garantia de camara", "customer_id": cust["id"]}).json()
    assert client.patch(f"/tickets/{gratis['id']}/amount", json={"amount": 100}).status_code == 409


def test_quien_ve_costos_solo_por_ajustes(client, auth):
    """PATCH /tenant no cambia "Quien ve costos" (no pasaria por el validador ni por la bitacora)."""
    assert client.patch("/tenant", json={"settings": {"cost_roles": ["contabilidad"]}}).status_code == 422
    assert client.get("/settings/users").json()["cost_roles"] == []
    # reenviar el dict completo sin cambiar quien ve costos sigue funcionando
    r = client.patch("/tenant", json={"settings": {"cost_roles": [], "quote_footer": "Gracias por su compra"}})
    assert r.status_code == 200 and client.get("/settings/users").json()["cost_roles"] == []


def test_fichas_no_enlazan_datos_de_otra_empresa(client, auth, db_session):
    """Proyectos, activos, tickets y contratos guardan ids que la ficha convierte en nombres: si fueran de otra empresa,
    quien crea o edita leeria clientes y usuarios ajenos recorriendo ids (y el aviso del ticket iria a un correo ajeno)."""
    from app.core.security import hash_password
    from app.models import Customer, CustomerAsset, EmailOutbox, Project, Tenant, TenantUser, User

    tid = auth["tenant"]["id"]
    cust = _cliente(client)
    otra = Tenant(slug="otra-fichas", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    cli = Customer(tenant_id=otra.id, name="Cliente ajeno de prueba", id_type="fisica", currency="CRC")
    proj = Project(tenant_id=otra.id, number="PRO-AJ2", name="Proyecto ajeno")
    ajeno = User(email="ajeno-fichas@ejemplo.com", full_name="Usuario ajeno", password_hash=hash_password(PW))
    portal = User(email="portal-fichas@ejemplo.com", full_name="Usuario del portal", password_hash=hash_password(PW))
    db_session.add_all([cli, proj, ajeno, portal])
    db_session.flush()
    act = CustomerAsset(tenant_id=otra.id, customer_id=cli.id, name="Camara ajena")
    db_session.add_all(
        [
            act,
            TenantUser(tenant_id=otra.id, user_id=ajeno.id, role_code="tecnico"),
            # usuario del portal de clientes de ESTA empresa: tampoco se asigna (le llegaria el ticket de otro cliente)
            TenantUser(tenant_id=tid, user_id=portal.id, role_code="cliente_usuario", customer_id=cust["id"]),
        ]
    )
    db_session.commit()
    s = _entrar(client, db_session, tid, "sup-fichas@ejemplo.com", "supervisor")
    t = _entrar(client, db_session, tid, "tec-fichas@ejemplo.com", "tecnico")

    # proyectos: crear, editar y desde una cotizacion
    assert client.post("/projects", json={"name": "Proyecto x", "customer_id": cli.id}, headers=s).status_code == 404
    for uid in (ajeno.id, portal.id):
        assert client.post("/projects", json={"name": "Proyecto x", "supervisor_id": uid}, headers=s).status_code == 422
    pr = client.post("/projects", json={"name": "Proyecto propio", "customer_id": cust["id"]}, headers=s).json()
    assert client.put(f"/projects/{pr['id']}", json={"name": "Proyecto propio", "customer_id": cli.id}, headers=s).status_code == 404
    assert client.put(f"/projects/{pr['id']}", json={"name": "Proyecto propio", "supervisor_id": ajeno.id}, headers=s).status_code == 422
    out = client.get(f"/projects/{pr['id']}").json()
    assert out["customer_id"] == cust["id"] and out["supervisor_id"] is None
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]})
    assert q.status_code == 201, q.text
    assert client.post(f"/quotes/{q.json()['id']}/project", json={"supervisor_id": ajeno.id}).status_code == 422
    assert client.post(f"/quotes/{q.json()['id']}/project", json={"supervisor_id": _uid(client, s)}).status_code == 201

    # activos del cliente (el tecnico los crea)
    base = {"customer_id": cust["id"], "name": "Camara x"}
    for extra in ({"customer_id": cli.id}, {"project_id": proj.id}):
        assert client.post("/assets", json={**base, **extra}, headers=t).status_code == 404, extra
    assert client.post("/assets", json=base, headers=t).status_code == 201

    # tickets: lo que enlaza y a quien se asigna
    primero = client.post("/tickets", json={"subject": "Camara sin imagen", "customer_id": cust["id"]}, headers=t).json()
    for extra in ({"customer_id": cli.id}, {"asset_id": act.id}, {"project_id": proj.id}):
        assert client.post("/tickets", json={"subject": "Camara sin imagen", **extra}, headers=t).status_code == 404, extra
    for uid in (ajeno.id, portal.id):
        assert client.post("/tickets", json={"subject": "Camara sin imagen", "assigned_to": uid}, headers=t).status_code == 422
    tk = client.post("/tickets", json={"subject": "Camara sin imagen", "customer_id": cust["id"]}, headers=t).json()
    # lo rechazado no gasto consecutivo
    assert int(tk["number"].rsplit("-", 1)[1]) == int(primero["number"].rsplit("-", 1)[1]) + 1
    assert client.put(f"/tickets/{tk['id']}", json={"subject": "Camara sin imagen", "customer_id": cli.id}, headers=t).status_code == 404
    r = client.put(f"/tickets/{tk['id']}", json={"subject": "Camara sin imagen", "customer_id": cust["id"], "assigned_to": ajeno.id}, headers=t)
    assert r.status_code == 422
    assert not db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == ajeno.email)).all()
    assert client.get(f"/tickets/{tk['id']}").json()["customer_id"] == cust["id"]

    # contratos (los maneja el admin o el supervisor)
    assert client.post("/contracts", json={"customer_id": cli.id, "name": "Contrato x"}).status_code == 404
    assert client.post("/contracts", json={"customer_id": cust["id"], "project_id": proj.id, "name": "Contrato x"}).status_code == 404
    ctr = client.post("/contracts", json={"customer_id": cust["id"], "name": "Contrato propio"}).json()
    assert client.put(f"/contracts/{ctr['id']}", json={"customer_id": cli.id, "name": "Contrato propio"}).status_code == 404
    assert client.get("/contracts").json()[0]["customer"] != "Cliente ajeno de prueba"
