"""Nombres de producto legibles a partir de la descripcion del proveedor.

Eurocomp manda la descripcion en mayusculas, sin tildes, con su codigo interno al final (311326530) y marcas de lote
como (O-STD) o (E). En la tienda eso se lee como un inventario de bodega. `tidy` no inventa nada: quita ese ruido,
pone mayusculas solo donde van (marcas, siglas, modelos) y las tildes de las palabras comunes. Un nombre que ya viene
escrito a mano (con minusculas) no se toca.

La descripcion original se conserva en el producto (description_invoice) y en el articulo del proveedor.
"""

from __future__ import annotations

import re

# siglas y unidades: se escriben siempre asi
ACRONYMS = {
    "ip": "IP", "poe": "PoE", "poe+": "PoE+", "nvr": "NVR", "dvr": "DVR", "xvr": "XVR", "ptz": "PTZ", "pt": "PT",
    "wifi": "WiFi", "wi-fi": "WiFi", "hd": "HD", "fhd": "FHD", "uhd": "UHD", "tb": "TB", "gb": "GB", "mb": "MB",
    "mp": "MP", "ir": "IR", "utp": "UTP", "ftp": "FTP", "stp": "STP", "rj45": "RJ45", "ups": "UPS", "va": "VA",
    "led": "LED", "usb": "USB", "hdmi": "HDMI", "vga": "VGA", "sata": "SATA", "ssd": "SSD", "hdd": "HDD", "sd": "SD",
    "sdxc": "SDXC", "sdhc": "SDHC", "uhs-i": "UHS-I", "rf": "RF", "gps": "GPS", "lan": "LAN", "wan": "WAN",
    "sfp": "SFP", "ac": "AC", "dc": "DC", "vdc": "VDC", "vac": "VAC", "cmr": "CMR", "rpm": "RPM", "tvi": "TVI",
    "ahd": "AHD", "cvi": "CVI", "cvbs": "CVBS", "tvl": "TVL", "mbps": "Mbps", "gbps": "Gbps", "ghz": "GHz",
    "em": "EM", "nfc": "NFC", "pir": "PIR", "lte": "LTE", "cctv": "CCTV", "awg": "AWG", "pvc": "PVC", "ul": "UL",
    "wdr": "WDR", "dwdr": "DWDR", "onu": "ONU", "olt": "OLT", "rs485": "RS485", "ii": "II", "iii": "III", "iv": "IV",
    "cat5e": "Cat5e", "cat6": "Cat6", "cat6a": "Cat6A", "cat7": "Cat7", "ia": "IA", "ai": "AI", "lcd": "LCD",
    "ip66": "IP66", "ip67": "IP67", "ip68": "IP68", "4k": "4K", "dc12v": "DC12V", "12v": "12V", "24v": "24V",
    "h.265": "H.265", "h.265+": "H.265+", "h.264": "H.264", "h.264+": "H.264+", "app": "app", "ptt": "PTT",
}  # fmt: skip
# marcas y tecnologias con su forma propia
BRANDS = {
    "hikvision": "Hikvision", "hilook": "HiLook", "ezviz": "Ezviz", "hikmicro": "HikMicro", "hiksemi": "HikSemi",
    "ubiquiti": "Ubiquiti", "unifi": "UniFi", "apc": "APC", "grandstream": "Grandstream", "zkteco": "ZKTeco",
    "dahua": "Dahua", "paradox": "Paradox", "ruijie": "Ruijie", "reyee": "Reyee", "mikrotik": "MikroTik",
    "seagate": "Seagate", "western": "Western", "digital": "Digital", "purple": "Purple", "skyhawk": "SkyHawk",
    "nova": "Nova", "forza": "Forza", "nexxt": "Nexxt", "tp-link": "TP-Link", "kingston": "Kingston",
    "adata": "ADATA", "patriot": "Patriot", "agiler": "Agiler", "acusense": "AcuSense", "colorvu": "ColorVu",
    "darkfighter": "DarkFighter", "hik-connect": "Hik-Connect", "ax": "AX", "pro": "Pro", "turbo": "Turbo",
}  # fmt: skip
# tildes de las palabras que mas se repiten en el catalogo
ACCENTS = {
    "camara": "cámara", "camaras": "cámaras", "analoga": "análoga", "analogo": "análogo", "analogica": "analógica",
    "magnetico": "magnético", "magnetica": "magnética", "inalambrico": "inalámbrico", "inalambrica": "inalámbrica",
    "inalambricos": "inalámbricos", "deteccion": "detección", "vision": "visión", "electrico": "eléctrico",
    "electrica": "eléctrica", "electronica": "electrónica", "electronico": "electrónico", "bateria": "batería",
    "baterias": "baterías", "linea": "línea", "energia": "energía", "microfono": "micrófono", "optico": "óptico",
    "intrusion": "intrusión", "hibrida": "híbrida", "hibrido": "híbrido", "proteccion": "protección",
    "alimentacion": "alimentación", "conexion": "conexión", "resolucion": "resolución", "grabacion": "grabación",
    "telefono": "teléfono", "tecnologia": "tecnología", "modulo": "módulo", "boton": "botón", "tactil": "táctil",
    "termica": "térmica", "termico": "térmico", "angulo": "ángulo", "maximo": "máximo", "maxima": "máxima",
    "metalico": "metálico", "metalica": "metálica", "plastico": "plástico", "panoramica": "panorámica",
    "electroiman": "electroimán", "biometrico": "biométrico", "biometrica": "biométrica", "codigo": "código",
    "numerico": "numérico", "automatico": "automático", "automatica": "automática", "rapida": "rápida",
    "rapido": "rápido", "area": "área", "cache": "caché", "pulg": "pulg.", "videovigilancia": "videovigilancia",
    "puertos": "puertos", "mas": "más", "valvula": "válvula", "anden": "andén", "transmision": "transmisión",
    "vehiculos": "vehículos", "vehiculo": "vehículo", "peaton": "peatón", "aplicacion": "aplicación",
    "deteccion.": "detección.", "camion": "camión", "multiproposito": "multipropósito", "condominio": "condominio",
    "estandar": "estándar", "unico": "único", "unica": "única", "energetico": "energético",
}  # fmt: skip
NOISE = re.compile(r"\((?:O-STD|STD|E|D|LATAM[^)]*)\)|\b-AF\b", re.I)
INTERNAL_CODE = re.compile(r"(?<![\w-])\d{9}(?![\w-])")  # codigo interno de Eurocomp/Hikvision (311326530)
PREFIX = ((re.compile(r"^HD\s+INTERNO\b", re.I), "Disco duro interno"), (re.compile(r"^HD\s+EXTERNO\b", re.I), "Disco duro externo"))


