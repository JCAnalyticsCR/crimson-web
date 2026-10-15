"""Firma XAdES-EPES (Anexo 2 de la resolucion de comprobantes v4.4) con signxml, y lectura del certificado .p12.

El .p12 y el PIN viven solo en variables locales de estas funciones (nunca en disco, logs ni Celery). Si algun dia se
agrega Sentry u otro rastreador de errores, va con include_local_variables=False: si no, un error al firmar subiria el
PIN y el .p12 en el volcado de variables.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod
from signxml.xades import XAdESDataObjectFormat, XAdESSignaturePolicy, XAdESSigner

POLICY_URL = (
    "https://cdn.comprobanteselectronicos.go.cr/xml-schemas/"
    "Resoluci%C3%B3n_General_sobre_disposiciones_t%C3%A9cnicas_comprobantes_electr%C3%B3nicos_para_efectos_tributarios.pdf"
)
POLICY_SHA256_B64 = "DWxin1xWOeI8OuWQXazh4VjLWAaCLAA954em7DMh0h8="  # comprobado contra el PDF de la politica
NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"


class CertError(Exception):
    """El certificado no sirve (PIN, vencido, llave debil). Mensaje legible, sin secretos."""


@dataclass
class CertInfo:
    subject: str
    issuer: str
    subject_cn: str | None
    issuer_cn: str | None
    serial: str
    serial_number_attr: str | None  # serialNumber del sujeto (CPJ-..., CPF-...)
    not_before: datetime
    not_after: datetime
    sha256: str
    key_bits: int

    @property
    def days_left(self) -> int:
        return (self.not_after - datetime.now(UTC)).days


class HaciendaXAdESSigner(XAdESSigner):
    """Solo SigningCertificate v1 (CertDigest SHA-256 + IssuerSerial), como el ejemplo del Anexo 2; sin SigningCertificateV2."""

    def add_signing_certificate(self, ssp, sig_root, signing_settings):
        cert = signing_settings.cert_chain[0]
        if not isinstance(cert, x509.Certificate):
            cert = x509.load_pem_x509_certificate(cert if isinstance(cert, bytes) else cert.encode())
        sc = etree.SubElement(ssp, f"{{{NS_XADES}}}SigningCertificate")
        c = etree.SubElement(sc, f"{{{NS_XADES}}}Cert")
        cd = etree.SubElement(c, f"{{{NS_XADES}}}CertDigest")
        etree.SubElement(cd, f"{{{NS_DS}}}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        etree.SubElement(cd, f"{{{NS_DS}}}DigestValue").text = base64.b64encode(hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()).decode()
        isr = etree.SubElement(c, f"{{{NS_XADES}}}IssuerSerial")
        etree.SubElement(isr, f"{{{NS_DS}}}X509IssuerName").text = cert.issuer.rfc4514_string()
        etree.SubElement(isr, f"{{{NS_DS}}}X509SerialNumber").text = str(cert.serial_number)


SIGNATURE_POLICY = XAdESSignaturePolicy(Identifier=POLICY_URL, Description="", DigestMethod=DigestAlgorithm.SHA256, DigestValue=POLICY_SHA256_B64)


def _signer() -> HaciendaXAdESSigner:
    return HaciendaXAdESSigner(
        signature_policy=SIGNATURE_POLICY,
        data_object_format=XAdESDataObjectFormat(Description="", MimeType="text/xml"),
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0,
    )


def _cn(name: x509.Name) -> str | None:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else None


def _load(p12: bytes, pin: str):
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(p12, (pin or "").encode("utf-8"))
    except Exception as e:  # noqa: BLE001 - ValueError del PIN o del archivo: mismo mensaje, sin detalles
        raise CertError("PIN incorrecto o archivo dañado") from e
    if key is None or cert is None:
        raise CertError("El archivo .p12 no trae llave privada y certificado")
    return key, cert, list(extra or [])


def info_of(cert: x509.Certificate) -> CertInfo:
    sn = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    pub = cert.public_key()
    return CertInfo(
        subject=cert.subject.rfc4514_string()[:300],
        issuer=cert.issuer.rfc4514_string()[:300],
        subject_cn=_cn(cert.subject),
        issuer_cn=_cn(cert.issuer),
        serial=str(cert.serial_number)[:80],
        serial_number_attr=str(sn[0].value) if sn else None,
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        sha256=cert.fingerprint(hashes.SHA256()).hex(),
        key_bits=pub.key_size if isinstance(pub, rsa.RSAPublicKey) else 0,
    )


def load_p12(p12: bytes, pin: str) -> CertInfo:
    """Valida el .p12 con su PIN: RSA >= 2048 y vigente. Devuelve los metadatos (nunca la llave)."""
    key, cert, _ = _load(p12, pin)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise CertError("La llave del certificado debe ser RSA de al menos 2048 bits")
    info = info_of(cert)
    if info.not_after <= datetime.now(UTC):
        raise CertError(f"El certificado venció el {info.not_after:%d/%m/%Y}")
    return info


def sign(root: etree._Element, p12: bytes, pin: str, *, include_chain: bool = False) -> bytes:
    """Firma ENVELOPED el comprobante o mensaje. Devuelve los bytes con declaracion UTF-8 (lo que se guarda y se envia)."""
    key, cert, extra = _load(p12, pin)
    return _sign_with(root, key, cert, extra if include_chain else [])


def _sign_with(root: etree._Element, key, cert, extra: list) -> bytes:
    signed = _signer().sign(root, key=key, cert=[cert, *extra])
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")


_sim_lock = threading.Lock()
_sim: dict = {}


def simulation_cert():
    """Par llave/certificado autofirmado efimero (RSA 2048, CN=SIMULACION SIN VALIDEZ FISCAL), uno por proceso. Sirve
    para que el XML simulado tenga la misma forma que el real y pase el XSD. Jamas se envia a Hacienda."""
    with _sim_lock:
        if "pair" not in _sim:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SIMULACION SIN VALIDEZ FISCAL")])
            now = datetime.now(UTC)
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(int.from_bytes(os.urandom(8), "big") >> 1)  # serie corta: libxml2 falla con ~48 digitos
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .sign(key, hashes.SHA256())
            )
            _sim["pair"] = (key, cert)
        return _sim["pair"]


def sign_simulated(root: etree._Element) -> bytes:
    key, cert = simulation_cert()
    return _sign_with(root, key, cert, [])
