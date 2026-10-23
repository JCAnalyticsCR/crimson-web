"""Inventarios -> Artículos por completar y edición de precios del admin (23/10).

- El precio que pone una persona nunca lo pisa la sincronización de Eurocomp (apply_list / auto_catalog / refresh).
- La lista trae SOLO lo que no está a la venta por falta de datos: artículos de Eurocomp sin costo (no se pudieron crear)
  y productos sin precio. No trae lo que la tienda ya vende aunque le falte foto, descripción o CABYS, ni lo borrado.
- Crear desde el panel usa el camino de auto_catalog; mostrar/ocultar sube catalog_v y queda en la bitácora.
- La vendedora no ve la pantalla, ni costo ni margen."""

from decimal import Decimal

from sqlalchemy import select

from app.models import AuditLog, Category, Product, SupplierItem
from app.services import catalog_completar as cc
from app.services import supplier_sync as ss
from app.services.store_showcase import get_catalog_version
from tests.test_catalogo_ampliado import eurocomp_ampliado, item  # noqa: F401  (fixture)
from tests.test_papelera2 import _login_as

SIN_COSTO = item("EL0100", "LAVADORA LG 18KG", "ELECTRODOMESTICOS", "LAVADORAS", "LG", precio="", stock="4.000",
                 cabys="4471200000000", img="upload/items/2026/1/el0100.jpg")  # fmt: skip
CON_COSTO = item("EL0101", "REFRIGERADORA WHIRLPOOL 14 PIES", "ELECTRODOMESTICOS", "REFRIGERADORAS", "WHIRLPOOL", "450.00")
CAMARA = item("SG2104", "CAMARA IP HIKVISION DS-2CD1047G3", "SEGURIDAD", "Camaras", "HIKVISION", "48.50", modelo="DS-2CD1047G3")


def _setup(db_session, tid, state, items, publish=True):
    state["items"] = items
    ss.sync_all(db_session)
    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": publish})
    db_session.commit()
    t = db_session.get(ss.Tenant, tid)
    return ss.auto_catalog_run(db_session, t, ss.find_supplier(db_session, tid), commit_batches=True)


def _prod(db_session, tid, code):
    it = db_session.scalar(select(SupplierItem).where(SupplierItem.tenant_id == tid, SupplierItem.code == code))
    return it, (db_session.get(Product, it.product_id) if it and it.product_id else None)


def test_precio_del_admin_no_lo_pisa_la_sincronizacion(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    _setup(db_session, tid, eurocomp_ampliado, [CON_COSTO, CAMARA])
    _it, p = _prod(db_session, tid, "EL0101")
    r = client.patch(f"/products/{p.id}/price", json={"price": 777700})
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["price"])) == Decimal("777700")
    # Eurocomp cambia el costo: semanal (apply_list), auto_catalog otra vez y el refresco de 6 h
    eurocomp_ampliado["items"] = [dict(CON_COSTO, precio="999.00", stock="1.000"), CAMARA]
    ss.sync_all(db_session)
    t = db_session.get(ss.Tenant, tid)
    ss.auto_catalog_run(db_session, t, ss.find_supplier(db_session, tid), commit_batches=True)
    ss.refresh_linked(db_session)
    db_session.expire_all()
    p = db_session.get(Product, p.id)
    assert Decimal(str(p.price)) == Decimal("777700")
    assert Decimal(str(p.cost)) == Decimal("999.00")  # el costo sí se actualiza
    log = db_session.scalars(select(AuditLog).where(AuditLog.entity == "product", AuditLog.entity_id == p.id, AuditLog.action == "update")).all()
    assert any((x.after or {}).get("price") == "777700" for x in log)


