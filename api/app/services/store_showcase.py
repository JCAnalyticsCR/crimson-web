"""Vitrina de la tienda propia (reemplazo de Fygaro): direcciones amigables, especificaciones deducidas, busqueda
tolerante, portada administrable, configurador "Arma tu sistema" y SEO (metas por ruta, JSON-LD, sitemap).

Todo lo administrable vive en tenant.settings["store"]["showcase"] (JSON, sin migracion). Por producto solo hay
tres columnas opcionales: compare_price, datasheet_url y specs (migracion tienda_vit_a1c4e7f20b93).
"""

from __future__ import annotations

import functools
import hashlib
import math
import re
import unicodedata
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from ..models import Product
from .store_seo import availability_schema, breadcrumb_ld, esc, head_html, ld_json  # noqa: F401 (SEO: vive en store_seo, O1-01)

# ---------------------------------------------------------------- contenido por defecto (editable en Mi Tienda)
DEFAULT_TRUST = [
    {"icon": "shield", "title": "Garantía real", "text": "Equipo nuevo con garantía de fábrica. Si falla, nosotros hacemos el trámite."},
    {"icon": "tool", "title": "Soporte técnico local", "text": "Técnicos de Crimson en Costa Rica, por WhatsApp, teléfono o en sitio."},
    {"icon": "truck", "title": "Envío o retiro", "text": "Envío a todo el país o retiro coordinado. Te avisamos cuando sale."},
    {"icon": "doc", "title": "Factura electrónica", "text": "Toda compra lleva su comprobante electrónico de Hacienda."},
]

DEFAULT_KITS = [
    {
        "id": "casa-4",
        "title": "Casa segura · 4 cámaras",
        "text": "2 adentro, 2 afuera, 15 días de grabación y vista desde el celular.",
        "answers": {"place": "casa", "indoor": 2, "outdoor": 2, "days": 15, "remote": True},
        "active": True,
    },
    {
        "id": "negocio-8",
        "title": "Negocio · 8 cámaras",
        "text": "Caja, bodega y entradas cubiertas. 30 días de grabación.",
        "answers": {"place": "negocio", "indoor": 4, "outdoor": 4, "days": 30, "remote": True},
        "active": True,
    },
    {
        "id": "bodega-16",
        "title": "Bodega · 16 cámaras",
        "text": "Perímetro, patio y andenes. Grabación de un mes.",
        "answers": {"place": "bodega", "indoor": 4, "outdoor": 12, "days": 30, "remote": True},
        "active": True,
    },
]

DEFAULT_PAGES = {
    "envios": (
        "Envíos\n"
        "Enviamos a todo Costa Rica por Correos de Costa Rica o mensajería. El costo se calcula al finalizar el pedido "
        "según el método elegido. También podés retirar en nuestras instalaciones coordinando por WhatsApp.\n\n"
        "Tiempos\n"
        "Producto disponible: sale en 1 a 2 días hábiles después de confirmado el pago. Producto bajo pedido: de 3 a 5 "
        "días hábiles; te avisamos la fecha de entrega en cuanto confirmamos tu pago.\n\n"
        "Devoluciones\n"
        "Si el producto llega dañado o no corresponde a lo pedido, escribinos en los 8 días naturales siguientes a la "
        "entrega con fotos del empaque y del equipo. Lo revisamos y, si procede, lo cambiamos o devolvemos el dinero. "
        "No se aceptan devoluciones de equipo instalado, configurado o con el empaque dañado por el cliente, ni de "
        "productos hechos o pedidos a la medida."
    ),
    "garantias": (
        "Garantía de fábrica\n"
        "Todo el equipo que vendemos es nuevo y lleva la garantía del fabricante (el plazo depende de la marca y el "
        "modelo; te lo indicamos en la factura o al consultar).\n\n"
        "Cómo hacerla válida\n"
        "Escribinos por WhatsApp o reportá la falla en crimsoncr.com con el número de factura y una descripción del "
        "problema. Nuestro equipo técnico diagnostica el equipo y gestiona el trámite con el fabricante.\n\n"
        "Qué no cubre\n"
        "Daños por golpes, humedad fuera de la especificación del equipo, descargas eléctricas sin protección, "
        "manipulación por terceros o instalación que no siga las indicaciones del fabricante.\n\n"
        "Instalación por Crimson\n"
        "Si nosotros instalamos, la mano de obra tiene su propia garantía y el soporte es directo con el técnico que "
        "conoce tu sistema."
    ),
}

DEFAULT_FAQ = [
    {"q": "¿Los precios incluyen IVA?", "a": "Sí. Todos los precios de la tienda incluyen el IVA. En la ficha de cada producto ves el desglose."},
    {
        "q": "¿Emiten factura electrónica?",
        "a": "Sí, toda compra lleva factura o tiquete electrónico de Hacienda. Si necesitás factura a nombre de una empresa, indicá la cédula jurídica al comprar.",
    },
    {
        "q": "¿Ustedes instalan?",
        "a": "Sí. Podés comprar solo el equipo o pedir que Crimson lo instale. Usá “Solicitar instalación” en la ficha o el formulario de asesoría y te cotizamos la mano de obra.",
    },
    {
        "q": "¿Qué significa “bajo pedido”?",
        "a": "Lo traemos del distribuidor para vos. Normalmente tarda de 3 a 5 días hábiles; te avisamos la fecha de entrega en cuanto confirmamos tu pago.",
    },
    {
        "q": "¿Puedo ver las cámaras desde el celular?",
        "a": "Sí. Los grabadores y cámaras IP que vendemos se ven desde la app del fabricante (por ejemplo Hik-Connect). Solo necesitás internet en el sitio.",
    },
    {"q": "¿Cómo pago?", "a": "Por transferencia o SINPE Móvil. Al confirmar el pedido te mostramos los datos y despachamos al recibir el comprobante."},
]

INFO_KINDS = {
    "terminos": "Términos y condiciones",
    "privacidad": "Política de privacidad",
    "envios": "Envíos y devoluciones",
    "garantias": "Garantías",
    "preguntas-frecuentes": "Preguntas frecuentes",
}

SOLUTIONS = {
    "cctv": "Videovigilancia",
    "acceso": "Control de acceso",
    "redes": "Redes y WiFi",
    "ups": "Respaldo eléctrico (UPS)",
    "cableado": "Cableado estructurado",
    "asistencia": "Control de asistencia",
    "anpr": "Lectura de placas",
    "integral": "Proyecto integral",
    "otro": "Otro",
}
# mismas opciones que el formulario de la landing (crimsoncr.com#contacto)
ENVIRONMENTS = {"casa": "Casa", "negocio": "Negocio", "condominio": "Condominio", "oficina": "Oficina", "bodega": "Bodega o planta", "otro": "Otro"}
TIMELINES = {"inmediato": "Inmediato", "semanas": "Próximas semanas", "evaluacion": "En evaluación", "futuro": "A futuro"}


def showcase_cfg(store: dict) -> dict:
    """Configuracion de la vitrina con sus valores por defecto (lo guardado manda)."""
    sc = dict(store.get("showcase") or {})
    return {
        "banners": sc.get("banners") or [],
        "featured": [int(x) for x in (sc.get("featured") or []) if str(x).isdigit()],
        "kits": sc.get("kits") if sc.get("kits") is not None else DEFAULT_KITS,
        "trust": sc.get("trust") or DEFAULT_TRUST,
        "pages": {**DEFAULT_PAGES, **{k: v for k, v in (sc.get("pages") or {}).items() if v}},
        "faq": sc.get("faq") or DEFAULT_FAQ,
        "headline": sc.get("headline") or "",
        "subline": sc.get("subline") or "",
        "seo_description": sc.get("seo_description") or "",
        # EX-NOGA: sin Google Analytics; un ga4_id viejo guardado en los ajustes se ignora al leer
    }


# ---------------------------------------------------------------- portada honesta (O1-04)
BANNER_PLACEHOLDERS = ("", "nuevo banner")  # titulos de relleno: el que traia el boton «+ Banner» y el vacio
BANNER_OFF_WARNING = "Se desactivó un banner sin imagen o sin título."


def banner_incomplete(b: dict) -> bool:
    """sin imagen, o con el titulo vacio o de relleno: no se publica"""
    return not str(b.get("image") or "").strip() or fold(str(b.get("title") or "").strip()) in BANNER_PLACEHOLDERS


def internal_link(link: str, domain: str | None) -> str:
    """un enlace al propio dominio de la tienda (https://tienda.ejemplo.com/c/x) se trata como ruta interna (/c/x)"""
    dom = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not dom or not link:
        return link
    m = re.match(r"^https?://(?:www\.)?([^/?#]+)(.*)$", link.strip(), re.I)
    if not m or m.group(1).lower() not in (dom, dom.removeprefix("www.")):
        return link
    rest = m.group(2) or "/"
    return rest if rest.startswith("/") else f"/{rest}"


def public_banners(banners: list[dict], domain: str | None) -> list[dict]:
    """los banners que la portada y el snapshot pintan: activos, con imagen y con titulo de verdad, en su orden"""
    out = [{**b, "link": internal_link(str(b.get("link") or ""), domain)} for b in banners or [] if b.get("active", True) and not banner_incomplete(b)]
    return sorted(out, key=lambda b: b.get("position", 0))


# un horario escrito: dias o una hora («lunes a viernes», «8 a. m.», «8:00», «17 h»)
_HOURS_RX = re.compile(r"\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo|l-v|24/7)\b|\b\d{1,2}(:\d{2})?\s?(a\.?\s?m|p\.?\s?m|h\b)")


def public_trust(trust: list[dict], hours: str | None) -> list[dict]:
    """Sellos con dato verificable (O1-04 k): «Soporte técnico» solo con horario; si el sello no lo dice y el comercio
    tiene horario (Mi Tienda -> Datos del comercio), se le agrega; sin horario, no se publica."""
    hours = (hours or "").strip()
    out = []
    for s in trust or []:
        if fold(s.get("title")).startswith("soporte") and not _HOURS_RX.search(fold(s.get("text"))):
            if not hours:
                continue
            s = {**s, "text": f"{str(s.get('text') or '').rstrip('. ')}. Horario: {hours.rstrip('.')}."}
        out.append(s)
    return out


METHOD_FAQ = {"tarjeta": "tarjeta de crédito o débito", "sinpe": "SINPE Móvil", "transferencia": "transferencia", "efectivo": "efectivo"}


def pay_answer(methods: list[dict]) -> str | None:
    """«Con tarjeta de crédito o débito, SINPE Móvil o transferencia.» desde los metodos activos del checkout"""
    order = ("tarjeta", "sinpe", "transferencia", "efectivo")
    names = []
    for code in order:
        m = next((x for x in methods if x.get("code") == code), None)
        if m:
            names.append(METHOD_FAQ[code] + (" al retirar" if code == "efectivo" and m.get("pickup_only") else ""))
    if not names:
        return None
    return "Con " + (names[0] if len(names) == 1 else f"{', '.join(names[:-1])} o {names[-1]}") + "."


