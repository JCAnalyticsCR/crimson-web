"""Catalogo de Eurocomp por SiReTT: solo entra lo de Crimson, se aplica con el mismo motor que la lista en Excel,
completa CABYS y fotos sin tocar la existencia propia, y una respuesta sospechosa o el limite por hora no rompen nada.
Eurocomp es falso (sin red): los articulos tienen la forma real de items_Row del WSDL."""

from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import settings
from app.models import Product, StockMovement, Supplier, SupplierItem
from app.providers.sirett import SirettClient, SirettError, image_url
from app.services import supplier_sync as ss


def item(code, desc, fam, sub, marca="", precio="10.00", stock="5.000", cabys="4526100000200", img="upload/items/2026/8/1_2.jpg", **kw):
    return {
        "codigo": code, "descripcion": desc, "desc_corta": "", "FamiliaPadre": fam, "familia": sub, "marca": marca, "modelo": kw.get("modelo", ""),
        "precio": precio, "currency_code": "USD", "stock": stock, "cod_hacienda": cabys, "image_url": img, "image_url_1": kw.get("img1", ""),
        "caracteristicas": kw.get("caracteristicas", ""),
    }  # fmt: skip


CAMARA = item("SG2104", "CAMARA IP HIKVISION DS-2CD1047G3-LIU 4MP COLORVU", "SEGURIDAD", "Camaras", "HIKVISION", "48.50", modelo="DS-2CD1047G3-LIU", img1="upload/items/x.jpg")
BALUN = item("AC1104", "ADAPTADOR VIDEO BALUN HIKVISION DS-1H18-A", "ACCESORIOS", "Adaptadores", "HIKVISION", "1.82", "16.000")
UTP = item("CB0001", "CABLE UTP CAT6 NEXXT 305M", "ACCESORIOS", "Cables", "NEXXT", "120.00")
HDMI = item("CB0002", "CABLE ARGOM HDMI 6 FT", "ACCESORIOS", "Cables", "ARGOM", "3.00")
PURPLE = item("HD0001", "HD INTERNO 4TB WESTERN DIGITAL PURPLE WD40PURX", "ALMACENAMIENTO", "Discos Duros Internos", "WESTERN DIGITAL", "95.00")
BLUE = item("HD0002", "HD INTERNO 1TB WESTERN DIGITAL BLUE WD10EZEX", "ALMACENAMIENTO", "Discos Duros Internos", "WESTERN DIGITAL", "40.00")
LAPTOP = item("LP0001", "LAPTOP ASUS VIVOBOOK 15", "LAPTOPS", "Español", "ASUS", "500.00")
ENCHUFE = item("SH0001", "ENCHUFE INTELIGENTE EZVIZ T30", "SMART HOME", "Corriente Inteligente", "EZVIZ", "12.00")
UPS = item("UP0001", "UPS APC BACK-UPS 600VA", "RESPALDO ELÉCTRICO", "UPS", "APC", "70.00")
POWERBANK = item("PB0001", "POWER BANK XIAOMI 10000", "RESPALDO ELÉCTRICO", "Power Banks", "XIAOMI", "15.00")
CATALOGO = [CAMARA, BALUN, UTP, HDMI, PURPLE, BLUE, LAPTOP, ENCHUFE, UPS, POWERBANK]


@pytest.fixture
def eurocomp(monkeypatch):
    """Credencial falsa + Eurocomp falso: `state["items"]` es lo que responde; `state["error"]` simula un result."""
    state = {"items": list(CATALOGO), "error": None, "calls": 0}
    monkeypatch.setattr(settings, "eurocomp_ws_id", "123")
    monkeypatch.setattr(settings, "eurocomp_ws_password", SecretStr("x"))
    monkeypatch.setattr(settings, "eurocomp_ws_key", None)

    def bodega_items(self, bid, keep=None):
        state["calls"] += 1
        if state["error"] is not None:
            raise SirettError(state["error"])
        assert bid == settings.eurocomp_bodega
        return [dict(i) for i in state["items"] if keep is None or keep(dict(i))]

    def items_list(self, bid, codes):
        state["list_calls"] = state.get("list_calls", 0) + 1
        return [dict(i) for i in state["items"] if i["codigo"] in codes]

    monkeypatch.setattr(SirettClient, "bodega_items", bodega_items)
    monkeypatch.setattr(SirettClient, "items_list", items_list)
    return state


