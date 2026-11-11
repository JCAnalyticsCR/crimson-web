"""Reglas que proponen el contenido de la tienda (Ola 3). Se registran en services/catalog_drafts por campo.

Todas son reglas y plantillas sobre lo que el producto ya tiene escrito: ninguna llama a una IA ni a un servicio
externo, y ninguna publica nada (solo proponen borradores que un administrador aprueba en «Contenido por aprobar»).
- web_name (O3-03, «nombre_v1»): product_names.commercial_name.
"""

from __future__ import annotations

from . import catalog_drafts as cd
from . import product_names


def _name(p, ctx: cd.GenCtx):
    return product_names.commercial_name(p.name, brand=p.brand, model=p.model, shown_type=ctx.dtype(p), description=p.description_store)


cd.register("web_name", "nombre_v1", _name)