def public_faq(faq: list[dict], methods: list[dict]) -> list[dict]:
    """«¿Cómo pago?» sale de los metodos activos (O1-04 l): nunca promete uno apagado. Sin metodos, no se publica."""
    ans = pay_answer(methods)
    out = []
    for f in faq or []:
        if re.sub(r"[^a-z ]", "", fold(f.get("q"))).strip() == "como pago":
            if not ans:
                continue
            f = {**f, "a": ans}
        out.append(f)
    return out


# ---------------------------------------------------------------- texto
_FOLD_MEMO_MAX = 200  # nombres, codigos y modelos; un texto largo (una descripcion) no se guarda


@functools.lru_cache(maxsize=16384)
def _fold_short(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def fold(s: object) -> str:
    """minusculas sin tildes. El armador de kits clasifica cada producto por su nombre varias veces por peticion: los
    textos cortos se recuerdan (solo dependen del texto)."""
    t = str(s or "")
    if len(t) > _FOLD_MEMO_MAX:
        return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return _fold_short(t)


def compact(s: object) -> str:
    """solo letras y numeros: 'DS-2CD 1043' -> 'ds2cd1043' (los modelos se escriben de mil maneras)"""
    return re.sub(r"[^a-z0-9]+", "", fold(s))


def slugify(s: object, limit: int = 70) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")
    if len(t) > limit:
        t = t[:limit].rsplit("-", 1)[0] or t[:limit]
    return t or "producto"


def product_path(p: Product) -> str:
    return f"/p/{slugify(p.name)}-{p.id}"


def product_id_from_ref(ref: str) -> int | None:
    m = re.search(r"(?:^|-)(\d{1,9})$", ref or "")
    return int(m.group(1)) if m else None


def category_slugs(cats: list) -> dict[int, str]:
    """slug por categoria; si dos se llaman igual, la segunda lleva el id"""
    out, seen = {}, set()
    for c in sorted(cats, key=lambda c: c.id):
        s = slugify(c.name, 60)
        if s in seen:
            s = f"{s}-{c.id}"
        seen.add(s)
        out[c.id] = s
    return out


# ---------------------------------------------------------------- especificaciones deducidas
TYPE_RULES = (
    # (tipo, palabras que deben aparecer en el nombre, palabras que lo descartan)
    ("Videoportero", ("videoportero", "intercom", "portero"), ()),
    ("Grabador NVR", ("nvr",), ()),
    ("Grabador DVR", ("dvr",), ()),
    ("Grabador NVR", ("grabador", "grabadora"), ("dvr", "analog")),
    ("Disco duro", ("disco duro", "hdd", "disco "), ("externo",)),
    ("Disco externo", ("disco duro externo", "disco externo"), ()),
    # la memoria es la tarjeta, no un aparato que la usa ("parlante bluetooth / micro SD", "camara wifi microsd")
    (
        "Memoria microSD",
        ("memoria micro", "memoria sd", "tarjeta micro", "tarjeta de memoria", "tarjeta sd"),
        ("parlante", "bocina", "camara", "camera", "audifono", "radio", "reproductor", "grabador", "lector", "dashcam"),
    ),
    ("Cámara PTZ", ("ptz",), ()),
    ("Cámara WiFi", ("wifi", "wi-fi", "inalambric"), ("switch", "router", "punto de acceso", "access point", "nvr", "grabador", "teclado", "alarma")),
    ("Cámara IP", ("camara", "camera", "bullet", "domo", "dome", "turret"), ("dashcam", "burbuja", "soporte", "caja", "brazo", "montaje")),
    ("Dashcam", ("dashcam",), ()),
    ("Switch PoE", ("switch",), ()),
    ("Punto de acceso WiFi", ("punto de acceso", "access point", "ubiwifi", "u6", "u7"), ()),
    ("Router", ("router", "gateway"), ()),
    ("Inyector PoE", ("inyector",), ()),
    ("UPS", ("ups", "back-ups"), ()),
    ("Control de acceso", ("control de acceso", "lector", "reconocimiento facial", "terminal"), ()),
    ("Cerradura", ("cerradura", "electroiman", "chapa"), ()),
    ("Alarma", ("alarma", "panico", "detector", "sensor", "sirena", "teclado"), ()),
    ("Gabinete", ("gabinete", "rack"), ()),
    ("Cable", ("cable", "bobina", "utp"), ()),
    ("Conector", ("conector", "rj45", "rj-45"), ()),
)


def product_type(p: Product) -> str:
    return _type_of(p.name or "")


@functools.lru_cache(maxsize=16384)
def _type_of(name: str) -> str:
    """El tipo sale solo del nombre: se recuerda por nombre (el armador de kits lo pide por cada pieza y producto)."""
    n = " " + fold(name) + " "
    # "Memoria micro SDXC 64GB ... para camaras": lo que manda es como empieza el nombre
    if n.lstrip().startswith(("memoria", "tarjeta")) and any(w in n for w in ("sd", "micro")):
        return "Memoria microSD"
    for label, must, never in TYPE_RULES:
        if any(w in n for w in must) and not any(w in n for w in never):
            if label == "Switch PoE" and "poe" not in n:
                return "Switch"
            if label == "Cámara IP" and ("turbo" in n or "analog" in n or "hd-tvi" in n):
                return "Cámara analógica"
            return label
    return "Accesorio" if fold(name).startswith(("soporte", "caja", "brazo", "base", "tornillo", "abrazadera")) else ""


_RX = {
    "mp": re.compile(r"(\d{1,2}(?:[.,]\d)?)\s?mp\b"),
    "k": re.compile(r"\b([2-8])k\b"),
    "p": re.compile(r"\b(1080|720|2160)\s?p\b"),
    "lens_range": re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s?(?:mm)?\s?(?:a|-|~|to)\s?(\d{1,2}(?:[.,]\d{1,2})?)\s?mm"),
    "lens": re.compile(r"(\d{1,2}[.,]\d{1,2}|\d)\s?mm\b"),
    "ir": re.compile(
        r"\b(?:ir|exir|infrarrojo|vision nocturna)\b[^0-9]{0,18}(\d{2,3})\s?(?:m|mts|metros)\b|(\d{2,3})\s?(?:m|mts|metros)\s?(?:de\s)?(?:ir|infrarrojo)"
    ),
    "ip": re.compile(r"\bip\s?(6[5-8])\b"),
    "ik": re.compile(r"\bik\s?(0[6-9]|10)\b"),
    "ch": re.compile(r"(\d{1,2})\s?(?:-\s?)?(?:ch|canales|canal)\b"),
    "tb": re.compile(r"(\d{1,2})\s?tb\b"),
    "gb": re.compile(r"(\d{2,4})\s?gb\b"),
    "ports": re.compile(r"(\d{1,2})\s?(?:puertos|ptos|port|-port|p\b)"),
    "va": re.compile(r"(\d{3,4})\s?va\b"),
    "zoom": re.compile(r"(\d{1,2})\s?x\b"),
}


def _num(s: str) -> str:
    return s.replace(",", ".")


# valores de relleno de las fichas del proveedor («Micrófono: No indicado»): no son un dato
NO_DATA = re.compile(r"[^\n;|]*\bno (?:indicado|aplica|especificado)\b[^\n;|]*")


def _no_data(v: object) -> bool:
    return bool(NO_DATA.fullmatch(fold(v).strip().rstrip(".")))


def derive_specs(p: Product, shown: str | None = None) -> dict[str, str]:
    """Especificaciones legibles a partir del nombre y la descripcion (Fygaro no las tenia en campos).
    Las que Andres edite a mano (product.specs) mandan sobre las deducidas.
    shown (O1-08): el tipo que muestra la tienda (product_typing.display_type). Con el, la ficha dice ese tipo, sin
    camara no deduce lente, vision nocturna ni microfono, y se ignoran los «No indicado / No aplica / No especificado».
    Sin el (armador de kits, equipment_reco) todo queda como siempre, con el tipo funcional."""
    name = fold(p.name)
    desc = fold(p.description_store or p.description_invoice or "")[:1500]
    if shown is not None:
        desc = NO_DATA.sub(" ", desc)
    text = name + " \n " + desc
    t = product_type(p) if shown is None else shown
    out: dict[str, str] = {}
    if t:
        out["Tipo"] = t
    if p.brand:
        out["Marca"] = p.brand
    if p.model:
        out["Modelo"] = p.model
    cam = t.startswith("Cámara") or t in ("Videoportero", "Dashcam")
    rec = t.startswith("Grabador")
    if cam or rec:
        m = _RX["mp"].search(name) or _RX["mp"].search(text)
        if m:
            out["Resolución"] = f"{_num(m.group(1))} MP"
        elif m := _RX["k"].search(text):
            out["Resolución"] = f"{m.group(1)}K"
        elif m := _RX["p"].search(text):
            out["Resolución"] = {"1080": "2 MP (1080p)", "720": "1 MP (720p)", "2160": "8 MP (4K)"}[m.group(1)]
    if cam:
        m = _RX["lens_range"].search(text)
        if m:
            out["Lente"] = f"{_num(m.group(1))}–{_num(m.group(2))} mm varifocal"
        elif m := _RX["lens"].search(text):
            out["Lente"] = f"{_num(m.group(1))} mm"
        m = _RX["ir"].search(text)
        if m:
            out["Visión nocturna"] = f"IR hasta {m.group(1) or m.group(2)} m"
        elif "colorvu" in text or "color nocturn" in text or "vision nocturna a color" in text:
            out["Visión nocturna"] = "A color"
        elif "smart hybrid" in text or "luz hibrida" in text:
            out["Visión nocturna"] = "Luz híbrida inteligente"
        elif "vision nocturna" in text or " ir " in text:
            out["Visión nocturna"] = "Sí"
        if t == "Cámara PTZ" and (m := _RX["zoom"].search(text)):
            out["Zoom óptico"] = f"{m.group(1)}×"
        if "bullet" in text:
            out["Formato"] = "Bullet"
        elif "turret" in text or "eyeball" in text:
            out["Formato"] = "Turret"
        elif "domo" in text or "dome" in text:
            out["Formato"] = "Domo"
        if "acusense" in text or "deteccion humana" in text or "deteccion de personas" in text or " ia" in text:
            out["Analítica"] = "Detección de personas y vehículos"
        if "microfono" in text or "audio" in text:
            out["Audio"] = "Micrófono integrado"
    m = _RX["ip"].search(text)
    if m:
        out["Protección"] = f"IP{m.group(1)}" + (f" / IK{_RX['ik'].search(text).group(1)}" if _RX["ik"].search(text) else "")
    if "poe" in text and t not in ("UPS",):
        out["PoE"] = "Sí"
    elif cam and ("12 vdc" in text or "12v" in text):
        out["Alimentación"] = "12 VDC"
    if "wifi" in text or "wi-fi" in text:
        out["Conexión"] = "WiFi"
    if rec and (m := _RX["ch"].search(text)):
        out["Canales"] = m.group(1)
    if t in ("Disco duro", "Disco externo", "Memoria microSD"):
        m = _RX["tb"].search(text)
        if m:
            out["Capacidad"] = f"{m.group(1)} TB"
        elif m := _RX["gb"].search(text):
            out["Capacidad"] = f"{m.group(1)} GB"
    if t.startswith("Switch") and (m := _RX["ports"].search(name)):
        out["Puertos"] = m.group(1)
    if t == "UPS" and (m := _RX["va"].search(text)):
        out["Capacidad"] = f"{m.group(1)} VA"
    for k, v in (p.specs or {}).items():
        if str(k).strip() and not (shown is not None and _no_data(v)):
            out[str(k).strip()[:40]] = str(v).strip()[:120]
    return out


def capacity_tb(p: Product) -> Decimal | None:
    t = fold(p.name)
    m = _RX["tb"].search(t)
    return Decimal(m.group(1)) if m else None


def channels(p: Product) -> int | None:
    m = _RX["ch"].search(fold(p.name)) or re.search(r"\b(4|8|16|32|64)\s?(?:camaras)", fold(p.name))
    if m:
        return int(m.group(1))
    m = re.search(r"7(?:1|6)(04|08|16|32)n", compact(p.code) + compact(p.model or ""))  # DS-7608NI -> 8 canales
    return int(m.group(1)) if m else None


def ports(p: Product) -> int | None:
    m = _RX["ports"].search(fold(p.name))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- busqueda tolerante (regla unica: store_search, O1-14)
def search_text(p: Product, cat_name: str | None, ptype: str | None = None) -> str:
    """campo de busqueda del producto (el mismo sf del snapshot): nombre, marca, modelo, codigo, SKU, categoria y tipo.
    ptype: el tipo que muestra la tienda (O1-08); sin el, el funcional."""
    from . import store_search as ss

    return ss.search_field(p.name, p.brand, p.model, p.code, p.supplier_sku, cat_name, product_type(p) if ptype is None else ptype)


def search_score(p: Product, cat_name: str | None, terms: list[str], hay: str | None = None, ptype: str | None = None) -> int:
    """0 = no coincide. La regla vive en services/store_search (la misma del navegador)."""
    from . import store_search as ss

    return ss.score(" ".join(terms), name=p.name, brand=p.brand, model=p.model, code=p.code, category=cat_name, sf=hay or search_text(p, cat_name, ptype))


def split_terms(q: str) -> list[str]:
    from . import store_search as ss

    return ss.split_query(q)


# ---------------------------------------------------------------- configurador "Arma tu sistema"
PLACES = {
    "casa": {"label": "Casa", "meters": 20},
    "negocio": {"label": "Negocio u oficina", "meters": 30},
    "condominio": {"label": "Condominio", "meters": 40},
    "bodega": {"label": "Bodega o planta", "meters": 45},
}
GB_PER_CAMERA_DAY = Decimal(21)  # 4 MP H.265+ continuo ~2 Mbps -> ~21 GB por dia (por movimiento rinde 2-3x)
# Calidad de imagen que pide el cliente: MP objetivo de las camaras y GB por camara al dia en H.265+ continuo
# (2 MP ~1 Mbps, 4 MP ~2 Mbps, 8 MP/4K ~4 Mbps).
QUALITIES = {
    "2mp": {"label": "2 MP (Full HD)", "mp": 2, "gb": Decimal(11)},
    "4mp": {"label": "4 MP (2K)", "mp": 4, "gb": GB_PER_CAMERA_DAY},
    "8mp": {"label": "8 MP (4K)", "mp": 8, "gb": Decimal(42)},
}
MOTION_FACTOR = Decimal("0.4")  # grabando solo con movimiento el disco rinde 2 a 3 veces mas: se cuenta el 40 %
# Distancia de la camara mas lejana al grabador (o switch). "media" usa la tipica del lugar (PLACES).
DISTANCES = {"corta": 15, "media": None, "larga": 70}
POE_MAX_M = 100  # tope de un tramo de cable de red con PoE
# Consumo para dimensionar la UPS: camara IP con IR ~7 W, grabador ~15 W, switch PoE ~10 W propios
CAM_W, NVR_W, SWITCH_W = 7, 15, 10
UPS_POWER_FACTOR = Decimal("0.6")  # una UPS de linea interactiva entrega ~60 % de sus VA en W
UPS_MARGIN = Decimal("1.3")  # la UPS no trabaja al tope: 30 % de holgura sobre el consumo
# Lo que se lee del nombre de una camara para las preferencias del cliente
COLOR_WORDS = ("colorvu", "full color", "a color", "color 24", "luz blanca", "smart hybrid", "hybrid light", "dual light", "luz hibrida", "color nocturn")
AUDIO_WORDS = ("audio", "microfono", " mic ", "bidireccional")
SMART_WORDS = ("acusense", "deteccion humana", "deteccion de personas", "humano y vehiculo", "personas y vehiculos", "vigi insight")
# Marcas: el grabador de la misma marca que las camaras aprovecha sus funciones (AcuSense, ColorVu) y una sola app
BRANDS = (
    ("hikvision", "hikvision"),
    ("hilook", "hikvision"),
    ("ezviz", "ezviz"),
    ("vigi", "tp-link"),
    ("tp link", "tp-link"),
    ("tp-link", "tp-link"),
    ("dahua", "dahua"),
    ("imou", "dahua"),
    ("uniview", "uniview"),
)
BRAND_LABELS = {"hikvision": "Hikvision", "ezviz": "Ezviz", "tp-link": "TP-Link", "dahua": "Dahua", "uniview": "Uniview"}


def _priced(prods: list[Product]) -> list[Product]:
    return [p for p in prods if _price(p) > 0]


# Cada pieza del kit tiene una llave estable: los kits de Mi Tienda fijan productos por llave (pins), el cliente cambia
# la sugerencia por llave (choices) y la direccion del configurador la guarda por llave (?nvr=123).
KIT_ROLES = {
    "cam_out": "Cámaras exteriores",
    "cam_in": "Cámaras interiores",
    "nvr": "Grabador",
    "disk": "Almacenamiento",
    "switch": "Red",
    "rj45": "Instalación",
    "cable": "Instalación",
    "box": "Instalación",
    "ups": "Respaldo eléctrico",
}
# como se llama cada pieza en Mi Tienda (el rotulo del cliente repite "Instalación" en tres)
KIT_ROLE_NAMES = {
    "cam_out": "Cámaras exteriores",
    "cam_in": "Cámaras interiores",
    "nvr": "Grabador (NVR)",
    "disk": "Disco duro",
    "switch": "Switch PoE",
    "rj45": "Conectores RJ45",
    "cable": "Cable UTP",
    "box": "Caja de paso exterior",
    "ups": "UPS (respaldo eléctrico)",
}
EXTRA_ROLE = "Adicional"
MAX_KIT_EXTRAS = 6
MAX_KIT_ALTERNATIVES = 4
# Variedad (pedido de Andres: "que los articulos varien, no siempre los mismos"): entre los candidatos que calzan IGUAL
# de bien (mismo nivel de ajuste) y cuestan hasta 18 % mas que el mejor, la sugerencia automatica rota con una semilla
# que cambia cada dia de Costa Rica. Ver _pick.
VARIETY_BAND = Decimal("1.18")
SURVEILLANCE_WORDS = ("purple", "skyhawk", "vigilancia", "vx")
NOT_HERE = "No está disponible en la tienda: está inactivo, sin publicar en la web u oculto por agotado."


def _list_price(p: Product) -> Decimal:
    """Precio base de la tienda en la moneda del producto: el precio web si lo tiene; si no, el de lista (la misma
    regla que services/store_inventory.web_price)."""
    v = p.web_price if getattr(p, "web_price", None) is not None else p.price
    return Decimal(str(v or 0))


def _price(p: Product) -> Decimal:
    """Precio con el que se comparan los productos cuando build_kit no recibe `price_of` (pruebas, scripts): el precio
    web en la moneda del producto; 0 = "a consultar" si el guardian de margen lo saco de venta. Los endpoints pasan el
    precio que ve el cliente (routers/store_showcase._kit_price), que ademas convierte la moneda."""
    return Decimal(0) if getattr(p, "margin_hold", False) else _list_price(p)


def _price_problem(p: Product, c: dict) -> str | None:
    """Por que un producto no tiene precio HOY en la tienda (None = si tiene). Uno "a consultar" no entra a un kit:
    ni como sugerencia, ni fijado, ni como adicional (bajaria el total de la tarjeta y no se podria cambiar)."""
    if _list_price(p) <= 0:
        return "No tiene precio."
    if getattr(p, "margin_hold", False):
        return "Está fuera de venta por margen (precio a consultar)."
    if c["price_of"](p) <= 0:
        return "Hoy no tiene precio en la tienda (falta el tipo de cambio de su moneda)."
    return None


def _mp(p: Product) -> float:
    m = _RX["mp"].search(fold(p.name))
    return float(_num(m.group(1))) if m else 0.0


_NVR_PORTS = re.compile(r"/(\d{1,2})p\b")


def nvr_poe(p: Product | None) -> bool:
    """Grabador con switch PoE integrado (/8P, /16P o "switch PoE" en el nombre)."""
    if p is None:
        return False
    return bool(_NVR_PORTS.search(fold(p.code + " " + (p.model or "") + " " + p.name))) or (" poe" in fold(p.name) and "switch poe" in fold(p.name))


def nvr_poe_ports(p: Product | None) -> int | None:
    """Cuantos puertos PoE trae el grabador (el NN de "/NNP"); None si no trae o si el nombre no dice cuantos."""
    if p is None:
        return None
    m = _NVR_PORTS.search(fold(p.code + " " + (p.model or "") + " " + p.name))
    return int(m.group(1)) if m else None


def nvr_poe_covers(p: Product | None, cameras: int) -> bool:
    """El PoE del grabador alimenta TODAS las camaras del kit (ahorra el switch aparte). Un 32 canales con 16 puertos
    PoE ("/16P") no alcanza para 20 camaras: ese kit lleva switch. Sin el dato de puertos se asume que alcanza."""
    if not nvr_poe(p):
        return False
    n = nvr_poe_ports(p)
    return n is None or n >= cameras


def brand_of(p: Product | None) -> str | None:
    """Marca del producto (de su campo o del nombre), normalizada: HiLook cuenta como Hikvision, VIGI como TP-Link."""
    if p is None:
        return None
    hay = " " + fold((p.brand or "") + " " + p.name) + " "
    return next((b for w, b in BRANDS if w in hay), None)


def cam_color(p: Product) -> bool:
    """Imagen a color de noche (ColorVu, full color o luz hibrida/blanca que se enciende con movimiento)."""
    return any(w in " " + fold(p.name) + " " for w in COLOR_WORDS)


def cam_audio(p: Product) -> bool:
    return any(w in " " + fold(p.name) + " " for w in AUDIO_WORDS)


def cam_smart(p: Product) -> bool:
    return any(w in fold(p.name) for w in SMART_WORDS)


_BAYS = re.compile(r"(\d{1,2})\s?(?:x\s?)?(?:sata|hdd|bahias?|discos?)\b")
_MAX_TB = re.compile(r"hasta\s?(\d{1,3})\s?tb\b")


def nvr_bays(p: Product | None) -> int | None:
    """Cuantos discos le caben al grabador ("2 SATA", "4 HDD (no incluidos)"); None si el nombre no lo dice."""
    if p is None:
        return None
    m = _BAYS.search(fold(p.name))
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 16 else None


def nvr_max_tb(p: Product | None) -> int | None:
    """Capacidad total que admite el grabador ("hasta 20 TB"); None si el nombre no lo dice."""
    if p is None:
        return None
    m = _MAX_TB.search(fold(p.name))
    return int(m.group(1)) if m else None


def nvr_smart(p: Product | None) -> bool:
    """Grabador con analitica (AcuSense: la serie NXI de Hikvision o "AcuSense" en el nombre)."""
    if p is None:
        return False
    n = fold((p.code or "") + " " + (p.model or "") + " " + p.name)
    return "acusense" in n or bool(re.search(r"\bds-7\d{3}nxi", n))


def ups_va(p: Product) -> int | None:
    m = _RX["va"].search(fold(p.name))
    return int(m.group(1)) if m else None


_UPS_W = re.compile(r"(\d{3,4})\s?w\b")


def ups_w(p: Product) -> int | None:
    """Watts de la UPS: los del nombre ("600VA 330W") o, si no los dice, ~60 % de sus VA."""
    m = _UPS_W.search(fold(p.name))
    if m:
        return int(m.group(1))
    va = ups_va(p)
    return int(va * UPS_POWER_FACTOR) if va else None


def channel_size(n: int) -> int:
    """Canales de grabador para n camaras con espacio para crecer: al menos la mitad mas (minimo 2 libres)."""
    want = n + max(2, math.ceil(n / 2))
    return next((s for s in (4, 8, 16, 32, 64) if s >= want), want)


def disk_qty(p: Product, need_tb: Decimal) -> int:
    """Cuantos discos de esta capacidad hacen falta: uno si alcanza, si no los que sumen lo necesario."""
    c = capacity_tb(p) or Decimal(0)
    if c <= 0:
        return 1
    return 1 if c >= need_tb else max(1, math.ceil(need_tb / c))


_LEN = re.compile(r"\b(\d{2,4})\s?(?:m|mts|metros)\b")


def reel_len(p: Product) -> int | None:
    """Metros de una bobina ("Cable UTP Cat6 305m", "Bobina UTP"); None si el cable se vende por metro."""
    n = fold(p.name)
    if "bobina" not in n and "305" not in n:
        return None
    m = _LEN.search(n)
    return int(m.group(1)) if m and int(m.group(1)) >= 50 else 305


def _by_words(prods: list[Product], words: tuple[str, ...], never: tuple[str, ...] = ()) -> list[Product]:
    return [p for p in prods if all(w in fold(p.name) for w in words) and not any(w in fold(p.name) for w in never)]


def _cam_ok(p: Product, outdoor: bool) -> bool:
    """Las camaras que el armador propone (y entre las que el cliente puede cambiar): IP con PoE, fijas; afuera, nada
    que diga que es de interior."""
    if product_type(p) != "Cámara IP":  # primero lo barato: la descripcion solo se revisa en las camaras
        return False
    n = fold(p.name)
    if not ("poe" in n or "acusense" in n or "poe" in fold(p.description_store or "")):
        return False
    if any(w in n for w in ("ptz", "wifi", "panoram", "fisheye", "lpr", "anpr", "termic", "kit")):
        return False
    return not (outdoor and any(w in n for w in ("interior", "indoor")))


def cam_misses(p: Product, c: dict) -> list[str]:
    """Lo que el cliente pidio y esta camara no trae (color de noche, audio, deteccion de personas)."""
    out = []
    if c.get("color") and not cam_color(p):
        out.append("color")
    if c.get("audio") and not cam_audio(p):
        out.append("audio")
    if c.get("smart") and not cam_smart(p):
        out.append("smart")
    return out


def _cam_tier(p: Product, outdoor: bool, c: dict | None = None) -> tuple:
    """Nivel de ajuste de una camara (menor = calza mejor): formato del lugar (bullet afuera, domo/turret adentro),
    cuantas preferencias del cliente no cumple y que tan lejos esta de la resolucion pedida (sin dato: al final)."""
    c = c or {}
    n = fold(p.name)
    fmt_ok = ("bullet" in n) if outdoor else any(w in n for w in ("domo", "dome", "turret", "eyeball"))
    mp = _mp(p)
    target = c.get("mp", 4)
    # una camara de menos MP que lo pedido calza peor que una de mas (8 MP pedidos: una de 6 antes que una de 4)
    gap = (abs(mp - target) + (2 if mp < target else 0)) if mp else 9
    return (not fmt_ok, len(cam_misses(p, c)), gap)


def _role_scan(key: str, prods: list[Product], c: dict) -> list[tuple[tuple, Product]]:
    """UNA pasada por el catalogo: todo lo que sirve para la pieza `key` y tiene precio hoy, con el dato que hace falta
    para ordenarlo. Es la parte cara (clasifica cada producto por su nombre) y no depende de con que se compare: por eso
    los candidatos de "lo que se puede comprar" y los de todo el catalogo salen de la misma pasada (_role_rank)."""
    price = c["price_of"]  # 0 = "a consultar" (retenido por margen, sin tipo de cambio): nunca es candidato
    if key in ("cam_out", "cam_in"):
        out = key == "cam_out"
        return [(_cam_tier(p, out, c), p) for p in prods if _cam_ok(p, out) and price(p) > 0]
    if key == "nvr":
        # NVR con canales suficientes (con espacio para crecer si el cliente lo pidio). Orden de ajuste:
        # 1. que le quepa el almacenamiento (bahias x el disco mas grande de la tienda y su tope "hasta N TB");
        # 2. la misma marca que las camaras (funciones inteligentes y una sola app);
        # 3. el de menos canales; 4. con analitica si se pidio deteccion de personas;
        # 5. el que alimenta todas las camaras por PoE (ahorra el switch).
        return [
            (
                (
                    not nvr_storage_ok(p, c),
                    nvr_brand_miss(p, c),
                    channels(p),
                    bool(c.get("smart")) and not nvr_smart(p),
                    not nvr_poe_covers(p, c["n"]),
                ),
                p,
            )
            for p in prods
            if product_type(p) == "Grabador NVR"
            and "wifi" not in fold(p.name)
            and "inalambric" not in fold(p.name)
            and "kit" not in fold(p.name).split()[:3]
            and (channels(p) or 0) >= c["need_ch"]
            and price(p) > 0
        ]
    if key == "disk":
        return [
            ((any(w in fold(p.name) for w in SURVEILLANCE_WORDS),), p) for p in prods if product_type(p) == "Disco duro" and capacity_tb(p) and price(p) > 0
        ]
    if key == "switch":
        return [((ports(p),), p) for p in prods if product_type(p) == "Switch PoE" and c["need_ports"] <= (ports(p) or 0) <= 24 and price(p) > 0]
    if key == "rj45":
        found = []
        for p in prods:
            n = fold(p.name)
            if "rj45" not in n or any(w in n for w in RJ45_NO):
                continue
            # nivel 0: dice "conector"; nivel 1: cualquier RJ45 que no sea un switch, un modulo o una ficha
            lvl = 0 if "conector" in n else (1 if not any(w in n for w in RJ45_LOOSE_NO) else None)
            if lvl is not None and _consumable(p) and price(p) > 0:
                found.append(((lvl,), p))
        return found
    if key == "cable":
        found = []
        for p in prods:
            n = fold(p.name)
            # nivel 0: bobina (o "cable ... 305"); nivel 1: cable UTP por metro
            if ("bobina" in n and "coaxial" not in n) or ("cable" in n and "305" in n):
                lvl = 0
            elif "cable" in n and "utp" in n and not any(w in n for w in ("patch", "hdmi", "coaxial")):
                lvl = 1
            else:
                continue
            if price(p) > 0:
                found.append(((lvl,), p))
        return found
    if key == "box":
        return [((0,), p) for p in _by_words(prods, ("caja", "exterior"), ("tapa", "fuerte", "carton")) if price(p) > 0]
    if key == "ups":
        # UPS con watts suficientes para la carga del sistema (la mas chica que alcanza); nunca una bateria suelta
        found = []
        for p in prods:
            if product_type(p) != "UPS" or fold(p.name).startswith("bateria") or price(p) <= 0:
                continue
            w = ups_w(p)
            if w and w >= c["ups_w"]:
                found.append(((w,), p))
        if found:  # solo las del tamano mas chico que alcanza (no una de 3000 VA para 4 camaras)
            low = min(t[0] for t, _ in found)
            found = [(t, p) for t, p in found if t[0] <= max(low * 2, c["ups_w"] * 2)]
        return found
    return []


def nvr_storage_ok(p: Product, c: dict) -> bool:
    """Al grabador le cabe el almacenamiento que pide el kit: sus bahias con el disco mas grande de la tienda y su
    tope ("hasta 20 TB"). Lo que el nombre no dice se da por bueno (lo confirma el tecnico)."""
    need = c["need_tb"]
    bays, top, biggest = nvr_bays(p), nvr_max_tb(p), c.get("max_disk_tb")
    if top is not None and top < need:
        return False
    return not (bays and biggest and bays * biggest < need)


def nvr_brand_miss(p: Product, c: dict) -> bool:
    want = c.get("brand")
    return bool(want) and brand_of(p) != want


def _role_rank(key: str, found: list[tuple[tuple, Product]], c: dict) -> list[tuple[tuple, Product]]:
    """Candidatos de la pieza con su nivel de ajuste (menor = calza mejor), a partir de lo que encontro _role_scan en
    un conjunto de productos (todo el catalogo o solo lo que se puede comprar). Aqui va lo que depende del conjunto:
    el formato de camara que hay, si hay discos de videovigilancia, bolsa o bobina."""
    if not found:
        return []
    if key in ("cam_out", "cam_in"):
        # si hay del formato que toca (bullet afuera, domo/turret adentro) solo esos: no se ofrece un domo para afuera
        fmt = min(t[0] for t, _ in found)
        return [(t, p) for t, p in found if t[0] == fmt]
    if key == "disk":
        surv = [p for t, p in found if t[0]] or [p for _, p in found]
        need = c["need_tb"]
        # el mas chico que alcanza solo; si ninguno alcanza, el mas grande (en varios). Nunca mas discos de los que le
        # caben al grabador elegido (sus bahias; sin el dato, 4) salvo que no haya otra forma.
        cap = max(c.get("bays") or 4, min(disk_qty(p, need) for p in surv))
        return [((0, capacity_tb(p)) if capacity_tb(p) >= need else (1, -capacity_tb(p)), p) for p in surv if disk_qty(p, need) <= cap]
    if key in ("rj45", "cable"):
        best = min(t[0] for t, _ in found)  # solo el mejor nivel que haya (conector antes que "RJ45"; bobina antes que metro)
        return [(t, p) for t, p in found if t[0] == best]
    return found


def _role_cands(key: str, pool: list[Product], c: dict) -> list[tuple[tuple, Product]]:
    """Todo lo del pool que sirve para la pieza `key`, con su nivel de ajuste (menor = calza mejor)."""
    return _role_rank(key, _role_scan(key, pool, c), c)


def _role_qty(key: str, p: Product, c: dict) -> int:
    """Cantidad de la pieza con ESTE producto: los discos por capacidad, los conectores por bolsa y el cable por bobina."""
    if key == "cam_out":
        return c["outdoor"]
    if key == "cam_in":
        return c["indoor"]
    if key == "disk":
        return disk_qty(p, c["need_tb"])
    if key == "rj45":
        return max(1, math.ceil(c["rj_qty"] / pack_size(p)))
    if key == "cable":
        n = reel_len(p)
        return max(1, math.ceil(c["cable_m"] / n)) if n else max(1, math.ceil(float(c["cable_m"])))
    if key == "box":
        return max(1, int(math.ceil(float(c["box_qty"]))))
    return 1


def _sub(key: str, p: Product, c: dict) -> Decimal:
    """Lo que cuesta la pieza con ESTE producto, en el precio con el que se compara (c["price_of"]: el que ve el cliente)."""
    return c["price_of"](p) * _role_qty(key, p, c)


def _seed_index(seed: str, n: int) -> int:
    """Indice estable para una semilla (sha256, no hash(): el de Python cambia en cada proceso)."""
    return int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16) % n if n > 1 else 0


