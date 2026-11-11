"""Ficha tecnica de la tienda a partir de lo que ya esta escrito (O3-05) y descripcion util (O3-06).

Ficha (web_specs, por lote aprobado):
- Las lineas «- Clave: valor» de la descripcion de tienda (las trae el proveedor en 1 356 productos) se copian tal
  cual, sin los rellenos «No especificado / No indicado / No aplica». Un 3 % viene pegado en una sola linea: se separa.
- Por familia se agrega lo que el NOMBRE dice con numero y unidad (parlante: potencia, Bluetooth, proteccion, bateria;
  router y punto de acceso: norma WiFi, velocidad, puertos; UPS: VA, W, tomas; power bank: mAh, W). Solo se copia el
  dato: «audio bidireccional» no se convierte en «Micrófono integrado».

Descripcion (web_description, por lote aprobado), solo para lo que no tiene texto propio: 3 a 5 viñetas de la ficha
aprobada y una frase por plantilla de la familia con esos mismos datos. Sin adjetivos que el dato no sostenga. Nunca la
descripcion de factura (mayusculas con el codigo interno al final).

Todo por reglas: ninguna IA ni servicio externo.
"""

from __future__ import annotations

import re
import unicodedata

KEY_MAX, VALUE_MAX, SPECS_MAX = 40, 120, 14
_BULLET = re.compile(r"^\s*[-•*·]\s*(?P<k>[^:\n]{2,60}?)\s*:\s*(?P<v>.+?)\s*$")
_GLUED = re.compile(r"\s[-•]\s(?=[A-ZÁÉÍÓÚÑ][^:\n]{1,50}:)")  # «- A: x - B: y» en una sola linea
_EMPTY = re.compile(r"^(no (indicado|aplica|especificado|disponible|incluye datos)|n/?a|ninguno|-+|—|sin datos?|no)\.?$", re.I)
_KEY_NOISE = re.compile(r"\s*\((?:si aplica|opcional)\)\s*", re.I)
INTERNAL = re.compile(r"(?<![\w-])\d{9}(?![\w-])")


def fold(s: object) -> str:
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def empty_value(v: object) -> bool:
    return bool(_EMPTY.match(" ".join(str(v or "").split()).strip()))


def bullet_lines(text: str | None) -> list[str]:
    out: list[str] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        out += [x.strip() for x in _GLUED.sub("\n- ", line).splitlines() if x.strip()]
    return out


def specs_from_text(text: str | None) -> dict[str, str]:
    """«- Clave: valor» -> {Clave: valor}, sin rellenos ni duplicados (gana la primera)."""
    out: dict[str, str] = {}
    for line in bullet_lines(text):
        m = _BULLET.match(line if line.lstrip().startswith(("-", "•", "*", "·")) else "")
        if not m:
            continue
        k = _KEY_NOISE.sub(" ", m.group("k")).strip(" .")
        v = m.group("v").strip().rstrip(".").strip()
        if not k or not v or empty_value(v):
            continue
        k = (k[:1].upper() + k[1:])[:KEY_MAX]
        if k in out:
            continue
        out[k] = v[:VALUE_MAX]
        if len(out) >= SPECS_MAX:
            break
    return out


