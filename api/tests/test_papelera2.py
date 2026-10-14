"""Papelera2: el basurero de cada lista (Andres: "que en cada apartado se puedan borrar").

Por cada tipo nuevo: mandar a la papelera, que no salga en su lista, restaurar, el bloqueo por dependencias (con la
alternativa archivar/desactivar) y los permisos. Ademas el chequeo previo que usa el portal (GET .../check) y la
purga del worker en orden (primero lo que cuelga, al final lo maestro)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tests.test_portal_cliente import _crear_cliente_portal
from tests.test_session4 import _user_with_role


def _login_as(client, db_session, tid, email, role):
    admin_h = dict(client.headers)
    pw = _user_with_role(db_session, tid, email, role)
    client.headers.pop("Authorization", None)
    h = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]}
    client.headers.update(admin_h)
    return h


def _ids(rows):
    rows = rows["items"] if isinstance(rows, dict) else rows
    return {r["id"] for r in rows}


def test_check_previo_dice_si_se_puede_y_la_alternativa(client, auth, db_session):
    s = client.post("/surveys", json={"kind": "redes", "site": "Bodega"}).json()
    r = client.get(f"/archive/survey/{s['id']}/check")
    assert r.status_code == 200
    c = r.json()
    assert c["number"] == s["number"] and c["trash"] == [] and c["purge_days"] == 30 and c["alt"] is None

    # cliente con facturas: no va a la papelera; se ofrece desactivarlo
    cust = client.get("/customers").json()["items"][0]
    client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 13}]})
    c = client.get(f"/archive/customer/{cust['id']}/check").json()
    assert c["trash"] and "factura" in c["trash"][0]
    assert c["alt"]["action"] == "archive" and c["alt"]["label"] == "Desactivar"
    r = client.post(f"/archive/customer/{cust['id']}/trash")
    assert r.status_code == 409 and "factura" in r.json()["detail"]
    # lo fiscal y lo que se anula o excluye responde por que no
    for kind in ("invoice", "quote", "payment", "expense", "stock_movement", "fuel", "order", "audit", "credit_note"):
        assert client.get(f"/archive/{kind}/1/check").status_code == 409


def test_cliente_sin_documentos_papelera_restaurar_y_purga(client, auth, db_session):
    from app.models import AuditLog, Customer, CustomerContact
    from app.services.archive import purge_expired

    c = client.post("/customers", json={"name": "Duplicado SA", "id_type": "juridica"}).json()
    client.post(f"/customers/{c['id']}/contacts", json={"name": "Ana", "email": "ana@dup.cr"})
    r = client.post(f"/archive/customer/{c['id']}/trash")
    assert r.status_code == 200 and r.json()["number"] == "Duplicado SA"
    assert c["id"] not in _ids(client.get("/customers", params={"q": "Duplicado"}).json())
    row = next(x for x in client.get("/archive", params={"state": "papelera", "kind": "customer"}).json()["rows"] if x["id"] == c["id"])
    assert row["kind_label"] == "Cliente" and row["status"] == "inactivo" and row["days_left"] == 30

    assert client.post(f"/archive/customer/{c['id']}/restore").status_code == 200
    assert c["id"] in _ids(client.get("/customers", params={"q": "Duplicado"}).json())
    assert db_session.get(Customer, c["id"]).active is True

    # vuelve a la papelera y el worker lo borra a los N dias, con sus contactos
    client.post(f"/archive/customer/{c['id']}/trash")
    assert purge_expired(db_session, datetime.now(UTC) + timedelta(days=31)) >= 1
    db_session.expire_all()
    assert db_session.get(Customer, c["id"]) is None
    assert not db_session.scalars(select(CustomerContact).where(CustomerContact.customer_id == c["id"])).all()
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "purge", AuditLog.entity == "customer", AuditLog.entity_id == c["id"]))
    assert log and log.diff["number"] == "Duplicado SA" and log.diff["auto"] is True


def test_cliente_con_documentos_se_desactiva_en_vez_de_borrarse(client, auth, db_session):
    c = client.post("/customers", json={"name": "Con Opp SA", "id_type": "juridica"}).json()
    client.post("/opportunities", json={"title": "Algo", "customer_id": c["id"], "amount": 1000})
    r = client.post(f"/archive/customer/{c['id']}/trash")
    assert r.status_code == 409 and "oportunidad" in r.json()["detail"]
    # archivar = desactivar: sale de la lista y queda en Archivo para reactivarlo
    assert client.post(f"/archive/customer/{c['id']}/archive").status_code == 200
    assert c["id"] not in _ids(client.get("/customers", params={"q": "Con Opp"}).json())
    assert any(x["id"] == c["id"] for x in client.get("/archive", params={"state": "archivado", "kind": "customer"}).json()["rows"])
    assert client.post(f"/archive/customer/{c['id']}/restore").status_code == 200
    assert c["id"] in _ids(client.get("/customers", params={"q": "Con Opp"}).json())


def test_producto_sin_movimientos_si_con_movimientos_no(client, auth, db_session):
    from app.models import Product
    from app.services.archive import purge_expired

    p = client.post("/products", json={"name": "Cámara de prueba", "code": "PAP-01", "price": 1000}).json()
    assert client.post(f"/archive/product/{p['id']}/trash").status_code == 200
    assert p["id"] not in _ids(client.get("/products", params={"q": "PAP-01"}).json())
    assert db_session.get(Product, p["id"]).active is False
    assert client.post(f"/archive/product/{p['id']}/restore").status_code == 200
    assert p["id"] in _ids(client.get("/products", params={"q": "PAP-01"}).json())

    # con una linea de cotizacion ya no se borra (se desactiva)
    cust = client.get("/customers").json()["items"][0]
    client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"product_id": p["id"], "name": "Cámara", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]})
    r = client.post(f"/archive/product/{p['id']}/trash")
    assert r.status_code == 409 and "cotizaci" in r.json()["detail"]
    chk = client.get(f"/archive/product/{p['id']}/check").json()
    assert chk["alt"]["label"] == "Desactivar"

    # sin nada: purga real
    q = client.post("/products", json={"name": "Otro", "code": "PAP-02", "price": 10}).json()
    client.post(f"/archive/product/{q['id']}/trash")
    purge_expired(db_session, datetime.now(UTC) + timedelta(days=31))
    db_session.expire_all()
    assert db_session.get(Product, q["id"]) is None and db_session.get(Product, p["id"]) is not None


def test_proveedor_y_cupon(client, auth, db_session):
    s = client.post("/suppliers", json={"name": "Proveedor viejo"}).json()
    assert client.post(f"/archive/supplier/{s['id']}/trash").status_code == 200
    assert s["id"] not in _ids(client.get("/suppliers").json())
    assert client.post(f"/archive/supplier/{s['id']}/restore").status_code == 200
    assert s["id"] in _ids(client.get("/suppliers").json())
    # con un producto que lo usa: no
    client.post("/products", json={"name": "Con proveedor", "code": "PAP-03", "price": 10, "supplier_id": s["id"]})
    r = client.post(f"/archive/supplier/{s['id']}/trash")
    assert r.status_code == 409 and "producto" in r.json()["detail"]

    cp = client.post("/coupons", json={"code": "PRUEBA10", "value": 10}).json()
    assert client.post(f"/archive/coupon/{cp['id']}/trash").status_code == 200
    assert cp["id"] not in _ids(client.get("/coupons").json())
    assert client.post(f"/archive/coupon/{cp['id']}/restore").status_code == 200
    rows = client.get("/coupons").json()
    assert any(x["id"] == cp["id"] and x["active"] for x in rows)
    from app.models import Coupon

    db_session.get(Coupon, cp["id"]).uses = 2
    db_session.commit()
    r = client.post(f"/archive/coupon/{cp['id']}/trash")
    assert r.status_code == 409 and "2 pedidos" in r.json()["detail"]


def test_ticket_papelera_lista_portal_cliente_y_permisos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    cust = client.get("/customers").json()["items"][0]
    t = client.post("/tickets", json={"subject": "Spam del formulario", "customer_id": cust["id"]}).json()
    keep = client.post("/tickets", json={"subject": "Falla real", "customer_id": cust["id"]}).json()
    ha = _crear_cliente_portal(client, admin_h, cust["id"], "jefe@pap.cr", "cliente_admin", "Jefe")
    client.headers.update(admin_h)
    assert {t["id"], keep["id"]} <= _ids(client.get("/cliente/tickets", headers=ha).json())

    # un tecnico atiende sus tickets pero no los borra
    tec = _login_as(client, db_session, tid, "tec-pap@ejemplo.com", "tecnico")
    assert client.post(f"/archive/ticket/{t['id']}/trash", headers=tec).status_code == 403

    assert client.post(f"/archive/ticket/{t['id']}/trash").status_code == 200
    assert t["id"] not in _ids(client.get("/tickets").json()) and keep["id"] in _ids(client.get("/tickets").json())
    assert t["id"] not in _ids(client.get("/cliente/tickets", headers=ha).json())
    assert client.get(f"/cliente/tickets/{t['id']}", headers=ha).status_code == 404
    assert client.get(f"/tickets/{t['id']}").status_code == 200  # se abre desde Papelera
    assert client.post(f"/archive/ticket/{t['id']}/restore").status_code == 200
    assert t["id"] in _ids(client.get("/tickets").json())

    # cobrado con factura: no se borra
    from app.models import SupportTicket

    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "Soporte", "quantity": 1, "unit_price": 100, "tax_rate": 13}]}).json()
    db_session.get(SupportTicket, keep["id"]).invoice_id = inv["id"]
    db_session.commit()
    r = client.post(f"/archive/ticket/{keep['id']}/trash")
    assert r.status_code == 409 and "factura" in r.json()["detail"]


def test_contrato_y_activo_frenan_con_tickets_vivos(client, auth, db_session):
    from app.models import CustomerAsset, MaintenanceContract, SupportTicket
    from app.services.archive import purge_expired

    cust = client.get("/customers").json()["items"][0]
    k = client.post("/contracts", json={"customer_id": cust["id"], "name": "Mantenimiento CCTV"}).json()
    a = client.post("/assets", json={"customer_id": cust["id"], "name": "NVR 16 canales", "serial": "PAP-SN-1"}).json()
    t = client.post("/tickets", json={"subject": "Revisar NVR", "customer_id": cust["id"], "asset_id": a["id"]}).json()
    db_session.get(SupportTicket, t["id"]).contract_id = k["id"]
    db_session.commit()

    r = client.post(f"/archive/asset/{a['id']}/trash")
    assert r.status_code == 409 and "ticket" in r.json()["detail"]
    r = client.post(f"/archive/contract/{k['id']}/trash")
    assert r.status_code == 409 and "ticket" in r.json()["detail"]
    assert client.get(f"/archive/asset/{a['id']}/check").json()["alt"]["label"] == "Archivar"

    # con el ticket en la papelera ya se puede; al purgar, el ticket (si aun existiera) suelta el enlace
    client.post(f"/archive/ticket/{t['id']}/trash")
    assert client.post(f"/archive/asset/{a['id']}/trash").status_code == 200
    assert client.post(f"/archive/contract/{k['id']}/trash").status_code == 200
    assert a["id"] not in _ids(client.get("/assets").json())
    assert k["id"] not in _ids(client.get("/contracts").json())
    assert db_session.get(MaintenanceContract, k["id"]).active is False
    # el worker purga en orden: ticket, activo, contrato
    assert purge_expired(db_session, datetime.now(UTC) + timedelta(days=31)) >= 3
    db_session.expire_all()
    assert db_session.get(SupportTicket, t["id"]) is None
    assert db_session.get(CustomerAsset, a["id"]) is None and db_session.get(MaintenanceContract, k["id"]) is None


def test_contrato_restaurado_vuelve_activo(client, auth, db_session):
    cust = client.get("/customers").json()["items"][0]
    k = client.post("/contracts", json={"customer_id": cust["id"], "name": "Soporte anual", "kind": "soporte"}).json()
    client.post(f"/archive/contract/{k['id']}/trash")
    assert client.post(f"/archive/contract/{k['id']}/restore").status_code == 200
    row = next(x for x in client.get("/contracts").json() if x["id"] == k["id"])
    assert row["active"] is True


def test_solicitud_de_compra_recibida_no_y_permisos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    r1 = client.post("/purchase-requests", json={"lines": [{"name": "Cable UTP", "quantity": 2}]}).json()
    r2 = client.post("/purchase-requests", json={"lines": [{"name": "Conectores", "quantity": 10}]}).json()
    # el supervisor pide materiales (purchases.crear) y borra la duplicada
    sup = _login_as(client, db_session, tid, "sup-pap@ejemplo.com", "supervisor")
    assert client.post(f"/archive/purchase/{r1['id']}/trash", headers=sup).status_code == 200
    assert r1["id"] not in _ids(client.get("/purchase-requests").json())
    assert client.post(f"/archive/purchase/{r1['id']}/restore", headers=sup).status_code == 200
    # recibida: ya entro al inventario
    client.patch(f"/purchase-requests/{r2['id']}", json={"status": "recibida"})
    r = client.post(f"/archive/purchase/{r2['id']}/trash")
    assert r.status_code == 409 and "recibi" in r.json()["detail"]
    # un vendedor no maneja compras ni productos; si maneja clientes (crm.editar)
    v = _login_as(client, db_session, tid, "vend-pap@ejemplo.com", "ventas")
    assert client.post(f"/archive/purchase/{r1['id']}/trash", headers=v).status_code == 403
    p = client.post("/products", json={"name": "Ajeno", "code": "PAP-09", "price": 1}).json()
    assert client.post(f"/archive/product/{p['id']}/trash", headers=v).status_code == 403
    assert client.get(f"/archive/product/{p['id']}/check", headers=v).status_code == 403
    # y en el Archivo solo ve los tipos de sus modulos
    kinds = {x["kind"] for x in client.get("/archive", params={"state": "papelera"}, headers=v).json()["rows"]}
    assert "purchase" not in kinds
