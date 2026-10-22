"""Reunion 21/10 · pedidos web: vendedor del pedido y correos del pedido.

- 24/42: el pedido web se asigna a un encargado (el admin lo asigna; el vendedor "toma" uno sin encargado). La factura
  que nace del pedido queda a su nombre (la ve en sus Facturas).
- Reunion 2, item 12: atender el pedido NO comisiona. Solo comisiona el "Vendedor que atendio" (el vendedor del
  cliente), que pone el admin o elige el cliente en el checkout si Mi Tienda lo habilita; la factura pasa a su nombre.
  Ponerlo despues de facturar comisiona lo ya cobrado, sin pagarle la venta a dos.
- 43: avisos y reenvios van al correo ACTUAL de la ficha del cliente (si no hay ficha o no tiene correo, al del pedido).
- 45: al marcar "Enviado" se elige el transportista ("Mensajería Crimson" incluido) y el correo lo dice sin guia.
- 47: rechazar el comprobante manda un correo con el motivo y como volver a subirlo (dedupe_key por rechazo).
- 40/49: todos los correos del pedido con logo, estado, resumen y el boton "Rastrear mi pedido".
"""

from sqlalchemy import select

from app.models import Commission, EmailOutbox, Invoice, Order
from tests.conftest import ADMIN
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


def _attend(client, oid, seller_id, h=None):
    return client.post(f"/store-orders/{oid}/attended-by", json={"seller_id": seller_id}, headers=h or {})


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
    assert client.get("/store-orders", params={"vendedor": "\u00b2"}).status_code == 422  # isdigit() lo aceptaba: 500

    # sin sales.ver_todo: los sin vendedor y los suyos; el pedido de otro vendedor no aparece en ningun lado
    assert {x["id"] for x in client.get("/store-orders", headers=a).json()["items"]} == {id1}
    assert {x["id"] for x in client.get("/store-orders", headers=b).json()["items"]} == {id2}
    assert client.get("/store-orders", params={"vendedor": str(ub)}, headers=a).status_code == 403
    assert client.get(f"/store-orders/{id2}", headers=a).status_code == 404
    assert client.post(f"/store-orders/{id2}/resend", headers=a).status_code == 404
    assert id2 not in {x["id"] for x in client.get("/orders", headers=a).json()}
    assert sum(client.get("/store-orders", headers=a).json()["counts"].values()) == 1

    # la asignacion queda en el historial interno y nunca en el seguimiento del comprador
    o = db_session.get(Order, id1)
    db_session.refresh(o)
    assert any(h.get("internal") and "Asignado a" in (h.get("note") or "") for h in o.history)
    pub = client.get(f"{SLUG}/orders/{o1['track_token']}").json()
    assert "Asignado" not in str(pub)


def test_atender_el_pedido_no_comisiona_y_el_vendedor_que_atendio_si(client, auth, db_session):
    """Reunion 2, item 12: el vendedor que toma el pedido lo atiende (la factura es suya) pero no comisiona; comisiona
    solo cuando el admin lo pone como "Vendedor que atendio" (el cliente es suyo)."""
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _assign(client, oid, ua, a).status_code == 200  # lo toma
    assert _st(client, oid, "pagado", payment_ref="SINPE 1").status_code == 200
    r = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True})
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.seller_id == ua and _vendor_commissions(db_session, inv.id) == []  # suya, pero sin comision
    # la vendedora ve la factura en sus Facturas y enlazada desde el pedido (item 24)
    assert client.get(f"/invoices/{inv.id}", headers=a).status_code == 200
    assert inv.id in {x["id"] for x in client.get("/invoices", headers=a).json()["items"]}
    assert client.get(f"/store-orders/{oid}", headers=a).json()["invoice_visible"] is True
    # el vendedor no se pone a si mismo como el que atendio: es del admin
    assert _attend(client, oid, ua, a).status_code == 403
    r = _attend(client, oid, ua)
    assert r.status_code == 200, r.text
    assert r.json()["attended_by_id"] == ua and r.json()["attended_by_name"] and r.json()["attended_source"] == "admin"
    db_session.expire_all()
    assert [c.user_id for c in _vendor_commissions(db_session, inv.id)] == [ua]
    assert _attend(client, oid, ua).status_code == 200  # repetir no duplica
    assert len(_vendor_commissions(db_session, inv.id)) == 1


