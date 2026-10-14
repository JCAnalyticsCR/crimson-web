"""Listas de precios de proveedor (Excel / CSV) -> catalogo del proveedor (SupplierItem). No toca el catalogo propio
salvo para copiar costo y existencia a los productos YA vinculados (ver supplier_catalog.sync_product).

Formato Eurocomp ("Listado de Precios"): titulos arriba, encabezado en la fila que dice Codigo y Descripcion
(Fotografia | Codigo | Descripcion | Stock | Precio | Ordenar | SubTotal) y filas de CATEGORIA con texto solo en una
celda ("SEGURIDAD Camaras"). Otros proveedores: encabezados por sinonimos o un mapeo explicito {campo: encabezado}.

Idempotente por (proveedor, codigo): una segunda corrida con el mismo archivo no cambia nada. Lo que ya no viene en
la lista nueva queda inactivo (no se borra). La fecha de la lista queda en cada item y en el historial.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Product, Supplier, SupplierItem, SupplierListImport
from . import pricing
from . import supplier_catalog as sc
from . import tabular as tb
from .totals import d

# encabezados normalizados (tb.norm) que se reconocen para cada campo
FIELDS: dict[str, tuple[str, ...]] = {
    "code": (
        "codigo",
        "cod",
        "sku",
        "code",
        "codigo_proveedor",
        "codigo_producto",
        "numero_de_parte",
        "no_parte",
        "part_number",
        "parte",
        "referencia",
        "item",
    ),
    "name": ("descripcion", "description", "producto", "nombre", "detalle", "nombre_producto"),
    "cost": (
        "precio",
        "costo",
        "price",
        "cost",
        "precio_unitario",
        "precio_lista",
        "precio_distribuidor",
        "precio_usd",
        "costo_usd",
        "precio_sin_iva",
        "precio_neto",
    ),
    "stock": ("stock", "existencia", "existencias", "disponible", "inventario", "cantidad", "qty", "disponibilidad"),
    "category": ("categoria", "familia", "linea", "grupo", "category"),
    "subcategory": ("subcategoria", "sub_categoria", "subfamilia", "subgrupo", "subcategory"),
    "brand": ("marca", "brand", "fabricante"),
    "model": ("modelo", "model", "numero_de_modelo"),
    "currency": ("moneda", "divisa", "currency"),
    "tax": ("iva", "impuesto", "tarifa_iva", "tax"),
}
FIELD_LABELS = {
    "code": "Código",
    "name": "Descripción",
    "cost": "Precio / costo",
    "stock": "Existencias",
    "category": "Categoría",
    "subcategory": "Subcategoría",
    "brand": "Marca",
    "model": "Modelo",
    "currency": "Moneda",
    "tax": "IVA",
}
# grupos de las listas de Eurocomp: lo que sigue es la subcategoria ("SEGURIDAD Camaras")
GROUPS = (
    "ACCESORIOS",
    "AUDIO Y SONIDO",
    "DISPOSITIVOS DE VISUALIZACION",
    "REDES Y CONECTIVDAD",
    "REDES Y CONECTIVIDAD",
    "RESPALDO ELECTRICO",
    "SEGURIDAD",
    "CCTV",
    "VIDEOVIGILANCIA",
    "COMPUTO",
    "ALMACENAMIENTO",
    "CABLEADO ESTRUCTURADO",
    "TELEFONIA",
    "ENERGIA",
)
FIX = {"redes_y_conectivdad": "Redes y conectividad"}  # errata de la lista de Eurocomp
COST_JUMP_PCT = Decimal(25)  # cambio de costo que se marca como dudoso


class ListError(Exception):
    pass


@dataclass
class Row:
    row: int
    code: str | None = None
    name: str | None = None
    description: str | None = None
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    subcategory: str | None = None
    stock: int | None = None
    stock_raw: object = None
    cost: Decimal | None = None
    currency: str | None = None
    tax_rate: Decimal | None = None
    raw: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Parsed:
    format: str
    header_row: int
    headers: list[str]
    columns: dict[str, str | None]
    rows: list[Row]
    categories: int


def _pretty(s: str | None) -> str | None:
    t = " ".join(str(s or "").split()).strip(" -·")
    if not t:
        return None
    if tb.norm(t) in FIX:
        return FIX[tb.norm(t)]
    if t.isupper() and len(t) > 3:
        t = t.capitalize()
    return t[:120]


def split_category(raw: str) -> tuple[str | None, str | None]:
    """'SEGURIDAD Camaras' -> ('Seguridad', 'Camaras'); 'REDES Y CONECTIVDAD Switches' -> ('Redes y conectividad', 'Switches').
    Sin grupo conocido: las palabras en MAYUSCULA del inicio son la categoria y el resto la subcategoria."""
    t = " ".join(str(raw or "").split())
    n = tb.norm(t)
    for g in sorted(GROUPS, key=len, reverse=True):
        gn = tb.norm(g)
        if n == gn or n.startswith(gn + "_"):
            words = len(g.split(" "))
            parts = t.split(" ")
            return _pretty(" ".join(parts[:words])), _pretty(" ".join(parts[words:]))
    parts = t.split(" ")
    i = 0
    while i < len(parts) and parts[i].isupper() and any(ch.isalpha() for ch in parts[i]):
        i += 1
    if 0 < i < len(parts):
        return _pretty(" ".join(parts[:i])), _pretty(" ".join(parts[i:]))
    return _pretty(t), None


def _json_safe(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _find_header(grid: list[tuple[int, list]], mapping: dict[str, str] | None) -> int:
    """Indice (en grid) de la fila de encabezado: la que tiene codigo y descripcion (o los encabezados del mapeo)."""
    want = {tb.norm(h) for h in (mapping or {}).values() if h}
    for gi, (_, r) in enumerate(grid[:60]):
        cells = {tb.norm(c) for c in r if isinstance(c, str) and c.strip()}
        if want and want <= cells:
            return gi
        if not want and cells & set(FIELDS["code"]) and cells & set(FIELDS["name"]):
            return gi
    # como el importador de siempre: la primera fila con al menos 2 celdas de texto
    return next((gi for gi, (_, r) in enumerate(grid[:15]) if sum(1 for c in r if isinstance(c, str) and c.strip()) >= 2), 0)


def parse(data: bytes, filename: str, mapping: dict[str, str] | None = None, default_currency: str = "USD", default_brand: str | None = None) -> Parsed:
    try:
        grid = tb.read_grid(data, filename)
    except Exception as e:  # noqa: BLE001
        raise ListError(f"No se pudo leer el archivo: {e}") from e
    if not grid:
        raise ListError("El archivo no tiene filas")
    mapping = {k: v for k, v in (mapping or {}).items() if k in FIELDS and v}
    hi = _find_header(grid, mapping)
    header_cells = grid[hi][1]
    headers = [str(c).strip() if c not in (None, "") else "" for c in header_cells]
    normed = [tb.norm(h) for h in headers]
    col: dict[str, int | None] = {}
    for f, aliases in FIELDS.items():
        if f in mapping:
            target = tb.norm(mapping[f])
            col[f] = normed.index(target) if target in normed else None
            continue
        col[f] = next((normed.index(a) for a in aliases if a in normed), None)
    if col["code"] is None or col["name"] is None:
        raise ListError("No se encontraron las columnas de código y descripción. Indicá qué columna es cada una.")
    top = " ".join(str(c) for _, r in grid[:hi] for c in r if c)
    eurocomp = "eurocomp" in top.lower() or {"fotografia", "codigo", "descripcion", "stock", "precio"} <= set(normed)
    rows: list[Row] = []
    section: tuple[str | None, str | None] = (None, None)
    cats = 0

    def cell(r: list, f: str):
        i = col.get(f)
        return r[i] if i is not None and i < len(r) else None

    for rownum, r in grid[hi + 1 :]:
        code = tb.text(cell(r, "code"), 60)
        texts = [c for c in r if c not in (None, "") and str(c).strip()]
        if not code:
            # fila de categoria: un solo texto y sin precio (asi vienen en Eurocomp)
            if len(texts) == 1 and isinstance(texts[0], str) and cell(r, "cost") in (None, ""):
                section = split_category(texts[0])
                cats += 1
            elif cell(r, "name") not in (None, ""):
                rows.append(Row(row=rownum, error="Fila sin código: no se puede importar"))
            continue
        raw_name = str(cell(r, "name") or "").strip()
        raw = {headers[i] or f"col_{i + 1}": _json_safe(v) for i, v in enumerate(r) if i < len(headers) and v not in (None, "")}
        if not raw_name:
            rows.append(Row(row=rownum, code=code, error=f"{code}: sin descripción", raw=raw))
            continue
        lines = [x.strip() for x in raw_name.splitlines() if x.strip()]
        headline = lines[0][:300]
        model = (tb.text(cell(r, "model"), 80) or "").upper() or sc.extract_model(raw_name)
        stock_raw = cell(r, "stock")
        stock = tb.num(stock_raw)
        cost = tb.num(cell(r, "cost"))
        cur_raw = tb.norm(cell(r, "currency"))
        currency = "CRC" if cur_raw in ("crc", "colones", "colon", "c") else ("USD" if cur_raw in ("usd", "dolares", "dolar", "us") else default_currency)
        tax = tb.num(cell(r, "tax"))
        if tax is not None and 0 < tax < 1:
            tax = tax * 100
        cat_cell, sub_cell = tb.text(cell(r, "category"), 120), tb.text(cell(r, "subcategory"), 120)
        if cat_cell and not sub_cell and section == (None, None):
            category, subcategory = split_category(cat_cell)
        elif cat_cell:
            category, subcategory = _pretty(cat_cell), _pretty(sub_cell)
        else:
            category, subcategory = section[0], _pretty(sub_cell) or section[1]
        rows.append(
            Row(
                row=rownum,
                code=code,
                name=headline,
                description="\n".join(lines)[:4000],
                brand=tb.text(cell(r, "brand"), 60) or sc.detect_brand(raw_name, default_brand),
                model=model[:80] if model else None,
                category=category,
                subcategory=subcategory,
                stock=int(stock) if stock is not None else None,
                stock_raw=stock_raw,
                cost=cost.quantize(Decimal("0.0001")) if cost is not None else None,
                currency=currency,
                tax_rate=tax,
                raw=raw,
            )
        )
    return Parsed(
        format="eurocomp" if eurocomp else "generico",
        header_row=grid[hi][0],
        headers=[h for h in headers if h],
        columns={f: (headers[i] if i is not None else None) for f, i in col.items()},
        rows=rows,
        categories=cats,
    )


def resolve_supplier(db: Session, tenant_id: int, supplier_id: int | None, name: str | None) -> tuple[Supplier, bool]:
    if supplier_id:
        s = db.get(Supplier, supplier_id)
        if not s or s.tenant_id != tenant_id:
            raise ListError("Proveedor no encontrado")
        return s, False
    nm = " ".join(str(name or "").split())
    if not nm:
        raise ListError("Indicá el proveedor de la lista")
    s = db.scalar(select(Supplier).where(Supplier.tenant_id == tenant_id, func.lower(Supplier.name) == nm.lower()))
    if s:
        return s, False
    s = Supplier(tenant_id=tenant_id, name=nm[:160])
    db.add(s)
    db.flush()
    return s, True


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float, Decimal)) or isinstance(b, (int, float, Decimal)):
        return d(a) == d(b)
    return a == b


def apply_list(
    db: Session,
    tenant,
    supplier: Supplier,
    parsed: Parsed,
    *,
    list_date: date,
    source: str = "excel",
    source_file: str | None = None,
    user_id: int | None = None,
) -> dict:
    """Aplica la lista (flush, sin commit): el router hace commit (confirmar) o rollback (vista previa).
    Asi la vista previa es exactamente lo que va a pasar."""
    tid = tenant.id
    fx = sc.usd_rate(db)
    existing = {
        i.code.strip().upper(): i for i in db.scalars(select(SupplierItem).where(SupplierItem.tenant_id == tid, SupplierItem.supplier_id == supplier.id))
    }
    out: list[dict] = []
    seen: dict[str, int] = {}
    for r in parsed.rows:
        if r.error:
            out.append({"row": r.row, "action": "error", "code": r.code, "detail": r.error, "warnings": []})
            continue
        key = r.code.strip().upper()
        if key in seen:
            out.append(
                {
                    "row": r.row,
                    "action": "error",
                    "code": r.code,
                    "detail": f"Código repetido en el archivo (ya viene en la fila {seen[key]}); se ignora",
                    "warnings": [],
                }
            )
            continue
        seen[key] = r.row
        warnings: list[str] = []
        if r.cost is None:
            warnings.append("sin precio")
        elif r.cost <= 0:
            warnings.append("precio en cero")
        if r.stock is None and r.stock_raw not in (None, ""):
            warnings.append(f"existencia no numérica: {str(r.stock_raw)[:20]}")
        if r.stock is not None and r.stock < 0:
            warnings.append("existencia negativa")
        item = existing.get(key)
        new_vals = {
            "name": r.name,
            "description": r.description,
            "brand": r.brand,
            "model": r.model,
            "model_key": sc.model_key(r.model),
            "category": r.category,
            "subcategory": r.subcategory,
            "stock": r.stock,
            "cost": r.cost,
            "currency": r.currency or "USD",
            "tax_rate": r.tax_rate,
        }
        prev_cost = None
        if item is None:
            base = "nuevo"
            changed = []
        else:
            changed = [k for k, v in new_vals.items() if not _same(getattr(item, k), v)]
            if not item.active:
                changed.append("active")
            base = "actualizado" if changed else "sin_cambios"
            if "cost" in changed and item.cost is not None and r.cost is not None:
                prev_cost = d(item.cost)
                if prev_cost > 0:
                    pct = (d(r.cost) - prev_cost) / prev_cost * 100
                    if abs(pct) >= COST_JUMP_PCT:
                        warnings.append(f"costo cambió {pct:+.0f} %")
        if item is None:
            item = SupplierItem(tenant_id=tid, supplier_id=supplier.id, code=r.code.strip())
            db.add(item)
            existing[key] = item
        if prev_cost is not None:
            item.prev_cost = prev_cost
        for k, v in new_vals.items():
            setattr(item, k, v)
        item.active = True
        item.source = source
        item.source_file = (source_file or "")[:200] or None
        item.list_date = list_date
        item.raw = r.raw
        out.append(
            {
                "row": r.row,
                "action": "dudoso" if warnings else base,
                "base": base,
                "code": r.code,
                "name": r.name,
                "model": r.model,
                "category": r.category,
                "subcategory": r.subcategory,
                "stock": r.stock,
                "cost": r.cost,
                "prev_cost": prev_cost,
                "currency": r.currency,
                "changed": changed,
                "warnings": warnings,
                "detail": None,
            }
        )
    # lo que ya no viene en la lista queda inactivo (no se borra: el historial y los vinculos se conservan)
    gone = []
    for key, it in existing.items():
        if key not in seen and it.active:
            it.active = False
            gone.append({"code": it.code, "name": it.name, "product_id": it.product_id})
    db.flush()

    # vinculos exactos (codigo del proveedor o modelo unico) se hacen solos y se reportan; lo demas se sugiere
    idx = sc.product_index(db, tid)
    items = [i for i in existing.values()]
    taken = {i.product_id for i in items if i.product_id}
    model_count = Counter(i.model_key for i in items if i.active and i.model_key)
    links, suggestions = [], 0
    for it in items:
        if not it.active or it.product_id:
            continue
        m = sc.exact_match(idx, code=it.code, supplier_id=supplier.id, mkey=it.model_key)
        if m and m[0].id not in taken and (m[1] == "sku" or model_count[it.model_key] == 1):
            prod, method = m
            sc.link(db, it, prod, method, user_id, sync=False)
            taken.add(prod.id)
            links.append({"code": it.code, "name": it.name, "product_id": prod.id, "product_code": prod.code, "product_name": prod.name, "method": method})
        elif sc.suggest(idx, code=it.code, supplier_id=supplier.id, model=it.model, name=it.name, limit=1):
            suggestions += 1

    # productos propios vinculados: costo, existencia del proveedor y fecha desde la mejor oferta
    products = []
    for pid in sorted({i.product_id for i in items if i.product_id}):
        prod = db.get(Product, pid)
        if prod is None:
            continue
        cost_before = prod.cost
        cur_before = prod.cost_currency
        changes = sc.sync_product(db, prod, fx=fx)
        if changes:
            price_crc = sc.to_crc(prod.price, prod.currency, fx)
            cb = sc.to_crc(cost_before, cur_before, fx) if cost_before is not None else None
            ca = sc.to_crc(prod.cost, prod.cost_currency, fx) if prod.cost is not None else None
            products.append(
                {
                    "product_id": prod.id,
                    "code": prod.code,
                    "name": prod.name,
                    "cost_before": changes.get("cost", (cost_before, None))[0],
                    "cost_after": prod.cost,
                    "currency": prod.cost_currency,
                    "stock_before": changes.get("supplier_stock", (prod.supplier_stock, None))[0],
                    "stock_after": prod.supplier_stock,
                    "margin_before": pricing.margin_of(price_crc, cb) if price_crc and cb else None,
                    "margin_after": pricing.margin_of(price_crc, ca) if price_crc and ca else None,
                }
            )
    db.flush()

    summary = {
        "nuevo": sum(1 for o in out if o["action"] == "nuevo"),
        "actualizado": sum(1 for o in out if o["action"] == "actualizado"),
        "sin_cambios": sum(1 for o in out if o["action"] == "sin_cambios"),
        "dudoso": sum(1 for o in out if o["action"] == "dudoso"),
        "error": sum(1 for o in out if o["action"] == "error"),
        "inactivar": len(gone),
        "vinculos": len(links),
        "sugerencias": suggestions,
        "productos": len(products),
    }
    cats = Counter((o.get("category"), o.get("subcategory")) for o in out if o["action"] != "error")
    db.add(
        SupplierListImport(
            tenant_id=tid,
            supplier_id=supplier.id,
            list_date=list_date,
            source=source,
            source_file=(source_file or "")[:200] or None,
            imported_at=datetime.now(UTC),
            imported_by=user_id,
            summary=summary,
        )
    )
    return {
        "format": parsed.format,
        "header_row": parsed.header_row,
        "headers": parsed.headers,
        "columns": parsed.columns,
        "labels": FIELD_LABELS,
        "supplier": {"id": supplier.id, "name": supplier.name},
        "list_date": list_date,
        "rows": len(parsed.rows),
        "summary": summary,
        "categories": [{"category": c, "subcategory": s, "count": n} for (c, s), n in sorted(cats.items(), key=lambda x: (x[0][0] or "~", x[0][1] or ""))],
        "result": out[:2000],
        "inactivar": gone[:500],
        "vinculos": links[:500],
        "productos": products[:500],
        "fx": fx,
    }
