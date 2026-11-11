"""O4-03: Mi Tienda -> Resumen («Lista para vender» y salud del catalogo), solo lectura.

- Los numeros coinciden con el panel Inventario web (publicados) y con el snapshot de la tienda (visibles, pagables).
- Cada punto lleva a su seccion o al catalogo ya filtrado; al corregir un punto, el avance sube.
- Presupuesto de consultas con el snapshot armado; el endpoint no escribe nada.
- Permisos: administrador y quien edita el catalogo; nadie mas.
Datos ficticios, precios de 2 000 colones o menos."""

import sqlalchemy as sa
from sqlalchemy import select

from app.models import Product
from app.services import store_health
from tests.test_session4 import _user_with_role
from tests.test_tienda_inventario import _in, _prod, _publish, _whs

S = "/public/store/crimson"


def _solo_estos(db_session):
    for p in db_session.scalars(select(Product)):
        p.show_on_web = False
    db_session.commit()


def _salud(client):
    store_health.clear_cache()
    r = client.get("/store/health")
    assert r.status_code == 200, r.text
    return r.json()


def _item(h, key):
    return next((x for x in h["items"] if x["key"] == key), None)


def test_los_numeros_coinciden_con_inventario_web_y_el_snapshot(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    main, _ = _whs(client)
    for i in range(5):
        p = _prod(client, f"SAL-{i}", f"Cámara de prueba {i}", price=1500 + i)
        if i < 2:
            _in(client, p["id"], main["id"], 3)  # en bodega: se paga en linea
    h = _salud(client)
    ind = h["indicadores"]
    inv = client.get("/store/inventory").json()["summary"]
    snap = client.get(f"{S}/snapshot").json()
    assert ind["publicados"] == inv["publicados"] == 5
    assert ind["visibles"] == snap["n"]
    comprar = sum(1 for x in snap["products"] if (x.get("availability") or {}).get("accion") == "comprar")
    assert ind["pagables"] == comprar == 2
    assert ind["solicitables"] == ind["visibles"] - ind["pagables"]
    txt = _item(h, "pagables")["texto"]
    assert "solo pueden pagar 2 productos en línea" in txt and f"los otros {ind['solicitables']}" in txt
    assert _item(h, "pagables")["to"] == "/mi-tienda/catalogo?estado=consultar"
    assert h["avance"]["total"] == len(h["items"]) and 0 <= h["avance"]["listos"] <= h["avance"]["total"]
    assert h["contadores"] is None  # O2-12 todavia no existe: no se inventan numeros


def test_cada_punto_lleva_a_donde_se_corrige_y_al_corregir_sube_el_avance(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    _prod(client, "SAL-LARGO", "Cámara de prueba con un nombre comercial larguísimo que Google cortaría en el resultado", price=1800)
    client.put("/store", json={"business": {"legal_name": "", "legal_id": ""}})
    h = _salud(client)
    for x in h["items"]:
        assert x["to"].startswith("/mi-tienda") or x["to"].startswith("/inventario"), x
        assert x["texto"] and x["grupo"] in ("Vender hoy", "Confianza y legalidad", "Verse bien", "Que la encuentren")
    assert _item(h, "cedula")["ok"] is False and _item(h, "cedula")["texto"] == "Falta la cédula jurídica en el pie"
    assert _item(h, "cedula")["to"] == "/mi-tienda/pagos-envios"
    largo = _item(h, "nombre_largo")
    assert largo["ok"] is False and largo["n"] == 1 and largo["to"] == "/mi-tienda/catalogo?falta=nombre_largo"
    # nada de medicion de terceros en la lista (decision del dueno)
    assert not any("medici" in x["texto"].lower() or "analytics" in x["texto"].lower() for x in h["items"])
    antes = h["avance"]["listos"]
    assert client.put("/store", json={"business": {"legal_name": "Comercio de Prueba S.A.", "legal_id": "3-101-000000"}}).status_code == 200
    h2 = _salud(client)
    assert _item(h2, "cedula")["ok"] is True and _item(h2, "razon_social")["ok"] is True
    assert h2["avance"]["listos"] == antes + 2


def test_titular_del_sinpe_distinto_y_retiro_sin_direccion(client, auth, db_session):
    _publish(client)
    client.put("/store", json={"business": {"legal_name": "Comercio de Prueba S.A."}})
    ck = {"methods": {"sinpe": {"active": True, "phone": "8888-0000", "holder": "Otra Persona"}}, "pickup": {"address": None}}
    assert client.put("/store/checkout", json=ck).status_code == 200
    h = _salud(client)
    assert _item(h, "titular_sinpe")["ok"] is False
    assert _item(h, "retiro")["texto"] == "Se ofrece retiro pero falta la dirección"  # la tarifa «Retiro en tienda» viene por defecto
    ck["methods"]["sinpe"]["holder"] = "COMERCIO DE PRUEBA, S. A."
    ck["pickup"] = {"address": "Oficina de prueba, San José"}
    assert client.put("/store/checkout", json=ck).status_code == 200
    h = _salud(client)
    assert _item(h, "titular_sinpe")["ok"] is True and _item(h, "retiro")["ok"] is True


def test_presupuesto_de_consultas_y_solo_lectura(client, auth, db_session):
    _publish(client)
    _solo_estos(db_session)
    for i in range(4):
        _prod(client, f"SAL-P{i}", f"Equipo de prueba {i}", price=1200)
    _salud(client)  # el snapshot se arma con la primera lectura
    store_health.clear_cache()
    engine = db_session.get_bind()
    sentencias: list[str] = []

    def _cuenta(conn, cursor, statement, params, context, executemany):
        sentencias.append(statement)

    sa.event.listen(engine, "before_cursor_execute", _cuenta)
    try:
        r = client.get("/store/health")
    finally:
        sa.event.remove(engine, "before_cursor_execute", _cuenta)
    assert r.status_code == 200
    escrituras = [s for s in sentencias if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    # la sesion del usuario puede refrescar su ultima actividad; el resumen en si no escribe nada de la tienda
    assert not [s for s in escrituras if "product" in s.lower() or "tenant " in s.lower() or "store" in s.lower()], escrituras
    lecturas = [s for s in sentencias if s.lstrip().upper().startswith("SELECT")]
    assert len(lecturas) <= 16, lecturas
    # cacheado: la segunda lectura no vuelve a calcular
    sentencias.clear()
    sa.event.listen(engine, "before_cursor_execute", _cuenta)
    try:
        client.get("/store/health")
    finally:
        sa.event.remove(engine, "before_cursor_execute", _cuenta)
    assert len([s for s in sentencias if "FROM product" in s]) == 0, sentencias


def test_permisos_del_resumen(client, auth, db_session):
    tid = auth["tenant"]["id"]

    def h(email, role):
        pw = _user_with_role(db_session, tid, email, role)
        keep = client.headers.pop("Authorization")
        tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
        client.headers["Authorization"] = keep
        return {"Authorization": f"Bearer {tok}"}

    assert client.get("/store/health", headers=h("vendedor.res@ejemplo.com", "ventas")).status_code == 403
    assert client.get("/store/health", headers=h("tecnico.res@ejemplo.com", "tecnico")).status_code == 403
    store_health.clear_cache()
    assert client.get("/store/health", headers=h("inventario.res@ejemplo.com", "inventario")).status_code == 200