def is_bullet_text(text: str | None) -> bool:
    """la descripcion es una lista «- Clave: valor» (la tienda la muestra como lista)"""
    lines = [x for x in (text or "").splitlines() if x.strip()]
    return bool(lines) and sum(1 for x in lines if x.lstrip().startswith(("-", "•"))) >= max(2, len(lines) // 2)


def _shouting(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.8


def useless_description(text: str | None, name: str | None) -> bool:
    """Descripcion que no sirve en la tienda: vacia, igual al nombre, o la linea del proveedor en mayusculas con
    codigo interno (la de factura)."""
    t = " ".join((text or "").split())
    if not t:
        return True
    if fold(t).strip(" .") == fold(name).strip(" ."):
        return True
    first = t.split("\n")[0]
    return _shouting(first) and len(t) < 400 and not is_bullet_text(text)


def store_description(p) -> str | None:
    """Lo que la ficha muestra: la descripcion propia aprobada (web_description) o la de tienda si sirve. Nunca la de
    factura."""
    own = (getattr(p, "web_description", None) or "").strip()
    if own:
        return own
    d = (p.description_store or "").strip()
    if useless_description(d, p.name):
        return None
    return NO_DATA_LINE.sub("", d).strip() or None


NO_DATA_LINE = re.compile(r"^\s*[-•]\s*[^:\n]{1,60}:\s*(?:no (?:indicado|aplica|especificado)|n/?a)\.?\s*$\n?", re.I | re.M)


# ---------------------------------------------------------------- por familia, desde el nombre
_W = re.compile(r"(?<![\d.])(\d{1,4}(?:[.,]\d)?)\s?w(?:atts?)?\b(?:\s?rms)?")
_IPX = re.compile(r"\b(ipx?\s?[4-8]{1,2})\b")
_HOURS = re.compile(r"(?:hasta\s)?(\d{1,3})\s?(?:h|horas)\b(?:\s?de\s(?:bateria|reproduccion|autonomia))?")
_MAH = re.compile(r"(?<![\d.])(\d{3,5})\s?mah\b")
_VA = re.compile(r"(?<!\d)(\d{3,4})\s?va\b")
_OUTLETS = re.compile(r"(?<!\d)(\d{1,2})\s?(?:tomas|tomacorrientes|salidas|outlets)\b")
_WIFI_STD = re.compile(r"wi-?fi\s?([4-7])\b|\b(ax|ac|be)\s?(\d{3,5})\b")
_SPEED = re.compile(r"(?<![\d.])(\d{3,5})\s?(mbps|gbps)\b")
_PORTS = re.compile(r"(?<!\d)(\d{1,2})\s?(?:puertos|ptos|port|-port)\b")
_BT = re.compile(r"bluetooth(?:\s?v?(\d\.\d)(?![\d.]|\s?w))?")  # «bluetooth 5.3»; «bluetooth 4.2W» es potencia


def family_specs(shown_type: str, name: str) -> dict[str, str]:
    """Datos con numero y unidad que el nombre YA dice, segun la familia del tipo mostrado."""
    t, n = fold(shown_type), " " + fold(name) + " "
    out: dict[str, str] = {}
    if any(w in t for w in ("parlante", "bocina", "barra de sonido", "audio")):
        if m := _W.search(n):
            out["Potencia"] = f"{m.group(1).replace(',', '.')} W"
        if m := _BT.search(n):
            out["Bluetooth"] = f"Bluetooth {m.group(1)}" if m.group(1) else "Sí"
        if m := _IPX.search(n):
            out["Protección"] = m.group(1).upper().replace(" ", "")
        if m := _HOURS.search(n):
            out["Batería"] = f"Hasta {m.group(1)} h"
    elif any(w in t for w in ("router", "punto de acceso", "repetidor", "mesh")):
        if m := _WIFI_STD.search(n):
            out["Norma WiFi"] = f"WiFi {m.group(1)}" if m.group(1) else {"ax": "WiFi 6", "ac": "WiFi 5", "be": "WiFi 7"}[m.group(2)] + f" ({m.group(2).upper()}{m.group(3)})"
        if m := _SPEED.search(n):
            out["Velocidad"] = f"{m.group(1)} {'Mbps' if m.group(2) == 'mbps' else 'Gbps'}"
        if m := _PORTS.search(n):
            out["Puertos"] = m.group(1)
    elif t == "ups" or "ups" in t.split():
        if m := _VA.search(n):
            out["Capacidad"] = f"{m.group(1)} VA"
        if m := re.search(r"(?<!\d)(\d{3,4})\s?w\b", n):
            out["Potencia"] = f"{m.group(1)} W"
        if m := _OUTLETS.search(n):
            out["Tomas"] = m.group(1)
    elif "power bank" in t or "bateria portatil" in t or "power bank" in n:
        if m := _MAH.search(n):
            out["Capacidad"] = f"{m.group(1)} mAh"
        if m := _W.search(n):
            out["Potencia"] = f"{m.group(1).replace(',', '.')} W"
    return out
# ---------------------------------------------------------------- descripcion por plantilla (O3-06)
_SKIP = ("Tipo", "Marca", "Modelo")


def _low(k: str) -> str:
    return k[:1].lower() + k[1:]


def describe(p, shown_type: str, specs: dict[str, str]) -> str | None:
    """Una frase con los mismos datos de la ficha y, con 3 datos o mas, de 3 a 5 viñetas. Sin adjetivos."""
    from .catalog_text import shown_name
    from .product_names import tidy

    items = [(k, str(v).strip()) for k, v in specs.items() if k not in _SKIP and str(v).strip() and not empty_value(v)]
    if not items:
        return None
    name = (getattr(p, "web_name", None) or "").strip() or tidy(p.name) or shown_name(p)
    d = dict(items)
    t = fold(shown_type)
    if "parlante" in t and d.get("Potencia"):
        extra = [x for x in ("Bluetooth" if d.get("Bluetooth") else None, f"protección {d['Protección']}" if d.get("Protección") else None) if x]
        sentence = f"{name}, de {d['Potencia']} de potencia" + (f", con {' y '.join(extra)}" if extra else "") + "."
    elif t.startswith("camara") and d.get("Resolución"):
        sentence = f"{name}, con resolución de {d['Resolución']}" + (f" y lente de {d['Lente']}" if d.get("Lente") else "") + "."
    elif t == "ups" and d.get("Capacidad"):
        sentence = f"{name}, de {d['Capacidad']}" + (f" y {d['Potencia']}" if d.get("Potencia") else "") + "."
    else:
        k1, v1 = items[0]
        sentence = f"{name}. {k1}: {v1}."
    sentence = INTERNAL.sub("", sentence).replace(" .", ".")
    if len(items) < 3:
        return sentence
    bullets = "\n".join(f"- {k}: {v}" for k, v in items[:5])
    return f"{sentence}\n{bullets}"


def meta_description(p, specs: dict[str, str] | None, limit: int = 155) -> str | None:
    """Meta descripcion corta: los primeros datos de la ficha, sin rellenos ni codigos internos."""
    items = [(k, v) for k, v in (specs or {}).items() if k not in _SKIP and not empty_value(v)]
    if not items:
        return None
    out = "; ".join(f"{k}: {v}" for k, v in items[:4])
    out = INTERNAL.sub("", out).strip(" ;")
    if len(out) > limit:
        out = out[: limit - 1].rsplit(" ", 1)[0].rstrip(" ;:,") + "…"
    return out
