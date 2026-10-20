"""Pago ONVO de una factura (plan 1.3): aviso a administracion y contabilidad, recibo al cliente con el saldo y
alerta roja (una sola vez) si la factura esta anulada, si el cobro viene en otra moneda o si hay sobrepago.
Nombres y correos ficticios (repo publico)."""

from sqlalchemy import select

from tests.test_cobranza_estados import _cliente, _factura
from tests.test_session4 import _user_with_role

CLIENTE = "pagos@cliente-prueba.ejemplo.com"
CONTA = "conta.cobro@ejemplo.com"


def _enlace(client, iid: int) -> str:
    return client.post(f"/invoices/{iid}/payment-link").json()["url"].rsplit("/", 1)[1]


def _salida(db_session, to: str | None = None) -> list:
    from app.models import EmailOutbox

    db_session.expire_all()
    q = select(EmailOutbox).order_by(EmailOutbox.id)
    if to:
        q = q.where(EmailOutbox.to == to)
    return db_session.scalars(q).all()


def test_pago_avisa_y_manda_recibo_con_saldo(client, auth, db_session, fake_onvo):
    tid = auth["tenant"]["id"]
    _user_with_role(db_session, tid, CONTA, "contabilidad")
    fake_onvo.connect(client)
    cid = _cliente(client)
    inv = _factura(client, cid)
    token = _enlace(client, inv["id"])
    client.post(f"/public/pay/{token}/onvo")
    fake_onvo.pay("cs_1", amount=50000 * 100)  # paga una parte
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 200
    recibo = _salida(db_session, CLIENTE)[-1]
    assert recibo.subject.startswith("Recibimos su pago") and "₡50,000.00" in recibo.html and "Saldo pendiente" in recibo.html and "₡63,000.00" in recibo.html
    for quien in ("admin@crimsonapp.com", CONTA):
        aviso = _salida(db_session, quien)[-1]
        assert aviso.subject.startswith("Pago en línea recibido") and inv["number"] in aviso.subject and "ALERTA" not in aviso.subject
    # el reintento del webhook no duplica ni el pago ni los avisos
    n = len(_salida(db_session))
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 200
    assert len(_salida(db_session)) == n


def test_pago_de_factura_anulada_alerta_roja_una_vez(client, auth, db_session, fake_onvo):
    tid = auth["tenant"]["id"]
    fake_onvo.connect(client)
    cid = _cliente(client)
    inv = _factura(client, cid)
    token = _enlace(client, inv["id"])
    client.post(f"/public/pay/{token}/onvo")
    assert client.post(f"/invoices/{inv['id']}/void").status_code == 200
    fake_onvo.pay("cs_1")
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"}).status_code == 200
    alertas = [m for m in _salida(db_session, "admin@crimsonapp.com") if m.subject.startswith("ALERTA")]
    assert len(alertas) == 1 and "devolver" in alertas[0].subject.lower() and inv["number"] in alertas[0].subject
    assert not _salida(db_session, CLIENTE) or not any(m.subject.startswith("Recibimos") for m in _salida(db_session, CLIENTE))
    # la consulta al volver no repite la alerta
    client.post(f"/public/pay/{token}/onvo/sync")
    assert len([m for m in _salida(db_session, "admin@crimsonapp.com") if m.subject.startswith("ALERTA")]) == 1
    assert client.get(f"/invoices/{inv['id']}").json()["payments"] == []


def test_otra_moneda_y_sobrepago_alertan(client, auth, db_session, fake_onvo):
    tid = auth["tenant"]["id"]
    fake_onvo.connect(client)
    cid = _cliente(client)
    a = _factura(client, cid)
    client.post(f"/public/pay/{_enlace(client, a['id'])}/onvo")
    fake_onvo.sessions["cs_1"]["currency"] = "USD"
    fake_onvo.pay("cs_1")
    fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_1"})
    otra_moneda = [m for m in _salida(db_session, "admin@crimsonapp.com") if m.subject.startswith("ALERTA") and "otra moneda" in m.subject]
    assert len(otra_moneda) == 1 and client.get(f"/invoices/{a['id']}").json()["payments"] == []

    b = _factura(client, cid)
    client.post(f"/public/pay/{_enlace(client, b['id'])}/onvo")
    fake_onvo.pay("cs_2", amount=200000 * 100)  # paga de mas
    fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": "cs_2"})
    sobre = [m for m in _salida(db_session, "admin@crimsonapp.com") if m.subject.startswith("ALERTA") and "Sobrepago" in m.subject]
    assert len(sobre) == 1 and "₡87,000.00" in sobre[0].html
    assert client.get(f"/invoices/{b['id']}").json()["status"] == "pagada"
    recibo = _salida(db_session, CLIENTE)[-1]
    assert "pagada en su totalidad" in recibo.html
