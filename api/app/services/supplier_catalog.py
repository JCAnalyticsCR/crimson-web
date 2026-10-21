"""Catalogo de proveedores: ofertas, vinculo con el catalogo propio y busqueda para levantamientos.

Dos mundos separados (Andres: "no mezclarlos con lo que nosotros tenemos localmente"):
- Product: lo que Crimson vende (su codigo, su precio de venta, su CABYS, sus existencias en bodega).
- SupplierItem: lo que ofrece cada proveedor (SU codigo, su descripcion, su existencia, su precio = nuestro costo).

Vinculo SupplierItem -> Product (product_id): exacto por codigo del proveedor (Product.supplier_sku) o por modelo
del fabricante se hace solo y se reporta; por nombre o modelo parecido solo se sugiere y alguien lo confirma.
Compatibilidad: cuando un producto esta vinculado, Product.cost / supplier_stock / supplier_updated_at se copian
de la mejor oferta (sync_product). Asi el costeo, la tienda "Bajo pedido" y todo lo existente sigue igual.

API para otros modulos (levantamientos):
    search_offers(db, tenant_id, *, terms=(), category=None, attrs=None, limit=10) -> list[dict]
    best_offer_for_product(db, product) -> dict | None
Cada oferta es un dict (ver _offer_propio / _offer_item): source 'propio'|'proveedor', product_id, supplier_item_id,
supplier_id, supplier, code, supplier_code, name, model, brand, category, cost, currency, cost_crc, sale_price,
sale_currency, stock_propio, stock_proveedor, updated_at, active (+ score en search_offers). Montos en Decimal.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, Product, Supplier, SupplierItem, Tenant
from . import availability, pricing
from . import tabular as tb
from .fx import rate_or_none
from .totals import d

# ---------- modelo del fabricante ----------
# Prefijos de modelos Hikvision / HiLook / Ezviz (DS-2CD..., iDS-..., AE-DC..., HWI-..., THC-..., PTZ-...)
MODEL_PREFIX = r"(?:I?DS|AE|HWI|HWT|HWN|HWP|HWK|THC|IPC|PTZ|ERI|CS)"
MODEL_RE = re.compile(rf"(?<![A-Za-z0-9])({MODEL_PREFIX}-[A-Za-z0-9][A-Za-z0-9\-/().+]*)", re.I)
BRANDS = {
    "HIKVISION": "Hikvision",
    "HILOOK": "HiLook",
    "EZVIZ": "Ezviz",
    "DAHUA": "Dahua",
    "UBIQUITI": "Ubiquiti",
    "TP-LINK": "TP-Link",
    "TPLINK": "TP-Link",
    "ZKTECO": "ZKTeco",
    "FORZA": "Forza",
    "APC": "APC",
    "EPCOM": "Epcom",
    "MIKROTIK": "MikroTik",
    "GRANDSTREAM": "Grandstream",
    "PANDUIT": "Panduit",
    "LINKEDPRO": "LinkedPro",
}
# modelo sin prefijo conocido: la palabra que sigue a la marca (HIKVISION NKS4245WBTH)
AFTER_BRAND_RE = re.compile(r"\b(?:" + "|".join(re.escape(b) for b in BRANDS) + r")\s+([A-Z0-9][A-Z0-9\-/()]{4,})", re.I)


def _balance(m: str) -> str:
    """Corta en el primer parentesis sin cerrar: 'DS-2CD1167G3-LIU(2.8MM)(LATAM' -> 'DS-2CD1167G3-LIU(2.8MM)'."""
    opened: list[int] = []
    for i, ch in enumerate(m):
        if ch == "(":
            opened.append(i)
        elif ch == ")":
            if not opened:
                return m[:i]
            opened.pop()
    return m[: opened[0]] if opened else m


def extract_model(text: str | None) -> str | None:
    """Modelo del fabricante dentro de una descripcion. None si no hay uno reconocible."""
    t = str(text or "")
    for line in [t.splitlines()[0] if t.strip() else "", t]:
        m = MODEL_RE.search(line)
        if m:
            out = _balance(m.group(1)).rstrip("-/.+").upper()
            if len(out) >= 5 and any(ch.isdigit() for ch in out):
                return out[:80]
    m = AFTER_BRAND_RE.search(t.splitlines()[0] if t.strip() else "")
    if m:
        out = _balance(m.group(1)).rstrip("-/.+").upper()
        if any(ch.isdigit() for ch in out) and any(ch.isalpha() for ch in out):
            return out[:80]
    return None


def model_key(model: str | None) -> str | None:
    """Modelo normalizado para casar: solo letras y numeros, en mayuscula (DS-2CD1047G3-LIU -> DS2CD1047G3LIU)."""
    k = re.sub(r"[^A-Z0-9]", "", str(model or "").upper())
    return k[:80] or None


def base_key(model: str | None) -> str | None:
    """Modelo sin variantes entre parentesis (lente, empaque, region): para SUGERIR, nunca para vincular solo."""
    m = re.sub(r"\([^)]*\)", "", str(model or ""))
    m = m.split("(")[0]
    return model_key(m)


def detect_brand(text: str | None, default: str | None = None) -> str | None:
    up = str(text or "").upper()
    best = None
    for raw, nice in BRANDS.items():
        m = re.search(rf"(?<![A-Z0-9]){re.escape(raw)}(?![A-Z0-9])", up)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), nice)
    return best[1] if best else default


STOP = {"de", "del", "la", "el", "los", "las", "para", "con", "sin", "y", "en", "a", "o", "por", "un", "una", "the", "and", "with", "for"}


def name_tokens(text: str | None) -> set[str]:
    return {t for t in tb.norm(text).split("_") if t and t not in STOP and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------- tipo de cambio y fechas ----------
def usd_rate(db: Session) -> Decimal | None:
    """Venta del dolar (services/fx) o None si no hay ninguna tasa: el refresco de existencias y las listas siguen
    funcionando; lo que necesita colones queda sin calcular (None), nunca convertido con 1."""
    return rate_or_none(db, "USD")


def to_crc(amount, currency: str | None, fx: Decimal | None) -> Decimal | None:
    if amount is None:
        return None
    a = d(amount)
    if (currency or "USD") == "CRC":
        return a
    return (a * d(fx)).quantize(Decimal("0.01")) if fx is not None and d(fx) > 0 else None


def list_datetime(list_date: date | None) -> datetime | None:
    """Fecha de la lista -> marca de tiempo para Product.supplier_updated_at (la tienda mide su antiguedad)."""
    if list_date is None:
        return None
    if list_date >= date.today():
        return datetime.now(UTC)
    return datetime.combine(list_date, time(12, 0), tzinfo=UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# ---------- indice del catalogo propio para casar ----------
@dataclass
class ProductIndex:
    by_sku: dict[str, list[Product]] = field(default_factory=lambda: defaultdict(list))
    by_model: dict[str, list[Product]] = field(default_factory=lambda: defaultdict(list))
    by_base: dict[str, list[Product]] = field(default_factory=lambda: defaultdict(list))
    tokens: list[tuple[Product, set[str]]] = field(default_factory=list)
    by_id: dict[int, Product] = field(default_factory=dict)


def product_model(p: Product) -> str | None:
    """Modelo del producto propio: el campo modelo o, si esta vacio, el que aparezca en el nombre."""
    return (p.model or "").strip() or extract_model(p.name)


def product_index(db: Session, tenant_id: int) -> ProductIndex:
    idx = ProductIndex()
    for p in db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.active, Product.item_type == "producto")):
        idx.by_id[p.id] = p
        if p.supplier_sku and p.supplier_sku.strip():
            idx.by_sku[p.supplier_sku.strip().upper()].append(p)
        m = product_model(p)
        k, b = model_key(m), base_key(m)
        if k and len(k) >= 6:
            idx.by_model[k].append(p)
        if b and len(b) >= 6:
            idx.by_base[b].append(p)
        idx.tokens.append((p, name_tokens(p.name)))
    return idx


def exact_match(idx: ProductIndex, *, code: str, supplier_id: int, mkey: str | None) -> tuple[Product, str] | None:
    """Vinculo que se puede hacer solo: codigo del proveedor igual (y el producto es de ese proveedor o no tiene
    proveedor) o modelo exacto, siempre que haya UN solo producto candidato."""
    c = (code or "").strip().upper()
    cands = [p for p in idx.by_sku.get(c, []) if p.supplier_id in (None, supplier_id)]
    if len(cands) == 1:
        return cands[0], "sku"
    if mkey and len(mkey) >= 6:
        cands = idx.by_model.get(mkey, [])
        if len(cands) == 1:
            return cands[0], "modelo"
    return None


def suggest(idx: ProductIndex, *, code: str, supplier_id: int, model: str | None, name: str, limit: int = 5, exclude: Iterable[int] = ()) -> list[dict]:
    """Candidatos para confirmar a mano: SKU de otro proveedor, modelo parecido (otra variante) o nombre parecido."""
    skip = set(exclude)
    out: dict[int, dict] = {}

    def add(p: Product, reason: str, score: float, label: str) -> None:
        if p.id in skip:
            return
        cur = out.get(p.id)
        if cur is None or score > cur["score"]:
            out[p.id] = {"product_id": p.id, "code": p.code, "name": p.name, "model": p.model, "reason": reason, "label": label, "score": round(score, 2)}

    c = (code or "").strip().upper()
    for p in idx.by_sku.get(c, []):
        add(p, "sku", 1.0, "Mismo código del proveedor")
    mk = model_key(model)
    if mk:
        for p in idx.by_model.get(mk, []):
            add(p, "modelo", 0.98, "Mismo modelo")
    bk = base_key(model)
    if bk:
        for p in idx.by_base.get(bk, []):
            add(p, "modelo_parecido", 0.9, "Mismo modelo, otra variante")
    toks = name_tokens(name)
    if toks:
        for p, pt in idx.tokens:
            j = _jaccard(toks, pt)
            if j >= 0.55:
                add(p, "nombre", 0.5 + j / 2.5, f"Nombre parecido ({round(j * 100)} %)")
    return sorted(out.values(), key=lambda r: -r["score"])[:limit]


# ---------- vinculo y sincronizacion ----------
def link(db: Session, item: SupplierItem, product: Product, method: str, user_id: int | None = None, *, sync: bool = True, fx: Decimal | None = None) -> None:
    item.product_id = product.id
    item.link_method = method
    item.linked_at = datetime.now(UTC)
    item.linked_by = user_id
    db.flush()
    if sync:
        sync_product(db, product, fx=fx)


def unlink(db: Session, item: SupplierItem, fx: Decimal | None = None) -> Product | None:
    """Quita el vinculo. Si el producto se queda sin ofertas y sus datos de proveedor venian de este item,
    deja de reportar existencia del proveedor (no queda "Bajo pedido" por un vinculo equivocado). El costo queda."""
    prod = db.get(Product, item.product_id) if item.product_id else None
    item.product_id = None
    item.link_method = None
    item.linked_at = None
    item.linked_by = None
    db.flush()
    if prod is not None:
        others = linked_items(db, prod.id)
        if others:
            sync_product(db, prod, fx=fx)
        elif prod.supplier_sku == item.code and prod.supplier_id == item.supplier_id:
            prod.supplier_stock = None
            prod.supplier_updated_at = None
    return prod


def linked_items(db: Session, product_id: int) -> list[SupplierItem]:
    return list(db.scalars(select(SupplierItem).where(SupplierItem.product_id == product_id).order_by(SupplierItem.id)))


def _best(items: Sequence[SupplierItem], fx: Decimal) -> SupplierItem | None:
    """Mejor oferta: activa, con existencia, la mas barata en colones y la lista mas reciente."""
    act = [i for i in items if i.active and i.cost is not None]
    if not act:
        return None

    def key(i: SupplierItem):
        return (0 if (i.stock or 0) > 0 else 1, to_crc(i.cost, i.currency, fx) or Decimal(0), -(i.list_date.toordinal() if i.list_date else 0), i.id)

    return sorted(act, key=key)[0]


def sync_product(db: Session, product: Product, fx: Decimal | None = None) -> dict:
    """Copia al producto propio el costo, la existencia y la fecha de la mejor oferta vinculada.
    Devuelve lo que cambio (para el reporte del import)."""
    items = linked_items(db, product.id)
    if not items:
        return {}
    fx = fx if fx is not None else usd_rate(db)
    before = {"cost": product.cost, "cost_currency": product.cost_currency, "supplier_stock": product.supplier_stock}
    best = _best(items, fx)
    dates = [i.list_date for i in items if i.list_date]
    newest = max(dates) if dates else None
    if best is None:
        # ninguna oferta vigente: el proveedor ya no lo tiene (no queda "Bajo pedido")
        product.supplier_stock = 0
        if newest:
            product.supplier_updated_at = list_datetime(newest)
    else:
        product.cost = best.cost
        product.cost_currency = best.currency or "USD"
        product.supplier_stock = sum(int(i.stock or 0) for i in items if i.active and (i.stock or 0) > 0)
        product.supplier_updated_at = list_datetime(best.list_date or newest) or product.supplier_updated_at
        product.supplier_id = best.supplier_id
        product.supplier_sku = best.code
    after = {"cost": product.cost, "cost_currency": product.cost_currency, "supplier_stock": product.supplier_stock}

    def same(a, b) -> bool:
        if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
            return d(a) == d(b)
        return a == b

    return {k: (before[k], after[k]) for k in before if not same(before[k], after[k])}


# ---------- ofertas ----------
def _supplier_names(db: Session, ids: Iterable[int]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {s.id: s.name for s in db.scalars(select(Supplier).where(Supplier.id.in_(ids)))}


def own_stock(db: Session, tenant_id: int, product_ids: Iterable[int]) -> dict[int, Decimal]:
    """Lo libre propio de cada producto con la funcion unica (bodegas de venta menos reservas; garantias no)."""
    ids = list({i for i in product_ids if i})
    if not ids:
        return {}
    rows = availability.availability_many(db, tenant_id, ids, audience="interno")
    return {pid: Decimal(str(r.get("own_free") or 0)) for pid, r in rows.items()}


def stock_fields(row: dict | None) -> dict:
    """Las existencias de una oferta propia, desde la fila interna de services/availability."""
    r = row or {}
    return {
        "stock_propio": Decimal(str(r.get("own_free") or 0)),
        "stock_proveedor": r.get("supplier_qty"),
        "stock_proveedor_vendible": r.get("supplier_available"),
        "supplier_fresh": bool(r.get("supplier_fresh")),
        "supplier_updated_at": r.get("supplier_updated_at"),
        "supplier_age": r.get("supplier_age"),
        "availability_state": r.get("state"),
    }


def item_freshness(i: SupplierItem, max_age_h: int, now: datetime | None = None) -> dict:
    """Frescura de un item SIN vincular (solo tiene la fecha de la lista): mismo umbral que la funcion unica."""
    at = availability.list_datetime(i.list_date)
    fresh = at is not None and (now or datetime.now(UTC)) - at <= timedelta(hours=max_age_h)
    return {"supplier_fresh": bool(fresh), "supplier_updated_at": at, "supplier_age": availability.age_text(at)}


def _offer_item(i: SupplierItem, supplier: str | None, fx: Decimal, max_age_h: int | None = None) -> dict:
    return {
        "source": "proveedor",
        "product_id": i.product_id,
        "supplier_item_id": i.id,
        "supplier_id": i.supplier_id,
        "supplier": supplier,
        "code": i.code,
        "supplier_code": i.code,
        "name": i.name,
        "model": i.model,
        "brand": i.brand,
        "category": " · ".join(x for x in (i.category, i.subcategory) if x) or None,
        "cost": d(i.cost) if i.cost is not None else None,
        "currency": i.currency or "USD",
        "cost_crc": to_crc(i.cost, i.currency, fx),
        "sale_price": None,
        "sale_currency": None,
        "stock_propio": None,
        "stock_proveedor": i.stock,
        "updated_at": i.list_date,
        "active": bool(i.active),
        **(item_freshness(i, max_age_h) if max_age_h is not None else {}),
    }


def _offer_propio(p: Product, row: dict | None, supplier: str | None, category: str | None, fx: Decimal) -> dict:
    """Oferta del catalogo propio: las existencias salen de la funcion unica (row = fila interna)."""
    return {
        "source": "propio",
        "product_id": p.id,
        "supplier_item_id": None,
        "supplier_id": p.supplier_id,
        "supplier": supplier,
        "code": p.code,
        "supplier_code": p.supplier_sku,
        "name": p.name,
        "model": p.model or extract_model(p.name),
        "brand": p.brand,
        "category": category,
        "cost": d(p.cost) if p.cost is not None else None,
        "currency": p.cost_currency or "USD",
        "cost_crc": to_crc(p.cost, p.cost_currency, fx),
        "sale_price": d(p.price) if p.price is not None else None,
        "sale_currency": p.currency,
        "updated_at": _aware(p.supplier_updated_at).date() if p.supplier_updated_at else None,
        "active": bool(p.active),
        **stock_fields(row),
    }


def offers_for_product(db: Session, product: Product, fx: Decimal | None = None) -> list[dict]:
    """Todas las ofertas de proveedor vinculadas a un producto propio (activas primero, la mejor arriba)."""
    fx = fx if fx is not None else usd_rate(db)
    items = linked_items(db, product.id)
    names = _supplier_names(db, (i.supplier_id for i in items))
    best = _best(items, fx)
    out = []
    for i in items:
        o = _offer_item(i, names.get(i.supplier_id), fx)
        o["best"] = best is not None and i.id == best.id
        out.append(o)
    out.sort(key=lambda o: (not o["best"], not o["active"], o["cost_crc"] if o["cost_crc"] is not None else Decimal("1e18")))
    return out


def best_offer_for_product(db: Session, product: Product) -> dict | None:
    """La mejor oferta de proveedor vinculada al producto (activa, con existencia, la mas barata). None si no tiene."""
    if product is None:
        return None
    fx = usd_rate(db)
    items = linked_items(db, product.id)
    best = _best(items, fx)
    if best is None:
        return None
    sup = db.get(Supplier, best.supplier_id)
    return _offer_item(best, sup.name if sup else None, fx)


# ---------- busqueda para levantamientos ----------
def _stems(tok: str) -> set[str]:
    out = {tok}
    if len(tok) > 4 and tok.endswith("s"):
        out.add(tok[:-1])
    if len(tok) > 5 and tok.endswith("es"):
        out.add(tok[:-2])
    return out


class _Hay:
    """Texto normalizado donde buscar: tokens (para "camara" -> "camaras") y compacto ("4mp" -> "4 MP")."""

    def __init__(self, text: str):
        self.tokens = [t for t in tb.norm(text).split("_") if t]
        self.compact = "".join(self.tokens)
        self.starts = set()
        pos = 0
        for t in self.tokens:
            self.starts.add(pos)
            pos += len(t)

    def has(self, term: str) -> bool:
        tt = [t for t in tb.norm(term).split("_") if t]
        if not tt:
            return False
        last = _stems(tt[-1])
        n = len(tt)
        for i in range(len(self.tokens) - n + 1):
            if all(self.tokens[i + j] == tt[j] for j in range(n - 1)) and any(self.tokens[i + n - 1].startswith(s) for s in last):
                return True
        c = "".join(tt)
        if len(c) >= 3:
            at = self.compact.find(c)
            while at >= 0:
                if at in self.starts:
                    return True
                at = self.compact.find(c, at + 1)
        return False


def _attr_terms(attrs: dict | None) -> list[str]:
    out: list[str] = []
    for k, v in (attrs or {}).items():
        vals = v if isinstance(v, (list, tuple, set)) else [v]
        for x in vals:
            if x is True:
                out.append(str(k))
            elif x in (None, False, "") or isinstance(x, dict):
                continue
            else:
                out.append(str(x))
    return out


def search_offers(
    db: Session,
    tenant_id: int,
    *,
    terms: Sequence[str] = (),
    category: str | None = None,
    attrs: dict | None = None,
    limit: int = 10,
) -> list[dict]:
    """Busca en el catalogo propio Y en los catalogos de proveedor, y devuelve las mejores ofertas.

    - terms: palabras o modelos ("camara", "domo", "DS-2CD1143G2-LIU"); cada uno que aparece suma 1 punto
      (0.5 si solo aparece en la descripcion larga). Hace falta que aparezca al menos uno.
    - category: filtro (categoria propia o del proveedor, o la palabra en el nombre): "camaras", "grabadoras".
    - attrs: atributos del levantamiento como terminos suaves (0.5 c/u): {"resolucion": "4 MP", "tipo": "Domo",
      "alimentacion": "PoE", "analiticas": ["AcuSense"], "exterior": True}. True usa la clave como termino.
    - Un item de proveedor ya vinculado a un producto propio que salio en la busqueda no se repite: va dentro de
      la fila 'propio' (costo de la mejor oferta, stock_proveedor). Los vinculados a productos inactivos si salen.
    Orden: puntaje, luego lo que hay en bodega, luego lo que el proveedor tiene, luego propio, luego el costo.
    """
    term_list = [t for t in (str(x).strip() for x in terms or ()) if t]
    extra = [t for t in (s.strip() for s in _attr_terms(attrs)) if t]
    cat = tb.norm(category) if category else ""
    if not term_list and not extra and not cat:
        return []
    fx = usd_rate(db)

    cats = {c.id: c for c in db.scalars(select(Category).where(Category.tenant_id == tenant_id))}

    def cat_name(cid: int | None) -> str | None:
        c = cats.get(cid) if cid else None
        if not c:
            return None
        parent = cats.get(c.parent_id) if c.parent_id else None
        return f"{parent.name} · {c.name}" if parent else c.name

    def score(head: str, body: str, catstr: str) -> float | None:
        h, b = _Hay(f"{head} {catstr}"), _Hay(body)
        if cat:
            if not (_Hay(catstr).has(cat) or h.has(cat)):
                return None
        s = 0.0
        hits = 0
        for t in term_list:
            if h.has(t):
                s += 1
                hits += 1
            elif b.has(t):
                s += 0.5
                hits += 1
        if term_list and not hits:
            return None
        for t in extra:
            if h.has(t):
                s += 0.5
            elif b.has(t):
                s += 0.25
        if cat:
            s += 0.25
        return s

    rows: list[dict] = []
    prods = list(db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.active, Product.item_type == "producto")))
    hit_products: dict[int, float] = {}
    for p in prods:
        head = " ".join(x for x in (p.name, p.model or "", p.code, p.brand or "", p.supplier_sku or "") if x)
        sc = score(head, (p.description_invoice or "") + " " + (p.description_store or "")[:800], cat_name(p.category_id) or "")
        if sc is not None:
            hit_products[p.id] = sc
    items = list(db.scalars(select(SupplierItem).where(SupplierItem.tenant_id == tenant_id, SupplierItem.active)))
    active_ids = {p.id for p in prods}
    hit_items: list[tuple[SupplierItem, float]] = []
    for i in items:
        head = " ".join(x for x in (i.name, i.model or "", i.code, i.brand or "") if x)
        sc = score(head, (i.description or "")[:800], " ".join(x for x in (i.category, i.subcategory) if x))
        if sc is None:
            continue
        if i.product_id and i.product_id in active_ids:
            # ya es nuestro: cuenta para el producto propio (con su puntaje si es mejor), no se repite
            hit_products[i.product_id] = max(hit_products.get(i.product_id, 0), sc)
            continue
        hit_items.append((i, sc))

    pmap = {p.id: p for p in prods}
    av = availability.availability_many(db, tenant_id, [pmap[pid] for pid in hit_products], audience="interno")
    max_age_h = availability.max_age_hours(availability.settings_of(db.get(Tenant, tenant_id)))
    names = _supplier_names(db, [pmap[pid].supplier_id for pid in hit_products] + [i.supplier_id for i, _ in hit_items])
    for pid, sc in hit_products.items():
        p = pmap[pid]
        a = av.get(pid) or {}
        o = _offer_propio(p, a, a.get("supplier_name") or (names.get(p.supplier_id) if p.supplier_id else None), cat_name(p.category_id), fx)
        o["score"] = sc
        rows.append(o)
    for i, sc in hit_items:
        o = _offer_item(i, names.get(i.supplier_id), fx, max_age_h)
        o["score"] = sc
        rows.append(o)

    def order(o: dict):
        own_ok = (o["stock_propio"] or 0) > 0
        sup_ok = (o["stock_proveedor"] or 0) > 0 and o.get("supplier_fresh", True)
        cost = o["cost_crc"] if o["cost_crc"] is not None else Decimal("1e18")
        return (-o["score"], not own_ok, not sup_ok, o["source"] != "propio", cost)

    rows.sort(key=order)
    return rows[: max(1, int(limit))]


# ---------- costo y margen para la lista de productos ----------
def cost_info(db: Session, tenant, products: Sequence[Product], fx: Decimal | None = None) -> dict[int, dict]:
    """De donde sale el costo de cada producto (proveedor vinculado, lista anterior o propio) y el margen real
    sobre la venta. Sin costo, el margen es el objetivo y queda marcado como provisional."""
    if not products:
        return {}
    fx = fx if fx is not None else usd_rate(db)
    ids = [p.id for p in products]
    items: dict[int, list[SupplierItem]] = defaultdict(list)
    for i in db.scalars(select(SupplierItem).where(SupplierItem.tenant_id == tenant.id, SupplierItem.product_id.in_(ids))):
        items[i.product_id].append(i)
    names = _supplier_names(db, [i.supplier_id for v in items.values() for i in v] + [p.supplier_id for p in products])
    default_margin = pricing.default_margin(tenant)
    out: dict[int, dict] = {}
    for p in products:
        linked = items.get(p.id, [])
        best = _best(linked, fx) if linked else None
        info: dict = {"cost_source": None, "cost_supplier": None, "cost_date": None, "supplier_offers": len([i for i in linked if i.active])}
        if linked:
            ref = best or max(linked, key=lambda i: (i.list_date or date.min, i.id))
            info.update(cost_source="proveedor", cost_supplier=names.get(ref.supplier_id), cost_date=ref.list_date, cost_active=best is not None)
        elif p.cost is not None and p.supplier_id and p.supplier_updated_at:
            info.update(cost_source="proveedor", cost_supplier=names.get(p.supplier_id), cost_date=_aware(p.supplier_updated_at).date(), cost_active=True)
        elif p.cost is not None:
            info.update(cost_source="propio", cost_date=_aware(p.updated_at).date() if p.updated_at else None)
        cost_crc = to_crc(p.cost, p.cost_currency, fx) if p.cost is not None else None
        price_crc = to_crc(p.price, p.currency, fx) if p.price is not None else None
        info["cost_crc"] = cost_crc
        if cost_crc and cost_crc > 0 and price_crc and price_crc > 0:
            info["margin_real_pct"] = pricing.margin_of(price_crc, cost_crc)
            info["margin_provisional"] = False
        elif price_crc and price_crc > 0:
            info["margin_real_pct"] = d(p.margin_pct) if p.margin_pct is not None else default_margin
            info["margin_provisional"] = True
        else:
            info["margin_real_pct"] = None
            info["margin_provisional"] = None
        out[p.id] = info
    return out
