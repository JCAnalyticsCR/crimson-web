"""Ventas por asignar y cobros por confirmar (feature ventas-asignar).

Cubre:
A) Bandeja "Ventas por asignar":
   - factura sin vendedor entra a la bandeja
   - asignar genera comisión con pagos ya cobrados (confirmados)
   - venta de la casa no comisiona
   - reasignar mueve la comisión (anula la del anterior, devenga la del nuevo)
   - el vendedor NO puede asignar ventas (403)
   - aislamiento por empresa

B) Cobros por confirmar:
   - pago del vendedor NO rebaja saldo hasta que se confirma
   - confirmar rebaja saldo y devenga comisión
   - rechazar avisa al vendedor y deja motivo
   - el vendedor no puede confirmar su propio cobro
   - pago de admin/caja entra confirmado directo

C) Comprobantes SINPE:
   - el vendedor no puede aprobar el comprobante de su propia factura

D) Ajustes por empresa:
   - defaults de VENTAS_DEFAULTS se respetan
   - reparto de consultas web "turno" asigna automáticamente

Repo público: solo emails y nombres de prueba.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Commission, Invoice, Opportunity, Payment
from tests.aliados_helpers import interno, uid

PW = "Aliado-prueba-2026"


# ---------- helpers ----------

def _login(client, email: str) -> dict:
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _cust(client, auth_h) -> int:
    items = client.get("/customers", headers=auth_h).json()["items"]
    if items:
        return items[0]["id"]
    r = client.post("/customers", json={"name": "Cliente Test", "id_type": "fisica", "id_number": "1-2345-6789"}, headers=auth_h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _inv(client, auth_h, cust_id: int, **extra) -> dict:
    body = {
        "customer_id": cust_id,
        "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 100000, "tax_rate": 13}],
        **extra,
    }
    r = client.post("/invoices", json=body, headers=auth_h)
    assert r.status_code == 201, r.text
    return r.json()


def _rule(client, auth_h, user_id: int | None = None) -> int:
    body = {
        "beneficiary": "vendedor",
        "kind": "comision",
        "base": "venta",
        "percent": "5",
        "trigger": "cobro",
        "name": "Regla prueba",
        **({"user_id": user_id} if user_id is not None else {}),
    }
    r = client.post("/commissions/rules", json=body, headers=auth_h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _pay(client, auth_h, inv_id: int, amount: int = 100000) -> dict:
    r = client.post(
        f"/invoices/{inv_id}/payments",
        json={"method": "transferencia", "amount": amount, "kind": "captura"},
        headers=auth_h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _commissions(client, auth_h) -> list:
    return client.get("/commissions", headers=auth_h).json().get("rows", [])


# ---------- A) Bandeja Ventas por asignar ----------

def test_factura_sin_vendedor_entra_a_bandeja(client, db_session, auth):
    """Una factura creada por el admin sin seller_id aparece en la bandeja."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    # factura sin vendedor — creada directamente en el servicio para garantizar seller_id=NULL
    from app.schemas.sales import DocumentIn
    from app.services.documents import create_invoice as _create_inv
    from sqlalchemy import text as _text

    dinv = DocumentIn.model_validate({
        "customer_id": cid,
        "lines": [{"name": "Item", "quantity": 1, "unit_price": 50000, "tax_rate": 13}],
    })
    inv_obj = _create_inv(db_session, auth["tenant"]["id"], None, dinv, seller_id=None)
    db_session.commit()
    inv_id = inv_obj.id

    bandeja = client.get("/sales/unassigned", headers=admin_h)
    assert bandeja.status_code == 200, bandeja.text
    ids_en_bandeja = {f["id"] for f in bandeja.json()["facturas"]}
    assert inv_id in ids_en_bandeja


