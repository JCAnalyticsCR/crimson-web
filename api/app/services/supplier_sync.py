"""Sincronizacion del catalogo de Eurocomp por su WebService SiReTT (providers/sirett.py) -> catalogo de proveedores.

Que entra: SOLO lo que Crimson vende o instala (ver `relevant`): seguridad (camaras, grabadoras, control de acceso,
alarmas), redes, respaldo electrico, y de las familias mixtas el cableado, conectores, soportes, herramientas de red y
discos de videovigilancia; mas todo lo de marcas de seguridad (Hikvision, Ezviz, Ubiquiti...) en cualquier familia.
De 11.538 articulos de Eurocomp quedan ~1.650 (2026-10-15), incluidos los 218 que Crimson ya tenia.

Como entra: la lista filtrada se aplica con el MISMO motor que la lista en Excel (supplier_lists.apply_list,
source="api"): idempotente por codigo, lo que deja de venir queda inactivo, vinculos exactos por codigo/modelo y el
costo y la existencia del proveedor pasan a los productos vinculados. Nunca toca el precio de venta ni la existencia
PROPIA de Crimson (eso solo lo mueve el kardex). Ademas, al producto vinculado que no los tiene le completa el CABYS
(13 digitos, lo recomienda el propio SiReTT) y las fotos.

Cuando corre: el catalogo completo UNA vez por semana (lunes 03:17); es la consulta pesada (34 MB) y solo hace falta
para descubrir articulos nuevos, CABYS y fotos. Las existencias y costos de lo que Crimson YA vende (los vinculados)
se refrescan cada 6 horas con la consulta por lista de codigos (`refresh_linked`): unas pocas llamadas chicas, sin
limite de frecuencia en Eurocomp. Asi la tienda sigue diciendo "bajo pedido" con datos de horas, no de dias.

Seguridad de datos: si Eurocomp responde una lista sospechosamente corta (menos de la mitad de lo que habia) no se
aplica, para no dejar inactivo medio catalogo por una respuesta cortada.
"""

from __future__ import annotations

import ctypes
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
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
FAMILIAS_COMPLETAS = {"SEGURIDAD", "REDES Y CONECTIVDAD"}  # asi, con la errata, viene de SiReTT
SUBFAMILIAS = {
    ("RESPALDO ELÉCTRICO", "UPS"),
    ("RESPALDO ELÉCTRICO", "Baterias"),
    ("RESPALDO ELÉCTRICO", "Reguladores de Voltaje"),
    ("RESPALDO ELÉCTRICO", "Regletas"),
    ("RESPALDO ELÉCTRICO", "Protectores de Voltaje"),
    ("RESPALDO ELÉCTRICO", "Inversores/Conversores de Energía"),
    ("RESPALDO ELÉCTRICO", "Paneles Solares"),
}
MARCAS = {"HIKVISION", "HILOOK", "EZVIZ", "HIKMICRO", "UBIQUITI", "APC", "GRANDSTREAM", "ZKTECO", "DAHUA", "PARADOX", "RUIJIE", "REYEE", "MIKROTIK"}
SIN_MARCA_EN = {"SMART HOME", "ELECTRODOMESTICOS", "COMPONENTES DE PC", "VENTAS FOB/SERVICIOS"}  # familia padre o sub
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
    raw = {k: v for k, v in i.items() if v not in (None, "")}
    raw["_regla"] = why
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
    """Lo que ve el catalogo de proveedores en lugar de "API pendiente"."""
    client = SirettClient.from_settings()
    if not is_eurocomp(supplier):
        return {"status": "no_aplica", "label": None}
    if client is None:
        return {"status": "pendiente", "label": "Conexión por API: pendiente de credenciales (SiReTT)"}
    s = _state(t)
    if s.get("last_error") and (s.get("last_try_at") or "") > (s.get("last_ok_at") or ""):
        return {"status": "error", "label": f"Conectado por API (SiReTT) · la última sincronización falló: {s['last_error']}", **s}
    if s.get("last_ok_at"):
        return {"status": "conectado", "label": "Conectado por API (SiReTT) · catálogo cada semana, existencias de lo vinculado cada 6 horas", **s}
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
    )
    enriched = enrich_products(db, t, supplier, now)
    db.flush()
    out = {"supplier_id": supplier.id, "recibidos": received if received is not None else len(items), "utiles": len(parsed.rows), **res["summary"], **enriched}
    audit(db, t.id, user_id, "supplier_sync", "supplier", supplier.id, out)
    return out


