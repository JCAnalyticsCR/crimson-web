"""Cobranza (plan 1.3): una sola definicion de estados de factura. Un borrador que el cliente nunca recibio no esta
"por cobrar", no vence, no lo ve el cliente; lo enviado (correo aceptado, enlace compartido, comprobante entregado) si.
Nombres y correos ficticios (repo publico)."""

import importlib.util
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

LINEA = [{"name": "Servicio de monitoreo", "quantity": 1, "unit_price": 100000, "tax_rate": 13, "cabys_code": "8121100000100"}]


def _cliente(client, email="pagos@cliente-prueba.ejemplo.com") -> int:
    r = client.post("/customers", json={"name": "Cliente Prueba Cobro S.A.", "id_type": "juridica", "id_number": "3-101-999991", "email": email})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _factura(client, cid: int, due: date | None = None) -> dict:
    body = {"customer_id": cid, "lines": LINEA, **({"due_date": due.isoformat()} if due else {})}
    r = client.post("/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _acepta_correo(monkeypatch, salida: list | None = None):
    from app.services import mail

    def deliver(m, files=None):
        m.status, m.provider_id = "enviado", f"re_{m.id}"
        if salida is not None:
            salida.append((m, files or []))

    monkeypatch.setattr(mail, "deliver", deliver)


def _pend(client, key: str) -> set[int]:
    return {r["id"] for r in client.get(f"/invoices?pendiente={key}&limit=100").json()["items"]}


def test_borrador_sin_enviar_no_esta_por_cobrar_ni_vence(client, auth, db_session):
    from app.models import Invoice
    from app.tasks.cobranza import mark_overdue

    cid = _cliente(client)
    hoy = date.today()
    inv = _factura(client, cid, due=hoy - timedelta(days=10))
    assert inv["status"] == "creado" and inv["sent_at"] is None  # antes nacia "vencida" con la fecha pasada
    assert inv["id"] not in _pend(client, "facturas_por_cobrar") and inv["id"] not in _pend(client, "facturas_vencidas")
    mark_overdue(db_session, today=hoy)
    assert db_session.get(Invoice, inv["id"]).status == "creado"
    dash = client.get("/dashboard").json()
    assert inv["id"] not in {x["id"] for x in dash["por_cobrar"]}


def test_correo_aceptado_la_deja_por_cobrar_y_vencible(client, auth, db_session, monkeypatch):
    from app.models import Invoice
    from app.tasks.cobranza import mark_overdue

    _acepta_correo(monkeypatch)
    cid = _cliente(client)
    hoy = date.today()
    inv = _factura(client, cid, due=hoy + timedelta(days=5))
    r = client.post(f"/invoices/{inv['id']}/email", json={})
    assert r.json()["sent"] is True
    f = client.get(f"/invoices/{inv['id']}").json()
    assert f["status"] == "enviada" and f["sent_at"]
    assert inv["id"] in _pend(client, "facturas_por_cobrar") and inv["id"] not in _pend(client, "facturas_vencidas")
    # el reenvio no mueve la fecha del primer envio
    primero = db_session.get(Invoice, inv["id"]).sent_at
    client.post(f"/invoices/{inv['id']}/email", json={})
    db_session.expire_all()
    assert db_session.get(Invoice, inv["id"]).sent_at == primero
    # vence: el worker la marca y aparece en "vencidas"
    assert mark_overdue(db_session, today=hoy + timedelta(days=6)) >= 1
    db_session.expire_all()
    assert db_session.get(Invoice, inv["id"]).status == "vencida"
    db_session.get(Invoice, inv["id"]).due_date = hoy - timedelta(days=3)  # holgura: la lista usa la fecha de Costa Rica
    db_session.commit()
    assert inv["id"] in _pend(client, "facturas_vencidas")


def test_correo_simulado_no_la_envia(client, auth, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    cid = _cliente(client)
    inv = _factura(client, cid)
    r = client.post(f"/invoices/{inv['id']}/email", json={}).json()
    assert r["status"] == "simulado" and r["sent"] is False
    f = client.get(f"/invoices/{inv['id']}").json()
    assert f["status"] == "creado" and f["sent_at"] is None


def test_enlace_de_pago_compartido_la_deja_por_cobrar(client, auth, db_session):
    from app.tasks.cobranza import mark_overdue

    cid = _cliente(client)
    hoy = date.today()
    inv = _factura(client, cid, due=hoy - timedelta(days=2))
    assert client.post(f"/invoices/{inv['id']}/payment-link").status_code == 200
    f = client.get(f"/invoices/{inv['id']}").json()
    assert f["status"] == "creado" and f["sent_at"]  # "creado" con sent_at: el cliente ya la tiene
    assert inv["id"] in _pend(client, "facturas_por_cobrar") and inv["id"] in _pend(client, "facturas_vencidas")
    mark_overdue(db_session, today=hoy)
    assert client.get(f"/invoices/{inv['id']}").json()["status"] == "vencida"
    # pagada: sale de por cobrar
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "kind": "captura", "amount": f["total"]})
    assert inv["id"] not in _pend(client, "facturas_por_cobrar")
    # anulada: no se le comparte un enlace
    otra = _factura(client, cid)
    client.post(f"/invoices/{otra['id']}/void")
    assert client.post(f"/invoices/{otra['id']}/payment-link").status_code == 409


