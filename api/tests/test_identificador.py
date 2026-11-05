"""El identificador avalado por Hacienda (services/identificador): coherencia de la clave con su XML y sello de Hacienda
en el acuse del proveedor. Certificados ficticios: uno con el titular y el emisor del sello de Hacienda y otro ajeno."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from app.providers.hacienda import xades
from app.services import identificador as idf
from tests.hacienda_utils import clave, make_p12, mensaje_hacienda

CONS = "00100001010000000123"
CLAVE = clave(consecutivo=CONS)  # emisor 3101000001, FE


def _p12_hacienda() -> tuple[bytes, str]:
    """Ficticio, con el titular y el emisor del sello real (CPJ-2-100-042005 / CA SINPE - PERSONA JURIDICA v2)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ESTADO-MINISTERIO DE HACIENDA (SELLO ELECTRONICO)"), x509.NameAttribute(NameOID.SERIAL_NUMBER, "CPJ-2-100-042005")])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA SINPE - PERSONA JURIDICA v2")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(int.from_bytes(os.urandom(8), "big") >> 1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    pin = "1234"
    return pkcs12.serialize_key_and_certificates(b"mh", key, cert, None, serialization.BestAvailableEncryption(pin.encode())), pin


def _firmado(p12_pin, clave_=CLAVE, mensaje="1") -> str:
    root = etree.fromstring(mensaje_hacienda(clave_, mensaje=mensaje).encode("utf-8"))
    return xades.sign(root, *p12_pin).decode("utf-8")


def _doc(**kw):
    base = {"clave": CLAVE, "issuer_id": "3101000001", "consecutive": CONS, "doc_type": "FE", "xml_supplier_response": None}
    return SimpleNamespace(**{**base, **kw})


def test_sello_de_hacienda_valido_alterado_y_ajeno():
    mh = _p12_hacienda()
    ok = _firmado(mh)
    assert idf.verificar_sello(ok) == ("ok", None)
    estado, por_que = idf.verificar_sello(ok.replace(">Aceptado<", ">Aceptad0<"))
    assert estado == "firma_invalida" and "modificado" in por_que
    assert idf.verificar_sello(_firmado(make_p12()))[0] == "certificado_ajeno"
    assert idf.verificar_sello(mensaje_hacienda(CLAVE))[0] == "sin_firma"
    assert idf.verificar_sello("no es xml")[0] == "firma_invalida"


def test_aval_confirmado_y_lo_que_bloquea():
    mh = _p12_hacienda()
    a = idf.aval(_doc(xml_supplier_response=_firmado(mh)))
    assert a["estado"] == "confirmado" and a["tone"] == "ok" and not a["bloquea"]
    # el consecutivo o la cedula del XML no son los de la clave: no es esa factura
    assert idf.aval(_doc(consecutive="00100001010000000999"))["bloquea"]
    assert idf.aval(_doc(issuer_id="3101999999"))["bloquea"]
    assert idf.aval(_doc(clave="123"))["bloquea"]
    # acuse de otra factura, o firmado por alguien que no es Hacienda
    otra = clave(consecutivo="00100001010000000124")
    assert idf.aval(_doc(xml_supplier_response=_firmado(mh, clave_=otra)))["estado"] == "no_coincide"
    assert idf.aval(_doc(xml_supplier_response=_firmado(make_p12())))["estado"] == "certificado_ajeno"


def test_aval_avisa_sin_bloquear():
    mh = _p12_hacienda()
    sin = idf.aval(_doc())
    assert sin["estado"] == "sin_acuse" and not sin["bloquea"]
    alterado = idf.aval(_doc(xml_supplier_response=_firmado(mh).replace(">Aceptado<", ">Aceptad0<")))
    assert alterado["estado"] == "firma_no_comprobable" and not alterado["bloquea"] and "La clave coincide" in alterado["detalle"]
    # pasaporte (alfanumerico): no se compara con la clave
    assert not idf.aval(_doc(issuer_id="AB1234567"))["bloquea"]
    assert idf.aval(_doc(xml_supplier_response=_firmado(mh, mensaje="3")))["estado"] == "rechazado"


def test_no_se_responde_una_factura_cuyo_identificador_no_coincide(client, auth):
    """El XML dice un consecutivo y la clave trae otro: no es esa factura. Ni respuesta ni gasto; la ficha dice por que."""
    from tests.hacienda_utils import fe_xml

    hoy = datetime.now(UTC).date()
    k = clave(fecha=hoy.strftime("%d%m%y"), consecutivo="00100001010000000777")
    xml = fe_xml(k, consecutivo="00100001010000000778", fecha=f"{hoy.isoformat()}T10:00:00-06:00", total=1130, iva=130)
    d = client.post("/reception/upload", files={"file": ("f.xml", xml.encode(), "application/xml")}).json()
    det = client.get(f"/reception/{d['id']}").json()
    assert det["identificador"]["estado"] == "no_coincide" and det["identificador"]["bloquea"]
    r = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada"})
    assert r.status_code == 409 and "no coincide" in r.json()["detail"]