def shouting(name: str) -> bool:
    """Viene en mayusculas (descripcion de proveedor) y no escrito a mano."""
    letters = [c for c in name if c.isalpha()]
    return len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.8


def _word(core: str) -> str:
    low = core.lower()
    if low in ACRONYMS:
        return ACRONYMS[low]
    if low in BRANDS:
        return BRANDS[low]
    if any(c.isdigit() for c in core):
        return core  # modelo, medida o capacidad: tal cual (DS-2CD2043G2-LI2U, 4MP, 2.8MM)
    if "/" in core:
        return "/".join(_word(x) for x in core.split("/"))
    return ACCENTS.get(low, low)


def _token(tok: str) -> str:
    m = re.match(r"^([(\[\"'¿¡]*)(.*?)([)\]\"',.;:!?]*)$", tok)
    lead, core, tail = m.groups() if m else ("", tok, "")
    return lead + (_word(core) if core else "") + tail


def tidy(name: str | None) -> str:
    """Nombre limpio para la tienda; vacio o ya escrito a mano -> igual que vino."""
    raw = " ".join((name or "").split())
    if not raw or not shouting(raw):
        return raw
    s = NOISE.sub(" ", raw)
    s = INTERNAL_CODE.sub(" ", s)
    s = " ".join(s.split())
    prefix = ""
    for rx, repl in PREFIX:
        if rx.match(s):
            prefix, s = repl, rx.sub("", s).strip()
            break
    words = [_token(t) for t in s.split()]
    out = " ".join(([prefix] if prefix else []) + words).strip(" -,")
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    return (out[:1].upper() + out[1:])[:200] if out else raw