def test_comisiona_el_vendedor_que_atendio_aunque_otro_atienda_el_pedido(client, auth, db_session):
    a, ua, b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _assign(client, oid, ua, a).status_code == 200  # A lo atiende
    assert _attend(client, oid, ub).status_code == 200  # el cliente es de B
    assert _attend(client, oid, 999999).status_code == 422  # no es del equipo
    # B ve el pedido de su cliente (vendedor que atendio) y A el que atiende
    assert oid in {x["id"] for x in client.get("/store-orders", headers=b).json()["items"]}
    assert oid in {x["id"] for x in client.get("/store-orders", params={"vendedor": "mios"}, headers=b).json()["items"]}
    assert client.get(f"/store-orders/{oid}", headers=a).status_code == 200
    assert _st(client, oid, "pagado").status_code == 200
    inv = db_session.get(Invoice, client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"])
    assert inv.seller_id == ub and [c.user_id for c in _vendor_commissions(db_session, inv.id)] == [ub]
    # cambiar el encargado no mueve la factura ni la comision
    assert _assign(client, oid, None).status_code == 200
    db_session.expire_all()
    assert db_session.get(Invoice, inv.id).seller_id == ub and len(_vendor_commissions(db_session, inv.id)) == 1
    # quitar al que atendio con la comision viva: primero se anula en Comisiones
    r = _attend(client, oid, None)
    assert r.status_code == 409 and "Comisiones" in r.json()["detail"]


def test_la_opcion_vieja_de_mi_tienda_ya_no_existe(client, auth, db_session):
    """La opcion de antes ("atender comisiona"): una pestana vieja que la manda puede guardar, y no vuelve."""
    ck = _ck_cfg(client, web_orders_commission=True)
    assert "web_orders_commission" not in ck and ck["seller_choice"] is False


def test_el_que_atendio_despues_de_facturar_recibe_la_factura_y_comisiona_una_vez(client, auth, db_session):
    _a, ua, _b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _st(client, oid, "pagado").status_code == 200
    inv_id = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"]
    assert db_session.get(Invoice, inv_id).seller_id is None and _vendor_commissions(db_session, inv_id) == []
    assert _attend(client, oid, ua).status_code == 200
    db_session.expire_all()
    assert db_session.get(Invoice, inv_id).seller_id == ua
    assert [c.user_id for c in _vendor_commissions(db_session, inv_id)] == [ua]
    # ya comisiono a la vendedora: cambiar al que atendio no le paga la misma venta a otro
    r = _attend(client, oid, ub)
    assert r.status_code == 409 and "Comisiones" in r.json()["detail"]
    assert _attend(client, oid, ua).status_code == 200  # el mismo vendedor: nada cambia
    assert len(_vendor_commissions(db_session, inv_id)) == 1


def test_el_vendedor_no_toma_ventas_facturadas_ni_cerradas(client, auth, db_session):
    """Tomar es para atender: un pedido entregado o ya facturado se lo asigna el admin (comision retroactiva)."""
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert next(x for x in client.get("/store-orders", headers=a).json()["items"] if x["id"] == oid)["takeable"] is True
    assert _st(client, oid, "pagado").status_code == 200
    inv_id = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"]
    assert _st(client, oid, "enviado", carrier="Mensajería Crimson").status_code == 200
    assert _st(client, oid, "entregado").status_code == 200
    fila = next(x for x in client.get("/store-orders", headers=a).json()["items"] if x["id"] == oid)
    assert fila["takeable"] is False and client.get(f"/store-orders/{oid}", headers=a).json()["takeable"] is False
    r = _assign(client, oid, ua, a)
    assert r.status_code == 409 and "administrador" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Invoice, inv_id).seller_id is None and _vendor_commissions(db_session, inv_id) == []
    # pagado y facturado, aunque siga por atender: tampoco
    o2 = _checkout(client, prods)
    oid2 = _oid(client, o2["number"])
    assert _st(client, oid2, "pagado").status_code == 200
    assert client.post(f"/orders/{oid2}/invoice", params={"doc_type": "FE", "register_payment": True}).status_code == 201
    assert _assign(client, oid2, ua, a).status_code == 409
    # el admin si lo asigna como encargado (sin comision) y como vendedor que atendio (comisiona lo cobrado)
    assert _assign(client, oid, ua).status_code == 200
    db_session.expire_all()
    assert db_session.get(Invoice, inv_id).seller_id == ua and _vendor_commissions(db_session, inv_id) == []
    assert _attend(client, oid, ua).status_code == 200
    db_session.expire_all()
    assert [c.user_id for c in _vendor_commissions(db_session, inv_id)] == [ua]


