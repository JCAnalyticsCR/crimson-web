"""Sugerencia de CABYS para productos sin codigo (pantalla "Completar CABYS").

El buscador de Hacienda es impreciso: busca por cualquiera de las palabras y ordena mal ("camara de vigilancia"
devuelve primero "Vigilancia de la funcion respiratoria" y "Cama de madera"). Por eso:
  1. se limpia el nombre (marca, modelo, medidas) y se traduce con un diccionario del rubro (CCTV, redes, acceso,
     alarmas, UPS) a la REDACCION OFICIAL del catalogo ("camaras de television", "fuente de alimentacion
     ininterrumpida", "unidades de almacenamiento de medios fijos");
  2. cada resultado se puntua: que tenga las palabras del termino, que se parezca al producto, y castigo a
     servicios, partes, uso medico e intervenciones de salud cuando lo que se vende es un equipo.

Es solo una SUGERENCIA: nunca se guarda sin que una persona la confirme (routers/cabys_masivo.py).
"""

from __future__ import annotations

import re
import unicodedata

from . import cabys as cabys_svc

# (palabras que aparecen en el nombre o la categoria) -> terminos a probar en Hacienda, en orden.
# Lo mas especifico va primero: "disco duro" antes que "duro", "patch cord" antes que "cable".
DICCIONARIO: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("disco duro", "hdd", "purple", "skyhawk"), ("unidades de almacenamiento de medios fijos", "unidades de almacenamiento")),
    (("tarjeta de memoria", "microsd", "micro sd", "memoria sd", "memoria usb", "ssd"), ("dispositivos de almacenamiento permanente de estado solido",)),
    (("patch cord", "patchcord", "cable de parcheo"), ("conductores electricos con pieza de conexion", "conductores electricos")),
    (
        ("cable utp", "utp", "cat6", "cat 6", "cat5", "cat 5e", "cat5e", "ftp", "cable de red"),
        ("conductores electricos sin pieza de conexion", "conductores electricos"),
    ),
    (("coaxial", "rg59", "rg6", "siames"), ("cable coaxial",)),
    (("fibra optica", "fibra"), ("cables de fibras opticas", "fibras opticas")),
    (("ups", "no break", "nobreak", "respaldo de energia"), ("fuente de alimentacion ininterrumpida",)),
    (
        ("fuente de poder", "fuente de alimentacion", "fuente", "power supply", "adaptador de corriente", "eliminador"),
        ("convertidores estaticos", "fuente de alimentacion"),
    ),
    (("regulador de voltaje", "regulador"), ("reguladores de voltaje", "convertidores estaticos")),
    (("bateria",), ("acumuladores electricos", "acumuladores")),
    (("nvr", "dvr", "xvr", "grabador", "grabadora", "videograbador"), ("aparatos para la grabacion o reproduccion de videos", "grabacion de videos")),
    (("camara", "bullet", "domo", "dome", "ptz", "turret", "eyeball"), ("camaras de television", "camaras videograbadoras", "camaras digitales")),
    (("videoportero", "intercomunicador", "portero", "citofono"), ("intercomunicadores", "porteros electricos")),
    (("panel de alarma", "alarma", "central de alarma", "sirena", "estrobo"), ("alarmas antirrobo o alarmas contra incendio", "alarmas antirrobo")),
    (
        ("contacto magnetico", "sensor magnetico", "magnetico", "detector de humo", "humo", "sensor", "detector", "pir", "movimiento"),
        ("alarmas antirrobo o alarmas contra incendio y aparatos similares", "detectores"),
    ),
    (
        (
            "switch",
            "conmutador",
            "router",
            "enrutador",
            "firewall",
            "access point",
            "punto de acceso",
            "unifi",
            "wifi",
            "transceiver",
            "sfp",
            "media converter",
            "convertidor de medios",
        ),
        (
            "aparatos para la recepcion conversion emision y transmision de voz imagen u otros datos",
            "aparatos de transmision o recepcion de voz imagenes u otros datos",
        ),
    ),
    (("antena", "radioenlace", "enlace inalambrico"), ("antenas y reflectores de antena", "antenas")),
    (("balun",), ("transformadores electricos", "conectores")),
    (("conector", "rj45", "rj-45", "bnc", "plug", "jack", "keystone"), ("aparatos para empalme o conexion de circuitos electricos", "conectores")),
    (("gabinete", "rack", "bandeja"), ("armarios", "gabinetes")),
    (("lectora", "lector", "biometrico", "huella", "reconocimiento facial", "control de acceso", "proximidad"), ("control de acceso", "lectores de tarjetas")),
    (("tarjeta rfid", "tarjeta de proximidad", "llavero"), ("tarjetas inteligentes", "tarjetas de proximidad")),
    (("cerradura", "electroiman", "electro iman", "chapa", "cerrojo", "contrachapa"), ("electroimanes", "cerraduras")),
    (("boton de salida", "pulsador", "boton"), ("interruptores", "pulsadores")),
    (("teclado",), ("teclados",)),
    (("monitor", "pantalla", "televisor"), ("monitores en colores", "monitores")),
    (("tubo", "conduit", "emt", "tuberia"), ("tubos rigidos de plastico", "tubos de plastico")),
    (("canaleta",), ("canaletas", "accesorios para tubos de plastico")),
    (("caja de registro", "caja"), ("cajas de empalme", "cajas")),
    (("soporte", "brazo", "base de pared", "montaje"), ("soportes", "accesorios de montaje")),
    (("mantenimiento",), ("servicios de mantenimiento",)),
    (("instalacion", "mano de obra", "configuracion", "cableado estructurado"), ("servicios de instalacion",)),
]

