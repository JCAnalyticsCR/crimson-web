"""«Acción realizada» y «Notificación a Hacienda» como en Fygaro (services/reception_state), y lo que cuelga de ellas:
el ensayo en Pruebas (no se envia), «Enviar en Producción» con la misma decision, nunca dos mensajes de Produccion,
las descargas solo del ultimo intento y el resumen que no cuenta los -29 de Pruebas como rechazos.

Datos 100 % ficticios y montos de 2 000 colones o menos. Nada sale a la red (send_response y el proveedor van
reemplazados)."""

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from app.services import reception_state as st
from tests.hacienda_utils import clave, fe_xml, modo_pruebas

HOY = date(2026, 11, 5)
MINUS29 = 'codigo, mensaje, fila, columna\n-29, ""La clave del comprobante no se encuentra registrada"", 0, 0'


def _doc(**kw):
    base = dict(
        status="respondido",
        doc_type="FE",
        action="aceptada",
        deadline=date(2026, 11, 11),
        supplier_hacienda_status="aceptado",
        supplier_hacienda_detail=None,
        discarded_reason=None,
        hacienda_status="ensayo",
        iva_condition="credito",
    )
    base.update(kw)
    return NS(**base)


def _r(**kw):
    base = dict(mode="pruebas", status="ensayo", hacienda_detail=None, last_error=None, hacienda_xml=None, attempts=0, message_code="1")
    base.update(kw)
    return NS(**base)


@pytest.mark.parametrize(
    "doc,rs,key,label,tone",
    [
        # el -29 del ambiente de Pruebas: pendiente ante la Hacienda real, en ambar y con la fecha
        (_doc(), [_r(status="rechazado", hacienda_detail=MINUS29, attempts=1)], "solo_pruebas", "Pendiente · solo en Pruebas", "warn"),
        (_doc(), [_r()], "solo_pruebas", "Pendiente · solo en Pruebas", "warn"),
        # el mismo, con el plazo vencido: rojo (debe constar en Fygaro)
        (_doc(deadline=date(2026, 11, 2)), [_r(status="rechazado", hacienda_detail=MINUS29)], "pruebas_vencida", "No enviada · plazo vencido", "bad"),
        (_doc(), [_r(mode="produccion", status="aceptado")], "aprobado", "Aprobado", "ok"),
        (_doc(), [_r(mode="produccion", status="rechazado", hacienda_detail="Rechazado por datos")], "rechazado", "Rechazado", "bad"),
        (_doc(), [_r(mode="produccion", status="enviado")], "en_proceso", "Pendiente · en proceso", "info"),
        (_doc(), [_r(mode="produccion", status="error", attempts=0, last_error="Cargue las credenciales")], "no_llego", "No llegó · responda de nuevo", "warn"),
        (_doc(), [_r(mode="produccion", status="error", attempts=2, last_error="Hacienda no encuentra el documento")], "verificar", "Error · verificar", "bad"),
        (
            _doc(),
            [_r(mode="produccion", status="error", attempts=1, last_error="Hacienda ya tiene un documento con esa clave: ¿se respondió o emitió desde Fygaro?")],
            "ya_recibido",
            "Ya respondido en otro sistema",
            "bad",
        ),
        (_doc(), [_r(mode="produccion", status="error", attempts=1, hacienda_xml="<MensajeHacienda/>")], "rechazado", "Rechazado", "bad"),
        (_doc(), [_r(mode="simulado", status="simulado")], "simulado", "Simulado · no se envió", "muted"),
        (_doc(status="historico"), [_r(mode="produccion", status="historico")], "fygaro", "Respondido en Fygaro", "ok"),
        (_doc(status="por_revisar", action=None), [], "pendiente", "Pendiente", "muted"),
        (_doc(status="sin_respuesta", doc_type="TE", action=None), [], "no_aplica", "No aplica", "muted"),
        (_doc(status="ajeno", action=None), [], "no_aplica", "No aplica", "muted"),
        (_doc(status="invalido", action=None), [], "no_aplica", "No aplica", "muted"),
        (_doc(status="descartado", action=None), [], "no_aplica", "No aplica", "muted"),
    ],
)
def test_notificacion(doc, rs, key, label, tone):
    n = st.notificacion(doc, rs, HOY)
    assert (n["key"], n["label"], n["tone"]) == (key, label, tone)


