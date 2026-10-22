"""Reunion 2 · Vendedor (rol Ventas).

- 10: las acciones pendientes del vendedor (cotizaciones por vencer, seguimientos de hoy, oportunidades sin proxima
  accion, pedidos por atender) salen en su Inicio, solo lo suyo; cada tarjeta lleva a la lista con esas mismas filas.
- 13: el responsable de cada oportunidad va en la lista y en el detalle (nombre e iniciales); el admin filtra por el.
"""

from datetime import date, timedelta

from tests.test_rol_vendedor import _entrar, _uid


def _linea():
    return [{"name": "Cámara", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]


def _vendedores(client, db_session, auth):
    tid = auth["tenant"]["id"]
    a = _entrar(client, db_session, tid, "vende.r2a@ejemplo.com", "ventas")
    b = _entrar(client, db_session, tid, "vende.r2b@ejemplo.com", "ventas")
    return a, _uid(client, a), b, _uid(client, b)


def _opp(client, h, **kw):
    r = client.post("/opportunities", json={"title": kw.pop("title", "Oportunidad"), **kw}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_inicio_del_vendedor_con_sus_acciones_pendientes(client, auth, db_session):
    a, ua, b, _ub = _vendedores(client, db_session, auth)
    cust = client.get("/customers").json()["items"][0]
    hoy = date.today()
    # cotizaciones: por vencer (dentro de 7 dias) la de A; la de B y la que vence en un mes no cuentan para A
    q_a = client.post("/quotes", headers=a, json={"customer_id": cust["id"], "due_date": (hoy + timedelta(days=3)).isoformat(), "lines": _linea()}).json()
    client.post("/quotes", headers=a, json={"customer_id": cust["id"], "due_date": (hoy + timedelta(days=30)).isoformat(), "lines": _linea()})
    client.post("/quotes", headers=b, json={"customer_id": cust["id"], "due_date": (hoy + timedelta(days=2)).isoformat(), "lines": _linea()})
    # oportunidades: seguimiento vencido, de hoy, futuro y sin proxima accion
    o_late = _opp(client, a, title="Vencida", next_action="Llamar", next_action_date=(hoy - timedelta(days=2)).isoformat())
    o_hoy = _opp(client, a, title="Hoy", next_action="Visitar", next_action_date=hoy.isoformat())
    _opp(client, a, title="Futura", next_action="Enviar", next_action_date=(hoy + timedelta(days=5)).isoformat())
    o_sin = _opp(client, a, title="Sin accion")
    o_sin_fecha = _opp(client, a, title="Sin fecha", next_action="Pensarlo")
    _opp(client, b, title="De B sin accion")
    _opp(client, b, title="De B vencida", next_action="Llamar", next_action_date=(hoy - timedelta(days=1)).isoformat())

    d = client.get("/dashboard", headers=a).json()
    assert d["scope"] == "mine"
    pend = d["acciones_pendientes"]
    assert pend["cotizaciones_por_vencer"] == 1
    assert pend["seguimientos_hoy"] == 2
    assert pend["sin_proxima_accion"] == 2
    assert "pedidos_web" in pend  # pedidos por atender: la cola sin encargado y los suyos

    # cada tarjeta lleva a la lista con exactamente esas filas
    qs = client.get("/quotes", params={"pendiente": "cotizaciones_por_vencer"}, headers=a).json()["items"]
    assert [x["id"] for x in qs] == [q_a["id"]]
    seg = client.get("/opportunities/pending", headers=a).json()
    assert {x["id"] for x in seg} == {o_late["id"], o_hoy["id"]}
    sin = client.get("/opportunities", params={"pendiente": "sin_proxima_accion"}, headers=a).json()
    assert {x["id"] for x in sin} == {o_sin["id"], o_sin_fecha["id"]}
    assert client.get("/opportunities", params={"pendiente": "otra"}, headers=a).status_code == 422

    # cerrada: ya no es pendiente
    assert client.post(f"/opportunities/{o_sin['id']}/touch", headers=a, json={"note": "Se perdió", "status": "perdida"}).status_code in (200, 201)
    assert client.get("/dashboard", headers=a).json()["acciones_pendientes"]["sin_proxima_accion"] == 1

    # el admin (inicio de la empresa) ve las de todo el equipo, con la misma definicion que sus listas
    adm = client.get("/dashboard").json()["acciones_pendientes"]
    assert adm["cotizaciones_por_vencer"] >= 2
    assert adm["seguimientos_hoy"] == len(client.get("/opportunities/pending", params={"everyone": True}).json())
    assert adm["sin_proxima_accion"] == len(client.get("/opportunities", params={"pendiente": "sin_proxima_accion"}).json())


def test_responsable_visible_en_la_lista_y_filtro_del_admin(client, auth, db_session):
    a, ua, b, ub = _vendedores(client, db_session, auth)
    oa = _opp(client, a, title="De A")
    ob = _opp(client, b, title="De B")
    fila = next(x for x in client.get("/opportunities").json() if x["id"] == oa["id"])
    assert fila["owner_id"] == ua and fila["owner"] and fila["owner_initials"]
    det = client.get(f"/opportunities/{ob['id']}").json()
    assert det["owner_id"] == ub and det["owner"] and det["owner_initials"]
    # filtro por responsable (admin)
    assert {x["id"] for x in client.get("/opportunities", params={"owner_id": ub}).json()} >= {ob["id"]}
    assert oa["id"] not in {x["id"] for x in client.get("/opportunities", params={"owner_id": ub}).json()}
    # el vendedor no abre lo de otro aunque pida el filtro
    assert {x["id"] for x in client.get("/opportunities", params={"owner_id": ub}, headers=a).json()} == {oa["id"]}
