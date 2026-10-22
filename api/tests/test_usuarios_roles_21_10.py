"""Reunion 21/10: usuarios, roles, avisos y comisiones (pedidos 20, 21, 22, 23, 25 y 48). Repo publico: solo nombres y
correos de prueba."""

from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.core.deps import ROLE_PERMISSIONS
from app.core.permisos import LOCKED, problems
from app.models import AuditLog, Customer, EmailOutbox, Invitation, Role, Tenant, TenantUser, User
from app.providers.einvoice import cr_now
from tests.aliados_helpers import ADMIN_PW, interno, login, uid

HOY = cr_now().date()


def _ids_por_correo(db_session, correo: str) -> list[EmailOutbox]:
    return list(db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == correo)))


# ---------- 20. Roles editables ----------
def test_los_permisos_de_fabrica_caben_en_el_catalogo():
    """Copiar o editar un rol predefinido nunca pierde nada: todo lo que tienen esta en el catalogo editable."""
    for code in ("ventas", "contabilidad", "tecnico", "supervisor", "caja", "inventario", "lectura"):
        assert problems(ROLE_PERMISSIONS[code]) == [], code
    assert {"sales.anular", "catalog.publicar"} <= LOCKED


def test_solo_el_admin_ve_y_edita_roles(client, auth, db_session):
    tid = auth["tenant"]["id"]
    r = client.get("/settings/roles")
    assert r.status_code == 200
    codes = [x["code"] for x in r.json()["roles"]]
    assert codes[0] == "admin" and "ventas" in codes and "soporte" not in codes and "aliado" not in codes
    assert any(m["module"] == "sales" for m in r.json()["catalog"])
    h = interno(client, db_session, tid, "conta.roles@ejemplo.com", "contabilidad")
    assert client.get("/settings/roles", headers=h).status_code == 403
    assert client.put("/settings/roles/ventas", json={"permissions": {"sales": ["ver"]}}, headers=h).status_code == 403
    assert client.post("/settings/roles", json={"name": "Otro", "from_code": "ventas"}, headers=h).status_code == 403


def test_editar_un_rol_predefinido_aplica_ya_y_queda_en_bitacora(client, auth, db_session):
    tid = auth["tenant"]["id"]
    h = interno(client, db_session, tid, "vende.roles@ejemplo.com", "ventas")
    assert "accounting" not in client.get("/auth/me", headers=h).json()["permissions"]
    nuevos = {**{k: list(v) for k, v in ROLE_PERMISSIONS["ventas"].items()}, "reports": ["ver"]}
    r = client.put("/settings/roles/ventas", json={"permissions": nuevos, "reason": "Que vea reportes"})
    assert r.status_code == 200 and r.json()["customized"] is True
    me = client.get("/auth/me", headers=h).json()
    assert me["permissions"]["reports"] == ["ver"] and me["role_customized"] is True
    assert client.get("/auth/roles").json()["matrix"]["ventas"]["reports"] == ["ver"]
    fila = next(x for x in client.get("/settings/roles").json()["roles"] if x["code"] == "ventas")
    assert fila["customized"] and fila["default_permissions"] == {k: v for k, v in ROLE_PERMISSIONS["ventas"].items()}
    log = db_session.scalars(select(AuditLog).where(AuditLog.entity == "role", AuditLog.action == "permissions")).all()
    assert log and log[-1].reason == "Que vea reportes"
    # quitar permisos tambien aplica en la siguiente peticion (sin cerrar sesion)
    assert client.put("/settings/roles/ventas", json={"permissions": {"dashboard": ["ver"], "crm": ["ver"]}}).status_code == 200
    assert client.get("/quotes", headers=h).status_code == 403
    # restablecer vuelve a los de fabrica
    assert client.post("/settings/roles/ventas/reset").status_code == 200
    assert client.get("/quotes", headers=h).status_code == 200
    assert client.get("/auth/me", headers=h).json()["role_customized"] is False
    # el administrador no se edita
    assert client.put("/settings/roles/admin", json={"permissions": {"sales": ["ver"]}}).status_code == 409


def test_un_rol_editado_nunca_recibe_anular_ni_publicar(client, auth, db_session):
    for malo in ({"sales": ["ver", "anular"]}, {"catalog": ["ver", "publicar"]}, {"catalog": ["ver", "costos"]}, {"*": ["*"]}, {"settings": ["configurar"]}):
        r = client.put("/settings/roles/supervisor", json={"permissions": malo})
        assert r.status_code == 422, malo
        assert "administrador" in r.json()["detail"]
        r = client.post("/settings/roles", json={"name": "Con anular", "from_code": "ventas", "permissions": malo})
        assert r.status_code == 422, malo
    assert client.put("/settings/roles/supervisor", json={"permissions": {"inventario_fantasma": ["ver"]}}).status_code == 422
    # aunque alguien escribiera el ajuste a mano en la base, no aplica
    t = db_session.get(Tenant, auth["tenant"]["id"])
    t.settings = {**(t.settings or {}), "role_overrides": {"caja": {"sales": ["ver", "anular", "ver_todo"], "catalog": ["ver", "publicar"]}}}
    db_session.commit()
    h = interno(client, db_session, t.id, "caja.roles@ejemplo.com", "caja")
    perms = client.get("/auth/me", headers=h).json()["permissions"]
    assert "anular" not in perms["sales"] and "publicar" not in perms.get("catalog", [])
    c = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 0}]}).json()
    assert client.post(f"/invoices/{inv['id']}/void", headers=h).status_code == 403


