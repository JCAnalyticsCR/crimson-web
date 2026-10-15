"""Revision final de la rama reunion-andres: hallazgos de revisores y QA con su prueba."""

import io
import re
import time
import tracemalloc
import zipfile
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from tests.aliados_helpers import interno, uid


# ---------- Bomba XLSX por dimension (tabular.read_grid) ----------
def _xlsx(rows: list[list], dimension: str | None = None) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if dimension is None:
        return data
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="' + dimension.encode() + b'"', body)
            z.writestr(info.filename, body)
    return out.getvalue()


def test_xlsx_con_dimension_falsa_no_rellena_memoria():
    from app.services import tabular as tb

    data = _xlsx([["Código", "Nombre"], ["A1", "Uno"], ["A2", "Dos"]], dimension="A1:XFD1048576")
    assert b"XFD1048576" in zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml")
    tracemalloc.start()
    t0 = time.perf_counter()
    grid = tb.read_grid(data, "bomba.xlsx")
    dt = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert grid == [(1, ["Código", "Nombre"]), (2, ["A1", "Uno"]), (3, ["A2", "Dos"])]
    assert peak < 30 * 1024 * 1024 and dt < 10  # antes: ~145 GB con esta dimension
    assert tb.read_table(data, "bomba.xlsx") == [{"codigo": "A1", "nombre": "Uno"}, {"codigo": "A2", "nombre": "Dos"}]


def test_xlsx_topes_de_filas_y_de_zip():
    from app.services import tabular as tb

    with pytest.raises(tb.TooManyRows):
        tb.read_grid(_xlsx([["x", i] for i in range(30)]), "muchas.xlsx", max_rows=20)
    with pytest.raises(tb.TooManyRows):
        tb.read_grid(("a;b\n" + "".join(f"{i};{i}\n" for i in range(30))).encode(), "muchas.csv", max_rows=20)
    # una parte que declara descomprimir mas de lo permitido (o con relacion de compresion absurda) se rechaza sin abrirla
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", b"<a>" + b" " * (5 * 1024 * 1024) + b"</a>")
    with pytest.raises(tb.TableTooLarge):
        tb.read_grid(buf.getvalue(), "zip.xlsx")


