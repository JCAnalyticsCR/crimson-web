"""Texto que ve el usuario: concordancia de número para asuntos y cuerpos de correo.

Nunca "(s)" ni "1 facturas": el asunto de un correo se lee en la bandeja, sin contexto, y el plural mal puesto se nota.
"""

from __future__ import annotations


def _n(n) -> str:
    """El número como se lee: 3, 1,5 (los días de un levantamiento pueden ser fraccionarios)."""
    if isinstance(n, int):
        return str(n)
    return f"{n:g}".replace(".", ",")


def plural(n, uno: str, varios: str) -> str:
    """Cantidad con su sustantivo: plural(1, "factura", "facturas") -> "1 factura"; plural(6, ...) -> "6 facturas"."""
    return f"{_n(n)} {uno if n == 1 else varios}"


def forma(n, uno: str, varios: str) -> str:
    """Solo la palabra que concuerda con la cantidad (para el verbo o el adjetivo que acompaña):
    forma(1, "espera", "esperan") -> "espera"."""
    return uno if n == 1 else varios


def pluralizar(frase: str) -> str:
    """Plural de un sustantivo o frase corta (la primera palabra lleva el plural: "punto de red" -> "puntos de red")."""
    palabra, _, resto = frase.partition(" ")
    if not palabra:
        return frase
    if palabra[-1] in "aeiouáéó":
        plural_ = palabra + "s"
    elif palabra[-1] == "z":
        plural_ = palabra[:-1] + "ces"
    elif palabra[-1] == "s":
        plural_ = palabra
    else:
        plural_ = palabra + "es"
    return f"{plural_} {resto}" if resto else plural_


def plural_de(n, frase: str) -> str:
    """plural_de(2, "punto de red") -> "2 puntos de red"; plural_de(1, ...) -> "1 punto de red"."""
    return plural(n, frase, pluralizar(frase))
