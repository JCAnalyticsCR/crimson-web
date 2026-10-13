"""P1 seguridad (documento interno de Andres §2, §13, AC02, AC03, AC07).

A) Archivos con permisos: lo publico sigue abierto (tienda, logo, lo de siempre); la evidencia interna nace privada
   y solo se entrega con enlace firmado que vence. PDF, informes y portal del cliente firman al armar la respuesta.
B) Bitacora completa: antes/despues de los campos que cambiaron, motivo, version y contexto, en levantamientos,
   miembros, gastos, exportaciones, costos de proyecto y reglas de comision; visor solo para administracion.
"""

import time
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import AuditLog, Media, Product, Survey
from app.services import bitacora
from app.services import media_access as ma
from tests.test_campo import _login, _survey
from tests.test_portal_cliente import _crear_cliente_portal, _dos_clientes

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")
PNG2 = PNG + b"\x00otra"  # misma firma de bytes, otro sha256


def _path(url: str) -> str:
    """La URL publica lleva el prefijo del proxy (/api); el TestClient habla directo con la API."""
    return url.split("/api", 1)[-1] if "/api/media/f/" in url else url


def _privada(client, data=PNG, module="field", headers=None):
    r = client.post(f"/media?visibility=private&module={module}", files={"file": ("evidencia.png", data, "image/png")}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- A) archivos
def test_publica_sigue_abierta_y_privada_solo_con_firma(client, auth):
    pub = client.post("/media", files={"file": ("logo.png", PNG, "image/png")}).json()
    assert pub["visibility"] == "public" and "signed_url" not in pub
    anon = client.headers.pop("Authorization")
    r = client.get(_path(pub["url"]))
    assert r.status_code == 200 and r.content == PNG and "public" in r.headers["cache-control"]
    client.headers["Authorization"] = anon

    m = _privada(client)
    assert m["visibility"] == "private" and m["module"] == "field" and "?" not in m["url"]  # la URL que se guarda es estable
    anon = client.headers.pop("Authorization")
    assert client.get(_path(m["url"])).status_code == 401  # privada sin firma
    ok = client.get(_path(m["signed_url"]))
    assert ok.status_code == 200 and ok.content == PNG
    assert ok.headers["cache-control"].startswith("private") and ok.headers["referrer-policy"] == "no-referrer"
    # firma de otra llave, firma alterada y fecha alterada: 403
    exp, sig = ma.sign(m["key"])
    assert client.get(f"{_path(m['url'])}?e={exp}&s={sig[:-2]}xx").status_code == 403
    assert client.get(f"{_path(m['url'])}?e={exp + 600}&s={sig}").status_code == 403
    _, sig_pub = ma.sign(pub["key"])
    assert client.get(f"{_path(m['url'])}?e={exp}&s={sig_pub}").status_code == 403
    # vencida: firmada hace dos horas con vida de un minuto
    viejo_exp, viejo_sig = ma.sign(m["key"], ttl_seconds=60, now=time.time() - 7200)
    r = client.get(f"{_path(m['url'])}?e={viejo_exp}&s={viejo_sig}")
    assert r.status_code == 403 and "venci" in r.json()["detail"]
    client.headers["Authorization"] = anon


def test_subida_privada_valida_modulo_y_permiso(client, auth, db_session):
    tid = auth["tenant"]["id"]
    assert client.post("/media?visibility=private", files={"file": ("a.png", PNG, "image/png")}).status_code == 422
    assert client.post("/media?visibility=private&module=tienda", files={"file": ("a.png", PNG, "image/png")}).status_code == 422
    caja = _login(client, db_session, tid, "caja.p1@ejemplo.com", "caja")
    assert client.post("/media?visibility=private&module=field", files={"file": ("a.png", PNG, "image/png")}, headers=caja).status_code == 403
    # el tecnico sube su evidencia de campo
    tec = _login(client, db_session, tid, "tec.p1@ejemplo.com", "tecnico")
    assert _privada(client, headers=tec)["module"] == "field"
    # dedupe por visibilidad: la misma foto publica no se reutiliza como privada ni al reves
    pub = client.post("/media", files={"file": ("x.png", PNG2, "image/png")}).json()
    priv = _privada(client, data=PNG2)
    assert pub["id"] != priv["id"] and pub["visibility"] == "public" and priv["visibility"] == "private"
    assert client.post("/media", files={"file": ("x.png", PNG2, "image/png")}).json()["id"] == pub["id"]
    assert _privada(client, data=PNG2)["id"] == priv["id"]


