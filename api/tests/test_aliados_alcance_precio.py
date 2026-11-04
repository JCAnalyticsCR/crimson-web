"""04/11: en el portal del aliado, (1) cada alcance que le toca se cotiza (el precio queda ligado a la partida y Crimson
lo ve en el alcance, solo quien ve costos) y (2) los levantamientos de la oportunidad en comun se ven todos (de Crimson
y de otros participantes), de solo lectura. Nombres y correos ficticios (repo publico)."""

from app.models import Survey
from app.services import scope_items as scopesvc

from .aliados_helpers import escenario
from .test_aliados_rev2 import _abierta


def test_el_aliado_cotiza_sus_alcances_y_crimson_ve_el_precio_en_cada_partida(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)
    oid, pa, pb = _abierta(client, s)
    base = f"/opportunities/{oid}/scope-items"
    cuerpos = {
        "mano": {"kind": "mano_obra", "description": "Mano de obra para instalar cámaras", "quantity": 2, "unit": "pers", "executor_kind": "aliado", "executor_participation_id": pa},
        "cable": {"kind": "materiales", "description": "Cable UTP", "quantity": 300, "unit": "m", "supplier_kind": "aliado", "supplier_participation_id": pa},
        "obra": {"kind": "obra_civil", "description": "Canalización", "executor_kind": "aliado", "executor_participation_id": pb},
    }
    for body in cuerpos.values():
        assert client.post(base, json=body).status_code == 201, body
    ids = {x["description"]: x["id"] for x in client.get(f"/opportunities/{oid}/scope").json()["items"]}
    mano, cable, obra = ids["Mano de obra para instalar cámaras"], ids["Cable UTP"], ids["Canalización"]

    # desglose: una linea por alcance, ligada a su partida
    url = f"/aliado/oportunidades/{oid}/precios"
    oferta = {
        "what": "Alcances de la obra",
        "currency": "CRC",
        "lines": [
            {"concept": "Mano de obra", "quantity": 2, "unit": "pers", "unit_price": 50000, "scope_item_id": mano},
            {"concept": "Cable UTP", "quantity": 300, "unit": "m", "unit_price": 100, "scope_item_id": cable},
        ],
    }
    r = client.post(url, json=oferta, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    al = {x["id"]: x for x in client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["alcances"]["items"]}
    assert al[mano]["offer"]["amount"] == "100000.00" and al[cable]["offer"]["amount"] == "30000.00"
    assert al[mano]["offer"]["status"] == "respondido" and al[obra]["offer"] is None  # el de otro: sin precio
    # un alcance de otra empresa no se cotiza (404, sin confirmar que existe)
    ajeno = {**oferta, "lines": [{"concept": "Obra", "quantity": 1, "unit_price": 1, "scope_item_id": obra}]}
    assert client.post(url, json=ajeno, headers=s.a_jefe).status_code == 404
    # el otro aliado no ve los precios de A en los alcances
    bl = {x["id"]: x for x in client.get(f"/aliado/oportunidades/{oid}", headers=s.b_jefe).json()["alcances"]["items"]}
    assert all(x.get("offer") is None for x in bl.values())

    # monto total por un solo alcance: tambien queda ligado; la ultima oferta gana
    r = client.post(url, json={"what": "Cable UTP", "amount": 25000, "scope_item_ids": [cable]}, headers=s.a_jefe)
    assert r.status_code == 201, r.text
    al = {x["id"]: x for x in client.get(f"/aliado/oportunidades/{oid}", headers=s.a_jefe).json()["alcances"]["items"]}
    assert al[cable]["offer"]["amount"] == "25000.00"

    # Crimson (admin, ve costos) ve el precio del aliado en cada partida; sin costos no sale
    crimson = {x["id"]: x for x in client.get(f"/opportunities/{oid}/scope").json()["items"]}
    assert crimson[mano]["offer"]["amount"] == "100000.00" and crimson[cable]["offer"]["amount"] == "25000.00"
    assert crimson[obra]["offer"] is None
    sin_costos = scopesvc.scope_out(db_session, s.tid, oid, None)
    assert all("offer" not in x for x in sin_costos["items"])
    # la solicitud dice que partidas cotiza
    reqs = [x for a in client.get(f"/opportunities/{oid}/allies").json() for x in a["requests"]]
    assert {ln["scope_item_id"] for x in reqs for ln in x.get("req_lines") or []} == {mano, cable}


def test_levantamientos_de_la_oportunidad_en_comun_se_ven_todos_de_solo_lectura(client, auth, db_session):
    s = escenario(client, dict(client.headers), db_session)

    def lev(numero, oid, **kw):
        x = Survey(tenant_id=s.tid, number=numero, kind="cctv", opportunity_id=oid, customer_id=s.c1, **kw)
        db_session.add(x)
        db_session.flush()
        return x.id

    # O3 es en comun con A y con B
    de_crimson = lev("LEV-CR3", s.O3, technician_id=s.admin_id, status="enviado", review_status="pendiente")
    de_b = lev("LEV-B3", s.O3, technician_id=s.uid_b_tec, ally_company_id=s.B, status="enviado", review_status="pendiente")
    borrador_b = lev("LEV-B3-BORR", s.O3, technician_id=s.uid_b_tec, ally_company_id=s.B, status="borrador")
    fuera = lev("LEV-O4", s.O4, technician_id=s.admin_id, status="enviado", review_status="pendiente")  # A no participa
    db_session.commit()

    vistos = {x["id"]: x for x in client.get("/aliado/levantamientos", headers=s.a_jefe).json()}
    assert de_crimson in vistos and de_b in vistos
    assert borrador_b not in vistos and fuera not in vistos  # el borrador de otro y lo ajeno, no
    assert vistos[de_crimson]["owner"] == "Crimson" and vistos[de_b]["owner"] == "Aliado Dos S.A."
    assert not vistos[de_b]["mine"] and not vistos[de_b]["can_edit"]
    # se abre y se lee, pero no se cambia
    assert client.get(f"/aliado/levantamientos/{de_b}", headers=s.a_jefe).status_code == 200
    assert client.post(f"/aliado/levantamientos/{de_b}/enviar", headers=s.a_jefe).status_code == 409
    # la oportunidad los lista juntos, con quien los hizo
    opp = client.get(f"/aliado/oportunidades/{s.O3}", headers=s.a_jefe).json()["surveys"]
    assert {x["number"]: x["owner"] for x in opp} == {"LEV-CR3": "Crimson", "LEV-B3": "Aliado Dos S.A."}
    # el tecnico del aliado sigue viendo solo lo suyo
    assert de_b not in {x["id"] for x in client.get("/aliado/levantamientos", headers=s.a_tec).json()}
