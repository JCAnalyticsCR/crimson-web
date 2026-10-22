"""Pagina publica de pago de la factura (/pagar), pedido 21/10 (item 16): logo, metodos con los datos de Mi Tienda ->
Pagos, comprobante de SINPE o transferencia que entra a la misma revision que los pedidos web y registra el pago al
aprobarse; tarjeta solo con ONVO activo. Nombres, numeros y cuentas ficticios (repo publico)."""

from decimal import Decimal

from sqlalchemy import select

from tests.test_cobranza_estados import _cliente, _factura

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PNG2 = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


def _pagos(client, **kw):
    cfg = {
        "methods": {
            "sinpe": {"active": True, "phone": "8888-7777", "holder": "Empresa de Prueba SA"},
            "transferencia": {"active": True, "iban": "CR00000000000000000001", "bank": "Banco Ficticio", "holder": "Empresa de Prueba SA"},
            "tarjeta": {"active": False},
            "efectivo": {"active": kw.get("efectivo", False)},
        },
        "auto_invoice": False,
    }
    assert client.put("/store/checkout", json=cfg).status_code == 200


def _enlace(client, iid: int) -> str:
    return client.post(f"/invoices/{iid}/payment-link").json()["url"].rsplit("/", 1)[1]


def test_pagina_muestra_logo_y_datos_de_mi_tienda_sin_tarjeta_inerte(client, auth, db_session):
    from app.models import Tenant

    t = db_session.get(Tenant, auth["tenant"]["id"])
    t.logo_url = "https://cdn.ejemplo.com/logo.png"
    db_session.commit()
    _pagos(client)
    inv = _factura(client, _cliente(client))
    d = client.get(f"/public/pay/{_enlace(client, inv['id'])}").json()
    assert d["business"]["logo_url"] == "https://cdn.ejemplo.com/logo.png"
    codes = [m["code"] for m in d["methods"]["list"]]
    assert codes == ["sinpe", "transferencia"]  # sin ONVO no hay tarjeta; efectivo apagado; nada de PayPal
    sinpe = d["methods"]["list"][0]
    assert sinpe["needs_proof"] and "8888-7777" in sinpe["instructions"] and inv["number"] in sinpe["instructions"]
    assert ["Número", "8888-7777"] in [list(x) for x in sinpe["details"]]
    tr = d["methods"]["list"][1]
    assert "CR00000000000000000001" in tr["instructions"] and ["Banco", "Banco Ficticio"] in [list(x) for x in tr["details"]]
    assert d["methods"]["online"]["onvo"] is False and d["proofs"] == []


def test_tarjeta_solo_con_onvo_activo(client, auth, db_session, fake_onvo):
    _pagos(client)
    fake_onvo.connect(client)
    inv = _factura(client, _cliente(client))
    d = client.get(f"/public/pay/{_enlace(client, inv['id'])}").json()
    assert "tarjeta" in [m["code"] for m in d["methods"]["list"]] and d["methods"]["online"]["onvo"] is True


