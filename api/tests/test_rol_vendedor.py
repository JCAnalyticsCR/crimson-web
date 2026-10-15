"""Rol vendedor al 100 % (reunion con Andres 13/10, 32:25-35:57).

- Lo suyo = vendedor responsable (seller_id), no quien tecleo: la cotizacion que la oficina genera desde el
  levantamiento es de la vendedora de la oportunidad, y la factura hereda su vendedor (y su comision).
- Lo ajeno no existe: 404 (nunca 403), ni en listas, buscadores, conteos ni totales.
- El costo no sale hacia el vendedor: ni en campos, ni en motivos, ni en notas, ni en el resumen del levantamiento.
- Embudo propio (filas y totales) en la moneda de la empresa, "Ganado este mes" y el mismo numero en el inicio.
- Levantamientos: los solicita desde la oportunidad y los ve en resumen; no los llena.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.deps import ROLE_PERMISSIONS
from app.models import AuditLog, EmailOutbox, ExchangeRate, Invoice, Opportunity, Quote, Survey, SurveyItem

from .test_session4 import _user_with_role
from .test_tienda_checkout import SLUG, _body, _shop

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")
COSTO = ("cost", "price", "margin", "tarifa", "per_diem", "transport", "supplier")


def _entrar(client, db_session, tid, email, role):
    """Usuario con ese rol y su encabezado de sesion; el admin sigue logueado en el cliente."""
    pw = _user_with_role(db_session, tid, email, role)
    admin = client.headers.pop("Authorization", None)
    r = client.post("/auth/login", json={"email": email, "password": pw})
    client.headers["Authorization"] = admin
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(client, h=None) -> int:
    return client.get("/auth/me", headers=h or {}).json()["user"]["id"]


def _vendedores(client, db_session, auth):
    tid = auth["tenant"]["id"]
    a = _entrar(client, db_session, tid, "vendedora.a@ejemplo.com", "ventas")
    b = _entrar(client, db_session, tid, "vendedor.b@ejemplo.com", "ventas")
    return a, b


def _cliente(client, name="Empresa Ejemplo"):
    r = client.post("/customers", json={"name": name, "id_type": "juridica", "id_number": "3-101-000000", "email": "compras@ejemplo.com"})
    assert r.status_code == 201, r.text
    return r.json()


def _linea(precio=100000):
    return [{"name": "Servicio de instalacion", "quantity": 1, "unit_price": precio, "tax_rate": 13}]


def _D(v) -> Decimal:
    return Decimal(str(v))


def _seq(number: str) -> int:
    return int(re.search(r"(\d+)$", number).group(1))


def _keys(obj, path=""):
    """Todas las claves de un JSON anidado (para buscar cualquier rastro de costos)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _keys(x)


# ---------- T1-T4: embudo propio, inicio y moneda ----------
def test_embudo_del_vendedor_filas_y_totales(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    admin_id = _uid(client)
    client.post("/opportunities", json={"title": "Grande del admin", "amount": 1000000, "probability": 30})
    mia = client.post("/opportunities", json={"title": "Mia", "amount": 200000, "probability": 50}, headers=a).json()
    client.post("/opportunities", json={"title": "Del otro", "amount": 300000}, headers=b)

    filas = client.get("/opportunities", headers=a).json()
    assert [o["id"] for o in filas] == [mia["id"]]
    for qs in ("", f"?owner_id={admin_id}"):  # pedir otro responsable no abre nada
        board = client.get(f"/opportunities/board{qs}", headers=a).json()
        assert _D(board["total"]) == 200000 and _D(board["weighted"]) == 100000
        assert sum(c["count"] for c in board["columns"]) == 1
    assert _D(client.get("/opportunities/board").json()["total"]) == 1500000
    # el selector de responsables es de quien asigna
    assert client.get("/opportunities/meta/config", headers=a).json()["sellers"] == []
    assert client.get("/opportunities/meta/config").json()["sellers"]


def test_oportunidad_sin_responsable_es_404_para_el_vendedor(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    tid = auth["tenant"]["id"]
    lead = Opportunity(tenant_id=tid, number="OPO-LEAD-1", title="Solicitud desde la tienda", owner_id=None, kind="proyecto")
    db_session.add(lead)
    db_session.commit()
    oid = lead.id
    assert client.get(f"/opportunities/{oid}", headers=a).status_code == 404
    assert client.put(f"/opportunities/{oid}", json={"title": "Tomada"}, headers=a).status_code == 404
    assert client.post(f"/opportunities/{oid}/touch", json={"note": "Llamé"}, headers=a).status_code == 404
    assert client.post(f"/opportunities/{oid}/quote", json={}, headers=a).status_code == 404
    assert client.post(f"/opportunities/{oid}/request-survey", json={}, headers=a).status_code == 404
    assert client.get(f"/opportunities/{oid}/allies", headers=a).status_code == 404
    assert oid not in {o["id"] for o in client.get("/opportunities", headers=a).json()}
    assert client.get(f"/opportunities/{oid}").status_code == 200


def test_inicio_del_vendedor_coincide_con_el_embudo(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    o1 = client.post("/opportunities", json={"title": "Uno", "amount": 250000, "probability": 40}, headers=a).json()
    client.post("/opportunities", json={"title": "Dos", "amount": 150000, "probability": 80}, headers=a)
    client.post("/opportunities", json={"title": "Del admin", "amount": 999999})

    dash = client.get("/dashboard", headers=a).json()
    board = client.get("/opportunities/board", headers=a).json()
    assert dash["scope"] == "mine" and dash["gerencia"] is None
    emb = dash["mi_embudo"]
    assert _D(emb["monto"]) == _D(board["total"]) == 400000
    assert _D(emb["ponderado"]) == _D(board["weighted"])
    assert emb["abiertas"] == sum(c["count"] for c in board["columns"]) == 2
    assert emb["ganadas_mes"] == 0 and board["won_month"]["count"] == 0 and emb["moneda"] == "CRC" and emb["convertido"] is False

    r = client.post(f"/opportunities/{o1['id']}/touch", json={"note": "Firmó el contrato", "status": "ganada"}, headers=a)
    assert r.status_code == 200 and r.json()["closed_at"]
    dash = client.get("/dashboard", headers=a).json()["mi_embudo"]
    board = client.get("/opportunities/board", headers=a).json()
    assert dash["ganadas_mes"] == 1 and _D(dash["ganado_mes"]) == 250000
    assert board["won_month"]["count"] == 1 and _D(board["won_month"]["amount"]) == 250000
    assert _D(dash["monto"]) == _D(board["total"]) == 150000
    # reabrir borra la fecha de cierre
    r = client.post(f"/opportunities/{o1['id']}/touch", json={"note": "Se cayó la firma", "status": "negociacion"}, headers=a)
    assert r.json()["closed_at"] is None
    assert client.get("/dashboard", headers=a).json()["mi_embudo"]["ganadas_mes"] == 0
    # el admin (dashboard.empresa) no lleva el bloque personal
    assert client.get("/dashboard").json()["mi_embudo"] is None


def test_embudo_convierte_dolares(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    fx = db_session.scalar(select(ExchangeRate).where(ExchangeRate.currency == "USD", ExchangeRate.date == date.today()))
    if fx:
        fx.sell, fx.buy = Decimal(500), Decimal(490)
    else:
        db_session.add(ExchangeRate(currency="USD", sell=Decimal(500), buy=Decimal(490), date=date.today()))
    db_session.commit()
    client.post("/opportunities", json={"title": "En dolares", "amount": 100, "currency": "USD"}, headers=a)
    client.post("/opportunities", json={"title": "En colones", "amount": 10000}, headers=a)
    board = client.get("/opportunities/board", headers=a).json()
    assert _D(board["total"]) == 60000 and board["converted"] is True and board["currency"] == "CRC"
    assert client.get("/dashboard", headers=a).json()["mi_embudo"]["convertido"] is True
    # "Mi facturacion" tambien suma en colones: una factura en dolares cuenta con su tipo de cambio
    cust = _cliente(client)
    r = client.post("/invoices", json={"customer_id": cust["id"], "currency": "USD", "fx_sell": 500, "lines": _linea(100)}, headers=a)
    assert r.status_code == 201, r.text
    client.post("/invoices", json={"customer_id": cust["id"], "lines": _linea(1000)}, headers=a)
    mes = _D(client.get("/dashboard", headers=a).json()["facturado"]["mes"])
    assert mes == Decimal(113) * 500 + Decimal(1130)


# ---------- T5-T9: vendedor responsable ----------
def _cotizacion_desde_levantamiento(client, db_session, a):
    """Oportunidad de A -> levantamiento del admin -> revision -> costeo -> cotizacion (la genera el admin)."""
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "CCTV bodega", "kind": "proyecto", "customer_id": cust["id"], "amount": 500000}, headers=a).json()
    s = client.post(
        "/surveys",
        json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": opp["id"], "items": [{"name": "Gabinete 15U", "quantity": 1, "kind": "equipo"}]},
    ).json()
    assert client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": s["items"][0]["id"], "unit_cost": "50000"}]).status_code == 200
    assert client.post(f"/surveys/{s['id']}/send").status_code == 200
    assert client.post(f"/surveys/{s['id']}/review", json={"action": "aprobar"}).status_code == 200
    r = client.post(f"/surveys/{s['id']}/quote", json={"margin": 35})
    assert r.status_code == 201, r.text
    return opp, s, r.json()["quote_id"]


def test_cotizacion_desde_levantamiento_es_de_la_vendedora(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    a_id, admin_id = _uid(client, a), _uid(client)
    opp, s, qid = _cotizacion_desde_levantamiento(client, db_session, a)
    q = db_session.get(Quote, qid)
    assert q.seller_id == a_id and q.created_by == admin_id
    assert client.get(f"/quotes/{qid}").json()["seller_name"]
    assert qid in {x["id"] for x in client.get("/quotes", headers=a).json()["items"]}
    mine = client.get(f"/quotes/{qid}", headers=a)
    assert mine.status_code == 200
    notas = mine.json()["internal_notes"] or ""
    assert "Costo estimado" not in notas and "margen" not in notas
    assert client.get(f"/quotes/{qid}", headers=b).status_code == 404
    assert client.get(f"/quotes/{qid}/pdf", headers=b).status_code == 404
    # le llega el aviso, sin montos
    a_mail = client.get("/auth/me", headers=a).json()["user"]["email"]
    mails = db_session.scalars(select(EmailOutbox).where(EmailOutbox.to == a_mail, EmailOutbox.entity == "quote", EmailOutbox.entity_id == qid)).all()
    assert len(mails) == 1 and f"/cotizaciones/{qid}" in mails[0].html and "₡" not in mails[0].html
    # la auditoria guarda el costo y el margen (no las notas)
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "quote", AuditLog.entity == "survey", AuditLog.entity_id == s["id"]))
    assert "margin_pct" in log.diff and "cost" in log.diff


def test_convertir_hereda_vendedor(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    a_id = _uid(client, a)
    _, _, qid = _cotizacion_desde_levantamiento(client, db_session, a)
    r = client.post(f"/quotes/{qid}/convert")
    assert r.status_code == 201, r.text
    inv = r.json()
    assert db_session.get(Invoice, inv["id"]).seller_id == a_id
    assert inv["id"] in {x["id"] for x in client.get("/invoices", headers=a).json()["items"]}
    assert client.get(f"/invoices/{inv['id']}", headers=a).status_code == 200
    assert client.get(f"/invoices/{inv['id']}/pdf", headers=a).status_code == 200
    assert client.get(f"/invoices/{inv['id']}", headers=b).status_code == 404
    # la comision es de la vendedora aunque la factura la haya hecho el admin
    client.post("/commissions/rules", json={"user_id": a_id, "name": "Vendedora A", "base": "venta", "percent": 5})
    assert client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "amount": 1000}).status_code == 201
    rows = client.get("/commissions").json()["rows"]
    assert rows and all(x["user_id"] == a_id for x in rows)
    assert client.get("/commissions", headers=a).json()["rows"]
    assert client.get("/commissions", headers=b).json()["rows"] == []