def test_crear_un_rol_a_partir_de_otro_y_asignarlo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    r = client.post("/settings/roles", json={"name": "Vendedor senior", "from_code": "ventas"})
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    role = db_session.scalar(select(Role).where(Role.code == code))
    assert role.tenant_id == tid and role.base_code == "ventas"
    assert role.permissions["sales"] == ROLE_PERMISSIONS["ventas"]["sales"]
    assert client.post("/settings/roles", json={"name": "vendedor SENIOR", "from_code": "ventas"}).status_code == 409
    # se le suma algo y se asigna a una persona: entra con esos permisos y su nombre de rol
    perms = {**role.permissions, "projects": ["ver", "ver_todo"]}
    assert client.put(f"/settings/roles/{code}", json={"permissions": perms, "name": "Vendedor senior"}).status_code == 200
    h = interno(client, db_session, tid, "senior.roles@ejemplo.com", "ventas")
    u = uid(client, h)
    assert code in client.get("/settings/users").json()["roles"]
    assert client.get("/settings/users").json()["role_labels"][code] == "Vendedor senior"
    assert client.patch(f"/settings/users/{u}", json={"role": code}).status_code == 200
    me = client.get("/auth/me", headers=h).json()
    assert me["role"] == code and me["role_label"] == "Vendedor senior" and me["permissions"]["projects"] == ["ver", "ver_todo"]
    assert "anular" not in me["permissions"]["sales"]
    assert code in client.get("/auth/roles").json()["matrix"]
    # invitar con ese rol tambien vale
    assert client.post("/settings/invitations", json={"email": "nuevo.senior@ejemplo.com", "role": code}).status_code == 201
    # en uso no se borra
    assert client.delete(f"/settings/roles/{code}").status_code == 409
    # copiar del administrador da todo lo repartible, sin lo de solo administrador
    r = client.post("/settings/roles", json={"name": "Casi todo", "from_code": "admin"})
    casi = db_session.scalar(select(Role).where(Role.code == r.json()["code"]))
    assert "anular" not in casi.permissions["sales"] and "publicar" not in casi.permissions["catalog"] and "settings" in casi.permissions
    assert client.delete(f"/settings/roles/{casi.code}").status_code == 200


# ---------- 21. Eliminar usuarios sin actividad ----------
def test_eliminar_usuario_sin_actividad_y_desactivar_al_que_tiene(client, auth, db_session):
    tid = auth["tenant"]["id"]
    h_prueba = interno(client, db_session, tid, "vendedor.prueba@ejemplo.com", "ventas")  # entro una vez: hay login
    prueba = uid(client, h_prueba)
    h_activo = interno(client, db_session, tid, "vendedor.activo@ejemplo.com", "ventas")
    activo = uid(client, h_activo)
    c = client.get("/customers").json()["items"][0]
    assert client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 0}]}, headers=h_activo).status_code == 201

    a = client.get(f"/settings/users/{activo}/activity").json()
    assert a["can_delete"] is False and "actividad" in a["reason"] and any(x["what"] == "Cotizaciones" for x in a["activity"])
    r = client.post(f"/settings/users/{activo}/delete", json={"admin_password": ADMIN_PW})
    assert r.status_code == 409 and "desactiv" in r.json()["detail"]

    assert client.get(f"/settings/users/{prueba}/activity").json()["can_delete"] is True
    assert client.post(f"/settings/users/{prueba}/delete", json={"admin_password": "otra-cosa-123"}).status_code == 403
    r = client.post(f"/settings/users/{prueba}/delete", json={"admin_password": ADMIN_PW, "reason": "cuenta de prueba"})
    assert r.status_code == 200, r.text
    assert prueba not in [x["id"] for x in client.get("/settings/users").json()["users"]]
    assert client.get("/auth/me", headers=h_prueba).status_code == 401  # su sesion muere
    u = db_session.get(User, prueba)
    db_session.refresh(u) if u is not None else None
    assert u is None or (not u.active and u.email.endswith("@eliminado.invalid"))
    assert db_session.scalar(select(TenantUser).where(TenantUser.user_id == prueba)) is None
    # el correo queda libre: se puede invitar de nuevo
    assert client.post("/settings/invitations", json={"email": "vendedor.prueba@ejemplo.com", "role": "ventas"}).status_code == 201
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "user_delete", AuditLog.entity_id == prueba)).one().reason == "cuenta de prueba"
    # a si mismo no; y no lo hace quien no es admin
    me = client.get("/auth/me").json()["user"]["id"]
    assert client.post(f"/settings/users/{me}/delete", json={"admin_password": ADMIN_PW}).status_code == 409


