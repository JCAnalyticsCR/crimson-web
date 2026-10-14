"""Ordenes de trabajo: "ponerle el tiempo" (reunion con Andres del 13/10, 30:48). Registro por jornada y por persona,
tiempo manual, asignacion rapida, estimado desde el levantamiento y el detalle que guarda solo lo que cambio.

Datos genericos (correos @ejemplo.com); sin dinero en ningun lado de este panel."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models import Product, User, WorkOrder, WorkOrderTime
from app.routers.fieldwork import _hours
from tests.test_session4 import _user_with_role

CR = timezone(timedelta(hours=-6))


def _login(client, db_session, tid, email, rol, admin_h):
    pw = _user_with_role(db_session, tid, email, rol)
    client.headers.pop("Authorization", None)
    r = client.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    client.headers.update(admin_h)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(db, email):
    return db.scalar(select(User).where(User.email == email)).id


def _orden(client, **kw):
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Obra demo", "customer_id": cust["id"]}).json()
    r = client.post("/work-orders", json={"title": "Instalación demo", "project_id": pr["id"], **kw})
    assert r.status_code == 201, r.text
    return r.json(), pr


def _dia(dias_atras: int, h: int, m: int = 0) -> str:
    """Hora de Costa Rica de hace N dias, en ISO con su zona."""
    base = (datetime.now(CR) - timedelta(days=dias_atras)).replace(hour=h, minute=m, second=0, microsecond=0)
    return base.isoformat()


def _equipo(client, db_session, tid, admin_h, n=1):
    out = []
    for i in range(n):
        email = f"tec{i}.ot@ejemplo.com"
        out.append((_login(client, db_session, tid, email, "tecnico", admin_h), _uid(db_session, email)))
    return out


def test_admin_registra_tiempo_sin_ser_el_tecnico(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    [(_, tec_id)] = _equipo(client, db_session, tid, admin_h)
    o, _ = _orden(client)
    assert o["technician_id"] is None and o["crew"] == [] and o["hours"] is None and o["hours_source"] is None
    r = client.post(f"/work-orders/{o['id']}/start", json={})
    assert r.status_code == 409 and "Asigná un técnico" in r.json()["detail"]
    manana = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = client.patch(f"/work-orders/{o['id']}/assign", json={"technician_id": tec_id, "scheduled_at": manana})
    assert r.status_code == 200, r.text
    assert r.json()["technician_id"] == tec_id and r.json()["scheduled_at"] and r.json()["crew"][0]["role"] == "tecnico"
    r = client.post(f"/work-orders/{o['id']}/start", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "en_proceso" and [x["user_id"] for x in r.json()["running"]] == [tec_id]
    e = db_session.get(WorkOrderTime, r.json()["running"][0]["id"])
    e.started_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()
    r = client.post(f"/work-orders/{o['id']}/pause", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["running"] == [] and out["time_entries"][0]["minutes"] == 120 and out["time_entries"][0]["kind"] == "reloj"
    assert out["hours"] == 2.0 and out["hours_source"] == "registro" and out["status"] == "en_proceso"
    assert client.post(f"/work-orders/{o['id']}/pause", json={}).status_code == 409
    # quien no puede recibir ordenes no se asigna
    _user_with_role(db_session, tid, "vend.ot@ejemplo.com", "ventas")
    r = client.patch(f"/work-orders/{o['id']}/assign", json={"helpers": [_uid(db_session, "vend.ot@ejemplo.com")]})
    assert r.status_code == 422 and r.json()["detail"] == "Esa persona no puede recibir órdenes"


def test_jornadas_de_varios_dias_no_cuentan_la_noche(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    (_, t1), (_, t2) = _equipo(client, db_session, tid, admin_h, 2)
    o, _ = _orden(client, technician_id=t1, helpers=[t2])
    for uid in (t1, t2):
        for desde, hasta in ((_dia(3, 8), _dia(3, 17)), (_dia(2, 8), _dia(2, 12))):
            r = client.post(f"/work-orders/{o['id']}/time", json={"user_id": uid, "started_at": desde, "ended_at": hasta})
            assert r.status_code == 201, r.text
    out = client.get(f"/work-orders/{o['id']}").json()
    assert out["hours"] == 26.0 and out["hours_source"] == "registro"  # ni 28 de reloj de pared ni 13 de una sola persona
    assert len(out["time_entries"]) == 4 and all(x["kind"] == "manual" for x in out["time_entries"])


def test_tiempo_manual_reglas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    (tec, tec_id), (_, ayu_id) = _equipo(client, db_session, tid, admin_h, 2)
    sup = _login(client, db_session, tid, "sup.ot@ejemplo.com", "supervisor", admin_h)
    sup_id = _uid(db_session, "sup.ot@ejemplo.com")
    o, _ = _orden(client, technician_id=tec_id, helpers=[ayu_id])
    url = f"/work-orders/{o['id']}/time"
    r = client.post(url, json={"user_id": tec_id, "started_at": _dia(1, 8), "ended_at": _dia(1, 12), "note": "Olvidó iniciar"}, headers=sup)
    assert r.status_code == 201, r.text
    e = next(x for x in r.json()["time_entries"] if x["id"] == r.json()["entry_id"])
    assert e["kind"] == "manual" and e["created_by"] == sup_id and e["created_by_name"] and e["minutes"] == 240 and e["note"] == "Olvidó iniciar"
    # el tecnico registra el suyo (con minutos), no el de otro
    assert client.post(url, json={"started_at": _dia(1, 13), "minutes": 90}, headers=tec).status_code == 201
    assert client.post(url, json={"user_id": ayu_id, "started_at": _dia(1, 13), "minutes": 90}, headers=tec).status_code == 403
    malos = [
        {"started_at": _dia(2, 12), "ended_at": _dia(2, 8)},  # fin antes del inicio
        {"started_at": _dia(3, 8), "ended_at": _dia(2, 9)},  # mas de 24 h
        {"started_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(), "minutes": 30},  # a futuro
        {"started_at": _dia(2, 8)},  # sin fin ni minutos
    ]
    for body in malos:
        assert client.post(url, json=body, headers=tec).status_code == 422, body
    r = client.post(url, json={"started_at": _dia(1, 11), "ended_at": _dia(1, 14)}, headers=tec)  # se solapa
    assert r.status_code == 409 and "Ya hay tiempo de" in r.json()["detail"]
    # otra persona en el mismo rango no se solapa
    assert client.post(url, json={"user_id": ayu_id, "started_at": _dia(1, 8), "ended_at": _dia(1, 12)}, headers=sup).status_code == 201
    # el supervisor le registra a alguien que no estaba: queda como ayudante
    r = client.post(url, json={"user_id": sup_id, "started_at": _dia(1, 8), "ended_at": _dia(1, 9)}, headers=sup)
    assert r.status_code == 201 and sup_id in r.json()["helpers"]


def test_tecnico_no_toca_tiempo_ajeno_ni_ot_finalizada(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    (tec, tec_id), (_, ayu_id) = _equipo(client, db_session, tid, admin_h, 2)
    sup = _login(client, db_session, tid, "sup2.ot@ejemplo.com", "supervisor", admin_h)
    o, _ = _orden(client, technician_id=tec_id, helpers=[ayu_id])
    r = client.post(f"/work-orders/{o['id']}/time", json={"user_id": ayu_id, "started_at": _dia(1, 8), "ended_at": _dia(1, 10)}, headers=sup)
    eid = r.json()["entry_id"]
    body = {"started_at": _dia(1, 8), "ended_at": _dia(1, 11)}
    assert client.put(f"/work-orders/{o['id']}/time/{eid}", json=body, headers=tec).status_code == 403
    assert client.delete(f"/work-orders/{o['id']}/time/{eid}", headers=tec).status_code == 403
    r = client.put(f"/work-orders/{o['id']}/time/{eid}", json=body, headers=sup)
    assert r.status_code == 200 and next(x for x in r.json()["time_entries"] if x["id"] == eid)["minutes"] == 180
    assert client.post(f"/work-orders/{o['id']}/finish", json={}, headers=tec).status_code == 200
    r = client.post(f"/work-orders/{o['id']}/time", json={"started_at": _dia(0, 0, 5), "minutes": 30}, headers=tec)
    assert r.status_code == 409
    # correccion posterior del supervisor: queda en la bitacora
    r = client.post(f"/work-orders/{o['id']}/time", json={"user_id": tec_id, "started_at": _dia(2, 8), "minutes": 60}, headers=sup)
    assert r.status_code == 201, r.text
    assert client.delete(f"/work-orders/{o['id']}/time/{eid}", headers=sup).status_code == 204


def test_finish_y_pause_cierran_y_frenan_olvidos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    [(tec, tec_id)] = _equipo(client, db_session, tid, admin_h)
    o, _ = _orden(client, technician_id=tec_id)
    r = client.post(f"/work-orders/{o['id']}/start", json={}, headers=tec)
    assert r.status_code == 200 and r.json()["stale"] is False
    eid = r.json()["running"][0]["id"]
    db_session.get(WorkOrderTime, eid).started_at = datetime.now(UTC) - timedelta(hours=20)
    db_session.commit()
    assert client.get(f"/work-orders/{o['id']}", headers=tec).json()["stale"] is True
    for action in ("pause", "finish"):
        r = client.post(f"/work-orders/{o['id']}/{action}", json={}, headers=tec)
        assert r.status_code == 409 and "Hay tiempo corriendo desde" in r.json()["detail"]
    inicio = datetime.now(UTC) - timedelta(hours=20)
    r = client.put(
        f"/work-orders/{o['id']}/time/{eid}", json={"started_at": inicio.isoformat(), "ended_at": (inicio + timedelta(hours=8)).isoformat()}, headers=tec
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/work-orders/{o['id']}/finish", json={}, headers=tec)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "finalizada" and r.json()["running"] == [] and r.json()["hours"] == 8.0
    assert db_session.scalar(select(WorkOrderTime).where(WorkOrderTime.work_order_id == o["id"], WorkOrderTime.ended_at.is_(None))) is None


def test_finish_con_tiempo_corriendo_lo_cierra_despues_de_validar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    [(tec, tec_id)] = _equipo(client, db_session, tid, admin_h)
    o, _ = _orden(client, technician_id=tec_id, materials=[{"name": "Cable UTP", "quantity": 0, "planned": 2}])
    client.post(f"/work-orders/{o['id']}/start", json={}, headers=tec)
    r = client.post(f"/work-orders/{o['id']}/finish", json={}, headers=tec)
    assert r.status_code == 422 and "Anotá cuánto usaste" in r.json()["detail"]
    assert client.get(f"/work-orders/{o['id']}", headers=tec).json()["running"]  # el 422 no cerro nada
    mid = client.get(f"/work-orders/{o['id']}", headers=tec).json()["materials"][0]["id"]
    r = client.post(f"/work-orders/{o['id']}/finish", json={"quantities": {str(mid): 2}}, headers=tec)
    assert r.status_code == 200, r.text
    assert r.json()["running"] == [] and r.json()["time_entries"][0]["kind"] == "reloj"


def test_horas_legado_sin_registro(client, auth, db_session):
    o, _ = _orden(client)
    w = db_session.get(WorkOrder, o["id"])
    w.started_at = datetime.now(UTC) - timedelta(hours=33)
    w.finished_at = datetime.now(UTC)
    w.status = "finalizada"
    db_session.commit()
    out = client.get(f"/work-orders/{o['id']}").json()
    assert out["hours_source"] == "legado" and out["hours"] == _hours(w.started_at, w.finished_at) == 33.0


def test_economics_e_informe_usan_horas_registradas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    [(_, tec_id)] = _equipo(client, db_session, tid, admin_h)
    o, pr = _orden(client, technician_id=tec_id)
    client.post(f"/work-orders/{o['id']}/time", json={"user_id": tec_id, "started_at": _dia(2, 8), "minutes": 90})
    client.post(f"/work-orders/{o['id']}/time", json={"user_id": tec_id, "started_at": _dia(1, 8), "minutes": 30})
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert eco["hours"] == 2.0 and eco["hours_estimated"] is None
    informe = client.get(f"/projects/{pr['id']}/report").text
    assert "2.0 h·persona" in informe


def test_estimado_desde_levantamiento(client, auth, db_session):
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    db_session.get(Product, cam["id"]).cost = Decimal("80")
    db_session.commit()
    lev = client.post(
        "/surveys",
        json={
            "kind": "cctv",
            "customer_id": cust["id"],
            "site": "Sitio demo",
            "techs": 2,
            "days": 2,
            "points": [{"code": "CAM-01", "label": "Entrada", "data": {"tipo": "Domo", "distancia_m": 20}}],
            "items": [{"product_id": cam["id"], "name": cam["name"], "quantity": 2, "unit": "Unid"}],
        },
    )
    assert lev.status_code == 201, lev.text
    lev = lev.json()
    assert client.post(f"/surveys/{lev['id']}/send").status_code == 200
    assert client.post(f"/surveys/{lev['id']}/review", json={"action": "aprobar"}).status_code == 200
    q = client.post(f"/surveys/{lev['id']}/quote", json={"include_labor": True})
    assert q.status_code == 201, q.text
    pr = client.post(f"/quotes/{q.json()['quote_id']}/project", json={})
    assert pr.status_code == 201, pr.text
    pr = pr.json()
    oid = client.get(f"/projects/{pr['id']}").json()["orders"][0]["id"]
    out = client.get(f"/work-orders/{oid}").json()
    assert out["hours_estimated"] == 32 and "2 × 2" in out["hours_estimated_label"]
    assert client.get(f"/projects/{pr['id']}").json()["economics"]["hours_estimated"] == 32


def test_progreso_parcial_no_pisa_y_guarda_recibido_por(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    [(tec, tec_id)] = _equipo(client, db_session, tid, admin_h)
    mats = [{"name": "Cable UTP", "quantity": 0, "planned": 10, "unit": "m"}, {"name": "Conector RJ45", "quantity": 0, "planned": 20}]
    o, _ = _orden(client, technician_id=tec_id, tasks=[{"text": "Correr cable", "done": True}, {"text": "Probar", "done": False}], materials=mats)
    ids = [m["id"] for m in client.get(f"/work-orders/{o['id']}").json()["materials"]]
    r = client.post(f"/work-orders/{o['id']}/progress", json={"notes": "Falta el rack"}, headers=tec).json()
    assert r["notes"] == "Falta el rack" and r["tasks"][0]["done"] is True and [m["id"] for m in r["materials"]] == ids
    r = client.post(f"/work-orders/{o['id']}/progress", json={"quantities": {str(ids[0]): 7.5}, "customer_signature": "Persona que recibe"}, headers=tec).json()
    assert [m["id"] for m in r["materials"]] == ids and Decimal(str(r["materials"][0]["quantity"])) == Decimal("7.5")
    assert r["customer_signature"] == "Persona que recibe"
    r = client.post(f"/work-orders/{o['id']}/progress", json={"removed": [ids[1]]}, headers=tec).json()
    assert [m["id"] for m in r["materials"]] == [ids[0]]


def test_ot_finalizada_no_admite_progreso(client, auth):
    o, _ = _orden(client)
    assert client.post(f"/work-orders/{o['id']}/finish", json={}).status_code == 200
    for action in ("progress", "arrive", "cancel", "start"):
        r = client.post(f"/work-orders/{o['id']}/{action}", json={"notes": "tarde"})
        assert r.status_code == 409 and "ya no se modifica" in r.json()["detail"], action
    assert client.patch(f"/work-orders/{o['id']}/assign", json={"scheduled_at": None}).status_code == 409
    assert client.post(f"/work-orders/{o['id']}/otra", json={}).status_code == 404


def test_put_conserva_usado_y_lo_no_enviado(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    (tec, tec_id), (_, ayu_id) = _equipo(client, db_session, tid, admin_h, 2)
    sup = _login(client, db_session, tid, "sup3.ot@ejemplo.com", "supervisor", admin_h)
    wh = next(w for w in client.get("/warehouses").json() if w["is_default"])
    o, _ = _orden(client, technician_id=tec_id, helpers=[ayu_id], warehouse_id=wh["id"], materials=[{"name": "Cable UTP", "quantity": 0, "planned": 4}])
    mid = client.get(f"/work-orders/{o['id']}").json()["materials"][0]["id"]
    client.post(f"/work-orders/{o['id']}/progress", json={"quantities": {str(mid): 3}}, headers=tec)
    body = {"title": "Instalación demo", "technician_id": tec_id, "materials": [{"id": mid, "name": "Cable UTP cat6", "quantity": 0, "planned": 5}]}
    r = client.put(f"/work-orders/{o['id']}", json=body, headers=sup)
    assert r.status_code == 200, r.text
    m = r.json()["materials"][0]
    assert m["id"] == mid and Decimal(str(m["quantity"])) == 3 and Decimal(str(m["planned"])) == 5 and m["name"] == "Cable UTP cat6"
    assert r.json()["helpers"] == [ayu_id] and r.json()["warehouse_id"] == wh["id"]
    # sin "materials" en el cuerpo, los materiales no se tocan
    r = client.put(f"/work-orders/{o['id']}", json={"title": "Instalación demo (2)"}, headers=sup).json()
    assert [x["id"] for x in r["materials"]] == [mid] and r["technician_id"] == tec_id


def test_vendedor_no_registra_tiempo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    vend = _login(client, db_session, tid, "vend2.ot@ejemplo.com", "ventas", admin_h)
    o, _ = _orden(client)
    assert client.post(f"/work-orders/{o['id']}/time", json={"started_at": _dia(1, 8), "minutes": 30}, headers=vend).status_code == 403
    assert client.post(f"/work-orders/{o['id']}/start", json={}, headers=vend).status_code == 403


def test_equipo_de_otra_empresa_no_entra_ni_se_nombra(client, auth, db_session):
    """POST y PUT validan a quien entra al equipo (antes un id de otra empresa quedaba en la orden y su nombre salia
    en crew/running/time_entries); una orden vieja con un id ajeno no devuelve el nombre ni le abre tiempo."""
    from app.models import Tenant, TenantUser

    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    (_, tec_id), (_, ayu_id) = _equipo(client, db_session, tid, admin_h, 2)
    otra = Tenant(slug="otra-ot", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    ajeno = User(email="ajeno.ot@ejemplo.com", full_name="Persona De Otra Empresa", password_hash="x")
    db_session.add(ajeno)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=otra.id, user_id=ajeno.id, role_code="tecnico"))
    db_session.commit()
    for cuerpo in ({"helpers": [ajeno.id]}, {"technician_id": ajeno.id}):
        r = client.post("/work-orders", json={"title": "Instalación demo", **cuerpo})
        assert r.status_code == 422 and r.json()["detail"] == "Esa persona no puede recibir órdenes"
    o, _ = _orden(client, technician_id=tec_id, helpers=[ayu_id])
    for cuerpo in ({"helpers": [ayu_id, ajeno.id]}, {"technician_id": ajeno.id}):
        r = client.put(f"/work-orders/{o['id']}", json={"title": "Instalación demo", **cuerpo})
        assert r.status_code == 422, r.text
    # quien ya estaba en el equipo y hoy esta inactivo no frena la edicion (solo se valida a quien entra)
    m = db_session.scalar(select(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.user_id == ayu_id))
    m.active = False
    db_session.commit()
    r = client.put(f"/work-orders/{o['id']}", json={"title": "Instalación demo (2)", "technician_id": tec_id, "helpers": [ayu_id]})
    assert r.status_code == 200, r.text
    # orden de antes de la validacion con un id ajeno: ni su nombre ni tiempo a su nombre
    w = db_session.get(WorkOrder, o["id"])
    w.helpers = [ayu_id, ajeno.id]
    db_session.commit()
    det = client.get(f"/work-orders/{o['id']}").json()
    assert "Persona De Otra Empresa" not in str(det)
    assert {c["id"]: c["name"] for c in det["crew"]}[ajeno.id] is None
    r = client.post(f"/work-orders/{o['id']}/time", json={"user_id": ajeno.id, "started_at": _dia(1, 8), "ended_at": _dia(1, 9)})
    assert r.status_code == 422, r.text
    assert client.post(f"/work-orders/{o['id']}/start", json={"workers": [ajeno.id]}).status_code == 422
    # sin "workers" arranca el equipo que hoy puede recibir ordenes (ni el ajeno ni el ayudante inactivo)
    r = client.post(f"/work-orders/{o['id']}/start", json={})
    assert r.status_code == 200, r.text
    assert [x["user_id"] for x in r.json()["running"]] == [tec_id]
    assert "Persona De Otra Empresa" not in str(client.get("/work-orders").json())


def test_ayudante_dado_de_baja_no_traba_asignacion_ni_jornada(client, auth, db_session):
    """Un ayudante que ya estaba en la orden y luego se inactivo: la asignacion rapida (el portal manda de vuelta los
    ayudantes que ya tenia) y "Iniciar jornada" siguen funcionando; el detalle dice quien ya no esta activo, iniciar
    con el dice su nombre y se le puede quitar."""
    from app.models import TenantUser

    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    (_, tec_id), (_, ayu_id), (_, otro_id) = _equipo(client, db_session, tid, admin_h, 3)
    db_session.get(User, ayu_id).full_name = "Ayudante De Baja"
    o, _ = _orden(client, helpers=[ayu_id])  # solo ayudantes, sin tecnico
    m = db_session.scalar(select(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.user_id == ayu_id))
    m.active = False
    db_session.commit()
    det = client.get(f"/work-orders/{o['id']}").json()
    assert {c["id"]: c["active"] for c in det["crew"]} == {ayu_id: False}
    # asignacion rapida: tecnico nuevo y los ayudantes que ya tenia
    r = client.patch(f"/work-orders/{o['id']}/assign", json={"technician_id": tec_id, "helpers": [ayu_id]})
    assert r.status_code == 200, r.text
    assert r.json()["technician_id"] == tec_id and r.json()["helpers"] == [ayu_id]
    assert {c["id"]: c["active"] for c in r.json()["crew"]} == {tec_id: True, ayu_id: False}
    # quien entra si se valida: el inactivo no pasa a tecnico
    assert client.patch(f"/work-orders/{o['id']}/assign", json={"technician_id": ayu_id}).status_code == 422
    # iniciar con el inactivo dice quien; sin el arranca
    r = client.post(f"/work-orders/{o['id']}/start", json={"workers": [tec_id, ayu_id]})
    assert r.status_code == 422 and "Ayudante De Baja" in r.json()["detail"], r.text
    assert not db_session.get(WorkOrder, o["id"]).time_entries  # el 422 no deja nada abierto
    r = client.post(f"/work-orders/{o['id']}/start", json={"workers": [tec_id]})
    assert r.status_code == 200, r.text
    assert [x["user_id"] for x in r.json()["running"]] == [tec_id]
    # se le puede quitar y sumar a alguien activo
    r = client.patch(f"/work-orders/{o['id']}/assign", json={"helpers": [otro_id]})
    assert r.status_code == 200, r.text
    assert r.json()["helpers"] == [otro_id]


def _otra_empresa(db_session, slug):
    """Empresa ajena con su cliente, proyecto y bodega (datos genericos)."""
    from app.models import Customer, Project, Tenant, Warehouse

    otra = Tenant(slug=slug, name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    cli = Customer(tenant_id=otra.id, name="Cliente Ajeno De Prueba")
    db_session.add(cli)
    db_session.flush()
    pr = Project(tenant_id=otra.id, number="PRY-AJENO-1", name="Obra Ajena De Prueba", customer_id=cli.id, status="en_curso")
    wh = Warehouse(tenant_id=otra.id, name="Bodega Ajena")
    db_session.add_all([pr, wh])
    db_session.commit()
    return cli, pr, wh


def test_ot_no_enlaza_fichas_de_otra_empresa(client, auth, db_session):
    """POST y PUT validan cliente, proyecto y bodega (antes un project_id ajeno dejaba ver el levantamiento de otra
    empresa y al finalizar le terminaba el proyecto); una orden vieja con un id ajeno no lo nombra ni lo toca."""
    from app.models import Project

    cli, pr_ajeno, wh = _otra_empresa(db_session, "otra-ot-refs")
    malos = (
        ({"customer_id": cli.id}, "Cliente no encontrado"),
        ({"project_id": pr_ajeno.id}, "Proyecto no encontrado"),
        ({"warehouse_id": wh.id}, "Bodega no encontrada"),
    )
    for cuerpo, msg in malos:
        r = client.post("/work-orders", json={"title": "Instalación demo", **cuerpo})
        assert r.status_code == 404 and r.json()["detail"] == msg, r.text
    o, pr = _orden(client)
    for cuerpo, msg in malos:
        r = client.put(f"/work-orders/{o['id']}", json=cuerpo)
        assert r.status_code == 404 and r.json()["detail"] == msg, r.text
    # lo propio se sigue aceptando (y lo que ya tenia no se vuelve a validar)
    assert client.put(f"/work-orders/{o['id']}", json={"project_id": pr["id"], "site": "Sitio nuevo"}).status_code == 200
    # orden de antes de la validacion con un proyecto y un cliente ajenos
    w = db_session.get(WorkOrder, o["id"])
    w.project_id, w.customer_id = pr_ajeno.id, cli.id
    db_session.commit()
    det = client.get(f"/work-orders/{o['id']}").json()
    assert det["project"] is None and det["customer"] is None and det["hours_estimated"] is None
    assert "Ajen" not in str(client.get("/work-orders").json())
    assert client.post(f"/work-orders/{o['id']}/finish", json={}).status_code == 200
    db_session.expire_all()
    assert db_session.get(Project, pr_ajeno.id).status == "en_curso"  # no se termina el proyecto de otra empresa


def test_put_parcial_no_pisa_lo_del_tecnico(client, auth, db_session):
    """El formulario Editar manda solo lo que cambio: el sitio nuevo no devuelve las tareas ni las notas a la copia
    vieja, y un material que el tecnico quito no reaparece."""
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    [(tec, tec_id)] = _equipo(client, db_session, tid, admin_h)
    mats = [{"name": "Cable UTP", "quantity": 0, "planned": 10, "unit": "m"}, {"name": "Conector RJ45", "quantity": 0, "planned": 20}]
    o, _ = _orden(client, technician_id=tec_id, tasks=[{"text": "Correr cable", "done": False}], materials=mats)
    ids = [m["id"] for m in client.get(f"/work-orders/{o['id']}").json()["materials"]]
    # el tecnico marca la tarea, anota y quita una linea mientras el supervisor tiene el formulario abierto
    r = client.post(
        f"/work-orders/{o['id']}/progress",
        json={"tasks": [{"text": "Correr cable", "done": True}], "notes": "Listo el tendido", "removed": [ids[1]]},
        headers=tec,
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/work-orders/{o['id']}", json={"site": "Bodega norte"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["site"] == "Bodega norte" and out["title"] == "Instalación demo"
    assert out["tasks"] == [{"text": "Correr cable", "done": True}] and out["notes"] == "Listo el tendido"
    # el formulario viejo todavia trae la linea quitada: no se recrea con el "usado" de esa copia
    viejos = [
        {"id": ids[0], "name": "Cable UTP", "quantity": 0, "planned": 12, "unit": "m"},
        {"id": ids[1], "name": "Conector RJ45", "quantity": 0, "planned": 20},
    ]
    out = client.put(f"/work-orders/{o['id']}", json={"materials": viejos}).json()
    assert [m["id"] for m in out["materials"]] == [ids[0]] and Decimal(str(out["materials"][0]["planned"])) == 12
    # el titulo no se borra con un null
    assert client.put(f"/work-orders/{o['id']}", json={"title": None}).json()["title"] == "Instalación demo"