def test_reasignar_mueve_cotizacion_abierta_no_la_factura(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    a_id, b_id = _uid(client, a), _uid(client, b)
    cust = _cliente(client)
    abierta = client.post("/opportunities", json={"title": "Abierta", "kind": "venta", "customer_id": cust["id"]}, headers=a).json()
    q1 = client.post(f"/opportunities/{abierta['id']}/quote", json={}, headers=a).json()
    assert db_session.get(Quote, q1["quote_id"]).seller_id == a_id
    assert client.post(f"/opportunities/{abierta['id']}/assign", json={"owner_id": b_id}).status_code == 200
    assert client.get(f"/quotes/{q1['quote_id']}", headers=a).status_code == 404
    assert client.get(f"/quotes/{q1['quote_id']}", headers=b).status_code == 200

    facturada = client.post("/opportunities", json={"title": "Facturada", "kind": "venta", "customer_id": cust["id"]}, headers=a).json()
    q2 = client.post(f"/opportunities/{facturada['id']}/quote", json={}, headers=a).json()
    assert client.put(f"/quotes/{q2['quote_id']}", json={"customer_id": cust["id"], "lines": _linea()}, headers=a).status_code == 200
    inv = client.post(f"/quotes/{q2['quote_id']}/convert", headers=a).json()
    assert client.post(f"/opportunities/{facturada['id']}/assign", json={"owner_id": b_id}).status_code == 200
    assert db_session.get(Invoice, inv["id"]).seller_id == a_id  # lo facturado no se mueve (ni su comision)
    assert db_session.get(Quote, q2["quote_id"]).seller_id == a_id
    assert client.get(f"/invoices/{inv['id']}", headers=a).status_code == 200


def test_busqueda_solo_lo_suyo(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    uno = _cliente(client, "Cliente Uno")
    dos = _cliente(client, "Cliente Dos")
    mia = client.post("/invoices", json={"customer_id": uno["id"], "lines": _linea()}, headers=a).json()
    ajena = client.post("/invoices", json={"customer_id": uno["id"], "lines": _linea()}).json()
    client.post("/invoices", json={"customer_id": dos["id"], "lines": _linea()})

    assert client.get("/invoices", params={"q": ajena["number"]}, headers=a).json()["items"] == []
    por_cliente = client.get("/invoices", params={"q": "Cliente Uno"}, headers=a).json()["items"]
    assert [x["id"] for x in por_cliente] == [mia["id"]]
    assert client.get("/invoices", params={"q": "%"}, headers=a).json()["items"] == []  # % literal, no "todo"
    assert client.get("/invoices", params={"q": "_"}, headers=a).json()["items"] == []
    assert len(client.get("/invoices", params={"q": "Cliente Uno"}).json()["items"]) == 2  # el admin ve las dos
    # "4" o "004": el que termina exactamente en ese consecutivo va primero
    n = str(_seq(mia["number"]))
    assert client.get("/invoices", params={"q": n.zfill(3)}, headers=a).json()["items"][0]["id"] == mia["id"]
    hits = client.get("/search", params={"q": ajena["number"]}, headers=a).json()
    assert all(h["id"] != ajena["id"] for h in hits if h["kind"] == "factura")
    assert client.get(f"/invoices/{ajena['id']}", headers=a).status_code == 404  # nunca 403: no delata que existe
    # columna Vendedor
    assert client.get("/invoices", params={"q": "Cliente Uno"}).json()["items"][0]["seller_name"]


def test_consecutivo_global_entre_vendedores(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    cust = _cliente(client)
    for path in ("/quotes", "/invoices"):
        n1 = client.post(path, json={"customer_id": cust["id"], "lines": _linea()}, headers=a).json()["number"]
        n2 = client.post(path, json={"customer_id": cust["id"], "lines": _linea()}, headers=b).json()["number"]
        n3 = client.post(path, json={"customer_id": cust["id"], "lines": _linea()}, headers=a).json()["number"]
        assert _seq(n2) == _seq(n1) + 1 and _seq(n3) == _seq(n1) + 2, (path, n1, n2, n3)


def test_admin_asigna_el_vendedor_del_documento(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    a_id, b_id = _uid(client, a), _uid(client, b)
    cust = _cliente(client)
    q = client.post("/quotes", json={"customer_id": cust["id"], "seller_id": a_id, "lines": _linea()}).json()
    assert q["seller_id"] == a_id and client.get(f"/quotes/{q['id']}", headers=a).status_code == 200
    # el vendedor no se asigna documentos a otro: el campo se ignora
    suya = client.post("/quotes", json={"customer_id": cust["id"], "seller_id": b_id, "lines": _linea()}, headers=a).json()
    assert suya["seller_id"] == a_id
    # cambiarlo desde el editor (solo admin) y validar que sea del equipo activo
    r = client.put(f"/quotes/{q['id']}", json={"customer_id": cust["id"], "seller_id": b_id, "lines": _linea()})
    assert r.status_code == 200 and r.json()["seller_id"] == b_id
    assert client.get(f"/quotes/{q['id']}", headers=a).status_code == 404
    assert client.put(f"/quotes/{q['id']}", json={"customer_id": cust["id"], "seller_id": 99999, "lines": _linea()}).status_code == 422
    # duplicar conserva el vendedor
    dup = client.post(f"/quotes/{q['id']}/duplicate").json()
    assert dup["seller_id"] == b_id


# ---------- T10-T11: el costo no sale ni dentro de un texto ----------
def test_motivo_de_aprobacion_sin_margen_para_el_vendedor(client, auth, db_session):
    from app.models import Product

    a, _ = _vendedores(client, db_session, auth)
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    # a precio de catalogo (sin descuento), con un costo que deja el margen bajo el minimo
    db_session.get(Product, cam["id"]).cost = Decimal("120")  # USD
    db_session.commit()
    flaca = client.post("/quotes", headers=a, json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "quantity": 1}]}).json()
    assert flaca["status"] == "por_aprobar"
    motivo = flaca["approval_reason"]
    assert "margen" in motivo and not re.search(r"\d+(?:[.,]\d+)? ?%", motivo)
    assert not re.search(r"\d+(?:[.,]\d+)? ?%", client.get(f"/quotes/{flaca['id']}", headers=a).json()["approval_reason"])
    assert re.search(r"\d+(?:[.,]\d+)? ?%", client.get(f"/quotes/{flaca['id']}").json()["approval_reason"])  # el admin ve la cifra
    # un motivo viejo con el margen exacto tampoco le llega
    db_session.get(Quote, flaca["id"]).approval_reason = "El margen queda en 9.0 % y el mínimo es 25 %."
    db_session.commit()
    assert "9.0" not in client.get(f"/quotes/{flaca['id']}", headers=a).json()["approval_reason"]
    assert "9.0" in client.get(f"/quotes/{flaca['id']}").json()["approval_reason"]
    # el tope de monto se sigue viendo completo
    grande = client.post("/quotes", headers=a, json={"customer_id": cust["id"], "lines": _linea(2500000)}).json()
    assert grande["status"] == "por_aprobar" and "supera el tope" in grande["approval_reason"]


def test_patron_de_nota_de_costo():
    from app.routers.fieldwork import COST_NOTE_RE

    limpio = lambda t: COST_NOTE_RE.sub("", t).strip()  # noqa: E731
    assert limpio("Levantamiento LEV-2026-0001. Costo estimado ₡1,234,567 · margen 35.0 %.") == "Levantamiento LEV-2026-0001."
    assert limpio("Levantamiento LEV-2026-0002. Costo estimado ₡0 · margen None %.") == "Levantamiento LEV-2026-0002."
    assert limpio("Levantamiento LEV-2026-0003. Costo estimado ₡10 · margen -5.2 %.") == "Levantamiento LEV-2026-0003."
    assert limpio("Cliente pide factura a 30 días") == "Cliente pide factura a 30 días"
    # la migracion usa el mismo patron
    assert _migracion().COST_NOTE_RE.pattern == COST_NOTE_RE.pattern


# ---------- T12-T13: levantamientos en resumen y solicitud ----------
def test_vendedor_ve_resumen_de_levantamientos_de_sus_oportunidades(client, auth, db_session):
    tid = auth["tenant"]["id"]
    a, b = _vendedores(client, db_session, auth)
    t = _entrar(client, db_session, tid, "tecnico.campo@ejemplo.com", "tecnico")
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "Bodega", "customer_id": cust["id"]}, headers=a).json()
    foto = client.post("/media?visibility=private&module=field", files={"file": ("p.png", PNG, "image/png")}, headers=t).json()
    # sin solicitud, el tecnico (no ve el embudo) no la liga: 404, no recorre oportunidades ajenas
    assert client.post("/surveys", json={"kind": "cctv", "opportunity_id": opp["id"]}, headers=t).status_code == 404
    # A lo solicita: la solicitud pendiente es la orden de visita del equipo de campo y el tecnico la liga
    assert client.post(f"/opportunities/{opp['id']}/request-survey", json={}, headers=a).status_code == 200
    s = client.post(
        "/surveys",
        headers=t,
        json={
            "kind": "cctv",
            "customer_id": cust["id"],
            "opportunity_id": opp["id"],
            "notes": "Acceso por el porton trasero",
            "points": [{"code": "CAM-01", "label": "Entrada", "data": {"tipo": "Bullet", "resolucion": "4 MP", "distancia_m": 30}, "photos": [foto["url"]]}],
            "items": [{"name": "Camara domo 4 MP", "quantity": 6, "kind": "equipo"}, {"name": "Cable UTP", "quantity": 100, "unit": "m"}],
        },
    )
    assert s.status_code == 201, s.text
    s = s.json()
    it = db_session.scalar(select(SurveyItem).where(SurveyItem.survey_id == s["id"], SurveyItem.kind == "equipo"))
    it.supplier_cost, it.supplier_currency, it.unit_cost = Decimal("35"), "USD", Decimal("18000")
    db_session.commit()

    assert s["id"] in {x["id"] for x in client.get("/surveys", headers=a).json()}
    r = client.get(f"/surveys/{s['id']}/summary", headers=a)
    assert r.status_code == 200, r.text
    res = r.json()
    malas = [k for k in _keys(res) if any(x in k.lower() for x in COSTO)]
    assert malas == [], malas
    assert res["paso"] == "borrador" and res["paso_texto"] and res["opportunity"]["id"] == opp["id"]
    assert [x["kind"] for x in res["equipos"]] == ["equipo", "material"]
    assert res["puntos"][0]["photos"] and "?e=" in res["puntos"][0]["photos"][0] and "&s=" in res["puntos"][0]["photos"][0]
    assert res["notes"] == "Acceso por el porton trasero" and res["cotizacion"] is None
    # GET /surveys/{id} tampoco le da el registro completo (data tecnico, seccion comun, proveedor): el mismo resumen
    full = client.get(f"/surveys/{s['id']}", headers=a)
    assert full.status_code == 200
    llaves = set(_keys(full.json()))
    assert not {"data", "common", "points", "items"} & llaves and not [k for k in llaves if any(x in k.lower() for x in COSTO)]
    assert full.json()["equipos"] and full.json()["paso"] == "borrador"
    assert "data" in client.get(f"/surveys/{s['id']}", headers=t).json()["points"][0]  # el tecnico sigue con el editor
    # solo lectura: nada de lo que escribe
    assert client.put(f"/surveys/{s['id']}", json={"kind": "cctv"}, headers=a).status_code == 403
    assert client.post(f"/surveys/{s['id']}/suggest", headers=a).status_code == 403
    assert client.get(f"/surveys/{s['id']}/costing", headers=a).status_code == 403
    assert client.post("/surveys", json={"kind": "cctv"}, headers=a).status_code == 403
    # el otro vendedor no lo ve
    assert s["id"] not in {x["id"] for x in client.get("/surveys", headers=b).json()}
    assert client.get(f"/surveys/{s['id']}/summary", headers=b).status_code == 404
    # sin tecnico ni oportunidad de A: 404 para A
    suelto = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"]}).json()
    db_session.get(Survey, suelto["id"]).technician_id = None
    db_session.commit()
    assert client.get(f"/surveys/{suelto['id']}/summary", headers=a).status_code == 404
    assert client.get(f"/surveys/{suelto['id']}", headers=a).status_code == 404
    # el tecnico sigue viendo los suyos (y el que quedo sin tecnico, como siempre)
    ids_t = {x["id"] for x in client.get("/surveys", headers=t).json()}
    assert s["id"] in ids_t
    assert client.get(f"/surveys/{suelto['id']}", headers=t).status_code == 200
    # desligado de la oportunidad deja de verse
    assert client.post(f"/surveys/{s['id']}/opportunity", json={"opportunity_id": None}).status_code == 200
    assert client.get(f"/surveys/{s['id']}/summary", headers=a).status_code == 404


