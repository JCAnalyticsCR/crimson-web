"""Portal del cliente: solicitud publica -> aprobacion -> invitacion -> cuenta, y aislamiento entre clientes.

Lo que se prueba aqui es la promesa de seguridad: escribir una cedula o un correo no da acceso a nada,
un cliente nunca ve lo de otro (404) ni nada interno (403), y las notas internas no salen al portal."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import AccessRequest, Customer, CustomerAsset, Invoice, MaintenanceContract, Quote, TenantUser, User

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PWD = "Cliente-2026-seguro"


def _solicitud(client, **extra):
    body = {"name": "Laura Mora", "email": "laura@condominio-a.cr", "phone": "8888-1234", "company": "Condominio A", "message": "Somos clientes"}
    return client.post("/public/access-requests", json={**body, **extra})


def _token(link: str) -> str:
    return link.rsplit("/invitacion/", 1)[1]


def _login(client, email: str, password: str = PWD) -> dict:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _crear_cliente_portal(client, admin_h, customer_id: int, email: str, role: str, nombre: str) -> dict:
    r = client.post(f"/customers/{customer_id}/portal/invitations", json={"email": email, "role": role}, headers=admin_h)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["emailed"] is False  # sin Resend: el enlace se manda a mano, nadie dice "le llego un correo"
    r = client.post("/settings/invitations/accept", json={"token": _token(inv["link"]), "full_name": nombre, "password": PWD})
    assert r.status_code == 200, r.text
    return _login(client, email)


def _dos_clientes(db):
    a, b = db.scalars(select(Customer).order_by(Customer.id).limit(2)).all()
    return a, b


def test_solicitud_publica_aprobacion_invitacion_y_cuenta(client, auth, db_session):
    admin_h = dict(client.headers)
    client.headers.pop("Authorization")
    a, _ = _dos_clientes(db_session)
    a.email = "admin@condominio-a.cr"
    db_session.commit()

    # 1) publico: solo una solicitud. Misma respuesta siempre; nada de acceso
    r1 = _solicitud(client, id_number=a.id_number or "3-101-123456")
    assert r1.status_code == 202, r1.text
    r2 = _solicitud(client)  # repetida: no duplica, responde identico
    r3 = _solicitud(client, email="nadie@desconocido.cr", company="Empresa que no existe")
    assert r1.json() == r2.json() == r3.json()
    pend = db_session.scalars(select(AccessRequest).where(AccessRequest.status == "pendiente")).all()
    assert len(pend) == 2
    assert db_session.scalar(select(User).where(User.email == "laura@condominio-a.cr")) is None
    # campos extra prohibidos y validacion sin eco del valor
    r = client.post("/public/access-requests", json={"name": "X", "email": "malo", "company": "C", "role": "cliente_admin"})
    assert r.status_code == 422 and "malo" not in r.text

    # 2) interno: la bandeja sugiere el cliente (dominio del correo), pero no liga sola
    client.headers.update(admin_h)
    lista = client.get("/access-requests").json()
    sol = next(x for x in lista if x["email"] == "laura@condominio-a.cr")
    assert sol["customer_id"] is None and any(s["id"] == a.id for s in sol["suggestions"])
    # elegir cliente existente o nuevo, no ambos ni ninguno
    assert client.post(f"/access-requests/{sol['id']}/approve", json={"role": "cliente_admin"}).status_code == 422
    r = client.post(f"/access-requests/{sol['id']}/approve", json={"customer_id": a.id, "role": "cliente_admin"})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["emailed"] is False and "/invitacion/" in inv["link"]
    assert client.post(f"/access-requests/{sol['id']}/approve", json={"customer_id": a.id}).status_code == 409  # ya revisada

    # rechazar la otra con motivo
    otra = next(x for x in lista if x["email"] == "nadie@desconocido.cr")
    assert client.post(f"/access-requests/{otra['id']}/reject", json={"reason": "No encontramos un contrato a ese nombre"}).status_code == 200
    assert db_session.get(AccessRequest, otra["id"]).status == "rechazada"

    # 3) el cliente acepta la invitacion y queda ligado a ESE cliente con ese rol
    client.headers.pop("Authorization")
    r = client.post("/settings/invitations/accept", json={"token": _token(inv["link"]), "full_name": "Laura Mora", "password": PWD})
    assert r.status_code == 200, r.text
    assert client.post("/settings/invitations/accept", json={"token": _token(inv["link"]), "full_name": "Otra", "password": PWD}).status_code == 404
    u = db_session.scalar(select(User).where(User.email == "laura@condominio-a.cr"))
    m = db_session.scalar(select(TenantUser).where(TenantUser.user_id == u.id))
    assert m.customer_id == a.id and m.role_code == "cliente_admin"
    h = _login(client, "laura@condominio-a.cr")
    me = client.get("/auth/me", headers=h).json()
    assert me["role"] == "cliente_admin" and list(me["permissions"]) == ["portal"]
    ini = client.get("/cliente/inicio", headers=h).json()
    assert ini["customer"]["name"] == a.name

    # 4) los usuarios de cliente no aparecen en Ajustes -> Usuarios ni se pueden promover desde ahi
    client.headers.update(admin_h)
    assert all(x["email"] != u.email for x in client.get("/settings/users").json()["users"])
    assert "cliente_admin" not in client.get("/settings/users").json()["roles"]
    assert client.patch(f"/settings/users/{u.id}", json={"role": "admin"}).status_code == 404
    assert client.post("/settings/invitations", json={"email": "x@y.cr", "role": "cliente_admin"}).status_code == 422


def test_honeypot_y_limite_por_ip(client, db_session):
    r = _solicitud(client, website="http://spam.example")
    assert r.status_code == 202
    assert db_session.scalars(select(AccessRequest)).all() == []
    for i in range(4):
        assert _solicitud(client, email=f"p{i}@x.cr").status_code == 202
    assert _solicitud(client, email="otra@x.cr").status_code == 429
    grande = client.post("/public/access-requests", content=b"{" + b" " * 7000 + b"}", headers={"Content-Type": "application/json"})
    assert grande.status_code in (413, 429)


def test_aislamiento_entre_clientes_y_endpoints_internos(client, auth, db_session):
    admin_h = dict(client.headers)
    a, b = _dos_clientes(db_session)
    tid = auth["tenant"]["id"]
    hoy = date.today()
    # datos de B: ticket con nota interna, activo, cotizacion y factura enviadas, contrato
    tb = client.post("/tickets", json={"subject": "NVR de B", "customer_id": b.id, "priority": "alta"}, headers=admin_h).json()
    asset_b = CustomerAsset(tenant_id=tid, customer_id=b.id, name="Cámara B", serial="SB-1")
    q_b = Quote(tenant_id=tid, number="COT-B1", customer_id=b.id, issue_date=hoy, status="enviada", total=Decimal("1000"))
    f_b = Invoice(tenant_id=tid, number="FE-B1", customer_id=b.id, issue_date=hoy, status="enviada", total=Decimal("1000"), balance=Decimal("1000"))
    # datos de A: un borrador que el cliente no debe ver, una factura visible, un activo y un contrato
    q_a_borrador = Quote(tenant_id=tid, number="COT-A0", customer_id=a.id, issue_date=hoy, status="creado", total=Decimal("5"))
    f_a = Invoice(tenant_id=tid, number="FE-A1", customer_id=a.id, issue_date=hoy, status="enviada", total=Decimal("50"), balance=Decimal("50"))
    asset_a = CustomerAsset(tenant_id=tid, customer_id=a.id, name="Cámara A", serial="SA-1", ip="10.0.0.5", purchase_ref="FAC-PROV-9")
    ctr_a = MaintenanceContract(tenant_id=tid, number="MNT-A", customer_id=a.id, name="Preventivo A", next_date=hoy + timedelta(days=20), amount=Decimal("99"))
    db_session.add_all([asset_b, q_b, f_b, q_a_borrador, f_a, asset_a, ctr_a])
    db_session.commit()

    ha = _crear_cliente_portal(client, admin_h, a.id, "jefe@a.cr", "cliente_admin", "Jefe A")
    hu = _crear_cliente_portal(client, admin_h, a.id, "usuario@a.cr", "cliente_usuario", "Usuario A")

    # --- B es invisible para A: 404 en todo, sin confirmar que exista ---
    assert client.get(f"/cliente/tickets/{tb['id']}", headers=ha).status_code == 404
    assert client.post(f"/cliente/tickets/{tb['id']}/comments", json={"body": "hola"}, headers=ha).status_code == 404
    assert client.get(f"/cliente/documentos/quotes/{q_b.id}/pdf", headers=ha).status_code == 404
    assert client.get(f"/cliente/documentos/invoices/{f_b.id}/pdf", headers=ha).status_code == 404
    assert client.get(f"/cliente/documentos/quotes/{q_a_borrador.id}/pdf", headers=ha).status_code == 404  # borrador propio
    assert client.post("/cliente/tickets", json={"kind": "falla", "subject": "x de B", "description": "equipo de otro cliente", "asset_id": asset_b.id}, headers=ha).status_code == 404
    assert all(t["id"] != tb["id"] for t in client.get("/cliente/tickets", headers=ha).json())
    eq = client.get("/cliente/equipos", headers=ha).json()
    assert [e["serial"] for e in eq] == ["SA-1"] and "ip" not in eq[0] and "purchase_ref" not in eq[0]
    docs = client.get("/cliente/documentos", headers=ha).json()
    assert [d["number"] for d in docs["invoices"]] == ["FE-A1"] and docs["quotes"] == []
    assert client.get(f"/cliente/documentos/invoices/{f_a.id}/pdf", headers=ha).status_code == 200
    mant = client.get("/cliente/mantenimientos", headers=ha).json()
    assert mant[0]["name"] == "Preventivo A" and "amount" not in mant[0]
    assert client.get("/cliente/inicio", headers=ha).json()["next_visit"]["name"] == "Preventivo A"
    # el navegador no puede elegir cliente: un customer_id en el cuerpo se rechaza
    assert client.post("/cliente/tickets", json={"kind": "falla", "subject": "x", "description": "intento de cambiar cliente", "customer_id": b.id}, headers=ha).status_code == 422

    # --- nada interno: 403 en endpoints internos, tambien en los que solo pedian sesion ---
    for path in ("/tickets", f"/tickets/{tb['id']}", "/customers", "/quotes", "/invoices", "/settings/users", "/access-requests", "/media", "/fx", "/tenant", "/auth/roles", f"/customers/{a.id}/portal"):
        for h in (ha, hu):
            r = client.get(path, headers=h)
            assert r.status_code == 403, (path, r.status_code)
    assert client.post("/media", files={"file": ("a.png", PNG, "image/png")}, headers=ha).status_code == 403
    assert client.post(f"/customers/{a.id}/portal/invitations", json={"email": "z@a.cr", "role": "cliente_admin"}, headers=ha).status_code == 403

    # --- cliente_usuario: sin dinero ni usuarios ---
    assert client.get("/cliente/documentos", headers=hu).status_code == 403
    assert client.get(f"/cliente/documentos/invoices/{f_a.id}/pdf", headers=hu).status_code == 403
    assert client.get("/cliente/usuarios", headers=hu).status_code == 403
    assert client.get("/cliente/equipos", headers=hu).status_code == 200


def test_ticket_del_portal_notas_internas_y_comentarios(client, auth, db_session):
    admin_h = dict(client.headers)
    a, _ = _dos_clientes(db_session)
    ha = _crear_cliente_portal(client, admin_h, a.id, "jefe@a.cr", "cliente_admin", "Jefe A")
    hu = _crear_cliente_portal(client, admin_h, a.id, "usuario@a.cr", "cliente_usuario", "Usuario A")

    foto = client.post("/cliente/media", files={"file": ("f.png", PNG, "image/png")}, headers=hu)
    assert foto.status_code == 201, foto.text
    pdf = client.post("/cliente/media", files={"file": ("x.pdf", b"%PDF-1.4 xx", "application/pdf")}, headers=hu)
    assert pdf.status_code == 415
    # foto ajena (URL externa) no se acepta
    r = client.post("/cliente/tickets", json={"kind": "falla", "subject": "Cámara", "description": "No graba desde ayer", "photos": ["https://evil.example/x.png"]}, headers=hu)
    assert r.status_code == 422
    r = client.post(
        "/cliente/tickets",
        json={"kind": "falla", "priority": "alta", "subject": "Cámara del portón", "description": "No graba desde ayer en la noche", "photos": [foto.json()["url"]]},
        headers=hu,
    )
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["number"].startswith("TCK") and t["can_comment"] and t["photos"]

    # el equipo lo ve como ticket normal, de canal portal, ligado al cliente y con SLA de la cascada
    interno = client.get(f"/tickets/{t['id']}", headers=admin_h).json()
    assert interno["channel"] == "portal" and interno["customer_id"] == a.id and interno["sla"]["origen"] == "defecto"
    client.post(f"/tickets/{t['id']}/notes", json={"body": "OJO: cliente moroso, cobrar visita", "internal": True}, headers=admin_h)
    client.post(f"/tickets/{t['id']}/notes", json={"body": "Vamos mañana a las 9", "status": "esperando_cliente"}, headers=admin_h)

    det = client.get(f"/cliente/tickets/{t['id']}", headers=hu).json()
    textos = [m["body"] for m in det["messages"]]
    assert "Vamos mañana a las 9" in textos and not any("moroso" in x for x in textos)
    assert "moroso" not in client.get(f"/cliente/tickets/{t['id']}", headers=ha).text
    assert next(m for m in det["messages"] if m["body"].startswith("Vamos"))["team"] is True
    for campo in ("hours", "amount", "billable", "assigned", "sla", "contact"):
        assert campo not in det

    # quien lo abrio contesta: vuelve a la cola
    r = client.post(f"/cliente/tickets/{t['id']}/comments", json={"body": "Perfecto, los esperamos"}, headers=hu)
    assert r.status_code == 201 and r.json()["status"] == "en_proceso"

    # ticket de la empresa abierto por OTRO usuario: el usuario comun lo ve pero no escribe; el admin del cliente si
    t2 = client.post("/cliente/tickets", json={"kind": "otro", "subject": "Licencia", "description": "Consulta por la licencia"}, headers=ha).json()
    vista = client.get(f"/cliente/tickets/{t2['id']}", headers=hu).json()
    assert vista["can_comment"] is False and vista["mine"] is False
    assert client.post(f"/cliente/tickets/{t2['id']}/comments", json={"body": "yo tambien"}, headers=hu).status_code == 403
    assert client.post(f"/cliente/tickets/{t['id']}/comments", json={"body": "Soy el encargado"}, headers=ha).status_code == 201


def test_admin_del_cliente_maneja_solo_usuarios_de_su_empresa(client, auth, db_session):
    admin_h = dict(client.headers)
    a, b = _dos_clientes(db_session)
    ha = _crear_cliente_portal(client, admin_h, a.id, "jefe@a.cr", "cliente_admin", "Jefe A")
    hb = _crear_cliente_portal(client, admin_h, b.id, "jefe@b.cr", "cliente_admin", "Jefe B")

    r = client.post("/cliente/usuarios", json={"email": "nuevo@a.cr"}, headers=ha)
    assert r.status_code == 201 and r.json()["emailed"] is False
    # intentar mandar rol o cliente desde el navegador: prohibido
    assert client.post("/cliente/usuarios", json={"email": "otro@a.cr", "role": "cliente_admin"}, headers=ha).status_code == 422
    # no puede "robarse" la cuenta de alguien de Crimson ni de otro cliente
    assert client.post("/cliente/usuarios", json={"email": "admin@crimsonapp.com"}, headers=ha).status_code == 409
    assert client.post("/cliente/usuarios", json={"email": "jefe@b.cr"}, headers=ha).status_code == 409
    r = client.post("/settings/invitations/accept", json={"token": _token(r.json()["link"]), "full_name": "Nuevo A", "password": PWD})
    nuevo = db_session.scalar(select(User).where(User.email == "nuevo@a.cr"))
    m = db_session.scalar(select(TenantUser).where(TenantUser.user_id == nuevo.id))
    assert m.role_code == "cliente_usuario" and m.customer_id == a.id

    jefe_b = db_session.scalar(select(User).where(User.email == "jefe@b.cr"))
    jefe_a = db_session.scalar(select(User).where(User.email == "jefe@a.cr"))
    assert client.patch(f"/cliente/usuarios/{jefe_b.id}", json={"active": False}, headers=ha).status_code == 404
    assert client.patch(f"/cliente/usuarios/{jefe_a.id}", json={"active": False}, headers=ha).status_code == 403
    assert client.patch(f"/cliente/usuarios/{nuevo.id}", json={"active": False}, headers=ha).status_code == 200
    # desactivado: su sesion deja de servir y no puede entrar
    assert client.post("/auth/login", json={"email": "nuevo@a.cr", "password": PWD}).status_code == 403
    emails = [u["email"] for u in client.get("/cliente/usuarios", headers=hb).json()["users"]]
    assert emails == ["jefe@b.cr"]