def test_reasignar_tras_anular_la_comision_le_comisiona_al_nuevo(client, auth, db_session):
    _a, ua, _b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _attend(client, oid, ua).status_code == 200
    assert _st(client, oid, "pagado").status_code == 200
    inv_id = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"]
    (com_a,) = _vendor_commissions(db_session, inv_id)
    assert com_a.user_id == ua
    assert _attend(client, oid, ub).status_code == 409
    r = client.patch("/commissions", json={"ids": [com_a.id], "status": "anulada", "reason": "Pedido de otro vendedor"})
    assert r.status_code == 200, r.text
    assert _attend(client, oid, ub).status_code == 200
    db_session.expire_all()
    assert [c.user_id for c in _vendor_commissions(db_session, inv_id)] == [ub]


def test_reasignar_a_b_y_de_vuelta_a_a_le_vuelve_a_comisionar(client, auth, db_session):
    _a, ua, _b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    oid = _oid(client, _checkout(client, prods)["number"])
    assert _attend(client, oid, ua).status_code == 200
    assert _st(client, oid, "pagado").status_code == 200
    inv_id = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"]
    for actual, siguiente in ((ua, ub), (ub, ua)):
        (com,) = _vendor_commissions(db_session, inv_id)
        assert com.user_id == actual
        assert client.patch("/commissions", json={"ids": [com.id], "status": "anulada", "reason": "Reasignado"}).status_code == 200
        assert _attend(client, oid, siguiente).status_code == 200
        db_session.expire_all()
    vivas = _vendor_commissions(db_session, inv_id)
    assert [c.user_id for c in vivas] == [ua] and vivas[0].source_key.endswith(":re1")
    assert _attend(client, oid, ua).status_code == 200  # repetir no duplica
    db_session.expire_all()
    assert len(_vendor_commissions(db_session, inv_id)) == 1


def test_aviso_del_pedido_asignado_no_le_llega_a_otro_vendedor(client, auth, db_session):
    a, ua, _b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    nuevo = {m.to for m in _mails(db_session, oid) if m.subject.startswith("Pedido web")}
    assert {"vendedora.web@ejemplo.com", "vendedor.web@ejemplo.com"} <= nuevo  # sin vendedor: la cola de todos
    assert _assign(client, oid, ua, a).status_code == 200
    assert client.post(f"{SLUG}/orders/{o['track_token']}/proof", files={"file": ("a.png", PNG, "image/png")}).status_code == 201
    aviso = {m.to for m in _mails(db_session, oid) if m.subject.startswith("Comprobante subido")}
    assert "vendedora.web@ejemplo.com" in aviso and "vendedor.web@ejemplo.com" not in aviso
    assert ADMIN[0].lower() in aviso  # el admin (sales.ver_todo) si


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
    # "Mi pedido" abre con el correo de la compra y con el actual de la ficha (al que le llegan los avisos)
    for correo in ("ana@correo.com", "ANA.NUEVA@correo.com"):
        r = client.post(f"{SLUG}/orders/lookup", json={"number": o["number"], "email": correo})
        assert r.status_code == 200, (correo, r.text)
    assert client.post(f"{SLUG}/orders/lookup", json={"number": o["number"], "email": "otra@correo.com"}).status_code == 404
    r = client.post(f"/store-orders/{oid}/resend")
    assert r.status_code == 200 and r.json()["to"] == "ana.nueva@correo.com"
    assert _st(client, oid, "pagado").json()["email_result"]["to"] == "ana.nueva@correo.com"
    # "preparando" no avisa al pasar; el reenvio manda el de pago confirmado (antes daba "sin correo")
    antes = len(_mails(db_session, oid))
    assert _st(client, oid, "preparando").status_code == 200
    assert len(_mails(db_session, oid)) == antes
    r = client.post(f"/store-orders/{oid}/resend")
    assert r.status_code == 200 and r.json()["to"] == "ana.nueva@correo.com"
    assert _mails(db_session, oid)[-1].subject.startswith("Pago confirmado")
    # sin ficha (o ficha sin correo): el correo del pedido
    ord_ = db_session.get(Order, oid)
    ord_.customer_id = None
    db_session.commit()
    assert client.post(f"/store-orders/{oid}/resend").json()["to"].lower() == "ana@correo.com"