# ---------------------------------------------------------------- nombre comercial (O3-03)
# Regla: [Tipo] [Marca oficial] [linea o modelo corto] · [1 o 2 datos de la familia] · [color, solo si viene escrito].
# Solo con lo que ya dice el nombre o la descripcion: nada se deduce de un codigo (BLK no es «Negro» sin revision) y
# el nombre original queda para la factura (Product.name). Sale como borrador (services/catalog_drafts): lo que no
# sale limpio va marcado para revision.
NAME_TARGET = 60  # la regla apunta a 60 caracteres o menos

# erratas vistas en nombres del proveedor (se corrigen en el nombre comercial, nunca en el fiscal)
TYPOS = {"hikvusion": "Hikvision", "varifocaal": "varifocal", "reconomiento": "reconocimiento", "camra": "cámara", "inalambirco": "inalámbrico"}

# como empieza el nombre -> tipo escrito bien (singular). El primero que calce gana; los largos primero.
TYPE_LEADS: tuple[tuple[str, str], ...] = (
    ("camara ip", "Cámara IP"), ("camara wifi", "Cámara WiFi"), ("camara de seguridad", "Cámara de seguridad"), ("camara", "Cámara"),
    ("kit de camaras", "Kit de cámaras"), ("kit", "Kit"), ("videoportero", "Videoportero"), ("timbre", "Timbre inteligente"),
    ("grabador", "Grabador"), ("grabadora", "Grabador"), ("nvr", "Grabador NVR"), ("dvr", "Grabador DVR"), ("xvr", "Grabador XVR"),
    ("disco duro externo", "Disco duro externo"), ("disco duro", "Disco duro"), ("disco solido", "Disco sólido"), ("ssd", "Disco sólido"),
    ("memoria micro sd", "Memoria microSD"), ("memoria microsd", "Memoria microSD"), ("memoria usb", "Memoria USB"), ("memoria", "Memoria"),
    ("switch poe", "Switch PoE"), ("switch", "Switch"), ("router", "Router"), ("punto de acceso", "Punto de acceso"),
    ("access point", "Punto de acceso"), ("repetidor", "Repetidor WiFi"), ("extensor", "Repetidor WiFi"), ("adaptador usb", "Adaptador USB"),
    ("adaptador", "Adaptador"), ("ups", "UPS"), ("regulador", "Regulador de voltaje"), ("power bank", "Power bank"),
    ("bateria portatil", "Power bank"), ("bateria", "Batería"), ("parlante", "Parlante"), ("bocina", "Parlante"), ("altavoz", "Parlante"),
    ("barra de sonido", "Barra de sonido"), ("audifonos", "Audífonos"), ("audifono", "Audífonos"), ("auriculares", "Audífonos"),
    ("microfono", "Micrófono"), ("control de acceso", "Control de acceso"), ("lector", "Lector"), ("cerradura", "Cerradura"),
    ("panel de alarma", "Panel de alarma"), ("sensor de movimiento", "Sensor de movimiento"), ("sistema mesh", "Sistema WiFi mesh"),
    ("freidora de aire", "Freidora de aire"), ("freidora", "Freidora"), ("licuadora", "Licuadora"), ("ventilador", "Ventilador"),
    ("hervidor", "Hervidor"), ("cargador", "Cargador"), ("afeitadora", "Afeitadora"), ("capturadora", "Capturadora de video"),
    ("alarma", "Alarma"), ("sensor", "Sensor"), ("detector", "Detector"), ("sirena", "Sirena"), ("panel", "Panel"),
    ("gabinete", "Gabinete"), ("rack", "Rack"), ("cable", "Cable"), ("bobina", "Bobina de cable"), ("conector", "Conector"),
    ("inyector", "Inyector PoE"), ("fuente de poder", "Fuente de poder"), ("fuente", "Fuente de poder"), ("enchufe", "Enchufe inteligente"),
    ("bombillo", "Bombillo inteligente"), ("foco", "Bombillo inteligente"), ("tira led", "Tira LED"), ("monitor", "Monitor"),
    ("teclado", "Teclado"), ("mouse", "Mouse"), ("soporte", "Soporte"), ("brazo", "Brazo"), ("caja", "Caja"),
)  # fmt: skip
# tipo mostrado (product_typing) -> como va en el nombre si el nombre no empieza con un tipo
TYPE_FROM_DISPLAY = {"Cámara IP": "Cámara IP", "Cámara WiFi": "Cámara WiFi", "Cámara PTZ": "Cámara PTZ", "Cámara analógica": "Cámara analógica",
                     "Grabador NVR": "Grabador NVR", "Grabador DVR": "Grabador DVR", "Disco duro": "Disco duro", "Disco externo": "Disco duro externo",
                     "Memoria microSD": "Memoria microSD", "Switch PoE": "Switch PoE", "Switch": "Switch", "Punto de acceso WiFi": "Punto de acceso",
                     "Router": "Router", "Inyector PoE": "Inyector PoE", "UPS": "UPS", "Videoportero": "Videoportero", "Alarma": "Alarma",
                     "Cerradura": "Cerradura", "Gabinete": "Gabinete", "Cable": "Cable", "Conector": "Conector", "Control de acceso": "Control de acceso"}  # fmt: skip
