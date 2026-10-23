"""Sincronizacion del catalogo de Eurocomp por su WebService SiReTT (providers/sirett.py) -> catalogo de proveedores.

Que entra: SOLO lo que Crimson vende o instala (ver `relevant`): seguridad (camaras, grabadoras, control de acceso,
alarmas), redes, respaldo electrico, smart home, electrodomesticos (23/10, lo pidio ventas), y de las familias mixtas el cableado, conectores, soportes, herramientas de red y
discos de videovigilancia; mas todo lo de marcas de seguridad (Hikvision, Ezviz, Ubiquiti...) en cualquier familia.
De 11.538 articulos de Eurocomp quedan ~1.650 (2026-10-15), incluidos los 218 que Crimson ya tenia.

Como entra: la lista filtrada se aplica con el MISMO motor que la lista en Excel (supplier_lists.apply_list,
source="api"): idempotente por codigo, lo que deja de venir queda inactivo, vinculos exactos por codigo/modelo y el
costo y la existencia del proveedor pasan a los productos vinculados. Nunca toca el precio de venta ni la existencia
PROPIA de Crimson (eso solo lo mueve el kardex). Ademas, al producto vinculado que no los tiene le completa el CABYS
(13 digitos, lo recomienda el propio SiReTT) y las fotos.

Cuando corre: el catalogo completo UNA vez por semana (lunes 03:17); es la consulta pesada (34 MB) y solo hace falta
para descubrir articulos nuevos, CABYS y fotos. De lo que YA estaba solo actualiza precio y existencia (pedido 21/10:
nunca vuelve a traer fotos ni descripciones; apply_list(only_price_stock=True)), y el CABYS y las fotos se completan
una sola vez por vinculo (lo recien vinculado o creado desde el panel, si le faltan). Lo que se agota en Eurocomp sale de la tienda en la
sincronizacion siguiente (Ajustes de la tienda: "supplier_out_of_stock", ver services/availability) y sigue en el
panel: nunca se desactiva ni se despublica el producto. Las existencias y costos de lo que Crimson YA vende (los vinculados)
se refrescan cada 6 horas con la consulta por lista de codigos (`refresh_linked`): unas pocas llamadas chicas, sin
limite de frecuencia en Eurocomp. Asi la tienda sigue diciendo "bajo pedido" con datos de horas, no de dias.

Seguridad de datos: si Eurocomp responde una lista sospechosamente corta (menos de la mitad de lo que habia) no se
aplica, para no dejar inactivo medio catalogo por una respuesta cortada.
"""

from __future__ import annotations

import ctypes
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Product, Supplier, SupplierItem, Tenant
from ..providers.sirett import SirettClient, SirettError, image_url
from . import supplier_catalog as sc
from . import supplier_lists as sl
from .documents import audit

log = logging.getLogger("crimson.suppliers")
CR = ZoneInfo("America/Costa_Rica")
MIN_RATIO = 0.5  # la lista nueva debe traer al menos la mitad de los articulos activos que habia
MAX_IMAGES = 6

# ---------- que le sirve a Crimson ----------
FAMILIAS_COMPLETAS = {"SEGURIDAD", "REDES Y CONECTIVDAD", "RESPALDO ELÉCTRICO", "SMART HOME", "ELECTRODOMESTICOS", "ELECTRODOMÉSTICOS"}  # con la errata de SiReTT
SUBFAMILIAS: set = set()  # todo lo de esas familias entra completo; antes solo subfamilias de RESPALDO ELÉCTRICO
MARCAS = {"HIKVISION", "HILOOK", "EZVIZ", "HIKMICRO", "UBIQUITI", "APC", "GRANDSTREAM", "ZKTECO", "DAHUA", "PARADOX", "RUIJIE", "REYEE", "MIKROTIK"}
SIN_MARCA_EN = {"COMPONENTES DE PC", "VENTAS FOB/SERVICIOS"}  # familia padre o sub
CLAVES = {
    ("ACCESORIOS", "Cables"): r"\b(UTP|FTP|STP|CAT ?5E?|CAT ?6A?|CAT ?7|PATCH|RJ ?45|COAXIAL|COAX|RG ?59|FIBRA|SIAMES|POE|ETHERNET|DE RED)\b",
    ("ACCESORIOS", "Adaptadores"): r"\b(BALUN|RJ ?45|POE|INYECTOR|CONECTOR|ACOPLE|COUPLER|ETHERNET|LAN|FIBRA|DE RED)\b",
    ("ACCESORIOS", "Herramientas"): r"(PONCHA|CRIMP|PROBADOR|TESTER|PELADOR|INSERCI|DE RED)",
    ("ACCESORIOS", "Soportes"): r"\b(CAMARA|CÁMARA|DOMO|BULLET|PTZ|TV|TELEVISOR|MONITOR|PARED|TECHO|POSTE|RACK)\b",
    ("ALMACENAMIENTO", "Discos Duros Internos"): r"\b(PURPLE|SKYHAWK|VIGILANCIA|SURVEILLANCE|VIDEOVIGILANCIA)\b",
    ("ALMACENAMIENTO", "Memorias Micro SD"): r"(ENDURANCE|VIGILANCIA|SURVEILLANCE|CAMARA)",
    ("DISPOSITIVOS DE VISUALIZACION", "Monitores"): r"(VIGILANCIA|CCTV|24/7)",
}
EXCLUIR = re.compile(r"(LIGHTNING|IPHONE|IPAD|MAC ?BOOK|GAMER|GAMING)")