def test_firma_y_listado_respetan_el_modulo_de_origen(client, auth, db_session):
    tid = auth["tenant"]["id"]
    campo = _privada(client, module="field")
    conta = _privada(client, data=PNG2, module="accounting")
    pub = client.post("/media", files={"file": ("p.png", PNG, "image/png")}).json()
    externa = "https://cdn.ejemplo.com/x.png"
    urls = [campo["url"], conta["url"], pub["url"], externa]

    adm = client.post("/media/sign", json={"urls": urls}).json()["urls"]
    assert "?e=" in adm[campo["url"]] and "?e=" in adm[conta["url"]] and adm[pub["url"]] == pub["url"] and externa not in adm

    caja = _login(client, db_session, tid, "caja.sign@ejemplo.com", "caja")  # sin campo ni contabilidad
    r = client.post("/media/sign", json={"urls": urls}, headers=caja).json()["urls"]
    assert campo["url"] not in r and conta["url"] not in r and r[pub["url"]] == pub["url"]
    ventas = _login(client, db_session, tid, "ventas.sign@ejemplo.com", "ventas")  # ve campo, no contabilidad
    r = client.post("/media/sign", json={"urls": urls}, headers=ventas).json()["urls"]
    assert campo["url"] in r and conta["url"] not in r
    assert client.get(_path(r[campo["url"]])).status_code == 200

    # GET /media: cada quien ve lo publico y lo privado de los modulos que ve; visibility=public para la biblioteca
    ids = lambda h: {x["id"] for x in client.get("/media?kind=image", headers=h).json()}  # noqa: E731
    assert {campo["id"], conta["id"], pub["id"]} <= ids({})
    assert campo["id"] not in ids(caja) and conta["id"] not in ids(caja) and pub["id"] in ids(caja)
    assert campo["id"] in ids(ventas) and conta["id"] not in ids(ventas)
    lib = {x["id"] for x in client.get("/media?kind=image&visibility=public").json()}
    assert pub["id"] in lib and campo["id"] not in lib
    # borrar un privado ajeno: como si no existiera
    assert client.delete(f"/media/{conta['id']}", headers=ventas).status_code == 404


def test_informe_de_levantamiento_firma_fotos_privadas(client, auth, db_session):
    m = _privada(client)
    externa = "https://cdn.ejemplo.com/f/1.jpg"
    s = _survey(client, photos=[m["url"]])
    sv = db_session.get(Survey, s["id"])
    sv.points[0].photos = [m["url"], externa]
    db_session.commit()
    from app.services.survey_report import context, render_survey_html

    t = db_session.get(Survey, s["id"])
    ctx = context(db_session, t, auth_tenant(db_session, auth))
    firmada = ctx["points"][0]["photos"][0]
    assert firmada.startswith(m["url"] + "?e=") and ctx["points"][0]["photos"][1] == externa and "?e=" in ctx["photos"][0]
    anon = client.headers.pop("Authorization")
    assert client.get(_path(firmada)).status_code == 200
    client.headers["Authorization"] = anon
    html = render_survey_html(db_session, t, auth_tenant(db_session, auth))
    assert m["url"] + "?e=" in html
    # para correo: la firma dura dias (el cliente abre el correo despues)
    largo = context(db_session, t, auth_tenant(db_session, auth), ttl=ma.email_ttl())["photos"][0]
    exp = int(largo.split("e=", 1)[1].split("&", 1)[0])
    assert exp > time.time() + 29 * 86400
    # el endpoint sigue respondiendo (PDF o HTML de respaldo)
    assert client.get(f"/surveys/{s['id']}/report.pdf").status_code == 200


