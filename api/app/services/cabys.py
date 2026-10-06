"""Buscador de CABYS contra la API publica de Hacienda.

Los productos importados de la lista del proveedor no traen CABYS y sin CABYS no se factura. Escribir 13
digitos de memoria no es realista: se busca por descripcion ("camara ip") y se elige.

El navegador no llama a Hacienda directo (CORS, y si Hacienda se cae el portal se queda colgado): el API hace
de proxy con timeout corto y guarda en memoria lo ya consultado. El catalogo CABYS cambia muy poco.
"""

from __future__ import annotations

import time
from threading import Lock

import httpx

BASE = "https://api.hacienda.go.cr/fe/cabys"
TIMEOUT = 6.0  # segundos: si Hacienda no contesta rapido, mejor avisar que dejar el formulario esperando
TTL = 12 * 3600
MAX_ENTRIES = 500

_cache: dict[tuple, tuple[float, list[dict]]] = {}
_lock = Lock()


class CabysUnavailable(Exception):
    """Hacienda no respondio o respondio algo que no se entiende."""


def _fetch(params: dict) -> object:
    """Unico punto que sale a internet (los tests lo reemplazan)."""
    r = httpx.get(BASE, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _normalize(raw: object) -> list[dict]:
    rows = raw.get("cabys", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    out = []
    for x in rows:
        if not isinstance(x, dict) or not x.get("codigo"):
            continue
        code = "".join(ch for ch in str(x["codigo"]) if ch.isdigit())
        if len(code) != 13:
            continue
        tax = x.get("impuesto")
        try:
            tax = float(tax) if tax is not None else None
        except (TypeError, ValueError):
            tax = None
        cats = x.get("categorias") or []
        out.append({"code": code, "description": str(x.get("descripcion") or "").strip(), "tax_rate": tax, "categories": [str(c) for c in cats][-2:]})
    return out


def search(q: str | None = None, codigo: str | None = None, top: int = 20) -> list[dict]:
    if codigo:
        codigo = "".join(ch for ch in codigo if ch.isdigit())
        key, params = ("codigo", codigo), {"codigo": codigo}
    else:
        q = " ".join((q or "").split()).lower()
        key, params = ("q", q, top), {"q": q, "top": top}
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    try:
        rows = _normalize(_fetch(params))
    except (httpx.HTTPError, ValueError) as e:
        raise CabysUnavailable(str(e)[:200]) from e
    with _lock:
        if len(_cache) >= MAX_ENTRIES:  # sin dependencias: se descarta lo mas viejo
            for k in sorted(_cache, key=lambda k: _cache[k][0])[: MAX_ENTRIES // 5]:
                _cache.pop(k, None)
        _cache[key] = (now, rows)
    return rows


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def find_code(code: str) -> dict | None:
    """Confirma que un codigo de 13 digitos existe en el CABYS. Primero mira lo ya consultado (una busqueda
    por descripcion que lo trajo cuenta como prueba) y si no, le pregunta a Hacienda por codigo.
    None = Hacienda respondio y el codigo no existe. CabysUnavailable = no se pudo validar."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 13:
        return None
    now = time.monotonic()
    with _lock:
        for ts, rows in _cache.values():
            if now - ts >= TTL:
                continue
            for r in rows:
                if r["code"] == code:
                    return r
    return next((r for r in search(codigo=code) if r["code"] == code), None)
