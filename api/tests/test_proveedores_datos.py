"""Datos de Eurocomp limpios y visibles: el importador Excel viejo ya no escribe Product.supplier_stock (va por el
catalogo de proveedores), un Excel no inactiva ni pisa lo que vino por la API (SiReTT) con dato vigente, el refresco
de existencias cada 6 h deja su error a la vista, Proveedores muestra la hora de existencias y los productos sin
vincular, y "Salud del inventario" lista los productos con proveedor pero sin item vinculado."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Product, Supplier, SupplierItem
from app.providers.sirett import SirettClient, SirettError
from app.services import availability as av
from app.services import supplier_sync as ss
from tests.test_proveedores import LISTA, _imp
from tests.test_session5 import CATALOGO as LISTA_VIEJA
from tests.test_session5 import _xlsx
from tests.test_supplier_sync import CAMARA, UTP, eurocomp  # noqa: F401 (fixture)


def test_importador_excel_viejo_pasa_por_el_catalogo_de_proveedores(client, auth, db_session):
    r = client.post(
        "/import/catalogo",
        params={"commit": True, "supplier": "Eurocomp Costa Rica", "margin": 35},
        files={"file": ("lista.xlsx", _xlsx(LISTA_VIEJA), "application/octet-stream")},
    )
    assert r.status_code == 200 and r.json()["summary"]["nuevo"] == 3
    prod = db_session.scalar(select(Product).where(Product.code == "SG2104"))
    it = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "SG2104"))
    # la existencia llega por el item vinculado (fuente excel, con fecha), no escrita a mano en el producto
    assert it.product_id == prod.id and it.source == "excel" and it.stock == 100 and it.list_date == date.today()
    row = av.one(db_session, prod.tenant_id, prod)
    assert row["supplier_linked"] and row["supplier_qty"] == 100 and row["supplier_source"] == "excel" and row["state"] == "bajo_pedido"

    # el item pasa a venir por la API (dato vigente): un Excel viejo ya no le pisa la existencia
    it.source, it.stock = "api", 7
    prod.supplier_stock = 7
    db_session.commit()
    viejo = [list(x) for x in LISTA_VIEJA]
    viejo[4][3] = 999
    client.post(
        "/import/catalogo", params={"commit": True, "supplier": "Eurocomp Costa Rica"}, files={"file": ("lista.xlsx", _xlsx(viejo), "application/octet-stream")}
    )
    db_session.refresh(it)
    db_session.refresh(prod)
    assert it.stock == 7 and it.source == "api" and prod.supplier_stock == 7


def test_excel_no_inactiva_ni_pisa_items_api_vigentes(client, auth, db_session):
    assert _imp(client, commit=True).status_code == 200
    sup = db_session.scalar(select(Supplier).where(Supplier.name == "Eurocomp"))
    items = {i.code: i for i in db_session.scalars(select(SupplierItem).where(SupplierItem.supplier_id == sup.id))}
    # dos llegaron por la API esta semana; uno por la API hace un mes (ya no esta vigente)
    for code, days in (("SG2104", 0), ("SG2200", 1), ("RC5079", 30)):
        items[code].source, items[code].list_date, items[code].stock = "api", date.today() - timedelta(days=days), 55
    db_session.commit()
    # un Excel que solo trae la camara de 8 MP (y la de 4 MP con otra existencia)
    corta = [r for r in LISTA if not (len(r) > 1 and r[1] in ("SG2200", "RC5079", 1205150, "UP100"))]
    corta = [list(r) for r in corta]
    for r in corta:
        if len(r) > 1 and r[1] == "SG2104":
            r[3] = 1
    res = _imp(client, rows=corta, commit=True).json()
    for i in items.values():
        db_session.refresh(i)
    assert items["SG2104"].active and items["SG2104"].stock == 55 and items["SG2104"].source == "api"  # no la pisa
    assert items["SG2200"].active and items["SG2200"].stock == 55  # no la inactiva
    assert not items["RC5079"].active  # dato de la API viejo: el Excel manda como siempre
    assert not items[str(1205150)].active  # vino por Excel: lo que no viene se inactiva como siempre
    assert res["summary"]["api_conservados"] == 2
    fila = next(x for x in res["result"] if x["code"] == "SG2104")
    assert fila["base"] == "api_vigente" and fila["action"] == "sin_cambios"


def test_refresco_de_existencias_deja_el_error_visible(client, auth, db_session, eurocomp, monkeypatch):  # noqa: F811
    tid = auth["tenant"]["id"]
    # publicado: el refresco de cada 6 h solo consulta lo que se ve en la tienda o esta en documentos abiertos
    prod = Product(tenant_id=tid, code="HIK-1047", name="Cámara 4MP ColorVu", price=Decimal("45000"), supplier_sku="SG2104", show_on_web=True)
    db_session.add(prod)
    db_session.commit()
    ss.sync_all(db_session)

    def falla(self, bid, codes):
        raise SirettError(99, "Eurocomp no respondió")

    monkeypatch.setattr(SirettClient, "items_list", falla)
    assert ss.refresh_linked(db_session)[tid] == {"error": "Eurocomp no respondió", "code": 99}
    sup = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"].lower().startswith("eurocomp"))
    st = sup["api"]
    assert st["status"] == "error" and "refresco de existencias falló" in st["label"] and st["last_stock_error"] == "Eurocomp no respondió"
    assert st["last_error"] == "Eurocomp no respondió" and st["last_ok_at"]  # la hora del catalogo semanal sigue a la vista

    monkeypatch.setattr(SirettClient, "items_list", lambda self, bid, codes: [dict(CAMARA, stock="3.000"), UTP])
    assert ss.refresh_linked(db_session)[tid]["vinculados"] == 1
    st = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"].lower().startswith("eurocomp"))["api"]
    assert st["status"] == "conectado" and st["last_stock_at"] and not st.get("last_stock_error") and not st.get("last_error")


def test_salud_y_proveedores_muestran_lo_sin_vincular(client, auth, db_session):
    tid = auth["tenant"]["id"]
    sup = Supplier(tenant_id=tid, name="Eurocomp Costa Rica")
    db_session.add(sup)
    db_session.flush()
    p = Product(tenant_id=tid, code="EXC-1", name="Cámara del Excel viejo", price=1000, supplier_id=sup.id, supplier_sku="SG9", supplier_stock=40)
    ok = Product(tenant_id=tid, code="EXC-2", name="Cámara vinculada", price=1000, supplier_id=sup.id)
    db_session.add_all([p, ok])
    db_session.flush()
    db_session.add(SupplierItem(tenant_id=tid, supplier_id=sup.id, code="SG10", name="x", product_id=ok.id, stock=2, list_date=date.today(), source="api"))
    db_session.commit()
    h = client.get("/stock/health").json()
    sin = [i for i in h["issues"] if i["kind"] == "sin_vincular"]
    assert [i["ref_id"] for i in sin] == [p.id] and sin[0]["action"] == "vincular" and "40" in sin[0]["detail"]
    row = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["id"] == sup.id)
    assert row["unlinked_products"] == 1
    # su existencia del proveedor no cuenta en ningun lado
    assert av.one(db_session, tid, p)["state"] == "agotado"
    assert [x["id"] for x in client.get("/stock/availability", params={"filtro": "sin_vincular"}).json()["items"]] == [p.id]
    assert [x["id"] for x in client.get("/products", params={"proveedor": "sin_vincular"}).json()["items"]] == [p.id]