def test_resumen_con_cotizacion_de_la_vendedora(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    _, s, qid = _cotizacion_desde_levantamiento(client, db_session, a)
    res = client.get(f"/surveys/{s['id']}/summary", headers=a).json()
    assert res["paso"] == "cotizado" and res["cotizacion"]["id"] == qid and res["sugerencias_pendientes"] == []


def test_solicitar_levantamiento(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "Porton trasero", "kind": "proyecto", "customer_id": cust["id"], "amount": 750000}, headers=a).json()
    r = client.post(f"/opportunities/{opp['id']}/request-survey", json={"note": "Portón trasero", "preferred_date": "2026-11-02"}, headers=a)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "requiere_visita" and "Portón trasero" in out["notes"] and "02/11/2026" in out["notes"] and out["notified"] >= 1
    mails = db_session.scalars(select(EmailOutbox).where(EmailOutbox.entity == "opportunity", EmailOutbox.entity_id == opp["id"])).all()
    assert mails and all("₡" not in m.html and "750" not in m.html for m in mails)
    assert f"/levantamientos?nuevo={opp['id']}" in mails[0].html
    # no se repite en 24 h
    assert client.post(f"/opportunities/{opp['id']}/request-survey", json={}, headers=a).status_code == 409
    # una de solo venta no lleva levantamiento
    venta = client.post("/opportunities", json={"title": "Solo equipo", "kind": "venta"}, headers=a).json()
    assert client.post(f"/opportunities/{venta['id']}/request-survey", json={}, headers=a).status_code == 409
    # con levantamiento vivo
    otra = client.post("/opportunities", json={"title": "Ya levantada", "customer_id": cust["id"]}, headers=a).json()
    client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": otra["id"]})
    r = client.post(f"/opportunities/{otra['id']}/request-survey", json={}, headers=a)
    assert r.status_code == 409 and "LEV" in r.json()["detail"]
    # cerrada
    cerrada = client.post("/opportunities", json={"title": "Perdida"}, headers=a).json()
    client.post(f"/opportunities/{cerrada['id']}/touch", json={"note": "No siguió", "status": "perdida"}, headers=a)
    assert client.post(f"/opportunities/{cerrada['id']}/request-survey", json={}, headers=a).status_code == 409
    # ajena
    assert client.post(f"/opportunities/{opp['id']}/request-survey", json={}, headers=b).status_code == 404


