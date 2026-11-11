"""O3-03, regla «nombre_v2»: los patrones por los que la muestra real de nombre_v1 no paso la revision (> 2 % de error).

Prueba dorada nueva: tests/fixtures/nombres_comerciales_v2.json, con nombres FICTICIOS que reproducen cada patron
(misma forma que los del proveedor; codigos y numeros cambiados) y la salida esperada escrita a mano. Por patron:
- inventado: ningun dato que no este en el nombre (ni color, ni PoE, ni potencia, ni nada de la descripcion libre);
  de la ficha aprobada solo la capacidad, los canales o los puertos, con la clave exacta. Un DVR nunca es PoE.
- restos: el modelo es el codigo real; fuera bandas sueltas (2.4, 5GHZ), codecs (H.265), «-CH», sufijos de region
  (us, eu, un, au, la), versiones de hardware (1.0, 2.0), puertos «1C1A» y codigos de bodega.
- tipo: el sustantivo con que empieza el original (Cuchara arrocera, Protector de voltaje, Barra de luces), con la
  errata del proveedor corregida («voltahe»). Sin sustantivo: el tipo de la categoria a revision, o ningun nombre.
- mayusculas: lineas con su forma (PartyBox), codigos en mayusculas (DS-PDSMK-E-WB), siglas (RGBIC).
- variante: el color (escrito o en el codigo del fabricante: BLU, GRY) y el paquete se quedan; dos variantes nunca
  dan el mismo nombre.
- ya_bien: lo que nombre_v1 ya hacia bien sigue igual.
Cambiar la regla es cambiar esta tabla a conciencia (y volver a aprobar una muestra)."""

import json
import re
from pathlib import Path

import pytest

from app.services import catalog_drafts as cd
from app.services import catalog_rules  # noqa: F401 (registra las reglas)
from app.services import product_names as pn
from app.services.brands import canonical_brand

TABLA = json.loads((Path(__file__).parent / "fixtures" / "nombres_comerciales_v2.json").read_text(encoding="utf-8"))
PATRONES = {"inventado": 8, "restos": 12, "tipo": 12, "mayusculas": 4, "variante": 6, "ya_bien": 12}


def _gen(row: dict) -> tuple[str | None, str | None]:
    return pn.commercial_name(
        row["proveedor"], brand=row["marca"], model=row["modelo"], shown_type=row["tipo"], description=row.get("descripcion"), specs=row.get("ficha")
    )


@pytest.mark.parametrize("row", TABLA, ids=[f"{r['patron']}:{r['proveedor'][:40]}" for r in TABLA])
def test_tabla_dorada_v2(row):
    assert _gen(row) == (row["comercial"], row["revisar"])


def test_la_tabla_cubre_todos_los_patrones():
    assert len(TABLA) >= 60
    for patron, minimo in PATRONES.items():
        assert sum(1 for r in TABLA if r["patron"] == patron) >= minimo, patron
    assert {r["patron"] for r in TABLA} == set(PATRONES)


def test_nada_inventado():
    for r in TABLA:
        n, src = r["comercial"], pn._fold(r["proveedor"])
        if not n:
            continue
        f = pn._fold(n)
        if re.search(r"\d+ w\b", f):
            assert re.search(r"\d+\s?w\b", src), n  # potencia solo si el nombre la dice
        if re.search(r"· poe\+?(?: |$)", f):
            assert "poe" in src and not re.search(r"\b[dx]vr\b", src), n
        assert not ("dvr" in f and "poe" in f), n
        for w in f.replace("·", " ").split():
            if w in pn.COLORS:
                assert pn._color(r["proveedor"]) == pn.COLORS[w], n
        for num in re.findall(r"(\d+) (?:mah|ah|va|tb|gb|canales|puertos)\b", f):
            ficha = " ".join(str(v) for k, v in (r.get("ficha") or {}).items() if k in ("Capacidad", "Puertos", "Canales"))
            assert num in src or num in ficha, n


def test_sin_restos_en_el_modelo():
    junk = re.compile(r"(?<![\w.])(2\.4|5ghz|2\.4ghz|h\.26[45]\+?|\d+-ch|us|eu|un|au|la|1\.0|2\.0|\d[ac]\d[ac])(?![\w.])|-(?:au|us|eu|la)\b|\(un\)|(?<![\w-])\d{5,}(?![\w-])|[a-z]\.[a-z0-9]", re.I)
    for r in TABLA:
        if r["comercial"]:
            head = r["comercial"].split(" · ")[0]
            assert not junk.search(head), r["comercial"]


def test_siempre_con_tipo_o_sin_nombre():
    for r in TABLA:
        n, marca = r["comercial"], canonical_brand(r["marca"])
        if n is None:
            assert r["revisar"] == "sin tipo", r
            continue
        assert marca and not n.startswith(marca), n  # empieza con el tipo, nunca con la marca
        assert not n.startswith("Electrodoméstico"), n


def test_mayusculas():
    for r in TABLA:
        n = r["comercial"] or ""
        assert not re.search(r"Rgbic|Partybox|PARTYBOX|Ds-|\bUs\b|\bEu\b", n), n
        for tok in n.replace("·", " ").split():
            if re.search(r"\d", tok) and re.search(r"[a-z]", tok) and not re.fullmatch(r"\d+(?:ta|mm)?|\(.*\)", tok):
                assert tok in r["proveedor"], (n, tok)  # un codigo con minusculas va tal como vino
    assert pn._nice_v2("rgbic", False) == "RGBIC" and pn._nice_v2("ds-pdsmk-e-wb", True) == "DS-PDSMK-E-WB"


def test_dos_variantes_nunca_dan_el_mismo_nombre():
    names = [r["comercial"] for r in TABLA if r["comercial"]]
    assert len(names) == len(set(names)), [n for n in names if names.count(n) > 1]


def test_la_regla_tiene_nombre_nuevo():
    assert cd.GENERATORS["web_name"][0] == "nombre_v2"  # el lote viejo (nombre_v1) se distingue
