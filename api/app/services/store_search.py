"""Buscador de la tienda (O1-14): UNA sola regla para el servidor (/catalog) y el navegador (catalogFilter.ts).

Normalizacion: minusculas y sin tildes; palabras = tramos de letras y numeros; raiz = sin plural (es / s) y sin la
vocal final (camara y camaras -> camar; grabador y grabadores -> grabador; parlante y parlantes -> parlant). Version
compacta = solo letras y numeros (tp-link, tp link y tplink -> tplink; DS-2CD1047G3 -> ds2cd1047g3).

Una palabra buscada coincide con un producto si su raiz (o la de un sinonimo) es el comienzo de alguna palabra del
producto, o si su version compacta aparece dentro de la version compacta del producto (codigos y modelos escritos
de cualquier manera). Todas las palabras tienen que coincidir. La busqueda entera en compacto tambien vale
(«tp link» encuentra «TP-Link»).

Relevancia: primero coincidencia en modelo o codigo, luego todas las palabras en el nombre, luego marca o categoria.

El snapshot lleva la tabla de sinonimos una vez (syn) y por producto el campo sf con nombre, marca, modelo, codigo,
SKU, categoria y tipo ya normalizados; la version compacta se deriva de sf en cada lado (no viaja: pesa).
Si cambias algo aqui, cambia lo mismo en portal/src/modules/store/catalogFilter.ts: la prueba de paridad
(tests/test_tienda_busqueda.py y __tests__/busqueda.test.ts sobre el mismo archivo de casos) lo exige.
"""

from __future__ import annotations

import re
import unicodedata

# grupos de sinonimos, escritos como se buscan (se normalizan al cargar). Uno por linea.
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("parlante", "bocina", "altavoz"),
    ("repetidor", "extensor"),
    ("timbre", "videoportero"),
    ("tplink", "tp-link"),
    ("wifi", "wi-fi", "inalambrico"),
    ("wifi6", "wifi 6"),
    ("grabador", "grabadora", "nvr", "dvr"),
    ("camara", "camera", "cam"),
    ("disco", "hdd"),
    ("ups", "back-ups"),
    ("acceso", "lector", "facial"),
)

_WORD = re.compile(r"[a-z0-9]+")


def fold(s: object) -> str:
    """minusculas y sin tildes"""
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def words(s: object) -> list[str]:
    return _WORD.findall(fold(s))


def compact(s: object) -> str:
    return "".join(words(s))


def stem(w: str) -> str:
    """raiz de una palabra: sin plural y sin la vocal final. Los numeros y las palabras cortas quedan igual."""
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("es") and len(w) > 4 and w[-3] not in "aeiou":
        w = w[:-2]
    elif w.endswith("s"):
        w = w[:-1]
    if len(w) > 3 and w[-1] in "aeo":
        w = w[:-1]
    return w


def _syn_table() -> dict[str, list[str]]:
    """raiz (o compacto) -> las raices de su grupo"""
    out: dict[str, list[str]] = {}
    for g in SYNONYM_GROUPS:
        keys = sorted({k for x in g for k in (stem(compact(x)), compact(x))})
        stems = sorted({stem(compact(x)) for x in g} | {compact(x) for x in g})
        for k in keys:
            out[k] = stems
    return out


SYNONYMS: dict[str, list[str]] = _syn_table()


def search_field(*parts: object) -> str:
    """campo de busqueda del snapshot: las palabras normalizadas, separadas por un espacio"""
    return " ".join(w for p in parts for w in words(p))


def split_query(q: str) -> list[str]:
    return words(q)[:8]


def term_matches(term: str, stems: list[str], comp: str, syn: dict[str, list[str]] | None = None) -> bool:
    syn = SYNONYMS if syn is None else syn
    t = stem(term)
    alts = syn.get(t) or syn.get(term) or [t]
    if any(s.startswith(a) for a in alts for s in stems):
        return True
    c = compact(term)
    return len(c) >= 3 and any(a in comp for a in ({c} | set(syn.get(c) or [])) if len(a) >= 3)


def score(
    q: str, *, name: str, brand: str | None, model: str | None, code: str | None, category: str | None, sf: str, syn: dict[str, list[str]] | None = None
) -> int:
    """0 = no coincide. Si coincide: 100 por modelo o codigo, 20 si todas las palabras estan en el nombre, 5 por marca o
    categoria, y 1 de base."""
    terms = split_query(q)
    if not terms:
        return 0
    field = words(sf)  # un sf viejo (con guiones y puntos) se normaliza igual
    stems = [stem(w) for w in field]
    comp = "".join(field)
    whole = compact(q)
    every = all(term_matches(t, stems, comp, syn) for t in terms)
    if not every and not (len(whole) >= 4 and whole in comp):
        return 0
    pts = 1
    codes = [compact(x) for x in (model, code) if x]
    if len(whole) >= 3 and any(c.startswith(whole) or whole in c for c in codes):
        pts += 100
    name_stems = [stem(w) for w in words(name)]
    if all(term_matches(t, name_stems, compact(name), syn) for t in terms):
        pts += 20
    side = [stem(w) for w in words(" ".join(x for x in (brand, category) if x))]
    pts += 5 * sum(1 for t in terms if term_matches(t, side, "", syn))
    return pts
