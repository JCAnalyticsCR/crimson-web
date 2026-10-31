"""Recurrencias de factura que se envian solas (plan 1.4): la primera espera "Aprobar y enviar", las siguientes salen
con su PDF y enlace de pago, "enviada" solo si el proveedor acepto el correo, aviso al admin si falla o si el cliente no
tiene correo, y vista previa al admin a D-3. Nombres y correos ficticios (repo publico)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.test_session4 import _user_with_role

HOY = date.today()
CLIENTE = "cuentas@cliente-recurrente.ejemplo.com"
LINEA = {"name": "Monitoreo mensual", "quantity": 1, "unit_price": 50000, "tax_rate": 13, "cabys_code": "8121100000100"}


@pytest.fixture
def correo(monkeypatch):
    from app.services import documents_send, mail

    estado = {"ok": True, "salida": []}

    def deliver(m, files=None):
        if estado["ok"]:
            m.status, m.provider_id = "enviado", f"re_{m.id}"
        else:
            m.status, m.error = "error", "Resend no responde (503)"
        estado["salida"].append((m, [n for n, _ in (files or [])]))

    monkeypatch.setattr(mail, "deliver", deliver)
    monkeypatch.setattr(documents_send, "render_pdf", lambda html: b"%PDF-1.7 prueba")
    return estado


def _cliente(client, email: str | None = CLIENTE) -> int:
    r = client.post("/customers", json={"name": "Cliente Recurrente S.A.", "id_type": "juridica", "id_number": "3-101-999992", **({"email": email} if email else {})})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _rec(client, cid: int, next_date: date = HOY, **extra) -> dict:
    body = {"name": "Monitoreo", "kind": "factura", "customer_id": cid, "template": {"lines": [LINEA]}, "frequency": "mensual", "next_date": next_date.isoformat(), **extra}
    r = client.post("/recurrences", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _al(correo, to: str) -> list:
    return [x for x in correo["salida"] if x[0].to == to]


def _admin(correo) -> list:
    return [m for m, _ in _al(correo, "admin@crimsonapp.com")]


@pytest.fixture
def conta_sin_envio(monkeypatch):
    """Contabilidad como antes del plan 1.7 (sin sales.enviar): asi se sigue probando la guarda de quien gestiona
    mensualidades pero no puede mandarlas. Con 1.7 integrado, el rol real de contabilidad si envia facturas."""
    from app.core.deps import ROLE_PERMISSIONS

    monkeypatch.setitem(ROLE_PERMISSIONS["contabilidad"], "sales", [x for x in ROLE_PERMISSIONS["contabilidad"]["sales"] if x != "enviar"])


def test_primera_por_aprobar_y_despues_salen_solas(client, auth, db_session, correo):
    from app.models import Invoice, Recurrence
    from app.routers.ops import run_due_recurrences

    cid = _cliente(client)
    rec = _rec(client, cid, next_date=HOY - timedelta(days=31))  # dos periodos atrasados
    assert rec["first_run_requires_approval"] is True and rec["auto_send"] is True
    assert run_due_recurrences(db_session, today=HOY) == 1
    r = db_session.get(Recurrence, rec["id"])
    db_session.refresh(r)
    primera = db_session.get(Invoice, r.last_invoice_id)
    assert r.last_send_status == "por_aprobar" and primera.status == "creado" and primera.sent_at is None
    assert not _al(correo, CLIENTE)  # nada al cliente todavia
    assert any("por aprobar" in m.subject for m in _admin(correo))
    # mientras la primera espera, el periodo atrasado no le apila otra factura al cliente
    assert run_due_recurrences(db_session, today=HOY) == 0
    assert db_session.scalar(select(Recurrence).where(Recurrence.id == rec["id"])).last_invoice_id == primera.id
    # la lista de Recurrencias muestra la ultima con su estado
    fila = next(x for x in client.get("/recurrences").json() if x["id"] == rec["id"])
    assert fila["last_invoice"]["number"] == primera.number and fila["last_send_status"] == "por_aprobar"
    # Aprobar y enviar
    out = client.post(f"/recurrences/{rec['id']}/approve-send").json()
    assert out["sent"] is True and out["status"] == "enviada"
    m, files = _al(correo, CLIENTE)[-1]
    # sin comprobante fiscal (modo "ninguno") cualquier recurrencia sale como aviso de cobro, no solo la mensualidad de
    # aliados (plan 1.4, render.doc_kind): contabilidad emite el comprobante real aparte y no circulan dos "facturas"
    assert m.subject.startswith("Aviso de cobro") and "Le compartimos el <b>aviso de cobro" in m.html and "/pagar/" in m.html and files == [f"{primera.number}.pdf"]
    assert primera.recurrence_id == rec["id"]
    assert client.post(f"/recurrences/{rec['id']}/approve-send").status_code == 409  # ya salio
    # el periodo siguiente sale solo
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    segunda = db_session.get(Invoice, r.last_invoice_id)
    assert segunda.id != primera.id and segunda.status == "enviada" and segunda.sent_at and r.last_send_status == "enviada"
    assert len(_al(correo, CLIENTE)) == 2
    # doble disparo del worker el mismo dia: ni otra factura ni otro correo
    assert run_due_recurrences(db_session, today=HOY) == 0 and len(_al(correo, CLIENTE)) == 2


def _retry_outbox(db_session) -> int:
    """Lo mismo que worker/celery_app.retry_outbox: reintenta pendiente|error SIN adjuntos (no se guardan)."""
    from app.models import EmailOutbox
    from app.services.mail import SENSITIVE_ENTITIES, deliver

    n = 0
    for m in db_session.scalars(select(EmailOutbox).where(EmailOutbox.status.in_(("pendiente", "error")), EmailOutbox.entity.not_in(list(SENSITIVE_ENTITIES)))):
        deliver(m)
        n += 1
    db_session.commit()
    return n


def test_resend_caido_queda_pendiente_y_avisa(client, auth, db_session, correo):
    from app.models import EmailOutbox, Invoice, Recurrence
    from app.routers.ops import run_due_recurrences

    cid = _cliente(client)
    rec = _rec(client, cid, first_run_requires_approval=False)
    correo["ok"] = False
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    inv = db_session.get(Invoice, r.last_invoice_id)
    assert r.last_send_status == "fallida" and "no salió" in r.last_send_note.lower()
    assert inv.status == "creado" and inv.sent_at is None  # nunca "enviada" sin que el proveedor acepte
    assert any("sin enviar" in m.subject for m in _admin(correo))
    # mail.retry_outbox (cada 15 min) no lo toma: lo mandaria sin el PDF y sin marcar la factura enviada
    al_cliente = db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == CLIENTE)).all()
    assert [m.status for m in al_cliente] == ["fallido"]
    correo["ok"] = True
    _retry_outbox(db_session)  # reintenta los avisos al admin (sin adjuntos), nunca el del cliente
    assert len(_al(correo, CLIENTE)) == 1
    db_session.expire_all()
    assert db_session.get(Invoice, inv.id).sent_at is None and db_session.get(Recurrence, rec["id"]).last_send_status == "fallida"
    correo["ok"] = False
    sec = next(x for x in client.get("/approvals").json()["sections"] if x["key"] == "mensualidades_por_aprobar")
    item = next(i for i in sec["items"] if i["id"] == inv.id)
    assert item["action"]["label"] == "Reenviar" and item["badge"] == "No salió: reenviar"
    correo["ok"] = True
    assert client.post(item["action"]["path"]).json()["sent"] is True
    db_session.expire_all()
    assert db_session.get(Invoice, inv.id).status == "enviada"


def test_sin_envio_automatico_y_cliente_sin_correo(client, auth, db_session, correo):
    from app.models import Recurrence
    from app.routers.ops import run_due_recurrences

    a = _rec(client, _cliente(client), auto_send=False)
    b = _rec(client, _cliente(client, email=None), first_run_requires_approval=False)
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    assert db_session.get(Recurrence, a["id"]).last_send_status == "pendiente"
    rb = db_session.get(Recurrence, b["id"])
    assert rb.last_send_status == "sin_correo"
    assert not _al(correo, CLIENTE)
    assert any("sin enviar" in m.subject and "no tiene correo" in m.html for m in _admin(correo))


def test_enviarla_a_mano_cuenta_como_aprobada(client, auth, db_session, correo):
    from app.models import EmailOutbox, Invoice, Recurrence
    from app.routers.ops import run_due_recurrences
    from app.tasks.mensualidades import preview_upcoming

    rec = _rec(client, _cliente(client), next_date=HOY - timedelta(days=31))
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    primera = db_session.get(Recurrence, rec["id"]).last_invoice_id
    assert client.post(f"/invoices/{primera}/email", json={}).json()["sent"] is True
    # la recurrencia lo registra en el momento (no al generar el periodo siguiente): Recurrencias dice "enviada", sale
    # de Aprobaciones y la vista previa D-3 ya no anuncia "Aprobar y enviar"
    fila = next(x for x in client.get("/recurrences").json() if x["id"] == rec["id"])
    assert fila["last_send_status"] == "enviada" and fila["first_run_requires_approval"] is False and fila["last_send_note"] is None
    assert primera not in _por_aprobar(client)
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    atrasada = r.next_date
    r.next_date = HOY + timedelta(days=3)
    db_session.commit()
    assert preview_upcoming(db_session, today=HOY) == 1
    m = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("En 3 días%"))).all()[-1]
    assert "Sale sola con su PDF" in m.html and "Aprobar y enviar" not in m.html
    r = db_session.get(Recurrence, rec["id"])
    r.next_date = atrasada
    db_session.commit()
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.first_run_requires_approval is False and r.last_invoice_id != primera
    assert db_session.get(Invoice, r.last_invoice_id).status == "enviada"


def test_aprobar_exige_permiso_de_enviar(client, auth, db_session, correo, conta_sin_envio):
    rec = _rec(client, _cliente(client))
    client.post(f"/recurrences/{rec['id']}/run")
    pw = _user_with_role(db_session, auth["tenant"]["id"], "conta.recurrente@ejemplo.com", "contabilidad")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "conta.recurrente@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"/recurrences/{rec['id']}/approve-send", headers=h).status_code == 403
    keys = [s["key"] for s in client.get("/approvals", headers=h).json()["sections"]]
    assert "mensualidades_por_aprobar" not in keys  # sin sales.enviar no ve la seccion (conta_sin_envio)


def test_vista_previa_al_admin_a_d_menos_3(client, auth, db_session, correo):
    from app.models import EmailOutbox
    from app.tasks.mensualidades import preview_upcoming

    cid = _cliente(client)
    _rec(client, cid, next_date=HOY + timedelta(days=3), name="Monitoreo sede central")
    _rec(client, cid, next_date=HOY + timedelta(days=5), name="Otra fecha")
    assert preview_upcoming(db_session, today=HOY) == 1
    assert preview_upcoming(db_session, today=HOY) == 0  # doble disparo del beat: una sola vista previa por dia
    m = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("En 3 días%"))).all()[-1]
    assert m.to == "admin@crimsonapp.com" and "Monitoreo sede central" in m.html and "Otra fecha" not in m.html
    assert "₡56,500.00" in m.html and "Aprobar y enviar" in m.html
    assert preview_upcoming(db_session, today=HOY + timedelta(days=1)) == 0  # ese dia no se cobra nada a 3 dias


def test_recurrencias_existentes_quedan_por_aprobar(db_session):
    """La migracion deja las recurrencias que ya existian con la aprobacion pedida (server_default): hasta hoy auto_send
    no lo leia nadie y encenderlo de golpe le mandaria facturas a clientes sin revisar."""
    from app.models import Recurrence

    col = Recurrence.__table__.c.first_run_requires_approval
    assert col.server_default is not None and col.default.arg is True


def test_genera_con_la_fecha_de_costa_rica(client, auth, db_session, correo, monkeypatch):
    """Borde 18:00-24:00 CR: a las 23:30 del 30/11 en Costa Rica ya es 1/12 en UTC; la mensualidad del 1/12 no sale un
    dia antes. Y el doble disparo del worker ese dia no genera otra."""
    from zoneinfo import ZoneInfo

    from app.models import Recurrence
    from app.providers import einvoice as prov
    from app.routers.ops import run_due_recurrences

    cr = ZoneInfo("America/Costa_Rica")
    rec = _rec(client, _cliente(client), next_date=date(2026, 12, 1), first_run_requires_approval=False)
    monkeypatch.setattr(prov, "cr_now", lambda: datetime(2026, 11, 30, 23, 30, tzinfo=cr))
    run_due_recurrences(db_session)
    db_session.expire_all()
    assert db_session.get(Recurrence, rec["id"]).runs == 0 and not _al(correo, CLIENTE)
    monkeypatch.setattr(prov, "cr_now", lambda: datetime(2026, 12, 1, 0, 10, tzinfo=cr))
    run_due_recurrences(db_session)
    run_due_recurrences(db_session)
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.runs == 1 and r.next_date == date(2027, 1, 1) and r.last_send_status == "enviada" and len(_al(correo, CLIENTE)) == 1


def test_error_interno_al_enviar_queda_para_reenviar(client, auth, db_session, correo, monkeypatch):
    from app.models import Invoice, Recurrence
    from app.routers.ops import run_due_recurrences
    from app.services import documents_send

    rec = _rec(client, _cliente(client), first_run_requires_approval=False)

    def revienta(*a, **k):
        raise RuntimeError("falla de prueba")

    monkeypatch.setattr(documents_send, "send_document", revienta)
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_send_status == "fallida" and db_session.get(Invoice, r.last_invoice_id).sent_at is None
    assert any("sin enviar" in m.subject for m in _admin(correo))
    sec = next(x for x in client.get("/approvals").json()["sections"] if x["key"] == "mensualidades_por_aprobar")
    assert r.last_invoice_id in {i["id"] for i in sec["items"]}


# ---------- revision humana y periodos detenidos (revision de la rama 1.3/1.4) ----------
def _como(client, db_session, auth, email: str, rol: str) -> dict:
    pw = _user_with_role(db_session, auth["tenant"]["id"], email, rol)
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    return {"Authorization": f"Bearer {tok}"}


def _body(rec: dict, **cambios) -> dict:
    keys = ("name", "kind", "customer_id", "template", "frequency", "next_date", "end_date", "auto_send", "active", "anchor_day", "first_run_requires_approval")
    return {**{k: rec[k] for k in keys}, **cambios}


def _por_aprobar(client) -> dict[int, dict]:
    sec = next(x for x in client.get("/approvals", params={"limit": 50}).json()["sections"] if x["key"] == "mensualidades_por_aprobar")
    return {i["id"]: i for i in sec["items"]}


def test_un_envio_viejo_no_apaga_la_revision_pedida(client, auth, db_session, correo):
    """La migracion deja las recurrencias existentes con la revision pedida, y el relleno de cobranza le puso sent_at a
    sus facturas viejas ("vencida"). Esa factura no es la que esperaba "Aprobar y enviar": la siguiente no sale sola."""
    from app.models import Invoice, Recurrence
    from app.routers.ops import run_due_recurrences

    cid = _cliente(client)
    rec = _rec(client, cid, next_date=HOY - timedelta(days=31))
    vieja = client.post("/invoices", json={"customer_id": cid, "lines": [LINEA]}).json()["id"]
    inv = db_session.get(Invoice, vieja)
    inv.status, inv.sent_at = "vencida", datetime.now(UTC) - timedelta(days=40)
    r = db_session.get(Recurrence, rec["id"])
    r.last_invoice_id, r.last_send_status = vieja, None
    db_session.commit()
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_invoice_id != vieja and r.last_send_status == "por_aprobar" and r.first_run_requires_approval is True
    assert not _al(correo, CLIENTE)


def test_volver_a_pedir_la_revision_detiene_la_proxima(client, auth, db_session, correo):
    from app.models import Recurrence
    from app.routers.ops import run_due_recurrences

    rec = _rec(client, _cliente(client), first_run_requires_approval=False)
    run_due_recurrences(db_session, today=HOY)
    assert len(_al(correo, CLIENTE)) == 1
    fila = next(x for x in client.get("/recurrences").json() if x["id"] == rec["id"])
    assert fila["last_send_status"] == "enviada"
    assert client.put(f"/recurrences/{rec['id']}", json=_body(fila, first_run_requires_approval=True)).json()["first_run_requires_approval"] is True
    db_session.expire_all()
    siguiente = db_session.get(Recurrence, rec["id"]).next_date
    assert run_due_recurrences(db_session, today=siguiente) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_send_status == "por_aprobar" and r.first_run_requires_approval is True and len(_al(correo, CLIENTE)) == 1


def test_generar_ahora_no_apila_otra_mientras_la_anterior_espera(client, auth, db_session, correo):
    rec = _rec(client, _cliente(client))
    a = client.post(f"/recurrences/{rec['id']}/run")
    assert a.status_code == 201 and a.json()["send_status"] == "por_aprobar"
    b = client.post(f"/recurrences/{rec['id']}/run")
    assert b.status_code == 409 and "Aprobar y enviar" in b.json()["detail"]
    fila = next(x for x in client.get("/recurrences").json() if x["id"] == rec["id"])
    assert fila["last_invoice_id"] == a.json()["invoice_id"] and fila["runs"] == 1
    assert a.json()["invoice_id"] in _por_aprobar(client)
    # anulada la anterior, ya se puede generar otra
    assert client.post(f"/invoices/{a.json()['invoice_id']}/void").status_code == 200
    assert client.post(f"/recurrences/{rec['id']}/run").status_code == 201


def test_la_que_no_salio_detiene_el_periodo_siguiente(client, auth, db_session, correo):
    from app.models import EmailOutbox, Recurrence
    from app.routers.ops import run_due_recurrences

    rec = _rec(client, _cliente(client), first_run_requires_approval=False)
    correo["ok"] = False
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    primera, siguiente = r.last_invoice_id, r.next_date
    correo["ok"] = True
    # llega el periodo siguiente: no se genera otra ni la anterior sale de Aprobaciones; un solo aviso aunque el worker
    # corra dos veces
    assert run_due_recurrences(db_session, today=siguiente) == 0
    assert run_due_recurrences(db_session, today=siguiente) == 0
    db_session.expire_all()
    assert db_session.get(Recurrence, rec["id"]).last_invoice_id == primera
    detenida = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("Recurrencia detenida%"))).all()
    assert len(detenida) == 1 and detenida[0].to == "admin@crimsonapp.com" and "no le llegó al cliente" in detenida[0].html
    assert _por_aprobar(client)[primera]["action"]["label"] == "Reenviar"
    assert client.post(f"/recurrences/{rec['id']}/run").status_code == 409
    # reenviada, el periodo siguiente sale solo
    assert client.post(f"/recurrences/{rec['id']}/approve-send").json()["sent"] is True
    assert run_due_recurrences(db_session, today=siguiente) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_invoice_id != primera and r.last_send_status == "enviada"
    assert len(_al(correo, CLIENTE)) == 3  # el intento que fallo, el reenvio y el periodo siguiente


def test_contabilidad_no_envia_ni_quita_la_revision(client, auth, db_session, correo, conta_sin_envio):
    from app.models import AuditLog

    h = _como(client, db_session, auth, "conta.envio@ejemplo.com", "contabilidad")
    cid = _cliente(client)
    body = {"name": "Soporte", "kind": "factura", "customer_id": cid, "template": {"lines": [LINEA]}, "frequency": "mensual", "next_date": HOY.isoformat()}
    assert client.post("/recurrences", json={**body, "first_run_requires_approval": False}, headers=h).status_code == 403
    rc = client.post("/recurrences", json=body, headers=h)
    assert rc.status_code == 201 and rc.json()["first_run_requires_approval"] is True
    assert client.put(f"/recurrences/{rc.json()['id']}", json={**body, "first_run_requires_approval": False}, headers=h).status_code == 403
    # una que ya sale sola (la dejo asi el admin, y queda en la bitacora): "Generar ahora" de contabilidad no le manda
    # nada al cliente, queda por aprobar para quien envia
    rec = _rec(client, cid, first_run_requires_approval=False)
    log = db_session.scalars(select(AuditLog).where(AuditLog.entity == "recurrence", AuditLog.entity_id == rec["id"], AuditLog.action == "create")).one()
    assert log.diff["first_run_requires_approval"] is False and log.diff["auto_send"] is True
    out = client.post(f"/recurrences/{rec['id']}/run", headers=h).json()
    assert out["send_status"] == "por_aprobar" and out["sent"] is False and not _al(correo, CLIENTE)
    item = _por_aprobar(client)[out["invoice_id"]]
    assert item["action"]["label"] == "Aprobar y enviar" and item["badge"] == "Por aprobar: revisala"
    assert client.post(item["action"]["path"], headers=h).status_code == 403
    assert client.post(item["action"]["path"]).json()["sent"] is True  # el admin la aprueba


def test_cambiar_cliente_o_monto_vuelve_a_pedir_la_revision(client, auth, db_session, correo):
    from app.models import AuditLog

    cid = _cliente(client)
    rec = _rec(client, cid, first_run_requires_approval=False)
    # solo el nombre: la revision no vuelve
    assert client.put(f"/recurrences/{rec['id']}", json=_body(rec, name="Monitoreo sede")).json()["first_run_requires_approval"] is False
    # otro precio: la proxima vuelve a esperar "Aprobar y enviar" aunque el formulario mande la bandera apagada
    caro = {"lines": [{**LINEA, "unit_price": 90000}]}
    assert client.put(f"/recurrences/{rec['id']}", json=_body(rec, template=caro)).json()["first_run_requires_approval"] is True
    q = select(AuditLog).where(AuditLog.entity == "recurrence", AuditLog.entity_id == rec["id"], AuditLog.action == "update").order_by(AuditLog.id)
    log = db_session.scalars(q).all()[-1]
    assert log.after["first_run_requires_approval"] is True and log.before["total"] != log.after["total"]
    # quien envia facturas la quita de nuevo en otro guardado (sin cambiar el monto)
    assert client.put(f"/recurrences/{rec['id']}", json=_body(rec, template=caro)).json()["first_run_requires_approval"] is False
    # otro cliente: tambien la vuelve a pedir
    otro = _cliente(client, email="otra@cliente-recurrente.ejemplo.com")
    assert client.put(f"/recurrences/{rec['id']}", json=_body(rec, template=caro, customer_id=otro)).json()["first_run_requires_approval"] is True


def test_pagada_por_otra_via_no_se_cobra_ni_detiene(client, auth, db_session, correo):
    from app.models import Recurrence
    from app.routers.ops import run_due_recurrences

    rec = _rec(client, _cliente(client), next_date=HOY - timedelta(days=31))
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    iid = db_session.get(Recurrence, rec["id"]).last_invoice_id
    total = client.get(f"/invoices/{iid}").json()["total"]
    assert client.post(f"/invoices/{iid}/payments", json={"method": "sinpe", "kind": "captura", "amount": total}).status_code in (200, 201)
    assert iid not in _por_aprobar(client)
    assert client.post(f"/recurrences/{rec['id']}/approve-send").status_code == 409
    assert not _al(correo, CLIENTE)
    # el periodo siguiente ya no espera a la pagada; nadie aprobo nada, asi que la nueva tambien espera
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_invoice_id != iid and r.last_send_status == "por_aprobar" and r.first_run_requires_approval is True


def test_produccion_sin_emitir_no_sale_como_aviso(client, auth, db_session, correo, monkeypatch):
    """Con Hacienda en produccion la factura sale con su comprobante (la entrega fiscal manda el XML): sin emitir no se
    manda sola ni desde "Aprobar y enviar"; queda por aprobar con la nota y el aviso al admin."""
    from app.models import Recurrence
    from app.routers.ops import run_due_recurrences
    from app.services import hacienda_config

    monkeypatch.setattr(hacienda_config, "hacienda_mode", lambda db, t: "produccion")
    rec = _rec(client, _cliente(client), first_run_requires_approval=False)
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_send_status == "por_aprobar" and "Emítala" in r.last_send_note and not _al(correo, CLIENTE)
    assert any("por aprobar" in m.subject and "producción" in m.html for m in _admin(correo))
    assert not _por_aprobar(client)[r.last_invoice_id].get("action")  # se abre la factura para emitirla
    res = client.post(f"/recurrences/{rec['id']}/approve-send")
    assert res.status_code == 409 and "Emítala" in res.json()["detail"] and not _al(correo, CLIENTE)


def test_enlace_compartido_tambien_la_registra(client, auth, db_session, correo):
    """Compartir el enlace de pago (POST /invoices/{id}/payment-link) es entregarla: la que no salio deja de figurar
    "fallida" y sale de Aprobaciones."""
    from app.models import Recurrence
    from app.routers.ops import run_due_recurrences

    rec = _rec(client, _cliente(client), first_run_requires_approval=False)
    correo["ok"] = False
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    iid = db_session.get(Recurrence, rec["id"]).last_invoice_id
    assert db_session.get(Recurrence, rec["id"]).last_send_status == "fallida" and iid in _por_aprobar(client)
    assert client.post(f"/invoices/{iid}/payment-link").status_code == 200
    fila = next(x for x in client.get("/recurrences").json() if x["id"] == rec["id"])
    assert fila["last_send_status"] == "enviada" and iid not in _por_aprobar(client)


def test_envio_interrumpido_no_se_pierde(client, auth, db_session, correo):
    """El worker se cae entre guardar la factura y mandarla (la tarea se reentrega, pero next_date ya avanzo): queda
    "enviando", detiene el periodo siguiente, sale en Aprobaciones y en la corrida siguiente pasa a "fallida" con el
    aviso al admin. No se manda sola otra vez (el correo pudo haber salido)."""
    from app.models import Recurrence
    from app.routers.ops import run_due_recurrences, run_recurrence

    rec = _rec(client, _cliente(client), first_run_requires_approval=False)
    r = db_session.get(Recurrence, rec["id"])
    factura = run_recurrence(db_session, r, None)
    db_session.commit()  # aqui se cae el worker: nunca llega a deliver_recurrence
    fid = factura.id
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    siguiente = r.next_date
    assert r.last_send_status == "enviando"
    item = _por_aprobar(client)[fid]
    assert item["badge"] == "Envío interrumpido: revisá y reenviá" and item["action"]["label"] == "Reenviar"
    # recien guardada (un envio en curso): la corrida no la toca, y tampoco genera el periodo siguiente
    assert run_due_recurrences(db_session, today=siguiente) == 0
    db_session.expire_all()
    assert db_session.get(Recurrence, rec["id"]).last_send_status == "enviando"
    # mas tarde: interrumpida -> fallida, aviso al admin, sin otro correo al cliente ni otra factura
    r = db_session.get(Recurrence, rec["id"])
    r.updated_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    assert run_due_recurrences(db_session, today=siguiente) == 0
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.last_send_status == "fallida" and "interrumpi" in r.last_send_note and r.last_invoice_id == fid
    assert not _al(correo, CLIENTE) and any("sin enviar" in m.subject and "interrumpió" in m.html for m in _admin(correo))
    assert client.post(f"/recurrences/{rec['id']}/approve-send").json()["sent"] is True


def test_generar_ahora_con_error_interno_queda_fallida(client, auth, db_session, correo, monkeypatch):
    from app.services import documents_send

    rec = _rec(client, _cliente(client), first_run_requires_approval=False)

    def revienta(*a, **k):
        raise RuntimeError("falla de prueba")

    monkeypatch.setattr(documents_send, "send_document", revienta)
    out = client.post(f"/recurrences/{rec['id']}/run")
    assert out.status_code == 201 and out.json()["send_status"] == "fallida" and out.json()["sent"] is False
    assert _por_aprobar(client)[out.json()["invoice_id"]]["action"]["label"] == "Reenviar"
    assert any("sin enviar" in m.subject for m in _admin(correo))


def test_contabilidad_cambia_cuando_sale_y_vuelve_la_revision(client, auth, db_session, correo, conta_sin_envio):
    """La revision humana cubre tambien cuando y cada cuanto sale: quien no envia facturas no puede, cambiando la
    frecuencia, adelantando la fecha o volviendo a encender el envio, hacer que salgan facturas sin que nadie las vea."""
    from app.models import AuditLog, Recurrence

    h = _como(client, db_session, auth, "conta.agenda@ejemplo.com", "contabilidad")
    cid = _cliente(client)
    fut = HOY + timedelta(days=20)

    def aprobada(**extra) -> dict:
        rec = _rec(client, cid, next_date=fut, first_run_requires_approval=False, **extra)
        assert rec["first_run_requires_approval"] is False
        return rec

    def guarda(rec, headers=None, **cambios) -> bool:
        r = client.put(f"/recurrences/{rec['id']}", json=_body(rec, **cambios), headers=headers or {})
        assert r.status_code == 200, r.text
        return r.json()["first_run_requires_approval"]

    a = aprobada()
    assert guarda(a, h, frequency="semanal") is True  # cada semana: vuelve a esperar "Aprobar y enviar"
    q = select(AuditLog).where(AuditLog.entity == "recurrence", AuditLog.entity_id == a["id"], AuditLog.action == "update").order_by(AuditLog.id)
    log = db_session.scalars(q).all()[-1]
    assert log.before["frequency"] == "mensual" and log.after["frequency"] == "semanal"
    b = aprobada()
    assert guarda(b, h, next_date=(HOY - timedelta(days=365)).isoformat()) is True  # una fecha de hace un ano
    c = aprobada(auto_send=False)
    assert guarda(c, h, auto_send=True) is True  # el envio que el admin habia apagado
    d = aprobada(active=False)
    assert guarda(d, h, active=True) is True
    # posponer o cambiar el nombre no la pide de nuevo
    e = aprobada()
    assert guarda(e, h, next_date=(fut + timedelta(days=5)).isoformat()) is False
    e = next(x for x in client.get("/recurrences").json() if x["id"] == e["id"])
    assert guarda(e, h, name="Monitoreo sede norte") is False
    # quien envia facturas si puede cambiar la frecuencia sin que vuelva la revision
    f = aprobada()
    assert guarda(f, frequency="semanal") is False
    db_session.expire_all()
    assert db_session.get(Recurrence, a["id"]).first_run_requires_approval is True


def test_mensualidad_no_se_edita_desde_recurrencias(client, auth, db_session, correo):
    """El formulario de Recurrencias arma la plantilla con solo producto y cantidad: guardarlo sobre la mensualidad de
    un aliado le quitaria el monto, la moneda y la marca de aliado. Se edita desde la ficha del aliado."""
    from app.models import AllyCompany, Recurrence

    a = client.post("/allies", json={"name": "Aliado Plantilla S.A.", "tax_id": "3-101-000088", "email": "pagos@aliado-plantilla.ejemplo.com"}).json()["id"]
    prod = {"name": "Mensualidad de la plataforma", "code": "SRV-PLAT-R", "item_type": "servicio", "price": 0, "currency": "CRC", "cabys_code": "8314100000000"}
    pid = client.post("/products", json=prod).json()["id"]
    plan = {"amount": 40, "currency": "USD", "mode": "fija", "service": "Soporte prioritario", "accepted": True, "next_date": (HOY + timedelta(days=10)).isoformat(), "product_id": pid, "crear_cliente": True}
    assert client.put(f"/allies/{a}/plan", json=plan).status_code == 200
    rid = db_session.get(AllyCompany, a).recurrence_id
    filas = client.get("/recurrences").json()
    fila = next(x for x in filas if x["id"] == rid)
    assert fila["plan"] is True and fila["ally_id"] == a
    form = {"lines": [{"product_id": pid, "quantity": "1"}]}  # lo que manda el modal de Recurrencias
    r = client.put(f"/recurrences/{rid}", json=_body(fila, template=form, first_run_requires_approval=True))
    assert r.status_code == 409 and "aliado" in r.json()["detail"]
    db_session.expire_all()
    tpl = db_session.get(Recurrence, rid).template
    assert tpl["external_order"] == "MENSUALIDAD-PLATAFORMA" and tpl["currency"] == "USD" and float(tpl["lines"][0]["unit_price"]) == 40
    # una recurrencia comun se sigue editando desde aqui
    comun = _rec(client, _cliente(client))
    assert comun["plan"] is False and comun["ally_id"] is None
    assert client.put(f"/recurrences/{comun['id']}", json=_body(comun, name="Monitoreo editado")).status_code == 200