# ---------- T14-T16: ficha del cliente, archivo, cobros y pedidos ----------
def test_vendedor_no_ve_oportunidades_ajenas_en_cliente_ni_archivo(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    cust = _cliente(client)
    mia = client.post("/opportunities", json={"title": "Mia", "customer_id": cust["id"]}, headers=a).json()
    ajena = client.post("/opportunities", json={"title": "Ajena", "customer_id": cust["id"]}).json()
    assert client.post(f"/archive/opportunity/{ajena['id']}/archive").status_code == 200
    ov = {x["id"] for x in client.get(f"/customers/{cust['id']}/overview", headers=a).json()["opportunities"]}
    assert mia["id"] in ov and ajena["id"] not in ov
    ids = {r["id"] for r in client.get("/archive", params={"state": "archivado"}, headers=a).json()["rows"] if r["kind"] == "opportunity"}
    assert ajena["id"] not in ids
    assert client.post(f"/archive/opportunity/{ajena['id']}/trash", headers=a).status_code == 404
    assert client.post(f"/archive/opportunity/{mia['id']}/trash", headers=a).status_code == 200
    # una sin responsable tampoco es suya en el archivo
    lead = Opportunity(tenant_id=auth["tenant"]["id"], number="OPO-LEAD-2", title="Lead", owner_id=None)
    db_session.add(lead)
    db_session.commit()
    assert client.post(f"/archive/opportunity/{lead.id}/archive", headers=a).status_code == 404


def test_cobros_de_mis_facturas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    a, _ = _vendedores(client, db_session, auth)
    caja = _entrar(client, db_session, tid, "caja.mostrador@ejemplo.com", "caja")
    cust = _cliente(client)
    mia = client.post("/invoices", json={"customer_id": cust["id"], "lines": _linea()}, headers=a).json()
    del_admin = client.post("/invoices", json={"customer_id": cust["id"], "lines": _linea()}).json()
    p1 = client.post(f"/invoices/{mia['id']}/payments", json={"method": "efectivo", "amount": 5000}, headers=caja)
    p2 = client.post(f"/invoices/{del_admin['id']}/payments", json={"method": "efectivo", "amount": 7000}, headers=caja)
    assert p1.status_code == 201 and p2.status_code == 201
    pagos = client.get("/payments", headers=a).json()
    assert {x["invoice_id"] for x in pagos} == {mia["id"]}


def test_pedidos_de_la_tienda_visibles_para_el_vendedor(client, auth, db_session):
    a, b = _vendedores(client, db_session, auth)
    prods = _shop(client)
    client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"]))
    lista = client.get("/store-orders", headers=a)
    assert lista.status_code == 200 and lista.json()["items"]
    oid = lista.json()["items"][0]["id"]
    assert client.get(f"/store-orders/{oid}", headers=a).status_code == 200
    r = client.post(f"/orders/{oid}/invoice", params={"doc_type": "FE"}, headers=a)
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.seller_id is None  # venta de la tienda: sin vendedor (no comisiona)
    # la factura la ve quien la hizo (sin vendedor, la tecleo ella); al otro vendedor le sale sin enlace
    assert client.get(f"/invoices/{inv.id}", headers=a).status_code == 200
    assert client.get(f"/store-orders/{oid}", headers=a).json()["invoice_visible"] is True
    assert client.get(f"/store-orders/{oid}", headers=b).json()["invoice_visible"] is False
    assert client.get("/store-orders", headers=b).json()["items"][0]["invoice_visible"] is False
    assert client.get(f"/invoices/{inv.id}", headers=b).status_code == 404


# ---------- T19-T20: permisos y migracion ----------
def test_menu_y_permisos_del_vendedor(client, auth):
    m = client.get("/auth/roles").json()["matrix"]
    assert m["ventas"]["field"] == ["ver", "resumen"] and "crear" not in m["ventas"]["field"]
    assert ROLE_PERMISSIONS["ventas"]["field"] == ["ver", "resumen"]
    for r, perms in ROLE_PERMISSIONS.items():  # resumen es solo del vendedor; asignar vendedor, solo del admin
        if r not in ("admin", "ventas"):
            assert "resumen" not in perms.get("field", []), r
        if r != "admin":
            assert "asignar" not in perms.get("sales", []), r


def _migracion():
    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("vendedor_*.py"))
    spec = importlib.util.spec_from_file_location("vendedor_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migracion_relleno(client, auth, db_session):
    from app.models import Order

    a, _ = _vendedores(client, db_session, auth)
    a_id, admin_id = _uid(client, a), _uid(client)
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "Vieja", "customer_id": cust["id"]}, headers=a).json()
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": _linea()}).json()  # la hizo el admin
    suelta = client.post("/quotes", json={"customer_id": cust["id"], "lines": _linea()}, headers=a).json()
    inv_q = client.post(f"/quotes/{q['id']}/convert").json()
    inv_web = client.post("/invoices", json={"customer_id": cust["id"], "lines": _linea()}).json()
    db_session.add(Order(tenant_id=auth["tenant"]["id"], number="WEB-MIG-1", channel="tienda", invoice_id=inv_web["id"], total=1, currency="CRC"))
    o = db_session.get(Opportunity, opp["id"])
    o.quote_id = q["id"]
    ganada = client.post("/opportunities", json={"title": "Ganada vieja"}, headers=a).json()
    vieja = "Levantamiento LEV-2026-0007. Costo estimado ₡50,000 · margen 35.0 %."
    # como estaban antes de la migracion: sin vendedor ni fecha de cierre, y con el costo en las notas
    for x in (
        db_session.get(Quote, q["id"]),
        db_session.get(Quote, suelta["id"]),
        db_session.get(Invoice, inv_q["id"]),
        db_session.get(Invoice, inv_web["id"]),
    ):
        x.seller_id = None
    db_session.get(Quote, q["id"]).internal_notes = vieja
    db_session.get(Invoice, inv_q["id"]).internal_notes = vieja
    g = db_session.get(Opportunity, ganada["id"])
    g.status = "ganada"
    db_session.flush()
    g.closed_at = None
    db_session.commit()

    mod = _migracion()
    mod.backfill(db_session.connection())
    mod.backfill(db_session.connection())  # idempotente
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Quote, q["id"]).seller_id == a_id  # cotizacion de la oportunidad de A hecha por el admin
    assert db_session.get(Quote, suelta["id"]).seller_id == a_id  # sin oportunidad: quien la tecleo
    assert db_session.get(Invoice, inv_q["id"]).seller_id == a_id  # hereda de su cotizacion
    assert db_session.get(Invoice, inv_web["id"]).seller_id is None  # pedido web: sin vendedor
    assert db_session.get(Opportunity, ganada["id"]).closed_at is not None
    assert db_session.get(Quote, q["id"]).internal_notes == "Levantamiento LEV-2026-0007."
    assert db_session.get(Invoice, inv_q["id"]).internal_notes == "Levantamiento LEV-2026-0007."
    assert admin_id != a_id


