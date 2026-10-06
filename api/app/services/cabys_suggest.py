"""Sugerencia de CABYS para productos sin codigo (pantalla "Completar CABYS").

El buscador de Hacienda busca por texto en la descripcion oficial: "Camara IP Hikvision DS-2CD1047G3 4MP 2.8mm"
no encuentra nada, "camara de vigilancia" si. Por eso primero se limpia el nombre (marca, modelo, medidas) y se
traduce con un diccionario del rubro (CCTV, redes, acceso, alarmas, UPS) a terminos genericos.

Es solo una SUGERENCIA: nunca se guarda sin que una persona la confirme (routers/cabys_masivo.py).
"""

from __future__ import annotations

import re
import unicodedata

from . import cabys as cabys_svc

# (palabras que aparecen en el nombre o la categoria) -> terminos a probar en Hacienda, en orden.
# Lo mas especifico va primero: "disco duro" antes que cualquier "duro", "cable utp" antes que "cable".
DICCIONARIO: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("disco duro", "hdd", "purple", "skyhawk", "surveillance hdd"), ("disco duro", "unidades de disco")),
    (("tarjeta de memoria", "microsd", "micro sd", "memoria sd"), ("tarjeta de memoria", "memoria")),
    (("cable utp", "utp", "cat6", "cat 6", "cat5", "cat 5e", "cat5e", "ftp"), ("cable utp", "cable de red", "cable para transmision de datos")),
    (("coaxial", "rg59", "rg6", "siamese"), ("cable coaxial",)),
    (("fibra optica", "fibra"), ("cable de fibra optica", "fibra optica")),
    (("patch cord", "patchcord"), ("patch cord", "cable de conexion")),
    (("patch panel",), ("panel de conexion", "patch panel")),
    (
        ("fuente de poder", "fuente de alimentacion", "fuente", "power supply", "adaptador de corriente", "eliminador"),
        ("fuente de poder", "fuente de alimentacion"),
    ),
    (("ups", "no break", "nobreak", "respaldo de energia", "regulador"), ("ups", "sistema de alimentacion ininterrumpida", "regulador de voltaje")),
    (("bateria",), ("bateria", "acumuladores")),
    (("nvr", "dvr", "xvr", "grabador", "grabadora", "videograbador"), ("grabador de video", "grabadora de video", "videograbadora")),
    (("camara", "bullet", "domo", "dome", "ptz", "turret", "eyeball"), ("camara de vigilancia", "camara de video", "camara")),
    (("videoportero", "intercomunicador", "portero", "citofono"), ("intercomunicador", "videoportero", "portero electrico")),
    (("switch", "conmutador"), ("switch", "conmutador de red")),
    (("router", "enrutador", "firewall", "gateway"), ("router", "enrutador")),
    (("access point", "punto de acceso", "unifi", "wifi", "inalambrico"), ("punto de acceso inalambrico", "equipo inalambrico")),
    (("antena", "radioenlace", "enlace inalambrico"), ("antena", "equipo de radiofrecuencia")),
    (("transceiver", "sfp", "media converter", "convertidor de medios"), ("convertidor de medios", "transceptor")),
    (("balun", "video balun"), ("balun", "transformador de video")),
    (("conector", "rj45", "rj-45", "bnc", "plug", "jack", "keystone"), ("conector", "conector electrico")),
    (("gabinete", "rack", "bandeja"), ("gabinete", "rack")),
    (
        ("lectora", "lector", "biometrico", "huella", "reconocimiento facial", "control de acceso", "proximidad"),
        ("control de acceso", "lector de tarjetas", "lector biometrico"),
    ),
    (("tarjeta rfid", "tarjeta de proximidad", "llavero"), ("tarjeta de proximidad", "tarjeta inteligente")),
    (("cerradura", "electroiman", "electro iman", "chapa", "cerrojo", "contrachapa"), ("cerradura electromagnetica", "cerradura electrica", "cerradura")),
    (("boton de salida", "pulsador", "boton"), ("pulsador", "boton de salida")),
    (("panel de alarma", "alarma", "central de alarma"), ("alarma contra robo", "sistema de alarma", "alarma")),
    (("contacto magnetico", "sensor magnetico", "magnetico"), ("contacto magnetico", "sensor magnetico")),
    (("detector de humo", "humo"), ("detector de humo",)),
    (("sensor", "detector", "pir", "movimiento"), ("detector de movimiento", "sensor de movimiento", "detector")),
    (("sirena", "estrobo"), ("sirena",)),
    (("teclado",), ("teclado",)),
    (("monitor", "pantalla", "televisor"), ("monitor", "pantalla")),
    (("microfono",), ("microfono",)),
    (("tubo", "conduit", "emt", "tuberia"), ("tubo conduit", "tubo pvc", "tubo")),
    (("canaleta",), ("canaleta",)),
    (("caja de registro", "caja"), ("caja de registro", "caja de conexion")),
    (("soporte", "brazo", "base de pared", "montaje"), ("soporte", "soporte de montaje")),
    (("mantenimiento",), ("servicios de mantenimiento", "mantenimiento")),
    (("instalacion", "mano de obra", "configuracion", "cableado estructurado"), ("servicios de instalacion", "instalacion")),
]