def test_relevance_rules():
    assert ss.relevant(CAMARA).startswith("familia:SEGURIDAD")
    assert ss.relevant(BALUN) == "marca:HIKVISION"
    assert ss.relevant(UTP).startswith("clave:") and ss.relevant(HDMI) is None
    assert ss.relevant(PURPLE).startswith("clave:") and ss.relevant(BLUE) is None
    assert ss.relevant(UPS) and ss.relevant(POWERBANK) is None
    assert ss.relevant(LAPTOP) is None and ss.relevant(ENCHUFE) is None  # marca de seguridad, familia de hogar
    assert image_url("upload/items/a.jpg", "https://eurocompcr.com/webservice.php") == "https://eurocompcr.com/upload/items/a.jpg"


def test_soap_response_parsing():
    xml = (
        '<?xml version="1.0" encoding="ISO-8859-1"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
        "<SOAP-ENV:Body><ns1:wsc_request_bodega_all_itemsResponse xmlns:ns1=\"urn:server\"><result>0</result><data>"
        "<item><codigo>AC1104</codigo><descripcion>ADAPTADOR BALUN SE\xd1AL</descripcion><precio>1.82</precio>"
        "<precios_extra><item><idx>1</idx><valor>2</valor></item></precios_extra><stock>16.000</stock></item>"
        "</data></ns1:wsc_request_bodega_all_itemsResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    ).encode("latin-1")
    from lxml import etree

    from app.providers.sirett import PARSER

    rows = SirettClient.rows(etree.fromstring(xml, PARSER))
    assert rows == [{"codigo": "AC1104", "descripcion": "ADAPTADOR BALUN SEÑAL", "precio": "1.82", "stock": "16.000"}]
    # por partes (como llega por la red), cortado en pedazos de 7 bytes: mismo resultado sin armar el documento entero
    chunks = [xml[i : i + 7] for i in range(0, len(xml), 7)]
    assert SirettClient.parse_rows(chunks) == rows
    assert SirettClient.parse_rows(chunks, keep=lambda r: r["codigo"] != "AC1104") == []
    throttled = xml.replace(b"<result>0</result>", b"<result>20</result>")
    with pytest.raises(SirettError) as e:
        SirettClient.parse_rows([throttled])
    assert e.value.code == 20
    with pytest.raises(SirettError):
        SirettClient.parse_rows([xml[:200]])  # respuesta cortada


def test_sync_links_enriches_and_never_touches_own_stock(client, auth, db_session, eurocomp):
    tid = auth["tenant"]["id"]
    # producto propio cargado antes con la lista vieja: mismo codigo del proveedor, sin CABYS ni fotos
    prod = Product(tenant_id=tid, code="HIK-1047", name="Cámara 4MP ColorVu", price=Decimal("45000"), supplier_sku="SG2104")
    db_session.add(prod)
    db_session.commit()
    moves_before = db_session.scalar(select(func.count()).select_from(StockMovement))

    sup = client.get("/supplier-catalog/suppliers").json()
    assert not [s for s in sup if s["name"].lower().startswith("eurocomp")]  # todavia no existe: lo crea la sincronizacion
    out = ss.sync_all(db_session)[tid]
    assert out["recibidos"] == 10 and out["utiles"] == 5  # camara, balun, UTP, disco Purple, UPS
    assert out["vinculos"] == 1 and out["cabys_completados"] == 1 and out["fotos_completadas"] == 1

    s = db_session.scalar(select(Supplier).where(Supplier.tenant_id == tid))
    codes = {i.code for i in db_session.scalars(select(SupplierItem).where(SupplierItem.supplier_id == s.id))}
    assert codes == {"SG2104", "AC1104", "CB0001", "HD0001", "UP0001"}
    db_session.refresh(prod)
    assert prod.cost == Decimal("48.5") and prod.cost_currency == "USD" and prod.supplier_stock == 5
    assert prod.cabys_code == "4526100000200" and prod.images[0] == {"url": "https://eurocompcr.com/upload/items/2026/8/1_2.jpg", "main": True}
    assert len(prod.images) == 2 and prod.price == Decimal("45000")  # el precio de venta no se toca
    assert db_session.scalar(select(func.count()).select_from(StockMovement)) == moves_before  # la existencia propia no se mueve

    # segunda hora: la camara se agoto en Eurocomp y el balun ya no viene -> queda inactivo; lo propio se respeta
    prod.cabys_code = "1111111111111"
    db_session.commit()
    eurocomp["items"] = [dict(CAMARA, stock="0.000"), UTP, PURPLE, UPS]
    out = ss.sync_all(db_session)[tid]
    assert out["inactivar"] == 1
    db_session.refresh(prod)
    assert prod.supplier_stock == 0 and prod.cabys_code == "1111111111111"  # nunca pisa un CABYS que ya estaba

    st = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["id"] == s.id)
    assert st["api"]["status"] == "conectado" and st["api"]["last_summary"]["utiles"] == 4