# ---------- Revision: correcciones de los hallazgos ----------
def test_ligar_levantamiento_a_oportunidad_se_valida(client, auth, db_session):
    """La oportunidad de un levantamiento decide quien lo ve y de quien es su cotizacion: no se acepta un id cualquiera."""
    tid = auth["tenant"]["id"]
    a, b = _vendedores(client, db_session, auth)
    b_id = _uid(client, b)
    t = _entrar(client, db_session, tid, "tecnico.liga@ejemplo.com", "tecnico")
    sup = _entrar(client, db_session, tid, "supervisor.liga@ejemplo.com", "supervisor")
    sup_id = _uid(client, sup)
    cust = _cliente(client)
    de_a = client.post("/opportunities", json={"title": "Bodega A", "customer_id": cust["id"]}, headers=a).json()
    de_b = client.post("/opportunities", json={"title": "Oficina B", "customer_id": cust["id"]}, headers=b).json()

    # el tecnico no la cuela por PUT: se ignora (se liga solo con POST /surveys/{id}/opportunity)
    s = client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"]}, headers=t).json()
    r = client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": de_b["id"]}, headers=t)
    assert r.status_code == 200 and r.json()["opportunity_id"] is None
    assert s["id"] not in {x["id"] for x in client.get("/surveys", headers=b).json()}
    # ni por POST ni por el enlace a una que no ve y que ya no espera levantamiento (ya tiene uno, o va mas adelante)
    client.post("/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": de_b["id"]})
    assert client.post("/surveys", json={"kind": "cctv", "opportunity_id": de_b["id"]}, headers=t).status_code == 404
    assert client.post(f"/surveys/{s['id']}/opportunity", json={"opportunity_id": de_b["id"]}, headers=t).status_code == 404
    cotizando = client.post("/opportunities", json={"title": "Cotizando", "customer_id": cust["id"], "status": "cotizando"}, headers=b).json()
    assert db_session.get(Opportunity, cotizando["id"]).status == "cotizando"
    assert client.post("/surveys", json={"kind": "cctv", "opportunity_id": cotizando["id"]}, headers=t).status_code == 404
    # ni de otro cliente
    otro = _cliente(client, "Otra Empresa Ejemplo")
    libre = client.post("/opportunities", json={"title": "Libre", "customer_id": cust["id"]}, headers=b).json()
    assert client.post("/surveys", json={"kind": "cctv", "customer_id": otro["id"], "opportunity_id": libre["id"]}, headers=t).status_code == 404
    assert client.post("/surveys", json={"kind": "cctv", "customer_id": otro["id"], "opportunity_id": libre["id"]}).status_code == 409  # el admin la ve
    # cliente de otra empresa (o inexistente): 404, nunca guardado
    assert client.post("/surveys", json={"kind": "cctv", "customer_id": 999999}, headers=t).status_code == 404
    assert client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "customer_id": 999999}, headers=t).status_code == 404

    # «Solicitar levantamiento»: el supervisor (sin ver_todo del embudo) prellena y crea el de A, con el cliente
    assert client.post(f"/opportunities/{de_a['id']}/request-survey", json={}, headers=a).status_code == 200
    assert client.get(f"/opportunities/{de_a['id']}", headers=sup).status_code == 404  # no la ve en el embudo
    pre = client.get(f"/surveys/from-opportunity/{de_a['id']}", headers=sup)
    assert pre.status_code == 200 and pre.json()["customer_id"] == cust["id"] and "amount" not in pre.json()
    nuevo = client.post("/surveys", json={"kind": "cctv", "opportunity_id": de_a["id"]}, headers=sup)
    assert nuevo.status_code == 201, nuevo.text
    assert nuevo.json()["customer_id"] == cust["id"]  # lo toma de la oportunidad: cotizar despues no falla
    assert nuevo.json()["id"] in {x["id"] for x in client.get("/surveys", headers=a).json()}
    # ya no espera levantamiento: otro no se cuela
    assert client.get(f"/surveys/from-opportunity/{de_a['id']}", headers=sup).status_code == 404

    # ligar no reasigna la venta a escondidas: una cotizacion de A a la oportunidad del supervisor -> 409
    _, s_a, qid = _cotizacion_desde_levantamiento(client, db_session, a)
    del_sup = client.post("/opportunities", json={"title": "Del supervisor", "owner_id": sup_id}).json()
    assert client.post(f"/surveys/{s_a['id']}/opportunity", json={"opportunity_id": None}).status_code == 200
    r = client.post(f"/surveys/{s_a['id']}/opportunity", json={"opportunity_id": del_sup["id"]}, headers=sup)
    assert r.status_code == 409 and "otro vendedor" in r.json()["detail"]
    assert db_session.get(Quote, qid).seller_id == _uid(client, a)
    # ni a una de B que pidio levantamiento (del mismo cliente): tambien le pasaria la venta de A a B
    otra_b = client.post("/opportunities", json={"title": "Otra de B", "customer_id": s_a["customer_id"]}, headers=b).json()
    # sin solicitud pendiente el supervisor ni la encuentra: no es suya
    assert client.post(f"/surveys/{s_a['id']}/opportunity", json={"opportunity_id": otra_b["id"]}, headers=sup).status_code == 404
    assert client.post(f"/opportunities/{otra_b['id']}/request-survey", json={}, headers=b).status_code == 200
    r = client.post(f"/surveys/{s_a['id']}/opportunity", json={"opportunity_id": otra_b["id"]}, headers=sup)
    assert r.status_code == 409 and "otro vendedor" in r.json()["detail"]
    # el admin si reasigna al ligar
    assert client.post(f"/surveys/{s_a['id']}/opportunity", json={"opportunity_id": otra_b["id"]}).status_code == 200
    db_session.expire_all()
    assert db_session.get(Quote, qid).seller_id == b_id


