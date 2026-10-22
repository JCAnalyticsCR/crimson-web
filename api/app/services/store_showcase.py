"""Vitrina de la tienda propia (reemplazo de Fygaro): direcciones amigables, especificaciones deducidas, busqueda
tolerante, portada administrable, configurador "Arma tu sistema" y SEO (metas por ruta, JSON-LD, sitemap).

Todo lo administrable vive en tenant.settings["store"]["showcase"] (JSON, sin migracion). Por producto solo hay
tres columnas opcionales: compare_price, datasheet_url y specs (migracion tienda_vit_a1c4e7f20b93).
"""

from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from decimal import Decimal

from ..models import Product

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
        # Google Analytics 4 (Fygaro usaba uno): solo se carga si la persona acepta la medicion en la tienda
        "ga4_id": sc.get("ga4_id") or "",
    }


# ---------------------------------------------------------------- texto
def fold(s: object) -> str:
    """minusculas sin tildes"""
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


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
    ("Memoria microSD", ("micro sd", "microsd", "sdxc", "sdhc"), ()),
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
    n = " " + fold(p.name) + " "
    for label, must, never in TYPE_RULES:
        if any(w in n for w in must) and not any(w in n for w in never):
            if label == "Switch PoE" and "poe" not in n:
                return "Switch"
            if label == "Cámara IP" and ("turbo" in n or "analog" in n or "hd-tvi" in n):
                return "Cámara analógica"
            return label
    return "Accesorio" if fold(p.name).startswith(("soporte", "caja", "brazo", "base", "tornillo", "abrazadera")) else ""


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


def derive_specs(p: Product) -> dict[str, str]:
    """Especificaciones legibles a partir del nombre y la descripcion (Fygaro no las tenia en campos).
    Las que Andres edite a mano (product.specs) mandan sobre las deducidas."""
    name = fold(p.name)
    text = name + " \n " + fold(p.description_store or p.description_invoice or "")[:1500]
    t = product_type(p)
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
        if str(k).strip():
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


# ---------------------------------------------------------------- busqueda tolerante
SYNONYMS = {
    "grabador": ("nvr", "dvr", "grabador", "grabadora"),
    "grabadora": ("nvr", "dvr", "grabador", "grabadora"),
    "camara": ("camara", "camera", "cam"),
    "camaras": ("camara", "camera", "cam"),
    "disco": ("disco", "hdd"),
    "ups": ("ups", "back-ups"),
    "wifi": ("wifi", "wi-fi", "inalambric"),
    "acceso": ("acceso", "lector", "facial"),
}


def haystack(p: Product, cat_name: str | None) -> tuple[str, str]:
    raw = " ".join(x or "" for x in (p.name, p.code, p.brand, p.model, p.supplier_sku, cat_name, product_type(p)))
    return " " + fold(raw) + " ", compact(raw)


def search_score(p: Product, cat_name: str | None, terms: list[str], hay: tuple[str, str] | None = None) -> int:
    """0 = no coincide. Todas las palabras deben aparecer (sin tildes, con o sin guiones). Mas puntos si el
    codigo o el modelo empiezan igual que lo buscado, o si la palabra esta en el nombre."""
    text, comp = hay or haystack(p, cat_name)
    name = fold(p.name)
    codes = [compact(p.code), compact(p.model or ""), compact(p.supplier_sku or "")]
    score = 1
    for t in terms:
        c = compact(t)
        if not c:
            continue
        alts = SYNONYMS.get(c, (c,))
        variants = (
            {compact(a) for a in alts} | ({c[:-1]} if len(c) > 4 and c.endswith("s") else set()) | ({c[:-2]} if len(c) > 5 and c.endswith("es") else set())
        )
        if not any(v and v in comp for v in variants):
            return 0
        if any(cd and cd.startswith(c) for cd in codes):
            score += 40
        elif any(cd and c in cd for cd in codes):
            score += 20
        if any(v in compact(name) for v in variants):
            score += 6
        if re.search(rf"\b{re.escape(fold(t))}\b", name):
            score += 4
    return score


