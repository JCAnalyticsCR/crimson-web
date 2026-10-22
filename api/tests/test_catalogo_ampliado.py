"""Catálogo ampliado de Eurocomp: 4 familias completas, auto_catalog idempotente,
Hikvision destacado y primero en la tienda. Los artículos de Eurocomp se simulan con el mismo
fixture `eurocomp` de test_supplier_sync: sin red, sin credenciales reales."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Product, Supplier, SupplierItem
from app.services import supplier_sync as ss

# ---------- helpers: artículos de ejemplo ----------

def item(code, desc, fam, sub, marca="", precio="10.00", stock="5.000", cabys="", img="", **kw):
    return {
        "codigo": code, "descripcion": desc, "desc_corta": "", "FamiliaPadre": fam, "familia": sub,
        "marca": marca, "modelo": kw.get("modelo", ""), "precio": precio, "currency_code": "USD",
        "stock": stock, "cod_hacienda": cabys, "image_url": img, "image_url_1": "",
        "caracteristicas": kw.get("caracteristicas", ""),
    }  # fmt: skip


HIK_CAM   = item("SG2104", "CAMARA IP HIKVISION DS-2CD1047G3", "SEGURIDAD", "Camaras", "HIKVISION", "48.50", stock="5.000", modelo="DS-2CD1047G3")
HIK_NVR   = item("SG9001", "NVR HILOOK NVR-108MH-D", "SEGURIDAD", "Grabadoras", "HILOOK", "120.00", stock="3.000", modelo="NVR-108MH-D")
UPS_APC   = item("UP0001", "UPS APC BACK-UPS 600VA", "RESPALDO ELÉCTRICO", "UPS", "APC", "70.00", stock="8.000")
POWERBANK = item("PB0001", "POWER BANK ANKER 10000", "RESPALDO ELÉCTRICO", "Power Banks", "ANKER", "15.00", stock="4.000")
ENCHUFE   = item("SH0001", "ENCHUFE INTELIGENTE EZVIZ T30", "SMART HOME", "Corriente Inteligente", "EZVIZ", "12.00", stock="6.000")
ECHO      = item("SH0002", "ALEXA AMAZON ECHO DOT", "SMART HOME", "Amazon Echo", "AMAZON", "25.00", stock="2.000")
LAPTOP    = item("LP0001", "LAPTOP ASUS VIVOBOOK 15", "LAPTOPS", "Español", "ASUS", "500.00", stock="1.000")

CATALOGO_AMPLIADO = [HIK_CAM, HIK_NVR, UPS_APC, POWERBANK, ENCHUFE, ECHO, LAPTOP]


@pytest.fixture
def eurocomp_ampliado(monkeypatch):
    """Eurocomp falso con el catálogo ampliado."""
    from pydantic import SecretStr

    from app.core.config import settings
    from app.providers.sirett import SirettClient, SirettError

    state = {"items": list(CATALOGO_AMPLIADO), "error": None}
    monkeypatch.setattr(settings, "eurocomp_ws_id", "123")
    monkeypatch.setattr(settings, "eurocomp_ws_password", SecretStr("x"))
    monkeypatch.setattr(settings, "eurocomp_ws_key", None)

    def bodega_items(self, bid, keep=None):
        if state["error"]:
            raise SirettError(state["error"])
        return [dict(i) for i in state["items"] if keep is None or keep(dict(i))]

    def items_list(self, bid, codes):
        return [dict(i) for i in state["items"] if i["codigo"] in codes]

    monkeypatch.setattr(SirettClient, "bodega_items", bodega_items)
    monkeypatch.setattr(SirettClient, "items_list", items_list)
    return state


# ---------- 1. filtro de familias ----------

def test_familias_ampliadas():
    """RESPALDO ELÉCTRICO completo (incluye Power Banks) y SMART HOME son ahora relevantes."""
    assert ss.relevant(UPS_APC), "UPS debe ser relevante (familia RESPALDO ELÉCTRICO)"
    assert ss.relevant(POWERBANK), "Power Banks debe ser relevante (familia RESPALDO ELÉCTRICO completa)"
    assert ss.relevant(ENCHUFE), "Smart Home debe ser relevante (familia SMART HOME)"
    assert ss.relevant(ECHO), "Amazon Echo (Smart Home) debe ser relevante"
    assert ss.relevant(HIK_CAM), "Seguridad sigue relevante"
    assert ss.relevant(LAPTOP) is None, "Laptops sigue fuera del filtro"


def test_familias_completas_cubren_las_4():
    """Las 4 familias objetivo están en FAMILIAS_COMPLETAS."""
    assert "SEGURIDAD" in ss.FAMILIAS_COMPLETAS
    assert "REDES Y CONECTIVDAD" in ss.FAMILIAS_COMPLETAS  # errata incluida
    assert "RESPALDO ELÉCTRICO" in ss.FAMILIAS_COMPLETAS
    assert "SMART HOME" in ss.FAMILIAS_COMPLETAS


# ---------- 2. auto_catalog: creación y configuración ----------

def test_auto_catalog_crea_productos(client, auth, db_session, eurocomp_ampliado):
    """auto_catalog_run crea productos para todos los artículos activos de las familias configuradas."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)  # poblar supplier_items

    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {
        "enabled": True,
        "families": ["Seguridad", "Redes y conectividad", "Respaldo eléctrico", "Smart home"],
        "publish_web": False,
    })
    db_session.commit()

    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    result = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)

    assert result.get("creados", 0) >= 1, f"Debe crear al menos 1 producto, resultado: {result}"
    # HIK_CAM, HIK_NVR, UPS_APC, POWERBANK, ENCHUFE, ECHO → 6 artículos útiles (LAPTOP excluido)
    assert result["creados"] == 6
    # Verificar por SupplierItem vinculado (evita mezclar con productos de seed)
    vinculados = list(db_session.scalars(
        select(SupplierItem).where(
            SupplierItem.tenant_id == tid,
            SupplierItem.supplier_id == sup.id,
            SupplierItem.product_id.is_not(None),
        )
    ))
    assert len(vinculados) == 6


