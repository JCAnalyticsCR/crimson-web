"""Area comercial: monto que sigue a la cotizacion, arrastre en el embudo, envio confirmado y bandeja de pendientes."""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Opportunity

from .test_session4 import _user_with_role


def _login(client, admin_h, db_session, tid, email, role):
    pw = _user_with_role(db_session, tid, email, role)
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    client.headers.update(admin_h)
    return {"Authorization": "Bearer " + tok}


def _opp_with_quote(client, db_session, amount=120119):
    cust = client.get("/customers").json()["items"][0]
    opp = client.post("/opportunities", json={"title": "Casa Guayos", "customer_id": cust["id"], "amount": amount}).json()
    body = {"customer_id": cust["id"], "lines": [{"name": "Kit CCTV", "quantity": 1, "unit_price": 100000, "tax_rate": 13}]}
    q = client.post("/quotes", json=body).json()
    # asi queda ligada al pasar por levantamiento -> cotizacion (fieldwork.survey_to_quote)
    o = db_session.get(Opportunity, opp["id"])
    o.quote_id = q["id"]
    db_session.commit()
    return opp, q, body


def test_amount_follows_quote_on_save(client, auth, db_session):
    opp, q, body = _opp_with_quote(client, db_session)
    assert Decimal(str(client.get(f"/opportunities/{opp['id']}").json()["amount"])) == Decimal("120119")  # manual hasta que se guarde
    body["lines"][0]["quantity"] = 3  # 300 000 + 13 % = 339 000
    r = client.put(f"/quotes/{q['id']}", json=body)
    assert r.status_code == 200
    got = client.get(f"/opportunities/{opp['id']}").json()
    assert Decimal(str(got["amount"])) == Decimal(str(r.json()["total"])) == Decimal("339000")
    board = client.get("/opportunities/board").json()
    assert Decimal(str(board["total"])) == Decimal("339000")
    # una oportunidad sin cotizacion conserva su monto manual
    o2 = client.post("/opportunities", json={"title": "Sin cotizar", "amount": 5000}).json()
    client.put(f"/quotes/{q['id']}", json=body)
    assert Decimal(str(client.get(f"/opportunities/{o2['id']}").json()["amount"])) == Decimal("5000")


def test_drag_stage_change_logs_and_respects_permissions(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    opp = client.post("/opportunities", json={"title": "Bodega Norte"}).json()
    r = client.post(f"/opportunities/{opp['id']}/touch", json={"note": "Movida de Nuevo a Contactado (embudo)", "status": "contactado"})
    assert r.status_code == 200 and r.json()["status"] == "contactado"
    assert "Movida de Nuevo a Contactado" in r.json()["notes"]
    # el vendedor no puede mover una oportunidad ajena; lectura no puede mover nada
    vend = _login(client, admin_h, db_session, tid, "vend@ejemplo.com", "ventas")
    lect = _login(client, admin_h, db_session, tid, "lect@ejemplo.com", "lectura")
    body = {"note": "Movida", "status": "cotizando"}
    assert client.post(f"/opportunities/{opp['id']}/touch", json=body, headers=vend).status_code == 404
    assert client.post(f"/opportunities/{opp['id']}/touch", json=body, headers=lect).status_code == 403
    mine = client.post("/opportunities", json={"title": "Del vendedor"}, headers=vend).json()
    assert client.post(f"/opportunities/{mine['id']}/touch", json=body, headers=vend).json()["status"] == "cotizando"


def test_send_only_marks_when_mail_left(client, auth, db_session, monkeypatch):
    from app.services import mail

    opp, q, _ = _opp_with_quote(client, db_session)
    c = client.get(f"/customers/{q['customer_id']}").json()
    client.put(f"/customers/{c['id']}", json={"name": c["name"], "email": "cliente@ejemplo.com"})
    # sin llave de Resend: simulado -> no se marca nada
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    r = client.post(f"/quotes/{q['id']}/email", json={}).json()
    assert r["sent"] is False and "NO salió" in r["note"]
    assert client.get(f"/quotes/{q['id']}").json()["status"] == "creado"
    assert client.get(f"/opportunities/{opp['id']}").json()["status"] == "nuevo"

    # error del proveedor -> tampoco
    def boom(m, files=None):
        m.status, m.error = "error", "422 dominio no verificado"

    monkeypatch.setattr(mail, "deliver", boom)
    r = client.post(f"/quotes/{q['id']}/email", json={}).json()
    assert r["sent"] is False and client.get(f"/quotes/{q['id']}").json()["status"] == "creado"

    # el proveedor acepta -> cotizacion y oportunidad pasan a enviada
    def ok(m, files=None):
        m.status = "enviado"

    monkeypatch.setattr(mail, "deliver", ok)
    r = client.post(f"/quotes/{q['id']}/email", json={}).json()
    assert r["sent"] is True
    assert client.get(f"/quotes/{q['id']}").json()["status"] == "enviada"
    assert client.get(f"/opportunities/{opp['id']}").json()["status"] == "enviada"


def test_pending_inbox(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    hoy, antes, despues = date.today(), date.today() - timedelta(days=3), date.today() + timedelta(days=2)
    client.post("/opportunities", json={"title": "Hoy", "next_action": "Llamar", "next_action_date": str(hoy)})
    client.post("/opportunities", json={"title": "Vencida", "next_action": "Visita", "next_action_date": str(antes)})
    client.post("/opportunities", json={"title": "Futura", "next_action": "Enviar", "next_action_date": str(despues)})
    client.post("/opportunities", json={"title": "Ganada vencida", "status": "ganada", "next_action_date": str(antes)})
    rows = client.get("/opportunities/pending").json()
    assert [x["title"] for x in rows] == ["Vencida", "Hoy"] and rows[0]["days_late"] == 3
    vend = _login(client, admin_h, db_session, tid, "vend2@ejemplo.com", "ventas")
    assert client.get("/opportunities/pending", headers=vend).json() == []  # solo las propias
    assert client.get("/opportunities/pending?everyone=true", headers=vend).json() == []
