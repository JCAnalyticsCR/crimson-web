"""Migracion de facturacion: relleno idempotente (dos corridas) y fusion de recepciones duplicadas por clave."""

import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.hacienda_utils import clave, fe_xml


def _migracion():
    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("facturacion_*.py"))
    spec = importlib.util.spec_from_file_location("facturacion_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_relleno_idempotente(client, auth, db_session):
    from app.models import ExchangeRate, Expense, FiscalActivity, FiscalProfile, ReceivedDocument, Recurrence, Tenant

    tid = auth["tenant"]["id"]
    # estado "viejo": sin perfil ni actividades nuevas, recepcion aprobada con la actividad del PROVEEDOR
    db_session.query(FiscalActivity).delete()
    db_session.query(FiscalProfile).delete()
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "activity_codes": ["6202.0", "4321.0", "malo"]}
    gasto = Expense(tenant_id=tid, description="Gasto viejo", date=date(2026, 9, 10), subtotal=100000, tax_rate=13, tax_amount=13000, total=113000, iva_credit="credito")
    usd = Expense(tenant_id=tid, description="Hosting", date=date(2026, 9, 12), currency="USD", subtotal=10, tax_rate=0, tax_amount=0, total=10, iva_credit="no_credito")
    db_session.add_all([gasto, usd, ExchangeRate(date=date(2026, 9, 11), currency="USD", sell=Decimal("505.5"), buy=Decimal("500"))])
    db_session.flush()
    resp = ReceivedDocument(
        tenant_id=tid, clave=clave(fecha="100926"), issue_date=date(2026, 9, 10), action="aceptada", activity_code="4651.0",
        hacienda_status="aprobado", xml_document=fe_xml(clave(fecha="100926"), tipo="TiqueteElectronico"), expense_id=gasto.id,
    )
    pend = ReceivedDocument(tenant_id=tid, clave=clave(fecha="100926", consecutivo="00100001010000000999"), issue_date=date(2026, 9, 30), activity_code="4651.0", hacienda_status="pendiente")
    db_session.add_all([resp, pend])
    rec = Recurrence(tenant_id=tid, name="R", kind="gasto", template={}, next_date=date(2026, 10, 31))
    db_session.add(rec)
    db_session.flush()
    # columnas nuevas en blanco, como quedan justo despues del add_column
    for r in (resp, pend):
        r.issuer_activity_code, r.deadline, r.status = None, None, "por_revisar"
    gasto.tax_credit_amount = usd.tax_credit_amount = None
    gasto.received_document_id = None
    usd.fx_rate = 1
    rec.anchor_day = None
    db_session.commit()

    mod = _migracion()
    mod.backfill(db_session.connection())
    mod.backfill(db_session.connection())  # idempotente
    db_session.commit()
    db_session.expire_all()
    assert db_session.query(FiscalProfile).filter(FiscalProfile.tenant_id == tid).count() == 1
    acts = db_session.query(FiscalActivity).filter(FiscalActivity.tenant_id == tid).order_by(FiscalActivity.id).all()
    assert [a.code for a in acts] == ["6202.0", "4321.0"] and acts[0].is_default and not acts[1].is_default
    r1 = db_session.get(ReceivedDocument, resp.id)
    assert r1.hacienda_status == "simulado" and r1.issuer_activity_code == "4651.0" and r1.status == "respondido" and r1.doc_type == "TE"
    assert r1.activity_code == "4651.0"  # respondida: la actividad queda (no se sabe cual era la propia)
    assert r1.deadline == date(2026, 10, 12)
    r2 = db_session.get(ReceivedDocument, pend.id)
    assert r2.hacienda_status == "sin_respuesta" and r2.status == "por_revisar" and r2.activity_code is None and r2.issuer_activity_code == "4651.0"
    g = db_session.get(Expense, gasto.id)
    assert Decimal(str(g.tax_credit_amount)) == Decimal("13000") and g.received_document_id == resp.id
    u = db_session.get(Expense, usd.id)
    assert Decimal(str(u.fx_rate)) == Decimal("505.5") and Decimal(str(u.tax_credit_amount)) == 0
    assert db_session.get(Recurrence, rec.id).anchor_day == 31


def _db_vieja():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE received_document (id INTEGER PRIMARY KEY, tenant_id INTEGER, clave VARCHAR(50), xml_document TEXT, action VARCHAR(12), expense_id INTEGER)"))
        c.execute(sa.text("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id INTEGER, at TIMESTAMP, action VARCHAR(40), entity VARCHAR(40), entity_id INTEGER, diff JSON)"))
    return eng


def test_duplicados_exactos_se_fusionan_y_los_otros_abortan():
    mod = _migracion()
    eng = _db_vieja()
    with eng.begin() as c:
        for i, (cl, xml, action, exp) in enumerate(
            [("A", "<x/>", None, None), ("A", "<x/>", None, None), ("A", "<x/>", "aceptada", 9), ("B", "<y/>", None, None)], start=1
        ):
            c.execute(sa.text("INSERT INTO received_document VALUES (:i, 1, :c, :x, :a, :e)"), {"i": i, "c": cl, "x": xml, "a": action, "e": exp})
        borrados = mod.dedupe_receptions(c)
    assert sorted(borrados) == [1, 2]  # queda la que tiene respuesta y gasto
    with eng.connect() as c:
        assert [r[0] for r in c.execute(sa.text("SELECT id FROM received_document ORDER BY id"))] == [3, 4]
        assert c.execute(sa.text("SELECT action FROM audit_log")).scalar() == "dedupe_migracion"
    for filas in ([("C", "<x/>", "aceptada", None), ("C", "<x/>", "rechazada", None)], [("D", "<x/>", None, None), ("D", "<otro/>", None, None)]):
        eng = _db_vieja()
        with eng.begin() as c:
            for i, (cl, xml, action, exp) in enumerate(filas, start=1):
                c.execute(sa.text("INSERT INTO received_document VALUES (:i, 1, :c, :x, :a, :e)"), {"i": i, "c": cl, "x": xml, "a": action, "e": exp})
        with eng.begin() as c, pytest.raises(RuntimeError, match="resolver a mano"):
            mod.dedupe_receptions(c)


def test_herramienta_de_duplicados():
    from app.tools.check_reception_dupes import find

    eng = _db_vieja()
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO received_document VALUES (1, 1, 'A', '<x/>', NULL, NULL), (2, 1, 'A', '<x/>', NULL, NULL)"))
    with eng.connect() as c:
        res = find(c)
    assert res == [{"tenant_id": 1, "ids": [1, 2], "auto": True}]
    assert datetime.now(UTC)