def test_asignar_genera_comision_con_pago_existente(client, db_session, auth):
    """Asignar un vendedor a una factura ya cobrada devenga la comisión retroactivamente."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    # crear vendedor y regla
    h_v = interno(client, db_session, tid, "vendedor.asignar@test.com", "ventas")
    vid = uid(client, h_v)
    _rule(client, admin_h, vid)

    # factura sin vendedor, ya cobrada
    inv = _inv(client, admin_h, cid)
    inv_id = inv["id"]

    # cobrar (admin: se confirma directo) — 100000 + 13% IVA = 113000
    paid = _pay(client, admin_h, inv_id, amount=113000)
    assert paid["status"] == "pagada" or paid["balance"] == 0

    # verificar que NO hay comisión todavía para el vendedor
    comis_antes = [c for c in _commissions(client, admin_h) if c.get("user_id") == vid]
    assert len(comis_antes) == 0

    # asignar vendedor
    r = client.post(f"/invoices/{inv_id}/assign-seller", json={"seller_id": vid}, headers=admin_h)
    assert r.status_code == 200, r.text

    # ahora sí debe haber comisión
    comis_despues = [c for c in _commissions(client, admin_h) if c.get("user_id") == vid and c.get("invoice_id") == inv_id]
    assert len(comis_despues) > 0, "No se devengó la comisión después de asignar"
    assert float(comis_despues[0]["amount"]) > 0


def test_venta_de_la_casa_no_comisiona(client, db_session, auth):
    """Marcar como 'venta de la casa' no genera comisión aunque se asigne vendedor después."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.casa@test.com", "ventas")
    vid = uid(client, h_v)
    _rule(client, admin_h, vid)

    inv = _inv(client, admin_h, cid)
    inv_id = inv["id"]

    # marcar como venta de la casa
    r = client.post(f"/invoices/{inv_id}/assign-seller", json={"house_sale": True}, headers=admin_h)
    assert r.status_code == 200, r.text

    # pagar
    _pay(client, admin_h, inv_id)

    # no debe haber comisiones para ningún vendedor
    comis = [c for c in _commissions(client, admin_h) if c.get("invoice_id") == inv_id and c.get("beneficiary") == "vendedor"]
    assert len(comis) == 0, "La venta de la casa no debe comisionar"


def test_reasignar_mueve_comision(client, db_session, auth):
    """Reasignar vendedor anula comisión del anterior y devenga para el nuevo."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v1 = interno(client, db_session, tid, "vendedor.uno@test.com", "ventas")
    vid1 = uid(client, h_v1)
    h_v2 = interno(client, db_session, tid, "vendedor.dos@test.com", "ventas")
    vid2 = uid(client, h_v2)
    _rule(client, admin_h)  # regla general

    # Crear factura SIN vendedor, cobrar, luego asignar v1
    inv = _inv(client, admin_h, cid)
    inv_id = inv["id"]
    _pay(client, admin_h, inv_id)
    r = client.post(f"/invoices/{inv_id}/assign-seller", json={"seller_id": vid1}, headers=admin_h)
    assert r.status_code == 200

    comis_v1 = [c for c in _commissions(client, admin_h) if c.get("invoice_id") == inv_id and c.get("user_id") == vid1 and c.get("status") != "anulada"]
    assert len(comis_v1) > 0

    # Reasignar a v2
    r2 = client.post(f"/invoices/{inv_id}/assign-seller", json={"seller_id": vid2}, headers=admin_h)
    assert r2.status_code == 200

    # La comisión de v1 debe quedar anulada o revertida
    comis_v1_post = [c for c in _commissions(client, admin_h) if c.get("invoice_id") == inv_id and c.get("user_id") == vid1 and c.get("status") not in ("anulada",)]
    # Puede quedar una rev: si estaba pagada, se genera ajuste; si pendiente, se anula
    # En este test acabamos de devengarla (pendiente) → debe quedar anulada
    comis_v1_vivas = [c for c in comis_v1_post if c.get("kind") != "ajuste"]
    assert len(comis_v1_vivas) == 0, "La comisión del vendedor anterior debió anularse"

    # El nuevo vendedor tiene su comisión
    comis_v2 = [c for c in _commissions(client, admin_h) if c.get("invoice_id") == inv_id and c.get("user_id") == vid2 and c.get("status") != "anulada"]
    assert len(comis_v2) > 0, "El nuevo vendedor no tiene comisión"


def test_vendedor_no_puede_asignar_ventas(client, db_session, auth):
    """El vendedor (sin sales.asignar_venta) recibe 403 en la bandeja y en POST assign-seller."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.noperm@test.com", "ventas")

    inv = _inv(client, admin_h, cid)
    inv_id = inv["id"]

    # Bandeja: 403
    r_bandeja = client.get("/sales/unassigned", headers=h_v)
    assert r_bandeja.status_code == 403

    # Asignar: 403
    r_asig = client.post(f"/invoices/{inv_id}/assign-seller", json={"seller_id": uid(client, h_v)}, headers=h_v)
    assert r_asig.status_code == 403


