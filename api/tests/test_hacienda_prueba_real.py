"""«Prueba real con Hacienda» (05/11): FE de 1 000 colones + IVA de la empresa a si misma y su mensaje receptor en el
ambiente de PRUEBAS, sin cambiar el modo, sin Invoice ni EInvoiceDocument y con T01/T05. Todo contra FakeHacienda y con
el CABYS en monkeypatch: nada sale a la red y produccion jamas se toca."""

import base64
import logging
from datetime import UTC, datetime, timedelta

import pytest

from tests.hacienda_utils import ADMIN_PASSWORD, FakeHacienda, load_credentials, make_p12, role_headers

URL = "/settings/hacienda/prueba-real"
CABYS = "8399000000000"  # servicio (empieza con 8): ficticio, find_code va con monkeypatch
USER, PASS = "usuario-pruebas@ejemplo", "Clave-ATV-de-prueba!"


class Reloj:
    def __init__(self):
        self.t = datetime.now(UTC)

    def __call__(self):
        return self.t

    def avanza(self, s: float):
        self.t += timedelta(seconds=s)


@pytest.fixture
def reloj(monkeypatch):
    from app.services import hacienda_prueba as hp

    r = Reloj()
    monkeypatch.setattr(hp, "_now", r)
    return r


@pytest.fixture
def cabys_ok(monkeypatch):
    from app.services import cabys

    def find(code):
        return {"code": code, "description": "Servicio de prueba", "tax_rate": 13.0, "categories": []}

    monkeypatch.setattr(cabys, "find_code", find)
    return find


def _body(**kw):
    return {"admin_password": ADMIN_PASSWORD, "cabys": CABYS, "con_mensaje_receptor": True, "confirmo": True, **kw}


def _grupos(db_session, tid):
    from app.models import BillingGroup

    return {g.doc_type: g.current for g in db_session.query(BillingGroup).filter(BillingGroup.tenant_id == tid)}


def _listo(client, monkeypatch, p12, **fake_kw):
    fake = FakeHacienda(sequence=["aceptado"], **fake_kw).install(monkeypatch)
    assert load_credentials(client, *p12).status_code == 200
    return fake


def _consultar(client, rid, reloj, s=5):
    reloj.avanza(s)
    r = client.post(f"{URL}/{rid}/consultar")
    assert r.status_code == 200, r.text
    return r.json()


