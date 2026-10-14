"""Recomendacion de equipos reales para las lineas genericas de la sugerencia del levantamiento.

Andres (LEV-2026-0008, CCTV): "cuando analice la cantidad de camaras y haga el costeo, aqui no me esta jalando
nada, ni me hace recomendacion. Deberia recomendar cuales camaras podria utilizar con base en los requerimientos
que le estoy dando, ligado a Productos y Servicios".

La sugerencia (survey_specs.suggest_materials) propone lineas genericas: "Cámara Bullet 4 MP", "NVR 8 canales",
"Switch PoE 16 puertos", "Cable (metros con 15 % de holgura)". Aqui, para cada una, se buscan hasta 3 productos
reales con supplier_catalog.search_offers (catalogo propio y de proveedor) usando los requisitos de los puntos
(tipo, resolucion, ambiente, iluminacion, alimentacion) y los calculos del sistema (TB, PoE). Nunca se elige
solo: el usuario confirma con "Usar este" (POST /surveys/{sid}/suggest/apply-product).

Puntuacion (la misma que se muestra en pantalla, criterio por criterio):
  1. cumple los requisitos duros de la linea (tipo, resolucion minima, exterior, PoE, canales, puertos, W...): +1000
  2. disponibilidad: existencias propias que alcanzan +300; propias que no alcanzan +200; proveedor con
     existencias +100; sin existencias 0
  3. coincidencias finas (formato exacto, IR/ColorVu, NVR con PoE, disco de videovigilancia...): hasta +99
  4. precio: el mas barato de los que se comparan +10, los demas en proporcion (costo; sin costo, precio de venta)
Un candidato que no cumple un requisito duro solo se muestra si ninguno lo cumple y nunca queda "recomendado".
El tecnico no ve costos ni precios: solo que equipo es y si hay existencias.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import Session

from ..models import Product
from . import pricing, supplier_catalog
from .documents import today_fx
from .store_showcase import capacity_tb, channels, compact, derive_specs, fold, ports, product_type
from .survey_specs import GROUP_KEY, SPECS, UNIDADES_METRO, _Pt, covered_line, parse_number, quantity_in_line, system_calcs

log = logging.getLogger(__name__)

MAX_CANDIDATES = 3
SEARCH_LIMIT = 30  # ofertas por busqueda: el filtro fino se hace aqui
DISK_LINE = "Disco duro de videovigilancia"  # linea extra: los discos salen del almacenamiento calculado

# puntos de la puntuacion (ver docstring)
PTS_MEETS = 1000
PTS_TIER = {3: 300, 2: 200, 1: 100, 0: 0}
PTS_PRICE = 10
MAX_SPEC = 99

# lo que solo ve quien ve costos / precios
COST_KEYS = ("cost", "currency", "cost_crc")
PRICE_KEYS = ("sale_price", "price_estimated")


# ---------------------------------------------------------------- texto y numeros
def _d(v) -> Decimal | None:
    try:
        n = parse_number(v)
    except ValueError:
        return None
    return n


def _g(x) -> str:
    """Numero corto para leer: 4, 10,4, 305."""
    x = Decimal(str(x))
    s = f"{x.normalize():f}" if x == x.to_integral_value() else f"{x.quantize(Decimal('0.1')).normalize():f}"
    return s.replace(".", ",")


def _money(x) -> str:
    """₡20 494: como lo muestra el portal (es-CR, miles con espacio)."""
    return f"₡{Decimal(str(x)):,.0f}".replace(",", " ")


def _ceil(x: Decimal) -> int:
    return int(math.ceil(float(x))) if x > 0 else 0


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


# ---------------------------------------------------------------- caracteristicas de una oferta
FORMATO_LABEL = {"bullet": "Bullet", "domo": "Domo", "turret": "Turret", "ptz": "PTZ", "fisheye": "Ojo de pez", "dual": "Lente dual", "360": "360°"}
TIPO_A_FORMATO = {
    "Bullet": "bullet",
    "Domo": "domo",
    "Turret": "turret",
    "PTZ": "ptz",
    "Fisheye": "fisheye",
    "Ojo de pez dual": "fisheye",
    "Lente dual 180°": "dual",
    "360°": "360",
}
FORMATO_TERM = {"bullet": "bullet", "domo": "domo", "turret": "turret", "ptz": "ptz", "fisheye": "fisheye", "dual": "dual", "360": "360"}
ANALOG = ("turbo", "hd-tvi", "hdtvi", "analog", "hdcvi", "ahd ")
ANPR_WORDS = ("anpr", "lpr", "placas", "matricula")
RX_W = re.compile(r"(\d{2,3})\s?w\b")
RX_DOORS = re.compile(r"(\d)\s?puertas?\b")
RX_VA = re.compile(r"(\d{3,4})\s?va\b")
RX_METERS = re.compile(r"(\d{2,4})\s?(?:m|mt|mts|metros)\b")
RX_PACK = (
    re.compile(r"(?:bolsa|paquete|caja|pack|kit|juego)\s(?:de\s)?(\d{2,4})\b"),
    re.compile(r"\b(\d{2,4})\s?(?:unidades|unds?|piezas|pzas?|pcs|uds)\b"),
)
RX_LB = re.compile(r"(\d{3,4})\s?(?:lb|lbs|libras)\b")
RX_KG = re.compile(r"(\d{3,4})\s?kg\b")
RX_IP = re.compile(r"\bip\s?(6[5-8])\b")


@dataclass
class Feat:
    """Lo que se sabe de una oferta a partir de su nombre, modelo, descripcion y especificaciones."""

    name: str
    text: str
    comp: str
    ptype: str
    unit: str
    mp: float | None = None
    formato: str | None = None
    exterior: bool | None = None
    night: str | None = None
    poe: bool | None = None
    acusense: bool = False
    channels: int | None = None
    nvr_poe: bool = False
    tb: Decimal | None = None
    surveillance: bool = False
    ports: int | None = None
    poe_w: Decimal | None = None
    doors: int | None = None
    va: int | None = None
    meters: Decimal | None = None
    pack: int | None = None
    hold_kg: Decimal | None = None


def features(offer: dict, prod: Product | None) -> Feat:
    ns = SimpleNamespace(
        name=offer.get("name") or "",
        code=offer.get("code") or "",
        model=offer.get("model"),
        brand=offer.get("brand") or (prod.brand if prod else None),
        description_store=prod.description_store if prod else None,
        description_invoice=prod.description_invoice if prod else offer.get("description"),
        specs=prod.specs if prod else None,
        supplier_sku=None,
    )
    name = fold(ns.name)
    specs = derive_specs(ns)
    raw = " ".join(str(x or "") for x in (ns.name, ns.model, ns.code, ns.description_store, ns.description_invoice))
    text = " " + fold(raw + " " + " ".join(str(v) for v in specs.values())) + " "
    unit = str(offer.get("unit") or (prod.unit if prod else "") or "Unid")
    f = Feat(name=name, text=text, comp=compact(raw), ptype=product_type(ns), unit=unit)
    # camaras
    res = specs.get("Resolución") or ""
    if m := re.search(r"(\d{1,2}(?:\.\d)?)\s?mp", fold(res)):
        f.mp = float(m.group(1))
    elif m := re.search(r"([2-4])k", fold(res)):
        f.mp = {2: 4.0, 3: 5.0, 4: 8.0}[int(m.group(1))]
    if f.ptype == "Cámara PTZ" or " ptz" in text:
        f.formato = "ptz"
    elif _has(text, "fisheye", "ojo de pez", "panoramic"):
        f.formato = "fisheye"
    elif _has(text, "lente dual", "dual lens", "doble lente", "180°", " 180 "):
        f.formato = "dual"
    elif " 360" in text:
        f.formato = "360"
    elif fm := specs.get("Formato"):
        f.formato = {"Bullet": "bullet", "Turret": "turret", "Domo": "domo"}.get(fm)
    # lo que dice el producto manda; sin dato, una bullet se asume de exterior
    if _has(text, "exterior", "outdoor", "intemperie") or RX_IP.search(text):
        f.exterior = True
    elif _has(text, "interior", "indoor"):
        f.exterior = False
    elif f.formato == "bullet":
        f.exterior = True
    vn = specs.get("Visión nocturna") or ""
    if vn == "A color" or _has(text, "colorvu", "color nocturn", "full color", "a color"):
        f.night = "colorvu"
    elif vn.startswith("Luz") or _has(text, "smart hybrid", "luz hibrida"):
        f.night = "hibrida"
    elif vn or re.search(r"\b(ir|exir|infrarrojo)\b", text):
        f.night = "ir"
    if "poe" in text:
        f.poe = True
    elif _has(text, *ANALOG) or (_has(text, "wifi", "wi-fi", "inalambric") and f.ptype.startswith("Cámara")):
        f.poe = False
    f.acusense = _has(text, "acusense", "deteccion humana", "deteccion de personas", "smart event")
    # grabadores, discos, switches
    f.channels = channels(ns)
    f.nvr_poe = bool(re.search(r"/\d{1,2}p\b", fold(f"{ns.code} {ns.model or ''} {ns.name}"))) or (f.ptype == "Grabador NVR" and "poe" in name)
    f.tb = capacity_tb(ns)
    f.surveillance = _has(text, "purple", "skyhawk", "vigilancia", "surveillance", " vx ")
    f.ports = ports(ns)
    ws = [Decimal(x) for x in RX_W.findall(text)]
    f.poe_w = max(ws) if ws else None
    if m := RX_DOORS.search(text):
        f.doors = int(m.group(1))
    elif m := re.search(r"k260(\d)", f.comp):
        f.doors = int(m.group(1))
    if m := RX_VA.search(text):
        f.va = int(m.group(1))
    # presentacion: metros por unidad (bobina) o unidades por paquete
    if unit.strip().lower() not in UNIDADES_METRO and (m := RX_METERS.search(name)) and _has(name, "bobina", "caja", "rollo", "carrete", "305"):
        f.meters = Decimal(m.group(1))
    for rx in RX_PACK:
        if m := rx.search(name):
            f.pack = int(m.group(1))
            break
    if m := RX_KG.search(text):
        f.hold_kg = Decimal(m.group(1))
    elif m := RX_LB.search(text):
        f.hold_kg = (Decimal(m.group(1)) * Decimal("0.4536")).quantize(Decimal(1))
    return f


# ---------------------------------------------------------------- requisitos de una linea
Result = tuple  # (estado True/False/None, texto, puntos)


@dataclass
class Rule:
    key: str
    hard: bool
    fn: Callable[[Feat], Result]


@dataclass
class Req:
    category: str
    label: str
    terms: list[str]
    is_type: Callable[[Feat], bool]
    rules: list[Rule] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    # unidades de la linea que cubre cada unidad del producto, y nota para leer ("230 m -> 1 bobina de 305 m")
    per_unit: Callable[[Feat, Decimal, str], tuple[Decimal, str | None, str | None]] | None = None


def _rule_words(key: str, label: str, words: tuple[str, ...], pts: int, hard: bool = False, miss: str | None = None) -> Rule:
    return Rule(key, hard, lambda f: (True, label, pts) if _has(f.text, *words) else (False if hard or miss else None, miss or f"{label} sin confirmar", 0))


def _pack_units(f: Feat, need: Decimal, unit: str) -> tuple[Decimal, str | None, str | None]:
    if f.pack and f.pack > 1 and unit.strip().lower() not in UNIDADES_METRO:
        n = _ceil(need / f.pack) if need > 0 else 0
        return Decimal(f.pack), (f"{_g(need)} → {n} paquete(s) de {f.pack}" if need > 0 else f"paquete de {f.pack}"), None
    return Decimal(1), None, None


def _meters_units(line_m: Decimal | None):
    """Cable: metros por unidad de la linea (1 si la linea va en metros, 305 si es 'Cable Cat6 (305 m)')."""

    def fn(f: Feat, need: Decimal, unit: str) -> tuple[Decimal, str | None, str | None]:
        prod_m = f.meters if f.meters else (Decimal(1) if f.unit.strip().lower() in UNIDADES_METRO else None)
        if line_m and prod_m:
            per = prod_m / line_m
            if per == 1:
                return Decimal(1), None, None
            n = _ceil(need / per) if need > 0 else 0
            nota = f"{_g(need)} {unit} → {n} × {_g(prod_m)} m" if need > 0 else f"presentación de {_g(prod_m)} m"
            return per, nota, None
        if line_m == 1 and prod_m is None:
            return Decimal(1), None, "presentación sin dato (¿por metro o por bobina?)"
        return Decimal(1), None, None

    return fn


def _canales(n: int, poe_cams: bool, mp: float | None) -> list[Rule]:
    def ch(f: Feat) -> Result:
        if f.channels is None:
            return None, "canales sin dato", 0
        if f.channels == n:
            return True, f"{n} canales", 25
        if f.channels > n:
            return True, f"{f.channels} canales (sobran {f.channels - n})", 15
        return False, f"{f.channels} canales (se piden {n})", 0

    rules = [Rule("canales", True, ch)]
    if poe_cams:
        rules.append(Rule("poe", False, lambda f: (True, "PoE integrado", 10) if f.nvr_poe else (None, "", 0)))
    if mp and mp >= 8:
        rules.append(Rule("4k", False, lambda f: (True, "4K", 6) if _has(f.text, "4k", "8mp", "8 mp", "12mp") else (None, "4K sin confirmar", 0)))
    return rules


class _Ctx:
    """Lo que la recomendacion necesita del levantamiento: puntos por grupo y calculos del sistema."""

    def __init__(self, survey):
        self.kind = survey.kind if survey.kind in SPECS else "otro"
        self.pts = [_Pt(p, self.kind) for p in survey.points]
        self.calcs = system_calcs(self.kind, list(survey.points), survey.common)
        self.groups: dict[str, tuple[str, list]] = {}
        for p in self.pts:
            self.groups.setdefault(group_label(p.kind, p.data).lower(), (p.kind, []))[1].append(p)
        self.cams = [p for p in self.pts if p.kind == "cctv"]
        self.doors = [p for p in self.pts if p.kind == "acceso"]

    def any(self, pts, key, *values) -> bool:
        return any(p.data.get(key) in values for p in pts)


def group_label(kind: str, data: dict) -> str:
    """Mismo nombre que la linea de equipo principal de survey_specs._main_equipment."""
    spec = SPECS.get(kind, SPECS["otro"])
    parts = []
    for k in GROUP_KEY.get(kind, ()):
        v = data.get(k)
        if isinstance(v, list):
            v = "/".join(v)
        if v:
            parts.append(str(v))
    return f"{spec['point_label']} {' '.join(parts)}".strip() if parts else f"{spec['point_label']} ({spec['point_prefix']})"


# ---------- camaras
def _req_camera(pts: list, anpr: bool = False) -> Req:
    datas = [p.data for p in pts]
    tipo = next((d.get("tipo") for d in datas if d.get("tipo")), None)
    formato = TIPO_A_FORMATO.get(tipo) if tipo else None
    mp = next((float(m.group(1)) for d in datas if (m := re.search(r"(\d)\s?MP", str(d.get("resolucion") or "")))), None)
    ext = any(d.get("ambiente") == "Exterior" for d in datas)
    inte = bool(datas) and all(d.get("ambiente") == "Interior" for d in datas)
    poe = any(d.get("alimentacion") == "PoE" for d in datas)
    noche = any(d.get("iluminacion") in ("Escasa", "Nula") for d in datas)
    contraluz = any(d.get("iluminacion") == "Contraluz fuerte" for d in datas)
    analiticas = {a for d in datas for a in (d.get("analiticas") or []) if isinstance(d.get("analiticas"), list)}
    rules: list[Rule] = []
    needs: list[str] = []
    if formato and not anpr:
        lab = FORMATO_LABEL[formato]
        needs.append(lab)

        def fmt(f: Feat, formato=formato, lab=lab) -> Result:
            if f.formato is None:
                return None, f"formato {lab.lower()} sin confirmar", 0
            return (True, lab, 30) if f.formato == formato else (False, f"{FORMATO_LABEL[f.formato]} (se pide {lab.lower()})", 0)

        rules.append(Rule("formato", True, fmt))
    if ext:
        needs.append("Exterior")
        rules.append(
            Rule(
                "ambiente",
                True,
                lambda f: {True: (True, "Exterior", 15), False: (False, "Interior (se pide exterior)", 0)}.get(
                    f.exterior, (None, "uso exterior sin confirmar", 0)
                ),
            )
        )
    elif inte:
        needs.append("Interior")
        rules.append(
            Rule("ambiente", False, lambda f: (True, "Interior", 5) if f.exterior is False else ((True, "Apta exterior", 2) if f.exterior else (None, "", 0)))
        )
    if mp:
        needs.append(f"{_g(mp)} MP")

        def res(f: Feat, mp=mp) -> Result:
            if f.mp is None:
                return None, "resolución sin dato", 0
            if abs(f.mp - mp) < 0.05:
                return True, f"{_g(mp)} MP", 25
            if f.mp > mp:
                return True, f"{_g(f.mp)} MP (supera {_g(mp)} MP)", 15
            return False, f"{_g(f.mp)} MP (se piden {_g(mp)} MP)", 0

        rules.append(Rule("resolucion", True, res))
    if poe:
        needs.append("PoE")
        rules.append(Rule("poe", True, lambda f: {True: (True, "PoE", 10), False: (False, "sin PoE", 0)}.get(f.poe, (None, "PoE sin confirmar", 0))))
    if noche:
        needs.append("Visión nocturna")
        lab_n = {"ir": "IR", "colorvu": "ColorVu", "hibrida": "Luz híbrida"}
        rules.append(Rule("noche", False, lambda f: (True, lab_n[f.night], 8) if f.night else (None, "visión nocturna sin confirmar", 0)))
    if "ColorVu" in analiticas:
        needs.append("ColorVu")
        rules.append(Rule("colorvu", False, lambda f: (True, "ColorVu", 12) if f.night == "colorvu" else (False, "sin ColorVu", 0)))
    if "AcuSense" in analiticas:
        needs.append("AcuSense")
        rules.append(Rule("acusense", False, lambda f: (True, "AcuSense", 6) if f.acusense else (False, "sin AcuSense", 0)))
    if contraluz:
        needs.append("WDR")
        rules.append(_rule_words("wdr", "WDR", ("wdr",), 5))
    if anpr:
        needs.insert(0, "ANPR / placas")

    def is_type(f: Feat) -> bool:
        if not f.ptype.startswith("Cámara") or _has(f.name, "kit ", "dashcam", "termic", "soporte", "caja "):
            return False
        lpr = _has(f.text, *ANPR_WORDS)
        return lpr if anpr else not lpr

    label = "Cámara ANPR" if anpr else ("Cámara " + FORMATO_LABEL[formato].lower() if formato else "Cámara")
    terms = (["anpr", "lpr"] if anpr else ([FORMATO_TERM[formato]] if formato else [])) + ["camara"]
    attrs = {"tipo": "camara", "formato": formato, "resolucion_mp": mp, "ambiente": "exterior" if ext else ("interior" if inte else None), "poe": poe or None}
    return Req("camara", label, terms, is_type, rules, needs, {k: v for k, v in attrs.items() if v is not None})


# ---------- control de acceso
METODO_WORDS = {
    "tarjeta": ("tarjeta", "card", "rfid", "proximidad", "mifare", "125khz", "125 khz", " em "),
    "huella": ("huella", "fingerprint", "biometric"),
    "facial": ("facial", "rostro", " face"),
    "pin": ("teclado", " pin", "keypad"),
    "qr": (" qr",),
}
METODO_LABEL = {"tarjeta": "Tarjeta", "huella": "Huella", "facial": "Facial", "pin": "Teclado PIN", "qr": "QR"}


def _door_soft(doors: list) -> tuple[list[Rule], list[str]]:
    rules, needs = [], []
    cred = {p.data.get("tipo_credencial") for p in doors}
    if "Tarjeta 125 kHz (EM)" in cred:
        needs.append("125 kHz (EM)")
        rules.append(_rule_words("em", "125 kHz (EM)", ("125", " em ", "emarine", "em4100"), 10))
    if "Tarjeta 13,56 MHz (Mifare)" in cred:
        needs.append("Mifare 13,56 MHz")
        rules.append(_rule_words("mifare", "Mifare", ("mifare", "13.56", "13,56"), 10))
    if any(p.data.get("proteccion_ip") in ("IP65", "IP66", "IP67") for p in doors):
        needs.append("IP65 o más")
        rules.append(
            Rule("ip", False, lambda f: (True, f"IP{RX_IP.search(f.text).group(1)}", 8) if RX_IP.search(f.text) else (None, "protección IP sin confirmar", 0))
        )
    proto = {p.data.get("protocolo") for p in doors} - {None, ""}
    words = {"Wiegand": ("wiegand",), "RS-485": ("485",), "OSDP": ("osdp",), "TCP/IP": ("tcp", "ethernet", " red ")}
    for pr in sorted(proto):
        if pr in words:
            needs.append(pr)
            rules.append(_rule_words(f"proto_{pr}", pr, words[pr], 5))
    return rules, needs


def _req_reader(n: str, doors: list) -> Req:
    if n.startswith("lector de tarjeta con teclado pin"):
        metodos = ["tarjeta", "pin"]
    elif n.startswith("lector de tarjeta"):
        metodos = ["tarjeta"]
    elif "huella o facial" in n:
        metodos = ["huella|facial"]
    elif "huella" in n:
        metodos = ["huella"]
    elif "facial" in n:
        metodos = ["facial"]
    elif n.startswith("teclado pin"):
        metodos = ["pin"]
    else:
        metodos = ["qr"]
    rules, needs = [], []
    for i, m in enumerate(metodos):
        keys = m.split("|")
        words = tuple(w for k in keys for w in METODO_WORDS[k])
        lab = " o ".join(METODO_LABEL[k] for k in keys)
        needs.append(lab)
        rules.append(_rule_words(m, lab, words, 25 if i == 0 else 12, hard=i == 0, miss=f"sin {lab.lower()}"))
    soft, extra = _door_soft(doors)
    term = {"tarjeta": "lector", "pin": "teclado", "huella": "huella", "facial": "facial", "qr": "qr", "huella|facial": "biometrico"}[metodos[0]]
    terms = list(dict.fromkeys([term, "lector"]))
    return Req(
        "lector",
        "Lector",
        terms,
        lambda f: _has(f.name, "lector", "terminal", "teclado", "reader", "keypad") and not _has(f.name, "codigo de barras", "memoria", "sd "),
        rules + soft,
        needs + extra,
        {"tipo": "lector", "metodo": metodos[0]},
    )


def _req_lock(cerradura: str | None) -> Req | None:
    if cerradura == "Existente":
        return None  # se reutiliza la del sitio: no se compra
    rules, needs = [], []
    if cerradura and cerradura.startswith("Magnética"):
        needs.append("Electroimán")
        rules.append(
            _rule_words("tipo", "Electroimán", ("electroiman", "magnetic", "magnetica", "maglock", "electro iman"), 15, hard=True, miss="no es electroimán")
        )
        kg = _d(re.sub(r"[^\d,.]", "", cerradura))
        if kg:
            needs.append(f"{_g(kg)} kg")

            def fuerza(f: Feat, kg=kg) -> Result:
                if f.hold_kg is None:
                    return None, "fuerza de retención sin dato", 0
                if f.hold_kg >= kg * Decimal("0.9"):
                    return True, f"{_g(f.hold_kg)} kg de retención", 15
                return False, f"{_g(f.hold_kg)} kg (se piden {_g(kg)} kg)", 0

            rules.append(Rule("fuerza", True, fuerza))
    elif cerradura == "Pestillo eléctrico":
        needs.append("Pestillo eléctrico")
        rules.append(_rule_words("tipo", "Pestillo eléctrico", ("pestillo", "cerradero", "strike", "contrachapa"), 15, hard=True, miss="no es pestillo"))
    return Req(
        "cerradura",
        "Cerradura",
        ["cerradura", "electroiman"],
        lambda f: (
            _has(f.name, "cerradura", "electroiman", "electro iman", "chapa", "cerradero", "pestillo", "maglock", "magnetic lock")
            and not _has(f.name, "soporte", "boton")
        ),
        rules,
        needs,
        {"tipo": "cerradura"},
    )


# ---------- tabla de lineas simples: (empieza con, categoria, etiqueta, terminos, el nombre debe decir alguna, nunca)
SIMPLE: tuple = (
    (("boton de salida",), "boton", "Botón de salida", ["boton"], ("boton",), ("panico", "emergencia")),
    (("sensor de salida",), "sensor", "Sensor de salida (PIR)", ["sensor", "pir"], ("pir", "sensor de salida", "rex"), ()),
    (("barra antipanico",), "barra", "Barra antipánico", ["barra"], ("antipanico", "anti panico", "panic bar"), ()),
    (("gabinete",), "gabinete", "Gabinete", ["gabinete", "rack"], ("gabinete", "rack"), ("ups", "bandeja", "organizador")),
    (("patch cord",), "patchcord", "Patch cord", ["patch"], ("patch cord", "patchcord", "parcheo"), ()),
    (("patch panel",), "patchpanel", "Patch panel", ["patch"], ("patch panel",), ()),
    (("jacks",), "jack", "Jack", ["jack", "keystone"], ("jack", "keystone"), ("audio", "3.5")),
    (("placas",), "placa", "Placa / faceplate", ["placa", "faceplate"], ("faceplate", "placa"), ("madre", "solar", "video")),
    (("organizadores",), "organizador", "Organizador", ["organizador"], ("organizador",), ()),
    (("inyector poe",), "inyector", "Inyector PoE", ["inyector"], ("inyector",), ()),
    (("barrera vehicular",), "barrera", "Barrera vehicular", ["barrera"], ("barrera",), ("antipanico",)),
    (("lazo o sensor",), "lazo", "Lazo o sensor vehicular", ["lazo", "detector"], ("lazo", "loop", "detector vehicular"), ()),
    (("convertidor de medios",), "convertidor", "Convertidor de medios", ["convertidor"], ("convertidor",), ()),
    (("enrolador",), "enrolador", "Enrolador de tarjetas", ["enrolador", "k1f100"], ("enrolador", "k1f100", "enrollment"), ()),
    (("caja de paso", "caja estanca"), "caja", "Caja de paso", ["caja"], ("caja",), ("herramienta", "fuerte", "registradora", "carton")),
    (("abrazaderas",), "abrazadera", "Abrazadera", ["abrazadera"], ("abrazadera",), ()),
    (("monitor",), "monitor", "Monitor", ["monitor"], ("monitor",), ()),
)
SOFT_SIMPLE = {
    "caja": [("ip", "IP66 / estanca", ("ip66", "ip65", "ip67", "estanca"), 10)],
    "patchcord": [("cat6", "Cat6", ("cat6", "cat 6"), 10)],
    "jack": [("cat6", "Cat6", ("cat6", "cat 6"), 10)],
}
STOP = {"de", "del", "la", "el", "los", "las", "y", "o", "con", "para", "por", "en", "a", "un", "una", "nuevo", "metros", "holgura", "material", "unid"}


def _simple(n: str) -> Req | None:
    for starts, cat, label, terms, must, never in SIMPLE:
        if n.startswith(starts):
            rules = [_rule_words(k, lab, words, pts) for k, lab, words, pts in SOFT_SIMPLE.get(cat, [])]
            return Req(
                cat,
                label,
                list(terms),
                lambda f, must=must, never=never: _has(f.name, *must) and not _has(f.name, *never),
                rules,
                [label],
                {"tipo": cat},
                per_unit=_pack_units,
            )
    return None


def _generic(n: str) -> Req | None:
    words = [w for w in re.findall(r"[a-z0-9]+", n) if len(w) >= 4 and w not in STOP][:2]
    if not words:
        return None
    return Req("otro", n.capitalize(), [words[0]], lambda f, words=words: all(w in f.text for w in words), [], [], {}, per_unit=_pack_units)


# ---------------------------------------------------------------- linea -> requisitos
def requirement(line: dict, ctx: _Ctx) -> Req | None:
    """Que equipo real sirve para esta linea generica. None = la linea no se compra (servicio, alquiler, punto)."""
    if line.get("kind") == "servicio":
        return None
    name = line.get("name") or ""
    n = fold(name).strip()
    grupo = ctx.groups.get(name.lower())
    if grupo:
        gkind, pts = grupo
        if gkind == "cctv":
            return _req_camera(pts)
        if gkind == "acceso":
            return _req_lock(next((p.data.get("cerradura") for p in pts if p.data.get("cerradura")), None))
        if gkind == "asistencia":
            metodos = {m for p in pts for m in (p.data.get("metodo") or []) if isinstance(p.data.get("metodo"), list)}
            rules = [
                _rule_words(m, m, METODO_WORDS.get({"Huella": "huella", "Facial": "facial", "Tarjeta": "tarjeta", "PIN": "pin"}.get(m, ""), (fold(m),)), 10)
                for m in sorted(metodos)
            ]
            return Req(
                "terminal",
                "Terminal de asistencia",
                ["terminal", "asistencia"],
                lambda f: _has(f.name, "terminal", "reloj", "asistencia"),
                rules,
                sorted(metodos),
                {"tipo": "terminal"},
            )
        if gkind == "redes":
            eq = next((p.data.get("equipo") for p in pts if p.data.get("equipo")), None)
            return _req_redes(eq)
        return None  # carril, punto de red, punto: no son un equipo
    cams = ctx.cams
    poe_cams = ctx.any(cams, "alimentacion", "PoE")
    if n.startswith("camara anpr"):
        return _req_camera([p for p in ctx.pts if p.kind == "anpr"], anpr=True)
    if m := re.match(r"(?:nvr|dvr|grabador)\D*(\d+)\s*canales", n):
        mp = max((float(x.group(1)) for p in cams if (x := re.search(r"(\d)\s?MP", str(p.data.get("resolucion") or "")))), default=None)
        ch = int(m.group(1))
        return Req(
            "nvr",
            "Grabador NVR",
            ["nvr", "grabador"],
            lambda f: f.ptype == "Grabador NVR",
            _canales(ch, poe_cams, mp),
            [f"{ch} canales"] + (["PoE integrado (opcional)"] if poe_cams else []),
            {"tipo": "nvr", "canales": ch},
        )
    if n.startswith(fold(DISK_LINE)):
        return _req_disk(line)
    if n.startswith("switch poe") or n == "switch poe":
        return _req_switch(n, ctx)
    if n.startswith("fuente"):
        rules = [_rule_words("12v", "12 V", ("12v", "12 v", "12vdc", "12 vdc"), 15)]
        needs = ["12 VDC"]
        if "respaldo" in n:
            rules.append(_rule_words("respaldo", "con respaldo", ("respaldo", "bateria", "backup"), 10, miss="sin respaldo de batería"))
            needs.append("con respaldo")
        return Req(
            "fuente",
            "Fuente de poder",
            ["fuente"],
            lambda f: "fuente" in f.name and not _has(f.name, "ups", "pc ", "computadora"),
            rules,
            needs,
            {"tipo": "fuente"},
        )
    if n.startswith("bateria"):
        rules = [_rule_words("12v", "12 V", ("12v", "12 v"), 10), _rule_words("7ah", "7 Ah", ("7ah", "7 ah", "7.0ah"), 10)]
        return Req(
            "bateria",
            "Batería",
            ["bateria"],
            lambda f: "bateria" in f.name and not _has(f.name, "ups", "celular", "aa "),
            rules,
            ["12 V", "7 Ah"],
            {"tipo": "bateria"},
        )
    if n.startswith(("lector", "teclado pin", "terminal de reconocimiento")):
        return _req_reader(n, ctx.doors)
    if m := re.match(r"controladora de acceso (\d) puertas", n):
        k = int(m.group(1))

        def doors(f: Feat, k=k) -> Result:
            if f.doors is None:
                return None, "puertas sin dato", 0
            if f.doors == k:
                return True, f"{k} puertas", 25
            return (True, f"{f.doors} puertas (sobran {f.doors - k})", 12) if f.doors > k else (False, f"{f.doors} puertas (se piden {k})", 0)

        return Req(
            "controladora",
            "Controladora de acceso",
            ["controlador"],
            lambda f: _has(f.name, "controlador", "controladora", "controller") and (_has(f.text, "acceso", "puerta", "access") or "k260" in f.comp),
            [Rule("puertas", True, doors)],
            [f"{k} puertas"],
            {"tipo": "controladora", "puertas": k},
        )
    if n.startswith("cerradura"):
        return _req_lock(None)
    if m := re.match(r"soporte (zl|l|u) para cerradura", n):
        tipo = m.group(1)
        return Req(
            "soporte",
            f"Soporte {tipo.upper()}",
            ["soporte"],
            lambda f: "soporte" in f.name and _has(f.text, "cerradura", "electroiman", "maglock", "magnetic"),
            [
                Rule(
                    "tipo",
                    True,
                    lambda f, t=tipo: (True, f"Soporte {t.upper()}", 20) if re.search(rf"\b{t}\b", f.name) else (False, f"no es soporte {t.upper()}", 0),
                )
            ],
            [f"Soporte {tipo.upper()}"],
            {"tipo": "soporte", "forma": tipo},
        )
    if n.startswith("cable 4x22"):
        return Req(
            "cable4x22",
            "Cable 4x22",
            ["cable"],
            lambda f: "cable" in f.name and ("4x22" in f.comp or ("22awg" in f.comp and _has(f.text, "4 hilos", "4 conductores", "4c"))),
            [],
            ["4 hilos calibre 22"],
            {"tipo": "cable", "calibre": "4x22"},
            per_unit=_meters_units(Decimal(1) if (line.get("unit") or "").lower() in UNIDADES_METRO else None),
        )
    if n.startswith("cable"):
        line_m = Decimal(1) if (line.get("unit") or "").lower() in UNIDADES_METRO else (Decimal(m.group(1)) if (m := re.search(r"\((\d+)\s?m\)", n)) else None)
        rules = [_rule_words("cat6", "Cat6", ("cat6", "cat 6", "categoria 6"), 15, miss="no es Cat6")]
        needs = ["UTP Cat6"]
        if ctx.any(ctx.pts, "ambiente", "Exterior"):
            rules.append(_rule_words("ext", "Uso exterior", ("exterior", "outdoor", "intemperie", "gel", "doble forro"), 8))
            needs.append("uso exterior (opcional)")
        return Req(
            "cable",
            "Cable UTP",
            ["utp", "cable"],
            lambda f: (
                "cable" in f.name
                and _has(f.text, "utp", "cat5", "cat6", "cat 6", "cat 5", "ftp")
                and not _has(f.name, "patch", "4x22", "coaxial", "hdmi", "usb", "electrico")
            ),
            rules,
            needs,
            {"tipo": "cable", "categoria": "cat6"},
            per_unit=_meters_units(line_m),
        )
    if "rj45" in n or "rj-45" in n:
        return Req(
            "rj45",
            "Conector RJ45",
            ["rj45"],
            lambda f: (
                _has(f.text, "rj45", "rj-45", "rj 45")
                and not _has(f.name, "tester", "ponchadora", "crimp", "pinza", "balun", "acoplador", "jack", "keystone", "hembra", "router", "punto de acceso")
            ),
            [_rule_words("cat6", "Cat6", ("cat6", "cat 6"), 10)],
            ["RJ45"],
            {"tipo": "conector"},
            per_unit=_pack_units,
        )
    if m := re.match(r"(?:tubo|canalizacion) (emt|pvc)", n):
        mat = m.group(1)
        return Req(
            "tubo",
            f"Tubo {mat.upper()}",
            [mat, "tubo"],
            lambda f, mat=mat: _has(f.name, "tubo", "conduit", "tuberia") and mat in f.text,
            [_rule_words("34", '3/4"', ("3/4", "19 mm", "19mm"), 15), _rule_words("3m", "3 m", (" 3 m", " 3m", "10 pies", "3 metros"), 5)],
            [mat.upper(), '3/4"'],
            {"tipo": "tubo", "material": mat},
        )
    if n.startswith("ups"):
        va = int(m.group(1)) if (m := re.match(r"ups (\d+) va", n)) else None
        rules = []
        if va:

            def cap(f: Feat, va=va) -> Result:
                if f.va is None:
                    return None, "capacidad sin dato", 0
                if f.va >= va:
                    return True, f"{f.va} VA", 20 if f.va <= va * 1.5 else 10
                return False, f"{f.va} VA (se piden {va} VA)", 0

            rules.append(Rule("va", True, cap))
        return Req(
            "ups",
            "UPS",
            ["ups"],
            lambda f: f.ptype == "UPS",
            rules,
            [f"{va} VA o más"] if va else ["UPS"],
            {"tipo": "ups", "va": va} if va else {"tipo": "ups"},
        )
    if n.startswith("credenciales"):
        rules, needs = [], []
        cred = {p.data.get("tipo_credencial") for p in ctx.doors}
        if "Tarjeta 13,56 MHz (Mifare)" in cred:
            rules.append(_rule_words("mifare", "Mifare", ("mifare", "13.56", "13,56"), 15))
            needs.append("Mifare")
        if "Tarjeta 125 kHz (EM)" in cred:
            rules.append(_rule_words("em", "125 kHz (EM)", ("125", " em ", "em4100"), 15))
            needs.append("125 kHz (EM)")
        if "Llavero" in cred:
            rules.append(_rule_words("llavero", "Llavero", ("llavero", "tag", "keyfob"), 10))
            needs.append("Llavero")
        paquete = "paquete" in n
        return Req(
            "credencial",
            "Credencial",
            ["tarjeta", "llavero"],
            lambda f: (
                _has(f.name, "tarjeta", "llavero", " tag", "keyfob")
                and not _has(f.name, "lector", "terminal", "memoria", " sd", "red ", "video", "madre", "sim ", "controlador")
            ),
            rules,
            needs or ["Tarjeta o llavero"],
            {"tipo": "credencial"},
            per_unit=None if paquete else _pack_units,
        )
    if n.startswith(("access point", "switch", "router", "firewall", "antena")):
        return _req_redes(
            {"access point": "Access point", "switch": "Switch", "router": "Router", "firewall": "Firewall", "antena": "Antena PtP"}[
                n.split()[0] if not n.startswith("access") else "access point"
            ]
        )
    if n.startswith("terminal"):
        return Req("terminal", "Terminal", ["terminal"], lambda f: "terminal" in f.name, [], [], {"tipo": "terminal"})
    if simple := _simple(n):
        return simple
    if n.startswith(("punto de red", "reparacion", "alquiler", "tomacorriente", "canalizacion", "cajas", "conectores")):
        return _generic(n) if n.startswith(("tomacorriente", "canalizacion")) else None
    return _generic(n)


def _req_redes(eq: str | None) -> Req | None:
    if eq == "Access point":
        return Req(
            "ap",
            "Access point",
            ["access point", "punto de acceso"],
            lambda f: f.ptype == "Punto de acceso WiFi",
            [],
            ["Access point"],
            {"tipo": "access point"},
        )
    if eq == "Switch":
        return Req("switch", "Switch", ["switch"], lambda f: f.ptype.startswith("Switch"), [], ["Switch"], {"tipo": "switch"})
    if eq == "Router":
        return Req("router", "Router", ["router"], lambda f: f.ptype == "Router", [], ["Router"], {"tipo": "router"})
    if eq == "Firewall":
        return Req("firewall", "Firewall", ["firewall"], lambda f: "firewall" in f.text, [], ["Firewall"], {"tipo": "firewall"})
    if eq == "Antena PtP":
        return Req("antena", "Antena", ["antena"], lambda f: "antena" in f.name, [], ["Antena PtP"], {"tipo": "antena"})
    return None


def _req_disk(line: dict) -> Req:
    def rules_for(need: Decimal) -> list[Rule]:
        def cuantos(f: Feat) -> Result:
            n = _ceil(need / f.tb) if need > 0 and f.tb else 0
            if not n:
                return None, "", 0
            return True, ("1 disco alcanza" if n == 1 else f"{n} discos"), {1: 15, 2: 8}.get(n, 3)

        return [
            _rule_words("vigilancia", "Para videovigilancia", ("purple", "skyhawk", "vigilancia", "surveillance", " vx "), 15),
            Rule("cuantos", False, cuantos),
        ]

    need = Decimal(str(line.get("quantity") or 0))

    def per(f: Feat, need: Decimal, unit: str) -> tuple[Decimal, str | None, str | None]:
        n = _ceil(need / f.tb) if need > 0 else 0
        nota = f"{_g(need)} TB → {n} × {_g(f.tb)} TB = {_g(f.tb * n)} TB" if n else f"disco de {_g(f.tb)} TB"
        return f.tb, nota, None

    return Req(
        "disco",
        "Disco duro",
        ["disco", "hdd"],
        lambda f: f.ptype == "Disco duro" and bool(f.tb),
        rules_for(need),
        [f"{_g(need)} TB en total" if need > 0 else "capacidad por calcular", "videovigilancia (opcional)"],
        {"tipo": "disco", "tb": float(need)} if need > 0 else {"tipo": "disco"},
        per_unit=per,
    )


def _req_switch(n: str, ctx: _Ctx) -> Req:
    k = int(m.group(1)) if (m := re.search(r"(\d+)\s*puertos", n)) else None
    poe = ((ctx.calcs or {}).get("cctv") or {}).get("poe") or {}
    watts = Decimal(str(poe.get("requerido_w") or 0)) if not poe.get("sin_consumo") and (poe.get("consumo_w") or 0) else Decimal(0)
    rules, needs = [], []
    if k:
        needs.append(f"{k} puertos PoE o más")

        def pts_(f: Feat, k=k) -> Result:
            if f.ports is None:
                return None, "puertos sin dato", 0
            if f.ports == k:
                return True, f"{k} puertos", 25
            return (True, f"{f.ports} puertos (sobran {f.ports - k})", 15) if f.ports > k else (False, f"{f.ports} puertos (se piden {k})", 0)

        rules.append(Rule("puertos", True, pts_))
    if watts > 0:
        needs.append(f"{_g(watts)} W PoE")

        def budget(f: Feat, w=watts) -> Result:
            if f.poe_w is None:
                return None, "presupuesto PoE sin dato", 0
            return (True, f"{_g(f.poe_w)} W PoE (se requieren {_g(w)} W)", 15) if f.poe_w >= w else (False, f"{_g(f.poe_w)} W PoE (se requieren {_g(w)} W)", 0)

        rules.append(Rule("watts", True, budget))
    return Req(
        "switch",
        "Switch PoE",
        ["switch"],
        lambda f: f.ptype == "Switch PoE",
        rules,
        needs or ["PoE"],
        {"tipo": "switch poe", "puertos": k} if k else {"tipo": "switch poe"},
    )


# ---------------------------------------------------------------- linea extra: discos
def extra_lines(ctx: _Ctx, items: list) -> list[dict]:
    """Discos del grabador: la sugerencia de siempre no los trae; salen del almacenamiento calculado (TB).
    Solo en el modo recomendacion, asi /suggest sin candidatos queda exactamente igual."""
    if not ctx.cams:
        return []
    alm = (((ctx.calcs or {}).get("cctv") or {}).get("almacenamiento")) or {}
    tb = alm.get("tb")
    tb = Decimal(str(tb)) if tb is not None else Decimal(0)
    o: dict = {
        "name": DISK_LINE,
        "quantity": tb,
        "unit": "TB",
        "kind": "equipo",
        "reason": (
            f"Almacenamiento calculado: {alm['detalle']}. {alm.get('leyenda') or ''}".strip()
            if tb > 0
            else "Para calcular los TB faltan datos del sistema de CCTV"
        ),
        "origin": "recomendacion",  # no viene de la sugerencia de siempre
        "apply_generic": False,  # se agrega eligiendo un disco real (los TB no son unidades)
    }
    if tb <= 0 and alm.get("faltan"):
        o["missing"] = list(alm["faltan"])
        o["reason"] += ": " + "; ".join(alm["faltan"])
    match = next((it for it in items if covered_line(it) == DISK_LINE.lower()), None)
    suelto = None if match else next((it for it in items if _has(fold(it.name), "disco duro", "hdd", "disco de")), None)
    o["item_id"] = (match or suelto).id if (match or suelto) else None
    o["existing_quantity"] = quantity_in_line(match, DISK_LINE) if match else Decimal(0)
    if tb <= 0 or suelto:
        o["action"], o["add_quantity"] = "revisar", Decimal(0)
        if suelto:
            o["matched_name"] = suelto.name
            o["reason"] += f"; ya está «{suelto.name}» cargado: confirmá que alcance"
    elif match:
        falta = tb - o["existing_quantity"]
        o["action"], o["add_quantity"] = ("sumar", falta) if falta > 0 else ("cubierto", Decimal(0))
        o["matched_name"] = match.name
    else:
        o["action"], o["add_quantity"] = "nuevo", tb
    return [o]


# ---------------------------------------------------------------- busqueda
class _Searcher:
    """search_offers con memoria por terminos (varias lineas buscan "camara") y un respaldo sin atributos."""

    def __init__(self, db: Session, tenant_id: int):
        self.db, self.tenant_id = db, tenant_id
        self.memo: dict = {}
        self.errors = 0

    def _call(self, term: str, attrs: dict | None) -> list[dict]:
        key = (term, tuple(sorted((attrs or {}).items(), key=lambda x: x[0])))
        if key not in self.memo:
            try:
                self.memo[key] = supplier_catalog.search_offers(self.db, self.tenant_id, terms=[term], attrs=attrs or None, limit=SEARCH_LIMIT) or []
            except Exception:  # noqa: BLE001 - la recomendacion nunca tumba la sugerencia
                log.exception("search_offers fallo (termino %r)", term)
                self.errors += 1
                self.memo[key] = []
        return self.memo[key]

    def offers(self, req: Req) -> list[dict]:
        out: dict[str, dict] = {}
        for t in req.terms:
            for o in self._call(t, req.attrs):
                out.setdefault(offer_key(o), o)
        if len(out) < MAX_CANDIDATES and req.attrs:
            for t in req.terms:
                for o in self._call(t, None):
                    out.setdefault(offer_key(o), o)
        return list(out.values())


def offer_key(o: dict) -> str:
    if o.get("product_id"):
        return f"p:{o['product_id']}"
    if o.get("supplier_item_id"):
        return f"s:{o['supplier_item_id']}"
    return f"c:{o.get('supplier') or ''}:{o.get('code') or o.get('name')}"


def own_offer(db: Session, prod: Product) -> dict:
    """Oferta de un producto propio elegido a mano (no salio en la busqueda)."""
    off = supplier_catalog.best_offer_for_product(db, prod) or {}
    if off.get("product_id") != prod.id:
        # la real puede devolver una oferta de proveedor como "mejor": para el producto propio se usan sus datos
        off = {**off, "source": "propio", "product_id": prod.id, "supplier_item_id": None, "name": prod.name, "model": prod.model, "code": prod.code}
    return off


# ---------------------------------------------------------------- evaluar y ordenar
def _availability(o: dict, units: int) -> tuple[int, str]:
    sp = _d(o.get("stock_propio")) or Decimal(0)
    sv = _d(o.get("stock_proveedor")) or Decimal(0)
    need = max(units, 1)
    if sp > 0 and sp >= need:
        return 3, f"{_g(sp)} en bodega"
    if sp > 0:
        return 2, f"{_g(sp)} en bodega (faltan {_g(need - sp)})"
    if sv > 0:
        quien = o.get("supplier") if o.get("source") == "proveedor" and o.get("supplier") else "Proveedor"
        return 1, f"{quien}: {_g(sv)} disponibles"
    return 0, "Sin existencias"


def evaluate(req: Req, line: dict, o: dict, prod: Product | None, fx: Decimal, margin: Decimal, db: Session) -> dict | None:
    f = features(o, prod)
    if not req.is_type(f):
        return None
    need = Decimal(str(line.get("quantity") or 0))
    unit = str(line.get("unit") or "Unid")
    per, qnote, qunknown = (req.per_unit or (lambda *_: (Decimal(1), None, None)))(f, need, unit)
    per = per if per and per > 0 else Decimal(1)
    units = _ceil(need / per) if need > 0 else 0
    matches, fails, misses, unknown, criteria = [], [], [], [], []
    spec = 0
    for r in req.rules:
        st, txt, pts = r.fn(f)
        if st is True:
            spec += pts
            if txt:
                matches.append(txt)
        elif st is False:
            (fails if r.hard else misses).append(txt)
        elif txt:
            unknown.append(txt)
        if txt:
            criteria.append({"label": txt, "ok": st, "points": pts if st is True else 0, "hard": r.hard})
    if qunknown:
        unknown.append(qunknown)
    tier, avail = _availability(o, units)
    cost_crc = _d(o.get("cost_crc"))
    if cost_crc is None and _d(o.get("cost")) is not None:
        cur = o.get("currency") or "USD"
        cost_crc = _d(o.get("cost")) * (fx if cur == "USD" else Decimal(1)) if cur in ("USD", "CRC") else None
    sale = _d(o.get("sale_price")) if o.get("source") == "propio" else None
    if sale is not None and (o.get("sale_currency") or "CRC") == "USD":
        sale = sale * fx
    estimated = False
    if (sale is None or sale <= 0) and cost_crc and cost_crc > 0:
        sale, estimated = pricing.sale_price(db, cost_crc, "CRC", "CRC", margin), True
    meets = not fails
    # se compara lo que cuesta cubrir la linea (3 discos de 4 TB contra 2 de 6 TB), no la unidad
    unit_price = cost_crc if cost_crc and cost_crc > 0 else (sale if sale and sale > 0 else None)
    return {
        "key": offer_key(o),
        "source": o.get("source") or ("propio" if o.get("product_id") else "proveedor"),
        "product_id": o.get("product_id"),
        "supplier_item_id": o.get("supplier_item_id"),
        "supplier": o.get("supplier"),
        "code": o.get("code"),
        "name": o.get("name"),
        "model": o.get("model"),
        "brand": o.get("brand") or (prod.brand if prod else None),
        "image": o.get("image") or _prod_image(prod),
        "stock_propio": _d(o.get("stock_propio")) or Decimal(0),
        "stock_proveedor": _d(o.get("stock_proveedor")),
        "availability": {3: "bodega", 2: "bodega_parcial", 1: "proveedor", 0: "sin_existencias"}[tier],
        "availability_label": avail,
        "quantity": Decimal(units),
        "unit": f.unit,
        "per_unit": per,
        "line_unit": unit,
        "quantity_note": qnote,
        "meets": meets,
        "matches": matches,
        "fails": fails,
        "misses": misses,
        "unknown": unknown,
        "criteria": criteria,
        "spec_points": min(spec, MAX_SPEC),
        "tier": tier,
        "cost": _d(o.get("cost")),
        "currency": o.get("currency"),
        "cost_crc": cost_crc.quantize(Decimal("0.01")) if cost_crc is not None else None,
        "sale_price": sale.quantize(Decimal("0.01")) if sale is not None else None,
        "price_estimated": estimated,
        "updated_at": o.get("updated_at"),
        "_sort_price": unit_price * max(units, 1) if unit_price else None,
    }


def _prod_image(prod: Product | None) -> str | None:
    if not prod:
        return None
    imgs = [x for x in (prod.images or []) if isinstance(x, dict) and x.get("url")]
    main = next((x for x in imgs if x.get("main")), imgs[0] if imgs else None)
    return main["url"] if main else None


def rank(cands: list[dict]) -> list[dict]:
    """Puntaje final: cumple (+1000), disponibilidad (+300/200/100/0), coincidencias (hasta 99) y precio (hasta 10)."""
    pool = [c for c in cands if c["meets"]] or cands
    precios = [c["_sort_price"] for c in pool if c["_sort_price"]]
    pmin = min(precios) if precios else None
    for c in cands:
        price_pts = int((Decimal(PTS_PRICE) * pmin / c["_sort_price"]).quantize(Decimal(1))) if pmin and c["_sort_price"] else 0
        c["price_points"] = price_pts
        c["score"] = (PTS_MEETS if c["meets"] else 0) + PTS_TIER[c["tier"]] + c["spec_points"] + price_pts
    big = Decimal(10) ** 12
    return sorted(cands, key=lambda c: (-c["score"], c["_sort_price"] or big, c["name"] or ""))


def _reason(c: dict, req: Req, sees_costs: bool, sees_prices: bool) -> str:
    partes = [", ".join(c["matches"][:4]) or req.label]
    if c["fails"]:
        partes.append("no cumple: " + ", ".join(c["fails"]))
    partes.append(c["availability_label"])
    if sees_costs and c.get("cost_crc"):
        partes.append(f"costo {_money(c['cost_crc'])}")
    elif sees_prices and c.get("sale_price"):
        partes.append(f"precio {_money(c['sale_price'])}{' estimado' if c.get('price_estimated') else ''}")
    return " · ".join(partes)


def _visible(c: dict, req: Req, sees_costs: bool, sees_prices: bool) -> dict:
    out = {k: v for k, v in c.items() if not k.startswith("_")}
    out["reason"] = _reason(c, req, sees_costs, sees_prices)
    crit = [
        {"label": "Cumple los requisitos" if c["meets"] else "No cumple: " + ", ".join(c["fails"]), "points": PTS_MEETS if c["meets"] else 0, "ok": c["meets"]}
    ]
    crit += c["criteria"]
    crit.append({"label": c["availability_label"], "points": PTS_TIER[c["tier"]], "ok": c["tier"] > 0})
    if sees_costs or sees_prices:
        crit.append({"label": "Precio (el más barato suma 10)", "points": c.get("price_points", 0), "ok": None})
    out["criteria"] = crit
    if not sees_costs:
        for k in COST_KEYS:
            out.pop(k, None)
    if not sees_prices:
        for k in PRICE_KEYS:
            out.pop(k, None)
    if not (sees_costs or sees_prices):
        out.pop("price_points", None)
    return out


def candidates_for(req: Req, line: dict, offers: list[dict], db: Session, fx: Decimal, margin: Decimal) -> list[dict]:
    """Todas las ofertas que son del tipo de la linea, evaluadas y ordenadas (sin recortar)."""
    out = []
    for o in offers:
        prod = db.get(Product, o["product_id"]) if o.get("product_id") else None
        c = evaluate(req, line, o, prod, fx, margin, db)
        if c:
            out.append(c)
    return rank(out)


# ---------------------------------------------------------------- entrada principal
def _current(line: dict, items: list) -> object | None:
    iid = line.get("item_id")
    it = next((x for x in items if iid and x.id == iid), None)
    if it is None:
        it = next((x for x in items if covered_line(x) == (line.get("name") or "").strip().lower()), None)
    return it


def recommend(db: Session, tenant, survey, lines: list[dict], *, sees_costs: bool, sees_prices: bool, margin_pct: Decimal | None = None) -> list[dict]:
    """Agrega a cada linea de la sugerencia `candidates` (hasta 3, el primero "recommended" si cumple) y `recommend`
    (que se busco, requisitos y el equipo en uso). Suma la linea de discos si hay camaras. No modifica nada."""
    ctx = _Ctx(survey)
    items = list(survey.items)
    lines = [dict(x) for x in lines] + extra_lines(ctx, items)
    fx = today_fx(db, "USD")[0]
    margin = Decimal(str(margin_pct)) if margin_pct is not None else pricing.default_margin(tenant)
    search = _Searcher(db, tenant.id)
    for line in lines:
        req = requirement(line, ctx)
        it = _current(line, items)
        meta: dict = {"searched": False, "need": line.get("quantity"), "unit": line.get("unit"), "current": None}
        if it is not None and (it.product_id or it.supplier_item_id):
            rec = it.recommendation if isinstance(it.recommendation, dict) else {}
            meta["current"] = {
                "item_id": it.id,
                "product_id": it.product_id,
                "supplier_item_id": it.supplier_item_id,
                "name": it.name,
                "quantity": it.quantity,
                "unit": it.unit,
                "source": rec.get("source"),
            }
        if req is None:
            line["candidates"], line["recommend"] = [], {**meta, "note": "Esta línea no lleva un equipo del catálogo."}
            continue
        errores = search.errors
        scored = candidates_for(req, line, search.offers(req), db, fx, margin)
        meets = [c for c in scored if c["meets"]]
        top = (meets or scored)[:MAX_CANDIDATES]
        cur = meta["current"]
        for i, c in enumerate(top):
            c["recommended"] = i == 0 and c["meets"]
            c["selected"] = bool(cur) and (
                (cur["product_id"] and c["product_id"] == cur["product_id"]) or (cur["supplier_item_id"] and c["supplier_item_id"] == cur["supplier_item_id"])
            )
        note = None
        if search.errors > errores:
            note = "No se pudo consultar el catálogo para esta línea."
        elif not scored:
            note = "No hay equipos de este tipo en el catálogo."
        elif not meets:
            note = "Ningún equipo del catálogo cumple todo: estos son los más cercanos (ninguno queda recomendado)."
        meta.update(
            {"searched": True, "category": req.category, "label": req.label, "needs": req.needs, "found": len(scored), "compliant": len(meets), "note": note}
        )
        line["recommend"] = meta
        line["candidates"] = [_visible(c, req, sees_costs, sees_prices) for c in top]
    return lines


def resolve_choice(
    db: Session,
    tenant,
    survey,
    lines: list[dict],
    line_name: str,
    *,
    product_id: int | None,
    supplier_item_id: int | None,
    code: str | None,
    margin_pct: Decimal | None = None,
) -> tuple[dict, dict]:
    """La linea y la oferta elegidas con "Usar este", recalculadas en el servidor (el costo nunca viene del
    navegador). Devuelve (linea, candidato completo con costo). ValueError/LookupError con el motivo."""
    ctx = _Ctx(survey)
    lines = list(lines) + extra_lines(ctx, list(survey.items))
    line = next((x for x in lines if (x.get("name") or "").strip().lower() == line_name.strip().lower()), None)
    if line is None:
        raise LookupError("Esa línea ya no está en la sugerencia: volvé a recomendar.")
    req = requirement(line, ctx)
    if req is None:
        raise ValueError("Esa línea no lleva un equipo del catálogo.")
    fx = today_fx(db, "USD")[0]
    margin = Decimal(str(margin_pct)) if margin_pct is not None else pricing.default_margin(tenant)
    search = _Searcher(db, tenant.id)
    key = f"p:{product_id}" if product_id else f"s:{supplier_item_id}"
    scored = candidates_for(req, line, search.offers(req), db, fx, margin)
    c = next((x for x in scored if x["key"] == key), None)
    if c is None and product_id:
        prod = db.get(Product, product_id)
        if prod is None or prod.tenant_id != tenant.id or not prod.active:
            raise LookupError("Producto no encontrado")
        o = own_offer(db, prod)
        c = evaluate(req, line, o, prod, fx, margin, db)
        if c is None:  # el usuario lo eligio a proposito aunque no parezca del tipo: se respeta, sin requisitos
            c = evaluate(Req(req.category, req.label, [], lambda f: True), line, o, prod, fx, margin, db)
        c = rank([c])[0]
    if c is None and supplier_item_id and code:
        extra = [
            o for o in (supplier_catalog.search_offers(db, tenant.id, terms=[code], limit=SEARCH_LIMIT) or []) if o.get("supplier_item_id") == supplier_item_id
        ]
        if extra:
            found = candidates_for(req, line, extra, db, fx, margin) or candidates_for(
                Req(req.category, req.label, [], lambda f: True), line, extra, db, fx, margin
            )
            c = found[0] if found else None
    if c is None:
        raise LookupError("La oferta del proveedor ya no está disponible: volvé a recomendar.")
    c["reason_plain"] = _reason(c, req, False, False)
    return line, c
