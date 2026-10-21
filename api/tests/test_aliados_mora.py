"""Mora de la mensualidad de aliados (plan 1.4): el aviso al aliado a D+5 lo da la secuencia de cobro y a D+30 aparece
la propuesta de suspension en Aprobaciones. El sistema nunca suspende solo: lo confirma una persona.
Nombres y correos ficticios (repo publico)."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

HOY = date.today()
PAGOS = "pagos@aliado-mora.ejemplo.com"


def _correo_ok(monkeypatch):
    from app.services import documents_send, mail

    def deliver(m, files=None):
        m.status, m.provider_id = "enviado", f"re_{m.id}"

    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(mail, "deliver", deliver)
    monkeypatch.setattr(documents_send, "render_pdf", lambda html: b"%PDF-1.7 prueba")


def _aliado_con_mensualidad(client, db_session, vence_hace: int, enviada: bool = True):
    """Aliado con su plan y una mensualidad que vencio hace `vence_hace` dias (enviada o borrador)."""
    from app.models import AllyCompany, Invoice

    a = client.post("/allies", json={"name": "Aliado Mora S.A.", "tax_id": "3-101-000077", "email": PAGOS}).json()["id"]
    body = {"amount": 30000, "currency": "CRC", "mode": "fija", "next_date": HOY.isoformat(), "crear_cliente": True}
    assert client.put(f"/allies/{a}/plan", json=body).status_code == 200
    comp = db_session.get(AllyCompany, a)
    r = client.post(f"/recurrences/{comp.recurrence_id}/run").json()
    inv = db_session.get(Invoice, r["invoice_id"])
    inv.issue_date = HOY - timedelta(days=vence_hace + 8)
    inv.due_date = HOY - timedelta(days=vence_hace)
    if enviada:
        inv.status, inv.sent_at = "enviada", datetime.now(UTC) - timedelta(days=vence_hace + 8)
    db_session.commit()
    return a, inv.id


def _mora(client) -> list[int]:
    sec = next(x for x in client.get("/approvals", params={"limit": 50}).json()["sections"] if x["key"] == "aliados_en_mora")
    return [i["id"] for i in sec["items"]]


def test_propuesta_de_suspension_a_d_mas_30(client, auth, db_session):
    from app.models import AllyCompany, Invoice

    # holgura de un dia: Aprobaciones usa la fecha de Costa Rica y la prueba puede correr con el reloj en UTC
    a, iid = _aliado_con_mensualidad(client, db_session, vence_hace=28)
    assert a not in _mora(client)  # menos de 30 dias: todavia no
    db_session.get(Invoice, iid).due_date = HOY - timedelta(days=31)
    db_session.commit()
    sec = next(x for x in client.get("/approvals").json()["sections"] if x["key"] == "aliados_en_mora")
    item = next(i for i in sec["items"] if i["id"] == a)
    assert item["badge"] == "Proponer suspensión" and "días de atraso" in item["subtitle"] and item["to"] == f"/aliados/{a}"
    assert float(item["amount"]) == 33900 and "action" not in item  # la suspension se decide en la ficha del aliado
    # el sistema no suspendio a nadie
    assert db_session.get(AllyCompany, a).suspended_at is None
    # una persona confirma: sale de la propuesta
    assert client.post(f"/allies/{a}/suspend", json={"reason": "Mensualidad con más de 30 días de atraso"}).status_code == 200
    assert a not in _mora(client)
    client.post(f"/allies/{a}/reactivate")
    assert a in _mora(client)
    # pagada: sale
    total = client.get(f"/invoices/{iid}").json()["total"]
    client.post(f"/invoices/{iid}/payments", json={"method": "transferencia", "kind": "captura", "amount": total})
    assert a not in _mora(client)


def test_borrador_vencido_no_es_mora(client, auth, db_session):
    a, _ = _aliado_con_mensualidad(client, db_session, vence_hace=45, enviada=False)
    assert a not in _mora(client)


def test_la_fecha_de_corte_deja_fuera_la_mora_vieja(client, auth, db_session):
    """Lo emitido antes de la fecha de corte de cartera (la de los recordatorios) pudo pagarse por fuera: no es motivo
    para proponer una suspension."""
    a, _ = _aliado_con_mensualidad(client, db_session, vence_hace=45)  # emitida hace 53 dias
    assert a in _mora(client)
    assert client.put("/settings", json={"collections_start_date": (HOY - timedelta(days=60)).isoformat()}).status_code == 200
    assert a in _mora(client)
    assert client.put("/settings", json={"collections_start_date": (HOY - timedelta(days=10)).isoformat()}).status_code == 200
    assert a not in _mora(client)


def test_aviso_al_aliado_a_d_mas_5_y_resumen(client, auth, db_session, monkeypatch):
    from app.models import EmailOutbox
    from app.tasks.cobranza import run_reminders, weekly_summary

    _correo_ok(monkeypatch)
    client.put("/settings", json={"notify_due": True, "collections_start_date": (HOY - timedelta(days=120)).isoformat()})
    a, iid = _aliado_con_mensualidad(client, db_session, vence_hace=5)
    assert run_reminders(db_session, today=HOY)["enviados"] == 1
    m = db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == PAGOS).order_by(EmailOutbox.id.desc())).first()
    assert "Mensualidad" in m.subject and "5 días de atraso" in m.subject and "/pagar/" in m.html
    assert [e["stage"] for e in client.get(f"/invoices/{iid}").json()["reminders"]] == ["D+5"]
    # 30 dias despues el resumen de cartera lo lista en mora (propuesta de suspension en Aprobaciones)
    assert weekly_summary(db_session, today=HOY + timedelta(days=25)) == 1
    resumen = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("Cartera de la semana%")).order_by(EmailOutbox.id.desc())).first()
    assert "Aliado Mora S.A." in resumen.html and "propuesta de suspensión" in resumen.html
