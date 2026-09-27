"""Archivo y papelera: archivar/restaurar, reglas de integridad (409), que no cuente en listas ni embudo,
purga del worker a los N dias con auditoria, y que lo fiscal no entre."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from tests.test_session4 import _user_with_role


def _opp(client, **kw):
    cust = client.get("/customers").json()["items"][0]
    body = {"title": "Oportunidad archivo", "customer_id": cust["id"], "amount": 500000, "probability": 50} | kw
    return client.post("/opportunities", json=body).json()


def test_archivar_y_restaurar_saca_de_listas_y_embudo(client, auth):
    o = _opp(client)
    total0 = Decimal(str(client.get("/opportunities/board").json()["total"]))
    pipe0 = Decimal(str(client.get("/dashboard").json()["gerencia"]["pipeline"]))

    r = client.post(f"/archive/opportunity/{o['id']}/archive")
    assert r.status_code == 200 and r.json()["archived_at"]
    assert all(x["id"] != o["id"] for x in client.get("/opportunities").json())
    assert Decimal(str(client.get("/opportunities/board").json()["total"])) == total0 - 500000
    assert Decimal(str(client.get("/dashboard").json()["gerencia"]["pipeline"])) == pipe0 - 500000
    arch = client.get("/archive", params={"state": "archivado"}).json()
    assert any(x["kind"] == "opportunity" and x["id"] == o["id"] and x["archived_by"] for x in arch["rows"])
    assert client.get(f"/opportunities/{o['id']}").status_code == 200  # se sigue pudiendo abrir desde Archivo

    assert client.post(f"/archive/opportunity/{o['id']}/restore").status_code == 200
    assert any(x["id"] == o["id"] for x in client.get("/opportunities").json())
    assert Decimal(str(client.get("/opportunities/board").json()["total"])) == total0


def test_papelera_muestra_dias_y_levantamiento_sale_de_la_lista(client, auth):
    s = client.post("/surveys", json={"kind": "redes", "site": "Bodega central"}).json()
    r = client.post(f"/archive/survey/{s['id']}/trash")
    assert r.status_code == 200 and r.json()["purge_at"]
    assert all(x["id"] != s["id"] for x in client.get("/surveys").json())
    rows = client.get("/archive", params={"state": "papelera", "kind": "survey"}).json()["rows"]
    row = next(x for x in rows if x["id"] == s["id"])
    assert row["days_left"] == 30 and row["trashed_by"]
    # no aparece en "Archivados": son dos pestañas distintas
    assert all(x["id"] != s["id"] for x in client.get("/archive", params={"state": "archivado"}).json()["rows"])


def test_dependencias_vivas_responden_409(client, auth, db_session):
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Proyecto con OT", "customer_id": cust["id"]}).json()
    ot = client.post("/work-orders", json={"title": "Instalar", "project_id": pr["id"]}).json()
    r = client.post(f"/archive/project/{pr['id']}/trash")
    assert r.status_code == 409 and ot["number"] in r.json()["detail"] and "abiertas" in r.json()["detail"]
    assert client.post(f"/archive/project/{pr['id']}/archive").status_code == 409
    # cancelada la orden, el proyecto ya puede ir a la papelera
    client.post(f"/work-orders/{ot['id']}/cancel", json={})
    assert client.post(f"/archive/project/{pr['id']}/trash").status_code == 200

    # oportunidad cuya cotizacion ya se aprobo y se convirtio en factura
    o = _opp(client, title="Ganada")
    q = client.post("/quotes", json={"customer_id": cust["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]}).json()
    from app.models import Opportunity

    db_session.get(Opportunity, o["id"]).quote_id = q["id"]  # la cotizacion salio de esta oportunidad
    db_session.commit()
    assert client.post(f"/quotes/{q['id']}/convert").status_code == 201
    r = client.post(f"/archive/opportunity/{o['id']}/trash")
    assert r.status_code == 409 and "factura" in r.json()["detail"]
    # archivarla si se puede: limpiar el embudo no borra nada
    assert client.post(f"/archive/opportunity/{o['id']}/archive").status_code == 200


def test_facturas_y_cotizaciones_no_van_a_la_papelera(client, auth):
    cust = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 13}]}).json()
    r = client.post(f"/archive/invoice/{inv['id']}/trash")
    assert r.status_code == 409 and "comprobante" in r.json()["detail"]
    assert client.delete(f"/archive/invoice/{inv['id']}").status_code in (403, 409)
    assert client.post("/archive/quote/1/trash").status_code == 409


def test_purga_del_worker_a_los_n_dias_con_auditoria(client, auth, db_session):
    from app.models import AuditLog, Survey, SurveyPoint, Tenant
    from app.services.archive import purge_expired

    tid = auth["tenant"]["id"]
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "archive_purge_days": 10}
    db_session.commit()
    s = client.post("/surveys", json={"kind": "cctv", "site": "Viejo", "points": [{"code": "CAM-01", "data": {}}]}).json()
    client.post(f"/archive/survey/{s['id']}/trash")
    assert client.get("/archive", params={"state": "papelera"}).json()["purge_days"] == 10

    # a los 9 dias todavia no
    assert purge_expired(db_session, datetime.now(UTC) + timedelta(days=9)) == 0
    assert db_session.get(Survey, s["id"]) is not None
    # a los 10 dias el worker lo borra, con sus puntos, y deja auditoria
    assert purge_expired(db_session, datetime.now(UTC) + timedelta(days=10, minutes=1)) == 1
    db_session.expire_all()
    assert db_session.get(Survey, s["id"]) is None
    assert not db_session.scalars(select(SurveyPoint).where(SurveyPoint.survey_id == s["id"])).all()
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "purge", AuditLog.entity == "survey", AuditLog.entity_id == s["id"]))
    assert log and log.user_id is None and log.diff["auto"] is True
    assert log.diff["trashed_by"] == auth["user"]["id"] and log.diff["trashed_at"] and log.diff["number"] == s["number"]


def test_purga_manual_solo_admin_y_solo_desde_papelera(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    pw = _user_with_role(db_session, tid, "sup-arch@ejemplo.com", "supervisor")
    client.headers.pop("Authorization", None)
    sup = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "sup-arch@ejemplo.com", "password": pw}).json()["access_token"]}
    client.headers.update(admin_h)

    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Duplicado", "customer_id": cust["id"]}).json()
    assert client.delete(f"/archive/project/{pr['id']}").status_code == 409  # no esta en la papelera
    # el supervisor edita proyectos: puede mandarlo a la papelera, pero no borrarlo ya
    assert client.post(f"/archive/project/{pr['id']}/trash", headers=sup).status_code == 200
    assert client.delete(f"/archive/project/{pr['id']}", headers=sup).status_code == 403
    assert client.delete(f"/archive/project/{pr['id']}").status_code == 200
    assert client.get(f"/projects/{pr['id']}").status_code == 404


def test_tecnico_sin_permiso_de_editar_no_archiva(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    pw = _user_with_role(db_session, tid, "vend-arch@ejemplo.com", "ventas")
    client.headers.pop("Authorization", None)
    v = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "vend-arch@ejemplo.com", "password": pw}).json()["access_token"]}
    client.headers.update(admin_h)
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Ajeno", "customer_id": cust["id"]}).json()
    assert client.post(f"/archive/project/{pr['id']}/archive", headers=v).status_code == 403  # ventas solo ve proyectos
