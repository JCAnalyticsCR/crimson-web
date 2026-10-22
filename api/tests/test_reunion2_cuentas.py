"""Reunion 2 (21/10), frente "cuentas": pedidos 6 (.p12 con cifrado viejo y errores claros), 8 (ambiente para emitir
y usuario ATV de cada ambiente), 26 (selectores sin inactivos ni duplicados), 27 (aceptar una invitacion con otra
sesion abierta), 28 (eliminar usuarios: borrar o anonimizar) y 29 (que ve cada rol, el admin no se recorta).

Repo publico: certificados autofirmados con cedulas ficticias, PIN aleatorios, nombres y correos de prueba."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from app.models import AuditLog, FiscalCredential, Invitation, RefreshToken, TenantUser, User
from app.providers.hacienda import p12legacy as L
from app.providers.hacienda import xades
from app.providers.hacienda.xades import CertError, load_p12
from tests.aliados_helpers import ADMIN_PW, PW, interno, login, uid
from tests.hacienda_utils import ADMIN_PASSWORD, EMPRESA, load_credentials

STAG = "cpj-3-102-000000@stag.comprobanteselectronicos.go.cr"  # forma del usuario ATV de pruebas (cedula ficticia)
PROD = "cpj-3-102-000000@prod.comprobanteselectronicos.go.cr"


# ---------------------------------------------------------------- .p12 "como los de Hacienda": RC2-40 + 3DES
def _der(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + L._encode_len(len(body)) + body


def _seq(*xs: bytes) -> bytes:
    return _der(0x30, b"".join(xs))


def _oct(b: bytes) -> bytes:
    return _der(0x04, b)


def _int(n: int) -> bytes:
    return _der(0x02, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True))


def _oid(dotted: str) -> bytes:
    return _der(0x06, L._oid(dotted))


def _ctx0(b: bytes) -> bytes:
    return _der(0xA0, b)


def _cert(cedula: str = EMPRESA, *, expired: bool = False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA FICTICIA DE PRUEBA SA"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CPJ-{cedula[:1]}-{cedula[1:4]}-{cedula[4:]}"),
        ]
    )
    now = datetime.now(UTC)
    nb, na = (now - timedelta(days=60), now - timedelta(days=1)) if expired else (now - timedelta(days=1), now + timedelta(days=30))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA DE PRUEBA NO VALIDA")]))
        .public_key(key.public_key())
        .serial_number(int.from_bytes(os.urandom(8), "big") >> 1)
        .not_valid_before(nb)
        .not_valid_after(na)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def legacy_p12(pin: str, *, expired: bool = False, iters: int = 2048) -> bytes:
    """PKCS#12 con los certificados cifrados con pbeWithSHAAnd40BitRC2-CBC y la llave con pbeWithSHAAnd3-KeyTripleDES-CBC
    (lo que hace `openssl pkcs12 -export -legacy` y traen llaves criptograficas viejas), MAC HMAC-SHA1. Armado a mano:
    cryptography no sabe escribir RC2."""
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES

    key, cert = _cert(expired=expired)
    pw = L.bmp_password(pin)
    # certificados: RC2-40
    salt_c = os.urandom(8)
    cert_bag = _seq(_oid("1.2.840.113549.1.12.10.1.3"), _ctx0(_seq(_oid("1.2.840.113549.1.9.22.1"), _ctx0(_oct(cert.public_bytes(serialization.Encoding.DER))))))
    k5 = L.pkcs12_kdf("sha1", pw, salt_c, 1, iters, 5)
    iv = L.pkcs12_kdf("sha1", pw, salt_c, 2, iters, 8)
    ct = L.rc2_cbc(k5, iv, L.pad(_seq(cert_bag), 8), encrypt=True)
    enc_certs = _seq(
        _oid("1.2.840.113549.1.7.6"),
        _ctx0(_seq(_int(0), _seq(_oid("1.2.840.113549.1.7.1"), _seq(_oid("1.2.840.113549.1.12.1.6"), _seq(_oct(salt_c), _int(iters))), _der(0x80, ct)))),
    )
    # llave: 3DES
    salt_k = os.urandom(8)
    pkcs8 = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    k24 = L.pkcs12_kdf("sha1", pw, salt_k, 1, iters, 24)
    ivk = L.pkcs12_kdf("sha1", pw, salt_k, 2, iters, 8)
    enc = Cipher(TripleDES(k24), modes.CBC(ivk)).encryptor()
    kct = enc.update(L.pad(pkcs8, 8)) + enc.finalize()
    key_bag = _seq(_oid("1.2.840.113549.1.12.10.1.2"), _ctx0(_seq(_seq(_oid("1.2.840.113549.1.12.1.3"), _seq(_oct(salt_k), _int(iters))), _oct(kct))))
    data_keys = _seq(_oid("1.2.840.113549.1.7.1"), _ctx0(_oct(_seq(key_bag))))
    auth_safe = _seq(enc_certs, data_keys)
    salt_m = os.urandom(8)
    mk = L.pkcs12_kdf("sha1", pw, salt_m, 3, iters, 20)
    mac = hmac.new(mk, auth_safe, hashlib.sha1).digest()
    mac_data = _seq(_seq(_seq(_oid("1.3.14.3.2.26"), _der(0x05, b"")), _oct(mac)), _oct(salt_m), _int(iters))
    return _seq(_int(3), _seq(_oid("1.2.840.113549.1.7.1"), _ctx0(_oct(auth_safe))), mac_data)


@pytest.fixture(scope="module")
def legacy():
    pin = secrets.token_urlsafe(10)
    return legacy_p12(pin), pin


@pytest.fixture
def sin_legacy(monkeypatch):
    """Servidor cuyo OpenSSL no carga el proveedor "legacy": cryptography rechaza RC2-40 igual que un PIN malo."""
    real = pkcs12.load_key_and_certificates

    def falla_con_rc2(data, password, *a, **k):
        if b"\x2a\x86\x48\x86\xf7\x0d\x01\x0c\x01\x06" in data:  # OID de pbeWithSHAAnd40BitRC2-CBC
            raise ValueError("Invalid password or PKCS12 data")
        return real(data, password, *a, **k)

    monkeypatch.setattr(xades.pkcs12, "load_key_and_certificates", falla_con_rc2)


# ---------- 6. .p12 con cifrado viejo ----------
def test_rc2_cumple_los_vectores_del_rfc_2268():
    assert sorted(L.PITABLE) == list(range(256))
    for key, bits, pt, ct in (
        ("0000000000000000", 63, "0000000000000000", "ebb773f993278eff"),
        ("ffffffffffffffff", 64, "ffffffffffffffff", "278b27e42e2f0d49"),
        ("3000000000000000", 64, "1000000000000001", "30649edf9be7d2c2"),
        ("88bca90e90875a7f0f79c384627bafb2", 128, "0000000000000000", "2269552ab0f85ca6"),
    ):
        k = L._rc2_keys(bytes.fromhex(key), bits)
        assert L._rc2_encrypt_block(k, bytes.fromhex(pt)).hex() == ct
        assert L._rc2_decrypt_block(k, bytes.fromhex(ct)).hex() == pt


def test_el_p12_viejo_es_estandar(legacy):
    """El .p12 armado aqui lo abre OpenSSL cuando tiene el proveedor legacy: es un PKCS#12 de verdad."""
    from cryptography.hazmat.bindings._rust import openssl as rust_openssl

    if not getattr(rust_openssl, "_legacy_provider_loaded", False):
        pytest.skip("OpenSSL sin proveedor legacy en esta maquina")
    data, pin = legacy
    _key, cert, _ = pkcs12.load_key_and_certificates(data, pin.encode())
    assert "CPJ-3-102-000000" in cert.subject.rfc4514_string()