def auth_tenant(db_session, auth):
    from app.models import Tenant

    return db_session.get(Tenant, auth["tenant"]["id"])


def test_portal_cliente_ve_fotos_privadas_firmadas_y_las_suyas_publicas(client, auth, db_session):
    admin_h = dict(client.headers)
    a, _ = _dos_clientes(db_session)
    hu = _crear_cliente_portal(client, admin_h, a.id, "usuario.p1@a.cr", "cliente_usuario", "Usuario A")
    suya = client.post("/cliente/media", files={"file": ("f.png", PNG, "image/png")}, headers=hu).json()
    assert db_session.get(Media, suya["id"]).visibility == "public"  # el portal del cliente mantiene su logica
    t = client.post(
        "/cliente/tickets", json={"kind": "falla", "subject": "Cámara", "description": "No graba desde ayer", "photos": [suya["url"]]}, headers=hu
    ).json()
    priv = _privada(client, data=PNG2, module="support_desk", headers=admin_h)
    r = client.post(f"/tickets/{t['id']}/notes", json={"body": "Cambiamos la fuente", "photos": [priv["url"]]}, headers=admin_h)
    assert r.status_code == 201, r.text
    det = client.get(f"/cliente/tickets/{t['id']}", headers=hu).json()
    assert det["photos"] == [suya["url"]]  # publica: igual que siempre
    foto = det["messages"][-1]["photos"][0]
    assert foto.startswith(priv["url"] + "?e=")
    assert client.get(_path(foto)).status_code == 200
    # el usuario del cliente no puede pedir firmas internas
    assert client.post("/media/sign", json={"urls": [priv["url"]]}, headers=hu).status_code == 403


def test_tienda_sigue_mostrando_imagenes(client, auth, db_session):
    from tests.test_store import _publish_store

    _publish_store(client, "tienda")
    img = client.post("/media", files={"file": ("prod.png", PNG, "image/png")}).json()
    prod = db_session.scalars(select(Product).where(Product.tenant_id == auth["tenant"]["id"])).first()
    prod.images = [{"url": img["url"], "main": True}]
    prod.active, prod.show_on_web = True, True
    db_session.commit()
    client.headers.pop("Authorization")
    prods = client.get("/public/store/crimson/products").json()
    vitrina = next(x for x in prods if x["id"] == prod.id)
    assert vitrina["image"] == img["url"] and vitrina["images"] == [img["url"]]  # sin firma: la vitrina no cambia
    r = client.get(_path(vitrina["image"]))
    assert r.status_code == 200 and r.content == PNG


# ---------------------------------------------------------------- B) bitacora
def test_helpers_solo_lo_que_cambio():
    assert bitacora.jsonable(Decimal("1500.00000")) == "1500" and bitacora.jsonable(Decimal("2.50")) == "2.5"
    assert bitacora.jsonable(date(2026, 10, 1)) == "2026-10-01"
    b, a = bitacora.changes({"x": 1, "y": "a", "z": None}, {"x": 1, "y": "b", "w": 3})
    assert b == {"y": "a", "w": None} and a == {"y": "b", "w": 3}
    res, pb, pa = bitacora.children_diff(
        {"CAM-01": {"label": "A", "notes": None}, "CAM-02": {"label": "B"}}, {"CAM-01": {"label": "A", "notes": "x"}, "CAM-03": {"label": "C"}}
    )
    assert res == {"agregados": ["CAM-03"], "quitados": ["CAM-02"], "cambiados": ["CAM-01"]}
    assert pb["CAM-01"] == {"notes": None} and pa["CAM-01"] == {"notes": "x"} and pa["CAM-03"] == {"label": "C"}