# palabras que no ayudan a buscar
_RUIDO = {
    "de", "del", "la", "el", "los", "las", "para", "por", "y", "o", "u", "en", "un", "una", "tipo", "modelo",
    "marca", "color", "blanco", "negro", "gris", "azul", "unidad", "unid", "pcs", "kit", "pro", "plus", "mini", "nuevo",
    "ip", "hd", "full", "ultra", "lite", "serie", "series", "metros", "metro", "pies", "pulgadas", "canales", "puertos",
    "exterior", "interior", "ncp",
}  # fmt: skip
# lo que delata que el resultado no es un equipo que se vende
_NO_EQUIPO = {"servicio", "mantenimiento", "alquiler", "arrendamiento", "reparacion", "instalacion", "fabricacion"}
_PARTES = {"parte", "repuesto"}  # "pieza" no: "con pieza de conexion" es un cable, no un repuesto
_MEDICO = {"medico", "odontologico", "veterinario", "pecuario", "acuicola"}
_DESECHO = {"desperdicio", "desecho", "chatarra", "residuo"}
_CORTAS = {"con", "sin"}  # cuentan para comparar ("con/sin pieza de conexion") pero no para buscar


def norm(texto: str | None) -> str:
    """minusculas, sin tildes, solo letras/numeros/espacios"""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def raiz(w: str) -> str:
    """plural -> singular aproximado: camaras/camara, conductores/conductor"""
    if len(w) > 5 and w.endswith("es"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


def raices(texto: str | None) -> set[str]:
    return {raiz(w) for w in norm(texto).split() if w not in _RUIDO and len(w) > 1}


def palabras_utiles(nombre: str, marca: str | None = None, modelo: str | None = None) -> list[str]:
    """Quita marca, modelo y todo token con numeros (DS-2CD1047G3, 4MP, 2.8mm, 8CH): queda lo generico."""
    quitar = set(norm(marca).split()) | set(norm(modelo).split())
    out = []
    for w in norm(nombre).split():
        if w in quitar or w in _RUIDO or w in _CORTAS or len(w) < 3 or any(ch.isdigit() for ch in w):
            continue
        out.append(w)
    return out


def terminos(nombre: str, categoria: str | None = None, marca: str | None = None, modelo: str | None = None, tipo: str | None = None) -> list[str]:
    """Terminos de busqueda en orden de preferencia (sin repetidos)."""
    texto = f" {norm(nombre)} {norm(categoria)} "
    out: list[str] = []
    for claves, buscar in DICCIONARIO:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", texto) for k in claves):
            out.extend(buscar)
            break  # el primer grupo que calza es el mas especifico
    utiles = palabras_utiles(nombre, marca, modelo)
    if len(utiles) >= 2:
        out.append(" ".join(utiles[:2]))
    if utiles:
        out.append(utiles[0])
    cat = " ".join(w for w in norm(categoria).split() if w not in _RUIDO)
    if len(cat) >= 3:
        out.append(cat)
    if tipo == "servicio" and not out:
        out.append("servicios de instalacion")
    vistos: set[str] = set()
    return [t for t in out if len(t) >= 3 and not (t in vistos or vistos.add(t))]


def puntaje(row: dict, producto: set[str], termino: str, tipo: str | None = None) -> float:
    desc = raices(row.get("description"))
    t = raices(termino)
    cobertura = len(desc & t) / max(len(t), 1)
    s = 3 * cobertura + 0.5 * len(desc & producto)
    if tipo != "servicio" and desc & _NO_EQUIPO and not producto & _NO_EQUIPO:
        s -= 2.5
    if desc & _PARTES and not producto & _PARTES:
        s -= 1
    if desc & _MEDICO and not producto & _MEDICO:
        s -= 2
    if desc & _DESECHO:
        s -= 3
    if str(row.get("code", "")).startswith("93"):  # intervenciones de salud
        s -= 3
    return s - 0.01 * len(desc)  # a igualdad, la descripcion mas corta (mas generica)


def sugerir(nombre: str, categoria: str | None = None, marca: str | None = None, modelo: str | None = None, tipo: str | None = None, top: int = 20) -> dict:
    """Mejor candidato + alternativas. Lanza CabysUnavailable si Hacienda no responde (el llamador decide)."""
    producto = raices(" ".join(palabras_utiles(nombre, marca, modelo))) | raices(categoria)
    probados: list[str] = []
    mejor: tuple[float, dict, list[dict], str] | None = None
    for termino in terminos(nombre, categoria, marca, modelo, tipo)[:4]:
        probados.append(termino)
        rows = cabys_svc.search(q=termino, top=top)
        if not rows:
            continue
        puntos = sorted(((puntaje(r, producto, termino, tipo), i, r) for i, r in enumerate(rows)), key=lambda x: (-x[0], x[1]))
        cand = (puntos[0][0], puntos[0][2], [p[2] for p in puntos[1:6]], termino)
        if mejor is None or cand[0] > mejor[0]:
            mejor = cand
        if cand[0] >= 2.5:  # tiene casi todas las palabras del termino y no es servicio/parte: no gastar mas consultas
            break
    if mejor is None or mejor[0] < 1:
        return {"suggestion": None, "alternatives": mejor[2] if mejor else [], "term": None, "tried": probados}
    return {"suggestion": mejor[1], "alternatives": mejor[2], "term": mejor[3], "tried": probados}