# palabras con que termina la «linea» (despues vienen datos, no el nombre del modelo)
_STOP = {
    "con", "de", "del", "para", "a", "y", "en", "sin", "tipo", "color", "bluetooth", "bt", "wifi", "wi-fi", "inalambrico", "inalambrica",
    "portatil", "recargable", "resistente", "prueba", "agua", "ip67", "ip66", "ip68", "ipx7", "ipx4", "ipx5", "ipx6", "rms", "usb", "hdmi",
    "poe", "doble", "dual", "interior", "exterior", "bullet", "domo", "turret", "camara", "negro", "negra", "blanco", "blanca", "azul",
    "rojo", "roja", "gris", "verde", "rosado", "rosada", "morado", "amarillo", "camuflado", "dorado", "plateado", "vision", "nocturna",
    "colorvu", "acusense", "turbo", "varifocal", "pan", "tilt", "kit", "reconocimiento", "facial", "gigabit", "techo", "acero", "bolsa",
    "unidades", "pulgadas", "canales", "canal", "puertos", "camaras", "lite", "ir", "full", "smart", "inteligente", "multicolor", "rgb",
}  # fmt: skip
UNIT_WORDS = {"canales", "canal", "ch", "puertos", "pulgadas", "unidades", "camaras", "m", "metros", "w", "mp", "tb", "gb", "va", "kg", "a", "v", "mm", "horas", "h"}
SUBLINE = re.compile(r"\b(tapo|deco|archer|unifi)\s+([a-z0-9-]*\d[a-z0-9-]*)", re.I)
COLORS = {"negro": "negro", "negra": "negro", "blanco": "blanco", "blanca": "blanco", "azul": "azul", "rojo": "rojo", "roja": "rojo",
          "gris": "gris", "verde": "verde", "rosado": "rosado", "rosada": "rosado", "morado": "morado", "amarillo": "amarillo",
          "camuflado": "camuflado", "dorado": "dorado", "plateado": "plateado", "celeste": "celeste", "beige": "beige"}  # fmt: skip
# codigos y marcas de lote que sobran al final o entre parentesis
_LOT = re.compile(r"\((?:us|am|am-std|o-std|std|e|d|eu|latam[^)]*|la)\)", re.I)
_TRAIL_CODES = (
    re.compile(r"\bB0[A-Z0-9]{8}\b"),  # ASIN
    re.compile(r"(?<![\w-])\d{8,14}(?![\w-])"),  # EAN/UPC o codigo interno largo
    re.compile(r"\bHK\d{3}-[\w-]+", re.I),  # HK096-...
    re.compile(r"\b[A-Z]{2,5}[A-Z0-9]*(?:BLK|BLU|RED|WHT|GRY|GRN|PNK|SQUAD|BLUE|BLACK|WHITE|TEAL|CAMO)[A-Z]{0,4}\b"),  # JBLCHARGE6BLKAM
)


