"""Cola fiscal "Por emitir" (plan maestro 1.7): mientras el modo fiscal es "ninguno", lo pagado o enviado espera a que
contabilidad lo emita en Fygaro y lo marque "emitida fuera". Lo marcado sale de la cola y la plataforma nunca lo vuelve a
emitir (tampoco lo importado del historico). Correo diario a contabilidad, una vez por dia de Costa Rica."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.hacienda_utils import role_headers

SLUG = "/public/store/crimson"


@pytest.fixture
def correos(monkeypatch):
    """El proveedor de correo acepta todo (como Resend con llave): lo enviado queda "enviado"."""
    from app.services import mail

    enviados = []

    def deliver(m, files=None):
        enviados.append({"to": m.to, "subject": m.subject, "html": m.html})
        m.status = "enviado"

    monkeypatch.setattr(mail, "deliver", deliver)
    return enviados


def _factura(client, headers=None):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    r = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def _pagar(client, inv, amount=None, headers=None):
    r = client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "amount": str(amount or inv["total"])}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def _cola(client, headers=None):
    r = client.get("/fiscal/queue", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(client, headers=None):
    return [x["id"] for x in _cola(client, headers)["items"]]


def _hoy():
    """Hoy en Costa Rica, no el de la maquina: en un runner en UTC, de 18:00 a 24:00 CR date.today() ya es manana y la
    marca responderia 422 "posterior a hoy" (regla 8 de la seccion 2)."""
    from app.services.fiscal_queue import cr_today

    return cr_today()


def _marcar(client, inv_id, number="00100001010000004321", when=None, headers=None):
    return client.post(f"/fiscal/queue/{inv_id}/external", json={"number": number, "date": (when or _hoy()).isoformat()}, headers=headers or {})


def _doc(db_session, tenant_id, inv_id, status, mode, einvoice_status, provider="hacienda"):
    """Un comprobante ya registrado (sin red): simulado, de pruebas o de produccion."""
    from app.models import EInvoiceDocument, Invoice

    db_session.add(
        EInvoiceDocument(
            tenant_id=tenant_id, invoice_id=inv_id, doc_type="FE", consecutive="00100001010000000999", provider=provider, status=status, mode=mode, attempts=1
        )
    )
    db_session.get(Invoice, inv_id).einvoice_status = einvoice_status
    db_session.commit()


def test_que_entra_a_por_emitir(client, auth, db_session, correos):
    from app.models import PaymentLink

    borrador = _factura(client)
    pagada = _pagar(client, _factura(client))
    parcial = _pagar(client, _factura(client), amount=Decimal("1000"))
    enviada = _factura(client)
    assert client.post(f"/invoices/{enviada['id']}/email", json={"to": "cliente@ejemplo.com"}).json()["sent"]
    anulada = _pagar(client, _factura(client))
    assert client.post(f"/invoices/{anulada['id']}/void").status_code == 200
    vencida_sin_enviar, vencida_con_enlace = _factura(client), _factura(client)
    client.post(f"/invoices/{vencida_con_enlace['id']}/payment-link")
    # compartida por WhatsApp con el enlace de pago y todavia "creado": ya salio de la empresa (igual que al vencer)
    creada_con_enlace = _factura(client)
    assert client.post(f"/invoices/{creada_con_enlace['id']}/payment-link").status_code == 200
    # "emitida" solo en SIMULADO (la tienda y el POS emiten solos en ese modo) o en PRUEBAS: sin validez fiscal, falta
    # emitirla de verdad. Con un comprobante de produccion no entra.
    tid = auth["tenant"]["id"]
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    simulada = _pagar(client, _factura(client))
    assert client.post(f"/invoices/{simulada['id']}/emit").json()["status"] == "aceptada"
    simulada_borrador = _factura(client)  # emitida en simulado pero ni enviada ni cobrada
    assert client.post(f"/invoices/{simulada_borrador['id']}/emit").json()["status"] == "aceptada"
    client.put("/settings", json={"einvoice_provider": "none"})
    rechazada_en_pruebas = _pagar(client, _factura(client))
    _doc(db_session, tid, rechazada_en_pruebas["id"], "rechazada", "pruebas", "rechazada")
    de_produccion = _pagar(client, _factura(client))
    _doc(db_session, tid, de_produccion["id"], "aceptada", "produccion", "aceptada")
    from app.models import Invoice

    for x in (vencida_sin_enviar, vencida_con_enlace):
        db_session.get(Invoice, x["id"]).status = "vencida"  # lo que hace sales.mark_overdue
    db_session.commit()
    assert db_session.query(PaymentLink).filter(PaymentLink.invoice_id == vencida_sin_enviar["id"]).count() == 0

    cola = _cola(client)
    ids = [x["id"] for x in cola["items"]]
    # un borrador sin enviar ni cobrar no se emite; una anulada tampoco; con comprobante de produccion ya esta emitida
    esperadas = {pagada["id"], parcial["id"], enviada["id"], vencida_con_enlace["id"], creada_con_enlace["id"], simulada["id"], rechazada_en_pruebas["id"]}
    assert set(ids) == esperadas
    for fuera in (borrador, anulada, vencida_sin_enviar, simulada_borrador, de_produccion):
        assert fuera["id"] not in ids
    assert cola["total"] == len(esperadas) and cola["mode"] == "ninguno" and cola["emitidas_fuera_mes"] == 0 and cola["can_mark"]
    estados = {x["id"]: x["einvoice_status"] for x in cola["items"]}
    assert estados[simulada["id"]] == "aceptada" and estados[pagada["id"]] == "sin_emitir"
    assert all(x["markable"] for x in cola["items"]) and cola["production_since"] is None
    # el contador y el correo diario salen de la misma definicion
    from app.services import fiscal_queue as fq

    assert fq.queue_count(db_session, tid) == len(esperadas)
    # la simulada se marca (la venta real se emitio en Fygaro) y sale de la cola
    assert _marcar(client, simulada["id"], number="FE-SIM-1").status_code == 200
    assert simulada["id"] not in _ids(client)
    assert ids == sorted(ids)  # la mas vieja primero (misma fecha: por numero)
    assert all(x["days_waiting"] == 0 for x in cola["items"]) and cola["oldest_issue_date"]


def test_venta_de_la_tienda_aparece_en_por_emitir(client, auth, db_session):
    from app.models import Invoice

    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    cfg = {
        "methods": {"sinpe": {"active": True, "phone": "8888-7777", "holder": "Crimson SA"}, "efectivo": {"active": False}},
        "auto_invoice": True,
        "pickup": {"address": "Oficinas Crimson, Escazú", "hours": "L-V 8-17"},
    }
    assert client.put("/store/checkout", json=cfg).status_code == 200
    prod = client.get(f"{SLUG}/products").json()[0]
    body = {
        "lines": [{"product_id": prod["id"], "quantity": 1}],
        "contact": {"name": "Ana Vega", "email": "ana@correo.com", "phone": "8888-0000", "id_type": "fisica", "id_number": "1-1111-0111"},
        "delivery": {"type": "retiro"},
        "payment_method": "sinpe",
        "accept_terms": True,
    }
    assert client.post(f"{SLUG}/checkout", json=body).status_code == 201
    oid = client.get("/store-orders").json()["items"][0]["id"]
    paid = client.post(f"/store-orders/{oid}/status", json={"status": "pagado", "payment_ref": "SINPE 123"}).json()
    inv = db_session.get(Invoice, paid["invoice_id"])
    assert inv.status == "pagada" and inv.einvoice_status == "sin_emitir"
    assert inv.id in _ids(client)
    # al marcarla sale de la cola y emit() la rechaza
    assert _marcar(client, inv.id).status_code == 200
    assert inv.id not in _ids(client)
    r = client.post(f"/invoices/{inv.id}/emit")
    assert r.status_code == 409 and "fuera de la plataforma" in r.json()["detail"] and "00100001010000004321" in r.json()["detail"]


def test_marcar_emitida_en_fygaro_y_emit_la_rechaza(client, auth, db_session):
    from app.models import AuditLog, EInvoiceDocument, Invoice

    inv = _pagar(client, _factura(client))
    ayer = _hoy() - timedelta(days=1)
    r = _marcar(client, inv["id"], number="  FE-0000123 ", when=ayer)
    assert r.status_code == 200, r.text
    assert r.json()["einvoice_status"] == "emitida_fuera" and r.json()["external_number"] == "FE-0000123"
    got = client.get(f"/invoices/{inv['id']}").json()
    assert got["einvoice_status"] == "emitida_fuera" and got["external_number"] == "FE-0000123"
    assert got["external_date"] == ayer.isoformat()
    log = db_session.query(AuditLog).filter(AuditLog.action == "emitida_fuera", AuditLog.entity_id == inv["id"]).one()
    assert log.diff["antes"] == "sin_emitir" and log.diff["numero"] == "FE-0000123"
    cola = _cola(client)
    assert inv["id"] not in [x["id"] for x in cola["items"]] and cola["emitidas_fuera_mes"] == (1 if ayer.month == _hoy().month else 0)

    # nunca se reemite: ni en simulado ni en pruebas ni en produccion (la guarda va antes del modo)
    for prov in ("none", "sandbox"):
        client.put("/settings", json={"einvoice_provider": prov})
        r = client.post(f"/invoices/{inv['id']}/emit")
        assert r.status_code == 409 and "n.º FE-0000123" in r.json()["detail"], r.text
    assert db_session.query(EInvoiceDocument).filter(EInvoiceDocument.invoice_id == inv["id"]).count() == 0
    # dos veces no: ya esta marcada
    assert _marcar(client, inv["id"], number="OTRO-1").status_code == 409
    # ni se edita aunque no tenga pagos (el comprobante vive en Fygaro)
    sin_pagos = _factura(client)
    assert _marcar(client, sin_pagos["id"], number="FE-0000124").status_code == 200
    body = {"customer_id": got["customer_id"], "lines": [{"name": "Otra cosa", "quantity": 1, "unit_price": 10}]}
    r = client.put(f"/invoices/{sin_pagos['id']}", json=body)
    assert r.status_code == 409 and db_session.get(Invoice, sin_pagos["id"]).total == Decimal(sin_pagos["total"])


def test_importada_tampoco_se_emite(client, auth, db_session):
    from app.models import Invoice

    inv = _factura(client)
    db_session.get(Invoice, inv["id"]).einvoice_status = "importada"
    db_session.commit()
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    r = client.post(f"/invoices/{inv['id']}/emit")
    assert r.status_code == 409 and "histórico" in r.json()["detail"]
    assert _marcar(client, inv["id"]).status_code == 409  # ya esta emitida fuera


def test_validaciones_de_la_marca(client, auth, db_session):
    from app.models import EInvoiceDocument, Invoice

    a, b = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    assert _marcar(client, a["id"], when=_hoy() + timedelta(days=1)).status_code == 422  # fecha futura (manana en Costa Rica)
    assert _marcar(client, a["id"], number="<script>").status_code == 422
    assert _marcar(client, a["id"], number="FE-77").status_code == 200
    r = _marcar(client, b["id"], number="FE-77")  # el mismo numero en otra factura: casi seguro un error
    assert r.status_code == 409 and a["number"] in r.json()["detail"]
    assert client.post("/fiscal/queue/999999/external", json={"number": "X1", "date": _hoy().isoformat()}).status_code == 404
    # anulada
    c = _factura(client)
    client.post(f"/invoices/{c['id']}/void")
    assert _marcar(client, c["id"], number="FE-78").status_code == 409

    # lo emitido en SIMULADO (sin validez fiscal, plan 0.5) si se marca: la venta real se emitio en Fygaro
    client.put("/settings", json={"einvoice_provider": "sandbox"})
    s = _pagar(client, _factura(client))
    assert client.post(f"/invoices/{s['id']}/emit").json()["status"] == "aceptada"
    client.put("/settings", json={"einvoice_provider": "none"})
    assert _marcar(client, s["id"], number="FE-79").status_code == 200
    assert client.post(f"/invoices/{s['id']}/credit-note", json={"reason": "No aplica"}).status_code == 409  # la NC va en Fygaro

    # un comprobante de PRODUCCION vivo nunca se tapa con la marca; uno en camino a Hacienda tampoco
    for status, einv in (("aceptada", "aceptada"), ("por_enviar", "pendiente")):
        p = _pagar(client, _factura(client))
        db_session.add(
            EInvoiceDocument(
                tenant_id=auth["tenant"]["id"],
                invoice_id=p["id"],
                doc_type="FE",
                consecutive="00100001010000000999",
                provider="hacienda",
                status=status,
                mode="produccion",
                attempts=1,
            )
        )
        db_session.get(Invoice, p["id"]).einvoice_status = einv
        db_session.commit()
        assert _marcar(client, p["id"], number=f"FE-{status}").status_code == 409


def test_produccion_cerrada_sin_envio_se_marca(client, auth, db_session):
    """Se apago produccion (o el servidor la bloqueo) con comprobantes de produccion esperando al worker: send_einvoice
    los cierra con "Cambio el modo" SIN ningun POST (attempts == 0) y la factura vuelve a "sin_emitir". Nunca llegaron a
    Hacienda: entran a "Por emitir", se marcan y la fila lo dice (markable). Uno con error DESPUES de un POST real pudo
    llegar: ese no se tapa con la marca, y la fila no ofrece el boton (la misma regla que void_invoice)."""
    from app.models import EInvoiceDocument, Invoice
    from app.services import hacienda_sync

    tid = auth["tenant"]["id"]
    nunca, quizas = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    doc = EInvoiceDocument(
        tenant_id=tid,
        invoice_id=nunca["id"],
        doc_type="FE",
        consecutive="00100001010000000901",
        provider="hacienda",
        status="por_enviar",
        mode="produccion",
        attempts=0,
    )
    db_session.add(doc)
    db_session.get(Invoice, nunca["id"]).einvoice_status = "pendiente"
    db_session.commit()
    assert _marcar(client, nunca["id"], number="FE-N0").status_code == 409  # en camino: se espera
    # el modo vigente es "ninguno" (produccion apagada): el worker la cierra sin mandarla
    assert hacienda_sync.send_einvoice(db_session, doc.id) == "error"
    db_session.refresh(doc)
    assert doc.attempts == 0 and "Cambió el modo" in doc.last_error
    assert db_session.get(Invoice, nunca["id"]).einvoice_status == "sin_emitir"
    db_session.add(
        EInvoiceDocument(
            tenant_id=tid,
            invoice_id=quizas["id"],
            doc_type="FE",
            consecutive="00100001010000000902",
            provider="hacienda",
            status="error",
            mode="produccion",
            attempts=1,
        )
    )
    db_session.get(Invoice, quizas["id"]).einvoice_status = "sin_emitir"  # p. ej. un intento posterior que no salio
    db_session.commit()
    marcable = {x["id"]: x["markable"] for x in _cola(client)["items"]}
    assert marcable[nunca["id"]] is True and marcable[quizas["id"]] is False
    r = _marcar(client, quizas["id"], number="FE-Q1")
    assert r.status_code == 409 and "Producción" in r.json()["detail"]
    assert _marcar(client, nunca["id"], number="FE-N1").status_code == 200
    assert nunca["id"] not in _ids(client)


def test_emitida_en_fygaro_con_un_comprobante_de_pruebas_se_anula(client, auth, db_session):
    """Aceptada en el ambiente de PRUEBAS (sin validez fiscal) y despues emitida en Fygaro: la NC tambien va en Fygaro y
    aqui se anula la factura interna. El comprobante de pruebas no lo impide (si no, no habria salida); uno de
    produccion, o uno viejo sin modo, si."""
    from app.models import EInvoiceDocument, Invoice

    tid = auth["tenant"]["id"]
    inv = _factura(client)
    _doc(db_session, tid, inv["id"], "aceptada", "pruebas", "aceptada")
    assert _marcar(client, inv["id"], number="FE-PR1").status_code == 200
    r = client.post(f"/invoices/{inv['id']}/void")
    assert r.status_code == 200 and r.json()["status"] == "anulada", r.text
    # sin la marca, la aceptada (aunque sea de pruebas) se anula con su nota de credito, como siempre
    otra = _factura(client)
    _doc(db_session, tid, otra["id"], "aceptada", "pruebas", "aceptada")
    assert client.post(f"/invoices/{otra['id']}/void").status_code == 409
    # emitida fuera con un comprobante sin modo (de antes de la columna) que pudo llegar a Hacienda: no se anula a ciegas
    vieja = _factura(client)
    assert _marcar(client, vieja["id"], number="FE-PR2").status_code == 200
    db_session.add(
        EInvoiceDocument(
            tenant_id=tid,
            invoice_id=vieja["id"],
            doc_type="FE",
            consecutive="00100001010000000903",
            provider="hacienda",
            status="aceptada",
            mode=None,
            attempts=1,
        )
    )
    db_session.commit()
    assert client.post(f"/invoices/{vieja['id']}/void").status_code == 409
    assert db_session.get(Invoice, vieja["id"]).status != "anulada"


def test_permisos_y_alcance(client, auth, db_session):
    tid = auth["tenant"]["id"]
    conta = role_headers(client, db_session, tid, "conta.cola@ejemplo.com", "contabilidad")
    lectura = role_headers(client, db_session, tid, "lectura.cola@ejemplo.com", "lectura")
    vend = role_headers(client, db_session, tid, "vend.cola@ejemplo.com", "ventas")
    del_admin = _pagar(client, _factura(client))
    del_vendedor = _pagar(client, _factura(client, headers=vend), headers=vend)

    # contabilidad ve toda la cola y marca (duena de la emision); solo lectura ve pero no marca
    assert set(_ids(client, conta)) == {del_admin["id"], del_vendedor["id"]}
    assert _cola(client, lectura)["can_mark"] is False
    assert _marcar(client, del_admin["id"], number="FE-1", headers=lectura).status_code == 403
    # el vendedor ve lo suyo pero no lo marca: la marca saca la venta de la cola y del correo a contabilidad, y la
    # emision es de contabilidad (sales.emitir_fuera, plan 1.7 y la RACI). Lo ajeno ni lo ve.
    vista = _cola(client, vend)
    assert [x["id"] for x in vista["items"]] == [del_vendedor["id"]] and vista["can_mark"] is False
    assert _marcar(client, del_vendedor["id"], number="FE-V1", headers=vend).status_code == 403
    assert _marcar(client, del_admin["id"], number="FE-1", headers=vend).status_code == 403
    assert del_vendedor["id"] in _ids(client, conta)
    assert _marcar(client, del_admin["id"], number="FE-1", headers=conta).status_code == 200
    # deshacer una marca equivocada: solo quien configura la empresa
    assert client.delete(f"/fiscal/queue/{del_admin['id']}/external", headers=conta).status_code == 403
    r = client.delete(f"/fiscal/queue/{del_admin['id']}/external")
    assert r.status_code == 200 and r.json()["einvoice_status"] == "sin_emitir" and r.json()["external_number"] is None
    assert del_admin["id"] in _ids(client, conta)
    assert client.delete(f"/fiscal/queue/{del_admin['id']}/external").status_code == 409


def test_numero_repetido_no_nombra_una_factura_ajena(client, auth, db_session):
    """Un rol con sales.emitir_fuera pero sin sales.ver_todo marca lo suyo; si el numero ya esta en una factura que no
    ve, el 409 no la nombra (lo ajeno no aparece en ningun lado)."""
    from app.models import Invoice, Role, TenantUser, User

    tid = auth["tenant"]["id"]
    db_session.add(Role(code="marca_propia", name="Marca lo propio", permissions={"sales": ["ver", "emitir_fuera"]}))
    db_session.commit()
    propio = role_headers(client, db_session, tid, "marca.propia@ejemplo.com", "marca_propia")
    uid = db_session.query(User).filter(User.email == "marca.propia@ejemplo.com").one().id
    assert db_session.query(TenantUser).filter(TenantUser.user_id == uid).one().role_code == "marca_propia"
    ajena, suya = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    db_session.get(Invoice, suya["id"]).seller_id = uid
    db_session.commit()
    assert _ids(client, propio) == [suya["id"]]
    assert _marcar(client, ajena["id"], number="FE-500").status_code == 200  # el admin, sobre la ajena
    r = _marcar(client, suya["id"], number="FE-500", headers=propio)
    assert r.status_code == 409 and ajena["number"] not in r.json()["detail"] and "otra factura" in r.json()["detail"]
    assert _marcar(client, suya["id"], number="FE-501", headers=propio).status_code == 200


def test_en_produccion_solo_se_marca_lo_de_antes_de_activarla(client, auth, db_session, monkeypatch):
    """El dia del corte (plan 2.1) lo vendido antes de activar produccion que ya salio en Fygaro se marca (reemitirlo
    lo duplicaria, plan 4.5); lo vendido despues se emite desde la plataforma y una marca lo sacaria de Hacienda."""
    from app.models import Invoice, Tenant
    from app.services import hacienda_config as hc

    antes, despues = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    ahora = datetime.now(UTC)
    db_session.get(Invoice, antes["id"]).created_at = ahora - timedelta(days=2)
    hc.profile(db_session, db_session.get(Tenant, auth["tenant"]["id"])).production_enabled_at = ahora - timedelta(days=1)
    db_session.commit()
    monkeypatch.setattr(hc, "hacienda_mode", lambda db, t: "produccion")
    cola = _cola(client)
    assert cola["mode"] == "produccion" and cola["production_since"]
    marcable = {x["id"]: x["markable"] for x in cola["items"]}
    assert marcable == {antes["id"]: True, despues["id"]: False}
    assert client.get("/fiscal/mode").json()["production_since"]
    r = _marcar(client, despues["id"], number="FE-P1")
    assert r.status_code == 409 and "Emitir a Hacienda" in r.json()["detail"]
    assert db_session.get(Invoice, despues["id"]).einvoice_status == "sin_emitir"
    assert _marcar(client, antes["id"], number="FE-P0").status_code == 200
    # fuera de produccion (Desactivada) se marca todo
    monkeypatch.setattr(hc, "hacienda_mode", lambda db, t: "ninguno")
    assert _cola(client)["production_since"] is None
    assert _marcar(client, despues["id"], number="FE-P1").status_code == 200


def test_emitidas_fuera_este_mes_en_hora_de_costa_rica(client, auth, db_session):
    from app.services import fiscal_queue as fq

    tid = auth["tenant"]["id"]
    a, b = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    from app.models import Invoice

    for inv, num, when in ((a, "FE-31", date(2026, 10, 31)), (b, "FE-01", date(2026, 11, 1))):
        x = db_session.get(Invoice, inv["id"])
        x.einvoice_status, x.external_number, x.external_date = "emitida_fuera", num, when
    db_session.commit()
    # 31/10 a las 21:00 en Costa Rica (ya 1/11 en UTC): todavia es octubre
    assert fq.external_month_count(db_session, tid, date(2026, 10, 31)) == 1
    assert fq.external_month_count(db_session, tid, date(2026, 11, 1)) == 1
    assert fq.external_month_count(db_session, tid, date(2026, 12, 15)) == 0
    assert fq.month_of(date(2026, 12, 9)) == (date(2026, 12, 1), date(2027, 1, 1))


def test_marcar_de_noche_usa_la_fecha_de_costa_rica(client, auth, db_session, monkeypatch):
    """31/10 a las 21:00 en Costa Rica ya es 1/11 en UTC: la fecha de hoy (y el mes del contador) es la de Costa Rica."""
    from app.providers.einvoice import CR
    from app.services import fiscal_queue as fq

    monkeypatch.setattr(fq, "cr_now", lambda: datetime(2026, 10, 31, 21, 0, tzinfo=CR))
    a, b = _pagar(client, _factura(client)), _pagar(client, _factura(client))
    # manana en Costa Rica (aunque en UTC ya sea "hoy") es fecha futura
    assert _marcar(client, a["id"], number="FE-N1", when=date(2026, 11, 1)).status_code == 422
    assert _marcar(client, a["id"], number="FE-N1", when=date(2026, 10, 31)).status_code == 200
    cola = _cola(client)
    assert cola["today"] == "2026-10-31" and cola["mes"] == "2026-10" and cola["emitidas_fuera_mes"] == 1
    assert [x["id"] for x in cola["items"]] == [b["id"]]
    # sin elegir la fecha (la de hoy por defecto, como el portal): la de Costa Rica, no la UTC de manana
    r = _marcar(client, b["id"], number="FE-N2")
    assert r.status_code == 200 and r.json()["external_date"] == "2026-10-31"


def test_sin_ventas_no_ve_la_cola(client, auth, db_session):
    tec = role_headers(client, db_session, auth["tenant"]["id"], "tec.cola@ejemplo.com", "tecnico")
    inv = _pagar(client, _factura(client))
    assert client.get("/fiscal/queue", headers=tec).status_code == 403
    assert _marcar(client, inv["id"], number="FE-T1", headers=tec).status_code == 403


def test_correo_diario_una_vez_por_dia_de_costa_rica(client, auth, db_session, correos, monkeypatch):
    from app.core.config import settings
    from app.models import EmailOutbox, TenantUser
    from app.tasks.fiscal import send_queue_digest

    monkeypatch.setattr(settings, "public_base_url", "https://panel.ejemplo.com")
    tid = auth["tenant"]["id"]
    # sin nada por emitir no sale nada
    assert send_queue_digest(db_session) == 0
    inv = _pagar(client, _factura(client))
    role_headers(client, db_session, tid, "conta.diario@ejemplo.com", "contabilidad")
    correos.clear()
    # 21:00 en Costa Rica del 20/10 = 03:00 UTC del 21/10: el correo es del 20 (no de manana)
    noche = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    assert send_queue_digest(db_session, now=noche) == 1
    assert [c["to"] for c in correos] == ["conta.diario@ejemplo.com"]  # contabilidad, no el admin
    html = correos[0]["html"]
    assert f"https://panel.ejemplo.com/facturas/{inv['id']}" in html and inv["number"] in html
    assert "https://panel.ejemplo.com/facturas?estado=por_emitir" in html and "Fygaro" in html
    assert "Facturas por emitir · 2026-10-20" in html and correos[0]["subject"].startswith("Por emitir: 1 factura")
    row = db_session.query(EmailOutbox).filter(EmailOutbox.entity == "fiscal_queue").one()
    assert row.entity_id == 20261020
    # doble disparo del beat el mismo dia de Costa Rica: no repite
    assert send_queue_digest(db_session, now=noche + timedelta(hours=2)) == 0
    # al dia siguiente (CR) si sale otra vez
    assert send_queue_digest(db_session, now=datetime(2026, 10, 21, 14, 30, tzinfo=UTC)) == 1
    # sin nadie de contabilidad activo, le llega al administrador (alguien es dueno del aviso)
    db_session.query(TenantUser).filter(TenantUser.role_code == "contabilidad").update({"active": False})
    db_session.commit()
    correos.clear()
    assert send_queue_digest(db_session, now=datetime(2026, 10, 22, 14, 30, tzinfo=UTC)) == 1
    assert correos[0]["to"] == "admin@crimsonapp.com"
    # marcada, la cola queda vacia y no hay correo
    assert _marcar(client, inv["id"]).status_code == 200
    assert send_queue_digest(db_session, now=datetime(2026, 10, 23, 14, 30, tzinfo=UTC)) == 0


def test_tarea_registrada_en_el_worker():
    from celery import Celery

    import app.tasks.fiscal  # noqa: F401

    w = Celery("prueba-cola")
    w.loader.import_default_modules()
    assert "fiscal.por_emitir_diario" in w.tasks