def test_vencidas_con_la_fecha_de_costa_rica(client, auth, db_session, monkeypatch):
    """23:30 en Costa Rica ya es el dia siguiente en UTC: una factura que vence hoy (CR) todavia no esta vencida."""
    from app.models import Invoice
    from app.providers import einvoice as prov

    cid = _cliente(client)
    hoy_cr = date(2026, 11, 2)
    monkeypatch.setattr(prov, "cr_now", lambda: datetime(2026, 11, 2, 23, 30, tzinfo=ZoneInfo("America/Costa_Rica")))
    inv = _factura(client, cid, due=hoy_cr)
    client.post(f"/invoices/{inv['id']}/payment-link")
    assert inv["id"] in _pend(client, "facturas_por_cobrar") and inv["id"] not in _pend(client, "facturas_vencidas")
    monkeypatch.setattr(prov, "cr_now", lambda: datetime(2026, 11, 3, 0, 5, tzinfo=ZoneInfo("America/Costa_Rica")))
    assert inv["id"] in _pend(client, "facturas_vencidas")
    assert db_session.get(Invoice, inv["id"]).status == "creado"


def test_portal_del_cliente_solo_ve_lo_enviado(client, auth, db_session, monkeypatch):
    from app.services.invoice_states import is_visible

    from app.models import Invoice

    cid = _cliente(client)
    borrador = _factura(client, cid)
    compartida = _factura(client, cid)
    client.post(f"/invoices/{compartida['id']}/payment-link")
    db_session.expire_all()
    assert not is_visible(db_session.get(Invoice, borrador["id"]))
    assert is_visible(db_session.get(Invoice, compartida["id"]))


def test_entrega_fiscal_en_produccion_la_marca_enviada(client, auth, db_session, monkeypatch):
    from app.models import EInvoiceDocument, Invoice
    from app.services import einvoice_delivery as ed

    _acepta_correo(monkeypatch)
    cid = _cliente(client)
    inv = _factura(client, cid)
    doc = EInvoiceDocument(
        tenant_id=auth["tenant"]["id"], invoice_id=inv["id"], doc_type="FE", consecutive="00100001010000000001", clave="5" * 50,
        provider="hacienda", status="enviado", mode="produccion", xml_document="<FacturaElectronica/>",
    )
    db_session.add(doc)
    db_session.commit()
    assert ed.deliver(db_session, doc.id, "emitido") == 1
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Invoice, inv["id"]).sent_at is not None
    # en pruebas va a los administradores, no al cliente: no cuenta como enviada
    otra = _factura(client, cid)
    prueba = EInvoiceDocument(
        tenant_id=auth["tenant"]["id"], invoice_id=otra["id"], doc_type="FE", consecutive="00100001010000000002", clave="6" * 50,
        provider="hacienda", status="enviado", mode="pruebas", xml_document="<FacturaElectronica/>",
    )
    db_session.add(prueba)
    db_session.commit()
    ed.deliver(db_session, prueba.id, "emitido")
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Invoice, otra["id"]).sent_at is None


def test_relleno_de_la_migracion(client, auth, db_session):
    from app.models import Invoice, PaymentLink

    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("cobranza_*.py"))
    spec = importlib.util.spec_from_file_location("cobranza_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cid = _cliente(client)
    ids = {k: _factura(client, cid)["id"] for k in ("borrador", "enviada", "vencida", "con_enlace", "con_pago")}
    client.post(f"/invoices/{ids['con_enlace']}/payment-link")
    client.post(f"/invoices/{ids['con_pago']}/payments", json={"method": "efectivo", "kind": "captura", "amount": 1000})
    db_session.expire_all()
    db_session.get(Invoice, ids["enviada"]).status = "enviada"
    db_session.get(Invoice, ids["vencida"]).status = "vencida"
    for i in ids.values():
        db_session.get(Invoice, i).sent_at = None  # como justo despues del add_column
    db_session.commit()
    assert db_session.scalar(select(PaymentLink).where(PaymentLink.invoice_id == ids["con_enlace"]))
    mod.backfill(db_session.connection())
    mod.backfill(db_session.connection())  # idempotente
    db_session.commit()
    db_session.expire_all()
    enviadas = {k for k, i in ids.items() if db_session.get(Invoice, i).sent_at is not None}
    assert enviadas == {"enviada", "vencida", "con_enlace", "con_pago"}
    assert Decimal(str(db_session.get(Invoice, ids["con_pago"]).balance)) > 0
    assert datetime.now(UTC) - db_session.get(Invoice, ids["enviada"]).sent_at.replace(tzinfo=UTC) < timedelta(hours=1)
