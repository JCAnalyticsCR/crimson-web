"""Marcas bien escritas (O3-02): una sola tabla para las altas nuevas, el filtro de la tienda y el guion de limpieza.

El proveedor escribe la marca como le sale («TP LINK», «Tp Link», «tplink», «JBL», «Jbl»); la tienda mostraba cada
grafia como una marca distinta en el filtro. La llave compacta (solo letras y numeros, en minuscula) junta todas las
formas y la tabla dice como se escribe la oficial. Una marca que no esta en la tabla queda como vino (si viene en
MAYUSCULAS gritadas, en Tipo Titulo), sin inventar nada.

Product.brand NO es fiscal (la factura no la lleva): corregirla no toca el codigo ni el nombre del comprobante.
"""

from __future__ import annotations

import re
import unicodedata

# grafia oficial de cada marca (como la escribe el fabricante). La llave se calcula con brand_key.
OFFICIAL: tuple[str, ...] = (
    "Hikvision", "HiLook", "HikMicro", "HikSemi", "EZVIZ", "TP-Link", "Mercusys", "D-Link", "Ubiquiti", "MikroTik",
    "Ruijie", "Reyee", "Grandstream", "ZKTeco", "Dahua", "Uniview", "Epcom", "Panduit", "LinkedPro", "Nexxt", "Cudy",
    "APC", "CDP", "Forza", "Argom", "JBL", "LG", "Samsung", "Xiaomi", "Govee", "Elgato", "XPG ADATA", "ADATA",
    "Kingston", "Seagate", "Western Digital", "Thonet & Vander", "Logitech", "Genius", "Ugreen", "Maono", "Razer",
    "Oraimo", "Amazon", "Smartbitt", "Winning Star", "Moonki", "Nova", "Logan", "Unno Tekno", "Klip Xtreme", "Xtech",
    "Anker", "Sony", "Philips", "Imou", "Paradox", "Tenda", "Tapo",
)  # fmt: skip
# otras formas que no se juntan solas por la llave compacta (abreviaturas y nombres viejos)
EXTRA: dict[str, str] = {
    "wd": "Western Digital",
    "westerndigitalwd": "Western Digital",
    "xpg": "XPG ADATA",
    "adataxpg": "XPG ADATA",
    "thonetvander": "Thonet & Vander",
    "thonetandvander": "Thonet & Vander",
    "hik": "Hikvision",
    "unnotekno": "Unno Tekno",
    "klipxtreme": "Klip Xtreme",
}


def brand_key(raw: object) -> str:
    """forma compacta para comparar: «Tp Link», «TP-LINK» y «tplink» -> «tplink»"""
    t = unicodedata.normalize("NFKD", str(raw or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t.replace("&", "and"))


_TABLE: dict[str, str] = {brand_key(b): b for b in OFFICIAL}
_TABLE.update({brand_key(b).replace("and", ""): b for b in OFFICIAL if "&" in b})
_TABLE.update(EXTRA)


def _shouting(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def canonical_brand(raw: object) -> str | None:
    """La marca como se escribe. Vacio -> None. Fuera de la tabla: tal cual, o en Tipo Titulo si viene gritada."""
    s = " ".join(str(raw or "").split())
    if not s:
        return None
    hit = _TABLE.get(brand_key(s))
    if hit:
        return hit
    return s.title() if _shouting(s) else s


def known(raw: object) -> bool:
    return brand_key(raw) in _TABLE