def test_cotizar_levantamiento_de_otro_cliente_que_su_oportunidad(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    cust = _cliente(client)
    otro = _cliente(client, "Otra Empresa Ejemplo")
    opp = client.post("/opportunities", json={"title": "CCTV", "customer_id": cust["id"]}, headers=a).json()
    s = client.post(
        "/surveys", json={"kind": "cctv", "customer_id": cust["id"], "opportunity_id": opp["id"], "items": [{"name": "Gabinete 15U", "quantity": 1}]}
    ).json()
    # el cliente se corrige en el editor despues de ligar: cotizar asi le daria a A la venta de otro cliente
    body = {"kind": "cctv", "customer_id": otro["id"], "items": [{"id": s["items"][0]["id"], "name": "Gabinete 15U", "quantity": 1}]}
    assert client.put(f"/surveys/{s['id']}", json=body).status_code == 200
    r = client.post(f"/surveys/{s['id']}/quote", json={"margin": 35, "skip_review": True})
    assert r.status_code == 409 and opp["number"] in r.json()["detail"]
    assert db_session.get(Opportunity, opp["id"]).quote_id is None


def test_responsable_que_no_puede_ser_vendedor_no_se_lleva_la_cotizacion(client, auth, db_session):
    from app.models import User

    a, _ = _vendedores(client, db_session, auth)
    a_id, admin_id = _uid(client, a), _uid(client)
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "Venta", "kind": "venta", "customer_id": cust["id"]}, headers=a).json()
    db_session.get(User, a_id).active = False  # dada de baja: su membresia sigue activa (pipeline._check_owner la acepta)
    db_session.commit()
    q = client.post(f"/opportunities/{opp['id']}/quote", json={})
    assert q.status_code == 201, q.text
    assert db_session.get(Quote, q.json()["quote_id"]).seller_id == admin_id  # quien la crea, no la usuaria inactiva


def test_editar_documento_de_un_vendedor_inactivo(client, auth, db_session):
    from app.models import TenantUser

    a, b = _vendedores(client, db_session, auth)
    a_id, b_id = _uid(client, a), _uid(client, b)
    cust = _cliente(client)
    q = client.post("/quotes", json={"customer_id": cust["id"], "seller_id": a_id, "lines": _linea()}).json()
    inv = client.post("/invoices", json={"customer_id": cust["id"], "seller_id": a_id, "lines": _linea()}).json()
    otra = client.post("/quotes", json={"customer_id": cust["id"], "lines": _linea()}).json()
    m = db_session.scalar(select(TenantUser).where(TenantUser.user_id == a_id))
    m.active = False
    db_session.commit()
    # el editor del admin manda el vendedor en cada guardado: si no cambia, no se revalida (corregir una linea)
    r = client.put(f"/quotes/{q['id']}", json={"customer_id": cust["id"], "seller_id": a_id, "lines": _linea(120000)})
    assert r.status_code == 200 and r.json()["seller_id"] == a_id, r.text
    r = client.put(f"/invoices/{inv['id']}", json={"customer_id": cust["id"], "seller_id": a_id, "lines": _linea(120000)})
    assert r.status_code == 200 and r.json()["seller_id"] == a_id, r.text
    # elegir a alguien inactivo en otro documento si se valida; reasignar a uno activo, si
    assert client.put(f"/quotes/{otra['id']}", json={"customer_id": cust["id"], "seller_id": a_id, "lines": _linea()}).status_code == 422
    assert client.put(f"/quotes/{q['id']}", json={"customer_id": cust["id"], "seller_id": b_id, "lines": _linea()}).json()["seller_id"] == b_id


def test_sin_vendedor_no_vuelve_a_quien_la_tecleo(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    cust = _cliente(client)
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": _linea()}, headers=a).json()
    r = client.put(f"/quotes/{q['id']}", json={"customer_id": cust["id"], "seller_id": None, "lines": _linea()})
    assert r.status_code == 200 and r.json()["seller_id"] is None
    inv = client.post(f"/quotes/{q['id']}/convert")
    assert inv.status_code == 201, inv.text
    assert db_session.get(Invoice, inv.json()["id"]).seller_id is None  # ni la factura ni su comision vuelven a A


def test_retencion_por_margen_no_se_suelta_subiendo_el_precio(client, auth, db_session):
    """Si subir el precio soltara la retencion, unos guardados darian el precio donde el margen llega al minimo (y el costo)."""
    from app.models import Product

    a, _ = _vendedores(client, db_session, auth)
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    db_session.get(Product, cam["id"]).cost = Decimal("120")  # USD: a precio de catalogo queda bajo el minimo
    db_session.commit()
    linea = lambda precio: [{"product_id": cam["id"], "name": cam["name"], "quantity": 1, "unit_price": precio, "tax_rate": 13}]  # noqa: E731
    q = client.post("/quotes", headers=a, json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "quantity": 1}]}).json()
    assert q["status"] == "por_aprobar"
    # duplicarla tampoco da una copia lista para enviar (antes la copia solo miraba el descuento)
    dup = client.post(f"/quotes/{q['id']}/duplicate", headers=a).json()
    assert dup["status"] == "por_aprobar" and "margen" in dup["approval_reason"]
    alto = client.put(f"/quotes/{q['id']}", headers=a, json={"customer_id": cust["id"], "lines": linea(900000)}).json()
    assert alto["status"] == "por_aprobar" and "margen" in alto["approval_reason"]
    assert not re.search(r"\d+(?:[.,]\d+)? ?%", alto["approval_reason"])
    # quien aprueba la suelta
    assert client.post(f"/quotes/{q['id']}/approve").json()["status"] == "creado"
    # la retencion por descuento sigue soltandose sola al corregir el descuento
    prod = client.post("/products", json={"name": "Servicio sin costo", "code": "SRV-RET", "price": 100000, "currency": "CRC"}).json()
    dq = client.post("/quotes", headers=a, json={"customer_id": cust["id"], "lines": [{"product_id": prod["id"], "quantity": 1, "discount_value": 30}]}).json()
    assert dq["status"] == "por_aprobar"
    ok = client.put(f"/quotes/{dq['id']}", headers=a, json={"customer_id": cust["id"], "lines": [{"product_id": prod["id"], "quantity": 1}]}).json()
    assert ok["status"] == "creado"


def test_responsables_para_la_solicitud_de_costo(client, auth, db_session):
    a, _ = _vendedores(client, db_session, auth)
    admin_id = _uid(client)
    meta = client.get("/opportunities/meta/config", headers=a).json()
    assert meta["sellers"] == [] and admin_id in {x["id"] for x in meta["assignees"]}


