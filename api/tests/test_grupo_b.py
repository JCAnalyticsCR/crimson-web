"""Grupo B: CABYS masivo con confirmacion humana, versiones/aceptacion de cotizaciones y revision del supervisor."""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.models import AuditLog, EmailOutbox, Product
from tests.test_campo import _login, _survey

# ---------- 1. CABYS masivo ----------
CATALOGO = [
    {"codigo": "4527100000100", "descripcion": "Cámaras de vigilancia de video", "impuesto": 13, "categorias": ["a", "b"]},
    {"codigo": "4527100000200", "descripcion": "Cámaras fotográficas", "impuesto": 13},
    {"codigo": "4631300000100", "descripcion": "Cable UTP para transmisión de datos", "impuesto": 13},
    {"codigo": "4523300000300", "descripcion": "Discos duros para computadora", "impuesto": 13},
    {"codigo": "4618100000100", "descripcion": "Fuentes de poder", "impuesto": 1},
    {"codigo": "4618100000200", "descripcion": "Adaptadores de corriente", "impuesto": 0.5},
]


@pytest.fixture
def hacienda(monkeypatch):
    """Proxy CABYS mockeado: busca por texto en descripciones sin tildes; por codigo, exacto."""
    from app.services import cabys as svc
    from app.services.cabys_suggest import norm

    svc.clear_cache()
    calls = []

    def fake(params):
        calls.append(params)
        if "codigo" in params:
            return [x for x in CATALOGO if x["codigo"] == params["codigo"]]
        words = norm(params["q"]).split()
        # como Hacienda: todas las palabras (sin la s final) dentro de la descripcion
        rows = [x for x in CATALOGO if all(w.rstrip("s")[:6] in norm(x["descripcion"]) for w in words)]
        return {"total": len(rows), "cabys": rows}

    monkeypatch.setattr(svc, "_fetch", fake)
    yield calls
    svc.clear_cache()


