"""Importador del catalogo de Fygaro (app.tools.import_fygaro): dry-run por defecto, emparejado sin duplicar,
idempotencia, fotos al mecanismo de multimedia propio y la vitrina publica en la moneda de la tienda con IVA."""

import json
import struct
import zlib
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.models import Media, Product, Tenant
from app.services import fygaro_import

SAMPLE = Path(__file__).parent / "fixtures" / "fygaro_sample.json"
A = "0ca92781-ac3e-47a0-8039-7afcb92509a0"  # control de acceso con SKU
B = "1a2adbb7-bc6f-41e7-8842-bd6ac7be741b"  # canaletas sin SKU
D = "48fa795d-5af8-4720-a679-98205d2ec149"  # mismo nombre que un producto existente


def _png(seed: int) -> bytes:
    raw = b"\x00" + bytes([seed % 256, 10, 20])
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _images_dir(tmp_path: Path) -> Path:
    d = tmp_path / "imgs"
    d.mkdir()
    for i, name in enumerate(("a344d64b-0483-46ff-a9a7-5bfe776b13c0.jpg", "b111d64b-0483-46ff-a9a7-5bfe776b13c0.png", "c222.jpg", "d333.jpg")):
        (d / name).write_bytes(_png(i + 1))  # la firma manda, no la extension
    return d


def _catalog() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _tenant(db) -> Tenant:
    return db.scalar(select(Tenant).where(Tenant.slug == "crimson"))


def _rows(report: dict) -> dict:
    return {r["fygaro_id"]: r for r in report["rows"]}


def test_dry_run_reports_without_writing(db_session):
    before = db_session.scalar(select(func.count()).select_from(Product))
    rep = fygaro_import.run(db_session, _tenant(db_session), _catalog())
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(Product)) == before  # no escribio nada
    s = rep["summary"]
    assert s["aplicado"] is False and s["total"] == 7
    assert s["nuevo"] == 5 and s["actualizar"] == 2 and s["error"] == 0
    assert s["por_nombre"] == 1 and s["sin_precio"] == 1 and s["precio_distinto"] == 2
    rows = _rows(rep)
    assert rows[A]["action"] == "nuevo" and rows[A]["code"] == "DS-K1T344EBFWX-E1" and rows[A]["tax_rate"] == "13"
    assert rows[B]["code"] == "FY-1A2ADBB7"  # sin SKU: codigo determinista desde el id de Fygaro
    # duplicado por nombre: se empareja con el existente y se reporta (no se crea otro)
    assert rows[D]["action"] == "actualizar" and rows[D]["match"] == "nombre" and rows[D]["code"] == "CAM-DOME-4MP" and rows[D]["doubtful"]
    nvr = rows["522fda9a-b09d-4c7a-9a06-40c1e2f8e0d8"]
    assert nvr["match"] == "codigo" and any("Precio distinto" in w for w in nvr["warnings"])
    rep_sku = rows["53aab128-d1c2-4c0d-8dbd-feec8fcc950e"]
    assert rep_sku["code"].startswith("FY-") and any("SKU repetido" in w for w in rep_sku["warnings"])
    assert any("servicio" in w for w in rows["682f7979-a3c0-49e8-8b5d-6cb11bbb1855"]["warnings"])
    assert not rows["4238a3c7-bbc4-44c0-a374-9be667e0a4ca"]["published"]
    assert s["legal"] == "importar terminos y privacidad"