# palabras que no ayudan a buscar
_RUIDO = {
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "con",
    "sin",
    "para",
    "por",
    "y",
    "o",
    "en",
    "un",
    "una",
    "tipo",
    "modelo",
    "marca",
    "color",
    "blanco",
    "negro",
    "gris",
    "unidad",
    "unid",
    "pieza",
    "pcs",
    "kit",
    "pro",
    "plus",
    "mini",
    "nuevo",
    "ip",
    "hd",
    "full",
    "ultra",
    "lite",
    "serie",
    "series",
    "metros",
    "metro",
    "pies",
    "pulgadas",
    "canales",
    "puertos",
}


def norm(texto: str | None) -> str:
    """minusculas, sin tildes, solo letras/numeros/espacios"""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def palabras_utiles(nombre: str, marca: str | None = None, modelo: str | None = None) -> list[str]:
    """Quita marca, modelo y todo token con numeros (DS-2CD1047G3, 4MP, 2.8mm, 8CH): queda lo generico."""
    quitar = set(norm(marca).split()) | set(norm(modelo).split())
    out = []
    for w in norm(nombre).split():
        if w in quitar or w in _RUIDO or len(w) < 3 or any(ch.isdigit() for ch in w):
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
    if len(utiles) >= 3:
        out.append(" ".join(utiles[:3]))
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


def _puntaje(row: dict, base: set[str], termino: str) -> float:
    desc = set(norm(row.get("description")).split())
    t = set(termino.split())
    return len(desc & base) + 1.5 * len(desc & t) / max(len(t), 1)


def sugerir(nombre: str, categoria: str | None = None, marca: str | None = None, modelo: str | None = None, tipo: str | None = None, top: int = 10) -> dict:
    """Mejor candidato + alternativas. Lanza CabysUnavailable si Hacienda no responde (el llamador decide)."""
    base = set(palabras_utiles(nombre, marca, modelo)) | {w for w in norm(categoria).split() if w not in _RUIDO}
    probados = []
    for termino in terminos(nombre, categoria, marca, modelo, tipo)[:6]:
        probados.append(termino)
        rows = cabys_svc.search(q=termino, top=top)
        if not rows:
            continue
        orden = sorted(range(len(rows)), key=lambda i: (-_puntaje(rows[i], base, termino), i))
        mejores = [rows[i] for i in orden]
        return {"suggestion": mejores[0], "alternatives": mejores[1:6], "term": termino, "tried": probados}
    return {"suggestion": None, "alternatives": [], "term": None, "tried": probados}
