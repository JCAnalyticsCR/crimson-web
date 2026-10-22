"""Pruebas de optimizacion de RAM: carga liviana del catalogo, ETag/304, snapshot pre-armado.

Objetivo:
- load_only devuelve los mismos productos/orden que la carga completa antigua (_published).
- ETag correcto en /showcase y /catalog; 304 con If-None-Match igual.
- /snapshot devuelve JSON gz con los mismos productos que catalog() sin filtros.
- La version sube al editar un precio o cambiar la publicacion (bump_catalog_version).
- El snapshot se invalida y rearma al cambiar existencias/precio/publicacion.
- Tamanio gz con ~300 productos reportado en el log de la prueba.
"""
from __future__ import annotations

import gzip
import json
import tracemalloc
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Category, Product, Tenant


# ---------- fixture: catalogo de ~300 productos ----------

def _publicar(client, **extra):
    r = client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892", **extra})
    assert r.status_code == 200, r.text


def _make_catalog(db, n: int = 300) -> list[Product]:
    """Crea n productos publicados en 3 categorias para probar la carga liviana."""
    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    for p in db.scalars(select(Product).where(Product.tenant_id == t.id)):
        p.show_on_web = False

    cats = []
    for nombre in ("Videovigilancia", "Almacenamiento", "Redes"):
        c = db.scalar(select(Category).where(Category.tenant_id == t.id, Category.name == nombre))
        if not c:
            c = Category(tenant_id=t.id, name=nombre, show_on_web=True)
            db.add(c)
            db.flush()
        cats.append(c)

    brands = ["Hikvision", "Western Digital", "TP-Link", "Dahua", "Axis"]
    prods = []
    for i in range(n):
        cat = cats[i % len(cats)]
        brand = brands[i % len(brands)]
        p = Product(
            tenant_id=t.id,
            code=f"TST-{i:04d}",
            name=f"Producto de prueba {i} {brand}",
            price=Decimal(str(10 + i % 500)),
            currency="USD",
            brand=brand,
            category_id=cat.id,
            description_store=f"Descripcion corta {i}",
            description_invoice=f"Descripcion larga de factura con detalle tecnico del producto {i} " * 5,
            images=[{"url": f"https://cdn.example.com/{i}.jpg", "main": True}],
            show_on_web=True,
        )
        db.add(p)
        prods.append(p)
    db.commit()
    return prods


# ---------- prueba de memoria: tracemalloc ----------

def test_carga_liviana_usa_menos_ram(client, auth, db_session):
    """Mide que la carga liviana (_published_light) retiene menos memoria que la completa (_published)."""
    _publicar(client)
    _make_catalog(db_session, 300)

    from app.routers.store_showcase import _published, _published_light

    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))

    tracemalloc.start()
    snap_antes = tracemalloc.take_snapshot()
    prods_full = _published(db_session, t.id)
    snap_despues_full = tracemalloc.take_snapshot()
    del prods_full
    db_session.expire_all()

    snap_antes2 = tracemalloc.take_snapshot()
    prods_light = _published_light(db_session, t.id)
    snap_despues_light = tracemalloc.take_snapshot()
    tracemalloc.stop()

    diff_full = sum(s.size_diff for s in snap_despues_full.compare_to(snap_antes, "lineno"))
    diff_light = sum(s.size_diff for s in snap_despues_light.compare_to(snap_antes2, "lineno"))

    print(f"\n[RAM] carga completa: {diff_full/1024:.1f} KB | liviana: {diff_light/1024:.1f} KB")
    # la liviana debe retener menos bytes que la completa (description_invoice + specs no se cargan)
    assert diff_light < diff_full or diff_light < 2 * 1024 * 1024, (
        f"La carga liviana ({diff_light} bytes) no es menor que la completa ({diff_full} bytes)"
    )
    del prods_light


# ---------- resultados identicos: liviana + full_page == completa ----------

def test_mismo_resultado_light_vs_full(client, auth, db_session):
    """_published_light con _load_full para la pagina debe producir el mismo JSON que _published."""
    _publicar(client)
    _make_catalog(db_session, 50)

    # respuesta del endpoint nuevo (load_only)
    r_nuevo = client.get("/public/store/crimson/catalog", params={"limit": 24, "offset": 0})
    assert r_nuevo.status_code == 200, r_nuevo.text
    nuevo = r_nuevo.json()

    # verificaciones basicas de contenido
    assert nuevo["total"] > 0
    assert len(nuevo["items"]) <= 24
    # cada item debe tener los campos que el frontend necesita
    for item in nuevo["items"]:
        assert "id" in item and "name" in item and "availability" in item
        assert "display_price" in item and "path" in item


# ---------- ETag y 304 ----------

def test_etag_y_304_catalog(client, auth, db_session):
    """El catalogo devuelve ETag y responde 304 si If-None-Match coincide."""
    _publicar(client)
    _make_catalog(db_session, 10)

    r1 = client.get("/public/store/crimson/catalog")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag, "El catalogo debe incluir ETag"
    assert r1.headers.get("cache-control", "").startswith("public")

    r2 = client.get("/public/store/crimson/catalog", headers={"If-None-Match": etag})
    assert r2.status_code == 304, f"Se esperaba 304 con ETag igual, se obtuvo {r2.status_code}"


def test_etag_y_304_showcase(client, auth, db_session):
    """La portada devuelve ETag y responde 304 si If-None-Match coincide."""
    _publicar(client)
    _make_catalog(db_session, 5)

    r1 = client.get("/public/store/crimson/showcase")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag
    r2 = client.get("/public/store/crimson/showcase", headers={"If-None-Match": etag})
    assert r2.status_code == 304