def test_suspicious_or_throttled_response_changes_nothing(client, auth, db_session, eurocomp, monkeypatch):
    tid = auth["tenant"]["id"]
    monkeypatch.setattr(ss, "MIN_RATIO", 0.5)
    ss.sync_all(db_session)
    s = db_session.scalar(select(Supplier).where(Supplier.tenant_id == tid))
    active = lambda: db_session.scalar(select(func.count()).where(SupplierItem.supplier_id == s.id, SupplierItem.active))  # noqa: E731
    assert active() == 5
    # respuesta cortada (nada util): no se aplica y queda el error visible
    eurocomp["items"] = [LAPTOP]
    assert "error" in ss.sync_all(db_session)[tid]
    assert active() == 5
    st = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["id"] == s.id)["api"]
    assert st["status"] == "error" and "no se aplica" in st["label"]
    # "sincronizar ahora" dentro de la misma hora: Eurocomp responde 20 -> 429 con el motivo
    eurocomp["items"], eurocomp["error"] = list(CATALOGO), 20
    r = client.post(f"/supplier-catalog/suppliers/{s.id}/sync")
    assert r.status_code == 429 and "por hora" in r.json()["detail"]
    eurocomp["error"] = None
    r = client.post(f"/supplier-catalog/suppliers/{s.id}/sync")
    assert r.status_code == 200 and r.json()["utiles"] == 5


def test_stock_refresh_updates_only_linked_products(client, auth, db_session, eurocomp):
    """Cada 6 horas: existencia y costo de lo vinculado, sin la consulta masiva; lo que Eurocomp ya no devuelve queda en 0."""
    tid = auth["tenant"]["id"]
    prod = Product(tenant_id=tid, code="HIK-1047", name="Cámara 4MP ColorVu", price=Decimal("45000"), supplier_sku="SG2104")
    db_session.add(prod)
    db_session.commit()
    ss.sync_all(db_session)
    assert eurocomp["calls"] == 1
    db_session.refresh(prod)
    assert prod.supplier_stock == 5
    before = prod.supplier_updated_at

    eurocomp["items"] = [dict(CAMARA, stock="2.000", precio="50.00"), UTP]
    out = ss.refresh_linked(db_session)[tid]
    assert out == {"vinculados": 1, "cambios": 1, "sin_dato": 0}
    assert eurocomp["calls"] == 1 and eurocomp["list_calls"] == 1  # nunca la consulta masiva
    db_session.refresh(prod)
    assert prod.supplier_stock == 2 and prod.cost == Decimal("50") and prod.price == Decimal("45000")
    assert prod.supplier_updated_at >= before
    item = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "SG2104"))
    assert item.prev_cost == Decimal("48.5") and item.active
    # el articulo de la lista sin vincular no se toca
    utp = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "CB0001"))
    assert utp.stock == 5

    eurocomp["items"] = [UTP]  # Eurocomp ya no confirma la camara: no se vende "bajo pedido"
    assert ss.refresh_linked(db_session)[tid]["sin_dato"] == 1
    db_session.refresh(prod)
    assert prod.supplier_stock == 0
    st = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"].lower().startswith("eurocomp"))["api"]
    assert st["last_stock_at"] and "cada 6 horas" in st["label"]


