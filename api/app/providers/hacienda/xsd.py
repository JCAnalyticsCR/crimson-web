"""Validacion contra los XSD oficiales v4.4 (copiados en xsd/esquemas, sin red). Todo lo que sale a Hacienda se valida
antes de enviarse: si hay errores no se envia (es un bug nuestro, no de Hacienda).

lxml no sirve: los XSD declaran vc:minVersion="1.1"; se usa xmlschema.XMLSchema11 con allow="local".
"""

from __future__ import annotations

import functools
import threading
from pathlib import Path

import xmlschema

BASE = Path(__file__).parent / "xsd" / "esquemas" / "2024" / "v4.4"
FILES = {
    "mensaje_receptor": "MensajeReceptor_V4.4.xsd",
    "mensaje_hacienda": "MensajeHacienda_V4.4.xsd",
    "FE": "FacturaElectronica_V4.4.xsd",
    "TE": "TiqueteElectronico_V4.4.xsd",
    "NC": "NotaCreditoElectronica_V4.4.xsd",
}
_lock = threading.Lock()


@functools.cache
def schema(kind: str) -> xmlschema.XMLSchema11:
    if kind not in FILES:
        raise ValueError(f"Esquema desconocido: {kind}")
    with _lock:
        return xmlschema.XMLSchema11(str(BASE / FILES[kind]), allow="local")


def validate(kind: str, xml_bytes: bytes | str) -> list[str]:
    """Errores de validacion (vacio = valido). Mensajes cortos: ruta + motivo."""
    data = xml_bytes.decode("utf-8") if isinstance(xml_bytes, bytes) else xml_bytes
    out = []
    for e in schema(kind).iter_errors(data):
        path = getattr(e, "path", None) or ""
        reason = getattr(e, "reason", None) or str(e).splitlines()[0]
        out.append(f"{path}: {reason}"[:300])
        if len(out) >= 20:
            break
    return out
