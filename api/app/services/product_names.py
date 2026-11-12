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
    "tv": "TV", "rgb": "RGB", "rgbic": "RGBIC", "rgbicww": "RGBICWW", "tws": "TWS", "uhf": "UHF",
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
    "rapido": "rápido", "area": "área", "cache": "caché", "senal": "señal", "pulg": "pulg.", "videovigilancia": "videovigilancia",
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


# ---------------------------------------------------------------- nombre comercial (O3-03, regla «nombre_v5»)
# Regla: [Tipo] [Marca oficial] [linea o modelo] · [1 o 2 datos de la familia] · [paquete] · [color].
# Solo con lo que ya dice el nombre original: el tipo es el sustantivo con que empieza, el modelo es el codigo real y
# los datos, el color y el paquete se copian si estan escritos (el color tambien del codigo del fabricante: BLU, GRY).
# El nombre original queda para la factura (Product.name). Sale como borrador (services/catalog_drafts): lo dudoso va
# a revision con su motivo. nombre_v1 fallo la revision de la muestra real (> 2 % de error) y no se uso.
NAME_TARGET = 60  # la regla apunta a 60 caracteres o menos
NAME_LONG = 65  # mas que esto (despues de recortar datos) va a revision por largo

# erratas vistas en nombres del proveedor (se corrigen en el nombre comercial, nunca en el fiscal)
TYPOS = {"hikvusion": "Hikvision", "varifocaal": "varifocal", "reconomiento": "reconocimiento", "camra": "cámara", "inalambirco": "inalámbrico", "voltahe": "voltaje"}

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
    "dome", "online", "offline", "interactiva",
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
    re.compile(r"\bJBL[A-Z0-9]{6,}\b"),  # JBLPBENCORE22MICAM
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


# --- regla v2 («nombre_v2»): las piezas de abajo reemplazan a las de nombre_v1, que no paso la revision de la muestra
# real (datos inventados, restos de bandas y codecs, tipos de la categoria, mayusculas y colores perdidos).

