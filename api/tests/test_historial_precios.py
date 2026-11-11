"""O0-07: historial de precios, web_updated_at y hidden_since (solo se registran; nada se muestra todavia).

- Cambiar un precio (lista, ficha, precio web, ajuste en bloque) deja UNA fila con origen y usuario; guardar el mismo
  precio no deja fila. Ningun precio cambia por registrar.
- La sincronizacion de costo o existencia no toca web_updated_at ni el historial; un cambio visible si lo toca.
- El rearmado del snapshot fija hidden_since al producto publicado que no sale y lo limpia cuando vuelve.
- La migracion sube con la fila inicial y baja limpia (la cadena completa y la cabeza unica: test_infra_migraciones).
Datos 100 % ficticios, precios de 2 000 colones o menos.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import select

from app.models import Product, ProductPriceHistory, Tax, Tenant, User
from app.models.catalog import ProductTax


def _tenant(db) -> Tenant:
    return db.scalar(select(Tenant).where(Tenant.slug == "crimson"))


def _producto(db, code="HP-0001", price="1500", **kw) -> Product:
    t = _tenant(db)
    iva = db.scalar(select(Tax).where(Tax.tenant_id == t.id, Tax.rate == 13))
    p = Product(tenant_id=t.id, code=code, name=f"Producto ficticio {code}", price=Decimal(price), currency="CRC", **kw)
    if iva is not None:
        p.taxes.append(ProductTax(tax_id=iva.id))
    db.add(p)
    db.commit()
    return p


def _filas(db, pid: int) -> list[ProductPriceHistory]:
    return list(db.scalars(select(ProductPriceHistory).where(ProductPriceHistory.product_id == pid).order_by(ProductPriceHistory.id)))


def _admin_id(db) -> int:
    return db.scalar(select(User.id).where(User.email == "admin@crimsonapp.com"))


def test_producto_nuevo_deja_fila_inicial_y_fecha_web(db_session):
    p = _producto(db_session)
    filas = _filas(db_session, p.id)
    assert len(filas) == 1
    f = filas[0]
    assert d(f.price_net) == Decimal("1500") and f.web_price_net is None and f.currency == "CRC"
    assert d(f.tax_rate) == Decimal(13) and d(f.price_final) == Decimal("1695.00")
    assert f.source == "manual" and f.changed_by is None
    assert p.web_updated_at is not None


def test_cambiar_precio_en_la_lista_crea_una_fila_con_usuario(client, auth, db_session):
    p = _producto(db_session)
    r = client.patch(f"/products/{p.id}/price", json={"price": "1800"})
    assert r.status_code == 200, r.text
    filas = _filas(db_session, p.id)
    assert len(filas) == 2
    assert d(filas[-1].price_net) == Decimal("1800") and d(filas[-1].price_final) == Decimal("2034.00")
    assert filas[-1].source == "manual" and filas[-1].changed_by == _admin_id(db_session)
    # registrar no cambia el precio
    db_session.refresh(p)
    assert d(p.price) == Decimal("1800")
    # mismo precio otra vez: ninguna fila nueva
    assert client.patch(f"/products/{p.id}/price", json={"price": "1800.00"}).status_code == 200
    assert len(_filas(db_session, p.id)) == 2


def test_precio_web_del_inventario_web_deja_fila_con_el_precio_final_de_la_tienda(client, auth, db_session):
    p = _producto(db_session, price="1000")
    r = client.patch(f"/store/inventory/products/{p.id}", json={"web_price": "900"})
    assert r.status_code == 200, r.text
    f = _filas(db_session, p.id)[-1]
    assert d(f.price_net) == Decimal("1000") and d(f.web_price_net) == Decimal("900")
    assert d(f.price_final) == Decimal("1017.00")  # 900 + 13 %
    # volver al precio de siempre tambien es un cambio
    assert client.patch(f"/store/inventory/products/{p.id}", json={"web_price": None}).status_code == 200
    f = _filas(db_session, p.id)[-1]
    assert f.web_price_net is None and d(f.price_final) == Decimal("1130.00")
    assert len(_filas(db_session, p.id)) == 3


def test_ficha_con_cambio_de_iva_deja_fila_con_la_tarifa_nueva(client, auth, db_session):
    t = _tenant(db_session)
    p = _producto(db_session, price="2000")
    iva4 = db_session.scalar(select(Tax).where(Tax.tenant_id == t.id, Tax.rate == 4))
    if iva4 is None:
        iva4 = Tax(tenant_id=t.id, name="IVA 4 % (ficticio)", rate=4, rate_code="04")
        db_session.add(iva4)
        db_session.commit()
    body = client.get(f"/products/{p.id}").json()
    body = {k: body[k] for k in ("code", "name", "price", "currency", "item_type", "unit") if k in body}
    body["tax_ids"] = [iva4.id]
    r = client.put(f"/products/{p.id}", json=body)
    assert r.status_code == 200, r.text
    filas = _filas(db_session, p.id)
    assert len(filas) == 2
    assert d(filas[-1].tax_rate) == Decimal(4) and d(filas[-1].price_final) == Decimal("2080.00")


def test_ajuste_en_bloque_queda_como_lote(client, auth, db_session):
    p = _producto(db_session, price="1000")
    r = client.post("/products/price-adjust", json={"mode": "pct", "value": "10", "ids": [p.id], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]
    f = _filas(db_session, p.id)[-1]
    assert f.source == "lote" and d(f.price_net) > Decimal("1000")
    # la siguiente peticion vuelve a ser manual
    db_session.refresh(p)
    assert client.patch(f"/products/{p.id}/price", json={"price": "1200"}).status_code == 200
    assert _filas(db_session, p.id)[-1].source == "manual"


def test_sincronizacion_de_existencia_y_costo_no_toca_web_updated_at(db_session):
    from app.services import price_history

    p = _producto(db_session)
    antes = datetime(2026, 1, 1, tzinfo=UTC)
    p.web_updated_at = antes
    db_session.commit()
    price_history.set_source(db_session, "sync")
    p.supplier_stock = 7
    p.cost = Decimal("5")
    p.supplier_updated_at = datetime.now(UTC)
    p.margin_info = {"margin_pct": "40"}
    db_session.commit()
    db_session.refresh(p)
    assert _aware(p.web_updated_at) == antes
    assert len(_filas(db_session, p.id)) == 1  # sin cambio de precio, sin fila
    # un cambio que el cliente ve (nombre) si la mueve
    p.name = "Producto ficticio renombrado"
    db_session.commit()
    db_session.refresh(p)
    assert _aware(p.web_updated_at) > antes
    price_history.set_actor(db_session, None)


def test_sync_product_real_no_toca_web_updated_at(db_session, monkeypatch):
    from app.services import supplier_catalog as supcat

    p = _producto(db_session)
    antes = datetime(2026, 2, 2, tzinfo=UTC)
    p.web_updated_at = antes
    db_session.commit()

    class Item:  # oferta vinculada ficticia
        active, cost, currency, stock, list_date, supplier_id, code, id = True, Decimal("3"), "USD", 4, None, None, "X-1", 1

    monkeypatch.setattr(supcat, "linked_items", lambda db, pid: [Item()])
    cambios = supcat.sync_product(db_session, p, fx=Decimal("500"))
    db_session.commit()
    db_session.refresh(p)
    assert "supplier_stock" in cambios
    assert _aware(p.web_updated_at) == antes
    assert len(_filas(db_session, p.id)) == 1


def test_rearmado_fija_y_limpia_hidden_since(client, auth, db_session, monkeypatch):
    from app.services import store_snapshot

    r = client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50600000000"})
    assert r.status_code == 200, r.text
    t = _tenant(db_session)
    a = _producto(db_session, code="HP-OCULTO", show_on_web=True)
    b = _producto(db_session, code="HP-VISIBLE", show_on_web=True)
    c = _producto(db_session, code="HP-SIN-PUBLICAR", show_on_web=False)

    store_snapshot.rebuild(db_session, t.id)
    for p in (a, b, c):
        db_session.refresh(p)
    assert a.hidden_since is None and b.hidden_since is None and c.hidden_since is None
    upd_b = b.updated_at

    real_build = store_snapshot.build

    def sin_a(db, t_, ctx):  # la tienda deja de mostrar a (p. ej. agotado oculto)
        data = json.loads(gzip.decompress(real_build(db, t_, ctx)))
        data["products"] = [x for x in data["products"] if x["id"] != a.id]
        return gzip.compress(json.dumps(data).encode())

    monkeypatch.setattr(store_snapshot, "build", sin_a)
    store_snapshot.rebuild(db_session, t.id)
    for p in (a, b, c):
        db_session.refresh(p)
    assert a.hidden_since is not None
    primera = _aware(a.hidden_since)
    assert b.hidden_since is None and c.hidden_since is None  # c no esta publicado: no es "oculto"
    assert b.updated_at == upd_b  # marcar oculto no edita el producto

    store_snapshot.rebuild(db_session, t.id)  # sigue oculto: conserva la fecha del primer rearmado
    db_session.refresh(a)
    assert _aware(a.hidden_since) == primera

    monkeypatch.setattr(store_snapshot, "build", real_build)
    store_snapshot.rebuild(db_session, t.id)  # vuelve a salir
    db_session.refresh(a)
    assert a.hidden_since is None

    # despublicar limpia la marca (no es "oculto")
    monkeypatch.setattr(store_snapshot, "build", sin_a)
    store_snapshot.rebuild(db_session, t.id)
    db_session.refresh(a)
    assert a.hidden_since is not None
    a.show_on_web = False
    db_session.commit()
    monkeypatch.setattr(store_snapshot, "build", real_build)
    store_snapshot.rebuild(db_session, t.id)
    db_session.refresh(a)
    assert a.hidden_since is None
    # ninguna de estas marcas dejo filas de precio
    assert all(len(_filas(db_session, p.id)) == 1 for p in (a, b, c))


def test_migracion_sube_con_fila_inicial_y_baja(tmp_path, monkeypatch):
    from alembic import command
    from app.core.config import settings
    from app.core.db import make_engine

    from .test_infra_migraciones import _config

    url = f"sqlite:///{(tmp_path / 'precios.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    cfg = _config()
    command.upgrade(cfg, "a1c4e7b9d203")
    eng = make_engine(url)
    ahora = datetime.now(UTC).isoformat(sep=" ")
    with eng.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        c.execute(sa.text("INSERT INTO tax (id, tenant_id, name, code, rate_code, rate, active) VALUES (1, 1, 'IVA 13', '01', '08', 13, 1)"))
        cols = "id, tenant_id, name, code, item_type, price, currency, cost_currency, unit, min_stock, active, serialized, show_on_web, web_featured, margin_hold, images, created_at, updated_at"
        c.execute(
            sa.text(
                f"INSERT INTO product ({cols}, web_price) VALUES (1, 1, 'Ficticio', 'F-1', 'producto', 1000, 'crc', 'USD', 'Unid', 0, 1, 0, 1, 0, 0, '[]', :t, :t, 900)"
            ),
            {"t": ahora},
        )
        c.execute(
            sa.text(f"INSERT INTO product ({cols}) VALUES (2, 1, 'Ficticio 2', 'F-2', 'producto', 2000, 'CRC', 'USD', 'Unid', 0, 1, 0, 0, 0, 0, '[]', :t, :t)"),
            {"t": ahora},
        )
        c.execute(sa.text("INSERT INTO product_tax (id, product_id, tax_id) VALUES (1, 1, 1)"))
    eng.dispose()

    command.upgrade(cfg, "b7d3e5f1a924")
    eng = make_engine(url)
    with eng.connect() as c:
        rows = c.execute(
            sa.text("SELECT product_id, price_net, web_price_net, currency, tax_rate, price_final, source FROM product_price_history ORDER BY product_id")
        ).all()
        precios = c.execute(sa.text("SELECT id, price, web_price FROM product ORDER BY id")).all()
        cols = {x["name"] for x in sa.inspect(c).get_columns("product")}
    eng.dispose()
    assert [(r[0], Decimal(str(r[1])), r[3], Decimal(str(r[4])), Decimal(str(r[5])), r[6]) for r in rows] == [
        (1, Decimal(1000), "CRC", Decimal(13), Decimal("1017"), "inicial"),
        (2, Decimal(2000), "CRC", Decimal(13), Decimal("2260"), "inicial"),  # sin impuesto: 13 % como la tienda
    ]
    assert Decimal(str(rows[0][2])) == Decimal(900) and rows[1][2] is None
    assert [(i, Decimal(str(p)), w) for i, p, w in precios][0][:2] == (1, Decimal(1000))  # ningun precio cambio
    assert {"web_updated_at", "hidden_since"} <= cols

    command.downgrade(cfg, "a1c4e7b9d203")
    eng = make_engine(url)
    with eng.connect() as c:
        assert "product_price_history" not in sa.inspect(c).get_table_names()
        cols = {x["name"] for x in sa.inspect(c).get_columns("product")}
        assert "web_updated_at" not in cols and "hidden_since" not in cols
        assert c.scalar(sa.text("SELECT price FROM product WHERE id = 1")) == 1000
    eng.dispose()


def d(v) -> Decimal:
    return Decimal(str(v))


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


# --- cierre de la Ola 0 (hallazgos de la revision) ---------------------------------------------------------------


def test_revertir_un_savepoint_no_pierde_el_cambio_de_precio_ya_vaciado(db_session):
    p = _producto(db_session, price="1000")
    p.price = Decimal("2000")
    db_session.flush()  # el cambio ya esta en la base y fuera de session.dirty
    sp = db_session.begin_nested()
    sp.rollback()  # un savepoint que falla (p. ej. margin_guard.config) no debe borrar los pendientes
    db_session.commit()
    filas = _filas(db_session, p.id)
    assert [d(f.price_net) for f in filas] == [Decimal("1000"), Decimal("2000")]


def test_lo_creado_dentro_de_un_savepoint_revertido_no_deja_fila(db_session):
    t = _tenant(db_session)
    sp = db_session.begin_nested()
    q = Product(tenant_id=t.id, code="SP-0001", name="Producto ficticio savepoint", price=Decimal("700"), currency="CRC")
    db_session.add(q)
    db_session.flush()
    sp.rollback()
    db_session.commit()
    assert db_session.scalar(select(sa.func.count()).select_from(ProductPriceHistory).where(ProductPriceHistory.product_id.is_(None))) == 0
    assert db_session.scalar(select(sa.func.count()).select_from(Product).where(Product.code == "SP-0001")) == 0


def test_confirmar_un_savepoint_no_escribe_el_historial_a_media_transaccion(db_session):
    p = _producto(db_session, price="1000")
    p.price = Decimal("1500")
    with db_session.begin_nested():
        pass
    assert len(_filas(db_session, p.id)) == 1  # todavia nada: se escribe con el commit real
    db_session.commit()
    filas = _filas(db_session, p.id)
    assert [d(f.price_net) for f in filas] == [Decimal("1000"), Decimal("1500")]


def test_cambio_masivo_de_precios_hace_lecturas_en_bloque(db_session):
    t = _tenant(db_session)
    iva = db_session.scalar(select(Tax).where(Tax.tenant_id == t.id, Tax.rate == 13))
    prods = []
    for i in range(60):
        p = Product(tenant_id=t.id, code=f"MAS-{i:04d}", name=f"Masivo ficticio {i}", price=Decimal("1000"), currency="CRC")
        if iva is not None:
            p.taxes.append(ProductTax(tax_id=iva.id))
        db_session.add(p)
        prods.append(p)
    db_session.commit()
    for p in prods:
        p.price = Decimal("1100")
    selects: list[str] = []
    engine = db_session.get_bind()

    def _cuenta(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    sa.event.listen(engine, "before_cursor_execute", _cuenta)
    try:
        db_session.commit()
    finally:
        sa.event.remove(engine, "before_cursor_execute", _cuenta)
    # productos + impuestos + ultima fila: unas pocas lecturas, no tres por producto
    assert len(selects) <= 5, len(selects)
    for p in prods[:3]:
        assert d(_filas(db_session, p.id)[-1].price_net) == Decimal("1100")
    assert db_session.scalar(select(sa.func.count()).select_from(ProductPriceHistory).where(ProductPriceHistory.price_net == Decimal("1100"))) == 60


def test_retener_o_liberar_por_margen_mueve_web_updated_at(db_session):
    p = _producto(db_session)
    antes = datetime(2026, 3, 3, tzinfo=UTC)
    p.web_updated_at = antes
    db_session.commit()
    # solo datos internos del guardian: no se mueve
    p.margin_info = {"margin_pct": "5"}
    p.margin_flagged_at = datetime.now(UTC)
    db_session.commit()
    db_session.refresh(p)
    assert _aware(p.web_updated_at) == antes
    # retener: la tienda pasa a «Consultar»
    p.margin_hold = True
    db_session.commit()
    db_session.refresh(p)
    retenido = _aware(p.web_updated_at)
    assert retenido > antes
    p.web_updated_at = antes
    db_session.commit()
    # liberar: vuelve el precio
    p.margin_hold = False
    db_session.commit()
    db_session.refresh(p)
    assert _aware(p.web_updated_at) > antes
    assert len(_filas(db_session, p.id)) == 1  # el precio guardado no cambio: sin fila nueva


def test_dos_impuestos_dan_siempre_la_tasa_del_de_menor_id(db_session):
    t = _tenant(db_session)
    iva13 = db_session.scalar(select(Tax).where(Tax.tenant_id == t.id, Tax.rate == 13))
    if iva13 is None:
        iva13 = Tax(tenant_id=t.id, name="IVA 13 % (ficticio)", rate=13, rate_code="08")
        db_session.add(iva13)
    iva1 = Tax(tenant_id=t.id, name="IVA 1 % (ficticio)", rate=1, rate_code="02")
    db_session.add(iva1)
    db_session.commit()
    p = Product(tenant_id=t.id, code="DOS-0001", name="Dos impuestos ficticio", price=Decimal("1000"), currency="CRC")
    p.taxes.append(ProductTax(tax_id=iva13.id))
    db_session.add(p)
    db_session.flush()
    p.taxes.append(ProductTax(tax_id=iva1.id))
    db_session.commit()
    assert d(_filas(db_session, p.id)[-1].tax_rate) == Decimal(13)
    for nombre, precio in (("Dos impuestos renombrado", "1100"), ("Dos impuestos otra vez", "1200")):
        db_session.expire_all()
        p = db_session.get(Product, p.id)
        assert [pt.tax_id for pt in p.taxes] == [iva13.id, iva1.id]  # relacion ordenada por id
        p.name, p.price = nombre, Decimal(precio)
        db_session.commit()
    assert {d(f.tax_rate) for f in _filas(db_session, p.id)} == {Decimal(13)}
    assert len(_filas(db_session, p.id)) == 3


def test_margen_por_categoria_queda_como_lote(client, auth, db_session):
    from tests.test_margen_categoria import _base

    x = _base(db_session, auth["tenant"]["id"])
    prev = client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 35}).json()
    r = client.post(f"/catalog/margins/{x['audio'].id}/apply", json={"margin_pct": 35, "token": prev["token"]})
    assert r.status_code == 200, r.text
    f = _filas(db_session, x["a"].id)[-1]
    assert f.source == "lote" and d(f.price_net) == Decimal(1000) and f.changed_by == _admin_id(db_session)


def test_importacion_de_fygaro_queda_como_lote(db_session, tmp_path):
    from app.services import fygaro_import
    from tests.test_fygaro_import import _catalog, _images_dir

    t = _tenant(db_session)
    tope = db_session.scalar(select(sa.func.max(ProductPriceHistory.id))) or 0  # lo que ya traia la semilla
    fygaro_import.run(db_session, t, _catalog(), apply=True, images_dir=_images_dir(tmp_path), download_images=False, update_prices=True)
    db_session.commit()
    filas = list(db_session.scalars(select(ProductPriceHistory).where(ProductPriceHistory.id > tope)))
    assert filas and {f.source for f in filas} == {"lote"}
