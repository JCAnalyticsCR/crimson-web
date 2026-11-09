"""Tienda de pruebas con ONVO modo Test (09/11, pedido de Andres): ambiente AISLADO del flujo publico y de los datos reales.

Todo contra FakeOnvo (llaves y secretos ficticios) y FakeHacienda: nada sale a la red, nada se cobra. Cubre:
- llaves de Pruebas aparte (solo onvo_test_, whsec_ propio), nunca devueltas ni registradas; estado API/webhook por ambiente;
- compra de prueba de punta a punta: aprobada, rechazada, cancelada, reabierta, pago tardio, pago doble, duplicados;
- el webhook de pruebas (firma sobre el cuerpo crudo, su whsec_, modo test, idempotencia, limitador propio) y las barreras
  inversas del camino real (evento de pruebas en produccion, metadata de pruebas, whsec_ cruzados);
- que la tienda publica, los enlaces de pago de facturas y Hacienda real NUNCA usan la llave de pruebas, y que no se toca
  inventario, clientes, pedidos, facturas, pagos, contabilidad, reportes ni correos reales;
- la factura de prueba en el ambiente de Pruebas de Hacienda (T01, TipoMedioPago del pago, receptor = la empresa).
"""

import hashlib
import hmac
import json
import logging
import time

import pytest
from sqlalchemy import func, select

from tests.hacienda_utils import ADMIN_PASSWORD, role_headers
from tests.test_hacienda_prueba_real import CABYS, _listo, cabys_ok, reloj  # noqa: F401  (fixtures)
from tests.test_onvo import _invoice
from tests.test_tienda_checkout import SLUG, _body, _shop

BASE = "/store/pruebas"


def _real_counts(db) -> dict:
    """Todo lo real que una compra de prueba NO puede tocar."""
    from app.models import (
        BillingGroup,
        Coupon,
        Customer,
        EInvoiceDocument,
        EmailOutbox,
        Invoice,
        JournalEntry,
        Order,
        OrderLine,
        Payment,
        Product,
        PurchaseRequest,
        StockMovement,
        StockReservation,
    )

    models = (
        Order,
        OrderLine,
        Customer,
        Invoice,
        EInvoiceDocument,
        Payment,
        StockReservation,
        StockMovement,
        PurchaseRequest,
        EmailOutbox,
        Coupon,
        JournalEntry,
        Product,
    )
    out = {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in models}
    out["consecutivos"] = sorted((g.tenant_id, g.doc_type, g.current) for g in db.scalars(select(BillingGroup)))
    return out


def _new(client, method="tarjeta", qty=1) -> dict:
    r = client.post(f"{BASE}/orders", json={"method": method, "quantity": qty})
    assert r.status_code == 201, r.text
    return r.json()


def _consult(client, oid, regreso=None) -> dict:
    r = client.post(f"{BASE}/orders/{oid}/consultar", json={"regreso": regreso} if regreso else None)
    assert r.status_code == 200, r.text
    return r.json()


def _sig(secret: str, raw: bytes) -> str:
    t = str(int(time.time()))
    return f"t={t},v1={hmac.new(secret.encode(), t.encode() + b'.' + raw, hashlib.sha256).hexdigest()}"