def test_punta_a_punta_aceptado_con_mr_y_sin_tocar_ventas(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    from app.models import EInvoiceDocument, Invoice, Tenant
    from app.services import hacienda_config as hc

    fake = _listo(client, monkeypatch, p12_empresa)
    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    modo, prov = hc.hacienda_mode(db_session, t), (t.settings or {}).get("einvoice_provider")
    antes = _grupos(db_session, tid)
    facturas, docs = db_session.query(Invoice).count(), db_session.query(EInvoiceDocument).count()
    from app.models import EmailOutbox

    correos = db_session.query(EmailOutbox).count()
    r = client.post(URL, json=_body())
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "enviado" and run["live"] and run["clave"] and len(run["clave"]) == 50
    assert run["created_by_name"] and "fe_xml" not in run and run["has_fe_xml"]
    v = _consultar(client, run["id"], reloj)
    assert v["state"] == "aceptado" and v["fe_estado"] == "aceptado" and v["mr_estado"] == "enviado" and v["live"]
    v = _consultar(client, run["id"], reloj)
    assert v["mr_estado"] == "aceptado" and not v["live"] and v["finished_at"]
    assert len(fake.posts) == 2 and fake.production_hits == []
    # nada de ventas: ni modo, ni proveedor, ni F0x, ni facturas, ni documentos fiscales, ni correos
    db_session.refresh(t)
    assert hc.hacienda_mode(db_session, t) == modo and (t.settings or {}).get("einvoice_provider") == prov
    despues = _grupos(db_session, tid)
    assert {k for k in despues if despues[k] != antes.get(k)} == {"T01", "T05"}
    assert db_session.query(Invoice).count() == facturas and db_session.query(EInvoiceDocument).count() == docs
    assert db_session.query(EmailOutbox).count() == correos
    # la prueba aceptada no cuenta para produccion
    from app.models import FiscalCredential

    row = db_session.query(FiscalCredential).filter(FiscalCredential.environment == "pruebas").one()
    assert row.last_test_at is None and hc.profile(db_session, t).consecutives_confirmed_at is None
    # descargas
    fe = client.get(f"{URL}/{run['id']}/xml/fe")
    assert fe.status_code == 200 and fe.content == fake.bodies()[0] and "prueba-fe.xml" in fe.headers["content-disposition"]
    assert client.get(f"{URL}/{run['id']}/xml/fe-respuesta").status_code == 200
    assert client.get(f"{URL}/{run['id']}/xml/mr").content == fake.bodies()[1]
    assert client.get(f"{URL}/{run['id']}/xml/otra").status_code == 404


def test_mr_con_los_montos_de_la_fe(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    from app.providers.hacienda import xml44

    fake = _listo(client, monkeypatch, p12_empresa)
    run = client.post(URL, json=_body()).json()
    v = _consultar(client, run["id"], reloj)
    fe = xml44.parse_xml_bytes(fake.bodies()[0])
    mr = xml44.parse_xml_bytes(fake.bodies()[1])
    assert xml44.text(fe, "ResumenFactura", "TotalComprobante") == xml44.text(mr, "TotalFactura") == "1130.00000"
    assert xml44.text(fe, "ResumenFactura", "TotalImpuesto") == xml44.text(mr, "MontoTotalImpuesto") == "130.00000"
    assert xml44.text(fe, "ResumenFactura", "TotalServGravados") == "1000.00000"  # unidad Sp: servicio
    assert fake.posts[1]["consecutivoReceptor"] == v["mr_consecutive"] and v["mr_consecutive"][8:10] == "05"
    assert xml44.text(mr, "Clave") == run["clave"]


def test_rechazado_con_el_motivo(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    run = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    fake.final[run["clave"]] = "rechazado"
    v = _consultar(client, run["id"], reloj)
    assert v["state"] == "rechazado" and "rechazado por la prueba" in v["fe_detalle"] and not v["live"]
    assert len(fake.posts) == 1


def test_403_del_gateway_termina_en_error(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa, gateway_403=True)
    run = client.post(URL, json=_body()).json()
    assert run["state"] == "error" and "entrada de Hacienda" in run["fe_post_cause"] and not run["live"]
    assert len(fake.posts) == 1 and fake.production_hits == []


def test_timeout_perdido_reenvia_los_mismos_bytes_una_vez(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    fake.post_plan = ["timeout_lost"]
    run = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    assert run["state"] == "firmado" and run["live"]
    v = _consultar(client, run["id"], reloj, 10)  # antes de 30 s: no se reenvia
    assert v["state"] == "firmado" and len(fake.posts) == 1
    v = _consultar(client, run["id"], reloj, 25)
    assert v["state"] == "enviado" and v["fe_resent"] and len(fake.posts) == 2
    b = fake.bodies()
    assert b[0] == b[1]
    v = _consultar(client, run["id"], reloj)
    assert v["state"] == "aceptado" and len(fake.posts) == 2


def test_timeout_que_si_llego_y_reenvio_ya_recibido(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa, not_found_style="400")
    fake.post_plan = ["timeout_lost"]
    run = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    fake.known[run["clave"]] = 0  # si llego, pero Hacienda todavia no lo indexa
    fake.get_plan[run["clave"]] = [(400, {"X-Error-Cause": f"El comprobante {run['clave']} no ha sido recibido."})]
    fake.post_plan = [(400, {"X-Error-Cause": "El comprobante ya fue recibido anteriormente"})]
    v = _consultar(client, run["id"], reloj, 31)
    assert v["state"] == "enviado" and v["fe_resent"] and len(fake.posts) == 2
    v = _consultar(client, run["id"], reloj)
    assert v["state"] == "aceptado"


def test_ya_recibido_en_el_primer_envio_no_concluyente(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    fake.post_plan = [(400, {"X-Error-Cause": "El comprobante ya fue recibido anteriormente"})]
    run = client.post(URL, json=_body()).json()
    assert run["state"] == "no_concluyente" and not run["live"] and len(fake.posts) == 1


def test_contrasena_y_permisos(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    r = client.post(URL, json=_body(admin_password=None))
    assert r.status_code == 403 and r.json()["detail"] == "Confirme con su contraseña de administrador"
    assert client.post(URL, json=_body(admin_password="mala")).status_code == 403
    assert client.post(URL, json=_body(confirmo=False)).status_code == 422
    assert fake.posts == []
    run = client.post(URL, json=_body()).json()
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.prueba@ejemplo.com", "contabilidad")
    lista = client.get(URL, headers=conta)
    assert lista.status_code == 200 and lista.json()["runs"][0]["id"] == run["id"] and not lista.json()["can_run"]
    assert client.get(f"{URL}/{run['id']}", headers=conta).status_code == 200
    assert client.post(URL, json=_body(), headers=conta).status_code == 403
    assert client.post(f"{URL}/{run['id']}/consultar", headers=conta).status_code == 403
    assert client.get(f"{URL}/{run['id']}/xml/fe", headers=conta).status_code == 403
    ventas = role_headers(client, db_session, auth["tenant"]["id"], "ventas.prueba@ejemplo.com", "ventas")
    assert client.get(URL, headers=ventas).status_code == 403


def test_bloqueos_produccion_viva_y_tope(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    from app.models import FiscalProfile, HaciendaTestRun, Tenant

    fake = _listo(client, monkeypatch, p12_empresa)
    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    # produccion encendida (aunque el servidor la bloquee): 409 y nada
    prof = db_session.query(FiscalProfile).one()
    t.settings = {**(t.settings or {}), "einvoice_provider": "hacienda"}
    prof.environment, prof.production_enabled_at = "produccion", datetime.now(UTC)
    db_session.commit()
    r = client.post(URL, json=_body())
    assert r.status_code == 409 and "Producción" in r.json()["detail"] and fake.posts == []
    assert client.get(URL).json()["can_run"] is False
    prof.environment, prof.production_enabled_at = "pruebas", None
    t.settings = {**(t.settings or {}), "einvoice_provider": "none"}
    db_session.commit()
    # una corrida viva puesta a mano: 409 y el T01 no se mueve
    viva = HaciendaTestRun(tenant_id=tid, environment="pruebas", state="enviado", amount=1130, con_mr=False, fe_resent=False, polls=0, created_at=reloj(), fe_body_sent_at=reloj())
    db_session.add(viva)
    db_session.commit()
    antes = _grupos(db_session, tid)["T01"]
    r = client.post(URL, json=_body())
    assert r.status_code == 409 and "en curso" in r.json()["detail"]
    assert _grupos(db_session, tid)["T01"] == antes and fake.posts == []
    viva.state = "aceptado"
    db_session.commit()
    # tope: 3 por hora (la viva cuenta como una)
    for _ in range(2):
        reloj.avanza(1)
        r = client.post(URL, json=_body(con_mensaje_receptor=False))
        assert r.status_code == 200, r.text
        fake.final[r.json()["clave"]] = "aceptado"
        _consultar(client, r.json()["id"], reloj)
    r = client.post(URL, json=_body())
    assert r.status_code == 429 and "hora" in r.json()["detail"]


def test_viva_vencida_pasa_a_sin_respuesta_y_libera(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    fake.post_plan = ["timeout_lost"]
    run = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    assert run["live"]
    reloj.avanza(16 * 60)
    lista = client.get(URL).json()
    assert lista["runs"][0]["state"] == "sin_respuesta" and not lista["runs"][0]["live"] and lista["can_run"]
    r = client.post(URL, json=_body(con_mensaje_receptor=False))
    assert r.status_code == 200 and r.json()["state"] == "enviado"
    # una respuesta tardia la pasa a su estado final (sin volver a viva)
    fake.known[run["clave"]] = 0
    v = _consultar(client, run["id"], reloj)
    assert v["state"] == "aceptado"


def test_dos_consultas_un_solo_mr(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    run = client.post(URL, json=_body()).json()
    _consultar(client, run["id"], reloj)
    _consultar(client, run["id"], reloj, 0.5)  # antes de 4 s: no llama
    _consultar(client, run["id"], reloj, 5)  # el MR ya salio: solo consulta
    assert len(fake.posts) == 2


def test_consulta_antes_de_4_s_no_llama(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    run = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    _consultar(client, run["id"], reloj)
    n = len(fake.gets)
    v = _consultar(client, run["id"], reloj, 1)
    assert len(fake.gets) == n and v["state"] == "aceptado"
    # con una viva: la segunda consulta seguida tampoco llama
    fake.post_plan = ["timeout_lost"]
    run2 = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    _consultar(client, run2["id"], reloj, 5)
    n = len(fake.gets)
    v = _consultar(client, run2["id"], reloj, 2)
    assert len(fake.gets) == n and v["state"] == "firmado"


def test_otra_empresa_404(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):
    from app.models import HaciendaTestRun, Tenant

    _listo(client, monkeypatch, p12_empresa)
    otra = Tenant(slug="otra-prueba", name="Otra", legal_name="Otra Empresa SA", tax_id="3-101-999999", default_currency="CRC")
    db_session.add(otra)
    db_session.flush()
    ajena = HaciendaTestRun(tenant_id=otra.id, environment="pruebas", state="aceptado", amount=1130, con_mr=False, fe_resent=False, polls=0, fe_xml="<x/>")
    db_session.add(ajena)
    db_session.commit()
    assert client.get(f"{URL}/{ajena.id}").status_code == 404
    assert client.post(f"{URL}/{ajena.id}/consultar").status_code == 404
    assert client.get(f"{URL}/{ajena.id}/xml/fe").status_code == 404


@pytest.mark.parametrize(
    "caso",
    ["cabys_bien", "cabys_inexistente", "cabys_iva4", "tel7", "tel506", "correo_doble", "cert_otra", "perfil"],
)
def test_requisitos_que_frenan_sin_enviar(caso, client, auth, db_session, monkeypatch, p12_empresa, reloj):
    from app.models import FiscalProfile
    from app.services import cabys

    monkeypatch.setattr(cabys, "find_code", lambda code: {"code": code, "description": "x", "tax_rate": 13.0})
    fake = FakeHacienda().install(monkeypatch)
    p12, pin = p12_empresa
    if caso == "cert_otra":
        otra, pin_o = make_p12("3101999999")
        assert load_credentials(client, otra, pin_o, allow_other_id="true").status_code == 200
    else:
        assert load_credentials(client, p12, pin).status_code == 200
    prof = db_session.query(FiscalProfile).one()
    cab = CABYS
    if caso == "cabys_bien":
        cab = "4321000000000"
    elif caso == "cabys_inexistente":
        monkeypatch.setattr(cabys, "find_code", lambda code: None)
    elif caso == "cabys_iva4":
        monkeypatch.setattr(cabys, "find_code", lambda code: {"code": code, "description": "x", "tax_rate": 4.0})
    elif caso == "tel7":
        prof.phone = "2222333"
    elif caso == "tel506":
        prof.phone = "50688887777"
    elif caso == "correo_doble":
        prof.email = "a@x.com;b@x.com"
    elif caso == "perfil":
        prof.other_signs = None
    db_session.commit()
    t01 = _grupos(db_session, auth["tenant"]["id"])["T01"]
    r = client.post(URL, json=_body(cabys=cab))
    assert r.status_code == 422, (caso, r.text)
    assert any(s["status"] == "falla" for s in r.json()["steps"])
    assert fake.posts == [] and fake.production_hits == []
    assert _grupos(db_session, auth["tenant"]["id"])["T01"] == t01


def test_cabys_sin_respuesta_avisa_y_envia(client, auth, monkeypatch, p12_empresa, reloj):
    from app.services import cabys

    def caido(code):
        raise cabys.CabysUnavailable("sin red")

    monkeypatch.setattr(cabys, "find_code", caido)
    fake = _listo(client, monkeypatch, p12_empresa)
    r = client.post(URL, json=_body())
    assert r.status_code == 200, r.text
    paso = next(s for s in r.json()["steps"] if s["label"] == "CABYS")
    assert paso["status"] == "aviso" and len(fake.posts) == 1


def test_sin_secretos_en_respuesta_logs_ni_bitacora(client, auth, db_session, monkeypatch, p12_empresa, reloj, cabys_ok, caplog):
    from app.models import AuditLog

    caplog.set_level(logging.DEBUG)
    p12, pin = p12_empresa
    _listo(client, monkeypatch, p12_empresa)
    r = client.post(URL, json=_body())
    v = _consultar(client, r.json()["id"], reloj)
    textos = r.text + str(v) + client.get(URL).text + caplog.text
    textos += " ".join(str(a.diff) for a in db_session.query(AuditLog).filter(AuditLog.action.like("hacienda_test%")))
    for secreto in (USER, PASS, pin, base64.b64encode(p12).decode()[:40], "tok-1"):
        assert secreto not in textos
    acciones = {a.action for a in db_session.query(AuditLog).filter(AuditLog.action.like("hacienda_test_%"))}
    assert {"hacienda_test_send", "hacienda_test_result"} <= acciones
    assert "<FacturaElectronica" not in " ".join(str(a.diff) for a in db_session.query(AuditLog))


def test_modelo_rechaza_monto_y_ambiente(db_session, auth):
    from sqlalchemy.exc import IntegrityError

    from app.models import HaciendaTestRun

    tid = auth["tenant"]["id"]
    for kw in ({"amount": 2001, "environment": "pruebas"}, {"amount": 1130, "environment": "produccion"}):
        db_session.add(HaciendaTestRun(tenant_id=tid, state="aceptado", con_mr=False, fe_resent=False, polls=0, **kw))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


def test_token_rechazado_en_consulta_no_da_500(client, auth, monkeypatch, p12_empresa, reloj, cabys_ok):
    fake = _listo(client, monkeypatch, p12_empresa)
    run = client.post(URL, json=_body(con_mensaje_receptor=False)).json()
    fake.get_plan[run["clave"]] = [401, 401]
    v = _consultar(client, run["id"], reloj)
    assert v["state"] == "error" and "Probar conexión" in v["fe_post_cause"]
