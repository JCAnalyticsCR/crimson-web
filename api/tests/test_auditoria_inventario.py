"""Auditoria de inventarios (10/11): lo que se corrigio en el codigo.

- Por ubicacion: los productos archivados o inactivos (los de demostracion) vienen marcados y no suman en los totales
  (Disponibilidad decia 9 y Por ubicacion 10 por NVR-8CH, archivado).
- Un pedido despachado con existencia de bodega cierra la solicitud de compra que seguia en BORRADOR, y deja de
  "prometer" existencia del proveedor (SC-2026-0006 / ORD-000003).
- El estado del proveedor trae la proxima consulta programada, la vigencia permitida y si el dato esta vencido.
- Inventario web: "Requieren accion" solo cuenta lo que no se puede comprar y tiene demanda (clientes esperando,
  pedidos o destacado); un agotado sin demanda es informacion, no una alerta.
Datos ficticios; precios de 2 000 o menos."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Order, Product, PurchaseRequest, Tenant
from app.services import availability as av
from app.services import supplier_sync as ss
from tests.test_disponibilidad import supplier_item
from tests.test_kardex_bajo_pedido import _pagos
from tests.test_tienda_inventario import _buy, _in, _prod, _publish, _whs

D = Decimal


def test_por_ubicacion_marca_los_archivados(client, auth, db_session):
    main, _ = _whs(client)
    a = _prod(client, "AUD-ACT", price=1500)
    b = _prod(client, "AUD-ARCH", price=1500)
    _in(client, a["id"], main["id"], 2)
    _in(client, b["id"], main["id"], 1)
    pb = db_session.get(Product, b["id"])
    pb.active, pb.archived_at = False, datetime.now(UTC)
    db_session.commit()
    levels = {lv["code"]: lv for lv in client.get("/stock").json()["levels"]}
    assert levels["AUD-ACT"]["active"] is True
    assert levels["AUD-ARCH"]["active"] is False


def test_despacho_con_bodega_cierra_la_solicitud_en_borrador(client, auth, db_session):
    _publish(client)
    _pagos(client)
    main, _ = _whs(client)
    p = _prod(client, "AUD-SENSOR", "Sensor de prueba", price=1800)
    supplier_item(db_session, p["id"], 5, code="EC-AUD-SENSOR")
    _in(client, p["id"], main["id"], 1)
    o = db_session.scalar(select(Order).where(Order.number == _buy(client, [{"product_id": p["id"], "quantity": 3}]).json()["number"]))
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "pagado", "payment_ref": "SINPE 1"}).status_code == 200
    req = db_session.scalar(select(PurchaseRequest).where(PurchaseRequest.order_id == o.id))
    assert req is not None and req.status == "borrador"
    t = db_session.get(Tenant, o.tenant_id)
    assert av._web_requests(db_session, t.id, [p["id"]]).get(p["id"], 0) == 2  # promete 2 del proveedor
    # llega existencia por otro lado y el pedido se despacha completo desde bodega
    _in(client, p["id"], main["id"], 2)
    assert client.post(f"/store-orders/{o.id}/status", json={"status": "enviado", "tracking_number": "G-1"}).status_code == 200
    db_session.expire_all()
    req = db_session.get(PurchaseRequest, req.id)
    assert req.status == "cancelada" and "existencia de bodega" in (req.notes or "")
    assert av._web_requests(db_session, t.id, [p["id"]]).get(p["id"], 0) == 0


def test_proxima_consulta_y_vigencia():
    # sin consulta previa: la proxima pasada del beat (03:17 CR = 09:17 UTC)
    assert ss.next_run_at(datetime(2026, 11, 11, 16, 0, tzinfo=UTC)) == datetime(2026, 11, 12, 9, 17, tzinfo=UTC)
    assert ss.next_run_at(datetime(2026, 11, 16, 8, 0, tzinfo=UTC)) == datetime(2026, 11, 16, 9, 17, tzinfo=UTC)


def test_catalogo_completo_cada_tres_dias(db_session, auth, monkeypatch):
    """13/11 (audio de Eurocomp): el beat pasa a diario a las 03:17 y la consulta global solo corre cada 3 dias."""
    t = db_session.get(Tenant, auth["tenant"]["id"])
    ultima = datetime(2026, 11, 13, 9, 17, tzinfo=UTC)  # 13/11 03:17 CR
    ss._save_state(t, last_global_at=ultima)
    assert ss.every_days(t) == 3
    assert not ss.due(t, datetime(2026, 11, 14, 9, 17, tzinfo=UTC))  # al dia siguiente no
    assert not ss.due(t, datetime(2026, 11, 15, 9, 17, tzinfo=UTC))
    assert ss.due(t, datetime(2026, 11, 16, 9, 17, tzinfo=UTC))  # a los 3 dias si
    assert ss.next_run_at(datetime(2026, 11, 13, 12, 0, tzinfo=UTC), t) == datetime(2026, 11, 16, 9, 17, tzinfo=UTC)
    # ajustable por empresa
    t.settings = {**(t.settings or {}), "supplier_sync_every_days": 1}
    assert ss.due(t, datetime(2026, 11, 14, 9, 17, tzinfo=UTC))
    # la pasada programada no llama a Eurocomp si no toca
    t.settings = {**(t.settings or {}), "supplier_sync_every_days": 3}
    ss._save_state(t, last_global_at=datetime.now(UTC))  # consulto hace un rato
    db_session.commit()
    monkeypatch.setattr(ss, "targets", lambda db: [t])
    monkeypatch.setattr(ss, "fetch", lambda client: (_ for _ in ()).throw(AssertionError("no debia consultar")))
    out = ss.sync_all(db_session, client=object(), scheduled=True)
    assert "todavía no toca" in out.get("skipped", "")


def test_dato_del_proveedor_vencido(db_session, auth):
    t = db_session.get(Tenant, auth["tenant"]["id"])
    ss._save_state(t, last_ok_at=datetime(2026, 10, 27, 9, 17, tzinfo=UTC))
    f = ss.freshness(t, now=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    assert f["stale"] is True and f["age_hours"] > f["max_age_hours"]
    f = ss.freshness(t, now=datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    assert f["stale"] is False


def test_requieren_accion_solo_con_demanda(client, auth, db_session):
    _publish(client)
    a = _prod(client, "AUD-AGOT", "Agotado sin demanda", price=1200)
    b = _prod(client, "AUD-DEST", "Agotado destacado", price=1200)
    pb = db_session.get(Product, b["id"])
    pb.web_featured = True
    db_session.commit()
    inv = client.get("/store/inventory").json()
    rows = {x["id"]: x for x in inv["items"]}
    assert rows[a["id"]]["estado"] == "agotado" and rows[a["id"]]["alerta"] is False
    assert rows[b["id"]]["alerta"] is True
    assert [x["id"] for x in client.get("/store/inventory", params={"estado": "alertas"}).json()["items"]] == [b["id"]]


def test_filtros_sin_categorias_vacias_y_marca_oficial(client, auth, db_session):
    from app.models import Category

    tid = auth["tenant"]["id"]
    fam = Category(tenant_id=tid, name="Familia AUD")
    db_session.add(fam)
    db_session.flush()
    sub = Category(tenant_id=tid, name="Sub AUD", parent_id=fam.id)
    vacia = Category(tenant_id=tid, name="Vacia AUD")
    db_session.add_all([sub, vacia])
    db_session.flush()
    a = _prod(client, "AUD-TPL1", price=1500)
    b = _prod(client, "AUD-TPL2", price=1500)
    for pid, br in ((a["id"], "Tp Link"), (b["id"], "TP-Link")):
        pr = db_session.get(Product, pid)
        pr.brand, pr.category_id = br, sub.id
    db_session.commit()
    f = client.get("/products/meta/filters").json()
    names = [c["name"] for c in f["categories"]]
    assert "Familia AUD › Sub AUD" in names and "Familia AUD" in names and "Vacia AUD" not in names
    assert f["brands"].count("TP-Link") == 1 and "Tp Link" not in f["brands"]
    got = {x["code"] for x in client.get("/products", params={"brand": "TP-Link", "limit": 50}).json()["items"]}
    assert {"AUD-TPL1", "AUD-TPL2"} <= got