def test_ligado_solo_por_cedula_los_avisos_siguen_al_correo_del_pedido(client, auth, db_session):
    """La cedula es publica: escribir la de una ficha con otro correo no desvia los avisos (con el enlace del pedido)."""
    prods = _shop(client)
    r = client.post("/customers", json={"name": "Titular de la ficha", "id_type": "fisica", "id_number": "1-2222-0333", "email": "titular@ejemplo.com"})
    assert r.status_code in (200, 201), r.text
    contacto = {"name": "Comprador", "email": "comprador@ejemplo.com", "phone": "8888-1111", "id_type": "fisica", "id_number": "1-2222-0333"}
    o = _checkout(client, prods, contact=contacto)
    oid = _oid(client, o["number"])
    det = client.get(f"/store-orders/{oid}").json()
    assert det["customer_id"] == r.json()["id"]  # la venta si queda en la ficha
    assert det["notify_email"] == "comprador@ejemplo.com"
    assert _st(client, oid, "pagado").json()["email_result"]["to"] == "comprador@ejemplo.com"
    assert client.post(f"/store-orders/{oid}/resend").json()["to"] == "comprador@ejemplo.com"
    al_cliente = {m.to for m in _mails(db_session, oid) if not m.subject.startswith(("Pedido web", "Comprobante"))}
    assert "titular@ejemplo.com" not in al_cliente and "comprador@ejemplo.com" in al_cliente
    assert client.post(f"{SLUG}/orders/lookup", json={"number": o["number"], "email": "titular@ejemplo.com"}).status_code == 404


def test_ficha_por_cedula_sin_correo_o_corregida_despues_los_avisos_la_siguen(client, auth, db_session):
    """Item 43: el correo que el equipo le pone (o le corrige) a la ficha despues de la compra manda en los avisos."""
    prods = _shop(client)
    r = client.post("/customers", json={"name": "Ficha sin correo", "id_type": "fisica", "id_number": "1-3333-0444"})
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]
    contacto = {"name": "Comprador", "email": "compra.sin@ejemplo.com", "phone": "8888-1111", "id_type": "fisica", "id_number": "1-3333-0444"}
    oid = _oid(client, _checkout(client, prods, contact=contacto)["number"])
    det = client.get(f"/store-orders/{oid}").json()
    assert det["customer_id"] == cid and det["notify_email"] == "compra.sin@ejemplo.com"
    assert client.put(f"/customers/{cid}", json={"name": "Ficha sin correo", "email": "agregado@ejemplo.com"}).status_code == 200
    assert client.get(f"/store-orders/{oid}").json()["notify_email"] == "agregado@ejemplo.com"
    assert client.post(f"/store-orders/{oid}/resend").json()["to"] == "agregado@ejemplo.com"

    # ficha con OTRO correo: sigue al del pedido hasta que el equipo cambie el de la ficha
    r = client.post("/customers", json={"name": "Titular", "id_type": "fisica", "id_number": "1-3333-0555", "email": "titular.dos@ejemplo.com"})
    cid2 = r.json()["id"]
    contacto = {**contacto, "email": "compra.otra@ejemplo.com", "id_number": "1-3333-0555"}
    oid2 = _oid(client, _checkout(client, prods, contact=contacto)["number"])
    assert client.get(f"/store-orders/{oid2}").json()["notify_email"] == "compra.otra@ejemplo.com"
    assert client.put(f"/customers/{cid2}", json={"name": "Titular", "email": "corregido@ejemplo.com"}).status_code == 200
    assert client.get(f"/store-orders/{oid2}").json()["notify_email"] == "corregido@ejemplo.com"


