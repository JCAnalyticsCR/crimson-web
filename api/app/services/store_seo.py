"""SEO de la vitrina: fragmento <head> que nginx incrusta por SSI (titulo, metas, Open Graph y JSON-LD), el estado
de schema.org de la disponibilidad y la miga de pan. Antes vivia en services/store_showcase.py (O1-01, paso 0);
los nombres viejos se siguen pudiendo importar desde alli."""

from __future__ import annotations

import html
import json
import re


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
        # O1-12: texto alternativo siempre; ancho y alto solo cuando se conocen de verdad (WhatsApp y Facebook miden
        # la foto ellos mismos; un tamano inventado la deforma)
        og["og:image:alt"] = meta.get("image_alt") or meta["title"]
        if meta.get("image_width") and meta.get("image_height"):
            og["og:image:width"], og["og:image:height"] = str(meta["image_width"]), str(meta["image_height"])
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


SCHEMA = "https://schema.org/"
# estados que se pagan en linea (los mismos de services/availability.SELLABLE_STATES)
BUYABLE_STATES = ("en_bodega", "bajo_pedido", "servicio")


def schema_availability(state: str | None, cfg: dict | None = None) -> str:
    """O1-06: la UNICA traduccion de la disponibilidad de la tienda a schema.org (lo que Google muestra).

    en bodega = InStock · bajo pedido = BackOrder · agotado = OutOfStock · servicio = InStock.
    «Por confirmar» (consultar: dato del proveedor viejo o comprometido) = OutOfStock mientras lo unico que se ofrece
    es escribir por WhatsApp; pasa a BackOrder solo si la tienda deja «Solicitar sin pagar» (P-03,
    out_of_stock_action = "solicitar"). Antes era BackOrder siempre: Google prometia un pedido que no se podia hacer.
    cfg: los ajustes de inventario de la tienda (services/availability.settings_of)."""
    if state in ("en_bodega", "disponible", "ultimas", "servicio"):
        return SCHEMA + "InStock"
    if state == "bajo_pedido":
        return SCHEMA + "BackOrder"
    if state == "consultar" and (cfg or {}).get("out_of_stock_action") == "solicitar":
        return SCHEMA + "BackOrder"
    return SCHEMA + "OutOfStock"


def availability_schema(state: str, ultimas: bool | None = False) -> str:
    """Compatibilidad (nombre viejo): la regla vive en schema_availability."""
    return schema_availability(state)


_DAYS = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?"
_HOURS_ONE = rf"{_DAYS}(?:,{_DAYS})* \d{{2}}:\d{{2}}-\d{{2}}:\d{{2}}"
_HOURS_SCHEMA = re.compile(rf"^{_HOURS_ONE}(?:\s*;\s*{_HOURS_ONE})*$")


def organization_ld(base: str, name: str, *, logo: str | None = None, whatsapp: str | None = None, business: dict | None = None) -> dict:
    """Organization del JSON-LD con los datos del comercio (O1-17, Mi Tienda -> Datos del comercio) cuando existen:
    razon social, cedula juridica, direccion, telefono, correo y horario. Nada vacio ni inventado.
    openingHours solo si el horario ya viene en el formato de schema.org («Mo-Fr 08:00-17:00»): un texto libre
    («Lunes a viernes de 8 a 5») no se publica ahi (seria un dato invalido para Google)."""
    b = business or {}
    phone = re.sub(r"\D", "", b.get("phone") or "") or re.sub(r"\D", "", whatsapp or "")
    org: dict = {"@type": "Organization", "@id": base + "/#org", "name": name, "url": base + "/"}
    if logo and str(logo).startswith("http"):
        org["logo"] = logo
    if b.get("legal_name"):
        org["legalName"] = b["legal_name"]
    if b.get("legal_id"):
        org["taxID"] = b["legal_id"]
    if phone:
        org["telephone"] = "+" + (phone if len(phone) > 8 else "506" + phone)
    if b.get("email"):
        org["email"] = b["email"]
    org["address"] = {"@type": "PostalAddress", **({"streetAddress": b["address"]} if b.get("address") else {}), "addressCountry": "CR"}
    hours = (b.get("hours") or "").strip()
    if hours and _HOURS_SCHEMA.match(hours):
        org["openingHours"] = [h.strip() for h in hours.split(";") if h.strip()]
    return org


def breadcrumb_ld(base: str, items: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": name, "item": base + path} for i, (name, path) in enumerate(items)],
    }
