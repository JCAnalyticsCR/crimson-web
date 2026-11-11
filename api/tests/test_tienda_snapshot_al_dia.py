"""O1-13: snapshot al dia. La lista (snapshot) dice lo mismo que la ficha (/item): mismo precio y mismo estado; un
movimiento de inventario o una reserva lo dejan viejo sin tocar la fila de la empresa (el checkout no actualiza
tenant); un 304 nunca sirve existencias viejas y el ETag debil que agrega el borde se acepta."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import select

from app.models import Product, StoreSnapshot, Tenant
from tests.test_disponibilidad import supplier_item
from tests.test_tienda_inventario import _buy, _in, _prod, _publish, _whs

S = "/public/store/crimson"


def _snap(client):
    r = client.get(f"{S}/snapshot")
    assert r.status_code == 200, r.text
    return r.json()


def _tenant(db):
    return db.scalar(select(Tenant).where(Tenant.slug == "crimson"))


def _viejo(db, segundos=60):
    """el ultimo armado fue hace un rato (fuera de los 20 s que juntan cambios seguidos)"""
    db.expire_all()
    row = db.get(StoreSnapshot, _tenant(db).id)
    row.built_at = datetime.now(UTC) - timedelta(seconds=segundos)
    db.commit()


def test_lista_y_ficha_dicen_el_mismo_precio_y_estado(client, auth, db_session):
    """Paridad en una muestra variada: con existencia propia, del proveedor vigente y vencido, sin nada, con precio web,
    retenido por margen y sin precio."""
    _publish(client)
    main, _ = _whs(client)
    for p in db_session.scalars(select(Product)):
        p.show_on_web = False  # solo los de esta prueba
    db_session.commit()
    ids = []
    for i in range(50):
        p = _prod(client, f"PAR-{i:02d}", f"Equipo de prueba {i}", price=[1000, 1500, 0, 1200, 900][i % 5])
        ids.append(p["id"])
        if i % 5 == 0:
            _in(client, p["id"], main["id"], 1 + i % 3)
        elif i % 5 == 1:
            supplier_item(db_session, p["id"], 4, hours_ago=2)
        elif i % 5 == 3:
            supplier_item(db_session, p["id"], 4, hours_ago=400)  # dato vencido: «por confirmar»
    prods = {p.id: p for p in db_session.scalars(select(Product).where(Product.id.in_(ids)))}
    prods[ids[3]].web_price = Decimal("1100")  # precio web distinto al de siempre
    prods[ids[8]].margin_hold = True  # retenido por margen: «precio a consultar» en ficha y lista
    prods[ids[13]].web_price = Decimal("800")
    db_session.commit()
    snap = _snap(client)
    assert snap["v"] == 2
    lista = {x["id"]: x for x in snap["products"]}
    for pid in ids:
        ficha = client.get(f"{S}/item/x-{pid}").json()
        assert pid in lista, pid
        assert Decimal(str(lista[pid]["display_price"])) == Decimal(str(ficha["display_price"])), (pid, lista[pid]["display_price"], ficha["display_price"])
        assert lista[pid]["availability"]["state"] == ficha["availability"]["state"], pid
        assert "compare_display_price" not in lista[pid]  # sin historial propio no hay tachado
    assert lista[ids[8]]["display_price"] == 0


def test_reservar_la_ultima_unidad_deja_el_snapshot_viejo_sin_tocar_la_empresa(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "ULT-1", "Cámara de prueba", price=1500)
    _in(client, p["id"], main["id"], 1)
    estado = lambda: next(x for x in _snap(client)["products"] if x["id"] == p["id"])["availability"]["state"]  # noqa: E731
    assert estado() == "en_bodega"
    t = _tenant(db_session)
    v0 = db_session.get(StoreSnapshot, t.id).stock_v

    engine = db_session.get_bind()
    sentencias: list[str] = []

    def _cuenta(conn, cursor, statement, params, context, executemany):
        sentencias.append(" ".join(statement.split()).lower())

    sa.event.listen(engine, "before_cursor_execute", _cuenta)
    try:
        r = _buy(client, [{"product_id": p["id"], "quantity": 1}])
    finally:
        sa.event.remove(engine, "before_cursor_execute", _cuenta)
    assert r.status_code == 201, r.text
    # el checkout no escribe la fila de la empresa (ni catalog_v ni nada): la marca va a la fila del snapshot
    assert not [x for x in sentencias if x.startswith("update tenant ")], [x for x in sentencias if x.startswith("update tenant ")]
    db_session.expire_all()
    row = db_session.get(StoreSnapshot, t.id)
    assert row.stock_v > v0 and row.dirty_at is not None
    # dentro de los 20 s se sirve el que hay; despues, el siguiente pedido lo rearma con la reserva
    _viejo(db_session)
    assert estado() != "en_bodega"


def test_un_movimiento_de_inventario_deja_el_snapshot_viejo(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "MOV-1", "Switch de prueba", price=1200)
    assert next(x for x in _snap(client)["products"] if x["id"] == p["id"])["availability"]["state"] != "en_bodega"
    _in(client, p["id"], main["id"], 3)
    _viejo(db_session)
    assert next(x for x in _snap(client)["products"] if x["id"] == p["id"])["availability"]["state"] == "en_bodega"


def test_etag_debil_y_sin_304_con_existencias_viejas(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    p = _prod(client, "ETAG-1", "Grabador de prueba", price=2000)
    for ruta in ("/showcase", "/catalog"):
        r = client.get(S + ruta)
        etag = r.headers["etag"]
        assert client.get(S + ruta, headers={"If-None-Match": f"W/{etag}"}).status_code == 304, ruta
        assert client.get(S + ruta, headers={"If-None-Match": etag}).status_code == 304, ruta
    r = client.get(f"{S}/snapshot")
    etag_snap = r.headers["etag"]
    assert client.get(f"{S}/snapshot", headers={"If-None-Match": f"W/{etag_snap}"}).status_code == 304
    viejos = {ruta: client.get(S + ruta).headers["etag"] for ruta in ("/showcase", "/catalog")}
    # entra mercaderia: el ETag de la vitrina cambia (lleva la version del inventario) y el 304 ya no aplica
    _in(client, p["id"], main["id"], 2)
    for ruta, etag in viejos.items():
        assert client.get(S + ruta, headers={"If-None-Match": f"W/{etag}"}).status_code == 200, ruta
    _viejo(db_session)
    r = client.get(f"{S}/snapshot", headers={"If-None-Match": f"W/{etag_snap}"})
    assert r.status_code == 200 and r.headers["etag"] != etag_snap
    assert any(x["id"] == p["id"] for x in r.json()["products"])
