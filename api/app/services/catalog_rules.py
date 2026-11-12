"""Reglas que proponen el contenido de la tienda (Ola 3). Se registran en services/catalog_drafts por campo.

Todas son reglas y plantillas sobre lo que el producto ya tiene escrito: ninguna llama a una IA ni a un servicio
externo, y ninguna publica nada (solo proponen borradores que un administrador aprueba en «Contenido por aprobar»).
- web_name (O3-03, «nombre_v4»): product_names.commercial_name, solo con el nombre original y la ficha aprobada (clave
  exacta). nombre_v1 sacaba datos de la descripcion libre y fallo la revision de la muestra real: el lote nuevo se
  distingue por la regla.
- web_specs (O3-05, «ficha_v1»): las viñetas «- Clave: valor» de la descripcion (sin rellenos) y lo que el nombre dice
  con numero y unidad por familia. Solo se copia el dato, nada se interpreta.
- web_description (O3-06, «descripcion_v1»): solo para lo que no tiene texto propio (o solo la linea del proveedor en
  mayusculas); una frase de plantilla por familia y de 3 a 5 viñetas con los datos de la ficha APROBADA (web_specs) o
  escrita a mano (specs). Sin adjetivos que el dato no sostenga.
- web_type (O3-04, «tipo_v1»): solo para lo que hoy sale SIN tipo; el que dice el comienzo del nombre («Parlante…»,
  «Router…») o, si no, la categoria en singular. La llave de los kits (product_type) no cambia.
"""

from __future__ import annotations

from . import catalog_drafts as cd
from . import product_names
from .catalog_fichas import describe, family_specs, specs_from_text, store_description


def _name(p, ctx: cd.GenCtx):
    specs = {**(p.web_specs or {}), **(p.specs or {})}
    return product_names.commercial_name(p.name, brand=p.brand, model=p.model, shown_type=ctx.dtype(p), specs=specs or None)


def _specs(p, ctx: cd.GenCtx):
    out = specs_from_text(p.description_store)
    for k, v in family_specs(ctx.dtype(p), p.name).items():
        out.setdefault(k, v)
    return (out or None), ("pocos datos" if len(out) == 1 else None)


def _description(p, ctx: cd.GenCtx):
    if (p.web_description or "").strip() or store_description(p) is not None:
        return None, None  # ya tiene texto propio: no se propone otro
    specs = {**(p.web_specs or {}), **(p.specs or {})}
    if not specs:
        return None, None  # sin ficha aprobada no hay de donde sacar datos (no se inventa)
    text = describe(p, ctx.dtype(p), specs)
    return text, ("pocos datos" if text and "\n" not in text else None)


def _type(p, ctx: cd.GenCtx):
    if ctx.dtype(p):
        return None, None  # ya tiene tipo: no se propone otro
    lead, _ = product_names._type_lead(product_names._clean_source(p.name))
    if lead:
        return lead, None
    leaf = ctx.chain.get(p.category_id, (None, None))[0]
    single = product_names._singular(leaf)
    return (single, "tipo por categoria") if single else (None, None)


cd.register("web_name", "nombre_v4", _name)
cd.register("web_specs", "ficha_v1", _specs)
cd.register("web_description", "descripcion_v1", _description)
cd.register("web_type", "tipo_v1", _type)