def test_usuario_que_nunca_entro_se_borra_de_verdad(client, auth, db_session):
    from app.core.security import hash_password

    u = User(email="nunca.entro@ejemplo.com", full_name="Nunca Entro", password_hash=hash_password("Prueba-2026-x"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=auth["tenant"]["id"], user_id=u.id, role_code="lectura"))
    db_session.commit()
    uid_ = u.id
    r = client.post(f"/settings/users/{uid_}/delete", json={"admin_password": ADMIN_PW})
    assert r.status_code == 200 and r.json()["mode"] == "borrada"
    db_session.expire_all()
    assert db_session.get(User, uid_) is None


# ---------- 22. Correo de avisos por usuario ----------
def test_correo_de_avisos_recibe_las_notificaciones_internas(client, auth, db_session):
    from app.services.mail import notify_roles

    tid = auth["tenant"]["id"]
    h = interno(client, db_session, tid, "vende.avisos@ejemplo.com", "ventas")
    u = uid(client, h)
    # desviar los avisos pide la contrasena del admin
    assert client.patch(f"/settings/users/{u}", json={"notify_email": "buzon.vendedor@ejemplo.com"}).status_code == 403
    r = client.patch(f"/settings/users/{u}", json={"notify_email": "buzon.vendedor@ejemplo.com", "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    fila = next(x for x in client.get("/settings/users").json()["users"] if x["id"] == u)
    assert fila["notify_email"] == "buzon.vendedor@ejemplo.com" and fila["email"] == "vende.avisos@ejemplo.com"
    # la persona se entera en su correo de entrada
    assert any(m.entity == "user_security" for m in _ids_por_correo(db_session, "vende.avisos@ejemplo.com"))
    t = db_session.get(Tenant, tid)
    notify_roles(db_session, t, ("ventas",), "Aviso interno de prueba", "<p>x</p>", "order", 1)
    db_session.commit()
    assert any(m.subject == "Aviso interno de prueba" for m in _ids_por_correo(db_session, "buzon.vendedor@ejemplo.com"))
    assert not any(m.subject == "Aviso interno de prueba" for m in _ids_por_correo(db_session, "vende.avisos@ejemplo.com"))
    # quitarlo no pide contrasena y los avisos vuelven al de entrada
    assert client.patch(f"/settings/users/{u}", json={"notify_email": ""}).status_code == 200
    assert db_session.scalar(select(TenantUser.notify_email).where(TenantUser.user_id == u)) is None


# ---------- 23. Ficha del cliente de solo lectura para el vendedor ----------
def test_vendedor_crea_clientes_pero_no_edita_los_existentes(client, auth, db_session):
    h = interno(client, db_session, auth["tenant"]["id"], "vende.ficha@ejemplo.com", "ventas")
    c = client.get("/customers", headers=h).json()["items"][0]
    assert client.get(f"/customers/{c['id']}", headers=h).status_code == 200
    assert client.get(f"/customers/{c['id']}/overview", headers=h).status_code == 200
    assert client.put(f"/customers/{c['id']}", json={"name": c["name"], "email": "otro@ejemplo.com"}, headers=h).status_code == 403
    assert client.post(f"/customers/{c['id']}/contacts", json={"name": "Contacto", "email": "otro@ejemplo.com"}, headers=h).status_code == 403
    assert client.delete(f"/customers/{c['id']}", headers=h).status_code == 403
    assert client.post(f"/customers/{c['id']}/notes", json={"body": "Llamar el lunes"}, headers=h).status_code == 201
    nuevo = client.post("/customers", json={"name": "Cliente Nuevo Prueba", "id_type": "fisica", "email": "nuevo@ejemplo.com"}, headers=h)
    assert nuevo.status_code == 201
    # el admin si edita
    assert client.put(f"/customers/{c['id']}", json={"name": c["name"], "phone": "8888-1111"}).status_code == 200


def test_importar_clientes_sin_editar_no_sobrescribe(client, auth, db_session):
    h = interno(client, db_session, auth["tenant"]["id"], "vende.importa@ejemplo.com", "ventas")
    c = client.get("/customers").json()["items"][0]
    assert c["email"]
    csv = f"Nombre,Correo electronico\nNombre Cambiado,{c['email']}\nCliente Importado,importado@ejemplo.com\n".encode()
    r = client.post("/import/customers?commit=true", files={"file": ("clientes.csv", csv, "text/csv")}, headers=h)
    assert r.status_code == 200, r.text
    acciones = [x["action"] for x in r.json()["result"]]
    assert acciones == ["omitir", "nuevo"]
    assert client.get(f"/customers/{c['id']}").json()["name"] == c["name"]


# ---------- 25. Regla de comision aplicada hacia atras ----------
def test_regla_nueva_se_aplica_a_fechas_anteriores_y_el_vendedor_la_ve_pendiente(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh = interno(client, db_session, tid, "vende.comision@ejemplo.com", "ventas")
    vid = uid(client, vh)
    c = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Instalación", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}]}, headers=vh).json()
    assert client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 1130000, "kind": "captura"}).status_code == 201
    assert client.get("/commissions/summary", headers=vh).json()["ganado"]["total"] in (0, "0", "0.00")
    desde = str(HOY.replace(day=1))
    body = {"user_id": vid, "name": "Vendedora", "percent": 5, "apply_from": desde}
    r = client.post("/commissions/rules", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["backfill"]["creados"] == 1
    s = client.get("/commissions/summary", headers=vh).json()
    assert Decimal(str(s["ganado"]["pendiente"])) == Decimal("50000.00")
    filas = client.get("/commissions", headers=vh).json()["rows"]
    assert [f["status"] for f in filas] == ["pendiente"]
    # fecha futura: 422 y la regla no se crea
    from datetime import timedelta

    n = len(client.get("/commissions/rules").json())
    assert client.post("/commissions/rules", json={**body, "name": "Otra", "apply_from": str(HOY + timedelta(days=2))}).status_code == 422
    assert len(client.get("/commissions/rules").json()) == n


# ---------- 48. Correo para avisos internos ----------
def test_correo_para_avisos_internos_recibe_pedidos_y_ventas(client, auth, db_session):
    from app.services.store_orders import notify_team

    # un buzon nuevo pide la contrasena (recibe datos de clientes y de sus pagos)
    assert client.put("/settings", json={"internal_notify_emails": ["Ventas.Buzon@ejemplo.com"]}).status_code == 403
    r = client.put("/settings", json={"internal_notify_emails": ["Ventas.Buzon@ejemplo.com"], "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    assert client.get("/settings").json()["internal_notify_emails"] == ["ventas.buzon@ejemplo.com"]
    # guardar lo mismo otra vez no escribe otra fila en la bitacora ni pide contrasena
    n_log = len(db_session.scalars(select(AuditLog).where(AuditLog.reason == "Correo para avisos internos")).all())
    assert n_log == 1
    assert client.put("/settings", json={"internal_notify_emails": ["ventas.buzon@ejemplo.com"]}).status_code == 200
    assert len(db_session.scalars(select(AuditLog).where(AuditLog.reason == "Correo para avisos internos")).all()) == 1
    t = db_session.get(Tenant, auth["tenant"]["id"])
    db_session.refresh(t)
    o = SimpleNamespace(
        id=1, number="WEB-0001", total=Decimal("10000"), currency="CRC", contact={"name": "Cliente Web", "email": "cliente@ejemplo.com"},
        payment_method="SINPE Móvil", shipping_method="Retiro", status="nuevo",
        # lo que el aviso con el diseno base lee del pedido (vendedor, resumen de lineas y totales)
        seller_id=None, lines=[], shipping=Decimal(0), tax_total=Decimal(0), discount_total=Decimal(0), coupon_code=None,
    )
    n = notify_team(db_session, t, o, "nuevo")
    db_session.commit()
    assert n >= 2  # el admin y el buzon interno
    buzon = _ids_por_correo(db_session, "ventas.buzon@ejemplo.com")
    assert len(buzon) == 1 and buzon[0].subject.startswith("Pedido web WEB-0001") and not buzon[0].bcc
    # en "Correos que no reciben nada del sistema": no le llega
    assert client.put("/settings", json={"mail_blocklist": ["ventas.buzon@ejemplo.com"]}).status_code == 200
    db_session.refresh(t)
    notify_team(db_session, t, o, "comprobante")
    db_session.commit()
    assert len(_ids_por_correo(db_session, "ventas.buzon@ejemplo.com")) == 1
    assert client.put("/settings", json={"internal_notify_emails": [f"x{i}@ejemplo.com" for i in range(6)]}).status_code == 422


def test_login_helper_sigue_sirviendo(client, auth, db_session):
    """Sanidad: un usuario creado directo en la base entra (los helpers de arriba dependen de esto)."""
    interno(client, db_session, auth["tenant"]["id"], "sanidad.roles@ejemplo.com", "lectura")
    assert login(client, "sanidad.roles@ejemplo.com")


# ---------- Revision: lo que el codigo decide por rol tambien alcanza a los roles creados ----------
def _rol(client, nombre: str, desde: str) -> str:
    r = client.post("/settings/roles", json={"name": nombre, "from_code": desde})
    assert r.status_code == 201, r.text
    return r.json()["code"]


def test_rol_creado_desde_ventas_comisiona_recibe_avisos_y_sale_como_vendedor(client, auth, db_session):
    from app.services.commissions import rules_for_user
    from app.services.mail import notify_roles
    from app.services.store_orders import checkout_cfg, notify_team

    tid = auth["tenant"]["id"]
    code = _rol(client, "Vendedor senior", "ventas")
    t = db_session.get(Tenant, tid)
    db_session.refresh(t)
    # hereda lo que en Ajustes se decide por rol: la regla de «todos los vendedores» y los avisos de pedidos web
    cfg = client.get("/commissions/config").json()
    assert code in cfg["general_rule_roles"] and code in cfg["role_choices"] and cfg["role_labels"][code] == "Vendedor senior"
    assert code in checkout_cfg(t)["notify_roles"]
    assert code in client.get("/store/checkout").json()["role_choices"]
    h = interno(client, db_session, tid, "senior.hereda@ejemplo.com", code)
    u = uid(client, h)
    # la regla general le alcanza
    assert client.post("/commissions/rules", json={"name": "General", "percent": 3}).status_code == 201
    assert any(r.user_id is None for r in rules_for_user(db_session, t, u, code))
    # sale en la lista de responsables de oportunidades
    assert u in [x["id"] for x in client.get("/opportunities/meta/config").json()["sellers"]]
    # aviso de pedido web y avisos a "ventas" escritos en el codigo (p. ej. la asesoria web de la tienda)
    o = SimpleNamespace(
        id=7, number="WEB-0007", total=Decimal("1000"), currency="CRC", contact={"name": "Cliente Web", "email": "cliente@ejemplo.com"},
        payment_method="SINPE Móvil", shipping_method="Retiro", status="nuevo",
        # lo que el aviso con el diseno base lee del pedido (vendedor, resumen de lineas y totales)
        seller_id=None, lines=[], shipping=Decimal(0), tax_total=Decimal(0), discount_total=Decimal(0), coupon_code=None,
    )
    notify_team(db_session, t, o, "nuevo")
    notify_roles(db_session, t, ("ventas",), "Asesoría web de prueba", "<p>x</p>", "opportunity", 1)
    db_session.commit()
    asuntos = [m.subject for m in _ids_por_correo(db_session, "senior.hereda@ejemplo.com")]
    assert any(a.startswith("Pedido web WEB-0007") for a in asuntos) and "Asesoría web de prueba" in asuntos
    # el correo de invitacion dice el nombre del rol, no el codigo
    assert client.post("/settings/invitations", json={"email": "otro.senior@ejemplo.com", "role": code}).status_code == 201
    inv = _ids_por_correo(db_session, "otro.senior@ejemplo.com")[-1]
    assert "Vendedor senior" in inv.html and code not in inv.html
    # borrar el rol (sin nadie que lo tenga) lo saca de esas listas
    db_session.execute(TenantUser.__table__.delete().where(TenantUser.user_id == u))
    for i in db_session.scalars(select(Invitation).where(Invitation.role_code == code)):
        i.revoked_at = cr_now()
    db_session.commit()
    assert client.delete(f"/settings/roles/{code}").status_code == 200
    assert code not in client.get("/commissions/config").json()["general_rule_roles"]


def test_role_label_usa_el_nombre_del_rol_creado(client, auth, db_session):
    from app.services.accounts import role_label

    code = _rol(client, "Bodega senior", "inventario")
    assert role_label(code, db_session) == "Bodega senior"
    assert role_label("ventas", db_session) == "Vendedor"


def test_rol_creado_desde_tecnico_recibe_ordenes_y_tickets(client, auth, db_session):
    from tests.test_portal_cliente import _crear_cliente_portal

    tid = auth["tenant"]["id"]
    code = _rol(client, "Técnico senior", "tecnico")
    h = interno(client, db_session, tid, "tec.senior@ejemplo.com", code)
    u = uid(client, h)
    assert u in [x["id"] for x in client.get("/field/technicians").json()]
    assert u in [x["id"] for x in client.get("/tickets/meta/config").json()["agents"]]
    # su correo de avisos recibe la respuesta del cliente en un ticket que atiende
    r = client.patch(f"/settings/users/{u}", json={"notify_email": "buzon.tecnico@ejemplo.com", "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    c = db_session.scalars(select(Customer).order_by(Customer.id)).first()
    hc = _crear_cliente_portal(client, dict(client.headers), c.id, "cliente.senior@ejemplo.com", "cliente_usuario", "Cliente Prueba")
    t = client.post("/cliente/tickets", json={"kind": "falla", "subject": "Cámara", "description": "No graba desde ayer en la noche"}, headers=hc)
    assert t.status_code == 201, t.text
    t = t.json()
    r = client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": u})
    assert r.status_code == 200, r.text
    assert client.post(f"/cliente/tickets/{t['id']}/comments", json={"body": "Sigue igual"}, headers=hc).status_code == 201
    assert any(m.subject.startswith("El cliente respondió") for m in _ids_por_correo(db_session, "buzon.tecnico@ejemplo.com"))
    assert not any(m.subject.startswith("El cliente respondió") for m in _ids_por_correo(db_session, "tec.senior@ejemplo.com"))


def test_rol_creado_desde_contabilidad_exige_2fa(client, auth, db_session):
    from app.core import twofa

    code = _rol(client, "Contador senior", "contabilidad")
    t = db_session.get(Tenant, auth["tenant"]["id"])
    db_session.refresh(t)
    assert twofa.is_required(t, code)
    assert code in client.get("/settings/users").json()["twofa_role_choices"]
    # quitarselo es aflojar: pide la contrasena; volver a exigirlo se puede
    roles = [r for r in twofa.required_roles(t) if r != code]
    assert client.put("/settings", json={"twofa_roles": roles}).status_code == 403
    assert client.put("/settings", json={"twofa_roles": roles, "admin_password": ADMIN_PW}).status_code == 200
    assert client.put("/settings", json={"twofa_roles": [*roles, code]}).status_code == 200
    db_session.refresh(t)
    assert twofa.is_required(t, code)
    # un codigo que no es de esta empresa no se acepta
    assert client.put("/settings", json={"twofa_roles": [*roles, "t999_inventado"]}).status_code == 422


def test_asignar_un_rol_creado_desde_admin_pide_la_contrasena(client, auth, db_session):
    tid = auth["tenant"]["id"]
    code = _rol(client, "Casi admin", "admin")
    assert code in client.get("/settings/users").json()["reauth_roles"]
    h = interno(client, db_session, tid, "casi.admin@ejemplo.com", "lectura")
    u = uid(client, h)
    assert client.patch(f"/settings/users/{u}", json={"role": code}).status_code == 403
    assert client.patch(f"/settings/users/{u}", json={"role": code, "admin_password": ADMIN_PW}).status_code == 200
    assert client.post("/settings/invitations", json={"email": "casi.otro@ejemplo.com", "role": code}).status_code == 403
    # uno copiado de ventas no la pide
    ventas2 = _rol(client, "Ventas dos", "ventas")
    assert ventas2 not in client.get("/settings/users").json()["reauth_roles"]


def test_aprobar_cotizaciones_es_solo_del_admin(client, auth, db_session):
    nuevos = {**{k: list(v) for k, v in ROLE_PERMISSIONS["ventas"].items()}, "sales": [*ROLE_PERMISSIONS["ventas"]["sales"], "aprobar"]}
    r = client.put("/settings/roles/ventas", json={"permissions": nuevos})
    assert r.status_code == 422 and "administrador" in r.json()["detail"]
    assert "sales.aprobar" in LOCKED


def test_eliminar_cuenta_de_prueba_no_revive_por_bootstrap(client, auth, db_session):
    from app.bootstrap import extra_invites

    t = db_session.get(Tenant, auth["tenant"]["id"])
    if t.slug != "crimson":  # extra_invites solo invita en la empresa "crimson"
        t.slug = "crimson"
        db_session.commit()
    h = interno(client, db_session, t.id, "cuenta.prueba@ejemplo.com", "ventas")
    u = uid(client, h)
    assert client.post(f"/settings/users/{u}/delete", json={"admin_password": ADMIN_PW}).status_code == 200
    db_session.expire_all()
    emitidas = extra_invites(db_session, "cuenta.prueba@ejemplo.com:ventas,nueva.prueba@ejemplo.com:lectura", base_url="https://ejemplo.invalid")
    assert [e for e, _r, _l in emitidas] == ["nueva.prueba@ejemplo.com"]
    vivas = db_session.scalars(select(Invitation).where(Invitation.email == "cuenta.prueba@ejemplo.com", Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))).all()
    assert vivas == []


def test_vendedor_no_duplica_un_cliente_existente(client, auth, db_session):
    h = interno(client, db_session, auth["tenant"]["id"], "vende.dup@ejemplo.com", "ventas")
    c = client.get("/customers").json()["items"][0]
    assert c["email"]
    r = client.post("/customers", json={"name": "Copia", "id_type": "fisica", "email": c["email"].upper()}, headers=h)
    assert r.status_code == 409 and r.json()["existing"]["id"] == c["id"]
    if c.get("id_number"):
        r = client.post("/customers", json={"name": "Copia", "id_type": c["id_type"], "id_number": c["id_number"], "email": "mio@ejemplo.com"}, headers=h)
        assert r.status_code == 409 and r.json()["existing"]["id"] == c["id"]
    # uno de verdad nuevo si se crea; y el admin (crm.editar) puede crear una ficha aparte
    assert client.post("/customers", json={"name": "Otro Cliente", "id_type": "fisica", "email": "otro.cliente@ejemplo.com"}, headers=h).status_code == 201
    assert client.post("/customers", json={"name": "Copia admin", "id_type": "fisica", "email": c["email"]}).status_code == 201


def test_correo_de_avisos_solo_lo_ve_el_admin(client, auth, db_session):
    tid = auth["tenant"]["id"]
    h = interno(client, db_session, tid, "vende.oculto@ejemplo.com", "ventas")
    u = uid(client, h)
    assert client.patch(f"/settings/users/{u}", json={"notify_email": "personal@ejemplo.com", "admin_password": ADMIN_PW}).status_code == 200
    assert next(x for x in client.get("/settings/users").json()["users"] if x["id"] == u)["notify_email"] == "personal@ejemplo.com"
    assert client.put("/settings/roles/lectura", json={"permissions": {"dashboard": ["ver"], "settings": ["ver"]}, "admin_password": ADMIN_PW}).status_code == 200
    hl = interno(client, db_session, tid, "lee.ajustes@ejemplo.com", "lectura")
    r = client.get("/settings/users", headers=hl)
    assert r.status_code == 200, r.text
    assert all(x["notify_email"] is None for x in r.json()["users"])


def test_regla_aplicada_hacia_atras_solo_devenga_esa_regla(client, auth, db_session):
    tid = auth["tenant"]["id"]
    h1 = interno(client, db_session, tid, "vende.uno@ejemplo.com", "ventas")
    h2 = interno(client, db_session, tid, "vende.dos@ejemplo.com", "ventas")
    v1, v2 = uid(client, h1), uid(client, h2)
    c = client.get("/customers").json()["items"][0]
    for h in (h1, h2):
        inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100000, "tax_rate": 0}]}, headers=h).json()
        assert client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 100000, "kind": "captura"}).status_code == 201
    # regla de la primera persona sin aplicarla hacia atras: no devenga nada
    assert client.post("/commissions/rules", json={"user_id": v1, "name": "Uno", "percent": 5}).status_code == 201
    # la de la segunda, hacia atras: solo devenga la suya, no la de la primera
    r = client.post("/commissions/rules", json={"user_id": v2, "name": "Dos", "percent": 5, "apply_from": str(HOY.replace(day=1))})
    assert r.status_code == 201 and r.json()["backfill"]["creados"] == 1
    assert len(client.get("/commissions", headers=h1).json()["rows"]) == 0
    assert len(client.get("/commissions", headers=h2).json()["rows"]) == 1


