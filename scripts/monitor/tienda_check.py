"""Monitor sintetico de la tienda (plan 1.8). Solo GET y solo biblioteca estandar (corre en GitHub Actions sin instalar).

Revisa lo que el monitor de palabra clave no ve:
- Una muestra rotativa del sitemap (cambia cada 30 min; en unas horas pasa por todo el catalogo) mas la portada y
  /catalogo: estado 200, sin el fragmento SSI comprimido pegado en crudo (bytes 1f 8b), con <title> y con el
  <script src="/assets/..."> de la app (sin el, la pagina queda en blanco).
- Una ruta inexistente responde 404 de verdad (no un 200 blando).
- checkout-config: ningun metodo de pago "simulated" (llave de pruebas o pago simulado en produccion).
- Opcional (--deep URL): /health/deep responde 200.

Uso:  python scripts/monitor/tienda_check.py [--base https://tienda.ejemplo.com] [--slug tienda] [--sample 12]
      [--deep https://panel.ejemplo.com/api/health/deep]
Variables equivalentes: TIENDA_URL, TIENDA_SLUG, MONITOR_SAMPLE, HEALTH_DEEP_URL. Sale con 1 si algo falla.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib

UA = "Mozilla/5.0 (monitor tienda; +plan 1.8)"
TITLE = re.compile(rb"<title[^>]*>\s*[^<\s][^<]*</title>", re.I)
APP_SCRIPT = re.compile(rb"<script[^>]+src=[\"']/assets/", re.I)
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def get(url: str, timeout: float = 25.0) -> tuple[int, bytes, dict]:
    """(estado, cuerpo descomprimido, cabeceras con el nombre en minuscula). Pide gzip como un navegador; los errores
    HTTP tambien se devuelven. Los nombres de cabecera no distinguen mayusculas (un proxy puede mandar
    `content-encoding`): se pasan a minuscula antes de buscarlos, si no el cuerpo quedaria comprimido."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate", "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (URL de configuracion, https)
            status, raw, items = r.status, r.read(), r.headers.items()
    except urllib.error.HTTPError as e:
        status, raw, items = e.code, e.read(), (e.headers.items() if e.headers else [])
    headers = {k.lower(): v for k, v in items}
    enc = (headers.get("content-encoding") or "").strip().lower()
    if enc in ("gzip", "x-gzip"):
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw)
    return status, raw, headers


def check_page(url: str) -> list[str]:
    status, body, _ = get(url)
    problems = []
    if status != 200:
        problems.append(f"estado {status}")
    if b"\x1f\x8b" in body:
        problems.append("fragmento SSI comprimido pegado en crudo (1f8b)")
    if not TITLE.search(body):
        problems.append("sin <title>")
    if not APP_SCRIPT.search(body):
        problems.append('sin <script src="/assets/..."> (pagina en blanco)')
    return problems


def sample(locs: list[str], n: int, now: float | None = None) -> list[str]:
    """Ventana rotativa: cada media hora toma las siguientes n URLs del sitemap."""
    if not locs or n <= 0:
        return []
    if len(locs) <= n:
        return list(locs)
    slot = int((time.time() if now is None else now) // 1800)
    start = (slot * n) % len(locs)
    return [locs[(start + i) % len(locs)] for i in range(n)]


def run(base: str, slug: str, n: int, deep: str | None) -> list[str]:
    base = base.rstrip("/")
    fails: list[str] = []
    status, body, _ = get(f"{base}/sitemap.xml")
    locs = LOC.findall(body.decode("utf-8", "replace")) if status == 200 else []
    if status != 200 or not locs:
        fails.append(f"sitemap.xml: estado {status}, {len(locs)} URL")
    urls = list(dict.fromkeys([f"{base}/", f"{base}/catalogo", *sample(locs, n)]))
    for u in urls:
        try:
            p = check_page(u)
        except Exception as e:  # noqa: BLE001  red o TLS: cuenta como falla de esa URL
            p = [f"{type(e).__name__}: {e}"]
        if p:
            fails.append(f"{u}: {'; '.join(p)}")
        time.sleep(0.2)
    missing = f"{base}/no-existe-monitor-{int(time.time())}"
    try:
        st, body, _ = get(missing)
        if st != 404:
            fails.append(f"ruta inexistente: estado {st} (se esperaba 404)")
    except Exception as e:  # noqa: BLE001
        fails.append(f"ruta inexistente: {type(e).__name__}")
    try:
        st, body, _ = get(f"{base}/api/public/store/{slug}/checkout-config")
        cfg = json.loads(body) if st == 200 else {}
        if st != 200:
            fails.append(f"checkout-config: estado {st}")
        sim = [m.get("code") for m in cfg.get("methods") or [] if m.get("simulated")]
        if sim:
            fails.append(f"checkout-config: metodos simulados {sim}")
    except Exception as e:  # noqa: BLE001
        fails.append(f"checkout-config: {type(e).__name__}: {e}")
    if deep:
        try:
            st, body, _ = get(deep)
            if st != 200:
                estado = (json.loads(body) or {}).get("status") if body.strip().startswith(b"{") else None
                fails.append(f"health/deep: estado {st}{f' ({estado})' if estado else ''}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"health/deep: {type(e).__name__}")
    print(f"revisadas={len(urls)} sitemap={len(locs)} fallas={len(fails)}")
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=os.getenv("TIENDA_URL", "https://tienda.crimsoncr.com"))
    ap.add_argument("--slug", default=os.getenv("TIENDA_SLUG", "crimson"))
    ap.add_argument("--sample", type=int, default=int(os.getenv("MONITOR_SAMPLE", "12")))
    ap.add_argument("--deep", default=os.getenv("HEALTH_DEEP_URL") or None)
    a = ap.parse_args(argv)
    fails = run(a.base, a.slug, a.sample, a.deep)
    for f in fails:
        print(f"FALLA {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