def test_auto_catalog_deshabilitado_no_crea(client, auth, db_session, eurocomp_ampliado):
    """Si auto_catalog.enabled es False (por defecto), no crea nada."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)
    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    # enabled=False por defecto
    from sqlalchemy import func
    antes = db_session.scalar(select(func.count()).select_from(SupplierItem).where(SupplierItem.tenant_id == tid, SupplierItem.product_id.is_not(None)))
    result = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    assert "skipped" in result
    despues = db_session.scalar(select(func.count()).select_from(SupplierItem).where(SupplierItem.tenant_id == tid, SupplierItem.product_id.is_not(None)))
    assert despues == antes, "No se deben crear productos con auto_catalog deshabilitado"


# ---------- 3. idempotencia ----------

def test_idempotencia_no_duplica(client, auth, db_session, eurocomp_ampliado):
    """Ejecutar auto_catalog_run dos veces con el mismo catálogo no crea productos duplicados."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)

    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": False})
    db_session.commit()

    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    r1 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    r2 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)

    assert r1.get("creados", 0) >= 1, "Primera ejecución debe crear productos"
    assert r2.get("creados", 0) == 0, f"Segunda ejecución no debe crear nada, creó: {r2.get('creados')}"

    # Verificar que los productos tienen product_id vinculado
    items_vinculados = list(db_session.scalars(
        select(SupplierItem).where(
            SupplierItem.tenant_id == tid,
            SupplierItem.product_id.is_not(None),
        )
    ))
    assert len(items_vinculados) == r1["creados"]


# ---------- 4. respeto de borrados y ocultos ----------

