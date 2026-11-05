"""Cliente del IdP y la API de Hacienda + envio/consulta (hacienda_sync) contra FakeHacienda. Ninguna prueba sale a la
red; produccion jamas se toca sin las tres llaves del servidor."""

import base64
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.providers.hacienda.client import HaciendaAuthError, HaciendaClient, ProductionBlocked
from tests.hacienda_utils import FakeHacienda, clave, fe_xml, mensaje_hacienda, modo_pruebas

HOY = date.today()
USER, PASS = "usuario-pruebas@ejemplo", "Clave-ATV-de-prueba!"


@pytest.fixture(autouse=True)
def _envio_a_pruebas(monkeypatch):
    """Estas pruebas ejercitan el envio y la consulta en el ambiente de Pruebas: hoy la recepcion en Pruebas solo se
    ensaya (services/reception.PRUEBAS_ENSAYO), asi que aqui se vuelve a enviar."""
    from app.services import reception as rsvc

    monkeypatch.setattr(rsvc, "PRUEBAS_ENSAYO", False)


def cl(n: int) -> str:
    return clave(fecha=HOY.strftime("%d%m%y"), consecutivo=f"0010000101{n:010d}")


def _client(fake, tenant_id=1):
    return HaciendaClient("pruebas", USER, PASS, tenant_id=tenant_id, http=httpx.Client(transport=httpx.MockTransport(fake.handler)))


def test_token_cache_refresh_y_error_sin_secretos(monkeypatch):
    from app.providers.hacienda import client as hclient

    fake = FakeHacienda()
    c = _client(fake)
    assert c.token() == "tok-1" and c.token() == "tok-1"  # cache: una sola peticion
    assert len(fake.tokens) == 1 and fake.tokens[0]["grant_type"] == "password" and fake.tokens[0]["client_id"] == "api-stag"
    assert c.ep["token"].startswith("https://") and "rut-stag" in c.ep["token"]
    # el access vence: se renueva con refresh_token
    key = c._key()
    hclient._cache[key]["exp"] = 0
    assert c.token() == "tok-2" and fake.tokens[-1]["grant_type"] == "refresh_token"
    malo = HaciendaClient("pruebas", USER, "otra-clave", tenant_id=99, http=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    with pytest.raises(HaciendaAuthError) as e:
        malo.token()
    assert USER not in str(e.value) and "otra-clave" not in str(e.value) and "pruebas" in str(e.value)


def test_post_y_get_variantes():
    fake = FakeHacienda()
    c = _client(fake)
    body = {"clave": cl(1), "fecha": "2026-10-14T10:00:00-06:00", "emisor": {}, "comprobanteXml": "eA=="}
    fake.post_plan = [(201, {"Location": "https://x/1"}), (202, {"Location": "https://x/2"}), (400, {"X-Error-Cause": "El comprobante YA FUE RECIBIDO"})]
    fake.post_plan += [(400, {"X-Error-Cause": "Firma invalida"}), (403, {}), (429, {"Retry-After": "120"}), (503, {})]
    assert c.post_recepcion(body).kind == "ok" and c.post_recepcion(body).location == "https://x/2"
    assert c.post_recepcion(body).kind == "already_received"
    r = c.post_recepcion(body)
    assert r.kind == "rejected" and r.cause == "Firma invalida"
    assert "negó el acceso" in c.post_recepcion(body).cause
    r = c.post_recepcion(body)
    assert r.kind == "retry" and r.retry_after == 120
    assert c.post_recepcion(body).kind == "retry"
    fake.post_plan = ["timeout_lost"]
    assert c.post_recepcion(body).kind == "retry"
    # GET con ind-estado / indEstado y respuesta en base64
    fake.get_plan["A"] = ["procesando"]
    assert c.get_estado("A").kind == "procesando"
    fake.known["B"] = 0
    fake.final["B"] = "rechazado"
    st = c.get_estado("B")
    assert st.kind == "rechazado" and "MensajeHacienda" in st.xml and st.detail == "Este comprobante fue rechazado por la prueba"
    assert c.get_estado("NO-EXISTE").kind == "not_found"

    def camel(request):
        if "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        xml = base64.b64encode(mensaje_hacienda(cl(2)).encode()).decode()
        return httpx.Response(200, json={"indEstado": "aceptado", "respuestaXml": xml})

    c2 = HaciendaClient("pruebas", USER, PASS, tenant_id=5, http=httpx.Client(transport=httpx.MockTransport(camel)))
    st = c2.get_estado("X")
    assert st.kind == "aceptado" and st.detail == "Aceptado"


def test_produccion_bloqueada_sin_las_tres_llaves(monkeypatch):
    from app.core.config import settings
    from app.providers.hacienda.client import instance_fingerprint

    fake = FakeHacienda()
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    with pytest.raises(ProductionBlocked):  # ENV=local
        HaciendaClient("produccion", USER, PASS, tenant_id=1, production_instance=instance_fingerprint(), http=http)
    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(ProductionBlocked):  # sin HACIENDA_PRODUCTION_ALLOWED
        HaciendaClient("produccion", USER, PASS, tenant_id=1, production_instance=instance_fingerprint(), http=http)
    monkeypatch.setattr(settings, "hacienda_production_allowed", True)
    with pytest.raises(ProductionBlocked, match="otro servidor"):  # huella distinta (ambiente duplicado en Railway)
        HaciendaClient("produccion", USER, PASS, tenant_id=1, production_instance="0123456789abcdef", http=http)
    HaciendaClient("produccion", USER, PASS, tenant_id=1, production_instance=instance_fingerprint(), http=http)  # con todo, si
    assert fake.production_hits == []


def _por_enviar(client, monkeypatch, p12, n=1):
    from app.models import ReceptionResponse

    fake = FakeHacienda(sequence=["aceptado"]).install(monkeypatch)
    modo_pruebas(client, *p12)
    d = client.post("/reception/upload", files={"file": ("f.xml", fe_xml(cl(n), fecha=f"{HOY}T10:00:00-06:00", consecutivo=f"0010000101{n:010d}").encode(), "application/xml")}).json()
    r = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "send_now": False}).json()
    return fake, d, r["responses"][0]["id"], ReceptionResponse