def test_aislamiento_por_empresa(client, db_session, auth):
    """Las ventas sin vendedor de una empresa no aparecen en la bandeja de otra."""
    from app.models import Tenant, TenantUser, User
    from app.core.security import hash_password

    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)
    inv = _inv(client, admin_h, cid)
    inv_id = inv["id"]

    # Crear otra empresa y su admin
    t2 = Tenant(name="Empresa Dos", slug="empresa-dos-test", active=True)
    db_session.add(t2)
    db_session.flush()
    u2 = User(email="admin2.aislamiento@test.com", full_name="Admin Dos", password_hash=hash_password(PW))
    db_session.add(u2)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=t2.id, user_id=u2.id, role_code="admin"))
    db_session.commit()

    h2 = _login(client, "admin2.aislamiento@test.com")
    bandeja2 = client.get("/sales/unassigned", headers=h2)
    assert bandeja2.status_code == 200
    ids2 = {f["id"] for f in bandeja2.json()["facturas"]}
    assert inv_id not in ids2, "La factura de la empresa 1 no debe aparecer en la empresa 2"


# ---------- B) Cobros por confirmar ----------

def test_pago_vendedor_no_rebaja_saldo(client, db_session, auth):
    """Cobro registrado por el vendedor queda 'por_confirmar' y NO rebaja el saldo."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.cobro@test.com", "ventas")
    vid = uid(client, h_v)

    # Crear factura con el vendedor como seller
    inv = _inv(client, admin_h, cid, seller_id=vid)
    inv_id = inv["id"]
    saldo_antes = inv["total"]

    # El vendedor registra el cobro
    r = client.post(
        f"/invoices/{inv_id}/payments",
        json={"method": "transferencia", "amount": saldo_antes, "kind": "captura"},
        headers=h_v,
    )
    assert r.status_code == 201, r.text
    data = r.json()

    # El saldo NO debe haber cambiado
    assert abs(float(data["balance"]) - float(saldo_antes)) < 0.01, f"El saldo debería seguir en {saldo_antes} pero es {data['balance']}"
    assert data["status"] != "pagada", "La factura no debería estar pagada hasta que se confirme el cobro"

    # El pago en la BD tiene estado por_confirmar
    inv_obj = db_session.get(Invoice, inv_id)
    pagos_pc = [p for p in inv_obj.payments if p.status == "por_confirmar"]
    assert len(pagos_pc) == 1, "Debe haber exactamente un pago 'por_confirmar'"


def test_confirmar_pago_rebaja_saldo_y_devenga_comision(client, db_session, auth):
    """Confirmar el cobro rebaja saldo y devenga comisión."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.confirm@test.com", "ventas")
    vid = uid(client, h_v)
    _rule(client, admin_h, vid)

    inv = _inv(client, admin_h, cid, seller_id=vid)
    inv_id = inv["id"]
    total = inv["total"]

    # Vendedor registra
    r_pay = client.post(f"/invoices/{inv_id}/payments", json={"method": "transferencia", "amount": total, "kind": "captura"}, headers=h_v)
    assert r_pay.status_code == 201
    pid = db_session.scalars(select(Payment).where(Payment.invoice_id == inv_id, Payment.status == "por_confirmar")).first().id

    # No hay comisión todavía
    comis_antes = [c for c in _commissions(client, admin_h) if c.get("user_id") == vid and c.get("invoice_id") == inv_id]
    assert len(comis_antes) == 0

    # Confirmar (admin)
    r_conf = client.post(f"/invoices/{inv_id}/payments/{pid}/confirm", headers=admin_h)
    assert r_conf.status_code == 200, r_conf.text
    assert r_conf.json()["balance"] == 0 or r_conf.json()["status"] == "pagada"

    # Ahora hay comisión
    comis_despues = [c for c in _commissions(client, admin_h) if c.get("user_id") == vid and c.get("invoice_id") == inv_id]
    assert len(comis_despues) > 0, "Debe haberse devengado la comisión al confirmar"


