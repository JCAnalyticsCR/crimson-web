"""SEO de la vitrina: fragmento <head> que nginx incrusta por SSI (titulo, metas, Open Graph y JSON-LD), el estado
de schema.org de la disponibilidad y la miga de pan. Antes vivia en services/store_showcase.py (O1-01, paso 0);
los nombres viejos se siguen pudiendo importar desde alli."""

from __future__ import annotations

import html
import json


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


def ld_json(obj) -> str:
    """JSON-LD seguro dentro de <script>: sin '<' ni '>' literales ni separadores de linea raros."""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def head_html(meta: dict) -> str:
    """Fragmento <head> que nginx incrusta por SSI (y que el SPA actualiza al navegar). Atributo data-sh para que
    el cliente reemplace estas etiquetas en vez de duplicarlas."""
    parts = [f"<title>{esc(meta['title'])}</title>"]
    parts.append(f'<meta data-sh name="description" content="{esc(str(meta.get("description") or "")[:400])}">')
    parts.append(f'<meta data-sh name="robots" content="{esc(meta.get("robots", "index,follow"))}">')
    if meta.get("canonical"):
        parts.append(f'<link data-sh rel="canonical" href="{esc(meta["canonical"])}">')
    og = {
        "og:type": meta.get("og_type", "website"),
        "og:title": meta["title"],
        "og:description": meta.get("description", ""),
        "og:url": meta.get("canonical", ""),
        "og:site_name": meta.get("site_name", ""),
        "og:locale": "es_CR",
    }
    if meta.get("image"):
        og["og:image"] = meta["image"]
    for k, v in og.items():
        if v:
            parts.append(f'<meta data-sh property="{k}" content="{esc(str(v)[:400])}">')
    parts.append(f'<meta data-sh name="twitter:card" content="{"summary_large_image" if meta.get("image") else "summary"}">')
    if meta.get("price") is not None:
        parts.append(f'<meta data-sh property="product:price:amount" content="{esc(meta["price"])}">')
        parts.append(f'<meta data-sh property="product:price:currency" content="{esc(meta.get("currency", "CRC"))}">')
    if meta.get("ld"):
        parts.append(f'<script data-sh type="application/ld+json" id="sh-ld">{ld_json(meta["ld"])}</script>')
    return "\n".join(parts)


def availability_schema(state: str, ultimas: bool | None = False) -> str:
    """Estado de services/availability -> schema.org (en_bodega con pocas unidades = LimitedAvailability)."""
    if state in ("en_bodega", "disponible", "ultimas"):
        return "https://schema.org/LimitedAvailability" if (ultimas or state == "ultimas") else "https://schema.org/InStock"
    return {
        "bajo_pedido": "https://schema.org/BackOrder",
        "agotado": "https://schema.org/OutOfStock",
        "servicio": "https://schema.org/InStock",
    }.get(state, "https://schema.org/BackOrder")  # "consultar": se trae del distribuidor


def breadcrumb_ld(base: str, items: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": name, "item": base + path} for i, (name, path) in enumerate(items)],
    }