# el sustantivo con que empieza el nombre manda; estas formas se escriben distinto (o sin la parte redundante)
V2_LEADS: tuple[tuple[str, str], ...] = (
    ("camara de seguridad wifi", "Cámara WiFi"), ("camara de seguridad ip", "Cámara IP"), ("camara de seguridad", "Cámara"),
    ("camara de vigilancia", "Cámara"), ("grabadora nvr", "Grabador NVR"), ("grabador nvr", "Grabador NVR"),
    ("grabadora dvr", "Grabador DVR"), ("grabador dvr", "Grabador DVR"), ("grabadora xvr", "Grabador XVR"), ("grabador xvr", "Grabador XVR"),
    ("hd interno", "Disco duro"), ("hd externo", "Disco duro externo"), ("modulo sfp", "Módulo SFP"), ("switch hdmi", "Switch HDMI"),
    ("sistema wifi", "Sistema WiFi"),
)  # fmt: skip
# sustantivos que abren un nombre fuera de seguridad y redes (cocina, cuidado personal, energia, iluminacion...)
NOUNS = {
    "cuchara": "Cuchara", "cucharon": "Cucharón", "cacerola": "Cacerola", "olla": "Olla", "sarten": "Sartén", "cuchillo": "Cuchillo",
    "espatula": "Espátula", "colador": "Colador", "tabla": "Tabla", "juego": "Juego", "set": "Set", "plato": "Plato", "vaso": "Vaso",
    "taza": "Taza", "botella": "Botella", "termo": "Termo", "cafetera": "Cafetera", "tostadora": "Tostadora", "batidora": "Batidora",
    "exprimidor": "Exprimidor", "plancha": "Plancha", "secadora": "Secadora", "secador": "Secador", "bascula": "Báscula",
    "cepillo": "Cepillo", "recortadora": "Recortadora", "conversor": "Conversor", "inversor": "Inversor", "protector": "Protector",
    "supresor": "Supresor", "amplificador": "Amplificador", "barra": "Barra", "lampara": "Lámpara", "reflector": "Reflector",
    "estuche": "Estuche", "funda": "Funda", "carcasa": "Carcasa", "base": "Base", "control": "Control", "tarjeta": "Tarjeta",
    "impresora": "Impresora", "tableta": "Tableta", "reloj": "Reloj", "pulsera": "Pulsera", "antena": "Antena",
    "regleta": "Regleta", "boton": "Botón", "modulo": "Módulo", "espumadera": "Espumadera", "pinza": "Pinza", "rallador": "Rallador",
}  # fmt: skip
_DE = {"de", "del", "sin"}  # «Detector de humo», «Botón sin contacto»
_ADJ_END = ("ico", "ica", "icos", "icas", "ado", "ada", "ido", "ida", "ente", "ble", "al", "ivo", "iva", "era", "ero", "oso", "osa", "ante")
_NOT_ADJ = {"recargable", "profesional", "original", "nuevo", "nueva", "portatil", "general", "digital", "total", "lateral"}
_ADJ_PLACE = {"interior", "exterior"}  # «Monitor interior de videoportero»: sin ellos se lee como otro equipo
# adjetivo pegado DESPUES de la marca que es del tipo («Afeitadora xiaomi eléctrica S101»): solo terminaciones claras
_ADJ_AFTER_BRAND = ("ico", "ica", "icos", "icas", "ble", "ente")  # no «-ada»: «omada» es una linea
# camara analogica: el prefijo del modelo (HiLook THC-, Hikvision DS-2CE) o el texto lo dice
_ANALOG = re.compile(r"(?<![\w-])i?(?:thc-|ds-2ce)|\b4 en 1\b|\bhd-?tvi\b|\bturbo hd\b|\b(?:ahd|cvi|cvbs)\b|\banalog", re.I)
# marcas de lote del proveedor pegadas al codigo: «(HILOOK std)», «(AM-STD)», «(Mul)», «(O-NEU)»
_LOT_V3 = re.compile(r"\((?:hilook|hik|ezviz)?[\s-]*(?:am-|o-)?std\)|\((?:hilook|mul)\)|\(o-[a-z]{2,4}\)", re.I)
_WIFI_TYPES = ("Router", "Punto de acceso", "Repetidor WiFi", "Sistema WiFi mesh", "Adaptador USB")
_REDUNDANT_WIFI = {"inalambrico", "inalambrica", "wireless"}
# lo que va detras de la marca y no es la linea ni el modelo: se salta (rasgos) o corta (region, bandas, codecs)
REGIONS = {"us", "eu", "un", "au", "la", "uk"}
_REGION_SUFFIX = re.compile(r"(?:-(?:us|eu|un|au|la|uk)|\((?:us|eu|un|au|la|uk)\))$", re.I)
LINE_SKIP = {"led", "rgb", "rgbic", "rgbicww", "rgbww", "tws", "smart"}
LINE_AFTER_CODE = {"pro", "max", "plus", "mini", "ultra", "nano", "select", "outdoor", "air", "x", "se", "neo", "go"}
LINE_CASE = {"partybox": "PartyBox", "freepods": "FreePods", "powercore": "PowerCore", "smartshaver": "SmartShaver", "deathadder": "DeathAdder"}
_UNIT = r"(?:w|mp|mpx|tb|gb|mb|mah|ah|va|kva|v|vdc|vac|mm|cm|m|mts|p|k|ch|hz|ghz|mhz|mbps|gbps|kg|g|a|l|u|pulg)"
# siglas tecnicas: si quedan sueltas en la linea o el modelo, el nombre va a revision (RJ45, IP, 1080P, 3.6MM, 2MPX)
TECH = {"rj45", "rj11", "ip", "poe", "poe+", "usb", "hdmi", "lan", "wan", "wifi", "wi-fi", "led", "rgb", "rgbic", "hd", "fhd", "uhd", "ir",
        "sfp", "utp", "ftp", "dwdr", "wdr", "lcd", "avr", "ac", "dc", "vac", "vdc", "sata", "nvr", "dvr", "xvr", "cmos", "gbe"}  # fmt: skip
