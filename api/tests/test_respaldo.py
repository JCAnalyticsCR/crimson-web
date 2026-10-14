"""Respaldo y recuperacion (secciones 16 y 18): ida y vuelta completa a otra base SQLite, por empresa y desde el portal."""

import json
import zipfile
from io import BytesIO

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, get_db, make_engine
from app.main import create_app
from app.models import Category, Customer, FuelLoad, Invoice, Media, Quote, Tenant, Vehicle
from app.services import backup as bk
from app.tools import restore as restore_tool
from tests.conftest import ADMIN
from tests.test_session4 import _user_with_role
from tests.test_vehiculos import _setup

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"datos-de-prueba"


def _seed_mas(client, db_session):
    """Datos de prueba de todo tipo: FK ciclicas (cotizacion <-> factura), auto-referencia (categorias),
    binarios (multimedia), vehiculos con exclusiones y una SEGUNDA empresa que no debe colarse."""
    p, s, _ = _setup(client)
    m = client.post("/media", files={"file": ("comprobante.png", PNG, "image/png")})
    assert m.status_code == 201, m.text
    load = client.get("/vehicles/fuel").json()[0]
    body = {k: load[k] for k in ("vehicle_id", "date", "liters", "amount", "currency", "odometer", "full_tank", "excluded", "exclusion_reason")}
    assert client.put(f"/vehicles/fuel/{load['id']}", json={**body, "receipt_media_id": m.json()["id"]}).status_code == 200
    cust = client.get("/customers").json()["items"][0]
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]}).json()
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    tid = db_session.scalar(sa.select(Tenant.id).where(Tenant.slug == "crimson"))
    padre = Category(tenant_id=tid, name="Padre de prueba")
    db_session.add(padre)
    db_session.flush()
    db_session.add(Category(tenant_id=tid, name="Hija de prueba", parent_id=padre.id))
    otra = Tenant(slug="otra-empresa", name="Otra empresa de prueba", default_currency="CRC", settings={"inbox": {"password_enc": "x"}})
    db_session.add(otra)
    db_session.flush()
    db_session.add(Customer(tenant_id=otra.id, name="Cliente ajeno de prueba", id_type="fisica", currency="CRC"))
    db_session.add(Vehicle(tenant_id=otra.id, name="Vehiculo ajeno", owner="empresa"))
    db_session.commit()
    return {"tid": tid, "otra": otra.id, "invoice": inv["id"], "quote": q["id"], "vehicle_excluido": s, "media": m.json()["id"]}


def _counts(conn) -> dict[str, int]:
    return {t.name: conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one() for t in Base.metadata.sorted_tables if t.name not in bk.SKIP_TABLES}


def test_ida_y_vuelta_toda_la_base(client, auth, db_session, tmp_path):
    ids = _seed_mas(client, db_session)
    zpath = tmp_path / "respaldo.zip"
    m = bk.write_to_path(db_session.connection(), Base.metadata, zpath)
    db_session.commit()
    assert m["scope"] == "all" and m["totals"]["blobs"] >= 1 and not m["redacted"]
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        assert {"manifest.json", "data/vehicle.json", "data/fuel_load.json", "media_index.csv"} <= names
        assert "data/refresh_token.json" not in names  # las sesiones nunca se respaldan
        assert "comprobante.png" in z.read("media_index.csv").decode()

    target = f"sqlite:///{(tmp_path / 'destino.db').as_posix()}"
    assert restore_tool.main([str(zpath), "--target", target]) == 0
    # se niega a restaurar sobre una base con datos
    assert restore_tool.main([str(zpath), "--target", target]) == 4

    eng = make_engine(target)
    with eng.connect() as c:
        dst = _counts(c)
    src = _counts(db_session.connection())
    assert dst == src
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S() as t:
        assert t.get(Invoice, ids["invoice"]).quote_id == ids["quote"]  # FK ciclica completada al final
        assert t.get(Quote, ids["quote"]).converted_invoice_id == ids["invoice"]
        hija = t.scalar(sa.select(Category).where(Category.name == "Hija de prueba"))
        assert hija.parent_id == t.scalar(sa.select(Category.id).where(Category.name == "Padre de prueba"))
        v = t.get(Vehicle, ids["vehicle_excluido"])
        assert v.excluded_from_analysis and v.exclusion_reason == "vehiculo personal"
        assert t.get(Media, ids["media"]).data == PNG
        assert t.scalar(sa.select(sa.func.count()).select_from(FuelLoad).where(FuelLoad.excluded)) == 1
        assert sorted(r[0] for r in t.execute(sa.text("SELECT version_num FROM alembic_version")))  # migracion marcada

    # la base restaurada FUNCIONA: el admin entra con su contrasena y el reporte da lo mismo
    app = create_app()
    sess = S()
    app.dependency_overrides[get_db] = lambda: sess
    c2 = TestClient(app)
    tok = c2.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).json()["access_token"]
    c2.headers["Authorization"] = f"Bearer {tok}"
    q = {"from": "2026-07-01", "to": "2026-08-31"}
    assert c2.get("/vehicles/report", params=q).json()["totals"] == client.get("/vehicles/report", params=q).json()["totals"]
    sess.close()
    eng.dispose()