def test_pedido_sin_marca_de_vinculo_no_se_desvia_a_la_ficha_de_otra_cedula(client, auth, db_session):
    """Pedidos de antes de customer_link (NULL): la ficha solo si su correo es el de la compra."""
    prods = _shop(client)
    r = client.post("/customers", json={"name": "Titular", "id_type": "fisica", "id_number": "1-4444-0666", "email": "titular.tres@ejemplo.com"})
    contacto = {"name": "Comprador", "email": "compra.vieja@ejemplo.com", "phone": "8888-1111", "id_type": "fisica", "id_number": "1-4444-0666"}
    o = _checkout(client, prods, contact=contacto)
    oid = _oid(client, o["number"])
    ord_ = db_session.get(Order, oid)
    ord_.customer_link, ord_.customer_link_email = None, None
    db_session.commit()
    assert client.get(f"/store-orders/{oid}").json()["customer_id"] == r.json()["id"]
    assert client.get(f"/store-orders/{oid}").json()["notify_email"] == "compra.vieja@ejemplo.com"
    assert client.post(f"{SLUG}/orders/lookup", json={"number": o["number"], "email": "titular.tres@ejemplo.com"}).status_code == 404
    # el de la compra en la ficha: la sigue
    o2 = _checkout(client, prods)
    oid2 = _oid(client, o2["number"])
    ord2 = db_session.get(Order, oid2)
    ord2.customer_link = None
    db_session.commit()
    assert client.get(f"/store-orders/{oid2}").json()["notify_email"].lower() == "ana@correo.com"


def test_reenviar_en_efectivo_sin_pagar_no_dice_pago_confirmado(client, auth, db_session):
    from app.services.store_orders import _steps

    prods = _shop(client)
    o = _checkout(client, prods, payment_method="efectivo", delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    oid = _oid(client, o["number"])
    assert _st(client, oid, "preparando").status_code == 200
    r = client.post(f"/store-orders/{oid}/resend")
    assert r.status_code == 200, r.text
    m = _mails(db_session, oid)[-1]
    assert m.subject.startswith("Estamos preparando") and "Confirmamos el pago" not in m.html
    # la linea de avance no marca el pago como hecho
    ord_ = db_session.get(Order, oid)
    db_session.refresh(ord_)
    pasos = _steps(ord_)
    assert pasos[1] == ("Pago al retirar", False) and pasos[2] == ("Preparando", True)


def test_el_seguimiento_publico_nunca_muestra_entradas_internas(client, auth, db_session):
    from app.models import Tenant
    from app.services.store_orders import order_public

    prods = _shop(client)
    o = _checkout(client, prods)
    ord_ = db_session.get(Order, _oid(client, o["number"]))
    db_session.refresh(ord_)
    ord_.history = [*ord_.history, {"status": "nuevo", "at": "2026-10-21T12:00:00+00:00", "by": 1, "note": "Asignado a X", "internal": True}]
    db_session.commit()
    pub = order_public(db_session, db_session.get(Tenant, ord_.tenant_id), ord_)
    assert len(pub["history"]) == 1 and "Asignado" not in str(pub)


# ---------- 47 ----------
def test_rechazar_el_comprobante_avisa_con_motivo_y_como_volver_a_subirlo(client, auth, db_session):
    prods = _shop(client)
    o = _checkout(client, prods)
    oid, tok = _oid(client, o["number"]), o["track_token"]
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("a.png", PNG, "image/png")}).status_code == 201
    r = _st(client, oid, "nuevo", note="Ilegible: no se lee el monto")
    assert r.status_code == 200 and r.json()["email_result"]["status"] == "simulado"
    rech = [m for m in _mails(db_session, oid) if m.subject.startswith("Revisamos su comprobante")]
    assert len(rech) == 1
    html = rech[0].html
    assert "Ilegible: no se lee el monto" in html and "Cómo volver a subirlo" in html and "Subir el comprobante" in html
    assert "Rastrear mi pedido" in html and "?pedido=" in html and "8888-7777" in html  # como pagar, otra vez
    assert rech[0].dedupe_key == f"tienda-rech:{oid}:2"
    # "Reenviar correo" tras el rechazo reenvia el rechazo con el motivo (no "¡Gracias por su compra!"), sin llave
    r = client.post(f"/store-orders/{oid}/resend")
    assert r.status_code == 200
    ultimo = _mails(db_session, oid)[-1]
    assert ultimo.subject.startswith("Revisamos su comprobante") and "Ilegible: no se lee el monto" in ultimo.html
    assert ultimo.dedupe_key is None and "¡Gracias por su compra!" not in ultimo.html
    # un segundo comprobante rechazado: otro correo (otra llave)
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("b.png", PNG + b"1", "image/png")}).status_code == 201
    assert _st(client, oid, "nuevo", note="Falta otro comprobante").status_code == 200
    rech = [m for m in _mails(db_session, oid) if m.subject.startswith("Revisamos su comprobante")]
    assert [m.dedupe_key for m in rech] == [f"tienda-rech:{oid}:2", None, f"tienda-rech:{oid}:3"]
    assert "Falta otro comprobante" in rech[2].html