def test_ya_fue_recibido_primero_y_reintento(client, auth, db_session, monkeypatch, p12_empresa):
    from app.services.hacienda_sync import send_response

    fake, d, rid, RR = _por_enviar(client, monkeypatch, p12_empresa)  # noqa: N806
    fake.post_plan = [(400, {"X-Error-Cause": "El comprobante ya fue recibido anteriormente"})]
    send_response(db_session, rid)
    r = db_session.get(RR, rid)
    assert r.status == "error" and "Fygaro" in r.last_error  # en el primer intento NO es exito
    # timeout que si llego: el reintento manda los mismos bytes y "ya fue recibido" cuenta como enviado
    fake2, d2, rid2, _ = _por_enviar(client, monkeypatch, p12_empresa, n=2)
    fake2.post_plan = ["timeout"]
    send_response(db_session, rid2)
    r2 = db_session.get(RR, rid2)
    assert r2.status == "por_enviar" and r2.attempts == 1 and r2.next_attempt_at is not None and r2.xml_signed
    r2.next_attempt_at = None
    db_session.commit()
    send_response(db_session, rid2)
    db_session.refresh(r2)
    assert r2.status == "enviado" and r2.attempts == 2
    b = fake2.bodies()
    assert len(b) == 2 and b[0] == b[1]  # D6: mismos bytes, no re-firmado