def _pick(cands: list[tuple[tuple, Product]], key: str, c: dict, seed: str | None) -> Product | None:
    """Sugerencia automatica de una pieza.

    Regla de variedad: se ordenan los candidatos por (nivel de ajuste, subtotal de la pieza, id). Los "equivalentes" son
    los del MISMO nivel de ajuste que el mejor y con un subtotal de hasta VARIETY_BAND (18 %) sobre el mas barato de ese
    nivel. Entre ellos se elige con `seed` = dia de Costa Rica (ordinal) + llave de la pieza + respuestas, asi que:
    - las mismas respuestas dan el mismo kit todo el dia (la tarjeta de la portada y la pagina del kit cuadran);
    - de un dia a otro puede salir otro articulo equivalente (no siempre el mismo);
    - nunca se sube a un nivel de ajuste peor ni a algo bastante mas caro.
    Los productos fijados en Mi Tienda (pins) y los que eligio el cliente (choices) no pasan por aqui: no rotan."""
    if not cands:
        return None
    ordered = sorted(cands, key=lambda x: (x[0], _sub(key, x[1], c), x[1].id))
    tier0, best = ordered[0][0], _sub(key, ordered[0][1], c)
    eq = [p for t, p in ordered if t == tier0 and _sub(key, p, c) <= best * VARIETY_BAND]
    return eq[_seed_index(seed, len(eq))] if seed else eq[0]


