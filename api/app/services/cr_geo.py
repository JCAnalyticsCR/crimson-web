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