def _order(client, oid) -> dict:
    r = client.get(f"{BASE}/orders/{oid}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- llaves y estado por ambiente
def test_llaves_de_pruebas_aparte_validadas_y_nunca_devueltas(client, auth, fake_onvo, db_session, monkeypatch, caplog):
    from app.core.config import settings
    from app.models import AuditLog

    caplog.set_level(logging.DEBUG)
    tid = auth["tenant"]["id"]
    monkeypatch.setattr(settings, "env", "production")
    # Produccion sigue rechazando una llave de pruebas (la regla de siempre)
    assert client.put("/settings/gateways", json={"provider": "onvo", "secret": fake_onvo.test_key, "active": True}).status_code == 422
    fake_onvo.connect(client, secret=fake_onvo.live_key, client_id=fake_onvo.live_public, webhook_secret=fake_onvo.live_wh)
    put = lambda **b: client.put("/settings/gateways/onvo-pruebas", json={"enabled": True, **b})  # noqa: E731
    r = put(secret=fake_onvo.live_key)
    assert r.status_code == 422 and "onvo_test_" in r.json()["detail"]
    assert put(secret=fake_onvo.test_key, publishable=fake_onvo.live_public).status_code == 422
    assert put(secret=fake_onvo.test_key, webhook_secret=fake_onvo.live_wh).status_code == 422  # el whsec_ de produccion
    assert put(secret=fake_onvo.test_key, webhook_secret=fake_onvo.wh).status_code == 422  # formato anterior: solo firmado
    assert put(secret=fake_onvo.test_public).status_code == 422  # publicable en el campo secreto
    assert put().status_code == 422  # habilitar sin llave
    st = fake_onvo.connect_test(client)
    assert st["api"]["configured"] and st["api"]["enabled"] and st["api"]["mode"] == "test" and st["api"]["secret_mask"].startswith("onvo_test_secret_key_••••")
    assert st["webhook"]["set"] and st["webhook"]["format"] == "firmado" and st["webhook"]["url"].endswith(f"/webhooks/onvo/{tid}/pruebas")
    # barrera inversa: Produccion no acepta el whsec_ de pruebas
    assert (
        client.put("/settings/gateways", json={"provider": "onvo", "webhook_secret": fake_onvo.test_wh, "active": True, "is_primary": True}).status_code == 422
    )
    # guardar Produccion sin reenviar la publicable no la borra
    assert client.put("/settings/gateways", json={"provider": "onvo", "active": True, "is_primary": True}).status_code == 200
    g = client.get("/settings/gateways").json()
    assert len(g) == 1 and g[0]["client_id"] == fake_onvo.live_public and g[0]["mode"] == "live"  # Pruebas no se lista ahi
    # «Probar» de Pruebas usa SOLO la llave de pruebas
    r = client.post("/settings/gateways/onvo-pruebas/probar").json()
    assert r == {"ok": True, "mode": "test", "detail": "Conectado a ONVO en modo pruebas"} and fake_onvo.pings == [fake_onvo.test_key]
    live = client.get("/settings/gateways/onvo/status").json()
    assert live["configured"] and live["mode"] == "live" and not any("pruebas" in p.lower() and "onvo_test" in p for p in live["problems"])
    # nada devuelve ni registra una llave o un whsec_
    dump = json.dumps([g, client.get("/settings/gateways/onvo-pruebas").json(), live, st], ensure_ascii=False, default=str)
    diffs = json.dumps([a.diff for a in db_session.scalars(select(AuditLog))], ensure_ascii=False, default=str)
    for k in (fake_onvo.test_key, fake_onvo.test_wh, fake_onvo.live_key, fake_onvo.live_wh):
        assert k not in dump and k not in caplog.text and k not in diffs
    # un 422 de Pasarelas no devuelve lo que se mando
    largo = "onvo_test_secret_key_" + "z" * 400
    for url, body in (("/settings/gateways", {"provider": "onvo", "secret": largo}), ("/settings/gateways/onvo-pruebas", {"secret": largo})):
        r = client.put(url, json=body)
        assert r.status_code == 422 and "z" * 40 not in r.text and "input" not in r.text
    # quitar las llaves de pruebas
    assert client.delete("/settings/gateways/onvo-pruebas").json()["api"]["configured"] is False
    assert client.post(f"{BASE}/orders", json={"method": "tarjeta"}).status_code == 409


def test_permisos_y_probar_sin_la_llave_de_entorno_de_otra_empresa(client, auth, fake_onvo, db_session, monkeypatch):
    from app.core.config import settings

    fake_onvo.connect_test(client)
    ventas = role_headers(client, db_session, auth["tenant"]["id"], "ventas.prueba@ejemplo.com", "ventas")
    for method, url in (
        ("put", "/settings/gateways/onvo-pruebas"),
        ("delete", "/settings/gateways/onvo-pruebas"),
        ("post", "/settings/gateways/onvo-pruebas/probar"),
    ):
        kw = {"json": {"enabled": False}} if method == "put" else {}
        assert getattr(client, method)(url, headers=ventas, **kw).status_code == 404  # las llaves de pruebas son solo del administrador real (como la Tienda de pruebas)
    # la Tienda de pruebas no existe para quien no es administrador
    assert client.get(BASE, headers=ventas).status_code == 404
    assert client.post(f"{BASE}/orders", json={"method": "tarjeta"}, headers=ventas).status_code == 404
    # «Probar» de Produccion: la llave de entorno de OTRA empresa (ONVO_ENV_TENANT) nunca se usa
    monkeypatch.setattr(settings, "onvo_secret_key", "onvo_live_secret_key_" + "E" * 24)
    monkeypatch.setattr(settings, "onvo_env_tenant", "otra-empresa")
    r = client.post("/settings/gateways/onvo/test").json()
    assert not r["ok"] and r["detail"] == "No hay llave secreta de ONVO guardada" and fake_onvo.pings == []


def test_cifrado_aislado_sin_llavero_y_rotacion(client, auth, fake_onvo, db_session, monkeypatch):
    import base64
    import os

    from pydantic import SecretStr

    from app.core.config import settings
    from app.models import OnvoTestConfig, PaymentGatewayConfig
    from app.services import onvo, onvo_test
    from app.tools.rotate_fiscal_keys import rotate

    tid = auth["tenant"]["id"]
    fake_onvo.connect_test(client)
    c = db_session.scalar(select(OnvoTestConfig))
    # un token de pruebas copiado a la fila de produccion no se lee: la tienda publica no ofrece tarjeta
    db_session.add(PaymentGatewayConfig(tenant_id=tid, provider="onvo", secret_encrypted=c.secret_enc, active=True, is_primary=True, mode="test"))
    db_session.commit()
    assert onvo.secret_key(db_session, tid) is None and onvo.unreadable(onvo.gateway(db_session, tid))["secret"]
    # y uno de produccion copiado a la de pruebas tampoco
    g = onvo.gateway(db_session, tid)
    g.secret_encrypted = onvo.seal_secret(tid, "secret", fake_onvo.live_key)
    keep = c.secret_enc
    c.secret_enc = g.secret_encrypted
    db_session.commit()
    assert onvo_test.creds(db_session, tid) is None
    c.secret_enc = keep
    db_session.commit()
    assert onvo_test.creds(db_session, tid).secret == fake_onvo.test_key
    assert "t" * 10 not in repr(onvo_test.creds(db_session, tid))
    # rotacion: la llave nueva adelante, se vuelve a cifrar y sigue legible sin la vieja
    vieja = settings.fiscal_keys.get_secret_value()
    nueva = "knew:" + base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "fiscal_keys", SecretStr(f"{nueva},{vieja}"))
    res = rotate(db_session)
    assert res["onvo_pruebas"] == 2 and res["onvo_pruebas_ilegibles"] == 0
    monkeypatch.setattr(settings, "fiscal_keys", SecretStr(nueva))
    db_session.expire_all()
    cr = onvo_test.creds(db_session, tid)
    assert cr.secret == fake_onvo.test_key and cr.webhook == fake_onvo.test_wh
    # sin FISCAL_KEYS (y sin la llave de desarrollo): no se guarda y el estado lo dice antes
    monkeypatch.setattr(settings, "fiscal_keys", None)
    monkeypatch.setattr(settings, "env", "production")
    st = client.get("/settings/gateways/onvo-pruebas").json()
    assert not st["fiscal_keys"] and any("FISCAL_KEYS" in p for p in st["problems"])
    r = client.put("/settings/gateways/onvo-pruebas", json={"secret": fake_onvo.test_key, "enabled": True})
    assert r.status_code == 503 and "FISCAL_KEYS" in r.json()["detail"]


