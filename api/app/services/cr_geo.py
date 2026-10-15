"""Division territorial de Costa Rica para la direccion de entrega (provincia -> canton). El distrito queda como texto.

Zona de envio: las provincias de Guanacaste, Puntarenas y Limon no tienen cantones en la GAM; en las cuatro
centrales hay cantones claramente fuera de ella. La lista es conservadora: solo marca "fuera de GAM" lo que
seguro lo es, para que nadie pague tarifa GAM por un envio a San Carlos o a Liberia.
"""

from __future__ import annotations

PROVINCIAS: dict[str, list[str]] = {
    "San José": [
        "San José",
        "Escazú",
        "Desamparados",
        "Puriscal",
        "Tarrazú",
        "Aserrí",
        "Mora",
        "Goicoechea",
        "Santa Ana",
        "Alajuelita",
        "Vázquez de Coronado",
        "Acosta",
        "Tibás",
        "Moravia",
        "Montes de Oca",
        "Turrubares",
        "Dota",
        "Curridabat",
        "Pérez Zeledón",
        "León Cortés Castro",
    ],
    "Alajuela": [
        "Alajuela",
        "San Ramón",
        "Grecia",
        "San Mateo",
        "Atenas",
        "Naranjo",
        "Palmares",
        "Poás",
        "Orotina",
        "San Carlos",
        "Zarcero",
        "Sarchí",
        "Upala",
        "Los Chiles",
        "Guatuso",
        "Río Cuarto",
    ],
    "Cartago": ["Cartago", "Paraíso", "La Unión", "Jiménez", "Turrialba", "Alvarado", "Oreamuno", "El Guarco"],
    "Heredia": ["Heredia", "Barva", "Santo Domingo", "Santa Bárbara", "San Rafael", "San Isidro", "Belén", "Flores", "San Pablo", "Sarapiquí"],
    "Guanacaste": ["Liberia", "Nicoya", "Santa Cruz", "Bagaces", "Carrillo", "Cañas", "Abangares", "Tilarán", "Nandayure", "La Cruz", "Hojancha"],
    "Puntarenas": [
        "Puntarenas",
        "Esparza",
        "Buenos Aires",
        "Montes de Oro",
        "Osa",
        "Quepos",
        "Golfito",
        "Coto Brus",
        "Parrita",
        "Corredores",
        "Garabito",
        "Monteverde",
        "Puerto Jiménez",
    ],
    "Limón": ["Limón", "Pococí", "Siquirres", "Talamanca", "Matina", "Guácimo"],
}

PROVINCIAS_FUERA_GAM = {"Guanacaste", "Puntarenas", "Limón"}
CANTONES_FUERA_GAM = {
    "Pérez Zeledón",
    "Tarrazú",
    "Dota",
    "León Cortés Castro",
    "Turrubares",
    "San Carlos",
    "Upala",
    "Los Chiles",
    "Guatuso",
    "Río Cuarto",
    "Turrialba",
    "Jiménez",
    "Sarapiquí",
}


def valid(provincia: str | None, canton: str | None) -> bool:
    return bool(provincia in PROVINCIAS and canton in PROVINCIAS[provincia])


def fuera_gam(provincia: str | None, canton: str | None) -> bool:
    return provincia in PROVINCIAS_FUERA_GAM or canton in CANTONES_FUERA_GAM


# Codificacion de ubicacion de Hacienda (v4.4): provincia 1-7 y canton por su posicion en la lista oficial (las listas
# de arriba siguen ese orden). El distrito se escribe por codigo (el catalogo completo de distritos es fase 2).
PROVINCE_CODES = {"San José": "1", "Alajuela": "2", "Cartago": "3", "Heredia": "4", "Guanacaste": "5", "Puntarenas": "6", "Limón": "7"}


def canton_code(provincia: str, canton: str) -> str:
    """"NN" = posicion del canton en la lista de su provincia + 1. KeyError/ValueError si no existe."""
    return f"{PROVINCIAS[provincia].index(canton) + 1:02d}"


def geo_catalog() -> list[dict]:
    return [
        {"code": code, "name": prov, "cantons": [{"code": f"{i + 1:02d}", "name": c} for i, c in enumerate(PROVINCIAS[prov])]}
        for prov, code in sorted(PROVINCE_CODES.items(), key=lambda x: x[1])
    ]
