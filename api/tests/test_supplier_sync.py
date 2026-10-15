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

    def bodega_items(self, bid):
        state["calls"] += 1
        if state["error"] is not None:
            raise SirettError(state["error"])
        assert bid == settings.eurocomp_bodega
        return [dict(i) for i in state["items"]]

    monkeypatch.setattr(SirettClient, "bodega_items", bodega_items)
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


def test_without_credentials_nothing_runs(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "eurocomp_ws_id", None)
    monkeypatch.setattr(settings, "eurocomp_ws_password", None)
    monkeypatch.setattr(settings, "eurocomp_ws_key", None)
    db_session.add(Supplier(tenant_id=auth["tenant"]["id"], name="Eurocomp"))
    db_session.commit()
    assert ss.sync_all(db_session) == {"skipped": "sin credenciales de Eurocomp"}
    sup = next(x for x in client.get("/supplier-catalog/suppliers").json() if x["name"] == "Eurocomp")
    assert sup["api"]["status"] == "pendiente" and "SiReTT" in sup["api"]["label"]
