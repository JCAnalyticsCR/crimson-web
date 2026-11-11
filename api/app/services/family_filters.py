"""Filtros propios de cada familia (O3-04): cada familia tiene los suyos, con 4 grupos como maximo.

- Camaras: resolucion, interior o exterior, conexion (WiFi, PoE o analogica).
- Parlantes: potencia y resistencia al agua.
- Switches: puertos y PoE.
- UPS: capacidad (VA).
Los valores salen de lo que el producto ya dice (nombre, descripcion de tienda y ficha aprobada), con la misma regla en
el API (/catalog) y en el navegador (el snapshot lleva cada valor ya calculado en «fa» y la familia en «fam»: el
navegador no recalcula nada). Un dato que no esta escrito no se deduce: el producto queda fuera de ese grupo.
El tipo de equipo que se muestra (product_typing.display_type o el web_type corregido a mano) decide la familia; la
llave funcional de los kits (store_showcase.product_type) no se toca.
"""

from __future__ import annotations

import re

from .store_showcase import fold

MAX_GROUPS = 4
GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "camaras": (("resolucion", "Resolución"), ("ambiente", "Interior o exterior"), ("conexion", "Conexión")),
    "parlantes": (("potencia", "Potencia"), ("agua", "Resistencia al agua")),
    "switches": (("puertos", "Puertos"), ("poe", "PoE")),
    "ups": (("capacidad", "Capacidad"),),
}
# orden de los valores en el filtro (lo que no esta aqui va despues, por nombre)
ORDER = {
    "resolucion": ["1 MP", "2 MP", "3 MP", "4 MP", "5 MP", "6 MP", "8 MP (4K)", "12 MP"],
    "ambiente": ["Interior", "Exterior"],
    "conexion": ["WiFi", "PoE", "Analógica"],
    "potencia": ["Menos de 10 W", "10 a 30 W", "30 a 100 W", "100 W o más"],
    "puertos": ["4 puertos", "5 puertos", "8 puertos", "16 puertos", "24 puertos", "48 puertos"],
    "poe": ["Con PoE", "Sin PoE"],
    "capacidad": ["Hasta 750 VA", "751 a 1500 VA", "Más de 1500 VA"],
}

_MP = re.compile(r"(?<![\d.])(\d{1,2})(?:[.,]\d)?\s?mp\b")
_P = re.compile(r"\b(720|1080|1440|2160)\s?p\b")
_K = re.compile(r"\b([2-8])k\b")
_W = re.compile(r"(?<![\d.])(\d{1,4})(?:[.,]\d)?\s?w(?:atts?)?\b")
_WATER = re.compile(r"\bipx?\s?[4-8]\b|\bip6[5-8]\b|prueba de agua|resistente al agua|sumergible|waterproof")
_PORTS = re.compile(r"(?<![\d.])(\d{1,2})\s?(?:puertos|ptos|port|-port|p\b)")
_VA = re.compile(r"(?<!\d)(\d{3,4})\s?va\b")


def family(shown_type: str | None, leaf: str | None = None) -> str | None:
    t, lf = fold(shown_type), fold(leaf)
    if t.startswith("camara"):
        return "camaras"
    if "parlante" in t or "parlante" in lf or "bocina" in t:
        return "parlantes"
    if t.startswith("switch") and "hdmi" not in t:
        return "switches"
    if t == "ups":
        return "ups"
    return None


def _text(p) -> str:
    """nombre y descripcion de tienda (las dos van en las columnas livianas: sin consultas de mas por producto)"""
    return " " + fold(f"{p.name} {(p.description_store or '')[:800]}") + " "


def attrs(p, shown_type: str | None, leaf: str | None = None) -> dict[str, str]:
    """valor de cada grupo de su familia (solo lo escrito)"""
    fam = family(shown_type, leaf)
    if fam is None:
        return {}
    n = _text(p)
    out: dict[str, str] = {}
    if fam == "camaras":
        if m := _MP.search(n):
            mp = int(m.group(1))
            out["resolucion"] = "8 MP (4K)" if mp == 8 else f"{mp} MP"
        elif m := _K.search(n):
            out["resolucion"] = "8 MP (4K)" if m.group(1) == "4" else f"{m.group(1)}K"
        elif m := _P.search(n):
            out["resolucion"] = {"720": "1 MP", "1080": "2 MP", "1440": "4 MP", "2160": "8 MP (4K)"}[m.group(1)]
        if re.search(r"\b(exterior|outdoor|intemperie)\b|\bip6[5-8]\b|\bbullet\b", n):
            out["ambiente"] = "Exterior"
        elif re.search(r"\b(interior|indoor)\b", n):
            out["ambiente"] = "Interior"
        if fold(shown_type) == "camara analogica" or re.search(r"\b(turbo|hd-tvi|tvi|analog)", n):
            out["conexion"] = "Analógica"
        elif "wifi" in n or "wi-fi" in n or "inalambric" in n:
            out["conexion"] = "WiFi"
        elif " poe" in n:
            out["conexion"] = "PoE"
    elif fam == "parlantes":
        if m := _W.search(n):
            w = int(m.group(1))
            out["potencia"] = "Menos de 10 W" if w < 10 else "10 a 30 W" if w < 30 else "30 a 100 W" if w < 100 else "100 W o más"
        if _WATER.search(n):
            out["agua"] = "Resistente al agua"
    elif fam == "switches":
        if m := _PORTS.search(n):
            out["puertos"] = f"{int(m.group(1))} puertos"
        out["poe"] = "Con PoE" if re.search(r"\bpoe\b", n) else "Sin PoE"
    elif fam == "ups":
        if m := _VA.search(n):
            va = int(m.group(1))
            out["capacidad"] = "Hasta 750 VA" if va <= 750 else "751 a 1500 VA" if va <= 1500 else "Más de 1500 VA"
    return out


def dominant(fams: list[str | None], share: float = 0.6) -> str | None:
    """la familia de la pagina: la de la mayoria (60 % o mas) de lo que se esta viendo"""
    seen = [f for f in fams if f]
    if not fams or not seen:
        return None
    best = max(set(seen), key=seen.count)
    return best if seen.count(best) / len(fams) >= share else None


def parse(values: list[str] | None) -> dict[str, str]:
    """?f=grupo:valor (uno por grupo; el ultimo gana)"""
    out: dict[str, str] = {}
    for v in values or []:
        g, _, val = str(v).partition(":")
        if g and val:
            out[g.strip()[:30]] = val.strip()[:60]
    return out


def sort_values(group: str, counts: dict[str, int]) -> list[dict]:
    order = ORDER.get(group, [])
    return [{"name": k, "count": n} for k, n in sorted(counts.items(), key=lambda x: (order.index(x[0]) if x[0] in order else len(order), x[0]))]
