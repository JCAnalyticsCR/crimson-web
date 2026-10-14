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
            {
                "key": "tipo",
                "label": "Tipo",
                "type": "select",
                "options": ["Bullet", "Domo", "Turret", "PTZ", "Fisheye", "Ojo de pez dual", "Lente dual 180°", "360°"],
            },
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
            # v2: opcionales, alimentan los "Calculos del sistema" (almacenamiento y PoE)
            {"key": "bitrate_mbps", "label": "Bitrate (Mb/s)", "type": "number", "unit": "Mb/s", "hint": "Vacío = bitrate de diseño del sistema."},
            {"key": "consumo_w", "label": "Consumo (W)", "type": "number", "unit": "W", "hint": "Para el presupuesto PoE del switch."},
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
        # v2 (curso Hikvision, casos AC08-AC11): todos los campos nuevos son opcionales; los puntos viejos siguen igual.
        # "salida" queda como campo anterior (legacy): metodo_salida lo reemplaza, pero se sigue leyendo.
        "fields": [
            {"key": "ubicacion", "label": "Puerta / ubicación", "type": "text", "section": "Puerta"},
            {"key": "zona_origen", "label": "Zona de origen", "type": "text", "placeholder": "Recepción", "section": "Puerta"},
            {"key": "zona_destino", "label": "Zona de destino", "type": "text", "placeholder": "Bodega", "section": "Puerta"},
            {
                "key": "tipo_puerta",
                "label": "Tipo de puerta",
                "type": "select",
                "options": ["Madera", "Metal", "Vidrio", "Portón", "Torniquete"],
                "section": "Puerta",
            },
            {"key": "material", "label": "Material", "type": "select", "options": ["Madera", "Metal", "Vidrio", "Aluminio", "Otro"], "section": "Puerta"},
            {"key": "hojas", "label": "Hojas", "type": "select", "options": ["1", "2"], "section": "Puerta"},
            {"key": "hojas_controladas", "label": "Hojas controladas", "type": "select", "options": ["1", "2"], "section": "Puerta"},
            {"key": "marco", "label": "Tiene marco", "type": "bool", "section": "Puerta"},
            {"key": "vidrio_sin_marco", "label": "Vidrio sin marco", "type": "bool", "section": "Puerta"},
            {"key": "espesor_mm", "label": "Espesor (mm)", "type": "number", "unit": "mm", "section": "Puerta"},
            {
                "key": "apertura",
                "label": "Sentido de apertura",
                "type": "select",
                "options": ["Hacia adentro", "Hacia afuera", "Vaivén", "Corrediza"],
                "section": "Puerta",
            },
            {"key": "herrajes", "label": "Herrajes", "type": "text", "placeholder": "Bisagras, manija, pivote…", "section": "Puerta"},
            {
                "key": "cierrapuertas",
                "label": "Cierrapuertas",
                "type": "select",
                "options": ["Existe y funciona", "Hay que instalarlo", "No aplica"],
                "section": "Puerta",
            },
            {"key": "alineacion_ok", "label": "Puerta alineada (cierra bien)", "type": "bool", "section": "Puerta"},
            {
                "key": "metodo_ingreso",
                "label": "Método de ingreso",
                "type": "multi",
                "options": ["Tarjeta", "PIN", "Biometría", "Botón", "Otro"],
                "section": "Identificación",
            },
            {
                "key": "metodo_salida",
                "label": "Método de salida",
                "type": "multi",
                "options": ["Tarjeta", "PIN", "Biometría", "Botón", "Otro"],
                "section": "Identificación",
            },
            {
                "key": "lectura",
                "label": "Identificación",
                "type": "multi",
                "options": ["Tarjeta", "Huella", "Facial", "PIN", "App / QR"],
                "section": "Identificación",
            },
            {
                "key": "salida",
                "label": "Salida",  # mismo nombre en el informe del cliente
                "type": "select",
                "options": ["Botón", "Sensor de movimiento", "Lector interior", "Barra antipánico"],
                "section": "Identificación",
                "legacy": True,  # el portal lo muestra solo si el punto ya lo trae, marcado como dato anterior
            },
            {
                "key": "tipo_credencial",
                "label": "Tipo de credencial",
                "type": "select",
                "options": ["Tarjeta 125 kHz (EM)", "Tarjeta 13,56 MHz (Mifare)", "Llavero", "Móvil / QR", "Otro"],
                "section": "Identificación",
            },
            {"key": "teclado_fisico", "label": "Teclado físico", "type": "bool", "section": "Identificación"},
            {
                "key": "proteccion_ip",
                "label": "Protección IP",
                "type": "select",
                "options": ["No requiere", "IP65", "IP66", "IP67"],
                "section": "Identificación",
            },
            {
                "key": "proteccion_ik",
                "label": "Protección IK (vandalismo)",
                "type": "select",
                "options": ["No requiere", "IK08", "IK10"],
                "section": "Identificación",
            },
            {
                "key": "cerradura",
                "label": "Cerradura",
                "type": "select",
                "options": ["Magnética 280 kg", "Magnética 600 kg", "Pestillo eléctrico", "Existente"],
                "section": "Cerradura y seguridad",
            },
            {
                "key": "soporte_montaje",
                "label": "Soporte de montaje",
                "type": "select",
                "options": ["L", "ZL", "U", "No requiere", "Por definir"],
                "section": "Cerradura y seguridad",
            },
            {"key": "contacto_puerta", "label": "Contacto de puerta", "type": "bool", "section": "Cerradura y seguridad"},
            {"key": "alarma_forzada", "label": "Alarma de puerta forzada", "type": "bool", "section": "Cerradura y seguridad"},
            {
                "key": "liberacion_emergencia",
                "label": "Liberación de emergencia",
                "type": "bool",
                "hint": "Requiere diseño y aprobación técnica.",
                "section": "Cerradura y seguridad",
            },
            {
                "key": "respaldo_energia",
                "label": "Respaldo de energía",
                "type": "select",
                "options": ["Batería en la fuente", "UPS", "No"],
                "section": "Cerradura y seguridad",
            },
            {"key": "energia", "label": "Energía disponible", "type": "select", "options": ["Sí, en sitio", "Hay que llevarla"], "section": "Conexión"},
            {"key": "distancia_m", "label": "Distancia al gabinete (m)", "type": "number", "unit": "m", "section": "Conexión"},
            {"key": "controlador_asignado", "label": "Controlador asignado", "type": "text", "placeholder": "CTRL-01", "section": "Conexión"},
            {"key": "puerto", "label": "Puerto del controlador", "type": "text", "placeholder": "Puerta 1 / lector 2", "section": "Conexión"},
            {"key": "protocolo", "label": "Protocolo del lector", "type": "select", "options": ["Wiegand", "RS-485", "OSDP", "TCP/IP"], "section": "Conexión"},
            # funciones avanzadas: ocultas por defecto en el formulario
            {
                "key": "antipassback",
                "label": "Anti-passback",
                "type": "bool",
                "advanced": True,
                "hint": "Exige lector de entrada y de salida y un controlador compatible.",
            },
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

# Version de la plantilla (SPECS). Los levantamientos guardan con que version se llenaron: los existentes quedan
# en 1 y los nuevos toman esta. Subirla cuando cambien campos o reglas de forma que importe saber la version.
TEMPLATE_VERSION = 2

# Estado de cada campo de un punto (SurveyPoint.data_status, paralelo a data). Sin estados = comportamiento de
# siempre. Con estados, un campo pendiente o vacio nunca se toma como 0: la regla que lo usa pide "revisar".
DATA_STATUS = ("confirmado", "estimado", "no_aplica", "pendiente")

# Seccion comun del levantamiento (documento de Andres §4) y bloques de sistema. Vive en Survey.common.
COMMON_FIELDS: list[FieldSpec] = [
    {"key": "objetivo", "label": "Objetivo del cliente", "type": "text", "placeholder": "Controlar el ingreso a bodega"},
    {"key": "areas", "label": "Áreas", "type": "text"},
    {"key": "infraestructura", "label": "Infraestructura existente", "type": "text"},
    {"key": "condiciones_ambientales", "label": "Condiciones ambientales", "type": "text"},
    {"key": "energia", "label": "Energía", "type": "text"},
    {"key": "red", "label": "Red", "type": "text"},
    {"key": "canalizaciones", "label": "Canalizaciones", "type": "text"},
    {"key": "rutas", "label": "Rutas", "type": "text"},
    {"key": "alturas", "label": "Alturas", "type": "text"},
    {"key": "acceso_sitio", "label": "Acceso al sitio", "type": "text"},
    {"key": "horarios", "label": "Horarios de trabajo", "type": "text"},
    {"key": "restricciones", "label": "Restricciones", "type": "text"},
    {"key": "permisos", "label": "Permisos", "type": "text"},
    {"key": "personal_cliente", "label": "Personal del cliente", "type": "text"},
    {"key": "trabajos_previos", "label": "Trabajos previos", "type": "text"},
]
ACCESO_SISTEMA_FIELDS: list[FieldSpec] = [
    {"key": "usuarios", "label": "Usuarios", "type": "number"},
    {"key": "visitantes", "label": "Visitantes", "type": "number"},
    {"key": "reserva_credenciales", "label": "Reserva de credenciales", "type": "number", "hint": "Explícita: no se agrega reserva automática.", "default": 0},
    {"key": "paquete_credenciales", "label": "Credenciales por paquete", "type": "number", "hint": "Opcional: convierte el total a paquetes."},
    {"key": "asistencia", "label": "Lleva tiempo y asistencia", "type": "bool"},
    {"key": "horarios", "label": "Horarios de acceso", "type": "text", "placeholder": "L-V 7:00-17:00"},
    {
        "key": "admin_central",
        "label": "Administración",
        "type": "select",
        "options": ["Software central (PC)", "Web / nube", "Solo en el controlador", "Por definir"],
    },
    {"key": "emisor", "label": "Enrolador de tarjetas", "type": "bool"},
]
CCTV_SISTEMA_FIELDS: list[FieldSpec] = [
    {"key": "canales", "label": "Canales del grabador", "type": "number"},
    {"key": "modo", "label": "Grabación", "type": "select", "options": ["Continua", "Por evento"]},
    {"key": "dias_retencion", "label": "Días de retención", "type": "number", "unit": "días"},
    {
        "key": "bitrate_mbps",
        "label": "Bitrate de diseño por cámara (Mb/s)",
        "type": "number",
        "unit": "Mb/s",
        "hint": "Se usa en las cámaras sin bitrate propio.",
    },
    {"key": "codec", "label": "Códec", "type": "select", "options": ["H.264", "H.265", "H.265+", "Smart codec"]},
    {"key": "redundancia", "label": "Redundancia", "type": "select", "options": ["Ninguna", "RAID 1", "RAID 5", "Grabador de respaldo"]},
    {"key": "entrada_nvr_mbps", "label": "Ancho de entrada del grabador (Mb/s)", "type": "number", "unit": "Mb/s"},
    {"key": "switch_poe_w", "label": "Presupuesto PoE del switch (W)", "type": "number", "unit": "W"},
    {"key": "reserva_poe_pct", "label": "Reserva PoE (%)", "type": "number", "unit": "%", "default": 20},
]
PARAMETROS_FIELDS: list[FieldSpec] = [
    {"key": "desperdicio_cable_pct", "label": "Desperdicio de cable y tubo (%)", "type": "number", "unit": "%", "default": 15},
]
COMMON_BLOCKS = {"acceso_sistema": ACCESO_SISTEMA_FIELDS, "cctv_sistema": CCTV_SISTEMA_FIELDS, "parametros": PARAMETROS_FIELDS}
DESPERDICIO_DEFAULT = Decimal(15)  # % de holgura de cable y tubo que se usaba fijo en el codigo

# Modelos vistos en el curso Hikvision: nunca son catalogo confirmado. Si una sugerencia los nombra queda en
# "revisar" como referencia del curso, por verificar.
COURSE_REFS = ("DS-K1104MK", "DS-K7P04", "DS-K4H250S", "DS-K2624", "DS-K2621", "IC S50", "DS-K1F100A-D8E")


def common_spec() -> dict:
    return {
        "template_version": TEMPLATE_VERSION,
        "data_status": list(DATA_STATUS),
        "fields": COMMON_FIELDS,
        "blocks": {
            "acceso_sistema": {"label": "Sistema de control de acceso", "kinds": ["acceso"], "fields": ACCESO_SISTEMA_FIELDS},
            "cctv_sistema": {"label": "Sistema de CCTV", "kinds": ["cctv"], "fields": CCTV_SISTEMA_FIELDS},
            "parametros": {"label": "Parámetros de cálculo", "kinds": [], "fields": PARAMETROS_FIELDS},
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


# Alquileres y servicios de terceros: no son material por metro ni equipo instalado. Se cotizan por dia,
# jornada o servicio (un elevador salia como material en "m").
SERVICE_WORDS = (
    "alquiler",
    "elevador",
    "plataforma elevadora",
    "manlift",
    "montacargas",
    "andamio",
    "grua",
    "grúa",
    "transporte",
    "flete",
    "acarreo",
)


def classify_kind(name: str) -> str:
    n = (name or "").lower()
    if any(w in n for w in SERVICE_WORDS):
        return "servicio"  # va primero: "alquiler de UPS" es un servicio, no el equipo
    return "equipo" if any(w in n for w in EQUIPMENT_WORDS) else "material"


def normalize_item(name: str, kind: str | None, unit: str | None) -> tuple[str, str]:
    """Tipo y unidad finales de una linea del levantamiento. Un alquiler/servicio cargado en el bloque de
    materiales pasa a servicio, y un servicio nunca queda medido en metros."""
    k = kind or classify_kind(name)
    if k == "material" and classify_kind(name) == "servicio":
        k = "servicio"
    u = (unit or "").strip() or "Unid"
    if k == "servicio" and (u.lower() in UNIDADES_METRO or u == "Unid"):
        u = "servicio"
    return k, u


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


def normalize_data_status(status: dict | None) -> dict | None:
    """Estados por campo. Vacio -> None (comportamiento de siempre). Un estado desconocido es error."""
    if not status:
        return None
    out = {}
    for k, v in status.items():
        if v in (None, ""):
            continue
        if v not in DATA_STATUS:
            raise ValueError(f"estado '{v}' de '{k}' no es válido ({', '.join(DATA_STATUS)})")
        out[str(k)[:60]] = v
    return out or None


def _clean_number(label: str, v):
    try:
        n = parse_number(v)
    except ValueError as e:
        raise ValueError(f"{label}: '{e}' no es un número") from e
    if n is not None and n < 0:
        raise ValueError(f"{label}: no puede ser negativo")
    return None if n is None else (int(n) if n == n.to_integral_value() else float(n))


def normalize_common(common: dict | None) -> dict | None:
    """Seccion comun: textos recortados y numeros de los bloques de sistema validados igual que los del punto."""
    if common is None:
        return None
    out = dict(common)
    for f in COMMON_FIELDS:
        if isinstance(out.get(f["key"]), str):
            out[f["key"]] = out[f["key"]][:2000]
    for block, fields in COMMON_BLOCKS.items():
        b = out.get(block)
        if b is None:
            continue
        if not isinstance(b, dict):
            raise ValueError(f"{block}: tiene que ser un objeto")
        b = dict(b)
        for f in fields:
            if f["type"] == "number" and f["key"] in b:
                b[f["key"]] = _clean_number(f["label"], b[f["key"]])
        out[block] = b
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
    (("andamio", "escalera", "muy alto", "altura"), "Alquiler de andamio / escalera extensible", "servicio", "dia"),
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
    ("alquiler", ("alquiler", "elevador", "andamio", "grua", "grúa", "montacargas", "plataforma elevadora")),
    ("transporte", ("transporte", "flete", "acarreo")),
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


def covered_line(it) -> str | None:
    """Linea generica de la sugerencia que cubre un equipo elegido con la recomendacion ("Cámara Bullet 4 MP" ->
    el producto real). Las lineas de siempre no la tienen."""
    rec = getattr(it, "recommendation", None)
    line = rec.get("line") if isinstance(rec, dict) else None
    return line.strip().lower() if isinstance(line, str) and line.strip() else None


def per_unit(it) -> Decimal:
    """Unidades de la linea generica que cubre cada unidad del producto elegido (1 bobina = 305 m; 1 disco = 6 TB).
    1 para todo lo demas."""
    rec = getattr(it, "recommendation", None)
    try:
        n = parse_number(rec.get("per_unit")) if isinstance(rec, dict) else None
    except ValueError:
        n = None
    return n if n is not None and n > 0 else Decimal(1)


def quantity_in_line(it, line_name: str) -> Decimal:
    """Cantidad ya cargada expresada en las unidades de la linea sugerida."""
    q = Decimal(str(getattr(it, "quantity", 0) or 0))
    return q * per_unit(it) if covered_line(it) == line_name.strip().lower() else q


def _mismo_material(sugerido: dict, it) -> bool:
    cubre = covered_line(it)
    if cubre:  # equipo real elegido con la recomendacion: cubre solo su linea generica
        return cubre == sugerido["name"].strip().lower()
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


def point_kind(p, default: str) -> str:
    """Tipo de un punto: el suyo si lo tiene (una puerta en un levantamiento de CCTV), si no el del levantamiento."""
    k = p.kind if hasattr(p, "kind") else (p.get("kind") if isinstance(p, dict) else None)
    return k if k in SPECS else default


# ---------- puntos preparados para las reglas (estado por campo) ----------
# Campos que alimentan una cantidad, por tipo de punto. Con estados en el punto, si uno de estos esta pendiente o
# vacio la linea que lo usa queda en "revisar" con "falta <campo>" (nunca se toma como 0).
CALC_FIELDS = {
    "cctv": ("distancia_m", "altura_m"),
    "cableado": ("metros",),
    "acceso": ("distancia_m",),
    "anpr": ("distancia_camara_m",),
    "otro": ("distancia_m",),
    "ups": ("carga_w", "tomas"),
}


class _Pt:
    """Un punto listo para las reglas: datos sin lo pendiente ni lo que no aplica, y los campos faltantes y
    estimados (solo si el punto trae estados; sin estados es el punto de siempre)."""

    __slots__ = ("id", "code", "kind", "label", "data", "notes", "status", "missing", "estimated")

    def __init__(self, p, default_kind: str):
        self.id = getattr(p, "id", None) if not isinstance(p, dict) else p.get("id")
        self.code = _code(p)
        self.kind = point_kind(p, default_kind)
        self.label = (p.get("label") if isinstance(p, dict) else getattr(p, "label", None)) or ""
        self.notes = (p.get("notes") if isinstance(p, dict) else getattr(p, "notes", None)) or ""
        status = (p.get("data_status") if isinstance(p, dict) else getattr(p, "data_status", None)) or {}
        self.status = dict(status) if isinstance(status, dict) else {}
        self.data = dict(_data(p))
        self.missing: dict[str, str] = {}
        self.estimated: dict[str, str] = {}
        if not self.status:
            return
        fields = {f["key"]: f for f in SPECS[self.kind]["fields"]}
        for key, st in self.status.items():
            # pendiente: un numero sin confirmar no se usa; no aplica: no suma
            if st == "no_aplica" or (st == "pendiente" and fields.get(key, {}).get("type") == "number"):
                self.data.pop(key, None)
        calc = list(CALC_FIELDS.get(self.kind, ()))
        if self.kind == "acceso" and _acceso_nuevo(self.data) and not self.data.get("salida"):
            calc.append("metodo_salida")  # sin salida no se sabe cuantos lectores ni botones lleva
        if self.kind == "acceso" and str(self.data.get("hojas") or "") == "2":
            calc.append("hojas_controladas")
        for key in calc:
            st = self.status.get(key, "confirmado")
            label = fields.get(key, {}).get("label", key)
            if st == "no_aplica":
                continue
            if st == "pendiente" or _empty(self.data.get(key)):
                self.missing[key] = label
            elif st == "estimado":
                self.estimated[key] = label


def _empty(v) -> bool:
    return v is None or v == "" or v == []


def _gaps(pts: list, *keys: str) -> dict:
    """Extras de una linea: campos faltantes y estimados de los puntos que la alimentan."""
    falta: dict[str, list[str]] = {}
    est: dict[str, list[str]] = {}
    for p in pts:
        for k in keys:
            if k in getattr(p, "missing", {}):
                falta.setdefault(p.missing[k], []).append(_code(p))
            elif k in getattr(p, "estimated", {}):
                est.setdefault(p.estimated[k], []).append(_code(p))
    out = {}
    if falta:
        out["_falta"] = falta
    if est:
        out["_estimado"] = est
    return out


class _Ctx:
    """Parametros del calculo que antes estaban fijos en el codigo."""

    def __init__(self, common: dict | None):
        self.common = common or {}
        self.pct = waste_pct(self.common)
        self.factor = 1 + self.pct / 100  # 15 % -> 1.15, exactamente lo de antes
        self.pct_txt = _pct_txt(self.pct)


def waste_pct(common: dict | None) -> Decimal:
    """Desperdicio de cable y tubo (%): parametro del levantamiento, 15 por defecto."""
    v = (((common or {}).get("parametros")) or {}).get("desperdicio_cable_pct")
    try:
        n = parse_number(v)
    except ValueError:
        n = None
    return DESPERDICIO_DEFAULT if n is None or n < 0 else n


def _pct_txt(x: Decimal) -> str:
    return str(int(x)) if x == x.to_integral_value() else f"{x.normalize():f}"


# Agrupacion del equipo principal de cada punto segun sus caracteristicas
GROUP_KEY = {"cctv": ("tipo", "resolucion"), "redes": ("equipo",), "acceso": ("cerradura",), "asistencia": ("metodo",), "anpr": ()}
# Llaves de distancia que se convierten en metros de cable UTP (acceso lleva su propio cable 4x22)
METER_KEYS = ("metros", "distancia_m", "distancia_camara_m")
LECTORES = {
    "Tarjeta": "Lector de tarjeta",
    "Huella": "Lector biométrico de huella",
    "Facial": "Terminal de reconocimiento facial",
    "PIN": "Teclado PIN",
    "App / QR": "Lector QR",
}
SALIDAS = {"Botón": "Botón de salida", "Sensor de movimiento": "Sensor de salida (PIR)", "Barra antipánico": "Barra antipánico", "Lector interior": None}


def _main_equipment(kind: str, pts: list, add) -> None:
    """1) el equipo principal de cada punto, agrupado por sus caracteristicas."""
    spec = SPECS[kind]
    if kind == "ups":
        # el "punto" de un levantamiento de UPS es lo que se respalda; el equipo es la UPS que alcanza esa carga
        for p in pts:
            w = _num(_data(p), "carga_w")
            if w > 0:
                va = _ceil(w / Decimal("0.6") / 500) * 500  # factor de potencia 0.6 y escalones de 500 VA
                add(f"UPS {va} VA", 1, "Unid", f"{_code(p)}: {w:g} W a respaldar", "equipo", **_gaps([p], "carga_w"))
            else:
                add("UPS (capacidad por definir)", 1, "Unid", f"{_code(p)}: sin carga anotada", "equipo", **_gaps([p], "carga_w"))
        return
    groups: dict[str, list[str]] = {}
    for p in pts:
        data = _data(p)
        parts = []
        for k in GROUP_KEY.get(kind, ()):
            v = data.get(k)
            if isinstance(v, list):
                v = "/".join(v)
            if v:
                parts.append(str(v))
        label = f"{spec['point_label']} {' '.join(parts)}".strip() if parts else f"{spec['point_label']} ({spec['point_prefix']})"
        groups.setdefault(label, []).append(_code(p))
    for label, codes in groups.items():
        add(label, len(codes), "Unid", f"{len(codes)} punto(s) levantado(s): {_codes(codes)}", "equipo")


def _rules_cctv(pts: list, add, ctx: _Ctx) -> None:
    n = len(pts)
    poe, dc, ext, alto, emt, pvc = [], [], [], [], Decimal(0), Decimal(0)
    emt_pts, pvc_pts = [], []
    for p in pts:
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
            emt_pts.append(p)
        elif data.get("canalizacion") == "PVC":
            pvc += dist
            pvc_pts.append(p)
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
    for nombre, metros, tramo in (("EMT", emt, emt_pts), ("PVC", pvc, pvc_pts)):
        gaps = _gaps(tramo, "distancia_m")
        if metros > 0:
            # tubos de 3 m: metros x (1 + desperdicio) / 3, hacia arriba
            add(
                f"Tubo {nombre} 3/4 (3 m)",
                _ceil(metros * ctx.factor / 3),
                "Unid",
                f"{metros:g} m en {nombre} + {ctx.pct_txt} %, tubos de 3 m",
                "material",
                **gaps,
            )
        elif gaps:
            add(f"Tubo {nombre} 3/4 (3 m)", 0, "Unid", f"metros en {nombre} sin dato", "material", **gaps)
    gaps = _gaps(pts, "altura_m")
    if alto:
        add("Alquiler de andamio / escalera extensible", 1, "dia", f"montaje sobre 4 m: {_codes(alto)}", "servicio", **gaps)
    elif gaps:
        add("Alquiler de andamio / escalera extensible", 0, "dia", "altura de montaje sin dato: no se sabe si pasa de 4 m", "servicio", **gaps)


# ---------- control de acceso (v2, curso Hikvision) ----------
METODOS_CREDENCIAL = ("Tarjeta", "PIN", "Biometría")  # metodos que exigen un lector en ese lado de la puerta
# lectura vieja -> metodo; salida vieja -> metodos de salida (compatibilidad: "Lector interior" es credencial)
LECTURA_A_METODO = {"Tarjeta": "Tarjeta", "PIN": "PIN", "Huella": "Biometría", "Facial": "Biometría", "App / QR": "Otro"}
SALIDA_A_METODO = {"Botón": ["Botón"], "Lector interior": ["Tarjeta"], "Sensor de movimiento": ["Otro"], "Barra antipánico": ["Otro"]}
SOPORTES = ("L", "ZL", "U")


def _acceso_nuevo(data: dict) -> bool:
    """La puerta usa los metodos de ingreso/salida (v2). Sin ellos se calcula exactamente como antes."""
    return bool(data.get("metodo_ingreso") or data.get("metodo_salida"))


def door_methods(data: dict) -> tuple[list[str], list[str]]:
    """Metodos de ingreso y de salida de una puerta; si no estan, se leen de los campos viejos (lectura, salida)."""
    ing = list(data.get("metodo_ingreso") or []) or list(dict.fromkeys(LECTURA_A_METODO[x] for x in data.get("lectura") or [] if x in LECTURA_A_METODO))
    sal = list(data.get("metodo_salida") or []) or list(SALIDA_A_METODO.get(data.get("salida") or "", []))
    return ing, sal


def door_readers(data: dict) -> tuple[int, int]:
    """Lectores de una puerta: (entrada, salida).
    Entrada: 1, salvo que el ingreso este definido y no use credencial (solo boton u otro).
    Salida: 1 si la salida usa tarjeta, PIN o biometria (o el viejo "Lector interior"); si no, 0.
    Total del sistema = n + 2(N - n), con N puertas y n las que salen sin credencial."""
    ing, sal = door_methods(data)
    entrada = 0 if ing and not any(m in METODOS_CREDENCIAL for m in ing) else 1
    salida = 1 if any(m in METODOS_CREDENCIAL for m in sal) else 0
    return entrada, salida


def _reader_name(metodos: list[str], lectura: list[str] | None = None) -> str | None:
    m = [x for x in metodos if x in METODOS_CREDENCIAL]
    if not m:
        return None
    if "Biometría" in m:
        lect = lectura or []
        if "Facial" in lect:
            return LECTORES["Facial"]
        return LECTORES["Huella"] if "Huella" in lect else "Lector biométrico (huella o facial por definir)"
    if "Tarjeta" in m and "PIN" in m:
        return "Lector de tarjeta con teclado PIN"
    return LECTORES["Tarjeta"] if "Tarjeta" in m else LECTORES["PIN"]


def controller_options(n: int) -> list[dict]:
    """Combinaciones de controladoras (1, 2 o 4 puertas) que cubren n puertas, para que el humano elija.
    Capacidad = suma de puertas de cada controladora; reserva = capacidad - n. La marcada "actual" es la regla
    de siempre (hasta 2 puertas una de 2; si no, ceil(n/4) de 4) y sigue siendo la sugerencia por defecto.
    Ejemplo 17 puertas: 4x4 + 1x1 = 17 exacto, 4x4 + 1x2 = 18 (1 de reserva), 5x4 = 20 (3 de reserva)."""
    if n <= 0:
        return []
    q, r = divmod(n, 4)
    combos = {
        "actual": ((2, 1),) if n <= 2 else ((4, _ceil(Decimal(n) / 4)),),
        "exacta": tuple(x for x in ((4, q), (2, r // 2), (1, r % 2)) if x[1]),
        "con_2": tuple(x for x in ((4, q), (2, 1 if r else 0)) if x[1]) if r in (1, 2) else None,
        "solo_2": ((2, _ceil(Decimal(n) / 2)),),
    }
    out, vistos = [], set()
    for clave, combo in combos.items():
        if not combo or combo in vistos:
            continue
        vistos.add(combo)
        cap = sum(p * c for p, c in combo)
        out.append(
            {
                "label": " + ".join(f"{c}×{p}" for p, c in combo) + f" = {cap} puertas",
                "combo": [{"puertas": p, "cantidad": c} for p, c in combo],
                "capacidad": cap,
                "reserva": cap - n,
                "controladoras": sum(c for _, c in combo),
                "actual": clave == "actual",
            }
        )
    return sorted(out, key=lambda o: (o["reserva"], o["controladoras"]))


def credentials(sis: dict) -> dict | None:
    """Credenciales = usuarios + visitantes + reserva explicita (por defecto 0; nunca una reserva oculta).
    Con paquete: paquetes = ceil(total / unidades por paquete). 200 en paquetes de 25 -> 8 paquetes."""
    if not sis or all(_empty(sis.get(k)) for k in ("usuarios", "visitantes", "reserva_credenciales", "paquete_credenciales")):
        return None
    faltan = [lab for k, lab in (("usuarios", "Usuarios"), ("visitantes", "Visitantes")) if _empty(sis.get(k))]
    u, v, r = _num(sis, "usuarios"), _num(sis, "visitantes"), _num(sis, "reserva_credenciales")
    total = u + v + r
    paq = _num(sis, "paquete_credenciales")
    out = {
        "usuarios": u,
        "visitantes": v,
        "reserva": r,
        "total": total,
        "paquete": paq or None,
        "paquetes": _ceil(total / paq) if paq > 0 else None,
        "formula": f"{u:g} usuarios + {v:g} visitantes + {r:g} de reserva = {total:g} credenciales",
        "faltan": faltan,
    }
    if paq > 0:
        out["formula"] += f"; paquetes de {paq:g} → {out['paquetes']}"
    return out


def _door_alerts(pts: list, sis: dict) -> tuple[list[str], list[dict]]:
    """Advertencias y dependencias tecnicas de las puertas. El anti-passback solo cuenta si esta activo (AC10);
    activo sin la arquitectura que lo soporta queda como dependencia pendiente visible (AC11). No bloquea nada."""
    adv: list[str] = []
    dep: list[dict] = []
    dobles = [_code(p) for p in pts if door_readers(_data(p))[1]]
    if dobles:
        adv.append(f"{len(dobles)} puerta(s) con credencial también a la salida ({_codes(dobles)}): revisar controlador, puertos y accesorios.")
    emerg = [_code(p) for p in pts if _data(p).get("liberacion_emergencia") is True]
    if emerg:
        adv.append(f"Liberación de emergencia en {_codes(emerg)}: requiere diseño y aprobación técnica.")
    for p in pts:
        d = _data(p)
        if d.get("antipassback") is not True:
            continue
        faltan = []
        if not door_readers(d)[1]:
            faltan.append("lector de salida")
        if _empty(d.get("controlador_asignado")):
            faltan.append("controlador asignado")
        if _empty(d.get("protocolo")):
            faltan.append("protocolo del lector")
        if _empty(sis.get("admin_central")) or sis.get("admin_central") == "Por definir":
            faltan.append("arquitectura de administración del sistema")
        if faltan:
            dep.append(
                {
                    "point_code": _code(p),
                    "text": f"{_code(p)}: anti-passback activo, dependencia técnica pendiente (falta {', '.join(faltan)}).",
                    "missing": faltan,
                }
            )
    return adv, dep


def _rules_acceso(pts: list, add, ctx: _Ctx) -> None:
    """Por puerta: fuente con respaldo, lectores segun la identificacion, la salida y el cable 4x22 de la cerradura.
    Puertas con metodo de ingreso/salida (v2) se cuentan con door_readers; las demas exactamente como antes."""
    n = len(pts)
    codes = [_code(p) for p in pts]
    add("Fuente 12 VDC con respaldo", n, "Unid", f"1 por puerta: {_codes(codes)}", "equipo")
    add("Batería de respaldo 12 V 7 Ah", n, "Unid", f"1 por fuente ({n} puerta(s))", "equipo")
    lectores: dict[str, list[str]] = {}
    salidas: dict[str, list[str]] = {}
    lectores_v2: dict[str, dict[str, list]] = {}  # nombre -> {"entrada": puntos, "salida": puntos}
    botones_v2: list = []
    metros = Decimal(0)
    for p in pts:
        data, code = _data(p), _code(p)
        if _acceso_nuevo(data):
            ing, sal = door_methods(data)
            ent, sale = door_readers(data)
            if ent:
                nombre = _reader_name(ing or ["Tarjeta"], data.get("lectura")) or LECTORES["Tarjeta"]
                lectores_v2.setdefault(nombre, {"entrada": [], "salida": []})["entrada"].append(p)
            if sale:
                lectores_v2.setdefault(_reader_name(sal), {"entrada": [], "salida": []})["salida"].append(p)
            if "Botón" in sal:
                botones_v2.append(p)
            elif not data.get("metodo_salida") and SALIDAS.get(data.get("salida") or ""):
                salidas.setdefault(SALIDAS[data["salida"]], []).append(code)
        else:
            for x in data.get("lectura") or []:
                if LECTORES.get(x):
                    lectores.setdefault(LECTORES[x], []).append(code)
            if SALIDAS.get(data.get("salida") or ""):
                salidas.setdefault(SALIDAS[data["salida"]], []).append(code)
        metros += _num(data, "distancia_m")
    for name, cs in lectores.items():
        add(name, len(cs), "Unid", f"identificación en {_codes(cs)}", "equipo")
    for name, lados in lectores_v2.items():
        ent, sal = lados["entrada"], lados["salida"]
        partes = [f"entrada en {_codes([_code(p) for p in ent])}"] if ent else []
        if sal:
            partes.append(f"salida con credencial en {_codes([_code(p) for p in sal])}")
        add(name, len(ent) + len(sal), "Unid", "lector de " + "; lector de ".join(partes), "equipo", **_gaps(ent + sal, "metodo_salida"))
    for name, cs in salidas.items():
        add(name, len(cs), "Unid", f"salida de {_codes(cs)}", None)
    if botones_v2:
        add("Botón de salida", len(botones_v2), "Unid", f"salida con botón: {_codes([_code(p) for p in botones_v2])}", None)
    # cerraduras y soportes segun hojas controladas: con 2 hojas no se asume 1 cerradura por puerta
    dos = [p for p in pts if str(_data(p).get("hojas_controladas") or "") == "2" or "hojas_controladas" in getattr(p, "missing", {})]
    if dos:
        add(
            "Cerradura y soporte para la segunda hoja",
            0,
            "Unid",
            f"puertas de 2 hojas en {_codes([_code(p) for p in dos])}: revisar cerraduras y soportes (no se asume 1 por puerta)",
            "equipo",
            **_gaps(dos, "hojas_controladas"),
        )
    soportes: dict[str, list[str]] = {}
    for p in pts:
        s = _data(p).get("soporte_montaje")
        if s in SOPORTES and p not in dos:
            soportes.setdefault(s, []).append(_code(p))
    for s, cs in soportes.items():
        add(f"Soporte {s} para cerradura", len(cs), "Unid", f"1 por puerta de una hoja: {_codes(cs)}", "material")
    adv, dep = _door_alerts(pts, (ctx.common.get("acceso_sistema") or {}))
    extra = {"alternatives": controller_options(n)}
    if adv or dep:
        extra["warnings"] = adv + [d["text"] for d in dep]
    if n <= 2:
        add("Controladora de acceso 2 puertas", 1, "Unid", f"{n} puerta(s)", "equipo", **extra)
    else:
        add("Controladora de acceso 4 puertas", _ceil(Decimal(n) / 4), "Unid", f"{n} puertas, 4 por controladora", "equipo", **extra)
    gaps = _gaps(pts, "distancia_m")
    if metros > 0:
        add(
            f"Cable 4x22 (metros con {ctx.pct_txt} % de holgura)",
            (metros * ctx.factor).quantize(Decimal(1)),
            "m",
            f"{metros:g} m a las puertas + {ctx.pct_txt} %",
            "material",
            **gaps,
        )
    elif gaps:
        add(f"Cable 4x22 (metros con {ctx.pct_txt} % de holgura)", 0, "m", "distancia a las puertas sin dato", "material", **gaps)


def _rules_redes(pts: list, add, ctx: _Ctx) -> None:
    sin_poe = [_code(p) for p in pts if _data(p).get("equipo") == "Access point" and _data(p).get("poe") is False]
    if sin_poe:
        add("Inyector PoE", len(sin_poe), "Unid", f"access point sin PoE disponible: {_codes(sin_poe)}", "equipo")
    add("Patch cords", len(pts) * 2, "Unid", f"2 por equipo ({len(pts)} equipos)", "material")


def _rules_asistencia(pts: list, add, ctx: _Ctx) -> None:
    add("Soporte de pared para terminal", len(pts), "Unid", f"1 por terminal ({len(pts)})", "material")
    cableada = [_code(p) for p in pts if _data(p).get("red") == "Cableada"]
    if cableada:
        add("Patch cords", len(cableada), "Unid", f"terminales en red cableada: {_codes(cableada)}", "material")
    sin_red = [_code(p) for p in pts if _data(p).get("red") == "No hay"]
    if sin_red:
        add("Punto de red nuevo (cableado)", len(sin_red), "Unid", f"no hay red en {_codes(sin_red)}", "material")


def _rules_anpr(pts: list, add, ctx: _Ctx) -> None:
    codes = [_code(p) for p in pts]
    add("Cámara ANPR", len(pts), "Unid", f"1 por carril: {_codes(codes)}", "equipo")
    nuevas = [_code(p) for p in pts if _data(p).get("barrera") == "Nueva"]
    if nuevas:
        add("Barrera vehicular", len(nuevas), "Unid", f"barrera nueva en {_codes(nuevas)}", "equipo")
        add("Lazo o sensor", len(nuevas), "Unid", f"1 por barrera ({_codes(nuevas)})", "material")


def _rules_ups(pts: list, add, ctx: _Ctx) -> None:
    tomas = [(_code(p), _num(_data(p), "tomas")) for p in pts if _num(_data(p), "tomas") > 6]
    gaps = _gaps(pts, "tomas")
    if tomas:
        add("Regleta de tomas", len(tomas), "Unid", "más de 6 tomas en " + _codes([c for c, _ in tomas]), "material", **gaps)
    elif "_falta" in gaps:
        add("Regleta de tomas", 0, "Unid", "tomas necesarias sin dato: no se sabe si pasan de 6", "material", **gaps)


KIND_RULES = {"cctv": _rules_cctv, "acceso": _rules_acceso, "redes": _rules_redes, "asistencia": _rules_asistencia, "anpr": _rules_anpr, "ups": _rules_ups}


def _merge_extra(o: dict, extra: dict) -> None:
    for k, v in extra.items():
        if not v:
            continue
        if k in ("_falta", "_estimado"):
            dst = o.setdefault(k, {})
            for lab, cs in v.items():
                dst.setdefault(lab, []).extend(c for c in cs if c not in dst.get(lab, []))
        elif k == "warnings":
            o.setdefault(k, []).extend(x for x in v if x not in o.get(k, []))
        else:
            o[k] = v


def _gap_text(g: dict) -> list[str]:
    return [f"{lab} en {_codes(cs)}" for lab, cs in g.items()]


def suggest_materials(kind: str, points: list, items: list | None = None, common: dict | None = None) -> list[dict]:
    """Sugerencia a partir de los puntos levantados, sus observaciones y lo que ya esta cargado.

    Cada punto aporta segun SU tipo: en un levantamiento de CCTV una "Puerta" agregada suma las reglas de control de
    acceso y un "Punto de red" las de cableado. Sin puntos no queda vacio: devuelve los materiales habituales del tipo
    de solucion para revisar cantidad (Andres: "no siempre va a ser camaras").

    No reemplaza nada: devuelve propuestas con el motivo; el usuario elige cuales agregar.
    action: nuevo (no existe), sumar (ya existe y falta cantidad), cubierto (ya alcanza), revisar
    (material habitual sin cantidad calculable, dato faltante o referencia del curso por verificar).

    Campos opcionales por linea (v2, solo si aplican): missing (datos que faltan), estimated (datos estimados),
    warnings (advertencias tecnicas), alternatives (combinaciones de controladoras), reference (modelo del curso).
    Un levantamiento sin estados por campo ni seccion comun da exactamente las mismas lineas que antes."""
    kind = kind if kind in SPECS else "otro"
    ctx = _Ctx(common)
    out: list[dict] = []

    def add(name: str, qty, unit: str, reason: str, kind_: str | None = None, **extra) -> None:
        qty = Decimal(str(qty))
        for o in out:
            if o["name"] == name:
                o["quantity"] += qty
                o["reason"] += f"; {reason}"
                _merge_extra(o, extra)
                return
        o = {"name": name, "quantity": qty, "unit": unit, "kind": kind_ or classify_kind(name), "reason": reason}
        _merge_extra(o, extra)
        out.append(o)

    points = [_Pt(p, kind) for p in points]
    # puntos agrupados por su tipo, el del levantamiento primero
    by_kind: dict[str, list] = {}
    for p in points:
        by_kind.setdefault(p.kind, []).append(p)
    kinds = sorted(by_kind, key=lambda k: (k != kind, list(SPECS).index(k)))

    utp_meters = Decimal(0)  # una sola linea de cable para todos los tipos, redondeada una vez
    utp_gaps: dict = {}
    for k in kinds:
        pts = by_kind[k]
        # 1) el equipo principal de cada punto
        _main_equipment(k, pts, add)
        # 2) metros de cable UTP con el desperdicio del levantamiento (15 % por defecto; acceso lleva cable 4x22)
        rules = SPECS[k].get("suggest", {})
        for name, qty in rules.get("per_point", []):
            add(name, len(pts) * qty, "Unid", f"{qty} por cada uno de los {len(pts)} puntos")
        meters = Decimal(0)
        gaps: dict = {}
        if k != "acceso":
            for p in pts:
                for key in METER_KEYS:
                    meters += _num(_data(p), key)
            gaps = _gaps(pts, *METER_KEYS)
            _merge_extra(utp_gaps, gaps)
        if meters > 0:
            utp_meters += meters
            holgura = (meters * ctx.factor).quantize(Decimal(1))
            for name, per in rules.get("per_meters", []):
                add(name, max(1, _ceil(holgura / per)), "Unid", f"{holgura} m de cable / {per} m por unidad", **gaps)
        elif gaps:
            for name, per in rules.get("per_meters", []):
                add(name, 0, "Unid", f"metros sin dato / {per} m por unidad", **gaps)
        # 3) reglas por campo segun el tipo del punto
        if k in KIND_RULES:
            KIND_RULES[k](pts, add, ctx)

    cable = f"Cable (metros con {ctx.pct_txt} % de holgura)"
    if utp_meters > 0:
        holgura = (utp_meters * ctx.factor).quantize(Decimal(1))
        add(cable, holgura, "m", f"{utp_meters:g} m medidos entre los puntos + {ctx.pct_txt} %", "material", **utp_gaps)
    elif utp_gaps:
        add(cable, 0, "m", "metros entre los puntos sin dato", "material", **utp_gaps)

    # 3b) sistema de acceso (seccion comun): credenciales y enrolador. Solo si se llenaron esos datos.
    sis = ctx.common.get("acceso_sistema") or {}
    if "acceso" in by_kind or kind == "acceso":
        cred = credentials(sis)
        if cred:
            falta = {"_falta": {lab: ["sistema de acceso"] for lab in cred["faltan"]}} if cred["faltan"] else {}
            if cred["paquetes"]:
                add(f"Credenciales (paquete de {cred['paquete']:g})", cred["paquetes"], "Unid", cred["formula"], "material", **falta)
            else:
                add("Credenciales de acceso", cred["total"], "Unid", cred["formula"], "material", **falta)
        if sis.get("emisor") is True:
            add("Enrolador de tarjetas USB (DS-K1F100A-D8E)", 1, "Unid", "enrolar credenciales desde la PC de administración", "equipo")

    # 4) observaciones de cada punto
    for words, name, kind_, unit in NOTE_RULES:
        hits = [_code(p) for p in points if any(w in _notes(p) for w in words)]
        if hits and not any(o["name"] == name for o in out):
            qty = 1 if "andamio" in name.lower() else len(hits)
            add(name, qty, unit, f"observaciones de {_codes(hits)}", kind_)

    # 5) materiales habituales de cada tipo presente (y del tipo del levantamiento aunque no haya puntos):
    #    sin cantidad, para revisar
    for k in [kind] + [x for x in kinds if x != kind]:
        for extra in SPECS[k].get("materials", []):
            ya = any(extra.lower() in o["name"].lower() or o["name"].lower() in extra.lower() for o in out)
            if not ya and _familia(extra) and any(_familia(o["name"]) == _familia(extra) for o in out):
                ya = True  # "Cable UTP Cat6" habitual sobra si ya se calcularon los metros de cable
            if not ya:
                reason = "Habitual en este tipo de solución: confirmá la cantidad"
                if k != kind:
                    reason = f"Habitual en {SPECS[k]['label'].lower()}: confirmá la cantidad"
                elif not points:
                    reason = "Todavía no hay puntos levantados: habitual en este tipo de solución, confirmá la cantidad"
                out.append({"name": extra, "quantity": Decimal(0), "unit": "Unid", "kind": classify_kind(extra), "reason": reason})

    # 6) cruce con lo ya cargado: no duplicar, proponer sumar
    existing = list(items or [])
    for o in out:
        falta, est = o.pop("_falta", None), o.pop("_estimado", None)
        if est:
            o["estimated"] = _gap_text(est)
            o["reason"] += "; dato estimado: " + ", ".join(o["estimated"])
        if falta:
            o["missing"] = _gap_text(falta)
            o["reason"] += "; falta " + "; falta ".join(o["missing"])
        if any(ref.lower() in o["name"].lower() for ref in COURSE_REFS):
            o["reference"] = True  # modelo del curso: nunca entra como catalogo confirmado
            o["reason"] += "; referencia del curso, por verificar"
        match = next((it for it in existing if _mismo_material(o, it)), None)
        o["item_id"] = getattr(match, "id", None) if match else None
        o["existing_quantity"] = quantity_in_line(match, o["name"]) if match else Decimal(0)
        if o["quantity"] <= 0 or falta or o.get("reference"):
            # sin cantidad calculada (o con datos faltantes) no se puede afirmar nada: solo que hay que revisarla
            o["action"], o["add_quantity"] = "revisar", Decimal(0)
        elif match:
            if match and (getattr(match, "name", "") or "").strip().lower() != o["name"].lower():
                o["matched_name"] = match.name  # se suma a la linea que ya existe, con su nombre
            falta_q = o["quantity"] - o["existing_quantity"]
            o["action"], o["add_quantity"] = ("sumar", falta_q) if falta_q > 0 else ("cubierto", Decimal(0))
        else:
            o["action"], o["add_quantity"] = "nuevo", o["quantity"]
    return out


# ---------- calculos del sistema (tarjeta "Calculos del sistema") ----------
def _es(x: Decimal) -> str:
    """Numero para leer en Costa Rica: coma decimal (10,368 TB)."""
    return f"{x:g}".replace(".", ",")


def _calc_acceso(pts: list, sis: dict) -> dict:
    N = len(pts)
    per = [(p, *door_readers(_data(p))) for p in pts]
    con_salida = [p for p, _, s in per if s]
    n = N - len(con_salida)  # puertas cuya salida no usa credencial
    total = sum(e + s for _, e, s in per)
    sin_entrada = [_code(p) for p, e, _ in per if not e]
    botones = [_code(p) for p in pts if "Botón" in door_methods(_data(p))[1]]
    dos = [_code(p) for p in pts if str(_data(p).get("hojas_controladas") or "") == "2"]
    legacy = [_code(p) for p in pts if not _acceso_nuevo(_data(p)) and _data(p).get("salida") == "Lector interior"]
    adv, dep = _door_alerts(pts, sis)
    if dos:
        adv.append(f"2 hojas controladas en {_codes(dos)}: revisar cerraduras y soportes (no se asume 1 por puerta).")
    if legacy:
        adv.append(
            f"{_codes(legacy)} {'usa' if len(legacy) == 1 else 'usan'} «Lector interior» (dato anterior): aquí cuentan 2 lectores; la sugerencia de materiales "
            "mantiene el cálculo anterior. Pasalo a «Método de salida» para que coincidan."
        )
    formula = f"n + 2(N − n) = {n} + 2 × {N - n} = {n + 2 * (N - n)}"
    if sin_entrada:
        formula += f"; menos {len(sin_entrada)} puerta(s) sin lector de entrada ({_codes(sin_entrada)}) = {total}"
    opts = controller_options(N)
    return {
        "puertas": N,
        "salida_sin_credencial": n,
        "salida_con_credencial": N - n,
        "lectores": total,
        "formula_lectores": formula,
        "botones": len(botones),
        "controladoras": {"actual": next((o for o in opts if o["actual"]), None), "alternativas": opts},
        "credenciales": credentials(sis),
        "advertencias": adv,
        "dependencias": dep,
    }


def _calc_cctv(pts: list, sis: dict, ctx: _Ctx) -> dict:
    n = len(pts)
    adv: list[str] = []
    dias = _num(sis, "dias_retencion")
    diseno = _num(sis, "bitrate_mbps")
    bit_total, sin_bitrate = Decimal(0), []
    for p in pts:
        b = _num(_data(p), "bitrate_mbps")
        if b <= 0 and (p.status.get("bitrate_mbps") != "pendiente"):
            b = diseno
        if b > 0:
            bit_total += b
        else:
            sin_bitrate.append(_code(p))
    faltan = []
    if dias <= 0:
        faltan.append("días de retención")
    if sin_bitrate:
        faltan.append(f"bitrate en {_codes(sin_bitrate)} (o bitrate de diseño)")
    # TB decimales = suma Mb/s x 86 400 s x dias / 8 000 000  (Mb -> MB /8, MB -> TB /1 000 000)
    tb = (bit_total * 86400 * dias / Decimal(8_000_000)).quantize(Decimal("0.001")) if dias > 0 else None
    canales = int(_num(sis, "canales")) or None
    if canales and n > canales:
        adv.append(f"{n} cámaras y el grabador tiene {canales} canales.")
    entrada = _num(sis, "entrada_nvr_mbps")
    if entrada > 0 and bit_total > entrada:
        adv.append(f"El grabador recibe {_es(entrada)} Mb/s y las cámaras suman {_es(bit_total)} Mb/s.")
    if sis.get("modo") == "Por evento":
        adv.append("Grabación por evento: el almacenamiento se calcula como si fuera continua (es el máximo).")
    # PoE: suma de W de las camaras PoE (o sin alimentacion anotada) + reserva %, contra el presupuesto del switch
    poe_pts = [p for p in pts if _data(p).get("alimentacion") not in ("12 VDC", "Solar")]
    consumo = sum((_num(_data(p), "consumo_w") for p in poe_pts), Decimal(0))
    sin_consumo = [_code(p) for p in poe_pts if _num(_data(p), "consumo_w") <= 0]
    reserva = _num(sis, "reserva_poe_pct") if not _empty(sis.get("reserva_poe_pct")) else Decimal(20)
    requerido = (consumo * (1 + reserva / 100)).quantize(Decimal("0.1"))
    switch = _num(sis, "switch_poe_w")
    poe_ok = None if switch <= 0 or sin_consumo else requerido <= switch
    if poe_ok is False:
        adv.append(f"PoE: se requieren {_es(requerido)} W con la reserva y el switch da {_es(switch)} W.")
    return {
        "camaras": n,
        "canales": canales,
        "canales_sugeridos": 4 if n <= 4 else (8 if n <= 8 else (16 if n <= 16 else 32)),
        "modo": sis.get("modo"),
        "codec": sis.get("codec"),
        "redundancia": sis.get("redundancia"),
        "almacenamiento": {
            "tb": tb,
            "bitrate_total_mbps": bit_total,
            "dias": dias,
            "formula": "Σ Mb/s × 86 400 s × días ÷ 8 000 000 = TB (decimales)",
            "detalle": f"{_es(bit_total)} Mb/s × 86 400 × {_es(dias)} días ÷ 8 000 000 = {_es(tb)} TB" if tb is not None else None,
            "completo": not faltan,
            "faltan": faltan,
            "leyenda": "Estimación: validar con la herramienta del fabricante.",
        },
        "entrada_nvr": {"requerido_mbps": bit_total, "disponible_mbps": entrada or None, "ok": None if entrada <= 0 else bit_total <= entrada},
        "poe": {
            "camaras": len(poe_pts),
            "consumo_w": consumo,
            "reserva_pct": reserva,
            "requerido_w": requerido,
            "switch_w": switch or None,
            "ok": poe_ok,
            "sin_consumo": sin_consumo,
            "formula": "Σ W por cámara × (1 + reserva %)",
        },
        "advertencias": adv,
    }


def system_calcs(kind: str, points: list, common: dict | None = None) -> dict:
    """Calculos del sistema para la tarjeta del levantamiento. No modifica nada ni cambia la sugerencia."""
    kind = kind if kind in SPECS else "otro"
    ctx = _Ctx(common)
    pts = [_Pt(p, kind) for p in points]
    out: dict = {
        "template_version": TEMPLATE_VERSION,
        "parametros": {"desperdicio_cable_pct": ctx.pct, "desperdicio_default": DESPERDICIO_DEFAULT},
        "acceso": None,
        "cctv": None,
        "advertencias": [],
        "dependencias": [],
    }
    doors = [p for p in pts if p.kind == "acceso"]
    if doors or kind == "acceso":
        out["acceso"] = _calc_acceso(doors, ctx.common.get("acceso_sistema") or {})
        out["advertencias"] += out["acceso"]["advertencias"]
        out["dependencias"] += out["acceso"]["dependencias"]
    cams = [p for p in pts if p.kind == "cctv"]
    if cams or kind == "cctv":
        out["cctv"] = _calc_cctv(cams, ctx.common.get("cctv_sistema") or {}, ctx)
        out["advertencias"] += out["cctv"]["advertencias"]
    return out
