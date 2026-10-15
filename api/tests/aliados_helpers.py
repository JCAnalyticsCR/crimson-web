"""Ayudas de las pruebas del portal de aliados (repo publico: solo nombres ficticios y correos de prueba).

escenario(): dos empresas aliadas (A y B) con un usuario de cada rol y datos en comun y ajenos, para probar el
aislamiento (aliado_final §12). Lo que no se prueba aqui (proyectos, OT, tickets...) se arma directo con db_session;
empresas, usuarios y participaciones pasan por la API (lo que se esta probando)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

PW = "Aliado-prueba-2026"
ADMIN_PW = "Crimson-2026-seguro"


def login(client, email: str, password: str = PW) -> dict:
    keep = client.headers.pop("Authorization", None)
    try:
        r = client.post("/auth/login", json={"email": email, "password": password})
    finally:
        if keep:
            client.headers["Authorization"] = keep
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def aliado(client, admin_h: dict, nombre: str, cedula: str | None = None, **extra) -> int:
    r = client.post("/allies", json={"name": nombre, "tax_id": cedula, **extra}, headers=admin_h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def token_de(link: str) -> str:
    return link.rsplit("/", 1)[-1]


def aceptar(client, link: str, nombre: str = "Persona Aliada") -> None:
    keep = client.headers.pop("Authorization", None)
    try:
        r = client.post("/settings/invitations/accept", json={"token": token_de(link), "full_name": nombre, "password": PW})
    finally:
        if keep:
            client.headers["Authorization"] = keep
    assert r.status_code == 200, r.text


def usuario(client, admin_h: dict, ally_id: int, email: str, role: str, nombre: str | None = None) -> dict[str, str]:
    """Invitacion desde Administracion -> Aliados, aceptacion y login. Devuelve los headers de la sesion."""
    r = client.post(f"/allies/{ally_id}/invitations", json={"email": email, "role": role}, headers=admin_h)
    assert r.status_code == 201, r.text
    aceptar(client, r.json()["link"], nombre or email.split("@")[0].replace(".", " ").title())
    return login(client, email)


def uid(client, h: dict) -> int:
    return client.get("/auth/me", headers=h).json()["user"]["id"]


def interno(client, db_session, tid: int, email: str, role: str) -> dict[str, str]:
    """Usuario interno con ese rol (directo en la base) y su sesion."""
    from app.core.security import hash_password
    from app.models import TenantUser, User

    u = User(email=email, full_name=role.capitalize() + " Prueba", password_hash=hash_password(PW))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code=role))
    db_session.commit()
    return login(client, email)


def keys_rec(obj, acc: set | None = None) -> set:
    """Todas las llaves de un JSON, a cualquier profundidad."""
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            keys_rec(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            keys_rec(v, acc)
    return acc


def ids(rows) -> set:
    return {r["id"] for r in rows}


def _opp(client, admin_h, titulo: str, customer_id: int) -> dict:
    r = client.post("/opportunities", json={"title": titulo, "customer_id": customer_id, "amount": 1500000, "probability": 60}, headers=admin_h)
    assert r.status_code == 201, r.text
    return r.json()


def participa(client, admin_h, oid: int, ally_id: int, role: str = "subcontratista", **extra) -> int:
    r = client.post(f"/opportunities/{oid}/allies", json={"ally_company_id": ally_id, "role": role, **extra}, headers=admin_h)
    assert r.status_code == 201, r.text
    return next(a["id"] for a in r.json() if a.get("ally_company_id") == ally_id and a["role"] == role)


def proyecto(db_session, tid: int, opp_id: int, nombre: str, customer_id: int):
    """Proyecto ganado desde la oportunidad (lo que hace /quotes/{id}/project): las participaciones quedan ligadas."""
    from app.models import AllyParticipation, Opportunity, Project

    o = db_session.get(Opportunity, opp_id)
    pr = Project(
        tenant_id=tid,
        number=f"PRO-T{opp_id:04d}",
        name=nombre,
        customer_id=customer_id,
        opportunity_id=o.id,
        status="en_curso",
        price=2000000,
        notes="Nota interna de Crimson",
    )
    db_session.add(pr)
    db_session.flush()
    o.project_id, o.status = pr.id, "ganada"
    for a in db_session.scalars(select(AllyParticipation).where(AllyParticipation.opportunity_id == o.id)):
        a.project_id = pr.id
    db_session.commit()
    return pr


def escenario(client, admin_h: dict, db_session) -> SimpleNamespace:
    from app.models import (
        AllyCompany,
        CustomerAsset,
        MaintenanceContract,
        Opportunity,
        Supplier,
        SupportNote,
        SupportTicket,
        Survey,
        TenantUser,
        WorkOrder,
    )

    tid = client.get("/auth/me", headers=admin_h).json()["tenant"]["id"]
    admin_id = client.get("/auth/me", headers=admin_h).json()["user"]["id"]
    s = SimpleNamespace(tid=tid, admin_h=admin_h, admin_id=admin_id)
    s.A = aliado(client, admin_h, "Aliado Uno S.A.", "3-101-000001")
    s.B = aliado(client, admin_h, "Aliado Dos S.A.", "3-101-000002")
    s.a_jefe = usuario(client, admin_h, s.A, "jefe@aliado-uno.ejemplo.com", "aliado", "Ana Jefa Uno")
    s.a_tec = usuario(client, admin_h, s.A, "tec@aliado-uno.ejemplo.com", "tecnico_aliado", "Tomas Tecnico Uno")
    s.a_tec2 = usuario(client, admin_h, s.A, "tec2@aliado-uno.ejemplo.com", "tecnico_aliado", "Teo Tecnico Uno")
    s.b_jefe = usuario(client, admin_h, s.B, "jefe@aliado-dos.ejemplo.com", "aliado", "Bea Jefa Dos")
    s.b_tec = usuario(client, admin_h, s.B, "tec@aliado-dos.ejemplo.com", "tecnico_aliado", "Beto Tecnico Dos")
    m = {mm.user.email: mm.user_id for mm in db_session.scalars(select(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.ally_company_id.is_not(None)))}
    s.uid_a_jefe, s.uid_a_tec, s.uid_a_tec2 = m["jefe@aliado-uno.ejemplo.com"], m["tec@aliado-uno.ejemplo.com"], m["tec2@aliado-uno.ejemplo.com"]
    s.uid_b_jefe, s.uid_b_tec = m["jefe@aliado-dos.ejemplo.com"], m["tec@aliado-dos.ejemplo.com"]

    c1 = client.post("/customers", json={"name": "Cliente Demo Uno", "id_type": "juridica", "id_number": "3-101-111111"}, headers=admin_h).json()
    c2 = client.post("/customers", json={"name": "Cliente Demo Dos", "id_type": "juridica", "id_number": "3-101-222222"}, headers=admin_h).json()
    s.c1, s.c2 = c1["id"], c2["id"]
    s.O1 = _opp(client, admin_h, "Cámaras bodega norte", s.c1)["id"]
    s.O2 = _opp(client, admin_h, "Red oficina sur", s.c2)["id"]
    s.O3 = _opp(client, admin_h, "Control de acceso planta", s.c1)["id"]
    s.O4 = _opp(client, admin_h, "Lead sin aliados", s.c1)["id"]
    s.O5 = _opp(client, admin_h, "Oculta al aliado", s.c1)["id"]
    s.O6 = _opp(client, admin_h, "Participacion vieja por proveedor", s.c1)["id"]
    s.part_O1 = participa(client, admin_h, s.O1, s.A, scope="Instalación de cámaras")
    s.part_O2 = participa(client, admin_h, s.O2, s.B)
    s.part_O3_A = participa(client, admin_h, s.O3, s.A)
    s.part_O3_A2 = participa(client, admin_h, s.O3, s.A, role="suministro")
    s.part_O3_B = participa(client, admin_h, s.O3, s.B)
    s.part_O5 = participa(client, admin_h, s.O5, s.A, visible_to_ally=False)
    # O4: sin responsable (caza la fuga por owner_id NULL)
    db_session.get(Opportunity, s.O4).owner_id = None
    # O6: participacion vieja por un proveedor que la empresa A tiene ligado (no da acceso hasta convertirla)
    sup = Supplier(tenant_id=tid, name="Aliado Uno Proveedor", tax_id="3-101-000001")
    db_session.add(sup)
    db_session.flush()
    db_session.get(AllyCompany, s.A).supplier_id = sup.id
    db_session.commit()
    s.sup_A = sup.id
    r = client.post(f"/opportunities/{s.O6}/allies", json={"supplier_id": sup.id, "role": "subcontratista"}, headers=admin_h)
    assert r.status_code == 201, r.text
    s.part_O6 = r.json()[0]["id"]

    s.P1 = proyecto(db_session, tid, s.O1, "Proyecto bodega norte", s.c1).id
    s.P2 = proyecto(db_session, tid, s.O2, "Proyecto oficina sur", s.c2).id
    s.P3 = proyecto(db_session, tid, s.O3, "Proyecto planta", s.c1).id

    from app.models import Project

    p_cr = Project(tenant_id=tid, number="PRO-CR01", name="Proyecto solo Crimson", customer_id=s.c1, status="en_curso")
    db_session.add(p_cr)
    db_session.flush()
    s.P_cr = p_cr.id

    ahora = datetime.now(UTC)
    sv = [
        Survey(
            tenant_id=tid,
            number="LEV-A1",
            kind="cctv",
            opportunity_id=s.O1,
            customer_id=s.c1,
            technician_id=s.uid_a_tec,
            ally_company_id=s.A,
            status="borrador",
        ),
        Survey(
            tenant_id=tid,
            number="LEV-B1",
            kind="cctv",
            opportunity_id=s.O2,
            customer_id=s.c2,
            technician_id=s.uid_b_tec,
            ally_company_id=s.B,
            status="borrador",
        ),
        Survey(tenant_id=tid, number="LEV-CR1", kind="cctv", opportunity_id=s.O1, customer_id=s.c1, technician_id=admin_id, status="borrador"),
        Survey(tenant_id=tid, number="LEV-NULL", kind="cctv", status="borrador"),
    ]
    db_session.add_all(sv)
    db_session.flush()
    s.S_A, s.S_B, s.S_cr, s.S_null = (x.id for x in sv)

    def ot(n, **kw):
        return WorkOrder(tenant_id=tid, number=n, title=f"Trabajo {n}", kind="instalacion", **kw)

    ws = [
        ot("OT-HOY", project_id=s.P1, customer_id=s.c1, ally_company_id=s.A, technician_id=s.uid_a_tec, scheduled_at=ahora),
        ot("OT-MANANA", project_id=s.P1, customer_id=s.c1, ally_company_id=s.A, technician_id=s.uid_a_tec, scheduled_at=ahora + timedelta(days=1)),
        ot("OT-OTRO", project_id=s.P1, customer_id=s.c1, ally_company_id=s.A, technician_id=s.uid_a_tec2, scheduled_at=ahora),
        ot(
            "OT-AYER",
            project_id=s.P1,
            customer_id=s.c1,
            ally_company_id=s.A,
            technician_id=s.uid_a_tec,
            scheduled_at=ahora - timedelta(days=1),
            status="finalizada",
            finished_at=ahora - timedelta(days=1),
        ),
        ot("OT-B", project_id=s.P3, customer_id=s.c1, ally_company_id=s.B, technician_id=s.uid_b_tec, scheduled_at=ahora),
        ot("OT-NULL", customer_id=s.c1),
    ]
    db_session.add_all(ws)
    db_session.flush()
    s.W_hoy, s.W_manana, s.W_otro, s.W_ayer_fin, s.W_B, s.W_null = (x.id for x in ws)

    def tk(n, **kw):
        return SupportTicket(
            tenant_id=tid, number=n, subject=f"Falla {n}", kind="soporte", priority="media", customer_id=s.c1, billable=True, amount=45000, **kw
        )

    ts = [
        tk("TK-A", project_id=s.P1, ally_company_id=s.A, assigned_to=s.uid_a_tec, status="asignado"),
        tk("TK-A-LIBRE", project_id=s.P1, ally_company_id=s.A, status="nuevo"),
        tk("TK-B", project_id=s.P2, ally_company_id=s.B, status="nuevo"),
        tk("TK-NULL", status="nuevo", opened_by=admin_id),
    ]
    db_session.add_all(ts)
    db_session.flush()
    s.T_A, s.T_A_libre, s.T_B, s.T_null = (x.id for x in ts)
    ta = ts[0]
    ta.notes.append(SupportNote(tenant_id=tid, user_id=admin_id, body="Nota interna solo Crimson", internal=True))
    ta.notes.append(SupportNote(tenant_id=tid, user_id=admin_id, body="Nota interna compartida con el aliado", internal=True, share_ally=True))
    ta.notes.append(SupportNote(tenant_id=tid, user_id=admin_id, body="Respuesta publica al cliente", internal=False))
    ta.notes.append(SupportNote(tenant_id=tid, user_id=s.uid_a_jefe, body="Nota del aliado", internal=True))

    ks = [
        MaintenanceContract(
            tenant_id=tid, number="CTR-A", customer_id=s.c2, name="Mantenimiento asignado a A", ally_company_id=s.A, amount=50000, notes="interno"
        ),
        MaintenanceContract(tenant_id=tid, number="CTR-P1", customer_id=s.c1, project_id=s.P1, name="Mantenimiento del proyecto P1", amount=80000),
        MaintenanceContract(tenant_id=tid, number="CTR-OTRO", customer_id=s.c2, name="Mantenimiento ajeno", amount=30000),
    ]
    db_session.add_all(ks)
    db_session.flush()
    s.K_A, s.K_P1, s.K_otro = (x.id for x in ks)
    acts = [
        CustomerAsset(
            tenant_id=tid,
            customer_id=s.c1,
            project_id=s.P1,
            name="Cámara entrada",
            serial="SER-P1",
            ip="10.0.0.5",
            mac="AA:BB",
            firmware="v1",
            purchase_ref="FC-1",
        ),
        CustomerAsset(tenant_id=tid, customer_id=s.c1, project_id=s.P_cr, name="Cámara de otro proyecto", serial="SER-CR"),
        CustomerAsset(tenant_id=tid, customer_id=s.c2, name="Equipo de otro cliente", serial="SER-C2"),
    ]
    db_session.add_all(acts)
    db_session.commit()
    s.asset_p1, s.asset_otro_proy, s.asset_otro_cliente = (x.id for x in acts)

    iva = next(t for t in client.get("/taxes", headers=admin_h).json() if float(t["rate"]) == 13)
    r = client.post(
        "/products",
        json={
            "name": "Cámara demo aliado",
            "code": "ALI-01",
            "price": 115000,
            "currency": "CRC",
            "cost": 100000,
            "cost_currency": "CRC",
            "tax_ids": [iva["id"]],
        },
        headers=admin_h,
    )
    assert r.status_code == 201, r.text
    s.prod = r.json()["id"]
    s.iva = iva["id"]
    return s
