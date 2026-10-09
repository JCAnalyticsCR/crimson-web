"""Importador del catalogo publico de Fygaro (tienda.crimsoncr.com) al catalogo propio.

Entrada: el JSON del relevamiento (ver `app/tools/import_fygaro.py`):
    {"categories": [{"fygaro_id", "name"}], "products": [{"fygaro_id", "sku", "name", "description", "price",
     "price_with_tax", "currency", "tax_rate", "categories", "images", "variants", "availability"}]}

Reglas (idempotente; el modo por defecto NO escribe nada):
- Emparejado, en este orden: 1) el mapa fygaro_id -> producto que deja cada corrida aplicada (tenant.settings
  ["fygaro"]), 2) codigo == SKU, 3) SKU == modelo / SKU de proveedor / codigo normalizados, 4) nombre normalizado.
  El 4) es "dudoso": se empareja (nunca se duplica) y se reporta para que alguien lo mire.
- Producto nuevo: codigo = SKU (o FY-<8 primeros del id de Fygaro si no trae SKU). Precio de Fygaro SIN IVA
  (el `offers.price` del JSON-LD), en su moneda. Queda publicado en la tienda salvo que el precio venga vacio.
- Producto que ya existia (no creado por este importador): solo se completa lo que esta vacio (descripcion de
  tienda, fotos, categoria, IVA) y se publica. El precio NO se toca salvo `update_prices=True`; la diferencia sale
  en el reporte. Asi el import no pisa precios calculados por costo + margen (lista de proveedor).
- Producto creado por este importador: se sincroniza completo en cada corrida (nombre, descripcion, precio, fotos...).
- Fotos: se suben al mecanismo de multimedia propio (tabla media, dedupe por sha256). Se toman de una carpeta
  local (`images_dir`, lo que dejo el relevamiento) o se descargan SOLO del bucket de Fygaro (lista blanca de host).
- CABYS: Fygaro no lo publica. Los productos nuevos quedan sin CABYS -> Productos -> Completar CABYS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, Product, ProductTax, Tax, Tenant
from .documents import audit
from .tabular import norm

IMAGE_HOSTS = {"fygaro-subscribers.s3.amazonaws.com"}  # unico origen desde el que se descargan fotos
MAX_IMAGES = 8
# solo si el nombre EMPIEZA asi ("Kit de mantenimiento SPROTEK" es un producto; "Instalacion de camara" no)
SERVICE_RE = re.compile(r"^\s*(servicio|instalaci[oó]n|asesor[ií]a|mantenimiento|soporte t[eé]cnico|configuraci[oó]n)\b", re.I)
KNOWN_RATES = (Decimal(13), Decimal(4), Decimal(2), Decimal(1), Decimal(0))


@dataclass
class Row:
    fygaro_id: str
    name: str
    code: str | None = None
    action: str = "nuevo"  # nuevo | actualizar | sin_cambios | omitir | error
    match: str | None = None  # fygaro | codigo | modelo | nombre
    product_id: int | None = None
    price: str | None = None
    currency: str | None = None
    tax_rate: str | None = None
    category: str | None = None
    images: int = 0
    published: bool = False
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def doubtful(self) -> bool:
        return bool(self.warnings) or self.action == "error"

    def as_dict(self) -> dict:
        return {**self.__dict__, "doubtful": self.doubtful}


def _dec(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _rate(item: dict) -> tuple[Decimal, str | None]:
    """Tarifa de IVA a partir de lo que Fygaro exhibia. Primero se busca la tarifa vigente que, redondeada al
    centimo, reproduce exactamente el precio con IVA (en precios chicos el % deducido sale "12.0" o "13.6" solo por
    redondeo: 0.25 -> 0.28). Si ninguna calza, la mas cercana al % deducido; si queda lejos, 13 % y aviso."""
    net, gross, raw = _dec(item.get("price")), _dec(item.get("price_with_tax")), _dec(item.get("tax_rate"))
    if net and gross and net > 0:
        fits = [k for k in KNOWN_RATES if abs((net * (1 + k / 100)).quantize(Decimal("0.01")) - gross) <= Decimal("0.011")]
        if fits:  # en precios de 1 dolar el 0 % y el 1 % redondean igual: manda el % deducido
            k = min(fits, key=lambda x: abs(x - raw)) if raw is not None else fits[0]
            return k, ("Fygaro lo vendia SIN IVA (0 %): confirmar si es exento o corregir IVA/CABYS" if k == 0 else None)
    if raw is None:
        return Decimal(13), "IVA no deducible del precio: se asume 13 %"
    best = min(KNOWN_RATES, key=lambda k: abs(k - raw))
    if abs(best - raw) > Decimal("0.6"):
        return Decimal(13), f"IVA deducido {raw} % no corresponde a ninguna tarifa: se asume 13 %"
    return best, None


class _Index:
    """Busquedas en memoria sobre los productos del tenant (un SELECT, no uno por fila)."""

    def __init__(self, products: list[Product]):
        self.by_id = {p.id: p for p in products}
        self.by_code: dict[str, Product] = {}
        self.by_ref: dict[str, list[Product]] = {}
        self.by_name: dict[str, list[Product]] = {}
        for p in products:
            self.add(p)

    def add_new(self, p: Product) -> None:
        """Producto creado en esta misma corrida: solo por id y codigo. No entra al emparejado por modelo/nombre,
        asi el dry-run y la corrida real dan el mismo resultado."""
        self.by_id[p.id] = p
        self.by_code[p.code] = p

    def add(self, p: Product) -> None:
        self.by_id[p.id] = p
        self.by_code[p.code] = p
        for ref in {norm(p.code), norm(p.model), norm(p.supplier_sku)} - {""}:
            self.by_ref.setdefault(ref, []).append(p)
        self.by_name.setdefault(norm(p.name), []).append(p)


def _image_bytes(url: str, images_dir: Path | None, download: bool, client: httpx.Client | None) -> bytes | None:
    u = urlparse(url)
    base = Path(u.path).name
    if images_dir and base:
        f = images_dir / base
        if f.is_file():
            return f.read_bytes()
    if not download or u.scheme != "https" or u.hostname not in IMAGE_HOSTS or client is None:
        return None
    r = client.get(url, timeout=30)
    r.raise_for_status()
    return r.content


def run(
    db: Session,
    tenant: Tenant,
    catalog: dict,
    *,
    apply: bool = False,
    images_dir: Path | None = None,
    download_images: bool = True,
    update_prices: bool = False,
    user_id: int | None = None,
) -> dict:
    """Planifica (y con apply=True aplica) la importacion. Devuelve {summary, rows, redirects}. No hace commit."""
    from ..routers.media import public_url, save_media

    tid = tenant.id
    state = dict((tenant.settings or {}).get("fygaro") or {})
    pmap: dict[str, dict] = dict(state.get("products") or {})
    cmap: dict[str, int] = dict(state.get("categories") or {})
    index = _Index(list(db.scalars(select(Product).where(Product.tenant_id == tid))))
    taxes = {Decimal(str(t.rate)).quantize(Decimal(1)): t for t in db.scalars(select(Tax).where(Tax.tenant_id == tid, Tax.active))}
    cats = {norm(c.name): c for c in db.scalars(select(Category).where(Category.tenant_id == tid))}
    fy_cats = {str(c.get("fygaro_id")): c.get("name") for c in catalog.get("categories") or []}

    rows: list[Row] = []
    claimed: dict[int, str] = {}  # product_id -> fygaro_id (dos productos de Fygaro no pueden caer en el mismo)
    seen_sku: dict[str, str] = {}
    seen_name: dict[str, str] = {}
    client = httpx.Client(headers={"User-Agent": "CrimsonImport/1.0"}, follow_redirects=False) if apply and download_images else None
    stats = {"images_uploaded": 0, "images_reused": 0, "images_missing": 0}
    cabys_missing = 0

    def category_for(name: str | None, row: Row) -> Category | None:
        if not name:
            return None
        key = norm(name)
        c = cats.get(key)
        if not c:
            row.changes.append(f"categoria nueva: {name}")
            if apply:
                c = Category(tenant_id=tid, name=name[:120], show_on_web=True)
                db.add(c)
                db.flush()
                cats[key] = c
        return c

    try:
        for item in catalog.get("products") or []:
            fid = str(item.get("fygaro_id") or "").strip()
            name = " ".join(str(item.get("name") or "").split())[:200]
            row = Row(fygaro_id=fid, name=name)
            rows.append(row)
            if not fid or not name:
                row.action, row.warnings = "error", ["Sin id de Fygaro o sin nombre"]
                continue

            sku = " ".join(str(item.get("sku") or "").split())[:60] or None
            if sku and sku in seen_sku:
                row.warnings.append(f"SKU repetido en Fygaro ({sku}, tambien en {seen_sku[sku][:8]}): se usa codigo propio")
                sku_for_code = None
            else:
                sku_for_code = sku
                if sku:
                    seen_sku[sku] = fid
            code = sku_for_code or f"FY-{fid[:8].upper()}"
            nname = norm(name)
            if nname in seen_name:
                row.warnings.append(f"Nombre repetido dentro de Fygaro (tambien {seen_name[nname][:8]})")
            seen_name.setdefault(nname, fid)

            price = _dec(item.get("price"))
            currency = (item.get("currency") or tenant.default_currency or "CRC").upper()[:3]
            rate, rate_warn = _rate(item)
            if rate_warn:
                row.warnings.append(rate_warn)
            if rate not in taxes:
                row.warnings.append(f"Tarifa de IVA {rate} % no configurada: el producto queda sin impuesto")
            if price is None or price <= 0:
                row.warnings.append("Precio vacio en Fygaro: se importa sin publicar")
            if (item.get("availability") or "") == "agotado":
                row.warnings.append("Agotado en Fygaro")
            if item.get("variants"):
                row.warnings.append("Tiene variantes en Fygaro: revisar y crearlas a mano")
            kind = "servicio" if SERVICE_RE.search(name) else "producto"
            if kind == "servicio":
                row.warnings.append("Parece un servicio por el nombre: se importa como servicio")
            cat_names = [c for c in item.get("categories") or [] if c]
            if len(cat_names) > 1:
                row.warnings.append(f"En {len(cat_names)} categorias de Fygaro: se usa {cat_names[0]}")
            images = [u for u in (item.get("images") or []) if isinstance(u, str) and u.startswith("http")][:MAX_IMAGES]
            description = str(item.get("description") or "").strip()[:4000] or None

            # --- emparejado ---
            prod: Product | None = None
            entry = pmap.get(fid)
            if entry and index.by_id.get(entry.get("id")):
                prod, row.match = index.by_id[entry["id"]], "fygaro"
            elif code in index.by_code:
                prod, row.match = index.by_code[code], "codigo"
            elif sku and index.by_ref.get(norm(sku)):
                cands = index.by_ref[norm(sku)]
                prod, row.match = cands[0], "modelo"
                if len({p.id for p in cands}) > 1:
                    row.warnings.append(f"SKU {sku} coincide con {len(cands)} productos: se usa {cands[0].code}")
            elif index.by_name.get(nname):
                cands = index.by_name[nname]
                prod, row.match = cands[0], "nombre"
                row.warnings.append(f"Emparejado por nombre con {cands[0].code}: revisar que sea el mismo producto")
            if prod is not None and prod.id in claimed and claimed[prod.id] != fid:
                row.action = "omitir"
                row.warnings.append(f"El producto {prod.code} ya lo tomo otro articulo de Fygaro ({claimed[prod.id][:8]}): no se duplica")
                row.product_id, row.code = prod.id, prod.code
                continue

            owned = bool(prod is not None and entry and entry.get("created") and entry.get("id") == prod.id) or (
                prod is not None and prod.code == f"FY-{fid[:8].upper()}"
            )
            cat = category_for(cat_names[0] if cat_names else None, row)
            publish = price is not None and price > 0

            if prod is None:
                row.action, row.code = "nuevo", code
                row.changes.append(f"crear {code}")
                if apply:
                    prod = Product(tenant_id=tid, code=code, name=name, item_type=kind, unit="Sp" if kind == "servicio" else "Unid")
                    db.add(prod)
                    db.flush()
                    index.add_new(prod)
                owned = True
            else:
                row.action, row.code, row.product_id = "actualizar", prod.code, prod.id

            # --- que cambia (en un producto nuevo todo cambia: el reporte solo dice "crear") ---
            def note(label: str, row: Row = row):
                if row.action != "nuevo":
                    row.changes.append(label)

            def setf(attr: str, value, label: str, *, only_if_empty: bool = False, prod: Product | None = prod):
                cur = getattr(prod, attr) if prod is not None else None
                if only_if_empty and cur not in (None, "", []):
                    return
                if cur != value:
                    note(label)
                    if apply and prod is not None:
                        setattr(prod, attr, value)

            if owned:
                setf("name", name, "nombre")
                setf("item_type", kind, "tipo")
                if description:
                    setf("description_store", description, "descripcion de tienda")
                    setf("description_invoice", description, "descripcion de factura", only_if_empty=True)
            elif description:
                setf("description_store", description, "descripcion de tienda", only_if_empty=True)

            if price is not None and price > 0:
                cur_price = _dec(prod.price) if prod is not None and prod.price is not None else None
                cur_cur = prod.currency if prod is not None else None
                if owned or update_prices:
                    if cur_price is None or cur_price != price or cur_cur != currency:
                        note(f"precio {currency} {price}")
                        if apply and prod is not None:
                            prod.price, prod.currency = price, currency
                elif cur_price is not None and (cur_price != price or cur_cur != currency):
                    row.warnings.append(f"Precio distinto: sistema {cur_cur} {cur_price:.2f} vs Fygaro {currency} {price:.2f} (no se cambia)")
            row.price, row.currency, row.tax_rate = (str(price) if price is not None else None), currency, str(rate)

            if cat is not None and (owned or prod is None or prod.category_id is None):
                row.category = cat.name
                if prod is None or prod.category_id != cat.id:
                    note(f"categoria {cat.name}")
                    if apply and prod is not None:
                        prod.category_id = cat.id
            elif cat_names:
                row.category = cat_names[0]

            if rate in taxes and (owned or prod is None or not prod.taxes):
                has = prod is not None and len(prod.taxes) == 1 and prod.taxes[0].tax_id == taxes[rate].id
                if not has:
                    note(f"IVA {rate} %")
                    if apply and prod is not None:
                        prod.taxes.clear()
                        db.flush()
                        prod.taxes.append(ProductTax(tax_id=taxes[rate].id))

            if publish and (prod is None or not prod.show_on_web):
                note("publicar en tienda")
                if apply and prod is not None:
                    prod.show_on_web = True
            if not publish and owned and prod is not None and prod.show_on_web:
                row.changes.append("ocultar de la tienda (sin precio)")
                if apply:
                    prod.show_on_web = False
            row.published = publish or bool(prod is not None and prod.show_on_web and not owned)

            # --- fotos ---
            row.images = len(images)
            if images and (owned or prod is None or not prod.images):
                if apply and prod is not None:
                    out = []
                    for i, url in enumerate(images):
                        try:
                            data = _image_bytes(url, images_dir, download_images, client)
                        except (httpx.HTTPError, OSError) as e:
                            row.warnings.append(f"Foto {i + 1} no se pudo leer: {e.__class__.__name__}")
                            data = None
                        if not data:
                            stats["images_missing"] += 1
                            continue
                        try:
                            m, new = save_media(db, tid, data, Path(urlparse(url).path).name, user_id)
                        except HTTPException as e:
                            row.warnings.append(f"Foto {i + 1} rechazada: {e.detail}")
                            continue
                        stats["images_uploaded" if new else "images_reused"] += 1
                        out.append({"url": public_url(m), "main": not out})
                    if [x["url"] for x in out] != [x.get("url") for x in (prod.images or [])]:
                        note(f"{len(out)} foto(s)")
                        prod.images = out
                elif not apply and (prod is None or not prod.images):
                    note(f"{len(images)} foto(s)")

            if row.action == "actualizar" and not row.changes:
                row.action = "sin_cambios"
            if row.action == "nuevo" or (prod is not None and not prod.cabys_code):
                cabys_missing += 1
            if prod is not None:
                row.product_id = prod.id
                claimed[prod.id] = fid
                if apply:
                    pmap[fid] = {"id": prod.id, "created": bool(entry.get("created")) if entry else row.action == "nuevo", "code": prod.code}
                    if row.action in ("nuevo", "actualizar"):
                        audit(db, tid, user_id, "import", "product", prod.id, {"source": "fygaro", "fygaro_id": fid, "action": row.action, "match": row.match})
    finally:
        if client is not None:
            client.close()

    # terminos y privacidad de la tienda: se copian de Fygaro solo si Mi Tienda no tiene texto propio
    legal_in = {k: str(v).strip() for k, v in (catalog.get("legal") or {}).items() if k in ("terms", "privacy") and str(v or "").strip()}
    store_now = dict((tenant.settings or {}).get("store") or {})
    legal_now = dict(store_now.get("legal") or {})
    legal_new = {k: v[:20000] for k, v in legal_in.items() if not (legal_now.get(k) or "").strip()}
    legal_note = (
        ("importar " + " y ".join("terminos" if k == "terms" else "privacidad" for k in legal_new))
        if legal_new
        else ("ya tiene texto propio" if legal_in else "sin datos")
    )

    if apply:
        if legal_new:
            settings0 = dict(tenant.settings or {})
            settings0["store"] = {**store_now, "legal": {**legal_now, **legal_new}}
            tenant.settings = settings0
        for fcid, fname in fy_cats.items():
            c = cats.get(norm(fname or ""))
            if c is not None:
                cmap[fcid] = c.id
        settings = dict(tenant.settings or {})
        settings["fygaro"] = {"products": pmap, "categories": cmap, "imported_at": datetime.now(UTC).isoformat(timespec="seconds")}
        tenant.settings = settings  # reasignar: SQLAlchemy no detecta cambios dentro del JSON
        db.flush()

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.action] = counts.get(r.action, 0) + 1
    summary = {
        "total": len(rows),
        **{k: counts.get(k, 0) for k in ("nuevo", "actualizar", "sin_cambios", "omitir", "error")},
        "dudosos": sum(1 for r in rows if r.doubtful),
        "por_nombre": sum(1 for r in rows if r.match == "nombre"),
        "sin_precio": sum(1 for r in rows if any(w.startswith("Precio vacio") for w in r.warnings)),
        "precio_distinto": sum(1 for r in rows if any(w.startswith("Precio distinto") for w in r.warnings)),
        "sin_cabys": cabys_missing,
        "fotos": sum(r.images for r in rows),
        "monedas": sorted({r.currency for r in rows if r.currency}),
        "legal": legal_note,
        "aplicado": apply,
        **(stats if apply else {}),
    }
    redirects = {r.fygaro_id: r.product_id for r in rows if r.product_id}
    return {"summary": summary, "rows": [r.as_dict() for r in rows], "redirects": redirects}
