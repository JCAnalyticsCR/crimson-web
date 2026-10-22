"""Secuencia de recordatorios de cobro (plan 1.3): D-3, D0, D+3, D+10 con PDF y enlace de pago, una vez por etapa,
solo sobre lo enviado, con fecha de corte de cartera, pausa por cliente y por factura, y corrida en seco que no
escribe nada. Nombres y correos ficticios (repo publico)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.test_cobranza_estados import _cliente, _factura

HOY = date.today()


@pytest.fixture
def correo(monkeypatch):
    """Correo configurado y aceptado por el proveedor; guarda (fila, adjuntos) de cada envio."""
    from app.services import documents_send, mail

    salida: list = []

    def deliver(m, files=None):
        m.status, m.provider_id = "enviado", f"re_{m.id}"
        salida.append((m, [n for n, _ in (files or [])]))

    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(mail, "deliver", deliver)
    monkeypatch.setattr(documents_send, "render_pdf", lambda html: b"%PDF-1.7 prueba")
    return salida


def _encender(client, desde: date = HOY - timedelta(days=90), **extra):
    r = client.put("/settings", json={"notify_due": True, "collections_start_date": desde.isoformat(), **extra})
    assert r.status_code == 200, r.text


def _enviada(db_session, client, cid: int, due: date, hace: int = 20, issue: date | None = None) -> int:
    """Factura enviada al cliente hace `hace` dias (como si el correo hubiera salido entonces)."""
    from app.models import Invoice

    inv = _factura(client, cid, due=due)
    i = db_session.get(Invoice, inv["id"])
    i.sent_at = datetime.now(UTC) - timedelta(days=hace)
    i.status = "enviada"
    if issue:
        i.issue_date = issue
    db_session.commit()
    return inv["id"]


def _pago(db_session, iid: int, monto: Decimal, el: date, provider: str = "manual", method: str = "onvo"):
    """Pago confirmado de la factura en la fecha `el` (provider "onvo" = entro por el enlace de pago)."""
    from app.models import Invoice
    from app.schemas.sales import PaymentIn
    from app.services.documents import add_payment

    inv = db_session.get(Invoice, iid)
    p = add_payment(db_session, inv.tenant_id, None, inv, PaymentIn(method=method, kind="captura", currency=inv.currency, amount=monto, paid_at=el))
    p.provider = provider
    db_session.commit()


def _al_cliente(salida, email="pagos@cliente-prueba.ejemplo.com"):
    return [(m, f) for m, f in salida if m.to == email]


def test_secuencia_completa_y_un_pago_la_corta(client, auth, db_session, correo):
    from app.models import CollectionReminder
    from app.tasks.cobranza import mark_overdue, run_reminders

    cid = _cliente(client)
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY + timedelta(days=3))
    borrador = _factura(client, cid, due=HOY + timedelta(days=3))["id"]  # nunca enviado: no recibe nada

    r = run_reminders(db_session, today=HOY)  # D-3
    assert r["enviados"] == 1
    m, files = _al_cliente(correo)[-1]
    assert "vence el" in m.subject and "/pagar/" in m.html and files == [f"{_numero(client, iid)}.pdf"]
    assert m.entity == "invoice" and m.entity_id == iid
    # doble disparo el mismo dia: no se repite
    assert run_reminders(db_session, today=HOY)["enviados"] == 0
    assert run_reminders(db_session, today=HOY + timedelta(days=1))["enviados"] == 0
    assert run_reminders(db_session, today=HOY + timedelta(days=3))["enviados"] == 1  # D0
    assert "Hoy vence" in _al_cliente(correo)[-1][0].subject
    mark_overdue(db_session, today=HOY + timedelta(days=4))
    assert client.get(f"/invoices/{iid}").json()["status"] == "vencida"
    assert run_reminders(db_session, today=HOY + timedelta(days=6))["enviados"] == 1  # D+3
    m, files = _al_cliente(correo)[-1]
    assert "3 días de atraso" in m.subject and "/pagar/" in m.html and files
    etapas = client.get(f"/invoices/{iid}").json()["reminders"]
    assert [e["stage"] for e in etapas] == ["D-3", "D0", "D+3"] and all(e["status"] == "enviado" for e in etapas)
    # el cliente paga: la secuencia se corta (D+10 ya no sale)
    total = client.get(f"/invoices/{iid}").json()["total"]
    client.post(f"/invoices/{iid}/payments", json={"method": "sinpe", "kind": "captura", "amount": total})
    assert run_reminders(db_session, today=HOY + timedelta(days=13))["enviados"] == 0
    assert db_session.scalar(select(func.count()).select_from(CollectionReminder).where(CollectionReminder.invoice_id == borrador)) == 0


def _numero(client, iid: int) -> str:
    return client.get(f"/invoices/{iid}").json()["number"]


def test_worker_caido_no_manda_etapas_atrasadas_de_golpe(client, auth, db_session, correo):
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client)
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY - timedelta(days=12), hace=40)
    assert run_reminders(db_session, today=HOY)["enviados"] == 1
    assert [e["stage"] for e in client.get(f"/invoices/{iid}").json()["reminders"]] == ["D+10"]


def test_no_recuerda_el_mismo_dia_que_la_recibio(client, auth, db_session, correo):
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client)
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY, hace=0)  # contado: vence el dia que se envia
    assert run_reminders(db_session, today=HOY)["enviados"] == 0
    assert run_reminders(db_session, today=HOY + timedelta(days=1))["enviados"] == 0
    assert run_reminders(db_session, today=HOY + timedelta(days=3))["enviados"] == 1
    assert [e["stage"] for e in client.get(f"/invoices/{iid}").json()["reminders"]] == ["D+3"]


def test_fecha_de_corte_y_sin_corte_no_sale_nada(client, auth, db_session, correo):
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client)
    vieja = _enviada(db_session, client, cid, due=HOY - timedelta(days=3), hace=60, issue=HOY - timedelta(days=60))
    nueva = _enviada(db_session, client, cid, due=HOY - timedelta(days=3), hace=10, issue=HOY - timedelta(days=10))
    client.put("/settings", json={"notify_due": True})
    assert run_reminders(db_session, today=HOY)["enviados"] == 0  # sin fecha de corte: nada
    _encender(client, desde=HOY - timedelta(days=30))
    assert run_reminders(db_session, today=HOY)["enviados"] == 1
    assert client.get(f"/invoices/{vieja}").json()["reminders"] == []
    assert len(client.get(f"/invoices/{nueva}").json()["reminders"]) == 1


def test_pausas_apagado_y_sin_correo(client, auth, db_session, correo, monkeypatch):
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client)
    otro = _cliente(client, email="tesoreria@otro-cliente.ejemplo.com")
    _encender(client)
    a = _enviada(db_session, client, cid, due=HOY - timedelta(days=3))
    b = _enviada(db_session, client, otro, due=HOY - timedelta(days=3))
    # pausa por factura y por cliente
    assert client.put(f"/invoices/{a}/reminders", json={"paused": True}).json()["reminders_paused"] is True
    r = client.put(f"/customers/{otro}", json={"name": "Cliente Prueba Cobro S.A.", "no_reminders": True})
    assert r.status_code == 200 and r.json()["no_reminders"] is True
    assert run_reminders(db_session, today=HOY) == {"enviados": 0, "fallidos": 0, "omitidos": 2}
    client.put(f"/invoices/{a}/reminders", json={"paused": False})
    # apagado: nada
    client.put("/settings", json={"notify_due": False})
    assert run_reminders(db_session, today=HOY)["enviados"] == 0
    client.put("/settings", json={"notify_due": True})
    # sin servicio de correo no se gasta la etapa
    monkeypatch.delenv("RESEND_API_KEY")
    assert run_reminders(db_session, today=HOY).get("sin_correo") is True
    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    assert run_reminders(db_session, today=HOY)["enviados"] == 1
    assert len(client.get(f"/invoices/{a}").json()["reminders"]) == 1 and client.get(f"/invoices/{b}").json()["reminders"] == []


def test_correo_que_falla_queda_marcado_y_avisa(client, auth, db_session, monkeypatch):
    from app.models import EmailOutbox
    from app.services import documents_send, mail
    from app.tasks.cobranza import run_reminders

    def deliver(m, files=None):
        m.status, m.error = "error", "rechazado por el proveedor"

    monkeypatch.setenv("RESEND_API_KEY", "re_prueba")
    monkeypatch.setattr(mail, "deliver", deliver)
    monkeypatch.setattr(documents_send, "render_pdf", lambda html: b"%PDF-1.7 prueba")
    cid = _cliente(client)
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY - timedelta(days=3))
    r = run_reminders(db_session, today=HOY)
    assert r["fallidos"] == 1 and r["enviados"] == 0
    assert client.get(f"/invoices/{iid}").json()["reminders"][0]["status"] == "error"
    # no se reintenta sin el PDF (mail.retry_outbox toma pendiente|error) y el admin recibe el aviso
    al_cliente = db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == "pagos@cliente-prueba.ejemplo.com")).all()
    assert [m.status for m in al_cliente] == ["fallido"]
    assert db_session.scalar(select(EmailOutbox).where(EmailOutbox.subject.like("Cobranza:%")))
    assert run_reminders(db_session, today=HOY)["fallidos"] == 0  # la etapa no se repite


def test_corrida_en_seco_no_escribe_nada(client, auth, db_session, correo):
    from app.models import CollectionReminder, EmailOutbox, PaymentLink

    cid = _cliente(client)
    sin_correo = client.post("/customers", json={"name": "Cliente Sin Correo", "id_type": "fisica"}).json()["id"]
    a = _enviada(db_session, client, cid, due=HOY - timedelta(days=3))
    b = _enviada(db_session, client, sin_correo, due=HOY + timedelta(days=3))
    _factura(client, cid, due=HOY)  # borrador
    antes = (db_session.scalar(select(func.count()).select_from(EmailOutbox)), db_session.scalar(select(func.count()).select_from(PaymentLink)))
    pv = client.get("/collections/preview", params={"fecha": HOY.isoformat()}).json()
    assert any("fecha de corte" in w for w in pv["warnings"])  # sin corte: muestra todas y avisa
    assert [x["invoice_id"] for x in pv["items"]] == [a] and pv["items"][0]["stage"] == "D+3"
    assert pv["skipped"][0]["invoice_id"] == b and pv["skipped"][0]["reason"] == "El cliente no tiene correo"
    assert pv["ejemplo"]["subject"] == pv["items"][0]["subject"] and "Pagar factura" in pv["ejemplo"]["html"]
    assert Decimal(str(pv["cartera"]["vencida"])) > 0 and pv["baseline"] is None
    # otra fecha
    pv2 = client.get("/collections/preview", params={"fecha": (HOY + timedelta(days=7)).isoformat()}).json()
    assert [x["stage"] for x in pv2["items"]] == ["D+10"] and [x["stage"] for x in pv2["skipped"]] == ["D+3"]
    despues = (db_session.scalar(select(func.count()).select_from(EmailOutbox)), db_session.scalar(select(func.count()).select_from(PaymentLink)))
    assert antes == despues and db_session.scalar(select(func.count()).select_from(CollectionReminder)) == 0
    # solo quien configura la cobranza (Ajustes)
    from tests.test_session4 import _user_with_role

    pw = _user_with_role(db_session, auth["tenant"]["id"], "vende.cobro@ejemplo.com", "ventas")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "vende.cobro@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    assert client.get("/collections/preview", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    # caja busca cualquier factura (sales.ver_todo) pero no ve la cartera de la empresa ni la lista de deudores
    pw = _user_with_role(db_session, auth["tenant"]["id"], "caja.cobro@ejemplo.com", "caja")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "caja.cobro@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    assert client.get("/collections/preview", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_mensualidad_de_aliado_avisa_a_d_mas_5(client, auth, db_session, correo, monkeypatch):
    from app.models import Invoice, Tenant
    from app.services import documents_send
    from app.services.render import render_html
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client, email="pagos@aliado-cobro.ejemplo.com")
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY - timedelta(days=4), hace=30)
    db_session.get(Invoice, iid).external_order = "MENSUALIDAD-PLATAFORMA"
    db_session.commit()
    run_reminders(db_session, today=HOY - timedelta(days=4))  # D0
    assert run_reminders(db_session, today=HOY - timedelta(days=1))["enviados"] == 0  # D+3 no es etapa de aliados
    pdfs: list[str] = []
    monkeypatch.setattr(documents_send, "render_pdf", lambda h: pdfs.append(h) or b"%PDF-1.7 prueba")
    assert run_reminders(db_session, today=HOY + timedelta(days=1))["enviados"] == 1  # D+5
    m, files = _al_cliente(correo, "pagos@aliado-cobro.ejemplo.com")[-1]
    assert "Mensualidad" in m.subject and "5 días de atraso" in m.subject and files
    # sin comprobante fiscal es un aviso de cobro: el PDF del recordatorio no se titula "Factura" (render.doc_kind),
    # igual que el primer correo y el PDF que descarga el aliado
    assert "Aviso de cobro" in pdfs[-1] and ">Factura<" not in pdfs[-1]
    t = db_session.get(Tenant, auth["tenant"]["id"])
    inv = db_session.get(Invoice, iid)
    assert "Aviso de cobro" in render_html(db_session, inv, t)
    inv.einvoice_status = "aceptada"  # con el comprobante aceptado ya es la factura
    assert "Aviso de cobro" not in render_html(db_session, inv, t)
    db_session.rollback()
    # etapas configurables
    assert client.put("/settings", json={"collections_ally_stages": [-200]}).status_code == 422
    assert client.put("/settings", json={"collections_stages": [10, 0, -3, 0]}).json()["collections_stages"] == [-3, 0, 10]


def test_resumen_semanal_con_linea_base_y_cobranza_recuperada(client, auth, db_session, correo):
    from app.models import EmailOutbox, Tenant
    from app.tasks.cobranza import run_reminders, weekly_summary

    cid = _cliente(client)
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY - timedelta(days=10), hace=40)
    lunes = HOY
    assert weekly_summary(db_session, today=lunes) == 1
    assert weekly_summary(db_session, today=lunes) == 0  # doble disparo del beat: un solo resumen por semana
    t = db_session.get(Tenant, auth["tenant"]["id"])
    base = t.settings["collections_baseline"]
    assert base["date"] == lunes.isoformat() and Decimal(base["overdue"]) == Decimal("113000.00")
    primero = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("Cartera de la semana%"))).all()[-1]
    assert "Línea base registrada hoy" in primero.html
    # recordatorio, el cliente paga despues: solo lo cobrado POR EL ENLACE es cobranza recuperada (C.2); un SINPE
    # registrado a mano se muestra aparte y no infla la cifra de valor
    run_reminders(db_session, today=lunes + timedelta(days=1))
    _pago(db_session, iid, Decimal("50000"), lunes + timedelta(days=2), provider="onvo")
    _pago(db_session, iid, Decimal("13000"), lunes + timedelta(days=3), provider="manual", method="sinpe")
    assert weekly_summary(db_session, today=lunes + timedelta(days=7)) == 1
    ultimo = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("Cartera de la semana%")).order_by(EmailOutbox.id.desc())).first()
    assert "₡113,000.00" in ultimo.html and "Día 0" in ultimo.html and "1 recordatorio(s) enviados" in ultimo.html
    assert "después de un recordatorio): <b>₡50,000.00</b>" in ultimo.html
    assert "Por otros medios después de un aviso" in ultimo.html and "₡13,000.00" in ultimo.html
    # linea base manual desde Ajustes
    r = client.post("/collections/baseline")
    assert r.status_code == 200 and Decimal(r.json()["overdue"]) == Decimal("50000")  # el saldo que queda


def test_corrida_diaria_con_la_fecha_de_costa_rica(client, auth, db_session, correo, monkeypatch):
    """Borde 18:00-24:00 CR: a las 23:30 en Costa Rica ya es el dia siguiente en UTC; la etapa no sale un dia antes."""
    from zoneinfo import ZoneInfo

    from app.providers import einvoice as prov
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client)
    _encender(client)
    vence = HOY + timedelta(days=30)
    iid = _enviada(db_session, client, cid, due=vence)
    d3 = vence - timedelta(days=3)
    cr = ZoneInfo("America/Costa_Rica")
    monkeypatch.setattr(prov, "cr_now", lambda: datetime(d3.year, d3.month, d3.day, 0, 10, tzinfo=cr) - timedelta(minutes=40))  # 23:30 CR
    assert run_reminders(db_session)["enviados"] == 0
    monkeypatch.setattr(prov, "cr_now", lambda: datetime(d3.year, d3.month, d3.day, 0, 10, tzinfo=cr))
    assert run_reminders(db_session)["enviados"] == 1
    assert run_reminders(db_session)["enviados"] == 0  # doble disparo del beat
    assert [e["stage"] for e in client.get(f"/invoices/{iid}").json()["reminders"]] == ["D-3"]


def test_pausa_por_cliente_es_de_quien_cobra_y_queda_en_bitacora(client, auth, db_session):
    """La pausa por cliente apaga los recordatorios de TODAS sus facturas: un vendedor (clientes compartidos, ficha de
    solo lectura desde el 21/10) no la toca; el admin si, y queda en la bitacora con el antes y el despues."""
    from app.models import AuditLog
    from tests.test_session4 import _user_with_role

    cid = _cliente(client)
    pw = _user_with_role(db_session, auth["tenant"]["id"], "vende.pausa@ejemplo.com", "ventas")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "vende.pausa@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    assert client.put(f"/customers/{cid}", json={"name": "Cliente Prueba Cobro S.A.", "no_reminders": True}, headers=h).status_code == 403
    assert client.post("/customers", json={"name": "Otro Cliente", "id_type": "fisica", "no_reminders": True}, headers=h).status_code == 403
    # reunion 21/10 (pedido 23): la ficha de un cliente existente es de solo lectura para el vendedor
    r = client.put(f"/customers/{cid}", json={"name": "Cliente Prueba Cobro S.A.", "phone": "8888-0000", "no_reminders": False}, headers=h)
    assert r.status_code == 403
    assert client.put(f"/customers/{cid}", json={"name": "Cliente Prueba Cobro S.A.", "no_reminders": True}).json()["no_reminders"] is True
    log = db_session.scalars(select(AuditLog).where(AuditLog.entity == "customer", AuditLog.entity_id == cid, AuditLog.action == "reminders")).one()
    assert log.diff == {"antes": False, "despues": True}


def test_fecha_de_corte_con_un_anio_razonable(client, auth):
    """Al teclear el anio digito por digito el navegador da fechas intermedias (0202-...): no se guardan."""
    assert client.put("/settings", json={"collections_start_date": "0202-05-01"}).status_code == 422
    assert client.put("/settings", json={"collections_start_date": "2999-05-01"}).status_code == 422
    assert client.put("/settings", json={"collections_start_date": (HOY - timedelta(days=30)).isoformat()}).status_code == 200


def test_recordatorio_de_una_recurrencia_es_aviso_de_cobro(client, auth, db_session, correo, monkeypatch):
    """Lo que genera una recurrencia (no solo la mensualidad de aliados) sin comprobante fiscal es un aviso de cobro:
    el recordatorio lo nombra igual que el primer correo y el PDF (render.doc_kind)."""
    from app.models import Invoice, Recurrence, Tenant
    from app.services import documents_send
    from app.tasks.cobranza import run_reminders

    cid = _cliente(client, email="pagos@cliente-aviso.ejemplo.com")
    _encender(client)
    iid = _enviada(db_session, client, cid, due=HOY - timedelta(days=3), hace=30)
    rec = Recurrence(tenant_id=auth["tenant"]["id"], name="Monitoreo", kind="factura", customer_id=cid, template={"lines": []}, frequency="mensual", next_date=HOY)
    db_session.add(rec)
    db_session.flush()
    db_session.get(Invoice, iid).recurrence_id = rec.id
    db_session.commit()
    pdfs: list[str] = []
    monkeypatch.setattr(documents_send, "render_pdf", lambda h: pdfs.append(h) or b"%PDF-1.7 prueba")
    assert run_reminders(db_session, today=HOY)["enviados"] == 1  # D+3
    m, _ = _al_cliente(correo, "pagos@cliente-aviso.ejemplo.com")[-1]
    assert m.subject.startswith("Aviso de cobro") and "3 días de atraso" in m.subject and "El aviso de cobro <b>" in m.html
    assert "Aviso de cobro" in pdfs[-1] and ">Factura<" not in pdfs[-1]
    # con el comprobante aceptado ya es la factura
    from app.tasks.cobranza import reminder_email

    inv = db_session.get(Invoice, iid)
    inv.einvoice_status = "aceptada"
    asunto, cuerpo = reminder_email(db_session.get(Tenant, auth["tenant"]["id"]), inv, 3, HOY, None, True)
    assert asunto.startswith("Factura") and "La factura <b>" in cuerpo
    db_session.rollback()