def test_without_credentials_nothing_runs(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "eurocomp_ws_id", None)
    monkeypatch.setattr(settings, "eurocomp_ws_password", None)
    monkeypatch.setattr(settings, "eurocomp_ws_key", None)
    db_session.add(Supplier(tenant_id=auth["tenant"]["id"], name="Eurocomp"))
    db_session.commit()
    assert ss.sync_all(db_session) == {"skipped": "sin credenciales de Eurocomp"}
    sup = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"] == "Eurocomp")
    assert sup["api"]["status"] == "pendiente" and "SiReTT" in sup["api"]["label"]


def test_semanal_solo_precio_y_existencia_de_lo_que_ya_esta(client, auth, db_session, eurocomp):
    """Pedido 21/10 (item 9): la sincronizacion semanal no vuelve a traer fotos ni descripciones de lo ya creado."""
    tid = auth["tenant"]["id"]
    prod = Product(tenant_id=tid, code="HIK-1047", name="Cámara 4MP ColorVu", price=Decimal("45000"), supplier_sku="SG2104")
    db_session.add(prod)
    db_session.commit()
    ss.sync_all(db_session)
    db_session.refresh(prod)
    assert len(prod.images) == 2
    # alguien quita las fotos del producto; Eurocomp cambia la descripcion, la foto, el precio y la existencia
    prod.images = []
    db_session.commit()
    eurocomp["items"] = [
        dict(CAMARA, descripcion="CAMARA IP NUEVA DESCRIPCION", img1="upload/items/otra.jpg", image_url="upload/items/otra2.jpg", precio="51.00", stock="9.000"),
        BALUN,
        UTP,
        PURPLE,
        UPS,
    ]
    out = ss.sync_all(db_session)[tid]
    assert out["fotos_completadas"] == 0 and out["cabys_completados"] == 0
    it = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "SG2104"))
    assert it.description.startswith("CAMARA IP HIKVISION") and it.raw["image_url"] == "upload/items/2026/8/1_2.jpg"
    assert it.cost == Decimal("51") and it.stock == 9 and it.raw["precio"] == "51.00" and it.raw["stock"] == "9.000"
    db_session.refresh(prod)
    assert prod.images == [] and prod.cost == Decimal("51") and prod.supplier_stock == 9
    # lo nuevo si entra completo
    eurocomp["items"] = [*eurocomp["items"], item("SG9999", "GRABADORA NVR HIKVISION DS-7608NI", "SEGURIDAD", "Grabadoras", "HIKVISION", "80.00")]
    assert ss.sync_all(db_session)[tid]["nuevo"] == 1
    nuevo = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "SG9999"))
    assert nuevo.description.startswith("GRABADORA NVR") and nuevo.raw.get("image_url") and nuevo.changed_at is not None


