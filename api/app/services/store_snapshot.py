"""Snapshot pre-armado de la tienda publica por empresa.

Arma un JSON compacto con todo lo que la tienda necesita para listar, buscar, filtrar y ordenar en
el cliente, lo comprime con gzip y lo guarda en store_snapshot(tenant_id). El endpoint
GET /public/store/{slug}/snapshot devuelve el blob gz directamente sin deserializar: cero RAM de
productos por peticion (solo lectura de un LargeBinary de ~60-200 KB).

Contrato JSON del snapshot (lo consume el frontend; version v2, O1-13):
{
  "v": 2,                           -- version del formato: el navegador descarta uno guardado con v menor
  "version": "1729600000000",       -- catalog_v: unix-ms que coincide con ETag
  "built_at": "2026-10-22T...",     -- ISO 8601 UTC
  "currency": "CRC"|"USD",
  "n": 1673,                        -- numero de productos en el snapshot

  "products": [                     -- solo los publicados y visibles (sin los agotados ocultos)
    {
      "id": 42,
      "path": "/p/hikvision-ds-2cd2143g2-i",  -- URL de la ficha
      "name": "Hikvision DS-2CD2143G2-I",
      "display_name": "Cámara IP Hikvision ..." -- (opcional, O3-01) nombre comercial aprobado; sin el, name
      "brand": "Hikvision" | null,
      "model": "DS-2CD2143G2-I" | null,
      "code": "HK-CAM-001",
      "category_id": 7 | null,
      "display_price": 89900,       -- precio con IVA en la moneda del tenant; 0 = a consultar. El MISMO de la
                                    -- ficha (/item): precio web vigente, retencion por margen y tipo de cambio (O1-13)
      "compare_display_price": 99900, -- (opcional) precio anterior tachado: solo si hay historial propio que lo
                                    -- respalde (30 dias vendido a ese precio o mas); hoy ningun producto lo trae
      "web_featured": true | false,
      "image": "https://..." | null, -- primera imagen (para la tarjeta de lista)
      "type": "Camara IP" | null,   -- O1-08: el tipo que se muestra (product_typing.display_type), no el de los kits
      "created_ts": 1729500000,     -- unix seconds (para ordenar "reciente")
      "availability": {
        "state": "en_bodega"|"bajo_pedido"|"agotado"|"servicio",
        "label": "En bodega",
        "accion": "Agregar" | null,
        "max_qty": 5 | null,        -- null = sin tope
        "plazo": null | "3-5 dias"
      },
      "sf": "hikvision ds 2cd2143g2 i camara ip"  -- O1-14: nombre, marca, modelo, codigo, SKU, categoria y tipo
                                                    -- normalizados (services/store_search; el navegador usa la misma regla)
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

  "types": [                        -- tipos que se muestran (display_type) con conteo
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
- La busqueda usa "sf" y la tabla "syn" con la regla de services/store_search (O1-14), la misma en el navegador.
- Los agotados ocultos NO estan en el snapshot (misma regla que catalog() hoy).
- La ficha completa (descripcion larga, specs, accesorios sugeridos) sigue en /catalog?ids=<id>.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import weakref
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

log = logging.getLogger("crimson.snapshot")

FORMAT_V = 2  # O1-13: precio de la ficha, comparacion con respaldo; el navegador descarta un snapshot guardado con v menor
COMPARE_MIN_DAYS = 30  # un tachado necesita 30 dias de historial propio a ese precio (plan: nada de tachados sin respaldo)


def build(db: Session, t, ctx) -> bytes:
    """Arma el snapshot de la tienda para el tenant t y lo devuelve como bytes gzip.
    ctx es una instancia de Ctx (routers/store_showcase) que ya cargo las categorias y el pricer.
    Se llama desde rebuild() con sus propios objetos.
    """
    # importaciones locales para no crear dependencias circulares en tiempo de importacion
    from ..routers.store import _gross, business_of, store_cfg
    from ..routers.store_showcase import (
        _avail,
        _cat_tree,
        _load_full,
        _published_light,
        _stock_map,
    )
    from ..services import store_search
    from ..services import store_showcase as sv

    prods = _published_light(db, t.id)
    stock_all = _stock_map(db, t.id, prods)
    # misma regla que catalog(): ocultar agotados
    pool = [p for p in prods if not (stock_all.get(p.id) or {}).get("ocultar")]

    # O2-06: los datos clave de la tarjeta (sv.key_specs) leen la descripcion y las specs editadas: una sola carga
    # completa de lo publicado (el snapshot se arma pocas veces; /catalog y /showcase siguen livianos)
    _load_full(db, [p.id for p in pool])

    # campos de tarjeta liviana (sin descripciones ni specs)
    backed = compare_backed(db, t.id, [p for p in pool if p.compare_price])
    items = []
    for p in pool:
        own = stock_all.get(p.id)
        # O1-13: el mismo precio que la ficha (Ctx.card -> _prod_pub): precio web vigente, retencion por margen y tipo de
        # cambio. Antes se calculaba con p.price y la lista decia un precio y la ficha «Precio a consultar».
        rate = float(p.taxes[0].tax.rate) if p.taxes else 13
        price_with_tax = _gross(ctx.pricer.of(p), rate)
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
                "type": ctx.dtype(p),  # O1-08: el tipo que se muestra y se filtra (el funcional queda para los kits)
                "created_ts": int(p.created_at.timestamp()) if p.created_at else 0,
                "availability": _avail(own),
                # O2-06 (aditivo): 0 a 4 datos clave de la tarjeta; el primero es el que distingue a la familia
                "ks": sv.key_specs(p),
                # O1-14: nombre, marca, modelo, codigo, SKU, categoria y tipo, normalizados (services/store_search)
                "sf": sv.search_text(p, ctx.cat_name.get(p.category_id), ctx.dtype(p)),
            }
        )
        if p.web_name and p.web_name.strip() and p.web_name.strip() != p.name:
            # O3-01: nombre comercial aprobado (solo si hay: el snapshot no crece para el resto)
            items[-1]["display_name"] = p.web_name.strip()
        if p.id in backed and price_with_tax > 0:
            cmp_ = _gross(ctx.pricer.of(p, p.compare_price), rate)
            if cmp_ > price_with_tax:
                items[-1]["compare_display_price"] = float(cmp_)

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
        t_ = ctx.dtype(p)
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
        # O1-04 d/k: los mismos de /showcase (sin banners de relleno; sellos con dato verificable)
        "banners": sv.public_banners(sc.get("banners") or [], cfg.get("domain")),
        "trust": sv.public_trust(sc.get("trust") or [], business_of(cfg).get("hours")),
    }

    version = sv.get_catalog_version(t)
    payload = {
        "v": FORMAT_V,
        "version": version,
        "syn": store_search.SYNONYMS,  # O1-14: la tabla de sinonimos una vez (el navegador usa la misma regla)
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
    cv = (t.settings or {}).get("catalog_v") or 0  # version del catalogo con la que se arma (antes de leer nada)
    # O1-13: la hora de INICIO del armado; una marca (dirty_at) posterior, aunque llegue mientras se arma, lo deja viejo
    started = datetime.now(UTC)
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
    row.built_at = started
    row.n_products = len(gz)  # sobreescribimos abajo
    # calcular n_products real sin deserializar el gz (leemos el campo n del JSON)
    shown: set[int] | None = None
    try:
        data = json.loads(gzip.decompress(gz))
        row.n_products = data.get("n", 0)
        shown = {int(x["id"]) for x in data.get("products") or []}
    except Exception:  # noqa: BLE001
        pass
    if shown is not None:
        mark_hidden(db, tid, shown, now)
    # Armar el snapshot tarda (todo el catalogo) y `t` se cargo antes: lo que se haya guardado mientras tanto en los
    # ajustes (un guardado de la vitrina, por ejemplo) NO se pisa. Se relee la fila con candado y solo se anota la
    # marca. `cv` es la version con la que se armo: si hubo un guardado en el camino, catalog_v queda mayor y la
    # siguiente visita rearma con lo nuevo.
    db.refresh(t, attribute_names=["settings"], with_for_update=True)
    st = dict(t.settings or {})
    st["snapshot_cv"] = cv
    t.settings = st
    db.commit()
    log.info("Snapshot empresa %s: %s bytes gz", tid, len(gz))


def mark_hidden(db: Session, tid: int, shown: set[int], now: datetime) -> None:
    """hidden_since (O0-07, aun no se muestra): un producto publicado (activo y show_on_web) que no quedo en el
    snapshot (agotado oculto u otra regla de la tienda) recibe la fecha del primer rearmado que lo oculto; vuelve a
    nulo cuando reaparece o cuando deja de estar publicado (despublicar no es "oculto"). UPDATE por SQL: no toca
    web_updated_at ni el historial de precios."""
    from sqlalchemy import and_, not_, true, update

    from ..models import Product

    base = (Product.tenant_id == tid,)
    published = (Product.active, Product.show_on_web)
    keep = {"updated_at": Product.updated_at}  # marcar oculto no es editar el producto
    db.execute(
        update(Product).where(*base, *published, Product.hidden_since.is_(None), Product.id.not_in(shown) if shown else true()).values(hidden_since=now, **keep)
    )
    if shown:
        db.execute(update(Product).where(*base, Product.hidden_since.is_not(None), Product.id.in_(shown)).values(hidden_since=None, **keep))
    db.execute(update(Product).where(*base, Product.hidden_since.is_not(None), not_(and_(*published))).values(hidden_since=None, **keep))


def get_or_rebuild(db: Session, tid: int) -> tuple[bytes, str]:
    """Lee el snapshot de la DB; si no existe lo arma (con candado simple). Devuelve (gz, version)."""

    from ..models import StoreSnapshot, Tenant
    from .supplier_sync import trim_memory

    row = db.scalar(select(StoreSnapshot).where(StoreSnapshot.tenant_id == tid))
    t = db.get(Tenant, tid)
    st = (t.settings or {}) if t is not None else {}
    # la API no tiene la cola del worker: si algo visible cambio despues de armarlo (catalog_v > snapshot_cv), la
    # siguiente visita lo rearma una vez; 20 s de margen juntan varios guardados seguidos en un solo armado
    stale = row is not None and is_stale(row, st)
    if row is None or row.gz is None or stale:
        rebuild(db, tid)
        trim_memory()  # armar el snapshot toca todo el catalogo: la memoria vuelve al sistema
        row = db.scalar(select(StoreSnapshot).where(StoreSnapshot.tenant_id == tid))
    if row is None or row.gz is None:
        raise RuntimeError(f"no se pudo armar el snapshot para empresa {tid}")
    return row.gz, row.version


def _utc(d: datetime | None) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=UTC))


def is_stale(row, settings: dict | None, margin_s: int = 20) -> bool:
    """El snapshot guardado quedo viejo: cambio algo del panel (catalog_v > snapshot_cv) o algo marcado despues de
    armarlo (dirty_at > built_at: inventario, reservas, margen; O1-13). 20 s de margen juntan varios cambios seguidos
    en un solo armado."""
    st = settings or {}
    built = _utc(row.built_at)
    changed = (st.get("catalog_v") or 0) > (st.get("snapshot_cv") or 0)
    dirty = _utc(getattr(row, "dirty_at", None))
    changed = changed or (dirty is not None and (built is None or dirty > built))
    return changed and (built is None or (datetime.now(UTC) - built).total_seconds() > margin_s)


def compare_backed(db: Session, tid: int, prods: list) -> set[int]:
    """Productos cuyo precio de comparacion (tachado) tiene respaldo en el historial propio (O0-07): hubo un tramo de
    COMPARE_MIN_DAYS dias o mas vendido a ese precio o a uno mayor (precio web, o el de siempre si no habia web, en la
    misma moneda). Sin historial suficiente no se tacha nada (hoy, ninguno)."""
    if not prods:
        return set()
    from datetime import timedelta

    from ..models.catalog import ProductPriceHistory as H

    want = {p.id: Decimal(str(p.compare_price)) for p in prods if p.compare_price}
    rows = db.execute(
        select(H.product_id, H.valid_from, H.price_net, H.web_price_net)
        .where(H.tenant_id == tid, H.product_id.in_(list(want)))
        .order_by(H.product_id, H.valid_from)
    ).all()
    now = datetime.now(UTC)
    out: set[int] = set()
    for i, (pid, since, net, web) in enumerate(rows):
        until = rows[i + 1][1] if i + 1 < len(rows) and rows[i + 1][0] == pid else now
        sold = Decimal(str(web if web is not None else net or 0))
        if sold >= want[pid] and (_utc(until) - _utc(since)) >= timedelta(days=COMPARE_MIN_DAYS):
            out.add(pid)
    return out


# ---------------------------------------------------------------- O1-13: marcar el snapshot viejo despues del commit
def mark_dirty_after_commit(db: Session, tid: int | None) -> None:
    """Deja anotado que el snapshot de la empresa quedo viejo; la marca se escribe DESPUES del commit de esta sesion,
    en su propia transaccion y sobre la fila del snapshot (nunca la de la empresa: el checkout no actualiza tenant).
    Si la transaccion se revierte, no se marca nada."""
    if tid:
        db.info.setdefault("snapshot_dirty", set()).add(int(tid))


def mark_dirty_now(bind, tids) -> None:
    """La marca en si: stock_v + 1 y dirty_at = ahora en la fila del snapshot (si aun no hay fila, la primera visita lo
    arma de cero y no hace falta marcar)."""
    from sqlalchemy.pool import SingletonThreadPool, StaticPool

    from ..models import StoreSnapshot

    tids = sorted({int(x) for x in tids if x})
    if not tids:
        return
    if isinstance(getattr(bind, "pool", None), StaticPool | SingletonThreadPool):
        # una sola conexion compartida (SQLite en memoria de las pruebas): una transaccion aparte la cerraria debajo
        # de otra sesion. La marca se aplica al empezar la siguiente transaccion, dentro de ella.
        _PENDING.setdefault(bind, set()).update(tids)
        return
    try:
        with bind.connect() as conn:
            conn.execute(_mark_stmt(StoreSnapshot, tids))
            conn.commit()
    except Exception:  # noqa: BLE001 - la marca nunca tumba lo que ya se confirmo; a lo sumo el snapshot espera al siguiente cambio
        log.exception("No se pudo marcar el snapshot de %s", tids)
    # Sin encolar el rearmado aqui: la marca va despues de CADA commit que toca inventario y un broker lento o caido no
    # puede retener a quien confirmo (en las pruebas, el cliente de Redis reintentaba minutos). La siguiente visita lo
    # rearma (get_or_rebuild); encolarlo con candado es O0-04.


_PENDING: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # por motor: una marca de una base nunca va a otra


def _mark_stmt(model, tids):
    return update(model).where(model.tenant_id.in_(sorted(tids))).values(dirty_at=datetime.now(UTC), stock_v=model.stock_v + 1)


@event.listens_for(Session, "after_begin")
def _apply_pending(session: Session, _transaction, connection) -> None:
    """Solo con la conexion compartida de las pruebas (ver mark_dirty_now)."""
    tids = _PENDING.pop(connection.engine, None) if len(_PENDING) else None
    if tids:
        from ..models import StoreSnapshot

        connection.execute(_mark_stmt(StoreSnapshot, tids))


# lo que cambia la disponibilidad o el precio que ve la tienda sin pasar por un guardado del panel que ya sube catalog_v
_PRODUCT_ATTRS = (
    "margin_hold",
    "show_on_web",
    "web_featured",
    "web_price",
    "price",
    "compare_price",
    "active",
    "name",
    "images",
    "category_id",
    "brand",
    "model",
    "currency",
    "supplier_stock",
    "supplier_updated_at",
    "web_name",  # O3-01: lo que el snapshot lleva del contenido propio
    "web_type",
)


def _tenant_of(obj) -> int | None:
    from ..models import Product, PurchaseRequest, PurchaseRequestLine, StockMovement, StockReservation, StockUnit

    if isinstance(obj, (StockMovement, StockReservation, StockUnit, PurchaseRequest)):
        return getattr(obj, "tenant_id", None)
    if isinstance(obj, PurchaseRequestLine):
        req = obj.__dict__.get("request")  # sin consultar: solo si ya esta cargada
        return getattr(req, "tenant_id", None)
    if isinstance(obj, Product):
        from sqlalchemy import inspect

        st = inspect(obj)
        if st.pending or any(st.attrs[a].history.has_changes() for a in _PRODUCT_ATTRS if a in st.attrs):
            return obj.tenant_id
    return None


@event.listens_for(Session, "before_flush")
def _track(session: Session, _ctx, _instances) -> None:
    """Inventario, reservas, compras para pedidos web y productos (margen, precio web, publicacion): toda escritura que
    cambie lo que la tienda muestra deja la empresa anotada; se marca al confirmar (after_commit)."""
    for obj in (*session.new, *session.dirty, *session.deleted):
        tid = _tenant_of(obj)
        if tid:
            session.info.setdefault("snapshot_dirty", set()).add(int(tid))


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    tids = session.info.pop("snapshot_dirty", None)
    if tids:
        bind = session.get_bind()
        mark_dirty_now(getattr(bind, "engine", bind), tids)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("snapshot_dirty", None)


# ---------------------------------------------------------------- O1-05: conteos reales, los del snapshot
_COUNTS: dict[tuple[int, str], dict] = {}


def counts(db: Session, tid: int) -> dict | None:
    """Total, conteo por categoria (con sus hijas) y por marca tomados del snapshot YA armado: lo que la tienda lista
    (sin los ocultos). Nunca rearma ni recalcula disponibilidad (GET /public/store/{slug} bloquea toda la tienda):
    sin snapshot guardado devuelve None y quien llama usa su respaldo. Se recuerda por version (una descompresion por
    snapshot nuevo, no por visita)."""
    from ..models import StoreSnapshot

    ver = db.scalar(select(StoreSnapshot.version).where(StoreSnapshot.tenant_id == tid))
    if not ver:
        return None
    key = (tid, ver)
    if key not in _COUNTS:
        gz = db.scalar(select(StoreSnapshot.gz).where(StoreSnapshot.tenant_id == tid))
        if not gz:
            return None
        try:
            data = json.loads(gzip.decompress(gz))
        except Exception:  # noqa: BLE001
            return None
        tree: dict[int, int] = {}

        def walk(nodes) -> None:
            for c in nodes or []:
                tree[int(c["id"])] = int(c.get("count") or 0)
                walk(c.get("children"))

        walk(data.get("categories"))
        direct: dict[int, int] = {}
        for p in data.get("products") or []:
            if p.get("category_id"):
                direct[int(p["category_id"])] = direct.get(int(p["category_id"]), 0) + 1
        for k in [k for k in _COUNTS if k[0] == tid]:
            _COUNTS.pop(k, None)
        _COUNTS[key] = {"n": int(data.get("n") or 0), "categories": tree, "direct": direct, "brands": data.get("brands") or []}
    return _COUNTS[key]


def enqueue_rebuild(tid: int, countdown: int = 30) -> None:
    """Encola store.rebuild_snapshot con debounce (countdown segundos) para no apilar llamadas.

    Solo encola el proceso que tiene la cola configurada (el worker). La API no tiene broker: ahi no se intenta nada
    (antes cada guardado esperaba varios segundos a un broker que no existe, con la fila de la empresa ya modificada y
    sin confirmar) y el snapshot se rearma en la siguiente visita (get_or_rebuild: catalog_v > snapshot_cv).
    Con cola, un solo intento sin reintentos: un broker caido no retiene al que llama.
    """
    try:
        from celery import current_app as celery_app  # type: ignore[import]

        if not celery_app.conf.broker_url:
            return
        celery_app.send_task("store.rebuild_snapshot", args=[tid], countdown=countdown, retry=False)
    except Exception:  # noqa: BLE001
        # sin Celery o con el broker caido: la siguiente visita rearma
        pass
