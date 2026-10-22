"""Arranque del inventario local (reunion 21/10, item 11): archivar lo viejo sin existencia de la tienda anterior y
asegurar "Bodega principal" de venta (app/tools/inventario_local.py usa estas mismas funciones)."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, Tenant, Warehouse
from app.services import inventory as invsvc
from app.services import inventory_cleanup as ic
from tests.test_disponibilidad import supplier_item


def _prod(db, tid, code):
    p = Product(tenant_id=tid, code=code, name=f"Producto {code}", price=Decimal("1000"), show_on_web=True)
    db.add(p)
    db.flush()
    return p


def test_archiva_solo_lo_viejo_sin_existencia_y_asegura_bodega_principal(client, auth, db_session):
    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    main = invsvc.default_warehouse(db_session, tid)
    viejo = _prod(db_session, tid, "FY-VIEJO")  # de la tienda anterior, nunca tuvo existencia: se archiva
    con_stock = _prod(db_session, tid, "FY-STOCK")  # de la tienda anterior, con existencia: se deja
    vinculado = _prod(db_session, tid, "FY-EURO")  # de la tienda anterior, vinculado a Eurocomp: lo maneja la sincronizacion
    negativo = _prod(db_session, tid, "FY-NEG")  # negativo: primero el Kardex
    propio = _prod(db_session, tid, "PROPIO-1")  # creado aqui, no de la tienda anterior: nunca se toca
    invsvc.move(db_session, tid, None, con_stock.id, main.id, "entrada", Decimal(2), "INI")
    invsvc.move(db_session, tid, None, negativo.id, main.id, "salida", Decimal(-1), "X")
    supplier_item(db_session, vinculado.id, 0)
    t.settings = {**(t.settings or {}), "fygaro": {"products": {str(i): {"id": p.id, "created": True, "code": p.code} for i, p in enumerate([viejo, con_stock, vinculado, negativo])}}}
    db_session.commit()

    cands = {c["code"]: c for c in ic.candidates(db_session, t)}
    assert set(cands) == {"FY-VIEJO", "FY-STOCK", "FY-EURO", "FY-NEG"}
    assert cands["FY-VIEJO"]["archivar"] and not cands["FY-STOCK"]["archivar"] and not cands["FY-EURO"]["archivar"] and not cands["FY-NEG"]["archivar"]
    assert "Kardex" in cands["FY-NEG"]["motivo"]

    assert ic.archive_legacy(db_session, t) == 1
    db_session.commit()
    db_session.refresh(viejo)
    assert viejo.archived_at is not None and viejo.active is False  # archivado, no borrado (se restaura desde Archivo)
    for p in (con_stock, vinculado, negativo, propio):
        db_session.refresh(p)
        assert p.archived_at is None and p.active
    assert ic.archive_legacy(db_session, t) == 0  # dos veces no hace nada mas

    # Bodega principal de venta y predeterminada (se crea si no existe; si existe, se ajusta)
    w = ic.find_main(db_session, tid)
    if w is not None:
        w.kind, w.is_default = "revision", False
        db_session.commit()
    plan = ic.main_warehouse_plan(db_session, tid)
    assert plan["accion"] in ("crear", "ajustar")
    w = ic.ensure_main_warehouse(db_session, tid)
    db_session.commit()
    assert w.name == "Bodega principal" and w.kind == "venta" and w.active and w.is_default
    assert db_session.scalar(select(Warehouse.id).where(Warehouse.tenant_id == tid, Warehouse.is_default, Warehouse.id != w.id)) is None
    assert ic.main_warehouse_plan(db_session, tid)["accion"] == "ok"
    assert invsvc.default_warehouse(db_session, tid).id == w.id
