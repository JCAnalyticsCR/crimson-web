"""Relevamiento del catalogo PUBLICO de la tienda en Fygaro -> JSON para app.tools.import_fygaro.

    uv run python -m app.tools.fygaro_crawl ./fygaro                # catalogo -> ./fygaro/fygaro_catalog.json
    uv run python -m app.tools.fygaro_crawl ./fygaro --images       # ademas baja las fotos a ./fygaro/images

Solo lee paginas publicas y respeta robots.txt (Fygaro prohibe /cart y /checkout: no se tocan). Pausa entre
pedidos (1.5 s por defecto) y guarda el HTML crudo en ./fygaro/raw, asi una segunda corrida no vuelve a pedir
nada (borrar raw/ para refrescar precios). La carpeta de salida NO va a git (fotos y HTML de terceros).

De cada producto sale: id de Fygaro, SKU, nombre, descripcion, precio sin IVA (offers.price del JSON-LD) y con IVA
(el exhibido), moneda, IVA deducido, categoria (de los listados por categoria), fotos, variantes y disponibilidad.
Tambien los textos de terminos y privacidad de la tienda.
"""

from __future__ import annotations

import argparse
import html as H
import json
import re
import sys
import time
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

BASE = "https://tienda.crimsoncr.com"
UA = "CrimsonCatalogAudit/1.0 (+https://crimsoncr.com)"
IMAGE_HOST = "fygaro-subscribers.s3.amazonaws.com"


class Crawler:
    def __init__(self, out: Path, base: str, pause: float):
        self.out, self.base, self.pause = out, base.rstrip("/"), pause
        self.raw = out / "raw"
        for d in ("products", "categories", "pages"):
            (self.raw / d).mkdir(parents=True, exist_ok=True)
        self.robots = urllib.robotparser.RobotFileParser(f"{self.base}/robots.txt")
        self.robots.read()

    def get(self, url: str, cache: Path) -> str:
        if cache.exists():
            return cache.read_text(encoding="utf-8")
        if not self.robots.can_fetch(UA, url):
            raise RuntimeError(f"robots.txt no permite {url}")
        time.sleep(self.pause)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-CR,es;q=0.9"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", "replace")
                cache.write_text(body, encoding="utf-8")
                return body
            except OSError as e:
                print("  reintento", url, e, flush=True)
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"no se pudo leer {url}")


def text(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|li|div|h\d)>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = H.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def money(s: str) -> float | None:
    m = re.search(r"([\d.,]+)", s or "")
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except ValueError:
        return None


