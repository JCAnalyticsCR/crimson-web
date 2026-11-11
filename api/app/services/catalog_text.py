"""Lo que la tienda MUESTRA de un producto (Ola 3), separado de lo fiscal.

Product.name y Product.code son del comprobante (cotizacion, factura, kardex, Hacienda) y no se tocan. La tienda muestra:
- shown_name: el nombre comercial aprobado (web_name) o, si no hay, el de siempre.
La direccion de la ficha (store_showcase.product_path) sigue saliendo del nombre de siempre: no se mueve ninguna URL.
"""

from __future__ import annotations


def shown_name(p) -> str:
    """Nombre que ve el cliente: el comercial aprobado o, sin el, el nombre de siempre."""
    return (getattr(p, "web_name", None) or "").strip() or (p.name or "")
