"""Lotes de contenido de la tienda (O3-11): borradores, aprobacion, muestra de exactitud, aplicar y deshacer.

Antes, el tipo, las especificaciones y los datos clave se calculaban en cada peticion: cambiar una regla cambiaba toda
la tienda al instante, sin revision ni marcha atras. Ahora una regla PROPONE (catalog_draft) y nada se ve en la tienda
hasta que un administrador:
1. revisa el lote en el panel («Contenido por aprobar») y aprueba por fila o el lote entero;
2. registra una muestra revisada a mano (50 productos, o todo el lote si es menor) con menos de 2 % de error;
3. aplica. Aplicar escribe SOLO columnas web_ (nunca costo, existencia, precio, nombre fiscal ni codigo), guarda lo
   que habia y marca el catalogo una vez por lote. Deshacer vuelve exactamente a lo de antes, por lote.

Lo que tenga web_manual (escrito a mano) no se toca nunca. Un solo borrador abierto (propuesto o aprobado) por producto
y campo: generar dos veces la misma regla no duplica nada (idempotente).

Los generadores (que valor proponer para cada campo) se registran aqui por campo: nombre comercial (O3-03, product_names),
especificaciones (O3-05) y descripcion (O3-06). Todo por reglas y plantillas: ninguna IA ni servicio externo.

Las corridas grandes van por lotes de 50 con commit por lote y liberacion de memoria (como auto_catalog_run), ordenadas
por id y nunca dentro de la ventana de la sincronizacion de los lunes 03:17 ni del guardian de margen que la sigue.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CatalogBatch, CatalogDraft, Category, Product, Tenant

# campo -> (columna del producto, nombre visible). Aplicar solo escribe estas columnas.
FIELDS: dict[str, str] = {
    "web_name": "Nombre comercial",
    "web_subtitle": "Subtítulo",
    "web_type": "Tipo de equipo",
    "web_specs": "Especificaciones",
    "web_description": "Descripción",
}
STATES = ("propuesto", "aprobado", "aplicado", "descartado", "deshecho")
OPEN = ("propuesto", "aprobado")
CHUNK = 50  # productos por tanda (commit y memoria)
SAMPLE_SIZE = 50  # muestra revisada a mano antes de aplicar (o todo el lote si es menor)
MAX_ERROR = Decimal("0.02")  # error de la muestra: menor al 2 %
PREVIEW_MAX = 200  # filas que devuelve una simulacion

CR = ZoneInfo("America/Costa_Rica")
# lunes 03:17 sincroniza Eurocomp y despues corre el guardian de margen: margen amplio antes y despues
SYNC_WINDOW = (0, time(2, 45), time(5, 30))  # (lunes, desde, hasta) hora de Costa Rica


class DraftError(Exception):
    """Algo que el panel le explica a la persona (422)."""


# generador: (producto, contexto) -> (valor propuesto o None, motivo para revisar o None)
Generator = Callable[[Product, "GenCtx"], tuple[object | None, str | None]]
GENERATORS: dict[str, tuple[str, Generator]] = {}


def register(field: str, rule: str, fn: Generator) -> None:
    GENERATORS[field] = (rule, fn)


class GenCtx:
    """Lo que un generador necesita una sola vez por corrida: categorias (tipo mostrado) y lotes aplicados."""

    def __init__(self, db: Session, tid: int):
        from . import product_typing as pt

        self.db, self.tid = db, tid
        self.cats = list(db.scalars(select(Category).where(Category.tenant_id == tid)))
        self.chain = pt.category_chain(self.cats)

    def dtype(self, p: Product) -> str:
        from . import product_typing as pt

        if p.web_type:
            return p.web_type
        leaf, top = self.chain.get(p.category_id, (None, None))
        return pt.display_type(p, leaf, top)

    def family(self, p: Product) -> str | None:
        return self.chain.get(p.category_id, (None, None))[1]


def in_sync_window(now: datetime | None = None) -> bool:
    n = (now or datetime.now(UTC)).astimezone(CR)
    day, start, end = SYNC_WINDOW
    return n.weekday() == day and start <= n.time() <= end


def _norm(v: object) -> object:
    if isinstance(v, str):
        return " ".join(v.split()) or None
    if isinstance(v, dict):
        return {str(k): str(x) for k, x in v.items()} or None
    return v


def _subtree(cats: list[Category], cid: int) -> set[int]:
    out, todo = {cid}, [cid]
    while todo:
        cur = todo.pop()
        for c in cats:
            if c.parent_id == cur and c.id not in out:
                out.add(c.id)
                todo.append(c.id)
    return out


def candidates(db: Session, tid: int, *, category_id: int | None = None, product_ids: Iterable[int] | None = None, published_only: bool = False) -> list[int]:
    q = select(Product.id).where(Product.tenant_id == tid, Product.active, Product.item_type == "producto", Product.web_manual.is_(False))
    if published_only:
        q = q.where(Product.show_on_web)
    if category_id is not None:
        cats = list(db.scalars(select(Category).where(Category.tenant_id == tid)))
        q = q.where(Product.category_id.in_(_subtree(cats, category_id)))
    if product_ids is not None:
        q = q.where(Product.id.in_(list(product_ids)))
    return list(db.scalars(q.order_by(Product.id)))


def _open_drafts(db: Session, field: str, pids: list[int]) -> dict[int, CatalogDraft]:
    if not pids:
        return {}
    rows = db.scalars(select(CatalogDraft).where(CatalogDraft.field == field, CatalogDraft.product_id.in_(pids), CatalogDraft.status.in_(OPEN)))
    return {d.product_id: d for d in rows}


def generate(
    db: Session,
    t: Tenant,
    field: str,
    *,
    category_id: int | None = None,
    product_ids: Iterable[int] | None = None,
    simulate: bool = False,
    user_id: int | None = None,
    source: str = "manual",
    commit: bool = False,
    published_only: bool = False,
    trim: bool = True,
) -> dict:
    """Propone el campo para los productos elegidos. simulate: no escribe nada y devuelve la vista previa. Sin
    simulate: crea UN lote con los borradores nuevos (idempotente: lo que ya tiene el mismo borrador abierto, o ya vale
    eso, no se repite; un borrador abierto con otro valor se reemplaza)."""
    from .supplier_sync import trim_memory

    if field not in FIELDS:
        raise DraftError("Campo no válido")
    if field not in GENERATORS:
        raise DraftError(f"Todavía no hay regla para «{FIELDS[field]}»")
    if not simulate and source != "alta" and in_sync_window():
        raise DraftError("Ahora corre la sincronización con el proveedor (lunes de 2:45 a 5:30): intente después")
    rule, fn = GENERATORS[field]
    ctx = GenCtx(db, t.id)
    ids = candidates(db, t.id, category_id=category_id, product_ids=product_ids, published_only=published_only)
    now = datetime.now(UTC)
    batch: CatalogBatch | None = None
    preview: list[dict] = []
    counts = {"productos": len(ids), "propuestos": 0, "sin_cambio": 0, "sin_dato": 0, "ya_propuesto": 0, "a_revision": 0}
    # O3-03: dos productos con el mismo nombre comercial terminan en pedidos equivocados: los dos van a revision
    unique = field == "web_name"
    seen: dict[str, dict | CatalogDraft] = {}

    def repeated(row: dict | CatalogDraft) -> None:
        for x in (row, seen.get(str(_v(row)))):
            if x is None:
                continue
            f = _flag(x)
            if not f or "nombre repetido" not in f:
                _set_flag(x, f"{f}, nombre repetido" if f else "nombre repetido")
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i : i + CHUNK]
        prods = list(db.scalars(select(Product).where(Product.id.in_(chunk)).order_by(Product.id)))
        opened = _open_drafts(db, field, chunk)
        for p in prods:
            value, flag = fn(p, ctx)
            value = _norm(value)
            if value is None:
                counts["sin_dato"] += 1
                continue
            if _norm(getattr(p, field)) == value:
                counts["sin_cambio"] += 1
                continue
            prev = opened.get(p.id)
            if prev is not None and _norm(prev.value) == value:
                counts["ya_propuesto"] += 1
                continue
            counts["propuestos"] += 1
            counts["a_revision"] += 1 if flag else 0
            if simulate:
                row = {"product_id": p.id, "name": p.name, "code": p.code, "before": getattr(p, field), "after": value, "flag": flag}
                if len(preview) < PREVIEW_MAX:
                    preview.append(row)
                if unique:
                    if str(value) in seen:
                        repeated(row)
                    seen.setdefault(str(value), row)
                continue
            if batch is None:
                batch = CatalogBatch(tenant_id=t.id, field=field, rule=rule, category_id=category_id, source=source, status="propuesto", created_by=user_id, created_at=now)
                db.add(batch)
                db.flush()
            if prev is not None:
                prev.status, prev.decided_at, prev.decided_by = "descartado", now, user_id  # reemplazado por la regla nueva
            draft = CatalogDraft(tenant_id=t.id, batch_id=batch.id, product_id=p.id, field=field, value=value, rule=rule, flag=flag, status="propuesto", created_at=now)
            db.add(draft)
            if unique:
                if str(value) in seen:
                    repeated(draft)
                seen.setdefault(str(value), draft)
        db.flush()
        if commit and not simulate:
            db.commit()
        if trim:
            trim_memory()
    out = {"field": field, "rule": rule, "counts": counts, "batch_id": batch.id if batch else None}
    if simulate:
        out["preview"] = preview
    return out


def _v(x: dict | CatalogDraft) -> object:
    return x["after"] if isinstance(x, dict) else x.value


def _flag(x: dict | CatalogDraft) -> str | None:
    return x["flag"] if isinstance(x, dict) else x.flag


def _set_flag(x: dict | CatalogDraft, f: str) -> None:
    if isinstance(x, dict):
        x["flag"] = f
    else:
        x.flag = f[:120]


def _batch(db: Session, tid: int, bid: int) -> CatalogBatch:
    b = db.get(CatalogBatch, bid)
    if not b or b.tenant_id != tid:
        raise LookupError("Lote no encontrado")
    return b


def _drafts(db: Session, b: CatalogBatch, ids: Iterable[int] | None = None, states: tuple[str, ...] | None = None) -> list[CatalogDraft]:
    q = select(CatalogDraft).where(CatalogDraft.batch_id == b.id)
    if ids is not None:
        q = q.where(CatalogDraft.id.in_(list(ids)))
    if states:
        q = q.where(CatalogDraft.status.in_(states))
    return list(db.scalars(q.order_by(CatalogDraft.id)))


def decide(db: Session, tid: int, bid: int, status: str, draft_ids: Iterable[int] | None, user_id: int | None) -> int:
    """aprobar (status=aprobado) o descartar (status=descartado) filas abiertas del lote (todas si draft_ids es None)."""
    if status not in ("aprobado", "descartado", "propuesto"):
        raise DraftError("Estado no válido")
    b = _batch(db, tid, bid)
    if b.status != "propuesto":
        raise DraftError("El lote ya se aplicó o se cerró: no se puede cambiar")
    now = datetime.now(UTC)
    rows = _drafts(db, b, draft_ids, OPEN)
    for d in rows:
        d.status, d.decided_by, d.decided_at = status, user_id, now
    if status == "descartado" and not _drafts(db, b, states=OPEN):
        b.status = "descartado"
    db.flush()
    return len(rows)


def sample_needed(n: int) -> int:
    return min(SAMPLE_SIZE, n)


def pick_sample(db: Session, tid: int, bid: int, seed: int | None = None) -> list[int]:
    """muestra al azar (reproducible por lote) de las filas aprobadas o propuestas"""
    b = _batch(db, tid, bid)
    ids = [d.id for d in _drafts(db, b, states=OPEN)]
    rnd = random.Random(seed if seed is not None else b.id)
    return sorted(rnd.sample(ids, sample_needed(len(ids))))


def record_sample(db: Session, tid: int, bid: int, checked: list[int], wrong: list[int], user_id: int | None) -> dict:
    """Registra la revision a mano. La muestra son filas del lote; las que la persona marco con error se descartan."""
    b = _batch(db, tid, bid)
    if b.status != "propuesto":
        raise DraftError("El lote ya se aplicó o se cerró")
    own = {d.id: d for d in _drafts(db, b)}
    checked_s, wrong_s = set(checked), set(wrong)
    if not checked_s <= set(own) or not wrong_s <= checked_s:
        raise DraftError("La muestra tiene filas que no son de este lote")
    rate = Decimal(len(wrong_s)) / Decimal(len(checked_s)) if checked_s else Decimal(1)
    b.sample = {"checked": sorted(checked_s), "wrong": sorted(wrong_s), "error": str(rate.quantize(Decimal("0.0001"))), "by": user_id, "at": datetime.now(UTC).isoformat()}
    now = datetime.now(UTC)
    for i in wrong_s:  # lo que esta mal no se aplica
        if own[i].status in OPEN:
            own[i].status, own[i].decided_by, own[i].decided_at = "descartado", user_id, now
    db.flush()
    return sample_status(db, b)


def sample_status(db: Session, b: CatalogBatch) -> dict:
    """La compuerta de exactitud: muestra de 50 (o todo el lote) y error menor al 2 %."""
    total = len([d for d in _drafts(db, b) if d.status != "descartado" or d.id in set((b.sample or {}).get("wrong") or [])])
    need = sample_needed(total)
    s = b.sample or {}
    checked, wrong = len(s.get("checked") or []), len(s.get("wrong") or [])
    rate = Decimal(wrong) / Decimal(checked) if checked else None
    ok = checked >= need and rate is not None and rate < MAX_ERROR
    return {"needed": need, "checked": checked, "wrong": wrong, "error": str(rate) if rate is not None else None, "ok": ok}


def apply(db: Session, t: Tenant, bid: int, user_id: int | None, commit: bool = False) -> dict:
    """Aplica las filas aprobadas del lote: escribe solo la columna web_ del campo y guarda lo anterior."""
    from .store_showcase import bump_catalog_version

    b = _batch(db, t.id, bid)
    if b.status != "propuesto":
        raise DraftError("El lote ya se aplicó o se cerró")
    st = sample_status(db, b)
    if not st["ok"]:
        raise DraftError(f"Falta la muestra revisada a mano: {st['needed']} filas con error menor al 2 % (hay {st['checked']} revisadas, {st['wrong']} con error)")
    rows = _drafts(db, b, states=("aprobado",))
    if not rows:
        raise DraftError("No hay filas aprobadas en el lote")
    col, now, applied, skipped = b.field, datetime.now(UTC), 0, 0
    for i in range(0, len(rows), CHUNK):
        part = rows[i : i + CHUNK]
        prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([d.product_id for d in part])))}
        for d in part:
            p = prods.get(d.product_id)
            if p is None or p.web_manual:  # alguien lo escribio a mano despues de proponer: no se pisa
                d.status, d.decided_at = "descartado", now
                skipped += 1
                continue
            d.previous = getattr(p, col)
            setattr(p, col, d.value)
            d.status, d.decided_by, d.decided_at = "aplicado", user_id, now
            applied += 1
        db.flush()
        if commit:
            db.commit()
    b.status, b.applied_by, b.applied_at = "aplicado", user_id, now
    bump_catalog_version(db, t)  # una vez por lote
    db.flush()
    return {"aplicados": applied, "omitidos": skipped}


def undo(db: Session, t: Tenant, bid: int, user_id: int | None) -> dict:
    """Deshace el lote: cada producto vuelve a lo que tenia antes de aplicar. Si alguien lo cambio despues (a mano o
    con otro lote), esa fila no se toca y se informa."""
    from .store_showcase import bump_catalog_version

    b = _batch(db, t.id, bid)
    if b.status != "aplicado":
        raise DraftError("Solo se puede deshacer un lote aplicado")
    rows = _drafts(db, b, states=("aplicado",))
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([d.product_id for d in rows] or [0])))}
    now, undone, changed = datetime.now(UTC), 0, []
    for d in rows:
        p = prods.get(d.product_id)
        if p is None:
            continue
        if _norm(getattr(p, b.field)) != _norm(d.value):
            changed.append(p.id)
            continue
        setattr(p, b.field, d.previous)
        d.status, d.decided_by, d.decided_at = "deshecho", user_id, now
        undone += 1
    b.status, b.undone_by, b.undone_at = "deshecho", user_id, now
    bump_catalog_version(db, t)
    db.flush()
    return {"deshechos": undone, "cambiados_despues": changed}


def on_new_product(db: Session, p: Product) -> None:
    """Gancho de product_from_item: cada producto nuevo deja sus borradores en el lote abierto de altas de cada campo
    (uno por campo y por empresa). No aplica nada. Si algo falla, el alta sigue (el contenido se puede generar luego)."""
    try:
        t = db.get(Tenant, p.tenant_id)
        for field in GENERATORS:
            b = db.scalar(
                select(CatalogBatch).where(
                    CatalogBatch.tenant_id == p.tenant_id, CatalogBatch.field == field, CatalogBatch.source == "alta", CatalogBatch.status == "propuesto"
                )
            )
            res = generate(db, t, field, product_ids=[p.id], source="alta", trim=False)
            if res["batch_id"] and b is not None and res["batch_id"] != b.id:
                # un solo lote abierto de altas por campo: los borradores nuevos se juntan con los anteriores
                for d in db.scalars(select(CatalogDraft).where(CatalogDraft.batch_id == res["batch_id"])):
                    d.batch_id = b.id
                db.delete(db.get(CatalogBatch, res["batch_id"]))
        db.flush()
    except Exception:  # noqa: BLE001 - el contenido nunca tumba un alta
        import logging

        logging.getLogger("crimson.catalogo").exception("No se pudieron proponer borradores para el producto %s", p.id)


def batches_out(db: Session, tid: int, limit: int = 100) -> list[dict]:
    from sqlalchemy import func

    rows = list(db.scalars(select(CatalogBatch).where(CatalogBatch.tenant_id == tid).order_by(CatalogBatch.id.desc()).limit(limit)))
    counts: dict[int, dict[str, int]] = {}
    if rows:
        for bid, st, n in db.execute(
            select(CatalogDraft.batch_id, CatalogDraft.status, func.count()).where(CatalogDraft.batch_id.in_([b.id for b in rows])).group_by(CatalogDraft.batch_id, CatalogDraft.status)
        ):
            counts.setdefault(bid, {})[st] = n
    cats = {c.id: c.name for c in db.scalars(select(Category).where(Category.tenant_id == tid))}
    return [batch_out(db, b, counts.get(b.id, {}), cats) for b in rows]


def batch_out(db: Session, b: CatalogBatch, counts: dict[str, int] | None = None, cats: dict[int, str] | None = None) -> dict:
    if counts is None:
        counts = {}
        for d in _drafts(db, b):
            counts[d.status] = counts.get(d.status, 0) + 1
    return {
        "id": b.id,
        "field": b.field,
        "field_label": FIELDS.get(b.field, b.field),
        "rule": b.rule,
        "category": (cats or {}).get(b.category_id) if b.category_id else None,
        "source": b.source,
        "status": b.status,
        "counts": counts,
        "sample": sample_status(db, b),
        "created_at": b.created_at,
        "applied_at": b.applied_at,
        "undone_at": b.undone_at,
    }


def drafts_out(db: Session, tid: int, bid: int) -> dict:
    b = _batch(db, tid, bid)
    rows = _drafts(db, b)
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([d.product_id for d in rows] or [0])))}
    return {
        "batch": batch_out(db, b),
        "items": [
            {
                "id": d.id,
                "product_id": d.product_id,
                "name": prods[d.product_id].name if d.product_id in prods else None,
                "code": prods[d.product_id].code if d.product_id in prods else None,
                "before": d.previous if d.status in ("aplicado", "deshecho") else (getattr(prods[d.product_id], b.field) if d.product_id in prods else None),
                "after": d.value,
                "flag": d.flag,
                "status": d.status,
                "in_sample": d.id in set((b.sample or {}).get("checked") or []),
                "wrong": d.id in set((b.sample or {}).get("wrong") or []),
            }
            for d in rows
        ],
    }


from . import catalog_rules  # noqa: E402, F401 - registra las reglas de cada campo (nombre, ficha, descripcion)
