"""Snapshot del catalogo publico para la DEMOSTRACION estatica de la tienda (docs/demo-tienda/).

Lee el catalogo publico exportado de Fygaro (infra/fygaro/catalogo-fygaro-*.json) y la division territorial
(api/app/services/cr_geo.py) y escribe portal/src/demo/snapshot.json, que el build de la demo empaqueta.

- Excluye productos sin precio.
- Precios: se guardan en USD sin IVA (como vienen); la demo los exhibe en colones con IVA usando un tipo de
  cambio FIJO de referencia (FX abajo). No es el tipo de cambio del dia.
- Imagenes: solo las URLs publicas originales de Fygaro (no se descarga nada).
- Disponibilidad: SIMULADA y deterministica (la mayoria "bajo pedido", algunas "disponible"); sin cantidades.
- Marca: deducida del nombre/SKU con reglas simples (el catalogo de Fygaro no la trae en un campo).

Uso (desde la raiz del repo):  python portal/scripts/demo_snapshot.py
"""

from __future__ import annotations

import json
import re
import runpy
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "infra" / "fygaro" / "catalogo-fygaro-2026-10-12.json"
GEO = ROOT / "api" / "app" / "services" / "cr_geo.py"
OUT = ROOT / "portal" / "src" / "demo" / "snapshot.json"
FX = 512  # colones por dolar, REFERENCIA fija para la demostracion
IVA = 13  # tasa general de Costa Rica (Fygaro trae tasas recalculadas con decimales: 13.2, 12.8...)

BRANDS: list[tuple[str, str]] = [
    ("Ezviz", r"ezviz|\bcs-|^cs[a-z0-9]"),
    ("Axis", r"\baxis\b"),
    ("Hikvision", r"hikvision|\bhik\b|hilook|acusense|colorvu|\bds-|^ds|\bids-?|^ids|^crds|^crae|^ae-|\bae-|^k1t|easylink|ax pro"),
    ("Ubiquiti", r"ubiquiti|unifi|ubiwifi|\bubi\b|\bui\b|^u[a-z0-9]{1,4}[-]|^usw|^uvc|^u6|^u7|^ucg|^ck-|^uf-|^udb|^umr|^urack|cloudkey|^cru"),
    ("Teklink-T", r"teklink"),
    ("APC", r"\bapc\b|back-ups"),
    ("CDP", r"\bcdp\b"),
    ("Nova", r"\bnova\b"),
    ("Kingston", r"kingston"),
    ("Western Digital", r"western digital|\bwdi?\b|purple|^crwd"),
    ("ADATA", r"adata"),
    ("Iflux", r"iflux|^fd-"),
    ("Seagate", r"seagate|skyhawk"),
    ("Lantek", r"lantek|^ltk"),
    ("Panduit", r"panduit|mini-?com|pan-net"),
    ("Sprotek", r"sprotek"),
    ("Smartbitt", r"smartbitt"),
    ("Xiaomi", r"xiaomi"),
    ("TP-Link", r"tp-link|tplink"),
    ("ZKTeco", r"zkteco"),
    ("Dahua", r"dahua"),
    ("Mikrotik", r"mikrotik"),
    ("Epcom", r"epcom"),
    ("AOC", r"aoc"),
    ("HP", r"hp"),
    ("Futronic", r"futronic"),
]


def brand_of(name: str, sku: str) -> str | None:
    t = f"{name} {sku}".lower()
    s = sku.lower().strip()
    for b, rx in BRANDS:
        if re.search(rx, t) or re.search(rx, s):
            return b
    return None


def main() -> int:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    geo = runpy.run_path(str(GEO))

    # categorias: madres 1..N, subcategorias 101.. (las vacias tambien: la demo solo muestra las que tienen productos)
    cats: list[dict] = []
    by_name: dict[tuple[str, str | None], int] = {}
    nid = 1
    sid = 101
    for c in d["categories"]:
        cats.append({"id": nid, "name": c["name"], "parent": None})
        by_name[(c["name"], None)] = nid
        for s in c.get("subcategories") or []:
            cats.append({"id": sid, "name": s["name"], "parent": nid})
            by_name[(c["name"], s["name"])] = sid
            sid += 1
        nid += 1

    prods = []
    for i, p in enumerate(d["products"], start=1):
        usd = float(p.get("price") or 0)
        if usd <= 0:
            continue  # sin precio: fuera de la demo
        cat = None
        if p.get("categories"):
            parent = p["categories"][0]
            sub = (p.get("subcategories") or [None])[0]
            cat = by_name.get((parent, sub)) or by_name.get((parent, None))
        sku = (p.get("sku") or "").strip()
        avail = "d" if zlib.crc32(sku.encode()) % 10 < 3 else "b"  # ~30 % "Disponible", el resto "Bajo pedido"
        prods.append(
            {
                "id": i,
                "code": sku or f"P{i}",
                "name": re.sub(r"\s+", " ", p["name"]).strip(),
                "brand": brand_of(p["name"], sku),
                "desc": (p.get("description") or "").strip() or None,
                "usd": round(usd, 2),
                "rate": IVA,
                "images": [u for u in p.get("images") or [] if u.startswith("https://")],
                "cat": cat,
                "mod": p.get("lastmod") or "",
                "avail": avail,
            }
        )

    out = {
        "source": SRC.relative_to(ROOT).as_posix(),
        "extracted_at": d.get("extracted_at"),
        "fx": FX,
        "legal": d.get("legal") or {},
        "provinces": geo["PROVINCIAS"],
        "outside_gam": {"provinces": sorted(geo["PROVINCIAS_FUERA_GAM"]), "cantons": sorted(geo["CANTONES_FUERA_GAM"])},
        "categories": cats,
        "products": prods,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    sin_marca = sum(1 for p in prods if not p["brand"])
    print(f"{len(prods)} productos ({len(d['products']) - len(prods)} sin precio excluidos), {sin_marca} sin marca -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
