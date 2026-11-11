"""O2-01b: contenido real de la vitrina dentro del HTML (cuerpo por SSI), para quien no ejecuta JavaScript y para que
la primera pintura ya diga algo. nginx lo incrusta en index.html (solo en el dominio de la tienda) y React lo reemplaza
al montar (portal/src/modules/store/ssrBody.ts). Es el MISMO contenido que ve la persona, con los mismos datos que usa
la vitrina (snapshot, /showcase y /item): no es encubrimiento.

Apagado por defecto: settings.store.ssr_body = {"ficha": bool, "categoria": bool, "portada": bool}. Se enciende un tipo
de ruta a la vez, midiendo CLS y LCP en un celular lento.

Nada interactivo que dependa de JavaScript: solo texto y enlaces reales (fichas, categorias, paginas, WhatsApp y
telefono). Todo texto pasa por esc(); nada de lo que escribe el comercio o trae el catalogo puede abrir etiquetas."""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote, urlencode

from .store_seo import esc

# tipos de ruta que se pueden encender por separado
TIPOS = ("ficha", "categoria", "portada")
POR_PAGINA = 24  # el mismo de CategoryPage.tsx (PAGE) y de seo_meta
SIMBOLO = {"CRC": "₡", "USD": "$", "EUR": "€"}
TITULAR = "Equipo profesional de seguridad, listo para instalar."  # el de StoreHome.tsx sin titular propio
BAJADA = "Cámaras, grabadores, redes y control de acceso con precio final, factura electrónica y técnicos locales que responden."


def flags(cfg: dict) -> dict[str, bool]:
    raw = cfg.get("ssr_body") or {}
    return {k: bool(raw.get(k)) if isinstance(raw, dict) else False for k in TIPOS}


def tipo_de(path: str) -> str | None:
    """Tipo de ruta (ficha, categoria o portada) o None si esa ruta no lleva cuerpo."""
    parts = [x for x in (path or "").strip("/").split("/") if x]
    if not parts:
        return "portada"
    if parts[0] == "catalogo" and len(parts) == 1:
        return "categoria"
    if parts[0] == "c" and len(parts) == 2:
        return "categoria"
    if parts[0] == "p" and len(parts) == 2:
        return "ficha"
    return None


def money(value: object, currency: str = "CRC") -> str:
    """El precio como lo escribe la vitrina (Intl.NumberFormat es-CR, narrowSymbol): ₡12 345, $1 234,50. Separador de
    miles: espacio duro, desde los 4 digitos; redondeo hacia arriba en el medio; colones sin decimales."""
    cur = (currency or "CRC").upper()
    dec = 0 if cur == "CRC" else 2
    d = Decimal(str(value or 0)).quantize(Decimal(1) if dec == 0 else Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = d < 0
    entero, _, frac = f"{abs(d):.{dec}f}".partition(".")
    grupos = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)
    num = " ".join(grupos) + (f",{frac}" if dec else "")
    simbolo = SIMBOLO.get(cur)
    return ("-" if neg else "") + (f"{simbolo}{num}" if simbolo else f"{cur} {num}")


def precio(p: dict, currency: str) -> str:
    """Precio con IVA de una tarjeta, o «Precio a consultar» (como PriceTag de la vitrina)."""
    v = p.get("display_price")
    if p.get("price_on_request") or not v or Decimal(str(v)) <= 0:
        return "Precio a consultar"
    return money(v, p.get("display_currency") or currency)


def _producto(p: dict, currency: str) -> str:
    label = ((p.get("availability") or {}).get("label") or "").strip()
    return (
        f'<li data-sh-id="{int(p["id"])}"><a href="{esc(p["path"])}">{esc(p.get("display_name") or p["name"])}</a>'
        f' · <span class="sh-ssr__precio">{esc(precio(p, currency))}</span>' + (f' · <span class="sh-ssr__disp">{esc(label)}</span>' if label else "") + "</li>"
    )


def _lista(prods: list[dict], currency: str) -> str:
    return "<ul>" + "".join(_producto(p, currency) for p in prods) + "</ul>" if prods else ""


def _migas(items: list[tuple[str, str | None]]) -> str:
    li = "".join(f'<li><a href="{esc(path)}">{esc(name)}</a></li>' if path else f'<li aria-current="page">{esc(name)}</li>' for name, path in items)
    return f'<nav aria-label="Migas de pan"><ol>{li}</ol></nav>'


