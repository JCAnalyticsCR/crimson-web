"""Reunion 21/10 · pedidos web: vendedor del pedido y correos del pedido.

- 24/42: el pedido web se asigna a un vendedor (el admin lo asigna; el vendedor "toma" uno sin vendedor). La factura
  que nace del pedido queda a su nombre (la ve en sus Facturas) y comisiona como una venta propia, salvo que Mi Tienda
  lo apague. Asignar despues de facturar pasa la factura y comisiona lo ya cobrado, sin pagarle la venta a dos.
- 43: avisos y reenvios van al correo ACTUAL de la ficha del cliente (si no hay ficha o no tiene correo, al del pedido).
- 45: al marcar "Enviado" se elige el transportista ("Mensajería Crimson" incluido) y el correo lo dice sin guia.
- 47: rechazar el comprobante manda un correo con el motivo y como volver a subirlo (dedupe_key por rechazo).
- 40/49: todos los correos del pedido con logo, estado, resumen y el boton "Rastrear mi pedido".
"""

from sqlalchemy import select

from app.models import Commission, EmailOutbox, Invoice, Order
from tests.test_rol_vendedor import _entrar, _uid
from tests.test_tienda_checkout import PNG, SLUG, _body, _shop

_n = iter(range(1, 1000))


def _checkout(client, prods, **kw):
    r = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], idempotency_key=f"ck-reu-{next(_n):04d}", **kw))
    assert r.status_code == 201, r.text
    return r.json()


def _oid(client, number) -> int:
    return next(o["id"] for o in client.get("/store-orders").json()["items"] if o["number"] == number)


def _mails(db, oid):
    return db.scalars(select(EmailOutbox).where(EmailOutbox.entity == "order", EmailOutbox.entity_id == oid).order_by(EmailOutbox.id)).all()


def _st(client, oid, status, h=None, **kw):
    return client.post(f"/store-orders/{oid}/status", json={"status": status, **kw}, headers=h or {})


def _assign(client, oid, seller_id, h=None):
    return client.post(f"/store-orders/{oid}/seller", json={"seller_id": seller_id}, headers=h or {})


def _vendedores(client, db_session, auth):
    tid = auth["tenant"]["id"]
    a = _entrar(client, db_session, tid, "vendedora.web@ejemplo.com", "ventas")
    b = _entrar(client, db_session, tid, "vendedor.web@ejemplo.com", "ventas")
    return a, _uid(client, a), b, _uid(client, b)


def _ck_cfg(client, **patch):
    ck = client.get("/store/checkout").json()["checkout"]
    ck.update(patch)
    r = client.put("/store/checkout", json=ck)
    assert r.status_code == 200, r.text
    return r.json()["checkout"]


def _vendor_commissions(db, inv_id):
    return db.scalars(select(Commission).where(Commission.invoice_id == inv_id, Commission.beneficiary == "vendedor", Commission.status != "anulada")).all()


# ---------- 24/42 ----------
def test_admin_asigna_y_el_vendedor_toma_un_pedido_sin_vendedor(client, auth, db_session):
    a, ua, b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    o1, o2 = _checkout(client, prods), _checkout(client, prods)
    id1, id2 = _oid(client, o1["number"]), _oid(client, o2["number"])

    lista_a = client.get("/store-orders", headers=a).json()
    assert lista_a["can_assign"] is False and lista_a["can_take"] is True and lista_a["sellers"] == [] and lista_a["me"] == ua
    # el vendedor toma uno sin vendedor; otro vendedor ya no lo puede tomar ni asignar
    r = _assign(client, id1, ua, a)
    assert r.status_code == 200, r.text
    assert r.json()["seller_id"] == ua and r.json()["seller_name"] and r.json()["assigned_at"]
    assert _assign(client, id1, ub, b).status_code == 409
    assert _assign(client, id2, ua, b).status_code == 403  # asignar a otro es del admin
    assert _assign(client, id1, None, a).status_code == 403  # soltarlo tambien
    assert _assign(client, id1, ua, a).status_code == 200  # repetir no cambia nada

    # el admin ve al equipo y asigna a cualquiera
    lista = client.get("/store-orders").json()
    assert lista["can_assign"] is True and {ua, ub} <= {s["id"] for s in lista["sellers"]}
    assert _assign(client, id2, ub).status_code == 200
    assert _assign(client, id2, 999999).status_code == 422  # no es del equipo

    # filtros por vendedor
    mios = client.get("/store-orders", params={"vendedor": "mios"}, headers=a).json()["items"]
    assert [x["id"] for x in mios] == [id1] and mios[0]["seller_name"]
    assert client.get("/store-orders", params={"vendedor": "sin_asignar"}).json()["items"] == []
    assert [x["id"] for x in client.get("/store-orders", params={"vendedor": str(ub)}).json()["items"]] == [id2]
    assert client.get("/store-orders", params={"vendedor": "x"}).status_code == 422

    # la asignacion queda en el historial interno y nunca en el seguimiento del comprador
    o = db_session.get(Order, id1)
    db_session.refresh(o)
    assert any(h.get("internal") and "Asignado a" in (h.get("note") or "") for h in o.history)
    pub = client.get(f"{SLUG}/orders/{o1['track_token']}").json()
    assert "Asignado" not in str(pub)