def test_comprobante_entra_a_revision_y_al_aprobarse_registra_el_pago(client, auth, db_session):
    from app.models import EmailOutbox, Invoice, Payment

    _pagos(client)
    inv = _factura(client, _cliente(client))
    tok = _enlace(client, inv["id"])
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc()).limit(1)) or 0
    # metodo que no lleva comprobante
    assert client.post(f"/public/pay/{tok}/proof", data={"method": "efectivo"}, files={"file": ("a.png", PNG, "image/png")}).status_code == 422
    r = client.post(f"/public/pay/{tok}/proof", data={"method": "sinpe", "reference": "SINPE-123", "amount": "50000"}, files={"file": ("sinpe.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    assert r.json()["proofs"][0]["status"] == "pendiente" and r.json()["proofs"][0]["status_label"] == "En revisión"
    # el mismo archivo dos veces no
    assert client.post(f"/public/pay/{tok}/proof", data={"method": "sinpe"}, files={"file": ("x.png", PNG, "image/png")}).status_code == 409
    # aviso al equipo, como el comprobante de un pedido web
    avisos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes)).all()
    assert any("Comprobante subido" in m.subject and inv["number"] in m.subject for m in avisos)

    # misma revision que los pedidos web: el contador sale en Pedidos de la tienda
    assert client.get("/store-orders").json()["comprobantes_facturas"] == 1
    lista = client.get("/payment-proofs").json()
    assert lista["pendientes"] == 1
    pr = lista["items"][0]
    assert pr["invoice_number"] == inv["number"] and pr["reference"] == "SINPE-123" and Decimal(str(pr["amount"])) == Decimal("50000")
    f = client.get(f"/payment-proofs/{pr['id']}/file")
    assert f.status_code == 200 and f.content == PNG and "no-store" in f.headers["cache-control"]

    ok = client.post(f"/payment-proofs/{pr['id']}/approve", json={})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "aprobado" and ok.json()["payment_id"]
    db_session.expire_all()
    pay = db_session.get(Payment, ok.json()["payment_id"])
    assert pay.method == "sinpe" and Decimal(str(pay.amount)) == Decimal("50000") and pay.external_ref == "SINPE-123"
    assert Decimal(str(db_session.get(Invoice, inv["id"]).balance)) == Decimal("63000")
    # no se aprueba dos veces
    assert client.post(f"/payment-proofs/{pr['id']}/approve", json={}).status_code == 409
    assert client.get(f"/public/pay/{tok}").json()["proofs"][0]["status"] == "aprobado"


def test_rechazo_deja_el_motivo_al_cliente_y_no_toca_la_factura(client, auth, db_session):
    from app.models import Invoice

    _pagos(client)
    inv = _factura(client, _cliente(client))
    tok = _enlace(client, inv["id"])
    client.post(f"/public/pay/{tok}/proof", data={"method": "transferencia"}, files={"file": ("t.png", PNG2, "image/png")})
    pid = client.get("/payment-proofs").json()["items"][0]["id"]
    assert client.post(f"/payment-proofs/{pid}/reject", json={"note": ""}).status_code == 422
    r = client.post(f"/payment-proofs/{pid}/reject", json={"note": "El comprobante no se lee"})
    assert r.status_code == 200 and r.json()["status"] == "rechazado"
    pub = client.get(f"/public/pay/{tok}").json()["proofs"][0]
    assert pub["status"] == "rechazado" and pub["note"] == "El comprobante no se lee"
    db_session.expire_all()
    assert Decimal(str(db_session.get(Invoice, inv["id"]).balance)) == Decimal(str(inv["total"]))


def test_aprobar_mas_que_el_saldo_no(client, auth, db_session):
    _pagos(client)
    inv = _factura(client, _cliente(client))
    tok = _enlace(client, inv["id"])
    client.post(f"/public/pay/{tok}/proof", data={"method": "sinpe"}, files={"file": ("s.png", PNG, "image/png")})
    pid = client.get("/payment-proofs").json()["items"][0]["id"]
    assert client.post(f"/payment-proofs/{pid}/approve", json={"amount": "999999999"}).status_code == 422
    ok = client.post(f"/payment-proofs/{pid}/approve", json={})  # sin monto: el saldo completo
    assert ok.status_code == 200
    assert Decimal(str(client.get(f"/public/pay/{tok}").json()["invoice"]["balance"])) == 0


def test_comprobante_aprobado_le_avisa_al_cliente(client, auth, db_session):
    """La pagina promete "te avisamos al aplicar el pago": al aprobar sale el recibo al correo del cliente."""
    from app.models import EmailOutbox

    _pagos(client)
    inv = _factura(client, _cliente(client))
    tok = _enlace(client, inv["id"])
    client.post(f"/public/pay/{tok}/proof", data={"method": "sinpe", "amount": "10000"}, files={"file": ("s.png", PNG, "image/png")})
    pid = client.get("/payment-proofs").json()["items"][0]["id"]
    assert client.post(f"/payment-proofs/{pid}/approve", json={}).status_code == 200
    recibos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like(f"Pago aplicado · {inv['number']}%"))).all()
    assert len(recibos) == 1 and recibos[0].to == "pagos@cliente-prueba.ejemplo.com" and "Saldo pendiente" in recibos[0].html