# ---------- Revision 2: copias del admin, roles sensibles y cedulas con guiones ----------
def _quote_q(client, db_session):
    from app.models import Quote

    c = client.get("/customers").json()["items"][0]
    q = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 0}]})
    assert q.status_code == 201, q.text
    return db_session.get(Quote, q.json()["id"])


def test_rol_creado_desde_supervisor_y_contador_reciben_lo_de_su_rol(client, auth, db_session):
    from app.routers.fieldwork import _reviewers
    from app.services.avisos_venta import cotizacion_aceptada
    from app.tasks.fiscal import recipients

    tid = auth["tenant"]["id"]
    sup = _rol(client, "Supervisor senior", "supervisor")
    con = _rol(client, "Contador senior", "contabilidad")
    hs = interno(client, db_session, tid, "sup.senior@ejemplo.com", sup)
    interno(client, db_session, tid, "conta.senior@ejemplo.com", con)
    t = db_session.get(Tenant, tid)
    db_session.refresh(t)
    q = _quote_q(client, db_session)
    assert cotizacion_aceptada(db_session, t, q, version=1, quien="Cliente", medio="portal", actor_id=None) >= 1
    db_session.commit()
    assert _ids_por_correo(db_session, "sup.senior@ejemplo.com")
    assert "conta.senior@ejemplo.com" in recipients(db_session, t)
    assert uid(client, hs) in [x["id"] for x in _reviewers(db_session, t)]


