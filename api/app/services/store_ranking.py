"""Reglas de publicacion y orden «Recomendados» (O3-08), detras de un interruptor APAGADO.

Cambia decisiones del dueno del 21/10 (Hikvision destacado al crearse y primero en el orden): no se activa hasta que
el dueno lo reconfirme por escrito. Con el interruptor apagado la tienda ordena y publica exactamente como antes.

Interruptor y datos (tenant.settings["tienda_o3"]):
- recomendados (bool, apagado): el orden por defecto pasa a ser «Recomendados» (es una preferencia del comercio):
  1. comprable hoy (en bodega o bajo pedido confirmado), 2. fijado a mano en su categoria (maximo 8 por categoria),
  3. calidad de ficha (nombre comercial, 2 fotos o mas, especificaciones), 4. precio (los «a consultar» al final),
  5. nombre. «Precio» y «Nombre» quedan estrictamente neutrales (sin fijados ni destacados arriba) y «Nombre» es de la
  A a la Z de verdad (por el nombre que se ve). Ademas, las altas automaticas ya no nacen destacadas por ser Hikvision.
- pins: {id de categoria: [ids de producto]} («Fijar arriba en su categoria», 8 como maximo).
- min_publish_crc: precio final minimo (con IVA, en colones) para que el alta automatica publique (propuesta del plan:
  3 000; el envio mas barato cuesta 3 955). Tambien deja sin publicar lo que no es de la especialidad y lo que no
  tiene categoria hoja. Solo aplica con el interruptor encendido y la publicacion automatica activa.
El orden esta duplicado en el navegador (portal/src/modules/store/catalogFilter.ts): la prueba de paridad
(tests/test_tienda_orden_recomendados.py y __tests__/orden.test.ts sobre orden_casos.json) exige que den lo mismo.
"""

from __future__ import annotations

import unicodedata

MAX_PINS = 8
DEFAULT_MIN_PUBLISH_CRC = 3000
BUYABLE = ("en_bodega", "bajo_pedido")


def cfg(t) -> dict:
    raw = (t.settings or {}).get("tienda_o3") or {}
    pins = {str(k): [int(x) for x in (v or [])][:MAX_PINS] for k, v in (raw.get("pins") or {}).items()}
    return {
        "recomendados": bool(raw.get("recomendados")),
        "pins": pins,
        "min_publish_crc": int(raw.get("min_publish_crc") or DEFAULT_MIN_PUBLISH_CRC),
    }


def fold(s: object) -> str:
    """minusculas sin tildes, igual que fold() de catalogFilter.ts (NFKD y fuera lo que no es ASCII)"""
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def quality(web_name: str | None, n_images: int, has_specs: bool) -> int:
    """calidad de ficha: nombre comercial, 2 fotos o mas y especificaciones (0 a 3)"""
    return int(bool((web_name or "").strip())) + int(n_images >= 2) + int(bool(has_specs))


def recommended_key(state: str | None, pinned: bool, q: int, price: float, name: str, pid: int) -> tuple:
    """la llave del orden «Recomendados» (la misma en el navegador: cmpRecomendados)"""
    return (state not in BUYABLE, not pinned, -int(q), price <= 0, price if price > 0 else 0, fold(name), pid)


def name_key(name: str, pid: int) -> tuple:
    """«Nombre»: de la A a la Z, sin nada arriba"""
    return (fold(name), pid)


def pinned_in(pins: dict[str, list[int]], category_ids: set[int] | None, pid: int, own_category: int | None) -> bool:
    """fijado en la categoria que se esta viendo (o, sin categoria, en la suya)"""
    cats = category_ids if category_ids else ({own_category} if own_category is not None else set())
    return any(pid in pins.get(str(c), []) for c in cats)


# ---------------------------------------------------------------- limpiar «destacado web» (una sola vez, con aprobacion)
def featured_simulate(db, tid: int) -> dict:
    """los productos marcados «destacado web» (los reemplaza «Fijar arriba en su categoria»). No escribe nada."""
    from sqlalchemy import select

    from ..models import Product

    ids = list(db.scalars(select(Product.id).where(Product.tenant_id == tid, Product.web_featured.is_(True)).order_by(Product.id)))
    return {"tenant_id": tid, "product_ids": ids, "count": len(ids)}


def featured_apply(db, tid: int, report: dict) -> dict:
    """quita el destacado SOLO a los del informe aprobado que siguen destacados; devuelve la bitacora"""
    from sqlalchemy import select

    from ..models import Product

    if report.get("tenant_id") != tid:
        raise ValueError("El informe es de otra empresa")
    want = [int(x) for x in report.get("product_ids") or []]
    done = []
    for p in db.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_(want or [0]), Product.web_featured.is_(True))):
        p.web_featured = False
        done.append(p.id)
    db.flush()
    return {"tenant_id": tid, "cleared": sorted(done)}


def featured_undo(db, tid: int, journal: dict) -> int:
    from sqlalchemy import select

    from ..models import Product

    ids = [int(x) for x in journal.get("cleared") or []]
    n = 0
    for p in db.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_(ids or [0]))):
        p.web_featured = True
        n += 1
    db.flush()
    return n