def test_otros_canales_factura_sin_enlace_si_es_ajena(client, auth, db_session):
    from app.models import Order

    a, b = _vendedores(client, db_session, auth)
    cust = _cliente(client)
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": _linea()}, headers=a).json()
    db_session.add(Order(tenant_id=auth["tenant"]["id"], number="POS-ENL-1", channel="pos", invoice_id=inv["id"], total=1, currency="CRC"))
    db_session.commit()
    vista = lambda h: next(o for o in client.get("/orders", headers=h).json() if o["number"] == "POS-ENL-1")  # noqa: E731
    assert vista(a)["invoice_visible"] is True
    assert vista(b)["invoice_visible"] is False
    assert vista({})["invoice_visible"] is True


def test_solicitar_levantamiento_tiene_tope_por_persona(client, auth, db_session, monkeypatch):
    from app.core.ratelimit import survey_request_limiter

    a, _ = _vendedores(client, db_session, auth)
    monkeypatch.setattr(survey_request_limiter, "limit", 2)
    ops = [client.post("/opportunities", json={"title": f"Pedido {i}"}, headers=a).json() for i in range(3)]
    assert [client.post(f"/opportunities/{o['id']}/request-survey", json={}, headers=a).status_code for o in ops] == [200, 200, 429]
    assert db_session.get(Opportunity, ops[2]["id"]).status != "requiere_visita"  # el tope corta antes de avisar


# ---------- Segunda revision ----------
def test_comision_sobre_utilidad_sin_porcentaje_para_el_vendedor(client, auth, db_session):
    """Con base utilidad, comision / % x 100 = utilidad (y de ahi el costo): ni la base ni el % le llegan."""
    from app.models import Commission

    a, _ = _vendedores(client, db_session, auth)
    a_id = _uid(client, a)
    client.post("/commissions/rules", json={"user_id": a_id, "name": "Sobre utilidad", "base": "margen", "percent": 10})
    client.post("/commissions/rules", json={"name": "General", "base": "venta", "percent": 3})
    reglas = {r["name"]: r for r in client.get("/commissions/rules", headers=a).json()}
    assert reglas["Sobre utilidad"]["percent"] is None and _D(reglas["General"]["percent"]) == 3
    assert all(r["percent"] is not None for r in client.get("/commissions/rules").json())  # el admin si
    db_session.add(Commission(tenant_id=auth["tenant"]["id"], user_id=a_id, base="margen", base_amount=500, percent=10, amount=50, earned_on=date.today()))
    db_session.commit()
    fila = client.get("/commissions", headers=a).json()["rows"][0]
    assert fila["base_amount"] is None and fila["percent"] is None and _D(fila["amount"]) == 50
    suya = client.get("/commissions").json()["rows"][0]
    assert _D(suya["base_amount"]) == 500 and _D(suya["percent"]) == 10


def test_solicitud_de_levantamiento_se_atiende_en_cualquier_etapa_abierta(client, auth, db_session):
    """La regla de la solicitud y la de quien la atiende es la misma: pedida en "cotizando" (no retrocede), el equipo
    de campo la puede atender; sin solicitud, ni el supervisor ni el tecnico abren una ajena, y un aliado nunca."""
    tid = auth["tenant"]["id"]
    a, _ = _vendedores(client, db_session, auth)
    sup = _entrar(client, db_session, tid, "supervisor.etapa@ejemplo.com", "supervisor")
    t = _entrar(client, db_session, tid, "tecnico.etapa@ejemplo.com", "tecnico")
    ali = _entrar(client, db_session, tid, "aliado.etapa@ejemplo.com", "aliado")
    cust = _cliente(client)
    opp = client.post("/opportunities", json={"title": "Ya cotizando", "kind": "proyecto", "customer_id": cust["id"]}, headers=a).json()
    assert client.post(f"/opportunities/{opp['id']}/quote", json={}, headers=a).status_code == 201
    assert client.get(f"/opportunities/{opp['id']}", headers=a).json()["survey_request"] is None
    for h in (sup, t):  # sin solicitud: nadie que no la vea en el embudo la abre
        assert client.get(f"/surveys/from-opportunity/{opp['id']}", headers=h).status_code == 404
    # un aliado no entra a ningun endpoint interno (aliado_final §4: get_principal lo rechaza; su portal es /aliado)
    assert client.get(f"/surveys/from-opportunity/{opp['id']}", headers=ali).status_code == 403
    r = client.post(f"/opportunities/{opp['id']}/request-survey", json={}, headers=a)
    assert r.status_code == 200 and r.json()["status"] == "cotizando"  # el embudo no retrocede
    assert client.get(f"/opportunities/{opp['id']}", headers=a).json()["survey_request"] is not None
    # pedida: la atiende el equipo de campo interno; un aliado sigue sin verla (ni leer ni ligar)
    assert client.get(f"/surveys/from-opportunity/{opp['id']}", headers=ali).status_code == 403
    assert client.post("/surveys", json={"kind": "cctv", "opportunity_id": opp["id"]}, headers=ali).status_code == 403
    assert client.get(f"/surveys/from-opportunity/{opp['id']}", headers=t).status_code == 200
    pre = client.get(f"/surveys/from-opportunity/{opp['id']}", headers=sup)
    assert pre.status_code == 200 and pre.json()["customer_id"] == cust["id"]
    s = client.post("/surveys", json={"kind": "cctv", "opportunity_id": opp["id"]}, headers=sup)
    assert s.status_code == 201, s.text
    assert db_session.get(Opportunity, opp["id"]).status == "cotizando"
    assert s.json()["id"] in {x["id"] for x in client.get("/surveys", headers=a).json()}
    # atendida: deja de estar pendiente, aunque despues ese levantamiento se archive
    assert client.get(f"/opportunities/{opp['id']}", headers=a).json()["survey_request"] is None
    assert client.post(f"/archive/survey/{s.json()['id']}/archive").status_code == 200
    assert client.get(f"/surveys/from-opportunity/{opp['id']}", headers=sup).status_code == 404


def test_lead_sin_cliente_no_da_el_contacto_a_quien_no_lo_ve(client, auth, db_session):
    tid = auth["tenant"]["id"]
    a, _ = _vendedores(client, db_session, auth)
    sup = _entrar(client, db_session, tid, "supervisor.lead@ejemplo.com", "supervisor")
    lead = client.post("/opportunities", json={"title": "Lead de la tienda", "contact": {"name": "Persona Ejemplo"}}, headers=a).json()
    assert client.post(f"/opportunities/{lead['id']}/request-survey", json={}, headers=a).status_code == 200
    pre = client.get(f"/surveys/from-opportunity/{lead['id']}", headers=sup)
    assert pre.status_code == 200 and pre.json()["customer"] is None and "Persona Ejemplo" not in pre.text
    assert client.get(f"/surveys/from-opportunity/{lead['id']}").json()["customer"] == "Persona Ejemplo"  # el admin la ve


