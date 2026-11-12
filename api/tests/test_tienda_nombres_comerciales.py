"""O3-03: nombres comerciales por regla, aprobados por lote (regla «nombre_v4»; v1 a v3 no pasaron las muestras reales).

Prueba dorada: 100 nombres con la forma de los del proveedor (marcas y modelos de fabricante; codigos internos y de
lote ficticios) y la salida aprobada en tests/fixtures/nombres_comerciales.json. Cambiar la regla es cambiar esa
tabla a conciencia (y volver a aprobarla). Los patrones de la muestra rechazada tienen su propia tabla
(test_tienda_nombres_v2.py). Ademas, sobre toda la tabla:
- promedio de 50 caracteres o menos y menos del 20 % sobre 60;
- ningun nombre termina en un codigo (ASIN, EAN, codigo interno, JBL...AM) ni trae un color que no este escrito en el
  original (la palabra o el codigo de color del fabricante: JBLCHARGE6BLKAM -> negro);
- el original queda para la factura: el generador solo propone web_name (borrador), nunca toca Product.name.
Datos ficticios, precios en colones <= 2 000."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import CatalogBatch, CatalogDraft, Category, Product, Tenant
from app.services import catalog_drafts as cd
from app.services import product_names as pn

DORADA = json.loads((Path(__file__).parent / "fixtures" / "nombres_comerciales.json").read_text(encoding="utf-8"))
COLORES = set(pn.COLORS)


@pytest.mark.parametrize("row", DORADA, ids=[r["proveedor"][:40] for r in DORADA])
def test_tabla_dorada(row):
    out, flag = pn.commercial_name(row["proveedor"], brand=row["marca"], model=row["modelo"], shown_type=row["tipo"])
    assert (out, flag) == (row["comercial"], row["revisar"])


def test_la_tabla_cumple_la_regla():
    assert len(DORADA) == 100
    names = [r["comercial"] for r in DORADA if r["comercial"]]
    assert sum(len(n) for n in names) / len(names) <= 50
    assert sum(1 for n in names if len(n) > 60) / len(names) < 0.20
    for r in DORADA:
        n = r["comercial"]
        if not n:
            continue
        assert not re.search(r"\b(B0[A-Z0-9]{8}|\d{8,14}|JBL[A-Z0-9]+AM|HK\d{3}-\S+)$", n), n
        assert "(" not in n or ")" in n, n
        for w in pn._fold(n).replace("·", " ").split():
            if w in COLORES:  # el color solo si esta escrito en el original (palabra o codigo de color del fabricante)
                assert pn._color(r["proveedor"]) == pn.COLORS[w], (n, w)


def test_errata_y_codigo_final_se_corrigen_solo_en_el_comercial():
    out, _ = pn.commercial_name("Camara IP hikvusion DS-2CD1023G2-LIU 2MP bullet", brand="Hikvision", model="DS-2CD1023G2-LIU", shown_type="Cámara IP")
    assert "hikvusion" not in out.lower() and out.startswith("Cámara IP Hikvision")
    out, _ = pn.commercial_name("Parlante JBL Flip 6 bluetooth JBLFLIP6BLKAM", brand="JBL", model=None, shown_type="Parlantes")
    assert out == "Parlante JBL Flip 6 · Bluetooth · negro"  # el color del codigo del fabricante se queda; el codigo no
    out, _ = pn.commercial_name("Parlante JBL Flip 6 bluetooth", brand="JBL", model=None, shown_type="Parlantes")
    assert out == "Parlante JBL Flip 6 · Bluetooth"  # sin color escrito, sin color


def test_lote_de_nombres_por_regla_con_repetidos_a_revision(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(cd, "in_sync_window", lambda now=None: False)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    cat = Category(tenant_id=t.id, name="Parlantes de prueba", show_on_web=True)
    db_session.add(cat)
    db_session.flush()
    datos = [
        ("JBL-1", "Parlante JBL Charge 6 45W bluetooth negro JBLCHARGE6BLKAM"),
        ("JBL-2", "Parlante JBL Charge 6 45W bluetooth negro JBLCHARGE6BLKAM (US)"),  # el mismo nombre comercial
        ("JBL-3", "Parlante JBL Go 4 bluetooth rojo JBLGO4REDAM"),
        ("JBL-4", "Parlante JBL Charge 6 45W bluetooth JBLCHARGE6BLUAM"),  # otro color (solo en el codigo): no es repetido
    ]
    ids = []
    for code, name in datos:
        p = Product(tenant_id=t.id, code=code, name=name, brand="Jbl", price=Decimal("1800"), currency="CRC", category_id=cat.id, show_on_web=True)
        db_session.add(p)
        db_session.flush()
        ids.append(p.id)
    db_session.commit()
    sim = client.post("/store/content/generate", json={"field": "web_name", "category_id": cat.id, "simulate": True}).json()
    assert sim["rule"] == "nombre_v4"  # el lote nuevo se distingue de los rechazados (nombre_v1 a v3)
    assert [r["after"] for r in sim["preview"]] == ["Parlante JBL Charge 6 · 45 W · Bluetooth · negro"] * 2 + [
        "Parlante JBL Go 4 · Bluetooth · rojo",
        "Parlante JBL Charge 6 · 45 W · Bluetooth · azul",
    ]
    assert [r["flag"] for r in sim["preview"]] == ["nombre repetido", "nombre repetido", "modelo dudoso", None]  # «Go 4»: 3 caracteres
    bid = client.post("/store/content/generate", json={"field": "web_name", "category_id": cat.id, "simulate": False}).json()["batch_id"]
    flags = {d.product_id: d.flag for d in db_session.scalars(select(CatalogDraft).where(CatalogDraft.batch_id == bid))}
    assert flags == {ids[0]: "nombre repetido", ids[1]: "nombre repetido", ids[2]: "modelo dudoso", ids[3]: None}
    assert db_session.get(CatalogBatch, bid).rule == "nombre_v4"
    db_session.expire_all()
    assert [db_session.get(Product, i).name for i in ids] == [d[1] for d in datos]  # el fiscal no cambia
    assert all(db_session.get(Product, i).web_name is None for i in ids)  # hasta que se apruebe y aplique