def contacto(cfg: dict, business: dict, texto: str) -> str:
    """WhatsApp y telefono reales del comercio (los mismos del bloque sin JavaScript de O1-06)."""
    wa = re.sub(r"\D", "", cfg.get("whatsapp") or "")
    tel = re.sub(r"\D", "", business.get("phone") or "") or wa
    links = []
    if wa:
        links.append(f'<a href="https://wa.me/{wa}?text={quote(texto, safe="")}">Escribir por WhatsApp</a>')
    if tel:
        tel = tel if len(tel) > 8 else "506" + tel
        links.append(f'<a href="tel:+{tel}">Llamar al {esc(business.get("phone") or "+" + tel)}</a>')
    return f'<p class="sh-ssr__contacto">{" · ".join(links)}</p>' if links else ""


def marco(cfg: dict, business: dict, contenido: str, texto_wa: str) -> str:
    """Cabecera minima (nombre y menu con enlaces reales), el contenido y el contacto. Todo dentro de .shop (la hoja de la
    tienda ya esta cargada: index.html la enlaza antes del bloque)."""
    nombre = esc(cfg["name"])
    nav = '<a href="/catalogo">Catálogo</a> · <a href="/arma-tu-sistema">Armá tu sistema</a> · <a href="/asesoria">Asesoría</a>'
    return (
        '<div class="shop sh-ssr">'
        f'<header class="sh-ssr__head"><div class="sh-wrap"><a href="/"><b>{nombre}</b></a> <nav aria-label="Principal">{nav}</nav></div></header>'
        f'<main class="sh-wrap">{contenido}{contacto(cfg, business, texto_wa)}</main>'
        "</div>"
    )


# ---------- portada ----------
def portada(cfg: dict, business: dict, sc: dict) -> str:
    cur = sc.get("currency") or cfg.get("currency") or "CRC"
    cats = "".join(
        f'<li><a href="{esc(c["path"])}">{esc(c["name"])}</a> ({int(c.get("count") or 0)})</li>' for c in sc.get("categories") or [] if c.get("count")
    )
    kits = []
    for k in sc.get("kits") or []:
        q = {a: (str(b).lower() if isinstance(b, bool) else str(b)) for a, b in (k.get("answers") or {}).items()}
        q["kit"] = str(k.get("id") or "")
        etiqueta = "Precio de referencia" if k.get("payable") is False else "Solo equipo"
        kits.append(
            f'<li><a href="/arma-tu-sistema?{esc(urlencode(q))}">{esc(k.get("title") or "Kit")}</a> · {etiqueta} {esc(money(k.get("total"), cur))}</li>'
        )
    contenido = (
        f"<h1>{esc(sc.get('headline') or TITULAR)}</h1><p>{esc(sc.get('subline') or BAJADA)}</p>"
        '<p><a href="/arma-tu-sistema">Armá tu sistema</a> · <a href="/catalogo">Ver catálogo</a></p>'
        + (f"<h2>Categorías</h2><ul>{cats}</ul>" if cats else "")
        + (f"<h2>Kits listos</h2><ul>{''.join(kits)}</ul>" if kits else "")
        + (f"<h2>Destacados</h2>{_lista(sc.get('featured') or [], cur)}" if sc.get("featured") else "")
    )
    return marco(cfg, business, contenido, f"Hola {cfg['name']}, tengo una consulta.")


# ---------- categoria y catalogo ----------
def _plano(cats: list[dict], parent: int | None = None, out: list[dict] | None = None) -> list[dict]:
    """El snapshot manda las categorias como arbol (children); acepta tambien la forma plana (parent_id)."""
    out = [] if out is None else out
    for c in cats or []:
        out.append({**{k: v for k, v in c.items() if k != "children"}, "parent_id": c.get("parent_id", parent)})
        _plano(c.get("children") or [], c.get("id"), out)
    return out


def _sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def orden_por_defecto(p: dict) -> tuple:
    """El orden «Relevancia» sin busqueda de la vitrina (catalogFilter.ts: disponibilidad, destacado, Hikvision, con
    foto, con precio, nombre)."""
    st = (p.get("availability") or {}).get("state") or "consultar"
    return (
        0 if st == "en_bodega" else 1 if st == "bajo_pedido" else 2,
        not p.get("web_featured"),
        (p.get("brand") or "").lower() != "hikvision",
        not p.get("image"),
        not (float(p.get("display_price") or 0) > 0),
        _sin_tildes(p.get("name") or ""),
    )


