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


def suggest_materials(kind: str, points: list) -> list[dict]:
    """Sugerencia simple a partir de los puntos: cantidad de puntos y metros sumados.
    No pretende ser exacta; es el punto de partida que administracion ajusta."""
    spec = SPECS.get(kind, SPECS["otro"])
    n = len(points)
    if not n:
        return []
    out: list[dict] = [{"name": f"{spec['point_label']} ({spec['point_prefix']})", "quantity": n, "unit": "Unid"}]
    meters = Decimal(0)
    for p in points:
        data = p.data if hasattr(p, "data") else (p.get("data") or {})
        for key in ("metros", "distancia_m", "distancia_camara_m"):
            try:
                meters += Decimal(str(data.get(key) or 0))
            except Exception:  # noqa: BLE001 - dato escrito a mano en el celular
                continue
    rules = spec.get("suggest", {})
    for name, qty in rules.get("per_point", []):
        out.append({"name": name, "quantity": n * qty, "unit": "Unid"})
    if meters > 0:
        holgura = (meters * Decimal("1.15")).quantize(Decimal(1))  # 15 % de holgura por rutas reales
        out.append({"name": "Cable (metros con 15 % de holgura)", "quantity": holgura, "unit": "m"})
        for name, per in rules.get("per_meters", []):
            out.append({"name": name, "quantity": max(1, int(holgura / per) + (1 if holgura % per else 0)), "unit": "Unid"})
    for extra in spec.get("materials", []):
        if not any(o["name"] == extra for o in out):
            out.append({"name": extra, "quantity": 0, "unit": "Unid"})
    return out