def split_terms(q: str) -> list[str]:
    return [w for w in re.split(r"[\s,;/]+", (q or "").strip()) if w][:8]


# ---------------------------------------------------------------- configurador "Arma tu sistema"
PLACES = {
    "casa": {"label": "Casa", "meters": 20},
    "negocio": {"label": "Negocio u oficina", "meters": 30},
    "condominio": {"label": "Condominio", "meters": 40},
    "bodega": {"label": "Bodega o planta", "meters": 45},
}
GB_PER_CAMERA_DAY = Decimal(21)  # 4 MP H.265+ continuo ~2 Mbps -> ~21 GB por dia (por movimiento rinde 2-3x)


def _priced(prods: list[Product]) -> list[Product]:
    return [p for p in prods if p.price and Decimal(str(p.price)) > 0]


def _pick_camera(prods: list[Product], outdoor: bool) -> Product | None:
    cams = []
    for p in _priced(prods):
        n = fold(p.name)
        poe = "poe" in fold(p.name + " " + (p.description_store or "")) or "acusense" in n
        if product_type(p) != "Cámara IP" or not poe:
            continue
        if any(w in n for w in ("ptz", "wifi", "panoram", "fisheye", "lpr", "anpr", "termic", "kit")):
            continue
        cams.append(p)
    if not cams:
        return None

    def rank(p: Product):
        n = fold(p.name)
        fmt_ok = ("bullet" in n) if outdoor else any(w in n for w in ("domo", "dome", "turret", "eyeball"))
        mp = _RX["mp"].search(n)
        mp_v = float(_num(mp.group(1))) if mp else 0
        return (not fmt_ok, abs(mp_v - 4) if mp_v else 9, Decimal(str(p.price)))

    return sorted(cams, key=rank)[0]


def _pick_recorder(prods: list[Product], n_cams: int) -> tuple[Product | None, bool]:
    """NVR con canales suficientes. Si hay uno con PoE integrado (/8P, /16P) se prefiere: ahorra el switch."""
    best: list[tuple] = []
    for p in _priced(prods):
        if product_type(p) != "Grabador NVR" or "wifi" in fold(p.name) or "inalambric" in fold(p.name):
            continue
        ch = channels(p)
        if not ch or ch < n_cams:
            continue
        poe = bool(re.search(r"/\d{1,2}p\b", fold(p.code + " " + (p.model or "") + " " + p.name))) or (" poe" in fold(p.name) and "switch poe" in fold(p.name))
        best.append((ch, not poe, Decimal(str(p.price)), p, poe))
    if not best:
        return None, False
    ch, _, _, p, poe = sorted(best, key=lambda x: (x[0], x[1], x[2]))[0]
    return p, poe


def _pick_disk(prods: list[Product], need_tb: Decimal) -> tuple[Product | None, int]:
    disks = [(capacity_tb(p), p) for p in _priced(prods) if product_type(p) == "Disco duro" and capacity_tb(p)]
    if not disks:
        return None, 0
    surv = [(c, p) for c, p in disks if any(w in fold(p.name) for w in ("purple", "skyhawk", "vigilancia", "vx"))] or disks
    fits = sorted([(c, p) for c, p in surv if c >= need_tb], key=lambda x: (x[0], Decimal(str(x[1].price))))
    if fits:
        return fits[0][1], 1
    c, p = max(surv, key=lambda x: x[0])
    return p, max(1, math.ceil(need_tb / c))


def _pick_switch(prods: list[Product], need_ports: int) -> Product | None:
    sw = [(ports(p) or 0, p) for p in _priced(prods) if product_type(p) == "Switch PoE"]
    sw = [(n, p) for n, p in sw if n >= need_ports and n <= 24]
    return sorted(sw, key=lambda x: (x[0], Decimal(str(x[1].price))))[0][1] if sw else None


def _pick_by_words(prods: list[Product], words: tuple[str, ...], never: tuple[str, ...] = ()) -> Product | None:
    c = [p for p in _priced(prods) if all(w in fold(p.name) for w in words) and not any(w in fold(p.name) for w in never)]
    return sorted(c, key=lambda p: Decimal(str(p.price)))[0] if c else None