# ---------------------------------------------------------------- compra de prueba de punta a punta
def test_compra_aprobada_de_punta_a_punta_sin_tocar_nada_real(client, auth, fake_onvo, db_session):
    tid = auth["tenant"]["id"]
    antes = _real_counts(db_session)
    # sin llave de pruebas: 409 y no se llama a ONVO (ni se simula)
    r = client.post(f"{BASE}/orders", json={"method": "tarjeta"})
    assert r.status_code == 409 and "llave secreta de pruebas" in r.json()["detail"] and fake_onvo.created == []
    fake_onvo.connect_test(client)
    o = _new(client)
    assert o["number"] == "PRB-0001" and o["total"] == "113.00" and o["subtotal"] == "100.00" and o["tax"] == "13.00"
    assert o["status"] == "en_onvo" and o["checkout_url"].startswith("https://checkout.onvopay.com/") and o["buyer"] == {"name": "Cliente de prueba"}
    kw = fake_onvo.created[0]
    assert kw["key"] == fake_onvo.test_key and kw["amount_cents"] == 11300 and kw["currency"] == "CRC"
    assert kw["metadata"] == {"crimson_env": "test", "test_order_id": o["id"], "tenant_id": tid}  # nunca order_id ni invoice_id
    assert kw["customer"] == {"name": "Cliente de prueba"} and kw["description"].startswith("PRUEBA · PRB-0001")
    assert "/mi-tienda/pruebas?prueba=" in kw["redirect_url"] and kw["redirect_url"].endswith("&r=ok") and kw["cancel_url"].endswith("&r=cancelado")
    assert "/public" not in kw["redirect_url"] and "/tienda/" not in kw["redirect_url"]
    # volver sin pagar no cambia nada
    assert _consult(client, o["id"], "ok")["status"] == "en_onvo"
    # paga con la 4242 (aprobada): Consultar ONVO lo confirma con la llave de pruebas
    fake_onvo.pay(o["sessions"][-1], method="card")
    v = _consult(client, o["id"], "ok")
    assert v["status"] == "pagado" and v["paid_method"] == "tarjeta" and v["intent_id"] == f"pi_{o['sessions'][-1]}"
    assert [k for k, c in v["checks"].items() if c["ok"] is False] == []
    for k in ("sesion", "pagado", "ambiente", "meta_ambiente", "meta_pedido", "meta_empresa", "monto", "moneda", "metodo"):
        assert v["checks"][k]["ok"] is True, (k, v["checks"][k])
    assert v["checks"]["monto"]["origen"] == "sesión" and v["checks"]["metodo"]["origen"] == "intención"
    assert {k for _, k in fake_onvo.reads} == {fake_onvo.test_key}
    # otra consulta: «duplicado ignorado», sigue pagado una sola vez
    v2 = _consult(client, o["id"])
    assert v2["status"] == "pagado" and v2["paid_at"] == v["paid_at"] and any(e["what"] == "Duplicado ignorado" for e in v2["timeline"])
    # evidencia descargable sin llaves
    ev = client.get(f"{BASE}/orders/{o['id']}/evidencia")
    assert ev.status_code == 200 and "attachment" in ev.headers["content-disposition"] and "PRB-0001" in ev.headers["content-disposition"]
    assert fake_onvo.test_key not in ev.text and json.loads(ev.text)["onvo"]["ambiente"] == "test"
    # nada real cambio: pedidos, clientes, inventario, facturas, pagos, contabilidad, cupones, consecutivos ni correos
    assert _real_counts(db_session) == antes
    # la lista
    home = client.get(BASE).json()
    assert [x["number"] for x in home["orders"]] == ["PRB-0001"] and home["status"]["api"]["configured"]
    assert fake_onvo.test_key not in json.dumps(home, default=str)


