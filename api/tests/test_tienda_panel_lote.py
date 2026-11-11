"""O4-05: edicion en lote del catalogo de Mi Tienda, con vista previa identica a lo aplicado y deshacer.

- confirm=false no cambia nada y devuelve el antes y el despues; confirm=true aplica exactamente esas filas, deja una
  sola entrada de bitacora y marca el catalogo una vez; deshacer vuelve exacto (salvo lo que alguien cambio despues).
- «Seleccionar los N del filtro» cambia todos los del filtro en una operacion.
- Precio web escrito FINAL con IVA: la ida y vuelta (1 950 -> neto con 5 decimales -> 1 950,00) coincide con /quote
  y con el comprobante (services/totals). Nunca bajo el margen minimo; otra moneda, se salta.
- Publicar u ocultar en lote: solo el administrador.
La herramienta existe pero las pruebas usan datos ficticios (precios de 2 000 colones o menos): ningun precio real
cambia. Sin reglas automaticas de precio ni ofertas en lote."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models import AuditLog, Product, Tenant
from app.services.store_bulk import gross_from_net, net_from_gross
from app.services.totals import LineIn, compute_document
from tests.test_session4 import _user_with_role
from tests.test_tienda_inventario import _in, _prod, _publish, _whs

SLUG = "/public/store/crimson"


def _solo_estos(db_session):
    for p in db_session.scalars(select(Product)):
        p.show_on_web = False
    db_session.commit()


def _bulk(client, **kw):
    r = client.post("/store/catalog/bulk", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_vista_previa_identica_a_lo_aplicado_y_deshacer_exacto(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    ids = [_prod(client, f"LOT-{i:02d}", f"Equipo de lote {i:02d}", price=1000 + i, web=False)["id"] for i in range(60)]
    tid = auth["tenant"]["id"]
    v0 = (db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0
    pv = _bulk(client, accion="publicar", ids=ids, confirm=False)
    assert pv["n"] == 60 and {f["id"] for f in pv["filas"]} == set(ids)
    assert all(f["antes"] == "No publicado" and f["despues"] == "Publicado" for f in pv["filas"])
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Product).where(Product.id.in_(ids), Product.show_on_web)) == 0  # nada cambio
    n_logs = db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tid))
    r = _bulk(client, accion="publicar", ids=ids, confirm=True)
    assert r["n"] == pv["n"] and r["op"]
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Product).where(Product.id.in_(ids), Product.show_on_web)) == 60
    # una sola entrada de bitacora para toda la operacion, con el antes de cada producto
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tid)) == n_logs + 1
    assert len(db_session.get(AuditLog, r["op"]).before) == 60
    assert ((db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0) > v0  # el catalogo queda marcado (la tienda lo ve)
    # alguien cambia uno despues: deshacer no lo pisa
    client.patch(f"/store/catalog/{ids[0]}", json={"show_on_web": False})
    u = client.post(f"/store/catalog/bulk/{r['op']}/undo").json()
    assert u == {"n": 59, "saltados": 1}
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Product).where(Product.id.in_(ids), Product.show_on_web)) == 0
    assert client.post(f"/store/catalog/bulk/{r['op']}/undo").status_code == 409  # no se deshace dos veces


def test_seleccionar_todos_los_del_filtro(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    for i in range(55):
        _prod(client, f"FIL-{i:02d}", f"Cámara filtro {i:02d}", price=1200)
    r = _bulk(client, accion="fijar", filtro={"q": "cámara filtro"}, confirm=False)
    assert r["n"] == 55  # mas de una pagina (48): el lote toma todos los del filtro
    assert _bulk(client, accion="fijar", filtro={"q": "cámara filtro"}, confirm=True)["n"] == 55
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Product).where(Product.code.like("FIL-%"), Product.web_featured)) == 55


def test_precio_web_con_iva_ida_y_vuelta_igual_que_quote_y_factura(client, auth, db_session):
    _publish(client)
    main, _ = _whs(client)
    # sin costo cargado: el guardian de margen no tiene con que comparar (con costo, ver la prueba de abajo)
    pr = client.post("/products", json={"name": "Equipo con IVA", "code": "IVA-1", "price": 1500, "show_on_web": True}).json()
    _in(client, pr["id"], main["id"], 5)
    pv = _bulk(client, accion="precio_web", valor="1950", ids=[pr["id"]], confirm=False)
    assert pv["n"] == 1 and "1.950" in pv["filas"][0]["despues"]
    assert _bulk(client, accion="precio_web", valor="1950", ids=[pr["id"]], confirm=True)["n"] == 1
    db_session.expire_all()
    p = db_session.get(Product, pr["id"])
    neto = Decimal(str(p.web_price))
    assert neto == Decimal("1725.66372") == net_from_gross(Decimal(1950), Decimal(13))
    assert gross_from_net(neto, Decimal(13)) == Decimal("1950.00")
    assert Decimal(str(p.price)) == Decimal("1500")  # el precio de siempre no se toca
    # /quote (lo que paga el comprador) y el comprobante dicen lo mismo
    q = client.post(f"{SLUG}/quote", json={"lines": [{"product_id": pr["id"], "quantity": 1}]})
    assert q.status_code == 200, q.text
    linea = next(x for x in q.json()["lines"] if not x["shipping"])
    assert Decimal(str(linea["total"])) == Decimal("1950.00")
    doc = compute_document([LineIn(quantity=Decimal(1), unit_price=neto, tax_rate=Decimal(13))])
    assert Decimal(str(doc.total)).quantize(Decimal("0.01")) == Decimal("1950.00")
    # vacio = vuelve al precio de siempre (y se puede deshacer)
    r = _bulk(client, accion="precio_web", valor=None, ids=[pr["id"]], confirm=True)
    db_session.expire_all()
    assert db_session.get(Product, pr["id"]).web_price is None
    client.post(f"/store/catalog/bulk/{r['op']}/undo")
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, pr["id"]).web_price)) == Decimal("1725.66372")


def test_el_lote_respeta_el_guardian_de_margen_y_la_moneda(client, auth, db_session):
    _publish(client)
    caro = client.post(
        "/products", json={"name": "Equipo con costo", "code": "MRG-1", "price": 1900, "cost": 1800, "cost_currency": "CRC", "show_on_web": True}
    ).json()
    usd = client.post("/products", json={"name": "Equipo en dólares", "code": "USD-1", "price": 3, "currency": "USD", "show_on_web": True}).json()
    pv = _bulk(client, accion="precio_web", valor="1500", ids=[caro["id"], usd["id"]], confirm=False)
    motivos = {s["id"]: s["motivo"] for s in pv["saltados"]}
    assert "margen mínimo" in motivos[caro["id"]] and "USD" in motivos[usd["id"]] and pv["n"] == 0
    assert _bulk(client, accion="precio_web", valor="1500", ids=[caro["id"], usd["id"]], confirm=True)["n"] == 0
    db_session.expire_all()
    assert db_session.get(Product, caro["id"]).web_price is None


def test_permisos_del_lote(client, auth, db_session):
    _publish(client)
    pr = _prod(client, "LOT-PERM", "Equipo permisos", price=1500, web=False)
    pw = _user_with_role(db_session, auth["tenant"]["id"], "inventario.lote@ejemplo.com", "inventario")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "inventario.lote@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    for accion in ("publicar", "ocultar"):
        assert client.post("/store/catalog/bulk", json={"accion": accion, "ids": [pr["id"]]}, headers=h).status_code == 403
    assert client.post("/store/catalog/bulk", json={"accion": "fijar", "ids": [pr["id"]], "confirm": True}, headers=h).status_code == 200
    assert client.post("/store/catalog/bulk", json={"accion": "oferta", "ids": [pr["id"]]}).status_code == 422  # no hay ofertas en lote