def parse_product(pid: str, s: str, base: str) -> dict:
    ld: dict = {}
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', s, re.S)
    if m:
        try:
            ld = json.loads(m.group(1))
        except json.JSONDecodeError:
            ld = {}
    title = re.search(r'<h1 class="typewr-detail-title">(.*?)</h1>', s, re.S)
    shown = re.search(r'<div class="typewr-detail-price">(.*?)</div>', s, re.S)
    desc = re.search(r'<div class="typewr-description-content">(.*?)</div>\s*</div>\s*</div>\s*</div>\s*</section>', s, re.S)
    imgs = list(dict.fromkeys(re.findall(r'class="typewr-image-data" data-image="([^"]+)"', s))) or list(dict.fromkeys(x for x in (ld.get("image") or []) if x))
    offers = ld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    net = offers.get("price")
    gross = money(text(shown.group(1))) if shown else None
    rate = round((gross / float(net) - 1) * 100, 1) if net and gross else None
    form = re.search(r'<form class="typewr-detail-form".*?</form>', s, re.S)
    variants = []
    if form:
        for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form.group(0), re.S):
            opts = [{"value": v, "label": text(t)} for v, t in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', body, re.S) if v]
            variants.append({"field": name, "options": opts})
    avail = str(offers.get("availability") or ld.get("availability") or "")
    sold_out = "OutOfStock" in avail or bool(re.search(r"\bagotad[oa]\b", s, re.I))
    return {
        "fygaro_id": pid,
        "url": f"{base}/products/{pid}/",
        "sku": (ld.get("sku") or "").strip() or None,
        "name": text(title.group(1)) if title else ld.get("name"),
        "description": text(desc.group(1)) if desc else (ld.get("description") or ""),
        "price": float(net) if net not in (None, "") else None,
        "price_with_tax": gross,
        "currency": offers.get("priceCurrency") or None,
        "tax_rate": rate,
        "images": imgs,
        "variants": variants,
        "availability": "agotado" if sold_out else ("disponible" if "InStock" in avail else None),
    }


def crawl(c: Crawler) -> dict:
    sitemap = c.get(f"{c.base}/sitemap.xml", c.raw / "sitemap.xml")
    cat_ids = re.findall(r"/products/category/(\d+)/", sitemap)
    prod_ids = list(dict.fromkeys(re.findall(r"/products/([0-9a-f-]{36})/", sitemap)))
    lastmod = dict(re.findall(r"/products/([0-9a-f-]{36})/</loc>\s*<priority>[^<]*</priority>\s*<lastmod>([^<]+)</lastmod>", sitemap))
    print(f"sitemap: {len(cat_ids)} categorias, {len(prod_ids)} productos", flush=True)

    categories, prod_cat = [], {}
    for cid in cat_ids:
        page, name = 1, None
        while True:
            s = c.get(f"{c.base}/products/category/{cid}/?page={page}", c.raw / "categories" / f"{cid}_{page}.html")
            if name is None:
                m = re.search(rf'category={cid}&amp;subcategory=[^"]*"\s*aria-current="page">([^<]+)</a>', s) or re.search(r"<title>(.*?) products</title>", s)
                name = H.unescape(m.group(1)).strip() if m else f"Categoria {cid}"
            ids = list(dict.fromkeys(re.findall(r'href="/products/([0-9a-f-]{36})/"', s)))
            for pid in ids:
                prod_cat.setdefault(pid, [])
                if name not in prod_cat[pid]:
                    prod_cat[pid].append(name)
            total = re.search(r"P\S+gina \d+ de (\d+)", s)
            if page >= (int(total.group(1)) if total else 1) or not ids:
                break
            page += 1
        categories.append({"fygaro_id": cid, "name": name})
        print(f"  {name}: {page} pagina(s)", flush=True)

    products = []
    for i, pid in enumerate(prod_ids, 1):
        p = parse_product(pid, c.get(f"{c.base}/products/{pid}/", c.raw / "products" / f"{pid}.html"), c.base)
        p["categories"] = prod_cat.get(pid, [])
        p["lastmod"] = lastmod.get(pid)
        products.append(p)
        if i % 50 == 0:
            print(f"  productos {i}/{len(prod_ids)}", flush=True)

    legal = {}
    for key, path in (("terms", "terms/"), ("privacy", "policies/")):
        s = c.get(f"{c.base}/{path}", c.raw / "pages" / f"{key}.html")
        m = re.search(r"<main.*?</main>", s, re.S)
        # el titulo viene repetido (encabezado de la pagina + del documento): fuera todas las copias del inicio
        legal[key] = re.sub(r"^(\s*(T\S+rminos y Condiciones|Pol\S+ticas? de Privacidad)\s*\n)+", "", text(m.group(0)) if m else "").strip()

    return {"source": c.base, "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "legal": legal, "categories": categories, "products": products}


def download_images(catalog: dict, out: Path, pause: float) -> None:
    folder = out / "images"
    folder.mkdir(exist_ok=True)
    urls = list(dict.fromkeys(u for p in catalog["products"] for u in p["images"]))
    got = fail = 0
    for u in urls:
        f = folder / Path(urlparse(u).path).name
        if f.exists() or urlparse(u).hostname != IMAGE_HOST:
            continue
        time.sleep(pause)
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30) as r:
                f.write_bytes(r.read())
            got += 1
        except OSError as e:
            fail += 1
            print("  foto fallida", u, e, flush=True)
    print(f"fotos: {len(urls)} en el catalogo, {got} descargadas ahora, {fail} fallidas -> {folder}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.fygaro_crawl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, help="carpeta de salida (fuera de git)")
    ap.add_argument("--base", default=BASE, help=f"tienda Fygaro (por defecto {BASE})")
    ap.add_argument("--pause", type=float, default=1.5, help="segundos entre pedidos a Fygaro")
    ap.add_argument("--images", action="store_true", help="descargar tambien las fotos a <out>/images")
    args = ap.parse_args(argv)
    if args.pause < 0.5:
        sys.exit("Pausa minima 0.5 s: no cargar el servidor de Fygaro")
    args.out.mkdir(parents=True, exist_ok=True)
    catalog = crawl(Crawler(args.out, args.base, args.pause))
    dest = args.out / "fygaro_catalog.json"
    dest.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"listo: {len(catalog['products'])} productos -> {dest}", flush=True)
    if args.images:
        download_images(catalog, args.out, max(0.3, args.pause / 4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