def test_el_mismo_comprobante_en_otra_factura_pide_confirmar(client, auth, db_session):
    """Un comprobante de un pago no paga dos facturas sin que el revisor lo vea y lo confirme."""
    _pagos(client)
    cid = _cliente(client)
    f1, f2, f3 = (_factura(client, cid) for _ in range(3))
    for f in (f1, f2):
        assert client.post(f"/public/pay/{_enlace(client, f['id'])}/proof", data={"method": "sinpe"}, files={"file": ("s.png", PNG, "image/png")}).status_code == 201
    rows = {r["invoice_id"]: r for r in client.get("/payment-proofs").json()["items"]}
    assert rows[f1["id"]]["reused_in"] == [f"la factura {f2['number']} (en revisión)"]
    r = client.post(f"/payment-proofs/{rows[f1['id']]['id']}/approve", json={})
    assert r.status_code == 409 and f2["number"] in r.json()["detail"]
    # el de la segunda era el repetido: se rechaza y la primera ya se aprueba sin aviso
    assert client.post(f"/payment-proofs/{rows[f2['id']]['id']}/reject", json={"note": "Comprobante repetido"}).status_code == 200
    assert client.post(f"/payment-proofs/{rows[f1['id']]['id']}/approve", json={}).status_code == 200
    # otra factura con el mismo archivo: hay que confirmarlo
    client.post(f"/public/pay/{_enlace(client, f3['id'])}/proof", data={"method": "sinpe"}, files={"file": ("s.png", PNG, "image/png")})
    p3 = next(r for r in client.get("/payment-proofs").json()["items"] if r["invoice_id"] == f3["id"])
    assert p3["reused_in"] == [f"la factura {f1['number']} (aprobado)"]
    assert client.post(f"/payment-proofs/{p3['id']}/approve", json={}).status_code == 409
    assert client.post(f"/payment-proofs/{p3['id']}/approve", json={"confirm_reused": True}).status_code == 200


def test_vendedor_solo_ve_y_aprueba_los_de_sus_facturas(client, auth, db_session):
    """Las facturas son de cada vendedor (services/ownership): sus comprobantes tambien, y el aviso tambien."""
    from app.models import EmailOutbox
    from tests.test_cobranza_estados import LINEA
    from tests.test_rol_vendedor import _vendedores

    _pagos(client)  # avisar a: admin y ventas (el defecto de Mi Tienda -> Pagos)
    a, b = _vendedores(client, db_session, auth)
    cid = _cliente(client)
    mia = client.post("/invoices", json={"customer_id": cid, "lines": LINEA}, headers=a)
    assert mia.status_code == 201, mia.text
    mia = mia.json()
    ajena = _factura(client, cid)  # del admin
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc()).limit(1)) or 0
    for f, png in ((mia, PNG), (ajena, PNG2)):
        assert client.post(f"/public/pay/{_enlace(client, f['id'])}/proof", data={"method": "sinpe"}, files={"file": ("s.png", png, "image/png")}).status_code == 201
    lista = client.get("/payment-proofs", params={"status": "todos"}, headers=a).json()
    assert [r["invoice_id"] for r in lista["items"]] == [mia["id"]] and lista["pendientes"] == 1
    assert client.get("/store-orders", headers=a).json()["comprobantes_facturas"] == 1
    assert client.get("/payment-proofs", headers=b).json()["items"] == []
    todos = {r["invoice_id"]: r["id"] for r in client.get("/payment-proofs").json()["items"]}
    assert set(todos) == {mia["id"], ajena["id"]}
    # lo ajeno no existe: ni el archivo, ni aprobarlo, ni rechazarlo
    assert client.get(f"/payment-proofs/{todos[ajena['id']]}/file", headers=a).status_code == 404
    assert client.post(f"/payment-proofs/{todos[ajena['id']]}/approve", json={}, headers=a).status_code == 404
    assert client.post(f"/payment-proofs/{todos[ajena['id']]}/reject", json={"note": "no es mio"}, headers=a).status_code == 404
    assert client.post(f"/payment-proofs/{todos[mia['id']]}/approve", json={}, headers=a).status_code == 200
    # el aviso: al admin de las dos; a la vendedora solo el de su factura; al otro vendedor ninguno; sin copia
    avisos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes, EmailOutbox.subject.like("Comprobante subido%"))).all()
    por = {(m.to, m.subject.rsplit(" ", 1)[1]) for m in avisos}
    assert ("vendedora.a@ejemplo.com", mia["number"]) in por and ("vendedora.a@ejemplo.com", ajena["number"]) not in por
    assert not [m for m in avisos if m.to == "vendedor.b@ejemplo.com"] and all(not m.bcc for m in avisos)
    assert {n for _, n in por} == {mia["number"], ajena["number"]}
