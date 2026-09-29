"""Que se pregunta en cada tipo de levantamiento y que materiales sugiere.

Un levantamiento de CCTV no pide los mismos datos que uno de cableado. El portal dibuja el formulario con esta
especificacion, asi agregar un tipo nuevo es tocar solo este archivo.
"""

from __future__ import annotations

from decimal import Decimal

FieldSpec = dict

SPECS: dict[str, dict] = {
    "cctv": {
        "label": "CCTV / videovigilancia",
        "point_prefix": "CAM",
        "point_label": "Cámara",
        "fields": [
            {"key": "ubicacion", "label": "Ubicación", "type": "text", "placeholder": "Entrada principal"},
            {"key": "tipo", "label": "Tipo", "type": "select", "options": ["Bullet", "Domo", "Turret", "PTZ", "Fisheye", "Ojo de pez dual"]},
            {"key": "ambiente", "label": "Ambiente", "type": "select", "options": ["Exterior", "Interior"]},
            {"key": "resolucion", "label": "Resolución requerida", "type": "select", "options": ["2 MP", "4 MP", "6 MP", "8 MP (4K)"]},
            {"key": "distancia_m", "label": "Distancia al gabinete (m)", "type": "number", "unit": "m"},
            {"key": "altura_m", "label": "Altura de montaje (m)", "type": "number", "unit": "m"},
            {"key": "canalizacion", "label": "Canalización", "type": "select", "options": ["EMT", "PVC", "Bandeja", "Aéreo", "Existente"]},
            {"key": "alimentacion", "label": "Alimentación", "type": "select", "options": ["PoE", "12 VDC", "Solar"]},
            {"key": "iluminacion", "label": "Iluminación", "type": "select", "options": ["Buena", "Escasa", "Nula", "Contraluz fuerte"]},
            {
                "key": "analiticas",
                "label": "Analíticas requeridas",
                "type": "multi",
                "options": ["AcuSense", "ColorVu", "Conteo", "ANPR", "Reconocimiento facial"],
            },
        ],
        "materials": ["Cable UTP Cat6", "Conectores RJ45", "Canalización EMT", "Cajas", "Abrazaderas", "Patch cords", "Gabinete", "UPS"],
    },
    "cableado": {
        "label": "Cableado estructurado",
        "point_prefix": "DAT",
        "point_label": "Punto de red",
        "fields": [
            {"key": "origen", "label": "Origen", "type": "text", "placeholder": "Rack principal"},
            {"key": "destino", "label": "Destino", "type": "text", "placeholder": "Oficina 3"},
            {"key": "metros", "label": "Metros estimados", "type": "number", "unit": "m"},
            {"key": "categoria", "label": "Categoría", "type": "select", "options": ["Cat5e", "Cat6", "Cat6A", "Fibra OM3", "Fibra OS2"]},
            {"key": "canalizacion", "label": "Canalización", "type": "select", "options": ["Bandeja", "EMT", "PVC", "Canaleta", "Existente"]},
            {"key": "faceplate", "label": "Faceplate", "type": "select", "options": ["1 puerto", "2 puertos", "4 puertos"]},
            {"key": "patch_panel", "label": "Patch panel", "type": "text", "placeholder": "Rack 01"},
            {"key": "certificacion", "label": "Certificación requerida", "type": "bool"},
        ],
        "materials": ["Cable Cat6 (305 m)", "Jacks", "Placas", "Patch cords", "Patch panel", "Organizadores", "Etiquetas"],
        # 1 punto = 1 jack + 1 placa + 2 patch cords; cada 90 m de cable, una carrucha de 305 m
        "suggest": {"per_point": [("Jacks", 1), ("Placas", 1), ("Patch cords", 2)], "per_meters": [("Cable Cat6 (305 m)", 305)]},
    },
    "acceso": {
        "label": "Control de acceso",
        "point_prefix": "ACC",
        "point_label": "Puerta",
        "fields": [
            {"key": "ubicacion", "label": "Puerta / ubicación", "type": "text"},
            {"key": "tipo_puerta", "label": "Tipo de puerta", "type": "select", "options": ["Madera", "Metal", "Vidrio", "Portón", "Torniquete"]},
            {
                "key": "cerradura",
                "label": "Cerradura",
                "type": "select",
                "options": ["Magnética 280 kg", "Magnética 600 kg", "Pestillo eléctrico", "Existente"],
            },
            {"key": "lectura", "label": "Identificación", "type": "multi", "options": ["Tarjeta", "Huella", "Facial", "PIN", "App / QR"]},
            {"key": "salida", "label": "Salida", "type": "select", "options": ["Botón", "Sensor de movimiento", "Lector interior", "Barra antipánico"]},
            {"key": "distancia_m", "label": "Distancia al gabinete (m)", "type": "number", "unit": "m"},
            {"key": "energia", "label": "Energía disponible", "type": "select", "options": ["Sí, en sitio", "Hay que llevarla"]},
        ],
        "materials": ["Cerradura", "Fuente", "Botón de salida", "Cable 4x22", "Canalización", "Batería de respaldo"],
    },
    "asistencia": {
        "label": "Tiempo y asistencia",
        "point_prefix": "TA",
        "point_label": "Terminal",
        "fields": [
            {"key": "ubicacion", "label": "Ubicación", "type": "text"},
            {"key": "personas", "label": "Personas que marcan", "type": "number"},
            {"key": "metodo", "label": "Método", "type": "multi", "options": ["Huella", "Facial", "Tarjeta", "PIN"]},
            {"key": "red", "label": "Red disponible", "type": "select", "options": ["Cableada", "WiFi", "No hay"]},
            {"key": "integracion", "label": "Integración con planilla", "type": "bool"},
        ],
        "materials": ["Terminal", "Fuente", "Punto de red", "Soporte"],
    },
    "redes": {
        "label": "Redes / WiFi",
        "point_prefix": "AP",
        "point_label": "Equipo",
        "fields": [
            {"key": "ubicacion", "label": "Ubicación", "type": "text"},
            {"key": "equipo", "label": "Equipo", "type": "select", "options": ["Access point", "Switch", "Router", "Antena PtP", "Firewall"]},
            {"key": "area_m2", "label": "Área a cubrir (m²)", "type": "number", "unit": "m²"},
            {"key": "usuarios", "label": "Usuarios simultáneos", "type": "number"},
            {"key": "montaje", "label": "Montaje", "type": "select", "options": ["Techo", "Pared", "Poste", "Rack"]},
            {"key": "poe", "label": "PoE disponible", "type": "bool"},
        ],
        "materials": ["Access point", "Switch PoE", "Cable Cat6", "Patch cords", "Soportes", "UPS"],
    },
    "ups": {
        "label": "Respaldo eléctrico (UPS)",
        "point_prefix": "UPS",
        "point_label": "Equipo a respaldar",
        "fields": [
            {"key": "ubicacion", "label": "Ubicación", "type": "text"},
            {"key": "carga_w", "label": "Carga a respaldar (W)", "type": "number", "unit": "W"},
            {"key": "autonomia_min", "label": "Autonomía requerida (min)", "type": "number", "unit": "min"},
            {"key": "tension", "label": "Tensión", "type": "select", "options": ["120 V", "240 V"]},
            {"key": "tomas", "label": "Tomas necesarias", "type": "number"},
        ],
        "materials": ["UPS", "Baterías", "Regleta", "Gabinete"],
    },
    "anpr": {
        "label": "ANPR / placas y barreras",
        "point_prefix": "ANP",
        "point_label": "Carril",
        "fields": [
            {"key": "ubicacion", "label": "Carril / acceso", "type": "text"},
            {"key": "sentido", "label": "Sentido", "type": "select", "options": ["Entrada", "Salida", "Bidireccional"]},
            {"key": "ancho_m", "label": "Ancho del carril (m)", "type": "number", "unit": "m"},
            {"key": "distancia_camara_m", "label": "Distancia de la cámara (m)", "type": "number", "unit": "m"},
            {"key": "barrera", "label": "Barrera", "type": "select", "options": ["Nueva", "Existente", "No aplica"]},
            {"key": "integracion", "label": "Integración", "type": "multi", "options": ["Control de acceso", "Condominio", "Parqueo pago"]},
        ],
        "materials": ["Cámara ANPR", "Barrera", "Lazo o sensor", "Gabinete", "Canalización", "UPS"],
    },
    "otro": {
        "label": "Otro",
        "point_prefix": "PTO",
        "point_label": "Punto",
        "fields": [
            {"key": "ubicacion", "label": "Ubicación", "type": "text"},
            {"key": "detalle", "label": "Qué se requiere", "type": "text"},
            {"key": "distancia_m", "label": "Distancia (m)", "type": "number", "unit": "m"},
        ],
        "materials": [],
    },
}


