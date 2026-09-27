"""Informe preliminar del levantamiento: se le manda al cliente antes de cotizar, asi que no puede llevar
ningun costo ni precio. Tambien permisos (field.ver) y que el tecnico no vea informes ajenos."""

import re
from decimal import Decimal

from tests.test_session4 import _user_with_role


def _survey_con_plata(client, db_session):
    """Levantamiento con producto que tiene costo y precio, costo escrito en el costeo y tarifas configuradas:
    todo lo que NO debe aparecer en el informe."""
    from app.models import Product, SurveyItem, Tenant

    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    prod = db_session.get(Product, cam["id"])
    prod.cost, prod.price = Decimal("81.37"), Decimal("64321")
    t = db_session.get(Tenant, client.get("/auth/me").json()["tenant"]["id"])
    t.settings = {**(t.settings or {}), "labor_day_cost": 27777, "travel_cost": 39999}
    db_session.commit()
    s = client.post(
        "/surveys",
        json={
            "kind": "cctv",
            "customer_id": cust["id"],
            "site": "Condominio Las Palmas, Heredia",
            "visit_date": "2026-09-20",
            "notes": "Portón principal sin energía cercana.",
            "labor": {"tecnico": {"people": 2, "days": 3}, "civil": {"people": 1, "days": 1}},
            "points": [
                {
                    "code": "CAM-01",
                    "label": "Entrada",
                    "data": {"tipo": "Bullet", "distancia_m": 72},
                    "notes": "Poste de 5 m",
                    "photos": ["https://cdn.ejemplo.com/f/1.jpg"],
                },
            ],
            "items": [
                {"product_id": cam["id"], "name": cam["name"], "quantity": 4, "kind": "equipo"},
                {"name": "Cable UTP Cat6", "quantity": 120, "unit": "m", "kind": "material"},
            ],
        },
    ).json()
    item = db_session.get(SurveyItem, s["items"][1]["id"])
    item.unit_cost = Decimal("555.55")  # costo escrito a mano en el costeo
    db_session.commit()
    return s, cam


def test_informe_sin_costos_ni_precios(client, auth, db_session):
    s, cam = _survey_con_plata(client, db_session)
    r = client.get(f"/surveys/{s['id']}/report.pdf")
    assert r.status_code == 200
    if r.headers.get("x-pdf-fallback") == "html" or r.headers["content-type"].startswith("text/html"):
        page = r.text
    else:  # con WeasyPrint instalado: se revisa el HTML que genero el PDF
        assert r.content[:4] == b"%PDF"
        from app.models import Survey, Tenant
        from app.services.survey_report import render_survey_html

        page = render_survey_html(db_session, db_session.get(Survey, s["id"]), db_session.get(Tenant, auth["tenant"]["id"]))
    # lo que si va
    assert s["number"] in page and "Condominio Las Palmas" in page and "CAM-01" in page and "Poste de 5 m" in page
    assert cam["name"] in page and "Cable UTP Cat6" in page and "Personal técnico" in page and "Personal de obra civil" in page
    assert "Portón principal" in page and "https://cdn.ejemplo.com/f/1.jpg" in page
    assert "Documento preliminar, no constituye cotización" in page
    # lo que jamas va: montos, simbolos de moneda, costos ni tarifas
    for prohibido in (
        "₡",
        "$",
        "81.37",
        "64321",
        "64 321",
        "64,321",
        "555.55",
        "27777",
        "39999",
        "25000",
        "Costo",
        "costo",
        "Precio",
        "precio",
        "Margen",
        "Total",
    ):
        assert prohibido not in page, prohibido
    assert not re.search(r"\d{1,3}(,\d{3})+\.\d{2}", page)  # nada con formato de dinero


def test_informe_permisos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    s = client.post("/surveys", json={"kind": "redes", "site": "Oficina admin"}).json()
    pw = _user_with_role(db_session, tid, "tec-inf@ejemplo.com", "tecnico")
    pwc = _user_with_role(db_session, tid, "caja-inf@ejemplo.com", "caja")
    client.headers.pop("Authorization", None)
    tk = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "tec-inf@ejemplo.com", "password": pw}).json()["access_token"]}
    cj = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "caja-inf@ejemplo.com", "password": pwc}).json()["access_token"]}
    client.headers.update(admin_h)
    assert client.get(f"/surveys/{s['id']}/report.pdf", headers=cj).status_code == 403  # caja no tiene field.ver
    assert client.get(f"/surveys/{s['id']}/report.pdf", headers=tk).status_code == 404  # levantamiento ajeno
    propio = client.post("/surveys", headers=tk, json={"kind": "redes", "site": "Mío"}).json()
    assert client.get(f"/surveys/{propio['id']}/report.pdf", headers=tk).status_code == 200
    # enviar al cliente es una accion comercial: el tecnico no
    assert client.post(f"/surveys/{propio['id']}/report/email", headers=tk, json={"to": "x@ejemplo.com"}).status_code == 403


def test_enviar_informe_solo_cuenta_si_el_correo_sale(client, auth, db_session, monkeypatch):
    from sqlalchemy import select

    from app.models import AuditLog

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    s = client.post("/surveys", json={"kind": "redes", "site": "Sitio"}).json()
    assert client.post(f"/surveys/{s['id']}/report/email", json={}).status_code in (200, 422)
    r = client.post(f"/surveys/{s['id']}/report/email", json={"to": "cliente@ejemplo.com"}).json()
    assert r["status"] == "simulado" and r["sent"] is False and "NO salió" in r["note"]
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "send_report", AuditLog.entity_id == s["id"]).order_by(AuditLog.id.desc()))
    assert log.diff["mail"] == "simulado"