def test_notificacion_pruebas_lleva_la_fecha_y_el_codigo():
    n = st.notificacion(_doc(), [_r(status="rechazado", hacienda_detail=MINUS29, attempts=1)], HOY)
    assert n["vence"] == "2026-11-11" and "11/11" in n["hint"] and n["codigo"] == "-29" and n["modo"] == "pruebas"
    vencida = st.notificacion(_doc(deadline=date(2026, 11, 2)), [_r()], HOY)
    assert "Importar histórico" in vencida["hint"]


@pytest.mark.parametrize(
    "doc,rs,label,tone",
    [
        (_doc(), [_r()], "Aceptada", "ok"),
        (_doc(action="parcial"), [_r(message_code="2")], "Parcial", "warn"),
        (_doc(action="rechazada"), [_r(message_code="3")], "Rechazada", "bad"),
        (_doc(status="historico"), [_r(status="historico")], "Aceptada", "ok"),
        (_doc(status="por_revisar", action=None, deadline=HOY + timedelta(days=14)), [], "Pendiente", "warn"),
        (_doc(status="por_revisar", action=None, deadline=HOY + timedelta(days=1)), [], "Pendiente", "bad"),
        (_doc(status="por_revisar", action=None, deadline=HOY - timedelta(days=1)), [], "Vencida sin responder", "bad"),
        (_doc(status="por_revisar", action=None, deadline=HOY + timedelta(days=14)), [_r(status="rechazado")], "Pendiente · se responde de nuevo", "warn"),
        (_doc(status="sin_respuesta", doc_type="TE", action=None), [], "No aplica: tiquete", "muted"),
        (_doc(status="ajeno", action=None), [], "No aplica: no es para la empresa", "muted"),
        (_doc(status="invalido", action=None), [], "No se responde: Hacienda rechazó la factura al proveedor", "bad"),
        (_doc(status="descartado", action=None), [], "Descartado", "muted"),
    ],
)
def test_accion(doc, rs, label, tone):
    a = st.accion(doc, rs, HOY)
    assert (a["label"], a["tone"]) == (label, tone)


def test_accion_historico_y_alerta_del_proveedor():
    assert st.accion(_doc(status="historico"), [], HOY)["hint"] == "Respondida en Fygaro"
    a = st.accion(_doc(supplier_hacienda_status="rechazado"), [_r()], HOY)
    assert a["alerta"] and "no da crédito" in a["alerta"]
    assert st.accion(_doc(status="por_revisar", action=None, deadline=HOY + timedelta(days=14)), [], HOY)["hint"].startswith("Quedan")


# ---------------------------------------------------------------- con la API
FECHA = f"{date.today().isoformat()}T10:00:00-06:00"
DDMMAA = date.today().strftime("%d%m%y")


def cl(n: int) -> str:
    return clave(fecha=DDMMAA, consecutivo=f"0010000101{n:010d}")