def test_dos_envios_seguidos_un_solo_post_y_atascado(client, auth, db_session, monkeypatch, p12_empresa):
    from app.services.hacienda_sync import poll_status, send_response

    fake, d, rid, RR = _por_enviar(client, monkeypatch, p12_empresa, n=3)  # noqa: N806
    send_response(db_session, rid)
    send_response(db_session, rid)
    assert len(fake.posts) == 1
    # fila atascada en "enviando" (proceso muerto) y Hacienda no la tiene: vuelve a por_enviar con los mismos bytes
    fake2, d2, rid2, _ = _por_enviar(client, monkeypatch, p12_empresa, n=4)
    r = db_session.get(RR, rid2)
    from app.models import ReceivedDocument, Tenant
    from app.providers.hacienda import xml44
    from app.services.einvoice import provider_for

    doc = db_session.get(ReceivedDocument, r.received_document_id)
    t = db_session.get(Tenant, auth["tenant"]["id"])
    prov = provider_for(db_session, t, "recepcion")
    signed = prov.sign_receptor(xml44.build_mensaje_receptor(r, doc, t, xml44.digits(t.tax_id)))
    r.xml_signed = signed.decode()
    r.status, r.claimed_at, r.attempts, r.sent_at = "enviando", datetime.now(UTC) - timedelta(minutes=11), 1, datetime.now(UTC) - timedelta(minutes=11)
    db_session.commit()
    poll_status(db_session)
    db_session.refresh(r)
    assert r.status == "por_enviar" and fake2.gets
    send_response(db_session, rid2)
    db_session.refresh(r)
    assert r.status == "enviado" and fake2.bodies()[-1] == signed


def test_simulado_nunca_llega_al_transporte_y_logs_sin_secretos(client, auth, db_session, caplog, monkeypatch, p12_empresa):
    from app.models import ReceptionResponse
    from app.services.hacienda_sync import send_response

    caplog.set_level(logging.DEBUG)
    d = client.post("/reception/upload", files={"file": ("f.xml", fe_xml(cl(5), fecha=f"{HOY}T10:00:00-06:00", consecutivo="00100001010000000005").encode(), "application/xml")}).json()
    rid = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada"}).json()["responses"][0]["id"]
    r = db_session.get(ReceptionResponse, rid)
    r.status = "por_enviar"  # aunque alguien lo dejara por enviar, un simulado jamas sale
    db_session.commit()
    send_response(db_session, rid)  # el transporte de conftest revienta ante cualquier peticion
    db_session.refresh(r)
    assert r.status == "error"
    fake, d2, rid2, _ = _por_enviar(client, monkeypatch, p12_empresa, n=6)
    send_response(db_session, rid2)
    p12, pin = p12_empresa
    text = caplog.text
    assert PASS not in text and pin not in text and "tok-1" not in text and USER not in text


def test_ratelimit_en_cero_frena_el_resto_del_lote(client, auth, db_session, monkeypatch, p12_empresa):
    from app.services.hacienda_sync import send_pending

    fake, d, rid, RR = _por_enviar(client, monkeypatch, p12_empresa, n=7)  # noqa: N806
    _, d2, rid2, _ = _por_enviar(client, monkeypatch, p12_empresa, n=8)
    fake2 = FakeHacienda(sequence=["aceptado"]).install(monkeypatch)
    fake2.post_plan = [(201, {"Location": "https://x/1", "X-Ratelimit-Remaining": "0"})]
    send_pending(db_session)
    assert len(fake2.posts) == 1  # el segundo espera al siguiente ciclo
    assert {db_session.get(RR, rid).status, db_session.get(RR, rid2).status} == {"enviado", "por_enviar"}