def pin_type_problem(key: str, p: Product) -> str | None:
    """Lo que hace que un producto NUNCA sirva para la pieza (no depende de las respuestas ni del dia): se valida al
    guardar el kit en Mi Tienda."""
    t, n = product_type(p), fold(p.name)
    if key in ("cam_out", "cam_in"):
        if t not in ("Cámara IP", "Cámara PTZ"):
            return "No es una cámara IP."
        if "wifi" in n or "inalambric" in n:
            return "Es una cámara WiFi: el kit usa cámaras con cable de red (PoE)."
        if key == "cam_out" and any(w in n for w in ("interior", "indoor")):
            return "Es una cámara de interior."
    elif key == "nvr":
        if t != "Grabador NVR":
            return "No es un grabador NVR."
    elif key == "disk":
        if t != "Disco duro" or not capacity_tb(p):
            return "No es un disco duro con la capacidad en el nombre (por ejemplo «4TB»)."
    elif key == "switch":
        if t != "Switch PoE":
            return "No es un switch PoE."
    elif key == "rj45":
        if "rj45" not in compact(p.name):
            return "No es un conector RJ45."
    elif key == "cable":
        if t != "Cable":
            return "No es cable de red."
    elif key == "box":
        if "caja" not in n:
            return "No es una caja de paso."
    elif key == "ups":
        if t != "UPS" or not ups_w(p):
            return "No es una UPS con su capacidad en el nombre (por ejemplo «600VA»)."
    return None