def test_rechazada_cancelada_reabierta_y_pago_tardio(client, auth, fake_onvo, db_session):
    antes = _real_counts(db_session)
    fake_onvo.connect_test(client)
    o = _new(client)
    # 4000 0000 0000 0002: ONVO rechaza; Consultar ONVO lo marca Fallido con el mensaje
    fake_onvo.fail(o["sessions"][0], "Tarjeta rechazada por el banco")
    v = _consult(client, o["id"])
    assert v["status"] == "fallido" and any(e["what"] == "ONVO rechazó el pago" and "rechazada" in e["detail"] for e in v["timeline"])
    assert v["can_reopen"]
    r = client.post(f"{BASE}/orders/{o['id']}/reabrir")
    assert r.status_code == 200 and r.json()["status"] == "en_onvo" and len(r.json()["sessions"]) == 2 and r.json()["checkout_url"]
    # cancela en ONVO y vuelve: Cancelado
    v = _consult(client, o["id"], "cancelado")
    assert v["status"] == "cancelado"
    # la sesion abierta igual se paga despues: pasa a Pagado con la nota
    fake_onvo.pay(v["sessions"][-1])
    v = _consult(client, o["id"])
    assert v["status"] == "pagado" and any("llegó con el pedido «Cancelado»" in e["what"] for e in v["timeline"])
    assert client.post(f"{BASE}/orders/{o['id']}/reabrir").status_code == 409  # pagado: no se reabre
    # tope de 5 pagos por pedido
    o2 = _new(client)
    for _ in range(4):
        assert client.post(f"{BASE}/orders/{o2['id']}/reabrir").status_code == 200
    r = client.post(f"{BASE}/orders/{o2['id']}/reabrir")
    assert r.status_code == 409 and "5" in r.json()["detail"]
    # boton Cancelar del panel
    o3 = _new(client)
    assert client.post(f"{BASE}/orders/{o3['id']}/cancelar").json()["status"] == "cancelado"
    assert _real_counts(db_session) == antes


def test_monto_moneda_y_pago_doble_van_a_revisar(client, auth, fake_onvo):
    fake_onvo.connect_test(client)
    a = _new(client)
    fake_onvo.pay(a["sessions"][0], amount=5000)  # ONVO cobro otro monto
    v = _consult(client, a["id"])
    assert v["status"] == "revisar" and v["checks"]["monto"]["ok"] is False and v["paid_at"] is None
    b = _new(client)
    fake_onvo.pay(b["sessions"][0], currency="USD")
    v = _consult(client, b["id"])
    assert v["status"] == "revisar" and v["checks"]["moneda"]["ok"] is False
    c = _new(client)
    sid1 = c["sessions"][0]
    fake_onvo.fail(sid1)
    _consult(client, c["id"])
    sid2 = client.post(f"{BASE}/orders/{c['id']}/reabrir").json()["sessions"][-1]
    fake_onvo.pay(sid2)
    assert _consult(client, c["id"])["status"] == "pagado"
    fake_onvo.pay(sid1)  # la primera sesion tambien termino pagada: pago doble
    v = _consult(client, c["id"])
    assert v["status"] == "revisar" and v["checks"]["pago_doble"]["ok"] is False and any("pago doble" in e["what"] for e in v["timeline"])