RJ45_NO = ("tester", "ponchadora", "crimp", "pinza", "balun", "pareja", "acoplador", "punto de acceso", "router", "onu")


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
    return p if Decimal(str(p.price)) / pack_size(p) <= limit else None


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


def default_featured(prods: list[Product], limit: int = 8, prefer: set[int] | None = None) -> list[Product]:
    """Destacados cuando Andres no eligio: uno por tipo de equipo que mas se vende (camara, grabador, switch...),
    con foto y precio, el de precio mas representativo (la mediana del tipo, no el mas barato ni el mas caro).
    prefer: ids de lo que se puede comprar ya (services/availability); si un tipo tiene alguno, se elige entre ellos."""
    out: list[Product] = []
    for t in FEATURED_TYPES:
        c = sorted((p for p in _priced(prods) if p.images and product_type(p) == t), key=lambda p: Decimal(str(p.price)))
        if prefer and any(p.id in prefer for p in c):
            c = [p for p in c if p.id in prefer]
        if c:
            out.append(c[len(c) // 2])
        if len(out) >= limit:
            break
    return out


def _first(pools: list[list[Product]], fn, *args):
    """El primer pool donde el selector encuentra algo (lo vendible primero; si no hay, el catalogo publicado)."""
    r = None
    for pool in pools:
        r = fn(pool, *args)
        if (r[0] if isinstance(r, tuple) else r) is not None:
            return r
    return r


def normalize_answers(answers: dict | None) -> dict:
    """Las 4 respuestas del configurador con sus topes (lo que build_kit usa y lo que se compara con los kits de Andres)."""
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


def build_kit(prods: list[Product], answers: dict, prefer: set[int] | None = None) -> dict:
    """Kit sugerido con productos reales del catalogo. Las cantidades salen de las MISMAS reglas que usa el
    levantamiento tecnico (survey_specs.suggest_materials); aqui solo se eligen los productos. Lo que no se
    encuentra en la tienda se lista para cotizarlo (no se inventa).
    prefer: ids de lo que se puede comprar ya (en bodega o bajo pedido, services/availability): cada pieza se elige
    primero entre esos; solo si ninguno sirve se usa el resto del catalogo publicado."""
    from .survey_specs import suggest_materials

    pools = [[p for p in prods if p.id in prefer], prods] if prefer is not None else [prods]

    na = normalize_answers(answers)
    place, indoor, outdoor, days, remote = na["place"], na["indoor"], na["outdoor"], na["days"], na["remote"]
    n = indoor + outdoor
    meters = PLACES[place]["meters"]
    points = [
        {"code": f"C{i + 1}", "data": {"alimentacion": "PoE", "ambiente": "Exterior" if i < outdoor else "Interior", "distancia_m": meters}} for i in range(n)
    ]
    sug = {s["name"]: s for s in suggest_materials("cctv", points)}

    lines: list[dict] = []
    missing: list[dict] = []

    def add(p: Product | None, qty, role: str, reason: str, fallback: str):
        if p is None:
            missing.append({"name": fallback, "quantity": int(qty) if float(qty).is_integer() else float(qty), "reason": reason, "role": role})
        else:
            lines.append({"product": p, "quantity": int(math.ceil(float(qty))), "role": role, "reason": reason})

    cam_out = _first(pools, _pick_camera, True) if outdoor else None
    cam_in = _first(pools, _pick_camera, False) if indoor else None
    if outdoor:
        add(cam_out, outdoor, "Cámaras exteriores", f"{outdoor} punto(s) afuera: formato bullet, resistente al agua", "Cámara IP exterior PoE 4 MP")
    if indoor:
        add(cam_in, indoor, "Cámaras interiores", f"{indoor} punto(s) adentro: formato domo, discreto", "Cámara IP interior PoE 4 MP")

    nvr, nvr_poe = _first(pools, _pick_recorder, n)
    nvr_label = next((k for k in sug if k.startswith("NVR")), f"NVR {n} canales")
    add(nvr, 1, "Grabador", f"{n} cámara(s): {nvr_label.lower()}" + (" con PoE integrado (no hace falta switch)" if nvr_poe else ""), nvr_label)

    need_tb = (Decimal(n) * Decimal(days) * GB_PER_CAMERA_DAY / 1000 * Decimal("1.1")).quantize(Decimal("0.1"))
    disk, disk_qty = _first(pools, _pick_disk, need_tb)
    add(
        disk,
        disk_qty or 1,
        "Almacenamiento",
        f"{days} días × {n} cámara(s) ≈ {need_tb} TB en grabación continua",
        f"Disco de videovigilancia {math.ceil(need_tb)} TB",
    )

    sw_name = next((k for k in sug if k.startswith("Switch PoE")), None)
    if sw_name and not nvr_poe:
        need_ports = int(re.search(r"(\d+)", sw_name).group(1))
        add(_first(pools, _pick_switch, min(need_ports, n)), 1, "Red", f"alimenta las {n} cámaras por el mismo cable (PoE)", sw_name)

    rj = sug.get("Conectores RJ45")
    if rj:
        conn = _consumable(
            _first(pools, _pick_by_words, ("conector", "rj45"), RJ45_NO)
            or _first(pools, _pick_by_words, ("rj45",), RJ45_NO + ("switch", "puerto", "modulo", "ficha"))
        )
        pack = pack_size(conn) if conn else 1
        add(
            conn,
            math.ceil(rj["quantity"] / pack),
            "Instalación",
            rj["reason"] + (f" (bolsa de {pack})" if pack > 1 else ""),
            "Conectores RJ45",
        )
    cable_m = (Decimal(n * meters) * Decimal("1.15")).quantize(Decimal(1))
    bobina = _first(pools, _pick_by_words, ("bobina",), ("coaxial",)) or _first(pools, _pick_by_words, ("cable", "305"))
    if bobina:
        add(bobina, max(1, math.ceil(cable_m / 305)), "Instalación", f"{cable_m} m de cable UTP ({meters} m por cámara + 15 %)", "Cable UTP")
    else:
        add(
            _first(pools, _pick_by_words, ("cable", "utp"), ("patch", "hdmi", "coaxial")),
            cable_m,
            "Instalación",
            f"{cable_m} m de cable UTP ({meters} m por cámara + 15 %)",
            f"Cable UTP Cat6 ({cable_m} m)",
        )
    ext = sug.get("Caja de paso exterior")
    if ext:
        add(_first(pools, _pick_by_words, ("caja", "exterior"), ("tapa", "fuerte", "carton")), ext["quantity"], "Instalación", ext["reason"], "Caja de paso exterior")

    notes = [
        "Kit sugerido: antes de instalar, un técnico de Crimson revisa distancias, alturas y red del sitio.",
        f"Almacenamiento estimado con grabación continua a 4 MP en H.265+ (~{GB_PER_CAMERA_DAY} GB por cámara al día). Con grabación por movimiento rinde 2 a 3 veces más.",
    ]
    if remote:
        notes.append("Vista desde el celular: los grabadores sugeridos usan la app gratuita del fabricante; solo necesitás internet en el sitio.")
    return {
        "answers": {"place": place, "indoor": indoor, "outdoor": outdoor, "days": days, "remote": remote},
        "place_label": PLACES[place]["label"],
        "cameras": n,
        "storage_tb": need_tb,
        "lines": lines,
        "missing": missing,
        "notes": notes,
    }


# ---------------------------------------------------------------- SEO
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
    Tambien encola store.rebuild_snapshot con debounce de 30 s para no apilar cambios rapidos.
    Devuelve la nueva version como string."""
    from datetime import UTC, datetime

    v = int(datetime.now(UTC).timestamp() * 1000)
    s = dict(t.settings or {})
    s["catalog_v"] = v
    t.settings = s
    db.flush()
    # debounce: el snapshot se rearma ~30 s despues del ultimo cambio, no por cada guardado individual
    try:
        from .store_snapshot import enqueue_rebuild

        enqueue_rebuild(t.id, countdown=30)
    except Exception:  # noqa: BLE001
        pass
    return str(v)
