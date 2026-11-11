"""Direccion /c/<slug> de las categorias de la tienda (O3-07a).

Antes se calculaba del nombre en cada peticion: renombrar, fusionar o reordenar categorias daba un 404 real en
direcciones ya indexadas y compartidas. Ahora:
- category.slug guarda la direccion (la migracion la relleno con la que se calculaba ese dia: nada cambio de lugar).
- Una categoria sin slug guardado (creada por un camino viejo) usa la regla de siempre, sin chocar con las guardadas.
- Renombrar (rename) calcula la direccion nueva y deja la vieja en category_slug_alias: la vieja responde 301 a la
  vigente (seo_exists -> nginx), la nueva 200 y un slug que no es de nadie, 404 real.

Quien mas usa la categoria del producto FUERA de la tienda (entrada de la simulacion de O3-07b, reclasificar):
- routers/pos.py: el buscador del punto de venta filtra y agrupa por category_id.
- routers/ally_portal.py: el catalogo del portal de aliados lista y filtra por categoria (y su arbol).
- routers/cabys_masivo.py y services/cabys_suggest.py: las sugerencias de CABYS en bloque se agrupan por categoria.
- services/reports.py: ventas por categoria (reportes y tablero).
- services/category_margin.py y routers/catalog.py (Margenes, Ajustar precios): el margen se hereda de la categoria
  mas cercana. Mover un producto de categoria puede CAMBIAR SU PRECIO sugerido: la simulacion de O3-07b lo tiene que
  mostrar antes de aplicar (regla: no cambiar precios sin aprobacion).
- services/supplier_sync._suggest_cat: las altas nuevas caen en la categoria del proveedor.
Los slugs no afectan a ninguno de ellos (usan el id); reclasificar si.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, CategorySlugAlias
from .store_showcase import slugify

SLUG_MAX = 60


def computed(cats: list) -> dict[int, str]:
    """La regla de siempre: slug del nombre y, si dos se llaman igual, la segunda lleva el id (por id)."""
    out, seen = {}, set()
    for c in sorted(cats, key=lambda c: c.id):
        s = slugify(c.name, SLUG_MAX)
        if s in seen:
            s = f"{s}-{c.id}"
        seen.add(s)
        out[c.id] = s
    return out


def slugs(cats: list) -> dict[int, str]:
    """slug vigente por categoria: el guardado; sin el, la regla de siempre sin chocar con los guardados."""
    out = {c.id: c.slug for c in cats if getattr(c, "slug", None)}
    taken = set(out.values())
    for c in sorted((c for c in cats if c.id not in out), key=lambda c: c.id):
        s = slugify(c.name, SLUG_MAX)
        if s in taken:
            s = f"{s}-{c.id}"
        taken.add(s)
        out[c.id] = s
    return out


def _taken(db: Session, tid: int, except_id: int | None = None) -> set[str]:
    own = {s for i, s in db.execute(select(Category.id, Category.slug).where(Category.tenant_id == tid)) if s and i != except_id}
    alias = {s for s, cid in db.execute(select(CategorySlugAlias.slug, CategorySlugAlias.category_id).where(CategorySlugAlias.tenant_id == tid)) if cid != except_id}
    return own | alias


def unique_slug(db: Session, tid: int, name: str, cid: int | None) -> str:
    s = slugify(name, SLUG_MAX)
    taken = _taken(db, tid, cid)
    if s not in taken:
        return s
    if cid is not None and f"{s}-{cid}" not in taken:
        return f"{s}-{cid}"
    n = 2
    while f"{s}-{n}" in taken:
        n += 1
    return f"{s}-{n}"


def assign(db: Session, cat: Category) -> str:
    """Guarda la direccion de una categoria nueva (solo flush). Una que ya la tiene no cambia."""
    if not cat.slug:
        if cat.id is None:
            db.flush()
        cat.slug = unique_slug(db, cat.tenant_id, cat.name, cat.id)
    return cat.slug


def rename(db: Session, cat: Category, new_name: str) -> str | None:
    """Renombra la categoria. Si su direccion cambia, la vieja queda como alias (301). Devuelve el slug viejo que quedo
    como alias, o None si la direccion no cambio. Solo flush."""
    old = cat.slug or slugs([c for c in db.scalars(select(Category).where(Category.tenant_id == cat.tenant_id, Category.show_on_web))] + ([cat] if not cat.show_on_web else [])).get(cat.id)
    cat.name = new_name
    new = unique_slug(db, cat.tenant_id, new_name, cat.id)
    if old == new or slugify(new_name, SLUG_MAX) == old:
        cat.slug = old
        return None
    # volver a un nombre anterior: su alias deja de ser alias
    for a in db.scalars(select(CategorySlugAlias).where(CategorySlugAlias.tenant_id == cat.tenant_id, CategorySlugAlias.slug == new)):
        db.delete(a)
    db.flush()
    cat.slug = new
    if old:
        db.add(CategorySlugAlias(tenant_id=cat.tenant_id, slug=old, category_id=cat.id, created_at=datetime.now(UTC)))
    db.flush()
    return old


def aliases(db: Session, tid: int) -> dict[str, int]:
    """slug viejo -> id de la categoria"""
    return dict(db.execute(select(CategorySlugAlias.slug, CategorySlugAlias.category_id).where(CategorySlugAlias.tenant_id == tid)).all())


def resolve(db: Session, tid: int, cats: list, slug: str) -> tuple[int | None, bool]:
    """(id de la categoria, es_alias) para un slug de la direccion. (None, False) si no es de nadie."""
    cur = slugs(cats)
    hit = next((i for i, s in cur.items() if s == slug), None)
    if hit is not None:
        return hit, False
    if slug.isdigit() and any(c.id == int(slug) for c in cats):
        return int(slug), False
    cid = aliases(db, tid).get(slug)
    if cid is not None and cid in cur:
        return cid, True
    return None, False