def test_carga_p12_rc2_40_sin_proveedor_legacy_y_con_espacios_en_el_pin(legacy, sin_legacy):
    data, pin = legacy
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(data, pin.encode())  # asi fallaba antes: el mismo error que un PIN malo
    info = load_p12(data, f"  {pin}\n")
    assert info.key_bits == 2048 and "CPJ" in (info.serial_number_attr or "") and info.pin_ok == pin
    # y firma con el (la misma ruta que emitir)
    from lxml import etree

    firmado = xades.sign(etree.fromstring(b"<a xmlns='urn:x'><b>1</b></a>"), data, pin)
    assert b"SignatureValue" in firmado


def test_errores_distintos_pin_archivo_y_vencido(legacy, sin_legacy):
    data, pin = legacy
    with pytest.raises(CertError, match="El PIN no abre") as e:
        load_p12(data, pin + "x")
    assert e.value.code == "pin"
    for malo in (data[: len(data) // 2], b"esto no es un certificado", b"\x30\x03\x02\x01\x03"):
        with pytest.raises(CertError, match="dañado o no es") as e:
            load_p12(malo, pin)
        assert e.value.code == "archivo"
    pin_v = "1234"
    with pytest.raises(CertError, match="venció") as e:
        load_p12(legacy_p12(pin_v, expired=True), pin_v)
    assert e.value.code == "vencido"


def test_p12_moderno_tambien_distingue_pin_y_archivo(p12_empresa):
    p12, pin = p12_empresa
    with pytest.raises(CertError) as e:
        load_p12(p12, "otro-pin")
    assert e.value.code == "pin"
    with pytest.raises(CertError) as e:
        load_p12(p12[:-40], pin)
    assert e.value.code == "archivo"
    assert load_p12(p12, f" {pin} ").pin_ok == pin


def test_p12_generado_por_openssl_con_rc2(tmp_path, sin_legacy):
    """Comprobacion independiente: el .p12 que genera la herramienta openssl con -legacy (RC2-40 + 3DES)."""
    exe = shutil.which("openssl")
    if not exe:
        pytest.skip("sin openssl en esta maquina")
    key, cert = _cert()
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pin = secrets.token_hex(6)
    out = tmp_path / "llave.p12"
    r = subprocess.run(
        [exe, "pkcs12", "-export", "-legacy", "-inkey", str(tmp_path / "k.pem"), "-in", str(tmp_path / "c.pem"), "-out", str(out), "-passout", f"pass:{pin}", "-certpbe", "PBE-SHA1-RC2-40", "-keypbe", "PBE-SHA1-3DES"],
        capture_output=True,
        timeout=60,
    )
    if r.returncode != 0 or not out.exists():
        pytest.skip("openssl sin -legacy en esta maquina")
    assert load_p12(out.read_bytes(), pin).key_bits == 2048


def test_api_guarda_el_pin_sin_espacios_y_explica_cada_error(client, auth, db_session, legacy, sin_legacy):
    from app.core import secretbox

    data, pin = legacy
    r = load_credentials(client, data, pin + "x", user=STAG)
    assert r.status_code == 422 and "El PIN no abre" in r.json()["detail"]
    r = load_credentials(client, b"no es un p12", pin, user=STAG)
    assert r.status_code == 422 and "dañado" in r.json()["detail"]
    r = load_credentials(client, data, f" {pin} ", user=STAG)
    assert r.status_code == 200, r.text
    row = db_session.scalar(select(FiscalCredential).where(FiscalCredential.tenant_id == auth["tenant"]["id"], FiscalCredential.environment == "pruebas"))
    db_session.refresh(row)
    assert secretbox.unseal_text(row.p12_pin_enc, secretbox.aad_for(auth["tenant"]["id"], "pruebas", "p12_pin")) == pin


# ---------- 8. Ambiente para emitir y usuario ATV de cada ambiente ----------
def test_usuario_atv_de_cada_ambiente_y_avisos(client, auth, p12_empresa):
    from app.services.hacienda_config import atv_user_env

    assert atv_user_env(STAG) == "pruebas" and atv_user_env(PROD) == "produccion" and atv_user_env("alguien@ejemplo.com") is None
    assert atv_user_env("cpj-3-102-000000-stag") == "pruebas"
    p12, pin = p12_empresa
    b = load_credentials(client, p12, pin, user=STAG).json()
    assert b["atv_user_env"] == "pruebas" and b["atv_user_warning"] is None and b["environment"] == "pruebas"
    # cruzado: se guarda (solo aviso, no bloquea) y se avisa en los dos lados
    r = load_credentials(client, p12, pin, env="produccion", user=STAG)
    assert r.status_code == 200
    assert r.json()["atv_user_env"] == "pruebas" and "stag" in r.json()["atv_user_warning"] and "Producción" in r.json()["atv_user_warning"]
    st = client.get("/settings/hacienda")
    assert st.json()["credentials"]["produccion"]["atv_user_warning"]
    assert STAG not in st.text and "cpj-3-102" not in st.text  # el usuario nunca sale, solo de que ambiente parece
    assert load_credentials(client, p12, pin, env="produccion", user=PROD).json()["atv_user_warning"] is None


def test_dice_con_que_ambiente_se_emite(client, auth, p12_empresa):
    st = client.get("/settings/hacienda").json()
    assert st["mode"] in ("ninguno", "simulado") and st["emission_environment"] is None
    load_credentials(client, *p12_empresa, user=STAG)
    st = client.put("/settings/hacienda/mode", json={"mode": "pruebas", "confirm_text": "PRUEBAS"}).json()
    assert st["mode"] == "pruebas" and st["emission_environment"] == "pruebas"
    st = client.put("/settings/hacienda/mode", json={"mode": "none"}).json()
    assert st["emission_environment"] is None


# ---------- 26. Selectores sin inactivos ni duplicados ----------
def test_responsable_sin_inactivos_ni_duplicados(client, auth, db_session):
    tid = auth["tenant"]["id"]
    activo = uid(client, interno(client, db_session, tid, "persona.repetida@ejemplo.com", "ventas"))
    gemelo = uid(client, interno(client, db_session, tid, "persona.repetida2@ejemplo.com", "ventas"))
    inactivo = uid(client, interno(client, db_session, tid, "persona.mal.escrita@ejemplo.com", "ventas"))
    for u_id in (activo, gemelo, inactivo):
        db_session.get(User, u_id).full_name = "Persona Repetida"
    db_session.scalar(select(TenantUser).where(TenantUser.user_id == inactivo)).active = False
    cuenta_off = uid(client, interno(client, db_session, tid, "cuenta.apagada@ejemplo.com", "supervisor"))
    db_session.get(User, cuenta_off).active = False
    db_session.commit()

    meta = client.get("/opportunities/meta/config").json()
    for lista in (meta["sellers"], meta["assignees"]):
        ids = [x["id"] for x in lista]
        assert len(ids) == len(set(ids)) and inactivo not in ids and cuenta_off not in ids
        assert activo in ids and gemelo in ids
        names = [x["name"] for x in lista]
        assert len(names) == len(set(names))  # mismo nombre: se distinguen por el correo
    tech = client.get("/field/technicians").json()
    assert cuenta_off not in [x["id"] for x in tech] and len({x["id"] for x in tech}) == len(tech)
    agentes = client.get("/tickets/meta/config").json()["agents"]
    assert inactivo not in [x["id"] for x in agentes] and cuenta_off not in [x["id"] for x in agentes]


# ---------- 27. Aceptar una invitacion con otra sesion abierta ----------
def test_aceptar_invitacion_cierra_la_sesion_de_ese_navegador(client, auth, db_session):
    raw = client.cookies.get("crimson_refresh")
    assert raw
    link = client.post("/settings/invitations", json={"email": "cuenta.nueva@ejemplo.com", "role": "ventas"}).json()["link"]
    keep = client.headers.pop("Authorization")
    try:
        r = client.post("/settings/invitations/accept", json={"token": link.rsplit("/", 1)[-1], "full_name": "Cuenta Nueva", "password": PW})
    finally:
        client.headers["Authorization"] = keep
    assert r.status_code == 200 and r.json()["previous_session_closed"] is True
    assert "crimson_refresh" in r.headers.get("set-cookie", "")
    from app.core.security import hash_token

    rt = db_session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    db_session.refresh(rt)
    assert rt.revoked_at is not None  # la sesion vieja ya no se renueva
    client.cookies.set("crimson_refresh", raw)
    assert client.post("/auth/refresh").status_code == 401
    # la cuenta nueva entra con lo suyo
    assert client.get("/auth/me", headers=login(client, "cuenta.nueva@ejemplo.com")).json()["user"]["email"] == "cuenta.nueva@ejemplo.com"
    acc = db_session.scalars(select(AuditLog).where(AuditLog.action == "invite_accept")).all()[-1]
    assert (acc.diff or {}).get("cerro_sesion_anterior") is True


def test_aceptar_sin_sesion_abierta(client, auth, db_session):
    link = client.post("/settings/invitations", json={"email": "otra.cuenta@ejemplo.com", "role": "lectura"}).json()["link"]
    client.cookies.clear()
    keep = client.headers.pop("Authorization")
    try:
        r = client.post("/settings/invitations/accept", json={"token": link.rsplit("/", 1)[-1], "full_name": "Otra Cuenta", "password": PW})
    finally:
        client.headers["Authorization"] = keep
    assert r.status_code == 200 and r.json()["previous_session_closed"] is False


# ---------- 28. Eliminar usuarios: borrar o anonimizar ----------
def test_con_historial_se_anonimiza_y_desactiva(client, auth, db_session):
    tid = auth["tenant"]["id"]
    h = interno(client, db_session, tid, "vendedor.historial@ejemplo.com", "ventas")
    u_id = uid(client, h)
    c = client.get("/customers").json()["items"][0]
    q = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 0}]}, headers=h)
    assert q.status_code == 201
    a = client.get(f"/settings/users/{u_id}/activity").json()
    assert a["can_delete"] is True and a["mode"] == "anonimizar" and any(x["what"] == "Cotizaciones" for x in a["activity"])
    ventas = interno(client, db_session, tid, "otro.vendedor@ejemplo.com", "ventas")
    assert client.post(f"/settings/users/{u_id}/delete", json={"admin_password": ADMIN_PW}, headers=ventas).status_code == 403
    assert client.post(f"/settings/users/{u_id}/delete", json={"admin_password": "no-es-la-clave-1"}).status_code == 403
    r = client.post(f"/settings/users/{u_id}/delete", json={"admin_password": ADMIN_PW, "reason": "cuenta de prueba con datos"})
    assert r.status_code == 200 and r.json()["mode"] == "anonimizada", r.text
    db_session.expire_all()
    u = db_session.get(User, u_id)
    assert u is not None and not u.active and u.full_name == f"Usuario eliminado #{u_id}" and u.email.endswith("@eliminado.invalid")
    assert not u.totp_enabled and u.totp_secret is None
    m = db_session.scalar(select(TenantUser).where(TenantUser.user_id == u_id))
    assert m is not None and not m.active and m.notify_email is None  # la membresia queda (integridad), apagada
    # sus registros siguen apuntando a la cuenta
    assert client.get(f"/quotes/{q.json()['id']}").status_code == 200
    assert u_id not in [x["id"] for x in client.get("/settings/users").json()["users"]]
    assert u_id not in [x["id"] for x in client.get("/opportunities/meta/config").json()["sellers"]]
    assert client.get("/auth/me", headers=h).status_code == 401  # su sesion muere
    log = db_session.scalars(select(AuditLog).where(AuditLog.action == "user_delete", AuditLog.entity_id == u_id)).one()
    assert log.reason == "cuenta de prueba con datos"
    # el correo queda libre para invitarlo de nuevo
    assert client.post("/settings/invitations", json={"email": "vendedor.historial@ejemplo.com", "role": "ventas"}).status_code == 201