def test_factura_del_pedido_asignado_es_del_vendedor_y_comisiona(client, auth, db_session):
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _assign(client, oid, ua).status_code == 200
    assert _st(client, oid, "pagado", payment_ref="SINPE 1").status_code == 200
    r = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True})
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.seller_id == ua
    com = _vendor_commissions(db_session, inv.id)
    assert len(com) == 1 and com[0].user_id == ua
    # la vendedora ve la factura en sus Facturas y enlazada desde el pedido (item 24)
    assert client.get(f"/invoices/{inv.id}", headers=a).status_code == 200
    assert inv.id in {x["id"] for x in client.get("/invoices", headers=a).json()["items"]}
    assert client.get(f"/store-orders/{oid}", headers=a).json()["invoice_visible"] is True


def test_mi_tienda_apaga_la_comision_de_los_pedidos_web(client, auth, db_session):
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert _ck_cfg(client, web_orders_commission=False)["web_orders_commission"] is False
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _assign(client, oid, ua).status_code == 200
    assert _st(client, oid, "pagado").status_code == 200
    inv = db_session.get(Invoice, client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"])
    assert inv.seller_id == ua and _vendor_commissions(db_session, inv.id) == []  # suya, pero sin comision
    assert client.get(f"/invoices/{inv.id}", headers=a).status_code == 200


def test_asignar_despues_de_facturar_pasa_la_factura_y_comisiona_una_vez(client, auth, db_session):
    _a, ua, _b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _st(client, oid, "pagado").status_code == 200
    inv_id = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"]
    assert db_session.get(Invoice, inv_id).seller_id is None and _vendor_commissions(db_session, inv_id) == []
    assert _assign(client, oid, ua).status_code == 200
    db_session.expire_all()
    assert db_session.get(Invoice, inv_id).seller_id == ua
    assert [c.user_id for c in _vendor_commissions(db_session, inv_id)] == [ua]
    # ya comisiono a la vendedora: reasignar no le paga la misma venta a otro
    r = _assign(client, oid, ub)
    assert r.status_code == 409 and "Comisiones" in r.json()["detail"]
    assert _assign(client, oid, ua).status_code == 200  # el mismo vendedor: nada cambia
    assert len(_vendor_commissions(db_session, inv_id)) == 1


# ---------- 43 ----------
def test_avisos_y_reenvios_van_al_correo_actual_del_cliente(client, auth, db_session):
    prods = _shop(client)
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    det = client.get(f"/store-orders/{oid}").json()
    assert det["notify_email"] == "Ana@Correo.com" or det["notify_email"].lower() == "ana@correo.com"
    cust = client.get(f"/customers/{det['customer_id']}").json()
    assert client.put(f"/customers/{cust['id']}", json={"name": cust["name"], "email": "ana.nueva@correo.com"}).status_code == 200
    assert client.get(f"/store-orders/{oid}").json()["notify_email"] == "ana.nueva@correo.com"
    r = client.post(f"/store-orders/{oid}/resend")
    assert r.status_code == 200 and r.json()["to"] == "ana.nueva@correo.com"
    assert _st(client, oid, "pagado").json()["email_result"]["to"] == "ana.nueva@correo.com"
    # "preparando" no tiene correo propio: el reenvio manda el de pago confirmado (antes daba "sin correo")
    assert _st(client, oid, "preparando").status_code == 200
    r = client.post(f"/store-orders/{oid}/resend")
    assert r.status_code == 200 and r.json()["to"] == "ana.nueva@correo.com"
    # sin ficha (o ficha sin correo): el correo del pedido
    ord_ = db_session.get(Order, oid)
    ord_.customer_id = None
    db_session.commit()
    assert client.post(f"/store-orders/{oid}/resend").json()["to"].lower() == "ana@correo.com"


# ---------- 47 ----------
def test_rechazar_el_comprobante_avisa_con_motivo_y_como_volver_a_subirlo(client, auth, db_session):
    prods = _shop(client)
    o = _checkout(client, prods)
    oid, tok = _oid(client, o["number"]), o["track_token"]
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("a.png", PNG, "image/png")}).status_code == 201
    r = _st(client, oid, "nuevo", note="Ilegible: no se lee el monto")
    assert r.status_code == 200 and r.json()["email_result"]["status"] == "simulado"
    rech = [m for m in _mails(db_session, oid) if m.subject.startswith("Revisamos tu comprobante")]
    assert len(rech) == 1
    html = rech[0].html
    assert "Ilegible: no se lee el monto" in html and "Cómo volver a subirlo" in html and "Subir el comprobante" in html
    assert "Rastrear mi pedido" in html and "?pedido=" in html and "8888-7777" in html  # como pagar, otra vez
    assert rech[0].dedupe_key == f"tienda-rech:{oid}:2"
    # un segundo comprobante rechazado: otro correo (otra llave)
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("b.png", PNG + b"1", "image/png")}).status_code == 201
    assert _st(client, oid, "nuevo", note="Falta otro comprobante").status_code == 200
    rech = [m for m in _mails(db_session, oid) if m.subject.startswith("Revisamos tu comprobante")]
    assert [m.dedupe_key for m in rech] == [f"tienda-rech:{oid}:2", f"tienda-rech:{oid}:3"]
    assert "Falta otro comprobante" in rech[1].html