def test_tope_vencimiento_acceso_y_limpiar(client, auth, fake_onvo, db_session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models import StoreTestOrder

    fake_onvo.connect_test(client)
    # total > 2000: 422 (precio 1 000 + IVA = 1 130 por unidad)
    assert client.put(f"{BASE}/config", json={"methods": {"tarjeta": True, "sinpe_onvo": True}, "price": 1000}).status_code == 200
    r = client.post(f"{BASE}/orders", json={"method": "tarjeta", "quantity": 2})
    assert r.status_code == 422 and "2 000" in r.json()["detail"]
    assert client.put(f"{BASE}/config", json={"methods": {"tarjeta": True, "sinpe_onvo": True}, "price": 1800}).status_code == 422
    assert client.put(f"{BASE}/config", json={"methods": {"tarjeta": False, "sinpe_onvo": True}, "price": 100}).status_code == 200
    assert client.post(f"{BASE}/orders", json={"method": "tarjeta"}).status_code == 422  # metodo apagado
    o = _new(client, "sinpe_onvo")
    # vence a las 24 h al leerlo (sin worker)
    row = db_session.get(StoreTestOrder, o["id"])
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert _order(client, o["id"])["status"] == "vencido"
    # la base no admite un pedido de prueba de mas de 2 000 ni en otra moneda
    from sqlalchemy.exc import IntegrityError

    for bad in ({"total": 2500}, {"currency": "USD"}):
        db_session.add(
            StoreTestOrder(
                **{
                    "tenant_id": auth["tenant"]["id"],
                    "number": "PRB-9999",
                    "status": "nuevo",
                    "method": "tarjeta",
                    "currency": "CRC",
                    "subtotal": 1,
                    "tax": 0,
                    "total": 1,
                    "lines": [],
                    "buyer": {},
                    "onvo_sessions": [],
                    "checks": {},
                    "timeline": [],
                    **bad,
                }
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
    # acceso: cliente/aliado -> 403 generico; no admin -> 404; nunca por /public
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.prueba@ejemplo.com", "contabilidad")
    assert client.get(BASE, headers=conta).status_code == 404
    assert client.get(f"{BASE}/orders/{o['id']}", headers=conta).status_code == 404
    for url in (f"{SLUG}/pruebas", f"/public{BASE}", f"{SLUG}/orders/pruebas"):
        assert client.get(url).status_code in (404, 405)
    assert client.get(BASE, headers={"Authorization": ""}).status_code in (401, 403)  # sin sesion
    # limpiar borra solo los pedidos de prueba
    antes = _real_counts(db_session)
    r = client.delete(f"{BASE}/orders")
    assert r.status_code == 200 and r.json()["borrados"] >= 1
    assert db_session.scalar(select(func.count()).select_from(StoreTestOrder)) == 0 and _real_counts(db_session) == antes


# ---------------------------------------------------------------- la tienda publica y las facturas nunca usan Pruebas
def test_tienda_publica_y_facturas_nunca_usan_la_llave_de_pruebas(client, auth, fake_onvo, db_session, monkeypatch):
    from app.core.config import settings

    fake_onvo.connect_test(client)
    # Tienda de pruebas con tarjeta encendida; la tienda publica con «Tarjeta (ONVO)» DESMARCADA (como en produccion)
    assert client.put(f"{BASE}/config", json={"methods": {"tarjeta": True, "sinpe_onvo": True}, "price": 100}).status_code == 200
    _shop(client, tarjeta=False)
    codes = [m["code"] for m in client.get(f"{SLUG}/checkout-config").json()["methods"]]
    assert "tarjeta" not in codes
    # aunque se marque en la tienda publica, en produccion sin llave LIVE la tarjeta no se ofrece (la de pruebas no cuenta)
    _shop(client, tarjeta=True)
    monkeypatch.setattr(settings, "env", "production")
    codes = [m["code"] for m in client.get(f"{SLUG}/checkout-config").json()["methods"]]
    assert "tarjeta" not in codes
    assert client.get("/store/checkout").json()["onvo"]["configured"] is False
    # enlace de pago de una factura: sin ONVO (503), nunca con la llave de pruebas
    monkeypatch.setattr(settings, "env", "local")
    inv, token = _invoice(client)
    monkeypatch.setattr(settings, "env", "production")
    pay = f"/public/pay/{token}"
    assert client.get(pay).json()["methods"]["online"]["onvo"] is False
    assert client.post(f"{pay}/onvo").status_code == 503
    # la vitrina publica no expone la Tienda de pruebas
    home = client.get(SLUG)
    assert home.status_code == 200 and "test_shop" not in home.text and "PRUEBA" not in home.text
    assert fake_onvo.created == [] and fake_onvo.reads == []  # nadie abrio ni consulto nada con ninguna llave
    # PATCH /tenant no puede tocar la configuracion de la tienda (ni la publica ni la de pruebas)
    monkeypatch.setattr(settings, "env", "local")
    r = client.patch("/tenant", json={"settings": {"store": {"checkout": {"methods": {"tarjeta": {"active": True}}}}}})
    assert r.status_code == 422


# ---------------------------------------------------------------- webhook de pruebas
def test_webhook_de_pruebas_firma_modo_duplicados_y_rechazos(client, auth, fake_onvo, db_session):
    from app.core import ratelimit
    from app.models import EmailOutbox, Order, WebhookEvent

    tid = auth["tenant"]["id"]
    url = f"/webhooks/onvo/{tid}/pruebas"
    live_url = f"/webhooks/onvo/{tid}"
    fake_onvo.connect(client, webhook_secret=fake_onvo.live_wh)  # Produccion (en este equipo, con la llave ficticia de siempre)
    fake_onvo.connect_test(client)
    # pedido REAL de la tienda publica con tarjeta (para ver que nada de pruebas lo toca)
    prods = _shop(client, tarjeta=True)
    assert client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta")).status_code == 201
    real = db_session.scalar(select(Order).order_by(Order.id.desc()))
    real_sid = next(s for s, v in fake_onvo.sessions.items() if v["metadata"].get("order_id") == str(real.id))
    o = _new(client)
    sid = o["sessions"][0]
    fake_onvo.pay(sid)
    obj = {"object": "checkout_session", "id": sid, "metadata": {"crimson_env": "test", "test_order_id": str(o["id"]), "tenant_id": str(tid)}}
    ratelimit.webhook_limiter.reset()
    # firmado con el whsec_ de PRODUCCION en la direccion de pruebas: 401, nada aplicado, aviso
    r = fake_onvo.signed(client, url, "checkout_session.succeeded", obj, fake_onvo.live_wh, evt_id="evt_cruzado")
    assert r.status_code == 401 and _order(client, o["id"])["status"] == "en_onvo"
    st = client.get("/settings/gateways/onvo-pruebas").json()
    assert any("PRODUCCIÓN apunta a la dirección de pruebas" in p for p in st["problems"])
    assert db_session.scalar(select(EmailOutbox).where(EmailOutbox.entity == "onvo_webhook_test_401")) is not None
    # firmado con el whsec_ de PRUEBAS en la direccion de PRODUCCION: 401 y el pedido real no cambia
    assert (
        fake_onvo.signed(
            client, live_url, "checkout_session.succeeded", {"object": "checkout_session", "id": real_sid}, fake_onvo.test_wh, evt_id="evt_x"
        ).status_code
        == 401
    )
    # formato anterior (X-Webhook-Secret) en pruebas: 401
    raw = json.dumps({"type": "checkout-session.succeeded", "data": {"id": sid}}).encode()
    assert client.post(url, content=raw, headers={"X-Webhook-Secret": fake_onvo.test_wh}).status_code == 401
    # cuerpo alterado despues de firmar: 401
    raw = json.dumps({"id": "evt_t", "object": "event", "mode": "test", "type": "checkout_session.succeeded", "data": {"object": obj}}).encode()
    sig = _sig(fake_onvo.test_wh, raw)
    assert (
        client.post(url, content=raw.replace(b"checkout_session.succeeded", b"checkout_session.succeedes"), headers={"X-Onvo-Signature": sig}).status_code
        == 401
    )
    # evento de modo live en la direccion de pruebas: 200 ignorado
    r = fake_onvo.signed(client, url, "checkout_session.succeeded", obj, fake_onvo.test_wh, mode="live", evt_id="evt_live")
    assert r.status_code == 200 and r.json()["ignored"] == "modo distinto" and _order(client, o["id"])["status"] == "en_onvo"
    # el bueno: aplicado (confirmado con la API de ONVO y la llave de pruebas)
    r = fake_onvo.signed(client, url, "checkout_session.succeeded", obj, fake_onvo.test_wh, evt_id="evt_ok")
    assert r.status_code == 200 and r.json() == {"ok": True}
    v = _order(client, o["id"])
    assert v["status"] == "pagado" and any("por webhook" in e["detail"] for e in v["timeline"])
    # el mismo evt_ otra vez: duplicado, una sola vez
    r = fake_onvo.signed(client, url, "checkout_session.succeeded", obj, fake_onvo.test_wh, evt_id="evt_ok")
    assert r.json()["duplicate"] is True
    v2 = _order(client, o["id"])
    assert v2["paid_at"] == v["paid_at"] and any(e["what"] == "Duplicado ignorado" and e.get("evt") == "evt_ok" for e in v2["timeline"])
    # evento de pruebas que trae un pedido REAL en la metadata: no lo toca
    r = fake_onvo.signed(
        client,
        url,
        "checkout_session.succeeded",
        {"object": "checkout_session", "id": real_sid, "metadata": {"order_id": str(real.id)}},
        fake_onvo.test_wh,
        evt_id="evt_real",
    )
    assert r.status_code == 200
    db_session.refresh(real)
    assert real.status == "nuevo" and real.paid_at is None
    # payment_intent.failed SIN metadata (4000 0000 0000 0002): se ubica por la sesion y queda Fallido
    f = _new(client)
    pi = fake_onvo.fail(f["sessions"][0], "Fondos insuficientes")
    r = fake_onvo.signed(
        client,
        url,
        "payment_intent.failed",
        {"object": "payment_intent", "id": pi["id"], "error": {"message": "Fondos insuficientes"}},
        fake_onvo.test_wh,
        evt_id="evt_fail",
    )
    assert r.status_code == 200
    v = _order(client, f["id"])
    assert v["status"] == "fallido" and any(e["what"] == "ONVO rechazó el pago" and e.get("evt") == "evt_fail" for e in v["timeline"])
    # payment_intent.succeeded sin metadata de otro pedido: se confirma por la sesion, sin tocar nada real
    d = _new(client)
    fake_onvo.pay(d["sessions"][0])
    r = fake_onvo.signed(
        client, url, "payment_intent.succeeded", {"object": "payment_intent", "id": f"pi_{d['sessions'][0]}"}, fake_onvo.test_wh, evt_id="evt_pi"
    )
    assert r.status_code == 200 and _order(client, d["id"])["status"] == "pagado"
    # limitador propio: nada de esto gasto el de produccion
    assert f"onvo:{tid}" in ratelimit.webhook_limiter.hits  # (solo el intento de la URL live de arriba)
    n_live = len(ratelimit.webhook_limiter.hits[f"onvo:{tid}"])
    fake_onvo.signed(client, url, "checkout_session.succeeded", obj, fake_onvo.test_wh, evt_id="evt_ok")
    assert len(ratelimit.webhook_limiter.hits[f"onvo:{tid}"]) == n_live and f"onvo-test:{tid}" in ratelimit.webhook_test_limiter.hits
    # ningun evento de pruebas quedo como provider "onvo" (produccion)
    provs = {(w.provider, w.external_id) for w in db_session.scalars(select(WebhookEvent))}
    assert all(p == "onvo_test" for p, e in provs if e.startswith("evt:evt_") and e != "evt:evt_x")
    db_session.refresh(real)
    assert real.status == "nuevo"


def test_prueba_de_conexion_por_ambiente_y_por_empresa(client, auth, fake_onvo, db_session):
    from app.models import Tenant, WebhookEvent
    from app.services import onvo, onvo_test

    tid = auth["tenant"]["id"]
    fake_onvo.connect(client, webhook_secret=fake_onvo.live_wh)
    fake_onvo.connect_test(client)
    test_obj = {"object": "webhook_endpoint", "id": "we_1"}
    assert (
        fake_onvo.signed(client, f"/webhooks/onvo/{tid}/pruebas", "webhook_endpoint.test", test_obj, fake_onvo.test_wh, evt_id="evt_tp").json()["test"] is True
    )
    live = client.get("/settings/gateways/onvo/status").json()
    test = client.get("/settings/gateways/onvo-pruebas").json()
    assert live["last_webhook"] is None and test["webhook"]["last"]["type"] == "webhook_endpoint.test"
    assert db_session.scalar(select(WebhookEvent).where(WebhookEvent.provider == "onvo")) is None
    assert (
        fake_onvo.signed(client, f"/webhooks/onvo/{tid}", "webhook_endpoint.test", test_obj, fake_onvo.live_wh, evt_id="evt_tl", mode="live").json()["test"]
        is True
    )
    assert client.get("/settings/gateways/onvo/status").json()["last_webhook"]["type"] == "webhook_endpoint.test"
    # otra empresa no ve los avisos de esta (antes se veia el ultimo de cualquiera)
    b = Tenant(slug="empresa-b", name="Empresa B", default_currency="CRC")
    db_session.add(b)
    db_session.commit()
    assert onvo.status(db_session, b)["last_webhook"] is None and onvo_test.status(db_session, b)["webhook"]["last"] is None
    # sin llaves de pruebas en B, su direccion de pruebas responde 401 (igual que una empresa que no existe)
    assert fake_onvo.signed(client, f"/webhooks/onvo/{b.id}/pruebas", "webhook_endpoint.test", test_obj, fake_onvo.test_wh, evt_id="evt_b").status_code == 401
    assert fake_onvo.signed(client, "/webhooks/onvo/99999/pruebas", "webhook_endpoint.test", test_obj, fake_onvo.test_wh, evt_id="evt_n").status_code == 401


def test_barreras_del_camino_real(client, auth, fake_onvo, db_session, monkeypatch):
    """Produccion: un evento de modo test nunca toca un pedido real (aunque la llave live no se pueda leer), y una
    metadata de pruebas nunca paga un pedido real (tambien en el formato anterior, que no trae modo)."""
    from app.core.config import settings
    from app.models import Order, PaymentGatewayConfig

    tid = auth["tenant"]["id"]
    live_url = f"/webhooks/onvo/{tid}"
    fake_onvo.connect(client, secret=fake_onvo.live_key, client_id=fake_onvo.live_public, webhook_secret=fake_onvo.live_wh)
    prods = _shop(client, tarjeta=True)
    assert client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], payment_method="tarjeta")).status_code == 201
    real = db_session.scalar(select(Order).order_by(Order.id.desc()))
    sid = next(s for s, v in fake_onvo.sessions.items() if v["metadata"].get("order_id") == str(real.id))
    assert fake_onvo.sessions[sid]["mode"] == "live" and fake_onvo.created[-1]["key"] == fake_onvo.live_key
    fake_onvo.pay(sid)
    obj = {"object": "checkout_session", "id": sid}
    monkeypatch.setattr(settings, "env", "production")
    r = fake_onvo.signed(client, live_url, "checkout_session.succeeded", obj, fake_onvo.live_wh, mode="test", evt_id="evt_t1")
    assert r.status_code == 200 and r.json()["ignored"] == "evento de pruebas en producción"
    db_session.refresh(real)
    assert real.status == "nuevo"
    # con la llave live ilegible (current_mode None) tambien se ignora
    g = db_session.scalar(select(PaymentGatewayConfig))
    tok = g.secret_encrypted
    g.secret_encrypted = "v1.kx.roto"
    db_session.commit()
    assert (
        fake_onvo.signed(client, live_url, "checkout_session.succeeded", obj, fake_onvo.live_wh, mode="test", evt_id="evt_t2").json()["ignored"]
        == "evento de pruebas en producción"
    )
    g.secret_encrypted = tok
    db_session.commit()
    # metadata de la Tienda de pruebas sobre un pedido real: no lo paga (sesion con crimson_env=test)
    fake_onvo.sessions[sid]["metadata"]["crimson_env"] = "test"
    r = fake_onvo.signed(client, live_url, "checkout_session.succeeded", obj, fake_onvo.live_wh, mode="live", evt_id="evt_l1")
    assert r.status_code == 200
    db_session.refresh(real)
    assert real.status == "nuevo" and real.paid_at is None
    # formato anterior (sin modo): la misma barrera
    monkeypatch.setattr(settings, "env", "local")
    fake_onvo.connect(client, secret=fake_onvo.key, client_id=fake_onvo.public, webhook_secret=fake_onvo.wh)
    fake_onvo.sessions[sid]["mode"] = "test"
    fake_onvo.intents[f"pi_{sid}"].update(mode="test", metadata={"order_id": str(real.id), "test_order_id": "1"})
    assert fake_onvo.send(client, tid, "checkout-session.succeeded", {"id": sid}).status_code == 200
    assert fake_onvo.send(client, tid, "payment-intent.succeeded", {"id": f"pi_{sid}"}).status_code == 200
    assert (
        fake_onvo.send(client, tid, "payment-intent.failed", {"id": f"pi_{sid}", "metadata": {"order_id": str(real.id), "crimson_env": "test"}}).status_code
        == 200
    )
    db_session.refresh(real)
    assert real.status == "nuevo" and not any("ONVO rechazó" in (h.get("note") or "") for h in (real.history or []))
    # y sin la marca de pruebas el camino real sigue funcionando (evento nuevo: los de arriba ya se consumieron)
    fake_onvo.sessions[sid]["metadata"].pop("crimson_env")
    fake_onvo.connect(client, secret=fake_onvo.key, client_id=fake_onvo.public, webhook_secret=fake_onvo.live_wh)
    assert fake_onvo.signed(client, live_url, "checkout_session.succeeded", obj, fake_onvo.live_wh, mode="test", evt_id="evt_l2").json() == {"ok": True}
    db_session.refresh(real)
    assert real.status == "pagado"