def test_sin_historial_se_borra_de_verdad_y_nunca_a_si_mismo(client, auth, db_session):
    from app.core.security import hash_password

    u = User(email="sin.historial@ejemplo.com", full_name="Sin Historial", password_hash=hash_password("Prueba-2026-x"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=auth["tenant"]["id"], user_id=u.id, role_code="lectura"))
    db_session.commit()
    u_id = u.id
    a = client.get(f"/settings/users/{u_id}/activity").json()
    assert a["can_delete"] is True and a["mode"] == "borrar"
    r = client.post(f"/settings/users/{u_id}/delete", json={"admin_password": ADMIN_PW})
    assert r.status_code == 200 and r.json()["mode"] == "borrada"
    db_session.expire_all()
    assert db_session.get(User, u_id) is None
    me = client.get("/auth/me").json()["user"]["id"]
    assert client.get(f"/settings/users/{me}/activity").json()["can_delete"] is False
    assert client.post(f"/settings/users/{me}/delete", json={"admin_password": ADMIN_PW}).status_code == 409


def test_un_invitado_pendiente_se_revoca_al_eliminar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    u_id = uid(client, interno(client, db_session, tid, "con.invitacion@ejemplo.com", "ventas"))
    inv = Invitation(tenant_id=tid, email="con.invitacion@ejemplo.com", role_code="contabilidad", token_hash=secrets.token_hex(32), expires_at=datetime.now(UTC) + timedelta(days=3))
    db_session.add(inv)
    db_session.commit()
    assert client.post(f"/settings/users/{u_id}/delete", json={"admin_password": ADMIN_PASSWORD}).status_code == 200
    db_session.refresh(inv)
    assert inv.revoked_at is not None