def relevant(i: dict) -> str | None:
    """Por que un articulo de Eurocomp le sirve a Crimson (se guarda en raw), o None si no entra."""
    fam, sub = (i.get("FamiliaPadre") or "").strip(), (i.get("familia") or "").strip()
    marca = (i.get("marca") or "").strip().upper()
    txt = f"{i.get('descripcion') or ''} {i.get('modelo') or ''}".upper()
    if EXCLUIR.search(txt):
        return None
    if fam in FAMILIAS_COMPLETAS or (fam, sub) in SUBFAMILIAS:
        return f"familia:{fam}/{sub}"
    if marca in MARCAS and fam not in SIN_MARCA_EN and sub not in SIN_MARCA_EN:
        return f"marca:{marca}"
    pat = CLAVES.get((fam, sub))
    if pat and re.search(pat, txt):
        return f"clave:{fam}/{sub}"
    return None


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v).strip()) if str(v or "").strip() else None
    except InvalidOperation:
        return None


def to_row(n: int, i: dict, why: str) -> sl.Row:
    desc = " ".join((i.get("descripcion") or "").split())
    corta = " ".join((i.get("desc_corta") or "").split())
    marca = (i.get("marca") or "").strip()
    stock = _dec(i.get("stock"))
    # Solo los campos que el codigo usa: CABYS, hasta 6 fotos, precio, existencia y la regla.
    # El resto (descripcion, desc_corta, marca, modelo, familias) queda en columnas propias del item.
    raw: dict = {"_regla": why}
    for k in ("cod_hacienda", "stock", "precio", "image_url",
              "image_url_1", "image_url_2", "image_url_3", "image_url_4", "image_url_5"):
        v = i.get(k)
        if v not in (None, ""):
            raw[k] = v
    return sl.Row(
        row=n,
        code=(i.get("codigo") or "").strip() or None,
        name=(corta or desc)[:300] or (i.get("codigo") or ""),
        description=desc + (f"\n{i['caracteristicas'].strip()}" if (i.get("caracteristicas") or "").strip() else ""),
        brand=sc.detect_brand(desc, default=marca.title() if marca else None),
        model=((i.get("modelo") or "").strip() or sc.extract_model(desc) or None),
        category=sl._pretty(i.get("FamiliaPadre")),
        subcategory=sl._pretty(i.get("familia")),
        stock=int(stock) if stock is not None else None,
        stock_raw=i.get("stock"),
        cost=_dec(i.get("precio")),
        currency=(i.get("currency_code") or "USD").strip().upper()[:3],
        tax_rate=None,  # mismo criterio que la lista en Excel (los precios coinciden al ±1 %)
        raw=raw,
        error=None if (i.get("codigo") or "").strip() else "sin código",
    )


def build_parsed(items: list[dict]) -> sl.Parsed:
    rows = [to_row(n, i, why) for n, i in enumerate(items, start=1) if (why := relevant(i))]
    return sl.Parsed(
        format="sirett",
        header_row=0,
        headers=[],
        columns={},
        rows=rows,
        categories=len({(r.category, r.subcategory) for r in rows}),
    )


# ---------- quien es Eurocomp ----------
def _key(name: str | None) -> str:
    """ "Eurocomp", "EUROCOMP COSTA RICA" y "Eurocomp Costa Rica" son el mismo proveedor (primera palabra)."""
    return (name or "").strip().split(" ")[0].lower()


