"""Enlaces viejos de productos de Fygaro (tienda.crimsoncr.com/products/<uuid>/) cuando el producto no quedo mapeado.

Los productos de la tienda propia salen del catalogo del proveedor, no de la importacion de Fygaro, asi que el mapa de
la importacion (tenant.settings["fygaro"]["products"]) casi nunca apunta a un producto visible. Para que los enlaces ya
compartidos (WhatsApp, redes, Google) no terminen en el catalogo general:
1. el mismo equipo en el catalogo vivo, por codigo, modelo o SKU de proveedor (normalizados);
2. si no existe, una busqueda con las primeras palabras de su nombre.
fygaro_legacy.json es el catalogo PUBLICO que tenia la tienda vieja: id de Fygaro -> [SKU, nombre].
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product

_STOP = {"de", "del", "la", "el", "con", "para", "y", "marca", "compatible", "en", "a"}


@functools.cache
def catalog() -> dict[str, tuple[str, str]]:
    path = Path(__file__).resolve().parent / "fygaro_legacy.json"
    try:
        return {k: (v[0], v[1]) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match(db: Session, tid: int, codes: list[str]) -> int | None:
    """Producto visible en la tienda con el mismo codigo, modelo o SKU de proveedor."""
    wanted = {c for c in (norm(x) for x in codes) if len(c) >= 4}
    if not wanted:
        return None
    rows = db.execute(
        select(Product.id, Product.code, Product.model, Product.supplier_sku, Product.name).where(Product.tenant_id == tid, Product.active, Product.show_on_web)
    ).all()
    for pid, code, model, ssku, _ in rows:
        if wanted & {norm(code), norm(model), norm(ssku)}:
            return pid
    # Los productos del proveedor traen el modelo solo dentro del nombre ("Switch Hikvision DS-3E1518P-SI 16 puertos"):
    # se compara PALABRA COMPLETA (con y sin lo que va entre parentesis), nunca un pedazo, para no confundir
    # DS-3E1518P-SI con DS-3E1518P-EI/M.
    for pid, _, _, _, name in rows:
        for tok in (name or "").split():
            if norm(tok) in wanted or norm(tok.split("(")[0]) in wanted:
                return pid
    return None


def query(name: str) -> str:
    """Las dos primeras palabras utiles del nombre viejo: el buscador exige TODAS las palabras, dos generales encuentran
    equivalentes ("Gabinete pared", "Grabador IP")."""
    words = [w for w in re.split(r"[^\w-]+", name or "") if w and w.lower() not in _STOP]
    return " ".join(words[:2])[:80]


def resolve(db: Session, tid: int, ref: str, entry: dict | None) -> tuple[str, int | str] | None:
    """("product", id) si hay equivalente visible; ("search", texto) si no; None si el id no es de Fygaro."""
    sku, name = catalog().get(ref, ("", ""))
    found = match(db, tid, [(entry or {}).get("code") or "", sku])
    if found:
        return ("product", found)
    q = query(name or sku)
    return ("search", q) if q else None