# ---------- 29. Que ve cada rol: editable, el admin no se recorta ----------
def test_el_admin_no_se_recorta(client, auth, db_session):
    from app.models import Tenant

    assert client.put("/settings/roles/admin", json={"permissions": {"sales": ["ver"]}}).status_code == 409
    assert client.post("/settings/roles/admin/reset").status_code == 404
    t = db_session.get(Tenant, auth["tenant"]["id"])
    t.settings = {**(t.settings or {}), "role_overrides": {"admin": {"sales": ["ver"]}}}  # aunque alguien lo escriba a mano
    db_session.commit()
    me = client.get("/auth/me").json()
    assert me["permissions"] == {"*": ["*"]} or "*" in me["permissions"]
    roles = client.get("/settings/roles").json()["roles"]
    admin = next(r for r in roles if r["code"] == "admin")
    assert admin["editable"] is False and admin["permissions"] == {"*": ["*"]}
    # y los demas si se editan desde Ajustes (lo que ve la matriz «Qué ve cada rol»)
    assert next(r for r in roles if r["code"] == "lectura")["editable"] is True
    assert client.put("/settings/roles/lectura", json={"permissions": {"dashboard": ["ver"], "reports": ["ver"]}}).status_code == 200
    assert client.get("/auth/roles").json()["matrix"]["lectura"] == {"dashboard": ["ver"], "reports": ["ver"]}