# ---------------------------------------------------------------- factura de prueba en Hacienda (Pruebas)
def test_factura_de_prueba_en_hacienda_desde_un_pedido_pagado(client, auth, fake_onvo, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):  # noqa: F811
    from app.models import EInvoiceDocument, EmailOutbox, Invoice, Tenant
    from app.providers.hacienda import xml44
    from app.services import hacienda_config as hc

    fake = _listo(client, monkeypatch, p12_empresa)
    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    modo = hc.hacienda_mode(db_session, t)
    fake_onvo.connect_test(client)
    o = _new(client, "sinpe_onvo")
    body = {"admin_password": ADMIN_PASSWORD, "cabys": CABYS, "confirmo": True}
    r = client.post(f"{BASE}/orders/{o['id']}/factura-prueba", json=body)
    assert r.status_code == 409  # no pagado
    fake_onvo.pay(o["sessions"][0], method="mobile_number")  # SINPE Movil de prueba (+506 8888 8888)
    assert _consult(client, o["id"])["paid_method"] == "sinpe_onvo"
    antes = _real_counts(db_session)
    correos = db_session.scalar(select(func.count()).select_from(EmailOutbox))
    assert client.post(f"{BASE}/orders/{o['id']}/factura-prueba", json={**body, "admin_password": "otra"}).status_code == 403
    assert client.post(f"{BASE}/orders/{o['id']}/factura-prueba", json={**body, "confirmo": False}).status_code == 422
    r = client.post(f"{BASE}/orders/{o['id']}/factura-prueba", json=body)
    assert r.status_code == 200, r.text
    v = r.json()
    run = v["hacienda"]
    assert run["origin"] == "tienda_pruebas" and run["consecutive"].startswith("00100001") and run["consecutive"][8:10] == "01" and run["state"] == "enviado"
    assert run["amount"].startswith("113") and not run["con_mr"]
    assert any("Factura de prueba enviada" in e["what"] for e in v["timeline"])
    root = xml44.parse_xml_bytes(fake.bodies()[0])
    assert xml44.text(root, "DetalleServicio", "LineaDetalle", "Detalle") == "Pedido de prueba PRB-0001"
    assert xml44.dec(xml44.text(root, "ResumenFactura", "TotalComprobante")) == 113
    assert xml44.text(root, "ResumenFactura", "MedioPago", "TipoMedioPago") == "06"
    assert xml44.text(root, "Receptor", "Identificacion", "Numero") == xml44.text(root, "Emisor", "Identificacion", "Numero")
    # solo cambio el T01 (numeracion de pruebas): F01, facturas, documentos fiscales, correos y el modo, iguales
    despues = _real_counts(db_session)
    t01 = lambda c: [x for x in c["consecutivos"] if x[1] != "T01"]  # noqa: E731
    assert t01(despues) == t01(antes) and {k: v for k, v in despues.items() if k != "consecutivos"} == {k: v for k, v in antes.items() if k != "consecutivos"}
    assert db_session.scalar(select(func.count()).select_from(EmailOutbox)) == correos
    assert (
        db_session.scalar(select(func.count()).select_from(Invoice)) == antes["invoice"]
        and db_session.scalar(select(func.count()).select_from(EInvoiceDocument)) == antes["einvoice_document"]
    )
    db_session.refresh(t)
    assert hc.hacienda_mode(db_session, t) == modo and fake.production_hits == []
    # leer el pedido nunca llama a Hacienda; consultar si (POST explicito)
    gets = len(fake.gets)
    _order(client, o["id"])
    client.get(BASE)
    assert len(fake.gets) == gets
    reloj.avanza(5)
    v = client.post(f"{BASE}/orders/{o['id']}/factura-prueba/consultar").json()
    assert v["hacienda"]["state"] == "aceptado" and len(fake.gets) == gets + 1
    # una sola factura por pedido; aparece en la lista de Ajustes con su origen
    assert client.post(f"{BASE}/orders/{o['id']}/factura-prueba", json=body).status_code == 409
    lista = client.get("/settings/hacienda/prueba-real").json()["runs"]
    assert lista[0]["origin"] == "tienda_pruebas"


