"""Ajustes -> Hacienda: credenciales cifradas (solo se escriben), permisos, llaves FISCAL_KEYS, credenciales de entorno,
modo, interruptor de produccion con sus tres llaves y la huella de instancia, consecutivos fiscales."""

import base64
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from pydantic import SecretStr

from tests.hacienda_utils import ADMIN_PASSWORD, EMPRESA, load_credentials, make_p12, role_headers

USER, PASS = "usuario-pruebas@ejemplo", "Clave-ATV-de-prueba!"


def _key(kid: str) -> str:
    return f"{kid}:" + base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_cargar_credenciales_reautenticacion_y_nada_sale(client, auth, db_session, p12_empresa):
    p12, pin = p12_empresa
    r = client.put("/settings/hacienda/credentials/pruebas", data={"atv_user": USER, "atv_password": PASS, "pin": pin}, files={"p12": ("k.p12", p12)})
    assert r.status_code == 403  # sin contrasena de administrador
    r = load_credentials(client, p12, pin + "malo")
    assert r.status_code == 422 and "PIN" in r.json()["detail"]
    st = client.get("/settings/hacienda").json()["credentials"]["pruebas"]
    assert st["source"] == "ninguna" and not st["atv_user_set"]  # PIN malo: no se guardo nada
    otra, pin_o = make_p12("3101999999")
    r = load_credentials(client, otra, pin_o)
    assert r.status_code == 422 and "otra cédula" in r.json()["detail"]
    assert load_credentials(client, otra, pin_o, allow_other_id="true").status_code == 200
    r = load_credentials(client, p12, pin)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["source"] == "ajustes" and b["atv_user_set"] and b["atv_password_set"] and b["p12_set"] and b["pin_set"] and b["readable"]
    assert b["cert"]["subject_cn"] == "EMPRESA FICTICIA DE PRUEBA SA" and b["cert"]["days_left"] >= 29
    crudo = client.get("/settings/hacienda").text
    for secreto in (USER, PASS, pin, base64.b64encode(p12).decode()[:40], "v1."):
        assert secreto not in crudo
    # vacio = conservar
    r = client.put("/settings/hacienda/credentials/pruebas", data={"admin_password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.json()["p12_set"]


def test_permisos_contabilidad_y_ventas(client, auth, db_session, p12_empresa):
    tid = auth["tenant"]["id"]
    conta = role_headers(client, db_session, tid, "conta.hac@ejemplo.com", "contabilidad")
    ventas = role_headers(client, db_session, tid, "ventas.hac@ejemplo.com", "ventas")
    assert client.get("/settings/hacienda", headers=conta).status_code == 200
    assert client.put("/settings/hacienda/credentials/pruebas", data={"admin_password": "x"}, headers=conta).status_code == 403
    assert client.request("DELETE", "/settings/hacienda/credentials/pruebas", json={"admin_password": "x"}, headers=conta).status_code == 403
    assert client.put("/settings/hacienda/mode", json={"mode": "pruebas"}, headers=conta).status_code == 403
    assert client.get("/settings/hacienda", headers=ventas).status_code == 403
    assert client.get("/fiscal/activities", headers=ventas).status_code == 200  # el editor de facturas las usa


def test_respaldo_del_portal_tacha_credenciales(client, auth, db_session, p12_empresa):
    load_credentials(client, *p12_empresa)
    r = client.get("/settings/data-export")
    z = zipfile.ZipFile(BytesIO(r.content))
    data = json.loads(z.read("data/fiscal_credential.json"))
    cols = data["columns"]
    for row in data["rows"]:
        for c in ("atv_user_enc", "atv_password_enc", "p12_enc", "p12_pin_enc"):
            assert row[cols.index(c)] is None


def test_secretbox_aad_rotacion_y_sin_llave(client, auth, db_session, monkeypatch, p12_empresa):
    from app.core import secretbox
    from app.core.config import settings
    from app.models import FiscalCredential
    from app.tools.rotate_fiscal_keys import rotate

    tok = secretbox.seal_text("secreto", "7:pruebas:atv_password")
    assert secretbox.unseal_text(tok, "7:pruebas:atv_password") == "secreto"
    for otro in ("8:pruebas:atv_password", "7:produccion:atv_password", "7:pruebas:atv_user"):
        with pytest.raises(secretbox.SecretUnavailable):
            secretbox.unseal(tok, otro)  # copiado a otra empresa, ambiente o campo: no se descifra
    load_credentials(client, *p12_empresa)
    vieja = settings.fiscal_keys.get_secret_value()
    monkeypatch.setattr(settings, "fiscal_keys", SecretStr(_key("k2") + "," + vieja))  # rotacion: la nueva cifra, la vieja descifra
    st = client.get("/settings/hacienda").json()["credentials"]["pruebas"]
    assert st["readable"]
    res = rotate(db_session)
    assert res["credenciales"] == 1 and res["campos"] == 4
    row = db_session.query(FiscalCredential).one()
    assert row.key_id == "k2" and row.p12_enc.startswith("v1.k2.")
    monkeypatch.setattr(settings, "fiscal_keys", SecretStr(_key("k3")))  # otra llave: ilegible, sin 500
    st = client.get("/settings/hacienda").json()["credentials"]["pruebas"]
    assert st["readable"] is False
    monkeypatch.setattr(settings, "fiscal_keys", None)
    monkeypatch.setattr(settings, "env", "staging")
    assert not secretbox.available()
    assert load_credentials(client, *p12_empresa).status_code == 503
    monkeypatch.setattr(settings, "env", "local")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u:p@db/crimson")
    assert not secretbox.available()  # un servidor que olvido ENV (queda local) contra Postgres: tampoco


def test_validacion_de_fiscal_keys():
    from app.core.config import parse_fiscal_keys

    assert parse_fiscal_keys(_key("a") + "," + _key("b"))[0][0] == "a"
    for malo in ("sinkid", "a:corta", _key("a") + "," + _key("a")):
        with pytest.raises(ValueError):
            parse_fiscal_keys(malo)


def test_credenciales_de_entorno_solo_su_ambiente_y_empresa(client, auth, db_session, monkeypatch, p12_empresa):
    from app.core.config import settings
    from app.models import Tenant
    from app.services import hacienda_config as hc

    p12, pin = p12_empresa
    monkeypatch.setattr(settings, "hacienda_username", SecretStr(USER))
    monkeypatch.setattr(settings, "hacienda_password", SecretStr(PASS))
    monkeypatch.setattr(settings, "hacienda_p12_b64", SecretStr(base64.b64encode(p12).decode()))
    monkeypatch.setattr(settings, "hacienda_p12_pin", SecretStr(pin))
    t = db_session.get(Tenant, auth["tenant"]["id"])
    c = hc.credentials(db_session, t, "pruebas")
    assert c is not None and c.source == "entorno" and USER not in repr(c) and pin not in repr(c)
    assert hc.credentials(db_session, t, "produccion") is None  # solo su ambiente
    monkeypatch.setattr(settings, "hacienda_env_tenant", "otra-empresa")
    assert hc.credentials(db_session, t, "pruebas") is None  # solo su empresa
    monkeypatch.setattr(settings, "hacienda_env_tenant", None)
    assert client.get("/settings/hacienda").json()["credentials"]["pruebas"]["source"] == "entorno"
    load_credentials(client, p12, pin, user="otro-usuario@ejemplo")
    assert hc.credentials(db_session, t, "pruebas").source == "ajustes"  # Ajustes manda sobre entorno


def _listo_para_produccion(client, db_session, auth, monkeypatch, p12):
    from app.core.config import settings
    from app.models import FiscalCredential

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "hacienda_production_allowed", True)
    monkeypatch.setattr(settings, "railway_environment_id", "ambiente-prod")
    assert load_credentials(client, *p12, env="produccion").status_code == 200
    row = db_session.query(FiscalCredential).filter(FiscalCredential.environment == "produccion").one()
    row.last_test_at, row.last_test_ok, row.last_test_detail = datetime.now(UTC), True, "Conectado"  # (FakeHacienda no atiende produccion)
    db_session.commit()
    assert client.post("/settings/hacienda/consecutives/confirm", json={"admin_password": ADMIN_PASSWORD}).status_code == 200


def test_produccion_interruptor_y_huella(client, auth, db_session, monkeypatch, p12_empresa):
    from app.core.config import settings
    from app.models import FiscalProfile, Tenant
    from app.services import hacienda_config as hc

    on = {"enabled": True, "admin_password": ADMIN_PASSWORD, "confirm_text": "PRODUCCIÓN"}
    r = client.post("/settings/hacienda/production", json=on)
    assert r.status_code == 409 and "Falta" in r.json()["detail"]  # checks en rojo (sin credenciales, sin servidor)
    _listo_para_produccion(client, db_session, auth, monkeypatch, p12_empresa)
    assert client.post("/settings/hacienda/production", json={**on, "admin_password": "mala"}).status_code == 403
    assert client.post("/settings/hacienda/production", json={**on, "confirm_text": "si"}).status_code == 422
    monkeypatch.setattr(settings, "hacienda_production_allowed", False)
    assert client.post("/settings/hacienda/production", json=on).status_code == 409
    monkeypatch.setattr(settings, "hacienda_production_allowed", True)
    monkeypatch.setattr(settings, "env", "staging")
    assert client.post("/settings/hacienda/production", json=on).status_code == 409
    monkeypatch.setattr(settings, "env", "production")
    # confirmacion de consecutivos de hace mas de 24 h ya no vale
    prof = db_session.query(FiscalProfile).one()
    prof.consecutives_confirmed_at = datetime.now(UTC) - timedelta(hours=25)
    db_session.commit()
    assert client.post("/settings/hacienda/production", json=on).status_code == 409
    client.post("/settings/hacienda/consecutives/confirm", json={"admin_password": ADMIN_PASSWORD})
    # por debajo del maximo importado de Fygaro
    t = db_session.get(Tenant, auth["tenant"]["id"])
    t.settings = {**(t.settings or {}), "fygaro_import": {"max_receptor": {"05": 359}}}
    db_session.commit()
    r = client.post("/settings/hacienda/production", json=on)
    assert r.status_code == 409 and "Fygaro" in r.json()["detail"]
    g = next(c for c in client.get("/settings/hacienda").json()["consecutives"] if c["code"] == "05")
    assert client.put(f"/settings/billing-groups/{g['group_id']}", json={"prefix": "F05", "current": 359}).status_code == 200
    from app.core.ratelimit import reauth_limiter

    reauth_limiter.reset()  # esta prueba reautentica mas de 10 veces seguidas
    client.post("/settings/hacienda/consecutives/confirm", json={"admin_password": ADMIN_PASSWORD})  # el cambio borro la confirmacion
    r = client.post("/settings/hacienda/production", json=on)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["mode"] == "produccion" and st["production"]["effective"]
    prof = db_session.query(FiscalProfile).one()
    db_session.refresh(prof)
    assert prof.production_instance and prof.environment == "produccion"
    # otro servidor (ambiente duplicado en Railway) o una llave del servidor que falla: no se emite nada. Nunca cae a
    # pruebas: cada venta real iria al ambiente de pruebas sin que nadie escribiera PRUEBAS (plan 1.11 y 4.6)
    monkeypatch.setattr(settings, "railway_environment_id", "ambiente-clonado")
    assert hc.hacienda_mode(db_session, t) == "ninguno" and hc.production_blocked(db_session, t)
    from app.models import EInvoiceDocument

    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]}).json()
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 409 and "no la permite" in r.json()["detail"], r.text
    assert db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).count() == 0
    monkeypatch.setattr(settings, "railway_environment_id", "ambiente-prod")
    assert hc.production_blocked(db_session, t) is None
    assert hc.hacienda_mode(db_session, t) == "produccion"
    # con produccion activa no se cambia la cedula ni un F0x
    perfil = client.get("/settings/hacienda").json()["profile"]
    perfil = {k: v for k, v in perfil.items() if v is not None}
    assert client.put("/settings/hacienda/profile", json={**perfil, "tax_id": "3-102-111111"}).status_code == 409
    assert client.put(f"/settings/billing-groups/{g['group_id']}", json={"prefix": "F05", "current": 400}).status_code == 409
    # apagar: la empresa queda en Desactivada (no se emite; lo vendido entra a "Por emitir"), nunca en Pruebas sin
    # escribir PRUEBAS
    r = client.post("/settings/hacienda/production", json={"enabled": False, "admin_password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.json()["mode"] == "ninguno" and r.json()["einvoice_provider"] == "none", r.text
    assert r.json()["production"]["enabled_at"] is None
    from app.models import AuditLog

    log = db_session.query(AuditLog).filter(AuditLog.action == "hacienda_mode").order_by(AuditLog.id.desc()).first()
    assert log.diff == {"antes": "produccion", "despues": "none"}
    # apagar otra vez no toca un modo que ya no es produccion
    assert client.put("/settings/hacienda/mode", json={"mode": "pruebas", "confirm_text": "PRUEBAS"}).json()["mode"] == "pruebas"
    r = client.post("/settings/hacienda/production", json={"enabled": False, "admin_password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.json()["mode"] == "pruebas"


def test_cambiar_de_modo_apaga_produccion(client, auth, db_session, monkeypatch, p12_empresa):
    from app.models import FiscalProfile

    on = {"enabled": True, "admin_password": ADMIN_PASSWORD, "confirm_text": "PRODUCCION"}
    _listo_para_produccion(client, db_session, auth, monkeypatch, p12_empresa)
    assert client.post("/settings/hacienda/production", json=on).status_code == 200
    assert client.put("/settings/hacienda/mode", json={"mode": "produccion"}).status_code == 422
    r = client.put("/settings/hacienda/mode", json={"mode": "sandbox"})
    assert r.json()["mode"] == "simulado" and r.json()["production"]["enabled_at"] is None
    client.post("/settings/hacienda/consecutives/confirm", json={"admin_password": ADMIN_PASSWORD})
    assert client.post("/settings/hacienda/production", json=on).status_code == 200
    assert client.put("/settings", json={"einvoice_provider": "hacienda"}).status_code == 422  # la ruta generica no entra
    assert client.put("/settings", json={"einvoice_provider": "sandbox"}).status_code == 200  # ...pero si apaga
    prof = db_session.query(FiscalProfile).one()
    db_session.refresh(prof)
    assert prof.environment == "pruebas" and prof.production_enabled_at is None and prof.production_instance is None
    r = client.put("/settings/hacienda/mode", json={"mode": "pruebas", "confirm_text": "PRUEBAS"})
    assert r.json()["mode"] == "pruebas"  # volver a "hacienda" no revive produccion
    assert client.patch("/tenant", json={"settings": {"einvoice_provider": "hacienda"}}).status_code == 422


def test_consecutivos_fiscales_y_settings_sin_texto_cifrado(client, auth):
    st = client.get("/settings/hacienda").json()
    assert [c["code"] for c in st["consecutives"]] == ["01", "02", "03", "04", "05", "06", "07"]
    g = st["consecutives"][0]
    assert client.put(f"/settings/billing-groups/{g['group_id']}", json={"prefix": "OTRO", "current": 1}).status_code == 422
    assert client.put(f"/settings/billing-groups/{g['group_id']}", json={"prefix": "F01", "is_default": False}).status_code == 422
    assert client.post("/settings/hacienda/consecutives/confirm", json={"admin_password": ADMIN_PASSWORD}).json()["consecutives_confirmed_at"]
    assert client.put(f"/settings/billing-groups/{g['group_id']}", json={"prefix": "F01", "current": 85}).status_code == 200
    st = client.get("/settings/hacienda").json()
    assert st["consecutives_confirmed_at"] is None and st["consecutives"][0]["current"] == 85
    assert client.put(f"/settings/billing-groups/{g['group_id']}", json={"prefix": "F01", "current": 84}).status_code == 422  # nunca retrocede
    client.put("/settings/inbox", json={"enabled": True, "host": "imap.ejemplo.com", "user": "f@ejemplo.com", "password": "clave-123", "admin_password": ADMIN_PASSWORD})
    assert "password_enc" not in client.get("/settings").text


def test_perfil_actividades_y_geo(client, auth):
    perfil = client.get("/settings/hacienda").json()["profile"]
    assert perfil["province"] == "1" and perfil["tax_id"] == "3-102-000000"
    body = {k: v for k, v in perfil.items() if v is not None}
    assert client.put("/settings/hacienda/profile", json={**body, "id_type": "01"}).status_code == 422  # fisica: 9 digitos
    r = client.put("/settings/hacienda/profile", json={**body, "commercial_name": "Crimson", "barrio": "Barrio de prueba"})
    assert r.status_code == 200 and r.json()["profile"]["commercial_name"] == "Crimson"
    acts = client.get("/fiscal/activities").json()
    assert acts[0]["code"] == "6202.0" and acts[0]["is_default"]
    nueva = client.post("/settings/hacienda/activities", json={"code": "4652.0", "description": "Venta de equipo"}).json()
    assert client.request("DELETE", f"/settings/hacienda/activities/{acts[0]['id']}").status_code == 409  # la por defecto no
    client.put(f"/settings/hacienda/activities/{nueva['id']}", json={"code": "4652.0", "is_default": True})
    assert client.get("/fiscal/activities").json()[0]["code"] == "4652.0"
    assert "4652.0" in client.get("/settings").json()["activity_codes"]
    geo = client.get("/fiscal/geo").json()
    sj = next(p for p in geo if p["code"] == "1")
    assert next(c for c in sj["cantons"] if c["name"] == "Desamparados")["code"] == "03"


def test_codigos_de_ubicacion():
    from app.services.cr_geo import canton_code

    assert canton_code("San José", "Desamparados") == "03"
    assert canton_code("Alajuela", "Alajuela") == "01"
    assert canton_code("Cartago", "El Guarco") == "08"
    assert canton_code("Limón", "Guácimo") == "06"
    assert canton_code("Puntarenas", "Monteverde") == "12" and canton_code("Puntarenas", "Puerto Jiménez") == "13"


def test_probar_conexion_con_fakehacienda(client, auth, monkeypatch, p12_empresa):
    from tests.hacienda_utils import FakeHacienda

    fake = FakeHacienda().install(monkeypatch)
    load_credentials(client, *p12_empresa)
    r = client.post("/settings/hacienda/credentials/pruebas/test")
    assert r.status_code == 200 and r.json()["ok"] and "pruebas" in r.json()["detail"]
    assert fake.tokens and fake.production_hits == []
    st = client.get("/settings/hacienda").json()["credentials"]["pruebas"]
    assert st["last_test_ok"] is True
    load_credentials(client, *p12_empresa, password="incorrecta")
    r = client.post("/settings/hacienda/credentials/pruebas/test").json()
    assert not r["ok"] and "incorrectos" in r["detail"] and "incorrecta" not in r["detail"]
    assert EMPRESA in client.get("/settings/hacienda").json()["profile"]["tax_id"].replace("-", "")


def test_actividad_quitada_se_puede_volver_a_agregar(client, auth):
    """"Quitar" solo desactiva: agregar el mismo codigo (a mano o con "Usar" del lookup) la reactiva en vez de un 409."""
    nueva = client.post("/settings/hacienda/activities", json={"code": "4653.0", "description": "Venta de cableado"})
    assert nueva.status_code == 201, nueva.text
    aid = nueva.json()["id"]
    assert client.post("/settings/hacienda/activities", json={"code": "4653.0"}).status_code == 409  # activa: duplicada
    assert client.request("DELETE", f"/settings/hacienda/activities/{aid}").status_code == 200
    assert "4653.0" not in [a["code"] for a in client.get("/fiscal/activities").json()]
    r = client.post("/settings/hacienda/activities", json={"code": "4653.0", "description": "Venta de cable estructurado"})
    assert r.status_code == 201 and r.json()["id"] == aid and r.json()["active"] and not r.json()["is_default"]
    got = next(a for a in client.get("/fiscal/activities").json() if a["code"] == "4653.0")
    assert got["description"] == "Venta de cable estructurado"
    assert "4653.0" in client.get("/settings").json()["activity_codes"]
