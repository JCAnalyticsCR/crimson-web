"""Recurrencias de factura que se envian solas (plan 1.4): la primera espera "Aprobar y enviar", las siguientes salen
con su PDF y enlace de pago, "enviada" solo si el proveedor acepto el correo, aviso al admin si falla o si el cliente no
tiene correo, y vista previa al admin a D-3. Nombres y correos ficticios (repo publico)."""

from datetime import date, timedelta

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
    assert m.subject.startswith("Aviso de cobro") and "/pagar/" in m.html and files == [f"{primera.number}.pdf"]
    assert client.post(f"/recurrences/{rec['id']}/approve-send").status_code == 409  # ya salio
    # el periodo siguiente sale solo
    assert run_due_recurrences(db_session, today=HOY) == 1
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    segunda = db_session.get(Invoice, r.last_invoice_id)
    assert segunda.id != primera.id and segunda.status == "enviada" and segunda.sent_at and r.last_send_status == "enviada"
    assert len(_al(correo, CLIENTE)) == 2


def test_resend_caido_queda_pendiente_y_avisa(client, auth, db_session, correo):
    from app.models import Invoice, Recurrence
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
    from app.models import Invoice, Recurrence
    from app.routers.ops import run_due_recurrences

    rec = _rec(client, _cliente(client), next_date=HOY - timedelta(days=31))
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    primera = db_session.get(Recurrence, rec["id"]).last_invoice_id
    assert client.post(f"/invoices/{primera}/email", json={}).json()["sent"] is True
    run_due_recurrences(db_session, today=HOY)
    db_session.expire_all()
    r = db_session.get(Recurrence, rec["id"])
    assert r.first_run_requires_approval is False and r.last_invoice_id != primera
    assert db_session.get(Invoice, r.last_invoice_id).status == "enviada"


def test_aprobar_exige_permiso_de_enviar(client, auth, db_session, correo):
    rec = _rec(client, _cliente(client))
    client.post(f"/recurrences/{rec['id']}/run")
    pw = _user_with_role(db_session, auth["tenant"]["id"], "conta.recurrente@ejemplo.com", "contabilidad")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "conta.recurrente@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"/recurrences/{rec['id']}/approve-send", headers=h).status_code == 403
    keys = [s["key"] for s in client.get("/approvals", headers=h).json()["sections"]]
    assert "mensualidades_por_aprobar" not in keys  # contabilidad todavia no envia (plan 1.7)


def test_vista_previa_al_admin_a_d_menos_3(client, auth, db_session, correo):
    from app.models import EmailOutbox
    from app.tasks.mensualidades import preview_upcoming

    cid = _cliente(client)
    _rec(client, cid, next_date=HOY + timedelta(days=3), name="Monitoreo sede central")
    _rec(client, cid, next_date=HOY + timedelta(days=5), name="Otra fecha")
    assert preview_upcoming(db_session, today=HOY) == 1
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
