"""Precio de aliado (18:39-19:16: costo 100 -> aliado 105 -> publico 115) e inventario solo con cantidades
(aliado_final §12.3, P1-P7)."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.aliados_helpers import escenario, interno, keys_rec


def _producto(client, code, **kw) -> int:
    body = {"name": f"Producto {code}", "code": code, "price": 115000, "currency": "CRC", **kw}
    r = client.post("/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ficha(client, h, pid):
    r = client.get(f"/aliado/catalogo/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_costo_100_aliado_105_publico_115(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    vend = interno(client, db_session, s.tid, "vend.precios@ejemplo.com", "ventas")
    pv = client.get(f"/products/{s.prod}", headers=vend).json()
    assert Decimal(str(pv["price"])) == 115000 and pv["ally_price"] is None and pv["cost"] is None
    adm = client.get(f"/products/{s.prod}").json()
    assert Decimal(str(adm["ally_price"])) == 105000 and adm["ally_price_source"] == "recargo"
    f = _ficha(client, s.a_jefe, s.prod)
    assert Decimal(str(f["precio_aliado"])) == 105000 and Decimal(str(f["precio_publico"])) == 115000
    assert Decimal(str(f["precio_aliado_con_iva"])) == Decimal("118650") and f["precio_aliado_estado"] == "ok"
    assert not {"cost", "cost_currency", "margin_pct", "markup_pct", "supplier_id"} & keys_rec(f)
    lista = client.get("/aliado/catalogo?q=demo aliado", headers=s.a_jefe).json()
    assert lista["items"][0]["id"] == s.prod and lista["items"][0]["image"] is None and "next_cursor" in lista


def test_precio_fijo_general_y_por_aliado(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    r = client.put("/allies/prices", json=[{"product_id": s.prod, "price": 104000}, {"product_id": s.prod, "ally_company_id": s.A, "price": 103000}])
    assert r.status_code == 200, r.text
    assert Decimal(str(_ficha(client, s.a_jefe, s.prod)["precio_aliado"])) == 103000
    assert Decimal(str(_ficha(client, s.b_jefe, s.prod)["precio_aliado"])) == 104000
    prev = client.get(f"/allies/price-preview?product_id={s.prod}&ally_company_id={s.A}").json()
    assert prev["fuente"] == "precio_aliado" and Decimal(str(prev["precio_aliado"])) == 103000
    assert Decimal(str(client.get(f"/products/{s.prod}").json()["ally_price"])) == 104000  # adentro: el general


def test_un_solo_precio_general_por_producto(client, auth, db_session):
    from app.models import AllyProductPrice

    s = escenario(client, dict(client.headers), db_session)
    for precio in (104000, 102000):
        assert client.put("/allies/prices", json=[{"product_id": s.prod, "price": precio}]).status_code == 200
    filas = db_session.scalars(select(AllyProductPrice).where(AllyProductPrice.product_id == s.prod)).all()
    assert len(filas) == 1 and Decimal(str(filas[0].price)) == 102000
    assert len(client.get(f"/allies/prices?product_id={s.prod}").json()) == 1
    db_session.add(AllyProductPrice(tenant_id=s.tid, product_id=s.prod, ally_company_id=None, price=1, currency="CRC"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_recargo_propio_y_tope(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    assert client.put(f"/allies/{s.A}", json={"markup_pct": 3}).status_code == 200
    assert Decimal(str(_ficha(client, s.a_jefe, s.prod)["precio_aliado"])) == 103000
    assert client.put(f"/allies/{s.A}", json={"markup_pct": 50}).status_code == 200
    f = _ficha(client, s.a_jefe, s.prod)
    assert Decimal(str(f["precio_aliado"])) == 115000 and f["precio_aliado_estado"] == "igual_al_publico"
    sin = _producto(client, "SIN-COSTO")
    f = _ficha(client, s.a_jefe, sin)
    assert f["precio_aliado"] is None and f["precio_aliado_estado"] == "a_consultar"
    # el recargo lo pone quien ve costos
    vend = interno(client, db_session, s.tid, "vend.recargo@ejemplo.com", "ventas")
    assert client.put(f"/allies/{s.A}", json={"markup_pct": 1}, headers=vend).status_code == 403


def test_redondeo_crc_a_diez_y_usd(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    crc = _producto(client, "RED-CRC", price=5000, cost=1003, cost_currency="CRC")
    usd = _producto(client, "RED-USD", price=200, currency="USD", cost=100, cost_currency="USD")
    assert Decimal(str(_ficha(client, s.a_jefe, crc)["precio_aliado"])) == 1060
    assert Decimal(str(_ficha(client, s.a_jefe, usd)["precio_aliado"])) == Decimal("105.00")


def test_inventario_solo_cantidades(client, auth, db_session):
    from app.models import StockReservation, Warehouse
    from app.services import inventory as invsvc

    s = escenario(client, dict(client.headers), db_session)
    bodegas = db_session.scalars(select(Warehouse).where(Warehouse.tenant_id == s.tid).order_by(Warehouse.id)).all()
    w1, w2 = bodegas[0], bodegas[1]
    inactiva = Warehouse(tenant_id=s.tid, name="Bodega cerrada", active=False)
    db_session.add(inactiva)
    db_session.flush()
    for w, q in ((w1, 10), (w2, 4), (inactiva, 7)):
        invsvc.move(db_session, s.tid, s.admin_id, s.prod, w.id, "entrada", Decimal(q), unit_cost=100000)
    db_session.add(StockReservation(tenant_id=s.tid, product_id=s.prod, warehouse_id=w1.id, quantity=3, consumed_quantity=0, status="confirmada"))
    servicio = _producto(client, "SRV-01", item_type="servicio", price=1000)
    db_session.commit()
    rows = client.get("/aliado/inventario?q=", headers=s.a_jefe).json()["rows"]
    fila = next(r for r in rows if r["product_id"] == s.prod)
    assert Decimal(str(fila["disponible_total"])) == 11  # 10 - 3 reservadas + 4; la bodega inactiva no cuenta
    assert {b["nombre"] for b in fila["bodegas"]} == {w1.name, w2.name}
    assert servicio not in {r["product_id"] for r in rows}
    llaves = keys_rec(rows)
    assert not {k for k in llaves if any(x in k for x in ("cost", "price", "value", "amount"))}
    assert client.get("/stock", headers=s.a_jefe).status_code == 403


def test_el_porcentaje_nunca_sale(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    client.put(f"/allies/{s.A}", json={"markup_pct": 7})
    for url in ("/aliado/catalogo", f"/aliado/catalogo/{s.prod}", "/aliado/inicio", "/aliado/catalogo/meta", "/aliado/suscripcion"):
        llaves = keys_rec(client.get(url, headers=s.a_jefe).json())
        assert not {k for k in llaves if "markup" in k or "recargo" in k}, url
    assert client.get("/allies/config", headers=s.a_jefe).status_code == 403
    assert client.get("/allies/config").json()["markup_pct"] is not None