# prefijos de codigo que el proveedor separa del resto («tp link tl WR941HP» -> TL-WR941HP)
CODE_PREFIX = {"tl": "TL", "ds": "DS", "cs": "CS", "hs": "HS"}
# palabras cortas que si pueden ir antes de un codigo (no son un prefijo suelto)
SHORT_OK = {"pro", "max", "mini", "air", "plus", "ax", "mi", "go", "x", "se", "neo", "lt"}
_DATUM = re.compile(rf"\d+(?:[.,]\d+)?{_UNIT}")
_CODE = re.compile(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9+]+(?:\.\d+[A-Za-z0-9]*)?)*(?:\([A-Za-z0-9.\-]+\))?")
_PACK = re.compile(r"\(?\b(\d{1,2})\s*-?\s*pack\b\)?|\bpaquete de (\d{1,2})\b", re.I)
_CODE_COLOR = re.compile(r"[A-Z]{2,}[A-Z0-9]*?(BLACK|BLUE|WHITE|BLK|BLU|WHT|GRY|RED|GRN|PNK|CAMO)(?:[A-Z]{2,3})?")
# color o variante que el generador no traduce: si esta en el original y no en el nombre, va a revision
COLORS_EN = {"black", "white", "blue", "red", "gray", "grey", "pink", "green", "purple", "silver", "gold", "orange", "yellow"}
_CODE_VARIANT = re.compile(r"[A-Z]{2,}[A-Z0-9]*?(SQUAD|TEAL|PURPLE|ORANGE|SILVER|GOLD|PINK)(?:[A-Z]{2,3})?")
CODE_COLORS = {"BLK": "negro", "BLACK": "negro", "BLU": "azul", "BLUE": "azul", "WHT": "blanco", "WHITE": "blanco", "GRY": "gris",
               "RED": "rojo", "GRN": "verde", "PNK": "rosado", "CAMO": "camuflado"}  # fmt: skip
# datos que pueden venir de la ficha aprobada: solo con la clave exacta y del tipo que calza. Nunca color, PoE ni potencia.
FICHA_KEYS = {"disco": ("Capacidad",), "memoria": ("Capacidad",), "ups": ("Capacidad",), "powerbank": ("Capacidad de la batería", "Capacidad"),
              "grabador": ("Canales",), "switch": ("Puertos",)}  # fmt: skip


def _common(w: str) -> str:
    """palabra comun en minuscula, con la tilde que le toca si vino sin ella"""
    f = _fold(w)
    return w.lower() if f != w.lower() else ACCENTS.get(f, f)


def _adjective(w: str) -> bool:
    f = _fold(w)
    if f in _ADJ_PLACE:
        return True
    return f.isalpha() and len(f) >= 5 and f.endswith(_ADJ_END) and f not in _NOT_ADJ and f not in _STOP - {"inalambrico", "inalambrica"}


def _type_v2(clean: str, brand: str | None) -> tuple[str | None, str | None, list[str]]:
    """(tipo escrito bien, el mismo sin adjetivos, palabras que siguen) segun el sustantivo con que empieza el nombre:
    «Detector de humo inalambrico» -> («Detector de humo inalámbrico», «Detector de humo», resto); sin uno, (None, None, todo)"""
    from .brands import brand_key, known

    words = clean.split()
    f = _fold(clean)
    label, n = None, 0
    for lead, lab in (*V2_LEADS, *TYPE_LEADS):
        if f == lead or f.startswith(lead + " "):
            label, n = lab, len(lead.split())
            break
    if label is None and words and _fold(words[0]) in NOUNS:
        label, n = NOUNS[_fold(words[0])], 1
    if label is None:
        return None, None, words
    rest = words[n:]
    if _fold(label) != " ".join(_fold(w) for w in words[:n]) or label.startswith("Cámara"):
        return label, label, rest  # el tipo ya se escribe distinto del nombre (Grabadora -> Grabador): no se le suma nada
    bk = brand_key(brand) if brand else ""

    def brandish(w: str) -> bool:
        k = brand_key(w)
        return bool(k) and (k == bk or known(w) or bool(bk) and bk.startswith(k) and len(k) > 3)

    extra: list[str] = []
    i = 0
    # un adjetivo de lugar antes del complemento («Monitor interior de videoportero»)
    if len(rest) > 2 and _fold(rest[0]) in _ADJ_PLACE and _fold(rest[1]) in _DE:
        extra, i = [_common(rest[0])], 1
    # el complemento «de X» (uno o dos: «Detector de rotura de vidrio») y despues los adjetivos, hasta 5 palabras
    while (
        len(rest) > i + 1
        and n + len(extra) + 2 <= 5
        and _fold(rest[i]) in _DE
        and _fold(rest[i + 1]).isalpha()
        and not brandish(rest[i + 1])
        and _fold(rest[i + 1]) not in _STOP
    ):
        extra, i = [*extra, rest[i].lower(), _common(rest[i + 1])], i + 2
    short = " ".join([label, *extra])
    while i < len(rest) and n + len(extra) < 5 and _adjective(rest[i]) and not brandish(rest[i]):
        if not (label in _WIFI_TYPES and _fold(rest[i]) in _REDUNDANT_WIFI):
            extra.append(_common(rest[i]))
        i += 1
    return " ".join([label, *extra]), short, rest[i:]


