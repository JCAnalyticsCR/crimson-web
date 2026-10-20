"""La funcion unica de disponibilidad (services/availability): propio en bodegas de venta (nunca las de garantias),
reservas, proveedor solo vinculado y con fecha (fresco / viejo / sin fecha / Excel sin vincular), servicio, max_qty
(menos lo ya prometido a pedidos web) y lo que ve cada audiencia (el publico y el aliado nunca ven el proveedor ni
costos)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Product, PurchaseRequest, PurchaseRequestLine, Supplier, SupplierItem, Tenant, Warehouse
from app.services import availability as av
from app.services import inventory as invsvc
from app.services import reservations as rsv

D = Decimal
EUROCOMP = "Eurocomp Costa Rica"


# ---------- utilidades compartidas (las usan otras pruebas de inventario y tienda) ----------
def supplier_item(db, pid, qty, *, hours_ago=0, days_ago=0, source="api", supplier=EUROCOMP, code=None, cost=50, dated=True, active=True):
    """Item del catalogo de proveedores VINCULADO al producto, como lo deja sync_product (o SiReTT): es lo unico que
    la disponibilidad cuenta como existencia del proveedor. dated=False: sin fecha (dato viejo)."""
    p = db.get(Product, pid)
    sup = db.scalar(select(Supplier).where(Supplier.tenant_id == p.tenant_id, Supplier.name == supplier))
    if sup is None:
        sup = Supplier(tenant_id=p.tenant_id, name=supplier)
        db.add(sup)
        db.flush()
    it = db.scalar(select(SupplierItem).where(SupplierItem.product_id == pid, SupplierItem.supplier_id == sup.id))
    if it is None:
        it = SupplierItem(tenant_id=p.tenant_id, supplier_id=sup.id, code=code or f"EC-{pid}", name=p.name, product_id=pid, link_method="sku")
        db.add(it)
    when = datetime.now(UTC) - timedelta(days=days_ago, hours=hours_ago)
    it.stock, it.cost, it.currency, it.source, it.active = qty, cost, "USD", source, active
    it.list_date = when.date() if dated else None
    db.flush()
    p.supplier_id, p.supplier_sku = sup.id, it.code
    p.supplier_stock = qty
    p.supplier_updated_at = when if dated else None
    db.commit()
    return it


def _tid(db):
    return db.scalar(select(Tenant)).id


def _prod(db, code, item_type="producto", **kw):
    t = _tid(db)
    p = Product(tenant_id=t, code=code, name=kw.pop("name", f"Producto {code}"), item_type=item_type, price=kw.pop("price", 10000), **kw)
    db.add(p)
    db.commit()
    return p


def _whs(db):
    t = _tid(db)
    main = invsvc.default_warehouse(db, t)
    other = db.scalar(select(Warehouse).where(Warehouse.tenant_id == t, Warehouse.id != main.id, Warehouse.kind == "venta"))
    return t, main, other


def _one(db, p, audience="interno", **kw):
    return av.one(db, _tid(db), p, audience=audience, **kw)


# ---------- propio ----------
def test_propio_cuenta_solo_bodegas_de_venta_y_resta_reservas(db_session):
    t, main, other = _whs(db_session)
    p = _prod(db_session, "AV-PROPIO")
    invsvc.move(db_session, t, None, p.id, main.id, "entrada", D(5))
    invsvc.move(db_session, t, None, p.id, other.id, "entrada", D(2))
    # equipos de clientes en garantia: revision y taller del distribuidor nunca se venden
    rev = invsvc.special_warehouse(db_session, t, "revision")
    dist = invsvc.special_warehouse(db_session, t, "proveedor")
    invsvc.move(db_session, t, None, p.id, rev.id, "entrada", D(7))
    invsvc.move(db_session, t, None, p.id, dist.id, "entrada", D(3))
    db_session.commit()
    r = _one(db_session, p)
    assert r["own_on_hand"] == 7 and r["own_free"] == 7 and r["state"] == "en_bodega"
    # una reserva confirmada y vigente compromete; una vencida no
    res = rsv.create(db_session, t, None, p.id, D(3), main.id)
    db_session.commit()
    rsv.confirm(db_session, res, None)
    r = _one(db_session, p)
    assert r["own_free"] == 4 and r["own_reserved"] == 3 and r["max_qty"] == 4
    res.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert _one(db_session, p)["own_free"] == 7
    # por bodega: solo las de venta
    r = _one(db_session, p, by_warehouse=True)
    assert {b["nombre"] for b in r["bodegas"]} == {main.name, other.name}
    # todo comprometido: agotado (sin proveedor)
    q = invsvc.move(db_session, t, None, p.id, main.id, "salida", D(-5))
    q2 = invsvc.move(db_session, t, None, p.id, other.id, "salida", D(-2))
    db_session.commit()
    assert q and q2 and _one(db_session, p)["state"] == "agotado"


# ---------- proveedor ----------
def test_proveedor_fresco_viejo_sin_fecha_y_excel_sin_vincular(db_session):
    fresco = _prod(db_session, "AV-FRESCO")
    viejo = _prod(db_session, "AV-VIEJO")
    sin_fecha = _prod(db_session, "AV-SINFECHA")
    excel = _prod(db_session, "AV-EXCEL")
    supplier_item(db_session, fresco.id, 12, hours_ago=5)
    supplier_item(db_session, viejo.id, 12, hours_ago=60)  # mas de 48 h
    supplier_item(db_session, sin_fecha.id, 12, dated=False)
    # lo que dejaba el importador Excel viejo: existencia en el producto, sin item vinculado
    excel.supplier_stock, excel.supplier_updated_at = 40, datetime.now(UTC)
    db_session.commit()
    rows = av.availability_many(db_session, _tid(db_session), [fresco, viejo, sin_fecha, excel], audience="interno")
    f, v, s, e = rows[fresco.id], rows[viejo.id], rows[sin_fecha.id], rows[excel.id]
    assert f["state"] == "bajo_pedido" and f["supplier_fresh"] and f["supplier_qty"] == 12 and f["max_qty"] == 12
    assert f["supplier_name"] == EUROCOMP and f["supplier_source"] == "api" and "actualizado hace 5 h" in f["label"]
    assert v["state"] == "consultar" and not v["supplier_fresh"] and v["max_qty"] == 0 and "dato viejo" in v["label"]
    assert s["state"] == "consultar" and s["supplier_updated_at"] is None  # sin fecha = viejo
    assert e["state"] == "agotado" and not e["supplier_linked"] and e["supplier_unlinked_qty"] == 40 and e["supplier_qty"] is None
    # el umbral se configura en Ajustes de la tienda (horas)
    t = db_session.scalar(select(Tenant))
    t.settings = {**(t.settings or {}), "store_inventory": {"supplier_max_age_hours": 72}}
    db_session.commit()
    assert _one(db_session, viejo)["state"] == "bajo_pedido"
    # un item inactivo (ya no viene en la lista) no cuenta
    supplier_item(db_session, fresco.id, 12, hours_ago=1, active=False)
    assert _one(db_session, fresco)["state"] == "agotado"


def test_max_qty_resta_lo_prometido_a_pedidos_web(db_session):
    t, main, _ = _whs(db_session)
    p = _prod(db_session, "AV-MAX")
    invsvc.move(db_session, t, None, p.id, main.id, "entrada", D(2))
    supplier_item(db_session, p.id, 5, hours_ago=1)
    assert _one(db_session, p)["max_qty"] == 7
    req = PurchaseRequest(tenant_id=t, number="SC-T1", status="borrador", reason="pedido_web")
    req.lines.append(PurchaseRequestLine(product_id=p.id, name=p.name, quantity=3, unit_cost=0))
    db_session.add(req)
    db_session.commit()
    r = _one(db_session, p)
    assert r["supplier_committed"] == 3 and r["supplier_available"] == 2 and r["max_qty"] == 4
    req.status = "solicitada"
    db_session.commit()
    assert _one(db_session, p)["max_qty"] == 4  # pedida al proveedor: sigue prometida
    req.status = "recibida"  # ya entro a bodega (el kardex la cuenta): deja de restar al proveedor
    db_session.commit()
    assert _one(db_session, p)["max_qty"] == 7
    # sin propio y todo prometido: no se vende bajo pedido (consultar)
    q = _prod(db_session, "AV-MAX2")
    supplier_item(db_session, q.id, 2, hours_ago=1)
    req2 = PurchaseRequest(tenant_id=t, number="SC-T2", status="borrador", reason="pedido_web")
    req2.lines.append(PurchaseRequestLine(product_id=q.id, name=q.name, quantity=2, unit_cost=0))
    db_session.add(req2)
    db_session.commit()
    assert _one(db_session, q)["state"] == "consultar" and _one(db_session, q)["max_qty"] == 0


def test_servicio_y_bajo_pedido_apagado(db_session):
    s = _prod(db_session, "AV-SRV", item_type="servicio")
    r = _one(db_session, s, audience="publico")
    assert r["state"] == "servicio" and r["disponible"] and r["accion"] == "comprar" and r["max_qty"] is None
    p = _prod(db_session, "AV-BPOFF")
    supplier_item(db_session, p.id, 9, hours_ago=1)
    t = db_session.scalar(select(Tenant))
    t.settings = {**(t.settings or {}), "store_inventory": {"backorder_enabled": False, "backorder_lead": "8 días"}}
    db_session.commit()
    r = _one(db_session, p, audience="publico")
    assert r["state"] == "consultar" and not r["disponible"] and r["accion"] == "whatsapp"
    t.settings = {**(t.settings or {}), "store_inventory": {"backorder_lead": "8 días"}}
    db_session.commit()
    r = _one(db_session, p, audience="publico")
    assert r["state"] == "bajo_pedido" and r["label"] == "Bajo pedido · entrega en 8 días" and r["plazo"] == "8 días"


# ---------- audiencias ----------
PROHIBIDAS_PUBLICO = {"supplier_name", "supplier_qty", "supplier_source", "supplier_updated_at", "proveedor", "propio", "cost", "costo"}


def test_audiencias_publico_y_aliado_sin_proveedor_ni_costo(db_session):
    t, main, _ = _whs(db_session)
    p = _prod(db_session, "AV-AUD", cost=80)
    invsvc.move(db_session, t, None, p.id, main.id, "entrada", D(1))
    supplier_item(db_session, p.id, 6, hours_ago=2)
    pub = _one(db_session, p, audience="publico")
    ali = _one(db_session, p, audience="aliado", by_warehouse=True)
    interno = _one(db_session, p, audience="interno")
    for row in (pub, ali):
        assert not ({k for k in row if "supplier" in k or "cost" in k or "price" in k} | (PROHIBIDAS_PUBLICO & set(row)))
        assert EUROCOMP not in str(row) and "Eurocomp" not in str(row)
    assert pub["state"] == ali["state"] == interno["state"] == "en_bodega"
    assert pub["label"] == "En bodega · 1 disponible" and pub["cantidad"] == 1 and pub["max_qty"] == 7
    assert ali["en_bodega"] == 1 and ali["bajo_pedido_cantidad"] == 6 and ali["actualizado_al"] and ali["label"] == "En bodega: 1 · bajo pedido: 6"
    assert not {k for k in ali["bodegas"][0] if "warehouse" in k}
    assert interno["label"].startswith("Propio: 1 · Eurocomp Costa Rica: 6 · actualizado hace 2 h")
    assert "cost" not in str(sorted(interno))  # ni el panel recibe costos de esta funcion
    # la tienda en modo umbral no publica cantidades (ni max_qty)
    tt = db_session.scalar(select(Tenant))
    tt.settings = {**(tt.settings or {}), "store_inventory": {"quantity_display": "umbral", "low_threshold": 3}}
    db_session.commit()
    pub = _one(db_session, p, audience="publico")
    assert pub["label"] == "¡Últimas unidades!" and pub["cantidad"] is None and pub["max_qty"] is None and pub["ultimas"]


def test_un_solo_diccionario_de_estados():
    assert set(av.STATES) == {"en_bodega", "bajo_pedido", "consultar", "agotado", "servicio"}
    assert set(av.SELLABLE_STATES) == {"en_bodega", "bajo_pedido", "servicio"}
    from app.services import store_inventory as sinv

    assert not hasattr(sinv, "LEGACY_STATE")  # el diccionario viejo ya no existe
    assert sinv.settings_of is av.settings_of and sinv.DEFAULTS is av.DEFAULTS  # reexportados, no duplicados
    assert av.qty_text(D("3.000")) == "3" and av.qty_text(D("2.50")) == "2,5"


def test_stock_bajo_no_marca_lo_que_se_vende_bajo_pedido(db_session):
    t, main, _ = _whs(db_session)
    bajo_pedido = _prod(db_session, "AV-MIN-BP", min_stock=3)
    propio = _prod(db_session, "AV-MIN-PROP", min_stock=3)
    supplier_item(db_session, bajo_pedido.id, 20, hours_ago=1)
    low = {x["product_id"] for x in invsvc.low_stock(db_session, t)}
    assert propio.id in low and bajo_pedido.id not in low  # nunca se tuvo en bodega: se vende bajo pedido
    # una vez que Crimson lo tuvo en bodega, el minimo vuelve a importar
    invsvc.move(db_session, t, None, bajo_pedido.id, main.id, "entrada", D(1))
    db_session.commit()
    assert bajo_pedido.id in {x["product_id"] for x in invsvc.low_stock(db_session, t)}