# ---------- 45, 40 y 49 ----------
def test_enviado_con_mensajeria_y_correos_presentables(client, auth, db_session):
    prods = _shop(client)
    client.put("/store", json={"logo_url": "https://cdn.ejemplo.com/logo-tienda.png"})
    lista = client.get("/store-orders").json()
    assert "Mensajería Crimson" in lista["carriers"]
    assert _ck_cfg(client, carriers=["Mensajería Crimson", " mensajería crimson ", "Moto Express"])["carriers"] == ["Mensajería Crimson", "Moto Express"]

    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    # el detalle trae la lista de Mi Tienda (el pedido abierto desde el enlace del aviso no depende del listado)
    assert client.get(f"/store-orders/{oid}").json()["carriers"] == ["Mensajería Crimson", "Moto Express"]
    recibido = next(m for m in _mails(db_session, oid) if m.subject.startswith("Recibimos su pedido"))
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
    # lista vacia en Mi Tienda: se respeta (el transportista se escribe a mano)
    assert _ck_cfg(client, carriers=[])["carriers"] == []
    assert client.get(f"/store-orders/{oid}").json()["carriers"] == [] and client.get("/store-orders").json()["carriers"] == []


def test_listo_para_retiro_dice_donde_y_que_presentar(client, auth, db_session):
    prods = _shop(client)
    o = _checkout(client, prods, delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    oid = _oid(client, o["number"])
    assert _st(client, oid, "pagado").status_code == 200
    assert _st(client, oid, "listo").status_code == 200
    listo = next(m for m in _mails(db_session, oid) if m.subject.endswith("listo para retiro"))
    assert "Oficinas Crimson, Escazú" in listo.html and "Presente el número de pedido" in listo.html and "Rastrear mi pedido" in listo.html


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


def test_la_factura_automatica_comisiona_solo_al_vendedor_que_atendio(client, auth, db_session):
    """El camino normal en produccion (auto_invoice): el cobro aplicado a la factura automatica de un pedido que el
    vendedor solo atiende no le comisiona; el del pedido de su cliente (vendedor que atendio) si."""
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    prods = _shop(client, auto_invoice=True)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert _assign(client, oid, ua, a).status_code == 200
    inv = db_session.get(Invoice, _st(client, oid, "pagado").json()["invoice_id"])
    assert inv.seller_id == ua and _vendor_commissions(db_session, inv.id) == []

    o2 = _checkout(client, prods)
    oid2 = _oid(client, o2["number"])
    assert _attend(client, oid2, ua).status_code == 200
    inv2 = db_session.get(Invoice, _st(client, oid2, "pagado").json()["invoice_id"])
    assert inv2.seller_id == ua and [c.user_id for c in _vendor_commissions(db_session, inv2.id)] == [ua]


def test_el_cliente_elige_en_el_checkout_al_vendedor_que_lo_atendio(client, auth, db_session):
    """Mi Tienda -> "el cliente elige al vendedor que lo atendio" (apagado por defecto): lista del equipo comercial
    (solo nombres y una referencia opaca), opcional. La eleccion del cliente es una SUGERENCIA: no comisiona ni se lleva
    la factura hasta que el admin la confirma en el pedido."""
    from app.services.store_orders import _seller_ref

    tid = auth["tenant"]["id"]
    a, ua, _b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    # apagado: el checkout no muestra la lista y lo que mande se ignora (lo asigna el admin)
    assert client.get(f"{SLUG}/checkout-config").json()["sellers"] == []
    o = _checkout(client, prods, attended_by=_seller_ref(tid, ua))
    assert db_session.get(Order, _oid(client, o["number"])).attended_by_id is None

    assert _ck_cfg(client, seller_choice=True)["seller_choice"] is True
    vend = client.get(f"{SLUG}/checkout-config").json()["sellers"]
    assert all(set(v) == {"id", "name"} for v in vend)
    refs = {v["id"] for v in vend}
    # el id interno nunca sale: referencias opacas; el mismo equipo que el admin ve en el pedido (admin incluido)
    assert {_seller_ref(tid, ua), _seller_ref(tid, ub), _seller_ref(tid, _uid(client, {}))} <= refs
    assert not {str(ua), str(ub)} & refs
    assert len(vend) == len(client.get("/store-orders").json()["sellers"])
    r = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], idempotency_key=f"ck-reu-{next(_n):04d}", attended_by="abc123"))
    assert r.status_code == 422 and "vendedor" in r.json()["detail"]
    o = _checkout(client, prods, attended_by=_seller_ref(tid, ua))
    oid = _oid(client, o["number"])
    det = client.get(f"/store-orders/{oid}").json()
    assert det["attended_by_id"] == ua and det["attended_source"] == "cliente" and det["seller_id"] is None
    assert "atendió" not in str(client.get(f"{SLUG}/orders/{o['track_token']}").json())  # nota interna
    # sin elegir: opcional
    o3 = _checkout(client, prods)
    assert db_session.get(Order, _oid(client, o3["number"])).attended_by_id is None
    # la elegida ve el pedido de su cliente, pero sin confirmar no comisiona ni es suya la factura
    assert oid in {x["id"] for x in client.get("/store-orders", headers=a).json()["items"]}
    assert _st(client, oid, "pagado").status_code == 200
    inv_id = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE", "register_payment": True}).json()["invoice_id"]
    assert db_session.get(Invoice, inv_id).seller_id is None and _vendor_commissions(db_session, inv_id) == []
    # el admin confirma la sugerencia (la misma vendedora): ahora si comisiona, una vez
    r = _attend(client, oid, ua)
    assert r.status_code == 200 and r.json()["attended_source"] == "admin"
    db_session.expire_all()
    assert db_session.get(Invoice, inv_id).seller_id == ua and [c.user_id for c in _vendor_commissions(db_session, inv_id)] == [ua]
    assert _attend(client, oid, ua).status_code == 200
    assert len(_vendor_commissions(db_session, inv_id)) == 1