def is_eurocomp(supplier: Supplier | None) -> bool:
    return supplier is not None and _key(supplier.name) == _key(settings.eurocomp_supplier)


def find_supplier(db: Session, tenant_id: int) -> Supplier | None:
    return next((s for s in db.scalars(select(Supplier).where(Supplier.tenant_id == tenant_id).order_by(Supplier.id)) if is_eurocomp(s)), None)


# ---------- estado (para Ajustes / catalogo de proveedores) ----------
def _state(t: Tenant) -> dict:
    return dict(((t.settings or {}).get("supplier_sync") or {}).get("eurocomp") or {})


def _save_state(t: Tenant, **vals) -> None:
    st = dict(t.settings or {})
    ss = dict(st.get("supplier_sync") or {})
    ss["eurocomp"] = {**_state(t), **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in vals.items()}}
    st["supplier_sync"] = ss
    t.settings = st


def status(t: Tenant, supplier: Supplier | None) -> dict:
    """Lo que ve el catalogo de proveedores en lugar de "API pendiente". Trae las dos horas: el catalogo semanal
    (last_ok_at) y las existencias de lo vinculado cada 6 h (last_stock_at), y el ultimo error de cualquiera de las dos."""
    client = SirettClient.from_settings()
    if not is_eurocomp(supplier):
        return {"status": "no_aplica", "label": None}
    if client is None:
        return {"status": "pendiente", "label": "Conexión por API: pendiente de credenciales (SiReTT)"}
    s = _state(t)
    if s.get("last_stock_error") and (s.get("last_stock_try_at") or "") > (s.get("last_stock_at") or ""):
        return {"status": "error", "label": f"Conectado por API (SiReTT) · el último refresco de existencias falló: {s['last_stock_error']}", **s}
    if s.get("last_error") and (s.get("last_try_at") or "") > (s.get("last_ok_at") or ""):
        return {"status": "error", "label": f"Conectado por API (SiReTT) · la última sincronización falló: {s['last_error']}", **s}
    if s.get("last_ok_at") or s.get("last_stock_at"):
        return {
            "status": "conectado",
            "label": "Conectado por API (SiReTT) · catálogo cada semana (solo precios y existencias de lo que ya está), existencias de lo vinculado cada 6 horas",
            **s,
        }
    return {"status": "listo", "label": "Conectado por API (SiReTT) · todavía no se sincroniza", **s}


# ---------- sincronizar ----------
class SyncRefused(Exception):
    pass


