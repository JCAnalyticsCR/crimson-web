"""SEO por dominio de la tienda: lo que nginx pide para el dominio de la vitrina (metas por ruta via SSI, robots.txt,
sitemap.xml, existencia de fichas para el 404 real y redirecciones 301 de las direcciones viejas de Fygaro).

Antes vivia en routers/store_showcase.py (O1-01, paso 0: movido sin cambio de comportamiento); los nombres viejos se
siguen pudiendo importar desde alli.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import Product, Tenant
from ..services import fygaro_legacy
from ..services import store_showcase as sv
from ..services.availability import settings_of
from ..services.store_seo import BUYABLE_STATES, boot_html, organization_ld, schema_availability
from .store import _stock_of, business_of, store_cfg
from .store_showcase import Ctx, _categories

seo = APIRouter(prefix="/public/seo", tags=["publico"])


def _tenant_by_host(db: Session, host: str | None) -> Tenant | None:
    h = (host or "").strip().lower().split(":")[0]
    if not h:
        return None
    for t in db.scalars(select(Tenant).where(Tenant.active)):
        cfg = store_cfg(t)
        dom = (cfg.get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        if cfg["published"] and dom and dom == h:
            return t
    return None


def _host(x_store_host: str | None, host: str | None) -> str:
    return (x_store_host or host or "").strip().lower().split(":")[0][:200]


def _base(host: str) -> str:
    return f"https://{host}"


def _abs(base: str, url: str) -> str:
    """WhatsApp y Google necesitan la foto con direccion completa (las del media del API vienen como /api/...)."""
    return f"{base}{url}" if url and url.startswith("/") and not url.startswith("//") else url


def _brand_image(base: str, cfg: dict, sc: dict) -> str | None:
    """Foto para compartir la portada y el catalogo (O1-12): el banner principal activo con foto o, si no hay, el logo."""
    for b in sorted(sc.get("banners") or [], key=lambda b: b.get("position", 0)):
        if b.get("active", True) and b.get("image"):
            return _abs(base, b["image"])
    logo = cfg.get("logo_url") or ""
    return _abs(base, logo) if logo.startswith(("http", "/")) else None


def _category_image(db: Session, t: Tenant, cats: list, cid: int) -> str | None:
    """Foto de la categoria (O1-12): la misma regla de la portada (/showcase): la primera foto de un producto publicado
    y con precio de la categoria o de sus hijas. Una consulta corta (lee solo fotos, como mucho 40 filas)."""
    ids, todo = {cid}, [cid]
    while todo:
        cur = todo.pop()
        for c in cats:
            if c.parent_id == cur and c.id not in ids:
                ids.add(c.id)
                todo.append(c.id)
    rows = db.execute(
        select(Product.images)
        .where(Product.tenant_id == t.id, Product.active, Product.show_on_web, Product.category_id.in_(ids), Product.price > 0, Product.images.is_not(None))
        .order_by(Product.web_featured.desc(), Product.id)
        .limit(40)
    )
    for (imgs,) in rows:
        url = ((imgs or [None])[0] or {}).get("url") if isinstance(imgs, list) and imgs else None
        if url:
            return url
    return None


# O2-01c: productos por pagina del catalogo y de cada categoria (CategoryPage.tsx, PAGE) y tope de ?pagina=
POR_PAGINA = 24
PAGINA_MAX = 200


def pagina_de(valor: object) -> int:
    """?pagina=N de la direccion: entero de 1 a PAGINA_MAX; cualquier otra cosa es la primera pagina."""
    s = str(valor or "").strip()
    return min(PAGINA_MAX, int(s)) if s.isdigit() and int(s) >= 1 else 1


def _paginado(url: str, pagina: int, total: int | None) -> dict:
    """Canonica propia de la pagina N de un listado (O2-01c) y sus vecinas. total = productos del listado (None si no
    se sabe: entonces siempre hay siguiente). Una pagina que no existe (mas alla de la ultima) no se indexa y apunta a
    la primera."""
    ultima = max(1, -(-total // POR_PAGINA)) if total is not None else None
    if ultima is not None and pagina > ultima:
        return {"pagina": pagina, "canonical": url, "robots": "noindex,follow", "fuera": True}
    canonical = url if pagina == 1 else f"{url}?pagina={pagina}"
    prev = None if pagina == 1 else (url if pagina == 2 else f"{url}?pagina={pagina - 1}")
    nxt = f"{url}?pagina={pagina + 1}" if ultima is None or pagina < ultima else None
    return {"pagina": pagina, "canonical": canonical, "prev": prev, "next": nxt, "ultima": ultima}


def _total_listado(db: Session, t: Tenant, cat_id: int | None) -> int | None:
    """Productos que lista la tienda en la categoria (con sus hijas) o en todo el catalogo: el snapshot ya armado."""
    from ..services.store_snapshot import counts as snapshot_counts

    c = snapshot_counts(db, t.id)
    if c is None:
        return None
    return int(c["n"]) if cat_id is None else int(c["categories"].get(cat_id) or 0)


def seo_meta(db: Session, t: Tenant, base: str, path: str, *, ctx: Ctx | None = None, card: dict | None = None, pagina: int = 1) -> dict:
    """Titulo, descripcion, canonica, Open Graph y JSON-LD de una ruta de la vitrina. La ficha (/item) pasa su ctx y su
    tarjeta ya armadas para no repetir las consultas (mismo resultado que sin ellas). pagina (O2-01c, ?pagina=N): solo
    en /catalogo y /c/<categoria>, que tienen una canonica por pagina."""
    cfg = store_cfg(t)
    sc = sv.showcase_cfg(cfg)
    name = cfg["name"]
    path = "/" + (path or "").strip("/")
    parts = [x for x in path.split("/") if x]
    site_desc = (
        sc["seo_description"]
        or f"{name}: cámaras de seguridad, grabadores, redes, control de acceso y respaldo eléctrico en Costa Rica. Precios con IVA, factura electrónica, soporte técnico local e instalación."
    )
    org = organization_ld(base, name, logo=cfg.get("logo_url"), whatsapp=cfg.get("whatsapp"), business=business_of(cfg))
    meta = {"site_name": name, "robots": "index,follow", "description": site_desc}
    brand_img = _brand_image(base, cfg, sc)
    if not parts:
        meta |= {
            "title": f"{name} · Tienda de seguridad electrónica y redes en Costa Rica",
            "canonical": base + "/",
            "image": brand_img,
            "image_alt": name,
            "ld": {
                "@context": "https://schema.org",
                "@graph": [
                    org,
                    {
                        "@type": "WebSite",
                        "url": base + "/",
                        "name": f"Tienda {name}",
                        "inLanguage": "es-CR",
                        "publisher": {"@id": base + "/#org"},
                        "potentialAction": {
                            "@type": "SearchAction",
                            "target": base + "/buscar?q={search_term_string}",
                            "query-input": "required name=search_term_string",
                        },
                    },
                ],
            },
        }
        return meta
    head = parts[0]
    if head == "p" and len(parts) > 1:
        pid = sv.product_id_from_ref(parts[1])
        p = db.get(Product, pid) if pid else None
        if not p or p.tenant_id != t.id or not p.show_on_web or not p.active:
            return meta | {"title": f"Producto no disponible · {name}", "robots": "noindex,follow", "canonical": base + "/catalogo"}
        if card is None:
            ctx = ctx or Ctx(db, t)
            card = ctx.card(p, _stock_of(db, t.id, [p.id]).get(p.id), full=True)
        c = card
        state = c["availability"]["state"]
        url = base + c["path"]
        desc = re.sub(r"\s+", " ", (p.description_store or p.description_invoice or p.name)).strip()
        price = Decimal(str(c["display_price"]))
        crumbs = [("Tienda", "/")]
        if c.get("category_path"):
            crumbs.append((c["category"], c["category_path"]))
        crumbs.append((p.name, c["path"]))
        offer = {
            "@type": "Offer",
            "url": url,
            "priceCurrency": c["display_currency"],
            "availability": schema_availability(state, (ctx.cfg_inv if ctx else None) or settings_of(t)),
            "itemCondition": "https://schema.org/NewCondition",
            "seller": {"@id": base + "/#org"},
        }
        if price > 0:
            offer["price"] = str(price)
            if state in BUYABLE_STATES:  # O1-06: solo se promete el precio por 30 dias en lo que se puede pagar
                offer["priceValidUntil"] = (date.today() + timedelta(days=30)).isoformat()
        product_ld = {
            "@type": "Product",
            "name": p.name,
            "sku": p.code,
            "description": desc[:500],
            "image": [_abs(base, u) for u in (c["images"][:5] or ([c["image"]] if c["image"] else []))],
            "category": c.get("category") or c.get("type") or None,
            "offers": offer,
        }
        if p.brand:
            product_ld["brand"] = {"@type": "Brand", "name": p.brand}
        if p.model:
            product_ld["mpn"] = p.model
        return meta | {
            "title": f"{p.name} · {name}"[:160],
            "description": (f"{desc[:200]} · " if desc else "")
            + ("Precio con IVA incluido. " if price > 0 else "")
            + "Envío a todo Costa Rica, factura electrónica y soporte local.",
            "canonical": url,
            "og_type": "product",
            "image": _abs(base, c["image"]) if c["image"] else None,
            "image_alt": p.name,
            "price": str(price) if price > 0 else None,
            "currency": c["display_currency"],
            "ld": {"@context": "https://schema.org", "@graph": [product_ld, sv.breadcrumb_ld(base, crumbs), org]},
        }
    if head == "c" and len(parts) > 1:
        cats = _categories(db, t.id)
        slugs = sv.category_slugs(cats)
        cid = next((i for i, s in slugs.items() if s == parts[1]), None)
        cat = next((c for c in cats if c.id == cid), None)
        if not cat:
            return meta | {"title": f"Categoría no disponible · {name}", "robots": "noindex,follow", "canonical": base + "/catalogo"}
        url = f"{base}/c/{slugs[cat.id]}"
        cat_img = _category_image(db, t, cats, cat.id)
        pg = _paginado(url, pagina, _total_listado(db, t, cat.id))
        n_pag = pagina if pagina > 1 and not pg.get("fuera") else 1
        desc = cat.description or f"{cat.name} en {name}: precios con IVA, disponibilidad, envío a todo Costa Rica y asesoría técnica."
        return meta | {
            "title": f"{cat.name} · {name}" + (f" · Página {n_pag}" if n_pag > 1 else ""),
            "image": _abs(base, cat_img) if cat_img else brand_img,
            "image_alt": cat.name if cat_img else name,
            "description": desc + (f" Página {n_pag}." if n_pag > 1 else ""),
            "canonical": pg["canonical"],
            "prev": pg.get("prev"),
            "next": pg.get("next"),
            **({"robots": pg["robots"]} if pg.get("robots") else {}),
            "ld": {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "CollectionPage", "name": cat.name, "url": pg["canonical"], "isPartOf": {"@type": "WebSite", "url": base + "/"}},
                    sv.breadcrumb_ld(base, [("Tienda", "/"), (cat.name, f"/c/{slugs[cat.id]}")]),
                    org,
                ],
            },
        }
    simple = {
        "catalogo": ("Catálogo completo", "Todos los productos: cámaras, grabadores, redes, control de acceso, UPS y herramientas, con precio e IVA incluido."),
        "arma-tu-sistema": (
            "Arma tu sistema de cámaras",
            "Respondé 4 preguntas y te armamos un kit de videovigilancia con productos reales, validado por un técnico de Crimson.",
        ),
        "asesoria": (
            "Solicitud de asesoría personalizada",
            "Contanos qué necesitás proteger o conectar y un asesor de Crimson te contacta con una propuesta a la medida.",
        ),
    }
    if head in simple and len(parts) == 1:
        title, desc = simple[head]
        url = f"{base}/{head}"
        out = {
            "title": f"{title} · {name}",
            "description": desc,
            "canonical": url,
            "image": brand_img,
            "image_alt": name,
            "ld": {"@context": "https://schema.org", "@graph": [sv.breadcrumb_ld(base, [("Tienda", "/"), (title, f"/{head}")]), org]},
        }
        if head == "catalogo":  # O2-01c: el catalogo completo tambien pagina
            pg = _paginado(url, pagina, _total_listado(db, t, None))
            out |= {"canonical": pg["canonical"], "prev": pg.get("prev"), "next": pg.get("next")}
            if pg.get("robots"):
                out["robots"] = pg["robots"]
            elif pagina > 1:
                out["title"] = f"{title} · {name} · Página {pagina}"
                out["description"] = f"{desc} Página {pagina}."
        return meta | out
    if head == "info" and len(parts) == 2 and parts[1] in sv.INFO_KINDS:
        title = sv.INFO_KINDS[parts[1]]
        url = f"{base}/info/{parts[1]}"
        ld: dict = {"@context": "https://schema.org", "@graph": [sv.breadcrumb_ld(base, [("Tienda", "/"), (title, f"/info/{parts[1]}")]), org]}
        if parts[1] == "preguntas-frecuentes":
            from ..services import store_orders as so

            faq = sv.public_faq(sc["faq"], so.methods_available(db, t))  # la misma de /info (O1-04 l)
            ld["@graph"].insert(
                0,
                {
                    "@type": "FAQPage",
                    "mainEntity": [{"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq],
                },
            )
        return meta | {"title": f"{title} · {name}", "description": f"{title} de la tienda {name}.", "canonical": url, "ld": ld}
    # busqueda, comparador y cualquier otra: no se indexan (contenido duplicado o vacio)
    return meta | {"title": f"Tienda · {name}", "robots": "noindex,follow", "canonical": base + "/"}


@seo.get("/head", response_class=PlainTextResponse)
@seo.get("/head/{path:path}", response_class=PlainTextResponse)
def seo_head(
    path: str = "",
    host: str | None = Query(None, max_length=200),
    pagina: str | None = Query(None, max_length=12),
    x_store_host: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Fragmento <head> por ruta. nginx lo incrusta con SSI en index.html para el dominio de la tienda: asi
    WhatsApp, Facebook y Google ven titulo, foto y precio sin ejecutar JavaScript. Cuerpo vacio = nginx usa el
    titulo por defecto (dominio que no es tienda, tienda despublicada). pagina: el unico parametro de la direccion que
    nginx deja pasar (O2-01c); cualquier otro no cambia la canonica."""
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    if not t:
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    try:
        meta = seo_meta(db, t, _base(h), path, pagina=pagina_de(pagina))
    except HTTPException:
        return PlainTextResponse("")
    return PlainTextResponse(sv.head_html(meta), media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def boot_data(db: Session, t: Tenant, path: str) -> dict:
    """O2-01a: lo que la vitrina necesita para pintar sin dos llamadas en serie (/store-domain y luego /store): el slug,
    los datos base de la tienda (los mismos de /public/store/<slug>) y, en la portada, la portada (/showcase). Los textos
    legales (decenas de KB) no viajan: solo si existen (legal_on); el texto completo llega con /store, que la vitrina
    igual pide en segundo plano."""
    from .store import store_home
    from .store_showcase import showcase_public

    path = "/" + (path or "").strip("/")
    home = store_home(t.slug, db)
    legal = home["store"].get("legal") or {}
    home["store"] = {
        **home["store"],
        "legal": {"privacy": "", "terms": ""},
        "legal_on": {"privacy": bool(legal.get("privacy")), "terms": bool(legal.get("terms"))},
    }
    data: dict = {"v": 1, "slug": t.slug, "path": path, "store": home}
    if path == "/":
        sc = showcase_public(t.slug, Response(), None, db)
        sc.pop("seo", None)  # el <head> ya lo trae por SSI y en la primera ruta el navegador no lo toca (O1-06)
        data["showcase"] = sc
    return data


@seo.get("/boot", response_class=PlainTextResponse)
@seo.get("/boot/{path:path}", response_class=PlainTextResponse)
def seo_boot(path: str = "", host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    """O2-01a: <script type="application/json" id="sh-boot"> con los datos de arranque de la ruta. nginx lo incrusta
    por SSI en el <body> del dominio de la tienda (y lo guarda 60 s). Vacio en otro dominio, en una tienda despublicada
    o si algo falla: la vitrina sigue como antes (pide /store-domain y /store)."""
    t = _tenant_by_host(db, _host(x_store_host, host))
    if not t:
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    try:
        body = boot_html(boot_data(db, t, path))
    except Exception:  # noqa: BLE001 - el arranque rapido nunca tumba el HTML de la tienda
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    return PlainTextResponse(body, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def body_html(db: Session, t: Tenant, path: str, pagina: int = 1) -> str:
    """O2-01b: el cuerpo de la ruta (services/store_ssr.py) si su tipo esta encendido en settings.store.ssr_body; vacio
    si no. Los datos son los mismos de la vitrina: /showcase en la portada, el snapshot en el catalogo y las categorias,
    /item en la ficha."""
    import gzip
    import json

    from ..services import store_ssr as ssr
    from ..services.store_snapshot import get_or_rebuild

    cfg = store_cfg(t)
    tipo = ssr.tipo_de(path)
    if not tipo or not ssr.flags(cfg)[tipo]:
        return ""
    biz = business_of(cfg)
    parts = [x for x in path.strip("/").split("/") if x]
    if tipo == "portada":
        from .store_showcase import showcase_public

        return ssr.portada(cfg, biz, showcase_public(t.slug, Response(), None, db))
    if tipo == "ficha":
        from .store_showcase import item

        try:
            it = item(t.slug, parts[1], Response(), db)
        except HTTPException:
            return ""
        return ssr.ficha(cfg, biz, it)
    gz, _ = get_or_rebuild(db, t.id)
    snap = json.loads(gzip.decompress(gz))
    slug = parts[1] if parts[0] == "c" else None
    desc, padre = None, None
    if slug:
        cats = _categories(db, t.id)
        slugs = sv.category_slugs(cats)
        cat = next((c for c in cats if slugs.get(c.id) == slug), None)
        if cat is None:
            return ""
        desc = cat.description
        up = next((c for c in cats if c.id == cat.parent_id), None)
        padre = (up.name, f"/c/{slugs[up.id]}") if up and up.id in slugs else None
    return ssr.categoria(cfg, biz, snap, slug, pagina, desc, padre)


@seo.get("/body", response_class=PlainTextResponse)
@seo.get("/body/{path:path}", response_class=PlainTextResponse)
def seo_body(
    path: str = "",
    host: str | None = Query(None, max_length=200),
    pagina: str | None = Query(None, max_length=12),
    x_store_host: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """O2-01b: contenido real de la ruta para el HTML (H1, precios, disponibilidad y enlaces). nginx lo incrusta por SSI
    dentro del <body> del dominio de la tienda, con tiempo maximo de 3 s; vacio (tipo apagado, otro dominio o error) =
    la pagina sigue como hoy."""
    t = _tenant_by_host(db, _host(x_store_host, host))
    if not t:
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    try:
        body = body_html(db, t, "/" + (path or "").strip("/"), pagina_de(pagina))
    except Exception:  # noqa: BLE001 - el cuerpo del servidor nunca tumba el HTML de la tienda
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    # vacio (tipo apagado: lo normal hoy) se guarda 5 min en nginx: el HTML de la tienda espera esta subpeticion (include
    # set=) y asi casi nunca llega al API
    cc = "public, max-age=60" if body else "public, max-age=300"
    return PlainTextResponse(body, media_type="text/html; charset=utf-8", headers={"Cache-Control": cc})


def store_base(t: Tenant) -> str:
    """https://<dominio de la tienda> (vacio si la tienda no tiene dominio propio)."""
    dom = (store_cfg(t).get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    return _base(dom) if dom else ""


def seo_block(db: Session, t: Tenant, path: str, *, ctx: Ctx | None = None, card: dict | None = None) -> dict | None:
    """O1-06: lo mismo que /public/seo/head para esa ruta, como datos, para que la vitrina lo aplique al navegar sin
    armar su propio JSON-LD (un solo JSON-LD: el del servidor). Aditivo: si falla, None y el navegador deja lo suyo."""
    try:
        m = seo_meta(db, t, store_base(t), path, ctx=ctx, card=card)
    except Exception:  # noqa: BLE001 - el SEO nunca tumba la ficha ni el catalogo
        return None
    return {
        "title": m["title"],
        "description": str(m.get("description") or "")[:400],
        "canonical": m.get("canonical"),
        "robots": m.get("robots", "index,follow"),
        "og_type": m.get("og_type", "website"),
        "og_image": m.get("image"),
        "og_image_alt": m.get("image_alt") if m.get("image") else None,
        "ld": m.get("ld"),
    }


@seo.get("/noscript", response_class=PlainTextResponse)
def seo_noscript(host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    """O1-06: lo que ve quien abre la tienda sin JavaScript (nginx lo incrusta por SSI dentro de <noscript>, solo en el
    dominio de la tienda): el nombre del comercio y como contactarlo de verdad (WhatsApp y telefono). Vacio en otro
    dominio."""
    t = _tenant_by_host(db, _host(x_store_host, host))
    if not t:
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})
    cfg = store_cfg(t)
    b = business_of(cfg)
    wa = re.sub(r"\D", "", cfg.get("whatsapp") or "")
    tel = re.sub(r"\D", "", b.get("phone") or "") or wa
    links = []
    if wa:
        links.append(f'<a href="https://wa.me/{wa}">WhatsApp</a>')
    if tel:
        tel = tel if len(tel) > 8 else "506" + tel
        links.append(f'<a href="tel:+{tel}">Llamar al {sv.esc(b.get("phone") or "+" + tel)}</a>')
    contacto = f"<p>Para ver los productos y comprar, activá JavaScript en tu navegador{(', o escribinos: ' + ' · '.join(links)) if links else ''}.</p>"
    extra = "".join(f"<p>{sv.esc(x)}</p>" for x in (b.get("legal_name"), b.get("address"), b.get("hours")) if x)
    body = f'<div class="sh-noscript"><h1>{sv.esc(cfg["name"])}</h1>{contacto}{extra}</div>'
    return PlainTextResponse(body, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})


def _sitemap(db: Session, t: Tenant, base: str) -> str:
    """O1-07: el sitemap es el catalogo VISIBLE, el mismo del snapshot (no vuelve a cargar todos los publicados):
    fichas que la tienda lista, categorias con productos visibles (las madre incluidas), paginas informativas con
    contenido y /catalogo. Sin priority (Google la ignora); lastmod solo con product.web_updated_at (O0-07: la ultima
    vez que cambio algo que el cliente ve), nunca la fecha de una sincronizacion. Las fichas ocultas no salen aqui pero
    siguen respondiendo (OutOfStock): noindex recien tras 60 dias ocultas (hidden_since, O3-01).
    Tres sentencias o menos: el snapshot, sus fechas web_updated_at y nada mas (el tenant ya viene cargado)."""
    import gzip
    import json

    from ..services.store_snapshot import get_or_rebuild

    gz, _ = get_or_rebuild(db, t.id)
    snap = json.loads(gzip.decompress(gz))
    prods = snap.get("products") or []
    shown = {int(x["id"]) for x in prods}
    when = {
        pid: ts.date().isoformat()
        for pid, ts in db.execute(
            select(Product.id, Product.web_updated_at).where(
                Product.tenant_id == t.id, Product.active, Product.show_on_web, Product.web_updated_at.is_not(None)
            )
        )
        if pid in shown
    }

    def cats(nodes: list) -> list[str]:
        out = []
        for c in nodes or []:
            if c.get("count") and c.get("slug"):
                out.append(c["slug"])
            out += cats(c.get("children") or [])
        return out

    urls: list[tuple[str, str | None]] = [(base + "/", None), (base + "/catalogo", None), (base + "/arma-tu-sistema", None), (base + "/asesoria", None)]
    urls += [(f"{base}/c/{slug}", None) for slug in dict.fromkeys(cats(snap.get("categories") or []))]
    legal = store_cfg(t).get("legal") or {}
    for k in sv.INFO_KINDS:
        if (k == "terminos" and not legal.get("terms")) or (k == "privacidad" and not legal.get("privacy")):
            continue
        urls.append((f"{base}/info/{k}", None))
    urls += [(base + x["path"], when.get(int(x["id"]))) for x in prods if x.get("path")]
    body = "".join(f"<url><loc>{htmllib.escape(u)}</loc>{f'<lastmod>{lm}</lastmod>' if lm else ''}</url>" for u, lm in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'


@seo.get("/exists")
@seo.get("/exists/{path:path}")
def seo_exists(
    path: str = "",
    host: str | None = Query(None, max_length=200),
    x_store_host: str | None = Header(None),
    x_original_uri: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Para nginx (auth_request): 204 si la ficha o la categoria existe, 403 si no (nginx lo sirve como 404 real
    con la vitrina). En un dominio que no es tienda no bloquea nada."""
    t = _tenant_by_host(db, _host(x_store_host, host))
    if not t:
        return Response(status_code=204)
    path = path or urlsplit(x_original_uri or "/").path
    parts = [x for x in (path or "").strip("/").split("/") if x]
    ok = True
    if len(parts) >= 2 and parts[0] == "p":
        pid = sv.product_id_from_ref(parts[1])
        p = db.get(Product, pid) if pid else None
        ok = bool(p and p.tenant_id == t.id and p.show_on_web and p.active)
    elif len(parts) >= 2 and parts[0] == "c":
        cats = _categories(db, t.id)
        ok = parts[1] in set(sv.category_slugs(cats).values()) or (parts[1].isdigit() and any(c.id == int(parts[1]) for c in cats))
    return Response(status_code=204 if ok else 403, headers={"Cache-Control": "no-store"})


@seo.get("/sitemap.xml")
def seo_sitemap(host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    if not t:
        raise HTTPException(404, "Sin sitemap para este dominio")
    return Response(_sitemap(db, t, _base(h)), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@seo.get("/robots.txt", response_class=PlainTextResponse)
def seo_robots(host: str | None = Query(None, max_length=200), x_store_host: str | None = Header(None), db: Session = Depends(get_db)):
    """Dominio de la tienda: se indexa (menos busqueda, comparador y direcciones con parametros de pedido, version o
    carrito) y anuncia el sitemap. Cualquier otro (el panel): no se indexa nada."""
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    if not t:
        return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"Cache-Control": "public, max-age=3600"})
    return PlainTextResponse(robots_txt(_base(h)), headers={"Cache-Control": "public, max-age=3600"})


# O1-01: la vitrina pide sus datos a /api/public/ y sus fotos a /api/media/f/. Con «Disallow: /api/» a secas, el
# buscador que ejecuta JavaScript veia «Tienda no disponible». Gana la regla mas especifica (la mas larga): /api/public/
# queda abierto, el resto de /api/ cerrado, y los pedidos (datos de una compra) cerrados aunque esten bajo /api/public/.
# El JSON igual nunca se indexa: nginx le pone X-Robots-Tag noindex (O0-05a).
ROBOTS_RULES = (
    "Allow: /",
    "Allow: /api/public/",
    "Allow: /api/media/f/",
    "Disallow: /api/",
    "Disallow: /api/public/store/*/orders/",
    "Disallow: /buscar",
    "Disallow: /comparar",
    "Disallow: /*?pedido=",
    "Disallow: /*?v=",
    "Disallow: /*?carrito=",
)


def robots_txt(base: str) -> str:
    """robots.txt del dominio de la tienda: una regla por linea y el sitemap al final."""
    return "User-agent: *\n" + "\n".join(ROBOTS_RULES) + f"\n\nSitemap: {base}/sitemap.xml\n"


@seo.get("/legacy")
def seo_legacy(
    uri: str | None = Query(None, max_length=1000),
    host: str | None = Query(None, max_length=200),
    x_store_host: str | None = Header(None),
    x_original_uri: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """301 de las direcciones de Fygaro (/products/<uuid>/, /products/category/<id>/, /products/search/?keywords=,
    /terms, /policies, /page/...) a las nuevas. Los enlaces ya compartidos y lo que Google tiene indexado no mueren."""
    h = _host(x_store_host, host)
    t = _tenant_by_host(db, h)
    raw = x_original_uri or uri or "/"
    parts = urlsplit(raw if raw.startswith("/") else "/" + raw)
    if not t:
        return RedirectResponse("/", status_code=302)
    fy = (t.settings or {}).get("fygaro") or {}
    cats = _categories(db, t.id)
    slugs = sv.category_slugs(cats)

    def to_product(pid: int) -> str | None:
        p = db.get(Product, pid)
        return sv.product_path(p) if p and p.tenant_id == t.id and p.show_on_web and p.active else None

    def to_category(cid: int) -> str:
        return f"/c/{slugs[cid]}" if cid in slugs else "/catalogo"

    def unmapped(ref: str, entry: dict | None) -> str | None:
        hit = fygaro_legacy.resolve(db, t.id, ref, entry)
        if not hit:
            return None
        return to_product(int(hit[1])) if hit[0] == "product" else f"/buscar?q={quote(str(hit[1]), safe='')}"

    q = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
    target = sv.legacy_target(parts.path, q, fy.get("products") or {}, fy.get("categories") or {}, to_product, to_category, unmapped)
    return RedirectResponse(_base(h) + target, status_code=301, headers={"Cache-Control": "public, max-age=86400"})