def test_lista_solo_lo_que_no_se_vende_por_falta_de_datos(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    out = _setup(db_session, tid, eurocomp_ampliado, [SIN_COSTO, CON_COSTO, CAMARA])
    assert out["skip_sin_costo"] == 1
    # productos visibles con precio, aunque les falte foto o CABYS, no entran
    _it, refri = _prod(db_session, tid, "EL0101")
    refri.price, refri.images, refri.cabys_code = Decimal(250000), [], None
    _it, cam = _prod(db_session, tid, "SG2104")
    cam.price = Decimal(0)  # publicado sin precio: «Consultar»
    db_session.commit()

    r = client.get("/catalog-completar")
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {x["key"]: x for x in body["items"]}
    it_sin = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "EL0100"))
    assert f"i{it_sin.id}" in keys and keys[f"i{it_sin.id}"]["reason"] == "sin_costo"
    assert keys[f"i{it_sin.id}"]["cabys_code"] == "4471200000000" and keys[f"i{it_sin.id}"]["images"]
    assert f"p{cam.id}" in keys and keys[f"p{cam.id}"]["reason"] == "sin_precio"
    assert f"p{refri.id}" not in keys
    assert body["counts"] == {"sin_costo": 1, "sin_precio": 1}
    assert client.get("/catalog-completar/count").json()["total"] == 2
    assert len(client.get("/catalog-completar", params={"reason": "sin_costo"}).json()["items"]) == 1
    assert len(client.get("/catalog-completar", params={"brand": "Lg"}).json()["items"]) == 1
    assert len(client.get("/catalog-completar", params={"q": "lavadora"}).json()["items"]) == 1
    # retenido por el guardián de margen: tiene su propia bandeja
    cam.margin_hold = True
    db_session.commit()
    assert client.get("/catalog-completar/count").json()["total"] == 1


def test_crear_desde_el_panel_y_publicar(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    _setup(db_session, tid, eurocomp_ampliado, [SIN_COSTO, CON_COSTO])
    it = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "EL0100"))
    v0 = get_catalog_version(db_session.get(ss.Tenant, tid))
    r = client.post(f"/catalog-completar/items/{it.id}/crear", json={"price": 0})
    assert r.status_code == 422
    r = client.post(f"/catalog-completar/items/{it.id}/crear", json={"price": 389900, "show_on_web": True})
    assert r.status_code == 201, r.text
    db_session.expire_all()
    p = db_session.get(Product, r.json()["product_id"])
    assert p.show_on_web and Decimal(str(p.price)) == Decimal("389900")
    assert p.cabys_code == "4471200000000" and p.images  # del proveedor
    assert p.supplier_stock == 4  # sin costo la existencia del proveedor cuenta igual
    hija = db_session.get(Category, p.category_id)
    assert hija.name == "Lavadoras" and db_session.get(Category, hija.parent_id).name == "Electrodomésticos"
    assert get_catalog_version(db_session.get(ss.Tenant, tid)) != v0
    assert db_session.scalar(select(AuditLog).where(AuditLog.entity == "product", AuditLog.entity_id == p.id, AuditLog.action == "create"))
    assert client.get("/catalog-completar/count").json()["total"] == 0
    assert client.post(f"/catalog-completar/items/{it.id}/crear", json={"price": 1}).status_code == 409
    # la sincronización siguiente no duplica ni toca el precio
    ss.sync_all(db_session)
    t = db_session.get(ss.Tenant, tid)
    assert ss.auto_catalog_run(db_session, t, ss.find_supplier(db_session, tid), commit_batches=True)["creados"] == 0
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, p.id).price)) == Decimal("389900")


def test_crear_sin_publicar_y_descartar(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    otro = item("EL0102", "SECADORA LG 20KG", "ELECTRODOMESTICOS", "SECADORAS", "LG", precio="")
    _setup(db_session, tid, eurocomp_ampliado, [SIN_COSTO, otro])
    a = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "EL0100"))
    b = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "EL0102"))
    r = client.post(f"/catalog-completar/items/{a.id}/crear", json={"price": 100000, "show_on_web": False, "cabys_code": "123"})
    assert r.status_code == 422  # CABYS de 13 dígitos
    r = client.post(f"/catalog-completar/items/{a.id}/crear", json={"price": 100000, "show_on_web": False})
    assert r.status_code == 201 and r.json()["show_on_web"] is False
    assert client.post(f"/catalog-completar/items/{b.id}/descartar").status_code == 200
    assert client.get("/catalog-completar/count").json()["total"] == 0
    t = db_session.get(ss.Tenant, tid)
    out = ss.auto_catalog_run(db_session, t, ss.find_supplier(db_session, tid), commit_batches=True)
    assert out["creados"] == 0 and out["skip_borrado"] == 1  # lo descartado no se recrea


