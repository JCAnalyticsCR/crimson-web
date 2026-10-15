"""Proveedores fiscales sobre la interfaz EInvoiceProvider: simulado (sin validez fiscal) y directo con Hacienda.

El directo expone pasos separados (firmar una vez, enviar, consultar) porque services/hacienda_sync guarda los bytes
firmados y hace commit ANTES del POST (D6): si un POST llega a Hacienda y se pierde la respuesta, el reintento manda
exactamente esos bytes.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from lxml import etree

from ..einvoice import EInvoiceProvider, EmitResult
from . import xades, xml44, xsd
from .client import HaciendaClient, PostResult, StatusResult


class InvalidXml(Exception):
    """El XML que armamos no pasa el XSD: es un bug nuestro, no se envia."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[:3]))


@dataclass(repr=False)
class Credentials:
    environment: str
    source: str  # ajustes | entorno
    username: str
    password: str
    p12: bytes
    pin: str

    def __repr__(self) -> str:  # nunca muestra nada sensible (ni en logs ni en volcados)
        return f"Credentials(environment={self.environment!r}, source={self.source!r})"

    __str__ = __repr__


def validated(kind: str, data: bytes) -> bytes:
    errs = xsd.validate(kind, data)
    if errs:
        raise InvalidXml(errs)
    return data


class SimulatedHacienda(EInvoiceProvider):
    """Modo simulado: arma el comprobante con el constructor real, lo firma con el certificado de simulacion y valida el
    XSD. Responde "aceptada" con un MensajeHacienda que dice SIMULACION · sin validez fiscal. Nada sale a Hacienda."""

    name = "sandbox"  # se guarda en EInvoiceDocument.provider (la tienda arma la nota "SANDBOX: sin validez fiscal")

    def sign_document(self, payload: dict) -> bytes:
        root = xml44.build_comprobante(payload)
        return validated(payload["doc_type"], xades.sign_simulated(root))

    def emit(self, payload: dict) -> EmitResult:
        signed = self.sign_document(payload)
        clave = payload["clave"]
        resp = xml44.mensaje_hacienda_simulado(clave, xml44.digits(payload["emisor"]["id_number"]), (payload.get("receptor") or {}).get("id_number"), payload.get("tax_total"), payload.get("total"))
        return EmitResult(
            provider_ref=f"sbx-{clave[-12:]}",
            status="aceptada",
            clave=clave,
            xml_document=signed.decode("utf-8"),
            xml_response=resp,
            message="SIMULACIÓN · sin validez fiscal",
        )

    def status(self, provider_ref: str) -> EmitResult:
        return EmitResult(provider_ref=provider_ref, status="aceptada")

    def sign_receptor(self, root: etree._Element) -> bytes:
        return validated("mensaje_receptor", xades.sign_simulated(root))

    def send_receptor_message(self, resp, doc=None, tenant=None, receiver_digits: str = "") -> EmitResult:
        signed = self.sign_receptor(xml44.build_mensaje_receptor(resp, doc, tenant, receiver_digits))
        return EmitResult(provider_ref=f"sbx-{resp.consecutive}", status="simulado", xml_document=signed.decode("utf-8"))

    def receptor_status(self, provider_ref: str) -> EmitResult:
        return EmitResult(provider_ref=provider_ref, status="simulado")


class HaciendaDirectProvider(EInvoiceProvider):
    """Directo con Hacienda (pruebas o produccion). La guarda de produccion vive en HaciendaClient."""

    name = "hacienda"

    def __init__(self, tenant, environment: str, creds: Credentials, http=None, production_instance: str | None = None):
        self.tenant = tenant
        self.environment = environment
        self.creds = creds
        self.client = HaciendaClient(
            environment, creds.username, creds.password, tenant_id=tenant.id, production_instance=production_instance, http=http
        )

    # ---------- firmar (una sola vez)
    def sign_document(self, payload: dict) -> bytes:
        root = xml44.build_comprobante(payload)
        return validated(payload["doc_type"], xades.sign(root, self.creds.p12, self.creds.pin))

    def sign_receptor(self, root: etree._Element) -> bytes:
        return validated("mensaje_receptor", xades.sign(root, self.creds.p12, self.creds.pin))

    # ---------- enviar / consultar
    def post(self, body: dict) -> PostResult:
        return self.client.post_recepcion(body)

    def get_status(self, ref: str) -> StatusResult:
        return self.client.get_estado(ref)

    # ---------- interfaz EInvoiceProvider (herramientas; el flujo normal pasa por hacienda_sync)
    def emit(self, payload: dict) -> EmitResult:
        signed = self.sign_document(payload)
        res = self.post(comprobante_body(payload, signed))
        status = {"ok": "enviado", "already_received": "enviado", "retry": "por_enviar"}.get(res.kind, "error")
        return EmitResult(provider_ref=payload["clave"], status=status, clave=payload["clave"], xml_document=signed.decode("utf-8"), message=res.cause)

    def status(self, provider_ref: str) -> EmitResult:
        st = self.get_status(provider_ref)
        mapped = {"aceptado": "aceptada", "rechazado": "rechazada"}.get(st.kind, "pendiente" if st.kind in ("procesando", "retry", "not_found") else "error")
        return EmitResult(provider_ref=provider_ref, status=mapped, xml_response=st.xml, message=st.detail)

    def send_receptor_message(self, resp, doc=None, tenant=None, receiver_digits: str = "") -> EmitResult:  # pragma: no cover - hacienda_sync
        raise NotImplementedError("El mensaje receptor se envía con services.hacienda_sync.send_response")

    def receptor_status(self, provider_ref: str) -> EmitResult:  # pragma: no cover
        return self.status(provider_ref)


def _party(id_type: str | None, number: str | None) -> dict:
    return {"tipoIdentificacion": id_type or "02", "numeroIdentificacion": number or ""}


def comprobante_body(payload: dict, signed: bytes) -> dict:
    em = payload["emisor"]
    body = {
        "clave": payload["clave"],
        "fecha": xml44.cr_iso(payload["fecha"]),
        "emisor": _party(em["id_type"], em["id_number"]),
        "comprobanteXml": base64.b64encode(signed).decode(),
    }
    rc = payload.get("receptor")
    if rc and rc.get("id_number"):
        body["receptor"] = _party(rc.get("id_type"), rc["id_number"])
    return body


def receptor_body(resp, doc, receiver_type: str, receiver_digits: str, signed: bytes) -> dict:
    return {
        "clave": doc.clave,
        "fecha": xml44.cr_iso(resp.emitted_at),
        "emisor": _party(doc.issuer_id_type, doc.issuer_id),
        "receptor": _party(receiver_type, receiver_digits),
        "comprobanteXml": base64.b64encode(signed).decode(),
        "consecutivoReceptor": resp.consecutive,
    }


def receptor_ref(doc, resp) -> str:
    """Ruta de consulta del mensaje receptor: {clave}-{consecutivo} (practica de la comunidad; confirmar en pruebas)."""
    return f"{doc.clave}-{resp.consecutive}"
