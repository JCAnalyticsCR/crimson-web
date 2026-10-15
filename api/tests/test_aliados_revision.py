"""Portal de aliados: lo que encontro la revision independiente (personal del levantamiento, jornada y cuadrillas
mixtas en las OT, materiales por id, facturas emitidas, invitaciones, notas compartidas por empresa, recargo de 0 %,
migracion reversible). Repo publico: solo nombres ficticios y correos de prueba."""

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from tests.aliados_helpers import PW, escenario, interno, participa, uid, usuario

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------- levantamiento: guardar desde el portal no pisa el personal ni la visita ----------
def test_guardar_conserva_personal_y_visita(client, auth, db_session):
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    sv = db_session.get(Survey, s.S_A)
    sv.labor = {
        "tecnico": {"people": 3, "days": "2"},
        "civil": {"people": 2, "days": "1"},
        "contratado": {"people": 0, "days": "0"},
        "tarifas": {"tecnico": "40000"},
    }
    sv.techs, sv.days = 3, Decimal(2)
    sv.visit_tech_ids = [s.admin_id, s.uid_a_tec]
    db_session.commit()

    def fresco():
        db_session.expire_all()
        return db_session.get(Survey, s.S_A)

    # el formulario del aliado no manda personal ni visita: se conservan
    r = client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "site": "Bodega 3"}, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert r.json()["visit_tech_ids"] == [s.uid_a_tec]  # el de Crimson no sale al aliado
    sv = fresco()
    assert sv.site == "Bodega 3" and sv.techs == 3 and Decimal(str(sv.days)) == 2
    assert sv.labor["tecnico"] == {"people": 3, "days": "2"} and sv.labor["civil"]["people"] == 2 and sv.labor["tarifas"]["tecnico"] == "40000"
    assert sv.visit_tech_ids == [s.admin_id, s.uid_a_tec]
    # cambia los suyos: el de Crimson queda y uno de otra empresa no entra
    r = client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "visit_tech_ids": [s.uid_a_tec2, s.uid_b_tec]}, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert fresco().visit_tech_ids == [s.admin_id, s.uid_a_tec2]
    # y si corrige el personal, manda lo que mando (las tarifas de Crimson siguen)
    r = client.put(f"/aliado/levantamientos/{s.S_A}", json={"kind": "cctv", "labor": {"tecnico": {"people": 4, "days": 3}}}, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    sv = fresco()
    assert sv.labor["tecnico"] == {"people": 4, "days": "3"} and sv.labor["civil"]["people"] == 0 and sv.labor["tarifas"]["tecnico"] == "40000"
    assert "tarifas" not in r.text


# ---------- OT: terminar jornada, tiempo olvidado y cuadrillas mixtas ----------
def test_ot_terminar_jornada_y_tiempo_olvidado(client, auth, db_session):
    from app.models import WorkOrder

    s = escenario(client, dict(client.headers), db_session)
    o = db_session.get(WorkOrder, s.W_hoy)
    o.helpers = [s.admin_id]  # cuadrilla mixta: un ayudante de Crimson
    db_session.commit()
    r = client.post(f"/aliado/ordenes/{s.W_hoy}/start", json={}, headers=s.a_tec)
    assert r.status_code == 200, r.text
    assert {x["user_id"] for x in r.json()["running"]} == {s.uid_a_tec}  # el de Crimson lleva su propio tiempo
    r = client.post(f"/aliado/ordenes/{s.W_hoy}/pause", json={}, headers=s.a_tec)
    assert r.status_code == 200 and r.json()["running"] == [], r.text
    assert client.post(f"/aliado/ordenes/{s.W_hoy}/pause", json={}, headers=s.a_tec).status_code == 409
    assert client.post(f"/aliado/ordenes/{s.W_hoy}/start", json={}, headers=s.a_tec).status_code == 200
    # se olvidaron de terminar la jornada: 20 h corriendo
    db_session.expire_all()
    o = db_session.get(WorkOrder, s.W_hoy)
    abierta = next(e for e in o.time_entries if e.ended_at is None)
    inicio = datetime.now(UTC) - timedelta(hours=20)
    abierta.started_at = inicio
    db_session.commit()
    assert client.get(f"/aliado/ordenes/{s.W_hoy}", headers=s.a_tec).json()["stale"] is True
    r = client.post(f"/aliado/ordenes/{s.W_hoy}/finish", json={}, headers=s.a_tec)
    assert r.status_code == 409 and "tiempo corriendo" in r.json()["detail"]
    assert (
        client.post(f"/aliado/ordenes/{s.W_hoy}/progress", json={"fin_jornada": (inicio + timedelta(hours=8)).isoformat()}, headers=s.a_tec).status_code == 422
    )
    r = client.post(f"/aliado/ordenes/{s.W_hoy}/finish", json={"fin_jornada": (inicio + timedelta(hours=17)).isoformat()}, headers=s.a_tec)
    assert r.status_code == 422
    r = client.post(
        f"/aliado/ordenes/{s.W_hoy}/finish", json={"fin_jornada": (inicio + timedelta(hours=8)).isoformat(), "customer_signature": "Recibe"}, headers=s.a_tec
    )
    assert r.status_code == 200 and r.json()["status"] == "finalizada", r.text
    db_session.expire_all()
    e = next(x for x in db_session.get(WorkOrder, s.W_hoy).time_entries if x.id == abierta.id)
    assert e.minutes == 480


def test_cuadrilla_mixta_y_reasignacion(client, auth, db_session):
    from app.models import SupportTicket, WorkOrder

    s = escenario(client, dict(client.headers), db_session)
    db_session.get(WorkOrder, s.W_hoy).helpers = [s.admin_id]
    db_session.commit()
    r = client.patch(f"/aliado/ordenes/{s.W_hoy}/asignar", json={"technician_id": s.uid_a_tec2, "helpers": [s.uid_a_tec]}, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert set(r.json()["helpers"]) == {s.admin_id, s.uid_a_tec}  # el ayudante de Crimson se conserva
    r = client.patch(f"/aliado/ordenes/{s.W_hoy}/asignar", json={"technician_id": s.uid_a_tec2, "helpers": []}, headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["helpers"] == [s.admin_id], r.text
    assert client.patch(f"/aliado/ordenes/{s.W_hoy}/asignar", json={"technician_id": s.uid_b_tec}, headers=s.a_jefe).status_code == 422
    # tecnico a cargo de Crimson: el aliado no se lo quita, pero puede sumar a su gente
    w = db_session.get(WorkOrder, s.W_otro)
    w.technician_id, w.helpers = s.admin_id, [s.uid_a_tec2]
    db_session.commit()
    assert client.patch(f"/aliado/ordenes/{s.W_otro}/asignar", json={"technician_id": s.uid_a_tec}, headers=s.a_jefe).status_code == 409
    r = client.patch(f"/aliado/ordenes/{s.W_otro}/asignar", json={"technician_id": s.admin_id, "helpers": [s.uid_a_tec]}, headers=s.a_jefe)
    assert r.status_code == 200 and r.json()["technician_id"] == s.admin_id and r.json()["helpers"] == [s.uid_a_tec], r.text
    # ticket que atiende Crimson: tampoco
    db_session.get(SupportTicket, s.T_A_libre).assigned_to = s.admin_id
    db_session.commit()
    assert client.patch(f"/aliado/tickets/{s.T_A_libre}", json={"assigned_to": s.uid_a_tec}, headers=s.a_jefe).status_code == 409


def test_material_por_id_sin_reescribir_la_lista(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    mats = [{"product_id": s.prod, "name": "Cámara demo aliado", "quantity": 0, "planned": 2}]
    body = {"title": "Instalar", "project_id": s.P1, "technician_id": s.uid_a_tec, "scheduled_at": datetime.now(UTC).isoformat(), "materials": mats}
    r = client.post("/work-orders", json=body)
    assert r.status_code == 201, r.text
    oid, mid = r.json()["id"], r.json()["materials"][0]["id"]
    # una lista vacia no borra lo planificado
    r = client.post(f"/aliado/ordenes/{oid}/progress", json={"materials": []}, headers=s.a_tec)
    assert r.status_code == 200 and [m["id"] for m in r.json()["materials"]] == [mid], r.text
    # usado por id y lineas nuevas que se suman (el id de la planificada no cambia)
    r = client.post(
        f"/aliado/ordenes/{oid}/progress", json={"quantities": {str(mid): 2}, "agregados": [{"name": "Cinta aislante", "quantity": 1}]}, headers=s.a_tec
    )
    assert r.status_code == 200, r.text
    filas = {m["name"]: m for m in r.json()["materials"]}
    assert filas["Cámara demo aliado"]["id"] == mid and Decimal(str(filas["Cámara demo aliado"]["quantity"])) == 2 and "Cinta aislante" in filas
    nueva = filas["Cinta aislante"]["id"]
    # lo que Crimson planifica mientras tanto no se pierde con el siguiente guardado del aliado
    r = client.put(f"/work-orders/{oid}", json={"materials": [*r.json()["materials"], {"name": "Conector RJ45", "quantity": 0, "planned": 10}]})
    assert r.status_code == 200, r.text
    r = client.post(f"/aliado/ordenes/{oid}/progress", json={"notes": "Avance", "removed": [nueva]}, headers=s.a_tec)
    assert r.status_code == 200, r.text
    assert {m["name"] for m in r.json()["materials"]} == {"Cámara demo aliado", "Conector RJ45"}


def test_ot_editar_con_aliado_suspendido_y_volver_a_crimson(client, auth, db_session):
    from app.models import WorkOrder

    s = escenario(client, dict(client.headers), db_session)
    base = {"title": "Instalación", "project_id": s.P1, "scheduled_at": datetime.now(UTC).isoformat()}
    r = client.post("/work-orders", json={**base, "technician_id": s.uid_a_tec})
    assert r.status_code == 201 and r.json()["ally_company_id"] == s.A, r.text
    oid = r.json()["id"]
    assert client.post(f"/allies/{s.A}/suspend", json={"reason": "Mensualidad atrasada"}).status_code == 200
    # editar lo que ya tenia no se traba por la suspension
    assert client.put(f"/work-orders/{oid}", json={"site": "Otro sitio", "project_id": s.P1}).status_code == 200
    # equipo solo de Crimson (PUT sin la empresa): la orden vuelve a Crimson, como en /assign
    r = client.put(f"/work-orders/{oid}", json={"technician_id": s.admin_id, "helpers": []})
    assert r.status_code == 200 and r.json()["ally_company_id"] is None, r.text
    # trabajo NUEVO a la suspendida, no
    assert client.post("/work-orders", json={**base, "technician_id": s.uid_a_tec}).status_code == 422
    # ticket de la suspendida: la visita con un tecnico de Crimson es de Crimson
    r = client.post(f"/tickets/{s.T_A_libre}/work-order", json={"technician_id": s.admin_id})
    assert r.status_code == 201, r.text
    assert db_session.get(WorkOrder, r.json()["work_order_id"]).ally_company_id is None


# ---------- soporte y mantenimientos ----------
def test_nota_compartida_solo_con_esa_empresa(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert "Nota interna compartida con el aliado" in client.get(f"/aliado/tickets/{s.T_A}", headers=s.a_jefe).text
    body = {"subject": "Falla TK-A", "customer_id": s.c1, "project_id": s.P3, "assigned_to": None, "ally_company_id": s.B, "priority": "media"}
    r = client.put(f"/tickets/{s.T_A}", json=body)
    assert r.status_code == 200 and r.json()["ally_company_id"] == s.B, r.text
    txt = client.get(f"/aliado/tickets/{s.T_A}", headers=s.b_jefe).text
    assert "Nota interna compartida con el aliado" not in txt and "Nota del aliado" not in txt and "Respuesta publica al cliente" in txt
    assert client.post(f"/tickets/{s.T_A}/notes", json={"body": "Compartida con Dos", "internal": True, "share_ally": True}).status_code == 201
    assert "Compartida con Dos" in client.get(f"/aliado/tickets/{s.T_A}", headers=s.b_jefe).text
    # el filtro de la bandeja valida la llave (nada de 500 con ?ally=a: o a:abc)
    for mala in ("a:", "a:abc", "x:1"):
        assert client.get(f"/tickets?ally={mala}").status_code == 422, mala


def test_mantenimiento_responsable_de_crimson_solo_nombre_de_pila(client, auth, db_session):
    from app.models import SupportTicket

    s = escenario(client, dict(client.headers), db_session)
    db_session.add(
        SupportTicket(
            tenant_id=s.tid,
            number="TK-CTR",
            subject="Visita preventiva",
            kind="mantenimiento",
            priority="media",
            customer_id=s.c1,
            project_id=s.P1,
            contract_id=s.K_P1,
            ally_company_id=s.A,
            assigned_to=s.admin_id,
            status="asignado",
        )
    )
    db_session.commit()
    rows = client.get("/aliado/mantenimientos", headers=s.a_jefe).json()
    k = next(x for x in rows if x["id"] == s.K_P1)
    assert next(t for t in k["tickets_abiertos"] if t["number"] == "TK-CTR")["assigned"] == "Administrador"
    assert "Administrador Crimson" not in str(rows)


def test_tope_de_archivos_y_tickets(client, auth, db_session, monkeypatch):
    from app.core import ratelimit
    from app.core.config import settings

    s = escenario(client, dict(client.headers), db_session)
    monkeypatch.setattr(settings, "ally_media_quota_mb", 0)
    r = client.post("/aliado/media", files={"file": ("foto.png", PNG_BYTES, "image/png")}, headers=s.a_tec)
    assert r.status_code == 413 and "tope" in r.json()["detail"]
    monkeypatch.setattr(ratelimit.ally_ticket_limiter, "limit", 1)
    t = {"project_id": s.P1, "subject": "No graba la cámara"}
    assert client.post("/aliado/tickets", json=t, headers=s.a_jefe).status_code == 201
    assert client.post("/aliado/tickets", json=t, headers=s.a_jefe).status_code == 429


# ---------- invitaciones del portal ----------
def test_invitaciones_del_portal_no_pisan_ni_revelan(client, auth, db_session, monkeypatch):
    from app.core import ratelimit
    from app.models import AllyCompany, EmailOutbox, Invitation, Tenant, TenantUser
    from app.services import ally_access

    s = escenario(client, dict(client.headers), db_session)
    ahora = datetime.now(UTC)
    # una invitacion interna pendiente y una de otra empresa aliada: el aliado no las anula ni se queda con el correo
    assert client.post("/settings/invitations", json={"email": "nuevo.interno@ejemplo.com", "role": "ventas"}).status_code == 201
    assert client.post(f"/allies/{s.B}/invitations", json={"email": "pend@aliado-dos.ejemplo.com", "role": "tecnico_aliado"}).status_code == 201
    respuestas = [
        client.post("/aliado/usuarios", json={"email": correo, "name": "Hola <b>Promo</b>"}, headers=s.a_jefe)
        for correo in ("nuevo.interno@ejemplo.com", "pend@aliado-dos.ejemplo.com", "admin@crimsonapp.com", "libre@aliado-uno.ejemplo.com")
    ]
    # misma respuesta para todos: el portal no confirma de quien es un correo, y nunca devuelve el enlace
    assert {r.status_code for r in respuestas} == {202} and len({r.text for r in respuestas}) == 1 and "link" not in respuestas[0].json()
    db_session.expire_all()
    for correo, empresa in (("nuevo.interno@ejemplo.com", None), ("pend@aliado-dos.ejemplo.com", s.B)):
        vivas = db_session.scalars(select(Invitation).where(func.lower(Invitation.email) == correo)).all()
        assert len(vivas) == 1 and vivas[0].ally_company_id == empresa and vivas[0].revoked_at is None and _aware(vivas[0].expires_at) > ahora, correo
    emails = {i["email"] for i in client.get("/aliado/usuarios", headers=s.a_jefe).json()["invitations"]}
    assert "libre@aliado-uno.ejemplo.com" in emails and not {"nuevo.interno@ejemplo.com", "pend@aliado-dos.ejemplo.com"} & emails
    correo = db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == "libre@aliado-uno.ejemplo.com")).first()
    assert correo is not None and "Promo" not in correo.html  # sin texto libre del usuario externo
    # un gerente que Crimson desactivo no vuelve por una invitacion del aliado
    jefe2 = usuario(client, s.admin_h, s.A, "jefe2@aliado-uno.ejemplo.com", "aliado", "Juana Jefa Dos")
    uid2 = uid(client, jefe2)
    assert client.patch(f"/allies/{s.A}/users/{uid2}", json={"active": False}).status_code == 200
    assert client.post("/aliado/usuarios", json={"email": "jefe2@aliado-uno.ejemplo.com"}, headers=s.a_jefe).status_code == 202
    assert "jefe2@aliado-uno.ejemplo.com" not in {i["email"] for i in client.get("/aliado/usuarios", headers=s.a_jefe).json()["invitations"]}
    # ni con una invitacion del portal de antes de esta regla: al aceptarla no se reactiva ni cambia de rol
    inv = ally_access.invite_ally_user(
        db_session, db_session.get(Tenant, s.tid), db_session.get(AllyCompany, s.A), "jefe2@aliado-uno.ejemplo.com", "tecnico_aliado", s.uid_a_jefe
    )
    db_session.commit()
    keep = client.headers.pop("Authorization")
    r = client.post("/settings/invitations/accept", json={"token": inv["link"].rsplit("/", 1)[-1], "password": PW})
    client.headers["Authorization"] = keep
    assert r.status_code == 409, r.text
    db_session.expire_all()
    m = db_session.scalars(select(TenantUser).where(TenantUser.tenant_id == s.tid, TenantUser.user_id == uid2)).one()
    assert m.role_code == "aliado" and m.active is False
    # tope de invitaciones por empresa y dia
    monkeypatch.setattr(ratelimit.ally_invite_limiter, "limit", 1)
    ratelimit.ally_invite_limiter.reset()
    assert client.post("/aliado/usuarios", json={"email": "otro1@aliado-uno.ejemplo.com"}, headers=s.a_jefe).status_code == 202
    assert client.post("/aliado/usuarios", json={"email": "otro2@aliado-uno.ejemplo.com"}, headers=s.a_jefe).status_code == 429


# ---------- participaciones ----------
def test_participacion_de_aliado_inactivo_se_sigue_editando(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert client.post(f"/allies/{s.A}/deactivate").status_code == 200
    r = client.put(f"/opportunity-allies/{s.part_O1}", json={"ally_company_id": s.A, "role": "subcontratista", "visible_to_ally": False})
    assert r.status_code == 200, r.text
    assert next(a for a in r.json() if a["id"] == s.part_O1)["visible_to_ally"] is False
    # una participacion NUEVA con la empresa inactiva, no
    assert client.post(f"/opportunities/{s.O4}/allies", json={"ally_company_id": s.A, "role": "subcontratista"}).status_code == 422


def test_cambiar_de_empresa_con_solicitudes(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert client.post(f"/opportunity-allies/{s.part_O1}/requests", json={"what": "Precio de cableado"}).status_code == 201
    assert client.put(f"/opportunity-allies/{s.part_O1}", json={"ally_company_id": s.B, "role": "subcontratista"}).status_code == 409
    # sin solicitudes si, y los contactos pasan a ser los de la empresa nueva (no los de la anterior)
    assert client.put(f"/allies/{s.A}", json={"contacts": {"tecnico": {"name": "Contacto Uno"}}}).status_code == 200
    assert client.put(f"/allies/{s.B}", json={"contacts": {"tecnico": {"name": "Contacto Dos"}}}).status_code == 200
    o7 = client.post("/opportunities", json={"title": "Otra en común", "customer_id": s.c1}).json()["id"]
    aid = participa(client, s.admin_h, o7, s.A)
    r = client.put(f"/opportunity-allies/{aid}", json={"ally_company_id": s.B, "role": "subcontratista", "contacts": {"tecnico": {"name": "Contacto Uno"}}})
    assert r.status_code == 200, r.text
    assert next(a for a in r.json() if a["id"] == aid)["contacts"]["tecnico"]["name"] == "Contacto Dos"
    # convertir una participacion vieja con solicitudes: solo a la empresa que ES ese proveedor
    assert client.post(f"/opportunity-allies/{s.part_O6}/requests", json={"what": "Precio viejo"}).status_code == 201
    assert client.post(f"/opportunity-allies/{s.part_O6}/to-ally", json={"ally_company_id": s.B}).status_code == 409
    assert client.post(f"/opportunity-allies/{s.part_O6}/to-ally", json={"ally_company_id": s.A}).status_code == 200


# ---------- administracion ----------
def test_recargo_cero_nunca_da_el_costo(client, auth, db_session):
    from app.models import Tenant

    s = escenario(client, dict(client.headers), db_session)
    assert client.put("/allies/config", json={"markup_pct": 0}).status_code == 422
    assert client.put(f"/allies/{s.A}", json={"markup_pct": 0}).status_code == 422
    t = db_session.get(Tenant, s.tid)
    t.settings = {**(t.settings or {}), "ally_markup_pct": "0"}  # dato viejo
    db_session.commit()
    f = client.get(f"/aliado/catalogo/{s.prod}", headers=s.a_jefe).json()
    assert f["precio_aliado"] is None and f["precio_aliado_estado"] == "a_consultar"


def test_ficha_del_aliado_sin_el_embudo_ajeno(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    conta = interno(client, db_session, s.tid, "conta.ficha@ejemplo.com", "contabilidad")
    r = client.get(f"/allies/{s.A}", headers=conta)
    assert r.status_code == 200 and r.json()["participations"] and all(p["opportunity"] is None for p in r.json()["participations"])
    assert "Cámaras bodega norte" not in r.text
    assert any(p["opportunity"] for p in client.get(f"/allies/{s.A}").json()["participations"])


# ---------- migracion: el downgrade deshace lo mismo que hizo el upgrade ----------
def _mig():
    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("aliados_e9c4a7b21d63_*.py"))
    spec = importlib.util.spec_from_file_location("aliados_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _correr(conn, fn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _base(conn):
    for t in ("tenant", "customer", "supplier", "recurrence", "user"):
        conn.exec_driver_sql(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY)')


def test_migracion_downgrade_respeta_la_tabla_de_economia(tmp_path):
    import sqlalchemy as sa

    mod = _mig()
    # economia ya creo la tabla minima: el downgrade quita solo lo que esta migracion le agrego
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'economia.db'}")
    with eng.begin() as conn:
        _base(conn)
        conn.exec_driver_sql(
            "CREATE TABLE ally_company (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenant(id), name VARCHAR(160) NOT NULL,"
            " active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        conn.exec_driver_sql("CREATE INDEX ix_ally_company_tenant_id ON ally_company (tenant_id)")
        conn.exec_driver_sql("INSERT INTO ally_company VALUES (1, 1, 'Aliado Uno S.A.', 1, '2026-10-01', '2026-10-01')")
        _correr(conn, mod._complete_ally_company)
        assert {"tax_id", "markup_pct", "recurrence_id"} <= {c["name"] for c in sa.inspect(conn).get_columns("ally_company")}
        _correr(conn, mod._drop_ally_company)
        insp = sa.inspect(conn)
        assert insp.has_table("ally_company") and not insp.has_table(mod._MARCA)
        assert {c["name"] for c in insp.get_columns("ally_company")} == {"id", "tenant_id", "name", "active", "created_at", "updated_at"}
        assert {i["name"] for i in insp.get_indexes("ally_company")} == {"ix_ally_company_tenant_id"}
        assert conn.exec_driver_sql("SELECT name FROM ally_company").scalar() == "Aliado Uno S.A."
    eng.dispose()
    # la tabla es de esta migracion: el downgrade la borra entera
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'propia.db'}")
    with eng.begin() as conn:
        _base(conn)
        _correr(conn, mod._create_ally_company)
        assert sa.inspect(conn).has_table("ally_company")
        _correr(conn, mod._drop_ally_company)
        assert not sa.inspect(conn).has_table("ally_company")
    eng.dispose()
