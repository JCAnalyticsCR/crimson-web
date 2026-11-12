"""O3-03, reglas «nombre_v2» a «nombre_v4»: los patrones por los que las muestras reales no pasaron la revision.

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
- analogica (nombre_v3): THC-, DS-2CE, «4 en 1», HD-TVI, Turbo HD o AHD/CVI/CVBS hacen «Cámara analógica»; «luz
  blanca» no es un color y «(HILOOK std)» no es parte del codigo. En «tipo», el adjetivo que sigue a la marca va con
  el tipo («Afeitadora eléctrica Xiaomi») y el complemento se conserva («Monitor interior de videoportero»).
- v4 (nombre_v4, conservadora): lo que NO va a revision tiene que estar bien siempre. Gana el sustantivo inicial
  (Regleta, Botón sin contacto, Switch HDMI, Módulo SFP, Espumadera); un tipo que no sale del nombre va a revision.
  «Cámara IP/WiFi» solo si el nombre lo sostiene. El prefijo suelto se une si es inequivoco (tl WR941HP ->
  TL-WR941HP); si no, revision. Modelo de menos de 4 letras o numeros, terminado en «/», con un dato tecnico suelto
  (RJ45, 1080P, 2MPX) o con una palabra que no esta en el original: revision.
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
PATRONES = {"inventado": 8, "restos": 12, "tipo": 16, "mayusculas": 4, "variante": 6, "ya_bien": 12, "analogica": 4, "v4": 10}


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


def test_validacion_final_del_modelo():
    assert "modelo dudoso" in pn._check_line("Dome 2mpx/", "Camara logan dome 2MPX/ 1080P", None)
    assert "dato técnico suelto" in pn._check_line("ISD-SMG1118L RJ45", "Detector de metal ISD-SMG1118L RJ45", None)
    assert "dato técnico suelto" in pn._check_line("LX2 1080P", "Camara LX2 1080P", None)
    assert "prefijo suelto" in pn._check_line("Tl WR941HP", "Router tl WR941HP", None)
    assert "palabra sin origen" in pn._check_line("Archer ZZ9000", "Router archer AX10", None)
    assert pn._check_line("TL-WR941HP", "Router tp link tl WR941HP", None) == []  # unido: el original lo permite
    assert pn._check_line("Charge 5", "Parlante jbl charge 5", None) == []


def test_lo_que_no_va_a_revision_pasa_la_validacion():
    """ninguna fila propuesta sin motivo de revision trae un modelo dudoso ni un tipo que no sea el del nombre"""
    for tabla in (TABLA, json.loads((Path(__file__).parent / "fixtures" / "nombres_comerciales.json").read_text(encoding="utf-8"))):
        for r in tabla:
            out, flag = pn.commercial_name(r["proveedor"], brand=r["marca"], model=r["modelo"], shown_type=r["tipo"], specs=r.get("ficha"))
            if out and not flag:
                assert not re.search(r"(?<![\w.(-])(rj\d\d|\d{3,4}p|\d+(?:\.\d+)?mm|\d+mpx?)(?![\w-])|/$", out.split(" · ")[0], re.I), out


def test_la_regla_tiene_nombre_nuevo():
    assert cd.GENERATORS["web_name"][0] == "nombre_v4"  # los lotes rechazados (nombre_v1 a v3) se distinguen
