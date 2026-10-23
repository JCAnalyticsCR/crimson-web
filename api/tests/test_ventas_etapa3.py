"""Ventas etapa 3: metas y tablero, agenda y tareas, kits y plantillas, aceptacion en linea y resumen de la manana.

Repo publico: solo correos y nombres de prueba.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.models import AuditLog, EmailOutbox, Opportunity, Payment, Quote, QuoteAcceptance, User
from app.models.ventas3 import QuoteLink, SalesTask
from tests.aliados_helpers import interno


def _persona(client, db_session, tid: int, email: str, role: str, nombre: str) -> tuple[dict, int]:
    h = interno(client, db_session, tid, email, role)
    u = db_session.scalar(select(User).where(User.email == email))
    u.full_name = nombre
    db_session.commit()
    return h, u.id


def _producto(client, code: str, price: float, cost: float | None = None) -> int:
    body = {"name": f"Equipo {code}", "code": code, "price": price, "currency": "CRC"}
    if cost is not None:
        body |= {"cost": cost, "cost_currency": "CRC"}
    r = client.post("/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _cliente(client, h, nombre: str, **extra) -> dict:
    r = client.post("/customers", json={"name": nombre, "id_type": "fisica", **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _enviada(client, h, cid: int, pid: int, pct: float = 0, due: date | None = None) -> dict:
    body = {"customer_id": cid, "lines": [{"product_id": pid, "name": "Equipo", "quantity": 1, "discount_type": "percent", "discount_value": pct, "tax_rate": 13}]}
    if due:
        body["due_date"] = due.isoformat()
    q = client.post("/quotes", json=body, headers=h).json()
    if q["status"] == "creado":
        r = client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=h)
        assert r.status_code == 200, r.text
        q = r.json()
    return q


# ---------- Metas ----------
def test_meta_y_tablero_cuentan_solo_lo_cobrado_y_confirmado(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _persona(client, db_session, tid, "meta.v@ejemplo.com", "ventas", "Vera Meta")
    jh, _ = _persona(client, db_session, tid, "meta.j@ejemplo.com", "jefe_ventas", "Jefe Meta")
    c = _cliente(client, vh, "Cliente Meta", email="cm@ejemplo.com")
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Visita", "quantity": 1, "unit_price": 20000, "tax_rate": 13}]}, headers=vh).json()
    # el vendedor no pone su meta ni ve el tablero
    assert client.put("/goals", json={"user_id": vid, "amount": 100000}, headers=vh).status_code == 403
    assert client.get("/goals/board", headers=vh).status_code == 403
    r = client.put("/goals", json={"user_id": vid, "amount": 100000}, headers=jh)
    assert r.status_code == 200 and float(r.json()["meta"]) == 100000
    # un cobro por confirmar no cuenta; confirmado, cuenta sin IVA
    hoy = date.today()
    db_session.add(Payment(tenant_id=tid, invoice_id=inv["id"], amount=22600, currency="CRC", method="sinpe", kind="captura", paid_at=hoy, status="por_confirmar"))
    db_session.commit()
    assert float(client.get("/goals/me", headers=vh).json()["vendido"]) == 0
    db_session.add(Payment(tenant_id=tid, invoice_id=inv["id"], amount=11300, currency="CRC", method="sinpe", kind="captura", paid_at=hoy, status="confirmado"))
    db_session.commit()
    me = client.get("/goals/me", headers=vh).json()
    assert float(me["vendido"]) == 10000 and me["avance_pct"] == 10.0
    assert client.get("/dashboard", headers=vh).json()["meta"]["mes"] == me["mes"]
    board = client.get("/goals/board", headers=jh).json()
    fila = next(x for x in board["vendedores"] if x["id"] == vid)
    assert fila["ranking"] >= 1 and float(fila["vendido"]) == 10000 and "ticket_promedio" in fila and "tasa_cierre" in fila
    assert not {"costo", "cost", "margen", "margin", "utilidad"} & {k.lower() for k in fila}
    # la meta se arrastra al mes siguiente
    nxt = (hoy.replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
    assert float(client.get(f"/goals/me?month={nxt}", headers=vh).json()["meta"]) == 100000


# ---------- Agenda ----------
def test_agenda_respeta_la_cartera_y_marca_lo_atrasado(client, auth, db_session):
    tid = auth["tenant"]["id"]
    ah, aid = _persona(client, db_session, tid, "ag.a@ejemplo.com", "ventas", "Ana Agenda")
    bh, _ = _persona(client, db_session, tid, "ag.b@ejemplo.com", "ventas", "Beto Agenda")
    c = _cliente(client, ah, "Cliente Agenda", email="ca@ejemplo.com")
    ayer = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    r = client.post("/tasks", json={"kind": "llamada", "title": "Llamar por la propuesta", "due_at": ayer, "customer_id": c["id"]}, headers=ah)
    assert r.status_code == 201 and r.json()["overdue"] is True, r.text
    # otro vendedor no liga tareas al cliente de otro, ni se las asigna a otro
    assert client.post("/tasks", json={"title": "Robar", "due_at": ayer, "customer_id": c["id"]}, headers=bh).status_code == 404
    assert client.post("/tasks", json={"title": "Para otro", "due_at": ayer, "assigned_to": aid}, headers=bh).status_code == 403
    assert client.get("/tasks", headers=bh).json() == []
    hoy = client.get("/tasks/today", headers=ah).json()
    assert [t["title"] for t in hoy] == ["Llamar por la propuesta"]
    assert client.get("/dashboard", headers=ah).json()["agenda"][0]["overdue"] is True
    ov = client.get(f"/customers/{c['id']}/overview", headers=ah).json()
    assert any(e["kind"] == "tarea" and e["status"] == "atrasada" for e in ov["timeline"])
    # hora sin zona = hora de Costa Rica
    r = client.post("/tasks", json={"kind": "visita", "title": "Visita", "due_at": "2026-11-03T09:30:00"}, headers=ah)
    assert r.json()["due_at"].startswith("2026-11-03T15:30")
    assert client.patch(f"/tasks/{hoy[0]['id']}", json={"done": True}, headers=bh).status_code == 404
    assert client.patch(f"/tasks/{hoy[0]['id']}", json={"done": True}, headers=ah).json()["done_at"]


def test_resumen_de_la_manana(client, auth, db_session):
    from app.models import Tenant
    from app.services import agenda

    tid = auth["tenant"]["id"]
    ah, aid = _persona(client, db_session, tid, "dig.a@ejemplo.com", "ventas", "Dina Digest")
    client.post("/tasks", json={"title": "Visitar bodega", "due_at": datetime.now(UTC).isoformat()}, headers=ah)
    t = db_session.get(Tenant, tid)
    assert agenda.digest(db_session, t) == 1
    assert agenda.digest(db_session, t) == 1  # un doble disparo no duplica el correo (dedupe del dia)
    db_session.commit()
    assert len(db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == "dig.a@ejemplo.com", EmailOutbox.subject.like("Su agenda de hoy%"))).all()) == 1


def test_beat_de_la_agenda_a_las_645_lunes_a_sabado():
    import os
    import sys

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    e = celery_app.celery.conf.beat_schedule["agenda-diaria"]
    assert e["task"] == "crm.agenda_diaria"
    assert e["schedule"].hour == {6} and e["schedule"].minute == {45} and e["schedule"].day_of_week == {1, 2, 3, 4, 5, 6}


# ---------- Kits y plantillas ----------
def test_kit_usa_el_precio_de_hoy_y_el_tope_de_descuento_sigue(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, _ = _persona(client, db_session, tid, "kit.v@ejemplo.com", "ventas", "Kiko Kit")
    cam = _producto(client, "KIT-CAM", 10000)
    nvr = _producto(client, "KIT-NVR", 5000)
    body = {"name": "4 cámaras + grabador", "lines": [{"product_id": cam, "quantity": 4}, {"product_id": nvr, "quantity": 1}]}
    assert client.post("/kits", json=body, headers=vh).status_code == 403
    k = client.post("/kits", json=body).json()
    client.put(f"/products/{cam}", json={"name": "Equipo KIT-CAM", "code": "KIT-CAM", "price": 12000, "currency": "CRC"})
    lines = client.get(f"/kits/{k['id']}/lines", headers=vh).json()["lines"]
    assert [(ln["product_id"], float(ln["quantity"]), float(ln["unit_price"])) for ln in lines] == [(cam, 4, 12000), (nvr, 1, 5000)]
    # editadas despues de insertar, pasan por el mismo tope: 20 % de descuento queda retenida
    c = _cliente(client, vh, "Cliente Kit", email="ck@ejemplo.com")
    lines[0]["discount_value"] = 20
    q = client.post("/quotes", json={"customer_id": c["id"], "lines": lines}, headers=vh).json()
    assert q["status"] == "por_aprobar"


def test_plantilla_con_variables_y_enlace(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, _ = _persona(client, db_session, tid, "tpl.v@ejemplo.com", "ventas", "Tina Plantilla")
    pid = _producto(client, "TPL-1", 10000)
    c = _cliente(client, vh, "Cliente Plantilla", email="cp@ejemplo.com", phone="8888-1234")
    t = client.post("/message-templates", json={"name": "WhatsApp corto", "channel": "whatsapp", "body": "Hola {cliente}: la {numero} por {total}. Acéptela aquí: {enlace} ({vendedor})"}).json()
    q = _enviada(client, vh, c["id"], pid)
    r = client.post(f"/quotes/{q['id']}/message?template_id={t['id']}", headers=vh).json()
    assert r["text"].startswith("Hola Cliente Plantilla: la ") and "/cotizacion/" in r["text"] and "Tina Plantilla" in r["text"]
    assert r["whatsapp_url"].startswith("https://wa.me/50688881234?text=")


# ---------- Aceptacion en linea ----------
def test_aceptacion_en_linea(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, vid = _persona(client, db_session, tid, "acc.v@ejemplo.com", "ventas", "Vale Acepta")
    pid = _producto(client, "ACC-1", 10000, cost=6000)
    c = _cliente(client, vh, "Cliente Acepta", email="cacc@ejemplo.com")
    o = client.post("/opportunities", json={"title": "Cámaras", "customer_id": c["id"]}, headers=vh).json()
    q = client.post(f"/opportunities/{o['id']}/quote", json={}, headers=vh).json()
    client.put(f"/quotes/{q['quote_id']}", json={"customer_id": c["id"], "lines": [{"product_id": pid, "name": "Equipo", "quantity": 1, "tax_rate": 13}]}, headers=vh)
    qid = q["quote_id"]
    # sin enviar no hay enlace
    assert db_session.scalar(select(QuoteLink).where(QuoteLink.quote_id == qid)) is None
    sent = client.post(f"/quotes/{qid}/send", json={"channel": "whatsapp"}, headers=vh).json()
    url = sent["online_link"]["url"]
    token = url.rsplit("/", 1)[1]
    client.headers.pop("Authorization")
    page = client.get(f"/public/quote/{token}")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["can_accept"] is True and body["number"] == sent["number"]
    plano = str(body).lower()
    assert "6000" not in plano and "cost" not in plano and "margin" not in plano and "internal" not in plano
    assert client.get("/public/quote/" + "x" * 43).status_code == 404
    r = client.post(f"/public/quote/{token}/accept", json={"name": "Do", "id_number": "1"})
    assert r.status_code == 422
    r = client.post(f"/public/quote/{token}/accept", json={"name": "Carla Cliente", "id_number": "1-1111-1111"})
    assert r.status_code == 200 and r.json()["accepted"]["name"] == "Carla Cliente", r.text
    assert client.post(f"/public/quote/{token}/accept", json={"name": "Carla Cliente", "id_number": "1-1111-1111"}).status_code == 409
    db_session.expire_all()
    qq = db_session.get(Quote, qid)
    assert qq.acceptance_status == "aceptada"
    acc = db_session.scalar(select(QuoteAcceptance).where(QuoteAcceptance.quote_id == qid))
    assert acc.channel == "enlace" and acc.contact_id_number == "1-1111-1111" and acc.ip
    # la oportunidad NO queda ganada; la vendedora recibe la tarea de facturar y los avisos
    assert db_session.get(Opportunity, o["id"]).status != "ganada"
    tarea = db_session.scalar(select(SalesTask).where(SalesTask.quote_id == qid, SalesTask.kind == "facturar"))
    assert tarea and tarea.assigned_to == vid
    link = db_session.scalar(select(QuoteLink).where(QuoteLink.quote_id == qid))
    assert link.first_opened_at and link.last_opened_at and link.open_count >= 1
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "link_open", AuditLog.entity_id == qid))
    assert db_session.scalar(select(EmailOutbox).where(EmailOutbox.to == "acc.v@ejemplo.com", EmailOutbox.subject.like("%abrió la cotización%")))


def test_no_se_acepta_lo_retenido_ni_lo_vencido(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vh, _ = _persona(client, db_session, tid, "acc2.v@ejemplo.com", "ventas", "Vito Retiene")
    pid = _producto(client, "ACC-2", 10000)
    c = _cliente(client, vh, "Cliente Retenido", email="cret@ejemplo.com")
    retenida = _enviada(client, vh, c["id"], pid, pct=30)
    assert retenida["status"] == "por_aprobar"
    assert client.post(f"/quotes/{retenida['id']}/send", json={"channel": "whatsapp"}, headers=vh).status_code == 409
    assert db_session.scalar(select(QuoteLink).where(QuoteLink.quote_id == retenida["id"])) is None
    # vencida: la validez ya paso
    q = _enviada(client, vh, c["id"], pid, due=date.today() + timedelta(days=3))
    token = q["online_link"]["url"].rsplit("/", 1)[1]
    db_session.get(Quote, q["id"]).due_date = date.today() - timedelta(days=2)
    db_session.commit()
    assert client.get(f"/public/quote/{token}").status_code == 410
    assert client.post(f"/public/quote/{token}/accept", json={"name": "Alguien Tarde", "id_number": "1-1111-1111"}).status_code == 410


def test_el_publico_tiene_limite(client, auth, db_session):
    from app.core.ratelimit import quote_accept_limiter

    for _ in range(quote_accept_limiter.limit):
        client.post("/public/quote/inexistente-token/accept", json={"name": "Nadie Nadie", "id_number": "1-1111-1111"})
    assert client.post("/public/quote/inexistente-token/accept", json={"name": "Nadie Nadie", "id_number": "1-1111-1111"}).status_code == 429


def test_ajustes_de_ventas_etapa3(client, auth):
    assert client.put("/settings", json={"ventas": {"enlace_cotizacion_dias": 0}}).status_code == 422
    assert client.put("/settings", json={"ventas": {"meta_mensual_default": -5}}).status_code == 422
    r = client.put("/settings", json={"ventas": {"meta_mensual_default": 2_000_000, "aceptacion_en_linea": False, "resumen_diario_vendedores": False, "enlace_cotizacion_dias": 15}})
    assert r.status_code == 200, r.text
    assert r.json()["ventas"]["aceptacion_en_linea"] is False