def trim_memory() -> None:
    """Devuelve al sistema la memoria que Python ya libero (glibc la retiene si no se le pide). En Railway se paga la
    RAM tomada, no la usada: sin esto cada proceso queda con el pico de la ultima sincronizacion."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):  # fuera de Linux/glibc no hace falta
        pass


def fetch(client: SirettClient | None = None) -> tuple[list[dict], int]:
    """Catalogo completo de la bodega de Eurocomp, leido por partes y filtrado al vuelo: devuelve (lo que le sirve a
    Crimson, cuantos articulos mando Eurocomp). Lanza SirettError."""
    client = client or SirettClient.from_settings()
    if client is None:
        raise SirettError(-1, "Faltan las credenciales de Eurocomp (EUROCOMP_WS_ID y EUROCOMP_WS_PASSWORD, o EUROCOMP_WS_KEY)")
    seen = 0

    def keep(i: dict) -> bool:
        nonlocal seen
        seen += 1
        return relevant(i) is not None

    return client.bodega_items(settings.eurocomp_bodega, keep=keep), seen


def apply(db: Session, t: Tenant, items: list[dict], *, user_id: int | None = None, received: int | None = None) -> dict:
    """Aplica el catalogo ya descargado a una empresa (flush, sin commit). Lanza SyncRefused si la lista es sospechosa."""
    supplier = find_supplier(db, t.id) or sl.resolve_supplier(db, t.id, None, settings.eurocomp_supplier)[0]
    parsed = build_parsed(items)
    before = (
        db.scalar(
            select(func.count()).where(
                SupplierItem.tenant_id == t.id, SupplierItem.supplier_id == supplier.id, SupplierItem.active, SupplierItem.source == "api"
            )
        )
        or 0
    )
    if not parsed.rows or (before >= 100 and len(parsed.rows) < before * MIN_RATIO):
        raise SyncRefused(f"Eurocomp devolvió {len(parsed.rows)} artículos útiles (había {before} activos): no se aplica por precaución")
    now = datetime.now(UTC)
    res = sl.apply_list(
        db,
        t,
        supplier,
        parsed,
        list_date=now.astimezone(CR).date(),
        source="api",
        source_file=f"SiReTT · bodega {settings.eurocomp_bodega}",
        user_id=user_id,
        only_price_stock=True,
    )
    enriched = enrich_products(db, t, supplier, now)
    db.flush()
    out = {"supplier_id": supplier.id, "recibidos": received if received is not None else len(items), "utiles": len(parsed.rows), **res["summary"], **enriched}
    audit(db, t.id, user_id, "supplier_sync", "supplier", supplier.id, out)
    return out


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def enrich_products(db: Session, t: Tenant, supplier: Supplier, now: datetime) -> dict:
    """A los productos vinculados: fecha real de la consulta (no solo el dia). CABYS y fotos (si les faltan) UNA sola
    vez por vinculo (enriched_at): lo recien vinculado o creado desde el panel ("Agregar a mi catalogo") los recibe en
    la corrida siguiente; lo que ya se completo no vuelve a traer fotos (pedido 21/10; si alguien borro las fotos de un
    producto, la sincronizacion no se las devuelve)."""
    cabys = images = 0
    linked = db.scalars(
        select(SupplierItem).where(
            SupplierItem.tenant_id == t.id, SupplierItem.supplier_id == supplier.id, SupplierItem.active, SupplierItem.product_id.is_not(None)
        )
    ).all()
    for it in linked:
        p = db.get(Product, it.product_id)
        if p is None or p.tenant_id != t.id:
            continue
        raw = it.raw or {}
        if p.supplier_id == supplier.id:
            p.supplier_updated_at = now  # la tienda decide "bajo pedido" con la hora real de la consulta
        if it.enriched_at is not None and (it.linked_at is None or _aware(it.linked_at) <= _aware(it.enriched_at)):
            continue  # este vinculo ya se completo: solo precio y existencia
        it.enriched_at = datetime.now(UTC)  # despues del vinculo de esta misma corrida (sc.link marca su propia hora)
        code = (raw.get("cod_hacienda") or "").strip()
        if not p.cabys_code and re.fullmatch(r"\d{13}", code):
            p.cabys_code, p.cabys_set_at = code, now  # cabys_set_by queda vacio: lo puso el proveedor, no una persona
            cabys += 1
        if not p.images:
            urls = [u for k in ("image_url", *(f"image_url_{n}" for n in range(1, 6))) if (u := image_url(raw.get(k)))]
            if urls:
                p.images = [{"url": u, "main": n == 0} for n, u in enumerate(dict.fromkeys(urls))][:MAX_IMAGES]
                images += 1
    return {"cabys_completados": cabys, "fotos_completadas": images}


def targets(db: Session) -> list[Tenant]:
    """Empresas que trabajan con Eurocomp: las que tienen el proveedor; si ninguna lo tiene, la unica empresa activa."""
    ids = {s.tenant_id for s in db.scalars(select(Supplier)) if is_eurocomp(s)}
    tenants = [t for t in db.scalars(select(Tenant).where(Tenant.active)) if t.id in ids]
    if not tenants:
        act = db.scalars(select(Tenant).where(Tenant.active).limit(2)).all()
        tenants = list(act) if len(act) == 1 else []
    return tenants


def sync_tenant(
    db: Session,
    t: Tenant,
    *,
    user_id: int | None = None,
    client: SirettClient | None = None,
    items: list[dict] | None = None,
    received: int | None = None,
) -> dict:
    """Descarga (si no viene ya descargado) y aplica para una empresa, guardando el estado (exito o error) aunque
    falle. Hace commit."""
    now = datetime.now(UTC)
    try:
        if items is None:
            items, received = fetch(client)
        out = apply(db, t, items, user_id=user_id, received=received)
    except (SirettError, SyncRefused) as e:
        db.rollback()
        t = db.get(Tenant, t.id)
        msg = e.message if isinstance(e, SirettError) else str(e)
        _save_state(t, last_try_at=now, last_error=msg, last_code=getattr(e, "code", None))
        db.commit()
        log.warning("Eurocomp (empresa %s): %s", t.id, msg)
        raise
    changed = {"last_change_at": now} if out.get("cambios_reales") else {}
    _save_state(t, last_try_at=now, last_ok_at=now, last_error=None, last_code=0, last_summary=out, **changed)
    db.commit()
    log.info(
        "Eurocomp (empresa %s): %s utiles de %s, %s nuevos, %s actualizados", t.id, out["utiles"], out["recibidos"], out.get("nuevo"), out.get("actualizado")
    )
    # el catalogo tambien copia el costo a lo vinculado: el guardian de margen revisa ya (su propio commit; nunca
    # deshace la sincronizacion)
    from . import margin_guard

    margin_guard.run_safe(db, t, reason="catálogo de Eurocomp")

    # version del catalogo sube: la tarea del snapshot la lee con debounce
    from .store_showcase import bump_catalog_version

    t_fresh = db.get(Tenant, t.id) or t  # re-leer por si margin_guard lo expiro de la sesion
    bump_catalog_version(db, t_fresh)
    db.commit()

    # purgar supplier_item inactivos viejos sin producto vinculado
    sup = find_supplier(db, t.id)
    if sup:
        purged = purge_old_inactive(db, t, sup)
        if purged:
            log.info("Eurocomp (empresa %s): %s supplier_item inactivos purgados", t.id, purged)
        db.commit()

    # auto_catalog: si está activo, crea productos para las familias configuradas con los artículos recién sincronizados
    t_fresh = db.get(Tenant, t.id)
    if t_fresh and ac_settings(t_fresh).get("enabled"):
        sup = find_supplier(db, t.id)
        if sup:
            try:
                ac_out = auto_catalog_run(db, t_fresh, sup, commit_batches=True)
                if ac_out.get("creados"):
                    log.info(
                        "Eurocomp auto_catalog (empresa %s): %s creados, %s Hikvision destacados",
                        t.id, ac_out["creados"], ac_out.get("destacados_hik", 0),
                    )
            except Exception as exc:
                log.warning("Eurocomp auto_catalog (empresa %s): error no crítico: %s", t.id, exc)

    return out


def sync_all(db: Session, client: SirettClient | None = None) -> dict:
    """Catalogo completo (lunes 03:17): UNA descarga aplicada a cada empresa que trabaja con Eurocomp."""
    tenants = targets(db)
    if not tenants:
        return {"skipped": "ninguna empresa con el proveedor"}
    if (client or SirettClient.from_settings()) is None:
        return {"skipped": "sin credenciales de Eurocomp"}
    now = datetime.now(UTC)
    try:
        items, received = fetch(client)
    except SirettError as e:
        for t in tenants:
            _save_state(t, last_try_at=now, last_error=e.message, last_code=e.code)
        db.commit()
        log.warning("Eurocomp: %s", e.message)
        return {"error": e.message, "code": e.code}
    out = {}
    for t in tenants:
        try:
            out[t.id] = sync_tenant(db, t, items=items, received=received)
        except (SirettError, SyncRefused) as e:  # el estado ya quedo guardado; las demas empresas siguen
            out[t.id] = {"error": str(e)}
    return out


# ---------- existencias de lo vinculado (cada 6 horas) ----------

def _priority_product_ids(db: Session, tid: int) -> set[int]:
    """IDs de productos que deben refrescarse en el ciclo de 6h: activos y publicados en la tienda,
    o usados en cotizaciones/pedidos abiertos. El resto espera al semanal."""
    from ..models.sales import Quote, QuoteLine
    from ..models.store import Order, OrderLine

    pub = set(db.scalars(
        select(Product.id).where(Product.tenant_id == tid, Product.active, Product.show_on_web)
    ))
    open_q = set(db.scalars(
        select(QuoteLine.product_id)
        .join(Quote, Quote.id == QuoteLine.quote_id)
        .where(Quote.tenant_id == tid, Quote.status.in_(("creado", "enviada")), QuoteLine.product_id.is_not(None))
    ))
    open_o = set(db.scalars(
        select(OrderLine.product_id)
        .join(Order, Order.id == OrderLine.order_id)
        .where(
            Order.tenant_id == tid,
            Order.status.not_in(("cancelado", "entregado", "reembolsado")),
            OrderLine.product_id.is_not(None),
        )
    ))
    return pub | open_q | open_o


def purge_old_inactive(db: Session, t: Tenant, supplier: Supplier) -> int:
    """Borra supplier_item inactivos sin producto vinculado con mas de 60 dias: reducen la RAM y el
    espacio en Postgres sin perder ningun vinculo vivo."""
    cutoff = datetime.now(UTC) - timedelta(days=60)
    result = db.execute(
        delete(SupplierItem).where(
            SupplierItem.tenant_id == t.id,
            SupplierItem.supplier_id == supplier.id,
            SupplierItem.active.is_(False),
            SupplierItem.product_id.is_(None),
            SupplierItem.updated_at < cutoff,
        )
    )
    return result.rowcount


def refresh_linked(db: Session, client: SirettClient | None = None) -> dict:
    """Existencia y costo de los articulos de Eurocomp vinculados a un producto, consultando en lotes
    de 50 codigos (items_list, sin limite de frecuencia en Eurocomp). Solo refresca los activos y
    publicados en la tienda o usados en cotizaciones/pedidos abiertos; el resto espera al semanal.
    Procesa y hace commit por lote para liberar memoria al terminar cada uno. No crea ni inactiva
    articulos: eso es del catalogo semanal. Un codigo que Eurocomp ya no devuelve queda en stock=0."""
    tenants = targets(db)
    if not tenants:
        return {"skipped": "ninguna empresa con el proveedor"}
    client = client or SirettClient.from_settings()
    if client is None:
        return {"skipped": "sin credenciales de Eurocomp"}
    now = datetime.now(UTC)
    fx = sc.usd_rate(db)
    out: dict = {}

    for t in tenants:
        sup = find_supplier(db, t.id)
        if sup is None:
            continue

        # Todos los items activos vinculados
        all_items = list(
            db.scalars(
                select(SupplierItem).where(
                    SupplierItem.tenant_id == t.id,
                    SupplierItem.supplier_id == sup.id,
                    SupplierItem.active,
                    SupplierItem.product_id.is_not(None),
                )
            )
        )

        # Solo los prioritarios (publicados o en documentos abiertos)
        priority = _priority_product_ids(db, t.id)
        items = [i for i in all_items if i.product_id in priority]
        if not items:
            out[t.id] = {"vinculados": 0, "cambios": 0, "sin_dato": 0}
            _save_state(t, last_stock_at=now, last_stock_try_at=now, last_stock_summary=out[t.id], last_stock_error=None)
            db.commit()
            continue

        cambios = sin_dato = 0
        batch_size = 50

        for batch_start in range(0, len(items), batch_size):
            batch = items[batch_start : batch_start + batch_size]
            batch_codes = [i.code for i in batch if i.code]

            try:
                fresh = {(r.get("codigo") or "").strip(): r for r in client.items_list(settings.eurocomp_bodega, batch_codes)}
            except SirettError as e:
                # Error en este lote: guardar estado y abortar los demas lotes de esta empresa
                _save_state(t, last_stock_try_at=now, last_stock_error=e.message, last_try_at=now, last_error=e.message, last_code=e.code)
                db.commit()
                log.warning("Eurocomp (existencias, empresa %s): %s", t.id, e.message)
                out[t.id] = {"error": e.message, "code": e.code}
                break

            for it in batch:
                r = fresh.get(it.code)
                if r is None:
                    sin_dato += 1
                stock = _dec(r.get("stock")) if r else Decimal(0)
                new_stock = int(stock) if stock is not None else it.stock
                cost = _dec(r.get("precio")) if r else None
                new_cost = cost if cost is not None and cost > 0 else it.cost
                if new_stock != it.stock or (new_cost is not None and (it.cost is None or Decimal(str(it.cost)) != new_cost)):
                    cambios += 1
                if new_cost is not None and it.cost is not None and Decimal(str(it.cost)) != new_cost:
                    it.prev_cost = it.cost
                if new_stock != it.stock or (new_cost is not None and (it.cost is None or Decimal(str(it.cost)) != new_cost)):
                    it.changed_at = now
                it.stock, it.cost = new_stock, new_cost
                if r:
                    it.raw = {**(it.raw or {}), "stock": r.get("stock"), "precio": r.get("precio")}
                it.list_date = now.astimezone(CR).date()

            db.flush()
            for pid in sorted({i.product_id for i in batch}):
                p = db.get(Product, pid)
                if p is None or p.tenant_id != t.id:
                    continue
                sc.sync_product(db, p, fx=fx)
                if any(i.product_id == pid and i.supplier_id == p.supplier_id for i in batch):
                    p.supplier_updated_at = now
            # Commit por lote: libera memoria de las instancias del lote anterior
            db.commit()
        else:
            # El bucle termino sin break: todos los lotes procesados correctamente
            res = {"vinculados": len(items), "cambios": cambios, "sin_dato": sin_dato}
            prev = _state(t)
            clear = {"last_error": None} if prev.get("last_error") and prev.get("last_error") == prev.get("last_stock_error") else {}
            if cambios:
                clear["last_stock_change_at"] = now
            _save_state(t, last_stock_at=now, last_stock_try_at=now, last_stock_summary=res, last_stock_error=None, **clear)
            from .store_showcase import bump_catalog_version

            bump_catalog_version(db, t)
            db.commit()
            out[t.id] = res

    log.info("Eurocomp (existencias): %s", out)
    # costo nuevo -> margen nuevo: su propio commit; un error suyo no deshace el refresco.
    from . import margin_guard

    for t in tenants:
        if t.id in out and "error" not in out.get(t.id, {}):
            margin_guard.run_safe(db, t, reason="costos de Eurocomp")
    return out


# ---------- catálogo automático (auto_catalog) ----------
# Marcas de la familia Hikvision que se destacan (web_featured=True) al crearse
HIKVISION_BRANDS = frozenset({"HIKVISION", "HILOOK", "HIKMICRO"})

AC_DEFAULT: dict = {
    "enabled": False,
    "families": ["Seguridad", "Redes y conectividad", "Respaldo eléctrico", "Smart home", "Electrodomésticos"],
    "publish_web": False,
}
# Familias que se agregaron despues de que la config ya estaba guardada: entran una sola vez; si el admin las quita
# despues, quedan en familias_vistas y no vuelven a aparecer.
AC_NUEVAS = ("Electrodomésticos",)


def ac_settings(t: Tenant) -> dict:
    """Configuración de publicación automática (tenant.settings.supplier_sync.eurocomp.auto_catalog)."""
    cfg = {**AC_DEFAULT, **(_state(t).get("auto_catalog") or {})}
    vistas = set(cfg.get("familias_vistas") or [])
    nuevas = [f for f in AC_NUEVAS if f not in vistas and f not in (cfg.get("families") or [])]
    if nuevas:
        cfg["families"] = [*(cfg.get("families") or []), *nuevas]
    return cfg


def ac_save(t: Tenant, vals: dict) -> None:
    """Guarda (merge) la configuración de auto_catalog en tenant.settings sin pisar las demás claves."""
    _save_state(t, auto_catalog={**ac_settings(t), **vals, "familias_vistas": list(AC_NUEVAS)})


def _make_code(db: Session, tid: int, it: SupplierItem, supplier_name: str | None) -> str:
    """Código propio sugerido: igual que el cajón 'Agregar a mi catálogo' del router."""
    pref = re.sub(r"[^A-Z0-9]", "", (supplier_name or "PRV").upper())[:3] or "PRV"
    base = (it.model or f"{pref}-{it.code}")[:56]
    cand, n = base, 2
    while db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tid, Product.code == cand)):
        cand, n = f"{base}-{n}", n + 1
    return cand


def _suggest_cat(db: Session, tid: int, it: SupplierItem) -> int | None:
    """Categoría propia del ítem del proveedor: la que ya se llame como su subcategoría (o categoría); si no hay, se
    crea la familia del proveedor como categoría padre y la subfamilia como hija (22/10: Smart Home no tenía ninguna y
    sus productos quedaban fuera del menú de la tienda)."""
    from ..models import Category

    def find(name: str, parent_id: int | None | bool = False) -> Category | None:
        q = select(Category).where(Category.tenant_id == tid, func.lower(Category.name) == name.lower())
        if parent_id is not False:
            q = q.where(Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id)
        return db.scalars(q.order_by(Category.id)).first()

    for name in (it.subcategory, it.category):
        if name and (c := find(name)):
            return c.id
    if not it.category:
        return None
    parent = find(it.category, None)
    if parent is None:
        parent = Category(tenant_id=tid, name=it.category[:120], show_on_web=True)
        db.add(parent)
        db.flush()
    if not it.subcategory:
        return parent.id
    child = find(it.subcategory, parent.id)
    if child is None:
        child = Category(tenant_id=tid, name=it.subcategory[:120], parent_id=parent.id, show_on_web=True)
        db.add(child)
        db.flush()
    return child.id


def auto_catalog_run(
    db: Session,
    t: Tenant,
    supplier: Supplier | None = None,
    *,
    batch_size: int = 50,
    dry_run: bool = False,
    commit_batches: bool = False,
) -> dict:
    """Crea productos para los artículos de Eurocomp de las familias configuradas en auto_catalog
    que todavía no tienen producto vinculado.

    Idempotente: nunca duplica; respeta lo borrado (link_method set + product_id NULL = el admin lo
    borró) y lo ocultado/inactivo (product_id no es NULL = ya existe, no se toca).
    Hikvision/HiLook/HiMicro se crean con web_featured=True para que sobresalgan en la tienda.

    commit_batches=True: commit después de cada lote (uso desde el worker o CLI).
    False (default): solo flush, el llamador hace el commit (uso dentro de sync_tenant).
    """
    from ..models import ProductTax, Tax
    from . import pricing, product_names
    from .fx import FxMissing

    supplier = supplier or find_supplier(db, t.id)
    if supplier is None:
        return {"skipped": "proveedor Eurocomp no encontrado"}
    cfg = ac_settings(t)
    if not cfg.get("enabled"):
        return {"skipped": "auto_catalog no habilitado"}
    families = list(cfg.get("families") or [])
    if not families:
        return {"skipped": "sin familias configuradas"}
    publish_web = bool(cfg.get("publish_web"))

    items = list(
        db.scalars(
            select(SupplierItem).where(
                SupplierItem.tenant_id == t.id,
                SupplierItem.supplier_id == supplier.id,
                SupplierItem.active,
                SupplierItem.category.in_(families),
                SupplierItem.product_id.is_(None),
            )
        )
    )

    fx = sc.usd_rate(db)
    margin = pricing.default_margin(t)
    iva_obj = db.scalar(select(Tax).where(Tax.tenant_id == t.id, Tax.rate_code == "08", Tax.active))
    sup_name = supplier.name

    creados = skip_borrado = skip_sin_costo = 0
    destacados = 0

    to_create = []
    for it in items:
        if it.link_method is not None:
            # link_method set pero product_id NULL → el producto fue borrado; respetamos la decisión del admin
            skip_borrado += 1
            continue
        if it.cost is None or Decimal(str(it.cost)) <= 0:
            skip_sin_costo += 1
            continue
        to_create.append(it)

    if dry_run:
        for it in to_create:
            if (it.brand or "").upper() in HIKVISION_BRANDS:
                destacados += 1
            creados += 1
        return {
            "creados": creados,
            "skip_borrado": skip_borrado,
            "skip_sin_costo": skip_sin_costo,
            "destacados_hik": destacados,
            "dry_run": True,
            "total_candidatos": len(to_create),
        }

    for i in range(0, len(to_create), batch_size):
        batch = to_create[i : i + batch_size]
        for it in batch:
            code = _make_code(db, t.id, it, sup_name)
            try:
                price = pricing.sale_price(db, Decimal(str(it.cost)), it.currency or "USD", "CRC", margin, fx)
            except FxMissing:
                price = Decimal(0)  # sin tipo de cambio: "precio a consultar" (el guardián de margen lo revisa)
            except Exception:
                skip_sin_costo += 1
                continue
            featured = (it.brand or "").upper() in HIKVISION_BRANDS
            specs = "\n".join((it.description or "").splitlines()[1:]).strip() or None
            cat_id = _suggest_cat(db, t.id, it)
            prod = Product(
                tenant_id=t.id,
                code=code,
                name=(product_names.tidy(it.name) or it.name)[:200],
                item_type="producto",
                unit="Unid",
                price=price,
                currency="CRC",
                cost=it.cost,
                cost_currency=it.currency or "USD",
                margin_pct=margin,
                brand=it.brand,
                model=it.model,
                supplier_id=it.supplier_id,
                supplier_sku=it.code,
                supplier_stock=it.stock,
                supplier_updated_at=sc.list_datetime(it.list_date),
                description_invoice=it.name[:2000],
                description_store=specs,
                category_id=cat_id,
                show_on_web=publish_web,
                web_featured=featured,
                cabys_code=None,
            )
            db.add(prod)
            db.flush()
            if iva_obj:
                prod.taxes.append(ProductTax(tax_id=iva_obj.id))
            sc.link(db, it, prod, "creado", None, fx=fx)
            if featured:
                destacados += 1
            creados += 1
        db.flush()
        if commit_batches:
            db.commit()
        trim_memory()

    if creados:
        # fotos y CABYS del proveedor para lo recien creado (lo mismo que hace la sincronizacion con lo vinculado)
        enrich_products(db, t, supplier, datetime.now(UTC))
        if commit_batches:
            db.commit()
    if not commit_batches:
        db.flush()

    return {
        "creados": creados,
        "skip_borrado": skip_borrado,
        "skip_sin_costo": skip_sin_costo,
        "destacados_hik": destacados,
        "publicados": creados if publish_web else 0,
    }