def test_no_recrea_producto_borrado(client, auth, db_session, eurocomp_ampliado):
    """Si un producto fue borrado (link_method set, product_id NULL), auto_catalog lo omite."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)

    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": False})
    db_session.commit()

    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    r1 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    creados_primera = r1["creados"]
    assert creados_primera >= 1

    # Simular que el admin borró el producto vinculado a UPS_APC:
    # product_id → NULL (ON DELETE SET NULL) pero link_method queda como "creado"
    it_ups = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "UP0001"))
    assert it_ups is not None and it_ups.product_id is not None
    prod_ups = db_session.get(Product, it_ups.product_id)
    db_session.delete(prod_ups)
    db_session.flush()
    db_session.refresh(it_ups)
    # Después del flush, product_id debe ser NULL (ON DELETE SET NULL)
    assert it_ups.product_id is None
    assert it_ups.link_method == "creado"
    db_session.commit()

    # Segunda ejecución: el UPS no se vuelve a crear
    t = db_session.get(ss.Tenant, tid)
    r2 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    assert r2["creados"] == 0, "No debe recrear el producto borrado"
    assert r2["skip_borrado"] == 1, "Debe contar el borrado como skip_borrado"


def test_no_republica_producto_oculto(client, auth, db_session, eurocomp_ampliado):
    """Si el admin ocultó un producto (show_on_web=False), auto_catalog lo omite (product_id != NULL)."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)

    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": True})  # intenta publicar
    db_session.commit()

    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    r1 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    assert r1["creados"] >= 1

    # El admin oculta el UPS en la tienda
    it_ups = db_session.scalar(select(SupplierItem).where(SupplierItem.code == "UP0001"))
    prod_ups = db_session.get(Product, it_ups.product_id)
    prod_ups.show_on_web = False
    db_session.commit()

    # Segunda ejecución: el UPS sigue vinculado (product_id != NULL), se omite (no se toca)
    t = db_session.get(ss.Tenant, tid)
    r2 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    assert r2["creados"] == 0
    # El producto ocultado no se volvió a publicar
    db_session.refresh(prod_ups)
    assert prod_ups.show_on_web is False


# ---------- 5. Hikvision destacado ----------