def test_levantamiento_audita_campos_y_puntos(client, auth, db_session):
    s = _survey(client)
    body = client.get(f"/surveys/{s['id']}").json()
    puntos = [dict(p) for p in body["points"]]
    puntos[0]["notes"] = "Cambiar el soporte"
    puntos = [puntos[0], {"code": "CAM-03", "label": "Bodega", "data": {"tipo": "Domo"}, "photos": [], "notes": None}]  # CAM-02 fuera
    upd = {"kind": "cctv", "site": "Bodega Heredia", "points": puntos, "items": []}
    r = client.put(f"/surveys/{s['id']}", json=upd)
    assert r.status_code == 200, r.text
    # el guardado sigue igual: los puntos quedan como vinieron
    assert [p["code"] for p in r.json()["points"]] == ["CAM-01", "CAM-03"]
    log = db_session.scalar(select(AuditLog).where(AuditLog.entity == "survey", AuditLog.entity_id == s["id"], AuditLog.action == "update"))
    assert log.before["site"] == "Bodega Alajuela" and log.after["site"] == "Bodega Heredia"
    assert log.diff["puntos"] == {"agregados": ["CAM-03"], "quitados": ["CAM-02"], "cambiados": ["CAM-01"]}
    assert log.before["puntos"]["CAM-01"] == {"notes": None} and log.after["puntos"]["CAM-01"] == {"notes": "Cambiar el soporte"}
    assert log.before["puntos"]["CAM-02"]["label"] == "Patio" and log.after["puntos"]["CAM-03"]["label"] == "Bodega"
    assert log.version == "1" and log.ip
    # guardar sin cambios no ensucia la bitacora; un segundo cambio es la version 2
    client.put(f"/surveys/{s['id']}", json=upd)
    upd["site"] = "Bodega Cartago"
    client.put(f"/surveys/{s['id']}", json=upd)
    rows = db_session.scalars(select(AuditLog).where(AuditLog.entity == "survey", AuditLog.entity_id == s["id"], AuditLog.action == "update")).all()
    assert [x.version for x in rows] == ["1", "2"] and rows[-1].before == {"site": "Bodega Heredia"}


def test_miembros_gastos_exportes_proyectos_y_comisiones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _login(client, db_session, tid, "miembro.p1@ejemplo.com", "lectura")
    uid = next(u["id"] for u in client.get("/settings/users").json()["users"] if u["email"] == "miembro.p1@ejemplo.com")
    assert client.patch(f"/settings/users/{uid}", json={"role": "ventas", "reason": "Pasa a ventas en octubre"}).json() == {"ok": True}
    log = db_session.scalar(select(AuditLog).where(AuditLog.entity == "user", AuditLog.entity_id == uid, AuditLog.action == "permissions"))
    assert log.before == {"role": "lectura"} and log.after == {"role": "ventas"} and log.reason == "Pasa a ventas en octubre"
    client.patch(f"/settings/users/{uid}", json={"active": False})
    perm = select(AuditLog).where(AuditLog.entity == "user", AuditLog.entity_id == uid, AuditLog.action == "permissions")
    assert db_session.scalars(perm).all()[-1].after == {"active": False}

    g = client.post("/expenses", json={"description": "Gasolina", "date": "2026-10-01", "subtotal": 10000}).json()
    client.put(f"/expenses/{g['id']}", json={"description": "Gasolina", "date": "2026-10-01", "subtotal": 12500, "reason": "Faltaba la factura 2"})
    rows = db_session.scalars(select(AuditLog).where(AuditLog.entity == "expense", AuditLog.entity_id == g["id"])).all()
    assert [r.action for r in rows] == ["create", "update"]
    assert rows[1].before["subtotal"] == "10000" and rows[1].after["subtotal"] == "12500" and rows[1].reason == "Faltaba la factura 2"
    assert rows[1].version == "1" and "description" not in rows[1].after

    x = client.get("/reports/gastos?format=xlsx&from=2026-10-01&to=2026-10-31")
    assert x.status_code == 200
    exp = db_session.scalar(select(AuditLog).where(AuditLog.action == "export", AuditLog.entity == "report"))
    assert exp.diff["report"] == "gastos" and exp.diff["format"] == "xlsx" and exp.diff["from"] == "2026-10-01"

    pr = client.post("/projects", json={"name": "Instalación CCTV"}).json()
    body = {"name": "Instalación CCTV", "cost_labor": 150000, "cost_travel": 0, "cost_extra": 0}
    assert client.put(f"/projects/{pr['id']}", json=body).status_code == 200
    log = db_session.scalar(select(AuditLog).where(AuditLog.entity == "project", AuditLog.entity_id == pr["id"], AuditLog.action == "update"))
    assert log.before == {"cost_labor": "0"} and log.after == {"cost_labor": "150000"}

    rid = client.post("/commissions/rules", json={"name": "Base", "percent": 5}).json()["id"]
    client.put(f"/commissions/rules/{rid}", json={"name": "Base", "percent": 7.5})
    rows = db_session.scalars(select(AuditLog).where(AuditLog.entity == "commission_rule", AuditLog.entity_id == rid)).all()
    assert rows[0].action == "create" and rows[1].before == {"percent": "5"} and rows[1].after == {"percent": "7.5"}