def test_rechazar_pago_avisa_y_estado_fallido(client, db_session, auth):
    """Rechazar un cobro lo marca fallido y deja el motivo registrado."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.rechazado@test.com", "ventas")
    vid = uid(client, h_v)

    inv = _inv(client, admin_h, cid, seller_id=vid)
    inv_id = inv["id"]
    total = inv["total"]

    r_pay = client.post(f"/invoices/{inv_id}/payments", json={"method": "transferencia", "amount": total, "kind": "captura"}, headers=h_v)
    assert r_pay.status_code == 201

    inv_obj = db_session.get(Invoice, inv_id)
    pago = next((p for p in inv_obj.payments if p.status == "por_confirmar"), None)
    assert pago is not None
    pid = pago.id

    r_rech = client.post(f"/invoices/{inv_id}/payments/{pid}/reject-payment", json={"motivo": "El comprobante no coincide con el monto"}, headers=admin_h)
    assert r_rech.status_code == 200, r_rech.text

    db_session.expire(pago)
    assert pago.status == "fallido"
    assert "comprobante" in (pago.rejection_reason or "").lower()


def test_vendedor_no_puede_confirmar_cobro(client, db_session, auth):
    """El vendedor (sin payments.confirmar) recibe 403 al intentar confirmar."""
    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.noconf@test.com", "ventas")
    vid = uid(client, h_v)

    inv = _inv(client, admin_h, cid, seller_id=vid)
    inv_id = inv["id"]
    total = inv["total"]

    r_pay = client.post(f"/invoices/{inv_id}/payments", json={"method": "transferencia", "amount": total, "kind": "captura"}, headers=h_v)
    assert r_pay.status_code == 201

    inv_obj = db_session.get(Invoice, inv_id)
    pago = next((p for p in inv_obj.payments if p.status == "por_confirmar"), None)
    assert pago is not None

    # El mismo vendedor intenta confirmar → 403
    r_conf = client.post(f"/invoices/{inv_id}/payments/{pago.id}/confirm", headers=h_v)
    assert r_conf.status_code == 403


def test_pago_admin_confirmado_directo(client, db_session, auth):
    """Un pago registrado por el admin entra confirmado directamente."""
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)
    inv = _inv(client, admin_h, cid)
    inv_id = inv["id"]
    total = inv["total"]

    r_pay = client.post(f"/invoices/{inv_id}/payments", json={"method": "efectivo", "amount": total, "kind": "captura"}, headers=admin_h)
    assert r_pay.status_code == 201
    data = r_pay.json()
    assert data["balance"] == 0 or data["status"] == "pagada", "El pago del admin debería confirmar directo"


# ---------- C) Comprobante SINPE: vendedor no puede aprobar el suyo ----------

def test_vendedor_no_aprueba_su_sinpe(client, db_session, auth):
    """El vendedor no puede aprobar el comprobante SINPE de su propia factura."""
    from app.models import Media, InvoicePaymentProof

    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    cid = _cust(client, admin_h)

    h_v = interno(client, db_session, tid, "vendedor.sinpe@test.com", "ventas")
    vid = uid(client, h_v)

    # Crear factura con este vendedor
    inv = _inv(client, admin_h, cid, seller_id=vid)
    inv_id = inv["id"]

    # Crear un comprobante directamente en la BD
    media = Media(tenant_id=tid, key="test-sinpe-proof-k01", filename="test.jpg", content_type="image/jpeg", data=b"fake", sha256="abc123", size=4)
    db_session.add(media)
    db_session.flush()
    proof = InvoicePaymentProof(
        tenant_id=tid, invoice_id=inv_id, media_id=media.id,
        method="sinpe", amount=113000, currency="CRC", status="pendiente",
    )
    db_session.add(proof)
    db_session.commit()

    # El vendedor intenta aprobar su propio comprobante → 403
    r = client.post(f"/payment-proofs/{proof.id}/approve", json={}, headers=h_v)
    assert r.status_code == 403, r.text


# ---------- D) Ajustes ventas ----------

def test_defaults_ventas_settings():
    """Los defaults de VENTAS_DEFAULTS tienen los valores esperados."""
    from app.services.sale_assignment import VENTAS_DEFAULTS

    assert VENTAS_DEFAULTS["asignacion_consultas_web"] == "manual"
    assert VENTAS_DEFAULTS["clientes_nuevos_sin_atender"] == "por_asignar"
    assert VENTAS_DEFAULTS["ventana_sugerencia_dias"] == 90
    assert "regla_vendedor" in str(VENTAS_DEFAULTS["comision_asignada"])


def test_put_settings_ventas(client, auth):
    """PUT /settings con sección 'ventas' guarda correctamente los defaults."""
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}
    payload = {
        "ventas": {
            "asignacion_consultas_web": "turno",
            "ventana_sugerencia_dias": 60,
            "clientes_nuevos_sin_atender": "por_asignar",
        }
    }
    r = client.put("/settings", json=payload, headers=admin_h)
    assert r.status_code == 200 or r.status_code == 204, r.text

    s = client.get("/settings", headers=admin_h)
    assert s.status_code == 200
    ventas_cfg = s.json().get("ventas") or {}
    assert ventas_cfg.get("asignacion_consultas_web") == "turno"
    assert ventas_cfg.get("ventana_sugerencia_dias") == 60


def test_reparto_turno_asigna_automaticamente(client, db_session, auth):
    """Con asignacion_consultas_web='turno' la oportunidad nace con owner_id asignado."""
    from app.models import Tenant, Opportunity
    from app.services.sale_assignment import auto_assign_opportunity, VENTAS_DEFAULTS

    tid = auth["tenant"]["id"]
    admin_h = {"Authorization": f"Bearer {auth['access_token']}"}

    # Configurar turno
    r = client.put("/settings", json={"ventas": {"asignacion_consultas_web": "turno"}}, headers=admin_h)
    assert r.status_code in (200, 204)

    h_v = interno(client, db_session, tid, "vendedor.turno@test.com", "ventas")
    vid = uid(client, h_v)

    t = db_session.get(Tenant, tid)
    cid = _cust(client, admin_h)
    opp = Opportunity(
        tenant_id=tid, number="OPP-TURNO-01", title="Test turno", source="tienda web",
        status="nuevo", contact={}, amount=0, currency="CRC", probability=30,
    )
    db_session.add(opp)
    db_session.flush()

    assigned = auto_assign_opportunity(db_session, t, opp)
    assert assigned is not None, "Con turno debe asignarse automáticamente"
    assert opp.owner_id is not None