def test_token_que_falla_no_cuenta_como_post(client, auth, db_session, monkeypatch, p12_empresa):
    """§5.7: attempts = POST reales. Contrasena ATV mala (o IdP caido) no manda nada: al corregirla, el primer POST real
    lleva attempts=1 y un "ya fue recibido" sigue siendo error (Fygaro), no exito."""
    from app.providers.hacienda.client import HaciendaClient, HaciendaUnavailable
    from app.services.hacienda_sync import send_response

    fake, d, rid, RR = _por_enviar(client, monkeypatch, p12_empresa, n=9)  # noqa: N806
    fake.fail_token = 1  # el IdP rechaza el usuario/contrasena
    send_response(db_session, rid)
    r = db_session.get(RR, rid)
    assert r.status == "por_enviar" and r.attempts == 0 and fake.posts == [] and "incorrectos" in r.last_error
    # IdP caido: tampoco cuenta
    real = HaciendaClient.token

    def caido(self, force=False):
        raise HaciendaUnavailable("Sin conexión con el servicio de autenticación de Hacienda")

    monkeypatch.setattr(HaciendaClient, "token", caido)
    r.next_attempt_at = None
    db_session.commit()
    send_response(db_session, rid)
    db_session.refresh(r)
    assert r.status == "por_enviar" and r.attempts == 0 and fake.posts == []
    # credenciales corregidas: primer POST real y Hacienda ya lo tiene (respondido desde Fygaro) -> error, no enviado
    monkeypatch.setattr(HaciendaClient, "token", real)
    fake.post_plan = [(400, {"X-Error-Cause": "El comprobante ya fue recibido anteriormente"})]
    r.next_attempt_at = None
    db_session.commit()
    send_response(db_session, rid)
    db_session.refresh(r)
    assert r.status == "error" and r.attempts == 1 and "Fygaro" in r.last_error and len(fake.posts) == 1


# ---------------------------------------------------------------- 05/11: ruta de Pruebas, guarda por host y sonda
def test_is_production_url_por_host_exacto():
    from app.providers.hacienda.endpoints import is_production_url

    for url in (
        "https://api.comprobanteselectronicos.go.cr/recepcion/v1/recepcion",
        "https://api.comprobanteselectronicos.go.cr/recepcion-sandbox/v1/recepcion/123",  # la ruta muerta tambien es ese host
        "https://idp.comprobanteselectronicos.go.cr/auth/realms/rut/protocol/openid-connect/token",
        "https://API.Comprobanteselectronicos.GO.CR/recepcion/v1/",
    ):
        assert is_production_url(url), url
    for url in (
        "https://api-sandbox.comprobanteselectronicos.go.cr/recepcion/v1/recepcion",
        "https://idp.comprobanteselectronicos.go.cr/auth/realms/rut-stag/protocol/openid-connect/token",
        "https://api-sandbox.comprobanteselectronicos.go.cr.evil.example/recepcion/v1/",
        "https://api.hacienda.go.cr/fe/ae",
    ):
        assert not is_production_url(url), url


def test_autocomprobacion_de_endpoints():
    from app.providers.hacienda.endpoints import ENDPOINTS, is_production_url

    for k in ("token", "logout", "api"):
        assert not is_production_url(ENDPOINTS["pruebas"][k])
        assert is_production_url(ENDPOINTS["produccion"][k])
    assert ENDPOINTS["pruebas"]["api"] == "https://api-sandbox.comprobanteselectronicos.go.cr/recepcion/v1/"
    assert FakeHacienda.API == ENDPOINTS["pruebas"]["api"]


def test_guarda_del_cliente_si_pruebas_apunta_a_produccion(monkeypatch):
    from app.providers.hacienda import endpoints

    fake = FakeHacienda()
    monkeypatch.setitem(endpoints.ENDPOINTS["pruebas"], "api", "https://api.comprobanteselectronicos.go.cr/recepcion/v1/")
    with pytest.raises(ProductionBlocked):
        _client(fake)
    assert fake.tokens == [] and fake.posts == [] and fake.gets == [] and fake.production_hits == []


