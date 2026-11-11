"""Mi Tienda -> Resumen (O4-03): «Lista para vender» y salud del catalogo. SOLO LECTURA: nada de aqui escribe.

Los numeros salen de las mismas fuentes que ya usa la tienda, para que nunca digan otra cosa:
- publicados: lo que cuenta el panel Inventario web (productos activos con «Mostrar en la tienda»).
- visibles, pagables y solicitables: el snapshot de la tienda (lo que el cliente ve), con la disponibilidad de la
  funcion unica: accion «comprar» = se paga en linea; «whatsapp»/«avisarme» = el cliente escribe.
- los puntos de la lista: la configuracion guardada (comercio, checkout, vitrina, paginas) y el catalogo visible.

Cada punto dice que pasa con un dato verificable, cuantos son y adonde ir a corregirlo (la seccion de Mi Tienda o el
catalogo ya filtrado). Nada de medicion de terceros: el dueno no usa Google Analytics, Ads ni pixeles.

Presupuesto de consultas (prueba): con el snapshot armado, a lo sumo 10 por calculo; el resultado se recuerda 60 s
por empresa y version del catalogo.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from ..models import Order, Product, Tenant

CACHE_S = 60
NOMBRE_LARGO = 60  # lo que Google muestra de un titulo; mas largo se corta (mismo filtro «Nombre largo» del catalogo)
_CACHE: dict[int, tuple[float, str, dict]] = {}

# filtros del catalogo de Mi Tienda (O4-04) a los que lleva cada punto
CAT = "/mi-tienda/catalogo"


def clear_cache() -> None:
    _CACHE.clear()


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def _norm(s: str | None) -> str:
    import unicodedata

    s = unicodedata.normalize("NFD", s or "")
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in s if unicodedata.category(c) != "Mn").lower())


def external_image(url: str | None) -> bool:
    """Foto servida por otro sitio (el proveedor): la tienda depende de que ese servidor responda."""
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://") or u.startswith("//")


_MESES = re.compile(r"\b(\d{1,2})\s*(?:\(\w+\)\s*)?mes(?:es)?\b")
_REEMBOLSO = re.compile(r"reembols\w*[^.\n]{0,120}?\b\d{1,3}\s*(?:d[ií]as|horas)|\b\d{1,3}\s*(?:d[ií]as|horas)[^.\n]{0,120}?reembols", re.I)


def _snapshot(db: Session, tid: int) -> dict:
    from . import store_snapshot

    gz, _ver = store_snapshot.get_or_rebuild(db, tid)
    return json.loads(gzip.decompress(gz))


def health(db: Session, t: Tenant) -> dict:
    """El resumen completo (cacheado 60 s por empresa y version del catalogo)."""
    from . import store_showcase as sv

    ver = sv.get_catalog_version(t)
    hit = _CACHE.get(t.id)
    now = time.monotonic()
    if hit and hit[0] > now and hit[1] == ver:
        return hit[2]
    out = _compute(db, t)
    _CACHE[t.id] = (now + CACHE_S, ver, out)
    return out


def _compute(db: Session, t: Tenant) -> dict:
    from ..routers.store import business_of, store_cfg
    from . import store_inventory as sinv
    from . import store_orders as so
    from . import store_showcase as sv
    from .catalog_fichas import store_description
    from .catalog_text import shown_name

    cfg = store_cfg(t)
    inv = sinv.settings_of(t)
    snap = _snapshot(db, t.id)
    prods = snap.get("products") or []
    accion = {int(p["id"]): ((p.get("availability") or {}).get("accion")) for p in prods}
    visibles = len(prods)
    pagables = sum(1 for a in accion.values() if a == "comprar")
    solicitables = sum(1 for a in accion.values() if a in ("whatsapp", "avisarme"))

    # mismo conteo que el panel Inventario web (routers/store_inventory.web_inventory -> summary.publicados)
    publicados = (
        db.scalar(
            select(func.count()).select_from(Product).where(Product.tenant_id == t.id, Product.active, Product.item_type == "producto", Product.show_on_web)
        )
        or 0
    )
    desde = datetime.now(UTC) - timedelta(days=30)
    por_estado = dict(
        db.execute(
            select(Order.status, func.count()).where(Order.tenant_id == t.id, Order.channel == "tienda", Order.created_at >= desde).group_by(Order.status)
        ).all()
    )
    pedidos_30 = sum(por_estado.values())
    pagados_30 = sum(n for s, n in por_estado.items() if s not in ("nuevo", "en_revision", "cancelado"))
    cancelados_30 = por_estado.get("cancelado", 0)
    prov_al = db.scalar(select(func.max(Product.supplier_updated_at)).where(Product.tenant_id == t.id, Product.active, Product.show_on_web))

    # el catalogo visible, con lo que hace falta para medir su calidad (una consulta, columnas livianas)
    ids = list(accion)
    detalle = (
        db.scalars(
            select(Product)
            .where(Product.id.in_(ids))
            .options(
                load_only(
                    Product.id,
                    Product.name,
                    Product.web_name,
                    Product.description_store,
                    Product.web_description,
                    Product.images,
                    Product.specs,
                    Product.web_specs,
                    Product.datasheet_url,
                )
            )
        ).all()
        if ids
        else []
    )
    # Ola 3: lo que la tienda muestra (nombre comercial o el fiscal; descripcion aprobada o la de tienda si sirve)
    largos = sum(1 for p in detalle if len(shown_name(p)) > NOMBRE_LARGO)
    sin_desc = sum(1 for p in detalle if not store_description(p))
    sin_foto = sum(1 for p in detalle if not [i for i in (p.images or []) if (i or {}).get("url")])
    fotos_prov = sum(1 for p in detalle if any(external_image((i or {}).get("url")) for i in (p.images or [])))

    sc = sv.showcase_cfg(cfg)
    ck = so.checkout_cfg(t)
    biz = business_of(cfg)
    m = ck.get("methods") or {}
    legal = cfg.get("legal") or {}
    items: list[dict] = []

    def add(grupo: str, key: str, ok: bool, texto: str, to: str, n: int | None = None) -> None:
        items.append({"grupo": grupo, "key": key, "ok": bool(ok), "texto": texto, "to": to, "n": n})

    # ---------------- Vender hoy
    g = "Vender hoy"
    add(
        g,
        "publicada",
        cfg.get("published"),
        "La tienda está publicada" if cfg.get("published") else "La tienda no está publicada: sus clientes no la ven",
        "/mi-tienda/publicacion",
    )
    add(
        g, "carrito", cfg.get("kind") == "tienda",
        "Sus clientes pueden pagar en línea (tienda con carrito)" if cfg.get("kind") == "tienda" else "La tienda está en modo catálogo: nadie puede pagar en línea",
        "/mi-tienda/publicacion",
    )  # fmt: skip
    if solicitables:
        txt = f"Hoy sus clientes solo pueden pagar {_plural(pagables, 'producto', 'productos')} en línea; los otros {solicitables} les piden escribir por WhatsApp"
    else:
        txt = f"Sus clientes pueden pagar en línea los {visibles} productos visibles"
    add(g, "pagables", solicitables == 0, txt, f"{CAT}?estado=consultar", solicitables)
    if prov_al is not None:
        prov_al = prov_al if prov_al.tzinfo else prov_al.replace(tzinfo=UTC)
        horas = max(0, int((datetime.now(UTC) - prov_al).total_seconds() // 3600))
        maximo = int(inv.get("supplier_max_age_hours") or 48)
        edad = _plural(horas // 24, "día", "días") if horas >= 48 else _plural(horas, "hora", "horas")
        add(
            g, "proveedor", horas <= maximo,
            f"El dato del proveedor tiene {edad}" + ("" if horas <= maximo else f" (la tienda lo acepta hasta {maximo} horas)"),
            "/inventario?tab=web", horas,
        )  # fmt: skip
    if sc["featured"]:
        malos = [i for i in sc["featured"] if accion.get(i) != "comprar"]
        add(
            g,
            "destacados",
            not malos,
            f"{_plural(len(malos), 'destacado no se puede', 'destacados no se pueden')} comprar" if malos else "Todos los destacados se pueden comprar",
            "/mi-tienda/kits",
            len(malos),
        )
    retiro = [r for r in cfg.get("shipping_rates") or [] if r.get("active", True) and (r.get("zone") == "retiro" or r.get("id") == "retiro")]
    if retiro:
        con_dir = bool(((ck.get("pickup") or {}).get("address") or "").strip())
        add(g, "retiro", con_dir, "El retiro en sitio tiene dirección" if con_dir else "Se ofrece retiro pero falta la dirección", "/mi-tienda/pagos-envios")
    activos = [k for k, v in m.items() if (v or {}).get("active")]
    add(
        g,
        "pagos",
        bool(activos),
        _plural(len(activos), "forma de pago activa", "formas de pago activas") if activos else "No hay ninguna forma de pago activa",
        "/mi-tienda/pagos-envios",
        len(activos),
    )
    if (m.get("sinpe") or {}).get("active") and not ((m.get("sinpe") or {}).get("phone") or "").strip():
        add(g, "sinpe_numero", False, "SINPE Móvil está activo pero no tiene número", "/mi-tienda/pagos-envios")
    if (m.get("transferencia") or {}).get("active") and not ((m.get("transferencia") or {}).get("iban") or "").strip():
        add(g, "iban", False, "La transferencia está activa pero no tiene cuenta IBAN", "/mi-tienda/pagos-envios")

    # ---------------- Confianza y legalidad
    g = "Confianza y legalidad"
    add(
        g,
        "razon_social",
        bool(biz["legal_name"]),
        "La razón social está en el pie" if biz["legal_name"] else "Falta la razón social en el pie",
        "/mi-tienda/pagos-envios",
    )
    add(
        g,
        "cedula",
        bool(biz["legal_id"]),
        "La cédula jurídica está en el pie" if biz["legal_id"] else "Falta la cédula jurídica en el pie",
        "/mi-tienda/pagos-envios",
    )
    for code, nombre in (("sinpe", "SINPE"), ("transferencia", "de la transferencia")):
        mm = m.get(code) or {}
        holder = (mm.get("holder") or "").strip()
        if mm.get("active") and holder and biz["legal_name"]:
            igual = _norm(holder) == _norm(biz["legal_name"])
            add(
                g,
                f"titular_{code}",
                igual,
                f"El titular {nombre} coincide con la razón social" if igual else f"El titular {nombre} no coincide con la razón social",
                "/mi-tienda/pagos-envios",
            )
    add(
        g,
        "terminos",
        bool((legal.get("terms") or "").strip()),
        "Tiene términos y condiciones" if (legal.get("terms") or "").strip() else "Faltan los términos y condiciones",
        "/mi-tienda/paginas",
    )
    add(
        g,
        "privacidad",
        bool((legal.get("privacy") or "").strip()),
        "Tiene política de privacidad" if (legal.get("privacy") or "").strip() else "Falta la política de privacidad",
        "/mi-tienda/paginas",
    )
    sin_dato = len(sc["trust"]) - len(sv.public_trust(sc["trust"], biz["hours"]))
    add(
        g,
        "sellos",
        sin_dato == 0,
        f"{_plural(sin_dato, 'sello sin dato no se muestra', 'sellos sin dato no se muestran')} (por ejemplo, atención sin horario)"
        if sin_dato
        else "Todos los sellos llevan su dato",
        "/mi-tienda/portada",
        sin_dato,
    )
    envios = (sc["pages"] or {}).get("envios") or ""
    plazo = bool(_REEMBOLSO.search(envios))
    add(
        g,
        "reembolso",
        plazo,
        "Envíos y devoluciones dice el plazo de reembolso" if plazo else "Falta el plazo de reembolso en Envíos y devoluciones",
        "/mi-tienda/paginas",
    )
    m_gar = set(_MESES.findall(sv.fold((sc["pages"] or {}).get("garantias") or "")))
    m_ter = set(_MESES.findall(sv.fold(legal.get("terms") or "")))
    if m_gar and m_ter:
        add(
            g,
            "plazos",
            m_gar == m_ter,
            "Términos y Garantías dicen el mismo plazo de garantía" if m_gar == m_ter else "Términos y Garantías indican plazos de garantía distintos",
            "/mi-tienda/paginas",
        )

    # ---------------- Verse bien
    g = "Verse bien"
    incompletos = sum(1 for b in sc["banners"] if sv.banner_incomplete(b))
    if sc["banners"]:
        add(
            g,
            "banners",
            incompletos == 0,
            f"Tiene {_plural(incompletos, 'banner sin título propio o sin imagen', 'banners sin título propio o sin imagen')} (no se muestran)"
            if incompletos
            else "Los banners tienen título e imagen",
            "/mi-tienda/portada",
            incompletos,
        )
    add(
        g,
        "nombre_largo",
        largos == 0,
        f"{_plural(largos, 'producto con nombre largo', 'productos con nombre largo')} (más de {NOMBRE_LARGO} caracteres)"
        if largos
        else "Ningún nombre se corta",
        f"{CAT}?falta=nombre_largo",
        largos,
    )
    add(
        g,
        "sin_foto",
        sin_foto == 0,
        f"{_plural(sin_foto, 'producto sin foto', 'productos sin foto')}" if sin_foto else "Todos los productos visibles tienen foto",
        f"{CAT}?falta=sin_foto",
        sin_foto,
    )
    add(
        g,
        "fotos_proveedor",
        fotos_prov == 0,
        f"{_plural(fotos_prov, 'producto tiene', 'productos tienen')} fotos que dependen del servidor del proveedor"
        if fotos_prov
        else "Ninguna foto depende del servidor del proveedor",
        f"{CAT}?falta=foto_proveedor",
        fotos_prov,
    )

    # ---------------- Que la encuentren
    g = "Que la encuentren"
    desc = bool((sc.get("seo_description") or "").strip())
    add(g, "seo_portada", desc, "La portada tiene descripción para Google" if desc else "Falta la descripción para Google de la portada", "/mi-tienda/seo")
    add(
        g,
        "sin_descripcion",
        sin_desc == 0,
        f"{_plural(sin_desc, 'producto sin descripción', 'productos sin descripción')}" if sin_desc else "Todos los productos visibles tienen descripción",
        f"{CAT}?falta=sin_descripcion",
        sin_desc,
    )

    listos = sum(1 for x in items if x["ok"])
    return {
        "indicadores": {
            "publicados": publicados,
            "visibles": visibles,
            "pagables": pagables,
            "solicitables": solicitables,
            "pedidos_30": pedidos_30,
            "pagados_30": pagados_30,
        },
        "avance": {"listos": listos, "total": len(items)},
        "items": items,
        # promesas cumplidas: solo lo que hoy se mide de verdad
        "promesas": {"cancelados_30": cancelados_30},
        # O2-12 (Ola 2): fichas mas vistas, clics de WhatsApp y busquedas sin resultado. Hasta que existan los
        # contadores propios, None (el panel no inventa numeros)
        "contadores": None,
        "snapshot_version": snap.get("version"),
        "calculado": datetime.now(UTC).isoformat(),
    }