def pin_problem(key: str, p: Product | None, c: dict) -> str | None:
    """Por que un producto fijado en Mi Tienda no se puede usar HOY en esta pieza (None = se usa). Si no se puede, el kit
    usa la sugerencia automatica y la vista previa de Mi Tienda muestra este motivo."""
    if p is None:
        return NOT_HERE
    prob = _price_problem(p, c) or pin_type_problem(key, p)
    if prob:
        return prob
    if key == "nvr":
        ch = channels(p)
        if not ch:
            return "No se sabe cuántos canales tiene (el nombre debería decirlo, por ejemplo «8CH»)."
        if ch < c["n"]:
            return f"Tiene {ch} canales y el kit lleva {c['n']} cámaras."
        if ch < c.get("need_ch", c["n"]):
            return f"Tiene {ch} canales y el cliente quiere espacio para {c['need_ch']} cámaras."
    if key == "switch" and ports(p) and ports(p) < c["need_ports"]:
        return f"Tiene {ports(p)} puertos y hacen falta {c['need_ports']}."
    if key == "rj45" and _consumable(p) is None:
        return "Cuesta demasiado por unidad para un conector (se cotiza aparte)."
    if key == "ups" and (ups_w(p) or 0) < c.get("ups_w", 0):
        return f"Da {ups_w(p)} W y el sistema necesita unos {c['ups_w']} W."
    return None


def _spec_score(key: str, p: Product, c: dict) -> tuple:
    """Que tan "superior" es un producto dentro de su pieza (para rotular las alternativas)."""
    if key in ("cam_out", "cam_in"):
        return (_mp(p), cam_smart(p) + cam_color(p) + cam_audio(p))
    if key == "nvr":
        return (channels(p) or 0, nvr_smart(p), nvr_poe_covers(p, c["n"]))
    if key == "ups":
        return (ups_w(p) or 0,)
    if key == "disk":
        return (capacity_tb(p) or 0,)
    if key == "switch":
        return (ports(p) or 0,)
    return (0,)


def _fmt_tb(v: Decimal | None) -> str:
    if v is None:
        return ""
    return f"{int(v)}" if v == int(v) else f"{v.normalize()}"


def key_specs(p: Product) -> list[str]:
    """2 a 4 datos clave para la linea del kit: MP y formato en camaras, canales y PoE en grabadores, TB en discos,
    puertos en switches (de los mismos helpers que la ficha)."""
    t = product_type(p)
    d = derive_specs(p)
    out: list[str] = []
    if t.startswith("Cámara"):
        out = [d[k] for k in ("Resolución", "Formato", "Visión nocturna") if d.get(k)]
        if cam_color(p) and not d.get("Visión nocturna", "").startswith(("A color", "Luz")):
            out.append("Color de noche")
        if cam_smart(p):
            out.append("Detecta personas y vehículos")
        elif cam_audio(p):
            out.append("Con audio")
        elif d.get("Lente"):
            out.append(d["Lente"])
    elif t.startswith("Grabador"):
        ch = channels(p)
        if ch:
            out.append(f"{ch} canales")
        pp = nvr_poe_ports(p)
        out.append((f"PoE integrado ({pp} puertos)" if pp else "PoE integrado") if nvr_poe(p) else "Sin PoE")
        if nvr_bays(p):
            out.append(f"{nvr_bays(p)} disco{'s' if nvr_bays(p) > 1 else ''}")
        elif d.get("Resolución"):
            out.append(f"Hasta {d['Resolución']}")
    elif t in ("Disco duro", "Disco externo"):
        cap = capacity_tb(p)
        if cap:
            out.append(f"{_fmt_tb(cap)} TB")
        if any(w in fold(p.name) for w in SURVEILLANCE_WORDS):
            out.append("Para videovigilancia")
    elif t.startswith("Switch"):
        if ports(p):
            out.append(f"{ports(p)} puertos")
        if t == "Switch PoE":
            out.append("PoE")
    elif t == "UPS" and d.get("Capacidad"):
        out.append(d["Capacidad"])
        if ups_w(p):
            out.append(f"{ups_w(p)} W")
    elif pack_size(p) > 1:
        out.append(f"Bolsa de {pack_size(p)}")
    elif reel_len(p):
        out.append(f"Bobina de {reel_len(p)} m")
    return out[:4]


RJ45_NO = ("tester", "ponchadora", "crimp", "pinza", "balun", "pareja", "acoplador", "punto de acceso", "router", "onu")
RJ45_LOOSE_NO = ("switch", "puerto", "modulo", "ficha")  # un "RJ45" sin la palabra "conector" que no es un conector


_PACK = re.compile(r"\b(\d{2,4})\s*(?:unidades|unid|uds|piezas|pzas|pcs)\b")


def pack_size(p: Product) -> int:
    """Cuantas unidades trae (los conectores se venden en bolsa: "RJ45 Cat6A 100 unidades"); 1 si no lo dice."""
    m = _PACK.search(fold(p.name))
    return int(m.group(1)) if m else 1


def _consumable(p: Product | None) -> Product | None:
    """Un conector suelto cuesta centavos: si el del catalogo es caro POR UNIDAD (un jack), no se suma al kit y queda
    en "lo cotizamos" para no inflar el total. Una bolsa se mide por lo que cuesta cada conector de la bolsa."""
    if p is None:
        return None
    limit = Decimal(2) if (p.currency or "CRC").upper() == "USD" else Decimal(1000)
    return p if _list_price(p) / pack_size(p) <= limit else None


FEATURED_TYPES = (
    "Cámara IP",
    "Grabador NVR",
    "Cámara WiFi",
    "Switch PoE",
    "Punto de acceso WiFi",
    "Control de acceso",
    "Videoportero",
    "UPS",
    "Disco duro",
    "Cámara PTZ",
)