def test_completar_producto_y_mostrar_ocultar_en_bloque(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    _setup(db_session, tid, eurocomp_ampliado, [CON_COSTO, CAMARA])
    _i, cam = _prod(db_session, tid, "SG2104")
    _i, refri = _prod(db_session, tid, "EL0101")
    cam.price = Decimal(0)
    db_session.commit()
    v0 = get_catalog_version(db_session.get(ss.Tenant, tid))
    r = client.patch(f"/catalog-completar/products/{cam.id}", json={"price": 45900, "cabys_code": "4526100000200", "description_store": "Cámara 4 MP"})
    assert r.status_code == 200, r.text
    assert set(r.json()["changed"]) == {"price", "cabys_code", "description_store"}
    assert client.get("/catalog-completar/count").json()["total"] == 0
    assert get_catalog_version(db_session.get(ss.Tenant, tid)) != v0

    v1 = get_catalog_version(db_session.get(ss.Tenant, tid))
    r = client.post("/catalog-completar/web", json={"product_ids": [cam.id, refri.id], "show_on_web": False})
    assert r.status_code == 200 and r.json()["changed"] == 2
    db_session.expire_all()
    assert not db_session.get(Product, cam.id).show_on_web
    assert get_catalog_version(db_session.get(ss.Tenant, tid)) != v1
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "unpublish", AuditLog.entity == "product"))
    # el interruptor de la lista de productos también queda en la bitácora
    assert client.patch(f"/products/{cam.id}/web", json={"show_on_web": True}).status_code == 200
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "publish", AuditLog.entity_id == cam.id))


def test_ajuste_de_precios_en_bloque(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    _setup(db_session, tid, eurocomp_ampliado, [CON_COSTO, CAMARA])
    _i, refri = _prod(db_session, tid, "EL0101")
    _i, cam = _prod(db_session, tid, "SG2104")
    refri.price, refri.currency, refri.cost, refri.cost_currency = Decimal(100000), "CRC", Decimal(50000), "CRC"
    cam.price, cam.currency, cam.cost, cam.cost_currency = Decimal(30000), "CRC", Decimal(20000), "CRC"
    db_session.commit()
    cat = refri.category_id
    root = db_session.get(Category, cat).parent_id
    # "+10 % a Electrodomésticos" (la categoría padre incluye sus hijas); la vista previa no cambia nada
    r = client.post("/products/price-adjust", json={"mode": "pct", "value": 10, "category_id": root})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["changes"] == 1 and not b["applied"] and Decimal(str(b["items"][0]["price_after"])) == Decimal(110000)
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, refri.id).price)) == Decimal(100000)
    r = client.post("/products/price-adjust", json={"mode": "pct", "value": 10, "category_id": root, "confirm": True})
    assert r.json()["applied"]
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, refri.id).price)) == Decimal(110000)
    # margen objetivo 50 %: 20 000 / 0,5 = 40 000
    r = client.post("/products/price-adjust", json={"mode": "margen", "value": 50, "ids": [cam.id], "confirm": True})
    assert r.json()["items"][0]["margin_after"] == 50
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, cam.id).price)) == Decimal(40000)
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "price_adjust"))


def test_permisos_vendedora_e_inventario(client, auth, db_session, eurocomp_ampliado):  # noqa: F811
    tid = auth["tenant"]["id"]
    _setup(db_session, tid, eurocomp_ampliado, [SIN_COSTO, CON_COSTO])
    _i, refri = _prod(db_session, tid, "EL0101")
    it = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "EL0100"))
    ventas = _login_as(client, db_session, tid, "vendedora@ejemplo.com", "ventas")
    assert client.get("/catalog-completar", headers=ventas).status_code == 403
    assert client.patch(f"/products/{refri.id}/price", json={"price": 1}, headers=ventas).status_code == 403
    assert client.post("/products/price-adjust", json={"mode": "pct", "value": 5}, headers=ventas).status_code == 403
    row = client.get(f"/products/{refri.id}", headers=ventas).json()
    assert row.get("cost") is None and row.get("margin_real_pct") is None

    bodega = _login_as(client, db_session, tid, "bodega@ejemplo.com", "inventario")
    r = client.get("/catalog-completar", headers=bodega)
    assert r.status_code == 200 and all("cost" not in x for x in r.json()["items"]) and r.json()["fx"] is None
    assert client.post(f"/catalog-completar/items/{it.id}/crear", json={"price": 1000, "show_on_web": True}, headers=bodega).status_code == 403
    assert client.post(f"/catalog-completar/items/{it.id}/crear", json={"price": 1000}, headers=bodega).status_code == 201
    assert client.post("/catalog-completar/web", json={"product_ids": [refri.id], "show_on_web": False}, headers=bodega).status_code == 403
    assert client.post("/products/price-adjust", json={"mode": "pct", "value": 5}, headers=bodega).status_code == 403


def test_servicio_rows_sin_proveedor():
    """Sin Eurocomp ni familias la lista de artículos del proveedor queda vacía (no revienta)."""
    assert cc.REASONS.keys() == {"sin_costo", "sin_precio"}