def test_cambio_real_aparte_de_la_consulta(client, auth, db_session, eurocomp):
    """Pedido 21/10 (item 12): "actualizado hace 3 h" era la hora de la consulta; el ultimo cambio real va aparte."""
    from datetime import UTC, datetime, timedelta

    from app.services.availability import availability_many

    tid = auth["tenant"]["id"]
    prod = Product(tenant_id=tid, code="HIK-1047", name="Cámara 4MP ColorVu", price=Decimal("45000"), supplier_sku="SG2104")
    db_session.add(prod)
    db_session.commit()
    ss.sync_all(db_session)
    it = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "SG2104"))
    hace_dos_dias = datetime.now(UTC) - timedelta(days=2)
    it.changed_at = hace_dos_dias
    db_session.commit()
    # el refresco de 6 h consulta pero nada cambia: el ultimo cambio sigue siendo de hace 2 dias
    out = ss.refresh_linked(db_session)[tid]
    assert out["cambios"] == 0
    db_session.refresh(it)
    assert abs((it.changed_at.replace(tzinfo=UTC) if it.changed_at.tzinfo is None else it.changed_at) - hace_dos_dias) < timedelta(seconds=1)
    row = availability_many(db_session, tid, [prod.id], audience="interno")[prod.id]
    assert row["supplier_age"] == "hace menos de 1 h" and row["supplier_changed_age"] == "hace 2 días"
    assert "consultado hace menos de 1 h · último cambio hace 2 días" in row["label"]
    st = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"].lower().startswith("eurocomp"))["api"]
    assert st["last_stock_at"] and not st.get("last_stock_change_at")
    # cambia la existencia: ahora si
    eurocomp["items"] = [dict(CAMARA, stock="1.000"), UTP]
    assert ss.refresh_linked(db_session)[tid]["cambios"] == 1
    row = availability_many(db_session, tid, [prod.id], audience="interno")[prod.id]
    assert row["supplier_changed_age"] == "hace menos de 1 h"
    st = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"].lower().startswith("eurocomp"))["api"]
    assert st["last_stock_change_at"]


def test_agotado_en_eurocomp_sale_de_la_tienda_y_sigue_en_el_panel(client, auth, db_session, eurocomp):
    """Pedido 21/10 (item 10): agotado en Eurocomp (y sin existencia propia) sale de la tienda en la siguiente
    sincronizacion; sigue en el panel. Con Ajustes "mostrar" queda visible como agotado."""
    assert client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"}).status_code == 200
    r = client.post("/products", json={"name": "Cámara 4MP ColorVu", "code": "HIK-1047", "price": 45000, "show_on_web": True, "supplier_sku": "SG2104"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    ss.sync_all(db_session)
    pub = lambda: {p["id"] for p in client.get("/public/store/crimson/products").json()}  # noqa: E731
    assert pid in pub()  # bajo pedido
    eurocomp["items"] = [dict(CAMARA, stock="0.000"), BALUN, UTP, PURPLE, UPS]
    ss.sync_all(db_session)
    assert pid not in pub()
    # sigue en el panel: Inventario web, Disponibilidad y el producto activo y publicado
    panel = {x["id"]: x for x in client.get("/store/inventory", params={"todos": True}).json()["items"]}
    assert panel[pid]["estado"] == "agotado" and panel[pid]["oculto_en_tienda"] is True
    disp = {x["id"]: x for x in client.get("/stock/availability").json()["items"]}
    assert disp[pid]["agotado_proveedor"] is True and disp[pid]["ocultar"] is True
    prod = client.get(f"/products/{pid}").json()
    assert prod["active"] and prod["show_on_web"]
    # la ficha directa sigue abriendo
    assert client.get(f"/public/store/crimson/products/{pid}").status_code == 200
    # Ajustes: mostrarlo como agotado
    assert client.put("/store/inventory/settings", json={"supplier_out_of_stock": "mostrar"}).status_code == 200
    assert pid in pub()
    # vuelve a haber en Eurocomp: vuelve a la tienda
    client.put("/store/inventory/settings", json={"supplier_out_of_stock": "ocultar"})
    eurocomp["items"] = [CAMARA, UTP]
    ss.refresh_linked(db_session)
    assert pid in pub()