def test_el_vendedor_que_atendio_es_del_equipo_comercial(client, auth, db_session):
    """El admin pone como vendedor que atendio solo a alguien del equipo comercial (la misma lista del checkout): ni un
    tecnico ni alguien de otra empresa."""
    _vendedores(client, db_session, auth)
    tec = _entrar(client, db_session, auth["tenant"]["id"], "tecnico.web@ejemplo.com", "tecnico")
    prods = _shop(client)
    oid = _oid(client, _checkout(client, prods)["number"])
    r = _attend(client, oid, _uid(client, tec))
    assert r.status_code == 422 and "vendedor" in r.json()["detail"]
    assert _uid(client, tec) not in {x["id"] for x in client.get("/store-orders").json()["sellers"]}


def test_el_vendedor_que_atendio_ve_pero_no_mueve_el_pedido_de_otro(client, auth, db_session):
    """El vendedor del cliente ve el pedido que atiende otra persona, pero no lo mueve (estado, notas, correos,
    factura): eso es del encargado o del admin."""
    a, ua, b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    oid = _oid(client, _checkout(client, prods)["number"])
    assert _assign(client, oid, ua, a).status_code == 200
    assert _attend(client, oid, ub).status_code == 200
    det = client.get(f"/store-orders/{oid}", headers=b)
    assert det.status_code == 200 and det.json()["can_edit"] is False
    assert client.get(f"/store-orders/{oid}", headers=a).json()["can_edit"] is True
    assert _st(client, oid, "cancelado", b).status_code == 403
    assert client.post(f"/store-orders/{oid}/note", json={"note": "Hola"}, headers=b).status_code == 403
    assert client.post(f"/store-orders/{oid}/resend", headers=b).status_code == 403
    assert client.patch(f"/orders/{oid}", json={"status": "cancelado"}, headers=b).status_code == 403
    assert client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE"}, headers=b).status_code == 403
    db_session.expire_all()
    assert db_session.get(Order, oid).status != "cancelado"
    # el encargado si lo mueve
    assert _st(client, oid, "pagado", a, payment_ref="SINPE 9").status_code == 200


