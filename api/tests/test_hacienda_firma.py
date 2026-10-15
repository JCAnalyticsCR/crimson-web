"""Firma XAdES-EPES (Anexo 2): politica con su hash, SigningCertificate v1 (sin V2), c14n 1.0; verificable con
XAdESVerifier y valida contra el XSD oficial. El .p12 se genera al correr (nada de llaves en el repo)."""

from datetime import datetime
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from signxml.xades import XAdESVerifier

from app.providers.hacienda import xml44, xsd
from app.providers.hacienda.xades import POLICY_SHA256_B64, SIGNATURE_POLICY, CertError, load_p12, sign, sign_simulated, simulation_cert
from tests.hacienda_utils import EMPRESA, clave, make_p12


def _mr():
    return xml44.mensaje_receptor_element(
        clave=clave(),
        issuer_id="3101000001",
        emitted_at=datetime(2026, 10, 14, 10, 0, tzinfo=xml44.CR),
        message_code="1",
        detail=None,
        tax_total=Decimal("13000"),
        activity_code="6202.0",
        condition_code="01",
        credit=Decimal("13000"),
        applicable=Decimal("100000"),
        include_amounts=False,
        total=Decimal("113000"),
        receiver_digits=EMPRESA,
        consecutive="00100001050000000001",
    )


def test_firma_xades_epes_verificable(p12_empresa):
    p12, pin = p12_empresa
    signed = sign(_mr(), p12, pin)
    _, cert, _ = pkcs12.load_key_and_certificates(p12, pin.encode())
    res = XAdESVerifier().verify(signed, x509_cert=cert.public_bytes(serialization.Encoding.PEM), expect_signature_policy=SIGNATURE_POLICY, expect_references=3)
    assert len(res) == 3
    assert b"SigningCertificateV2" not in signed and b"SigningCertificate" in signed
    assert b"SignaturePolicyIdentifier" in signed and POLICY_SHA256_B64.encode() in signed
    assert b"http://www.w3.org/TR/2001/REC-xml-c14n-20010315" in signed  # c14n 1.0, como Fygaro
    assert signed.startswith(b"<?xml version='1.0' encoding='UTF-8'?>") or signed.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert xsd.validate("mensaje_receptor", signed) == []


def test_load_p12_rechaza_pin_vencido_y_rsa_debil(p12_empresa):
    p12, pin = p12_empresa
    info = load_p12(p12, pin)
    assert info.key_bits == 2048 and info.days_left > 20 and "CPJ" in (info.serial_number_attr or "") and len(info.sha256) == 64
    with pytest.raises(CertError, match="PIN incorrecto"):
        load_p12(p12, pin + "x")
    vencido, pin_v = make_p12(EMPRESA, expired=True)
    with pytest.raises(CertError, match="venció"):
        load_p12(vencido, pin_v)
    debil, pin_d = make_p12(EMPRESA, key_size=1024)
    with pytest.raises(CertError, match="2048"):
        load_p12(debil, pin_d)


def test_certificado_de_simulacion_pasa_el_xsd():
    _, cert = simulation_cert()
    assert "SIMULACION SIN VALIDEZ FISCAL" in cert.subject.rfc4514_string()
    assert xsd.validate("mensaje_receptor", sign_simulated(_mr())) == []
