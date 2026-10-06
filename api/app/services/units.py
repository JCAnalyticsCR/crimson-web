"""Unidades de medida y tratamiento de lineas de cotizacion/factura.

Unidades: en la linea se guarda la clave que ve el usuario (p. ej. "jornada"); al emitir a Hacienda se traduce
a la unidad oficial v4.4 (Unid, m, m², Sp, Os, d, h). Asi "dia" y "jornada" siguen distinguiendose en el portal
y en el PDF aunque ambas viajen como "d".

Tratamiento de linea: explica por que una linea esta en 0 o no suma.
  normal    -> suma al total
  pendiente -> falta costo o precio del proveedor; se puede guardar pero NO enviar ni convertir
  aportado  -> equipo del cliente o de un aliado; sale en el documento con leyenda y no suma
  cortesia  -> sale con leyenda "Cortesía" y no suma
  excluido  -> sale en la seccion de exclusiones y no suma
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException

# clave guardada -> (etiqueta, unidad oficial de Hacienda, es servicio)
UNITS: dict[str, tuple[str, str, bool]] = {
    "Unid": ("Unidad", "Unid", False),
    "m": ("Metro", "m", False),
    "m²": ("Metro cuadrado", "m²", False),
    "servicio": ("Servicio", "Os", True),
    "Sp": ("Servicio profesional", "Sp", True),
    "dia": ("Día", "d", True),
    "jornada": ("Jornada", "d", True),
    "hora": ("Hora", "h", True),
    "mes": ("Mes", "Os", True),
    "Os": ("Otro servicio", "Os", True),
}
SERVICE_UNITS = {k for k, v in UNITS.items() if v[2]}
METER_UNITS = {"m", "mts", "mt", "metro", "metros"}


def hacienda_unit(unit: str | None) -> str:
    """Unidad oficial para el XML. Lo que no esta en la tabla viaja tal cual (datos viejos ya en codigo oficial)."""
    u = (unit or "").strip() or "Unid"
    return UNITS[u][1] if u in UNITS else u


def unit_label(unit: str | None) -> str:
    """Como se imprime junto a la cantidad: "2 día", "1 servicio", "10 m"."""
    u = (unit or "").strip()
    return "día" if u == "dia" else u


TREATMENTS = ("normal", "pendiente", "aportado", "cortesia", "excluido")
NO_SUMA = {"aportado", "cortesia", "excluido"}
LEYENDA = {
    "aportado": "Equipo aportado por el cliente/aliado",
    "cortesia": "Cortesía",
    "excluido": "Excluido de esta oferta",
    "pendiente": "Precio pendiente",
}


def counts(treatment: str | None) -> bool:
    return (treatment or "normal") not in NO_SUMA


def _names(lines) -> str:
    names = [ln.name for ln in lines]
    return ", ".join(names[:6]) + (f" y {len(names) - 6} más" if len(names) > 6 else "")


def check_ready(doc, accion: str) -> None:
    """409 si la cotizacion no puede salir al cliente ni convertirse: lineas pendientes o en 0 sin explicar."""
    pend = [ln for ln in doc.lines if (ln.treatment or "normal") == "pendiente"]
    if pend:
        raise HTTPException(
            409,
            f"No se puede {accion}: {len(pend)} línea(s) pendientes de costo o precio del proveedor ({_names(pend)}). "
            "Completá el precio o cambiá el tratamiento.",
        )
    ceros = [ln for ln in doc.lines if (ln.treatment or "normal") == "normal" and Decimal(str(ln.unit_price or 0)) <= 0]
    if ceros:
        raise HTTPException(
            409,
            f"No se puede {accion}: {len(ceros)} línea(s) en 0 sin explicar ({_names(ceros)}). "
            "Elegí si son pendientes, aportadas por el cliente/aliado, cortesía o exclusiones.",
        )
