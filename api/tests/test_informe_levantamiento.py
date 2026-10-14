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


def test_informe_fotos_grandes(client, auth, db_session):
    """Las fotos del informe se tienen que notar ("es lo mas importante", Andres 13/10): nada de miniaturas de
    110x82. Una sola va a todo el ancho (440 px); varias, de dos en dos (340 px) y numeradas; un punto con mas de
    una fila de fotos puede partirse entre paginas."""
    from app.models import Survey, Tenant
    from app.services.survey_report import render_survey_html

    url = "https://cdn.ejemplo.com/f/{}.jpg".format
    s = client.post(
        "/surveys",
        json={
            "kind": "cctv",
            "site": "Bodega central",
            "photos": [url(f"g{i}") for i in range(3)],
            "points": [
                {"code": "CAM-01", "label": "Entrada", "photos": [url("a")]},
                {"code": "CAM-02", "label": "Patio", "photos": [url(f"b{i}") for i in range(3)]},
                {"code": "CAM-03", "label": "Pasillo", "photos": [url(f"c{i}") for i in range(6)]},
            ],
        },
    ).json()
    page = render_survey_html(db_session, db_session.get(Survey, s["id"]), db_session.get(Tenant, auth["tenant"]["id"]))

    assert "110px" not in page and "object-fit:cover" not in page  # la miniatura vieja ya no existe
    assert "height:440px" in page and "height:340px" in page and "240px" not in page and "320px" not in page
    # una sola foto: bloque a todo el ancho
    assert f'<div class="ph1"><img src="{url("a")}" alt="Foto · CAM-01">' in page
    # tres fotos: dos filas (2 + 1), numeradas con su punto
    ini = page.index('alt="Foto 1 · CAM-02"')
    tabla_b = page[page.rindex('<table class="ph"', 0, ini) : page.index("</table>", ini)]
    assert tabla_b.count("<tr>") == 2 and 'alt="Foto 3 · CAM-02"' in tabla_b
    # mas de una fila de fotos: el punto puede partirse entre paginas; con 1 o 2 fotos se mantiene entero
    assert page.count('class="pt largo"') == 2 and 'alt="Foto 6 · CAM-03"' in page
    assert '<div class="pt"><h3><span class="code">CAM-01</span>' in page
    # fotos generales: misma grilla
    assert 'alt="Foto 3 · general"' in page
    for i in range(3):
        assert url(f"g{i}") in page


def test_informe_orden_notas_y_equipos_primero(client, auth, db_session):
    """Andres 13/10 [01:08-01:40]: las notas arriba ("son importantes") y los equipos requeridos de primero;
    despues el detalle por punto con sus fotos. Las secciones vacias no aparecen."""
    from app.models import Survey, Tenant
    from app.services.survey_report import render_survey_html

    tenant = db_session.get(Tenant, auth["tenant"]["id"])
    s = client.post(
        "/surveys",
        json={
            "kind": "cctv",
            "site": "Bodega central",
            "notes": "Revisar la acometida antes de instalar.",
            "labor": {"tecnico": {"people": 2, "days": 2}},
            "photos": ["https://cdn.ejemplo.com/f/g1.jpg"],
            "points": [{"code": "CAM-01", "label": "Entrada", "photos": ["https://cdn.ejemplo.com/f/p1.jpg"]}],
            "items": [
                {"name": "Cámara bullet 4 MP", "quantity": 2, "kind": "equipo"},
                {"name": "Cable UTP Cat6", "quantity": 120, "unit": "m", "kind": "material"},
                {"name": "Alquiler de andamio", "quantity": 1, "kind": "servicio"},
            ],
        },
    ).json()
    page = render_survey_html(db_session, db_session.get(Survey, s["id"]), tenant)
    orden = [
        '<div class="meta">',
        "<h2>Notas del levantamiento</h2>",
        "<h2>Equipos requeridos</h2>",
        "<h2>Materiales requeridos</h2>",
        "<h2>Servicios y alquileres</h2>",
        "<h2>Personal estimado para la obra</h2>",
        "<h2>Detalle por punto",
        "<h2>Fotos generales</h2>",
        '<div class="foot">',  # el pie: la frase "Documento preliminar" tambien esta antes, en el CSS de @page
    ]
    pos = [page.index(t) for t in orden]
    assert pos == sorted(pos)
    # las notas se distinguen como lo primero que hay que leer
    assert "border-left:3px solid #e2233a" in page

    # sin notas ni equipos: esas secciones no aparecen y el resto mantiene su orden
    vacio = client.post("/surveys", json={"kind": "cctv", "site": "Bodega central", "points": [{"code": "CAM-01"}]}).json()
    page = render_survey_html(db_session, db_session.get(Survey, vacio["id"]), tenant)
    assert "Notas del levantamiento" not in page and "Equipos requeridos" not in page
    assert "Materiales requeridos" not in page and "Servicios y alquileres" not in page
    assert page.index('<div class="meta">') < page.index("<h2>Detalle por punto") < page.index('<div class="foot">')


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