def listado(snap: dict, slug: str | None) -> tuple[dict | None, list[dict]]:
    """(categoria, productos en el orden de la vitrina) de /c/<slug>, o de todo el catalogo con slug None. Categoria
    None con slug = no existe."""
    cats = _plano(snap.get("categories") or [])
    prods = snap.get("products") or []
    if slug is None:
        return {"name": "Catálogo completo", "slug": None}, sorted(prods, key=orden_por_defecto)
    cat = next((c for c in cats if c.get("slug") == slug), None)
    if not cat:
        return None, []
    ids, todo = {cat["id"]}, [cat["id"]]
    while todo:
        cur = todo.pop()
        for c in cats:
            if c.get("parent_id") == cur and c["id"] not in ids:
                ids.add(c["id"])
                todo.append(c["id"])
    return cat, sorted((p for p in prods if p.get("category_id") in ids), key=orden_por_defecto)


def categoria(cfg: dict, business: dict, snap: dict, slug: str | None, pagina: int, descripcion: str | None, padre: tuple[str, str] | None) -> str:
    cat, prods = listado(snap, slug)
    if cat is None:
        return ""
    cur = snap.get("currency") or cfg.get("currency") or "CRC"
    base = f"/c/{cat['slug']}" if slug else "/catalogo"
    total = len(prods)
    ultima = max(1, -(-total // POR_PAGINA))
    if pagina > ultima:
        pagina = 1
    trozo = prods[(pagina - 1) * POR_PAGINA : pagina * POR_PAGINA]
    migas = [("Tienda", "/")] + ([padre] if padre else []) + [(cat["name"], None)]
    pag = []
    if pagina > 1:
        pag.append(f'<a rel="prev" href="{base if pagina == 2 else f"{base}?pagina={pagina - 1}"}">Página anterior</a>')
    if pagina < ultima:
        pag.append(f'<a rel="next" href="{base}?pagina={pagina + 1}">Página siguiente</a>')
    contenido = (
        _migas(migas)
        + f"<h1>{esc(cat['name'])}</h1>"
        + (f"<p>{esc(descripcion)}</p>" if descripcion else "")
        + f"<p>{total} producto{'' if total == 1 else 's'}{f' · Página {pagina} de {ultima}' if ultima > 1 else ''}</p>"
        + _lista(trozo, cur)
        + (f'<nav aria-label="Páginas">{" · ".join(pag)}</nav>' if pag else "")
    )
    return marco(cfg, business, contenido, f"Hola {cfg['name']}, estoy buscando {cat['name'].lower()}.")


# ---------- ficha ----------
def ficha(cfg: dict, business: dict, it: dict) -> str:
    cur = it.get("display_currency") or cfg.get("currency") or "CRC"
    migas = [(c["name"], c["path"]) for c in it.get("breadcrumbs") or []] + [(it.get("display_name") or it["name"], None)]
    label = ((it.get("availability") or {}).get("label") or "").strip()
    desc = re.sub(r"\s+", " ", str(it.get("description") or "")).strip()
    v = precio(it, cur)
    contenido = (
        _migas(migas)
        + f'<article data-sh-id="{int(it["id"])}"><h1>{esc(it.get("display_name") or it["name"])}</h1>'
        + f'<p><strong class="sh-ssr__precio">{esc(v)}</strong>{" IVA incluido" if v != "Precio a consultar" else ""}</p>'
        + (f'<p>Disponibilidad: <span class="sh-ssr__disp">{esc(label)}</span></p>' if label else "")
        + (f"<p>{esc(desc[:1200])}</p>" if desc else "")
        + (f'<p><a href="{esc(it["category_path"])}">Ver más en {esc(it.get("category") or "la categoría")}</a></p>' if it.get("category_path") else "")
        + "</article>"
        + (f"<h2>Relacionados</h2>{_lista(it.get('related') or [], cur)}" if it.get("related") else "")
    )
    codigo = f" (código {it['code']})" if it.get("code") else ""
    return marco(cfg, business, contenido, f"Hola {cfg['name']}, me interesa {it['name']}{codigo}.")