def default_featured(prods: list[Product], limit: int = 8, prefer: set[int] | None = None, type_of: Callable[[Product], str] | None = None) -> list[Product]:
    """Destacados cuando Andres no eligio: uno por tipo de equipo que mas se vende (camara, grabador, switch...),
    con foto y precio, el de precio mas representativo (la mediana del tipo, no el mas barato ni el mas caro).
    prefer: ids de lo que se puede comprar ya (services/availability); si un tipo tiene alguno, se elige entre ellos.
    type_of (O1-08): el tipo que muestra la tienda; una barra de sonido no representa a las camaras WiFi."""
    type_of = type_of or product_type
    out: list[Product] = []
    for t in FEATURED_TYPES:
        c = sorted((p for p in _priced(prods) if p.images and type_of(p) == t), key=_list_price)
        if prefer and any(p.id in prefer for p in c):
            c = [p for p in c if p.id in prefer]
        if c:
            out.append(c[len(c) // 2])
        if len(out) >= limit:
            break
    return out


def normalize_answers(answers: dict | None) -> dict:
    """Las respuestas del configurador con sus topes (lo que build_kit usa y lo que se compara con los kits de Andres)."""
    a = answers or {}
    indoor = max(0, min(32, int(a.get("indoor") or 0)))
    outdoor = max(0, min(32, int(a.get("outdoor") or 0)))
    if indoor + outdoor == 0:
        indoor = 1
    return {
        "place": a.get("place") if a.get("place") in PLACES else "casa",
        "indoor": indoor,
        "outdoor": outdoor,
        "days": max(1, min(90, int(a.get("days") or 15))),
        "remote": bool(a.get("remote", True)),
        # detalles (opcionales: un enlace o un kit de la tienda sin ellos toma lo de siempre)
        "quality": a.get("quality") if a.get("quality") in QUALITIES else "4mp",
        "night": a.get("night") if a.get("night") in ("ir", "color") else "ir",
        "audio": bool(a.get("audio", False)),
        "smart": bool(a.get("smart", False)),
        "record": a.get("record") if a.get("record") in ("continuo", "movimiento") else "continuo",
        "distance": a.get("distance") if a.get("distance") in DISTANCES else "media",
        "growth": bool(a.get("growth", False)),
        "ups": bool(a.get("ups", False)),
    }


# ---------------------------------------------------------------- fotos fijas del kit (pedido 21/10, item 28)
# La tarjeta del kit muestra los equipos reales que lo forman (camara, grabador, disco, switch), no la misma camara
# repetida con celdas vacias. Lo de instalacion (conectores, cable, cajas) no es "el equipo" y no sale en la foto.
KIT_PHOTO_ROLES = {
    "Cámaras exteriores": "Cámara exterior",
    "Cámaras interiores": "Cámara interior",
    "Grabador": "Grabador",
    "Almacenamiento": "Disco duro",
    "Red": "Switch PoE",
}
MAX_KIT_PHOTOS = 4


def product_images(p: Product | None) -> list[str]:
    return [i["url"] for i in (p.images or []) if isinstance(i, dict) and i.get("url")] if p is not None else []


def product_image(p: Product | None, prefer: str | None = None) -> str | None:
    """La foto elegida (si sigue siendo del producto) o la principal; None si el producto no tiene fotos."""
    urls = product_images(p)
    if not urls:
        return None
    if prefer and prefer in urls:
        return prefer
    return next((i["url"] for i in (p.images or []) if isinstance(i, dict) and i.get("main") and i.get("url")), urls[0])


def kit_photos(lines: list[dict], manual: list | None = None, products: dict[int, Product] | None = None) -> list[dict]:
    """Fotos de la tarjeta del kit. manual = lo que Andres eligio en Mi Tienda -> Vitrina ([{product_id, image}]): manda
    si alguna sigue valida (producto activo, publicado en la web y con foto). Si no, los equipos que el kit arma hoy, sin repetir producto."""
    out: list[dict] = []
    for m in manual or []:
        try:
            pid = int((m or {}).get("product_id") or 0)
        except (TypeError, ValueError):
            continue
        p = (products or {}).get(pid)
        img = product_image(p, (m or {}).get("image")) if p is not None and p.active and p.show_on_web else None
        if img and all(x["product_id"] != pid for x in out):
            out.append({"product_id": pid, "image": img, "label": product_type(p) or "Equipo", "name": p.name})
    if out:
        return out[:MAX_KIT_PHOTOS]
    seen: set[int] = set()
    for ln in lines:
        label = KIT_PHOTO_ROLES.get(ln.get("role"))
        p = ln.get("product")
        if not label or p is None or p.id in seen:
            continue
        img = product_image(p)
        if not img:
            continue
        seen.add(p.id)
        out.append({"product_id": p.id, "image": img, "label": label, "name": p.name})
        if len(out) >= MAX_KIT_PHOTOS:
            break
    return out


def preset_for(kits: list[dict], answers: dict) -> dict | None:
    """El kit de la vitrina (activo) que tiene exactamente estas respuestas: "Arma tu sistema" abierto desde su tarjeta
    muestra las mismas fotos que Andres eligio para ella."""
    na = normalize_answers(answers)
    return next((k for k in kits if k.get("active", True) and normalize_answers(k.get("answers")) == na), None)


MAX_PRODUCT_ID = 2**31 - 1  # un id mas grande no existe (ni cabe en la columna): se descarta sin consultar


def clean_pins(pins) -> dict[str, int]:
    """{llave: product_id} con llaves conocidas e ids enteros (lo guardado en el kit o lo que manda el cliente)."""
    out: dict[str, int] = {}
    for k, v in (pins or {}).items() if isinstance(pins, dict) else ():
        if k not in KIT_ROLES or isinstance(v, bool):
            continue
        try:
            pid = int(v)
        except (TypeError, ValueError, OverflowError):  # OverflowError: Infinity o 1e999 en el JSON de un enlace
            continue
        if 0 < pid <= MAX_PRODUCT_ID:
            out[k] = pid
    return out


def clean_extras(extras) -> list[dict]:
    out: list[dict] = []
    for e in extras or []:
        try:
            pid, qty = int((e or {}).get("product_id") or 0), int((e or {}).get("quantity") or 1)
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
        if 0 < pid <= MAX_PRODUCT_ID and all(x["product_id"] != pid for x in out):
            out.append({"product_id": pid, "quantity": max(1, min(99, qty))})
    return out[:MAX_KIT_EXTRAS]


def build_kit(
    prods: list[Product],
    answers: dict,
    prefer: set[int] | None = None,
    *,
    pins: dict | None = None,
    extras: list | None = None,
    choices: dict | None = None,
    day: date | None = None,
    alternatives: bool = False,
    options: int = 0,
    price_of: Callable[[Product], Decimal] | None = None,
) -> dict:
    """Kit sugerido con productos reales del catalogo. Las cantidades salen de las MISMAS reglas que usa el
    levantamiento tecnico (survey_specs.suggest_materials); aqui solo se eligen los productos. Lo que no se
    encuentra en la tienda se lista para cotizarlo (no se inventa).

    prefer: ids de lo que se puede comprar ya (en bodega o bajo pedido, services/availability): cada pieza se elige
    primero entre esos; solo si ninguno sirve se usa el resto del catalogo publicado.
    pins: {llave: product_id} fijados por Andres en Mi Tienda para este kit; se usan si siguen siendo validos
    (pin_problem), si no la pieza vuelve a la sugerencia automatica y pin_status dice por que. No rotan.
    extras: [{product_id, quantity}] lineas adicionales del kit (p. ej. una UPS).
    choices: {llave: product_id} que el cliente cambio en "Arma tu sistema" (o las piezas de un enlace compartido). Solo
    valen si el producto esta entre los candidatos de esa pieza para estas respuestas; si no, se ignoran. Mandan sobre
    los pins.
    day: dia de la rotacion (por defecto hoy en Costa Rica, ver _pick).
    alternatives: cada linea trae 2 a 4 alternativas para "Cambiar". options: cada linea trae hasta N candidatos (Mi Tienda).
    price_of: el precio con el que se COMPARAN los productos (orden, banda de variedad, rotulo «Económica»). Los
    endpoints pasan el que ve el cliente: precio web con IVA en la moneda de la tienda; 0 = "a consultar" (retenido por
    margen o sin tipo de cambio), que nunca se propone. Sin el, el precio web en la moneda del producto (_price). No
    cambia ningun precio: solo decide que se sugiere y como se rotula."""
    from ..core.timeutil import cr_today
    from .survey_specs import suggest_materials

    full = prods
    by_id = {p.id: p for p in full}

    na = normalize_answers(answers)
    place, indoor, outdoor, days, remote = na["place"], na["indoor"], na["outdoor"], na["days"], na["remote"]
    n = indoor + outdoor
    quality = QUALITIES[na["quality"]]
    motion = na["record"] == "movimiento"
    # distancia de la camara mas lejana: la que dijo el cliente o la tipica del lugar
    meters = DISTANCES[na["distance"]] or PLACES[place]["meters"]
    gb_day = quality["gb"] * (MOTION_FACTOR if motion else 1)
    need_ch = channel_size(n) if na["growth"] else n
    points = [
        {"code": f"C{i + 1}", "data": {"alimentacion": "PoE", "ambiente": "Exterior" if i < outdoor else "Interior", "distancia_m": meters}} for i in range(n)
    ]
    sug = {s["name"]: s for s in suggest_materials("cctv", points)}
    need_tb = (Decimal(n) * Decimal(days) * gb_day / 1000 * Decimal("1.1")).quantize(Decimal("0.1"))
    sw_name = next((k for k in sug if k.startswith("Switch PoE")), None)
    rj = sug.get("Conectores RJ45")
    ext = sug.get("Caja de paso exterior")
    cable_m = (Decimal(n * meters) * Decimal("1.15")).quantize(Decimal(1))

    base_price = price_of or _price
    prices: dict[int, Decimal] = {}

    def price(p: Product) -> Decimal:
        """precio de comparacion, una vez por producto (se pide en cada orden y en cada banda)"""
        v = prices.get(id(p))
        if v is None:
            v = base_price(p)
            v = prices[id(p)] = v if isinstance(v, Decimal) else Decimal(str(v or 0))
        return v

    c = {
        "n": n,
        "indoor": indoor,
        "outdoor": outdoor,
        "need_tb": need_tb,
        "need_ports": min(int(re.search(r"(\d+)", sw_name).group(1)), n) if sw_name else n,
        "rj_qty": rj["quantity"] if rj else 0,
        "cable_m": cable_m,
        "box_qty": ext["quantity"] if ext else 0,
        "price_of": price,
        # lo que el cliente pidio de las camaras y del grabador
        "mp": quality["mp"],
        "color": na["night"] == "color",
        "audio": na["audio"],
        "smart": na["smart"],
        "need_ch": need_ch,
        # se completan al elegir: la marca de las camaras (para el grabador), las bahias del grabador (para los discos)
        # y la carga del sistema (para la UPS)
        "brand": None,
        "bays": None,
        "max_disk_tb": None,
        "ups_w": 0,
    }
    seed_day = (day or cr_today()).toordinal()
    # sin el id del kit: la tarjeta y la pagina con las mismas respuestas cuadran
    sig = "-".join(str(na[k]) for k in ("place", "indoor", "outdoor", "days", "quality", "night", "audio", "smart", "record", "distance", "growth", "ups"))
    pins_ = clean_pins(pins)
    choices_ = clean_pins(choices)
    pin_status: dict[str, dict] = {}
    applied: dict[str, int] = {}

    lines: list[dict] = []
    missing: list[dict] = []

    scans: dict[str, list] = {}
    ranked: dict[tuple[str, bool], list] = {}

    def cands(key: str, sellable: bool = False) -> list[tuple[tuple, Product]]:
        """Candidatos de la pieza en todo el catalogo o (sellable) solo entre lo que se puede comprar ya. El catalogo
        se recorre UNA vez por pieza (_role_scan) y de ahi salen los dos conjuntos; cada uno se arma solo si se usa."""
        k = (key, sellable)
        if k not in ranked:
            if key not in scans:
                scans[key] = _role_scan(key, full, c)
            found = scans[key]
            ranked[k] = _role_rank(key, [x for x in found if x[1].id in prefer] if sellable else found, c)
        return ranked[k]

    tiers = (True, False) if prefer is not None else (False,)  # primero lo que se puede comprar

    def resolve(key: str, record: bool = True) -> dict:
        """Producto de la pieza y de donde sale: choice (lo eligio el cliente) > pin (fijado en el kit) > auto.
        record=False: solo consulta (que switch entraria con otro grabador), sin anotar elecciones ni pins."""
        auto = None
        for sellable in tiers:
            cs = cands(key, sellable)
            if cs:
                auto = _pick(cs, key, c, f"{seed_day}|{key}|{sig}")
                break
        pid = pins_.get(key)
        pin = by_id.get(pid) if pid else None
        prob = pin_problem(key, pin, c) if pid else None
        pin_ok = bool(pid) and prob is None
        default = pin if pin_ok else auto  # lo que el kit lleva en esta pieza si el cliente no elige nada
        ch = choices_.get(key)
        if ch:
            # vale lo que calza en esta pieza: del catalogo completo o de lo que se puede comprar (la sugerencia
            # automatica sale de ahi, y un enlace compartido la trae como eleccion)
            hit = next((p for _, p in cands(key) if p.id == ch), None)
            if hit is None and prefer is not None:
                hit = next((p for _, p in cands(key, True) if p.id == ch), None)
            if hit is not None:
                if record:
                    applied[key] = hit.id
                    if pid:
                        pin_status[key] = {"product_id": pid, "ok": pin_ok, "used": False, "reason": prob or "El cliente eligió otro producto."}
                return {"product": hit, "source": "choice", "auto": auto, "pinned": pin_ok, "suggested": default is not None and default.id == hit.id}
        if pid and record:
            pin_status[key] = {"product_id": pid, "ok": pin_ok, "used": pin_ok, "reason": prob}
        if pin_ok:
            return {"product": pin, "source": "pin", "auto": auto, "pinned": True, "suggested": True}
        return {"product": auto, "source": "auto", "auto": auto, "pinned": False, "suggested": True}

    def add(key: str, qty_fallback, reason: str, fallback: str, adds=None) -> Product | None:
        role = KIT_ROLES[key]
        r = resolve(key)
        p = r["product"]
        if p is None:
            q = qty_fallback
            missing.append({"name": fallback, "quantity": int(q) if float(q).is_integer() else float(q), "reason": reason, "role": role, "key": key})
            return None
        ln = {
            "product": p,
            "quantity": _role_qty(key, p, c),
            "role": role,
            "reason": reason,
            "key": key,
            "source": r["source"],
            # pinned: el kit de la tienda fija esta pieza (aunque el cliente haya elegido otra). suggested: es lo que
            # el kit lleva sin que el cliente elija (una eleccion igual a la sugerencia no es "un cambio del cliente")
            "pinned": r["pinned"],
            "suggested": r["suggested"],
            # lo que el kit suma por llevar este producto (el switch de un grabador que no alimenta todas las camaras)
            "adds": adds(p) if adds else [],
            "auto": r["auto"],  # la sugerencia de esa pieza (no viaja al cliente)
        }
        if alternatives:
            ln["alternatives"] = _alternatives(key, p, r["auto"], cands(key), prefer, c, adds)
        if options:
            ln["options"] = [x for _, x in sorted(cands(key), key=lambda x: (x[0], _sub(key, x[1], c), x[1].id))][:options]
        lines.append(ln)
        return p

    if outdoor:
        add("cam_out", outdoor, f"{outdoor} punto(s) afuera: formato bullet, resistente al agua", "Cámara IP exterior PoE 4 MP")
    if indoor:
        add("cam_in", indoor, f"{indoor} punto(s) adentro: formato domo, discreto", "Cámara IP interior PoE 4 MP")

    cams = [ln for ln in lines if ln["key"] in ("cam_out", "cam_in")]
    if cams:  # el grabador se busca de la marca de la mayoria de las camaras
        c["brand"] = brand_of(max(cams, key=lambda ln: ln["quantity"])["product"])
        # el disco se calcula con la resolucion de las camaras SUGERIDAS (pediste 2 MP y la de color que hay es de 4).
        # Las que elige el cliente no lo mueven: la diferencia anunciada al cambiar una camara es la de esa linea;
        # si elige una de mas resolucion, "Por que este kit" le dice que suba la calidad.
        real = max((_mp(ln["auto"]) for ln in cams if ln.get("auto") is not None), default=0)
        if real > quality["mp"]:
            gb_real = next(q["gb"] for q in QUALITIES.values() if real <= q["mp"] + 1) if real <= 9 else QUALITIES["8mp"]["gb"]
            if gb_real > quality["gb"]:
                need_tb = (Decimal(n) * Decimal(days) * gb_real * (MOTION_FACTOR if motion else 1) / 1000 * Decimal("1.1")).quantize(Decimal("0.1"))
                c["need_tb"] = need_tb
    if "disk" not in scans:
        scans["disk"] = _role_scan("disk", full, c)
    c["max_disk_tb"] = max((capacity_tb(p) for _, p in scans["disk"] if capacity_tb(p)), default=None)

    nvr_label = next((k for k in sug if k.startswith("NVR")), f"NVR {n} canales")
    if need_ch > n:
        nvr_label = f"NVR {need_ch} canales"
    # El grabador decide si el kit lleva switch aparte (solo lo ahorra el que alimenta TODAS las camaras por PoE). Con
    # las alternativas se averigua tambien que switch entraria, para que la diferencia y el rotulo de cada grabador
    # cuenten el kit completo y no solo su linea.
    sw_side = resolve("switch", record=False)["product"] if (alternatives and sw_name) else None

    def nvr_adds(p: Product) -> list[dict]:
        return [] if sw_side is None or nvr_poe_covers(p, n) else [{"product": sw_side, "quantity": 1}]

    nvr = add("nvr", 1, f"{n} cámara(s): {nvr_label.lower()}", nvr_label, adds=nvr_adds if alternatives else None)
    poe_in_nvr = nvr_poe_covers(nvr, n)
    c["bays"] = nvr_bays(nvr)
    if poe_in_nvr:
        lines[-1]["reason"] += " con PoE integrado (no hace falta switch)"
    elif nvr_poe(nvr):
        lines[-1]["reason"] += f"; sus {nvr_poe_ports(nvr)} puertos PoE no alcanzan para {n} cámaras (va con switch)"

    disk = add(
        "disk",
        1,
        f"{days} días × {n} cámara(s) a {quality['label']} ≈ {_es(need_tb)} TB {'grabando solo con movimiento' if motion else 'en grabación continua'}",
        f"Disco de videovigilancia {math.ceil(need_tb)} TB",
    )

    switch = None
    if sw_name and not poe_in_nvr:
        switch = add("switch", 1, f"alimenta las {n} cámaras por el mismo cable (PoE)", sw_name)

    # UPS: la carga del sistema (camaras, grabador y switch) con holgura; la mas chica que alcanza
    load_w = NVR_W + CAM_W * n + (SWITCH_W if switch is not None else 0)
    c["ups_w"] = math.ceil(load_w * UPS_MARGIN)
    ups = None
    if na["ups"]:
        need_va = math.ceil(c["ups_w"] / UPS_POWER_FACTOR / 100) * 100
        ups = add(
            "ups",
            1,
            f"respaldo para ~{load_w} W (grabador, {n} cámara(s){' y switch' if switch is not None else ''}): sigue grabando en un corte de luz",
            f"UPS de {max(600, need_va)} VA",
        )

    if rj:
        conn = add("rj45", rj["quantity"], rj["reason"], "Conectores RJ45")
        if conn is not None and pack_size(conn) > 1:
            lines[-1]["reason"] += f" (bolsa de {pack_size(conn)})"
    cable_reason = f"{cable_m} m de cable UTP ({meters} m por cámara + 15 %)"
    add("cable", cable_m, cable_reason, f"Cable UTP Cat6 ({cable_m} m)")
    if ext:
        add("box", ext["quantity"], ext["reason"], "Caja de paso exterior")

    # pins que no se usaron porque la pieza no va en este kit (sin camaras afuera, grabador con PoE, ...)
    for key, pid in pins_.items():
        if key in pin_status:
            continue
        why = "No aplica: el grabador ya trae PoE integrado." if key == "switch" and poe_in_nvr else "No aplica a este kit."
        pin_status[key] = {"product_id": pid, "ok": True, "used": False, "reason": why}

    extra_status: list[dict] = []
    for e in clean_extras(extras):
        p = by_id.get(e["product_id"])
        prob = NOT_HERE if p is None else _price_problem(p, c)
        extra_status.append({**e, "ok": prob is None, "reason": prob})
        if prob is None:
            lines.append(
                {
                    "product": p,
                    "quantity": e["quantity"],
                    "role": EXTRA_ROLE,
                    "reason": "Incluido en este kit",
                    "key": f"extra-{p.id}",
                    "source": "extra",
                    "pinned": False,
                    "suggested": True,
                    "adds": [],
                }
            )

    installed = None
    if disk is not None:
        dl = next(ln for ln in lines if ln["key"] == "disk")
        installed = (capacity_tb(disk) or Decimal(0)) * dl["quantity"]
    why = _kit_why(lines, na, c, n=n, need_tb=need_tb, installed=installed, meters=meters, quality=quality, nvr=nvr, ups=ups, load_w=load_w)
    notes = [
        "Kit sugerido: antes de instalar, un técnico de Crimson revisa distancias, alturas y red del sitio.",
        (
            f"Almacenamiento estimado grabando solo con movimiento a {quality['label']} en H.265+ (el 40 % de la grabación continua). "
            "Si hay movimiento casi todo el día (una calle, un parqueo), conviene grabación continua."
            if motion
            else f"Almacenamiento estimado con grabación continua a {quality['label']} en H.265+ (~{quality['gb']} GB por cámara al día). "
            "Con grabación por movimiento rinde 2 a 3 veces más."
        ),
    ]
    if remote:
        notes.append("Vista desde el celular: los grabadores sugeridos usan la app gratuita del fabricante; solo necesitás internet en el sitio.")
    return {
        "answers": dict(na),
        "place_label": PLACES[place]["label"],
        "cameras": n,
        "storage_tb": need_tb,
        "lines": lines,
        "missing": missing,
        "notes": notes,
        # "Que incluye tu kit": lo que el cliente necesita saber de un vistazo
        "summary": {
            "indoor": indoor,
            "outdoor": outdoor,
            "days": days,
            "need_tb": need_tb,
            "installed_tb": installed,
            "poe": "grabador" if poe_in_nvr else ("switch" if switch is not None else None),
            "switch_ports": ports(switch) if switch is not None else None,
            "remote": remote,
            "quality": quality["label"],
            "night": na["night"],
            "audio": na["audio"],
            "smart": na["smart"],
            "record": na["record"],
            "meters": meters,
            "channels": channels(nvr) if nvr is not None else None,
            "free_channels": (channels(nvr) or n) - n if nvr is not None else None,
            "brand": BRAND_LABELS.get(brand_of(nvr) or "") if nvr is not None else None,
            "ups": {"va": ups_va(ups), "w": ups_w(ups), "load_w": load_w}
            if ups is not None
            else ({"va": None, "w": None, "load_w": load_w} if na["ups"] else None),
        },
        # por que el kit es asi: lo que se eligio por cada respuesta y lo que no se pudo (en lenguaje del cliente)
        "why": why,
        "pin_status": pin_status,
        "extra_status": extra_status,
        "choices": applied,
    }


FEATURE_TEXT = {"color": "imagen a color de noche", "audio": "audio", "smart": "detección de personas y vehículos"}


def _es(v) -> str:
    """numero con coma decimal (1,4 TB)"""
    return _fmt_tb(Decimal(str(v))).replace(".", ",")


def _kit_why(lines: list[dict], na: dict, c: dict, *, n: int, need_tb, installed, meters: int, quality: dict, nvr, ups, load_w: int) -> list[dict]:
    """Por que el kit es asi, pieza por pieza: {"tone": ok|warn, "text"}. ok = se cumplio lo que pidio el cliente;
    warn = no se pudo del todo (y que hacer)."""
    out: list[dict] = []
    cams = [ln for ln in lines if ln["key"] in ("cam_out", "cam_in")]
    for ln in cams:
        p = ln["product"]
        where = "afuera" if ln["key"] == "cam_out" else "adentro"
        miss = cam_misses(p, c)
        asked = [f for f in ("color", "audio", "smart") if c.get(f)]
        got = [FEATURE_TEXT[f] for f in asked if f not in miss]
        if got:
            out.append({"tone": "ok", "text": f"Cámaras de {where} con {', '.join(got)}."})
        if miss:
            lacks = " ni ".join(FEATURE_TEXT[f] for f in miss)
            out.append(
                {
                    "tone": "warn",
                    "text": f"Hoy no hay cámaras de {where} con {lacks} que calcen en este kit: va la más parecida. Pedí asesoría y te buscamos otra.",
                }
            )
        mp = _mp(p)
        if mp and mp > quality["mp"] + 1 and ln.get("source") == "choice" and not ln.get("suggested"):
            out.append(
                {
                    "tone": "warn",
                    "text": f"Elegiste cámaras de {_es(mp)} MP para {where}: graban más que {quality['label']} y el disco puede no alcanzar. Subí la calidad en el paso 3.",
                }
            )
        if mp and mp < quality["mp"]:
            out.append(
                {"tone": "warn", "text": f"Las cámaras de {where} de {quality['label']} no están disponibles hoy: van de {_fmt_tb(Decimal(str(mp)))} MP."}
            )
    if nvr is not None:
        ch = channels(nvr) or n
        free = ch - n
        txt = f"Grabador de {ch} canales: tus {n} cámaras"
        out.append({"tone": "ok", "text": txt + (f" y espacio para {free} más." if free > 0 else ", sin canales libres.")})
        if na["growth"] and free < c["need_ch"] - n:
            out.append({"tone": "warn", "text": f"Pediste espacio para crecer y no hay un grabador de {c['need_ch']} canales disponible: este se queda justo."})
        b, want = brand_of(nvr), c.get("brand")
        if want and b == want:
            out.append(
                {
                    "tone": "ok",
                    "text": f"Grabador de la misma marca que las cámaras ({BRAND_LABELS.get(b, b)}): una sola app y las funciones inteligentes de las cámaras se ven en el grabador.",
                }
            )
        elif want and b and b != want:
            out.append(
                {
                    "tone": "warn",
                    "text": "El grabador es de otra marca que las cámaras: graban igual, pero algunas funciones inteligentes pueden no verse en el grabador. El técnico lo confirma.",
                }
            )
        if c.get("smart") and not nvr_smart(nvr):
            out.append(
                {
                    "tone": "warn",
                    "text": "La detección de personas la hacen las cámaras; este grabador no tiene analítica propia para buscar por persona o vehículo.",
                }
            )
        bays, top = nvr_bays(nvr), nvr_max_tb(nvr)
        dl = next((ln for ln in lines if ln["key"] == "disk"), None)
        if dl is not None and bays and dl["quantity"] > bays:
            out.append(
                {
                    "tone": "warn",
                    "text": f"Ese almacenamiento necesita {dl['quantity']} discos y al grabador le caben {bays}: bajá los días, grabá solo con movimiento o elegí un grabador más grande.",
                }
            )
        elif top is not None and top < need_tb:
            out.append(
                {"tone": "warn", "text": f"El grabador admite hasta {top} TB y hacen falta ≈ {_es(need_tb)} TB: bajá los días o grabá solo con movimiento."}
            )
    if installed is not None and installed >= need_tb:
        out.append({"tone": "ok", "text": f"{_es(installed)} TB de disco para ≈ {_es(need_tb)} TB que piden {na['days']} días de grabación."})
    if meters > 50:
        out.append(
            {
                "tone": "ok",
                "text": f"Cable calculado para cámaras a unos {meters} m: cada tramo queda dentro de los {POE_MAX_M} m que admite el PoE. Si alguna queda más lejos, el técnico agrega un switch intermedio.",
            }
        )
    if na["ups"]:
        if ups is not None:
            out.append(
                {
                    "tone": "ok",
                    "text": f"UPS de {ups_va(ups) or ups_w(ups)} {'VA' if ups_va(ups) else 'W'} para ~{load_w} W: el sistema sigue grabando en un corte de luz y queda protegido de picos.",
                }
            )
        else:
            out.append({"tone": "warn", "text": f"No hay una UPS disponible para ~{load_w} W: la cotizamos aparte."})
    return out


def _alternatives(key: str, cur: Product, auto: Product | None, cands: list, prefer: set[int] | None, c: dict, adds=None) -> list[dict]:
    """2 a 4 alternativas que tambien calzan en la pieza (para "Cambiar"): la recomendada (la sugerencia automatica de
    hoy), la mas economica, la de mejores especificaciones y las de precio mas cercano. Primero lo que se puede comprar.

    adds(p): lo que el kit suma por llevar ESE producto (el switch PoE de un grabador que no alimenta todas las camaras).
    Entra en el costo con el que se ordena y se rotula «Económica», y viaja en cada alternativa para que la diferencia
    que se le anuncia al cliente sea la del kit completo, no la de la linea sola."""
    price = c["price_of"]
    costs: dict[int, Decimal] = {}

    def cost(p: Product) -> Decimal:
        v = costs.get(p.id)
        if v is None:
            side = sum((price(x["product"]) * x["quantity"] for x in adds(p)), Decimal(0)) if adds else Decimal(0)
            v = costs[p.id] = _sub(key, p, c) + side
        return v

    pool = [p for _, p in cands if p.id != cur.id]
    if prefer is not None:
        sell = [p for p in pool if p.id in prefer]
        if sell:
            pool = sell + ([auto] if auto is not None and auto.id != cur.id and auto not in sell else [])
    if not pool:
        return (
            []
            if auto is None or auto.id == cur.id
            else [{"product": auto, "quantity": _role_qty(key, auto, c), "label": "Recomendada", "auto": True, "adds": adds(auto) if adds else []}]
        )
    cur_cost, cur_score = cost(cur), _spec_score(key, cur, c)
    picked: list[Product] = []

    def push(p: Product | None):
        if p is not None and p.id != cur.id and all(x.id != p.id for x in picked):
            picked.append(p)

    if auto is not None:
        push(auto)
    push(min(pool, key=lambda p: (cost(p), p.id)))
    push(max(pool, key=lambda p: (_spec_score(key, p, c), -cost(p), -p.id)))
    for p in sorted(pool, key=lambda p: (abs(cost(p) - cur_cost), p.id)):
        if len(picked) >= MAX_KIT_ALTERNATIVES:
            break
        push(p)
    out = []
    for p in picked[:MAX_KIT_ALTERNATIVES]:
        is_auto = auto is not None and p.id == auto.id
        if is_auto:
            label = "Recomendada"
        elif _spec_score(key, p, c) > cur_score:
            label = "Superior"
        elif cost(p) < cur_cost:
            label = "Económica"
        else:
            label = "Similar"
        out.append({"product": p, "quantity": _role_qty(key, p, c), "label": label, "auto": is_auto, "adds": adds(p) if adds else []})
    return sorted(out, key=lambda a: (cost(a["product"]), a["product"].id))


def legacy_target(path: str, query: dict, product_map: dict, category_map: dict, resolve_product, resolve_category, resolve_unmapped=None) -> str:
    """Direccion vieja de Fygaro -> direccion nueva (301). resolve_* reciben el id propio y devuelven la ruta."""
    p = "/" + (path or "").strip("/")
    parts = [x for x in p.split("/") if x]
    if not parts:
        return "/"
    head = parts[0]
    if head == "terms":
        return "/info/terminos"
    if head == "policies":
        return "/info/privacidad"
    if head == "page":
        if len(parts) > 1 and "asesoria" in parts[1]:
            return "/asesoria"
        return "/"
    if head == "products":
        if len(parts) == 1:
            return "/catalogo"
        if parts[1] == "search":
            kw = (query.get("keywords") or "").strip()
            return f"/buscar?q={_q(kw)}" if kw else "/catalogo"
        if parts[1] == "category" and len(parts) > 2:
            cid = category_map.get(parts[2])
            return resolve_category(cid) if cid else "/catalogo"
        e = product_map.get(parts[1])
        if e and e.get("id"):
            hit = resolve_product(int(e["id"]))
            if hit:
                return hit
        # sin mapa o el producto ya no esta: el mismo equipo por codigo/modelo, o una busqueda con su nombre
        return (resolve_unmapped(parts[1], e) if resolve_unmapped else None) or "/catalogo"
    return "/"


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


# ---------------------------------------------------------------- version del catalogo (ETag / LRU)


def get_catalog_version(t) -> str:
    """Version del catalogo para ETag: se lee de tenant.settings['catalog_v'] (entero unix-ms).
    Devuelve '0' si nunca se ha guardado (primera carga del tenant)."""
    return str((t.settings or {}).get("catalog_v") or 0)


def bump_catalog_version(db, t) -> str:
    """Sube la version del catalogo cuando cambia algo visible en la tienda: producto, categoria,
    precio, existencias o ajustes de tienda. Solo flush; el llamador hace el commit.
    En el worker (que tiene la cola) tambien encola store.rebuild_snapshot con debounce de 30 s para no apilar cambios
    rapidos; en la API no hay cola y no se intenta: el snapshot se rearma en la siguiente visita (store_snapshot).
    Devuelve la nueva version como string."""
    from datetime import UTC, datetime

    v = int(datetime.now(UTC).timestamp() * 1000)
    s = dict(t.settings or {})
    s["catalog_v"] = v
    t.settings = s
    db.flush()
    # debounce: el snapshot se rearma ~30 s despues del ultimo cambio, no por cada guardado individual (sin cola,
    # enqueue_rebuild vuelve de inmediato: nunca se espera a un broker dentro de la peticion)
    try:
        from .store_snapshot import enqueue_rebuild

        enqueue_rebuild(t.id, countdown=30)
    except Exception:  # noqa: BLE001
        pass
    return str(v)