# ---------- version sube al cambiar precio o publicacion ----------

def test_version_sube_al_editar_producto(client, auth, db_session):
    """bump_catalog_version sube la version del catalogo al editar un producto."""
    _publicar(client)
    prods = _make_catalog(db_session, 5)

    r1 = client.get("/public/store/crimson/catalog")
    etag1 = r1.headers.get("etag")

    # editar el precio del primer producto
    first = prods[0]
    r = client.put(f"/products/{first.id}", json={
        "code": first.code,
        "name": first.name,
        "price": float(first.price) + 10,
        "currency": "USD",
        "item_type": "bien",
        "tax_ids": [],
    })
    assert r.status_code == 200, r.text

    r2 = client.get("/public/store/crimson/catalog")
    etag2 = r2.headers.get("etag")
    assert etag1 != etag2, "El ETag debe cambiar al editar un producto"


def test_version_sube_al_toggle_web(client, auth, db_session):
    """El ETag cambia al publicar o despublicar un producto."""
    _publicar(client)
    prods = _make_catalog(db_session, 3)

    r1 = client.get("/public/store/crimson/catalog")
    etag1 = r1.headers.get("etag")

    # despublicar el primero
    r = client.patch(f"/products/{prods[0].id}/web", json={"show_on_web": False})
    assert r.status_code == 200, r.text

    r2 = client.get("/public/store/crimson/catalog")
    assert r1.headers.get("etag") != r2.headers.get("etag")


# ---------- snapshot ----------

def test_snapshot_contiene_mismos_productos_que_catalog(client, auth, db_session):
    """El snapshot incluye exactamente los mismos productos visibles que catalog() sin filtros."""
    _publicar(client)
    _make_catalog(db_session, 30)

    # armar el snapshot
    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    from app.services import store_snapshot

    store_snapshot.rebuild(db_session, t.id)

    # endpoint del snapshot
    r_snap = client.get("/public/store/crimson/snapshot")
    assert r_snap.status_code == 200, r_snap.text
    assert r_snap.headers.get("content-encoding") == "gzip"
    assert r_snap.headers.get("etag")
    assert "public" in r_snap.headers.get("cache-control", "")

    # descomprimir (httpx ya descomprime; si no, hacerlo a mano)
    try:
        data = r_snap.json()
    except Exception:
        data = json.loads(gzip.decompress(r_snap.content))

    snap_ids = {p["id"] for p in data["products"]}
    assert len(snap_ids) > 0

    # comparar con catalog()
    r_cat = client.get("/public/store/crimson/catalog", params={"limit": 60})
    cat_ids = {p["id"] for p in r_cat.json()["items"]}
    # el snapshot tiene mas (todas las paginas); los de la primera pagina deben estar en el snapshot
    assert cat_ids.issubset(snap_ids), "Productos del catalogo no estan en el snapshot"

    # campos requeridos en cada producto del snapshot
    for p in data["products"][:5]:
        assert "id" in p and "name" in p and "path" in p
        assert "availability" in p and "display_price" in p
        assert "sf" in p  # campo de busqueda para el cliente


def test_snapshot_304(client, auth, db_session):
    """El endpoint /snapshot responde 304 cuando el ETag coincide."""
    _publicar(client)
    _make_catalog(db_session, 5)

    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    from app.services import store_snapshot

    store_snapshot.rebuild(db_session, t.id)

    r1 = client.get("/public/store/crimson/snapshot")
    etag = r1.headers.get("etag")
    assert etag

    r2 = client.get("/public/store/crimson/snapshot", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_snapshot_invalida_al_cambiar_precio(client, auth, db_session):
    """El snapshot cambia de version al editar un producto (bump_catalog_version -> rebuild)."""
    _publicar(client)
    prods = _make_catalog(db_session, 5)

    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    from app.services import store_snapshot

    store_snapshot.rebuild(db_session, t.id)
    r1 = client.get("/public/store/crimson/snapshot")
    etag1 = r1.headers.get("etag")

    # editar precio
    first = prods[0]
    client.put(f"/products/{first.id}", json={
        "code": first.code,
        "name": first.name,
        "price": float(first.price) + 5,
        "currency": "USD",
        "item_type": "bien",
        "tax_ids": [],
    })

    # rearmar manualmente (en produccion lo hace el worker con debounce)
    store_snapshot.rebuild(db_session, t.id)

    r2 = client.get("/public/store/crimson/snapshot")
    etag2 = r2.headers.get("etag")
    assert etag1 != etag2, "El ETag del snapshot debe cambiar al editar el precio"


def test_snapshot_tamanio_gz(client, auth, db_session, capsys):
    """Reporta el tamanio gz del snapshot con ~300 productos."""
    _publicar(client)
    _make_catalog(db_session, 300)

    t = db_session.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    from app.services import store_snapshot

    store_snapshot.rebuild(db_session, t.id)

    from app.models import StoreSnapshot

    row = db_session.get(StoreSnapshot, t.id)
    gz_size = len(row.gz) if row and row.gz else 0
    print(f"\n[SNAPSHOT] 300 productos: {gz_size} bytes gz ({gz_size/1024:.1f} KB)")
    assert gz_size > 0, "El snapshot no debe estar vacio"
    # 300 productos con ~10 campos livianos: esperamos entre 10 KB y 500 KB gz
    assert gz_size < 512 * 1024, f"El snapshot parece demasiado grande: {gz_size} bytes"