def test_sondeo_del_margen_tiene_freno(client, auth, db_session, monkeypatch):
    """Probando precios no se saca el costo: el minimo no se dice y, pasado el tope de margenes distintos evaluados
    cerca del minimo, todo queda retenido con el mismo motivo (no se distingue si fue el margen) y administracion
    recibe un aviso. Repetir la misma cotizacion no dice nada nuevo y no cuenta."""
    from app.models import Product
    from app.services import discounts

    a, _ = _vendedores(client, db_session, auth)
    assert client.get("/sales/discount-limit", headers=a).json()["min_margin"] is None
    assert client.get("/sales/discount-limit").json()["min_margin"] is not None
    monkeypatch.setattr(discounts, "MARGIN_PROBE_LIMIT", 2)
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    db_session.get(Product, cam["id"]).cost = Decimal("120")  # USD: a precio de catalogo queda bajo el minimo
    db_session.commit()

    def flaca(n=1, precio=None):
        ln = {"product_id": cam["id"], "quantity": n} | ({"unit_price": precio} if precio else {})
        return client.post("/quotes", headers=a, json={"customer_id": cust["id"], "lines": [ln]}).json()

    def sana():
        return client.post("/quotes", headers=a, json={"customer_id": cust["id"], "lines": _linea()}).json()

    def aviso():
        return db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("Cotizaciones cerca del margen mínimo%"))).all()

    q1 = flaca()
    assert q1["status"] == "por_aprobar" and "margen" in q1["approval_reason"]
    # volver a guardar la misma no cuenta como otra retencion, ni hacer otra igual (mismo margen: nada nuevo)
    body = {"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "quantity": 2}]}
    assert client.put(f"/quotes/{q1['id']}", headers=a, json=body).status_code == 200
    assert "margen" in flaca(3)["approval_reason"]
    assert sana()["status"] == "creado" and not aviso()
    q2 = flaca(precio=70000)  # otro margen: llega al tope, aviso a administracion y desde ya no se distingue
    assert q2["status"] == "por_aprobar" and "margen" not in q2["approval_reason"]
    assert len(aviso()) == 1 and "₡" not in aviso()[0].html
    otra, q3 = sana(), flaca()
    assert otra["status"] == q3["status"] == "por_aprobar"
    assert otra["approval_reason"] == q3["approval_reason"] and "margen" not in q3["approval_reason"]
    assert "margen" in client.get(f"/quotes/{q3['id']}").json()["approval_reason"]  # el admin ve la razon real
    assert len(aviso()) == 1  # un aviso por tope, no uno por cotizacion


# ---------- Revision: freno del sondeo y margen aprobado ----------
def _camara_con_costo(client, db_session):
    """La camara del catalogo (CRC 68 500) con costo de 120 USD: a precio de catalogo el margen queda bajo el minimo."""
    from app.models import Product

    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    db_session.get(Product, cam["id"]).cost = Decimal("120")
    db_session.commit()

    def body(precio, moneda="CRC"):
        return {"customer_id": cust["id"], "currency": moneda, "lines": [{"product_id": cam["id"], "quantity": 1, "unit_price": precio}]}

    return body


def _sondeos(db_session) -> int:
    return len(db_session.scalars(select(AuditLog).where(AuditLog.action.in_(("margin_hold", "margin_probe")))).all())


def _avisos_de_sondeo(db_session) -> list:
    return db_session.scalars(select(EmailOutbox).where(EmailOutbox.subject.like("Cotizaciones cerca del margen mínimo%"))).all()


def test_aprobada_con_margen_bajo_no_se_vuelve_a_retener(client, auth, db_session, monkeypatch):
    """«Guardar & enviar al cliente» y «Convertir a factura» guardan antes (PUT): una cotizacion con margen bajo que el
    admin aprobo no vuelve a quedar retenida mientras no se bajen sus precios, y esos guardados no cuentan para el
    freno del sondeo (antes cinco ciclos de aprobar y enviar daban el aviso de "probando precios" y todo retenido)."""
    from app.services import discounts

    a, b = _vendedores(client, db_session, auth)
    body = _camara_con_costo(client, db_session)
    q = client.post("/quotes", headers=a, json=body(68500)).json()
    assert q["status"] == "por_aprobar"
    assert client.post(f"/quotes/{q['id']}/approve").json()["status"] == "creado"
    for _ in range(discounts.MARGIN_PROBE_LIMIT + 1):
        r = client.put(f"/quotes/{q['id']}", headers=a, json=body(68500)).json()
        assert r["status"] == "creado" and r["approval_reason"] is None
    assert _sondeos(db_session) == 1 and not _avisos_de_sondeo(db_session)
    assert client.post(f"/quotes/{q['id']}/send", headers=a).status_code == 200

    monkeypatch.setattr(discounts, "MARGIN_PROBE_LIMIT", 100)  # lo que sigue mira solo la aprobacion
    # subirle el precio tampoco la retiene
    assert client.put(f"/quotes/{q['id']}", headers=a, json=body(70000)).json()["status"] == "enviada"
    # bajarlo de lo aprobado si: es un margen que nadie aprobo
    baja = client.put(f"/quotes/{q['id']}", headers=a, json=body(66000)).json()
    assert baja["status"] == "por_aprobar" and "margen" in baja["approval_reason"]
    # y volver a los precios aprobados la suelta (no dice nada del costo: es el precio que ella ya conoce)
    assert client.put(f"/quotes/{q['id']}", headers=a, json=body(68500)).json()["status"] == "creado"

    # los mismos numeros en otra divisa no son lo aprobado
    usd = client.post("/quotes", headers=b, json=body(130, "USD")).json()
    assert usd["status"] == "por_aprobar"
    client.post(f"/quotes/{usd['id']}/approve")
    assert client.put(f"/quotes/{usd['id']}", headers=b, json=body(130, "USD")).json()["status"] == "creado"
    assert client.put(f"/quotes/{usd['id']}", headers=b, json=body(131)).json()["status"] == "por_aprobar"
    assert "margen" in client.get(f"/quotes/{usd['id']}").json()["approval_reason"]

    # la que corrige y guarda el admin queda aprobada a esos precios (no hace falta ademas «Aprobar»)
    q2 = client.post("/quotes", headers=a, json=body(67000)).json()
    assert q2["status"] == "por_aprobar"
    assert client.put(f"/quotes/{q2['id']}", json=body(67500)).json()["status"] == "creado"
    assert client.put(f"/quotes/{q2['id']}", headers=a, json=body(67500)).json()["status"] == "creado"
    assert client.put(f"/quotes/{q2['id']}", headers=a, json=body(67000)).json()["status"] == "por_aprobar"


def test_bajar_el_precio_de_a_poco_frena_antes_de_la_frase_del_margen(client, auth, db_session):
    """Los guardados que no retienen tambien cuentan si el margen queda cerca del minimo: bajando el precio 1 % por PUT
    el freno se pone antes de que aparezca la frase del margen (antes eran gratis y la primera retencion daba el precio
    umbral, y de ahi el costo). Igual en otra divisa, donde bajar el precio ni siquiera cuenta como descuento."""
    from app.services import discounts

    a, b = _vendedores(client, db_session, auth)
    body = _camara_con_costo(client, db_session)
    for h, moneda, precio, paso in ((a, "CRC", 100000, 1000), (b, "USD", 200, 2)):
        q = client.post("/quotes", headers=h, json=body(precio, moneda)).json()
        assert q["status"] == "creado"  # lejos del minimo
        frenada = None
        while precio > paso and frenada is None:
            precio -= paso
            r = client.put(f"/quotes/{q['id']}", headers=h, json=body(precio, moneda)).json()
            assert "margen" not in (r["approval_reason"] or "")  # la frase del margen nunca le llega
            if r["status"] == "por_aprobar":
                frenada = r
        assert frenada is not None and frenada["approval_reason"] == discounts.PROBE_REASON
        # quedo retenida por el freno, no por el margen: todavia estaba por encima del minimo
        assert "margen" not in client.get(f"/quotes/{q['id']}").json()["approval_reason"]
    assert len(_avisos_de_sondeo(db_session)) == 2  # un aviso por vendedor


def test_trabajo_normal_no_dispara_el_freno(client, auth, db_session):
    """Repetir el mismo precio (otra cotizacion igual, para otro cliente) no dice nada nuevo, y un margen lejos del
    minimo no dice nada del costo: ninguno de los dos cuenta."""
    from app.services import discounts

    a, _ = _vendedores(client, db_session, auth)
    body = _camara_con_costo(client, db_session)
    for _ in range(discounts.MARGIN_PROBE_LIMIT + 1):
        r = client.post("/quotes", headers=a, json=body(86000)).json()  # cerca del minimo, sin retener
        assert r["status"] == "creado" and r["approval_reason"] is None
    for precio in range(100000, 130000, 5000):
        assert client.post("/quotes", headers=a, json=body(precio)).json()["status"] == "creado"
    assert _sondeos(db_session) == 1 and not _avisos_de_sondeo(db_session)