def test_respaldo_alterado_no_se_restaura(client, auth, db_session, tmp_path):
    _seed_mas(client, db_session)
    zpath = tmp_path / "r.zip"
    bk.write_to_path(db_session.connection(), Base.metadata, zpath)
    bad = tmp_path / "alterado.zip"
    with zipfile.ZipFile(zpath) as zin, zipfile.ZipFile(bad, "w") as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "data/fuel_load.json":
                data = data.replace(b"30000", b"99999")
            zout.writestr(it, data)
    target = f"sqlite:///{(tmp_path / 'x.db').as_posix()}"
    assert restore_tool.main([str(bad), "--target", target]) == 3


def test_respaldo_de_una_empresa_no_trae_otras(client, auth, db_session, tmp_path):
    ids = _seed_mas(client, db_session)
    tenant = db_session.get(Tenant, ids["tid"])
    zpath = tmp_path / "empresa.zip"
    m = bk.write_to_path(db_session.connection(), Base.metadata, zpath, tenant=tenant)
    assert m["scope"] == "tenant" and m["tenant"]["slug"] == "crimson"
    assert m["tables"]["tenant"]["rows"] == 1
    target = f"sqlite:///{(tmp_path / 'empresa.db').as_posix()}"
    assert restore_tool.main([str(zpath), "--target", target]) == 0
    eng = make_engine(target)
    with sessionmaker(bind=eng)() as t:
        assert t.scalar(sa.select(sa.func.count()).select_from(Tenant)) == 1
        assert t.scalar(sa.select(sa.func.count()).select_from(Customer).where(Customer.name == "Cliente ajeno de prueba")) == 0
        assert t.scalar(sa.select(sa.func.count()).select_from(Vehicle)) == 2
        # las lineas (sin tenant_id) viajan con su documento
        src_lines = db_session.execute(sa.text("SELECT count(*) FROM invoice_line il JOIN invoice i ON i.id = il.invoice_id WHERE i.tenant_id = :t"), {"t": ids["tid"]}).scalar()
        assert t.execute(sa.text("SELECT count(*) FROM invoice_line")).scalar() == src_lines > 0
    eng.dispose()


def test_exportar_todos_mis_datos_desde_ajustes(client, auth, db_session):
    ids = _seed_mas(client, db_session)
    admin_h = dict(client.headers)
    r = client.get("/settings/data-export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(BytesIO(r.content))
    m = json.loads(z.read("manifest.json"))
    assert m["scope"] == "tenant" and m["redacted"] and r.headers["x-backup-sha256"] == m["sha256"]
    bk.verify_zip(z, m)
    users = json.loads(z.read("data/user.json"))
    col = users["columns"].index("password_hash")
    assert all(u[col].startswith("!redactado") for u in users["rows"])  # sin contrasenas
    tenants = json.loads(z.read("data/tenant.json"))
    assert len(tenants["rows"]) == 1 and "otra-empresa" not in json.dumps(tenants)
    assert "Cliente ajeno" not in z.read("data/customer.json").decode()
    # queda en la bitacora
    log = db_session.execute(sa.text("SELECT action, entity_id FROM audit_log WHERE action = 'export_all'")).all()
    assert log and log[-1][1] == ids["tid"]

    pw = _user_with_role(db_session, ids["tid"], "conta-export@ejemplo.com", "contabilidad")
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": "conta-export@ejemplo.com", "password": pw}).json()["access_token"]
    assert client.get("/settings/data-export", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    client.headers.update(admin_h)


def test_herramientas_de_linea_de_comandos(tmp_path):
    """Como se corre de verdad (cron): procesos aparte, base en archivo, sin nada importado de antemano."""
    import subprocess
    import sys
    from pathlib import Path

    from app.seeds import seed

    src = f"sqlite:///{(tmp_path / 'origen.db').as_posix()}"
    eng = make_engine(src)
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as s:
        seed(s, *ADMIN)
        tid = s.scalar(sa.select(Tenant.id))
        s.add(Vehicle(tenant_id=tid, name="Vehiculo CLI", owner="empresa"))
        s.commit()
    eng.dispose()
    api_dir = Path(__file__).resolve().parents[1]
    out = tmp_path / "cli.zip"
    r = subprocess.run([sys.executable, "-m", "app.tools.backup", "--database-url", src, "--salida", str(out)], cwd=api_dir, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK " in r.stdout and out.exists()
    m = json.loads(zipfile.ZipFile(out).read("manifest.json"))
    assert m["tables"]["vehicle"]["rows"] == 1 and m["totals"]["tables"] > 50
    dst = f"sqlite:///{(tmp_path / 'destino.db').as_posix()}"
    r = subprocess.run([sys.executable, "-m", "app.tools.restore", str(out), "--target", dst], cwd=api_dir, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK restaurado y verificado" in r.stdout