def enrich_products(db: Session, t: Tenant, supplier: Supplier, now: datetime) -> dict:
    """A los productos vinculados: fecha real de la consulta (no solo el dia) y, si les falta, CABYS y fotos."""
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
    _save_state(t, last_try_at=now, last_ok_at=now, last_error=None, last_code=0, last_summary=out)
    db.commit()
    log.info(
        "Eurocomp (empresa %s): %s utiles de %s, %s nuevos, %s actualizados", t.id, out["utiles"], out["recibidos"], out.get("nuevo"), out.get("actualizado")
    )
    return out


def sync_all(db: Session, client: SirettClient | None = None) -> dict:
    """Tarea de cada hora: UNA descarga (Eurocomp permite una por hora) aplicada a cada empresa que trabaja con ellos."""
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
def refresh_linked(db: Session, client: SirettClient | None = None) -> dict:
    """Existencia y costo al momento de los articulos de Eurocomp que Crimson YA vende (vinculados a un producto), con
    la consulta por lista de codigos (de a 50, sin limite de frecuencia). No crea ni inactiva articulos: eso es del
    catalogo semanal. Un codigo que Eurocomp ya no devuelve queda con existencia 0 (no se vende "bajo pedido" lo que el
    proveedor no confirma). Hace commit."""
    tenants = targets(db)
    if not tenants:
        return {"skipped": "ninguna empresa con el proveedor"}
    client = client or SirettClient.from_settings()
    if client is None:
        return {"skipped": "sin credenciales de Eurocomp"}
    linked: dict[int, list[SupplierItem]] = {}
    for t in tenants:
        sup = find_supplier(db, t.id)
        if sup is not None:
            linked[t.id] = list(
                db.scalars(
                    select(SupplierItem).where(
                        SupplierItem.tenant_id == t.id,
                        SupplierItem.supplier_id == sup.id,
                        SupplierItem.active,
                        SupplierItem.product_id.is_not(None),
                    )
                )
            )
    codes = sorted({i.code for items in linked.values() for i in items if i.code})
    if not codes:
        return {"skipped": "nada vinculado"}
    now = datetime.now(UTC)
    try:
        fresh = {(r.get("codigo") or "").strip(): r for r in client.items_list(settings.eurocomp_bodega, codes)}
    except SirettError as e:
        log.warning("Eurocomp (existencias): %s", e.message)
        return {"error": e.message, "code": e.code}
    fx = sc.usd_rate(db)
    out = {}
    for t in tenants:
        items = linked.get(t.id) or []
        cambios = sin_dato = 0
        for it in items:
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
            it.stock, it.cost = new_stock, new_cost
            if r:
                it.raw = {**(it.raw or {}), "stock": r.get("stock"), "precio": r.get("precio")}
            it.list_date = now.astimezone(CR).date()
        db.flush()
        for pid in sorted({i.product_id for i in items}):
            p = db.get(Product, pid)
            if p is None or p.tenant_id != t.id:
                continue
            sc.sync_product(db, p, fx=fx)
            if any(i.product_id == pid and i.supplier_id == p.supplier_id for i in items):
                p.supplier_updated_at = now  # la tienda decide "bajo pedido" con la hora real de la consulta
        res = {"vinculados": len(items), "cambios": cambios, "sin_dato": sin_dato}
        _save_state(t, last_stock_at=now, last_stock_summary=res)
        out[t.id] = res
    db.commit()
    log.info("Eurocomp (existencias): %s", out)
    return out