def _split_brand(words: list[str], brand: str | None) -> tuple[list[str], list[str] | None]:
    """(palabras antes de la marca, palabras despues) o (todo, None) si la marca no esta escrita en el nombre"""
    if not brand:
        return words, None
    bk = re.sub(r"[^a-z0-9]", "", _fold(brand))
    for i, w in enumerate(words):
        if re.sub(r"[^a-z0-9]", "", _fold(w)) == bk:
            return words[:i], words[i + 1 :]
        two = re.sub(r"[^a-z0-9]", "", _fold(" ".join(words[i : i + 3])))  # marcas de dos palabras (Winning Star, TP Link)
        if two.startswith(bk) and len(bk) > 4:
            n = 3 if two == bk else 2
            return words[:i], words[i + n :]
    return words, None


def _is_code(tok: str) -> bool:
    """codigo de fabricante: letras y numeros (DS-7616NI-Q2/16P, PB502, EAP245) o siglas con guiones (ds-pdsmk-e-wb)"""
    if not _CODE.fullmatch(tok):
        return False
    if re.search(r"\d", tok) and re.search(r"[A-Za-z]", tok):
        return True
    parts = tok.split("-")
    return len(parts) >= 3 and all(p.isalpha() and len(p) <= 6 for p in parts)


def _datum(tok: str, nxt: str) -> bool:
    """dato y no nombre: 45W, 4MP, 2000VA/2000W, 1080P, 16-CH, 1C1A, H.265, 4MP.H.265, 2.4 GHz, codigos de bodega (54044)"""
    f = _fold(tok).strip(",.;:")
    first = f.split("/")[0].strip("()")
    if _DATUM.fullmatch(first) or re.fullmatch(r"\d+-?ch", first) or re.fullmatch(r"\d+c\d+a|\d+a\d+c", f):
        return True
    if re.fullmatch(r"h\.?26[45]\+?", f) or re.search(r"[a-z]\.|\.[a-z]", f):
        return True
    if re.fullmatch(r"\d+(?:[.,]\d+)?", first) and (nxt in UNIT_WORDS or nxt in {"ghz", "mhz", "hz", "gb", "tb", "mm"} or "/" in f):
        return True
    return bool(re.fullmatch(r"\d{5,}|\d+-\d{5,}", f))


def _nice_v2(tok: str, code: bool) -> str:
    low = _fold(tok)
    for table in (ACRONYMS, BRANDS, LINE_CASE):
        if low in table:
            return table[low]
    if code:
        if tok == tok.lower() and tok[:1].isalpha():
            return tok.upper()  # «lt500», «ds-pdsmk-e-wb»: el codigo va en mayusculas
        return tok.upper() if re.fullmatch(r"[a-z]{1,3}\d+[a-z]?", low) else tok
    return tok[:1].upper() + tok[1:].lower() if tok.islower() or tok.isupper() else tok


def _line_v2(words: list[str], shouting_src: bool, limit: int = 3) -> list[str]:
    """la linea y el modelo que siguen a la marca, hasta el primer dato: «charge 5 40W» -> Charge 5,
    «archer VR400 2.4 GHz» -> Archer VR400, «LT500D-AU 2.0 5GHZ» -> LT500D, «C320WS eu» -> C320WS"""
    words = list(words)
    out: list[str] = []
    has_code = False
    for i, w in enumerate(words):
        raw = w.strip(",;:")
        raw = raw[:-1] if raw.endswith(".") and not re.search(r"\d\.$", raw) else raw
        if not raw or raw in {"/", "+", "-", "|", "&"}:
            break
        region = bool(_REGION_SUFFIX.search(raw)) and len(raw) > 4
        if region:
            raw = _REGION_SUFFIX.sub("", raw)
        f = _fold(raw).strip("()")
        nxt = _fold(words[i + 1]).strip(",.;:()") if i + 1 < len(words) else ""
        if f in LINE_SKIP:
            continue
        if f in REGIONS or f in _STOP or f in TECH or re.fullmatch(r"ip[x\d]\d|rj\d\d", f):
            break
        if f in CODE_PREFIX and i + 1 < len(words) and _is_code(words[i + 1].strip(",;:")) and not words[i + 1].startswith("-"):
            words[i + 1] = f"{CODE_PREFIX[f]}-{words[i + 1]}"  # «tl WR941HP» -> TL-WR941HP (el original lo dice asi)
            continue
        k_name = re.fullmatch(r"[2-8]k", f) and nxt.isalpha() and nxt not in _STOP  # «4K Select» es nombre; «2K interior», dato
        if not k_name and _datum(raw, nxt):
            break
        code = _is_code(raw)
        if has_code and not code:
            if re.fullmatch(r"\d+(?:[.,]\d+)?", f):
                break  # «1.0», «2.0»: version de hardware; «2.4»: banda
            if f not in LINE_AFTER_CODE and (shouting_src or not raw[:1].isupper()):
                break  # «ST-7567 silicona», «PD100 alambrico»: lo que sigue al modelo en minuscula es descripcion
        out.append(_nice_v2(raw, code))
        has_code = has_code or code
        if region or len(out) >= limit:
            break
    return out


