"""Hora de Costa Rica en el worker (plan 1.2): el cierre de caja de las 21:00 CR corre a las 03:00 UTC del dia siguiente
y antes reportaba "mañana"; el vencimiento de facturas usa el mismo dia de Costa Rica."""

import os
import sys
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import timeutil
from app.models import EmailOutbox, Invoice, Payment, Tenant

# 03:00 UTC del 11 de marzo = 21:00 CR del 10 de marzo (lejos de la fecha real: date.today() nunca coincide)
RELOJ = datetime(2026, 3, 11, 3, 0, tzinfo=UTC)
DIA_CR = date(2026, 3, 10)


@pytest.fixture
def worker(db_session, monkeypatch):
    pytest.importorskip("celery")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(timeutil, "_utcnow", lambda: RELOJ)
    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx(db_session))
    return celery_app


def test_reloj_de_costa_rica():
    assert timeutil.cr_today() == timeutil.cr_now().date()
    a, b = timeutil.cr_day_bounds(DIA_CR)
    assert a == datetime(2026, 3, 10, 6, 0, tzinfo=UTC) and (b - a).days == 1


def test_cr_now_reexportado_desde_el_proveedor(monkeypatch):
    from app.providers.einvoice import cr_now

    monkeypatch.setattr(timeutil, "_utcnow", lambda: RELOJ)
    assert cr_now().date() == DIA_CR and cr_now().utcoffset().total_seconds() == -6 * 3600


def test_cierre_de_las_21_reporta_el_dia_de_costa_rica(worker, db_session):
    t = db_session.scalar(select(Tenant).order_by(Tenant.id))
    t.settings = {**(t.settings or {}), "daily_close_email": True}
    inv = Invoice(tenant_id=t.id, number="FEC-000777", issue_date=DIA_CR, total=Decimal("26000"), balance=0, status="pagada")
    db_session.add(inv)
    db_session.flush()
    db_session.add_all(
        [
            Payment(tenant_id=t.id, invoice_id=inv.id, method="sinpe", amount=Decimal("15000"), paid_at=DIA_CR),
            # un cobro con fecha del 11 de marzo (el dia siguiente) no entra en el cierre del 10
            Payment(tenant_id=t.id, invoice_id=inv.id, method="efectivo", amount=Decimal("11000"), paid_at=date(2026, 3, 11)),
        ]
    )
    db_session.commit()
    assert worker.daily_close_email() >= 1
    correos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "report")).all()
    assert correos and all(str(DIA_CR) in c.subject for c in correos)
    assert "15000" in correos[0].html and "11000" not in correos[0].html
    # la tarea corre dos veces (dos beats, un reintento): el cierre no sale dos veces
    worker.daily_close_email()
    assert len(db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "report")).all()) == len(correos)


def test_cobro_de_la_noche_entra_en_el_cierre_de_su_dia(worker, db_session, monkeypatch):
    """Un cobro sin fecha (ONVO, pedido web, POS) a las 19:30 CR (01:30 UTC del dia siguiente) queda con el dia de
    Costa Rica, no con el UTC del contenedor: entra en el cierre de las 21:00 de ese mismo dia."""
    from app.schemas.sales import PaymentIn
    from app.services.documents import add_payment

    monkeypatch.setattr(timeutil, "_utcnow", lambda: datetime(2026, 3, 11, 1, 30, tzinfo=UTC))
    t = db_session.scalar(select(Tenant).order_by(Tenant.id))
    t.settings = {**(t.settings or {}), "daily_close_email": True}
    inv = Invoice(tenant_id=t.id, number="FEC-000781", issue_date=DIA_CR, total=Decimal("12500"), balance=Decimal("12500"), status="enviada")
    db_session.add(inv)
    db_session.flush()
    p = add_payment(db_session, t.id, None, inv, PaymentIn(method="sinpe", amount=Decimal("12500")))
    db_session.commit()
    assert p.paid_at == DIA_CR
    monkeypatch.setattr(timeutil, "_utcnow", lambda: RELOJ)  # 21:00 CR del mismo dia
    assert worker.daily_close_email() >= 1
    correos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "report")).all()
    assert correos and all(str(DIA_CR) in c.subject for c in correos) and "12500" in correos[0].html


def test_vencimiento_con_el_dia_de_costa_rica(worker, db_session):
    """sales.mark_overdue vive en app/tasks/cobranza.py (plan 1.3) y vence solo facturas que le llegaron al cliente
    (sent_at): a las 21:00 CR todavia es el dia de vencimiento, aunque en UTC ya sea el dia siguiente."""
    from app.tasks.cobranza import mark_overdue

    t = db_session.scalar(select(Tenant).order_by(Tenant.id))
    enviada = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
    hoy = Invoice(tenant_id=t.id, number="FEC-000778", issue_date=DIA_CR, due_date=DIA_CR, status="parcial", balance=Decimal("100"), sent_at=enviada)
    ayer = Invoice(
        tenant_id=t.id, number="FEC-000779", issue_date=DIA_CR, due_date=date(2026, 3, 9), status="parcial", balance=Decimal("100"), sent_at=enviada
    )
    db_session.add_all([hoy, ayer])
    db_session.commit()
    mark_overdue(db_session, today=DIA_CR)
    db_session.refresh(hoy)
    db_session.refresh(ayer)
    assert ayer.status == "vencida"
    assert hoy.status == "parcial"  # a las 21:00 CR todavia es su dia de vencimiento


# El recordatorio con el dia de Costa Rica y una sola vez por etapa (doble disparo del beat) se prueba en
# tests/test_cobranza_secuencia.py::test_corrida_diaria_con_la_fecha_de_costa_rica: sales.reminders es ahora la
# secuencia de cobro del plan 1.3 (app/tasks/cobranza.py).


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False
