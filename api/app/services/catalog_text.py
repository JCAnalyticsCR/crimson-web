"""Lo que la tienda MUESTRA de un producto (Ola 3), separado de lo fiscal.

Product.name y Product.code son del comprobante (cotizacion, factura, kardex, Hacienda) y no se tocan. La tienda muestra:
- shown_name: el nombre comercial aprobado (web_name) o, si no hay, el de siempre (O3-01).
- shown_brand: la marca con su grafia oficial (services/brands, O3-02).
- shown_model: el modelo sin parentesis truncados ni marcas de lote; «TAPO» y parecidos no son un modelo (O3-02).
- public_code: el codigo que ve el cliente (ficha, WhatsApp, JSON-LD): el modelo limpio o, sin el, el codigo propio
  sin el prefijo «EUR-» ni marcas de lote (O3-02). Product.code sigue igual (es unico y vive en kardex y facturas).
La direccion de la ficha (store_showcase.product_path) sigue saliendo del nombre de siempre: no se mueve ninguna URL.
"""

from __future__ import annotations

import re

from .brands import canonical_brand

_LOT = re.compile(r"\s*\((?:us|am|am-std|o-std|std|e|d|eu|la|latam[^)]*)\)", re.I)
_NOT_A_MODEL = {"tapo", "ezviz", "hikvision", "generico", "generica", "n/a", "na", "s/m", "sin modelo", "varios", "kit"}
_PREFIX = re.compile(r"^EUR-", re.I)


def shown_name(p) -> str:
    """Nombre que ve el cliente: el comercial aprobado o, sin el, el nombre de siempre."""
    return (getattr(p, "web_name", None) or "").strip() or (p.name or "")


def shown_brand(p) -> str | None:
    return canonical_brand(getattr(p, "brand", None))


def clean_model(model: str | None) -> str | None:
    """modelo para mostrar: sin marcas de lote ni parentesis abierto al final (truncado); None si no es un modelo"""
    m = " ".join(str(model or "").split())
    if not m:
        return None
    m = _LOT.sub("", m).strip()
    while m.count("(") > m.count(")"):
        m = m[: m.rfind("(")].rstrip(" -/")
    m = m.strip(" -/")
    low = m.lower()
    if not m or low in _NOT_A_MODEL or not any(c.isdigit() for c in m):
        return None
    if low.startswith("tapo ") and len(m) > 5:  # «TAPO C310» -> «C310» (Tapo es la linea, no el modelo)
        m = m[5:].strip()
    return m


def shown_model(p) -> str | None:
    return clean_model(getattr(p, "model", None))


def public_code(p) -> str:
    """Codigo que ve el cliente. Nunca empieza con «EUR-»."""
    m = shown_model(p)
    if m:
        return m
    c = _LOT.sub("", " ".join(str(p.code or "").split())).strip()
    while c.count("(") > c.count(")"):
        c = c[: c.rfind("(")].rstrip(" -/")
    c = _PREFIX.sub("", c).strip(" -/")
    return c or str(p.code or "")