def _first_code(words: list[str]) -> str | None:
    """el primer codigo de fabricante del resto (antes de «compatible con»: lo que sigue son otros equipos)"""
    for i, w in enumerate(words):
        raw = w.strip(",.;:")
        if _fold(raw).startswith("compatible"):
            return None
        raw = _REGION_SUFFIX.sub("", raw) if len(raw) > 4 else raw
        nxt = _fold(words[i + 1]).strip(",.;:()") if i + 1 < len(words) else ""
        f = _fold(raw)
        if f in TECH or re.fullmatch(r"ip[x\d]\d|rj\d\d|\d+-?ch|[2-8]k", f):
            continue
        if _is_code(raw) and not _datum(raw, nxt):
            tail = words[i + 1].strip(",.;:") if i + 1 < len(words) else ""
            if _is_code(tail) and len(re.sub(r"[^A-Za-z0-9]", "", tail)) <= 3:
                return f"{_nice_v2(raw, True)} {tail.upper()}"  # «WM600 C2»: el codigo corto pegado es parte del modelo
            return _nice_v2(raw, True)
    return None


def name_family(tipo: str) -> str | None:
    t = _fold(tipo)
    if t.startswith("monitor"):
        return None  # «Monitor interior de videoportero»: no es una camara (sin MP ni WiFi en el nombre comercial)
    for key, fam in (("camara", "camara"), ("videoportero", "camara"), ("grabador", "grabador"), ("switch", "switch"), ("ups", "ups"),
                     ("disco", "disco"), ("memoria", "memoria"), ("router", "wifi"), ("punto de acceso", "wifi"), ("repetidor", "wifi"),
                     ("parlante", "parlante"), ("barra de sonido", "parlante"), ("power bank", "powerbank"), ("bateria", "bateria")):  # fmt: skip
        if key in t:
            return fam
    return None