def test_importar_recepciones_tabla_con_tope(client, auth):
    from app.services import reception_import

    grande = b"Clave;Fecha\n" + b"1;2\n" * (reception_import.MAX_TABLE // 4 + 10)
    r = client.post("/import/recepciones", params={"commit": False}, files={"file": ("grande.csv", grande, "text/csv")})
    assert r.status_code == 413
    bomba = _xlsx([["Clave", "Fecha"], ["1" * 50, "2026-09-10"]], dimension="A1:XFD1048576")
    r = client.post("/import/recepciones", params={"commit": False}, files={"file": ("bomba.xlsx", bomba, "application/octet-stream")})
    assert r.status_code == 200, r.text


# ---------- Comisiones ----------

def _cust(client):
    return client.get("/customers").json()["items"][0]


def _quote(client, unit=200000, **extra):
    r = client.post("/quotes", json={"customer_id": _cust(client)["id"], "lines": [{"name": "Kit", "quantity": 1, "unit_price": unit, "tax_rate": 13}], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _acepta(client, qid):
    r = client.post(f"/quotes/{qid}/acceptance", json={"status": "aceptada", "contact_name": "Persona Cliente", "channel": "correo"})
    assert r.status_code == 200, r.text


def _vivas(client, **params):
    return [f for f in client.get("/commissions", params=params).json()["rows"] if f["status"] != "anulada"]


def _rule(client, **body):
    r = client.post("/commissions/rules", json=body)
    assert r.status_code == 201, r.text


def test_cambio_de_vendedor_tras_aceptar_no_duplica_la_comision(client, auth, db_session):
    from app.models import Quote

    tid = auth["tenant"]["id"]
    va = uid(client, interno(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas"))
    vb = uid(client, interno(client, db_session, tid, "vendedor.b@ejemplo.com", "ventas"))
    _rule(client, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, seller_id=va)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    assert [(f["user_id"], D(str(f["amount"]))) for f in _vivas(client)] == [(va, D("10000.00"))]
    # la oportunidad se reasigna (o se liga un levantamiento) y la cotizacion pasa a otro vendedor antes de convertir
    db_session.get(Quote, q["id"]).seller_id = vb
    db_session.commit()
    assert client.post(f"/quotes/{q['id']}/convert").status_code in (200, 201)
    assert [(f["user_id"], D(str(f["amount"]))) for f in _vivas(client)] == [(va, D("10000.00"))]


def test_anulada_a_mano_no_vuelve_al_convertir(client, auth, db_session):
    tid = auth["tenant"]["id"]
    va = uid(client, interno(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas"))
    _rule(client, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, seller_id=va)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    cid = _vivas(client)[0]["id"]
    assert client.patch("/commissions", json={"ids": [cid], "status": "anulada", "reason": "Venta compartida: se paga aparte"}).status_code == 200
    assert client.post(f"/quotes/{q['id']}/convert").status_code in (200, 201)
    assert client.post(f"/quotes/{q['id']}/project", json={}).status_code == 201
    assert _vivas(client) == []


def test_lo_devengado_al_aceptar_entra_a_la_economia_del_proyecto(client, auth, db_session):
    from app.models import Commission

    tid = auth["tenant"]["id"]
    va = uid(client, interno(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas"))
    _rule(client, name="Al aceptar", percent=5, trigger="cotizacion_aceptada")
    q = _quote(client, unit=1000000, seller_id=va)
    client.post(f"/quotes/{q['id']}/send")
    _acepta(client, q["id"])
    c = db_session.scalars(select(Commission)).one()
    assert c.project_id is None
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    db_session.expire_all()
    assert db_session.get(Commission, c.id).project_id == pr["id"]
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert D(str(eco["commissions"])) == D("50000.00")


def test_terminar_la_ultima_ot_no_reabre_un_proyecto_cancelado(client, auth, db_session):
    from app.models import Project, WorkOrder

    tid = auth["tenant"]["id"]
    va = uid(client, interno(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas"))
    _rule(client, user_id=va, name="Al completar", percent=10, trigger="proyecto_completado")
    q = _quote(client, unit=500000, seller_id=va)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    body = {"name": pr["name"], "status": "terminado"}
    assert client.put(f"/projects/{pr['id']}", json=body).status_code == 200
    assert len(_vivas(client)) == 1
    assert client.put(f"/projects/{pr['id']}", json={**body, "status": "cancelado"}).status_code == 200
    assert _vivas(client) == []
    ot = db_session.scalars(select(WorkOrder).where(WorkOrder.project_id == pr["id"])).first()
    r = client.post(f"/work-orders/{ot.id}/finish", json={})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Project, pr["id"]).status == "cancelado"
    assert _vivas(client) == []  # no vuelve a devengar saltandose la aprobacion de comisiones


def test_papelera_no_se_salta_las_comisiones_del_proyecto(client, auth, db_session):
    tid = auth["tenant"]["id"]
    va = uid(client, interno(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas"))
    _rule(client, user_id=va, name="Al completar", percent=10, trigger="proyecto_completado")
    q = _quote(client, unit=500000, seller_id=va)
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    assert client.put(f"/projects/{pr['id']}", json={"name": pr["name"], "status": "terminado"}).status_code == 200
    sh = interno(client, db_session, tid, "supervisora@ejemplo.com", "supervisor")
    r = client.post(f"/archive/project/{pr['id']}/trash", headers=sh)
    assert r.status_code == 409 and "comisiones" in r.text
    assert len(_vivas(client)) == 1


def test_backfill_no_duplica_el_cobro_de_quien_hizo_la_factura(client, auth, db_session):
    from app.models import Commission, Invoice

    tid = auth["tenant"]["id"]
    va = uid(client, interno(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas"))
    vb = uid(client, interno(client, db_session, tid, "vendedor.b@ejemplo.com", "ventas"))
    _rule(client, name="Todos", percent=5)
    inv = client.post("/invoices", json={"customer_id": _cust(client)["id"], "seller_id": va, "lines": [{"name": "Kit", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}]}).json()
    assert client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 1130000, "kind": "captura"}).status_code == 201
    c = db_session.scalars(select(Commission)).one()
    # comision vieja de main: clave con quien hizo la factura; la migracion le puso otro vendedor responsable
    db_session.get(Invoice, inv["id"]).seller_id = vb
    c.source_key = f"cobro:{c.payment_id}:v:{va}"
    db_session.commit()
    r = client.post("/commissions/backfill", json={"from": str(date.today()), "dry_run": False})
    assert r.status_code == 200 and r.json()["creados"] == 0, r.text


# ---------- Variables de entorno: vacias o raras no tumban el arranque ----------
def test_variables_vacias_o_raras_no_tumban_settings(monkeypatch):
    from app.core.config import Settings

    for k in ("HACIENDA_PRODUCTION_ALLOWED", "ALLY_MEDIA_QUOTA_MB", "HACIENDA_ENVIRONMENT", "RECEPTION_IMAP_PORT", "FISCAL_KEYS", "EINVOICE_PROVIDER"):
        monkeypatch.setenv(k, "")
    s = Settings(_env_file=None)
    assert s.hacienda_production_allowed is False and s.ally_media_quota_mb == 1024 and s.hacienda_environment == "pruebas"
    assert s.reception_imap_port == 993 and s.fiscal_keys is None and s.einvoice_provider == "none"
    for raw, esperado in (("Sandbox", "sandbox"), (" gti ", "gti"), ("manual", "none"), ("hacienda", "none")):
        monkeypatch.setenv("EINVOICE_PROVIDER", raw)
        assert Settings(_env_file=None).einvoice_provider == esperado
    monkeypatch.setenv("HACIENDA_ENVIRONMENT", "prod")
    assert Settings(_env_file=None).hacienda_environment == "pruebas"  # nunca produccion por un valor mal escrito
    monkeypatch.setenv("FISCAL_KEYS", "k1:abc")
    assert Settings(_env_file=None).fiscal_keys is not None  # mal escrita: no impide arrancar


def test_fiscal_keys_mal_escrita_deja_sin_llave(client, auth, monkeypatch):
    from pydantic import SecretStr

    from app.core import secretbox
    from app.core.config import settings

    monkeypatch.setattr(settings, "fiscal_keys", SecretStr("k1:abc"))
    monkeypatch.setattr(settings, "env", "staging")
    assert secretbox.keys() == [] and not secretbox.available()
    r = client.put("/settings/inbox", json={"enabled": True, "host": "imap.ejemplo.com", "user": "f@ejemplo.com", "password": "clave-123", "admin_password": "Crimson-2026-seguro"})
    assert r.status_code == 503 and "FISCAL_KEYS" in r.json()["detail"]


# ---------- Buzon IMAP de Ajustes ----------
INBOX = {"enabled": True, "host": "imap.ejemplo.com", "port": 993, "user": "facturas@ejemplo.com", "folder": "INBOX"}


def test_buzon_pide_reautenticacion_y_la_contrasena_al_cambiar_de_servidor(client, auth, db_session):
    from app.models import AuditLog

    pw = "Crimson-2026-seguro"
    assert client.put("/settings/inbox", json={**INBOX, "password": "clave-app-123"}).status_code == 403  # sin reautenticar
    r = client.put("/settings/inbox", json={**INBOX, "password": "clave-app-123", "admin_password": pw})
    assert r.status_code == 200 and r.json()["has_password"]
    # encender o apagar, o cambiar la carpeta, no pide nada
    assert client.put("/settings/inbox", json={**INBOX, "enabled": False, "folder": "Facturas"}).status_code == 200
    # otro servidor u otro usuario: la contrasena guardada no viaja sola
    r = client.put("/settings/inbox", json={**INBOX, "host": "imap.atacante.example", "admin_password": pw})
    assert r.status_code == 422 and "contraseña" in r.json()["detail"]
    assert client.put("/settings/inbox", json={**INBOX, "port": 143, "password": "otra", "admin_password": "mala"}).status_code == 403
    for host in ("127.0.0.1", "10.0.0.5", "redis.railway.internal", "localhost", "postgres"):
        r = client.put("/settings/inbox", json={**INBOX, "host": host, "password": "x", "admin_password": pw})
        assert r.status_code == 422, host
    r = client.put("/settings/inbox", json={**INBOX, "host": "imap.otro.example", "password": "otra-clave", "admin_password": pw})
    assert r.status_code == 200
    filas = db_session.query(AuditLog).filter(AuditLog.entity == "inbox").all()
    assert filas and not any("otra-clave" in str(f.diff) or "clave-app-123" in str(f.diff) for f in filas)
    assert any("imap.otro.example" in str(f.diff) for f in filas)


def test_buzon_error_de_conexion_sin_detalle_y_solo_direcciones_publicas(client, auth, db_session, monkeypatch):
    from app.core.config import settings
    from app.services import inbox

    client.put("/settings/inbox", json={**INBOX, "password": "clave-app-123", "admin_password": "Crimson-2026-seguro"})
    real_connect = inbox._connect

    def rehusada(cfg):
        raise ConnectionRefusedError("[Errno 111] Connection refused 10.250.3.4:6379")

    monkeypatch.setattr(inbox, "_connect", rehusada)
    for url in ("/settings/inbox/run", "/reception/inbox/run"):
        r = client.post(url)
        assert r.status_code == 502 and "10.250" not in r.text and "Errno" not in r.text
    with pytest.raises(OSError):
        inbox.public_address("127.0.0.1", 993)
    with pytest.raises(OSError):
        inbox.public_address("10.0.0.5", 993)
    assert inbox.public_address("8.8.8.8", 993) == "8.8.8.8"
    # el buzon de Ajustes nunca se conecta a una direccion interna (ni aunque el nombre resuelva a una)
    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(OSError, match="pública"):
        real_connect(inbox.MailboxConfig("ajustes", "127.0.0.1", 993, "u", "p", "INBOX"))


def test_contrasena_heredada_del_buzon_pasa_a_fiscal_keys(client, auth, db_session):
    from app.core import crypto
    from app.models import Tenant
    from app.services.inbox import decrypt_password, upgrade_legacy_passwords

    t = db_session.get(Tenant, auth["tenant"]["id"])
    t.settings = {**(t.settings or {}), "inbox": {**INBOX, "password_enc": crypto.encrypt("clave-vieja")}}
    db_session.commit()
    assert upgrade_legacy_passwords(db_session) == {"migradas": 1, "ilegibles": 0}
    db_session.commit()
    db_session.refresh(t)
    tok = t.settings["inbox"]["password_enc"]
    assert tok.startswith("v1.") and decrypt_password(t.id, tok) == "clave-vieja"
    assert upgrade_legacy_passwords(db_session) == {"migradas": 0, "ilegibles": 0}  # idempotente


def test_xml_con_doctype_tardio_se_rechaza():
    from app.providers.hacienda import xml44

    tarde = b'<?xml version="1.0"?><!--' + b"x" * 5000 + b'--><!DOCTYPE r [<!ENTITY e SYSTEM "file:///etc/passwd">]><r>&e;</r>'
    with pytest.raises(xml44.XmlRejected, match="DTD"):
        xml44.parse_xml_bytes(tarde)
    assert xml44.parse_xml_bytes(b'<?xml version="1.0"?><!--' + b"x" * 5000 + b"--><r>ok</r>").text == "ok"


# ---------- Nota de credito y anulacion con emision directa ----------
def _factura_domo(client):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    r = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_nc_de_una_factura_simulada_no_va_a_hacienda(client, auth, db_session, monkeypatch, p12_empresa):
    from app.models import EInvoiceDocument
    from tests.hacienda_utils import FakeHacienda, modo_pruebas

    client.put("/settings", json={"einvoice_provider": "sandbox"})
    sim = _factura_domo(client)
    assert client.post(f"/invoices/{sim['id']}/emit").json()["mode"] == "simulado"
    # como las de main: proveedor "sandbox" sin modo
    legado = _factura_domo(client)
    client.post(f"/invoices/{legado['id']}/emit")
    d = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == legado["id"]).one()
    d.mode = None
    db_session.commit()
    fake = FakeHacienda(sequence=["aceptado"]).install(monkeypatch)
    modo_pruebas(client, *p12_empresa)
    for inv in (sim, legado):
        r = client.post(f"/invoices/{inv['id']}/credit-note", json={"reason": "Error en cantidad"})
        assert r.status_code == 200, r.text
        nc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"], EInvoiceDocument.doc_type == "NC").one()
        assert nc.mode == "simulado" and nc.status == "aceptada"
        assert client.get(f"/invoices/{inv['id']}").json()["status"] == "anulada"
    assert fake.posts == []  # ninguna NC real que referencie una clave que Hacienda nunca recibio
    # una de produccion no se acredita en pruebas
    from app.services.einvoice import _nc_mode

    with pytest.raises(Exception, match="Producción"):
        _nc_mode(EInvoiceDocument(provider="hacienda", mode="produccion"), "pruebas")


def test_no_se_anula_una_factura_enviada_sin_respuesta(client, auth, db_session, monkeypatch, p12_empresa):
    from app.models import EInvoiceDocument, Invoice
    from tests.hacienda_utils import FakeHacienda, modo_pruebas

    fake = FakeHacienda(sequence=["aceptado"]).install(monkeypatch)
    modo_pruebas(client, *p12_empresa)
    fake.post_plan = [(403, {})]  # tras un POST: "error", Hacienda pudo haberla recibido
    inv = _factura_domo(client)
    assert client.post(f"/invoices/{inv['id']}/emit").status_code == 200
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).one()
    assert doc.status == "error" and doc.attempts == 1
    r = client.post(f"/invoices/{inv['id']}/void")
    assert r.status_code == 409 and "nota de crédito" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Invoice, inv["id"]).status != "anulada"
    # sin emitir se sigue anulando como siempre
    otra = _factura_domo(client)
    assert client.post(f"/invoices/{otra['id']}/void").status_code == 200


# ---------- Migraciones ----------
def _modulo(patron: str):
    import importlib.util
    from pathlib import Path

    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob(patron))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dedupe_de_recepciones_ignora_bom_y_espacios():
    import sqlalchemy as sa

    mod = _modulo("facturacion_*.py")
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE received_document (id INTEGER PRIMARY KEY, tenant_id INTEGER, clave VARCHAR(50), xml_document TEXT, action VARCHAR(12), expense_id INTEGER)"))
        c.execute(sa.text("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id INTEGER, at TIMESTAMP, action VARCHAR(40), entity VARCHAR(40), entity_id INTEGER, diff JSON)"))
        for i, xml in enumerate(["<x>1</x>", "﻿<x>1</x>\r\n", "  <x>1</x> "], start=1):
            c.execute(sa.text("INSERT INTO received_document VALUES (:i, 1, 'A', :x, NULL, NULL)"), {"i": i, "x": xml})
        assert sorted(mod.dedupe_receptions(c)) == [2, 3]


def test_migracion_de_comisiones_no_choca_con_pagos_repetidos():
    from unittest import mock

    import sqlalchemy as sa

    mod = _modulo("comisiones_a3f7c9e2d415_*.py")
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE commission (id INTEGER PRIMARY KEY, tenant_id INTEGER, payment_id INTEGER, user_id INTEGER, source_key VARCHAR(120), amount NUMERIC, amount_crc NUMERIC, fx NUMERIC, currency VARCHAR(3))"))
        c.execute(sa.text("CREATE UNIQUE INDEX uq_commission_source ON commission (tenant_id, source_key)"))
        c.execute(sa.text("CREATE TABLE project (id INTEGER PRIMARY KEY, status VARCHAR(20), completed_at TIMESTAMP, delivered_at TIMESTAMP, updated_at TIMESTAMP)"))
        for i, (pay, user) in enumerate([(2, 1), (2, 1), (3, 1), (None, 1)], start=1):
            c.execute(sa.text("INSERT INTO commission VALUES (:i, 1, :p, :u, NULL, 10, NULL, NULL, 'CRC')"), {"i": i, "p": pay, "u": user})
        fake = mock.MagicMock()
        fake.execute.side_effect = lambda sql: c.execute(sa.text(sql))
        with mock.patch.object(mod, "op", fake):
            mod.upgrade()
        keys = dict(c.execute(sa.text("SELECT id, source_key FROM commission")).all())
    assert keys == {1: "cobro:2:v:1", 2: None, 3: "cobro:3:v:1", 4: None}


# ---------- Detalles baratos ----------
def test_cantidad_en_bodega_se_lee_bien_y_404_con_genero():
    from fastapi import HTTPException

    from app.models import Opportunity
    from app.routers.catalog import qty_label
    from app.services.ally_scope import AllyScope

    assert [qty_label(v) for v in (D("3.000"), D("100"), D("2.50"), 7)] == ["3", "100", "2,5", "7"]
    scope = AllyScope.__new__(AllyScope)
    scope.db = type("DB", (), {"scalar": staticmethod(lambda *_a, **_k: None)})()
    for label, msg in (("Oportunidad", "Oportunidad no encontrada"), ("Orden de trabajo", "Orden de trabajo no encontrada"), ("Proyecto", "Proyecto no encontrado")):
        with pytest.raises(HTTPException) as e:
            scope.one(Opportunity, 1, [], label)
        assert e.value.detail == msg


def test_cuerpo_demasiado_grande_se_corta_antes_de_parsear(client, auth, monkeypatch):
    from app.core import bodylimit

    monkeypatch.setattr(bodylimit, "MAX_BODY", 4096)
    files = [("files", (f"f{i}.xml", b"<a>" + b"x" * 3000 + b"</a>", "text/xml")) for i in range(3)]
    r = client.post("/reception/upload-batch", files=files)
    assert r.status_code == 413 and "demasiado grande" in r.json()["detail"]

    # sin Content-Length (por partes): se cuenta lo recibido
    def trozos():
        for _ in range(4):
            yield b"x" * 2000

    r = client.post("/import/recepciones", content=trozos(), headers={"Content-Type": "multipart/form-data; boundary=zz"})
    assert r.status_code == 413
    assert client.get("/health").status_code == 200
