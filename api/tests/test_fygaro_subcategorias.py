"""Importador de Fygaro, segunda vuelta: subcategorias (la mas especifica, idempotente), "Revisar IVA" (0 %, tarifa
rara o sin dato; las raras no se publican solas), fotos fallidas reportadas sin perder las que ya habia, y la
disponibilidad que sale del inventario propio y nunca de Fygaro."""

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Category, Product, Tenant
from app.services import fygaro_import

from .test_fygaro_import import A, B, _catalog, _images_dir

SNAPSHOT = Path(__file__).resolve().parents[2] / "infra" / "fygaro" / "catalogo-fygaro-2026-10-12.json"
NVR = "522fda9a-b09d-4c7a-9a06-40c1e2f8e0d8"
SRV = "682f7979-a3c0-49e8-8b5d-6cb11bbb1855"


def _tenant(db) -> Tenant:
    return db.scalar(select(Tenant).where(Tenant.slug == "crimson"))


def _cat_with_subs() -> dict:
    c = copy.deepcopy(_catalog())
    for cat in c["categories"]:
        if cat["name"] == "Seguridad y Acceso":
            cat["subcategories"] = [{"fygaro_id": "9001", "name": "Control de Acceso", "products": 1}]
        if cat["name"] == "Redes":
            cat["subcategories"] = [{"fygaro_id": "9002", "name": "Accesorios", "products": 1}]
        if cat["name"] == "Videovigilancia":
            cat["subcategories"] = [{"fygaro_id": "9003", "name": "Accesorios", "products": 0}]  # mismo nombre, otra madre
    for p in c["products"]:
        if p["fygaro_id"] == A:
            p["subcategories"] = ["Control de Acceso"]
        if p["fygaro_id"] == B:
            p["subcategories"] = ["Accesorios"]  # Redes > Accesorios, no Videovigilancia > Accesorios
        if p["fygaro_id"] == SRV:
            p["tax_rate"], p["price_with_tax"] = 13.3, 45.32  # tarifa rara
    return c


def test_subcategorias_iva_y_disponibilidad(client, auth, db_session, tmp_path):
    t = _tenant(db_session)
    cat = _cat_with_subs()
    imgs = _images_dir(tmp_path)
    dry = fygaro_import.run(db_session, t, cat)
    db_session.rollback()
    rows = {r["fygaro_id"]: r for r in dry["rows"]}
    assert any("subcategoria nueva: Seguridad y Acceso > Control de Acceso" in c for c in rows[A]["changes"])
    assert rows[SRV]["iva_review"] == "rara" and any(w.startswith("Revisar IVA") for w in rows[SRV]["warnings"]) and not rows[SRV]["published"]
    assert dry["summary"]["revisar_iva"] >= 2 and dry["summary"]["iva_raro"] >= 2  # la rara y el kit sin dato

    rep = fygaro_import.run(db_session, t, cat, apply=True, images_dir=imgs, download_images=False)
    db_session.commit()
    a = db_session.scalar(select(Product).where(Product.code == "DS-K1T344EBFWX-E1"))
    sub = db_session.get(Category, a.category_id)
    madre = db_session.get(Category, sub.parent_id)
    assert sub.name == "Control de Acceso" and madre.name.lower() == "seguridad y acceso"
    b = db_session.scalar(select(Product).where(Product.code == "FY-1A2ADBB7"))
    bsub = db_session.get(Category, b.category_id)
    assert bsub.name == "Accesorios" and db_session.get(Category, bsub.parent_id).name == "Redes"
    srv = db_session.get(Product, rep["redirects"][SRV])
    assert not srv.show_on_web  # tarifa rara: no se publica sola
    assert t.settings["fygaro"]["categories"]["9001"] == sub.id  # enlace viejo de la subcategoria

    # idempotente: segunda corrida no crea categorias ni cambia nada
    n_cats = db_session.scalar(select(func.count()).select_from(Category))
    again = fygaro_import.run(db_session, t, cat, apply=True, images_dir=imgs, download_images=False)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(Category)) == n_cats
    assert again["summary"]["nuevo"] == 0 and again["summary"]["actualizar"] == 0
    # publicado a mano despues de revisar el IVA: otra corrida no lo vuelve a ocultar
    srv.show_on_web = True
    db_session.commit()
    fygaro_import.run(db_session, t, cat, apply=True, images_dir=imgs, download_images=False)
    db_session.commit()
    assert db_session.get(Product, srv.id).show_on_web

    # vitrina: la madre suma a sus hijas, filtrar por la madre incluye las subcategorias
    client.put("/store", json={"kind": "tienda", "published": True})
    home = client.get("/public/store/crimson").json()
    seg = next(c for c in home["categories"] if c["name"].lower() == "seguridad y acceso")
    assert seg["count"] >= 1 and seg["subcategories"][0]["name"] == "Control de Acceso"
    assert all(c["name"] != "Control de Acceso" for c in home["categories"])  # la hija no se repite arriba
    ids = {p["id"] for p in client.get("/public/store/crimson/products", params={"category_id": madre.id}).json()}
    assert a.id in ids
    # Fygaro decia "disponible": aqui manda el inventario propio (sin existencias ni proveedor -> agotado)
    av = client.get("/public/store/crimson/availability", params={"ids": str(a.id)}).json()[str(a.id)]
    assert av["estado"] == "agotado"


def test_fotos_fallidas_se_reportan_y_no_borran_las_que_habia(db_session, tmp_path):
    t = _tenant(db_session)
    imgs = _images_dir(tmp_path)
    fygaro_import.run(db_session, t, _catalog(), apply=True, images_dir=imgs, download_images=False)
    db_session.commit()
    a = db_session.scalar(select(Product).where(Product.code == "DS-K1T344EBFWX-E1"))
    antes = list(a.images)
    assert antes
    # la siguiente corrida no encuentra las fotos (sin carpeta y sin descarga): se reporta y se conservan
    rep = fygaro_import.run(db_session, t, _catalog(), apply=True, images_dir=None, download_images=False)
    db_session.commit()
    fallidas = rep["summary"]["fotos_fallidas"]
    assert any(f["code"] == "DS-K1T344EBFWX-E1" and f["url"].startswith("https://") for f in fallidas)
    db_session.refresh(a)
    assert a.images == antes and all("/api/media/f/" in i["url"] for i in a.images)  # nada apunta a Fygaro


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="sin el relevamiento de Fygaro")
def test_relevamiento_real_vista_previa():
    """El relevamiento de tienda.crimsoncr.com: 435 productos, 23 con IVA 0 %, tarifas raras sin publicar y
    subcategorias. Se corre en vista previa contra una base vacia (no escribe nada)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.db import Base
    from app.seeds import seed

    from .conftest import ADMIN

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        seed(s, *ADMIN)
        catalog = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        rep = fygaro_import.run(s, _tenant(s), catalog)
        sm = rep["summary"]
        assert sm["total"] == 435 and sm["iva_cero"] == 23
        raras = [r for r in rep["rows"] if r["iva_review"] in ("rara", "desconocida")]
        assert sm["iva_raro"] == len(raras) >= 20 and not any(r["published"] for r in raras if r["action"] == "nuevo")
        assert sm["subcategorias"] >= 30
        assert all(
            " > " in r["category"]
            for r in rep["rows"]
            if r["category"] and r["fygaro_id"] in {p["fygaro_id"] for p in catalog["products"] if p.get("subcategories")}
        )
    finally:
        s.close()
        engine.dispose()
