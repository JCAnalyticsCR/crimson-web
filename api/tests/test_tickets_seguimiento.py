"""Tickets: bandeja con semaforo de SLA, proximo paso, ultimo seguimiento, filtros rapidos, Tomar/asignar,
permiso support_desk.seguir, reabrir y papelera (reunion con Andres del 13/10: "que yo pueda dar los seguimientos").

Datos genericos: "Cliente Demo", "Empresa Aliada Demo", correos @ejemplo.com."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import EmailOutbox, Role, SupportNote, SupportTicket, TenantUser, User
from app.services import sla
from tests.test_portal_cliente import _crear_cliente_portal
from tests.test_session4 import _user_with_role


def _login(client, db_session, tid, email, rol, admin_h):
    pw = _user_with_role(db_session, tid, email, rol)
    client.headers.pop("Authorization", None)
    r = client.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    client.headers.update(admin_h)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(db, email):
    return db.scalar(select(User).where(User.email == email)).id


def _ticket(client, headers=None, **kw):
    body = {"subject": kw.pop("subject", "Falla de prueba"), "priority": kw.pop("priority", "media"), **kw}
    r = client.post("/tickets", json=body, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def _set(db_session, tid, **campos):
    t = db_session.get(SupportTicket, tid)
    for k, v in campos.items():
        setattr(t, k, v)
    db_session.commit()
    return t


def _ids(rows, mios):
    return [r["id"] for r in rows if r["id"] in mios]


def test_semaforo_rojo_ambar_verde_gris_neutro():
    ahora = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)

    def t(**kw):
        base = {"status": "nuevo", "priority": "alta", "created_at": ahora, "due_at": ahora + timedelta(hours=4), "resolve_due_at": ahora + timedelta(hours=24)}
        return SupportTicket(**{**base, **kw})

    s = sla.semaforo(t(), ahora)
    assert s["color"] == "verde" and s["plazo"] == "respuesta" and s["ventana_min"] == 240 and s["restante_min"] == 240
    # a 50 min de vencer con una ventana de 4 h (queda menos del 25 %)
    s = sla.semaforo(t(created_at=ahora - timedelta(minutes=190), due_at=ahora + timedelta(minutes=50)), ahora)
    assert s["color"] == "ambar" and s["restante_min"] == 50
    vencido = t(created_at=ahora - timedelta(hours=5), due_at=ahora - timedelta(minutes=61))
    s = sla.semaforo(vencido, ahora)
    assert s["color"] == "rojo" and s["plazo"] == "respuesta" and s["restante_min"] == -61
    assert sla.grupo(vencido, ahora) == "vencidos" and sla.cumple(vencido, "vencidos", ahora)
    # ya respondido: manda el plazo de resolucion
    s = sla.semaforo(t(first_reply_at=ahora), ahora)
    assert s["plazo"] == "resolucion" and s["color"] == "verde"
    # esperando al cliente: gris dentro del plazo, rojo si vence (el SLA no se pausa)
    assert sla.semaforo(t(status="esperando_cliente"), ahora)["color"] == "gris"
    assert sla.grupo(t(status="esperando_cliente"), ahora) == "esperando"
    assert sla.semaforo(t(status="esperando_cliente", due_at=ahora - timedelta(minutes=1)), ahora)["color"] == "rojo"
    resuelto = sla.semaforo(t(status="resuelto"), ahora)
    assert resuelto == {"color": "neutro", "plazo": None, "vence_at": None, "ventana_min": None, "restante_min": None}
    assert sla.grupo(t(status="cerrado"), ahora) == "cerrados"
    viejo = sla.semaforo(t(due_at=None, resolve_due_at=None), ahora)
    assert viejo["color"] == "neutro" and viejo["plazo"] is None
    assert sla.grupo(t(due_at=None, resolve_due_at=None), ahora) == "en_tiempo"
    # "hoy" es el dia de Costa Rica: 23:59:59 CR = 05:59:59 UTC del dia siguiente
    assert sla.fin_del_dia_cr(ahora) == datetime(2026, 10, 15, 5, 59, 59, tzinfo=UTC)
    atrasado = t(next_step_at=ahora - timedelta(days=1))
    assert sla.grupo(atrasado, ahora) == "seguimiento_hoy"


def test_lista_trae_semaforo_proximo_paso_y_ultimo_seguimiento(client, auth):
    t = _ticket(client, subject="Marcas incorrectas en relojes", priority="alta")
    assert client.post(f"/tickets/{t['id']}/notes", json={"body": "Se le escribió al cliente"}).status_code == 201
    assert client.post(f"/tickets/{t['id']}/notes", json={"body": "Se pidió el log del reloj", "internal": True}).status_code == 201
    row = next(x for x in client.get("/tickets").json() if x["id"] == t["id"])
    assert row["semaforo"]["color"] == "verde" and row["semaforo"]["plazo"] == "resolucion"  # ya hubo respuesta publica
    assert row["grupo"] == "en_tiempo"
    assert row["last_follow"]["body"] == "Se pidió el log del reloj" and row["last_follow"]["internal"] is True
    assert row["last_follow"]["from_client"] is False and row["last_follow"]["user"]
    assert row["last_activity_at"] and row["next_step"] is None and row["next_step_at"] is None
    assert row["opened_by_name"] and row["allies"] == [] and row["project"] is None
    assert "history" not in row and "notes" not in row
    det = client.get(f"/tickets/{t['id']}").json()
    assert len(det["notes"]) == 2 and det["history"][0]["label"] == "Abrió el ticket"


def test_filtros_rapidos(client, auth, db_session):
    me = auth["user"]["id"]
    ahora = datetime.now(UTC)
    a = _ticket(client, subject="Sin responsable", priority="baja")
    b = _ticket(client, subject="Vencido", assigned_to=me)
    c = _ticket(client, subject="Por vencer", priority="alta", assigned_to=me)
    hoy = _ticket(client, subject="Seguimiento hoy", priority="baja", assigned_to=me)
    quieto = _ticket(client, subject="Sin movimiento", priority="baja", assigned_to=me)
    _set(db_session, b["id"], created_at=ahora - timedelta(hours=10), due_at=ahora - timedelta(hours=2))
    _set(db_session, c["id"], created_at=ahora - timedelta(minutes=210), due_at=ahora + timedelta(minutes=30))
    _set(db_session, hoy["id"], next_step="Llamar al cliente", next_step_at=ahora - timedelta(minutes=5))
    client.post(f"/tickets/{quieto['id']}/notes", json={"body": "Se revisó de lejos", "internal": True})
    nota = db_session.scalar(select(SupportNote).where(SupportNote.ticket_id == quieto["id"]))
    nota.created_at = ahora - timedelta(days=3)
    _set(db_session, quieto["id"], created_at=ahora - timedelta(days=4), due_at=None, resolve_due_at=None)
    mios = {x["id"] for x in (a, b, c, hoy, quieto)}

    def ids(qs):
        r = client.get(f"/tickets?{qs}")
        assert r.status_code == 200, r.text
        return set(_ids(r.json(), mios))

    assert ids("filtro=sin_responsable") == {a["id"]}
    assert ids("filtro=vencidos") == {b["id"]} == ids("pendiente=fuera_de_tiempo")
    assert ids("filtro=por_vencer") == {c["id"]}
    assert ids("filtro=seguimiento_hoy") == {hoy["id"]}
    assert ids("filtro=sin_movimiento") == {quieto["id"]}
    assert ids("assigned=me") == mios - {a["id"]}
    assert ids("assigned=none") == {a["id"]}
    assert ids(f"assigned={me}") == mios - {a["id"]}
    assert ids("priority=alta") == {c["id"]}
    assert client.get("/tickets?filtro=otro").status_code == 422
    assert client.get("/tickets?ally=xx").status_code == 422
    assert client.get("/tickets?orden=raro").status_code == 422
    assert client.get("/tickets?pendiente=otro").status_code == 422  # el de siempre sigue igual


def test_meta_cuenta_lo_mismo_que_los_filtros(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    tec = _login(client, db_session, tid, "tec.meta@ejemplo.com", "tecnico", admin_h)
    tec_id = _uid(db_session, "tec.meta@ejemplo.com")
    ahora = datetime.now(UTC)
    v = _ticket(client, subject="Vencido del técnico", assigned_to=tec_id)
    _set(db_session, v["id"], created_at=ahora - timedelta(hours=10), due_at=ahora - timedelta(hours=1))
    s = _ticket(client, subject="Seguimiento del técnico", assigned_to=tec_id)
    _set(db_session, s["id"], next_step_at=ahora - timedelta(minutes=1))
    _ticket(client, subject="Sin responsable")
    _ticket(client, subject="Del técnico", headers=tec, assigned_to=tec_id)
    for h in (admin_h, tec):
        meta = client.get("/tickets/meta/config", headers=h).json()
        res = meta["resumen"]
        for f in ("vencidos", "por_vencer", "sin_responsable", "seguimiento_hoy", "sin_movimiento"):
            assert res[f] == len(client.get(f"/tickets?filtro={f}", headers=h).json()), f
        assert res["abiertos"] == meta["abiertos"] == len(client.get("/tickets?status=abiertos", headers=h).json())
        assert res["vencidos"] == meta["fuera_de_tiempo"]
        assert res["asignados_a_mi"] == len(client.get("/tickets?status=abiertos&assigned=me", headers=h).json())
        assert meta["umbral_ambar"] == 0.25 and meta["sin_movimiento_h"] == 48
    # el tecnico no ve tickets sin responsable ajenos: su contador viene en 0
    assert client.get("/tickets/meta/config", headers=tec).json()["resumen"]["sin_responsable"] == 0


def test_orden_por_urgencia(client, auth, db_session):
    me = auth["user"]["id"]
    ahora = datetime.now(UTC)
    t = {k: _ticket(client, subject=f"Ticket {k}", priority="baja", assigned_to=me)["id"] for k in ("ok", "esp", "hoy", "amb", "v2", "v1", "cer")}
    _set(db_session, t["v1"], created_at=ahora - timedelta(hours=10), due_at=ahora - timedelta(hours=3))
    _set(db_session, t["v2"], created_at=ahora - timedelta(hours=10), due_at=ahora - timedelta(hours=1))
    _set(db_session, t["amb"], created_at=ahora - timedelta(hours=23), due_at=ahora + timedelta(hours=1))
    _set(db_session, t["hoy"], next_step_at=ahora - timedelta(minutes=30))
    _set(db_session, t["esp"], status="esperando_cliente")
    client.patch(f"/tickets/{t['cer']}", json={"status": "resuelto", "solution": "Se cambió la fuente"})
    mios = set(t.values())
    abiertos = _ids(client.get("/tickets?orden=urgencia").json(), mios)
    assert abiertos == [t["v1"], t["v2"], t["amb"], t["hoy"], t["esp"], t["ok"], t["cer"]]
    assert _ids(client.get("/tickets?orden=urgencia&status=abiertos").json(), mios) == abiertos[:-1]
    # el defecto sigue siendo el de siempre: los mas nuevos primero
    assert _ids(client.get("/tickets?status=").json(), mios) == sorted(mios, reverse=True)
    grupos = {r["id"]: r["grupo"] for r in client.get("/tickets?orden=urgencia").json()}
    assert [grupos[t[k]] for k in ("v1", "amb", "hoy", "esp", "ok", "cer")] == [
        "vencidos",
        "por_vencer",
        "seguimiento_hoy",
        "esperando",
        "en_tiempo",
        "cerrados",
    ]


def test_proximo_paso_con_nota_y_sin_nota(client, auth):
    t = _ticket(client, subject="Cámara sin imagen")
    manana = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Llamé al cliente", "next_step": "Llamar de nuevo", "next_step_at": manana})
    assert r.status_code == 201, r.text
    assert r.json()["next_step"] == "Llamar de nuevo" and r.json()["next_step_at"]
    # una nota sin proximo paso lo conserva
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Sin respuesta del cliente", "internal": True})
    assert r.json()["next_step"] == "Llamar de nuevo"
    # sin texto: "Seguimiento"
    r = client.put(f"/tickets/{t['id']}/next-step", json={"next_step": None, "next_step_at": manana})
    assert r.status_code == 200 and r.json()["next_step"] == "Seguimiento"
    r = client.put(f"/tickets/{t['id']}/next-step", json={"next_step": None, "next_step_at": None})
    assert r.status_code == 200 and r.json()["next_step"] is None and r.json()["next_step_at"] is None
    pasada = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    r = client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Visita", "next_step_at": pasada})
    assert r.status_code == 422 and "ya pasó" in r.json()["detail"]
    # clear_next_step gana sobre lo demas
    client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Visita", "next_step_at": manana})
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Ya no hace falta", "next_step": "Otra", "clear_next_step": True, "internal": True})
    assert r.json()["next_step"] is None
    # resolver limpia el proximo paso; con el ticket resuelto no se planifica
    client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Visita", "next_step_at": manana})
    r = client.patch(f"/tickets/{t['id']}", json={"status": "resuelto", "solution": "Se reinició el grabador"})
    assert r.json()["next_step"] is None and r.json()["next_step_at"] is None
    r = client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Visita", "next_step_at": manana})
    assert r.status_code == 409 and "reabrilo" in r.json()["detail"]
    labels = [h["label"] for h in client.get(f"/tickets/{t['id']}").json()["history"]]
    assert any(x.startswith("Próximo paso: Llamar de nuevo · ") for x in labels)
    assert "Quitó el próximo paso" in labels and "Cambió el estado a Resuelto" in labels


def test_paso_atrasado_cambiar_solo_el_texto_no_rechaza_la_nota(client, auth, db_session):
    """El compositor manda la fecha que el ticket ya tenia junto con el texto nuevo: si ese paso ya esta atrasado, la
    nota se guarda igual (solo se valida una fecha nueva)."""
    t = _ticket(client, subject="Paso atrasado")
    atrasada = datetime.now(UTC) - timedelta(hours=3)
    _set(db_session, t["id"], next_step="Llamar al cliente", next_step_at=atrasada)
    fecha = client.get(f"/tickets/{t['id']}").json()["next_step_at"]  # tal cual la tiene el portal
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "No contestó", "internal": True, "next_step": "Llamar de nuevo", "next_step_at": fecha})
    assert r.status_code == 201, r.text
    assert r.json()["next_step"] == "Llamar de nuevo"
    assert datetime.fromisoformat(r.json()["next_step_at"]).replace(tzinfo=UTC).timestamp() == atrasada.timestamp()
    assert db_session.scalar(select(SupportNote).where(SupportNote.ticket_id == t["id"], SupportNote.body == "No contestó")) is not None
    # sin nota tambien
    r = client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Escribirle por correo", "next_step_at": fecha})
    assert r.status_code == 200 and r.json()["next_step"] == "Escribirle por correo"
    # una fecha NUEVA en el pasado sigue sin valer
    otra = (atrasada - timedelta(hours=1)).isoformat()
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Otra nota", "next_step": "Llamar", "next_step_at": otra})
    assert r.status_code == 422 and "ya pasó" in r.json()["detail"]


def test_tomar_y_asignar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    me = auth["user"]["id"]
    vend = _login(client, db_session, tid, "vend.asignar@ejemplo.com", "ventas", admin_h)
    t = _ticket(client, subject="Para tomar")
    assert t["status"] == "nuevo"
    r = client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": me})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "asignado" and r.json()["assigned_to"] == me
    nombre = r.json()["assigned"]
    assert f"Asignó a {nombre}" in [h["label"] for h in r.json()["history"]]
    r = client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": None})
    assert r.json()["status"] == "nuevo" and r.json()["assigned_to"] is None
    assert "Quitó el responsable" in [h["label"] for h in r.json()["history"]]
    # solo quien atiende soporte (admin, supervisor, tecnico)
    r = client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": _uid(db_session, "vend.asignar@ejemplo.com")})
    assert r.status_code == 422 and r.json()["detail"] == "Esa persona no atiende soporte"
    assert client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": me}, headers=vend).status_code == 403


def test_rol_con_seguir_registra_seguimiento_pero_no_resuelve(client, auth, db_session):
    """support_desk.seguir: anota y planifica el proximo paso en sus tickets, sin resolver ni reasignar. Hoy ningun
    rol predefinido lo tiene (la matriz del 13/10 dejo al vendedor sin soporte): se prueba con un rol a la medida."""
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    db_session.add(Role(code="seguimiento_demo", name="Seguimiento", permissions={"dashboard": ["ver"], "support_desk": ["ver", "crear", "seguir"]}))
    db_session.commit()
    seg = _login(client, db_session, tid, "seguimiento@ejemplo.com", "seguimiento_demo", admin_h)
    t = _ticket(client, subject="Abierto por quien da seguimiento", headers=seg)
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Le escribí al cliente"}, headers=seg)
    assert r.status_code == 201, r.text
    assert r.json()["first_reply_at"]  # cuenta como primera respuesta
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Ya quedó", "status": "resuelto"}, headers=seg)
    assert r.status_code == 403 and r.json()["detail"] == "Sin permiso para cambiar el estado"
    assert client.patch(f"/tickets/{t['id']}", json={"status": "resuelto", "solution": "x"}, headers=seg).status_code == 403
    assert client.put(f"/tickets/{t['id']}", json={"subject": "Cambio"}, headers=seg).status_code == 403
    assert client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": None}, headers=seg).status_code == 403
    manana = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Llamar", "next_step_at": manana}, headers=seg).status_code == 200
    ajeno = _ticket(client, subject="De otra persona", assigned_to=auth["user"]["id"])
    assert client.post(f"/tickets/{ajeno['id']}/notes", json={"body": "Hola"}, headers=seg).status_code == 404
    # el vendedor (matriz del 13/10) no tiene la mesa de soporte
    vend = _login(client, db_session, tid, "vend.seg@ejemplo.com", "ventas", admin_h)
    assert client.get("/tickets", headers=vend).status_code == 403
    assert client.post(f"/tickets/{t['id']}/notes", json={"body": "Hola"}, headers=vend).status_code == 403


def test_reabrir_limpia_resuelto(client, auth, db_session):
    t = _ticket(client, subject="Para reabrir")
    _set(db_session, t["id"], due_at=datetime.now(UTC) - timedelta(hours=1))
    r = client.patch(f"/tickets/{t['id']}", json={"status": "resuelto", "solution": "Se ajustó el enfoque"}).json()
    assert r["resolved_at"] and r["fuera_de_tiempo"] is False and r["semaforo"]["color"] == "neutro"
    r = client.patch(f"/tickets/{t['id']}", json={"status": "en_proceso"}).json()
    assert r["resolved_at"] is None and r["closed_at"] is None
    assert r["fuera_de_tiempo"] is True and r["semaforo"]["color"] == "rojo"  # reabierto con el plazo vencido
    r = client.patch(f"/tickets/{t['id']}", json={"status": "cerrado"}).json()
    assert r["closed_at"] and r["resolved_at"]
    r = client.patch(f"/tickets/{t['id']}", json={"status": "esperando_cliente"}).json()
    assert r["closed_at"] is None and r["resolved_at"] is None and r["status"] == "esperando_cliente"


def test_ticket_en_papelera_no_admite_cambios(client, auth):
    t = _ticket(client, subject="A la papelera")
    assert client.post(f"/archive/ticket/{t['id']}/trash").status_code == 200
    manana = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    for r in (
        client.post(f"/tickets/{t['id']}/notes", json={"body": "Hola"}),
        client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": auth["user"]["id"]}),
        client.put(f"/tickets/{t['id']}/next-step", json={"next_step": "Llamar", "next_step_at": manana}),
        client.patch(f"/tickets/{t['id']}", json={"status": "en_proceso"}),
        client.put(f"/tickets/{t['id']}", json={"subject": "Otro asunto"}),
    ):
        assert r.status_code == 409 and "papelera" in r.json()["detail"], r.text
    det = client.get(f"/tickets/{t['id']}")
    assert det.status_code == 200 and det.json()["trashed_at"]
    assert client.post(f"/archive/ticket/{t['id']}/restore").status_code == 200
    assert client.post(f"/tickets/{t['id']}/notes", json={"body": "Hola"}).status_code == 201


def _proyecto_con_aliado(client):
    """Oportunidad con una empresa aliada -> cotizacion -> proyecto (la participacion queda ligada al proyecto)."""
    contratante = client.get("/customers").json()["items"][0]
    aliado = client.post("/customers", json={"name": "Empresa Aliada Demo", "id_type": "juridica"}).json()
    o = client.post("/opportunities", json={"title": "Red del edificio", "customer_id": contratante["id"], "amount": 0}).json()
    r = client.post(f"/opportunities/{o['id']}/allies", json={"customer_id": aliado["id"], "role": "subcontratista"})
    assert r.status_code == 201, r.text
    qr = client.post(f"/opportunities/{o['id']}/quote", json={}).json()
    lines = [{"name": "Configuración", "quantity": 1, "unit_price": 450, "tax_rate": 13}]
    client.put(f"/quotes/{qr['quote_id']}", json={"customer_id": contratante["id"], "currency": "USD", "lines": lines})
    pr = client.post(f"/quotes/{qr['quote_id']}/project", json={})
    assert pr.status_code == 201, pr.text
    return contratante, aliado, pr.json()


def test_filtro_por_empresa_aliada(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    contratante, aliado, pr = _proyecto_con_aliado(client)
    con = _ticket(client, subject="Falla en la red del edificio", customer_id=contratante["id"], project_id=pr["id"])
    sin = _ticket(client, subject="Falla sin proyecto", customer_id=contratante["id"])
    assert con["project"] == pr["number"]
    assert con["allies"] == [{"key": f"c:{aliado['id']}", "name": "Empresa Aliada Demo", "role": "subcontratista"}]
    ids = [x["id"] for x in client.get(f"/tickets?ally=c:{aliado['id']}").json()]
    assert con["id"] in ids and sin["id"] not in ids
    # la llave de la empresa aliada explicita (a:<id>) ya se acepta; hasta que exista no trae nada
    assert client.get("/tickets?ally=a:999").json() == []
    meta = client.get("/tickets/meta/config").json()
    assert {"key": f"c:{aliado['id']}", "name": "Empresa Aliada Demo", "abiertos": 1} in meta["allies"]
    tec = _login(client, db_session, tid, "tec.aliado@ejemplo.com", "tecnico", admin_h)
    assert client.get("/tickets/meta/config", headers=tec).json()["allies"] == []
    # el proyecto se puede elegir por cliente en el formulario del ticket
    assert pr["id"] in [x["id"] for x in client.get(f"/projects?customer_id={contratante['id']}").json()]
    assert pr["id"] not in [x["id"] for x in client.get(f"/projects?customer_id={aliado['id']}").json()]


def test_historial_y_auditoria_de_edicion(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    _login(client, db_session, tid, "tec.hist@ejemplo.com", "tecnico", admin_h)
    tec_id = _uid(db_session, "tec.hist@ejemplo.com")
    t = _ticket(client, subject="Para editar", priority="baja")
    r = client.put(f"/tickets/{t['id']}", json={"subject": "Para editar", "priority": "alta", "assigned_to": tec_id})
    assert r.status_code == 200, r.text
    hist = r.json()["history"]
    acciones = [h["action"] for h in hist]
    assert acciones[0] == "create" and "update" in acciones and "assign" in acciones
    assert acciones.index("update") < acciones.index("assign")
    labels = [h["label"] for h in hist]
    assert "Editó: prioridad" in labels and "Asignó a Tecnico" in labels and "Cambió el estado a Asignado" in labels
    fechas = [h["at"] for h in hist]
    assert fechas == sorted(fechas)


def test_portal_cliente_no_ve_lo_nuevo(client, auth, db_session):
    admin_h = dict(client.headers)
    cust = client.get("/customers").json()["items"][0]
    cli = _crear_cliente_portal(client, admin_h, cust["id"], "cliente.seg@ejemplo.com", "cliente_usuario", "Cliente Demo")
    r = client.post("/cliente/tickets", json={"kind": "falla", "subject": "No graba la cámara", "description": "Desde ayer no graba nada"}, headers=cli)
    assert r.status_code == 201, r.text
    tk = r.json()["id"]
    client.headers.update(admin_h)
    assert client.post(f"/tickets/{tk}/notes", json={"body": "Nota interna del equipo", "internal": True}).status_code == 201
    assert client.post(f"/cliente/tickets/{tk}/comments", json={"body": "Sigue sin grabar"}, headers=cli).status_code == 201
    row = next(x for x in client.get("/tickets", headers=admin_h).json() if x["id"] == tk)
    assert row["last_follow"]["from_client"] is True and row["last_follow"]["body"] == "Sigue sin grabar"
    det = client.get(f"/tickets/{tk}", headers=admin_h).json()
    assert [n["from_client"] for n in det["notes"]] == [False, True]
    vista = client.get(f"/cliente/tickets/{tk}", headers=cli).json()
    assert "last_follow" not in vista and "history" not in vista and "notes" not in vista
    assert all("Nota interna" not in m["body"] for m in vista["messages"])
    # por las dudas: el usuario del portal es de rol cliente (asi se marca "Cliente respondió")
    uid = _uid(db_session, "cliente.seg@ejemplo.com")
    assert db_session.scalar(select(TenantUser.role_code).where(TenantUser.user_id == uid)) == "cliente_usuario"


def test_filtro_respeta_el_estado_elegido(client, auth, db_session):
    """Estado "Esperando al cliente" + "Vencidos" = los vencidos que esperan al cliente (antes el filtro pisaba el estado)."""
    me = auth["user"]["id"]
    ahora = datetime.now(UTC)
    esp = _ticket(client, subject="Vencido esperando", assigned_to=me)
    asg = _ticket(client, subject="Vencido asignado", assigned_to=me)
    for x in (esp, asg):
        _set(db_session, x["id"], created_at=ahora - timedelta(hours=10), due_at=ahora - timedelta(hours=2))
    _set(db_session, esp["id"], status="esperando_cliente")
    mios = {esp["id"], asg["id"]}

    def ids(qs):
        r = client.get(f"/tickets?{qs}")
        assert r.status_code == 200, r.text
        return set(_ids(r.json(), mios))

    assert ids("filtro=vencidos") == mios == ids("status=abiertos&filtro=vencidos")
    assert ids("status=esperando_cliente&filtro=vencidos&orden=urgencia") == {esp["id"]}
    assert ids("status=asignado&filtro=vencidos") == {asg["id"]}
    assert ids("status=esperando_cliente&pendiente=fuera_de_tiempo") == {esp["id"]}
    assert ids("status=resuelto&filtro=vencidos") == set()  # los contadores son siempre sobre abiertos


def test_tecnico_no_lee_ni_toma_tickets_sin_responsable(client, auth, db_session):
    """Sin ver_todo, el detalle tiene el mismo alcance que la lista: un ticket sin responsable no es de todos (si no,
    con ids seguidos se leen notas internas y se "toma" con POST /assign)."""
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    tec = _login(client, db_session, tid, "tec.libre@ejemplo.com", "tecnico", admin_h)
    tec_id = _uid(db_session, "tec.libre@ejemplo.com")
    libre = _ticket(client, subject="Sin responsable de nadie")
    client.post(f"/tickets/{libre['id']}/notes", json={"body": "Nota interna del equipo", "internal": True})
    for r in (
        client.get(f"/tickets/{libre['id']}", headers=tec),
        client.post(f"/tickets/{libre['id']}/assign", json={"assigned_to": tec_id}, headers=tec),
        client.post(f"/tickets/{libre['id']}/notes", json={"body": "Hola"}, headers=tec),
        client.put(f"/tickets/{libre['id']}", json={"subject": "Me lo quedo", "assigned_to": tec_id}, headers=tec),
    ):
        assert r.status_code == 404, r.text
    assert client.get(f"/tickets/{libre['id']}").json()["assigned_to"] is None
    # lo que abrio el (aunque no tenga responsable) y lo que le asignan si
    propio = _ticket(client, headers=tec, subject="Abierto por el técnico")
    assert client.get(f"/tickets/{propio['id']}", headers=tec).status_code == 200
    assert client.post(f"/tickets/{libre['id']}/assign", json={"assigned_to": tec_id}).status_code == 200
    assert client.get(f"/tickets/{libre['id']}", headers=tec).status_code == 200


def test_aliados_y_proyecto_solo_en_el_alcance(client, auth, db_session):
    """Las empresas aliadas de un ticket salen con support_desk.ver_todo (como meta.allies) o si el proyecto es del
    alcance de quien mira; y nadie liga un ticket a un proyecto que no ve (leeria su numero y sus aliados)."""
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    contratante, aliado, pr = _proyecto_con_aliado(client)
    tec = _login(client, db_session, tid, "tec.alcance@ejemplo.com", "tecnico", admin_h)
    tec_id = _uid(db_session, "tec.alcance@ejemplo.com")
    r = client.post("/tickets", json={"subject": "Falla de red", "customer_id": contratante["id"], "project_id": pr["id"]}, headers=tec)
    assert r.status_code == 404 and r.json()["detail"] == "Proyecto no encontrado"
    t = _ticket(client, subject="Falla en la red", customer_id=contratante["id"], project_id=pr["id"], assigned_to=tec_id)
    assert t["allies"] and t["allies"][0]["key"] == f"c:{aliado['id']}"
    assert client.get(f"/tickets/{t['id']}", headers=tec).json()["allies"] == []
    assert next(x for x in client.get("/tickets", headers=tec).json() if x["id"] == t["id"])["allies"] == []
    # editar su ticket sin cambiar el proyecto sigue funcionando
    cuerpo = {"subject": "Falla en la red del piso 2", "customer_id": contratante["id"], "project_id": pr["id"], "assigned_to": tec_id}
    r = client.put(f"/tickets/{t['id']}", json=cuerpo, headers=tec)
    assert r.status_code == 200, r.text
    # con una orden de trabajo en ese proyecto, el proyecto es de su alcance
    assert client.post("/work-orders", json={"title": "Revisión de red", "project_id": pr["id"], "technician_id": tec_id}).status_code == 201
    db_session.expire_all()  # la prueba comparte una sesion entre pedidos: el proyecto ya tenia sus ordenes cargadas
    assert client.get(f"/tickets/{t['id']}", headers=tec).json()["allies"][0]["key"] == f"c:{aliado['id']}"
    r = client.post("/tickets", json={"subject": "Otra falla de red", "customer_id": contratante["id"], "project_id": pr["id"]}, headers=tec)
    assert r.status_code == 201, r.text


def test_aviso_de_asignacion_no_se_repite(client, auth, db_session):
    """Alternar el responsable (Tomar, selector, autoguardado del PUT) no encola un correo por cada cambio."""
    tid = auth["tenant"]["id"]
    correos = ["tec.aviso1@ejemplo.com", "tec.aviso2@ejemplo.com"]
    ids = []
    for c in correos:
        _user_with_role(db_session, tid, c, "tecnico")
        ids.append(_uid(db_session, c))
    t = _ticket(client, subject="Para alternar el responsable")
    for uid in (ids[0], ids[1], ids[0], ids[1], ids[0]):
        assert client.post(f"/tickets/{t['id']}/assign", json={"assigned_to": uid}).status_code == 200
    r = client.put(f"/tickets/{t['id']}", json={"subject": "Para alternar el responsable", "assigned_to": ids[1]})
    assert r.status_code == 200, r.text
    enviados = db_session.scalars(
        select(EmailOutbox.to).where(EmailOutbox.entity == "support_ticket", EmailOutbox.entity_id == t["id"], EmailOutbox.to.in_(correos))
    ).all()
    assert sorted(enviados) == correos


def test_editar_no_quita_el_equipo(client, auth, db_session):
    """El formulario Editar no lleva el equipo: guardar (o el autoguardado al cerrar) no lo borra ni lo anota en el
    historial como si alguien lo hubiera quitado. Mandarlo en null si lo quita."""
    from app.models import CustomerAsset

    tid = auth["tenant"]["id"]
    cust = client.get("/customers").json()["items"][0]
    act = CustomerAsset(tenant_id=tid, customer_id=cust["id"], name="Cámara demo")
    db_session.add(act)
    db_session.commit()
    t = _ticket(client, subject="Con equipo", customer_id=cust["id"], asset_id=act.id)
    assert t["asset_id"] == act.id
    body = {"subject": "Con equipo", "customer_id": cust["id"], "priority": "alta", "contact": {}, "tags": []}
    r = client.put(f"/tickets/{t['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["asset_id"] == act.id and r.json()["asset"] == "Cámara demo"
    assert "Editó: equipo" not in " ".join(h["label"] for h in r.json()["history"])
    r = client.put(f"/tickets/{t['id']}", json={**body, "asset_id": None})
    assert r.status_code == 200 and r.json()["asset_id"] is None


def test_ot_desde_ticket_solo_a_quien_recibe_ordenes(client, auth, db_session):
    """El tecnico de la OT que nace del ticket es del equipo (como al asignar en Ordenes de trabajo): ni ventas, ni un
    cliente del portal, ni alguien de otra empresa (su nombre saldria en el informe de entrega)."""
    from app.models import Tenant, WorkOrder

    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    _login(client, db_session, tid, "vend.tck@ejemplo.com", "ventas", admin_h)
    _login(client, db_session, tid, "tec.tck@ejemplo.com", "tecnico", admin_h)
    otra = Tenant(slug="otra-tck-ot", name="Otra empresa de prueba", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    ajeno = User(email="ajeno.tck@ejemplo.com", full_name="Persona De Otra Empresa", password_hash="x")
    db_session.add(ajeno)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=otra.id, user_id=ajeno.id, role_code="tecnico"))
    db_session.commit()
    t = _ticket(client, subject="Hay que ir al sitio")
    antes = db_session.query(WorkOrder).count()
    for uid in (_uid(db_session, "vend.tck@ejemplo.com"), ajeno.id, 999_999):
        r = client.post(f"/tickets/{t['id']}/work-order", json={"technician_id": uid})
        assert r.status_code == 422 and r.json()["detail"] == "Esa persona no puede recibir órdenes", r.text
    assert db_session.query(WorkOrder).count() == antes
    r = client.post(f"/tickets/{t['id']}/work-order", json={"technician_id": _uid(db_session, "tec.tck@ejemplo.com")})
    assert r.status_code == 201, r.text
