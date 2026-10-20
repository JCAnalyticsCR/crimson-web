"""Snapshot del catalogo publico para la DEMOSTRACION estatica de la tienda (docs/demo-tienda/).

Descarga el catalogo real de la tienda propia (GET /public/store/crimson/products y /public/store/crimson)
y escribe portal/src/demo/snapshot.json, que el build de la demo empaqueta.

- Fuente: tienda propia en produccion (api-production-f07a.up.railway.app). Son datos publicos sin costo.
- Excluye productos sin precio.
- Precios: los productos vienen en CRC sin IVA (campo `price`). Se guardan en `crc`; el campo `usd` se
  conserva como crc / FX solo por compatibilidad con el campo `fx`. net() en catalog.ts usa `crc`.
- Imagenes: URLs publicas de eurocompcr.com tal como las devuelve la API.
- Disponibilidad REAL: 'b' bajo pedido, 'a' agotado, 'd' en bodega (del campo availability.state).
- Categorias: IDs reales del arbol de la tienda (no se reasignan IDs).
- `mod`: ID del producto cero-rellenado (ordena 'recientes' de forma estable, mayor id = mas reciente).
- Textos legales: de la tienda real si vienen; si estan vacios, de Fygaro de respaldo.
- Copia cruda fechada en infra/tienda/catalogo-tienda-AAAA-MM-DD.json (reproducibilidad sin red).

Uso (desde la raiz del repo):
  python portal/scripts/demo_snapshot.py                          # descarga y genera
  python portal/scripts/demo_snapshot.py --from-file <path>      # lee archivo local (sin red)
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "infra" / "tienda"
OUT = ROOT / "portal" / "src" / "demo" / "snapshot.json"
GEO = ROOT / "api" / "app" / "services" / "cr_geo.py"

API_BASE = "https://api-production-f07a.up.railway.app"
STORE_SLUG = "crimson"

FX = 512          # tipo de cambio fijo de REFERENCIA (la demo no convierte monedas en vivo)
IVA = 13          # tasa general Costa Rica; la API ya trae tax_rate por producto


# Textos legales de respaldo (Fygaro); se usan si la tienda real no tiene los suyos.
_LEGAL_FALLBACK = {
    "terms": (
        "Condiciones de Uso\nAl acceder o usar este sitio web, aceptás las presentes condiciones. "
        "Crimson CR se reserva el derecho de modificarlas en cualquier momento.\n\n"
        "Propiedad intelectual\nTodo el contenido de este sitio — textos, imágenes, logotipos y código — "
        "es propiedad de Crimson CR o de sus proveedores y está protegido por las leyes de propiedad "
        "intelectual de Costa Rica.\n\n"
        "Limitación de responsabilidad\nCrimson CR no se responsabiliza por daños directos o indirectos "
        "derivados del uso del sitio ni de los productos adquiridos, salvo lo que establezca la garantía "
        "de fábrica del fabricante."
    ),
    "privacy": (
        "Política de Privacidad\nCrimson CR recopila únicamente los datos necesarios para procesar tus "
        "pedidos: nombre, correo electrónico, dirección de entrega y método de pago. No vendemos ni "
        "compartimos tus datos con terceros salvo los estrictamente necesarios para el despacho (empresa "
        "de mensajería) o para el cumplimiento legal.\n\n"
        "Cookies\nUsamos cookies de sesión para el carrito de compras. No usamos cookies de rastreo de "
        "terceros ni publicidad comportamental.\n\n"
        "Contacto\nPara ejercer tus derechos de acceso, rectificación o cancelación, escribinos a "
        "info@crimsoncr.com."
    ),
}

AVAIL_MAP = {
    "bajo_pedido": "b",
    "agotado": "a",
    "en_bodega": "d",
    "disponible": "d",   # por si la tienda agrega este estado en el futuro
}


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def load_raw(from_file: str | None) -> tuple[list[dict], dict]:
    """Devuelve (products, store_info). Descarga de la red o lee el archivo local."""
    if from_file:
        p = Path(from_file)
        d = json.loads(p.read_text(encoding="utf-8"))
        prods = d["prods"]
        store = d["store"]
        print(f"Leyendo desde {p} ({len(prods)} productos)")
    else:
        print("Descargando catálogo de la tienda real...")
        prods = json.loads(_fetch(f"{API_BASE}/public/store/{STORE_SLUG}/products"))
        store = json.loads(_fetch(f"{API_BASE}/public/store/{STORE_SLUG}"))
        today = str(date.today())
        raw_path = RAW_DIR / f"catalogo-tienda-{today}.json"
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps({"prods": prods, "store": store, "fetched": today}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(f"Copia cruda guardada: {raw_path.relative_to(ROOT)}")
    return prods, store


def build_categories(store_cats: list[dict]) -> tuple[list[dict], dict[int, int]]:
    """Convierte el arbol de la API en lista plana y devuelve el mapa cat_id -> parent_id."""
    cats: list[dict] = []
    for c in store_cats:
        cats.append({"id": c["id"], "name": c["name"], "parent": None})
        for s in c.get("subcategories") or []:
            cats.append({"id": s["id"], "name": s["name"], "parent": c["id"]})
    by_parent = {c["id"]: (c["parent"] if c["parent"] is not None else None) for c in cats}
    return cats, by_parent


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera snapshot.json de la demo")
    parser.add_argument("--from-file", metavar="PATH", help="Lee el JSON crudo en lugar de descargar")
    ns = parser.parse_args(args)

    prods_raw, store_raw = load_raw(ns.from_file)

    # ---------------------------------------------------------------- fecha de extraccion
    if ns.from_file:
        p = Path(ns.from_file)
        try:
            extracted_at = json.loads(p.read_text(encoding="utf-8")).get("fetched", str(date.today()))
        except Exception:
            extracted_at = str(date.today())
    else:
        extracted_at = str(date.today())

    # ---------------------------------------------------------------- geografía
    try:
        import runpy
        geo = runpy.run_path(str(GEO))
        provincias = geo["PROVINCIAS"]
        outside_gam = {"provinces": sorted(geo["PROVINCIAS_FUERA_GAM"]), "cantons": sorted(geo["CANTONES_FUERA_GAM"])}
    except Exception as e:
        print(f"Advertencia: no se pudo cargar cr_geo.py ({e}); se usan valores vacíos")
        provincias = {}
        outside_gam = {"provinces": [], "cantons": []}

    # ---------------------------------------------------------------- categorias
    store_cats = store_raw.get("categories") or []
    cats, _ = build_categories(store_cats)

    # ---------------------------------------------------------------- productos
    prods_out: list[dict] = []
    for p in prods_raw:
        crc = float(p.get("price") or 0)
        if crc <= 0:
            continue  # sin precio: fuera de la demo
        avail_state = (p.get("availability") or {}).get("state", "bajo_pedido")
        avail = AVAIL_MAP.get(avail_state, "b")
        rate = float(p.get("tax_rate") or IVA)
        brand = p.get("brand") or None
        cat_id = p.get("category_id") or None
        images = [u for u in (p.get("images") or []) if isinstance(u, str) and u.startswith("https://")]
        code = (p.get("code") or "").strip() or f"P{p['id']}"
        desc = (p.get("description") or "").strip() or None
        prods_out.append({
            "id": p["id"],
            "code": code,
            "name": p["name"].strip(),
            "brand": brand,
            "desc": desc,
            "crc": round(crc),             # CRC sin IVA (fuente principal de precio)
            "usd": round(crc / FX, 2),     # solo para compatibilidad con fx; net() usa crc
            "rate": int(round(rate)),
            "images": images,
            "cat": cat_id,
            "mod": str(p["id"]).zfill(6),  # orden estable: mayor id = mas reciente
            "avail": avail,
        })

    # ---------------------------------------------------------------- textos legales
    store_legal = (store_raw.get("store") or {}).get("legal") or {}
    legal: dict[str, str] = {}
    for k in ("terms", "privacy"):
        v = store_legal.get(k, "").strip()
        legal[k] = v if v else _LEGAL_FALLBACK.get(k, "")

    # ---------------------------------------------------------------- salida
    out = {
        "source": f"{API_BASE}/public/store/{STORE_SLUG}/products",
        "extracted_at": extracted_at,
        "fx": FX,
        "legal": legal,
        "provinces": provincias,
        "outside_gam": outside_gam,
        "categories": cats,
        "products": prods_out,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    sin_imagen = sum(1 for p in prods_out if not p["images"])
    sin_marca = sum(1 for p in prods_out if not p["brand"])
    avail_counts: dict[str, int] = {}
    for p in prods_out:
        avail_counts[p["avail"]] = avail_counts.get(p["avail"], 0) + 1
    excluidos = len(prods_raw) - len(prods_out)
    print(
        f"{len(prods_out)} productos ({excluidos} sin precio excluidos): "
        + ", ".join(f"{v} {k}" for k, v in sorted(avail_counts.items()))
        + f"  |  {sin_imagen} sin imagen  |  {sin_marca} sin marca"
        + f"  ->  {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)"
    )
    print(f"Fecha de extracción: {extracted_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