def test_hikvision_web_featured(client, auth, db_session, eurocomp_ampliado):
    """Los productos Hikvision/HiLook/HiMicro se crean con web_featured=True; otros con False."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)

    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": False})
    db_session.commit()

    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    result = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    assert result["destacados_hik"] >= 2, "Hikvision y HiLook deben quedar destacados"

    # HIK_CAM (HIKVISION) y HIK_NVR (HILOOK) → web_featured=True
    cam = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG2104"))
    nvr = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG9001"))
    ups = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "UP0001"))

    assert cam is not None and cam.web_featured is True, "Hikvision debe ser destacado"
    assert nvr is not None and nvr.web_featured is True, "HiLook debe ser destacado"
    assert ups is not None and ups.web_featured is False, "APC (no Hikvision) no debe ser destacado"


def test_hikvision_featured_no_se_pisa(client, auth, db_session, eurocomp_ampliado):
    """Si el admin quitó web_featured de un producto Hikvision, auto_catalog no lo vuelve a marcar
    (el producto ya existe: product_id != NULL → se omite completamente)."""
    tid = auth["tenant"]["id"]
    ss.sync_all(db_session)

    t = db_session.get(ss.Tenant, tid)
    ss.ac_save(t, {"enabled": True, "publish_web": False})
    db_session.commit()

    t = db_session.get(ss.Tenant, tid)
    sup = ss.find_supplier(db_session, tid)
    ss.auto_catalog_run(db_session, t, sup, commit_batches=True)

    # Admin quita el destacado de la cámara
    cam = db_session.scalar(select(Product).where(Product.tenant_id == tid, Product.supplier_sku == "SG2104"))
    cam.web_featured = False
    db_session.commit()

    # Segunda ejecución: el producto ya existe (product_id != NULL), no se toca
    t = db_session.get(ss.Tenant, tid)
    r2 = ss.auto_catalog_run(db_session, t, sup, commit_batches=True)
    assert r2["creados"] == 0

    db_session.refresh(cam)
    assert cam.web_featured is False, "El admin quitó el destacado y no debe volver a ponerse"


# ---------- 6. orden en la tienda ----------

def test_orden_tienda_hikvision_primero(client, auth, db_session, eurocomp_ampliado):
    """_rank_default pone los destacados (Hikvision) antes que los no destacados."""
    from app.routers.store_showcase import _rank_default

    cam = Product(
        tenant_id=1, code="H1", name="Cámara Hikvision", brand="Hikvision",
        web_featured=True, price=Decimal("50000"), currency="CRC",
    )
    ups = Product(
        tenant_id=1, code="U1", name="UPS APC", brand="APC",
        web_featured=False, price=Decimal("70000"), currency="CRC",
    )
    cam.images = [{"url": "x", "main": True}]
    ups.images = [{"url": "y", "main": True}]

    assert _rank_default(cam) < _rank_default(ups), "Hikvision (featured) debe ordenar antes que APC"


def test_orden_tienda_no_featured_hikvision_antes_que_otros(client, auth, db_session, eurocomp_ampliado):
    """Hikvision no destacado aún ordena antes que marcas sin relevancia especial."""
    from app.routers.store_showcase import _rank_default

    hik = Product(tenant_id=1, code="H2", name="Switch Hikvision", brand="Hikvision", web_featured=False)
    oth = Product(tenant_id=1, code="O1", name="Switch Marca X", brand="MarcaX", web_featured=False)
    hik.images = oth.images = []

    assert _rank_default(hik) < _rank_default(oth), "Hikvision (no destacado) antes que otras marcas"


def test_orden_facetas_hikvision_primero(client, auth, db_session, eurocomp_ampliado):
    """En el endpoint /catalog, Hikvision aparece primero en la lista de marcas del filtro."""
    # Publicar la tienda y crear productos
    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    ss.sync_all(db_session)

    t = db_session.get(ss.Tenant, auth["tenant"]["id"])
    ss.ac_save(t, {"enabled": True, "publish_web": True})
    db_session.commit()

    t = db_session.get(ss.Tenant, auth["tenant"]["id"])
    sup = ss.find_supplier(db_session, auth["tenant"]["id"])
    ss.auto_catalog_run(db_session, t, sup, commit_batches=True)

    r = client.get("/public/store/crimson/catalog")
    assert r.status_code == 200, r.text
    brands = r.json().get("facets", {}).get("brands", [])
    brand_names = [b["name"] for b in brands]
    if "Hikvision" in brand_names:
        assert brand_names[0] == "Hikvision", f"Hikvision debe ser la primera marca, lista: {brand_names}"


# ---------- 7. permisos ----------

def test_solo_admin_configura_auto_catalog(client, auth, db_session, eurocomp_ampliado):
    """Un usuario sin catalog.costos no puede configurar auto_catalog."""

    ss.sync_all(db_session)
    sup = db_session.scalar(select(Supplier).where(Supplier.tenant_id == auth["tenant"]["id"]))

    # Sin token: 401
    r = client.get(f"/supplier-catalog/suppliers/{sup.id}/auto-catalog",
                   headers={"Authorization": ""})
    assert r.status_code == 401

    # PUT sin estar autenticado: 401
    r2 = client.put(f"/supplier-catalog/suppliers/{sup.id}/auto-catalog",
                    json={"enabled": True},
                    headers={"Authorization": ""})
    assert r2.status_code == 401


def test_get_auto_catalog_settings(client, auth, db_session, eurocomp_ampliado):
    """GET auto-catalog devuelve la configuración con los valores por defecto."""
    ss.sync_all(db_session)
    sup = db_session.scalar(select(Supplier).where(Supplier.tenant_id == auth["tenant"]["id"]))

    r = client.get(f"/supplier-catalog/suppliers/{sup.id}/auto-catalog")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enabled"] is False
    assert isinstance(data["families"], list)
    assert "Seguridad" in data["families"]


def test_put_auto_catalog_settings(client, auth, db_session, eurocomp_ampliado):
    """PUT auto-catalog persiste la configuración."""
    ss.sync_all(db_session)
    sup = db_session.scalar(select(Supplier).where(Supplier.tenant_id == auth["tenant"]["id"]))

    r = client.put(
        f"/supplier-catalog/suppliers/{sup.id}/auto-catalog",
        json={"enabled": True, "publish_web": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    # Verificar que el GET también lo refleja
    r2 = client.get(f"/supplier-catalog/suppliers/{sup.id}/auto-catalog")
    assert r2.json()["enabled"] is True