# ---------- equipos vs materiales ----------
# Un equipo es lo que se instala y tiene serie (camara, UPS, antena, gabinete); un material es consumible
# (cable, tubo, placa). Andres los quiere en dos bloques y el costeo los agrupa igual.
EQUIPMENT_WORDS = (
    "camara",
    "cámara",
    "nvr",
    "dvr",
    "ups",
    "antena",
    "gabinete",
    "rack",
    "switch",
    "access point",
    "router",
    "firewall",
    "terminal",
    "cerradura",
    "barrera",
    "lector",
    "disco duro",
    "monitor",
    "fuente",
    "bateria",
    "batería",
    "patch panel",
    "controladora",
    "ptz",
    "domo",
    "bullet",
)


def classify_kind(name: str) -> str:
    n = (name or "").lower()
    return "equipo" if any(w in n for w in EQUIPMENT_WORDS) else "material"


# ---------- numeros escritos en el celular ----------
def parse_number(v) -> Decimal | None:
    """ "2,5", "2.5", 2.5 -> Decimal("2.5"). Vacio -> None. Lo que no es numero -> ValueError."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v).strip().replace(",", "."))
    except Exception as e:  # noqa: BLE001
        raise ValueError(str(v)) from e


def normalize_point_data(kind: str, data: dict) -> dict:
    """Los campos numericos (metros, altura) se guardan como numero con decimales, nunca truncados.
    Se usa float en el JSON porque JSON no tiene Decimal; 2.5 m sigue siendo 2.5 m."""
    spec = SPECS.get(kind, SPECS["otro"])
    out = dict(data or {})
    for f in spec["fields"]:
        if f["type"] != "number" or f["key"] not in out:
            continue
        try:
            n = parse_number(out[f["key"]])
        except ValueError as e:
            raise ValueError(f"{f['label']}: '{e}' no es un número") from e
        if n is not None and n < 0:
            raise ValueError(f"{f['label']}: no puede ser negativo")
        out[f["key"]] = None if n is None else (int(n) if n == n.to_integral_value() else float(n))
    return out


# ---------- sugerencia de materiales ----------
# Palabras en las observaciones de cada punto que piden algo que el formulario no pregunta.
NOTE_RULES = (
    (("cielo raso", "cielorraso", "cielo falso"), "Reparación de cielo raso (material)", "material", "m²"),
    (("poste",), "Brazo / abrazadera para poste", "material", "Unid"),
    (("concreto", "romper", "perforar", "pared de block", "bloque"), "Anclajes y tacos expansivos", "material", "Unid"),
    (
        ("sin energia", "sin energía", "no hay energia", "no hay energía", "tomacorriente", "no hay toma"),
        "Tomacorriente y extensión eléctrica",
        "material",
        "Unid",
    ),
    (("andamio", "escalera", "muy alto", "altura"), "Alquiler de andamio / escalera extensible", "material", "Unid"),
    (("lluvia", "intemperie", "sol directo"), "Caja estanca IP66", "material", "Unid"),
    (("fibra",), "Convertidor de medios de fibra", "equipo", "Unid"),
)


def _data(p) -> dict:
    return (p.data if hasattr(p, "data") else p.get("data")) or {}


def _code(p) -> str:
    return (p.code if hasattr(p, "code") else p.get("code")) or "?"


def _notes(p) -> str:
    return ((p.notes if hasattr(p, "notes") else p.get("notes")) or "").lower()


def _num(data: dict, key: str) -> Decimal:
    try:
        return parse_number(data.get(key)) or Decimal(0)
    except ValueError:  # dato escrito a mano en el celular
        return Decimal(0)


def _codes(codes: list[str]) -> str:
    return ", ".join(codes[:6]) + (f" y {len(codes) - 6} más" if len(codes) > 6 else "")


def _ceil(x: Decimal) -> int:
    return int(x) + (1 if x % 1 else 0)


# Familias de material: el tecnico escribe "Cable UTP Cat6" y la sugerencia calcula "Cable (metros...)". Con
# comparar solo el nombre exacto se proponia el cable como nuevo y se terminaba con el doble de metros.
FAMILIAS = (
    ("cable", ("cable", "utp", "cat5", "cat6", "cat 6", "cat 5")),
    ("rj45", ("rj45", "rj-45", "conector")),
    ("grabador", ("nvr", "dvr", "grabador")),
    ("canalizacion", ("emt", "tubo", "canalizaci", "conduit")),
)
UNIDADES_METRO = {"m", "mts", "mt", "metro", "metros"}


def _familia(nombre: str) -> str | None:
    n = (nombre or "").lower()
    for fam, palabras in FAMILIAS:
        if any(w in n for w in palabras):
            return fam
    return None


def _mismo_material(sugerido: dict, it) -> bool:
    nombre = (getattr(it, "name", None) or "").strip().lower()
    if nombre == sugerido["name"].lower():
        return True
    fam = _familia(sugerido["name"])
    if not fam or fam != _familia(nombre):
        return False
    # el cable solo se compara metro contra metro; no se suma una bobina con metros
    if fam == "cable":
        return (getattr(it, "unit", "") or "").strip().lower() in UNIDADES_METRO and sugerido["unit"].lower() in UNIDADES_METRO
    return True


def suggest_materials(kind: str, points: list, items: list | None = None) -> list[dict]:
    """Sugerencia a partir de los puntos levantados, sus observaciones y lo que ya esta cargado.

    No reemplaza nada: devuelve propuestas con el motivo; el usuario elige cuales agregar.
    action: nuevo (no existe), sumar (ya existe y falta cantidad), cubierto (ya alcanza), revisar
    (material habitual del tipo de solucion sin cantidad calculable)."""
    spec = SPECS.get(kind, SPECS["otro"])
    n = len(points)
    if not n:
        return []
    out: list[dict] = []

    def add(name: str, qty, unit: str, reason: str, kind_: str | None = None) -> None:
        qty = Decimal(str(qty))
        for o in out:
            if o["name"] == name:
                o["quantity"] += qty
                o["reason"] += f"; {reason}"
                return
        out.append({"name": name, "quantity": qty, "unit": unit, "kind": kind_ or classify_kind(name), "reason": reason})

    # 1) el equipo principal de cada punto, agrupado por sus caracteristicas
    group_key = {"cctv": ("tipo", "resolucion"), "redes": ("equipo",), "acceso": ("cerradura",), "asistencia": ("metodo",), "anpr": ()}.get(kind, ())
    groups: dict[str, list[str]] = {}
    for p in points:
        data = _data(p)
        parts = []
        for k in group_key:
            v = data.get(k)
            if isinstance(v, list):
                v = "/".join(v)
            if v:
                parts.append(str(v))
        label = f"{spec['point_label']} {' '.join(parts)}".strip() if parts else f"{spec['point_label']} ({spec['point_prefix']})"
        groups.setdefault(label, []).append(_code(p))
    for label, codes in groups.items():
        add(label, len(codes), "Unid", f"{len(codes)} punto(s) levantado(s): {_codes(codes)}", "equipo")

    # 2) metros: cable con 15 % de holgura por rutas reales
    meters = Decimal(0)
    for p in points:
        data = _data(p)
        for key in ("metros", "distancia_m", "distancia_camara_m"):
            meters += _num(data, key)
    rules = spec.get("suggest", {})
    for name, qty in rules.get("per_point", []):
        add(name, n * qty, "Unid", f"{qty} por cada uno de los {n} puntos")
    if meters > 0:
        holgura = (meters * Decimal("1.15")).quantize(Decimal(1))
        add("Cable (metros con 15 % de holgura)", holgura, "m", f"{meters:g} m medidos entre los puntos + 15 %", "material")
        for name, per in rules.get("per_meters", []):
            add(name, max(1, _ceil(holgura / per)), "Unid", f"{holgura} m de cable / {per} m por unidad")

    # 3) reglas por campo del punto (CCTV es el grueso del negocio de Crimson)
    if kind == "cctv":
        poe, dc, ext, alto, emt, pvc = [], [], [], [], Decimal(0), Decimal(0)
        for p in points:
            data, code = _data(p), _code(p)
            if data.get("alimentacion") == "PoE":
                poe.append(code)
            elif data.get("alimentacion") == "12 VDC":
                dc.append(code)
            if data.get("ambiente") == "Exterior":
                ext.append(code)
            if _num(data, "altura_m") > 4:
                alto.append(f"{code} ({_num(data, 'altura_m'):g} m)")
            dist = _num(data, "distancia_m")
            if data.get("canalizacion") == "EMT":
                emt += dist
            elif data.get("canalizacion") == "PVC":
                pvc += dist
        add("Conectores RJ45", n * 2, "Unid", f"2 por cámara ({n} cámaras)", "material")
        if poe:
            puertos = 8 if len(poe) <= 7 else (16 if len(poe) <= 15 else 24)
            add(f"Switch PoE {puertos} puertos", 1, "Unid", f"{len(poe)} cámara(s) PoE: {_codes(poe)}", "equipo")
        if dc:
            add("Fuente 12 VDC", len(dc), "Unid", f"cámaras a 12 VDC: {_codes(dc)}", "equipo")
        if ext:
            add("Caja de paso exterior", len(ext), "Unid", f"cámaras en exterior: {_codes(ext)}", "material")
        canales = 4 if n <= 4 else (8 if n <= 8 else (16 if n <= 16 else 32))
        add(f"NVR {canales} canales", 1, "Unid", f"{n} cámara(s) levantada(s)", "equipo")
        if emt > 0:
            add("Tubo EMT 3/4 (3 m)", _ceil(emt * Decimal("1.15") / 3), "Unid", f"{emt:g} m en EMT + 15 %, tubos de 3 m", "material")
        if pvc > 0:
            add("Tubo PVC 3/4 (3 m)", _ceil(pvc * Decimal("1.15") / 3), "Unid", f"{pvc:g} m en PVC + 15 %, tubos de 3 m", "material")
        if alto:
            add("Alquiler de andamio / escalera extensible", 1, "Unid", f"montaje sobre 4 m: {_codes(alto)}", "material")

    # 4) observaciones de cada punto
    for words, name, kind_, unit in NOTE_RULES:
        hits = [_code(p) for p in points if any(w in _notes(p) for w in words)]
        if hits and not any(o["name"] == name for o in out):
            qty = 1 if "andamio" in name.lower() else len(hits)
            add(name, qty, unit, f"observaciones de {_codes(hits)}", kind_)

    # 5) materiales habituales del tipo de solucion: sin cantidad, para revisar
    for extra in spec.get("materials", []):
        ya = any(extra.lower() in o["name"].lower() or o["name"].lower() in extra.lower() for o in out)
        if not ya and _familia(extra) and any(_familia(o["name"]) == _familia(extra) for o in out):
            ya = True  # "Cable UTP Cat6" habitual sobra si ya se calcularon los metros de cable
        if not ya:
            out.append(
                {
                    "name": extra,
                    "quantity": Decimal(0),
                    "unit": "Unid",
                    "kind": classify_kind(extra),
                    "reason": "Habitual en este tipo de solución: confirmá la cantidad",
                }
            )

    # 6) cruce con lo ya cargado: no duplicar, proponer sumar
    existing = list(items or [])
    for o in out:
        match = next((it for it in existing if _mismo_material(o, it)), None)
        o["item_id"] = getattr(match, "id", None) if match else None
        o["existing_quantity"] = Decimal(str(match.quantity or 0)) if match else Decimal(0)
        if o["quantity"] <= 0:
            # sin cantidad calculada no se puede afirmar que "ya alcanza": solo que hay que revisarla
            o["action"], o["add_quantity"] = "revisar", Decimal(0)
        elif match:
            if match and (getattr(match, "name", "") or "").strip().lower() != o["name"].lower():
                o["matched_name"] = match.name  # se suma a la linea que ya existe, con su nombre
            falta = o["quantity"] - o["existing_quantity"]
            o["action"], o["add_quantity"] = ("sumar", falta) if falta > 0 else ("cubierto", Decimal(0))
        else:
            o["action"], o["add_quantity"] = "nuevo", o["quantity"]
    return out
