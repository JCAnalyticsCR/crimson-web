"""O3-08: reglas de publicacion y orden «Recomendados», detras de un interruptor apagado (reconfirmacion del dueno).

- Paridad del orden con el navegador (portal/src/modules/store/__tests__/orden_casos.json).
- Con el interruptor apagado (por defecto) nada cambia: el orden y las altas son los de siempre.
- Encendido: los primeros de la categoria son comprables o fijados a mano (8 como maximo); «Nombre» es de la A a la
  Z sin destacados arriba y «Precio» no tiene fijados; Hikvision ya no nace destacado; el alta automatica no publica
  por debajo del minimo, fuera de la especialidad ni sin categoria hoja.
Datos ficticios, precios en colones <= 2 000."""

import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import Category, Product, Tenant
from app.services import store_ranking as r
from app.services import supplier_sync as ss
from tests.test_catalogo_ampliado import eurocomp_ampliado  # noqa: F401, F811 (fixture)
from tests.test_store_vitrina import _publicar

SLUG = "/public/store/crimson"
CASOS = json.loads((Path(__file__).resolve().parents[2] / "portal/src/modules/store/__tests__/orden_casos.json").read_text(encoding="utf-8"))


def test_paridad_del_orden():
    P = CASOS["productos"]
    assert [p["id"] for p in sorted(P, key=lambda p: r.recommended_key(p["state"], p["pinned"], p["q"], p["price"], p["name"], p["id"]))] == CASOS["recomendados"]
    assert [p["id"] for p in sorted(P, key=lambda p: r.name_key(p["name"], p["id"]))] == CASOS["nombre"]


def _setup(client, db):
    _publicar(client)
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False
    cat = Category(tenant_id=t.id, name="Orden de prueba", show_on_web=True)
    db.add(cat)
    db.flush()
    img = lambda n: [{"url": f"https://cdn.example.com/o{i}.jpg", "main": i == 0} for i in range(n)]  # noqa: E731
    datos = [("Zeta camara", "Hikvision", True, 1500, 1), ("Alfa disco", "Otra", False, 1200, 2), ("Beta switch", "Otra", False, 0, 2), ("Gama ups", "Otra", False, 1900, 1)]
    out = {}
    for name, brand, feat, price, n in datos:
        p = Product(tenant_id=t.id, code=name[:4].upper(), name=name, brand=brand, web_featured=feat, price=Decimal(price), currency="CRC", category_id=cat.id,
                    show_on_web=True, images=img(n))  # fmt: skip
        db.add(p)
        db.flush()
        out[name] = p
    db.commit()
    main = next(w for w in client.get("/warehouses").json() if w["is_default"])
    for name in ("Alfa disco", "Gama ups"):  # comprables hoy
        assert client.post("/stock/movements", json={"product_id": out[name].id, "warehouse_id": main["id"], "kind": "entrada", "quantity": 3}).status_code == 201
    return t, cat, out


def _names(client, **params):
    return [x["name"] for x in client.get(f"{SLUG}/catalog", params={"category": "orden-de-prueba", **params}).json()["items"]]


def test_apagado_no_cambia_nada_y_encendido_ordena_recomendados(client, auth, db_session):
    t, cat, out = _setup(client, db_session)
    assert r.cfg(t)["recomendados"] is False
    assert _names(client, sort="name")[0] == "Zeta camara"  # como siempre: destacado y Hikvision arriba
    assert _names(client)[0] == "Alfa disco" or _names(client)[0] == "Gama ups"
    snap = client.get(f"{SLUG}/snapshot").json()
    assert "rank" not in snap
    # el dueno lo reconfirma: se enciende y se fija «Beta switch» en su categoria
    assert client.put("/store/content/orden", json={"recomendados": True, "min_publish_crc": 3000}).status_code == 200
    r_ = client.put(f"/store/content/pins/{cat.id}", json={"product_ids": [out["Beta switch"].id]})
    assert r_.status_code == 200, r_.text
    assert client.put(f"/store/content/pins/{cat.id}", json={"product_ids": list(range(1, 10))}).status_code == 422  # 8 como maximo
    # comprables primero (Alfa: 2 fotos, mas calidad de ficha que Gama), luego el fijado aunque no se pueda comprar hoy
    assert _names(client) == ["Alfa disco", "Gama ups", "Beta switch", "Zeta camara"]
    assert _names(client, sort="name") == ["Alfa disco", "Beta switch", "Gama ups", "Zeta camara"]  # A-Z de verdad
    assert _names(client, sort="price_asc") == ["Alfa disco", "Zeta camara", "Gama ups", "Beta switch"]  # sin fijados
    from app.services import store_snapshot

    store_snapshot.rebuild(db_session, t.id)  # el armado se encola; aqui se arma ya
    snap = client.get(f"{SLUG}/snapshot").json()
    assert snap["rank"] == "recomendados" and snap["pins"] == {str(cat.id): [out["Beta switch"].id]}


def test_altas_con_el_interruptor(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    ss.sync_all(db_session)
    ss.ac_save(t, {"enabled": True, "publish_web": True})
    st = dict(t.settings or {})
    st["tienda_o3"] = {"recomendados": True, "min_publish_crc": 3000}
    t.settings = st
    db_session.commit()
    ss.auto_catalog_run(db_session, db_session.get(Tenant, tid), ss.find_supplier(db_session, tid), commit_batches=True)
    cam = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG2104"))
    assert cam.web_featured is False  # Hikvision ya no nace destacado
    echo = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SH0002"))
    assert echo.show_on_web is False  # smart home: fuera de la especialidad, no se publica solo
    for p in db_session.scalars(select(Product).where(Product.tenant_id == tid, Product.supplier_sku.is_not(None))):
        if p.show_on_web:
            assert Decimal(str(p.price)) * Decimal("1.13") >= 3000  # nada publicado por debajo del minimo


def test_limpiar_destacados_con_informe_y_deshacer(client, auth, db_session):
    t, cat, out = _setup(client, db_session)
    rep = r.featured_simulate(db_session, t.id)
    assert out["Zeta camara"].id in rep["product_ids"]
    db_session.expire_all()
    assert db_session.get(Product, out["Zeta camara"].id).web_featured is True  # simular no escribe
    log = r.featured_apply(db_session, t.id, rep)
    db_session.commit()
    assert db_session.get(Product, out["Zeta camara"].id).web_featured is False
    assert r.featured_undo(db_session, t.id, log) == len(log["cleared"])
    db_session.commit()
    assert db_session.get(Product, out["Zeta camara"].id).web_featured is True