def test_visor_de_bitacora_filtros_y_solo_admin(client, auth, db_session):
    tid = auth["tenant"]["id"]
    s = _survey(client)
    client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "site": "Otro sitio", "points": [], "items": []})
    r = client.get("/settings/audit?entity=survey")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows and all(x["entity"] == "survey" for x in rows)
    upd = next(x for x in rows if x["action"] == "update")
    assert upd["before"]["site"] == "Bodega Alajuela" and upd["user"] and upd["version"] == "1"
    from datetime import datetime

    from app.services.documents import CR

    hoy = datetime.now(CR).date().isoformat()  # el filtro es por dia de Costa Rica
    assert client.get(f"/settings/audit?entity=survey&from={hoy}&to={hoy}").json()["rows"]
    assert client.get("/settings/audit?entity=survey&from=2001-01-01&to=2001-01-02").json()["rows"] == []
    assert client.get(f"/settings/audit?user_id={auth['user']['id']}&action=update&entity_id={s['id']}").json()["rows"]
    pag = client.get("/settings/audit?limit=1").json()
    assert len(pag["rows"]) == 1 and pag["next_before_id"]
    assert client.get(f"/settings/audit?limit=1&before_id={pag['next_before_id']}").json()["rows"][0]["id"] < pag["rows"][0]["id"]
    f = client.get("/settings/audit/facets").json()
    assert "survey" in f["entities"] and "update" in f["actions"] and f["users"]
    conta = _login(client, db_session, tid, "conta.p1@ejemplo.com", "contabilidad")
    assert client.get("/settings/audit", headers=conta).status_code == 403
    assert client.get("/settings/audit/facets", headers=conta).status_code == 403


def test_script_opcional_a_privado_es_conservador(client, auth, db_session):
    """El script (NO se corre solo) pasaria a privado lo que usa solo la evidencia interna, nunca lo de la tienda."""
    from app.tools.media_a_privado import plan

    solo_campo = client.post("/media", files={"file": ("a.png", PNG, "image/png")}).json()
    compartida = client.post("/media", files={"file": ("b.png", PNG2, "image/png")}).json()
    _survey(client, photos=[solo_campo["url"], compartida["url"]])
    prod = db_session.scalars(select(Product).where(Product.tenant_id == auth["tenant"]["id"])).first()
    prod.images = [{"url": compartida["url"], "main": True}]
    db_session.commit()
    p = plan(db_session, auth["tenant"]["id"])
    assert p.get(solo_campo["key"]) == "field" and compartida["key"] not in p
    assert db_session.get(Media, solo_campo["id"]).visibility == "public"  # el plan no escribe nada