def subir(client, n: int, total: int = 1130, iva: int = 130) -> dict:
    xml = fe_xml(cl(n), fecha=FECHA, consecutivo=f"0010000101{n:010d}", total=total, iva=iva).encode()
    r = client.post("/reception/upload", files={"file": ("f.xml", xml, "application/xml")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def sin_red(monkeypatch):
    """send_response nunca sale: registra lo que se habria enviado."""
    from app.services import hacienda_sync

    sent: list[int] = []

    def fake_send(db, response_id):
        sent.append(response_id)
        return "por_enviar"

    monkeypatch.setattr(hacienda_sync, "send_response", fake_send)
    return sent


def test_en_pruebas_se_ensaya_sin_enviar_y_el_gasto_se_crea(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import Expense, ReceptionResponse

    modo_pruebas(client, *p12_empresa)
    d = subir(client, 801)
    r = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert sin_red == []  # nada se manda a Hacienda
    assert out["hacienda_status"] == "ensayo" and out["notificacion"]["key"] == "solo_pruebas" and out["notificacion"]["tone"] == "warn"
    assert out["accion"]["label"] == "Aceptada" and out["expense_id"]
    assert any("No se envió a Hacienda porque Crimson está en Pruebas" in w for w in out["warnings"])
    resp = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"]).one()
    assert resp.status == "ensayo" and resp.mode == "pruebas" and resp.hacienda_xml is None and resp.xml_signed
    assert resp.consecutive[8:10] == "05"
    e = db_session.get(Expense, out["expense_id"])
    assert e.reference == cl(801) and float(e.total) == 1130
    # la descarga dice que es un ensayo; sin respuesta de Hacienda
    f = client.get(f"/reception/{d['id']}/file/mr")
    assert f.status_code == 200 and "ENSAYO-sin-validez" in f.headers["content-disposition"]
    assert client.get(f"/reception/{d['id']}/file/hacienda").status_code == 404
    # en Pruebas se puede cambiar la decision (no le cuesta nada a Hacienda)
    assert out["can_retry"]
    assert client.post(f"/reception/{d['id']}/retry").json()["status"] == "por_revisar"


def test_aprobar_en_lote_en_pruebas_no_envia(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    modo_pruebas(client, *p12_empresa)
    ids = [subir(client, n)["id"] for n in (802, 803)]
    r = client.post("/reception/approve-batch", json={"ids": ids}).json()
    assert len(r["aprobadas"]) + len(r["omitidas"]) == 2
    assert all(a["hacienda_status"] == "ensayo" for a in r["aprobadas"])
    assert sin_red == []


def test_resumen_no_cuenta_los_de_pruebas_como_rechazos(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import ReceptionResponse

    modo_pruebas(client, *p12_empresa)
    ids = []
    for n in (811, 812, 813, 814):
        d = subir(client, n)
        client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
        ids.append(d["id"])
    # los 4 viejos: enviados a Pruebas y rechazados con -29
    for r in db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id.in_(ids)):
        r.status, r.hacienda_detail, r.attempts = "rechazado", MINUS29, 1
    db_session.commit()
    s = client.get("/reception/summary").json()
    assert s["mensajes_rechazados"] == 0 and s["errores"] == 0 and s["mode"] == "pruebas"
    assert s["solo_pruebas"]["n"] == 4 and s["solo_pruebas"]["primera_fecha_limite"]
    page = client.get("/reception/documents?pend_prod=1").json()
    assert page["total"] == 4 and page["counts"]["solo_pruebas"] == 4
    assert all(it["notificacion"]["key"] == "solo_pruebas" for it in page["items"])


def test_solo_pruebas_que_vence_pronto_enciende_el_menu(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import ReceivedDocument

    modo_pruebas(client, *p12_empresa)
    d = subir(client, 815)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    db_session.get(ReceivedDocument, d["id"]).deadline = date.today()
    db_session.commit()
    s = client.get("/reception/summary").json()
    assert s["solo_pruebas"]["vencen_pronto"] == 1


def _produccion(monkeypatch):
    from app.services import hacienda_config

    monkeypatch.setattr(hacienda_config, "reception_mode", lambda db, t: "produccion")


def test_enviar_en_produccion_la_misma_decision(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import AuditLog, Expense, ReceptionResponse

    modo_pruebas(client, *p12_empresa)
    d = subir(client, 821)
    eid = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"}).json()["expense_id"]
    _produccion(monkeypatch)
    det = client.get(f"/reception/{d['id']}").json()
    assert det["can_send_production"] and not det["can_retry"]  # en Produccion lo de Pruebas va por «Enviar en Producción»
    assert client.post(f"/reception/{d['id']}/retry").status_code == 409
    assert client.post(f"/reception/{d['id']}/send-production", json={}).status_code == 422  # sin la casilla
    r = client.post(f"/reception/{d['id']}/send-production", json={"confirm_not_in_fygaro": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [x["mode"] for x in out["responses"]] == ["pruebas", "produccion"]
    nuevo = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"], ReceptionResponse.attempt == 2).one()
    assert nuevo.mode == "produccion" and nuevo.status == "por_enviar" and nuevo.xml_signed is None  # lo firma el .p12 de produccion
    assert nuevo.message_code == "1" and nuevo.iva_condition_code == "01" and nuevo.consecutive[8:10] == "05"
    assert sin_red == [nuevo.id]
    assert out["expense_id"] == eid and db_session.query(Expense).filter(Expense.received_document_id == d["id"]).count() == 1
    assert out["notificacion"]["key"] == "en_proceso"
    assert db_session.query(AuditLog).filter(AuditLog.action == "reception_send_production").count() == 1
    # ya tiene un mensaje de produccion: no se envia otra vez
    assert client.post(f"/reception/{d['id']}/send-production", json={"confirm_not_in_fygaro": True}).status_code == 409
    # descargar: el mensaje de produccion aun sin firmar -> no se baja el ensayo de Pruebas como si fuera el vigente
    assert client.get(f"/reception/{d['id']}/file/mr").status_code == 404
    assert client.get(f"/reception/{d['id']}/file/hacienda").status_code == 404
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/reception/{d['id']}/file/todo").content))
    assert not any("mensaje-receptor" in n or "respuesta-hacienda" in n for n in z.namelist())
    assert out["has_hacienda_answer"] is False and out["has_response"] is False


def test_enviar_en_produccion_bloqueos(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import ReceivedDocument

    modo_pruebas(client, *p12_empresa)
    d = subir(client, 822)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    # en Pruebas no hay «Enviar en Producción»
    assert client.post(f"/reception/{d['id']}/send-production", json={"confirm_not_in_fygaro": True}).status_code == 409
    _produccion(monkeypatch)
    doc = db_session.get(ReceivedDocument, d["id"])
    doc.deadline = date.today() - timedelta(days=1)
    db_session.commit()
    r = client.post(f"/reception/{d['id']}/send-production", json={"confirm_not_in_fygaro": True})
    assert r.status_code == 409 and "plazo" in r.json()["detail"]
    assert sin_red == []
    # en lote: cada uno por su cuenta; la casilla es obligatoria
    assert client.post("/reception/send-production", json={"ids": [d["id"]]}).status_code == 422
    r = client.post("/reception/send-production", json={"ids": [d["id"]], "confirm_not_in_fygaro": True}).json()
    assert r["enviadas"] == [] and r["omitidas"][0]["id"] == d["id"]


def test_enviar_en_produccion_con_actividad_inactiva_da_422(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import ReceivedDocument, ReceptionResponse

    modo_pruebas(client, *p12_empresa)
    d = subir(client, 823)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    db_session.get(ReceivedDocument, d["id"]).activity_code = "9999.9"
    db_session.commit()
    _produccion(monkeypatch)
    r = client.post(f"/reception/{d['id']}/send-production", json={"confirm_not_in_fygaro": True})
    assert r.status_code == 422 and "no está activa" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(ReceivedDocument, d["id"]).status == "respondido"  # se deshizo
    assert db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"]).count() == 1


def _prod_error(db_session, rid: int, **kw):
    from app.models import ReceptionResponse

    r = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == rid).order_by(ReceptionResponse.attempt.desc()).first()
    r.mode, r.status, r.hacienda_xml = "produccion", "error", None  # el simulado trae su respuesta simulada
    for k, v in kw.items():
        setattr(r, k, v)
    db_session.commit()
    return r


def test_ya_recibido_en_el_primer_envio_no_se_responde_de_nuevo(client, auth, db_session, monkeypatch, sin_red):
    d = subir(client, 831)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    _prod_error(db_session, d["id"], attempts=1, last_error="Hacienda ya tiene un documento con esa clave: ¿se respondió o emitió desde Fygaro?")
    _produccion(monkeypatch)
    det = client.get(f"/reception/{d['id']}").json()
    assert not det["can_retry"] and det["notificacion"]["key"] == "ya_recibido"
    r = client.post(f"/reception/{d['id']}/retry")
    assert r.status_code == 409 and "Fygaro" in r.json()["detail"]
    assert client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada"}).status_code == 409


def test_un_400_del_envio_deja_responder_de_nuevo_sin_consultar(client, auth, db_session, monkeypatch, sin_red):
    from app.services import hacienda_sync

    monkeypatch.setattr(hacienda_sync, "verify_response", lambda db, r: pytest.fail("no se consulta"))
    d = subir(client, 832)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    _prod_error(db_session, d["id"], attempts=1, last_error="Hacienda rechazó el envío")
    _produccion(monkeypatch)
    assert client.post(f"/reception/{d['id']}/retry").status_code == 200


@pytest.mark.parametrize("kind,code", [("not_found", 200), ("rechazado", 200), ("aceptado", 409), ("sin_conexion", 409)])
def test_error_ambiguo_se_consulta_antes_de_responder(client, auth, db_session, monkeypatch, sin_red, kind, code):
    from app.services import hacienda_sync

    seen = []

    def fake_verify(db, r):
        seen.append(r.id)
        if kind == "aceptado":
            r.status = "aceptado"
        return kind

    monkeypatch.setattr(hacienda_sync, "verify_response", fake_verify)
    d = subir(client, 840 + ["not_found", "rechazado", "aceptado", "sin_conexion"].index(kind))
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    r = _prod_error(db_session, d["id"], attempts=2, last_error="Hacienda no encuentra el documento")
    _produccion(monkeypatch)
    assert client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada"}).status_code == 409  # primero se consulta
    res = client.post(f"/reception/{d['id']}/retry")
    assert res.status_code == code and seen == [r.id]
    if kind == "aceptado":
        db_session.refresh(r)
        assert r.status == "aceptado"


def test_verify_response_consulta_sin_importar_el_estado(client, auth, db_session, monkeypatch):
    from app.models import ReceptionResponse
    from app.providers.hacienda.client import StatusResult
    from app.services import hacienda_sync

    d = subir(client, 850)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    r = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"]).one()
    r.mode, r.status, r.attempts, r.last_error, r.hacienda_xml = "produccion", "error", 2, "Hacienda no encuentra el documento", None
    db_session.commit()
    asked = []

    class Prov:
        def get_status(self, ref):
            asked.append(ref)
            return StatusResult(kind="not_found")

    monkeypatch.setattr(hacienda_sync, "_provider", lambda db, t, m: (Prov(), None, None))
    assert hacienda_sync.verify_response(db_session, r) == "not_found"
    assert asked == [f"{cl(850)}-{r.consecutive}"] and r.status == "error"  # not_found no lo toca


def test_exportacion_usa_las_etiquetas_de_fygaro(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    modo_pruebas(client, *p12_empresa)
    d = subir(client, 860)
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    from app.models import ReceivedDocument, Tenant
    from app.services import reports

    t = db_session.get(Tenant, auth["tenant"]["id"])
    doc = db_session.get(ReceivedDocument, d["id"])
    a = st.accion(doc, [], date.today())
    assert a["label"] == "Aceptada"
    assert hasattr(reports, "reception_rows_labels")
    labels = reports.reception_rows_labels(db_session, t.id, [doc])
    assert labels[doc.id] == ("Aceptada", "Pendiente · solo en Pruebas")


def test_iva_del_mes_no_saca_los_de_pruebas_y_si_los_rechazos_de_produccion(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import ReceptionResponse, Tenant
    from app.services import hacienda_tasks, reports

    modo_pruebas(client, *p12_empresa)
    a = subir(client, 871)  # 1 130 con 130 de IVA
    b = subir(client, 872)
    for d in (a, b):
        client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    ra = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == a["id"]).one()
    ra.status, ra.hacienda_detail, ra.attempts = "rechazado", MINUS29, 1  # el -29 de Pruebas
    rb = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == b["id"]).one()
    rb.mode, rb.status, rb.hacienda_detail, rb.attempts = "produccion", "rechazado", "Rechazado por la Hacienda real", 1
    from datetime import UTC, datetime

    ra.resolved_at = rb.resolved_at = datetime.now(UTC)
    db_session.commit()
    hoy = date.today()
    x = reports.iva_breakdown(db_session, auth["tenant"]["id"], hoy.replace(day=1), hoy)
    assert float(x["credito_xml_crc"]) == 130 and float(x["rechazado_produccion_crc"]) == 130
    assert [r["id"] for r in x["falta_respuesta_produccion"]] == [a["id"]] and [r["id"] for r in x["rechazado_produccion"]] == [b["id"]]
    t = db_session.get(Tenant, auth["tenant"]["id"])
    alertas = hacienda_tasks.alerts_for(db_session, t)
    assert sum(1 for s in alertas if s.startswith("Hacienda rechazó el mensaje receptor")) == 1  # solo el de Produccion
    s = client.get("/reception/summary").json()
    assert s["mensajes_rechazados"] == 1 and s["solo_pruebas"]["n"] == 1


def test_gasto_vinculado_a_un_menos_29_sale_en_ambar(client, auth, db_session, monkeypatch, p12_empresa, sin_red):
    from app.models import ReceptionResponse

    modo_pruebas(client, *p12_empresa)
    d = subir(client, 881)
    eid = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"}).json()["expense_id"]
    r = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"]).one()
    r.status, r.hacienda_detail, r.attempts = "rechazado", MINUS29, 1
    db_session.commit()
    g = next(x for x in client.get("/expenses").json() if x["id"] == eid)
    h = g.get("hacienda") or {}
    assert h["tone"] == "warn" and "solo en Pruebas" in h["label"] and h["clave"] == cl(881)
