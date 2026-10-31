"""Revision 2 de Andres (30/10) sobre aliados y oportunidades: solicitud de precio separada de la oferta (7-9),
transparencia por niveles (10), acuerdos con versiones (11), mensualidad solo por servicio adicional (13), relaciones
concretas (A2) y alcances y responsables (3, A3). Nombres y correos ficticios (repo publico)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from .aliados_helpers import escenario, interno, keys_rec, participa

AYER = (date.today() - timedelta(days=1)).isoformat()
EN_10 = (date.today() + timedelta(days=10)).isoformat()


def _reqs(r) -> list[dict]:
    return [x for a in r.json() for x in a.get("requests") or []]


def _req(client, oid: int, rid: int, headers=None) -> dict:
    allies = client.get(f"/opportunities/{oid}/allies", headers=headers).json()
    return next(x for a in allies for x in a["requests"] if x["id"] == rid)


def _peticion(**kw) -> dict:
    return {
        "what": "Instalación de 8 cámaras",
        "scope": "Tendido, montaje y configuración básica",
        "site": "Bodega norte, portón 2",
        "inclusions": "Canaletas y conectores",
        "req_exclusions": "Obra civil",
        "req_lines": [{"concept": "Cámara instalada", "quantity": 8, "unit": "u"}, {"concept": "Cable UTP", "quantity": 120, "unit": "m"}],
        "due_date": EN_10,
        "response_channel": "portal",
        **kw,
    }


def _abierta(client, s, titulo: str = "Cámaras patio de carga") -> tuple[int, int, int]:
    """Oportunidad ABIERTA (las del escenario ya estan ganadas) con los aliados A y B: (oid, part_A, part_B)."""
    o = client.post("/opportunities", json={"title": titulo, "customer_id": s.c1, "site": "Patio de carga"}, headers=s.admin_h).json()
    return o["id"], participa(client, s.admin_h, o["id"], s.A), participa(client, s.admin_h, o["id"], s.B)


def _mails(db_session, rid: int):
    from app.models import EmailOutbox

    return db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "ally_cost_request", EmailOutbox.entity_id == rid)).all()


# ---------- 7, 8 y 9: peticion separada de la oferta; borrador, enviado, visto, respondido ----------
def test_peticion_borrador_enviada_vista_y_respondida(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    oid, pa, _pb = _abierta(client, s)
    r = client.post(f"/opportunity-allies/{pa}/requests", json=_peticion(status="borrador", responsible_id=s.admin_id))
    assert r.status_code == 201, r.text
    req = next(x for x in _reqs(r) if x["what"] == "Instalación de 8 cámaras")
    rid = req["id"]
    assert req["status"] == "borrador" and req["delivery"]["sent_at"] is None and req["delivery"]["to"] == "Aliado Uno S.A."
    assert req["delivery"]["channel"] == "portal" and req["responsible"]  # "Responsable interno de seguimiento"
    assert [ln["unit"] for ln in req["req_lines"]] == ["u", "m"] and req["site"] == "Bodega norte, portón 2"
    # la peticion no lleva precio: sin oferta todavia
    assert req["amount"] is None and req["has_offer"] is False
    # el borrador no le llega al aliado ni existe en su portal
    assert _mails(db_session, rid) == []
    assert rid not in {x["id"] for x in client.get("/aliado/solicitudes", headers=s.a_jefe).json()}
    assert client.get(f"/aliado/solicitudes/{rid}", headers=s.a_jefe).status_code == 404
    assert client.put(f"/aliado/solicitudes/{rid}", json={"amount": 10, "currency": "CRC"}, headers=s.a_jefe).status_code == 404

    # enviar: le llega el aviso (sin montos) y la ve en su portal
    r = client.post(f"/opportunity-allies/requests/{rid}/send")
    assert r.status_code == 200, r.text
    req = _req(client, oid, rid)
    assert req["status"] == "enviado" and req["delivery"]["sent_at"] and req["delivery"]["seen_at"] is None
    assert [m.to for m in _mails(db_session, rid)] == ["jefe@aliado-uno.ejemplo.com"]
    assert client.post(f"/opportunity-allies/requests/{rid}/send").status_code == 409
    lista = client.get("/aliado/solicitudes?pendientes=true", headers=s.a_jefe).json()
    mia = next(x for x in lista if x["id"] == rid)
    assert mia["scope"].startswith("Tendido") and mia["req_exclusions"] == "Obra civil" and mia["inclusions"] == "Canaletas y conectores"
    assert mia["status"] == "enviado" and mia["can_respond"]

    # abrirla la deja "vista", con fecha, y la bitacora lo dice (una sola vez)
    d = client.get(f"/aliado/solicitudes/{rid}", headers=s.a_jefe)
    assert d.status_code == 200 and d.json()["status"] == "visto" and d.json()["seen_at"]
    client.get(f"/aliado/solicitudes/{rid}", headers=s.a_jefe)
    req = _req(client, oid, rid)
    assert req["status"] == "visto" and req["delivery"]["seen_at"] and req["delivery"]["seen_by"] == "Ana Jefa Uno"
    notas = client.get(f"/opportunities/{oid}").json()["notes"]
    assert notas.count("vio la solicitud de precio «Instalación de 8 cámaras»") == 1
    # el tecnico del aliado no ve precios
    assert client.get(f"/aliado/solicitudes/{rid}", headers=s.a_tec).status_code == 403

    # la oferta la completa el aliado: precio por desglose, moneda, vigencia, condiciones y sus exclusiones
    oferta = {
        "lines": [{"concept": "Cámara instalada", "quantity": 8, "unit": "u", "unit_price": 15000}],
        "currency": "CRC",
        "valid_until": EN_10,
        "conditions": "50 % de adelanto",
        "exclusions": "Permisos municipales",
    }
    r = client.put(f"/aliado/solicitudes/{rid}", json=oferta, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "respondido" and Decimal(str(r.json()["amount"])) == Decimal("120000.00") and r.json()["conditions"] == "50 % de adelanto"
    adm = _req(client, oid, rid)
    assert adm["status"] == "respondido" and adm["conditions"] == "50 % de adelanto" and adm["exclusions"] == "Permisos municipales"
    assert adm["req_exclusions"] == "Obra civil"  # lo que Crimson no pidio sigue aparte de lo que el aliado excluye

    # quien no ve costos (jefe de ventas: ve todo el embudo) ve la peticion completa y el estado, nunca la oferta
    jefe = interno(client, db_session, s.tid, "jefe.ventas.rev2@ejemplo.com", "jefe_ventas")
    vista = _req(client, oid, rid, headers=jefe)
    assert vista["scope"] and vista["status"] == "respondido" and vista["has_offer"] and vista["delivery"]["seen_at"]
    for k in ("amount", "lines", "conditions", "exclusions", "attachments", "agreements"):
        assert k not in vista
    vend = interno(client, db_session, s.tid, "vendedora.rev2@ejemplo.com", "ventas")
    assert client.get(f"/opportunities/{oid}/allies", headers=vend).status_code == 404  # ajena


def test_vencida_por_fecha_limite_sin_respuesta(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    oid, pa, _pb = _abierta(client, s)
    r = client.post(f"/opportunity-allies/{pa}/requests", json=_peticion(what="Fibra entre bodegas", due_date=AYER))
    rid = next(x["id"] for x in _reqs(r) if x["what"] == "Fibra entre bodegas")
    req = _req(client, oid, rid)
    assert req["status"] == "vencido" and req["stored_status"] == "enviado" and req["overdue"] and req["pending"]
    assert next(x for x in client.get("/opportunity-allies/pending").json() if x["id"] == rid)["overdue"]
    # el aliado la ve vencida y aun puede responder tarde: queda respondida (ya no vencida)
    portal = next(x for x in client.get("/aliado/solicitudes", headers=s.a_jefe).json() if x["id"] == rid)
    assert portal["status"] == "vencido" and portal["can_respond"]
    assert client.put(f"/aliado/solicitudes/{rid}", json={"amount": 5000, "currency": "CRC"}, headers=s.a_jefe).status_code == 200
    req = _req(client, oid, rid)
    assert req["status"] == "respondido" and not req["overdue"]
    # "vencido" no se guarda: mandarlo como estado no cambia nada
    r = client.put(f"/opportunity-allies/requests/{rid}", json={"what": "Fibra entre bodegas", "status": "vencido"})
    assert r.status_code == 200 and _req(client, oid, rid)["stored_status"] == "respondido"
    # una que vence en el futuro sigue enviada
    r = client.post(f"/opportunity-allies/{pa}/requests", json=_peticion(what="Rack de piso", due_date=EN_10))
    otra = next(x for x in _reqs(r) if x["what"] == "Rack de piso")
    assert otra["status"] == "enviado" and not otra["overdue"]


# ---------- 11: aceptar guarda el acuerdo; los cambios son versiones ----------
def test_aceptar_guarda_acuerdo_y_los_cambios_son_versiones(client, auth, db_session):
    from app.models import AllyAgreement, AllyCostRequest

    s = escenario(client, dict(client.headers), db_session)
    r = client.post(f"/opportunity-allies/{s.part_O1}/requests", json=_peticion(what="Mano de obra"))
    rid = next(x["id"] for x in _reqs(r) if x["what"] == "Mano de obra")
    # sin oferta no se acepta
    assert client.post(f"/opportunity-allies/requests/{rid}/decision", json={"action": "aceptar"}).status_code == 409
    oferta = {"amount": 100000, "currency": "CRC", "valid_until": EN_10, "conditions": "Pago contra entrega", "exclusions": "Andamios"}
    assert client.put(f"/aliado/solicitudes/{rid}", json=oferta, headers=s.a_jefe).status_code == 200

    # aceptar es de quien ve costos
    sup = interno(client, db_session, s.tid, "jefe.acuerdo@ejemplo.com", "jefe_ventas")  # ve todo el embudo, no costos
    assert client.post(f"/opportunity-allies/requests/{rid}/decision", json={"action": "aceptar"}, headers=sup).status_code == 403
    r = client.post(f"/opportunity-allies/requests/{rid}/decision", json={"action": "aceptar", "note": "Precio negociado"})
    assert r.status_code == 200, r.text
    req = _req(client, s.O1, rid)
    assert req["status"] == "aceptado" and req["decided_by"] and not req["pending"]
    v1 = req["agreements"][0]
    assert (v1["version"], v1["status"], Decimal(str(v1["amount"])), v1["currency"]) == (1, "vigente", Decimal("100000.00"), "CRC")
    assert v1["conditions"] == "Pago contra entrega" and v1["exclusions"] == "Andamios" and v1["valid_until"] == EN_10 and v1["shared"] is True
    # ya aceptada el aliado no la cambia
    assert client.put(f"/aliado/solicitudes/{rid}", json={"amount": 90000, "currency": "CRC"}, headers=s.a_jefe).status_code == 409

    # un cambio posterior es una version nueva: quien, cuando y que cambio
    assert client.post(f"/opportunity-allies/requests/{rid}/agreement", json={"amount": 100000}).status_code == 409  # nada cambio
    assert client.post(f"/opportunity-allies/requests/{rid}/agreement", json={"amount": 110000}, headers=sup).status_code == 403
    r = client.post(f"/opportunity-allies/requests/{rid}/agreement", json={"amount": 110000, "valid_until": None, "note": "Se sumaron 2 cámaras"})
    assert r.status_code == 200, r.text
    rows = db_session.scalars(select(AllyAgreement).where(AllyAgreement.request_id == rid).order_by(AllyAgreement.version)).all()
    assert [(g.version, g.status) for g in rows] == [(1, "reemplazado"), (2, "vigente")]
    assert any(c.startswith("precio:") for c in rows[1].changes) and any(c.startswith("vigencia:") for c in rows[1].changes)
    assert rows[1].note == "Se sumaron 2 cámaras" and rows[1].created_by == s.admin_id
    assert Decimal(str(db_session.get(AllyCostRequest, rid).amount)) == Decimal("110000.00")  # el pago acordado es la vigente
    notas = client.get(f"/opportunities/{s.O1}").json()["notes"]
    assert "versión 2 · cambió precio, vigencia" in notas and "110" not in notas.split("versión 2")[0].split("\n")[-1]

    # el historial completo para quien ve costos; sin montos para el resto
    hist = client.get(f"/opportunities/{s.O1}/agreements").json()
    assert hist[0]["version"] == 2 and [v["version"] for v in hist[0]["versions"]] == [2, 1]
    sin = client.get(f"/opportunities/{s.O1}/agreements", headers=sup).json()
    assert sin[0]["version"] == 2 and "current" not in sin[0] and "versions" not in sin[0]

    # el aliado ve SUS acuerdos con las versiones
    acu = client.get(f"/aliado/oportunidades/{s.O1}", headers=s.a_jefe).json()["acuerdos"]
    assert [v["version"] for v in acu["mine"][0]["versions"]] == [2, 1] and Decimal(str(acu["mine"][0]["amount"])) == Decimal("110000.00")
    assert "created_by" not in acu["mine"][0]

    # rechazar despues de aceptar anula el acuerdo vigente
    assert client.post(f"/opportunity-allies/requests/{rid}/decision", json={"action": "rechazar"}).status_code == 200
    db_session.expire_all()
    assert db_session.get(AllyAgreement, rows[1].id).status == "anulado"
    assert _req(client, s.O1, rid)["status"] == "rechazado"


def test_estados_viejos_siguen_valiendo_como_sinonimos(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    body = {"what": "Licencias", "status": "aprobado", "amount": 500, "currency": "USD"}
    r = client.post(f"/opportunity-allies/{s.part_O1}/requests", json=body)
    assert r.status_code == 201, r.text
    req = next(x for x in _reqs(r) if x["what"] == "Licencias")
    assert req["status"] == "aceptado" and req["agreements"][0]["version"] == 1
    r = client.put(f"/opportunity-allies/requests/{req['id']}", json={**body, "amount": 650})  # cambio de precio = version
    assert [g["version"] for g in _req(client, s.O1, req["id"])["agreements"]] == [1, 2]
    assert client.put(f"/opportunity-allies/requests/{req['id']}", json={**body, "status": "solicitado"}).status_code == 200
    assert _req(client, s.O1, req["id"])["status"] == "enviado"


# ---------- 10: transparencia por niveles ----------
def _oferta_de_b(client, s, shared: bool = True) -> int:
    r = client.post(f"/opportunity-allies/{s.part_O3_B}/requests", json=_peticion(what="Cableado del aliado dos"))
    rid = next(x["id"] for x in _reqs(r) if x["what"] == "Cableado del aliado dos")
    lines = [{"concept": "Punto de red", "quantity": 10, "unit": "u", "unit_price": 7000}]
    assert client.put(f"/aliado/solicitudes/{rid}", json={"lines": lines, "currency": "CRC", "conditions": "Contado"}, headers=s.b_jefe).status_code == 200
    assert client.post(f"/opportunity-allies/requests/{rid}/decision", json={"action": "aceptar", "shared": shared}).status_code == 200
    return rid


def test_transparencia_montos_acordados_y_costos_abiertos(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    _oferta_de_b(client, s)
    det = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe).json()
    acu = det["acuerdos"]
    # por defecto: el monto ACORDADO de B (afecta el presupuesto comun), sin su desglose ni sus condiciones
    assert acu["open_costs"] is False and acu["open_offers"] == []
    assert len(acu["shared"]) == 1 and acu["shared"][0]["ally"] == "Aliado Dos S.A." and Decimal(str(acu["shared"][0]["amount"])) == Decimal("70000.00")
    assert acu["shared"][0]["lines"] is None and "conditions" not in acu["shared"][0]
    # nada de B fuera de eso: ni su solicitud, ni sus contactos, ni costos o margenes de Crimson
    assert all(x["participation_id"] != s.part_O3_B for x in det["requests"])
    llaves = keys_rec(det)
    for prohibida in ("cost", "unit_cost", "margin", "markup_pct", "price", "commission", "contacts_b"):
        assert prohibida not in llaves

    # costos abiertos: lo decide quien ve costos; entonces A ve la oferta original de B (desglose)
    sup = interno(client, db_session, s.tid, "jefe.transp@ejemplo.com", "jefe_ventas")  # ve todo el embudo, no costos
    assert client.put(f"/opportunities/{s.O3}/transparency", json={"open_costs": True}, headers=sup).status_code == 403
    assert client.put(f"/opportunities/{s.O3}/transparency", json={"open_costs": True}).json()["open_costs"] is True
    acu = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe).json()["acuerdos"]
    assert acu["open_costs"] is True and acu["shared"][0]["lines"][0]["concept"] == "Punto de red"
    assert acu["open_offers"][0]["ally"] == "Aliado Dos S.A." and acu["open_offers"][0]["lines"][0]["unit_price"] in ("7000", "7000.0", 7000)
    # B ve su propio acuerdo completo, con condiciones
    mine = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.b_jefe).json()["acuerdos"]["mine"]
    assert mine[0]["conditions"] == "Contado"


def test_acuerdo_no_compartido_no_lo_ven_los_demas(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    _oferta_de_b(client, s, shared=False)
    client.put(f"/opportunities/{s.O3}/transparency", json={"open_costs": True})
    acu = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe).json()["acuerdos"]
    assert acu["shared"] == [] and acu["open_offers"] == []
    # y un aliado de otra oportunidad no ve nada de O3
    assert client.get(f"/aliado/oportunidades/{s.O3}", headers=s.b_jefe).status_code == 200
    assert client.get(f"/aliado/oportunidades/{s.O2}", headers=s.a_jefe).status_code == 404


# ---------- 3 y A3: alcances y responsables ----------
def test_alcances_una_sola_fuente_y_el_aliado_confirma_lo_suyo(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    # desde un levantamiento sin oportunidad: funciona igual (viven en el levantamiento)
    r = client.post(f"/surveys/{s.S_null}/scope-items", json={"kind": "viaticos", "description": "Traslado y alimentación", "supplier_kind": "crimson", "executor_kind": "crimson"})
    assert r.status_code == 201, r.text
    assert r.json()["source"] == {"opportunity_id": None, "survey_id": s.S_null} and r.json()["items"][0]["kind_label"] == "Viáticos"
    # sin oportunidad no hay a quien de aliado asignar
    assert client.post(f"/surveys/{s.S_null}/scope-items", json={"description": "Algo", "executor_kind": "aliado", "executor_participation_id": s.part_O3_A}).status_code == 422
    # al ligarlo a una oportunidad sus alcances pasan a ella
    oid, pa, pb = _abierta(client, s)
    assert client.post(f"/surveys/{s.S_null}/opportunity", json={"opportunity_id": oid}).status_code == 200
    opp = client.get(f"/opportunities/{oid}/scope").json()
    assert [x["description"] for x in opp["items"]] == ["Traslado y alimentación"]
    assert client.get(f"/surveys/{s.S_null}/scope").json()["items"] == opp["items"]  # la misma fuente

    base = f"/opportunities/{oid}/scope-items"
    a_mia = {"kind": "mano_obra", "description": "Mano de obra para instalar cámaras", "quantity": 2, "unit": "pers", "executor_kind": "aliado", "executor_participation_id": pa, "supplier_kind": "aliado", "supplier_participation_id": pa}
    b_comp = {"kind": "obra_civil", "description": "Canalización y soportes", "executor_kind": "aliado", "executor_participation_id": pb}
    b_priv = {"kind": "configuracion", "description": "Configuración del NVR", "executor_kind": "aliado", "executor_participation_id": pb, "shared": False}
    for body in (a_mia, b_comp, b_priv):
        assert client.post(base, json=body).status_code == 201
    # otra empresa que no participa: 422
    assert client.post(base, json={"description": "X", "executor_kind": "aliado", "executor_participation_id": s.part_O1}).status_code == 422
    items = client.get(f"/opportunities/{oid}/scope").json()
    ids = {x["description"]: x["id"] for x in items["items"]}
    assert items["summary"]["total"] == 4 and items["summary"]["por_estado"]["por_confirmar"] == 4
    assert {o["name"] for o in items["options"]} >= {"Crimson", "Por asignar", "Aliado Uno S.A.", "Aliado Dos S.A."}

    # el aliado A ve lo suyo (con accion) y lo compartido (de B, solo el nombre); nunca lo privado de B
    al = client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["alcances"]["items"]
    vistos = {x["description"]: x for x in al}
    assert set(vistos) == {"Traslado y alimentación", "Mano de obra para instalar cámaras", "Canalización y soportes"}
    assert vistos["Mano de obra para instalar cámaras"]["mine"] and vistos["Mano de obra para instalar cámaras"]["can_act"]
    assert not vistos["Canalización y soportes"]["mine"] and vistos["Canalización y soportes"]["executor"]["name"] == "Aliado Dos S.A."

    # confirma su alcance (con nota) y despues el avance; lo de otro: 404; saltos invalidos: 409
    url = f"/aliado/alcances/{ids['Mano de obra para instalar cámaras']}"
    assert client.post(url, json={"status": "completado"}, headers=s.a_jefe).status_code == 409
    r = client.post(url, json={"status": "confirmado", "note": "Confirmamos 2 técnicos por 3 días"}, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert client.post(f"/aliado/alcances/{ids['Canalización y soportes']}", json={"status": "confirmado"}, headers=s.a_jefe).status_code == 404
    assert client.post(f"/aliado/alcances/{ids['Configuración del NVR']}", json={"status": "confirmado"}, headers=s.a_jefe).status_code == 404
    assert client.post(url, json={"status": "confirmado"}, headers=s.a_tec).status_code == 403
    it = next(x for x in client.get(f"/opportunities/{oid}/scope").json()["items"] if x["id"] == ids["Mano de obra para instalar cámaras"])
    assert it["status"] == "confirmado" and it["confirmed_by"] == "Ana Jefa Uno" and "Confirmamos 2 técnicos" in it["notes"]
    assert "confirmó el alcance «Mano de obra · Mano de obra para instalar cámaras»" in client.get(f"/opportunities/{oid}").json()["notes"]
    assert client.post(url, json={"status": "en_curso"}, headers=s.a_jefe).status_code == 200

    # Crimson cambia el responsable: vuelve a "por confirmar" (le toca confirmarlo al nuevo)
    r = client.put(f"{base}/{ids['Mano de obra para instalar cámaras']}", json={**a_mia, "executor_kind": "crimson", "executor_participation_id": None})
    it = next(x for x in r.json()["items"] if x["id"] == ids["Mano de obra para instalar cámaras"])
    assert it["status"] == "por_confirmar" and it["executor"]["name"] == "Crimson"

    # el proyecto muestra el mismo resumen (el de su oportunidad)
    from .aliados_helpers import proyecto

    pr_id = proyecto(db_session, s.tid, oid, "Proyecto patio de carga", s.c1).id
    pr = client.get(f"/projects/{pr_id}/scope").json()
    assert pr["source"]["opportunity_id"] == oid and len(pr["items"]) == 4

    # quitar a B de la oportunidad: lo suyo queda por asignar
    assert client.delete(f"/opportunity-allies/{pb}").status_code == 200
    resto = {x["description"]: x for x in client.get(f"/opportunities/{oid}/scope").json()["items"]}
    assert resto["Canalización y soportes"]["executor"]["kind"] == "por_asignar" and resto["Canalización y soportes"]["status"] == "por_confirmar"


def test_alcances_permisos_internos(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    vend = interno(client, db_session, s.tid, "vendedora.alcance@ejemplo.com", "ventas")
    tec = interno(client, db_session, s.tid, "tecnico.alcance@ejemplo.com", "tecnico")
    # la vendedora no ve oportunidades ajenas ni sus alcances; en las suyas si los define
    assert client.get(f"/opportunities/{s.O3}/scope", headers=vend).status_code == 404
    mia = client.post("/opportunities", json={"title": "Mía con alcance", "customer_id": s.c1}, headers=vend).json()
    assert client.post(f"/opportunities/{mia['id']}/scope-items", json={"description": "Equipos y grabador", "kind": "equipos", "supplier_kind": "crimson"}, headers=vend).status_code == 201
    # el tecnico ve y define el alcance de SU levantamiento; no el de uno ajeno
    lev = client.post("/surveys", json={"kind": "cctv", "opportunity_id": None}, headers=tec)
    assert lev.status_code == 201, lev.text
    sid = lev.json()["id"]
    assert client.post(f"/surveys/{sid}/scope-items", json={"description": "Escalera de 6 m"}, headers=tec).status_code == 201
    assert client.get(f"/surveys/{s.S_cr}/scope", headers=tec).status_code == 404
    # una partida no se toca desde otro contenedor
    otra = client.get(f"/opportunities/{mia['id']}/scope", headers=vend).json()["items"][0]["id"]
    assert client.put(f"/surveys/{sid}/scope-items/{otra}", json={"description": "Hackeo"}, headers=tec).status_code == 404


def test_personal_requerido_dice_quien_lo_aporta(client, auth, db_session):
    from app.models import Survey

    s = escenario(client, dict(client.headers), db_session)
    oid, pa, _pb = _abierta(client, s)
    labor = {"tecnico": {"people": 2, "days": 3, "provider": "aliado", "participation_id": pa}, "civil": {"people": 1, "days": 1, "provider": "contratado"}}
    r = client.post("/surveys", json={"kind": "cctv", "opportunity_id": oid, "customer_id": s.c1, "labor": labor})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    lab = r.json()["labor"]
    assert lab["tecnico"]["provider_name"] == "Aliado Uno S.A." and lab["civil"]["provider_name"] == "Contratado" and lab["contratado"]["provider"] is None
    # guardar sin decir quien aporta (formulario viejo o el portal del aliado) conserva lo que estaba
    r = client.put(f"/surveys/{sid}", json={"kind": "cctv", "customer_id": s.c1, "labor": {"tecnico": {"people": 3, "days": 3}}})
    assert r.status_code == 200, r.text
    assert r.json()["labor"]["tecnico"]["people"] == 3 and r.json()["labor"]["tecnico"]["provider_name"] == "Aliado Uno S.A."
    # un aliado que no participa en la oportunidad: 422
    bad = {"tecnico": {"people": 1, "days": 1, "provider": "aliado", "participation_id": s.part_O2}}
    assert client.put(f"/surveys/{sid}", json={"kind": "cctv", "customer_id": s.c1, "labor": bad}).status_code == 422
    # el resumen tambien lo dice
    per = client.get(f"/surveys/{sid}/summary").json()["personal"]
    assert any(x["provider_name"] == "Aliado Uno S.A." for x in per)

    # el aliado no cambia quien aporta el personal desde su portal
    sv = db_session.get(Survey, s.S_A)
    sv.labor = {"tecnico": {"people": 2, "days": "1", "provider": "crimson"}}
    db_session.commit()
    body = {"kind": "cctv", "labor": {"tecnico": {"people": 4, "days": 2, "provider": "aliado", "participation_id": s.part_O1}}}
    r = client.put(f"/aliado/levantamientos/{s.S_A}", json=body, headers=s.a_jefe)
    assert r.status_code == 200, r.text
    assert r.json()["labor"]["tecnico"]["people"] == 4 and r.json()["labor"]["tecnico"]["provider"] == "crimson"


# ---------- A2: relaciones concretas ----------
def test_relaciones_yobel_nodo_crimson(client, auth, db_session):
    from app.models import AllyCompany, AllyParticipation, Customer, Opportunity

    admin_h = dict(client.headers)
    yobel = client.post("/customers", json={"name": "Yobel Demo", "id_type": "juridica", "id_number": "3-101-555001"}).json()
    nodo = client.post("/allies", json={"name": "Nodo Demo S.A.", "tax_id": "3-101-555002", "email": "pagos@nodo.ejemplo.com"}).json()["id"]
    o = client.post("/opportunities", json={"title": "CCTV CEDI", "customer_id": yobel["id"]}).json()
    pid = participa(client, admin_h, o["id"], nodo)
    rel = client.get(f"/opportunities/{o['id']}").json()["relations"]
    assert rel["payer"]["is_end_customer"] and rel["executor"]["name"] == "Crimson" and rel["origin"] is None

    # Nodo origino, ejecuta Crimson y quien le paga a Crimson es Nodo (su cliente ligado, sin duplicar fichas)
    r = client.put(f"/opportunities/{o['id']}/relations", json={"origin_participation_id": pid, "executor_participation_id": None, "payer": "participation", "payer_participation_id": pid})
    assert r.status_code == 200, r.text
    rel = r.json()["relations"]
    comp = db_session.get(AllyCompany, nodo)
    assert rel["origin"]["name"] == "Nodo Demo S.A." and rel["executor"]["kind"] == "crimson"
    assert rel["payer"]["customer_id"] == comp.customer_id and rel["payer"]["participation_id"] == pid and not rel["payer"]["is_end_customer"]
    assert rel["end_customer"]["name"] == "Yobel Demo" and rel["billing_line"] == "Crimson le factura a Nodo Demo S.A. (por la obra de Yobel Demo)"
    db_session.expire_all()
    opp = db_session.get(Opportunity, o["id"])
    assert opp.customer_id == comp.customer_id and opp.end_customer_id == yobel["id"]
    part = db_session.get(AllyParticipation, pid)
    assert part.is_origin and part.role == "contratante"  # le paga a Crimson: no gana la regla general de comisiones
    ally = next(a for a in r.json()["allies"] if a["id"] == pid)
    assert set(ally["relations"]) == {"Aliado comercial", "Le paga a Crimson"}
    # repetirlo no crea otra ficha de cliente
    n = db_session.scalar(select(__import__("sqlalchemy").func.count()).select_from(Customer).where(Customer.name == "Nodo Demo S.A."))
    client.put(f"/opportunities/{o['id']}/relations", json={"payer": "participation", "payer_participation_id": pid})
    assert db_session.scalar(select(__import__("sqlalchemy").func.count()).select_from(Customer).where(Customer.name == "Nodo Demo S.A.")) == n == 1

    # vuelve a pagar el cliente final: Nodo deja de ser contratante y sigue como aliado comercial (referido)
    r = client.put(f"/opportunities/{o['id']}/relations", json={"payer": "end_customer"})
    assert r.json()["relations"]["payer"]["is_end_customer"] and r.json()["relations"]["payer"]["customer_id"] == yobel["id"]
    db_session.expire_all()
    assert db_session.get(AllyParticipation, pid).role == "referido"
    # otra empresa que no participa no puede ser el responsable de ejecucion
    otro = client.post("/opportunities", json={"title": "Otra", "customer_id": yobel["id"]}).json()
    pid2 = participa(client, admin_h, otro["id"], nodo)
    assert client.put(f"/opportunities/{o['id']}/relations", json={"executor_participation_id": pid2}).status_code == 404


def test_relaciones_desde_el_formulario_del_aliado_y_varios_aliados(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    # A1: varios aliados por oportunidad siguen funcionando; una sola participacion es responsable de ejecucion
    r = client.put(f"/opportunity-allies/{s.part_O3_A}", json={"ally_company_id": s.A, "is_executor": True})
    assert r.status_code == 200, r.text
    r = client.put(f"/opportunity-allies/{s.part_O3_B}", json={"ally_company_id": s.B, "is_executor": True})
    ej = {a["id"]: a["is_executor"] for a in r.json()}
    assert ej[s.part_O3_B] and not ej[s.part_O3_A] and len(r.json()) == 3
    # el papel que no se manda se conserva (la interfaz ya no pide "Rol")
    assert next(a for a in r.json() if a["id"] == s.part_O3_A2)["role"] == "suministro"
    # el aliado ve sus relaciones en su portal
    det = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.b_jefe).json()
    assert "Responsable de ejecución" in det["my_relations"]
    assert "Responsable de ejecución" not in client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe).json()["my_relations"]


# ---------- 13: mensualidad solo por un servicio adicional definido y aceptado ----------
def _servicio(client) -> int:
    body = {"name": "Mensualidad de la plataforma", "code": "SRV-PLAT", "item_type": "servicio", "price": 0, "currency": "CRC", "cabys_code": "8314100000000"}
    return client.post("/products", json=body).json()["id"]


def test_mensualidad_solo_con_servicio_adicional_aceptado(client, auth, db_session):
    from app.models import AllyCompany, Recurrence

    from .aliados_helpers import aliado, usuario

    admin_h = dict(client.headers)
    a = aliado(client, admin_h, "Aliado Servicio S.A.", "3-101-777001", email="pagos@aliado-servicio.ejemplo.com")
    jefe = usuario(client, admin_h, a, "jefe@aliado-servicio.ejemplo.com", "aliado", "Jefa Servicio")
    tec = usuario(client, admin_h, a, "tec@aliado-servicio.ejemplo.com", "tecnico_aliado", "Tecnico Servicio")
    pid = _servicio(client)
    plan = {"amount": 25000, "currency": "CRC", "mode": "fija", "next_date": EN_10, "product_id": pid, "crear_cliente": True}
    # con monto hace falta decir que servicio adicional se cobra (el acceso basico no se cobra)
    r = client.put(f"/allies/{a}/plan", json=plan)
    assert r.status_code == 422 and "acceso básico" in r.json()["detail"]
    r = client.put(f"/allies/{a}/plan", json={**plan, "service": "Monitoreo remoto 24/7"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["service"]["descripcion"] == "Monitoreo remoto 24/7" and out["service"]["aceptado"] is False and out["basic_access_free"]
    comp = db_session.get(AllyCompany, a)
    rec = db_session.get(Recurrence, comp.recurrence_id)
    assert rec.active is False  # sin aceptacion no se cobra nada
    assert "Monitoreo remoto 24/7" in rec.template["lines"][0]["name"]

    # el portal dice que el acceso basico esta incluido y muestra el servicio por aceptar
    sus = client.get("/aliado/suscripcion", headers=jefe).json()
    assert "no se cobra" in sus["acceso_basico"] and sus["servicio"]["aceptado"] is False
    assert client.post("/aliado/suscripcion/aceptar", headers=tec).status_code == 403
    r = client.post("/aliado/suscripcion/aceptar", headers=jefe)
    assert r.status_code == 200, r.text
    assert r.json()["servicio"]["aceptado"] and "Jefa Servicio" in r.json()["servicio"]["aceptado_por"]
    db_session.expire_all()
    assert db_session.get(Recurrence, comp.recurrence_id).active is True
    assert client.post("/aliado/suscripcion/aceptar", headers=jefe).status_code == 409
    # cambiar el servicio vuelve a pedir la aceptacion (y apaga el cobro)
    r = client.put(f"/allies/{a}/plan", json={**plan, "service": "Monitoreo y respaldo"})
    assert r.json()["service"]["aceptado"] is False
    db_session.expire_all()
    assert db_session.get(Recurrence, comp.recurrence_id).active is False
    # Crimson puede registrar que lo acepto por escrito
    r = client.put(f"/allies/{a}/plan", json={**plan, "service": "Monitoreo y respaldo", "accepted": True})
    assert r.json()["service"]["aceptado"] and r.json()["service"]["aceptado_por"].startswith("Registrado por")


def test_sin_servicio_adicional_la_mora_no_propone_suspender(client, auth, db_session, monkeypatch):
    from app.models import AllyCompany
    from app.services.ally_access import mora_where

    from .test_aliados_mora import HOY, _aliado_con_mensualidad, _correo_ok

    _correo_ok(monkeypatch)
    a, _inv = _aliado_con_mensualidad(client, db_session, vence_hace=40)
    tid = auth["tenant"]["id"]
    assert [c.id for c in db_session.scalars(select(AllyCompany).where(*mora_where(tid, HOY)))] == [a]
    # el mismo atraso sin un servicio adicional aceptado (acceso basico): nunca lleva a suspender
    db_session.get(AllyCompany, a).extra_service_accepted_at = None
    db_session.commit()
    assert list(db_session.scalars(select(AllyCompany).where(*mora_where(tid, HOY)))) == []
    # y el aliado sin servicio entra a su portal normalmente
    from .aliados_helpers import usuario

    h = usuario(client, dict(client.headers), a, "jefe@aliado-mora.ejemplo.com", "aliado", "Jefe Mora")
    assert client.get("/aliado/oportunidades", headers=h).status_code == 200


# ---------- migracion: estados viejos y acuerdos ----------
def test_migracion_estados_y_acuerdos(tmp_path, monkeypatch):
    import sqlalchemy as sa

    from alembic import command
    from app.core.config import settings
    from app.core.db import make_engine

    from .test_infra_migraciones import _config

    url = f"sqlite:///{(tmp_path / 'rev2.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    cfg = _config()
    command.upgrade(cfg, "d4b7e1a9c352")
    eng = make_engine(url)
    ahora = datetime.now(UTC).isoformat(sep=" ")
    with eng.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        for pid, comp, role in ((1, 7, "referido"), (2, None, "subcontratista"), (3, 8, "referido")):
            c.execute(
                sa.text(
                    "INSERT INTO ally_participation (id, tenant_id, opportunity_id, ally_company_id, visible_to_ally, name, role, scope_lines, contacts, created_at, updated_at)"
                    " VALUES (:id, 1, 1, :comp, 1, 'Empresa', :role, '[]', '{}', :t, :t)"
                ),
                {"id": pid, "comp": comp, "role": role, "t": ahora},
            )
        for rid, part, st, amount in ((1, 1, "solicitado", None), (2, 2, "recibido", 100), (3, 1, "aprobado", 250), (4, 1, "rechazado", 50)):
            c.execute(
                sa.text(
                    "INSERT INTO ally_cost_request (id, tenant_id, participation_id, opportunity_id, what, status, amount, currency, attachments, lines, source, created_at, updated_at)"
                    " VALUES (:id, 1, :part, 1, 'Pedido', :st, :amount, 'CRC', '[]', '[]', 'crimson', :t, :t)"
                ),
                {"id": rid, "part": part, "st": st, "amount": amount, "t": ahora},
            )
    eng.dispose()
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    with eng.connect() as c:
        rows = dict(c.execute(sa.text("SELECT id, status FROM ally_cost_request")).fetchall())
        assert rows == {1: "enviado", 2: "respondido", 3: "aceptado", 4: "rechazado"}
        assert c.scalar(sa.text("SELECT COUNT(*) FROM ally_cost_request WHERE sent_at IS NULL")) == 0
        assert c.scalar(sa.text("SELECT response_channel FROM ally_cost_request WHERE id = 2")) == "correo"  # sin portal
        acuerdos = c.execute(sa.text("SELECT request_id, version, status, amount, shared FROM ally_agreement")).fetchall()
        assert [(a[0], a[1], a[2], Decimal(str(a[3])), bool(a[4])) for a in acuerdos] == [(3, 1, "vigente", Decimal("250"), False)]
        assert dict(c.execute(sa.text("SELECT id, is_origin FROM ally_participation")).fetchall()) == {1: 1, 2: 0, 3: 0}
    eng.dispose()
    command.downgrade(cfg, "d4b7e1a9c352")
    eng = make_engine(url)
    with eng.connect() as c:
        rows = dict(c.execute(sa.text("SELECT id, status FROM ally_cost_request")).fetchall())
        assert rows == {1: "solicitado", 2: "recibido", 3: "aprobado", 4: "rechazado"}
        assert "ally_agreement" not in sa.inspect(c).get_table_names()
    eng.dispose()