def test_400_no_ha_sido_recibido_sigue_siendo_retry():
    from app.providers.hacienda.client import not_received

    fake = FakeHacienda(not_found_style="400")
    c = _client(fake)
    k = cl(11)
    st = c.get_estado(k)
    assert st.kind == "retry" and st.http_status == 400 and "no ha sido recibido" in st.cause  # sync no cambia
    assert not_received(st, k)
    st2 = c.get_estado(f"{k}-00100001050000000001")
    assert st2.kind == "retry" and not not_received(st2, f"{k}-00100001050000000001")
    from app.providers.hacienda.client import StatusResult

    assert not_received(StatusResult("retry", http_status=400, cause=f"El comprobante {k} no ha sido recibído"), k)
    assert not not_received(StatusResult("retry", http_status=404, cause="no ha sido recibido"), k)
    assert fake.production_hits == []


def test_403_del_gateway_en_consulta_y_envio():
    fake = FakeHacienda(gateway_403=True)
    c = _client(fake)
    st = c.get_estado(cl(12))
    assert st.kind == "retry" and st.gateway and st.http_status == 403 and "entrada de Hacienda" in st.detail
    assert st.retry_after is None
    assert len(fake.gets) == 1
    r = c.post_recepcion({"clave": cl(12), "fecha": "x", "emisor": {}, "comprobanteXml": "eA=="})
    assert r.kind == "rejected" and r.gateway and r.http_status == 403 and "revise que las credenciales" not in r.cause and "no llegó" in r.cause
    assert len(fake.posts) == 1 and fake.production_hits == []
    # 403 sin la cabecera del gateway: el texto de siempre
    fake2 = FakeHacienda()
    fake2.post_plan = [(403, {})]
    r2 = _client(fake2).post_recepcion({"clave": cl(13), "fecha": "x", "emisor": {}, "comprobanteXml": "eA=="})
    assert r2.kind == "rejected" and not r2.gateway and "negó el acceso" in r2.cause


def test_probe_estado_nunca_lanza():
    k = cl(14)
    fake = FakeHacienda()
    assert _client(fake).probe_estado(k).status == "ok"
    fake = FakeHacienda(not_found_style="400")
    assert _client(fake).probe_estado(k).status == "ok"
    fake = FakeHacienda(gateway_403=True)
    p = _client(fake).probe_estado(k)
    assert (p.status, p.motivo) == ("falla", "ruta")
    fake = FakeHacienda()
    fake.get_plan[k] = [403]
    p = _client(fake).probe_estado(k)
    assert (p.status, p.motivo) == ("falla", "acceso")
    fake = FakeHacienda()
    malo = HaciendaClient("pruebas", USER, "otra-clave", tenant_id=7, http=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    p = malo.probe_estado(k)
    assert (p.status, p.motivo) == ("falla", "credenciales") and "otra-clave" not in p.detail and USER not in p.detail
    fake = FakeHacienda()
    fake.get_plan[k] = [401, 401]
    p = _client(fake).probe_estado(k)
    assert (p.status, p.motivo) == ("falla", "token")
    fake = FakeHacienda()
    fake.get_plan[k] = [429]
    assert _client(fake).probe_estado(k).motivo == "esperar"
    fake = FakeHacienda()
    fake.get_plan[k] = [503]
    p = _client(fake).probe_estado(k)
    assert p.status == "aviso" and p.http_status == 503
    fake = FakeHacienda()
    fake.get_plan[k] = [(302, {"Location": "https://otra.example/"})]
    assert _client(fake).probe_estado(k).motivo == "redireccion"
    fake = FakeHacienda()
    fake.get_plan[k] = [(400, {"X-Error-Cause": "Clave con formato invalido"})]
    p = _client(fake).probe_estado(k)
    assert p.status == "aviso" and "formato" in p.detail
    assert fake.production_hits == []


def test_actividades_siguen_con_el_doble(monkeypatch):
    from app.providers.hacienda.client import lookup_activities

    fake = FakeHacienda().install(monkeypatch)
    assert lookup_activities("3102000000")[0]["code"] == "6202.0"
    assert fake.production_hits == []