def test_apply_creates_publishes_uploads_and_is_idempotent(db_session, tmp_path):
    t = _tenant(db_session)
    imgs = _images_dir(tmp_path)
    cam = db_session.scalar(select(Product).where(Product.code == "CAM-DOME-4MP"))
    cam_price = Decimal(str(cam.price))
    before = db_session.scalar(select(func.count()).select_from(Product))

    rep = fygaro_import.run(db_session, t, _catalog(), apply=True, images_dir=imgs, download_images=False)
    db_session.commit()
    assert rep["summary"]["nuevo"] == 5 and rep["summary"]["images_uploaded"] == 4
    assert db_session.scalar(select(func.count()).select_from(Product)) == before + 5

    a = db_session.scalar(select(Product).where(Product.code == "DS-K1T344EBFWX-E1"))
    assert a.show_on_web and a.currency == "USD" and Decimal(str(a.price)) == Decimal("236.3")
    assert a.taxes[0].tax.rate == 13 and a.cabys_code is None
    assert len(a.images) == 2 and a.images[0]["main"] and "/api/media/f/" in a.images[0]["url"]
    assert db_session.get(Product, a.id).category_id is not None
    sin_precio = db_session.scalar(select(Product).where(Product.code == "KIT-SIN-PRECIO"))
    assert not sin_precio.show_on_web
    assert db_session.scalar(select(Product).where(Product.name == "Instalación de cámara adicional")).item_type == "servicio"
    # el existente emparejado por nombre: publicado, con foto, SIN cambiar su precio
    db_session.refresh(cam)
    assert cam.show_on_web and Decimal(str(cam.price)) == cam_price and cam.images
    assert t.settings["store"]["legal"]["terms"].startswith("Devoluciones")  # Mi Tienda no tenia texto propio
    assert t.settings["fygaro"]["products"][A]["created"] is True
    assert t.settings["fygaro"]["products"][D]["created"] is False

    media_before = db_session.scalar(select(func.count()).select_from(Media))
    again = fygaro_import.run(db_session, t, _catalog(), apply=True, images_dir=imgs, download_images=False)
    db_session.commit()
    s = again["summary"]
    assert s["nuevo"] == 0 and s["actualizar"] == 0 and s["sin_cambios"] == 7  # segunda corrida: nada que hacer
    assert db_session.scalar(select(func.count()).select_from(Product)) == before + 5
    assert db_session.scalar(select(func.count()).select_from(Media)) == media_before
    assert all(r["match"] == "fygaro" for r in again["rows"])


def test_update_prices_flag_overrides_existing_price(db_session, tmp_path):
    t = _tenant(db_session)
    fygaro_import.run(db_session, t, _catalog(), apply=True, images_dir=_images_dir(tmp_path), update_prices=True, download_images=False)
    db_session.commit()
    nvr = db_session.scalar(select(Product).where(Product.code == "NVR-8CH"))
    assert Decimal(str(nvr.price)) == Decimal(350) and nvr.currency == "USD"