def test_rol_creado_desde_admin_no_recibe_lo_que_es_solo_del_admin(client, auth, db_session):
    from app.core.deps import roles_like
    from app.services.mail import notify_roles

    tid = auth["tenant"]["id"]
    code = _rol(client, "Jefe de bodega", "admin")
    hj = interno(client, db_session, tid, "jefe.bodega@ejemplo.com", "lectura")
    assert client.patch(f"/settings/users/{uid(client, hj)}", json={"role": code, "admin_password": ADMIN_PW}).status_code == 200
    t = db_session.get(Tenant, tid)
    # resumen de margen (margin_summary): precios y margenes, de donde se deduce el costo
    notify_roles(db_session, t, ("admin",), "Resumen de margen de prueba", "<p>x</p>", "margin_summary", 0)
    notify_roles(db_session, t, ("admin", "supervisor"), "Ticket de prueba", "<p>x</p>", "support_ticket", 0)
    db_session.commit()
    assert _ids_por_correo(db_session, "jefe.bodega@ejemplo.com") == []
    assert code not in roles_like(db_session, t, ("admin",))
    # en las listas operativas (agentes de tickets, responsables) si cuenta, por lo que puede hacer
    assert code in roles_like(db_session, t, ("admin",), admin=True)


def test_sumarle_algo_sensible_a_un_rol_pide_la_contrasena(client, auth, db_session):
    tid = auth["tenant"]["id"]
    code = _rol(client, "Inocente", "lectura")
    h = interno(client, db_session, tid, "inocente@ejemplo.com", "lectura")
    u = uid(client, h)
    assert client.patch(f"/settings/users/{u}", json={"role": code}).status_code == 200  # no es sensible: sin contrasena
    base = {k: list(v) for k, v in ROLE_PERMISSIONS["lectura"].items()}
    for extra in ({"payroll": ["ver"]}, {"accounting": ["ver"]}, {"settings": ["ver"]}, {"aliados": ["ver", "usuarios"]}, {"portal_clientes": ["ver", "aprobar"]}):
        assert client.put(f"/settings/roles/{code}", json={"permissions": {**base, **extra}}).status_code == 403, extra
        assert client.put("/settings/roles/lectura", json={"permissions": {**base, **extra}}).status_code == 403, extra
    assert "payroll" not in client.get("/auth/me", headers=h).json()["permissions"]
    # con la contrasena si; quitarlo despues no la pide
    assert client.put(f"/settings/roles/{code}", json={"permissions": {**base, "payroll": ["ver"]}, "admin_password": ADMIN_PW}).status_code == 200
    assert client.get("/auth/me", headers=h).json()["permissions"]["payroll"] == ["ver"]
    assert client.put(f"/settings/roles/{code}", json={"permissions": base}).status_code == 200
    # un predefinido al que se le agrego algo sensible tambien pide la contrasena al asignarlo
    assert client.put("/settings/roles/lectura", json={"permissions": {**base, "payroll": ["ver"]}, "admin_password": ADMIN_PW}).status_code == 200
    assert "lectura" in client.get("/settings/users").json()["reauth_roles"]
    assert client.patch(f"/settings/users/{u}", json={"role": "lectura"}).status_code == 403
    assert client.patch(f"/settings/users/{u}", json={"role": "lectura", "admin_password": ADMIN_PW}).status_code == 200
    # lo que el Contador trae de fabrica no la pide
    assert "contabilidad" not in client.get("/settings/users").json()["reauth_roles"]