def _prod(client, name, code, **extra):
    r = client.post("/products", json={"name": name, "code": code, "price": 1000, "cabys_code": None, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_terminos_genericos_quitan_marca_y_modelo():
    from app.services.cabys_suggest import terminos

    t = terminos("Cámara IP Bullet Hikvision DS-2CD1047G3-LIU 4MP 2.8mm", "Cámaras", "Hikvision", "DS-2CD1047G3-LIU")
    assert t[0] == "camara de vigilancia" and not any("hikvision" in x or "ds" in x.split() for x in t)
    assert terminos("Disco duro WD Purple 2TB", None, "WD")[0] == "disco duro"
    assert terminos("Bobina cable UTP Cat6 305m")[0] == "cable utp"
    assert terminos("Fuente de poder 12V 5A")[0] == "fuente de poder"
    assert terminos("UPS 1000VA interactiva")[0] == "ups"


def test_cabys_pendientes_sugerencia_y_asignacion(client, auth, db_session, hacienda):
    cats = {c["name"]: c["id"] for c in [client.post("/categories", json={"name": n}).json() for n in ("Cámaras", "Cableado")]}
    cam1 = _prod(client, "Cámara IP Bullet Hikvision DS-2CD1047G3 4MP", "CAM-HK-1", brand="Hikvision", model="DS-2CD1047G3", category_id=cats["Cámaras"])
    cam2 = _prod(client, "Cámara domo Dahua 2MP", "CAM-DH-2", brand="Dahua", category_id=cats["Cámaras"])
    utp = _prod(client, "Bobina cable UTP Cat6 305m", "UTP-305", category_id=cats["Cableado"])
    raro = _prod(client, "Artículo misterioso", "XX-1")
    wh = client.get("/warehouses").json()[0]["id"]
    assert client.post("/stock/movements", json={"product_id": utp["id"], "warehouse_id": wh, "kind": "entrada", "quantity": 4}).status_code == 201

    pend = client.get("/cabys/pending").json()
    ids = [x["id"] for x in pend["items"]]
    assert {cam1["id"], cam2["id"], utp["id"], raro["id"]} <= set(ids)
    assert ids[0] == utp["id"]  # con existencias primero
    assert pend["with_stock"] >= 1 and any(g["name"] == "Cámaras" and g["count"] == 2 for g in pend["categories"])

    sug = client.post("/cabys/suggest", json={"product_ids": [cam1["id"], cam2["id"], utp["id"], raro["id"]]}).json()["items"]
    by = {x["product_id"]: x for x in sug}
    assert by[cam1["id"]]["suggestion"]["code"] == "4527100000100" and by[cam1["id"]]["term"] == "camara de vigilancia"
    assert by[cam1["id"]]["suggestion"]["tax_rate"] == 13 and by[cam1["id"]]["suggestion"]["tax_configured"] is True
    assert by[utp["id"]]["suggestion"]["code"] == "4631300000100"
    assert by[raro["id"]]["suggestion"] is None and by[raro["id"]]["error"]
    n_calls = len(hacienda)
    client.post("/cabys/suggest", json={"product_ids": [cam1["id"], cam2["id"]]})
    assert len(hacienda) == n_calls  # mismas busquedas: salen de la cache

    # sugerir no guarda nada
    assert client.get(f"/products/{cam1['id']}").json()["cabys_code"] is None

    r = client.post(
        "/cabys/assign",
        json={
            "items": [
                {"product_id": cam1["id"], "code": "4527100000100"},
                {"product_id": cam2["id"], "code": "4527100000100"},
                {"product_id": utp["id"], "code": "123456789012"},  # 12 digitos
                {"product_id": raro["id"], "code": "9999999999999"},  # no existe
            ]
        },
    ).json()
    assert r["saved"] == 2 and r["errors"] == 2
    errs = {x["product_id"]: x["error"] for x in r["results"] if not x["ok"]}
    assert "13 dígitos" in errs[utp["id"]] and "no existe" in errs[raro["id"]]
    full = client.get(f"/products/{cam1['id']}").json()
    assert full["cabys_code"] == "4527100000100" and full["cabys_description"] == "Cámaras de vigilancia de video"
    pr = db_session.get(Product, cam1["id"])
    assert pr.cabys_set_by == auth["user"]["id"] and pr.cabys_set_at is not None
    log = db_session.scalars(select(AuditLog).where(AuditLog.action == "cabys_assign", AuditLog.entity_id == cam1["id"])).all()
    assert len(log) == 1 and log[0].diff["a"] == "4527100000100" and log[0].user_id == auth["user"]["id"]
    assert cam1["id"] not in [x["id"] for x in client.get("/cabys/pending").json()["items"]]

    # IVA del CABYS: se aplica si existe en Ajustes (1%); si no (0,5%) se avisa y no se toca el impuesto
    fuente = _prod(client, "Fuente de poder 12V", "FP-12", tax_ids=[t["id"] for t in client.get("/taxes").json() if float(t["rate"]) == 13])
    r = client.post("/cabys/assign", json={"items": [{"product_id": fuente["id"], "code": "4618100000100"}]}).json()
    assert r["saved"] == 1 and r["results"][0]["tax_applied"] is True and client.get(f"/products/{fuente['id']}").json()["tax_rate"] == 1
    adap = _prod(client, "Adaptador de corriente", "AD-1")
    r = client.post("/cabys/assign", json={"items": [{"product_id": adap["id"], "code": "4618100000200"}]}).json()
    assert r["saved"] == 1 and "no está configurado" in r["results"][0]["warning"]


def test_cabys_permisos_y_hacienda_caida(client, auth, db_session, monkeypatch, hacienda):
    from app.services import cabys as svc

    p = _prod(client, "Disco duro WD Purple 2TB", "HDD-2")
    tk = _login(client, db_session, auth["tenant"]["id"], "tec-cabys@ejemplo.com", "tecnico")
    assert client.post("/cabys/assign", json={"items": [{"product_id": p["id"], "code": "4523300000300"}]}, headers=tk).status_code == 403
    assert client.post("/cabys/suggest", json={"product_ids": [p["id"]]}, headers=tk).status_code == 403

    def down(params):
        raise httpx.ConnectTimeout("sin red")

    svc.clear_cache()
    monkeypatch.setattr(svc, "_fetch", down)
    sug = client.post("/cabys/suggest", json={"product_ids": [p["id"]]}).json()["items"][0]
    assert "Hacienda" in sug["error"]
    r = client.post("/cabys/assign", json={"items": [{"product_id": p["id"], "code": "4523300000300"}]}).json()
    assert r["saved"] == 0 and "validar" in r["results"][0]["error"]
    assert client.get(f"/products/{p['id']}").json()["cabys_code"] is None  # nunca se guarda sin validar


# ---------- 2. Versiones de cotizacion ----------
def _quote(client, price=1000):
    c = client.get("/customers").json()["items"][0]
    r = client.post("/quotes", json={"customer_id": c["id"], "lines": [{"name": "Cámara", "quantity": 2, "unit_price": price, "tax_rate": 13}]})
    assert r.status_code == 201, r.text
    return r.json()


def _edit(client, q, price):
    body = {"customer_id": q["customer_id"], "issue_date": q["issue_date"], "lines": [{"name": "Cámara", "quantity": 2, "unit_price": price, "tax_rate": 13}]}
    return client.put(f"/quotes/{q['id']}", json=body)


def test_versiones_al_enviar(client, auth):
    q = _quote(client)
    assert q["current_version"] is None and q["acceptance_status"] == "pendiente"
    s1 = client.post(f"/quotes/{q['id']}/send", json={"channel": "whatsapp", "to": "8888-0000"}).json()
    assert s1["status"] == "enviada" and s1["current_version"] == 1 and s1["has_unsent_changes"] is False
    assert client.post(f"/quotes/{q['id']}/send").json()["current_version"] == 1  # reenviar igual no crea version

    e = _edit(client, q, 1500).json()
    assert e["has_unsent_changes"] is True and e["status"] == "enviada"
    s2 = client.post(f"/quotes/{q['id']}/send").json()
    assert s2["current_version"] == 2 and s2["has_unsent_changes"] is False

    vs = client.get(f"/quotes/{q['id']}/versions").json()
    assert [v["version"] for v in vs["versions"]] == [2, 1]
    v1 = vs["versions"][1]
    assert v1["channel"] == "whatsapp" and v1["recipient"] == "8888-0000" and v1["sent_by"] and Decimal(str(v1["total"])) == Decimal("2260")
    # cada version se ve como salio (la v1 con el precio viejo)
    h1 = client.get(f"/quotes/{q['id']}/versions/1/html").text
    h2 = client.get(f"/quotes/{q['id']}/versions/2/pdf").text
    assert "1,000.00" in h1 and "v1" in h1 and "1,500.00" in h2
    assert client.get(f"/quotes/{q['id']}/versions/9/html").status_code == 404

    # notas internas no generan version nueva
    body = {
        "customer_id": q["customer_id"],
        "issue_date": q["issue_date"],
        "internal_notes": "solo nosotros",
        "lines": [{"name": "Cámara", "quantity": 2, "unit_price": 1500, "tax_rate": 13}],
    }
    assert client.put(f"/quotes/{q['id']}", json=body).json()["has_unsent_changes"] is False


def test_aceptacion_registra_y_convertir_usa_la_version(client, auth, db_session):
    q = _quote(client)
    assert (
        client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Ana", "channel": "correo"}).status_code == 409
    )  # sin versiones
    client.post(f"/quotes/{q['id']}/send")  # v1 a 1000
    _edit(client, q, 1800)  # cambio sin enviar
    assert client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "channel": "correo"}).status_code == 422  # falta quien
    r = client.post(
        f"/quotes/{q['id']}/acceptance",
        json={"status": "aceptada", "version": 1, "contact_name": "Ana Mora", "channel": "whatsapp", "decided_on": "2026-10-01"},
    )
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["acceptance_status"] == "aceptada" and a["accepted_version"] == 1 and a["status"] == "enviada"  # emision y aceptacion separadas
    assert Decimal(a["total"]) == Decimal("2260") and a["has_unsent_changes"] is False  # volvio a la version aceptada
    assert _edit(client, q, 2000).status_code == 409  # aceptada: no se edita

    hist = client.get(f"/quotes/{q['id']}/versions").json()
    assert hist["acceptances"][0]["contact_name"] == "Ana Mora" and hist["acceptances"][0]["recorded_by"] and hist["versions"][0]["accepted"] is True
    inv = client.post(f"/quotes/{q['id']}/convert").json()
    assert Decimal(inv["total"]) == Decimal("2260")