# ---------- 45, 40 y 49 ----------
def test_enviado_con_mensajeria_y_correos_presentables(client, auth, db_session):
    prods = _shop(client)
    client.put("/store", json={"logo_url": "https://cdn.ejemplo.com/logo-tienda.png"})
    lista = client.get("/store-orders").json()
    assert "Mensajería Crimson" in lista["carriers"]
    assert _ck_cfg(client, carriers=["Mensajería Crimson", " mensajería crimson ", "Moto Express"])["carriers"] == ["Mensajería Crimson", "Moto Express"]

    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    recibido = next(m for m in _mails(db_session, oid) if m.subject.startswith("Recibimos tu pedido"))
    for txt in ("Rastrear mi pedido", "?pedido=", "logo-tienda.png", o["number"], "Pendiente de pago", "Resumen", "Total", "Cómo pagar"):
        assert txt in recibido.html, txt
    equipo = next(m for m in _mails(db_session, oid) if m.subject.startswith("Pedido web"))
    assert f"/ordenes?pedido={oid}" in equipo.html and "logo-tienda.png" in equipo.html

    assert _st(client, oid, "pagado").status_code == 200
    r = _st(client, oid, "enviado", carrier="Mensajería Crimson", tracking_number="")
    assert r.status_code == 200 and r.json()["carrier"] == "Mensajería Crimson" and r.json()["tracking_number"] is None
    camino = next(m for m in _mails(db_session, oid) if m.subject.endswith("va en camino"))
    assert "Lo lleva: <b>Mensajería Crimson</b>" in camino.html and "Número de guía" not in camino.html
    assert "Rastrear mi pedido" in camino.html and "Enviado" in camino.html
    assert _st(client, oid, "entregado").status_code == 200
    entregado = next(m for m in _mails(db_session, oid) if m.subject.endswith("entregado"))
    assert "Rastrear mi pedido" in entregado.html and "Pedido entregado" in entregado.html


def test_listo_para_retiro_dice_donde_y_que_presentar(client, auth, db_session):
    prods = _shop(client)
    o = _checkout(client, prods, delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    oid = _oid(client, o["number"])
    assert _st(client, oid, "pagado").status_code == 200
    assert _st(client, oid, "listo").status_code == 200
    listo = next(m for m in _mails(db_session, oid) if m.subject.endswith("listo para retiro"))
    assert "Oficinas Crimson, Escazú" in listo.html and "Presentá el número de pedido" in listo.html and "Rastrear mi pedido" in listo.html


def test_la_factura_automatica_sale_en_las_facturas_de_quien_confirmo_el_pago(client, auth, db_session):
    """Item 24: en la prueba el vendedor confirmo pedidos web y su lista de Facturas salia vacia."""
    a, ua, b, _ub = _vendedores(client, db_session, auth)
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    prods = _shop(client, auto_invoice=True)
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    paid = _st(client, oid, "pagado", a)
    assert paid.status_code == 200, paid.text
    inv = db_session.get(Invoice, paid.json()["invoice_id"])
    assert inv.created_by == ua and inv.seller_id is None  # sin vendedor asignado: no comisiona
    assert inv.id in {x["id"] for x in client.get("/invoices", headers=a).json()["items"]}
    assert inv.id not in {x["id"] for x in client.get("/invoices", headers=b).json()["items"]}
    # asignado antes de pagar: la factura automatica es del vendedor asignado
    o2 = _checkout(client, prods)
    oid2 = _oid(client, o2["number"])
    assert _assign(client, oid2, ua, a).status_code == 200
    inv2 = db_session.get(Invoice, _st(client, oid2, "pagado").json()["invoice_id"])
    assert inv2.seller_id == ua
    assert inv2.id in {x["id"] for x in client.get("/invoices", headers=a).json()["items"]}
