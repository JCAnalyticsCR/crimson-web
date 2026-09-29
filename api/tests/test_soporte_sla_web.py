"""SLA configurables (contrato > empresa > defecto), avisos de atraso de resolucion y reporte de
incidencias desde la pagina web (endpoint publico, sin sesion)."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.models import Customer, EmailOutbox, SupportTicket, Tenant
from app.services.sla import avisar_atrasos

SLA_EMPRESA = {
    "critica": {"respuesta": 1, "resolucion": 6},
    "alta": {"respuesta": 3, "resolucion": 12},
    "media": {"respuesta": 6, "resolucion": 48},
    "baja": {"respuesta": 12, "resolucion": 96},
}


def _horas(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def test_sla_cascada_defecto_empresa_contrato(client, auth, db_session):
    custs = client.get("/customers").json()["items"]
    c1, c2 = custs[0], custs[1]

    # 1) sin configurar: valores por defecto
    t = client.post("/tickets", json={"subject": "Cámara sin video", "customer_id": c1["id"], "priority": "alta"}).json()
    assert t["sla"]["origen"] == "defecto" and t["sla"]["respuesta_h"] == 4 and t["sla"]["resolucion_h"] == 24
    assert round(_horas(t["due_at"], t["resolve_due_at"])) == 20

    # 2) la empresa define su SLA en Ajustes (tabla completa y valida)
    assert client.put("/settings", json={"sla": {"alta": {"respuesta": 3, "resolucion": 12}}}).status_code == 422
    assert client.put("/settings", json={"sla": {**SLA_EMPRESA, "baja": {"respuesta": 0, "resolucion": 5}}}).status_code == 422
    assert client.put("/settings", json={"sla": SLA_EMPRESA}).status_code == 200
    assert client.get("/settings").json()["sla"]["alta"] == {"respuesta": 3, "resolucion": 12}
    assert client.get("/tickets/meta/config").json()["sla_horas"]["critica"] == 1
    t = client.post("/tickets", json={"subject": "Portón no abre", "customer_id": c1["id"], "priority": "alta"}).json()
    assert t["sla"]["origen"] == "empresa" and t["sla"]["resolucion_h"] == 12

    # 3) contrato activo del cliente con SLA propio: manda sobre la empresa, solo para ese cliente
    sla_ctr = {"alta": {"respuesta": 1, "resolucion": 4}}
    assert (
        client.post("/contracts", json={"customer_id": c1["id"], "name": "Soporte oro", "kind": "soporte", "sla": {"alta": {"respuesta": 0}}}).status_code
        == 422
    )
    ctr = client.post(
        "/contracts",
        json={"customer_id": c1["id"], "name": "Soporte oro", "kind": "soporte", "sla": sla_ctr, "next_date": str(date.today() + timedelta(days=90))},
    ).json()
    assert ctr["sla"]["alta"] == {"respuesta": 1, "resolucion": 4}
    t = client.post("/tickets", json={"subject": "NVR apagado", "customer_id": c1["id"], "priority": "alta"}).json()
    assert t["sla"]["origen"] == "contrato" and t["sla"]["contrato"] == ctr["number"] and t["sla"]["resolucion_h"] == 4
    # prioridad que el contrato no cubre: cae al SLA de la empresa
    t2 = client.post("/tickets", json={"subject": "Consulta de licencia", "customer_id": c1["id"], "priority": "baja"}).json()
    assert t2["sla"]["origen"] == "empresa"
    # otro cliente sin contrato: empresa
    t3 = client.post("/tickets", json={"subject": "Switch con luz naranja", "customer_id": c2["id"], "priority": "alta"}).json()
    assert t3["sla"]["origen"] == "empresa"

    # contrato inactivo ya no cuenta; cambiar la prioridad recalcula desde la creacion del ticket
    client.put(f"/contracts/{ctr['id']}", json={**{k: ctr[k] for k in ("customer_id", "name", "kind")}, "sla": sla_ctr, "active": False})
    t = client.put(f"/tickets/{t['id']}", json={"subject": "NVR apagado", "customer_id": c1["id"], "priority": "critica"}).json()
    assert t["sla"]["origen"] == "empresa" and t["sla"]["respuesta_h"] == 1


def test_aviso_de_atraso_de_resolucion_una_sola_vez(client, auth, db_session):
    cust = client.get("/customers").json()["items"][0]
    t = client.post("/tickets", json={"subject": "Cerca eléctrica sin tensión", "customer_id": cust["id"], "priority": "critica"}).json()
    # se contesta a tiempo, pero no se resuelve
    client.post(f"/tickets/{t['id']}/notes", json={"body": "Vamos en camino"})
    tenant = db_session.get(Tenant, auth["tenant"]["id"])
    antes = db_session.scalar(select(func.count()).select_from(EmailOutbox))

    assert avisar_atrasos(db_session, tenant, datetime.now(UTC) + timedelta(hours=3)) == 0  # dentro de las 8 h
    n = avisar_atrasos(db_session, tenant, datetime.now(UTC) + timedelta(hours=9))
    assert n == 1
    correos = db_session.scalars(select(EmailOutbox).order_by(EmailOutbox.id.desc())).all()
    assert db_session.scalar(select(func.count()).select_from(EmailOutbox)) > antes
    assert any("resolucion atrasada" in m.subject for m in correos)
    # la siguiente corrida del worker no repite el mismo aviso
    assert avisar_atrasos(db_session, tenant, datetime.now(UTC) + timedelta(hours=10)) == 0
    tk = client.get(f"/tickets/{t['id']}").json()
    assert tk["resolucion_vencida"] is False  # ahora mismo aun no vence
    assert "resolucion" in db_session.get(SupportTicket, t["id"]).sla["avisos"]


# ---------- Reporte desde la pagina web ----------
WEB = {
    "name": "María Pérez",
    "company": "Residencial Los Robles",
    "phone": "8888-1234",
    "email": "maria.perez@ejemplo.com",
    "kind": "falla",
    "severity": "alta",
    "description": "La cámara de la entrada principal no graba desde anoche.",
    "location": "Heredia, casa 14",
    "website": "",
}


def test_reporte_web_feliz_sin_sesion(client, db_session):
    assert "Authorization" not in client.headers
    antes_clientes = db_session.scalar(select(func.count()).select_from(Customer))
    antes_correos = db_session.scalar(select(func.count()).select_from(EmailOutbox))
    r = client.post("/public/incidents", json=WEB)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"ok", "number"} and body["number"].startswith("TCK-")  # nada interno en la respuesta
    t = db_session.scalar(select(SupportTicket).where(SupportTicket.number == body["number"]))
    assert t.channel == "web" and t.kind == "soporte" and t.priority == "alta" and t.customer_id is None
    assert t.contact["email"] == "maria.perez@ejemplo.com" and t.contact["company"] == "Residencial Los Robles"
    assert t.sla["origen"] in ("defecto", "empresa") and t.due_at and t.resolve_due_at
    # no crea clientes y avisa a admin/supervisor
    assert db_session.scalar(select(func.count()).select_from(Customer)) == antes_clientes
    assert db_session.scalar(select(func.count()).select_from(EmailOutbox)) > antes_correos


def test_reporte_web_liga_cliente_existente_sin_duplicar(client, db_session):
    c = db_session.scalars(select(Customer).order_by(Customer.id)).first()
    c.email, c.phone = "Cliente.Real@Ejemplo.com", "+506 7000-1111"
    db_session.commit()
    antes = db_session.scalar(select(func.count()).select_from(Customer))
    por_correo = client.post("/public/incidents", json={**WEB, "email": "cliente.real@ejemplo.com", "phone": None}).json()
    por_tel = client.post("/public/incidents", json={**WEB, "email": None, "phone": "70001111"}).json()
    desconocido = client.post("/public/incidents", json={**WEB, "email": "nadie@ejemplo.com", "phone": None}).json()
    assert db_session.scalar(select(func.count()).select_from(Customer)) == antes
    tk = {
        n: db_session.scalar(select(SupportTicket).where(SupportTicket.number == n)) for n in (por_correo["number"], por_tel["number"], desconocido["number"])
    }
    assert tk[por_correo["number"]].customer_id == c.id and tk[por_tel["number"]].customer_id == c.id
    assert tk[desconocido["number"]].customer_id is None
    # misma forma de respuesta exista o no el cliente: no sirve para averiguar quien es cliente
    assert set(por_correo) == set(desconocido) == {"ok", "number"}


def test_reporte_web_honeypot_no_crea_nada(client, db_session):
    antes = db_session.scalar(select(func.count()).select_from(SupportTicket))
    r = client.post("/public/incidents", json={**WEB, "website": "http://spam.example"})
    assert r.status_code == 201 and r.json() == {"ok": True, "number": None}
    assert db_session.scalar(select(func.count()).select_from(SupportTicket)) == antes


def test_reporte_web_validacion_y_tamano(client, db_session):
    from app.core.ratelimit import incident_limiter

    # los intentos invalidos tambien cuentan para el limite por IP (a proposito); aqui se prueba solo validacion
    orig = client.post

    def post(*a, **k):
        incident_limiter.reset()
        return orig(*a, **k)

    client.post = post
    assert client.post("/public/incidents", json={**WEB, "email": None, "phone": None}).status_code == 422
    assert client.post("/public/incidents", json={**WEB, "kind": "hackeo"}).status_code == 422
    assert client.post("/public/incidents", json={**WEB, "description": "corto"}).status_code == 422
    assert client.post("/public/incidents", json={**WEB, "tenant_id": 2}).status_code == 422  # campos extra prohibidos
    assert client.post("/public/incidents", json={**WEB, "phone": "<script>"}).status_code == 422
    r = client.post("/public/incidents", json={**WEB, "email": "no-es-correo"})
    assert r.status_code == 422 and "no-es-correo" not in r.text  # no hace eco del valor
    grande = client.post("/public/incidents", content=b'{"name":"' + b"x" * 9000 + b'"}', headers={"Content-Type": "application/json"})
    assert grande.status_code == 413


def test_reporte_web_limite_por_ip(client, db_session):
    for _ in range(5):
        assert client.post("/public/incidents", json=WEB).status_code == 201
    r = client.post("/public/incidents", json=WEB)
    assert r.status_code == 429 and r.headers.get("Retry-After")


def test_reporte_web_cors_para_la_landing(db_session, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.core.db import get_db
    from app.main import create_app

    monkeypatch.setattr(settings, "landing_origins", ["https://crimsoncr.com"])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    c = TestClient(app)
    pre = c.options(
        "/public/incidents",
        headers={"Origin": "https://crimsoncr.com", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
    )
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "https://crimsoncr.com"
    otro = c.options("/public/incidents", headers={"Origin": "https://malo.example", "Access-Control-Request-Method": "POST"})
    assert otro.headers.get("access-control-allow-origin") != "https://malo.example"


def test_sla_publico_sigue_a_ajustes(client, auth):
    """La pagina promete tiempos de respuesta. Si salian escritos a mano en el HTML, cambiar el SLA en Ajustes
    dejaba a la pagina prometiendo otra cosa. /public/sla los lee de la configuracion, sin sesion."""
    anon = client.get("/public/sla", headers={"Authorization": ""})
    assert anon.status_code == 200 and anon.json() == {"critica": 2, "alta": 4, "media": 8, "baja": 24}
    assert client.put("/settings", json={"sla": SLA_EMPRESA}).status_code == 200
    nuevo = client.get("/public/sla").json()
    assert nuevo == {pr: v["respuesta"] for pr, v in SLA_EMPRESA.items()}
    assert set(nuevo) == {"critica", "alta", "media", "baja"}  # solo horas de respuesta, nada interno