def test_reenviar_invitacion_de_un_rol_sensible_pide_la_contrasena(client, auth, db_session):
    code = _rol(client, "Casi admin dos", "admin")
    r = client.post("/settings/invitations", json={"email": "casi.dos@ejemplo.com", "role": code, "admin_password": ADMIN_PW})
    assert r.status_code == 201, r.text
    iid = db_session.scalar(select(Invitation.id).where(Invitation.email == "casi.dos@ejemplo.com"))
    assert client.post(f"/settings/invitations/{iid}/resend").status_code == 403
    assert client.post(f"/settings/invitations/{iid}/resend", json={"admin_password": ADMIN_PW}).status_code == 200


def test_vendedor_no_duplica_un_cliente_por_la_cedula_con_o_sin_guiones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    for guardada, escrita in (("3-101-778812", "3101778812"), ("3102445523", "3-102-445523")):
        c = Customer(tenant_id=tid, name=f"Empresa {guardada}", id_type="juridica", id_number=guardada, email=f"e{guardada[-3:]}@ejemplo.com")
        db_session.add(c)
        db_session.commit()
        h = interno(client, db_session, tid, f"vende.ced{guardada[-3:]}@ejemplo.com", "ventas")
        r = client.post("/customers", json={"name": "Copia", "id_type": "juridica", "id_number": escrita, "email": f"mio{guardada[-3:]}@ejemplo.com"}, headers=h)
        assert r.status_code == 409 and r.json()["existing"]["id"] == c.id, r.text
        # por importacion: la fila se omite, no se crea una segunda ficha con el correo del vendedor
        csv = ("Nombre,Identificación,Correo electrónico" + chr(10) + f"Copia,{escrita},mio{guardada[-3:]}@ejemplo.com" + chr(10)).encode()
        r = client.post("/import/customers?commit=true", files={"file": ("clientes.csv", csv, "text/csv")}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["omitir"] == 1 and r.json()["summary"]["nuevo"] == 0, r.json()
        assert db_session.scalar(select(Customer).where(Customer.email == f"mio{guardada[-3:]}@ejemplo.com")) is None
