"""Tipo de equipo QUE SE MUESTRA en la tienda (O1-08): tarjeta, ficha («Tipo»), filtro ?tipo= y buscador.

No confundir con store_showcase.product_type: ese es la llave FUNCIONAL con la que se arman los kits, los accesorios y
las piezas fijadas, y no se toca (cambiarlo cambia los kits publicados). Aqui solo se decide el rotulo:

- Fuera de seguridad, redes y energia (audio, smart home, electrodomesticos, computo...) el tipo es la subcategoria
  del producto: una barra de sonido es «Barras de sonido», nunca «Cámara WiFi» porque su nombre dice «inalámbrico».
- Dentro de esas familias se usa la misma tabla de reglas del tipo funcional, con dos correcciones:
  «Cámara …» exige una categoria de camaras o las palabras cámara, camera, bullet, domo, turret o PTZ en el nombre
  (si no, se sigue con la regla siguiente), y «rack», «cable» y «alarma» se buscan como palabra entera. Si ninguna
  regla calza, la subcategoria.
- derive_specs (store_showcase) recibe este tipo cuando arma lo que se muestra: sin camara no hay lente, vision
  nocturna ni microfono.
"""

from __future__ import annotations

import functools
import re

from .store_showcase import TYPE_RULES, fold

# familias (categoria de primer nivel) en las que el tipo funcional tiene sentido; se comparan por palabra
FUNCTIONAL_FAMILY = (
    "seguridad",
    "videovigilancia",
    "vigilancia",
    "cctv",
    "camara",
    "camaras",
    "acceso",
    "alarma",
    "alarmas",
    "redes",
    "red",
    "conectividad",
    "networking",
    "cableado",
    "cable",
    "cables",
    "energia",
    "respaldo",
    "ups",
    "almacenamiento",
)
CAMERA_CATEGORY = re.compile(r"\b(camaras?|cameras?|cctv|videovigilancia)\b")
CAMERA_WORDS = re.compile(r"\b(camaras?|cameras?|bullet|domos?|turret|ptz)\b")
# en el tipo funcional son subcadenas («rack» calza en «bracket», «cable» en «cableado»): aqui, palabra entera
BOUNDED = {w: re.compile(rf"\b{w}s?\b") for w in ("rack", "cable", "alarma")}
_WORD = re.compile(r"[a-z0-9]+")


def _has(n: str, w: str) -> bool:
    rx = BOUNDED.get(w)
    return bool(rx.search(n)) if rx else w in n


def category_chain(cats) -> dict[int, tuple[str, str]]:
    """id de categoria -> (nombre de la categoria, nombre de su categoria de primer nivel). cats: objetos con id, name y
    parent_id (Category o el arbol plano del snapshot). Un ciclo o un padre que no esta en la lista corta la cadena."""
    by_id = {c.id: c for c in cats}
    out: dict[int, tuple[str, str]] = {}
    for c in cats:
        top, seen = c, {c.id}
        while top.parent_id in by_id and top.parent_id not in seen:
            seen.add(top.parent_id)
            top = by_id[top.parent_id]
        out[c.id] = (c.name or "", top.name or "")
    return out


def functional_family(top: str | None) -> bool:
    """la categoria de primer nivel es de seguridad, redes o energia (o el producto no tiene categoria)"""
    if top is None:
        return True
    return any(w in FUNCTIONAL_FAMILY for w in _WORD.findall(fold(top)))


def display_type(p, leaf: str | None = None, top: str | None = None) -> str:
    """Rotulo del tipo de equipo para la tienda. leaf: nombre de la categoria del producto; top: el de su categoria de
    primer nivel (el mismo si no tiene padre). Sin categoria se clasifica solo por el nombre."""
    return _display(p.name or "", leaf, top if top is not None else leaf)


@functools.lru_cache(maxsize=16384)
def _display(name: str, leaf: str | None, top: str | None) -> str:
    if not functional_family(top):
        return (leaf or "").strip()
    cam_ok = bool(CAMERA_WORDS.search(fold(name))) or bool(CAMERA_CATEGORY.search(fold(f"{leaf or ''} {top or ''}")))
    # sin regla que calce, la subcategoria («Seguridad > Barreras vehiculares» -> «Barreras vehiculares»)
    return _rules(name, cam_ok) or (leaf.strip() if leaf and leaf != top else "")


def _rules(name: str, cam_ok: bool) -> str:
    """La tabla del tipo funcional (TYPE_RULES) con palabra entera en rack, cable y alarma y sin «Cámara …» cuando el
    producto no es de camaras."""
    n = " " + fold(name) + " "
    if n.lstrip().startswith(("memoria", "tarjeta")) and any(w in n for w in ("sd", "micro")):
        return "Memoria microSD"
    for label, must, never in TYPE_RULES:
        if label.startswith("Cámara") and not cam_ok:
            continue
        if any(_has(n, w) for w in must) and not any(_has(n, w) for w in never):
            if label == "Switch PoE" and "poe" not in n:
                return "Switch"
            if label == "Cámara IP" and ("turbo" in n or "analog" in n or "hd-tvi" in n):
                return "Cámara analógica"
            return label
    return "Accesorio" if fold(name).startswith(("soporte", "caja", "brazo", "base", "tornillo", "abrazadera")) else ""


def is_camera(t: str | None) -> bool:
    """el tipo mostrado es de camara (lente, vision nocturna y microfono solo tienen sentido ahi)"""
    return bool(t) and (t.startswith("Cámara") or t in ("Videoportero", "Dashcam"))