def test_factura_de_prueba_respeta_la_prueba_de_ajustes_en_curso(client, auth, fake_onvo, db_session, monkeypatch, p12_empresa, reloj, cabys_ok):  # noqa: F811
    _listo(client, monkeypatch, p12_empresa)
    fake_onvo.connect_test(client)
    o = _new(client)
    fake_onvo.pay(o["sessions"][0])
    _consult(client, o["id"])
    r = client.post("/settings/hacienda/prueba-real", json={"admin_password": ADMIN_PASSWORD, "cabys": CABYS, "con_mensaje_receptor": True, "confirmo": True})
    assert r.status_code == 200 and r.json()["origin"] == "ajustes"
    r = client.post(f"{BASE}/orders/{o['id']}/factura-prueba", json={"admin_password": ADMIN_PASSWORD, "cabys": CABYS, "confirmo": True})
    assert r.status_code == 409 and "en curso" in r.json()["detail"]


def test_respaldo_de_la_empresa_sin_las_llaves(client, auth, fake_onvo):
    import zipfile
    from io import BytesIO

    fake_onvo.connect(client, webhook_secret=fake_onvo.live_wh)
    fake_onvo.connect_test(client)
    r = client.get("/settings/data-export")
    assert r.status_code == 200
    z = zipfile.ZipFile(BytesIO(r.content))
    for name, cols in (
        ("onvo_test_config", ("secret_enc", "webhook_secret_enc")),
        ("payment_gateway_config", ("secret_encrypted", "webhook_secret_encrypted")),
    ):
        doc = json.loads(z.read(f"data/{name}.json"))
        for c in cols:
            i = doc["columns"].index(c)
            assert all(row[i] is None for row in doc["rows"]) and doc["rows"], (name, c)
