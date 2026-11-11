"""O1-05: conteos reales. La portada, el menu y el catalogo dicen el mismo numero: el del snapshot (lo que la tienda
lista, sin los ocultos), por categoria y por marca. GET /public/store/{slug} no recalcula disponibilidad."""

import sqlalchemy as sa
from sqlalchemy import select

from app.models import Product, StoreSnapshot, Tenant
from tests.test_tienda_inventario import _cfg, _in, _prod, _publish, _whs

S = "/public/store/crimson"


def test_portada_menu_y_catalogo_dicen_el_mismo_numero(client, auth, db_session):
    _publish(client)
    for p in db_session.scalars(select(Product)):
        p.show_on_web = False  # solo los de esta prueba
    db_session.commit()
    main, _ = _whs(client)
    hay = []
    for i in range(6):
        p = _prod(client, f"CNT-{i}", f"Equipo {i}", price=1000 + i)
        if i < 2:
            _in(client, p["id"], main["id"], 2)
            hay.append(p["id"])
    # con «ocultar agotados» la tienda lista 2 de 6: los conteos no pueden decir 6
    _cfg(client, out_of_stock="ocultar")
    client.get(f"{S}/snapshot")  # el snapshot se arma con la primera visita
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    n = db_session.get(StoreSnapshot, t.id).n_products
    assert n == 2

    engine = db_session.get_bind()
    sentencias: list[str] = []

    def _cuenta(conn, cursor, statement, params, context, executemany):
        sentencias.append(statement)

    client.get(S)  # primera vez despues del rearmado: lee el blob una vez
    sa.event.listen(engine, "before_cursor_execute", _cuenta)
    try:
        home = client.get(S).json()
    finally:
        sa.event.remove(engine, "before_cursor_execute", _cuenta)
    # presupuesto: antes eran 4 (tienda, paginas, categorias y el conteo por categoria); ahora el conteo sale del
    # snapshot: su version (y, solo la primera vez despues de cada rearmado, el blob). En regimen no sube
    assert len(sentencias) <= 4, sentencias
    catalogo = client.get(f"{S}/catalog").json()
    portada = client.get(f"{S}/showcase").json()
    assert home["product_count"] == catalogo["total"] == portada["product_count"] == n
    assert sum(c["count"] for c in home["categories"]) <= n
    assert {b["name"]: b["count"] for b in portada["brands"]} == {b["name"]: b["count"] for b in client.get(f"{S}/snapshot").json()["brands"]}


def test_sin_snapshot_guardado_cuenta_los_publicados(client, auth, db_session):
    _publish(client)
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    assert db_session.get(StoreSnapshot, t.id) is None
    publicados = db_session.scalar(select(sa.func.count()).select_from(Product).where(Product.tenant_id == t.id, Product.active, Product.show_on_web))
    assert client.get(S).json()["product_count"] == publicados
