"""Proveedor de factura electronica (interfaz + sandbox). Alanube/GTI se enchufan aqui sin tocar el resto.

Contrato: emit(document_payload) -> EmitResult(provider_ref, status, clave, xml, message); status(provider_ref) -> EmitResult.
El sandbox simula Hacienda: genera clave de 50 digitos, XML minimo v4.4 y responde 'aceptada'.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class EmitResult:
    provider_ref: str
    status: str  # pendiente | aceptada | rechazada | error
    clave: str | None = None
    xml_document: str | None = None
    xml_response: str | None = None
    message: str | None = None


class EInvoiceProvider:
    name = "base"

    def emit(self, payload: dict) -> EmitResult:  # pragma: no cover - interfaz
        raise NotImplementedError

    def status(self, provider_ref: str) -> EmitResult:  # pragma: no cover
        raise NotImplementedError


def build_clave(country: str, day: datetime, emisor_id: str, consecutive: str, situation: str = "1", security: str | None = None) -> str:
    """Clave v4.4: pais(3) + dia(2) mes(2) ano(2) + cedula emisor(12) + consecutivo(20) + situacion(1) + codigo seguridad(8)."""
    ced = "".join(ch for ch in emisor_id if ch.isdigit()).zfill(12)[-12:]
    sec = (security or hashlib.sha256(f"{consecutive}{day.isoformat()}".encode()).hexdigest()[:8]).zfill(8)
    sec = "".join(str(int(c, 16) % 10) for c in sec)  # solo digitos
    return f"{country}{day:%d%m%y}{ced}{consecutive}{situation}{sec}"


class SandboxProvider(EInvoiceProvider):
    """Simula el proveedor: util para desarrollo, demos y tests de contrato del flujo (no valida esquema)."""

    name = "sandbox"

    def emit(self, payload: dict) -> EmitResult:
        now = datetime.now(UTC)
        clave = build_clave("506", now, payload["emisor"]["tax_id"] or "0", payload["consecutive"])
        lines = "".join(
            f"<LineaDetalle><NumeroLinea>{i + 1}</NumeroLinea><CodigoCABYS>{ln.get('cabys_code') or ''}</CodigoCABYS><Cantidad>{ln['quantity']}</Cantidad>"
            f"<UnidadMedida>{ln.get('unit') or 'Unid'}</UnidadMedida><Detalle>{ln['name']}</Detalle><PrecioUnitario>{ln['unit_price']}</PrecioUnitario>"
            f"<SubTotal>{ln['subtotal']}</SubTotal><Impuesto><Codigo>01</Codigo><Tarifa>{ln['tax_rate']}</Tarifa><Monto>{ln['tax_amount']}</Monto></Impuesto>"
            f"<MontoTotalLinea>{ln['total']}</MontoTotalLinea></LineaDetalle>"
            for i, ln in enumerate(payload["lines"])
        )
        rec = payload.get("receptor") or {}
        xml = (
            f'<?xml version="1.0" encoding="utf-8"?><{payload["root"]} xmlns="https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/{payload["root"].lower()}">'
            f"<Clave>{clave}</Clave><CodigoActividadEmisor>{payload.get('activity_code') or ''}</CodigoActividadEmisor><NumeroConsecutivo>{payload['consecutive']}</NumeroConsecutivo>"
            f"<FechaEmision>{now.isoformat()}</FechaEmision><Emisor><Nombre>{payload['emisor']['name']}</Nombre><Identificacion><Numero>{payload['emisor']['tax_id'] or ''}</Numero></Identificacion></Emisor>"
            + (
                f"<Receptor><Nombre>{rec.get('name')}</Nombre><Identificacion><Numero>{rec.get('id_number') or ''}</Numero></Identificacion></Receptor>"
                if rec
                else ""
            )
            + f"<CondicionVenta>{payload.get('sale_condition', '01')}</CondicionVenta><MedioPago>{payload.get('payment_method', '01')}</MedioPago><DetalleServicio>{lines}</DetalleServicio>"
            f"<ResumenFactura><CodigoTipoMoneda><CodigoMoneda>{payload['currency']}</CodigoMoneda><TipoCambio>{payload['fx']}</TipoCambio></CodigoTipoMoneda>"
            f"<TotalVenta>{payload['subtotal']}</TotalVenta><TotalDescuentos>{payload['discount_total']}</TotalDescuentos><TotalImpuesto>{payload['tax_total']}</TotalImpuesto><TotalComprobante>{payload['total']}</TotalComprobante></ResumenFactura>"
            + (
                f"<InformacionReferencia><TipoDoc>01</TipoDoc><Numero>{payload['reference_clave']}</Numero><Codigo>01</Codigo><Razon>Anulacion</Razon></InformacionReferencia>"
                if payload.get("reference_clave")
                else ""
            )
            + f"</{payload['root']}>"
        )
        resp = (
            '<?xml version="1.0" encoding="utf-8"?><MensajeHacienda><Clave>' + clave + "</Clave><NombreEmisorMensaje>SANDBOX</NombreEmisorMensaje>"
            "<Mensaje>1</Mensaje><DetalleMensaje>Comprobante aceptado (simulacion sandbox; sin validez fiscal)</DetalleMensaje></MensajeHacienda>"
        )
        return EmitResult(
            provider_ref=f"sbx-{clave[-12:]}", status="aceptada", clave=clave, xml_document=xml, xml_response=resp, message="Aceptado por sandbox"
        )

    def status(self, provider_ref: str) -> EmitResult:
        return EmitResult(provider_ref=provider_ref, status="aceptada")


class NoneProvider(EInvoiceProvider):
    name = "none"

    def emit(self, payload: dict) -> EmitResult:
        return EmitResult(provider_ref="", status="error", message="Sin proveedor fiscal configurado (EINVOICE_PROVIDER=sandbox|alanube|gti)")

    def status(self, provider_ref: str) -> EmitResult:
        return EmitResult(provider_ref=provider_ref, status="error")


def get_provider(name: str) -> EInvoiceProvider:
    # alanube / gti: adaptadores reales en Fase 2 con credenciales del tenant
    return {"sandbox": SandboxProvider}.get(name, NoneProvider)()