def _attrs(family: str, text: str, tipo: str = "", specs: dict | None = None) -> list[str]:
    """1 o 2 datos segun la familia, solo los que estan escritos en el nombre (o, si no, en la ficha con la clave
    exacta y del tipo que calza). Nunca se suponen color, PoE ni potencia."""
    f = " " + _fold(text) + " "
    out: list[str] = []

    def find(rx: str, fmt: str, src: str = f) -> bool:
        m = re.search(rx, src)
        if m:
            out.append(fmt.format(*[g.replace(",", ".") for g in m.groups()]))
        return bool(m)

    slot = {
        "grabador": (r"(?<![\d.])(\d{1,2})\s?(?:-\s?)?(?:ch|canales|canal)\b", "{} canales"),
        "switch": (r"(?<![\d.])(\d{1,2})\s?(?:puertos|ptos|port|-port)\b", "{} puertos"),
        "ups": (r"(?<![\d.])(\d{3,4})\s?va\b", "{} VA"),
        "memoria": (r"(?<![\d.])(\d{2,4})\s?gb\b", "{} GB"),
        "powerbank": (r"(?<![\d.])(\d{4,5})\s?mah\b", "{} mAh"),
    }
    if family == "camara":
        find(r"(?<![\d.])(\d{1,2}(?:[.,]\d)?)\s?mpx?\b", "{} MP")
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
        if re.search(r"\b(?:dos|2)\s+microfonos\b", f):
            out.append("2 micrófonos")
    elif family in slot:
        find(*slot[family])
        if family == "grabador" and re.search(r"\bpoe\b", f) and "DVR" not in tipo and "XVR" not in tipo and not re.search(r"\b[dx]vr\b", f):
            out.append("PoE")  # PoE solo si lo dice el nombre, y nunca en un DVR
        if family == "switch" and re.search(r"\bpoe\b", f):
            out.append("PoE+" if "poe+" in f else "PoE")
    elif family == "disco":
        find(r"(?<![\d.])(\d{1,2})\s?tb\b", "{} TB") or find(r"(?<![\d.])(\d{3,4})\s?gb\b", "{} GB")
    elif family == "wifi":
        if re.search(r"\b4g\b", f):
            out.append("4G")
        m = re.search(r"wi-?fi\s?([4-7])\b", f) or re.search(r"\b(ax|ac|be)\s?\d{3,5}\b", f)
        if m:
            g = m.group(1)
            out.append({"ax": "WiFi 6", "ac": "WiFi 5", "be": "WiFi 7"}.get(g, f"WiFi {g}"))
    elif family == "bateria":
        find(r"(?<![\d.])(\d{1,3})\s?ah\b", "{} Ah")
        find(r"(?<![\d.])(\d{1,2})\s?v\b", "{} V")
    if family in FICHA_KEYS and specs and not any(re.match(r"\d", a) for a in out):
        for key in FICHA_KEYS[family]:
            val = specs.get(key)
            rx = slot.get(family) or ((r"(?<![\d.])(\d{1,2})\s?tb\b", "{} TB") if family == "disco" else None)
            if isinstance(val, str) and rx and find(rx[0], rx[1], " " + _fold(val) + " "):
                out.insert(0, out.pop())
                break
    return out[:2]


def _color(text: str) -> str | None:
    """el color escrito («negro», «blanca») o el del codigo del fabricante (JBLCHARGE5BLUAM -> azul)"""
    prev = ""
    for w in _fold(text).split():
        w = w.strip(",.;:()")
        if w in COLORS and prev not in {"luz", "luces"}:  # «luz blanca» es la iluminacion, no el color
            return COLORS[w]
        prev = w
    for tok in text.split():
        tok = tok.strip(",.;:()")
        m = _CODE_COLOR.fullmatch(tok)
        if m and len(tok) >= 9 and re.search(r"\d", tok):
            return CODE_COLORS[m.group(1)]
    return None


def _camera_evidence(disp: str, clean: str) -> bool:
    """el tipo mostrado de camara se usa solo si el nombre lo dice"""
    f = " " + _fold(clean) + " "
    return {
        "Cámara IP": bool(re.search(r"\bip\b|(?<![\w-])i?ds-2c[dv]|(?<![\w-])ipc-", f)),
        "Cámara WiFi": bool(re.search(r"wi-?fi|inalambric", f)),
        "Cámara PTZ": " ptz " in f,
        "Cámara analógica": bool(_ANALOG.search(f) or " turbo " in f),
    }.get(disp, False)


def _alnum(s: str) -> int:
    return len(re.sub(r"[^A-Za-z0-9]", "", s))


def _check_line(line: str, name: str, model_s: str | None) -> list[str]:
    """Validacion final de la linea y el modelo (lo unico del nombre que no sale de una tabla): lo dudoso va a revision.
    - el modelo (el codigo mas largo o, sin codigo, la linea entera) tiene menos de 4 letras o numeros, o termina en «/»;
    - queda un prefijo suelto de 1 a 3 letras pegado al codigo («Tl WR941HP»);
    - queda una sigla o un dato tecnico suelto (RJ45, IP, 1080P, 3.6MM, 2MPX);
    - una palabra no esta en el nombre original."""
    if not line:
        return []
    out: list[str] = []
    toks = line.split()
    codes = [t for t in toks if _is_code(t)]
    model_tok = max(codes, key=_alnum) if codes else line
    if _alnum(model_tok) < 4 or any(t.endswith("/") for t in toks):
        out.append("modelo dudoso")
    src = re.sub(r"[^a-z0-9]", "", _fold(name))
    for i, t in enumerate(toks):
        f = _fold(t)
        if f in TECH or _datum(t, "") or re.fullmatch(r"ip[x\d]\d|rj\d\d|\d+p", f):
            out.append("dato técnico suelto")
        if re.fullmatch(r"[a-z]{1,3}", f) and f not in SHORT_OK and i + 1 < len(toks) and _is_code(toks[i + 1]):
            out.append("prefijo suelto")
        if re.sub(r"[^a-z0-9]", "", f) not in src:
            out.append("modelo sin origen en el nombre" if t == model_s or t in (model_s or "").split() else "palabra sin origen")
    return list(dict.fromkeys(out))


