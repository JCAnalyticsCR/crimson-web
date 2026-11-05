"""El identificador de una factura de proveedor (su clave de 50 digitos) avalado por Hacienda (05/11, pedido de Andres).

Hacienda empareja la venta que declara el proveedor con el gasto de la empresa por la CLAVE. Antes de responder o
registrar el gasto, Crimson comprueba dos cosas, sin llamar a Hacienda (sirve igual en Pruebas y en Produccion):

1. Que la clave sea coherente con su propio XML: 506 + fecha + cedula del emisor + consecutivo + situacion + codigo.
   Si la cedula o el consecutivo no calzan, el identificador NO es de esa factura: se bloquea.
2. Que el acuse que adjunto el proveedor (MensajeHacienda) lo firmo Hacienda: XMLDSig enveloped (Reference URI="" con
   XPath not(ancestor-or-self::ds:Signature)) + SignedProperties, exc-c14n, RSA-SHA256, con el sello electronico del
   Ministerio de Hacienda (cedula juridica 2-100-042005) emitido por la CA SINPE - PERSONA JURIDICA del BCCR; y que ese
   acuse sea de esta misma clave. signxml no aplica bien esa transformacion XPath (falla en las 19 reales): se verifica
   a mano, paso por paso del estandar. Verificado contra produccion: 18 de 19 validos; el de Prismar no, porque
   Gosocket altero el acuse ("&#13;" -> "0x26#13;") antes de enviarlo.

No se verifica la cadena hasta la raiz del BCCR (no hay almacen de confianza en el servidor): el certificado se reconoce
por su titular y su emisor. En Produccion, ademas, el mensaje receptor mismo lo valida Hacienda contra la clave.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import re
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
XPATH_ENVELOPED = "http://www.w3.org/TR/1999/REC-xpath-19991116"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
DIGESTS = {"http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256, "http://www.w3.org/2000/09/xmldsig#sha1": hashlib.sha1}
HACIENDA_SERIAL = "CPJ-2-100-042005"  # cedula juridica del Ministerio de Hacienda en el certificado del sello
EMISOR_CA = "CA SINPE - PERSONA JURIDICA"
# tipo de documento -> codigo en las posiciones 9-10 del consecutivo (Anexos v4.4)
TIPO = {"FE": "01", "ND": "02", "NC": "03", "TE": "04", "FEC": "08", "FEE": "09", "REP": "10"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=False)


C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
C14N_ALGS = (C14N, C14N + "#WithComments", EXC_C14N, EXC_C14N + "WithComments")


def _c14n(el, alg: str = C14N) -> bytes:
    """Canonicalizacion que DECLARA la firma (Hacienda usa exclusiva; otros firmadores, inclusiva, que es el defecto)."""
    return etree.tostring(el, method="c14n", exclusive=alg.startswith(EXC_C14N), with_comments=alg.endswith("WithComments"))


def _ref_c14n(ref) -> str:
    algs = [t.get("Algorithm") or "" for t in ref.findall(f"{{{DS}}}Transforms/{{{DS}}}Transform")]
    c = [a for a in algs if a in C14N_ALGS]
    return c[-1] if c else C14N


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def verificar_sello(xml_text: str) -> tuple[str, str | None]:
    """("ok" | "firma_invalida" | "certificado_ajeno" | "sin_firma", detalle). Nunca lanza."""
    try:
        root = etree.fromstring(xml_text.encode("utf-8"), _PARSER)
    except (etree.XMLSyntaxError, ValueError):
        return "firma_invalida", "el acuse no se puede leer como XML"
    sig = root.find(f".//{{{DS}}}Signature")
    if sig is None:
        return "sin_firma", "el acuse no trae firma"
    try:
        si = sig.find(f"{{{DS}}}SignedInfo")
        refs = si.findall(f"{{{DS}}}Reference")
        if not refs:
            return "firma_invalida", "la firma no tiene referencias"
        for ref in refs:
            uri = ref.get("URI")
            alg = DIGESTS.get(ref.find(f"{{{DS}}}DigestMethod").get("Algorithm") or "")
            if alg is None:
                return "firma_invalida", "algoritmo de resumen desconocido"
            if uri == "":
                # el documento entero sin la firma (la transformacion XPath / enveloped del estandar)
                doc = copy.deepcopy(root)
                for s in doc.findall(f".//{{{DS}}}Signature"):
                    s.getparent().remove(s)
                data = _c14n(doc, _ref_c14n(ref))
            elif uri and uri.startswith("#"):
                hits = root.xpath("//*[@Id=$i]", i=uri[1:])
                if len(hits) != 1:
                    return "firma_invalida", "referencia de la firma ambigua o ausente"
                data = _c14n(hits[0], _ref_c14n(ref))
            else:
                return "firma_invalida", "referencia externa en la firma"
            want = (ref.find(f"{{{DS}}}DigestValue").text or "").strip()
            if base64.b64encode(alg(data).digest()).decode() != want:
                return "firma_invalida", "el archivo fue modificado después de que Hacienda lo firmó"
        der = base64.b64decode(re.sub(r"\s", "", sig.find(f".//{{{DS}}}X509Certificate").text or ""))
        cert = x509.load_der_x509_certificate(der)
        si_alg = si.find(f"{{{DS}}}CanonicalizationMethod").get("Algorithm") or C14N
        cert.public_key().verify(base64.b64decode(re.sub(r"\s", "", sig.find(f"{{{DS}}}SignatureValue").text or "")), _c14n(si, si_alg), padding.PKCS1v15(), hashes.SHA256())
    except Exception:  # noqa: BLE001 - cualquier forma rara del acuse es "no comprobable", nunca un 500
        return "firma_invalida", "la firma no corresponde al contenido"
    subject = cert.subject.rfc4514_string()
    issuer = cert.issuer.rfc4514_string()
    if HACIENDA_SERIAL not in subject or EMISOR_CA not in issuer:
        return "certificado_ajeno", "el acuse está firmado, pero no con el sello electrónico de Hacienda"
    return "ok", None


@lru_cache(maxsize=2048)
def _sello_cached(clave: str, largo: int, cola: str, xml_text: str) -> tuple[str, str | None]:
    return verificar_sello(xml_text)


def _acuse(xml_text: str) -> dict:
    def t(tag: str) -> str | None:
        m = re.search(rf"<(?:\w+:)?{tag}>\s*([^<]*?)\s*</(?:\w+:)?{tag}>", xml_text)
        return m.group(1) if m else None

    return {"clave": t("Clave"), "emisor": t("NumeroCedulaEmisor"), "mensaje": t("Mensaje")}


def _ui(estado: str, tone: str, label: str, detalle: str | None = None, bloquea: bool = False) -> dict:
    return {"estado": estado, "tone": tone, "label": label, "detalle": detalle, "bloquea": bloquea}


def aval(doc) -> dict:
    """Estado del identificador de un comprobante recibido: lo que se muestra y si impide seguir."""
    clave = doc.clave or ""
    if not re.fullmatch(r"506\d{47}", clave):
        return _ui("no_coincide", "bad", "El identificador no es válido", "La clave no tiene 50 dígitos o no empieza con 506", True)
    raw_ced = (doc.issuer_id or "").strip()
    ced = _digits(raw_ced) if re.fullmatch(r"[\d\s-]{9,15}", raw_ced) else ""  # pasaporte u otra alfanumerica: no se compara
    if ced and clave[9:21] != ced.zfill(12)[-12:]:
        return _ui("no_coincide", "bad", "El identificador no coincide", "La cédula del emisor no es la que trae la clave: no corresponde a esta factura", True)
    if doc.consecutive and clave[21:41] != doc.consecutive:
        return _ui("no_coincide", "bad", "El identificador no coincide", "El consecutivo del XML no es el que trae la clave: no corresponde a esta factura", True)
    avisos = []
    tipo = TIPO.get(doc.doc_type or "")
    if tipo and clave[29:31] != tipo:
        avisos.append("el tipo de documento de la clave no es el del XML")
    xr = doc.xml_supplier_response
    if not xr:
        return _ui("sin_acuse", "warn", "Falta el acuse de Hacienda", "El proveedor no adjuntó la respuesta de Hacienda a su factura; pídasela para confirmar el identificador")
    a = _acuse(xr)
    if a["clave"] and a["clave"] != clave:
        return _ui("no_coincide", "bad", "El acuse es de otra factura", "La respuesta de Hacienda que adjuntó el proveedor trae otra clave", True)
    if a["emisor"] and ced and _digits(a["emisor"]).zfill(12)[-12:] != ced.zfill(12)[-12:]:
        return _ui("no_coincide", "bad", "El acuse es de otro emisor", "La respuesta de Hacienda que adjuntó el proveedor es de otra cédula", True)
    sello, por_que = _sello_cached(clave, len(xr), xr[-64:], xr)
    if sello == "certificado_ajeno":
        return _ui("certificado_ajeno", "bad", "El acuse no es de Hacienda", por_que, True)
    if sello != "ok":
        return _ui("firma_no_comprobable", "warn", "Acuse sin firma comprobable", f"La clave coincide, pero {por_que}. Pida al proveedor el acuse original de Hacienda")
    if a["mensaje"] == "3":
        return _ui("rechazado", "bad", "Hacienda rechazó esta factura al proveedor", "No da crédito de IVA ni se responde")
    det = "Firma del sello electrónico de Hacienda verificada y la clave coincide con la factura"
    if avisos:
        det += " · Ojo: " + "; ".join(avisos)
    return _ui("confirmado", "ok", "Hacienda confirmó el identificador", det)