def _fold(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _clean_source(name: str) -> str:
    s = " ".join((name or "").split())
    s = _LOT.sub(" ", s)
    s = INTERNAL_CODE.sub(" ", s)
    for rx in _TRAIL_CODES:
        s = rx.sub(" ", s)
    for bad, good in TYPOS.items():
        s = re.sub(rf"\b{bad}\b", good, s, flags=re.I)
    return " ".join(s.split()).strip(" -,;/")


def _type_lead(clean: str) -> tuple[str | None, str]:
    """(tipo escrito bien, resto del nombre) si el nombre empieza con un tipo conocido"""
    f = _fold(clean)
    for lead, label in TYPE_LEADS:
        if f == lead or f.startswith(lead + " "):
            return label, clean[len(lead) :].strip()
    return None, clean


def _nice(tok: str) -> str:
    low = _fold(tok)
    if low in ACRONYMS:
        return ACRONYMS[low]
    if low in BRANDS:
        return BRANDS[low]
    if any(c.isdigit() for c in tok):
        return tok.upper() if re.fullmatch(r"[a-z]{1,3}\d+[a-z]?", low) else tok
    return tok[:1].upper() + tok[1:].lower() if tok.islower() or tok.isupper() else tok


def _line(rest: str, brand: str | None) -> str:
    """las palabras despues de la marca hasta el primer dato (max. 3): «charge 6 45W rms» -> «Charge 6»"""
    words = rest.split()
    if brand:
        bk = re.sub(r"[^a-z0-9]", "", _fold(brand))
        for i, w in enumerate(words):
            if re.sub(r"[^a-z0-9]", "", _fold(w)) == bk:
                words = words[i + 1 :]
                break
            # marcas de dos palabras (Western Digital, Thonet & Vander)
            two = re.sub(r"[^a-z0-9]", "", _fold(" ".join(words[i : i + 3])))
            if two.startswith(bk) and len(bk) > 4:
                n = 3 if two == bk else 2
                words = words[i + n :]
                break
        else:
            return ""
    out = []
    for i, w in enumerate(words):
        f = _fold(w).strip(",.;:()")
        if f in _STOP or re.fullmatch(r"\d+(?:[.,]\d+)?(?:w|mp|tb|gb|mah|va|v|mm|m|p|k|ch|hz|mbps|gbps|kg|a|l)", f) or not f:
            break
        nxt = _fold(words[i + 1]).strip(",.;:()") if i + 1 < len(words) else ""
        if f.isdigit() and nxt in UNIT_WORDS:  # «8 canales», «24 pulgadas», «305 m»: es un dato, no el modelo
            break
        out.append(_nice(w.strip(",.;:")))
        if len(out) == 3:
            break
    return " ".join(out)


def _attrs(family: str, text: str) -> list[str]:
    """1 o 2 datos segun la familia, solo si estan escritos"""
    f = " " + _fold(text) + " "
    out: list[str] = []

    def find(rx: str, fmt: str) -> None:
        m = re.search(rx, f)
        if m:
            out.append(fmt.format(*[g.replace(",", ".") for g in m.groups()]))

    if family == "camara":
        find(r"(?<![\d.])(\d{1,2}(?:[.,]\d)?)\s?mp\b", "{} MP") or None
        if not out and (m := re.search(r"\b([2-8])k\b", f)):
            out.append(f"{m.group(1)}K")
        for word, label in (("colorvu", "ColorVu"), ("acusense", "AcuSense"), ("full color", "Full Color"), (" ptz ", "PTZ"), (" poe ", "PoE"), ("wifi", "WiFi"), ("wi-fi", "WiFi")):
            if word in f:
                out.append(label)
                break
    elif family == "parlante":
        find(r"(?<![\d.])(\d{1,4})\s?w(?:atts?)?\b", "{} W")
        if "bluetooth" in f or " bt " in f:
            out.append("Bluetooth")
    elif family == "grabador":
        find(r"(?<!\d)(\d{1,2})\s?(?:-\s?)?(?:ch|canales|canal)\b", "{} canales")
        if " poe " in f or "/p " in f or re.search(r"\d+p\b", f):
            out.append("PoE")
    elif family == "switch":
        find(r"(?<!\d)(\d{1,2})\s?(?:puertos|ptos|port|-port)\b", "{} puertos")
        if " poe" in f:
            out.append("PoE")
    elif family == "ups":
        find(r"(?<!\d)(\d{3,4})\s?va\b", "{} VA")
    elif family == "disco":
        find(r"(?<![\d.])(\d{1,2})\s?tb\b", "{} TB") or find(r"(?<![\d.])(\d{3,4})\s?gb\b", "{} GB")
    elif family == "memoria":
        find(r"(?<![\d.])(\d{2,4})\s?gb\b", "{} GB")
    elif family == "wifi":
        m = re.search(r"wi-?fi\s?([4-7])\b", f) or re.search(r"\b(ax|ac|be)\s?\d{3,5}\b", f)
        if m:
            g = m.group(1)
            out.append({"ax": "WiFi 6", "ac": "WiFi 5", "be": "WiFi 7"}.get(g, f"WiFi {g}"))
    elif family == "powerbank":
        find(r"(?<![\d.])(\d{4,5})\s?mah\b", "{} mAh")
    return out[:2]


def name_family(tipo: str) -> str | None:
    t = _fold(tipo)
    for key, fam in (("camara", "camara"), ("videoportero", "camara"), ("grabador", "grabador"), ("switch", "switch"), ("ups", "ups"),
                     ("disco", "disco"), ("memoria", "memoria"), ("router", "wifi"), ("punto de acceso", "wifi"), ("repetidor", "wifi"),
                     ("parlante", "parlante"), ("barra de sonido", "parlante"), ("power bank", "powerbank")):  # fmt: skip
        if key in t:
            return fam
    return None


def _color(text: str) -> str | None:
    for w in _fold(text).split():
        w = w.strip(",.;:()")
        if w in COLORS:
            return COLORS[w]
    return None


def commercial_name(name: str, *, brand: str | None, model: str | None, shown_type: str | None, description: str | None = None) -> tuple[str | None, str | None]:
    """(nombre comercial o None, motivo para revisarlo o None). Ver la regla arriba."""
    from .brands import canonical_brand

    clean = _clean_source(name)
    if not clean:
        return None, None
    from .catalog_text import clean_model

    tipo, rest = _type_lead(clean)
    disp = TYPE_FROM_DISPLAY.get(shown_type or "")
    if tipo is None or (tipo == "Cámara" and disp and disp.startswith("Cámara")):
        tipo = disp or _singular(shown_type) or tipo
    marca = canonical_brand(brand)
    line = _line(rest, marca)
    model_s = clean_model(model)
    flags: list[str] = []
    if not line and (m := SUBLINE.search(clean)):
        line = f"{m.group(1).title()} {m.group(2).upper()}"
    if not line:
        line = model_s or ""
    if not tipo and not marca:
        return None, None
    if not tipo:
        flags.append("sin tipo")
    if not marca:
        flags.append("sin marca")
    if not line:
        flags.append("sin modelo")
    fam = name_family(tipo or shown_type or "")
    attrs = _attrs(fam, f"{name} {(description or '')[:600]}") if fam else []
    color = _color(name)
    head = " ".join(x for x in (tipo, marca, line) if x)
    if not head or head == tipo:
        return None, None
    # no repetir un dato que ya esta en el nombre (PTZ en «Cámara PTZ», PoE en «Switch PoE»)
    attrs = [a for a in attrs if _fold(a).replace(" ", "") not in _fold(head).replace(" ", "")]
    parts = [head, *attrs] + ([color] if color else [])
    out = " · ".join(parts)
    if len(out) > NAME_TARGET:
        out = " · ".join([head, *attrs[:1]] + ([color] if color else []))
    if len(out) > NAME_TARGET:
        flags.append("largo")
    if re.search(r"[A-Z0-9]{8,}$", out.replace("-", "")) and not (model_s and out.endswith(model_s)):
        flags.append("termina en código")
    return out[:120], (", ".join(flags) or None)


def _singular(label: str | None) -> str | None:
    """«Freidoras» -> «Freidora», «Asistentes de voz» -> «Asistente de voz»; sin plural claro, None"""
    words = (label or "").split()
    if not words:
        return None
    w = words[0]
    if w.lower().endswith(("ores", "ones")):
        w = w[:-2]
    elif w.lower().endswith("s") and len(w) > 4:
        w = w[:-1]
    else:
        return None
    return " ".join([w, *words[1:]])
