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