def test_storefront_shows_imported_catalog_in_store_currency(client, auth, db_session, tmp_path):
    fygaro_import.run(db_session, _tenant(db_session), _catalog(), apply=True, images_dir=_images_dir(tmp_path), download_images=False)
    db_session.commit()
    client.put("/store", json={"kind": "tienda", "published": True, "domain": "tienda.crimsoncr.com"})

    assert client.get("/public/store-domain", params={"host": "tienda.crimsoncr.com"}).json() == {"slug": "crimson"}
    assert client.get("/public/store-domain", params={"host": "otra.com"}).status_code == 404
    home = client.get("/public/store/crimson").json()
    assert any(c["name"].lower() == "seguridad y acceso" and c["count"] >= 1 for c in home["categories"])

    r = client.get("/public/store/crimson/products", params={"q": "reconocimiento facial hikvision", "limit": 5, "compact": True})
    assert r.headers["x-total-count"] == "1"
    p = r.json()[0]
    # tienda en colones: USD 236.30 x 512.35 (venta del dia) x 1.13 de IVA
    assert p["display_currency"] == "CRC" and Decimal(str(p["display_price"])) == (Decimal("236.3") * Decimal("512.35") * Decimal("1.13")).quantize(
        Decimal("0.01")
    )
    assert p["category"].lower() == "seguridad y acceso" and len(p["images"]) == 2
    # busqueda sin tildes
    assert client.get("/public/store/crimson/products", params={"q": "camara domo"}).json()

    # enlaces viejos de Fygaro -> ids propios
    assert client.get(f"/public/store/crimson/legacy/product/{A}").json() == {"product_id": p["id"]}
    assert client.get("/public/store/crimson/legacy/product/no-existe").status_code == 404
    assert "category_id" in client.get("/public/store/crimson/legacy/category/12836").json()

    # tienda en dolares: el precio exhibido es el de Fygaro con IVA y el pedido sale en USD
    client.put("/store", json={"currency": "USD"})
    p = client.get(f"/public/store/crimson/products/{p['id']}").json()
    assert p["display_currency"] == "USD" and Decimal(str(p["display_price"])) == Decimal("267.02")
    body = {"lines": [{"product_id": p["id"], "quantity": 1}], "contact": {"name": "Ana", "email": "ana@crimsonapp.com"}}
    q = client.post("/public/store/crimson/quote", json=body).json()
    assert q["currency"] == "USD" and Decimal(str(q["total"])).quantize(Decimal("0.01")) == Decimal("267.02")
    # las tarifas de envio estan en colones: en una tienda en dolares se convierten (3500 / 512.35)
    ship = client.post("/public/store/crimson/quote", json={**body, "shipping_method": "Envío GAM"}).json()
    assert Decimal(str(ship["shipping"])) == Decimal("6.83")
    gam = next(r for r in client.get("/public/store/crimson").json()["shipping_rates"] if r["name"] == "Envío GAM")
    assert Decimal(str(gam["display_amount"])) == Decimal("7.72")  # 6.831 x 1.13, con IVA
    o = client.post("/public/store/crimson/checkout", json=body)
    assert o.status_code == 201 and o.json()["currency"] == "USD" and Decimal(str(o.json()["total"])).quantize(Decimal("0.01")) == Decimal("267.02")


def test_cli_dry_run_by_default(db_session, monkeypatch, capsys, tmp_path):
    from app.tools import import_fygaro as cli

    class _S:
        def __enter__(self):
            return db_session

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli, "SessionLocal", lambda: _S())
    before = db_session.scalar(select(func.count()).select_from(Product))
    out = tmp_path / "rep.json"
    assert cli.main([str(SAMPLE), "--tenant", "crimson", "--report", str(out)]) == 0
    assert "VISTA PREVIA" in capsys.readouterr().out
    assert db_session.scalar(select(func.count()).select_from(Product)) == before
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["nuevo"] == 5


def test_rate_detection_survives_cent_rounding():
    r = fygaro_import._rate
    assert r({"price": 0.25, "price_with_tax": 0.28, "tax_rate": 12.0}) == (13, None)  # el 12 % deducido es solo redondeo
    rate, warn = r({"price": 1.0, "price_with_tax": 1.0, "tax_rate": 0.0})  # 0 % y 1 % redondean igual: manda lo deducido
    assert rate == 0 and warn.startswith("Fygaro lo vendia SIN IVA")
    assert r({"price": 267.81, "price_with_tax": 305.31, "tax_rate": 14.0})[1].startswith("IVA deducido 14")
    assert fygaro_import.SERVICE_RE.search("Instalación de cámara") and not fygaro_import.SERVICE_RE.search("Kit de mantenimiento SPROTEK")


def test_crawler_parses_public_product_page():
    from app.tools.fygaro_crawl import parse_product

    html = (Path(__file__).parent / "fixtures" / "fygaro_product.html").read_text(encoding="utf-8")
    p = parse_product("0ca92781", html, "https://tienda.crimsoncr.com")
    assert p["sku"] == "DS-K1T344EBFWX-E1" and p["currency"] == "USD"
    assert p["price"] == 236.3 and p["price_with_tax"] == 267.02 and p["tax_rate"] == 13.0  # sin IVA (JSON-LD) vs exhibido
    assert p["name"].startswith("CONTROL DE ACCESO") and p["description"] == "Reconocimiento facial,\nlector de huellas & PIN"
    assert len(p["images"]) == 2 and p["availability"] == "disponible" and p["variants"] == []
