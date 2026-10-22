"""Detalle del pedido web: los datos del cliente los edita solo el administrador; el vendedor los ve sin poder editarlos
(el API tambien lo rechaza). El historial de correos del pedido no cambia al corregirlos."""

from sqlalchemy import select

from app.models import AuditLog, Order
from tests.test_pedidos_web_reunion import _checkout, _mails, _oid, _vendedores
from tests.test_tienda_checkout import _shop


def test_solo_el_admin_edita_los_datos_del_cliente_del_pedido(client, auth, db_session):
    a, ua, _b, _ub = _vendedores(client, db_session, auth)
    prods = _shop(client)
    o = _checkout(client, prods)
    oid = _oid(client, o["number"])
    assert client.post(f"/store-orders/{oid}/seller", json={"seller_id": ua}, headers=a).status_code == 200  # el pedido es suyo
    correos_antes = [(m.id, m.to, m.subject) for m in _mails(db_session, oid)]

    # el vendedor lo ve, pero sin el permiso de editar; el API lo rechaza
    det = client.get(f"/store-orders/{oid}", headers=a).json()
    assert det["can_edit_contact"] is False and det["contact"]["name"]
    r = client.put(f"/store-orders/{oid}/contact", json={"email": "otro@ejemplo.com"}, headers=a)
    assert r.status_code == 403
    db_session.expire_all()
    assert db_session.get(Order, oid).contact["email"] != "otro@ejemplo.com"

    # el admin corrige: lo que no manda no cambia; el correo queda en minuscula; la cedula vacia borra su tipo
    assert client.get(f"/store-orders/{oid}").json()["can_edit_contact"] is True
    r = client.put(f"/store-orders/{oid}/contact", json={"email": "Corregido@Ejemplo.com", "phone": "8888-1111", "id_number": ""})
    assert r.status_code == 200, r.text
    c = r.json()["contact"]
    assert c["email"] == "corregido@ejemplo.com" and c["phone"] == "8888-1111" and c["name"] == det["contact"]["name"]
    assert c["id_number"] is None and c["id_type"] is None
    assert any("Datos del cliente corregidos" in (h.get("note") or "") for h in r.json()["history"])
    log = db_session.scalars(select(AuditLog).where(AuditLog.entity == "order", AuditLog.entity_id == oid, AuditLog.action == "contact")).all()
    assert len(log) == 1 and log[0].diff["email"]["a"] == "corregido@ejemplo.com"

    # el historial de correos del pedido no se toca
    assert [(m.id, m.to, m.subject) for m in _mails(db_session, oid)] == correos_antes
    # validaciones: el nombre es obligatorio, el correo tiene que ser un correo, nada fuera del contrato
    assert client.put(f"/store-orders/{oid}/contact", json={"name": ""}).status_code == 422
    assert client.put(f"/store-orders/{oid}/contact", json={"email": "no-es-correo"}).status_code == 422
    assert client.put(f"/store-orders/{oid}/contact", json={"address": "x"}).status_code == 422


def test_la_cedula_corregida_pasa_por_la_misma_regla_del_checkout(client, auth, db_session):
    prods = _shop(client)
    oid = _oid(client, _checkout(client, prods)["number"])
    put = lambda body: client.put(f"/store-orders/{oid}/contact", json=body)  # noqa: E731
    # con guiones: quedan solo los digitos; sin tipo: fisica (lo que la factura del pedido usa como receptor)
    r = put({"id_number": "1-0234-0567", "id_type": None})
    assert r.status_code == 200, r.text
    assert r.json()["contact"]["id_number"] == "102340567" and r.json()["contact"]["id_type"] == "fisica"
    # largo incorrecto para el tipo: 422 y nada cambia
    assert put({"id_number": "12345"}).status_code == 422
    assert put({"id_type": "juridica"}).status_code == 422  # 9 digitos guardados no son una juridica
    db_session.expire_all()
    assert db_session.get(Order, oid).contact["id_number"] == "102340567"
    # juridica pide la razon social
    r = put({"id_type": "juridica", "id_number": "3-101-123456", "company": ""})
    assert r.status_code == 422 and "razón social" in r.json()["detail"]
    r = put({"id_type": "juridica", "id_number": "3-101-123456", "company": "Empresa de Ejemplo S.A."})
    assert r.status_code == 200 and r.json()["contact"]["id_number"] == "3101123456"