def test_mis_comisiones_no_proyecta_el_pedido_web_que_solo_atiende(client, auth, db_session):
    """Inicio / Mis comisiones (por cobrar): la factura abierta de un pedido web que el vendedor solo atiende no le
    proyecta comision; cuando el admin lo pone como vendedor que atendio, si."""
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    assert client.post("/commissions/rules", json={"name": "Vendedores 10%", "percent": 10}).status_code == 201
    oid = _oid(client, _checkout(client, prods)["number"])
    assert _assign(client, oid, ua, a).status_code == 200
    r = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE"})
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.seller_id == ua and inv.balance > 0
    assert float(client.get("/dashboard", headers=a).json()["comisiones"]["por_cobrar"]) == 0
    assert _attend(client, oid, ua).status_code == 200
    assert float(client.get("/dashboard", headers=a).json()["comisiones"]["por_cobrar"]) > 0


def test_inicio_del_vendedor_cuenta_solo_sus_pedidos_y_la_cola_aparte(client, auth, db_session):
    """Reunion 2, item 10: en el Inicio del vendedor, "Pedidos web por atender" son solo los suyos (los que atiende y
    los de sus clientes); la cola sin encargado va en su propia tarjeta. Cada numero es el de la lista a la que lleva."""
    a, ua, b, ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    ef = {"payment_method": "efectivo", "delivery": {"type": "retiro"}, "shipping_method": "Retiro en tienda"}  # por atender desde ya
    o_a = _oid(client, _checkout(client, prods, **ef)["number"])
    o_b = _oid(client, _checkout(client, prods[1:], **ef)["number"])  # otro producto: las existencias de la prueba son pocas
    o_cola = _oid(client, _checkout(client, prods, **ef)["number"])
    o_cliente = _oid(client, _checkout(client, prods[1:], **ef)["number"])
    assert _assign(client, o_a, ua, a).status_code == 200
    assert _assign(client, o_b, ub, b).status_code == 200
    assert _assign(client, o_cliente, ub).status_code == 200 and _attend(client, o_cliente, ua).status_code == 200
    pend = client.get("/dashboard", headers=a).json()["acciones_pendientes"]
    mios = client.get("/store-orders", params={"pendiente": "por_atender", "vendedor": "mios"}, headers=a).json()["items"]
    cola = client.get("/store-orders", params={"pendiente": "por_atender", "vendedor": "sin_asignar"}, headers=a).json()["items"]
    assert {x["id"] for x in mios} == {o_a, o_cliente}
    assert o_cola in {x["id"] for x in cola} and o_b not in {x["id"] for x in cola}
    proofs = client.get("/store-orders", headers=a).json()["comprobantes_facturas"] or 0
    assert pend["pedidos_web"] == len(mios) + proofs
    assert pend["pedidos_sin_encargado"] == len(cola)
    # el inicio de la empresa (admin): todos en una sola tarjeta
    adm = client.get("/dashboard").json()["acciones_pendientes"]
    assert "pedidos_sin_encargado" not in adm
    assert adm["pedidos_web"] >= 4
