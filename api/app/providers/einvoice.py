"""Proveedor de factura electronica (interfaz). Implementaciones en providers/hacienda/provider.py:
SimulatedHacienda (modo simulado, sin validez fiscal) y HaciendaDirectProvider (directo con Hacienda). Un intermediario
(Alanube) podria enchufarse despues sin tocar el resto.

Contrato: emit(payload) -> EmitResult; status(ref) -> EmitResult; send_receptor_message / receptor_status para el
mensaje receptor. services/einvoice.py y services/hacienda_sync.py eligen el proveedor con provider_for().
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

# cr_now vive en core/timeutil (una sola hora de Costa Rica para todo); se reexporta para los imports de siempre
from ..core.timeutil import CR, cr_now  # noqa: F401


@dataclass
class EmitResult:
    provider_ref: str
    status: str  # pendiente | por_enviar | enviado | aceptada | rechazada | error | simulado
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

    def send_receptor_message(self, resp, doc=None, tenant=None, receiver_digits: str = "") -> EmitResult:  # pragma: no cover
        raise NotImplementedError

    def receptor_status(self, provider_ref: str) -> EmitResult:  # pragma: no cover
        raise NotImplementedError


def security_code() -> str:
    return f"{secrets.randbelow(10**8):08d}"


def build_clave(country: str, day: datetime, emisor_id: str, consecutive: str, situation: str = "1", security: str | None = None) -> str:
    """Clave v4.4: pais(3) + dia(2) mes(2) ano(2) + cedula emisor(12) + consecutivo(20) + situacion(1) + seguridad(8).
    `day` debe venir en hora de Costa Rica (si trae otra zona se convierte)."""
    if day.tzinfo is not None:
        day = day.astimezone(CR)
    ced = "".join(ch for ch in emisor_id if ch.isdigit()).zfill(12)[-12:]
    sec = "".join(ch for ch in (security or security_code()) if ch.isdigit()).zfill(8)[-8:]
    return f"{country}{day:%d%m%y}{ced}{consecutive}{situation}{sec}"


class NoneProvider(EInvoiceProvider):
    name = "none"

    def emit(self, payload: dict) -> EmitResult:
        return EmitResult(provider_ref="", status="error", message="Sin proveedor fiscal configurado (Ajustes → Hacienda: elija el modo)")

    def status(self, provider_ref: str) -> EmitResult:
        return EmitResult(provider_ref=provider_ref, status="error")


def get_provider(name: str) -> EInvoiceProvider:
    """Compatibilidad: none -> NoneProvider, sandbox -> SimulatedHacienda. Alanube/GTI no estan: se tratan como none.
    Para Hacienda directo usar services.einvoice.provider_for(db, tenant, purpose)."""
    if name == "sandbox":
        from .hacienda.provider import SimulatedHacienda

        return SimulatedHacienda()
    return NoneProvider()


def __getattr__(name: str):  # SandboxProvider = SimulatedHacienda (alias para no romper imports viejos)
    if name == "SandboxProvider":
        from .hacienda.provider import SimulatedHacienda

        return SimulatedHacienda
    raise AttributeError(name)
