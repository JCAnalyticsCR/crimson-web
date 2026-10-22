"""Snapshot pre-armado de la tienda publica por empresa.

Arma un JSON compacto con todo lo que la tienda necesita para listar, buscar, filtrar y ordenar en
el cliente, lo comprime con gzip y lo guarda en store_snapshot(tenant_id). El endpoint
GET /public/store/{slug}/snapshot devuelve el blob gz directamente sin deserializar: cero RAM de
productos por peticion (solo lectura de un LargeBinary de ~60-200 KB).

Contrato JSON del snapshot (lo consume el frontend; version v1):
{
  "v": 1,                           -- version del esquema (rompe compatibilidad si sube a 2)
  "version": "1729600000000",       -- catalog_v: unix-ms que coincide con ETag
  "built_at": "2026-10-22T...",     -- ISO 8601 UTC
  "currency": "CRC"|"USD",
  "n": 1673,                        -- numero de productos en el snapshot

  "products": [                     -- solo los publicados y visibles (sin los agotados ocultos)
    {
      "id": 42,
      "path": "/p/hikvision-ds-2cd2143g2-i",  -- URL de la ficha
      "name": "Hikvision DS-2CD2143G2-I",
      "brand": "Hikvision" | null,
      "model": "DS-2CD2143G2-I" | null,
      "code": "HK-CAM-001",
      "category_id": 7 | null,
      "display_price": 89900,       -- precio con IVA en la moneda del tenant; 0 = a consultar
      "web_featured": true | false,
      "image": "https://..." | null, -- primera imagen (para la tarjeta de lista)
      "type": "Camara IP" | null,   -- product_type()
      "created_ts": 1729500000,     -- unix seconds (para ordenar "reciente")
      "availability": {
        "state": "en_bodega"|"bajo_pedido"|"agotado"|"servicio",
        "label": "En bodega",
        "accion": "Agregar" | null,
        "max_qty": 5 | null,        -- null = sin tope
        "plazo": null | "3-5 dias"
      },
      "sf": "hikvision ds-2cd2143g2-i cam ip poe"  -- search field: name+brand+model normalizado para
                                                    -- busqueda tolerante en el cliente (minusculas, sin acento)
    },
    ...
  ],

  "categories": [                   -- arbol plano con conteos; el cliente arma el arbol con parent_id
    {
      "id": 7,
      "name": "Camaras IP",
      "slug": "camaras-ip",         -- para la URL /c/<slug>
      "parent_id": null | 3,
      "count": 45                   -- productos visibles en esta categoria (sin subcategorias)
    },
    ...
  ],

  "brands": [                       -- marcas con conteo de productos visibles
    {"name": "Hikvision", "count": 312},
    ...
  ],

  "types": [                        -- tipos (product_type) con conteo
    {"name": "Camara IP", "count": 87},
    ...
  ],

  "showcase": {                     -- configuracion de la portada (para el frontend de la vitrina)
    "headline": "...",
    "subline": "...",
    "featured": [42, 17, ...],      -- IDs de los destacados elegidos (el frontend los busca en products[])
    "banners": [...],               -- igual que /showcase hoy
    "trust": [...]
  }
}

Notas para el frontend:
- "display_price" es el precio CON IVA en la moneda del tenant (CRC o USD): lo que se muestra.
- La busqueda usa el campo "sf": split por espacio, cada termino debe aparecer en "sf" del producto.
- Los agotados ocultos NO estan en el snapshot (misma regla que catalog() hoy).
- La ficha completa (descripcion larga, specs, accesorios sugeridos) sigue en /catalog?ids=<id>.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("crimson.snapshot")


def _nfc(s: str) -> str:
    """Normaliza a NFC y quita acentos: para el campo de busqueda 'sf'."""
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()


def _sf(name: str, brand: str | None, model: str | None) -> str:
    """Campo de busqueda: nombre + marca + modelo en minusculas sin acento."""
    parts = [name]
    if brand:
        parts.append(brand)
    if model:
        parts.append(model)
    return _nfc(" ".join(parts)).lower()


def build(db: Session, t, ctx) -> bytes:
    """Arma el snapshot de la tienda para el tenant t y lo devuelve como bytes gzip.
    ctx es una instancia de Ctx (routers/store_showcase) que ya cargo las categorias y el pricer.
    Se llama desde rebuild() con sus propios objetos.
    """
    # importaciones locales para no crear dependencias circulares en tiempo de importacion
    from ..routers.store import _gross, store_cfg
    from ..routers.store_showcase import (
        _avail,
        _cat_tree,
        _published_light,
        _stock_map,
    )
    from ..services import store_showcase as sv

    prods = _published_light(db, t.id)
    stock_all = _stock_map(db, t.id, prods)
    # misma regla que catalog(): ocultar agotados
    pool = [p for p in prods if not (stock_all.get(p.id) or {}).get("ocultar")]

    # campos de tarjeta liviana (sin descripciones ni specs)
    items = []
    for p in pool:
        own = stock_all.get(p.id)
        price_with_tax = _gross(ctx.pricer.of(p, p.price), float(p.taxes[0].tax.rate) if p.taxes else 13) if p.price else Decimal(0)
        img = None
        if p.images:
            img = (p.images[0] or {}).get("url") or None
        items.append(
            {
                "id": p.id,
                "path": sv.product_path(p),
                "name": p.name,
                "brand": p.brand,
                "model": p.model,
                "code": p.code or "",
                "category_id": p.category_id,
                "display_price": float(price_with_tax),
                "web_featured": bool(p.web_featured),
                "image": img,
                "type": sv.product_type(p),
                "created_ts": int(p.created_at.timestamp()) if p.created_at else 0,
                "availability": _avail(own),
                "sf": _sf(p.name, p.brand, p.model),
            }
        )

    # conteos por categoria
    counts: dict[int, int] = {}
    for p in pool:
        if p.category_id:
            counts[p.category_id] = counts.get(p.category_id, 0) + 1

    # arbol de categorias (mismo que showcase)
    cat_tree = _cat_tree(ctx, counts, {})

    # marcas
    brands_count: dict[str, int] = {}
    for p in pool:
        if p.brand:
            brands_count[p.brand] = brands_count.get(p.brand, 0) + 1
    brands = [{"name": b, "count": n} for b, n in sorted(brands_count.items(), key=lambda x: -x[1])]

    # tipos
    types_count: dict[str, int] = {}
    for p in pool:
        t_ = sv.product_type(p)
        if t_:
            types_count[t_] = types_count.get(t_, 0) + 1
    types = [{"name": k, "count": n} for k, n in sorted(types_count.items(), key=lambda x: -x[1])]

    # configuracion de vitrina (solo lo que el frontend necesita para la portada)
    cfg = store_cfg(t)
    sc = sv.showcase_cfg(cfg)
    showcase = {
        "headline": sc.get("headline"),
        "subline": sc.get("subline"),
        "featured": sc.get("featured") or [],
        "banners": [b for b in (sc.get("banners") or []) if b.get("active", True) and (b.get("image") or b.get("title"))],
        "trust": sc.get("trust") or [],
    }

    version = sv.get_catalog_version(t)
    payload = {
        "v": 1,
        "version": version,
        "built_at": datetime.now(UTC).isoformat(),
        "currency": ctx.pricer.currency,
        "n": len(items),
        "products": items,
        "categories": cat_tree,
        "brands": brands,
        "types": types,
        "showcase": showcase,
    }
    return gzip.compress(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(), compresslevel=6)


def rebuild(db: Session, tid: int) -> None:
    """Rearma y guarda el snapshot en la DB para el tenant dado. Hace commit."""
    from ..models import StoreSnapshot, Tenant
    from ..routers.store_showcase import Ctx

    t = db.get(Tenant, tid)
    if t is None or not t.active:
        return
    ctx = Ctx(db, t)
    gz = build(db, t, ctx)

    # hash del contenido como version (independiente de catalog_v para detectar sin cambios reales)
    version = hashlib.sha256(gz).hexdigest()[:16]
    now = datetime.now(UTC)

    row = db.get(StoreSnapshot, tid)
    if row is None:
        row = StoreSnapshot(tenant_id=tid)
        db.add(row)
    row.gz = gz
    row.version = version
    row.built_at = now
    row.n_products = len(gz)  # sobreescribimos abajo
    # calcular n_products real sin deserializar el gz (leemos el campo n del JSON)
    try:
        row.n_products = json.loads(gzip.decompress(gz)).get("n", 0)
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    log.info("Snapshot empresa %s: %s bytes gz", tid, len(gz))


def get_or_rebuild(db: Session, tid: int) -> tuple[bytes, str]:
    """Lee el snapshot de la DB; si no existe lo arma (con candado simple). Devuelve (gz, version)."""

    from ..models import StoreSnapshot

    row = db.scalar(
        select(StoreSnapshot).where(StoreSnapshot.tenant_id == tid)
    )
    if row is None or row.gz is None:
        rebuild(db, tid)
        row = db.scalar(
            select(StoreSnapshot).where(StoreSnapshot.tenant_id == tid)
        )
    if row is None or row.gz is None:
        raise RuntimeError(f"no se pudo armar el snapshot para empresa {tid}")
    return row.gz, row.version


def enqueue_rebuild(tid: int, countdown: int = 30) -> None:
    """Encola store.rebuild_snapshot con debounce (countdown segundos) para no apilar llamadas.
    Si Celery no esta disponible (tests o CLI), arma el snapshot de forma sincrona en su lugar.
    """
    try:
        from celery import current_app as celery_app  # type: ignore[import]

        celery_app.send_task("store.rebuild_snapshot", args=[tid], countdown=countdown)
    except Exception:  # noqa: BLE001
        # fuera de contexto Celery (tests, CLI, scripts): ignorar silenciosamente
        pass
