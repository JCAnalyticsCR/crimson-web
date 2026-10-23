"""Avisos que destraban la venta (plan maestro 1.6): cada evento que frena dinero genera exactamente un correo a quien
lo tiene que mover (RACI), con enlace directo a una pantalla que esa persona puede abrir, sin montos ni costos, y sin
repetirse. Mas: "Aceptadas por convertir" y "Levantamientos solicitados" en Aprobaciones, el boton "Ver PRO-..." de la
cotizacion y la OT de un ticket sin tecnico que queda "por programar"."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import AuditLog, EmailOutbox, Opportunity, Quote, Survey, TenantUser, User, WorkOrder
from tests.aliados_helpers import aliado, participa, uid, usuario
from tests.test_campo import _login

BASE = "https://panel.ejemplo.com"


def _correos(db, entity: str, entity_id: int, prefijo: str) -> list[EmailOutbox]:
    db.expire_all()
    return list(
        db.scalars(
            select(EmailOutbox)
            .where(EmailOutbox.entity == entity, EmailOutbox.entity_id == entity_id, EmailOutbox.subject.startswith(prefijo))
            .order_by(EmailOutbox.id)
        )
    )


def _para(mails: list[EmailOutbox]) -> list[str]:
    return sorted(m.to for m in mails)


def _cliente(client) -> dict:
    return client.get("/customers").json()["items"][0]


_CAMARA: dict = {}


@pytest.fixture(autouse=True)
def _camara_del_catalogo(db_session, auth):
    """La camara de las cotizaciones es del catalogo: una linea libre del vendedor sobre el tope (₡25 000) ya pide
    aprobacion por si sola y taparia el descuento, que es lo que se prueba aca."""
    from app.models import Product

    prod = Product(tenant_id=auth["tenant"]["id"], code="CAM-4MP-AV", name="Cámara IP 4 MP", price=100000)
    db_session.add(prod)
    db_session.commit()
    _CAMARA["id"] = prod.id
    yield
    _CAMARA.clear()


def _linea(precio=100000, descuento=0) -> dict:
    return {"product_id": _CAMARA["id"], "quantity": 2, "unit_price": precio, "tax_rate": 13, "discount_value": descuento}


def _sec(client, key, h=None) -> dict:
    r = client.get("/approvals", params={"limit": 50}, headers=h or {})
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["sections"] if s["key"] == key)


def _equipo(client, db_session, auth, sufijo: str):
    tid = auth["tenant"]["id"]
    ven = _login(client, db_session, tid, f"vendedora-{sufijo}@ejemplo.com", "ventas")
    sup = _login(client, db_session, tid, f"supervisor-{sufijo}@ejemplo.com", "supervisor")
    return tid, ven, sup


# ---------- cotizacion aceptada ----------
def test_aceptada_avisa_a_admin_supervisor_y_vendedora_con_enlace(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    _tid, ven, _sup = _equipo(client, db_session, auth, "acep")
    q = client.post("/quotes", headers=ven, json={"customer_id": _cliente(client)["id"], "lines": [_linea()]}).json()
    assert q["status"] == "creado"
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=ven).status_code == 200
    antes = _sec(client, "cotizaciones_aceptadas")["count"]

    # la registra la vendedora: le llega a administracion y al supervisor (ella no se avisa a si misma)
    r = client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Persona Cliente", "channel": "whatsapp"}, headers=ven)
    assert r.status_code == 200, r.text
    mails = _correos(db_session, "quote", q["id"], "Cotización aceptada")
    assert _para(mails) == sorted([auth["user"]["email"], "supervisor-acep@ejemplo.com"])
    admin = next(m for m in mails if m.to == auth["user"]["email"])
    assert f"{BASE}/cotizaciones/{q['id']}" in admin.html and "Persona Cliente" in admin.html
    # el supervisor no abre cotizaciones ajenas: su enlace lleva a donde si entra (nunca a un 404)
    sup_mail = next(m for m in mails if m.to.startswith("supervisor-"))
    assert f"/cotizaciones/{q['id']}" not in sup_mail.html and f"{BASE}/proyectos" in sup_mail.html
    # sin montos
    assert all("₡" not in m.html and "CRC" not in m.html and "226" not in m.html for m in mails)

    # deshacer y volver a registrarla no repite el aviso
    assert client.post(f"/quotes/{q['id']}/acceptance", json={"status": "pendiente"}, headers=ven).status_code == 200
    assert (
        client.post(
            f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Persona Cliente", "channel": "correo"}, headers=ven
        ).status_code
        == 200
    )
    assert len(_correos(db_session, "quote", q["id"], "Cotización aceptada")) == 2

    # Aprobaciones -> Aceptadas por convertir (y la tarjeta del inicio de la vendedora, con la misma definicion)
    sec = _sec(client, "cotizaciones_aceptadas")
    assert sec["count"] == antes + 1 and sec["to"] == "/cotizaciones?pendiente=cotizaciones_aceptadas"
    it = next(i for i in sec["items"] if i["id"] == q["id"])
    assert it["to"] == f"/cotizaciones/{q['id']}" and it["subtitle"].startswith("Aceptada v1") and it["who"] == "Ventas"
    assert client.get("/dashboard", headers=ven).json()["acciones_pendientes"]["cotizaciones_aceptadas"] == 1
    lista = client.get("/quotes", params={"pendiente": "cotizaciones_aceptadas", "limit": 100}).json()["items"]
    assert q["id"] in [x["id"] for x in lista]

    # convertida a proyecto: sale de la seccion y la cotizacion ya dice cual es su proyecto (el boton "Ver PRO-...")
    pr = client.post(f"/quotes/{q['id']}/project", json={})
    assert pr.status_code == 201, pr.text
    out = client.get(f"/quotes/{q['id']}").json()
    assert out["project_id"] == pr.json()["id"] and out["project_number"] == pr.json()["number"]
    assert q["id"] not in [i["id"] for i in _sec(client, "cotizaciones_aceptadas")["items"]]
    assert client.get("/dashboard", headers=ven).json()["acciones_pendientes"]["cotizaciones_aceptadas"] == 0


def _venta_de_equipo_aceptada(client, db_session, ven, cust) -> dict:
    opp = client.post("/opportunities", json={"title": "Solo equipo", "kind": "venta", "customer_id": cust["id"]}, headers=ven).json()
    q = client.post("/quotes", headers=ven, json={"customer_id": cust["id"], "lines": [_linea()]}).json()
    db_session.get(Opportunity, opp["id"]).quote_id = q["id"]
    db_session.commit()
    client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=ven)
    # la registra el admin (el no se avisa a si mismo)
    assert client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Cliente", "channel": "correo"}).status_code == 200
    return q


def test_aceptada_de_venta_de_equipo_avisa_al_supervisor_salvo_que_se_apague(client, auth, db_session):
    _tid, ven, _sup = _equipo(client, db_session, auth, "venta")
    cust = _cliente(client)
    # por defecto, lo que dice el plan: administracion, supervisor y vendedora
    assert client.get("/settings").json()["notify_supervisor_equipment_sale"] is True
    q = _venta_de_equipo_aceptada(client, db_session, ven, cust)
    mails = _correos(db_session, "quote", q["id"], "Cotización aceptada")
    assert _para(mails) == ["supervisor-venta@ejemplo.com", "vendedora-venta@ejemplo.com"]
    ella = next(m for m in mails if m.to.startswith("vendedora-"))
    assert "factura" in ella.html and f"/cotizaciones/{q['id']}" in ella.html
    it = next(i for i in _sec(client, "cotizaciones_aceptadas")["items"] if i["id"] == q["id"])
    assert "convertir a factura" in it["subtitle"]

    # Ajustes -> Notificaciones lo apaga: el supervisor no programa nada en una venta de equipo
    assert client.put("/settings", json={"notify_supervisor_equipment_sale": False}).status_code == 200
    q2 = _venta_de_equipo_aceptada(client, db_session, ven, cust)
    assert _para(_correos(db_session, "quote", q2["id"], "Cotización aceptada")) == ["vendedora-venta@ejemplo.com"]


# ---------- descuentos ----------
def test_descuento_por_aprobar_aprobar_y_rechazar(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    _tid, ven, _sup = _equipo(client, db_session, auth, "desc")
    cust = _cliente(client)
    antes = _sec(client, "cotizaciones_por_aprobar")["count"]

    # pasarse del limite (10 %): aviso inmediato a quien aprueba, con enlace y sin cifras de margen
    q = client.post("/quotes", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=30)]}).json()
    assert q["status"] == "por_aprobar"
    mails = _correos(db_session, "quote", q["id"], "Cotización por aprobar")
    assert _para(mails) == [auth["user"]["email"]]
    assert f"{BASE}/cotizaciones/{q['id']}" in mails[0].html and "30.0 %" in mails[0].html and "Ventas" in mails[0].html
    # volver a guardarla retenida no repite el aviso
    assert client.put(f"/quotes/{q['id']}", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=25)]}).json()["status"] == "por_aprobar"
    assert len(_correos(db_session, "quote", q["id"], "Cotización por aprobar")) == 1
    assert _sec(client, "cotizaciones_por_aprobar")["count"] == antes + 1

    # la vendedora no puede rechazar; quien aprueba tiene que dar un motivo
    assert client.post(f"/quotes/{q['id']}/reject", json={"reason": "No"}, headers=ven).status_code == 403
    assert client.post(f"/quotes/{q['id']}/reject", json={"reason": "  "}).status_code == 422
    r = client.post(f"/quotes/{q['id']}/reject", json={"reason": "El cliente ya tiene precio de distribuidor"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "por_aprobar" and r.json()["rejection"]["reason"] == "El cliente ya tiene precio de distribuidor"
    assert client.post(f"/quotes/{q['id']}/reject", json={"reason": "Otra vez"}).status_code == 409
    rech = _correos(db_session, "quote", q["id"], "Cotización rechazada")
    assert _para(rech) == ["vendedora-desc@ejemplo.com"] and "precio de distribuidor" in rech[0].html and f"/cotizaciones/{q['id']}" in rech[0].html
    # sigue retenida (no se envia ni se convierte) pero ya no espera a quien aprueba
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=ven).status_code == 409
    assert client.post(f"/quotes/{q['id']}/convert", headers=ven).status_code == 409
    assert q["id"] not in [i["id"] for i in _sec(client, "cotizaciones_por_aprobar")["items"]]
    assert client.get(f"/quotes/{q['id']}", headers=ven).json()["rejection"]["by"]

    # la vuelve a guardar fuera del limite: vuelve a la bandeja y quien aprueba recibe otro aviso
    assert client.put(f"/quotes/{q['id']}", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=20)]}).json()["status"] == "por_aprobar"
    assert len(_correos(db_session, "quote", q["id"], "Cotización por aprobar")) == 2
    assert q["id"] in [i["id"] for i in _sec(client, "cotizaciones_por_aprobar")["items"]]
    assert client.get(f"/quotes/{q['id']}", headers=ven).json()["rejection"] is None

    # aprobada: la vendedora se entera y ya la puede enviar
    assert client.post(f"/quotes/{q['id']}/approve").status_code == 200
    ok = _correos(db_session, "quote", q["id"], "Cotización aprobada")
    assert _para(ok) == ["vendedora-desc@ejemplo.com"] and f"/cotizaciones/{q['id']}" in ok[0].html
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=ven).status_code == 200

    # rechazada y corregida dentro del limite: se libera sola, sin otro aviso
    q2 = client.post("/quotes", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=40)]}).json()
    client.post(f"/quotes/{q2['id']}/reject", json={"reason": "Muy alto"})
    out = client.put(f"/quotes/{q2['id']}", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=5)]}).json()
    assert out["status"] == "creado" and out["rejection"] is None
    assert len(_correos(db_session, "quote", q2["id"], "Cotización por aprobar")) == 1


def test_quien_aprueba_la_libera_al_guardarla_y_la_vendedora_se_entera(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    _tid, ven, _sup = _equipo(client, db_session, auth, "libera")
    cust = _cliente(client)

    def body(d):
        return {"customer_id": cust["id"], "lines": [_linea(descuento=d)]}

    # el admin abre la retenida, le ajusta el descuento y la guarda (sin el boton "Aprobar descuento")
    q = client.post("/quotes", headers=ven, json=body(30)).json()
    assert q["status"] == "por_aprobar"
    assert client.put(f"/quotes/{q['id']}", json=body(20)).json()["status"] == "creado"
    ok = _correos(db_session, "quote", q["id"], "Cotización aprobada")
    assert _para(ok) == ["vendedora-libera@ejemplo.com"] and f"{BASE}/cotizaciones/{q['id']}" in ok[0].html
    accion = select(AuditLog).where(AuditLog.entity == "quote", AuditLog.entity_id == q["id"], AuditLog.action == "approve_quote")
    assert db_session.scalar(accion) is not None
    # una que el admin ya habia rechazado, corregida y guardada por el: tambien es su aprobacion
    q2 = client.post("/quotes", headers=ven, json=body(40)).json()
    assert client.post(f"/quotes/{q2['id']}/reject", json={"reason": "Muy alto"}).status_code == 200
    assert client.put(f"/quotes/{q2['id']}", json=body(15)).json()["status"] == "creado"
    assert _para(_correos(db_session, "quote", q2["id"], "Cotización aprobada")) == ["vendedora-libera@ejemplo.com"]
    # la vendedora que la corrige ella misma dentro de su limite no "se aprueba": ni aviso ni registro de aprobacion
    q3 = client.post("/quotes", headers=ven, json=body(30)).json()
    assert client.put(f"/quotes/{q3['id']}", headers=ven, json=body(5)).json()["status"] == "creado"
    assert _correos(db_session, "quote", q3["id"], "Cotización aprobada") == []
    assert db_session.scalar(select(AuditLog).where(AuditLog.entity_id == q3["id"], AuditLog.action == "approve_quote")) is None


def test_volver_a_pedir_aprobacion_despues_de_una_decision_avisa(client, auth, db_session):
    _tid, ven, _sup = _equipo(client, db_session, auth, "otravez")
    cust = _cliente(client)

    def guardar(qid, d):
        return client.put(f"/quotes/{qid}", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=d)]}).json()["status"]

    def pedidos(qid):
        return len(_correos(db_session, "quote", qid, "Cotización por aprobar"))

    q = client.post("/quotes", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=30)]}).json()
    assert pedidos(q["id"]) == 1
    # rechazada, liberada dentro del limite y enseguida vuelta a pasar: es un pedido nuevo (antes la ventana de 30
    # minutos del primer aviso se lo comia y volvia a la bandeja sin aviso)
    assert client.post(f"/quotes/{q['id']}/reject", json={"reason": "Muy alto"}).status_code == 200
    assert guardar(q["id"], 5) == "creado"
    assert guardar(q["id"], 25) == "por_aprobar"
    assert pedidos(q["id"]) == 2
    # corregir y volver a pasarse sin que nadie decida en medio no repite (ventana)
    assert guardar(q["id"], 5) == "creado"
    assert guardar(q["id"], 25) == "por_aprobar"
    assert pedidos(q["id"]) == 2
    # aprobada y vuelta a pasar del limite: otra decision en medio, otro aviso
    assert client.post(f"/quotes/{q['id']}/approve").status_code == 200
    assert guardar(q["id"], 30) == "por_aprobar"
    assert pedidos(q["id"]) == 3


def test_aprobada_o_rechazada_sin_vendedora_que_la_abra_va_a_administracion(client, auth, db_session):
    tid, ven, _sup = _equipo(client, db_session, auth, "sinver")
    _login(client, db_session, tid, "otra-admin@ejemplo.com", "admin")
    cust = _cliente(client)
    q = client.post("/quotes", headers=ven, json={"customer_id": cust["id"], "lines": [_linea(descuento=30)]}).json()
    # a la vendedora la pasan a tecnico: ya no abre cotizaciones ni ve precios
    ella = db_session.scalar(select(User).where(User.email == "vendedora-sinver@ejemplo.com")).id
    db_session.scalar(select(TenantUser).where(TenantUser.user_id == ella, TenantUser.tenant_id == tid)).role_code = "tecnico"
    db_session.commit()
    assert client.post(f"/quotes/{q['id']}/reject", json={"reason": "Muy alto"}).status_code == 200
    mails = _correos(db_session, "quote", q["id"], "Cotización rechazada")
    # la otra administradora (quien rechazo no se avisa a si mismo), para reasignarla
    assert _para(mails) == ["otra-admin@ejemplo.com"] and "reasígnela" in mails[0].html


def test_avisos_internos_no_van_al_bcc_de_la_empresa_ni_llevan_el_monto(client, auth, db_session):
    """Ajustes -> "Correos en copia (BCC) de toda factura" suele ser el archivo o el contador externo: los avisos del
    equipo (por aprobar, aprobada, aceptada) no le llegan. Y el motivo de una retenida por monto va sin el total ni el
    tope interno. Un correo de documento sigue yendo en copia, como antes."""
    from app.models import Tenant
    from app.services.mail import queue_email

    tid, ven, _sup = _equipo(client, db_session, auth, "bcc")
    assert client.put("/settings", json={"bcc": ["archivo@ejemplo.com"], "quote_approval_amount": 1000}).status_code == 200
    cust = _cliente(client)
    q = client.post("/quotes", headers=ven, json={"customer_id": cust["id"], "lines": [_linea()]}).json()
    assert q["status"] == "por_aprobar"
    pedido = _correos(db_session, "quote", q["id"], "Cotización por aprobar")
    assert len(pedido) == 1 and "supera el tope para vender sin aprobación" in pedido[0].html
    assert "CRC" not in pedido[0].html and "226" not in pedido[0].html and "1,000" not in pedido[0].html
    assert client.post(f"/quotes/{q['id']}/approve").status_code == 200
    assert client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp"}, headers=ven).status_code == 200
    r = client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Persona Cliente", "channel": "whatsapp"}, headers=ven)
    assert r.status_code == 200, r.text
    avisos = [m for p in ("Cotización por aprobar", "Cotización aprobada", "Cotización aceptada") for m in _correos(db_session, "quote", q["id"], p)]
    assert len(avisos) >= 3 and all(m.bcc is None for m in avisos)

    # un correo de documento (sin company_bcc=False) sigue con la copia de la empresa
    t = db_session.get(Tenant, tid)
    m = queue_email(db_session, t, "cliente@ejemplo.com", "Factura de prueba", "<p>x</p>", "invoice", 1)
    assert m.bcc == "archivo@ejemplo.com"
    db_session.rollback()


# ---------- ordenes de trabajo ----------
def test_ot_asignada_avisa_al_tecnico_interno_una_vez(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    tid = auth["tenant"]["id"]
    sup = _login(client, db_session, tid, "supervisor-ot@ejemplo.com", "supervisor")
    _login(client, db_session, tid, "tecnico-uno@ejemplo.com", "tecnico")
    _login(client, db_session, tid, "tecnico-dos@ejemplo.com", "tecnico")
    t1 = db_session.scalar(select(User).where(User.email == "tecnico-uno@ejemplo.com")).id
    t2 = db_session.scalar(select(User).where(User.email == "tecnico-dos@ejemplo.com")).id
    sup_id = db_session.scalar(select(User).where(User.email == "supervisor-ot@ejemplo.com")).id

    o = client.post("/work-orders", json={"title": "Revisión de cámaras", "technician_id": t1, "site": "Bodega central"}, headers=sup).json()
    mails = _correos(db_session, "work_order", o["id"], "Orden de trabajo asignada")
    assert _para(mails) == ["tecnico-uno@ejemplo.com"]
    assert f"{BASE}/ordenes-trabajo?id={o['id']}" in mails[0].html and "Bodega central" in mails[0].html and "por programar" in mails[0].html
    # cambiarla de tecnico: aviso al nuevo; volver al primero dentro de la ventana no repite
    assert client.patch(f"/work-orders/{o['id']}/assign", json={"technician_id": t2}, headers=sup).status_code == 200
    assert client.patch(f"/work-orders/{o['id']}/assign", json={"technician_id": t1}, headers=sup).status_code == 200
    assert _para(_correos(db_session, "work_order", o["id"], "Orden de trabajo asignada")) == ["tecnico-dos@ejemplo.com", "tecnico-uno@ejemplo.com"]
    # un ayudante nuevo por el PUT tambien se entera; quien se asigna a si mismo no
    assert client.put(f"/work-orders/{o['id']}", json={"helpers": [t2, sup_id]}, headers=sup).status_code == 200
    assert _para(_correos(db_session, "work_order", o["id"], "Orden de trabajo asignada")) == ["tecnico-dos@ejemplo.com", "tecnico-uno@ejemplo.com"]
    propia = client.post("/work-orders", json={"title": "Visita propia", "technician_id": sup_id}, headers=sup).json()
    assert _correos(db_session, "work_order", propia["id"], "Orden de trabajo asignada") == []


def test_ticket_sin_tecnico_queda_por_programar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _login(client, db_session, tid, "tecnico-tk@ejemplo.com", "tecnico")
    tec = db_session.scalar(select(User).where(User.email == "tecnico-tk@ejemplo.com")).id
    cust = _cliente(client)
    t = client.post("/tickets", json={"customer_id": cust["id"], "subject": "Cámara sin imagen", "priority": "alta"}).json()
    abiertos = client.get("/dashboard").json()["gerencia"]["jobs_open"]

    r = client.post(f"/tickets/{t['id']}/work-order", json={})
    assert r.status_code == 201 and r.json()["status"] == "por_programar", r.text
    oid = r.json()["work_order_id"]
    assert client.get(f"/tickets/{t['id']}").json()["work_order_status"] == "por_programar"
    # sigue contando como trabajo abierto en la fila de gerencia del inicio
    assert client.get("/dashboard").json()["gerencia"]["jobs_open"] == abiertos + 1
    # se destaca para quien asigna: en pendientes y en "Mi dia"
    assert oid in [x["id"] for x in client.get("/work-orders", params={"status": "pendientes"}).json()]
    assert oid in [x["id"] for x in client.get("/work-orders/meta/today").json()["next"]]
    # al programarla con un tecnico pasa a "asignada" y el tecnico recibe el aviso
    assert client.patch(f"/work-orders/{oid}/assign", json={"technician_id": tec}).json()["status"] == "asignada"
    assert _para(_correos(db_session, "work_order", oid, "Orden de trabajo asignada")) == ["tecnico-tk@ejemplo.com"]
    # no se archiva un ticket con su visita abierta (por programar cuenta como abierta)
    t2 = client.post("/tickets", json={"customer_id": cust["id"], "subject": "Otra falla", "priority": "media"}).json()
    client.post(f"/tickets/{t2['id']}/work-order", json={})
    assert client.post(f"/archive/ticket/{t2['id']}/trash").status_code == 409

    # con tecnico (el responsable del ticket): asignada y avisada
    t3 = client.post("/tickets", json={"customer_id": cust["id"], "subject": "UPS pitando", "priority": "media", "assigned_to": tec}).json()
    r = client.post(f"/tickets/{t3['id']}/work-order", json={"technician_id": tec})
    assert r.status_code == 201 and r.json()["status"] == "asignada"
    assert db_session.get(WorkOrder, r.json()["work_order_id"]).technician_id == tec
    assert _para(_correos(db_session, "work_order", r.json()["work_order_id"], "Orden de trabajo asignada")) == ["tecnico-tk@ejemplo.com"]


def test_visita_de_un_ticket_con_tecnico_aliado_le_avisa_por_su_portal(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    admin_h = dict(client.headers)
    a = aliado(client, admin_h, "Aliado Visita S.A.", "3-101-000099")
    tec_h = usuario(client, admin_h, a, "tec@aliado-visita.ejemplo.com", "tecnico_aliado", "Tecnico Aliado Visita")
    tec = uid(client, tec_h)
    body = {"customer_id": _cliente(client)["id"], "subject": "Grabador sin disco", "priority": "media", "assigned_to": tec}
    t = client.post("/tickets", json=body).json()
    assert t["ally_company_id"] == a, t

    # "Mandar un tecnico" con el responsable aliado: la OT es de su empresa y el tecnico recibe un solo aviso, el del
    # portal de aliados (el de Ordenes de trabajo de Crimson no, porque no la puede abrir)
    r = client.post(f"/tickets/{t['id']}/work-order", json={"technician_id": tec})
    assert r.status_code == 201 and r.json()["status"] == "asignada", r.text
    o = db_session.get(WorkOrder, r.json()["work_order_id"])
    assert o.ally_company_id == a and o.technician_id == tec
    mails = _correos(db_session, "ally_work", o.id, f"Tiene un trabajo nuevo: {o.number}")
    assert _para(mails) == ["tec@aliado-visita.ejemplo.com"] and f"{BASE}/" in mails[0].html, [m.subject for m in mails]
    assert _correos(db_session, "work_order", o.id, "Orden de trabajo asignada") == []


def test_ot_cerrada_editada_no_avisa_ni_se_reabre(client, auth, db_session):
    tid = auth["tenant"]["id"]
    sup = _login(client, db_session, tid, "supervisor-otc@ejemplo.com", "supervisor")
    _login(client, db_session, tid, "tecnico-otc1@ejemplo.com", "tecnico")
    _login(client, db_session, tid, "tecnico-otc2@ejemplo.com", "tecnico")
    t1 = db_session.scalar(select(User).where(User.email == "tecnico-otc1@ejemplo.com")).id
    t2 = db_session.scalar(select(User).where(User.email == "tecnico-otc2@ejemplo.com")).id
    o = client.post("/work-orders", json={"title": "Visita que se cancela", "technician_id": t1}, headers=sup).json()
    assert client.post(f"/work-orders/{o['id']}/cancel", json={}, headers=sup).status_code == 200
    # corregirla (p. ej. anotar quien iba a ir) no le avisa a nadie de trabajo por una orden cancelada
    r = client.put(f"/work-orders/{o['id']}", json={"helpers": [t2]}, headers=sup)
    assert r.status_code == 200 and r.json()["status"] == "cancelada", r.text
    assert _para(_correos(db_session, "work_order", o["id"], "Orden de trabajo asignada")) == ["tecnico-otc1@ejemplo.com"]


def test_tiempo_registrado_saca_la_ot_de_por_programar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _login(client, db_session, tid, "tecnico-tiempo@ejemplo.com", "tecnico")
    tec = db_session.scalar(select(User).where(User.email == "tecnico-tiempo@ejemplo.com")).id
    t = client.post("/tickets", json={"customer_id": _cliente(client)["id"], "subject": "Sin red", "priority": "media"}).json()
    oid = client.post(f"/tickets/{t['id']}/work-order", json={}).json()["work_order_id"]
    assert client.get(f"/work-orders/{oid}").json()["status"] == "por_programar"
    # el supervisor le registra el tiempo a quien fue: queda de ayudante y la orden ya tiene equipo
    inicio = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    r = client.post(f"/work-orders/{oid}/time", json={"user_id": tec, "started_at": inicio, "minutes": 60})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "asignada" and tec in r.json()["helpers"]


# ---------- precio del aliado ----------
def test_precio_del_aliado_le_llega_a_quien_aprueba_no_a_la_vendedora(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    admin_h = dict(client.headers)
    _tid, ven, _sup = _equipo(client, db_session, auth, "aliado")
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "Cámaras planta", "customer_id": cust["id"]}, headers=ven).json()
    a = aliado(client, admin_h, "Aliado Avisos S.A.", "3-101-000077")
    jefe = usuario(client, admin_h, a, "jefe@aliado-avisos.ejemplo.com", "aliado", "Jefa Aliada")
    participa(client, admin_h, opp["id"], a)

    r = client.post(f"/aliado/oportunidades/{opp['id']}/precios", json={"what": "Instalación de 8 cámaras", "amount": 450000, "currency": "CRC"}, headers=jefe)
    assert r.status_code == 201, r.text
    mails = _correos(db_session, "opportunity", opp["id"], "Aliado Avisos S.A. · precio recibido")
    assert _para(mails) == [auth["user"]["email"]]  # el admin aprueba y ve costos; la vendedora no
    assert f"{BASE}/oportunidades?id={opp['id']}" in mails[0].html and "450" not in mails[0].html
    # un segundo precio enseguida en la misma oportunidad: un solo aviso
    client.post(f"/aliado/oportunidades/{opp['id']}/precios", json={"what": "Cableado adicional", "amount": 90000, "currency": "CRC"}, headers=jefe)
    assert len(_correos(db_session, "opportunity", opp["id"], "Aliado Avisos S.A. · precio recibido")) == 1
    # la bitacora de la oportunidad (que la vendedora si ve) dice que llego
    assert "Instalación de 8 cámaras" in client.get(f"/opportunities/{opp['id']}", headers=ven).json()["notes"]


# ---------- levantamientos solicitados ----------
def test_levantamiento_solicitado_crear_y_asignar(client, auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", BASE)
    tid, ven, sup = _equipo(client, db_session, auth, "lev")
    _login(client, db_session, tid, "tecnico-lev@ejemplo.com", "tecnico")
    tec = db_session.scalar(select(User).where(User.email == "tecnico-lev@ejemplo.com")).id
    cust = _cliente(client)
    opp = client.post(
        "/opportunities", json={"title": "Acceso oficinas", "solution": "acceso", "site": "Oficinas centrales", "customer_id": cust["id"]}, headers=ven
    ).json()
    antes = _sec(client, "levantamientos_solicitados", sup)["count"]
    assert client.post(f"/opportunities/{opp['id']}/request-survey", json={"preferred_date": "2026-11-02"}, headers=ven).status_code == 200

    sec = _sec(client, "levantamientos_solicitados", sup)
    assert sec["count"] == antes + 1
    it = next(i for i in sec["items"] if i["id"] == opp["id"])
    assert it["action"] == "crear_levantamiento" and it["to"] == f"/levantamientos?nuevo={opp['id']}"
    assert it["title"] == cust["name"] and it["who"] == "Ventas" and it["badge"] == "Prefiere el 02/11" and it["amount"] is None
    assert client.get("/approvals", headers=ven).status_code == 403  # la vendedora no tiene la bandeja

    # crear y asignar: nace ligado a la oportunidad, a nombre del tecnico, que recibe el aviso con enlace
    r = client.post(f"/surveys/from-opportunity/{opp['id']}", json={"technician_id": tec, "visit_date": "2026-11-02"}, headers=sup)
    assert r.status_code == 201, r.text
    s = db_session.get(Survey, r.json()["id"])
    assert s.opportunity_id == opp["id"] and s.technician_id == tec and s.kind == "acceso" and s.customer_id == cust["id"] and s.site == "Oficinas centrales"
    db_session.expire_all()
    o = db_session.get(Opportunity, opp["id"])
    assert o.status == "levantamiento" and s.number in o.notes
    mails = _correos(db_session, "survey", s.id, "Tiene un levantamiento nuevo")
    assert _para(mails) == ["tecnico-lev@ejemplo.com"] and f"{BASE}/levantamientos?id={s.id}" in mails[0].html
    # atendida: sale de la seccion y no se crea dos veces
    assert opp["id"] not in [i["id"] for i in _sec(client, "levantamientos_solicitados", sup)["items"]]
    # (el supervisor ya no la ve: la solicitud se atendio; el admin, que ve el embudo, recibe el motivo)
    assert client.post(f"/surveys/from-opportunity/{opp['id']}", json={"technician_id": tec}, headers=sup).status_code == 404
    r = client.post(f"/surveys/from-opportunity/{opp['id']}", json={"technician_id": tec})
    assert r.status_code == 409 and s.number in r.json()["detail"]
    # el tecnico no crea y asigna; una oportunidad ajena sin solicitud no se puede usar desde el campo
    tk = _login(client, db_session, tid, "tecnico-lev2@ejemplo.com", "tecnico")
    otra = client.post("/opportunities", json={"title": "Sin solicitud", "customer_id": cust["id"]}, headers=ven).json()
    assert client.post(f"/surveys/from-opportunity/{otra['id']}", json={"technician_id": tec}, headers=tk).status_code == 403
    assert client.post(f"/surveys/from-opportunity/{otra['id']}", json={"technician_id": tec}, headers=sup).status_code == 404


def test_crear_y_asignar_no_reparte_el_correo_del_lead(client, auth, db_session):
    admin_h = dict(client.headers)
    tid, ven, sup = _equipo(client, db_session, auth, "lead")
    _login(client, db_session, tid, "tecnico-lead@ejemplo.com", "tecnico")
    tec = db_session.scalar(select(User).where(User.email == "tecnico-lead@ejemplo.com")).id
    contacto = {"name": "Persona Lead", "email": "persona.lead@ejemplo.com", "phone": "8800-0000"}

    def lead(titulo):
        return client.post("/opportunities", json={"title": titulo, "solution": "cctv", "site": "Local", "contact": contacto}, headers=ven).json()

    # supervisor que no ve la oportunidad en el embudo (entra por la solicitud pendiente): sin el contacto del lead
    o1 = lead("Lead uno")
    assert client.post(f"/opportunities/{o1['id']}/request-survey", json={}, headers=ven).status_code == 200
    r = client.post(f"/surveys/from-opportunity/{o1['id']}", json={"technician_id": tec}, headers=sup)
    assert r.status_code == 201, r.text
    assert "persona.lead@ejemplo.com" not in str(r.json()) and not db_session.get(Survey, r.json()["id"]).contact

    # quien si la ve, para un tecnico de Crimson: el contacto completo
    o2 = lead("Lead dos")
    r = client.post(f"/surveys/from-opportunity/{o2['id']}", json={"technician_id": tec})
    assert r.status_code == 201 and db_session.get(Survey, r.json()["id"]).contact == contacto, r.text

    # para un tecnico aliado: nombre y telefono, nunca el correo (tampoco en su portal)
    o3 = lead("Lead tres")
    a = aliado(client, admin_h, "Aliado Lead S.A.", "3-101-000088")
    tec_h = usuario(client, admin_h, a, "tec@aliado-lead.ejemplo.com", "tecnico_aliado", "Tecnico Aliado Lead")
    participa(client, admin_h, o3["id"], a)
    r = client.post(f"/surveys/from-opportunity/{o3['id']}", json={"technician_id": uid(client, tec_h)})
    assert r.status_code == 201, r.text
    s = db_session.get(Survey, r.json()["id"])
    assert s.ally_company_id == a and s.contact == {"name": "Persona Lead", "phone": "8800-0000"}
    vista = client.get(f"/aliado/levantamientos/{s.id}", headers=tec_h)
    assert vista.status_code == 200 and "persona.lead@ejemplo.com" not in vista.text, vista.text


def test_quote_out_sin_proyecto_y_sin_rechazo(client, auth):
    q = client.post("/quotes", json={"customer_id": _cliente(client)["id"], "lines": [_linea()]}).json()
    assert q["project_id"] is None and q["project_number"] is None and q["rejection"] is None
    assert Quote.__tablename__ == "quote"