def test_rechazo_bloquea_convertir_y_nueva_version_reabre(client, auth):
    q = _quote(client)
    client.post(f"/quotes/{q['id']}/send")
    r = client.post(f"/quotes/{q['id']}/acceptance", json={"status": "rechazada", "contact_name": "Luis", "channel": "telefono", "notes": "muy caro"}).json()
    assert r["acceptance_status"] == "rechazada"
    c = client.post(f"/quotes/{q['id']}/convert")
    assert c.status_code == 409 and "rechazó" in c.json()["detail"]
    _edit(client, q, 900)
    assert client.post(f"/quotes/{q['id']}/send").json()["acceptance_status"] == "pendiente"  # v2 espera respuesta
    # deshacer una aceptacion
    client.post(f"/quotes/{q['id']}/acceptance", json={"status": "aceptada", "contact_name": "Luis", "channel": "firma"})
    u = client.post(f"/quotes/{q['id']}/acceptance", json={"status": "pendiente"}).json()
    assert u["acceptance_status"] == "pendiente" and u["accepted_version"] is None
    assert _edit(client, q, 950).status_code == 200
    lst = client.get("/quotes").json()["items"]
    assert next(x for x in lst if x["id"] == q["id"])["acceptance_status"] == "pendiente"


# ---------- 3. Revision del supervisor ----------
def test_revision_supervisor_devolver_resolver_aprobar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tk = _login(client, db_session, tid, "tec-rev@ejemplo.com", "tecnico")
    sup = _login(client, db_session, tid, "sup-rev@ejemplo.com", "supervisor")
    cust = client.get("/customers").json()["items"][0]
    s = _survey(client, headers=tk, customer_id=cust["id"], items=[{"name": "Cámara bullet", "quantity": 2}])
    assert s["review_status"] is None
    # el tecnico no puede revisar
    sent = client.post(f"/surveys/{s['id']}/send", headers=tk).json()
    assert sent["review_status"] == "pendiente"
    assert client.post(f"/surveys/{s['id']}/review", json={"action": "aprobar"}, headers=tk).status_code == 403
    # cotizar sin revision aprobada: 409 claro
    r = client.post(f"/surveys/{s['id']}/quote", json={})
    assert r.status_code == 409 and "revisión" in r.json()["detail"]

    assert client.post(f"/surveys/{s['id']}/review", json={"action": "devolver"}, headers=sup).status_code == 422  # sin observaciones
    bad = client.post(f"/surveys/{s['id']}/review", json={"action": "devolver", "observations": [{"point_code": "CAM-99", "text": "x"}]}, headers=sup)
    assert bad.status_code == 422
    antes = len(db_session.scalars(select(EmailOutbox)).all())
    dev = client.post(
        f"/surveys/{s['id']}/review",
        json={"action": "devolver", "observations": [{"point_code": "CAM-01", "text": "Falta la altura real"}], "note": "Agregá fotos del rack"},
        headers=sup,
    ).json()
    assert dev["review_status"] == "devuelto" and dev["status"] == "borrador" and dev["open_observations"] == 2
    correos = db_session.scalars(select(EmailOutbox)).all()[antes:]
    assert any(m.to == "tec-rev@ejemplo.com" and "devuelto" in m.subject for m in correos)

    # el tecnico ve las observaciones; no puede reenviar hasta resolverlas
    mine = client.get(f"/surveys/{s['id']}", headers=tk).json()
    assert {o["point_code"] for o in mine["observations"]} == {"CAM-01", None}
    lista = next(x for x in client.get("/surveys", headers=tk).json() if x["id"] == s["id"])
    assert lista["review_status"] == "devuelto" and lista["open_observations"] == 2
    assert s["id"] in [x["id"] for x in client.get("/work-orders/meta/today", headers=tk).json()["surveys"]]
    assert client.post(f"/surveys/{s['id']}/send", headers=tk).status_code == 409
    for o in mine["observations"]:
        client.post(f"/surveys/{s['id']}/observations/{o['id']}", json={"resolution": "Corregido"}, headers=tk)
    again = client.post(f"/surveys/{s['id']}/send", headers=tk).json()
    assert again["review_status"] == "pendiente" and again["status"] == "enviado" and again["open_observations"] == 0

    ok = client.post(f"/surveys/{s['id']}/review", json={"action": "aprobar"}, headers=sup).json()
    assert ok["review_status"] == "aprobado" and ok["reviewed_by"] == "Supervisor" and ok["review_round"] == 2
    assert [x["id"] for x in client.get("/surveys", params={"review": "aprobado"}).json()] == [s["id"]]
    q = client.post(f"/surveys/{s['id']}/quote", json={})
    assert q.status_code == 201, q.text


def test_admin_puede_saltar_revision_con_confirmacion(client, auth, db_session):
    s = _survey(client, customer_id=client.get("/customers").json()["items"][0]["id"], items=[{"name": "Cámara", "quantity": 1}])
    r = client.post(f"/surveys/{s['id']}/quote", json={})
    assert r.status_code == 409 and "administrador" in r.json()["detail"]
    assert client.post(f"/surveys/{s['id']}/quote", json={"skip_review": True}).status_code == 201
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "review_skip", AuditLog.entity_id == s["id"])) is not None


def test_editar_despues_de_aprobado_vuelve_a_revision(client, auth, db_session):
    tid = auth["tenant"]["id"]
    tk = _login(client, db_session, tid, "tec-rev2@ejemplo.com", "tecnico")
    s = _survey(client, headers=tk)
    client.post(f"/surveys/{s['id']}/send", headers=tk)
    assert client.post(f"/surveys/{s['id']}/review", json={"action": "aprobar"}).json()["review_status"] == "aprobado"
    d = client.get(f"/surveys/{s['id']}", headers=tk).json()
    e = client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "points": d["points"][:1], "items": d["items"]}, headers=tk).json()
    assert e["review_status"] == "pendiente"