# nombre_v5: tokens tecnicos que tienen forma de codigo pero no son el del fabricante (AC1200, 10000MAH, 850NM, IP67...)
_TECH_CODE = re.compile(
    r"(?:ac|ax|axe|be|n)\d{3,5}|\d[\d.,-]*[a-z]{1,3}|\d+[ac]\d+[ac]|[1i]p[x\d]\d|rj\d\d|cat\d\w?|rs-?\d{3}|iec-?\d+|lifepo4"
    r"|h\.?26[45]\+?|\d+-?pack|\d+(?:canales|puertos|ch)|wi-?fi\d|usb\d(?:\.\d)?|dc\d+v"
)


def _maker_codes(clean: str) -> list[str]:
    """los codigos de fabricante del original: letras y digitos, 4 o mas, con o sin guiones o «+». Fuera los de bodega
    o proveedor (5 digitos o mas, ...-WST-..., EAN, codigo pegado de 7 digitos o mas) y los tecnicos (AC1200, 1080P...)"""
    out: list[str] = []
    for tok in clean.split():
        for part in tok.strip(",.;:").split("/"):
            core = re.sub(r"\(.*$", "", part.lstrip("(")).rstrip("+")
            core = _REGION_SUFFIX.sub("", core) if len(core) > 4 else core
            f = _fold(core)
            if not (_CODE.fullmatch(core) and re.search(r"\d", f) and re.search(r"[a-z]", f) and _alnum(core) >= 4):
                continue
            if re.search(r"\d{7,}", f) or "-wst-" in f or re.fullmatch(r"\d{5,}[a-z]?", f) or _TECH_CODE.fullmatch(f) or _datum(core, ""):
                continue
            out.append(core)
    return out


def _pack(text: str) -> str | None:
    m = _PACK.search(text)
    n = m and (m.group(1) or m.group(2))
    return f"paquete de {int(n)}" if n and int(n) > 1 else None


def _singular_v2(label: str | None) -> str | None:
    s = _singular(label)
    if not s:
        return None
    words = s.split()
    return " ".join([words[0], *(w if (_fold(w) in ACRONYMS or w.isupper()) else w.lower() for w in words[1:])])


