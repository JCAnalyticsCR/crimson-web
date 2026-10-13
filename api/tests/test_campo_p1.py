"""Plantilla v2 de levantamientos (documento de Andres §4, curso Hikvision): estado por campo, seccion comun,
control de acceso por metodo de ingreso/salida (AC08-AC11), credenciales, almacenamiento y PoE de CCTV.
REGLA DE ORO: un levantamiento viejo, sin los campos nuevos, sugiere exactamente lo mismo que antes."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.models import Survey
from app.services.survey_specs import TEMPLATE_VERSION, controller_options, credentials, suggest_materials
from tests.test_comercial2 import _cust

# claves que la v2 puede agregar a una linea; las de siempre no cambian
NUEVAS = {"alternatives", "warnings", "missing", "estimated", "reference"}
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "sugerencias_v1.json").read_text(encoding="utf-8"))


CANTIDADES = ("quantity", "existing_quantity", "add_quantity")


def _plain(lines):
    """Lineas sin las claves nuevas, con las cantidades comparadas por valor (la API las manda como numero)."""
    out = json.loads(json.dumps([{k: v for k, v in x.items() if k not in NUEVAS} for x in lines], default=str))
    for x in out:
        for k in CANTIDADES:
            x[k] = Decimal(str(x[k]))
    return out


def _by(sug):
    return {x["name"]: x for x in sug}


def _puertas(n, data):
    return [{"code": f"ACC-{i:02d}", "data": dict(data)} for i in range(1, n + 1)]


def _crear(client, **body):
    r = client.post("/surveys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- regresion: lo viejo sigue igual ----------
def test_levantamiento_viejo_sugiere_exactamente_lo_mismo():
    """Foto tomada con el codigo anterior (tests/fixtures/sugerencias_v1.json): 14 casos de todos los tipos."""
    for c in GOLDEN:
        got = suggest_materials(c["kind"], c["points"], [SimpleNamespace(**i) for i in c["items"]])
        assert _plain(got) == _plain(c["expected"]), c["case"]


def test_levantamiento_viejo_por_api_abre_sugiere_y_cotiza_igual(client, auth, db_session):
    caso = next(c for c in GOLDEN if c["case"] == "mixto")
    s = _crear(client, kind=caso["kind"], customer_id=_cust(client)["id"], points=[{k: v for k, v in p.items() if v is not None} for p in caso["points"]])
    # se simula un levantamiento de antes de la v2
    db_session.get(Survey, s["id"]).template_version = 1
    db_session.commit()
    full = client.get(f"/surveys/{s['id']}").json()
    assert full["template_version"] == 1 and full["common"] is None and all(p["data_status"] is None for p in full["points"])
    sug = client.post(f"/surveys/{s['id']}/suggest").json()
    assert _plain(sug) == _plain(caso["expected"])
    # guardarlo con el portal (sin common) no le cambia la version ni la seccion comun
    body = {"kind": "cctv", "customer_id": full["customer_id"], "points": full["points"], "items": []}
    assert client.put(f"/surveys/{s['id']}", json=body).json()["template_version"] == 1
    assert _plain(client.post(f"/surveys/{s['id']}/suggest").json()) == _plain(caso["expected"])
    picks = [{"name": x["name"], "quantity": x["add_quantity"], "unit": x["unit"], "kind": x["kind"]} for x in sug if x["action"] == "nuevo"]
    assert client.post(f"/surveys/{s['id']}/suggest/apply", json=picks).status_code == 200
    assert client.get(f"/surveys/{s['id']}/costing").status_code == 200
    assert client.post(f"/surveys/{s['id']}/quote", json={"skip_review": True}).status_code == 201


def test_version_de_plantilla(client, auth):
    specs = client.get("/field/specs").json()
    assert all(v["template_version"] == TEMPLATE_VERSION == 2 for v in specs.values())
    assert "acceso" in specs and "_common" not in specs  # el portal viejo itera los tipos: nada extra ahi
    common = client.get("/field/specs/common").json()
    assert common["template_version"] == 2 and common["data_status"] == ["confirmado", "estimado", "no_aplica", "pendiente"]
    assert {"acceso_sistema", "cctv_sistema", "parametros"} <= set(common["blocks"])
    assert _crear(client, kind="cctv")["template_version"] == 2
    keys = {f["key"] for f in specs["acceso"]["fields"]}
    assert {"metodo_ingreso", "metodo_salida", "hojas_controladas", "antipassback", "liberacion_emergencia", "salida", "lectura"} <= keys
    apb = next(f for f in specs["acceso"]["fields"] if f["key"] == "antipassback")
    assert apb["advanced"] is True  # oculto en "Funciones avanzadas"


def test_seccion_comun_y_quien_recibio(client, auth):
    common = {"objetivo": "Controlar bodega", "energia": "110 V en sitio", "acceso_sistema": {"usuarios": "200", "visitantes": 0}}
    s = _crear(client, kind="acceso", common=common, received_by="Doña Marta (recepción)")
    assert s["common"]["acceso_sistema"]["usuarios"] == 200 and s["received_by"] == "Doña Marta (recepción)"
    # un PUT que no manda common (portal viejo) no lo borra
    r = client.put(f"/surveys/{s['id']}", json={"kind": "acceso", "site": "Bodega"}).json()
    assert r["common"]["objetivo"] == "Controlar bodega" and r["received_by"] == "Doña Marta (recepción)"
    bad = client.put(f"/surveys/{s['id']}", json={"kind": "acceso", "common": {"cctv_sistema": {"dias_retencion": "mucho"}}})
    assert bad.status_code == 422 and "Días de retención" in bad.text


# ---------- estado por campo ----------
def test_campo_pendiente_o_vacio_nunca_es_cero(client, auth):
    s = _crear(
        client,
        kind="cctv",
        points=[
            {"code": "CAM-01", "data": {"tipo": "Domo", "distancia_m": 20, "altura_m": 3}, "data_status": {"distancia_m": "confirmado"}},
            {"code": "CAM-02", "data": {"tipo": "Domo", "distancia_m": 30, "altura_m": 3}, "data_status": {"distancia_m": "pendiente"}},
            {"code": "CAM-03", "data": {"tipo": "Domo", "distancia_m": 10, "altura_m": 3}, "data_status": {"distancia_m": "estimado"}},
            {"code": "CAM-04", "data": {"tipo": "Domo", "altura_m": 3}, "data_status": {"altura_m": "confirmado"}},
        ],
    )
    full = client.get(f"/surveys/{s['id']}").json()
    assert full["points"][1]["data_status"] == {"distancia_m": "pendiente"}
    cable = _by(client.post(f"/surveys/{s['id']}/suggest").json())["Cable (metros con 15 % de holgura)"]
    # el pendiente (30 m) no se usa y el vacio de CAM-04 tampoco cuenta como 0: la linea queda para revisar
    assert cable["action"] == "revisar" and Decimal(str(cable["add_quantity"])) == 0
    assert Decimal(str(cable["quantity"])) == 34  # (20 + 10) x 1.15 = 34.5 -> 34, solo como referencia
    assert "falta Distancia al gabinete (m) en CAM-02, CAM-04" in cable["reason"]
    assert cable["missing"] == ["Distancia al gabinete (m) en CAM-02, CAM-04"]
    assert "dato estimado: Distancia al gabinete (m) en CAM-03" in cable["reason"]
    bad = client.post("/surveys", json={"kind": "cctv", "points": [{"code": "CAM-01", "data": {}, "data_status": {"tipo": "quizas"}}]})
    assert bad.status_code == 422


def test_no_aplica_y_sin_estados_como_siempre():
    pts = [{"code": "CAM-01", "data": {"tipo": "Domo"}, "data_status": {"distancia_m": "no_aplica", "altura_m": "no_aplica"}}]
    by = _by(suggest_materials("cctv", pts))
    assert not any("andamio" in n.lower() or n.startswith("Cable (") for n in by)  # no aplica: no pide nada
    # sin estados el vacio sigue siendo lo de siempre: no hay cable y no aparece "revisar" nuevo
    by = _by(suggest_materials("cctv", [{"code": "CAM-01", "data": {"tipo": "Domo"}}]))
    assert not any(x.get("missing") for x in by.values())


def test_desperdicio_de_cable_es_parametro():
    pts = [{"code": f"CAM-{i}", "data": {"distancia_m": 20, "canalizacion": "EMT"}} for i in range(8)]
    base = _by(suggest_materials("cctv", pts))
    assert Decimal(base["Cable (metros con 15 % de holgura)"]["quantity"]) == 184  # 160 x 1.15, igual que antes
    assert base["Tubo EMT 3/4 (3 m)"]["quantity"] == 62  # ceil(160 x 1.15 / 3)
    otro = _by(suggest_materials("cctv", pts, common={"parametros": {"desperdicio_cable_pct": 20}}))
    assert Decimal(otro["Cable (metros con 20 % de holgura)"]["quantity"]) == 192
    assert "160 m en EMT + 20 %" in otro["Tubo EMT 3/4 (3 m)"]["reason"]


# ---------- control de acceso ----------
def test_ac08_tarjeta_y_boton(client, auth):
    s = _crear(client, kind="acceso", points=_puertas(17, {"metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Botón"], "distancia_m": 10}))
    by = _by(client.post(f"/surveys/{s['id']}/suggest").json())
    assert Decimal(str(by["Lector de tarjeta"]["quantity"])) == 17 and Decimal(str(by["Botón de salida"]["quantity"])) == 17
    ctrl = by["Controladora de acceso 4 puertas"]
    assert Decimal(str(ctrl["quantity"])) == 5 and "warnings" not in ctrl  # la sugerencia por defecto sigue igual
    sis = client.get(f"/surveys/{s['id']}/system").json()["acceso"]
    assert sis["lectores"] == 17 and sis["botones"] == 17 and sis["formula_lectores"] == "n + 2(N − n) = 17 + 2 × 0 = 17"
    assert not any("puertos" in a for a in sis["advertencias"])


def test_ac09_tarjeta_entrada_y_salida(client, auth):
    s = _crear(client, kind="acceso", points=_puertas(17, {"metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Tarjeta"]}))
    by = _by(client.post(f"/surveys/{s['id']}/suggest").json())
    assert Decimal(str(by["Lector de tarjeta"]["quantity"])) == 34
    assert by["Botón de salida"]["action"] == "revisar"  # solo el habitual sin cantidad: ninguna puerta sale con boton
    ctrl = by["Controladora de acceso 4 puertas"]
    assert any("revisar controlador, puertos y accesorios" in w for w in ctrl["warnings"])
    labels = {o["label"]: o for o in ctrl["alternatives"]}
    assert labels["4×4 + 1×1 = 17 puertas"]["reserva"] == 0
    assert labels["5×4 = 20 puertas"]["reserva"] == 3 and labels["5×4 = 20 puertas"]["actual"] is True
    sis = client.get(f"/surveys/{s['id']}/system").json()
    assert sis["acceso"]["lectores"] == 34 and any("puertos y accesorios" in a for a in sis["advertencias"])


def test_alternativas_de_controladoras():
    by = {o["label"]: o for o in controller_options(17)}
    assert list(by)[0] == "4×4 + 1×1 = 17 puertas"  # primero la exacta
    assert {"4×4 + 1×2 = 18 puertas", "5×4 = 20 puertas", "9×2 = 18 puertas"} <= set(by)
    assert [o for o in controller_options(2) if o["actual"]][0]["label"] == "1×2 = 2 puertas"


def test_ac10_antipassback_inactivo_no_genera_dependencia(client, auth):
    s = _crear(client, kind="acceso", points=_puertas(2, {"metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Botón"], "antipassback": False}))
    sis = client.get(f"/surveys/{s['id']}/system").json()
    assert sis["dependencias"] == []
    assert "warnings" not in _by(client.post(f"/surveys/{s['id']}/suggest").json())["Controladora de acceso 2 puertas"]


def test_ac11_antipassback_activo_sin_compatibilidad_es_visible(client, auth):
    s = _crear(client, kind="acceso", points=_puertas(1, {"metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Botón"], "antipassback": True}))
    dep = client.get(f"/surveys/{s['id']}/system").json()["dependencias"]
    assert len(dep) == 1 and "dependencia técnica pendiente" in dep[0]["text"]
    assert {"lector de salida", "controlador asignado", "protocolo del lector"} <= set(dep[0]["missing"])
    ctrl = _by(client.post(f"/surveys/{s['id']}/suggest").json())["Controladora de acceso 2 puertas"]
    assert any("anti-passback" in w for w in ctrl["warnings"])
    # con la arquitectura completa ya no queda pendiente
    ok = {"metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Tarjeta"], "antipassback": True, "controlador_asignado": "CTRL-01", "protocolo": "OSDP"}
    s2 = _crear(client, kind="acceso", points=_puertas(1, ok), common={"acceso_sistema": {"admin_central": "Software central (PC)"}})
    assert client.get(f"/surveys/{s2['id']}/system").json()["dependencias"] == []


def test_credenciales_sin_reserva_oculta(client, auth):
    c = credentials({"usuarios": 200, "visitantes": 0, "paquete_credenciales": 25})
    assert c["total"] == 200 and c["reserva"] == 0 and c["paquetes"] == 8
    common = {"acceso_sistema": {"usuarios": 180, "visitantes": 15, "reserva_credenciales": 5, "paquete_credenciales": 25}}
    s = _crear(client, kind="acceso", points=_puertas(1, {"lectura": ["Tarjeta"]}), common=common)
    line = _by(client.post(f"/surveys/{s['id']}/suggest").json())["Credenciales (paquete de 25)"]
    assert Decimal(str(line["quantity"])) == 8 and line["action"] == "nuevo"
    assert "180 usuarios + 15 visitantes + 5 de reserva = 200 credenciales; paquetes de 25 → 8" == line["reason"]
    # sin paquete: unidades; sin visitantes anotados: revisar, no se asume 0
    s2 = _crear(client, kind="acceso", points=_puertas(1, {"lectura": ["Tarjeta"]}), common={"acceso_sistema": {"usuarios": 50}})
    line = _by(client.post(f"/surveys/{s2['id']}/suggest").json())["Credenciales de acceso"]
    assert line["action"] == "revisar" and "falta Visitantes" in line["reason"]


def test_cerraduras_segun_hojas_y_referencias_del_curso(client, auth):
    pts = [
        {"code": "ACC-01", "data": {"cerradura": "Magnética 280 kg", "metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Botón"], "soporte_montaje": "ZL"}},
        {
            "code": "ACC-02",
            "data": {"cerradura": "Magnética 280 kg", "metodo_ingreso": ["Tarjeta"], "metodo_salida": ["Botón"], "hojas": "2", "hojas_controladas": "2"},
        },
    ]
    s = _crear(client, kind="acceso", points=pts, common={"acceso_sistema": {"emisor": True}})
    by = _by(client.post(f"/surveys/{s['id']}/suggest").json())
    assert Decimal(str(by["Soporte ZL para cerradura"]["quantity"])) == 1  # solo la puerta de una hoja
    dos = by["Cerradura y soporte para la segunda hoja"]
    assert dos["action"] == "revisar" and "ACC-02" in dos["reason"]
    ref = next(x for x in by.values() if "DS-K1F100A-D8E" in x["name"])
    assert ref["reference"] is True and ref["action"] == "revisar" and "referencia del curso, por verificar" in ref["reason"]


def test_lector_interior_viejo_se_lee_en_el_calculo():
    pts = [{"code": "ACC-01", "data": {"lectura": ["Tarjeta"], "salida": "Lector interior"}}]
    from app.services.survey_specs import system_calcs

    sis = system_calcs("acceso", pts)["acceso"]
    assert sis["lectores"] == 2 and any("Lector interior" in a for a in sis["advertencias"])
    assert Decimal(_by(suggest_materials("acceso", pts))["Lector de tarjeta"]["quantity"]) == 1  # sugerencia de antes


# ---------- CCTV ----------
def test_almacenamiento_y_poe(client, auth):
    common = {"cctv_sistema": {"bitrate_mbps": 4, "dias_retencion": 30, "switch_poe_w": 60, "entrada_nvr_mbps": 80, "canales": 8}}
    pts = [{"code": f"CAM-{i:02d}", "data": {"tipo": "Domo", "alimentacion": "PoE", "consumo_w": 6.5}} for i in range(1, 9)]
    s = _crear(client, kind="cctv", points=pts, common=common)
    cctv = client.get(f"/surveys/{s['id']}/system").json()["cctv"]
    alm = cctv["almacenamiento"]
    # 8 x 4 Mb/s x 86 400 s x 30 dias / 8 000 000 = 10,368 TB decimales
    assert Decimal(str(alm["tb"])) == Decimal("10.368") and alm["completo"] is True
    assert "validar con la herramienta del fabricante" in alm["leyenda"]
    poe = cctv["poe"]
    assert Decimal(str(poe["consumo_w"])) == 52 and Decimal(str(poe["requerido_w"])) == Decimal("62.4") and poe["ok"] is False
    assert cctv["entrada_nvr"]["ok"] is True and cctv["canales"] == 8
    # bitrate propio por camara manda sobre el de diseno; sin dias no se calcula
    pts[0]["data"]["bitrate_mbps"] = 8
    common["cctv_sistema"].update(switch_poe_w=120)
    r = client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "points": pts, "common": common}).json()
    cctv = client.get(f"/surveys/{r['id']}/system").json()["cctv"]
    assert Decimal(str(cctv["almacenamiento"]["tb"])) == Decimal("11.664") and cctv["poe"]["ok"] is True
    sin = client.put(f"/surveys/{s['id']}", json={"kind": "cctv", "points": pts, "common": {}}).json()
    alm = client.get(f"/surveys/{sin['id']}/system").json()["cctv"]["almacenamiento"]
    assert alm["tb"] is None and alm["completo"] is False and "días de retención" in alm["faltan"]
