"""Lectura tolerante de CSV / Excel exportados por bancos, Fygaro u hojas propias.

- Encabezados normalizados (sin tildes, minusculas, espacios -> _) y resueltos por sinonimos.
- Numeros en formato CR/US ("1.234,56", "1,234.56", "(1 000)", "₡ 5 000").
- Fechas dd/mm/aaaa, aaaa-mm-dd, dd-mm-aa y fechas nativas de Excel.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def norm(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def _delimiter(text: str) -> str:
    """El separador que aparece la misma cantidad de veces en mas lineas (los bancos ponen titulos sin separador)."""
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    best, score = ",", -1
    for cand in (";", ",", "\t", "|"):
        counts = [ln.count(cand) for ln in lines if ln.count(cand) > 0]
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        sc = counts.count(mode) * 100 + mode
        if sc > score:
            best, score = cand, sc
    return best


def read_table(data: bytes, filename: str) -> list[dict]:
    """Devuelve filas como dict {encabezado_normalizado: valor}. Salta filas vacias y titulos antes del encabezado."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        grid = [r for r in csv.reader(io.StringIO(text), delimiter=_delimiter(text))]
    grid = [r for r in grid if any(c not in (None, "") and str(c).strip() for c in r)]
    if not grid:
        return []
    # el encabezado es la primera fila con al menos 2 celdas de texto (los bancos ponen titulos arriba)
    hi = next((i for i, r in enumerate(grid[:15]) if sum(1 for c in r if isinstance(c, str) and c.strip()) >= 2), 0)
    headers = [norm(h) or f"col_{i}" for i, h in enumerate(grid[hi])]
    rows = []
    for r in grid[hi + 1 :]:
        rows.append({headers[i]: (r[i] if i < len(r) else None) for i in range(len(headers))})
    return rows


def pick(row: dict, *aliases: str):
    """Primer valor no vacio entre los alias (ya normalizados)."""
    for a in aliases:
        v = row.get(a)
        if v not in (None, "") and str(v).strip() != "":
            return v
    return None


def num(v) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^0-9,.\-]", "", s).strip("-")
    if not s:
        return None
    if "," in s and "." in s:
        # el ultimo separador es el decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", ".") if len(parts) == 2 and len(parts[1]) <= 2 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif s.count(".") == 1 and len(s.split(".")[1]) == 3 and s.split(".")[0] not in ("", "0"):
        s = s.replace(".", "")  # "₡1.500" en montos: punto de miles
    try:
        n = Decimal(s)
    except InvalidOperation:
        return None
    return -n if neg else n


def to_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def text(v, limit: int = 300) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if isinstance(v, float) and v.is_integer():
        s = str(int(v))
    return s[:limit] or None