def commercial_name(
    name: str, *, brand: str | None, model: str | None, shown_type: str | None, description: str | None = None, specs: dict | None = None
) -> tuple[str | None, str | None]:
    """(nombre comercial o None, motivo para revisarlo o None). Regla «nombre_v5»:
    [Tipo] [Marca] [linea o modelo] · [1 o 2 datos de la familia] · [paquete] · [color]
    - Tipo: el sustantivo con que empieza el nombre (Grabadora -> Grabador, sin «de seguridad»); si no lo hay, el tipo
      mostrado de seguridad y redes; si tampoco, la categoria en singular y a revision («tipo por categoría»). Sin tipo
      no hay nombre (None, «sin tipo»).
    - Modelo: el codigo real; fuera bandas sueltas, codecs, «-CH», sufijos de region y versiones de hardware.
    - Datos: solo los escritos en el nombre (o en la ficha con la clave exacta). Color y paquete si los dice el original.
    description no se usa (nombre_v1 sacaba la potencia de ahi y la inventaba en productos que no la decian)."""
    from .brands import canonical_brand
    from .catalog_text import clean_model

    clean = " ".join(_LOT_V3.sub(" ", _clean_source(name)).split())
    if not clean:
        return None, None
    marca = canonical_brand(brand)
    flags: list[str] = []
    tipo, short, rest = _type_v2(_PACK.sub(" ", clean), marca)
    disp = TYPE_FROM_DISPLAY.get(shown_type or "")
    from_name = tipo is not None
    deduced = False
    if tipo is None:
        tipo, deduced = disp, disp is not None
    elif tipo == "Cámara" and disp and disp.startswith("Cámara") and _camera_evidence(disp, clean):
        tipo = short = disp  # «Cámara IP» solo si el nombre lo sostiene (IP, DS-2CD...); 1080P o 3.6MM no dicen que sea IP
    if tipo in ("Cámara", "Cámara IP") and _ANALOG.search(clean):
        tipo = short = "Cámara analógica"  # THC-, DS-2CE, «4 en 1», HD-TVI, Turbo HD, AHD/CVI/CVBS
    if tipo == "Switch" and re.search(r"\bhdmi\b", _fold(clean)):
        tipo = short = "Switch HDMI"  # «Switch Nova ... switch HDMI 5 puertos»
    by_category = False
    if tipo is None:
        tipo = _singular_v2(shown_type)
        by_category = tipo is not None
    if not tipo:
        return None, ("sin tipo" if marca else None)
    src_shout = shouting(clean)
    pre, post = _split_brand(rest, marca)
    if post and from_name and tipo == short and not tipo.startswith(("Cámara", "Grabador")):
        # «Afeitadora xiaomi eléctrica S101»: el adjetivo en minuscula que sigue a la marca es del tipo
        moved: list[str] = []
        while post and post[0][:1].islower() and _adjective(post[0]) and _fold(post[0]).endswith(_ADJ_AFTER_BRAND):
            w = post.pop(0)
            if not (tipo in _WIFI_TYPES and _fold(w) in _REDUNDANT_WIFI):
                moved.append(_common(w))
        tipo = short = " ".join([tipo, *moved])
    line_words = _line_v2(post, src_shout) if post is not None else []
    if by_category and post is not None and pre:
        line_words = _line_v2(pre, src_shout) + line_words  # «Fire TV Stick amazon 4K Select»: la linea va antes de la marca
    line = " ".join(line_words)
    model_s = clean_model(model)
    if model_s and any(rx.fullmatch(model_s) for rx in _TRAIL_CODES[:2]):
        model_s = None  # un ASIN o un EAN no es un modelo
    if not line and (m := SUBLINE.search(clean)):
        line = f"{m.group(1).title()} {m.group(2).upper()}"
    if not line and model_s and _fold(model_s) in _fold(clean):
        line = model_s
    if not line and post is not None:
        line = _first_code(post) or ""
    if not line:
        line = model_s or ""
    if by_category:
        flags.append("tipo por categoría")
    if deduced:
        flags.append("tipo deducido, no es el del nombre")
    if not marca:
        flags.append("sin marca")
    if not line:
        flags.append("sin modelo")
    fam = name_family(tipo)
    attrs = _attrs(fam, clean, tipo, specs) if fam else []
    extras = [x for x in (_pack(clean), _color(name)) if x]
    head = " ".join(x for x in (tipo, marca, line) if x)
    if head == tipo:
        return None, None
    # no repetir un dato que ya esta en el nombre (PTZ en «Cámara PTZ», PoE en «Switch PoE»)
    attrs = [a for a in attrs if _fold(a).replace(" ", "") not in _fold(head).replace(" ", "")]
    out = " · ".join([head, *attrs, *extras])
    if len(out) > NAME_TARGET:
        out = " · ".join([head, *attrs[:1], *extras])
    if len(out) > NAME_TARGET and short and short != tipo:  # largo: el tipo sin sus adjetivos («Detector de humo»)
        head = " ".join(x for x in (short, marca, line) if x)
        out = " · ".join([head, *attrs[:1], *extras])
    if len(out) > NAME_LONG:
        flags.append("largo")
    last = head.split()[-1]
    if "-" not in last and len(last) >= 8 and _is_code(last) and last != model_s:
        flags.append("termina en código")
    flags += [r for r in _check_line(line, name, model_s) if r not in flags]
    line_f = set(_fold(line).split())
    if _color(name) is None and any(w in COLORS_EN and w not in line_f for w in _fold(name).replace("/", " ").split()):
        flags.append("color sin traducir")  # «aluminum black»: el original trae color y el nombre no lo lleva
    if any(_CODE_VARIANT.fullmatch(t.strip(",.;:()")) for t in name.split()):
        flags.append("variante en el código")  # JBLCHARGE6SQUADAM
    if re.search(r"\bkit\b", _fold(name)) and "kit" not in _fold(out):
        flags.append("variante sin indicar (kit)")  # «Tapo C410 kit ... con panel solar» no es solo la camara
    shown = re.sub(r"[^a-z0-9]", "", _fold(out))
    if any(re.sub(r"[^a-z0-9]", "", _fold(c)) not in shown for c in _maker_codes(clean)):
        flags.append("código del fabricante fuera del nombre")  # «TL-R480T+», «H6065», «OPC-TR13N» se perdian
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
