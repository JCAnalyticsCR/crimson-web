"""Reglas que proponen el contenido de la tienda (Ola 3). Se registran en services/catalog_drafts por campo.

Todas son reglas y plantillas sobre lo que el producto ya tiene escrito: ninguna llama a una IA ni a un servicio
externo, y ninguna publica nada (solo proponen borradores que un administrador aprueba en «Contenido por aprobar»).
- web_name (O3-03, «nombre_v1»): product_names.commercial_name.
- web_specs (O3-05, «ficha_v1»): las viñetas «- Clave: valor» de la descripcion (sin rellenos) y lo que el nombre dice
  con numero y unidad por familia. Solo se copia el dato, nada se interpreta.
"""

from __future__ import annotations

from . import catalog_drafts as cd
from . import product_names
from .catalog_fichas import family_specs, specs_from_text


def _name(p, ctx: cd.GenCtx):
    return product_names.commercial_name(p.name, brand=p.brand, model=p.model, shown_type=ctx.dtype(p), description=p.description_store)


def _specs(p, ctx: cd.GenCtx):
    out = specs_from_text(p.description_store)
    for k, v in family_specs(ctx.dtype(p), p.name).items():
        out.setdefault(k, v)
    return (out or None), ("pocos datos" if len(out) == 1 else None)


cd.register("web_name", "nombre_v1", _name)
cd.register("web_specs", "ficha_v1", _specs)
