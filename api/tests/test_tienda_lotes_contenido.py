"""O3-11: mecanica de lotes del contenido de la tienda (borradores, aprobacion, muestra de exactitud y deshacer).

- Generar es idempotente (dos veces la misma regla no duplica) y la simulacion no escribe nada.
- Sin la muestra revisada (50 filas o todo el lote) con error menor al 2 % no se aplica.
- Aplicar escribe solo la columna web_ del campo: costo, existencia, precio, nombre fiscal y codigo no cambian.
- Deshacer vuelve exactamente a lo de antes; lo que alguien cambio despues no se toca.
- Lo escrito a mano (web_manual) queda intacto. Un alta nueva deja su borrador. No corre en la ventana del lunes.
- Aprobar, la muestra, aplicar y deshacer son del administrador (catalog.publicar).
Datos ficticios, precios en colones <= 2 000."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.models import CatalogBatch, CatalogDraft, Product, Tenant
from app.services import catalog_drafts as cd
from tests.test_campo import _login
from tests.test_catalogo_ampliado import eurocomp_ampliado  # noqa: F401, F811 (fixture)

URL = "/store/content"


@pytest.fixture
def regla(monkeypatch):
    """una regla de prueba: «Nombre comercial de <codigo>» (las reales llegan con O3-03, O3-05 y O3-06)"""
    gens = dict(cd.GENERATORS)
    gens["web_name"] = ("prueba_v1", lambda p, ctx: (f"Nombre comercial {p.code}", "revisar" if p.code.endswith("7") else None))
    monkeypatch.setattr(cd, "GENERATORS", gens)
    monkeypatch.setattr(cd, "in_sync_window", lambda now=None: False)
    return gens


def _productos(db, n=60):
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    out = []
    for i in range(n):
        p = Product(tenant_id=t.id, code=f"LT-{i:03d}", name=f"PRODUCTO DE PRUEBA {i} 311326530", price=Decimal("1500"), currency="CRC", cost=Decimal("5"),
                    supplier_stock=4, show_on_web=True)  # fmt: skip
        db.add(p)
        out.append(p)
    db.commit()
    return t, out


def _fiscal(db, ids):
    db.expire_all()
    return {i: (p.name, p.code, p.price, p.cost, p.supplier_stock) for i in ids if (p := db.get(Product, i))}


def test_generar_es_idempotente_y_la_simulacion_no_escribe(client, auth, db_session, regla):
    t, prods = _productos(db_session, 10)
    ids = [p.id for p in prods]
    sim = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": ids, "simulate": True}).json()
    assert sim["counts"]["propuestos"] == 10 and sim["batch_id"] is None and len(sim["preview"]) == 10
    assert sim["counts"]["a_revision"] == 1  # LT-007
    assert db_session.scalar(select(func.count()).select_from(CatalogDraft)) == 0
    r = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": ids, "simulate": False}).json()
    assert r["batch_id"] and r["counts"]["propuestos"] == 10
    again = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": ids, "simulate": False}).json()
    assert again["batch_id"] is None and again["counts"]["ya_propuesto"] == 10
    assert db_session.scalar(select(func.count()).select_from(CatalogDraft)) == 10
    assert all(p.web_name is None for p in db_session.scalars(select(Product).where(Product.id.in_(ids))))  # nada aplicado
    lst = client.get(f"{URL}/batches").json()
    assert lst[0]["id"] == r["batch_id"] and lst[0]["counts"] == {"propuesto": 10} and lst[0]["sample"]["needed"] == 10


def test_no_se_aplica_sin_muestra_y_deshacer_es_exacto(client, auth, db_session, regla):
    t, prods = _productos(db_session, 60)
    ids = [p.id for p in prods]
    prods[0].web_name = "Nombre que ya tenia"
    db_session.commit()
    antes = _fiscal(db_session, ids)
    bid = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": ids, "simulate": False}).json()["batch_id"]
    assert client.post(f"{URL}/batches/{bid}/approve", json={"draft_ids": None}).status_code == 200
    r = client.post(f"{URL}/batches/{bid}/apply")
    assert r.status_code == 422 and "muestra" in r.json()["detail"]
    sample = client.get(f"{URL}/batches/{bid}/sample").json()["draft_ids"]
    assert len(sample) == 50 and sample == client.get(f"{URL}/batches/{bid}/sample").json()["draft_ids"]  # siempre la misma
    # 1 error en 50 = 2 %: no alcanza (tiene que ser MENOR al 2 %)
    st = client.post(f"{URL}/batches/{bid}/sample", json={"checked": sample, "wrong": sample[:1]}).json()
    assert st["ok"] is False and st["wrong"] == 1
    assert client.post(f"{URL}/batches/{bid}/apply").status_code == 422
    assert client.post(f"{URL}/batches/{bid}/sample", json={"checked": sample[:10], "wrong": []}).json()["ok"] is False  # muestra chica
    assert client.post(f"{URL}/batches/{bid}/sample", json={"checked": [999999], "wrong": []}).status_code == 422
    st = client.post(f"{URL}/batches/{bid}/sample", json={"checked": sample[1:] + [d for d in [x["id"] for x in client.get(f"{URL}/batches/{bid}").json()["items"]] if d not in sample][:1], "wrong": []}).json()
    assert st["ok"] is True
    out = client.post(f"{URL}/batches/{bid}/apply")
    assert out.status_code == 200, out.text
    assert out.json()["aplicados"] == 59  # la fila con error se descarto
    db_session.expire_all()
    shown = {p.id: p.web_name for p in db_session.scalars(select(Product).where(Product.id.in_(ids)))}
    assert sum(1 for v in shown.values() if v and v.startswith("Nombre comercial")) == 59
    assert _fiscal(db_session, ids) == antes  # costo, existencia, precio, nombre fiscal y codigo intactos
    assert client.post(f"{URL}/batches/{bid}/apply").status_code == 422  # un lote se aplica una vez
    # alguien edita uno a mano despues: deshacer no lo pisa
    otro = next(i for i, v in shown.items() if v and v.startswith("Nombre comercial") and i != prods[0].id)
    assert client.put(f"/store/showcase/products/{otro}", json={"web_name": "Escrito a mano"}).status_code == 200
    u = client.post(f"{URL}/batches/{bid}/undo").json()
    assert u["deshechos"] == 58 and u["cambiados_despues"] == [otro]
    db_session.expire_all()
    after = {p.id: p.web_name for p in db_session.scalars(select(Product).where(Product.id.in_(ids)))}
    assert after[prods[0].id] == "Nombre que ya tenia"  # vuelve exactamente a lo de antes
    assert after[otro] == "Escrito a mano"
    assert all(v is None for i, v in after.items() if i not in (prods[0].id, otro))
    assert db_session.get(CatalogBatch, bid).status == "deshecho"


def test_lo_escrito_a_mano_no_se_toca(client, auth, db_session, regla):
    t, prods = _productos(db_session, 3)
    prods[0].web_manual, prods[0].web_name = True, "Escrito por Andres"
    db_session.commit()
    r = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": [p.id for p in prods], "simulate": False}).json()
    assert r["counts"]["productos"] == 2  # el manual ni siquiera es candidato
    bid = r["batch_id"]
    prods[1].web_manual = True  # se marco a mano despues de proponer
    db_session.commit()
    client.post(f"{URL}/batches/{bid}/approve", json={})
    rows = [x["id"] for x in client.get(f"{URL}/batches/{bid}").json()["items"]]
    assert client.post(f"{URL}/batches/{bid}/sample", json={"checked": rows, "wrong": []}).json()["ok"]
    assert client.post(f"{URL}/batches/{bid}/apply").json() == {"aplicados": 1, "omitidos": 1}
    db_session.expire_all()
    assert db_session.get(Product, prods[0].id).web_name == "Escrito por Andres"
    assert db_session.get(Product, prods[1].id).web_name is None


def test_aprobar_y_aplicar_solo_el_administrador(client, auth, db_session, regla):
    t, prods = _productos(db_session, 2)
    bid = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": [p.id for p in prods], "simulate": False}).json()["batch_id"]
    h = _login(client, db_session, t.id, "vendedora.lotes@example.com", "ventas")
    assert client.get(f"{URL}/batches", headers=h).status_code == 200
    for path in ("approve", "sample", "apply", "undo"):
        r = client.post(f"{URL}/batches/{bid}/{path}", json={"draft_ids": None, "checked": [], "wrong": []} if path != "apply" and path != "undo" else None, headers=h)
        assert r.status_code == 403, (path, r.text)


def test_alta_nueva_genera_borrador(client, auth, db_session, regla, eurocomp_ampliado):  # noqa: F811
    from app.services import supplier_sync as ss

    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)
    ss.ac_save(db_session.get(ss.Tenant, tid), {"enabled": True, "publish_web": False})
    db_session.commit()
    ss.auto_catalog_run(db_session, db_session.get(ss.Tenant, tid), ss.find_supplier(db_session, tid), commit_batches=True)
    cam = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG2104"))
    d = db_session.scalar(select(CatalogDraft).where(CatalogDraft.product_id == cam.id, CatalogDraft.field == "web_name"))
    assert d is not None and d.status == "propuesto" and d.value == f"Nombre comercial {cam.code}"
    altas = list(db_session.scalars(select(CatalogBatch).where(CatalogBatch.source == "alta", CatalogBatch.field == "web_name")))
    assert len(altas) == 1  # un solo lote abierto de altas por campo
    assert db_session.scalar(select(func.count()).select_from(CatalogDraft).where(CatalogDraft.batch_id == altas[0].id)) >= 6
    assert cam.web_name is None  # no se aplica solo


def test_ventana_de_la_sincronizacion():
    cr = ZoneInfo("America/Costa_Rica")
    assert cd.in_sync_window(datetime(2026, 11, 16, 3, 17, tzinfo=cr))  # lunes 03:17
    assert cd.in_sync_window(datetime(2026, 11, 16, 5, 0, tzinfo=cr))  # guardian de margen despues
    assert not cd.in_sync_window(datetime(2026, 11, 16, 6, 0, tzinfo=cr))
    assert not cd.in_sync_window(datetime(2026, 11, 17, 3, 17, tzinfo=cr))  # martes


def test_generar_no_corre_en_la_ventana(client, auth, db_session, regla, monkeypatch):
    t, prods = _productos(db_session, 1)
    monkeypatch.setattr(cd, "in_sync_window", lambda now=None: True)
    r = client.post(f"{URL}/generate", json={"field": "web_name", "product_ids": [prods[0].id], "simulate": False})
    assert r.status_code == 422 and "sincronización" in r.json()["detail"]
